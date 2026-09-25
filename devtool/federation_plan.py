from __future__ import annotations

"""FRX-05 federated planning.

This module is intentionally planning-only. It resolves child-owned configs,
expands existing target workflow graphs, namespaces every child node by
repository, adds explicit cross-repository dependencies, and serializes a
stable DAG. It never executes a child command.
"""

from dataclasses import dataclass, field, replace
import heapq
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .federation_discovery import FederationDiscoveryError, resolve_federated_repository
from .workflow_runtime import WorkflowRuntimeError, plan_workspace_targets

if TYPE_CHECKING:
    from .models import ProjectConfig, FederatedWorkflowConfig


class FederationPlanError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FederatedPlanNode:
    key: str
    repository: str
    target: str
    workflow: str
    local_key: str
    depends_on: tuple[str, ...] = ()
    index: int = 0
    synthetic: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def federation_ref(self) -> str:
        if self.synthetic or not self.target:
            return ""
        return f"{self.repository}::{self.target}::{self.workflow}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "federation_ref": self.federation_ref,
            "repository": self.repository,
            "target": self.target,
            "workflow": self.workflow,
            "local_key": self.local_key,
            "depends_on": list(self.depends_on),
            "index": self.index,
            "synthetic": self.synthetic,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class FederatedPlanGraph:
    workflow: str
    nodes: tuple[FederatedPlanNode, ...]
    repositories: tuple[str, ...]

    @property
    def by_key(self) -> dict[str, FederatedPlanNode]:
        return {node.key: node for node in self.nodes}

    @property
    def roots(self) -> tuple[FederatedPlanNode, ...]:
        return tuple(node for node in self.nodes if not node.depends_on)

    @property
    def leaves(self) -> tuple[FederatedPlanNode, ...]:
        used = {dep for node in self.nodes for dep in node.depends_on}
        return tuple(node for node in self.nodes if node.key not in used)

    def topological_order(self) -> tuple[FederatedPlanNode, ...]:
        by_key = self.by_key
        if len(by_key) != len(self.nodes):
            raise FederationPlanError(f"Federation workflow {self.workflow!r} contains duplicate graph node keys")
        indegree = {node.key: len(node.depends_on) for node in self.nodes}
        dependents: dict[str, list[str]] = {node.key: [] for node in self.nodes}
        order_index = {node.key: node.index for node in self.nodes}
        for node in self.nodes:
            for dependency in node.depends_on:
                if dependency not in by_key:
                    raise FederationPlanError(
                        f"Federation node {node.key!r} depends on unknown node {dependency!r}"
                    )
                if dependency == node.key:
                    raise FederationPlanError(f"Federation node {node.key!r} cannot depend on itself")
                dependents[dependency].append(node.key)
        ready = [(order_index[key], key) for key, degree in indegree.items() if degree == 0]
        heapq.heapify(ready)
        result: list[FederatedPlanNode] = []
        while ready:
            _, key = heapq.heappop(ready)
            result.append(by_key[key])
            for dependent in sorted(dependents[key], key=lambda item: order_index[item]):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    heapq.heappush(ready, (order_index[dependent], dependent))
        if len(result) != len(self.nodes):
            blocked = sorted(key for key, degree in indegree.items() if degree > 0)
            raise FederationPlanError(
                f"Federation dependency cycle detected in {self.workflow!r}: {', '.join(blocked)}"
            )
        return tuple(result)

    @property
    def group(self) -> str:
        return self.workflow.split(":", 1)[1] if self.workflow.startswith("group:") else ""

    def as_dict(self) -> dict[str, Any]:
        ordered = self.topological_order()
        return {
            "schema_version": 1,
            "subject_kind": "group" if self.group else "workflow",
            "group": self.group,
            "workflow": self.workflow,
            "repositories": list(self.repositories),
            "roots": [node.key for node in self.roots],
            "leaves": [node.key for node in self.leaves],
            "order": [node.key for node in ordered],
            "nodes": [node.as_dict() for node in self.nodes],
        }


