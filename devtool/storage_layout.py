"""Repository-scoped storage ownership and non-destructive migration.

Source/configuration stays in Git; generated reports/builds remain ignored local
workspace; durable state lives under Devtool's XDG state/data roots.  Legacy
read locations are retained until an operator explicitly migrates them.
No filesystem operation happens while importing this module.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import functools
import threading
import time
from typing import Any, Iterator

from .xdg import data_dir, state_dir
from .host_file_lock import flock, LOCK_EX, LOCK_SH, LOCK_NB, LOCK_UN

_SCHEMA = "devtool.repository-storage.v1"
# One owner and disposition for each durable family. Do not classify an
# arbitrary new .devtool directory as safe to ignore, migrate, or prune.
@dataclass(frozen=True)
class StorageFamily:
    name: str
    owner: str
    class_name: str
    legacy_relative: str
    root_kind: str = "state"


FAMILIES = (
    StorageFamily("run-v3", "CORE/RUN", "durable-state", ".devtool/private/run-v3"),
    StorageFamily("android-validation", "TEST", "durable-state", ".devtool/state/android-validation"),
    StorageFamily("artifact-registry", "LIFE", "durable-state", ".devtool/artifacts"),
    StorageFamily("backup-history", "LIFE", "durable-backup", ".devtool/history/backups", "data"),
    StorageFamily("cleanup-receipts", "LIFE", "durable-evidence", ".devtool/private/cleanup/receipts"),
)
FAMILY_MAP = {item.name: item for item in FAMILIES}
LOCAL_OUTPUTS = {
    "tmp": ("temporary", "EXEC"),
    "reports": ("generated-report", "TEST"),
    "build": ("generated-build", "DIST"),
    "package": ("generated-build", "DIST"),
    "build-artifacts": ("published-output", "DIST"),
    "cache": ("disposable-cache", "DIST"),
    "state": ("legacy-state", "TEST"),
    "artifacts": ("legacy-state", "LIFE"),
    "artifacts.d": ("legacy-history", "LIFE"),
    "artifacts.json": ("legacy-history", "LIFE"),
    "history": ("legacy-backup", "LIFE"),
    "private": ("legacy-state", "CORE/LIFE"),
    "logs": ("legacy-log", "CORE"),
    "test-runs": ("legacy-test", "CORE"),
    "evidence": ("legacy-evidence", "CORE"),
    "desktop": ("legacy-history", "CORE"),
    "transactions": ("legacy-transaction", "LIFE"),
}
_MIGRATE_LIMIT_FILES = 25000
_MIGRATE_LIMIT_BYTES = 4 * 1024 * 1024 * 1024
# Family paths created by the canonical resolver. Writers must join the
# repository-scoped lock and re-check authority AFTER acquiring the lock.
# Unregistered ad-hoc test catalogs remain independent.
_REGISTERED: dict[str, tuple[Path, str, Path]] = {}
_REGISTER_LOCK = threading.RLock()


class StorageLayoutError(RuntimeError):
    """Fail closed when storage roots or migration evidence are ambiguous."""


def _repo(repo: str | Path) -> Path:
    root = Path(repo).expanduser().resolve(strict=False)
    if not root.is_absolute():
        raise StorageLayoutError("Repository path is not absolute")
    return root


def repository_key(repo: str | Path) -> str:
    root = _repo(repo)
    slug = re.sub(r"[^0-9A-Za-z_.-]+", "-", root.name or "repo").strip(".-") or "repo"
    digest = hashlib.sha256(str(root).encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return f"{slug}-{digest}"


def _base(repo: str | Path, family: str) -> Path:
    item = FAMILY_MAP[family]
    xdg = data_dir() if item.root_kind == "data" else state_dir()
    root = _repo(repo)
    # Explicit XDG overrides (and custom XDG state/data homes) must not
    # silently write unignored runtime state inside the target Git worktree.
    if xdg == root or xdg.is_relative_to(root):
        raise StorageLayoutError("XDG storage inside target repository refused: " + str(xdg))
    if (xdg / "repositories") == root or (xdg / "repositories").is_relative_to(root):
        raise StorageLayoutError("XDG repositories inside target repository refused")
    _check_xdg_ancestors(xdg / "repositories" / repository_key(root), xdg)
    return xdg / "repositories" / repository_key(root)


def _check_xdg_ancestors(path: Path, xdg: Path) -> None:
    # Termux may have system-level symlinks (/data/data); only reject paths
    # at and below the Devtool XDG owner root, including ancestors on reads.
    xdg = Path(xdg).expanduser().absolute()
    path = Path(path).expanduser().absolute()
    if not path.is_relative_to(xdg):
        raise StorageLayoutError("Storage path escaped XDG root")
    current = xdg
    for part in ((), *( (x,) for x in path.relative_to(xdg).parts )):
        if part:
            current = current / part[0]
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise StorageLayoutError("Unsafe XDG storage ancestor: " + str(current))


def _register(repo: Path, family: str, path: Path) -> Path:
    with _REGISTER_LOCK:
        _REGISTERED[str(path.absolute())] = (repo, family, path.absolute())
    return path


def external_family_dir(repo: str | Path, family: str) -> Path:
    if family not in FAMILY_MAP:
        raise StorageLayoutError(f"Unknown storage family: {family}")
    return _base(repo, family) / family


def legacy_family_dirs(repo: str | Path, family: str) -> tuple[Path, ...]:
    if family not in FAMILY_MAP:
        raise StorageLayoutError(f"Unknown storage family: {family}")
    root = _repo(repo)
    paths = [root / FAMILY_MAP[family].legacy_relative]
    if family == "run-v3":
        # The previous DEVTOOL_XDG_DIR RUN-V3 convention must remain readable.
        paths.insert(0, state_dir() / "run-v3" / repository_key(root))
    return tuple(dict.fromkeys(paths))


def _check_root(path: Path) -> None:
    # Do not reject system ancestors (/data/data is a link on Termux), but
    # reject nested links in Devtool-owned storage trees themselves.
    if path.is_symlink():
        raise StorageLayoutError(f"Symlinked storage root refused: {path}")
    if path.exists() and not path.is_dir():
        raise StorageLayoutError(f"Non-directory storage root refused: {path}")


def ensure_private_storage_dir(path: Path) -> None:
    """Create Devtool-owned XDG directories privately, never touch repo files.

    Existing directories with unsafe permissions fail closed; we do not chmod
    operator-owned ancestors or accept symlinked Devtool-owned components.
    """
    path = Path(path).expanduser().absolute()
    candidates = (state_dir().absolute(), data_dir().absolute())
    base = next((p for p in candidates if path == p or path.is_relative_to(p)), None)
    if base is None:
        # Legacy in-worktree paths are retained as-is until explicit migration.
        # A symlink or non-directory ancestor must never redirect this write.
        for ancestor in (path, *path.parents):
            if ancestor.is_symlink():
                raise StorageLayoutError("Symlinked legacy storage ancestor: " + str(ancestor))
            if ancestor.name == ".devtool":
                break
        path.mkdir(parents=True, exist_ok=True)
        return
    _check_xdg_ancestors(path, base)
    base.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.relative_to(base).parts
    # Existing XDG base directories may be shared 0755; the per-repository
    # authority folder and all its descendants must be private 0700.
    for index in range(len(suffix)+1):
        current = base / Path(*suffix[:index])
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise StorageLayoutError(f"Unsafe XDG directory: {current}")
        current.mkdir(mode=0o700,exist_ok=True)
        private_component = index >= 2 if suffix[:1] == ("repositories",) else index == len(suffix)
        if private_component and os.name == "posix" and current.stat().st_mode & 0o077:
            raise StorageLayoutError(f"XDG storage must be private 0700: {current}")


def _lock_path(repo: Path) -> Path:
    return _base(repo, "run-v3") / ".storage-migration.lock"


@contextmanager
def storage_writer_guard_for_path(path: str | Path) -> Iterator[None]:
    """RUN/TEST/LIFE writer fence; stale handles cannot write after cutover.

    The lock is acquired *before* the write, and authoritative path selection
    is revalidated inside the lock. Migration holds the exclusive counterpart.
    """
    raw = Path(path).expanduser().absolute()
    with _REGISTER_LOCK:
        candidates = [(len(str(base)), repo, name, base) for base, (repo, name, _) in
                      ((v[2], v) for v in _REGISTERED.values())
                      if raw == base or raw.is_relative_to(base)]
    if not candidates:
        yield  # independent direct/catalog fixture, not a registered repo family
        return
    _, repo, name, registered = max(candidates, key=lambda x: x[0])
    location = _lock_path(repo)
    ensure_private_storage_dir(location.parent)
    if location.is_symlink():
        raise StorageLayoutError("Writer lock symlink refused")
    fd = os.open(location, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        flock(fd, LOCK_SH)
        try:
            canonical = external_family_dir(repo, name) if name == "cleanup-receipts" else family_dir(repo, name)
            if canonical != registered:
                raise StorageLayoutError("Stale storage writer after migration; reopen canonical " + name)
            yield
        finally:
            flock(fd, LOCK_UN)
    finally:
        os.close(fd)


def guarded_catalog_open(fn):
    @functools.wraps(fn)
    def inner(cls, root, *, create=True):
        if not create:
            return fn(cls, root, create=False)
        with storage_writer_guard_for_path(root):
            return fn(cls, root, create=True)
    return inner


def guarded_validation_write(fn):
    @functools.wraps(fn)
    def inner(session, *args, **kwargs):
        with storage_writer_guard_for_path(validation_session_dir(session.repo_root)):
            return fn(session, *args, **kwargs)
    return inner


def guarded_validation_context(fn):
    @functools.wraps(fn)
    @contextmanager
    def inner(session, *args, **kwargs):
        with storage_writer_guard_for_path(validation_session_dir(session.repo_root)):
            with fn(session, *args, **kwargs):
                yield
    return inner


def guarded_registry_lock(fn):
    @functools.wraps(fn)
    @contextmanager
    def inner(repo_root, *args, **kwargs):
        with storage_writer_guard_for_path(artifact_registry_file(repo_root).parent):
            with fn(repo_root, *args, **kwargs):
                yield
    return inner


def guarded_backup_write(fn):
    @functools.wraps(fn)
    def inner(repo_root, *args, **kwargs):
        with storage_writer_guard_for_path(backup_history_dir(repo_root)):
            return fn(repo_root, *args, **kwargs)
    return inner


def persist_receipt_bytes(repo: str | Path, receipt_id: str, document: dict[str, Any]) -> Path:
    directory = cleanup_receipt_write_dir(repo)
    with storage_writer_guard_for_path(directory):
        ensure_private_storage_dir(directory)
        path = directory / (receipt_id.replace(":", "-") + ".json")
        _atomic_json(path, document)
        return path


@contextmanager
def _migration_lock(repo: Path) -> Iterator[None]:
    location = _lock_path(repo)
    ensure_private_storage_dir(location.parent)
    if location.is_symlink():
        raise StorageLayoutError("Migration lock symlink refused")
    flags = os.O_RDWR | os.O_CREAT | getattr(os,"O_NOFOLLOW",0)
    fd = os.open(location,flags,0o600)
    try:
        try:
            flock(fd,LOCK_EX | LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            raise StorageLayoutError("Storage migration is already active") from exc
        try:
            yield
        finally:
            flock(fd,LOCK_UN)
    finally:
        os.close(fd)


def _has_payload(path: Path) -> bool:
    if not path.exists():
        return False
    _check_root(path)
    try:
        return next(path.iterdir(), None) is not None
    except OSError as exc:
        raise StorageLayoutError(f"Cannot inspect storage directory: {path}") from exc


def _marker(repo: str | Path, family: str) -> Path:
    return state_dir() / "repositories" / repository_key(repo) / ".layout-markers" / (family + ".json")


def _read_marker(repo: str | Path, family: str) -> dict[str, Any] | None:
    path = _marker(repo, family)
    if path.is_symlink():
        raise StorageLayoutError("Migration marker symlink refused")
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise StorageLayoutError(f"Unreadable migration marker: {path}") from exc
    target = external_family_dir(repo, family)
    if (not isinstance(doc, dict) or doc.get("schema") != _SCHEMA
        or doc.get("family") != family or doc.get("destination") != str(target)
        or doc.get("source") not in [str(x) for x in legacy_family_dirs(repo, family)]
        or not re.fullmatch(r"[a-f0-9]{64}", str(doc.get("source_digest") or ""))):
        raise StorageLayoutError(f"Invalid migration marker: {path}")
    if not target.is_dir() or target.is_symlink():
        raise StorageLayoutError("Migration marker points to missing or unsafe destination")
    return doc


def family_dir(repo: str | Path, family: str) -> Path:
    """Return canonical read/write location, refusing split-brain state.

    Pre-existing legacy state wins until explicit, verified migration. A new
    repository defaults to XDG. Historical data remains untouched on disk.
    """
    root = _repo(repo)
    target = external_family_dir(root, family)
    old = legacy_family_dirs(root, family)
    # Register source and destination for stale-writer checks. Legacy sources
    # are registered even after cutover so previously-open writers refuse.
    _register(root, family, target)
    for legacy_path in old:
        _register(root, family, legacy_path)
    for path in (target, *old):
        _check_root(path)
        if path.is_relative_to(_repo(repo)):
            child = _repo(repo)
            for part in path.relative_to(child).parts:
                child = child / part
                if child.is_symlink():
                    raise StorageLayoutError(f"Symlinked repository storage ancestor: {child}")
    if family == "cleanup-receipts":
        # New receipts are external even with legacy evidence retained;
        # readers inspect both locations without merging journal truth.
        return target
    marker = _read_marker(repo, family)
    if marker is not None:
        return target
    populated = [path for path in old if _has_payload(path)]
    if len(populated) > 1:
        raise StorageLayoutError(f"Multiple legacy {family} authorities; reconcile before proceeding")
    if populated and _has_payload(target):
        raise StorageLayoutError(f"Legacy and XDG {family} both contain data; migrate or reconcile first")
    if populated:
        return populated[0]
    if target.is_dir():
        return target
    return next((path for path in old if path.is_dir()), target)


def run_catalog_dir(repo: str | Path) -> Path:
    return family_dir(repo, "run-v3")


def validation_session_dir(repo: str | Path) -> Path:
    # A running isolated transaction executes against its private worktree,
    # but its durable session belongs to the primary repository. Resolve this
    # through the SAME XDG/legacy authority and writer fence as normal state.
    # Returning a hard-coded in-tree directory here could fail the migration
    # writer fence even when no legacy state exists, leaving validation stuck.
    redirected = str(os.environ.get("DEVTOOL_VALIDATION_STATE_REPO_ROOT") or "").strip()
    return family_dir(_repo(redirected) if redirected else repo, "android-validation")


def artifact_registry_file(repo: str | Path) -> Path:
    return family_dir(repo, "artifact-registry") / "registry.json"


def backup_history_dir(repo: str | Path) -> Path:
    return family_dir(repo, "backup-history")


def cleanup_receipt_write_dir(repo: str | Path) -> Path:
    """New cleanup receipts are always XDG, even when old receipts remain."""
    target = external_family_dir(repo, "cleanup-receipts")
    _check_root(target)
    return _register(_repo(repo), "cleanup-receipts", target)


def cleanup_receipt_read_dirs(repo: str | Path) -> tuple[Path, ...]:
    paths = (cleanup_receipt_write_dir(repo), *legacy_family_dirs(repo, "cleanup-receipts"))
    for prior in paths:
        _register(_repo(repo), "cleanup-receipts", prior)
    for path in paths:
        _check_root(path)
        if path.is_relative_to(_repo(repo)):
            child = _repo(repo)
            for part in path.relative_to(child).parts:
                child = child / part
                if child.is_symlink():
                    raise StorageLayoutError(f"Symlinked repository storage ancestor: {child}")
    return tuple(dict.fromkeys(paths))


def _read_only_activity_blockers(repo: Path) -> list[str]:
    """Conservative read-only indicators; never clean/modify stale markers.

    Native migration remains the authority for complete active process checks.
    Unlike transactions.find_active_transactions, planning may not remove a
    stale/corrupt marker simply by observing it.
    """
    flags: list[str] = []
    tx = data_dir() / "transactions" / repository_key(repo)
    if tx.is_symlink() or (tx / "active.json").is_symlink() or (tx / "active").is_symlink():
        flags.append("unsafe-transaction-marker")
    elif (tx / "active.json").exists() or (
        (tx / "active").is_dir() and next((tx / "active").glob("*.json"), None) is not None
    ):
        flags.append("transaction-marker-present")
    for location in (external_family_dir(repo, "android-validation"),
                     *legacy_family_dirs(repo, "android-validation")):
        p = location / "active.json"
        if p.is_symlink():
            flags.append("unsafe-validation-marker")
        elif p.is_file():
            try:
                if p.stat().st_size > 1024 * 1024:
                    raise ValueError("validation marker too large")
                doc = json.loads(p.read_text(encoding="utf-8"))
                status = str(doc.get("status") or doc.get("state") or "unknown").lower()
            except (OSError, ValueError, TypeError, AttributeError):
                flags.append("unreadable-validation-marker")
            else:
                if status not in {"completed", "failed", "aborted", "idle"}:
                    flags.append("active-validation-marker")
    for location in (external_family_dir(repo, "run-v3"), *legacy_family_dirs(repo, "run-v3")):
        p = location / "catalog.json"
        if p.is_symlink():
            flags.append("unsafe-run-catalog")
        elif p.is_file():
            try:
                if p.stat().st_size > 16 * 1024 * 1024:
                    raise ValueError("catalog too large for read-only preview")
                catalog = json.loads(p.read_text(encoding="utf-8"))
                runs = catalog.get("runs", {})
                if not isinstance(runs, dict):
                    raise ValueError("invalid catalog")
                if any(isinstance(x, dict) and str(x.get("status") or "").lower()
                       in {"running", "pending", "queued", "starting", "in_progress"}
                       for x in runs.values()):
                    flags.append("active-run-catalog")
            except (OSError, ValueError, AttributeError):
                flags.append("unreadable-run-catalog")
    return sorted(set(flags))


def storage_inventory(repo: str | Path) -> dict[str, Any]:
    """Read-only ownership inventory. Does not create a .devtool directory."""
    root = _repo(repo)
    rows = []
    for item in FAMILIES:
        dest = external_family_dir(root, item.name)
        legacy = legacy_family_dirs(root, item.name)
        try:
            current = family_dir(root, item.name)
            status = "external" if current == dest else "legacy"
            if item.name == "cleanup-receipts" and any(_has_payload(p) for p in legacy):
                status = "external-with-legacy-readable"
        except StorageLayoutError as exc:
            current = None
            status = "conflict:" + str(exc)
        rows.append({"family": item.name, "owner": item.owner,
                     "classification": item.class_name, "current": str(current) if current else None,
                     "destination": str(dest), "legacy": [str(p) for p in legacy],
                     "status": status, "migration_record": str(_marker(root,item.name)),
                     "needs_migration": bool((current and current != dest) or
                                             (item.name == "cleanup-receipts" and any(_has_payload(p) for p in legacy)))})
    local = root / ".devtool"
    unknown = []
    if local.is_symlink():
        unknown.append(".devtool (symlink)")
    elif local.is_dir():
        for entry in sorted(local.iterdir()):
            if entry.name not in LOCAL_OUTPUTS:
                unknown.append(entry.name)
    tracked = []
    ignore_status = "not-a-repo"
    try:
        probe = subprocess.run(["git","-C",str(root),"check-ignore","--no-index","-q","--",
                                ".devtool/.devtool-runtime-probe"],
                               capture_output=True,timeout=10,check=False)
        if probe.returncode in (0,1):
            ignore_status = "ignored" if probe.returncode==0 else "not-ignored"
        proc = subprocess.run(["git","-C",str(root),"ls-files","-z","--",".devtool"],
                              capture_output=True,timeout=10,check=False)
        if proc.returncode == 0:
            tracked = [p.decode("utf-8","surrogateescape") for p in proc.stdout.split(b"\0") if p]
    except (OSError,subprocess.TimeoutExpired):
        pass
    activity = _read_only_activity_blockers(root)
    return {"schema": _SCHEMA, "repository": str(root), "families": rows,
            "local_workspace": str(local), "git_ignore_status": ignore_status,
            "recommended_gitignore": "**/.devtool/",
            "unknown_local_families": unknown, "tracked_local_files": tracked,
            "active_blockers": activity,
            "safe_to_migrate": not unknown and not tracked and not activity and not any(row["status"].startswith("conflict:") for row in rows),
            "mutates": False}



def migration_plan(repo: str | Path) -> dict[str, Any]:
    """Bounded, read-only action preview; never creates a state directory."""
    inv = storage_inventory(repo)
    root = _repo(repo)
    blockers = ([] if not inv["unknown_local_families"] else ["unknown-local-family"])
    blockers.extend(["tracked-local-files"] if inv["tracked_local_files"] else [])
    blockers.extend(inv.get("active_blockers", []))
    operations = []
    for row in inv["families"]:
        family = row["family"]
        sources = [Path(p) for p in row["legacy"] if Path(p).is_dir() and _has_payload(Path(p))]
        family_blockers = []
        status = row["status"]
        if status.startswith("conflict:"):
            family_blockers.append("conflicting-authorities")
        if len(sources) > 1:
            family_blockers.append("multiple-legacy-sources")
        if family == "cleanup-receipts" and sources:
            action = "merge-verified-evidence"
        elif row["needs_migration"]:
            action = "copy-verify-and-cutover"
        else:
            action = "no-op"
        try:
            size = count = 0
            for source in sources:
                _, n, b = _fingerprint_dir(source)
                count += n; size += b
        except StorageLayoutError as exc:
            family_blockers.append("unreadable-or-unsafe-source:" + str(exc))
            count = size = None
        operations.append({"family": family, "owner": row["owner"], "action": action,
                           "sources": [str(s) for s in sources], "destination": row["destination"],
                           "estimated_files": count, "estimated_bytes": size,
                           "conflict_policy": "never-overwrite-different-bytes",
                           "originals_preserved": True, "blockers": family_blockers})
        blockers.extend(family_blockers)
    return {"schema": _SCHEMA, "operation": "migration-plan", "repository": str(root),
            "actions": operations, "blockers": sorted(set(blockers)),
            "ready": not blockers, "mutates": False,
            "confirmation_required": True}

def _fingerprint_dir(path: Path) -> tuple[str, int, int]:
    """Bounded, deterministic content+mode inventory with symlink refusal."""
    _check_root(path)
    if not path.is_dir():
        raise StorageLayoutError(f"Missing migration source: {path}")
    h = hashlib.sha256()
    count = size = 0
    for base, dirs, files in os.walk(path, topdown=True, followlinks=False):
        basepath = Path(base)
        for name in sorted(dirs):
            child = basepath / name
            if child.is_symlink():
                raise StorageLayoutError(f"Symlinked migration directory: {child}")
            mode = child.stat(follow_symlinks=False).st_mode
            if not stat.S_ISDIR(mode):
                raise StorageLayoutError(f"Non-directory migration entry: {child}")
            h.update(("D:" + child.relative_to(path).as_posix() + "\n").encode())
        dirs.sort()
        for name in sorted(files):
            child = basepath / name
            if child.is_symlink() or not child.is_file():
                raise StorageLayoutError(f"Unsupported migration entry: {child}")
            st = child.stat(follow_symlinks=False)
            count += 1
            size += st.st_size
            if count > _MIGRATE_LIMIT_FILES or size > _MIGRATE_LIMIT_BYTES:
                raise StorageLayoutError("Migration safety inventory limit exceeded")
            rel = child.relative_to(path).as_posix()
            h.update(f"F:{rel}:{st.st_size}:{stat.S_IMODE(st.st_mode)}\n".encode())
            with child.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024*1024), b""):
                    h.update(chunk)
    return h.hexdigest(), count, size


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    ensure_private_storage_dir(path.parent)
    if path.is_symlink():
        raise StorageLayoutError("Migration marker symlink refused")
    fd, temp = tempfile.mkstemp(prefix=".layout-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(document, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
        dfd=os.open(path.parent, os.O_RDONLY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def migrate_storage(repo: str | Path, *, confirmed: bool = False) -> dict[str, Any]:
    """Explicit, non-deleting migration; never moves an active authority.

    If the process crashes after atomic publication but before the marker, a
    retry can finish the marker only when both trees are byte-for-byte equal.
    """
    if not confirmed:
        raise StorageLayoutError("Migration requires explicit --yes approval")
    root = _repo(repo)
    with _migration_lock(root):
        return _migrate_storage_locked(root)


def _migrate_storage_locked(root: Path) -> dict[str, Any]:
    inventory = storage_inventory(root)
    if inventory["tracked_local_files"]:
        raise StorageLayoutError("Tracked .devtool files require review before migration")
    if inventory["unknown_local_families"]:
        raise StorageLayoutError("Unknown .devtool families require review before migration")
    # Do not race active validation or native LIFE transactions. Running
    # callers can still observe read-only inventory without these imports.
    from .transactions import find_active_transactions
    if find_active_transactions(root, include_live=True):
        raise StorageLayoutError("Active LIFE transaction protects storage; migration refused")
    from .validation_sessions import load_active_session
    # Examine every possible authority independently. During interrupted
    # migration the two trees may temporarily be identical but marker-free.
    # Never misinterpret that as permission to resume an active session.
    for location in (external_family_dir(root,"android-validation"),
                     *legacy_family_dirs(root,"android-validation")):
        marker_path = location / "active.json"
        if marker_path.is_symlink():
            raise StorageLayoutError("Symlinked validation marker protects storage")
        if marker_path.is_file():
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
                state = str(marker.get("status") or marker.get("state") or "unknown").lower()
            except (OSError,UnicodeError,ValueError,AttributeError) as exc:
                raise StorageLayoutError("Unreadable active validation marker protects storage") from exc
            if state not in {"completed","failed","aborted","idle"}:
                raise StorageLayoutError("Active validation session protects storage; migration refused")
    # Preserve existing load_active_session authority when there is only one
    # validated location; a marker-free interrupted publication is handled
    # by the byte-identical recovery step below.
    try:
        session = load_active_session(root)
    except StorageLayoutError:
        session = None
    if session is not None and str(getattr(session,"status","")).lower() not in {"", "completed", "failed", "aborted"}:
        raise StorageLayoutError("Active validation session protects storage; migration refused")
    from .run_store import RunCatalog
    for location in (external_family_dir(root,"run-v3"), *legacy_family_dirs(root,"run-v3")):
        if not (location/"catalog.json").is_file():
            continue
        catalog = RunCatalog.open(location, create=False)
        if any(str(run.get("status") or "").lower() in {"running","pending","queued","starting","in_progress"}
               for run in catalog.runs.values() if isinstance(run, dict)):
            raise StorageLayoutError("Active RUN catalog protects storage; migration refused")
    migrated=[]
    skipped=[]
    for family in FAMILIES:
        name = family.name
        dest=external_family_dir(root,name)
        if _read_marker(root,name):
            skipped.append({"family":name,"reason":"already-migrated"})
            continue
        sources=[p for p in legacy_family_dirs(root,name) if _has_payload(p)]
        if len(sources)>1:
            raise StorageLayoutError(f"Multiple legacy sources for {name}")
        if not sources:
            skipped.append({"family":name,"reason":"no-legacy-data"})
            continue
        source=sources[0]
        if source==dest:
            skipped.append({"family":name,"reason":"already-external"})
            continue
        source_hash,count,size = _fingerprint_dir(source)
        if dest.exists():
            # For cleanup receipts, both archives are readable; merge only
            # non-conflicting named evidence, never replace a newer receipt.
            if name == "cleanup-receipts":
                ensure_private_storage_dir(dest)
                for candidate in sorted(source.iterdir()):
                    if candidate.is_symlink() or not candidate.is_file():
                        raise StorageLayoutError("Unexpected cleanup receipt entry")
                    existing = dest / candidate.name
                    if existing.is_symlink():
                        raise StorageLayoutError("Symlinked cleanup receipt destination")
                    if existing.is_file():
                        if hashlib.sha256(existing.read_bytes()).digest() != hashlib.sha256(candidate.read_bytes()).digest():
                            raise StorageLayoutError("Conflicting cleanup receipt; no overwrite")
                        continue
                    fd, tmp_path = tempfile.mkstemp(prefix=".receipt-",suffix=".tmp",dir=dest)
                    temp = Path(tmp_path)
                    try:
                        with os.fdopen(fd,"wb") as out, candidate.open("rb") as inp:
                            os.fchmod(out.fileno(), 0o600)
                            shutil.copyfileobj(inp,out)
                            out.flush(); os.fsync(out.fileno())
                        if hashlib.sha256(temp.read_bytes()).digest() != hashlib.sha256(candidate.read_bytes()).digest():
                            raise StorageLayoutError("Receipt copy failed verification")
                        os.replace(temp,existing)
                    finally:
                        temp.unlink(missing_ok=True)
            else:
                # Valid recovery from interruption: do not overwrite unknown bytes.
                dest_hash,d_count,d_size = _fingerprint_dir(dest)
                if (dest_hash,d_count,d_size)!=(source_hash,count,size):
                    raise StorageLayoutError(f"Existing destination differs for {name}; no overwrite")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.TemporaryDirectory(prefix=".layout-stage-",dir=dest.parent) as stage_name:
                staging = Path(stage_name) / "copy"
                shutil.copytree(source, staging, symlinks=True)
                # Copied legacy directories are often 0755. Never publish a
                # directory which the new strict writer would immediately refuse.
                for base, dirs, files in os.walk(staging, followlinks=False):
                    if Path(base).is_symlink() or any((Path(base)/name).is_symlink() for name in dirs):
                        raise StorageLayoutError("Symlinked copied directory refused")
                    os.chmod(base, 0o700)
                copy_hash,c_count,c_size = _fingerprint_dir(staging)
                # Rehash the source just before publication; no partial copy
                # is allowed to become canonical if an active writer raced it.
                if ((copy_hash,c_count,c_size)!=(source_hash,count,size) or
                    _fingerprint_dir(source)!=(source_hash,count,size)):
                    raise StorageLayoutError(f"Source changed during migration: {name}")
                # Final drift check while the exclusive writer lock remains held.
                if _fingerprint_dir(source)!=(source_hash,count,size):
                    raise StorageLayoutError("Source drift before publication: " + name)
                os.replace(staging,dest)
                ensure_private_storage_dir(dest)
                dfd=os.open(dest.parent, os.O_RDONLY)
                try: os.fsync(dfd)
                finally: os.close(dfd)
        doc={"schema":_SCHEMA,"family":name,"source":str(source),
             "destination":str(dest),"source_digest":source_hash,
             "files":count,"bytes":size,"original_preserved":True}
        if _fingerprint_dir(source) != (source_hash,count,size):
            raise StorageLayoutError(f"Source drift after copy: {name}; manual review required")
        _atomic_json(_marker(root,name),doc)
        # One final check catches uncooperative older writers at the cutover.
        # A mismatch is NOT a success; the evidence remains for reconciliation.
        if _fingerprint_dir(source)!=(source_hash,count,size):
            # Never retain a falsely qualifying marker after observing drift.
            marker_path = _marker(root,name)
            marker_path.unlink(missing_ok=True)
            dfd = os.open(marker_path.parent, os.O_RDONLY)
            try: os.fsync(dfd)
            finally: os.close(dfd)
            raise StorageLayoutError("Concurrent legacy writer detected after migration marker: " + name)
        migrated.append(doc)
    return {"schema":_SCHEMA,"repo":str(root),"migrated":migrated,
            "skipped":skipped,"originals_preserved":True,"mutates":bool(migrated)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Devtool repository storage audit and explicit safe migration")
    p.add_argument("operation",choices=("audit","plan","ignore","migrate"),nargs="?",default="audit")
    p.add_argument("--repo",default=".")
    p.add_argument("--yes",action="store_true",help="Explicitly approve verified non-destructive migration")
    args = p.parse_args(argv)
    try:
        out = (migrate_storage(args.repo,confirmed=args.yes) if args.operation=="migrate" else
               migration_plan(args.repo) if args.operation=="plan" else storage_inventory(args.repo))
        if args.operation == "ignore":
            out = {"repository":out["repository"],
                   "git_ignore_status":out["git_ignore_status"],
                   "recommended_gitignore":out["recommended_gitignore"],
                   "tracked_local_files":out["tracked_local_files"],
                   "mutates":False}
    except (StorageLayoutError,OSError,ValueError) as exc:
        print(json.dumps({"status":"refused","reason":str(exc)},ensure_ascii=False))
        return 2
    print(json.dumps(out,indent=2,ensure_ascii=False,sort_keys=True))
    return 0 if out.get("safe_to_migrate",out.get("ready",True)) else 3


if __name__ == "__main__":
    raise SystemExit(main())
