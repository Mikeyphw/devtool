"""Portable, fail-closed interprocess file locking.

POSIX reuses native flock unchanged. Windows uses exclusive byte-range locks
for BOTH shared and exclusive requests: safe serialization at the cost of
parallelism; Windows has no POSIX flock compatibility promise.
"""
from __future__ import annotations
import os
import errno

LOCK_SH = 1
LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8

if os.name != "nt":
    import fcntl as _native
    LOCK_SH = _native.LOCK_SH
    LOCK_EX = _native.LOCK_EX
    LOCK_NB = _native.LOCK_NB
    LOCK_UN = _native.LOCK_UN


def _windows_flock(fd: int, operation: int, *, backend: object) -> None:
    """Use a fixed byte at offset zero; file size and append position vary."""
    os.lseek(fd, 0, os.SEEK_SET)
    if operation & LOCK_UN:
        backend.locking(fd, backend.LK_UNLCK, 1)
        return
    if not operation & (LOCK_SH | LOCK_EX):
        raise ValueError("unknown host lock operation")
    # Neither no-op nor user-space-only locks preserve cross-process safety.
    mode = backend.LK_NBLCK if operation & LOCK_NB else backend.LK_LOCK
    try:
        backend.locking(fd, mode, 1)
    except OSError as exc:
        if operation & LOCK_NB and getattr(exc, "errno", None) in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            raise BlockingIOError(errno.EAGAIN, "another process holds the host lock") from exc
        raise


def flock(fd: int, operation: int) -> None:
    if os.name != "nt":
        _native.flock(fd, operation)
    else:
        import msvcrt
        _windows_flock(fd, operation, backend=msvcrt)
