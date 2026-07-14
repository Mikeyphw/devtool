from __future__ import annotations

"""Cleanup helpers for devtool-managed caches and transient state."""

from pathlib import Path
import shutil


def default_cache_root() -> Path:
    return Path.home() / ".cache" / "devtool"


def default_state_root() -> Path:
    return Path.home() / ".local" / "state" / "devtool"


def default_share_root() -> Path:
    return Path.home() / ".local" / "share" / "devtool"


def _remove_path(path: Path, *, dry_run: bool) -> bool:
    if not path.exists():
        return False
    if dry_run:
        return True
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
    return True


def cleanup_devtool_caches(
    *,
    cache_root: Path | None = None,
    state_root: Path | None = None,
    share_root: Path | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Remove devtool-owned caches and transient state.

    The cleanup stays scoped to devtool-managed directories so it is safe to
    run without repo-specific knowledge. Project-local build outputs remain
    handled by the normal ``clean`` task for each runner.
    """

    cache_root = cache_root or default_cache_root()
    state_root = state_root or default_state_root()
    share_root = share_root or default_share_root()

    targets = [
        cache_root,
        state_root / "logs",
        share_root / "transactions",
        share_root / "performance-latest.json",
    ]
    removed: list[str] = []
    for target in targets:
        if _remove_path(target, dry_run=dry_run):
            removed.append(str(target))
    return removed
