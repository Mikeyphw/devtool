from __future__ import annotations

import contextlib
import fcntl
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Iterable

from .reservations import (
    Reservation,
    ReservationConflict,
    assert_reservation_available,
    normalize_paths,
    repo_key,
    reservation_coordinator,
    reservations_compatible,
)
from .xdg import state_dir


def _state_home() -> Path:
    # Repository locks must live under one stable Devtool state root.
    # XDG_RUNTIME_DIR is session-scoped and may differ between processes.
    return state_dir()


def lock_path(repo_root: Path) -> Path:
    """Legacy repository-wide compatibility lock.

    New Devtool processes hold this lock shared so they can coexist when their
    reservations are proven disjoint. Older Devtool releases take it exclusive,
    which keeps mixed-version execution safely serialized.
    """
    return _state_home() / "locks" / repo_key(repo_root) / "run.lock"


def _run_lock_path(repo_root: Path, run_id: str) -> Path:
    safe_id = "".join(ch for ch in str(run_id) if ch.isalnum() or ch in "._-") or f"pid-{os.getpid()}"
    return _state_home() / "locks" / repo_key(repo_root) / "runs" / f"{safe_id}.lock"


@dataclass(slots=True)
class SingleRunLock:
    """Reserve one target/write-set in a repository.

    Different targets may run concurrently only when both write sets are known
    and disjoint. Same-target, overlapping, or unknown reservations remain
    serialized. This intentionally fails closed.
    """

    repo_root: Path
    command: str = "devtool"
    run_id: str = ""
    log_path: str = ""
    target: str = ""
    reserved_paths: tuple[str, ...] | list[str] = ()
    paths_known: bool = False
    owned_transaction_id: str = ""
    _handle: IO[str] | None = None
    _legacy_handle: IO[str] | None = None
    _payload: dict[str, Any] | None = None
    _path: Path | None = None

    def acquire(self) -> "SingleRunLock":
        repo_root = self.repo_root.resolve()
        self.repo_root = repo_root
        self.reserved_paths = normalize_paths(self.reserved_paths) if self.paths_known else ()

        # Compatibility gate: old releases request LOCK_EX here, while new
        # releases share it and coordinate through scoped reservations below.
        legacy_path = lock_path(repo_root)
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy = legacy_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(legacy.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            legacy.seek(0)
            existing = legacy.read().strip()
            detail = ""
            if existing:
                try:
                    payload = json.loads(existing)
                    if isinstance(payload, dict):
                        detail = f" (pid {payload.get('pid', '?')}, {payload.get('command', 'devtool')})"
                except Exception:
                    pass
            legacy.close()
            raise BlockingIOError(f"Another devtool run is already active for this repository{detail}.")

        run_path = _run_lock_path(repo_root, self.run_id)
        run_path.parent.mkdir(parents=True, exist_ok=True)
        handle: IO[str] | None = None
        try:
            with reservation_coordinator(repo_root):
                assert_reservation_available(
                    repo_root,
                    target=self.target,
                    paths=self.reserved_paths,
                    paths_known=self.paths_known,
                    exclude_run_id=self.run_id,
                    exclude_transaction_id=self.owned_transaction_id,
                )
                handle = run_path.open("a+", encoding="utf-8")
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise ReservationConflict(f"Run reservation {self.run_id or run_path.stem!r} is already active.") from exc
                payload = {
                    "schema_version": 2,
                    "pid": os.getpid(),
                    "run_id": self.run_id,
                    "command": self.command,
                    "repo_root": str(repo_root),
                    "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "status": "running",
                    "log_path": self.log_path,
                    "target": str(self.target or ""),
                    "reserved_paths": list(self.reserved_paths),
                    "paths_known": bool(self.paths_known),
                    "owned_transaction_id": str(self.owned_transaction_id or ""),
                }
                self._write_payload(handle, payload)
        except (ReservationConflict, OSError) as exc:
            if handle is not None:
                with contextlib.suppress(OSError):
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            with contextlib.suppress(OSError):
                fcntl.flock(legacy.fileno(), fcntl.LOCK_UN)
            legacy.close()
            if isinstance(exc, ReservationConflict):
                raise BlockingIOError(str(exc)) from exc
            raise

        self._legacy_handle = legacy
        self._handle = handle
        self._payload = payload
        self._path = run_path
        return self

    @staticmethod
    def _write_payload(handle: IO[str], payload: dict[str, Any]) -> None:
        handle.seek(0)
        handle.truncate(0)
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())

    def update_metadata(self, **values: Any) -> None:
        handle = self._handle
        if handle is None:
            return
        payload = dict(self._payload or {})
        payload.update({key: value for key, value in values.items() if value is not None})
        self._write_payload(handle, payload)
        self._payload = payload
        if "target" in values and values["target"] is not None:
            self.target = str(values["target"])
        if "reserved_paths" in values and values["reserved_paths"] is not None:
            self.reserved_paths = normalize_paths(values["reserved_paths"])
        if "paths_known" in values and values["paths_known"] is not None:
            self.paths_known = bool(values["paths_known"])

    def allows_transaction(self, transaction: dict[str, Any]) -> bool:
        txid = str(transaction.get("transaction_id") or "")
        if self.owned_transaction_id and txid == self.owned_transaction_id:
            return True
        raw_paths = transaction.get("reserved_paths")
        if not isinstance(raw_paths, list) and isinstance(transaction.get("affected_paths"), list):
            raw_paths = [
                str(item.get("path") or "")
                for item in transaction.get("affected_paths") or []
                if isinstance(item, dict) and str(item.get("path") or "")
            ]
        other = Reservation(
            source="transaction",
            reservation_id=txid or str(transaction.get("path") or "transaction"),
            target=str(transaction.get("target") or ""),
            paths=normalize_paths(raw_paths or []) if int(transaction.get("schema_version") or 0) >= 3 else (),
            paths_known=int(transaction.get("schema_version") or 0) >= 3 and isinstance(raw_paths, list),
            status=str(transaction.get("status") or ""),
            run_id=str(transaction.get("run_id") or ""),
            transaction_id=txid,
        )
        return reservations_compatible(
            target=self.target,
            paths=self.reserved_paths,
            paths_known=self.paths_known,
            other=other,
        )

    def release(self) -> None:
        handle, self._handle = self._handle, None
        legacy, self._legacy_handle = self._legacy_handle, None
        path, self._path = self._path, None
        self._payload = None
        if handle is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
        if path is not None:
            with contextlib.suppress(OSError):
                path.unlink()
        if legacy is not None:
            try:
                fcntl.flock(legacy.fileno(), fcntl.LOCK_UN)
            finally:
                legacy.close()

    def __enter__(self) -> "SingleRunLock":
        return self.acquire()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()


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


