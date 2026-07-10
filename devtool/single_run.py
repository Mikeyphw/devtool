from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any


def _state_home() -> Path:
    override = os.environ.get("DEVTOOL_XDG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime:
        return Path(runtime).expanduser() / "devtool"
    state = os.environ.get("XDG_STATE_HOME", "").strip()
    if state:
        return Path(state).expanduser() / "devtool"
    return Path.home() / ".local" / "state" / "devtool"


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
    _handle: IO[str] | None = None

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
        }
        handle.seek(0)
        handle.truncate(0)
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        self._handle = handle
        return self

    def release(self) -> None:
        handle, self._handle = self._handle, None
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
