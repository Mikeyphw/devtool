from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Callable

# Watch exclusions are repository-relative, not basename-wide. A legitimate
# source directory such as src/bin must remain observable even when a top-level
# build output directory named bin is ignored.
_IGNORED_TOP_LEVEL = {
    ".git", ".devtool", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "bin", "obj", "build", "dist",
}
_IGNORED_SUFFIXES = {".pyc", ".pyo"}


def _ignored_relative(relative: Path) -> bool:
    parts = relative.parts
    if not parts:
        return False
    if parts[0] in _IGNORED_TOP_LEVEL:
        return True
    if relative.suffix.lower() in _IGNORED_SUFFIXES:
        return True
    return False


def _content_signature(path: Path) -> str:
    digest = hashlib.blake2b(digest_size=16)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_mtimes(repo_root: Path) -> dict[str, tuple[int, int, int, str]]:
    """Return a content-authoritative watch snapshot.

    The historical function name is retained for compatibility. Identity now
    includes inode, byte size, nanosecond mtime and a content signature, so an
    editor that preserves timestamps (or replaces a file with a same-size
    inode) still triggers a rerun.
    """
    root = repo_root.resolve()
    snapshot: dict[str, tuple[int, int, int, str]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        relative_dir = base.relative_to(root)
        dirnames[:] = [
            name for name in dirnames
            if not _ignored_relative(relative_dir / name)
        ]
        for filename in filenames:
            path = base / filename
            relative = path.relative_to(root)
            if _ignored_relative(relative):
                continue
            try:
                stat = path.stat()
                if not path.is_file():
                    continue
                snapshot[relative.as_posix()] = (
                    int(getattr(stat, "st_ino", 0)),
                    int(stat.st_size),
                    int(stat.st_mtime_ns),
                    _content_signature(path),
                )
            except OSError:
                # A file disappearing during a scan is represented by absence in
                # the completed snapshot and will be observed on the next pass.
                continue
    return snapshot


def watch_loop(repo_root: Path, run_once: Callable[[], int], *, interval: float = 1.5) -> int:
    try:
        last_rc = run_once()
        if last_rc == 130:
            return 130
        # Snapshot only after the run has quiesced. Generated Devtool output is
        # excluded explicitly, and any other build mutation completed by the run
        # becomes the new baseline instead of self-triggering another invocation.
        previous = snapshot_mtimes(repo_root)
        while True:
            time.sleep(max(0.2, interval))
            current = snapshot_mtimes(repo_root)
            if current != previous:
                last_rc = run_once()
                if last_rc == 130:
                    return 130
                previous = snapshot_mtimes(repo_root)
    except KeyboardInterrupt:
        # Ctrl-C is itself the final result of this watch invocation. Returning a
        # previous successful/failed run would falsely report an interrupted watch.
        return 130
