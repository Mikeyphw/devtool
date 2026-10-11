"""Recoverable, single-worker log rotation off the Termux foreground path.

The foreground always writes/fsyncs the authoritative XDG log first.
Only the existing logstore._rotate_log_root -> LIFE path can delete logs.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

_REQUEST = ".devtool-retention-request.json"
_SIGNAL_LOCK = ".devtool-retention-signal.lock"
_WORKER_LOCK = ".devtool-retention-worker.lock"


def _enabled() -> bool:
    mode = os.environ.get("DEVTOOL_LOG_RETENTION_MODE", "auto").strip().lower()
    if mode in {"sync", "synchronous"}:
        return False
    if mode in {"async", "asynchronous"}:
        return True
    # An ordinary Linux test that simulates PREFIX must retain synchronous
    # semantics. Actual Termux Python (including its venvs) lives here.
    return mode == "auto" and os.path.realpath(sys.executable).startswith("/data/data/com.termux/")


def _lock(path: Path, *, nonblocking: bool = False) -> int | None:
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise
    return fd


def _unlock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _request_path(root: Path) -> Path:
    return root / _REQUEST


def _read_request(root: Path) -> dict[str, str] | None:
    try:
        data = json.loads(_request_path(root).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            return {"id": data["id"], "protected": str(data.get("protected", ""))}
    except (OSError, ValueError):
        pass
    return None


def _persist_request(root: Path, protected: Path | None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    # Store only a basename, and only for a file actually within this root.
    filename = ""
    if protected is not None and protected.parent.resolve(strict=False) == root.resolve(strict=False):
        filename = protected.name
    fd = _lock(root / _SIGNAL_LOCK)
    assert fd is not None
    try:
        temp = root / (".devtool-retention-" + uuid.uuid4().hex + ".tmp")
        try:
            with temp.open("x", encoding="utf-8") as handle:
                json.dump({"id": uuid.uuid4().hex, "protected": filename}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, _request_path(root))
            # The pending request survives an abrupt Termux process exit.
            from .logstore import _fsync_directory
            _fsync_directory(root)
        finally:
            temp.unlink(missing_ok=True)
    finally:
        _unlock(fd)


def schedule_retention(root: Path, *, protected: Path | None) -> bool:
    """Return True when the request is durable and an async worker was launched.

    A failure returns False and the caller runs the original synchronous policy.
    """
    if not _enabled() or os.environ.get("DEVTOOL_LOG_RETENTION_WORKER") == "1":
        return False
    try:
        _persist_request(root, protected)
        env = os.environ.copy()
        package_root = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = package_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["DEVTOOL_LOG_RETENTION_WORKER"] = "1"
        subprocess.Popen(
            [sys.executable, "-m", "devtool.log_retention_worker", str(root)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, start_new_session=True, env=env,
        )
        return True
    except (OSError, ValueError):
        return False


def drain_requests(root: Path) -> int:
    """Single worker drains every observed request, including concurrent arrivals."""
    from .logstore import _rotate_log_root, _fsync_directory

    root = root.expanduser().resolve(strict=False)
    if not root.is_dir():
        return 1
    worker_fd = _lock(root / _WORKER_LOCK, nonblocking=True)
    if worker_fd is None:
        # Another worker owns the rotation; it must observe the latest request.
        return 0
    try:
        while True:
            signal_fd = _lock(root / _SIGNAL_LOCK)
            assert signal_fd is not None
            try:
                request = _read_request(root)
            finally:
                _unlock(signal_fd)
            if request is None:
                return 0
            candidate = root / request["protected"] if request["protected"] else None
            if candidate is not None and (candidate.parent != root or candidate.suffix != ".log"):
                candidate = None
            _rotate_log_root(root, protected=candidate)
            signal_fd = _lock(root / _SIGNAL_LOCK)
            assert signal_fd is not None
            try:
                current = _read_request(root)
                if current is not None and current["id"] == request["id"]:
                    _request_path(root).unlink(missing_ok=True)
                    _fsync_directory(root)
                    return 0
            finally:
                _unlock(signal_fd)
            # New writes landed during retention: re-evaluate the newest view.
    finally:
        _unlock(worker_fd)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python -m devtool.log_retention_worker LOG_DIR", file=sys.stderr)
        return 2
    return drain_requests(Path(sys.argv[1]))


if __name__ == "__main__":
    raise SystemExit(main())
