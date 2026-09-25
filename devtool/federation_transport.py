from __future__ import annotations

"""FRX-09 repository identity and attestation across execution transports.

A provider-resolved path is routing information, not repository identity.  For
federated child execution Devtool therefore carries portable attestation signals
that can be recomputed in a different checkout path before the child phase is
allowed to start.
"""

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any, Mapping

from .invocation_origin import federation_lineage_from_environment
from .repository_identity import RepositoryIdentity


class FederationTransportError(ValueError):
    pass


FEDERATION_ATTESTATION_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class FederationRepositoryAttestation:
    schema_version: int = FEDERATION_ATTESTATION_SCHEMA_VERSION
    config_sha256: str = ""
    git_remote_sha256: str = ""

    @property
    def available(self) -> bool:
        return bool(self.config_sha256 or self.git_remote_sha256)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "config_sha256": self.config_sha256,
            "git_remote_sha256": self.git_remote_sha256,
            "available": self.available,
        }


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
    "federation_attestation_schema_version",
    "federation_attestation_config_sha256",
    "federation_attestation_git_remote_sha256",
    "federation_attestation_required",
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_remote_text(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    # This normalization is intentionally portable to POSIX shell: remove the
    # command's line ending and surrounding whitespace, but never use local paths
    # as identity and never expose the remote URL itself.
    return completed.stdout.strip()


def _git_remote_sha256(root: Path) -> str:
    remote = _git_remote_text(root)
    return hashlib.sha256(remote.encode("utf-8")).hexdigest() if remote else ""


def build_repository_attestation(
    local_root: str | Path,
    *,
    identity: RepositoryIdentity | Mapping[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> FederationRepositoryAttestation:
    """Build path-independent repository evidence from the local checkout.

    A root-local ``.devtool.toml`` digest and the digest of the normalized Git
    origin are portable across different absolute checkout paths.  Either signal
    can independently identify the enrolled repository; when both exist both are
    required to match remotely.
    """

    root = Path(local_root).expanduser().resolve(strict=False)
    identity_obj: RepositoryIdentity | None
    if isinstance(identity, RepositoryIdentity):
        identity_obj = identity
    elif isinstance(identity, Mapping) and identity:
        identity_obj = RepositoryIdentity.from_dict(identity)
    else:
        identity_obj = None

    candidate: Path | None = None
    explicit = str(config_path or "").strip()
    if explicit:
        candidate = Path(explicit).expanduser().resolve(strict=False)
    elif identity_obj is not None and identity_obj.config_path:
        candidate = Path(identity_obj.config_path).expanduser().resolve(strict=False)
    else:
        default = root / ".devtool.toml"
        if default.is_file():
            candidate = default.resolve(strict=False)

    config_sha256 = ""
    if candidate is not None and candidate.is_file():
        try:
            candidate.relative_to(root)
        except ValueError:
            # Federation children are root-owned.  An ancestor/foreign config is
            # not portable child identity and must not become an attestation token.
            candidate = None
        if candidate is not None:
            config_sha256 = _sha256_file(candidate)

    git_remote_sha256 = _git_remote_sha256(root)
    if identity_obj is not None and identity_obj.git_remote_fingerprint:
        if git_remote_sha256 and git_remote_sha256 != identity_obj.git_remote_fingerprint:
            raise FederationTransportError(
                "Federated child Git remote changed after repository identity was resolved"
            )
        # If git is temporarily unavailable locally, retain the already-computed
        # secret-neutral identity signal from FRX-01.
        git_remote_sha256 = git_remote_sha256 or identity_obj.git_remote_fingerprint

    return FederationRepositoryAttestation(
        config_sha256=config_sha256,
        git_remote_sha256=git_remote_sha256,
    )


def federation_attestation_environment(attestation: FederationRepositoryAttestation) -> dict[str, str]:
    return {
        "DEVTOOL_FEDERATION_ATTESTATION_SCHEMA_VERSION": str(attestation.schema_version),
        "DEVTOOL_FEDERATION_ATTESTATION_AVAILABLE": "1" if attestation.available else "0",
        "DEVTOOL_FEDERATION_ATTESTATION_CONFIG_SHA256": attestation.config_sha256,
        "DEVTOOL_FEDERATION_ATTESTATION_GIT_REMOTE_SHA256": attestation.git_remote_sha256,
    }


def federation_attestation_from_environment(environment: Mapping[str, str] | None = None) -> FederationRepositoryAttestation:
    import os

    env = os.environ if environment is None else environment
    raw_schema = str(env.get("DEVTOOL_FEDERATION_ATTESTATION_SCHEMA_VERSION") or "").strip()
    if not raw_schema:
        return FederationRepositoryAttestation()
    try:
        schema = int(raw_schema)
    except ValueError as exc:
        raise FederationTransportError("Invalid federated repository attestation schema") from exc
    if schema != FEDERATION_ATTESTATION_SCHEMA_VERSION:
        raise FederationTransportError(
            f"Unsupported federated repository attestation schema {schema}; expected {FEDERATION_ATTESTATION_SCHEMA_VERSION}"
        )
    raw_available = str(env.get("DEVTOOL_FEDERATION_ATTESTATION_AVAILABLE") or "").strip()
    if raw_available not in {"0", "1"}:
        raise FederationTransportError("Invalid federated repository attestation availability marker")
    attestation = FederationRepositoryAttestation(
        schema_version=schema,
        config_sha256=str(env.get("DEVTOOL_FEDERATION_ATTESTATION_CONFIG_SHA256") or "").strip(),
        git_remote_sha256=str(env.get("DEVTOOL_FEDERATION_ATTESTATION_GIT_REMOTE_SHA256") or "").strip(),
    )
    if (raw_available == "1") != attestation.available:
        raise FederationTransportError(
            "Federated repository attestation availability marker does not match its portable identity signals"
        )
    return attestation


def require_federation_remote_attestation_environment(
    environment: Mapping[str, str] | None = None,
) -> FederationRepositoryAttestation:
    """Fail closed before whole-run remote delegation of a federation child."""

    import os

    env = os.environ if environment is None else environment
    relationship = str(env.get("DEVTOOL_FEDERATION_RELATIONSHIP") or "").strip().lower()
    parent_run_id = str(env.get("DEVTOOL_FEDERATION_PARENT_RUN_ID") or "").strip()
    if relationship != "child" and not parent_run_id:
        return FederationRepositoryAttestation()
    expected = federation_attestation_from_environment(env)
    if not expected.available:
        raise FederationTransportError(
            "Federated whole-run remote execution requires a portable repository attestation signal"
        )
    return expected


def verify_federation_environment_attestation(
    repo_root: str | Path,
    *,
    config_path: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> FederationRepositoryAttestation:
    """Verify attestation carried by a federated remote Devtool re-entry.

    No attestation environment means an ordinary/local load.  If one or more
    expected signals are present, every present signal must match the repository
    being loaded at its current path.
    """

    expected = federation_attestation_from_environment(environment)
    if not expected.available:
        return expected
    actual = build_repository_attestation(repo_root, config_path=config_path)
    if expected.config_sha256 and actual.config_sha256 != expected.config_sha256:
        raise FederationTransportError(
            "Federated repository attestation mismatch: .devtool.toml digest does not identify the enrolled child"
        )
    if expected.git_remote_sha256 and actual.git_remote_sha256 != expected.git_remote_sha256:
        raise FederationTransportError(
            "Federated repository attestation mismatch: Git remote identity does not identify the enrolled child"
        )
    return actual


def _attestation_values(metadata: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(metadata.get("federation_attestation_config_sha256") or "").strip(),
        str(metadata.get("federation_attestation_git_remote_sha256") or "").strip(),
    )


def _attestation_root_assignment(remote_root: str) -> str:
    """Render a safe shell assignment while preserving managed HOME-relative roots."""

    root = str(remote_root or "").strip()
    if root in {"~", "$HOME"}:
        return 'frx_root="$HOME"'
    for prefix in ("~/", "$HOME/"):
        if root.startswith(prefix):
            tail = root[len(prefix):]
            return 'frx_root="$HOME"/' + shlex.quote(tail)
    return "frx_root=" + shlex.quote(root)


def build_federation_attestation_shell(
    metadata: Mapping[str, Any],
    *,
    remote_root: str,
) -> str:
    """Return a fail-closed POSIX-shell probe for command-mode remote providers."""

    config_sha256, git_remote_sha256 = _attestation_values(metadata)
    required = bool(metadata.get("federation_attestation_required"))
    if not config_sha256 and not git_remote_sha256:
        if required:
            raise FederationTransportError(
                "Federated remote execution has no portable repository attestation signal"
            )
        return ""
    root = str(remote_root or "").strip()
    if not root:
        raise FederationTransportError("Federated repository attestation requires a provider-resolved remote root")

    root_assignment = _attestation_root_assignment(root)
    qconfig = shlex.quote(config_sha256)
    qremote = shlex.quote(git_remote_sha256)
    parts = [
        root_assignment,
        f"frx_expected_config={qconfig}",
        f"frx_expected_remote={qremote}",
        '[ -d "$frx_root" ] || { echo "Devtool federation: remote repository root is missing" >&2; exit 74; }',
        "frx_matched=0",
    ]
    if config_sha256:
        parts.extend([
            'command -v sha256sum >/dev/null 2>&1 || { echo "Devtool federation: sha256sum is required for repository attestation" >&2; exit 74; }',
            '[ -f "$frx_root/.devtool.toml" ] || { echo "Devtool federation: remote .devtool.toml is missing" >&2; exit 74; }',
            'frx_actual_config="$(sha256sum "$frx_root/.devtool.toml")"',
            'frx_actual_config="${frx_actual_config%% *}"',
            '[ "$frx_actual_config" = "$frx_expected_config" ] || { echo "Devtool federation: remote repository config attestation mismatch" >&2; exit 74; }',
            "frx_matched=1",
        ])
    if git_remote_sha256:
        parts.extend([
            'command -v git >/dev/null 2>&1 || { echo "Devtool federation: git is required for repository attestation" >&2; exit 74; }',
            'command -v sha256sum >/dev/null 2>&1 || { echo "Devtool federation: sha256sum is required for repository attestation" >&2; exit 74; }',
            'frx_remote="$(git -C "$frx_root" config --get remote.origin.url 2>/dev/null || true)"',
            '[ -n "$frx_remote" ] || { echo "Devtool federation: remote Git origin identity is missing" >&2; exit 74; }',
            'frx_actual_remote="$(printf %s "$frx_remote" | sha256sum)"',
            'frx_actual_remote="${frx_actual_remote%% *}"',
            '[ "$frx_actual_remote" = "$frx_expected_remote" ] || { echo "Devtool federation: remote Git origin attestation mismatch" >&2; exit 74; }',
            "frx_matched=1",
        ])
    parts.append('[ "$frx_matched" -eq 1 ] || { echo "Devtool federation: repository attestation produced no evidence" >&2; exit 74; }')
    return "; ".join(parts)


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
    remote_required = environment in {
        "chroot", "termux-host-chroot", "proot-distro", "ssh", "ssh-command", "ssh-proot-oci", "oci"
    }
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

    attestation = build_repository_attestation(local, identity=identity)
    if remote_required and not attestation.available:
        raise FederationTransportError(
            "Federated remote repository cannot be independently attested: "
            "the enrolled child has neither a root .devtool.toml nor a Git origin identity"
        )

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
        "federation_attestation_schema_version": attestation.schema_version,
        "federation_attestation_config_sha256": attestation.config_sha256,
        "federation_attestation_git_remote_sha256": attestation.git_remote_sha256,
        "federation_attestation_required": remote_required,
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
    "FEDERATION_ATTESTATION_SCHEMA_VERSION",
    "FederationRepositoryAttestation",
    "FederationTransportError",
    "attach_federation_transport_metadata",
    "build_federation_attestation_shell",
    "build_federation_transport_metadata",
    "build_repository_attestation",
    "federation_attestation_environment",
    "federation_attestation_from_environment",
    "federation_transport_provenance",
    "require_federation_remote_attestation_environment",
    "verify_federation_environment_attestation",
]
