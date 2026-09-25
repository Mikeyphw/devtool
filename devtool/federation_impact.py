from __future__ import annotations

"""FRX-Y05 dependency-aware federation impact analysis."""

from dataclasses import dataclass, field, replace
from pathlib import Path
import subprocess
from typing import Any, Iterable

from .federation_capabilities import build_capability_graph
from .federation_plan import materialize_federation_group
from .models import FederationConfig, FederatedWorkflowConfig, ProjectConfig


class FederationImpactError(ValueError):
    pass


def _git(root: str | Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _changed(root: str | Path, since: str) -> tuple[bool, str]:
    path = Path(root)
    if not (path / ".git").exists() and _git(path, "rev-parse", "--git-dir").returncode != 0:
        return False, "not-git"
    if since:
        verify = _git(path, "rev-parse", "--verify", f"{since}^{{commit}}")
        if verify.returncode != 0:
            return False, "unresolved-revision"
        diff = _git(path, "diff", "--quiet", since, "--", ".")
        if diff.returncode not in {0, 1}:
            return False, "git-diff-error"
        untracked = _git(path, "ls-files", "--others", "--exclude-standard")
        if untracked.returncode != 0:
            return False, "git-untracked-error"
        return diff.returncode == 1 or bool(untracked.stdout.strip()), "since"
    status = _git(path, "status", "--porcelain", "--untracked-files=all")
    if status.returncode != 0:
        return False, "git-status-error"
    return bool(status.stdout.strip()), "working-tree"


@dataclass(frozen=True, slots=True)
class FederationImpactReport:
    direct_repositories: tuple[str, ...]
    affected_repositories: tuple[str, ...]
    direct_capabilities: tuple[str, ...]
    affected_capabilities: tuple[str, ...]
    workflows: tuple[str, ...]
    groups: tuple[str, ...]
    interfaces: tuple[str, ...] = ()
    workflow_repositories: dict[str, tuple[str, ...]] = field(default_factory=dict)
    group_repositories: dict[str, tuple[str, ...]] = field(default_factory=dict)
    reasons: dict[str, tuple[dict[str, Any], ...]] = field(default_factory=dict)
    discovery: dict[str, str] = field(default_factory=dict)
    capability_graph: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "direct_repositories": list(self.direct_repositories),
            "affected_repositories": list(self.affected_repositories),
            "direct_capabilities": list(self.direct_capabilities),
            "affected_capabilities": list(self.affected_capabilities),
            "workflows": list(self.workflows),
            "groups": list(self.groups),
            "interfaces": list(self.interfaces),
            "workflow_repositories": {name: list(values) for name, values in sorted(self.workflow_repositories.items())},
            "group_repositories": {name: list(values) for name, values in sorted(self.group_repositories.items())},
            "reasons": {name: [dict(item) for item in rows] for name, rows in sorted(self.reasons.items())},
            "discovery": dict(sorted(self.discovery.items())),
            "capability_graph": dict(self.capability_graph),
        }


def discover_direct_repositories(
    config: ProjectConfig,
    *,
    repositories: Iterable[str] = (),
    since: str = "",
) -> tuple[tuple[str, ...], dict[str, str]]:
    explicit = tuple(dict.fromkeys(str(name).strip() for name in repositories if str(name).strip()))
    unknown = [name for name in explicit if name not in config.federation.repositories]
    if unknown:
        available = ", ".join(sorted(config.federation.repositories)) or "<none>"
        raise FederationImpactError(
            f"Unknown federation repository/repositories: {', '.join(unknown)}. Available: {available}"
        )
    if explicit and not since:
        return explicit, {name: "explicit" for name in explicit}

    candidates = explicit or tuple(sorted(config.federation.repositories))
    direct: list[str] = []
    discovery: dict[str, str] = {}
    unresolved: list[str] = []
    for name in candidates:
        repo = config.federation.repositories[name]
        if not repo.available:
            discovery[name] = "unavailable"
            continue
        changed, reason = _changed(repo.root, since)
        discovery[name] = reason
        if reason == "unresolved-revision":
            unresolved.append(name)
        if changed:
            direct.append(name)
    if since and unresolved:
        raise FederationImpactError(
            f"Revision {since!r} cannot be resolved in federation repository/repositories: {', '.join(unresolved)}"
        )
    return tuple(direct), discovery


def _affected_groups(config: ProjectConfig, affected: set[str]) -> tuple[str, ...]:
    result: list[str] = []
    for name, group in sorted(config.federation.groups.items()):
        if affected.intersection(group.repositories):
            result.append(name)
            continue
        if not group.members:
            continue
        try:
            workflow = materialize_federation_group(config, name)
        except Exception:
            continue
        if any(step.repository in affected for step in workflow.steps):
            result.append(name)
    return tuple(result)


