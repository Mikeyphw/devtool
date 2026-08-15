from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Iterable

from .xdg import state_dir


def _state_home() -> Path:
    # Repository locks must live under one stable Devtool state root.
    # XDG_RUNTIME_DIR is session-scoped and may differ between processes.
    return state_dir()

def _repo_key(repo_root: Path) -> str:
    resolved = str(repo_root.resolve())
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", repo_root.resolve().name or "repo").strip(".-") or "repo"
    digest = hashlib.sha256(resolved.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return f"{slug}-{digest}"


def lock_path(repo_root: Path) -> Path:
    return _state_home() / "locks" / _repo_key(repo_root) / "run.lock"


@dataclass(slots=True)
class SingleRunLock:
    """Serialize devtool operations for one repository.

    There are no path scopes, parallel-run modes, or wait policies. A second
    invocation fails clearly while the current run owns the lock.
    """

    repo_root: Path
    command: str = "devtool"
    run_id: str = ""
    log_path: str = ""
    _handle: IO[str] | None = None
    _payload: dict[str, Any] | None = None

    def acquire(self) -> "SingleRunLock":
        path = lock_path(self.repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.seek(0)
            existing = handle.read().strip()
            detail = ""
            if existing:
                try:
                    payload: dict[str, Any] = json.loads(existing)
                    detail = f" (pid {payload.get('pid', '?')}, {payload.get('command', 'devtool')})"
                except Exception:
                    detail = ""
            handle.close()
            raise BlockingIOError(f"Another devtool run is already active for this repository{detail}.")
        payload = {
            "schema_version": 1,
            "pid": os.getpid(),
            "run_id": self.run_id,
            "command": self.command,
            "repo_root": str(self.repo_root.resolve()),
            "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "status": "running",
            "log_path": self.log_path,
        }
        self._write_payload(handle, payload)
        self._handle = handle
        self._payload = payload
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
        """Update metadata stored in the authoritative active-run lock."""
        handle = self._handle
        if handle is None:
            return
        payload = dict(self._payload or {})
        payload.update({key: value for key, value in values.items() if value is not None})
        self._write_payload(handle, payload)
        self._payload = payload

    def release(self) -> None:
        handle, self._handle = self._handle, None
        self._payload = None
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

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
    """Return True when held, False when definitely free, or None if unreadable."""
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


def active_run_lock_records(extra_roots: Iterable[Path] = ()) -> list[dict[str, Any]]:
    """Return authoritative metadata for currently held repository run locks."""
    records: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for state_root in _candidate_lock_state_roots(extra_roots):
        locks_root = state_root / "locks"
        try:
            paths = sorted(locks_root.glob("*/run.lock"))
        except OSError:
            continue
        for path in paths:
            if path in seen_paths:
                continue
            seen_paths.add(path)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            try:
                pid = int(payload.get("pid") or 0)
            except (TypeError, ValueError):
                pid = 0
            hold_status = _lock_hold_status(path)
            if hold_status is False:
                # The metadata file intentionally survives release, so a live
                # or recycled PID must never resurrect a completed run.
                continue
            if hold_status is None and not _pid_is_alive(pid):
                # Cross-user readers may be unable to open the lock. In that
                # one case, PID existence is the best available fallback.
                continue
            record = dict(payload)
            record["lock_path"] = str(path)
            records.append(record)
    return records
