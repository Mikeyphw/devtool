from __future__ import annotations

"""Safe repository housekeeping for historical Devtool backup files."""

from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import shutil
import os
import subprocess
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




def _git_path_sets(repo_root: Path) -> tuple[bool, set[str], set[str]]:
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd=repo_root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False, set(), set()
    if inside.returncode != 0:
        return False, set(), set()
    try:
        tracked_proc = subprocess.run(
            ["git", "ls-files", "-z"], cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False
        )
        ignored_proc = subprocess.run(
            ["git", "ls-files", "-z", "--others", "--ignored", "--exclude-standard"],
            cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True, set(), set()
    decode = lambda payload: {
        raw.decode("utf-8", "surrogateescape").replace("\\", "/")
        for raw in bytes(payload or b"").split(b"\0") if raw
    }
    tracked = decode(tracked_proc.stdout) if tracked_proc.returncode == 0 else set()
    ignored = decode(ignored_proc.stdout) if ignored_proc.returncode == 0 else set()
    return True, tracked, ignored


def _path_or_descendant_in(rel: str, paths: set[str]) -> bool:
    if rel in paths:
        return True
    prefix = rel.rstrip("/") + "/"
    return any(item.startswith(prefix) for item in paths)


def _known_devtool_backup(path: Path) -> bool:
    return path.is_dir() and any(path.name.startswith(prefix) for prefix in _BACKUP_DIR_PREFIXES)


def _candidate_cleanup_safe(path: Path, repo_root: Path, *, is_git: bool, tracked: set[str], ignored: set[str]) -> bool:
    try:
        rel = path.relative_to(repo_root).as_posix()
    except ValueError:
        return False
    if _path_or_descendant_in(rel, tracked):
        return False
    if _known_devtool_backup(path):
        return True
    # Generic .bak/.orig/.before-* naming is only a heuristic.  In Git repos,
    # require explicit ignore ownership; outside Git, preserve unknown data.
    return is_git and _path_or_descendant_in(rel, ignored)

def discover_backup_candidates(repo_root: Path) -> list[Path]:
    repo_root = repo_root.expanduser().resolve()
    candidates: list[Path] = []
    is_git, tracked, ignored = _git_path_sets(repo_root)
    for current, dirnames, filenames in os.walk(repo_root, topdown=True, followlinks=False):
        current_path = Path(current)
        if current_path == repo_root:
            dirnames[:] = [name for name in dirnames if name not in {".git", ".devtool"}]
        selected_dirs: list[str] = []
        for name in list(dirnames):
            path = current_path / name
            if _is_backup_candidate(path, repo_root) and _candidate_cleanup_safe(path, repo_root, is_git=is_git, tracked=tracked, ignored=ignored):
                candidates.append(path)
                selected_dirs.append(name)
        # Do not descend into a directory that will be archived as one unit.
        if selected_dirs:
            dirnames[:] = [name for name in dirnames if name not in selected_dirs]
        for name in filenames:
            path = current_path / name
            if _is_backup_candidate(path, repo_root) and _candidate_cleanup_safe(path, repo_root, is_git=is_git, tracked=tracked, ignored=ignored):
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
    if not dry_run:
        # Cleanup must never race an artifact transaction or consume recovery
        # material while rollback/validation still owns repository state.
        from .transactions import find_active_transactions
        active = find_active_transactions(repo_root, include_live=True)
        if active:
            raise RuntimeError(f"Refusing repository cleanup while an artifact transaction is active: {active[0]}")
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
