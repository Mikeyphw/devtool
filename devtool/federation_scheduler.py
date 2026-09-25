from __future__ import annotations

"""FRX-Y01 federation child scheduling built on the shared workflow scheduler.

The federation layer schedules one child workflow invocation atomically.  It
reuses ``workflow_scheduler.build_safe_schedule_waves`` after collapsing each
child workflow's local node scopes into one conservative repository-scoped
reservation.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .federation_plan import FederatedPlanGraph
from .models import FederatedWorkflowConfig
from .workflow_scheduler import (
    WorkflowScheduleNode,
    WorkflowScheduleWave,
    build_safe_schedule_waves,
)


_RESOURCE_PRIORITY = {
    "device-exclusive": 60,
    "gradle-heavy": 50,
    "gradle": 50,
    "dotnet-heavy": 45,
    "dotnet": 45,
    "network-heavy": 40,
    "cpu-heavy": 30,
    "normal": 20,
    "generic-read-only": 10,
}


@dataclass(frozen=True, slots=True)
class FederatedScheduleNode:
    step_id: str
    repository: str
    target: str
    workflow: str
    depends_on: tuple[str, ...]
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    resource_class: str
    global_reservations: tuple[str, ...]
    read_only: bool
    scope_reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "repository": self.repository,
            "target": self.target,
            "workflow": self.workflow,
            "depends_on": list(self.depends_on),
            "reads": list(self.reads),
            "writes": list(self.writes),
            "resource_class": self.resource_class,
            "global_reservations": list(self.global_reservations),
            "read_only": self.read_only,
            "scope_reason": self.scope_reason,
        }


@dataclass(frozen=True, slots=True)
class FederatedSchedulePlan:
    parallel_requested: bool
    parallel_effective: bool
    max_parallel: int
    nodes: tuple[FederatedScheduleNode, ...]
    waves: tuple[WorkflowScheduleWave, ...]
    notes: tuple[str, ...] = ()

    @property
    def by_step(self) -> dict[str, FederatedScheduleNode]:
        return {node.step_id: node for node in self.nodes}

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "parallel_requested": self.parallel_requested,
            "parallel_effective": self.parallel_effective,
            "max_parallel": self.max_parallel,
            "nodes": [node.as_dict() for node in self.nodes],
            "waves": [wave.as_dict() for wave in self.waves],
            "notes": list(self.notes),
        }


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


def _logical_scope(repository: str, raw: str) -> str:
    text = str(raw or ".").replace("\\", "/").strip().lstrip("./")
    return f"repo-{repository}/{text or '.'}"


def _repository_root(plan: FederatedPlanGraph, step_id: str) -> Path | None:
    for node in plan.nodes:
        if node.synthetic or str(node.metadata.get("federation_step") or "") != step_id:
            continue
        identity = node.metadata.get("repository_identity")
        if isinstance(identity, Mapping):
            raw = str(identity.get("root") or "").strip()
            if raw:
                return Path(raw).expanduser().resolve(strict=False)
    return None


def _roots_overlap(left: Path | None, right: Path | None) -> bool:
    if left is None or right is None:
        return True
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def _resource_class(scopes: Sequence[Mapping[str, Any]]) -> str:
    classes = [str(scope.get("resource_class") or "normal").strip().lower() for scope in scopes]
    if scopes and all(bool(scope.get("read_only")) for scope in scopes):
        if not any(_RESOURCE_PRIORITY.get(value, 20) > _RESOURCE_PRIORITY["generic-read-only"] for value in classes):
            return "generic-read-only"
    chosen = max(classes or ["normal"], key=lambda value: _RESOURCE_PRIORITY.get(value, 20))
    if chosen == "gradle":
        return "gradle-heavy"
    if chosen == "dotnet":
        return "dotnet-heavy"
    if chosen == "normal":
        return "generic-read-only" if scopes and all(bool(scope.get("read_only")) for scope in scopes) else "cpu-heavy"
    return chosen


def _step_schedule_node(
    workflow: FederatedWorkflowConfig,
    plan: FederatedPlanGraph,
    step_id: str,
) -> FederatedScheduleNode:
    step = next(step for step in workflow.steps if step.step_id == step_id)
    local_nodes = [
        node
        for node in plan.nodes
        if not node.synthetic and str(node.metadata.get("federation_step") or "") == step_id
    ]
    if not local_nodes:
        raise ValueError(f"Federation scheduling step {step_id!r} has no planned child nodes")

    target_values = {node.target for node in local_nodes if node.target}
    target = next(iter(target_values)) if len(target_values) == 1 else ""
    scopes: list[Mapping[str, Any]] = []
    for node in local_nodes:
        value = node.metadata.get("child_schedule")
        scopes.append(value if isinstance(value, Mapping) else {})

    read_only = bool(scopes) and all(bool(scope.get("read_only")) for scope in scopes)
    reads: list[str] = []
    writes: list[str] = []
    reservations: list[str] = []
    unknown_write = False
    for scope in scopes:
        for raw in scope.get("reads", []) or []:
            reads.append(_logical_scope(step.repository, str(raw)))
        for raw in scope.get("writes", []) or []:
            writes.append(_logical_scope(step.repository, str(raw)))
        for token in scope.get("global_reservations", []) or []:
            reservations.append(str(token))
        if not bool(scope.get("writes_known")) and not bool(scope.get("read_only")):
            unknown_write = True

    # At federation granularity an unknown child write set is still bounded by
    # the enrolled repository root.  Treat the whole repository as reserved;
    # this allows unrelated repositories to overlap while same-repo work stays
    # safely serialized.
    if unknown_write:
        writes.append(_logical_scope(step.repository, "."))
    if not reads:
        reads.append(_logical_scope(step.repository, "."))

    resource_class = _resource_class(scopes)
    if resource_class == "device-exclusive":
        reservations.append("resource:device-exclusive")
    elif resource_class == "network-heavy":
        reservations.append("resource:network-heavy")

    return FederatedScheduleNode(
        step_id=step.step_id,
        repository=step.repository,
        target=target,
        workflow=step.workflow,
        depends_on=tuple(step.depends_on),
        reads=_dedupe(reads),
        writes=_dedupe(writes),
        resource_class=resource_class,
        global_reservations=_dedupe(reservations),
        read_only=read_only,
        scope_reason=(
            "child workflow is read-only"
            if read_only
            else "child write set is bounded by the enrolled repository root"
            if unknown_write
            else "child write set comes from local workflow scopes"
        ),
    )


def build_federated_schedule(
    workflow: FederatedWorkflowConfig,
    plan: FederatedPlanGraph,
    *,
    parallel_requested: bool,
    max_parallel: int,
    max_gradle: int = 1,
    max_dotnet: int = 1,
) -> FederatedSchedulePlan:
    nodes = [_step_schedule_node(workflow, plan, step.step_id) for step in workflow.steps]

    # Distinct enrollment names are not automatically distinct filesystem
    # trees.  Nested/identical roots get a shared global reservation so the
    # generic scheduler serializes them conservatively.
    roots = {node.step_id: _repository_root(plan, node.step_id) for node in nodes}
    augmented: list[FederatedScheduleNode] = []
    for node in nodes:
        reservations = list(node.global_reservations)
        for other in nodes:
            if other.step_id <= node.step_id or other.repository == node.repository:
                continue
            if _roots_overlap(roots[node.step_id], roots[other.step_id]):
                token = "repository-overlap:" + "|".join(sorted((node.repository, other.repository)))
                reservations.append(token)
        # Add the same pair token from the other direction too.
        for other in nodes:
            if other.step_id == node.step_id or other.repository == node.repository:
                continue
            if _roots_overlap(roots[node.step_id], roots[other.step_id]):
                token = "repository-overlap:" + "|".join(sorted((node.repository, other.repository)))
                reservations.append(token)
        augmented.append(
            FederatedScheduleNode(
                step_id=node.step_id,
                repository=node.repository,
                target=node.target,
                workflow=node.workflow,
                depends_on=node.depends_on,
                reads=node.reads,
                writes=node.writes,
                resource_class=node.resource_class,
                global_reservations=_dedupe(reservations),
                read_only=node.read_only,
                scope_reason=node.scope_reason,
            )
        )
    nodes = augmented

    shared_nodes = tuple(
        WorkflowScheduleNode(
            key=node.step_id,
            target=node.target,
            workflow=node.workflow,
            runner="federation",
            action="invoke",
            job_scope="federation",
            job_target=node.target,
            job_local_name=node.workflow,
            job_key=f"{node.repository}::{node.target}::{node.workflow}",
            depends_on=node.depends_on,
            spec_indexes=(),
            spec_names=(),
            reads=node.reads,
            writes=node.writes,
            writes_known=True,
            read_only=node.read_only,
            resource_class=node.resource_class,
            global_reservations=node.global_reservations,
            scope_reason=node.scope_reason,
        )
        for node in nodes
    )
    waves, notes = build_safe_schedule_waves(
        shared_nodes,
        parallel_requested=parallel_requested,
        max_parallel_nodes=max(1, int(max_parallel or 1)),
        max_gradle_nodes=max_gradle,
        max_dotnet_nodes=max_dotnet,
    )
    return FederatedSchedulePlan(
        parallel_requested=bool(parallel_requested),
        parallel_effective=bool(parallel_requested and any(len(wave.nodes) > 1 for wave in waves)),
        max_parallel=max(1, int(max_parallel or 1)),
        nodes=tuple(nodes),
        waves=waves,
        notes=notes,
    )


__all__ = [
    "FederatedScheduleNode",
    "FederatedSchedulePlan",
    "build_federated_schedule",
]