def _lock_hold_status(path: Path) -> bool | None:
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


def _candidate_lock_state_roots(extra_roots: Iterable[Path] = ()) -> list[Path]:
    roots: list[Path] = []

    def add(path: Path) -> None:
        expanded = path.expanduser()
        if expanded not in roots:
            roots.append(expanded)

    add(_state_home())
    add(state_dir())
    if not os.environ.get("DEVTOOL_XDG_DIR", "").strip():
        runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
        if runtime:
            add(Path(runtime) / "devtool")
        with contextlib.suppress(OSError):
            add(Path("/run/user") / str(os.getuid()) / "devtool")
    for root in extra_roots:
        add(root)
    return roots


def _record_if_active(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        pid = int(payload.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    hold_status = _lock_hold_status(path)
    if hold_status is False:
        return None
    if hold_status is None and not _pid_is_alive(pid):
        return None
    record = dict(payload)
    record["lock_path"] = str(path)
    return record


def active_run_lock_records(extra_roots: Iterable[Path] = ()) -> list[dict[str, Any]]:
    """Return metadata for held legacy or scoped repository run locks."""
    records: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for state_root in _candidate_lock_state_roots(extra_roots):
        locks_root = state_root / "locks"
        try:
            paths = [*sorted(locks_root.glob("*/runs/*.lock")), *sorted(locks_root.glob("*/run.lock"))]
        except OSError:
            continue
        for path in paths:
            if path in seen_paths:
                continue
            seen_paths.add(path)
            # A new Devtool holds run.lock shared only as a compatibility gate;
            # its stale legacy payload must not appear as another active run.
            if path.name == "run.lock":
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) >= 2:
                    continue
                try:
                    pid = int(payload.get("pid") or 0)
                except (TypeError, ValueError):
                    pid = 0
                if not _pid_is_alive(pid):
                    continue
            record = _record_if_active(path)
            if record is not None:
                records.append(record)
    return records
