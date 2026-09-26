from __future__ import annotations

"""FRX-Y09 provider/worker capability projection for federation scheduling."""

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .execution_environments import AUTO, SSH, PROOT_DISTRO, detect_current_environment
from .ssh_profiles import list_profiles
from .ssh_provider_evidence import load_provider_evidence


@dataclass(frozen=True, slots=True)
class FederationProviderCapability:
    provider_id: str
    kind: str
    profile: str = ""
    execution_environments: tuple[str, ...] = ()
    healthy: bool = True
    evidence_source: str = ""
    evidence_digest: str = ""
    resources: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "kind": self.kind,
            "profile": self.profile,
            "execution_environments": list(self.execution_environments),
            "healthy": self.healthy,
            "evidence_source": self.evidence_source,
            "evidence_digest": self.evidence_digest,
            "resources": dict(self.resources),
            "metadata": dict(self.metadata),
        }


def local_provider_capability() -> FederationProviderCapability:
    current = detect_current_environment()
    return FederationProviderCapability(
        provider_id="local",
        kind="local",
        execution_environments=(AUTO, current),
        healthy=True,
        evidence_source="controller-runtime",
        resources={"local-worker": 1},
        metadata={"current_environment": current},
    )


def _from_ssh_record(profile_row: Mapping[str, Any], evidence: Mapping[str, Any]) -> FederationProviderCapability:
    profile = str(profile_row.get("name") or evidence.get("profile") or "").strip()
    kind = str(evidence.get("execution_provider") or profile_row.get("execution_provider") or "ssh-native").strip()
    environments = tuple(str(value) for value in (evidence.get("execution_environments") or [SSH]) if str(value))
    resources = {
        str(key): max(1, int(value))
        for key, value in dict(evidence.get("resources") or {}).items()
        if str(key) and not isinstance(value, bool) and isinstance(value, int) and int(value) > 0
    }
    return FederationProviderCapability(
        provider_id=f"ssh:{profile}",
        kind=kind,
        profile=profile,
        execution_environments=environments or (SSH,),
        healthy=bool(evidence.get("ok", False)),
        evidence_source=str(evidence.get("source") or "ssh-provider-evidence"),
        evidence_digest=str(evidence.get("evidence_digest") or ""),
        resources=resources,
        metadata={
            "scope": str(profile_row.get("scope") or ""),
            "default": bool(profile_row.get("default")),
            "worker": dict(evidence.get("worker") or {}),
            "capacity": dict(evidence.get("capacity") or {}),
            "tools": list(evidence.get("tools") or []),
            "qualified_target": str(evidence.get("target") or ""),
        },
    )


def load_federation_provider_capabilities(parent: Any, *, target: str = "") -> tuple[FederationProviderCapability, ...]:
    """Load provider choices without probing any remote worker.

    The only remote providers admitted are named SSH profiles with persisted
    SSH-01/03 provider evidence. Missing/stale evidence is a scheduling reason,
    not a trigger for an independent federation probe.
    """

    result: list[FederationProviderCapability] = [local_provider_capability()]
    try:
        profiles = list_profiles(repo=parent.repo_root, config_path=parent.config_path or "")
    except (OSError, ValueError):
        profiles = {"records": []}
    for row in profiles.get("records", []) if isinstance(profiles, Mapping) else []:
        if not isinstance(row, Mapping):
            continue
        profile = str(row.get("name") or "").strip()
        if not profile:
            continue
        evidence = load_provider_evidence(profile, target=target)
        if evidence is None:
            continue
        result.append(_from_ssh_record(row, evidence))
    return tuple(result)


def provider_matches_environment(provider: FederationProviderCapability, allowed: Sequence[str]) -> bool:
    values = tuple(str(value or "").strip() for value in allowed if str(value or "").strip())
    if not values or AUTO in values:
        return provider.healthy
    return provider.healthy and bool(set(values).intersection(provider.execution_environments))


def provider_execution_environment(provider: FederationProviderCapability, allowed: Sequence[str], preferred: str = "") -> str:
    preferred = str(preferred or "").strip()
    if preferred and preferred != AUTO and preferred in provider.execution_environments:
        return preferred
    explicit = [str(value) for value in allowed if str(value) and str(value) != AUTO]
    for value in explicit:
        if value in provider.execution_environments:
            return value
    if provider.profile:
        return PROOT_DISTRO if PROOT_DISTRO in provider.execution_environments else SSH
    # Preserve the historical local ``auto`` contract.  Provider placement is
    # authoritative about *where* the child runs, but a local provider must not
    # eagerly collapse an auto child contract into the controller's detected
    # environment.  The child execution layer remains the authority that
    # resolves ``auto``.  Remote providers are explicit above because transport
    # selection has already crossed the SSH/provider boundary.
    if AUTO in tuple(str(value) for value in allowed):
        return AUTO
    return str(provider.metadata.get("current_environment") or AUTO)


__all__ = [
    "FederationProviderCapability",
    "load_federation_provider_capabilities",
    "local_provider_capability",
    "provider_execution_environment",
    "provider_matches_environment",
]