def _load_child(parent: "ProjectConfig", repository: str) -> "ProjectConfig":
    resolution = resolve_federated_repository(parent, repository)
    if not resolution.available:
        raise FederationPlanError(
            f"Federation workflow requires unavailable optional repository {repository!r}: {resolution.root}"
        )
    from .config import load_project_config
    try:
        child = load_project_config(
            resolution.root,
            resolution.config_path or None,
            target_name="",
            allow_ambiguous_target=True,
            allow_ambiguous_project_kind=True,
            allow_parent_config=False,
        )
    except SystemExit as exc:
        raise FederationPlanError(f"Cannot load federation repository {repository!r}: {exc}") from exc
    identity = child.repository_identity
    if identity is None or Path(identity.root).resolve(strict=False) != Path(resolution.root).resolve(strict=False):
        raise FederationPlanError(f"Federation repository {repository!r} changed ownership while planning")
    return child


def _child_graph(
    parent: "ProjectConfig",
    repository: str,
    workflow: str,
    requested_target: str = "",
):
    child = _load_child(parent, repository)
    explicit_target = str(requested_target or "").strip()
    target = explicit_target or str(child.selected_target or "").strip()
    if explicit_target:
        if target not in child.targets:
            available = ", ".join(sorted(child.targets)) or "<none>"
            raise FederationPlanError(
                f"Federation repository {repository!r} has no target {target!r} for "
                f"target-qualified workflow {repository}::{target}::{workflow}. "
                f"Available targets: {available}"
            )
    elif not target:
        if len(child.targets) == 1:
            target = next(iter(child.targets))
        else:
            available = ", ".join(sorted(child.targets)) or "<none>"
            raise FederationPlanError(
                f"Federation repository {repository!r} has no unambiguous selected target for workflow {workflow!r}. "
                f"Available targets: {available}. Use repository::target::workflow to select one explicitly."
            )
    target_config = child.targets.get(target)
    if target_config is None or workflow not in target_config.workflows:
        available = ", ".join(sorted(target_config.workflows)) if target_config is not None else "<none>"
        ref = f"{repository}::{target}::{workflow}"
        raise FederationPlanError(
            f"Federation repository {repository!r} target {target!r} has no workflow {workflow!r} "
            f"for {ref}. Available workflows: {available or '<none>'}"
        )
    try:
        graph = plan_workspace_targets(child, [target], workflow)
    except WorkflowRuntimeError as exc:
        raise FederationPlanError(str(exc)) from exc
    return child, target, graph


