from __future__ import annotations

"""Safe repository housekeeping for historical Devtool backup files."""

from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import shutil
import os
from typing import Any

_BACKUP_FILE_MARKERS = (
    ".before-",
    ".pre-",
)
_BACKUP_FILE_SUFFIXES = (".bak", ".backup", ".orig")
_BACKUP_DIR_PREFIXES = (
    ".devtool-python-autofix-backup-",
    ".diagnostic-surface-backup-",
    ".warning-fix-backup-",
    ".connected-test-skip-backup-",
    ".no-device-config-cache-fix-",
    ".transitive-connected-skip-backup-",
)


@dataclass(frozen=True)
class CleanupMove:
    source: str
    destination: str
    kind: str


def _history_root(repo_root: Path) -> Path:
    return repo_root / ".devtool" / "history" / "backups"


def _is_backup_candidate(path: Path, repo_root: Path) -> bool:
    try:
        relative = path.relative_to(repo_root)
    except ValueError:
        return False
    if not relative.parts:
        return False
    if relative.parts[0] in {".git", ".devtool"}:
        return False
    name = path.name
    if path.is_dir():
        if any(name.startswith(prefix) for prefix in _BACKUP_DIR_PREFIXES):
            return True
        # Historical migration helpers consistently created hidden top-level
        # backup directories. Recognize future variants without treating an
        # arbitrary nested directory containing the word "backup" as disposable.
        if path.parent == repo_root and name.startswith(".") and ("backup" in name.lower() or ".before-" in name.lower() or ".pre-" in name.lower()):
            return True
    if path.is_file():
        if any(marker in name for marker in _BACKUP_FILE_MARKERS):
            return True
        if name.endswith(_BACKUP_FILE_SUFFIXES):
            return True
    return False


def discover_backup_candidates(repo_root: Path) -> list[Path]:
    repo_root = repo_root.expanduser().resolve()
    candidates: list[Path] = []
    for current, dirnames, filenames in os.walk(repo_root, topdown=True, followlinks=False):
        current_path = Path(current)
        if current_path == repo_root:
            dirnames[:] = [name for name in dirnames if name not in {".git", ".devtool"}]
        selected_dirs: list[str] = []
        for name in list(dirnames):
            path = current_path / name
            if _is_backup_candidate(path, repo_root):
                candidates.append(path)
                selected_dirs.append(name)
        # Do not descend into a directory that will be archived as one unit.
        if selected_dirs:
            dirnames[:] = [name for name in dirnames if name not in selected_dirs]
        for name in filenames:
            path = current_path / name
            if _is_backup_candidate(path, repo_root):
                candidates.append(path)
    return sorted(candidates, key=lambda item: str(item.relative_to(repo_root)))


def _unique_destination(base: Path) -> Path:
    if not base.exists():
        return base
    counter = 2
    while True:
        candidate = base.with_name(f"{base.name}.{counter}")
        if not candidate.exists():
            return candidate
        counter += 1


def archive_backup_candidates(repo_root: Path, *, dry_run: bool = False, stamp: str | None = None) -> list[CleanupMove]:
    repo_root = repo_root.expanduser().resolve()
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination_root = _history_root(repo_root) / stamp
    moves: list[CleanupMove] = []
    for source in discover_backup_candidates(repo_root):
        relative = source.relative_to(repo_root)
        destination = _unique_destination(destination_root / relative)
        moves.append(CleanupMove(str(relative), str(destination.relative_to(repo_root)), "directory" if source.is_dir() else "file"))
        if dry_run:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
    return moves


def prune_backup_history(repo_root: Path, *, older_than_days: float, dry_run: bool = False) -> list[str]:
    if older_than_days <= 0:
        return []
    root = _history_root(repo_root)
    if not root.is_dir():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=float(older_than_days))
    removed: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            mtime = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime >= cutoff:
            continue
        removed.append(str(child.relative_to(repo_root)))
        if not dry_run:
            shutil.rmtree(child)
    return removed


def cleanup_repository(repo_root: Path, *, dry_run: bool = False, prune_history_days: float = 0.0) -> dict[str, Any]:
    repo_root = repo_root.expanduser().resolve()
    moves = archive_backup_candidates(repo_root, dry_run=dry_run)
    pruned = prune_backup_history(repo_root, older_than_days=prune_history_days, dry_run=dry_run)
    return {
        "repo": str(repo_root),
        "dry_run": dry_run,
        "archived": [asdict(item) for item in moves],
        "archived_count": len(moves),
        "pruned_history": pruned,
        "pruned_count": len(pruned),
    }
