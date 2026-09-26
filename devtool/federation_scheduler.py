from __future__ import annotations

"""FRX-Y01 federation child scheduling built on the shared workflow scheduler.

The federation layer schedules one child workflow invocation atomically.  It
reuses ``workflow_scheduler.build_safe_schedule_waves`` after collapsing each
child workflow's local node scopes into one conservative repository-scoped
reservation.
"""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .federation_plan import FederatedPlanGraph
from .federation_provider import (
    FederationProviderCapability,
    local_provider_capability,
    provider_execution_environment,
    provider_matches_environment,
)
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
    contract_type: str
    depends_on: tuple[str, ...]
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    resource_class: str
    global_reservations: tuple[str, ...]
    read_only: bool
    scope_reason: str
    allowed_execution_environments: tuple[str, ...] = ("auto",)
    preferred_execution_environment: str = "auto"
    preferred_worker: str = ""
    provider_id: str = ""
    provider_kind: str = ""
    provider_profile: str = ""
    execution_environment: str = "auto"
    provider_evidence_digest: str = ""
    resource_requirements: tuple[tuple[str, int], ...] = ()
    queue_reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "repository": self.repository,
            "target": self.target,
            "workflow": self.workflow,
            "contract_type": self.contract_type,
            "depends_on": list(self.depends_on),
            "reads": list(self.reads),
            "writes": list(self.writes),
            "resource_class": self.resource_class,
            "global_reservations": list(self.global_reservations),
            "read_only": self.read_only,
            "scope_reason": self.scope_reason,
            "allowed_execution_environments": list(self.allowed_execution_environments),
            "preferred_execution_environment": self.preferred_execution_environment,
            "preferred_worker": self.preferred_worker,
            "placement": {
                "provider_id": self.provider_id,
                "provider_kind": self.provider_kind,
                "profile": self.provider_profile,
                "execution_environment": self.execution_environment,
                "provider_evidence_digest": self.provider_evidence_digest,
            },
            "resource_requirements": {key: value for key, value in self.resource_requirements},
            "queue_reasons": list(self.queue_reasons),
        }


@dataclass(frozen=True, slots=True)
class FederatedSchedulePlan:
    parallel_requested: bool
    parallel_effective: bool
    max_parallel: int
    nodes: tuple[FederatedScheduleNode, ...]
    waves: tuple[WorkflowScheduleWave, ...]
    provider_budgets: dict[str, int] = None  # type: ignore[assignment]
    resource_budgets: dict[str, int] = None  # type: ignore[assignment]
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.provider_budgets is None:
            object.__setattr__(self, "provider_budgets", {})
        if self.resource_budgets is None:
            object.__setattr__(self, "resource_budgets", {})

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
            "provider_budgets": dict(self.provider_budgets),
            "resource_budgets": dict(self.resource_budgets),
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
    contracts = [
        node.metadata.get("execution_contract")
        for node in local_nodes
        if isinstance(node.metadata.get("execution_contract"), Mapping)
    ]
    contract = dict(contracts[0]) if contracts else {}
    allowed_execution_environments = tuple(
        str(value) for value in (contract.get("allowed_execution_environments") or ["auto"]) if str(value)
    ) or ("auto",)
    preferred_execution_environment = str(contract.get("preferred_execution_environment") or "auto")
    preferred_worker = str(contract.get("preferred_worker") or "")

    if resource_class == "device-exclusive":
        reservations.append("resource:device-exclusive")
    elif resource_class == "network-heavy":
        reservations.append("resource:network-heavy")

    return FederatedScheduleNode(
        step_id=step.step_id,
        repository=step.repository,
        target=target,
        workflow=step.workflow or step.child_contract,
        contract_type=step.contract_type,
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
        allowed_execution_environments=allowed_execution_environments,
        preferred_execution_environment=preferred_execution_environment,
        preferred_worker=preferred_worker,
        resource_requirements=tuple(
            sorted(
                (str(key), int(value))
                for key, value in dict(contract.get("resources") or {}).items()
                if str(key) and isinstance(value, int) and not isinstance(value, bool) and int(value) > 0
            )
        ),
    )


