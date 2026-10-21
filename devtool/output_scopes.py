from __future__ import annotations

"""RM-01: reviewed, typed output scopes for workflow scheduling.

This module only describes and safely provisions declared output roots. It is
not a sandbox for commands: declarations are scheduling/cleanup contracts and
must not be taken as permission to write elsewhere.
"""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
from typing import Any, Mapping

from .reservations import repo_key
from .xdg import state_dir


class OutputScopeError(ValueError):
    pass


_KINDS = frozenset({"repository", "run-temp", "shared-cache", "external"})
_LIFETIMES = frozenset({"node", "run", "persistent"})
_CLEANUPS = frozenset({"none", "on-terminal"})
_HASH_POLICIES = frozenset({"never", "on-success", "always"})
_ALLOWED_FIELDS = frozenset({"kind", "path", "lifetime", "cleanup", "hash_retention", "reservation"})
_SLUG = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class OutputScope:
    kind: str
    path: str
    lifetime: str
    cleanup: str
    hash_retention: str
    reservation: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "path": self.path, "lifetime": self.lifetime,
                "cleanup": self.cleanup, "hash_retention": self.hash_retention,
                "reservation": self.reservation}


def _relative_path(value: str, *, context: str) -> str:
    if "\\" in value or "\x00" in value:
        raise OutputScopeError(f"{context}: backslash or NUL not allowed")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"..", ".git"} for part in path.parts):
        raise OutputScopeError(f"{context}: path must be relative, non-traversing, outside .git")
    if path.as_posix() in {".", ""}:
        raise OutputScopeError(f"{context}: root path is not a permitted output")
    return path.as_posix()


def parse_output_scopes(raw: object, *, context: str) -> tuple[OutputScope, ...]:
    if raw in (None, ""):
        return ()
    if not isinstance(raw, list):
        raise OutputScopeError(f"{context}: expected an array of output-scope tables")
    parsed: list[OutputScope] = []
    for index, entry in enumerate(raw):
        at = f"{context}[{index}]"
        if not isinstance(entry, Mapping):
            raise OutputScopeError(f"{at}: expected table")
        unknown = set(entry) - _ALLOWED_FIELDS
        if unknown:
            raise OutputScopeError(f"{at}: unknown keys: {', '.join(sorted(unknown))}")
        kind = entry.get("kind")
        if not isinstance(kind, str) or kind not in _KINDS:
            raise OutputScopeError(f"{at}: kind must be repository, run-temp, shared-cache, or external")
        path = entry.get("path")
        if not isinstance(path, str) or not path.strip() or path != path.strip():
            raise OutputScopeError(f"{at}: path must be a nonempty, unpadded string")
        if kind == "external":
            if not Path(path).is_absolute() or ".." in Path(path).parts or "\x00" in path:
                raise OutputScopeError(f"{at}: external path requires a literal absolute path without traversal")
            if Path(path).anchor == path:
                raise OutputScopeError(f"{at}: cannot reserve the filesystem root")
        else:
            path = _relative_path(path, context=at)
        default_life = "run" if kind == "run-temp" else "persistent"
        lifetime = entry.get("lifetime", default_life)
        cleanup = entry.get("cleanup", "on-terminal" if kind == "run-temp" else "none")
        hash_retention = entry.get("hash_retention", "never")
        reservation = entry.get("reservation", "")
        if lifetime not in _LIFETIMES or cleanup not in _CLEANUPS or hash_retention not in _HASH_POLICIES:
            raise OutputScopeError(f"{at}: invalid lifetime, cleanup, or hash_retention")
        if kind == "run-temp":
            if lifetime != "run" or cleanup != "on-terminal":
                raise OutputScopeError(f"{at}: run-temp requires lifetime=run and cleanup=on-terminal")
        elif lifetime != "persistent" or cleanup != "none" or hash_retention != "never":
            raise OutputScopeError(f"{at}: persistent output needs lifetime=persistent, cleanup=none, hash_retention=never")
        if not isinstance(reservation, str) or (reservation and not _SLUG.fullmatch(reservation)):
            raise OutputScopeError(f"{at}: invalid shared resource reservation key")
        if kind == "external" and not reservation:
            raise OutputScopeError(f"{at}: external destinations require an explicit reservation key")
        if kind == "run-temp" and reservation:
            raise OutputScopeError(f"{at}: run-temp outputs are already isolated by node")
        parsed.append(OutputScope(kind, path, lifetime, cleanup, hash_retention, reservation))
    if len({(item.kind, item.path) for item in parsed}) != len(parsed):
        raise OutputScopeError(f"{context}: duplicate output scope")
    return tuple(parsed)


