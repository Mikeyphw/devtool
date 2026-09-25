from __future__ import annotations

"""FRX-Y04 repository capability/dependency graph.

Capability identifiers describe architecture contracts, not package-manager
versions.  Provider selection is deterministic and fail-closed: a mandatory
requirement must resolve to exactly one enrolled repository.  Compatibility
aliases are explicit lists, so Devtool never invents a compatible version.
"""

from dataclasses import dataclass, field
from typing import Any

from .models import FederationCapabilityRequirement, ProjectConfig


class FederationCapabilityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CapabilityResolution:
    consumer: str
    requirement: str
    optional: bool
    policy: str
    accepted: tuple[str, ...]
    providers: tuple[str, ...]
    matched_capabilities: tuple[str, ...]
    status: str

    @property
    def resolved(self) -> bool:
        return self.status == "resolved" and len(self.providers) == 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "consumer": self.consumer,
            "requirement": self.requirement,
            "optional": self.optional,
            "policy": self.policy,
            "accepted": list(self.accepted),
            "providers": list(self.providers),
            "matched_capabilities": list(self.matched_capabilities),
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class CapabilityDependencyEdge:
    provider: str
    consumer: str
    requirement: str
    matched_capability: str
    optional: bool
    policy: str

    @property
    def reason(self) -> str:
        return (
            f"{self.consumer} requires {self.requirement}; "
            f"{self.provider} exports {self.matched_capability}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "consumer": self.consumer,
            "requirement": self.requirement,
            "matched_capability": self.matched_capability,
            "optional": self.optional,
            "policy": self.policy,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class FederationCapabilityGraph:
    exports: dict[str, tuple[str, ...]] = field(default_factory=dict)
    requirements: dict[str, tuple[FederationCapabilityRequirement, ...]] = field(default_factory=dict)
    resolutions: tuple[CapabilityResolution, ...] = ()
    edges: tuple[CapabilityDependencyEdge, ...] = ()

    @property
    def healthy(self) -> bool:
        return not any(row.status in {"missing", "ambiguous"} for row in self.resolutions)

    def consumers_of(self, provider: str) -> tuple[CapabilityDependencyEdge, ...]:
        return tuple(edge for edge in self.edges if edge.provider == provider)

    def providers_for(self, consumer: str) -> tuple[CapabilityDependencyEdge, ...]:
        return tuple(edge for edge in self.edges if edge.consumer == consumer)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "healthy": self.healthy,
            "exports": {name: list(values) for name, values in sorted(self.exports.items())},
            "requirements": {
                name: [
                    {
                        "name": req.name,
                        "optional": req.optional,
                        "compatible": list(req.compatible),
                        "policy": req.policy,
                    }
                    for req in values
                ]
                for name, values in sorted(self.requirements.items())
            },
            "resolutions": [row.as_dict() for row in self.resolutions],
            "edges": [edge.as_dict() for edge in self.edges],
        }


def _provider_candidates(config: ProjectConfig, requirement: FederationCapabilityRequirement) -> tuple[tuple[str, str], ...]:
    # Preserve requirement preference order: the exact identifier always wins
    # over an explicitly accepted compatibility alias when the same provider
    # exports both. Repository ordering then makes selection evidence stable.
    accepted = requirement.accepted_capabilities
    rank = {capability: index for index, capability in enumerate(accepted)}
    candidates: list[tuple[str, str]] = []
    for repository, repo in sorted(config.federation.repositories.items()):
        for capability in repo.exports:
            if capability in rank:
                candidates.append((repository, capability))
    candidates.sort(key=lambda item: (rank[item[1]], item[0], item[1]))
    return tuple(candidates)


def build_capability_graph(config: ProjectConfig, *, strict: bool = True) -> FederationCapabilityGraph:
    exports = {
        name: tuple(repo.exports)
        for name, repo in sorted(config.federation.repositories.items())
        if repo.exports
    }
    requirements = {
        name: tuple(repo.requires)
        for name, repo in sorted(config.federation.repositories.items())
        if repo.requires
    }
    resolutions: list[CapabilityResolution] = []
    edges: list[CapabilityDependencyEdge] = []

    for consumer, repo in sorted(config.federation.repositories.items()):
        for requirement in repo.requires:
            candidates = [item for item in _provider_candidates(config, requirement) if item[0] != consumer]
            provider_names = tuple(dict.fromkeys(name for name, _capability in candidates))
            matched = tuple(dict.fromkeys(capability for _name, capability in candidates))
            if len(provider_names) == 1:
                status = "resolved"
                provider = provider_names[0]
                matched_capability = next(cap for name, cap in candidates if name == provider)
                edges.append(CapabilityDependencyEdge(
                    provider=provider,
                    consumer=consumer,
                    requirement=requirement.name,
                    matched_capability=matched_capability,
                    optional=requirement.optional,
                    policy=requirement.policy,
                ))
            elif not provider_names:
                status = "optional-missing" if requirement.optional else "missing"
            else:
                status = "optional-ambiguous" if requirement.optional else "ambiguous"
            row = CapabilityResolution(
                consumer=consumer,
                requirement=requirement.name,
                optional=requirement.optional,
                policy=requirement.policy,
                accepted=requirement.accepted_capabilities,
                providers=provider_names,
                matched_capabilities=matched,
                status=status,
            )
            resolutions.append(row)
            if strict and status == "missing":
                accepted = ", ".join(requirement.accepted_capabilities)
                raise FederationCapabilityError(
                    f"Federation repository {consumer!r} requires capability {requirement.name!r}, "
                    f"but no enrolled repository exports an accepted identifier ({accepted})"
                )
            if strict and status == "ambiguous":
                raise FederationCapabilityError(
                    f"Federation repository {consumer!r} requires capability {requirement.name!r}, "
                    f"but multiple enrolled providers match: {', '.join(provider_names)}"
                )

    return FederationCapabilityGraph(
        exports=exports,
        requirements=requirements,
        resolutions=tuple(resolutions),
        edges=tuple(edges),
    )


def project_capability_dependencies(config: ProjectConfig, workflow):
    """Return a workflow whose step DAG includes declared capability edges."""
    from dataclasses import replace

    graph = build_capability_graph(config, strict=True)
    steps_by_repo: dict[str, list[str]] = {}
    for step in workflow.steps:
        steps_by_repo.setdefault(step.repository, []).append(step.step_id)
    rewritten = []
    for step in workflow.steps:
        deps = list(step.depends_on)
        evidence = []
        for edge in graph.providers_for(step.repository):
            for provider_step in steps_by_repo.get(edge.provider, []):
                if provider_step != step.step_id and provider_step not in deps:
                    deps.append(provider_step)
                    evidence.append(edge.as_dict())
        rewritten.append(replace(
            step,
            depends_on=tuple(deps),
            depends_on_declared=step.depends_on_declared or bool(evidence),
        ))
    return replace(workflow, steps=tuple(rewritten))


__all__ = [
    "FederationCapabilityError",
    "CapabilityResolution",
    "CapabilityDependencyEdge",
    "FederationCapabilityGraph",
    "build_capability_graph",
    "project_capability_dependencies",
]
