"""Private, bounded, producer-owned raw output spool for Go JSON tests.

The producer is the *only* reader of the process stdout descriptor.  The
consumer performs positional reads using its own file descriptor, so slow
presentation cannot exert backpressure on the child until the explicit disk
capacity limit is reached.  No JSON decoding, UI work or fsync runs in the
producer thread.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import select
import tempfile
import threading
import time
from typing import BinaryIO

DEFAULT_MAX_BYTES = 256 * 1024 * 1024
MAX_ALLOWED_BYTES = 4 * 1024 * 1024 * 1024


def capacity_from_env(env: dict[str, str]) -> int:
    value = str(env.get("DEVTOOL_GO_OUTPUT_SPOOL_MAX_BYTES", "") or "").strip()
    if not value:
        return DEFAULT_MAX_BYTES
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError("invalid DEVTOOL_GO_OUTPUT_SPOOL_MAX_BYTES") from exc
    if not 1024 <= parsed <= MAX_ALLOWED_BYTES:
        raise ValueError("DEVTOOL_GO_OUTPUT_SPOOL_MAX_BYTES outside allowed range")
    return parsed


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class GoOutputSpool:
    """Append-only file with atomic per-chunk commit visibility."""

    def __init__(self, *, max_bytes: int = DEFAULT_MAX_BYTES, directory: str | None = None):
        if not 1024 <= max_bytes <= MAX_ALLOWED_BYTES:
            raise ValueError("invalid Go spool capacity")
        fd, filename = tempfile.mkstemp(prefix="devtool-go-spool-", suffix=".raw", dir=directory)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            else:
                os.chmod(filename, 0o600)
            reader = os.open(filename, os.O_RDONLY)
        except BaseException:
            os.close(fd)
            os.unlink(filename)
            raise
        self._writer = fd
        self._reader = reader
        self._path = Path(filename)
        self._max_bytes = max_bytes
        self._cond = threading.Condition()
        self._committed = 0
        self._consumed = 0
        self._drained = 0
        self._last_drain_monotonic = time.monotonic()
        self._peak_backlog = 0
        self._eof = False
        self._error: str | None = None
        self._drain_at: str | None = None
        self._present_at: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False

    def start(self, stdout: BinaryIO) -> None:
        if self._thread is not None:
            raise RuntimeError("Go spool producer already started")
        self._thread = threading.Thread(target=self._produce, args=(stdout,), name="devtool-go-stdout-drain", daemon=True)
        self._thread.start()

    def _produce(self, stdout: BinaryIO) -> None:
        fd = stdout.fileno()
        old_flags = None
        kernel32 = None
        native_handle = None
        try:
            if os.name == "nt":
                import ctypes
                import msvcrt
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.PeekNamedPipe.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                    ctypes.c_uint, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint), ctypes.c_void_p]
                kernel32.PeekNamedPipe.restype = ctypes.c_int
                native_handle = msvcrt.get_osfhandle(fd)
            if os.name == "posix":
                import fcntl
                old_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, old_flags | os.O_NONBLOCK)
            while not self._stop.is_set():
                if os.name == "posix":
                    readable, _, _ = select.select([fd], [], [], 0.10)
                    if not readable:
                        continue
                if kernel32 is not None:
                    # Windows anonymous pipes cannot be passed to select().
                    # PeekNamedPipe prevents an uninterruptible blocking os.read
                    # when a grandchild inherits stdout after the main child exits.
                    import ctypes
                    available = ctypes.c_uint(0)
                    if not kernel32.PeekNamedPipe(native_handle, None, 0, None,
                                                  ctypes.byref(available), None):
                        error_code = ctypes.get_last_error()
                        if error_code in {109, 232}:  # ERROR_BROKEN_PIPE / NO_DATA
                            with self._cond:
                                self._eof = True
                                self._drain_at = utc_now()
                                self._cond.notify_all()
                            return
                        raise OSError(error_code, "PeekNamedPipe failed")
                    if available.value == 0:
                        self._stop.wait(0.05)
                        continue
                    requested = min(available.value, 65536)
                else:
                    requested = 65536
                try:
                    chunk = os.read(fd, requested)
                except BlockingIOError:
                    continue
                if not chunk:
                    with self._cond:
                        self._eof = True
                        self._drain_at = utc_now()
                        self._cond.notify_all()
                    return
                with self._cond:
                    self._drained += len(chunk)
                    self._last_drain_monotonic = time.monotonic()
                    if self._committed + len(chunk) > self._max_bytes:
                        self._error = f"capacity_exceeded: hard_limit={self._max_bytes} bytes"
                        self._drain_at = utc_now()
                        self._cond.notify_all()
                        return
                # Finish the entire write *before* announcing the committed offset.
                offset = 0
                while offset < len(chunk):
                    count = os.write(self._writer, chunk[offset:])
                    if count <= 0:
                        raise OSError("Go spool write made no forward progress")
                    offset += count
                with self._cond:
                    self._committed += len(chunk)
                    self._peak_backlog = max(self._peak_backlog, self._committed - self._consumed)
                    self._cond.notify_all()
        except BaseException as exc:
            with self._cond:
                if self._error is None and not self._stop.is_set():
                    self._error = f"{type(exc).__name__}: {exc}"
                self._drain_at = utc_now()
                self._cond.notify_all()
        finally:
            if old_flags is not None:
                try:
                    import fcntl
                    fcntl.fcntl(fd, fcntl.F_SETFL, old_flags)
                except (OSError, ValueError):
                    pass

    def read_available(self, size: int = 65536, wait: float = 0.10) -> bytes:
        with self._cond:
            if self._committed <= self._consumed and not (self._eof or self._error or self._stop.is_set()):
                self._cond.wait(timeout=wait)
            length = min(size, self._committed - self._consumed)
            start = self._consumed
        if length <= 0:
            return b""
        # Reader offset is independent of the producer's append offset.
        if hasattr(os, "pread"):
            chunk = os.pread(self._reader, length, start)
        else:
            os.lseek(self._reader, start, os.SEEK_SET)
            chunk = os.read(self._reader, length)
        if not chunk:
            self.fail("committed_spool_bytes_unreadable")
            return b""
        with self._cond:
            self._consumed += len(chunk)
            if self._eof and self._consumed == self._committed:
                self._present_at = utc_now()
        return chunk

    def fail(self, reason: str) -> None:
        with self._cond:
            if self._error is None:
                self._error = reason
            self._cond.notify_all()

    def state(self) -> dict[str, object]:
        with self._cond:
            return {"bytes_drained": self._drained, "spool_bytes": self._committed,
                    "peak_backlog_bytes": self._peak_backlog,
                    "bytes_consumed": self._consumed,
                    "pipe_eof_observed": self._eof,
                    "drain_completed_at": self._drain_at,
                    "presentation_completed_at": self._present_at,
                    "transport_error": self._error,
                    "capacity_bytes": self._max_bytes}

    def producer_activity_at(self) -> float:
        with self._cond:
            return self._last_drain_monotonic

    def mark_presented(self) -> None:
        with self._cond:
            self._present_at = utc_now()

    def close(self) -> None:
        if self._closed:
            return
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                self.fail("producer_thread_did_not_stop")
                # Do not close an FD that is still being used by the producer.
                return
        os.close(self._reader)
        os.close(self._writer)
        self._path.unlink(missing_ok=True)
        self._closed = True

    @property
    def temporary_path(self) -> Path:
        return self._path

    def __enter__(self) -> "GoOutputSpool":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