def _ensure_no_symlinks(path: Path, *, stop: Path | None = None) -> None:
    """Check existing components; keep root ownership checked at cleanup too."""
    current = path
    while True:
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(mode):
                raise OutputScopeError(f"output path contains symlink: {current}")
            if current != path and not stat.S_ISDIR(mode):
                raise OutputScopeError(f"output parent is not a directory: {current}")
        if current == current.parent or current == stop:
            return
        current = current.parent


def reservation_scope(scope: OutputScope, *, node_key: str, repo_root: Path, job_root: Path) -> str:
    """Return normalized, comparable logical path (not a real write path)."""
    if scope.kind == "repository":
        # Resolve is a validation check, never a permission to escape the repository.
        absolute = (job_root / scope.path)
        _ensure_no_symlinks(absolute, stop=repo_root)
        try:
            rel = absolute.resolve(strict=False).relative_to(repo_root.resolve())
        except ValueError as exc:
            raise OutputScopeError(f"repository output escapes worktree: {scope.path}") from exc
        return rel.as_posix()
    if scope.kind == "run-temp":
        node_hash = hashlib.sha256(node_key.encode()).hexdigest()[:24]
        return f"@run-temp/{node_hash}/{scope.path}"
    if scope.kind == "shared-cache":
        return f"@shared-cache/{repo_key(repo_root)}/{scope.path}"
    # Two external paths must compare conservatively even when job authors
    # gave distinct reservation tokens for their overlapping destinations.
    return "@external" + str(Path(scope.path))


def provision_node_temp(repo_root: Path, run_id: str, node_key: str) -> tuple[Path, tuple[int, int]]:
    """Create a private temp root; caller owns its lifetime and cleanup."""
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
        raise OutputScopeError("invalid run ID for temporary root")
    node_hash = hashlib.sha256(node_key.encode()).hexdigest()[:24]
    run_hash = hashlib.sha256(run_id.encode()).hexdigest()[:24]
    base = state_dir() / "workflow-outputs" / repo_key(repo_root)
    root = base / run_hash / node_hash
    _ensure_no_symlinks(root)
    root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ensure_no_symlinks(root.parent)
    # Exclusive creation prevents adopting files left by another run/owner.
    try:
        root.mkdir(mode=0o700, exist_ok=False)
    except FileExistsError as exc:
        raise OutputScopeError("owned temporary root already exists") from exc
    _ensure_no_symlinks(root)
    st = root.stat(follow_symlinks=False)
    if not stat.S_ISDIR(st.st_mode):
        raise OutputScopeError("temporary output root is not a directory")
    return root, (st.st_dev, st.st_ino)


def cleanup_owned_temp(path: Path, identity: tuple[int, int]) -> bool:
    """Only remove the exact owned directory; never follow a replacement link."""
    try:
        st = path.lstat()
    except FileNotFoundError:
        return True
    if not stat.S_ISDIR(st.st_mode) or (st.st_dev, st.st_ino) != identity:
        return False
    # Never recursively follow symlink children. Guard against a replaced root.
    _ensure_no_symlinks(path)
    shutil.rmtree(path)
    return True


def hash_owned_output(temp_root: Path, relative: str) -> dict[str, object]:
    """Hash a declared regular output before owned-root cleanup.

    Directories are not yet artifact containers (RM-05 owns aggregate proofs).
    Hash policy for a regular file is honored immediately; missing/symlink
    outputs remain explicitly unverifiable, never counted as success.
    """
    location = temp_root / _relative_path(relative, context="temporary artifact")
    try:
        _ensure_no_symlinks(location, stop=temp_root)
        descriptor = os.open(location, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except (OSError, OutputScopeError) as exc:
        return {"path": relative, "status": "unavailable", "reason": type(exc).__name__}
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            return {"path": relative, "status": "unsupported", "reason": "not_regular_file"}
        sha = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while True:
                part = stream.read(1024 * 1024)
                if not part:
                    break
                sha.update(part)
        return {"path": relative, "status": "hashed", "sha256": sha.hexdigest(), "size": info.st_size}
    finally:
        os.close(descriptor)
