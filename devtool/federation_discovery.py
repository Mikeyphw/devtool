from __future__ import annotations

"""Federated repository discovery and boundary resolution.

FRX-03 turns the explicit FRX-02 enrollment schema into authoritative child
repository ownership.  It intentionally stops before cross-repository planning
or execution: callers can list/inspect child repositories, resolve each child
through its own config/default target, and expose boundary diagnostics.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .repository_identity import RepositoryIdentity, same_repository_identity

if TYPE_CHECKING:
    from .models import ProjectConfig


class FederationDiscoveryError(ValueError):
    """Raised when an enrolled repository cannot be resolved safely."""


@dataclass(frozen=True, slots=True)
class FederatedRepositoryResolution:
    name: str
    declared_path: str
    root: str
    required: bool
    available: bool
    relation: str
    config_path: str = ""
    selected_target: str = ""
    runner: str = ""
    platform: str = ""
    repository_identity: RepositoryIdentity | None = None
    boundary_status: str = "ok"
    boundary_detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.declared_path,
            "root": self.root,
            "required": self.required,
            "available": self.available,
            "relation": self.relation,
            "config_path": self.config_path,
            "selected_target": self.selected_target,
            "runner": self.runner,
            "platform": self.platform,
            "repository_identity": self.repository_identity.as_dict() if self.repository_identity else {},
            "boundary_status": self.boundary_status,
            "boundary_detail": self.boundary_detail,
        }


@dataclass(frozen=True, slots=True)
class FederatedRepositoryOwner:
    requested_path: str
    canonical_path: str
    repository: str
    root: str
    source: str
    repository_identity: RepositoryIdentity | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_path": self.requested_path,
            "canonical_path": self.canonical_path,
            "repository": self.repository,
            "root": self.root,
            "source": self.source,
            "repository_identity": self.repository_identity.as_dict() if self.repository_identity else {},
        }


def _canonical(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _relation(parent_root: Path, child_root: Path) -> str:
    try:
        child_root.relative_to(parent_root)
    except ValueError:
        try:
            parent_root.relative_to(child_root)
        except ValueError:
            return "external"
        return "ancestor"
    return "nested"


def _child_config_path(root: Path) -> Path | None:
    candidate = root / ".devtool.toml"
    if not candidate.exists():
        return None
    if not candidate.is_file():
        raise FederationDiscoveryError(f"Federated repository config path is not a file: {candidate}")
    return candidate.resolve(strict=False)


def resolve_federated_repository(parent: "ProjectConfig", name: str) -> FederatedRepositoryResolution:
    """Resolve one explicitly enrolled child with strict repository boundaries.

    Parent CLI target/config state is deliberately not forwarded.  Child
    resolution starts at the enrolled root, allows only a child-owned
    ``.devtool.toml`` at that root, and then lets the child's own workspace
    default select the target.
    """

    key = str(name or "").strip()
    enrollment = parent.federation.repositories.get(key)
    if enrollment is None:
        available = ", ".join(sorted(parent.federation.repositories)) or "<none>"
        raise FederationDiscoveryError(
            f"Unknown federation repository {key!r}. Available repositories: {available}"
        )

    parent_identity = parent.repository_identity
    parent_owner = _canonical(parent_identity.root if parent_identity else parent.repo_root)
    root = _canonical(enrollment.root)
    relation = _relation(parent_owner, root)

    if not enrollment.available or not root.is_dir():
        if enrollment.required:
            raise FederationDiscoveryError(
                f"Required federation repository {key!r} is unavailable: {root}"
            )
        return FederatedRepositoryResolution(
            name=key,
            declared_path=enrollment.declared_path,
            root=str(root),
            required=enrollment.required,
            available=False,
            relation=relation,
            boundary_status="unavailable",
            boundary_detail="optional repository is not present",
        )

    child_config = _child_config_path(root)

    # Local import avoids a config -> federation_config -> models cycle.
    from .config import load_project_config

    try:
        child = load_project_config(
            str(root),
            str(child_config) if child_config is not None else None,
            target_name="",
            allow_ambiguous_target=True,
            allow_ambiguous_project_kind=True,
            allow_parent_config=False,
        )
    except SystemExit as exc:
        raise FederationDiscoveryError(
            f"Cannot resolve federation repository {key!r} at {root}: {exc}"
        ) from exc

    child_identity = child.repository_identity
    if child_identity is None:
        raise FederationDiscoveryError(
            f"Federation repository {key!r} at {root} has no repository identity"
        )

    child_owner = _canonical(child_identity.root)
    if child_owner != root:
        raise FederationDiscoveryError(
            f"Federation boundary mismatch for {key!r}: enrolled root is {root}, "
            f"but child config is owned by {child_owner}"
        )

    if parent_identity is not None and same_repository_identity(parent_identity, child_identity):
        raise FederationDiscoveryError(
            f"Federation repository {key!r} resolves to the parent repository identity; "
            "parent/child identity collapse is not allowed"
        )

    actual_config = str(child.config_path.resolve(strict=False)) if child.config_path else ""
    if child_config is None and actual_config:
        raise FederationDiscoveryError(
            f"Federation repository {key!r} borrowed configuration outside its boundary: {actual_config}"
        )
    if child_config is not None and actual_config != str(child_config):
        raise FederationDiscoveryError(
            f"Federation repository {key!r} resolved unexpected config {actual_config}; expected {child_config}"
        )

    config_detail = actual_config or "detected defaults (no child .devtool.toml)"
    target_detail = child.selected_target or "<none>"
    return FederatedRepositoryResolution(
        name=key,
        declared_path=enrollment.declared_path,
        root=str(root),
        required=enrollment.required,
        available=True,
        relation=relation,
        config_path=actual_config,
        selected_target=child.selected_target,
        runner=child.runner,
        platform=child.platform,
        repository_identity=child_identity,
        boundary_status="ok",
        boundary_detail=f"child-owned config: {config_detail}; selected target: {target_detail}",
    )


def resolve_federation_repository_owner(
    parent: "ProjectConfig",
    path: str | Path,
) -> FederatedRepositoryOwner | None:
    """Resolve a path to its explicit federation repository owner.

    Candidates are the config-owning parent plus the recursively explicit
    enrollment graph reachable from it.  Filesystem Git discovery is never
    consulted.  Canonical roots are compared after symlink resolution and the
    longest containing enrolled root wins.  Duplicate canonical ownership is
    rejected instead of choosing an arbitrary alias.
    """

    requested = str(Path(path).expanduser())
    canonical = _canonical(path)
    parent_identity = parent.repository_identity
    parent_root = _canonical(parent_identity.root if parent_identity else parent.repo_root)

    candidates: list[tuple[str, Path, str, RepositoryIdentity | None]] = [
        ("@owner", parent_root, "owner", parent_identity),
    ]
    seen_roots: dict[str, str] = {str(parent_root): "@owner"}
    expanded: set[str] = set()

    def visit(config: "ProjectConfig", prefix: tuple[str, ...]) -> None:
        config_identity = config.repository_identity
        config_root = _canonical(config_identity.root if config_identity else config.repo_root)
        config_key = str(config_root)
        if config_key in expanded:
            return
        expanded.add(config_key)

        for name in sorted(config.federation.repositories):
            enrollment = config.federation.repositories[name]
            if not enrollment.available:
                continue
            resolution = resolve_federated_repository(config, name)
            if not resolution.available:
                continue
            root = _canonical(resolution.root)
            qualified = "::".join((*prefix, name))
            key = str(root)
            previous = seen_roots.get(key)
            if previous is not None and previous != qualified:
                raise FederationDiscoveryError(
                    f"Ambiguous federation ownership: {previous!r} and {qualified!r} "
                    f"resolve to canonical root {root}"
                )
            seen_roots[key] = qualified
            candidates.append((qualified, root, "enrolled", resolution.repository_identity))

            # Recurse only by following this child's own explicit federation
            # contract.  This preserves nested autonomy and never treats an
            # unenrolled nested Git checkout as a federation member.
            from .config import load_project_config
            child = load_project_config(
                resolution.root,
                resolution.config_path or None,
                target_name="",
                variant_name="",
                allow_ambiguous_target=True,
                allow_ambiguous_project_kind=True,
                allow_parent_config=False,
            )
            visit(child, (*prefix, name))

    visit(parent, ())

    matches: list[tuple[int, str, Path, str, RepositoryIdentity | None]] = []
    for name, root, source, identity in candidates:
        try:
            canonical.relative_to(root)
        except ValueError:
            continue
        matches.append((len(root.parts), name, root, source, identity))
    if not matches:
        return None

    matches.sort(key=lambda item: item[0], reverse=True)
    longest = matches[0][0]
    winners = [item for item in matches if item[0] == longest]
    roots = {str(item[2]) for item in winners}
    if len(winners) != 1 or len(roots) != 1:
        rendered = ", ".join(sorted(f"{item[1]}={item[2]}" for item in winners))
        raise FederationDiscoveryError(
            f"Ambiguous federation ownership for {canonical}: {rendered}"
        )
    _, name, root, source, identity = winners[0]
    return FederatedRepositoryOwner(
        requested_path=requested,
        canonical_path=str(canonical),
        repository=name,
        root=str(root),
        source=source,
        repository_identity=identity,
    )

def federation_owner_payload(parent: "ProjectConfig", path: str | Path) -> dict[str, Any]:
    owner = resolve_federation_repository_owner(parent, path)
    return {
        "schema_version": 1,
        "path": str(path),
        "owner": owner.as_dict() if owner is not None else None,
    }


def federation_boundary_payload(parent: "ProjectConfig", *, tolerate_errors: bool = False) -> dict[str, Any]:
    repositories: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for name in sorted(parent.federation.repositories):
        try:
            resolution = resolve_federated_repository(parent, name)
        except FederationDiscoveryError as exc:
            if not tolerate_errors:
                raise
            enrollment = parent.federation.repositories[name]
            payload = {
                "name": name,
                "path": enrollment.declared_path,
                "root": enrollment.root,
                "required": enrollment.required,
                "available": enrollment.available,
                "relation": "unknown",
                "config_path": "",
                "selected_target": "",
                "runner": "",
                "platform": "",
                "repository_identity": {},
                "boundary_status": "error",
                "boundary_detail": str(exc),
            }
            repositories.append(payload)
            errors.append({"name": name, "error": str(exc)})
        else:
            repositories.append(resolution.as_dict())

    return {
        "schema_version": 1,
        "enabled": parent.federation.enabled,
        "owner_repository_identity": parent.repository_identity.as_dict() if parent.repository_identity else {},
        "repositories": repositories,
        "groups": {
            name: list(group.repositories)
            for name, group in sorted(parent.federation.groups.items())
        },
        "group_details": {
            name: {
                "repositories": list(group.repositories),
                "members": list(group.members),
                "executable": group.executable,
            }
            for name, group in sorted(parent.federation.groups.items())
        },
        "healthy": not errors,
        "errors": errors,
    }


def federation_inspect_payload(parent: "ProjectConfig", name: str) -> dict[str, Any]:
    resolution = resolve_federated_repository(parent, name)
    return {
        "schema_version": 1,
        "owner_repository_identity": parent.repository_identity.as_dict() if parent.repository_identity else {},
        "repository": resolution.as_dict(),
    }


__all__ = [
    "FederatedRepositoryOwner",
    "FederatedRepositoryResolution",
    "FederationDiscoveryError",
    "federation_boundary_payload",
    "federation_inspect_payload",
    "federation_owner_payload",
    "resolve_federated_repository",
    "resolve_federation_repository_owner",
]
