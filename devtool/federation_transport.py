from __future__ import annotations

"""FRX-09 repository identity contract across execution transports.

The transport layer never derives a remote repository path from the local path.
Each execution provider supplies its authoritative translated/managed remote
root, while federation contributes repository identity and parent/child EXO
lineage.  This module validates and joins those two pieces of evidence.
"""

from pathlib import Path
import re
from typing import Any, Mapping

from .invocation_origin import federation_lineage_from_environment
from .repository_identity import RepositoryIdentity


class FederationTransportError(ValueError):
    pass


_TRANSPORT_KEYS = (
    "federation_source_repository",
    "federation_child_repository",
    "federation_child_repository_fingerprint",
    "federation_child_local_root",
    "federation_child_remote_root",
    "federation_execution_environment",
    "federation_execution_provider",
    "federation_parent_run_id",
    "federation_parent_node_id",
    "federation_child_run_id",
    "federation_artifact_return_root",
    "federation_path_mapping",
)


def _canonical(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve(strict=False))


def _safe_component(value: str, fallback: str = "child") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip(".-")
    return text[:96] or fallback


def _lineage(metadata: Mapping[str, Any]) -> dict[str, Any]:
    value = metadata.get("run_lineage")
    if isinstance(value, Mapping) and str(value.get("kind") or "") == "federation":
        return {str(k): v for k, v in value.items()}
    env = federation_lineage_from_environment()
    return {str(k): v for k, v in env.items()} if env else {}


def _identity(metadata: Mapping[str, Any], fallback_identity: Mapping[str, Any] | None) -> dict[str, Any]:
    value = metadata.get("repository_identity")
    if isinstance(value, Mapping) and value:
        return {str(k): v for k, v in value.items()}
    return {str(k): v for k, v in (fallback_identity or {}).items()}


def build_federation_transport_metadata(
    metadata: Mapping[str, Any],
    *,
    local_root: str | Path,
    execution_environment: str,
    remote_root: str | Path | None = None,
    execution_provider: str = "",
    artifact_return_root: str | Path | None = None,
    path_mapping: str = "",
    fallback_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    lineage = _lineage(metadata)
    if not lineage or str(lineage.get("relationship") or "") != "child":
        return {}
    identity_payload = _identity(metadata, fallback_identity)
    if not identity_payload:
        raise FederationTransportError("Federated child transport is missing repository identity")
    try:
        identity = RepositoryIdentity.from_dict(identity_payload)
    except Exception as exc:
        raise FederationTransportError(f"Federated child repository identity is invalid: {exc}") from exc
    if not identity.fingerprint or not identity.root:
        raise FederationTransportError("Federated child repository identity is incomplete")

    effective = str(lineage.get("effective_repository") or "").strip()
    if effective and effective not in {identity.fingerprint, identity.root}:
        raise FederationTransportError(
            "Federated child transport identity mismatch: "
            f"lineage effective_repository={effective!r}, identity={identity.fingerprint!r}"
        )

    local = _canonical(local_root)
    identity_root = _canonical(identity.root)
    requested_root = _canonical(identity.requested_root or identity.root)
    # The project root may be below the config owner, but it cannot be outside
    # the identity's requested/owner boundary.
    try:
        Path(local).relative_to(Path(identity_root))
        local_owned = True
    except ValueError:
        try:
            Path(local).relative_to(Path(requested_root))
            local_owned = True
        except ValueError:
            local_owned = False
    if not local_owned:
        raise FederationTransportError(
            f"Federated child local root {local} is outside repository identity root {identity_root}"
        )

    environment = str(execution_environment or "").strip() or "local"
    remote = str(remote_root or "").strip()
    remote_required = environment in {"chroot", "termux-host-chroot", "proot-distro", "ssh", "ssh-command", "ssh-proot-oci", "oci"}
    if remote_required and not remote:
        raise FederationTransportError(
            f"Federated child transport {environment!r} requires an explicit provider-resolved remote repository root"
        )
    if remote and environment not in {"ssh", "ssh-command", "ssh-proot-oci"} and not remote.startswith("/"):
        raise FederationTransportError(
            f"Federated child remote repository root must be absolute for {environment}: {remote!r}"
        )
    if not remote:
        remote = local

    repository = str(lineage.get("repository") or lineage.get("requested_repository") or "").strip()
    child_run_id = str(lineage.get("child_run_id") or "").strip()
    return_root = str(artifact_return_root or "").strip()
    if not return_root:
        return_root = str(Path(local) / ".devtool" / "federation-returns" / _safe_component(repository) / _safe_component(child_run_id or "run"))

    mapping = str(path_mapping or "").strip()
    if not mapping:
        if environment in {"ssh", "ssh-command"}:
            mapping = "ssh-workspace"
        elif environment in {"ssh-proot-oci", "oci"}:
            mapping = "oci-workspace"
        elif environment in {"chroot", "termux-host-chroot"}:
            mapping = "chroot-path-map"
        elif environment == "proot-distro":
            mapping = "proot-bind"
        else:
            mapping = "identity"

    return {
        "federation_source_repository": str(lineage.get("source_repository") or ""),
        "federation_child_repository": repository,
        "federation_child_repository_fingerprint": identity.fingerprint,
        "federation_child_local_root": local,
        "federation_child_remote_root": remote,
        "federation_execution_environment": environment,
        "federation_execution_provider": str(execution_provider or ""),
        "federation_parent_run_id": str(lineage.get("parent_run_id") or ""),
        "federation_parent_node_id": str(lineage.get("parent_node_id") or ""),
        "federation_child_run_id": child_run_id,
        "federation_artifact_return_root": return_root,
        "federation_path_mapping": mapping,
    }


def attach_federation_transport_metadata(
    metadata: Mapping[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    updated = dict(metadata)
    transport = build_federation_transport_metadata(updated, **kwargs)
    if transport:
        updated.update(transport)
    return updated


def federation_transport_provenance(metadata: Mapping[str, Any]) -> dict[str, Any]:
    payload = {
        key.removeprefix("federation_"): metadata.get(key)
        for key in _TRANSPORT_KEYS
        if metadata.get(key) not in (None, "")
    }
    if not payload:
        return {}
    return {"federation": payload}


__all__ = [
    "FederationTransportError",
    "attach_federation_transport_metadata",
    "build_federation_transport_metadata",
    "federation_transport_provenance",
]