def _provider_score(
    node: FederatedScheduleNode,
    provider: FederationProviderCapability,
    provider_load: Mapping[str, int],
) -> tuple[int, int, int, str]:
    preferred_worker = str(node.preferred_worker or "").strip()
    preferred_environment = str(node.preferred_execution_environment or "auto").strip()
    worker_penalty = 0 if preferred_worker and provider.profile == preferred_worker else 1 if preferred_worker else 0
    environment_penalty = 0 if preferred_environment in {"", "auto"} or preferred_environment in provider.execution_environments else 1
    # Local remains the backwards-compatible default when the child has no
    # explicit preference. Explicit child constraints/preferences always win.
    locality_penalty = 0 if provider.provider_id == "local" and not preferred_worker and preferred_environment in {"", "auto"} else 1
    return (worker_penalty, environment_penalty, locality_penalty + int(provider_load.get(provider.provider_id, 0)), provider.provider_id)


def _place_nodes(
    nodes: Sequence[FederatedScheduleNode],
    providers: Sequence[FederationProviderCapability],
) -> tuple[FederatedScheduleNode, ...]:
    healthy = tuple(provider for provider in providers if provider.healthy)
    if not healthy:
        raise ValueError("Federation provider scheduling has no healthy provider capabilities")
    provider_load: dict[str, int] = {}
    placed: list[FederatedScheduleNode] = []
    for node in nodes:
        candidates = [
            provider for provider in healthy
            if provider_matches_environment(provider, node.allowed_execution_environments)
            and (
                not str(provider.metadata.get("qualified_target") or "")
                or str(provider.metadata.get("qualified_target") or "") == node.target
            )
            and (node.contract_type == "workflow" or not provider.profile)
        ]
        if node.preferred_worker:
            preferred = [provider for provider in candidates if provider.profile == node.preferred_worker]
            if preferred:
                candidates = preferred + [provider for provider in candidates if provider.profile != node.preferred_worker]
        if not candidates:
            allowed = ", ".join(node.allowed_execution_environments) or "auto"
            raise ValueError(
                f"Federation step {node.step_id!r} allows execution environments [{allowed}] but no "
                "authoritative provider capability matches. Run 'devtool ssh doctor PROFILE --deep --target "
                f"{node.target}' or change the child-owned target execution contract. "
                "Remote provider placement currently requires a workflow child contract."
            )
        provider = min(candidates, key=lambda value: _provider_score(node, value, provider_load))
        provider_load[provider.provider_id] = provider_load.get(provider.provider_id, 0) + 1
        environment = provider_execution_environment(
            provider, node.allowed_execution_environments, node.preferred_execution_environment
        )
        requirements = dict(node.resource_requirements)
        requirements[f"provider:{provider.provider_id}"] = max(1, requirements.get(f"provider:{provider.provider_id}", 0))
        requirements[f"provider-kind:{provider.kind}"] = max(1, requirements.get(f"provider-kind:{provider.kind}", 0))
        if provider.profile:
            requirements["ssh-worker"] = max(1, requirements.get("ssh-worker", 0))
            if "proot" in provider.kind or "oci" in provider.kind:
                requirements["oci-slot"] = max(1, requirements.get("oci-slot", 0))
        if node.resource_class in {"gradle", "gradle-heavy"}:
            worker_platform = str(provider.metadata.get("worker", {}).get("platform", "")) if isinstance(provider.metadata.get("worker"), Mapping) else ""
            if provider.provider_id == "local":
                requirements["local-gradle"] = max(1, requirements.get("local-gradle", 0))
            elif provider.profile == "tablet" or worker_platform == "termux":
                requirements["tablet-gradle"] = max(1, requirements.get("tablet-gradle", 0))
            elif "proot" in provider.kind or "oci" in provider.kind:
                requirements["ssh-oci-gradle"] = max(1, requirements.get("ssh-oci-gradle", 0))
            else:
                requirements["ssh-gradle"] = max(1, requirements.get("ssh-gradle", 0))
        if node.resource_class == "device-exclusive":
            requirements["android-device"] = max(1, requirements.get("android-device", 0))
        for reservation in node.global_reservations:
            requirements[f"reservation:{reservation}"] = max(
                1, requirements.get(f"reservation:{reservation}", 0)
            )
        placed.append(replace(
            node,
            provider_id=provider.provider_id,
            provider_kind=provider.kind,
            provider_profile=provider.profile,
            execution_environment=environment,
            provider_evidence_digest=provider.evidence_digest,
            resource_requirements=tuple(sorted(requirements.items())),
        ))
    return tuple(placed)


