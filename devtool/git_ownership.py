from __future__ import annotations

"""GIT-FRX-07 federated path-to-repository ownership.

The federation discovery layer already knows which repositories are explicitly
 enrolled.  This module promotes that boundary into the Git subsystem without
 turning filesystem Git discovery into enrollment: Git probing is used only to
 prove/exclude nested checkout boundaries and to attach stable Git/worktree
 identity to an already-explicit owner.
"""

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Any, TYPE_CHECKING

from .federation_discovery import (
    FederatedRepositoryOwner,
    FederationDiscoveryError,
    resolve_federation_repository_owner,
)
from .git_state import GitStateError, collect_git_state

if TYPE_CHECKING:
    from .models import ProjectConfig


GIT_FEDERATED_OWNERSHIP_SCHEMA_VERSION = 1


class GitFederatedOwnershipError(RuntimeError):
    """Raised when a path cannot be assigned to an explicit safe Git owner."""


@dataclass(frozen=True, slots=True)
class GitFederatedOwner:
    schema_version: int
    requested_path: str
    canonical_path: str
    repository: str
    root: str
    source: str
    repository_identity: dict[str, Any]
    git: dict[str, Any]
    boundary_status: str = "owned"
    boundary_detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "requested_path": self.requested_path,
            "canonical_path": self.canonical_path,
            "repository": self.repository,
            "root": self.root,
            "source": self.source,
            "repository_identity": dict(self.repository_identity),
            "git": dict(self.git),
            "boundary_status": self.boundary_status,
            "boundary_detail": self.boundary_detail,
        }


def _canonical(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _probe_directory(path: Path) -> Path:
    candidate = path
    if candidate.exists() and candidate.is_file():
        candidate = candidate.parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    if candidate.exists() and candidate.is_file():
        candidate = candidate.parent
    return candidate


def _git_toplevel(path: Path) -> Path | None:
    if not shutil.which("git"):
        return None
    probe = _probe_directory(path)
    try:
        result = subprocess.run(
            ["git", "-C", str(probe), "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return _canonical(result.stdout.strip())


def _git_summary(owner_root: Path) -> dict[str, Any]:
    try:
        state = collect_git_state(owner_root)
    except GitStateError as exc:
        return {
            "available": False,
            "repository_id": "",
            "root": "",
            "worktree_id": "",
            "is_linked_worktree": False,
            "git_common_dir": "",
            "detail": str(exc),
        }
    worktree = state.worktree if isinstance(state.worktree, dict) else {}
    return {
        "available": True,
        "repository_id": state.repository_id,
        "root": state.root,
        "worktree_id": str(worktree.get("id") or ""),
        "is_linked_worktree": bool(worktree.get("is_linked", False)),
        "git_common_dir": state.git_common_dir,
        "detail": "authoritative Git state collected for explicit owner",
    }


def _ensure_no_unenrolled_nested_git(owner: FederatedRepositoryOwner, canonical: Path) -> None:
    """Reject a nested Git checkout that the explicit federation graph does not own.

    Git discovery is deliberately negative-only here: finding a checkout never
    creates membership.  If the nearest Git toplevel lives strictly inside the
    selected explicit owner root, the federation resolver would have selected
    that deeper root if it were enrolled.  Therefore the nested checkout is an
    exclusion boundary, not inherited by the parent repository.
    """

    owner_root = _canonical(owner.root)
    git_root = _git_toplevel(canonical)
    if git_root is None or git_root == owner_root:
        return
    try:
        git_root.relative_to(owner_root)
    except ValueError:
        # The selected logical repository may itself live inside an ancestor
        # worktree.  That does not create sibling/nested ownership by itself.
        return
    raise GitFederatedOwnershipError(
        "Path enters an unenrolled nested Git repository: "
        f"{canonical} resolves inside Git root {git_root}, while explicit owner "
        f"{owner.repository!r} is rooted at {owner_root}. Enroll the nested "
        "repository explicitly or keep it outside this federated Git operation."
    )


def resolve_federated_git_owner(
    parent: "ProjectConfig",
    path: str | Path,
    *,
    require_owned: bool = True,
) -> GitFederatedOwner | None:
    """Resolve ``path`` to the authoritative explicitly enrolled Git owner.

    Longest-root, recursion, symlink canonicalization and duplicate-root safety
    come from the sealed FRX resolver.  This Git layer additionally prevents
    ownership inheritance into an *unenrolled* nested Git checkout and attaches
    stable repository/worktree identity for future frozen-source planning.
    """

    requested = str(Path(path).expanduser())
    canonical = _canonical(path)
    try:
        owner = resolve_federation_repository_owner(parent, canonical)
    except FederationDiscoveryError as exc:
        raise GitFederatedOwnershipError(str(exc)) from exc
    if owner is None:
        if not require_owned:
            return None
        raise GitFederatedOwnershipError(
            f"Path is outside the owning repository and all explicitly enrolled federation roots: {canonical}"
        )

    _ensure_no_unenrolled_nested_git(owner, canonical)
    owner_root = _canonical(owner.root)
    git = _git_summary(owner_root)
    identity = owner.repository_identity.as_dict() if owner.repository_identity else {}
    detail = (
        "longest explicit canonical federation root; Git identity attached"
        if git.get("available")
        else "longest explicit canonical federation root; no independent Git state at owner root"
    )
    return GitFederatedOwner(
        schema_version=GIT_FEDERATED_OWNERSHIP_SCHEMA_VERSION,
        requested_path=requested,
        canonical_path=str(canonical),
        repository=owner.repository,
        root=str(owner_root),
        source=owner.source,
        repository_identity=identity,
        git=git,
        boundary_status="owned",
        boundary_detail=detail,
    )


def federated_git_ownership_payload(parent: "ProjectConfig", path: str | Path) -> dict[str, Any]:
    owner = resolve_federated_git_owner(parent, path)
    return {
        "schema_version": GIT_FEDERATED_OWNERSHIP_SCHEMA_VERSION,
        "path": str(path),
        "owner": owner.as_dict() if owner is not None else None,
    }


__all__ = [
    "GIT_FEDERATED_OWNERSHIP_SCHEMA_VERSION",
    "GitFederatedOwner",
    "GitFederatedOwnershipError",
    "federated_git_ownership_payload",
    "resolve_federated_git_owner",
]
