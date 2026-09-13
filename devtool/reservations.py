from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Any, Iterable, Iterator

from .xdg import data_dir, state_dir


@dataclass(frozen=True, slots=True)
class Reservation:
    source: str
    reservation_id: str
    target: str
    paths: tuple[str, ...]
    paths_known: bool
    status: str = ""
    pid: int = 0
    run_id: str = ""
    transaction_id: str = ""
    metadata_path: str = ""


class ReservationConflict(RuntimeError):
    pass


def repo_key(repo_root: Path) -> str:
    resolved_path = repo_root.resolve()
    resolved = str(resolved_path)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", resolved_path.name or "repo").strip(".-") or "repo"
    digest = hashlib.sha256(resolved.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return f"{slug}-{digest}"


def normalize_path(raw: str | Path) -> str:
    text = str(raw).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise ReservationConflict(f"Unsafe reservation path: {raw}")
    if path.parts and path.parts[0] == ".git":
        raise ReservationConflict(f"Refusing to reserve Git metadata path: {raw}")
    return path.as_posix()


def normalize_paths(paths: Iterable[str | Path]) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        value = normalize_path(raw)
        if value not in seen:
            seen.add(value)
            values.append(value)
    values.sort()
    return tuple(values)


def paths_overlap(left: str | Path, right: str | Path) -> bool:
    a = PurePosixPath(normalize_path(left)).parts
    b = PurePosixPath(normalize_path(right)).parts
    common = min(len(a), len(b))
    return a[:common] == b[:common]


def reservation_conflicting_paths(left: Iterable[str], right: Iterable[str]) -> tuple[str, str] | None:
    for a in left:
        for b in right:
            if paths_overlap(a, b):
                return a, b
    return None


def coordinator_path(repo_root: Path) -> Path:
    return state_dir() / "locks" / repo_key(repo_root) / "coordinator.lock"


@contextmanager
def reservation_coordinator(repo_root: Path) -> Iterator[IO[str]]:
    path = coordinator_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield handle
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _lock_is_held(path: Path) -> bool | None:
    try:
        with path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            finally:
                with contextlib.suppress(OSError):
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except PermissionError:
        return None
    except OSError:
        return False
    return False


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _run_reservations(repo_root: Path) -> list[Reservation]:
    root = state_dir() / "locks" / repo_key(repo_root) / "runs"
    reservations: list[Reservation] = []
    try:
        paths = sorted(root.glob("*.lock"))
    except OSError:
        paths = []
    for path in paths:
        data = _read_json(path)
        if not data:
            continue
        try:
            pid = int(data.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        held = _lock_is_held(path)
        if held is False or (held is None and not _pid_is_alive(pid)):
            continue
        raw_paths = data.get("reserved_paths")
        paths_known = bool(data.get("paths_known")) and isinstance(raw_paths, list)
        try:
            reserved = normalize_paths(str(item) for item in raw_paths or []) if paths_known else ()
        except ReservationConflict:
            paths_known = False
            reserved = ()
        reservations.append(Reservation(
            source="run",
            reservation_id=str(data.get("run_id") or path.stem),
            target=str(data.get("target") or "").strip(),
            paths=reserved,
            paths_known=paths_known,
            status=str(data.get("status") or "running"),
            pid=pid,
            run_id=str(data.get("run_id") or ""),
            metadata_path=str(path),
        ))
    return reservations


def _transaction_reservations(repo_root: Path) -> list[Reservation]:
    root = data_dir() / "transactions" / repo_key(repo_root)
    markers: list[Path] = []
    legacy = root / "active.json"
    if legacy.exists():
        markers.append(legacy)
    active = root / "active"
    if active.exists():
        markers.extend(sorted(active.glob("*.json")))
    reservations: list[Reservation] = []
    seen: set[str] = set()
    for marker in markers:
        marker_data = _read_json(marker)
        if not marker_data:
            continue
        tx_path = Path(str(marker_data.get("path") or ""))
        tx = _read_json(tx_path) if tx_path.exists() else None
        data = tx or marker_data
        txid = str(data.get("transaction_id") or marker_data.get("transaction_id") or "")
        identity = txid or str(tx_path)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        raw_paths = data.get("reserved_paths")
        if not isinstance(raw_paths, list) and isinstance(data.get("affected_paths"), list):
            raw_paths = [
                str(item.get("path") or "")
                for item in data.get("affected_paths") or []
                if isinstance(item, dict) and str(item.get("path") or "")
            ]
        # Transactions created before scoped reservations are deliberately
        # treated as unknown so a new Devtool cannot race an older rollback.
        paths_known = int(data.get("schema_version") or 0) >= 3 and isinstance(raw_paths, list)
        try:
            reserved = normalize_paths(str(item) for item in raw_paths or []) if paths_known else ()
        except ReservationConflict:
            paths_known = False
            reserved = ()
        reservations.append(Reservation(
            source="transaction",
            reservation_id=identity,
            target=str(data.get("target") or marker_data.get("target") or "").strip(),
            paths=reserved,
            paths_known=paths_known,
            status=str(data.get("status") or marker_data.get("status") or "active"),
            pid=int(data.get("pid") or marker_data.get("pid") or 0),
            run_id=str(data.get("run_id") or marker_data.get("run_id") or ""),
            transaction_id=txid,
            metadata_path=str(tx_path or marker),
        ))
    return reservations


def active_reservations(repo_root: Path) -> list[Reservation]:
    return [*_run_reservations(repo_root), *_transaction_reservations(repo_root)]


def reservations_compatible(
    *,
    target: str,
    paths: Iterable[str],
    paths_known: bool,
    other: Reservation,
) -> bool:
    target = str(target or "").strip()
    if not target or not other.target or target == other.target:
        return False
    if not paths_known or not other.paths_known:
        return False
    current_paths = normalize_paths(paths)
    return reservation_conflicting_paths(current_paths, other.paths) is None


def _conflict_message(target: str, paths: tuple[str, ...], other: Reservation, *, paths_known: bool = True) -> str:
    collision = reservation_conflicting_paths(paths, other.paths) if paths and other.paths else None
    detail = ""
    if collision:
        detail = f"\nPath conflict: {collision[0]} <-> {collision[1]}"
    elif not target or not other.target:
        detail = "\nTarget identity is unknown; exclusive repository access is required."
    elif target == other.target:
        detail = f"\nBoth operations target {target!r}; same-target operations are serialized."
    elif not paths_known:
        detail = "\nThe current operation has an unknown write set; exclusive repository access is required."
    elif not other.paths_known:
        detail = "\nThe other operation has an unknown write set; exclusive repository access is required."
    return (
        "Another devtool operation conflicts with this repository reservation; another run is already active.\n"
        f"Current target: {target or '<unknown>'}\n"
        f"Blocking {other.source}: {other.reservation_id}\n"
        f"Blocking target: {other.target or '<unknown>'}\n"
        f"Blocking state: {other.status or '-'}"
        f"{detail}"
    )


def assert_reservation_available(
    repo_root: Path,
    *,
    target: str,
    paths: Iterable[str],
    paths_known: bool,
    exclude_run_id: str = "",
    exclude_transaction_id: str = "",
) -> None:
    current_paths = normalize_paths(paths) if paths_known else ()
    for other in active_reservations(repo_root):
        if exclude_run_id and other.run_id == exclude_run_id:
            continue
        if exclude_transaction_id and other.transaction_id == exclude_transaction_id:
            continue
        if reservations_compatible(
            target=target,
            paths=current_paths,
            paths_known=paths_known,
            other=other,
        ):
            continue
        raise ReservationConflict(_conflict_message(str(target or "").strip(), current_paths, other, paths_known=paths_known))