def _budget_limit(
    key: str,
    *,
    max_parallel: int,
    max_local_gradle: int,
    max_ssh_oci_gradle: int,
    max_tablet_gradle: int,
    max_android_device: int,
    provider_budgets: Mapping[str, int],
    resource_budgets: Mapping[str, int],
) -> int:
    if key.startswith("provider:"):
        provider_id = key.split(":", 1)[1]
        if key in provider_budgets:
            return int(provider_budgets[key])
        if provider_id in provider_budgets:
            return int(provider_budgets[provider_id])
        return max_parallel if provider_id == "local" else 1
    if key.startswith("provider-kind:"):
        provider_kind = key.split(":", 1)[1]
        if key in provider_budgets:
            return int(provider_budgets[key])
        if provider_kind in provider_budgets:
            return int(provider_budgets[provider_kind])
        return max_parallel
    if key in resource_budgets:
        return int(resource_budgets[key])
    if key == "local-gradle":
        return int(max_local_gradle)
    if key == "ssh-oci-gradle":
        return int(max_ssh_oci_gradle)
    if key == "tablet-gradle":
        return int(max_tablet_gradle)
    if key == "android-device":
        return int(max_android_device)
    if key == "ssh-worker":
        return int(provider_budgets.get("ssh-worker", max_parallel))
    if key == "oci-slot":
        return int(provider_budgets.get("oci-slot", max_parallel))
    # Named ports, reservations, and custom resources are exclusive unless the
    # repository explicitly raises their budget.
    if key.startswith(("reservation:", "port:", "custom:")):
        return 1
    return max_parallel


def _split_waves_for_budgets(
    waves: Sequence[WorkflowScheduleWave],
    nodes: Sequence[FederatedScheduleNode],
    *,
    max_parallel: int,
    max_local_gradle: int,
    max_ssh_oci_gradle: int,
    max_tablet_gradle: int,
    max_android_device: int,
    provider_budgets: Mapping[str, int],
    resource_budgets: Mapping[str, int],
) -> tuple[tuple[WorkflowScheduleWave, ...], tuple[FederatedScheduleNode, ...], tuple[str, ...]]:
    by_step = {node.step_id: node for node in nodes}
    reasons: dict[str, list[str]] = {node.step_id: [] for node in nodes}
    output: list[WorkflowScheduleWave] = []
    notes: list[str] = []

    for base_wave in waves:
        pending = list(base_wave.nodes)
        while pending:
            selected: list[str] = []
            usage: dict[str, int] = {}
            blocked_details: dict[str, list[str]] = {}
            for step_id in list(pending):
                node = by_step[step_id]
                violations: list[str] = []
                for key, amount in node.resource_requirements:
                    limit = _budget_limit(
                        key,
                        max_parallel=max_parallel,
                        max_local_gradle=max_local_gradle,
                        max_ssh_oci_gradle=max_ssh_oci_gradle,
                        max_tablet_gradle=max_tablet_gradle,
                        max_android_device=max_android_device,
                        provider_budgets=provider_budgets,
                        resource_budgets=resource_budgets,
                    )
                    if limit <= 0:
                        violations.append(f"{key}=0")
                    elif usage.get(key, 0) + int(amount) > limit:
                        violations.append(f"{key} {usage.get(key, 0) + int(amount)}>{limit}")
                if len(selected) >= max_parallel:
                    violations.append(f"max_parallel {len(selected) + 1}>{max_parallel}")
                if violations:
                    blocked_details[step_id] = violations
                    continue
                selected.append(step_id)
                for key, amount in node.resource_requirements:
                    usage[key] = usage.get(key, 0) + int(amount)
            if not selected:
                first = pending[0]
                details = ", ".join(blocked_details.get(first, []) or ["unknown budget conflict"])
                raise ValueError(f"Federation step {first!r} cannot be scheduled under provider/resource budgets: {details}")
            for step_id in pending:
                if step_id not in selected and blocked_details.get(step_id):
                    message = "queued by FRX-Y09 budget: " + ", ".join(blocked_details[step_id])
                    if message not in reasons[step_id]:
                        reasons[step_id].append(message)
                        notes.append(f"{step_id}: {message}")
            output.append(WorkflowScheduleWave(
                index=len(output) + 1,
                nodes=tuple(selected),
                serialized_reason=(
                    base_wave.serialized_reason
                    or ("FRX-Y09 provider/resource budget" if len(selected) < len(pending) else "")
                ),
            ))
            pending = [step_id for step_id in pending if step_id not in selected]

    enriched = tuple(replace(node, queue_reasons=tuple(reasons[node.step_id])) for node in nodes)
    return tuple(output), enriched, tuple(dict.fromkeys(notes))