def _ordered_steps(workflow: "FederatedWorkflowConfig"):
    by_id = {step.step_id: step for step in workflow.steps}
    indegree = {step.step_id: len(step.depends_on) for step in workflow.steps}
    dependents: dict[str, list[str]] = {step.step_id: [] for step in workflow.steps}
    declared = {step.step_id: index for index, step in enumerate(workflow.steps)}
    for step in workflow.steps:
        for dependency in step.depends_on:
            dependents[dependency].append(step.step_id)
    ready = [(declared[key], key) for key, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    result = []
    while ready:
        _, key = heapq.heappop(ready)
        result.append(by_id[key])
        for dependent in sorted(dependents[key], key=lambda item: declared[item]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(ready, (declared[dependent], dependent))
    if len(result) != len(workflow.steps):
        blocked = sorted(key for key, degree in indegree.items() if degree > 0)
        raise FederationPlanError(
            f"Federation dependency cycle detected in {workflow.name!r}: {', '.join(blocked)}"
        )
    return tuple(result)




def materialize_federation_group(parent: "ProjectConfig", group_name: str):
    """Expand one executable federation group into a synthetic workflow.

    Groups deliberately compile down to the existing FRX workflow contract so
    planning and execution use exactly the same child discovery, target
    resolution, dependency, ownership, and EXO machinery as named workflows.
    """

    from .models import FederatedWorkflowConfig, FederatedWorkflowStep

    name = str(group_name or "").strip()
    group = parent.federation.groups.get(name)
    if group is None:
        available = ", ".join(sorted(parent.federation.groups)) or "<none>"
        raise FederationPlanError(f"Unknown federation group {name!r}. Available groups: {available}")
    if not group.members:
        raise FederationPlanError(
            f"Federation group {name!r} has no executable members. "
            "Add members = [\"workflow-name\", \"repository::target::workflow\"] "
            "or use the group only as repository metadata."
        )

    steps: list[FederatedWorkflowStep] = []
    stack: list[str] = []
    sequence = 0

    def append_direct(ref: str, *, prefix: str) -> None:
        nonlocal sequence
        parts = [part.strip() for part in ref.split("::")]
        if len(parts) == 2:
            repository, workflow = parts
            target = ""
        elif len(parts) == 3:
            repository, target, workflow = parts
        else:
            raise FederationPlanError(f"Invalid normalized federation group child ref {ref!r}")
        sequence += 1
        steps.append(FederatedWorkflowStep(
            step_id=f"g{sequence:03d}.{prefix}.direct",
            ref=ref,
            repository=repository,
            target=target,
            workflow=workflow,
            depends_on=(),
            depends_on_declared=True,
        ))

    def append_workflow(workflow_name: str, *, prefix: str) -> None:
        nonlocal sequence
        workflow = parent.federation.workflows.get(workflow_name)
        if workflow is None:
            available = ", ".join(sorted(parent.federation.workflows)) or "<none>"
            raise FederationPlanError(
                f"Federation group {name!r} references unknown workflow {workflow_name!r}. "
                f"Available workflows: {available}"
            )
        id_map: dict[str, str] = {}
        base = sequence
        for offset, step in enumerate(workflow.steps, 1):
            id_map[step.step_id] = f"g{base + offset:03d}.{prefix}.{workflow_name}.{step.step_id}"
        for step in workflow.steps:
            sequence += 1
            steps.append(FederatedWorkflowStep(
                step_id=id_map[step.step_id],
                ref=step.ref,
                repository=step.repository,
                target=step.target,
                workflow=step.workflow,
                depends_on=tuple(id_map[dep] for dep in step.depends_on),
                depends_on_declared=step.depends_on_declared,
            ))

    def visit(current: str, *, prefix: str) -> None:
        if current in stack:
            start = stack.index(current)
            cycle = [*stack[start:], current]
            raise FederationPlanError(f"Federation group cycle detected while planning: {' -> '.join(cycle)}")
        nested = parent.federation.groups.get(current)
        if nested is None:
            available = ", ".join(sorted(parent.federation.groups)) or "<none>"
            raise FederationPlanError(f"Unknown federation group {current!r}. Available groups: {available}")
        if not nested.members:
            raise FederationPlanError(
                f"Federation group {current!r} is metadata-only and cannot be expanded by executable group {name!r}"
            )
        stack.append(current)
        try:
            for index, member in enumerate(nested.members, 1):
                member_prefix = f"{prefix}.m{index:02d}"
                if member.startswith("workflow:"):
                    append_workflow(member.split(":", 1)[1], prefix=member_prefix)
                elif member.startswith("group:"):
                    visit(member.split(":", 1)[1], prefix=member_prefix)
                elif "::" in member:
                    append_direct(member, prefix=member_prefix)
                else:
                    raise FederationPlanError(
                        f"Federation group {current!r} contains unresolved member {member!r}"
                    )
        finally:
            stack.pop()

    visit(name, prefix=name)
    return FederatedWorkflowConfig(name=f"group:{name}", steps=tuple(steps))


def project_with_federation_group(parent: "ProjectConfig", group_name: str):
    """Return a shallow ProjectConfig copy with one materialized group workflow."""

    from .models import FederationConfig

    workflow = materialize_federation_group(parent, group_name)
    federation = FederationConfig(
        schema=parent.federation.schema,
        repositories=dict(parent.federation.repositories),
        groups=dict(parent.federation.groups),
        workflows={**parent.federation.workflows, workflow.name: workflow},
    )
    return replace(parent, federation=federation), workflow


def build_federated_group_plan(parent: "ProjectConfig", group_name: str) -> FederatedPlanGraph:
    scoped, workflow = project_with_federation_group(parent, group_name)
    return build_federated_plan(scoped, workflow.name)


def build_federated_plan(parent: "ProjectConfig", workflow_name: str) -> FederatedPlanGraph:
    name = str(workflow_name or "").strip()
    workflow: FederatedWorkflowConfig | None = parent.federation.workflows.get(name)
    if workflow is None:
        available = ", ".join(sorted(parent.federation.workflows)) or "<none>"
        raise FederationPlanError(f"Unknown federation workflow {name!r}. Available workflows: {available}")

    nodes: list[FederatedPlanNode] = []
    invocation_bounds: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    seen_keys: set[str] = set()
    repositories: list[str] = []
    next_index = 1

    ordered_steps = _ordered_steps(workflow)
    for step in ordered_steps:
        child, target, local_graph = _child_graph(
            parent, step.repository, step.workflow, step.target
        )
        if step.repository not in repositories:
            repositories.append(step.repository)
        local_roots = {node.key for node in local_graph.roots}
        local_leaves = {node.key for node in local_graph.leaves}
        external_dependencies: list[str] = []
        for dependency_id in step.depends_on:
            bounds = invocation_bounds.get(dependency_id)
            if bounds is None:
                raise FederationPlanError(
                    f"Federation workflow {name!r} has unresolved dependency {dependency_id!r} for step {step.step_id!r}"
                )
            external_dependencies.extend(bounds[1])
        prefixed_roots: list[str] = []
        prefixed_leaves: list[str] = []
        identity = child.repository_identity.as_dict() if child.repository_identity else {}
        for local in local_graph.topological_order():
            key = f"{step.repository}::{local.key}"
            if key in seen_keys:
                raise FederationPlanError(
                    f"Federation workflow {name!r} invokes duplicate child graph node {key!r}; "
                    "duplicate repository/target/workflow invocations require distinct child graph identities"
                )
            seen_keys.add(key)
            deps = [f"{step.repository}::{dep}" for dep in local.depends_on]
            if local.key in local_roots:
                deps.extend(external_dependencies)
                prefixed_roots.append(key)
            if local.key in local_leaves:
                prefixed_leaves.append(key)
            nodes.append(FederatedPlanNode(
                key=key,
                repository=step.repository,
                target=target,
                workflow=step.workflow,
                local_key=local.key,
                depends_on=tuple(dict.fromkeys(deps)),
                index=next_index,
                metadata={
                    "federation_step": step.step_id,
                    "federation_ref": step.canonical_ref(target),
                    "requested_ref": step.ref,
                    "target_explicit": step.target_qualified,
                    "repository_identity": identity,
                    "child_metadata": dict(local.metadata),
                },
            ))
            next_index += 1
        invocation_bounds[step.step_id] = (tuple(prefixed_roots), tuple(prefixed_leaves))

    # A synthetic aggregate makes the top-level federation workflow explicit
    # without inventing an executable child command.
    depended_steps = {dep for step in workflow.steps for dep in step.depends_on}
    terminal_steps = [step for step in workflow.steps if step.step_id not in depended_steps]
    aggregate_deps: list[str] = []
    for step in terminal_steps:
        aggregate_deps.extend(invocation_bounds[step.step_id][1])
    aggregate_key = f"@federation::{name}"
    nodes.append(FederatedPlanNode(
        key=aggregate_key,
        repository="@federation",
        target="",
        workflow=name,
        local_key=name,
        depends_on=tuple(dict.fromkeys(aggregate_deps)),
        index=next_index,
        synthetic=True,
        metadata={"planning_only": True},
    ))

    graph = FederatedPlanGraph(name, tuple(nodes), tuple(repositories))
    graph.topological_order()
    return graph


__all__ = [
    "FederationPlanError",
    "FederatedPlanNode",
    "FederatedPlanGraph",
    "materialize_federation_group",
    "project_with_federation_group",
    "build_federated_plan",
    "build_federated_group_plan",
]
