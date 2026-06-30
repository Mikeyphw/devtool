from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable

IGNORED = {".git", "bin", "obj", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache"}


def snapshot_mtimes(repo_root: Path) -> dict[str, float]:
    mtimes: dict[str, float] = {}
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED]
        base = Path(dirpath)
        for filename in filenames:
            path = base / filename
            try:
                mtimes[str(path.relative_to(repo_root))] = path.stat().st_mtime
            except OSError:
                pass
    return mtimes


def watch_loop(repo_root: Path, run_once: Callable[[], int], *, interval: float = 1.5) -> int:
    last_rc = run_once()
    previous = snapshot_mtimes(repo_root)
    try:
        while True:
            time.sleep(max(0.2, interval))
            current = snapshot_mtimes(repo_root)
            if current != previous:
                previous = current
                last_rc = run_once()
    except KeyboardInterrupt:
        return last_rc