def build_federated_schedule(
    workflow: FederatedWorkflowConfig,
    plan: FederatedPlanGraph,
    *,
    parallel_requested: bool,
    max_parallel: int,
    max_gradle: int = 1,
    max_dotnet: int = 1,
    provider_capabilities: Sequence[FederationProviderCapability] | None = None,
    provider_budgets: Mapping[str, int] | None = None,
    resource_budgets: Mapping[str, int] | None = None,
    max_local_gradle: int = 1,
    max_ssh_oci_gradle: int = 2,
    max_tablet_gradle: int = 1,
    max_android_device: int = 1,
) -> FederatedSchedulePlan:
    nodes = [_step_schedule_node(workflow, plan, step.step_id) for step in workflow.steps]

    # Distinct enrollment names are not automatically distinct filesystem
    # trees. Nested/identical roots get a shared global reservation so the
    # generic scheduler serializes them conservatively.
    roots = {node.step_id: _repository_root(plan, node.step_id) for node in nodes}
    augmented: list[FederatedScheduleNode] = []
    for node in nodes:
        reservations = list(node.global_reservations)
        for other in nodes:
            if other.step_id == node.step_id or other.repository == node.repository:
                continue
            if _roots_overlap(roots[node.step_id], roots[other.step_id]):
                token = "repository-overlap:" + "|".join(sorted((node.repository, other.repository)))
                reservations.append(token)
        augmented.append(replace(node, global_reservations=_dedupe(reservations)))
    nodes = augmented

    providers = tuple(provider_capabilities or (local_provider_capability(),))
    nodes = list(_place_nodes(nodes, providers))

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
    base_waves, base_notes = build_safe_schedule_waves(
        shared_nodes,
        parallel_requested=parallel_requested,
        max_parallel_nodes=max(1, int(max_parallel or 1)),
        max_gradle_nodes=max_gradle,
        max_dotnet_nodes=max_dotnet,
    )
    provider_budgets = {str(key): int(value) for key, value in dict(provider_budgets or {}).items()}
    resource_budgets = {str(key): int(value) for key, value in dict(resource_budgets or {}).items()}
    waves, placed_nodes, budget_notes = _split_waves_for_budgets(
        base_waves,
        nodes,
        max_parallel=max(1, int(max_parallel or 1)),
        max_local_gradle=max(0, int(max_local_gradle)),
        max_ssh_oci_gradle=max(0, int(max_ssh_oci_gradle)),
        max_tablet_gradle=max(0, int(max_tablet_gradle)),
        max_android_device=max(0, int(max_android_device)),
        provider_budgets=provider_budgets,
        resource_budgets=resource_budgets,
    )
    return FederatedSchedulePlan(
        parallel_requested=bool(parallel_requested),
        parallel_effective=bool(parallel_requested and any(len(wave.nodes) > 1 for wave in waves)),
        max_parallel=max(1, int(max_parallel or 1)),
        nodes=placed_nodes,
        waves=waves,
        provider_budgets=provider_budgets,
        resource_budgets=resource_budgets,
        notes=tuple(dict.fromkeys((*base_notes, *budget_notes))),
    )


__all__ = [
    "FederatedScheduleNode",
    "FederatedSchedulePlan",
    "build_federated_schedule",
]