def analyze_federation_impact(
    config: ProjectConfig,
    *,
    repositories: Iterable[str] = (),
    since: str = "",
) -> FederationImpactReport:
    direct, discovery = discover_direct_repositories(config, repositories=repositories, since=since)
    capability_graph = build_capability_graph(config, strict=True)
    affected: set[str] = set(direct)
    reasons: dict[str, list[dict[str, Any]]] = {
        name: [{
            "kind": "direct",
            "repository": name,
            "reason": discovery.get(name, "explicit"),
            "path": [name],
        }]
        for name in direct
    }
    queue = list(direct)
    while queue:
        provider = queue.pop(0)
        for edge in capability_graph.consumers_of(provider):
            provider_rows = reasons.get(edge.provider, [])
            inherited_path = list(provider_rows[0].get("path") or [edge.provider]) if provider_rows else [edge.provider]
            evidence = {
                "kind": "capability",
                "provider": edge.provider,
                "consumer": edge.consumer,
                "capability": edge.requirement,
                "matched_capability": edge.matched_capability,
                "reason": edge.reason,
                "path": [*inherited_path, edge.consumer],
            }
            reasons.setdefault(edge.consumer, []).append(evidence)
            if edge.consumer not in affected:
                affected.add(edge.consumer)
                queue.append(edge.consumer)

    direct_capabilities = sorted({
        capability
        for repository in direct
        for capability in config.federation.repositories[repository].exports
    })
    affected_capabilities = sorted({
        capability
        for repository in affected
        for capability in config.federation.repositories[repository].exports
    })
    workflow_repositories = {
        name: tuple(sorted({step.repository for step in workflow.steps if step.repository in affected}))
        for name, workflow in config.federation.workflows.items()
        if any(step.repository in affected for step in workflow.steps)
    }
    workflows = tuple(sorted(workflow_repositories))
    groups = _affected_groups(config, affected)
    group_repositories: dict[str, tuple[str, ...]] = {}
    for group_name in groups:
        group = config.federation.groups[group_name]
        members = set(group.repositories).intersection(affected)
        if group.members:
            try:
                expanded = materialize_federation_group(config, group_name)
            except Exception:
                expanded = None
            if expanded is not None:
                members.update(step.repository for step in expanded.steps if step.repository in affected)
        group_repositories[group_name] = tuple(sorted(members))

    # Y06 adds concrete interface contracts. Keeping this projection generic
    # lets interface ownership plug in without changing Y05's reasoning engine.
    interfaces: list[str] = []
    configured_interfaces = getattr(config.federation, "interfaces", {})
    if isinstance(configured_interfaces, dict):
        for name, interface in sorted(configured_interfaces.items()):
            members = set(getattr(interface, "repositories", ()) or ())
            if members.intersection(affected):
                interfaces.append(str(name))

    return FederationImpactReport(
        direct_repositories=tuple(direct),
        affected_repositories=tuple(sorted(affected)),
        direct_capabilities=tuple(direct_capabilities),
        affected_capabilities=tuple(affected_capabilities),
        workflows=workflows,
        groups=groups,
        interfaces=tuple(interfaces),
        workflow_repositories=workflow_repositories,
        group_repositories=group_repositories,
        reasons={name: tuple(rows) for name, rows in reasons.items()},
        discovery=discovery,
        capability_graph=capability_graph.as_dict(),
    )


def explain_federation_impact(report: FederationImpactReport, subject: str) -> dict[str, Any]:
    value = str(subject or "").strip()
    repository = value.split("::", 1)[0] if "::" in value else value
    if repository in report.reasons or repository in report.affected_repositories:
        return {
            "subject": value,
            "kind": "repository" if value == repository else "node",
            "affected": repository in report.affected_repositories,
            "repository": repository,
            "reasons": [dict(item) for item in report.reasons.get(repository, ())],
        }
    if value in report.workflows:
        repositories = report.workflow_repositories.get(value, ())
        return {
            "subject": value,
            "kind": "workflow",
            "affected": True,
            "repositories": list(repositories),
            "reasons": [
                {
                    "kind": "workflow",
                    "workflow": value,
                    "repository": repository,
                    "reason": "workflow contains an affected repository step",
                    "repository_reasons": [dict(item) for item in report.reasons.get(repository, ())],
                }
                for repository in repositories
            ],
        }
    if value in report.groups:
        repositories = report.group_repositories.get(value, ())
        return {
            "subject": value,
            "kind": "group",
            "affected": True,
            "repositories": list(repositories),
            "reasons": [
                {
                    "kind": "group",
                    "group": value,
                    "repository": repository,
                    "reason": "group contains an affected repository/workflow",
                    "repository_reasons": [dict(item) for item in report.reasons.get(repository, ())],
                }
                for repository in repositories
            ],
        }
    return {"subject": value, "kind": "unknown", "affected": False, "reasons": []}


def project_affected_workflow(
    config: ProjectConfig,
    workflow_name: str,
    affected_repositories: Iterable[str],
) -> ProjectConfig | None:
    name = str(workflow_name or "").strip()
    workflow = config.federation.workflows.get(name)
    if workflow is None:
        raise FederationImpactError(f"Unknown federation workflow {name!r}")
    affected = set(affected_repositories)
    selected_ids = {step.step_id for step in workflow.steps if step.repository in affected}
    if not selected_ids:
        return None

    # Affected selection is an execution filter, not permission to violate the
    # workflow DAG. Include the complete prerequisite closure of every affected
    # step so an explicitly declared setup/provider dependency is never silently
    # dropped merely because its repository did not itself change.
    by_id = {step.step_id: step for step in workflow.steps}
    queue = list(selected_ids)
    while queue:
        step_id = queue.pop()
        step = by_id.get(step_id)
        if step is None:
            continue
        for dependency in step.depends_on:
            if dependency not in selected_ids:
                selected_ids.add(dependency)
                queue.append(dependency)

    kept = [step for step in workflow.steps if step.step_id in selected_ids]
    projected = tuple(replace(step, depends_on=tuple(step.depends_on)) for step in kept)
    scoped_workflow = FederatedWorkflowConfig(name=name, steps=projected)
    federation = FederationConfig(
        schema=config.federation.schema,
        repositories=dict(config.federation.repositories),
        groups=dict(config.federation.groups),
        workflows={**config.federation.workflows, name: scoped_workflow},
    )
    return replace(config, federation=federation)


__all__ = [
    "FederationImpactError",
    "FederationImpactReport",
    "discover_direct_repositories",
    "analyze_federation_impact",
    "explain_federation_impact",
    "project_affected_workflow",
]
