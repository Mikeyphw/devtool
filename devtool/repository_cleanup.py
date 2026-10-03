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
    """Discover archival candidates through LIFE-X03 ownership planning.

    Backup-shaped names are only discovery hints. Known Devtool backup
    directories have explicit producer ownership; generic ``.bak``/``.orig``
    resources remain discovery-only even when ignored.  Ignore state is not
    producer ownership. Tracked/user data and active lifecycle roots remain protected by the shared
    cleanup planner.
    """
    from .cleanup_lifecycle import (
        CleanupScope,
        build_cleanup_ownership_view,
        compile_cleanup_plan,
        descriptor_from_path,
    )

    repo_root = repo_root.expanduser().resolve()
    discovered: list[Path] = []
    for current, dirnames, filenames in os.walk(repo_root, topdown=True, followlinks=False):
        current_path = Path(current)
        if current_path == repo_root:
            dirnames[:] = [name for name in dirnames if name not in {".git", ".devtool"}]
        selected_dirs: list[str] = []
        for name in list(dirnames):
            path = current_path / name
            if _is_backup_candidate(path, repo_root):
                discovered.append(path)
                selected_dirs.append(name)
        if selected_dirs:
            dirnames[:] = [name for name in dirnames if name not in selected_dirs]
        for name in filenames:
            path = current_path / name
            if _is_backup_candidate(path, repo_root):
                discovered.append(path)

    descriptors = []
    by_resource: dict[str, Path] = {}
    for path in discovered:
        # Only exact producer-owned Devtool backup prefixes grant managed
        # ownership. A user directory merely containing "backup", ".before-",
        # or ".pre-" remains heuristic discovery and cannot be moved/deleted.
        explicit = _known_devtool_backup(path)
        descriptor = descriptor_from_path(
            path,
            owner="devtool-legacy-backup" if explicit else "legacy-backup-discovery",
            provider="repository-cleanup",
            category="backup-history",
            producer_id="devtool-backup" if explicit else f"backup-name:{path.name}",
            repo_root=repo_root,
            heuristic=not explicit,
            scope=CleanupScope.PROJECT,
            lifecycle="rebuildable",
        )
        descriptors.append(descriptor)
        by_resource[descriptor.resource_id] = path
    ownership = build_cleanup_ownership_view(repo_root, descriptors, policy_generation="life-x03-backup-v1")
    plan = compile_cleanup_plan(ownership, scope=CleanupScope.PROJECT, destination="local")
    selected = [by_resource[item.resource_id] for item in plan.selected if item.resource_id in by_resource]
    return sorted(selected, key=lambda item: str(item.relative_to(repo_root)))


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
    from .cleanup_lifecycle import (
        CleanupScope,
        GeneratedOutputDescriptor,
        apply_cleanup_archive_plan,
        build_cleanup_ownership_view,
        compile_cleanup_plan,
        persist_cleanup_receipt,
    )

    repo_root = repo_root.expanduser().resolve()
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination_root = _history_root(repo_root) / stamp
    sources = discover_backup_candidates(repo_root)
    if not sources:
        return []
    descriptors: list[GeneratedOutputDescriptor] = []
    source_by_resource: dict[str, Path] = {}
    destinations: dict[str, Path] = {}
    moves: list[CleanupMove] = []
    for source in sources:
        relative = source.relative_to(repo_root)
        destination = _unique_destination(destination_root / relative)
        descriptor = GeneratedOutputDescriptor.create(
            source, owner="devtool-legacy-backup", provider="repository-cleanup",
            category="backup-history", producer_id=f"devtool-backup:{source.name}", lifecycle="rebuildable",
            scope=CleanupScope.PROJECT, managed=True, published=False,
            provenance={"archive_destination": str(destination)},
        )
        descriptors.append(descriptor)
        source_by_resource[descriptor.resource_id] = source
        destinations[descriptor.resource_id] = destination
        moves.append(CleanupMove(str(relative), str(destination.relative_to(repo_root)), "directory" if source.is_dir() else "file"))
    ownership = build_cleanup_ownership_view(repo_root, descriptors, policy_generation="life-r03b-backup-archive-v1")
    plan = compile_cleanup_plan(ownership, scope=CleanupScope.PROJECT, destination="local")
    receipt = apply_cleanup_archive_plan(plan, ownership=ownership, destinations=destinations, dry_run=dry_run)
    if not dry_run:
        persist_cleanup_receipt(repo_root, receipt)
    allowed = {row.resource_id for row in receipt.results if row.status in {"planned", "applied"}}
    return [move for move, descriptor in zip(moves, descriptors) if descriptor.resource_id in allowed]


def prune_backup_history(repo_root: Path, *, older_than_days: float, dry_run: bool = False) -> list[str]:
    """Prune explicitly Devtool-owned history only after reference revalidation.

    Age is a selection policy, never deletion authority by itself. Candidate
    directories live under Devtool's private backup-history owner, then pass the
    same LIFE-X03 protection/reference/drift checks as other generated outputs.
    """
    if older_than_days <= 0:
        return []
    from .cleanup_lifecycle import (
        CleanupScope,
        GeneratedOutputDescriptor,
        apply_cleanup_plan,
        build_cleanup_ownership_view,
        compile_cleanup_plan,
    )

    repo_root = repo_root.expanduser().resolve()
    root = _history_root(repo_root)
    if not root.is_dir():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=float(older_than_days))
    descriptors: list[GeneratedOutputDescriptor] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            mtime = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime >= cutoff:
            continue
        descriptors.append(GeneratedOutputDescriptor.create(
            child,
            owner="devtool-backup-history",
            provider="repository-cleanup",
            category="backup-history",
            producer_id=f"backup-history:{child.name}",
            lifecycle="retained",
            rebuild_cost="unknown",
            scope=CleanupScope.DURABLE_HISTORY,
            destination="local",
            provenance={"age_policy_days": float(older_than_days), "mtime": mtime.isoformat()},
            managed=True,
            published=False,
        ))
    if not descriptors:
        return []
    ownership = build_cleanup_ownership_view(
        repo_root,
        descriptors,
        policy_generation=f"life-x03-history-v1:{float(older_than_days):.6f}",
    )
    plan = compile_cleanup_plan(ownership, scope=CleanupScope.DURABLE_HISTORY, destination="local")
    receipt = apply_cleanup_plan(plan, ownership=ownership, dry_run=dry_run, confirm=True)
    by_id = {item.resource_id: item for item in receipt.results}
    selected: list[str] = []
    for candidate in plan.candidates:
        result = by_id.get(candidate.resource_id)
        if result is not None and result.status in {"planned", "applied"}:
            selected.append(str(Path(candidate.path).relative_to(repo_root)))
    return selected


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
