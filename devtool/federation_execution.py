from __future__ import annotations

"""FRX-06 federated execution coordinator and EXO parent/child lineage.

The coordinator owns one parent EXO run.  Each configured federation workflow
step re-enters canonical Devtool with a distinct child run id.  Child work
therefore remains independently inspectable and never shares the coordinator's
authoritative run identity.
"""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping

from .core.execution_contract import ExecutionStatus, RunId
from .core.events import (
    ArtifactProduced,
    GraphSealed,
    NodeBlocked,
    NodeDiscovered,
    NodeFinished,
    NodeStarted,
    RunFinished,
    RunStarted,
    get_event_bus,
    get_execution_store,
    publish_event,
)
from .federation_discovery import resolve_federated_repository
from .federation_git_lineage import (
    direct_federated_git_lineage,
    finalize_federated_git_lineage,
    git_lineage_environment,
    persist_federated_git_lineage,
    planned_federated_git_lineage,
)
from .federation_capabilities import project_capability_dependencies
from .federation_plan import FederatedPlanGraph, build_federated_plan, project_with_federation_group
from .federation_scheduler import FederatedSchedulePlan, build_federated_schedule
from .federation_operations import (
    capture_federated_resume_checkpoint,
    collect_federated_operational_snapshot,
    request_federated_child_cancellation,
)
from .models import FederatedWorkflowConfig, FederatedWorkflowStep, ProjectConfig
from .repository_identity import RepositoryIdentity, same_repository_identity
from .distributed_execution import (
    DelegationLink,
    ExternalEventIngestor,
    import_child_run_journal,
    reconcile_terminal_truth,
)
from .distributed_transport import (
    AsyncJournalForwarder,
    DelegatedEventReceiver,
    LocalLoopbackTransport,
    distributed_provider_adapter,
)
from .invocation_origin import create_federation_lineage_handoff, create_managed_run_handoff, trusted_invocation_context


class FederationExecutionError(RuntimeError):
    pass


def _resolved_artifact_inputs(
    step: FederatedWorkflowStep,
    outcomes: Mapping[str, "FederatedChildRun"],
    *,
    consumer_root: str | Path,
    parent_run_id: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    if not step.artifact_inputs:
        return {}, {}
    from .federation_artifacts import FederationArtifactError, artifact_input_environment, resolve_artifact_inputs
    try:
        edges = resolve_artifact_inputs(
            step, outcomes, consumer_root=consumer_root, parent_run_id=parent_run_id
        )
    except FederationArtifactError as exc:
        raise FederationExecutionError(
            f"Federation artifact inputs for step {step.step_id!r} are not consumable: {exc}"
        ) from exc
    return edges, artifact_input_environment(edges)


def _resolve_frozen_execution_plan(parent: ProjectConfig, plan: FederatedPlanGraph, *, drift_policy: str) -> tuple[FederatedPlanGraph, dict[str, Any]]:
    from .federation_lock import FederationSourceLockError, resolve_execution_source_lock

    try:
        lock, verification = resolve_execution_source_lock(parent, plan, drift_policy=drift_policy)
    except FederationSourceLockError as exc:
        raise FederationExecutionError(str(exc)) from exc
    return replace(plan, source_lock=lock.as_dict()), verification


@dataclass(frozen=True, slots=True)
class FederatedChildRun:
    step_id: str
    repository: str
    workflow: str
    target: str
    run_id: str
    parent_node_id: str
    status: str
    exit_code: int
    repository_identity: dict[str, Any] = field(default_factory=dict)
    run_lineage: dict[str, Any] = field(default_factory=dict)
    operational_ownership: dict[str, Any] = field(default_factory=dict)
    federation_ref: str = ""
    requested_ref: str = ""
    child_contract: str = ""
    contract_ref: str = ""
    git_lineage: dict[str, Any] = field(default_factory=dict)
    lifecycle: str = ""
    artifact_inputs: dict[str, Any] = field(default_factory=dict)
    placement: dict[str, Any] = field(default_factory=dict)
    delegation_link: dict[str, Any] = field(default_factory=dict)
    terminal_reconciliation: dict[str, Any] = field(default_factory=dict)
    delivery_receipt: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "repository": self.repository,
            "workflow": self.workflow,
            "target": self.target,
            "run_id": self.run_id,
            "parent_node_id": self.parent_node_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "repository_identity": dict(self.repository_identity),
            "run_lineage": dict(self.run_lineage),
            "operational_ownership": dict(self.operational_ownership),
            "federation_ref": self.federation_ref,
            "requested_ref": self.requested_ref,
            "child_contract": self.child_contract,
            "contract_ref": self.contract_ref,
            "git_lineage": dict(self.git_lineage),
            "lifecycle": self.lifecycle,
            "artifact_inputs": dict(self.artifact_inputs),
            "placement": dict(self.placement),
            "delegation_link": dict(self.delegation_link),
            "terminal_reconciliation": dict(self.terminal_reconciliation),
            "delivery_receipt": dict(self.delivery_receipt),
        }


@dataclass(frozen=True, slots=True)
class FederatedRunReport:
    workflow: str
    parent_run_id: str
    status: str
    exit_code: int
    children: tuple[FederatedChildRun, ...]
    plan: FederatedPlanGraph
    schedule: dict[str, Any] = field(default_factory=dict)
    lifecycle: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.status == ExecutionStatus.SUCCEEDED.value

    @property
    def group(self) -> str:
        return self.workflow.split(":", 1)[1] if self.workflow.startswith("group:") else ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "subject_kind": "group" if self.group else "workflow",
            "group": self.group,
            "workflow": self.workflow,
            "parent_run_id": self.parent_run_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "children": [child.as_dict() for child in self.children],
            "plan": self.plan.as_dict(),
            "schedule": dict(self.schedule),
            "lifecycle": dict(self.lifecycle),
        }


def _ordered_steps(workflow: FederatedWorkflowConfig) -> tuple[FederatedWorkflowStep, ...]:
    by_id = {step.step_id: step for step in workflow.steps}
    declared = {step.step_id: index for index, step in enumerate(workflow.steps)}
    pending = {step.step_id: set(step.depends_on) for step in workflow.steps}
    ordered: list[FederatedWorkflowStep] = []
    while pending:
        ready = sorted((key for key, deps in pending.items() if not deps), key=lambda key: declared[key])
        if not ready:
            raise FederationExecutionError(
                f"Federation dependency cycle detected in {workflow.name!r}: {', '.join(sorted(pending))}"
            )
        for key in ready:
            ordered.append(by_id[key])
            pending.pop(key)
            for deps in pending.values():
                deps.discard(key)
    return tuple(ordered)


def _parent_node_id(workflow: str, step_id: str) -> str:
    return f"federation:{workflow}:{step_id}"


def _step_target(plan: FederatedPlanGraph, step: FederatedWorkflowStep) -> str:
    if step.hierarchical:
        return ""
    targets = {
        node.target
        for node in plan.nodes
        if not node.synthetic and str(node.metadata.get("federation_step") or "") == step.step_id
    }
    targets.discard("")
    if len(targets) != 1:
        rendered = ", ".join(sorted(targets)) or "<none>"
        raise FederationExecutionError(
            f"Federation step {step.step_id!r} did not resolve one child target; resolved: {rendered}"
        )
    return next(iter(targets))


def _step_federation_ref(plan: FederatedPlanGraph, step: FederatedWorkflowStep) -> str:
    refs = {
        node.federation_ref
        for node in plan.nodes
        if not node.synthetic and str(node.metadata.get("federation_step") or "") == step.step_id and node.federation_ref
    }
    if len(refs) != 1:
        rendered = ", ".join(sorted(refs)) or "<none>"
        raise FederationExecutionError(
            f"Federation step {step.step_id!r} did not resolve one canonical federation ref; resolved: {rendered}"
        )
    return next(iter(refs))


def _step_identity(plan: FederatedPlanGraph, step: FederatedWorkflowStep) -> dict[str, Any]:
    for node in plan.nodes:
        if node.synthetic or str(node.metadata.get("federation_step") or "") != step.step_id:
            continue
        identity = node.metadata.get("repository_identity")
        if isinstance(identity, dict):
            return dict(identity)
    return {}


def _step_planned_git_lineage(
    plan: FederatedPlanGraph,
    step: FederatedWorkflowStep,
    *,
    parent_run_id: str,
    parent_node_id: str,
    child_run_id: str,
    target: str,
) -> dict[str, Any]:
    return planned_federated_git_lineage(
        source_lock=plan.source_lock,
        repository=step.repository,
        parent_run_id=parent_run_id,
        parent_node_id=parent_node_id,
        child_run_id=child_run_id,
        target=target,
        contract_type=step.contract_type,
        contract_name=_step_child_contract(step),
        contract_ref=_step_contract_ref(plan, step),
        federation_workflow=plan.workflow,
        federation_step=step.step_id,
    )


def _publish_child_git_lineage(record: dict[str, Any], *, publish: Callable[[Any], None]) -> dict[str, Any]:
    path, digest, size = persist_federated_git_lineage(record)
    publish(ArtifactProduced(
        run_id=str(record.get("child_run_id") or ""),
        path=str(path),
        artifact_id=f"federation-git-lineage:{record.get('child_run_id') or 'child'}",
        sha256=digest,
        size=size,
        artifact_type="federation-git-lineage",
        producer_task="federation-git-lineage",
        verification_status="verified",
        source="federation",
    ))
    return record


def _finalize_child_git_lineage(
    resolution: Any,
    planned: dict[str, Any],
    *,
    terminal_status: str,
    publish: Callable[[Any], None],
) -> dict[str, Any]:
    finalized = finalize_federated_git_lineage(
        resolution.root,
        planned,
        terminal_status=terminal_status,
    )
    return _publish_child_git_lineage(finalized, publish=publish)


def _finalize_child_git_lineage_preserving_primary_failure(
    resolution: Any,
    planned: dict[str, Any],
    *,
    terminal_status: str,
    terminal_exit_code: int,
    publish: Callable[[Any], None],
) -> dict[str, Any]:
    """Keep an observed child failure authoritative if evidence finalization also fails.

    Git-lineage finalization is required evidence, but it is secondary to an
    already-observed child terminal failure.  A successful child still fails
    closed if finalization cannot complete; a failed/cancelled/blocked child
    keeps its primary status/exit code and records the finalization problem as
    secondary lineage evidence instead of replacing the root cause.
    """
    try:
        return _finalize_child_git_lineage(
            resolution,
            planned,
            terminal_status=terminal_status,
            publish=publish,
        )
    except Exception as exc:
        primary_failed = (
            str(terminal_status) != ExecutionStatus.SUCCEEDED.value
            or int(terminal_exit_code or 0) != 0
        )
        if not primary_failed:
            raise
        fallback = dict(planned)
        fallback.update({
            "state": "finalization-error-secondary",
            "terminal_status": str(terminal_status),
            "terminal_exit_code": int(terminal_exit_code or 0),
            "finalization_error": str(exc),
            "primary_failure_preserved": True,
        })
        try:
            return _publish_child_git_lineage(fallback, publish=publish)
        except Exception as publish_exc:
            # The parent NodeFinished metadata still carries this record, so a
            # secondary evidence-publish failure cannot replace the already
            # observed child execution failure.
            fallback["secondary_evidence_publish_error"] = str(publish_exc)
            return fallback


def _cancelled_git_lineage_fallback(
    planned: dict[str, Any],
    error: Exception,
    *,
    publish: Callable[[Any], None],
) -> dict[str, Any]:
    fallback = dict(planned)
    fallback.update({
        "state": "finalization-error",
        "terminal_status": ExecutionStatus.CANCELLED.value,
        "finalization_error": str(error),
    })
    return _publish_child_git_lineage(fallback, publish=publish)


def _assert_planned_resolution_identity(
    *,
    repository: str,
    planned_identity: dict[str, Any],
    resolution: Any,
) -> None:
    actual = getattr(resolution, "repository_identity", None)
    if actual is None:
        raise FederationExecutionError(
            f"Federation repository {repository!r} has no identity at execution time"
        )
    if not planned_identity:
        raise FederationExecutionError(
            f"Federation repository {repository!r} has no planned repository identity"
        )
    try:
        expected = RepositoryIdentity.from_dict(planned_identity)
    except Exception as exc:
        raise FederationExecutionError(
            f"Federation repository {repository!r} has invalid planned identity: {exc}"
        ) from exc
    if not same_repository_identity(expected, actual):
        raise FederationExecutionError(
            f"Federation repository {repository!r} identity changed between planning and execution: "
            f"planned {expected.fingerprint or '<missing>'} at {expected.root}, "
            f"resolved {actual.fingerprint or '<missing>'} at {actual.root}"
        )
    planned_root = Path(expected.root).resolve(strict=False)
    actual_root = Path(getattr(resolution, "root", actual.root)).resolve(strict=False)
    if planned_root != actual_root:
        raise FederationExecutionError(
            f"Federation repository {repository!r} root changed between planning and execution: "
            f"planned {planned_root}, resolved {actual_root}"
        )


def _assert_child_snapshot_authority(
    *,
    repository: str,
    child_run_id: str,
    expected_identity: dict[str, Any],
    expected_parent_run_id: str,
    expected_target: str = "",
    expected_child_workflow: str = "",
    expected_federation_ref: str = "",
    expected_requested_ref: str = "",
    expected_child_contract: str = "",
    expected_contract_ref: str = "",
    expected_federation_group: str = "",
    snapshot: Any,
) -> dict[str, Any]:
    if snapshot is None:
        return {}
    persisted_lineage = dict(getattr(snapshot, "run_lineage", {}) or {})
    if persisted_lineage.get("parent_run_id") != expected_parent_run_id:
        raise FederationExecutionError(
            f"Child run {child_run_id!r} did not persist parent federation lineage {expected_parent_run_id!r}"
        )
    if str(persisted_lineage.get("child_run_id") or child_run_id) != child_run_id:
        raise FederationExecutionError(
            f"Child run {child_run_id!r} persisted mismatched child_run_id "
            f"{persisted_lineage.get('child_run_id')!r}"
        )
    expected_fields = {
        "target": expected_target,
        "child_workflow": expected_child_workflow,
        "federation_ref": expected_federation_ref,
        "requested_ref": expected_requested_ref,
        "child_contract": expected_child_contract,
        "contract_ref": expected_contract_ref,
        "federation_group": expected_federation_group,
    }
    for field_name, expected_value in expected_fields.items():
        if expected_value and str(persisted_lineage.get(field_name) or "") != expected_value:
            raise FederationExecutionError(
                f"Child run {child_run_id!r} did not persist authoritative federation {field_name}: "
                f"expected {expected_value!r}, got {persisted_lineage.get(field_name)!r}"
            )
    persisted_identity = dict(getattr(snapshot, "repository_identity", {}) or {})
    if not persisted_identity:
        raise FederationExecutionError(
            f"Child run {child_run_id!r} for repository {repository!r} did not persist repository identity"
        )
    expected = RepositoryIdentity.from_dict(expected_identity)
    actual = RepositoryIdentity.from_dict(persisted_identity)
    if not same_repository_identity(expected, actual):
        raise FederationExecutionError(
            f"Child run {child_run_id!r} repository identity mismatch for {repository!r}: "
            f"expected {expected.fingerprint}, got {actual.fingerprint}"
        )
    return persisted_lineage


@contextmanager
def _federation_child_environment(values: dict[str, str]):
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _child_argv(
    *,
    root: str,
    target: str,
    workflow: str,
    invocation_command: str,
    provider_profile: str = "",
    execution_environment: str = "auto",
) -> list[str]:
    argv = ["--repo", root]
    profile = str(provider_profile or "").strip()
    environment = str(execution_environment or "auto").strip()
    if profile:
        argv.extend(["--ssh-profile", profile])
    elif environment not in {"", "auto", "ssh", "proot-distro"}:
        argv.extend(["--execution-environment", environment])
    argv.extend(["invoke", workflow, "--target", target])
    return argv


def _provider_capabilities_for_workflow(
    parent: ProjectConfig, workflow: FederatedWorkflowConfig, plan: FederatedPlanGraph
):
    """Load cached provider evidence for the child targets without remote probing."""
    from .federation_provider import load_federation_provider_capabilities

    targets = sorted({_step_target(plan, step) for step in workflow.steps if _step_target(plan, step)})
    rows = []
    seen: set[tuple[str, str, str]] = set()
    for target in targets or [""]:
        for provider in load_federation_provider_capabilities(parent, target=target):
            qualified = str(provider.metadata.get("qualified_target") or "")
            key = (provider.provider_id, qualified, provider.evidence_digest)
            if key in seen:
                continue
            seen.add(key)
            rows.append(provider)
    return tuple(rows)


def _step_child_contract(step: FederatedWorkflowStep) -> str:
    return step.child_contract


def _step_contract_ref(plan: FederatedPlanGraph, step: FederatedWorkflowStep) -> str:
    if step.hierarchical:
        return step.canonical_ref()
    target = _step_target(plan, step)
    return f"{step.repository}::{target}::{step.child_contract}" if target else ""




def _attach_federation_lineage_handoff(env: dict[str, str], lineage: Mapping[str, Any]) -> dict[str, str]:
    """Bind federation lineage to a private immediate-child capability.

    The DEVTOOL_FEDERATION_* variables stay in the environment for legacy
    provider routing and human diagnostics, but invocation metadata only trusts
    the handoff consumed by devtool.invocation_origin.
    """
    if lineage and str(lineage.get("parent_run_id") or "").strip():
        env["DEVTOOL_FEDERATION_LINEAGE_HANDOFF"] = create_federation_lineage_handoff(lineage)
    return env


def _step_display_workflow(step: FederatedWorkflowStep) -> str:
    if step.contract_type == "federation":
        return f"@federation:{step.contract_name}"
    if step.contract_type == "group":
        return f"@group:{step.contract_name}"
    return step.workflow or step.child_contract


def _execute_existing_child_contract(
    parent: ProjectConfig,
    step: FederatedWorkflowStep,
    *,
    child_run_id: str,
    lineage: dict[str, Any],
    reenter: Callable[[list[str]], int],
    publish: Callable[[Any], None],
    provider_profile: str = "",
    execution_environment: str = "auto",
) -> int:
    """Execute a workflow/job/wrapper through its existing authoritative service."""
    if step.contract_type == "workflow":
        resolution = resolve_federated_repository(parent, step.repository)
        invocation_command = f"{lineage.get('federation_workflow')}:{step.step_id}"
        argv = _child_argv(
            root=resolution.root,
            target=str(lineage.get("target") or step.target),
            workflow=step.workflow,
            invocation_command=invocation_command,
            provider_profile=provider_profile,
            execution_environment=execution_environment,
        )
        with trusted_invocation_context(source="federation", command=invocation_command):
            return int(reenter(argv))

    if step.hierarchical:
        if provider_profile:
            raise FederationExecutionError(
                "Hierarchical federation re-entry currently requires the local controller; "
                "remote placement belongs to the child federation's own Y09 scheduler"
            )
        resolution = resolve_federated_repository(parent, step.repository)
        invocation_command = f"{lineage.get('federation_workflow')}:{step.step_id}"
        argv = ["-r", str(resolution.root), "federation", "run"]
        if step.contract_type == "group":
            argv.extend(["--group", step.contract_name])
        else:
            argv.append(step.contract_name)
        with trusted_invocation_context(source="federation", command=invocation_command):
            return int(reenter(argv))

    from .federation_bridge import (
        execute_planned_federated_job_child,
        execute_planned_federated_wrapper_child,
        plan_federated_job,
        plan_federated_wrapper,
    )

    target = str(lineage.get("target") or step.target)
    if step.contract_type == "job":
        plan, child, specs = plan_federated_job(
            parent, step.repository, f"{target}:job:{step.contract_name}"
        )
        if plan.target != target or plan.contract_ref != str(lineage.get("contract_ref") or ""):
            raise FederationExecutionError(
                f"Federation job contract {step.ref!r} changed between planning and execution"
            )
        return execute_planned_federated_job_child(
            child,
            specs,
            child_run_id=child_run_id,
            lineage=lineage,
            job_reference=f"{target}:job:{step.contract_name}",
            publish=publish,
        )
    if step.contract_type == "wrapper":
        plan, child, resolution = plan_federated_wrapper(parent, step.repository, [step.contract_name])
        if plan.target != target or plan.contract_ref != str(lineage.get("contract_ref") or ""):
            raise FederationExecutionError(
                f"Federation wrapper contract {step.ref!r} changed between planning and execution"
            )
        return execute_planned_federated_wrapper_child(
            child,
            resolution,
            child_run_id=child_run_id,
            lineage=lineage,
            reenter=reenter,
            publish=publish,
        )
    raise FederationExecutionError(f"Unsupported federation contract type {step.contract_type!r}")


def _run_federated_workflow_sequential(
    parent: ProjectConfig,
    workflow_name: str,
    *,
    reenter: Callable[[list[str]], int],
    publish: Callable[[Any], None] = publish_event,
    snapshot_lookup: Callable[[str], Any] | None = None,
    parent_run_id: str = "",
    request_child_cancel: Callable[[str], Any] | None = None,
    drift_policy: str = "fail",
    reuse_children: Mapping[str, FederatedChildRun] | None = None,
    lifecycle_actions: Mapping[str, str] | None = None,
) -> FederatedRunReport:
    """Execute a configured federation workflow sequentially with EXO lineage.

    Scheduling is deliberately conservative in FRX-06: dependency-ready child
    workflow invocations execute one at a time.  Parallel federation scheduling
    can be layered later without changing the run-lineage contract.
    """

    name = str(workflow_name or "").strip()
    federation_group = name.split(":", 1)[1] if name.startswith("group:") else ""
    workflow = parent.federation.workflows.get(name)
    if workflow is None:
        available = ", ".join(sorted(parent.federation.workflows)) or "<none>"
        raise FederationExecutionError(f"Unknown federation workflow {name!r}. Available workflows: {available}")
    workflow = project_capability_dependencies(parent, workflow)

    plan = build_federated_plan(parent, name)
    plan, source_verification = _resolve_frozen_execution_plan(parent, plan, drift_policy=drift_policy)
    policy = parent.workspace.scheduler
    providers = _provider_capabilities_for_workflow(parent, workflow, plan)
    schedule = build_federated_schedule(
        workflow,
        plan,
        parallel_requested=False,
        max_parallel=1,
        max_gradle=max(0, int(policy.max_gradle_nodes)),
        max_dotnet=max(0, int(policy.max_dotnet_nodes)),
        provider_capabilities=providers,
        provider_budgets=policy.provider_budgets,
        resource_budgets=policy.resource_budgets,
        max_local_gradle=policy.max_local_gradle,
        max_ssh_oci_gradle=policy.max_ssh_oci_gradle,
        max_tablet_gradle=policy.max_tablet_gradle,
        max_android_device=policy.max_android_device,
    )
    schedule_nodes = schedule.by_step
    run_id = str(parent_run_id or RunId.new(prefix="federation"))
    owner_identity = parent.repository_identity.as_dict() if parent.repository_identity else {}
    owner_fingerprint = str(owner_identity.get("fingerprint") or owner_identity.get("root") or parent.repo_root)
    started = time.monotonic()
    snapshot_lookup = snapshot_lookup or get_execution_store().snapshot
    request_child_cancel = request_child_cancel or (lambda child_run_id: request_federated_child_cancellation(child_run_id))
    active_child_run_id = ""
    active_step: FederatedWorkflowStep | None = None
    active_target = ""
    active_identity: dict[str, Any] = {}
    active_lineage: dict[str, Any] = {}
    active_parent_node = ""
    active_child_started = 0.0

    parent_lineage = {
        "kind": "federation",
        "relationship": "coordinator",
        "child_run_id": run_id,
        "federation_workflow": name,
        "source_repository": owner_fingerprint,
        "effective_repository": owner_fingerprint,
    }
    outer_parent = str(os.environ.get("DEVTOOL_FEDERATION_PARENT_RUN_ID") or "").strip()
    if outer_parent:
        parent_lineage.update({
            "relationship": "nested-federation",
            "parent_run_id": outer_parent,
            "parent_node_id": str(os.environ.get("DEVTOOL_FEDERATION_PARENT_NODE_ID") or ""),
            "target": str(os.environ.get("DEVTOOL_FEDERATION_TARGET") or ""),
            "child_workflow": str(os.environ.get("DEVTOOL_FEDERATION_CHILD_WORKFLOW") or ""),
            "federation_ref": str(os.environ.get("DEVTOOL_FEDERATION_REF") or ""),
            "requested_ref": str(os.environ.get("DEVTOOL_FEDERATION_REQUESTED_REF") or ""),
            "child_contract": str(os.environ.get("DEVTOOL_FEDERATION_CHILD_CONTRACT") or ""),
            "contract_ref": str(os.environ.get("DEVTOOL_FEDERATION_CONTRACT_REF") or ""),
            "hierarchy_depth": int(os.environ.get("DEVTOOL_FEDERATION_HIERARCHY_DEPTH") or "0") + 1,
        })
    if federation_group:
        parent_lineage["federation_group"] = federation_group
    publish(RunStarted(
        run_id=run_id,
        command=(f"federation run --group {federation_group}" if federation_group else f"federation run {name}"),
        project=str(parent.repo_root),
        source="federation",
        metadata={
            "repository_identity": owner_identity,
            "run_lineage": parent_lineage,
            "federation_scheduler": schedule.as_dict(),
            "federation_source_lock": dict(plan.source_lock),
            "federation_source_verification": dict(source_verification),
        },
    ))

    ordered_steps = _ordered_steps(workflow)
    node_ids = {step.step_id: _parent_node_id(name, step.step_id) for step in ordered_steps}
    for step in ordered_steps:
        publish(NodeDiscovered(
            run_id=run_id,
            node_id=node_ids[step.step_id],
            label=_step_federation_ref(plan, step),
            kind="federation-child",
            depends_on=tuple(node_ids[dep] for dep in step.depends_on),
            source="federation",
            metadata={
                "federation_workflow": name,
                "federation_step": step.step_id,
                "repository": step.repository,
                "target": _step_target(plan, step),
                "workflow": _step_display_workflow(step),
                "contract_type": step.contract_type,
                "child_contract": _step_child_contract(step),
                "contract_ref": _step_contract_ref(plan, step),
                "federation_ref": _step_federation_ref(plan, step),
                "requested_ref": step.ref,
                "repository_identity": _step_identity(plan, step),
                "artifact_inputs": [item.as_dict() for item in step.artifact_inputs],
                "federation_stage": step.stage,
                "scheduler_state": "queued",
                "scheduler_wave": 0,
                "scheduler": schedule_nodes[step.step_id].as_dict(),
            },
        ))
    publish(GraphSealed(
        run_id=run_id,
        reason="federation child workflow set resolved with FRX-Y09 provider/resource placement",
        source="federation",
    ))

    outcomes: dict[str, FederatedChildRun] = {}
    children: list[FederatedChildRun] = []
    parent_status = ExecutionStatus.SUCCEEDED.value
    parent_exit = 0
    reuse_children = dict(reuse_children or {})
    lifecycle_actions = dict(lifecycle_actions or {})

    try:
        for step in ordered_steps:
            active_step = step
            scheduler_node = schedule_nodes[step.step_id]
            placement = dict(scheduler_node.as_dict().get("placement") or {})
            blocked_by = [dep for dep in step.depends_on if outcomes.get(dep) and outcomes[dep].status != ExecutionStatus.SUCCEEDED.value]
            if blocked_by:
                publish(NodeBlocked(
                    run_id=run_id,
                    node_id=node_ids[step.step_id],
                    blocked_by=tuple(node_ids[dep] for dep in blocked_by),
                    reason="federation dependency did not succeed",
                    source="federation",
                ))
                blocked = FederatedChildRun(
                    step.step_id, step.repository, _step_display_workflow(step), _step_target(plan, step), "",
                    node_ids[step.step_id], ExecutionStatus.BLOCKED.value, 1,
                    _step_identity(plan, step), {}, {},
                    federation_ref=_step_federation_ref(plan, step), requested_ref=step.ref,
                    child_contract=_step_child_contract(step), contract_ref=_step_contract_ref(plan, step), lifecycle="blocked",
                    placement=placement,
                )
                outcomes[step.step_id] = blocked
                children.append(blocked)
                parent_status = ExecutionStatus.FAILED.value
                if parent_exit == 0:
                    parent_exit = 1
                continue

            prior = reuse_children.get(step.step_id)
            if prior is not None:
                parent_node = node_ids[step.step_id]
                reused = replace(prior, parent_node_id=parent_node, lifecycle="reused")
                publish(NodeStarted(
                    run_id=run_id, node_id=parent_node, label=_step_federation_ref(plan, step),
                    kind="federation-child", argv=(), source="federation",
                    metadata={
                        "child_run_id": reused.run_id, "repository": step.repository,
                        "target": reused.target, "workflow": reused.workflow,
                        "federation_ref": reused.federation_ref, "requested_ref": reused.requested_ref,
                        "reuse_action": "reused", "reused_from_parent_run_id": str(reused.run_lineage.get("parent_run_id") or ""),
                        "scheduler": scheduler_node.as_dict(),
                    },
                ))
                publish(NodeFinished(
                    run_id=run_id, node_id=parent_node, status=ExecutionStatus.SUCCEEDED.value,
                    exit_code=0, duration_seconds=0.0, source="federation",
                    transition_reason=f"reused prior child result {reused.run_id}",
                    metadata={
                        "child_run_id": reused.run_id, "repository": step.repository,
                        "target": reused.target, "workflow": reused.workflow,
                        "federation_ref": reused.federation_ref, "requested_ref": reused.requested_ref,
                        "reuse_action": "reused", "result_reference": reused.as_dict(),
                        "scheduler": scheduler_node.as_dict(),
                    },
                ))
                outcomes[step.step_id] = reused
                children.append(reused)
                continue

            resolution = resolve_federated_repository(parent, step.repository)
            if not resolution.available:
                raise FederationExecutionError(f"Federation repository {step.repository!r} is unavailable at execution time")
            target = _step_target(plan, step)
            federation_ref = _step_federation_ref(plan, step)
            identity = _step_identity(plan, step)
            _assert_planned_resolution_identity(
                repository=step.repository,
                planned_identity=identity,
                resolution=resolution,
            )
            effective_fingerprint = str(identity.get("fingerprint") or identity.get("root") or resolution.root)
            artifact_inputs, artifact_env = _resolved_artifact_inputs(
                step, outcomes, consumer_root=resolution.root, parent_run_id=run_id
            )
            resume_checkpoint = capture_federated_resume_checkpoint(resolution)
            child_run_id = str(RunId.new(prefix=f"fed-{step.repository}"))
            active_child_run_id = child_run_id
            parent_node = node_ids[step.step_id]
            invocation_command = f"{name}:{step.step_id}"
            planned_git_lineage = _step_planned_git_lineage(
                plan, step, parent_run_id=run_id, parent_node_id=parent_node, child_run_id=child_run_id, target=target
            )
            lineage = {
                "kind": "federation",
                "relationship": "child",
                "parent_run_id": run_id,
                "parent_node_id": parent_node,
                "child_run_id": child_run_id,
                "federation_workflow": name,
                "federation_step": step.step_id,
                "repository": step.repository,
                "target": target,
                "child_workflow": _step_display_workflow(step),
                "child_contract": _step_child_contract(step),
                "contract_ref": _step_contract_ref(plan, step),
                "federation_ref": federation_ref,
                "requested_ref": step.ref,
                "source_repository": owner_fingerprint,
                "requested_repository": step.repository,
                "effective_repository": effective_fingerprint,
                "execution_environment": scheduler_node.execution_environment,
                "provider_placement": placement,
                "frozen_source_lock_digest": str(plan.source_lock.get("lock_digest") or ""),
                "frozen_source_equivalence_digest": str(plan.source_lock.get("equivalence_digest") or ""),
                "git_lineage": planned_git_lineage,
                "artifact_inputs": {key: value for key, value in artifact_inputs.items() if key != "__manifest__"},
            }
            from .federation_transport import build_repository_attestation, federation_attestation_environment
            attestation = build_repository_attestation(
                resolution.root,
                identity=RepositoryIdentity.from_dict(identity),
                config_path=resolution.config_path or None,
                lineage=lineage,
            )
            delegation_link = DelegationLink.from_federation(
                parent_run_id=run_id,
                parent_node_id=parent_node,
                child_run_id=child_run_id,
                lineage=lineage,
                repository_identity=identity,
                provider_identity=placement,
                attestation=attestation.as_dict(),
            )
            lineage["delegation_id"] = delegation_link.delegation_id
            lineage["delegation_link"] = delegation_link.as_dict()
            from .distributed_provider_control import DelegationRoute, record_delegation_relation
            dist_route = DelegationRoute.from_json(os.environ.get("DEVTOOL_DIST_ROUTE")).append(delegation_link)
            record_delegation_relation(parent.repo_root, delegation_link, delivery_health="starting")
            if federation_group:
                lineage["federation_group"] = federation_group
            active_target = target
            active_identity = identity
            active_lineage = lineage
            active_parent_node = parent_node
            publish(NodeStarted(
                run_id=run_id,
                node_id=parent_node,
                label=federation_ref,
                kind="federation-child",
                argv=tuple(
                    _child_argv(
                        root=resolution.root, target=target, workflow=step.workflow,
                        invocation_command=invocation_command, provider_profile=scheduler_node.provider_profile,
                        execution_environment=scheduler_node.execution_environment,
                    )
                    if step.contract_type == "workflow" else (step.ref,)
                ),
                source="federation",
                metadata={
                    "child_run_id": child_run_id,
                    "repository": step.repository,
                    "target": target,
                    "workflow": _step_display_workflow(step),
                    "contract_type": step.contract_type,
                    "child_contract": _step_child_contract(step),
                    "contract_ref": _step_contract_ref(plan, step),
                    "federation_ref": federation_ref,
                    "requested_ref": step.ref,
                    "run_lineage": lineage,
                    "delegation_link": delegation_link.as_dict(),
                    "resume_checkpoint": resume_checkpoint,
                    "artifact_inputs": {key: value for key, value in artifact_inputs.items() if key != "__manifest__"},
                    "scheduler_state": "running",
                    "scheduler_wave": 0,
                    "scheduler": scheduler_node.as_dict(),
                },
            ))

            env = {
                "DEVTOOL_RUN_ID": child_run_id,
                "DEVTOOL_FEDERATION_RELATIONSHIP": "child",
                "DEVTOOL_FEDERATION_PARENT_RUN_ID": run_id,
                "DEVTOOL_FEDERATION_PARENT_NODE_ID": parent_node,
                "DEVTOOL_FEDERATION_WORKFLOW": name,
                "DEVTOOL_FEDERATION_STEP": step.step_id,
                "DEVTOOL_FEDERATION_REPOSITORY": step.repository,
                "DEVTOOL_FEDERATION_TARGET": target,
                "DEVTOOL_FEDERATION_CHILD_WORKFLOW": _step_display_workflow(step),
                "DEVTOOL_FEDERATION_REF": federation_ref,
                "DEVTOOL_FEDERATION_REQUESTED_REF": step.ref,
                "DEVTOOL_FEDERATION_CHILD_CONTRACT": _step_child_contract(step),
                "DEVTOOL_FEDERATION_CONTRACT_REF": _step_contract_ref(plan, step),
                "DEVTOOL_FEDERATION_SOURCE_REPOSITORY": owner_fingerprint,
                "DEVTOOL_FEDERATION_REQUESTED_REPOSITORY": step.repository,
                "DEVTOOL_FEDERATION_EFFECTIVE_REPOSITORY": effective_fingerprint,
                "DEVTOOL_FEDERATION_EXECUTION_ENVIRONMENT": scheduler_node.execution_environment,
                "DEVTOOL_FEDERATION_SOURCE_LOCK_DIGEST": str(plan.source_lock.get("lock_digest") or ""),
                "DEVTOOL_FEDERATION_SOURCE_EQUIVALENCE_DIGEST": str(plan.source_lock.get("equivalence_digest") or ""),
                "DEVTOOL_DELEGATION_LINK_ID": delegation_link.delegation_id,
                "DEVTOOL_DIST_DELEGATION_ID": delegation_link.delegation_id,
                "DEVTOOL_DIST_ATTESTATION_DIGEST": delegation_link.attestation_digest,
                "DEVTOOL_DIST_ROUTE": dist_route.to_json(),
                "DEVTOOL_FEDERATION_HIERARCHY_DEPTH": str(int(os.environ.get("DEVTOOL_FEDERATION_HIERARCHY_DEPTH") or "0") + (1 if step.hierarchical else 0)),
            }
            if not scheduler_node.provider_profile and scheduler_node.execution_environment not in {"", "auto"}:
                env["DEVTOOL_PROJECT_EXECUTION_ENVIRONMENT"] = scheduler_node.execution_environment
            env.update(git_lineage_environment(planned_git_lineage))
            env.update(artifact_env)
            _attach_federation_lineage_handoff(env, lineage)
            if federation_group:
                env["DEVTOOL_FEDERATION_GROUP"] = federation_group
            env.update(federation_attestation_environment(attestation))
            child_started = time.monotonic()
            active_child_started = child_started
            with _federation_child_environment(env):
                rc = _execute_existing_child_contract(
                    parent,
                    step,
                    child_run_id=child_run_id,
                    lineage=lineage,
                    reenter=reenter,
                    publish=publish,
                    provider_profile=scheduler_node.provider_profile,
                    execution_environment=scheduler_node.execution_environment,
                )
            child_snapshot = snapshot_lookup(child_run_id)
            persisted_lineage = _assert_child_snapshot_authority(
                repository=step.repository,
                child_run_id=child_run_id,
                expected_identity=identity,
                expected_parent_run_id=run_id,
                expected_target=target,
                expected_child_workflow=_step_display_workflow(step),
                expected_federation_ref=federation_ref,
                expected_requested_ref=step.ref,
                expected_child_contract=(_step_child_contract(step) if step.contract_type != "workflow" else ""),
                expected_contract_ref=(_step_contract_ref(plan, step) if step.contract_type != "workflow" else ""),
                expected_federation_group=federation_group,
                snapshot=child_snapshot,
            ) if child_snapshot is not None else {}
            operational = collect_federated_operational_snapshot(
                resolution,
                child_run_id=child_run_id,
                execution_snapshot=child_snapshot,
                resume_checkpoint=resume_checkpoint,
            ).as_dict()
            active_child_run_id = ""
            terminal = reconcile_terminal_truth(transport_exit_code=rc, child_snapshot=child_snapshot)
            child_status = terminal.authoritative_status
            rc = terminal.authoritative_exit_code
            git_lineage = _finalize_child_git_lineage_preserving_primary_failure(
                resolution,
                planned_git_lineage,
                terminal_status=child_status,
                terminal_exit_code=rc,
                publish=publish,
            )
            publish(NodeFinished(
                run_id=run_id,
                node_id=parent_node,
                status=child_status,
                exit_code=rc,
                duration_seconds=time.monotonic() - child_started,
                source="federation",
                transition_reason=f"child run {child_run_id}",
                metadata={
                    "child_run_id": child_run_id,
                    "repository": step.repository,
                    "target": target,
                    "workflow": _step_display_workflow(step),
                    "contract_type": step.contract_type,
                    "child_contract": _step_child_contract(step),
                    "contract_ref": _step_contract_ref(plan, step),
                    "federation_ref": federation_ref,
                    "requested_ref": step.ref,
                    "run_lineage": persisted_lineage or lineage,
                    "delegation_link": delegation_link.as_dict(),
                    "terminal_reconciliation": terminal.as_dict(),
                    "operational_ownership": operational,
                    "git_lineage": git_lineage,
                    "artifact_inputs": {key: value for key, value in artifact_inputs.items() if key != "__manifest__"},
                    "scheduler_state": child_status,
                    "scheduler_wave": 0,
                    "scheduler": scheduler_node.as_dict(),
                },
            ))
            child = FederatedChildRun(
                step.step_id, step.repository, _step_display_workflow(step), target, child_run_id,
                parent_node, child_status, rc, identity, persisted_lineage or lineage, operational,
                federation_ref=federation_ref, requested_ref=step.ref,
                child_contract=_step_child_contract(step), contract_ref=_step_contract_ref(plan, step),
                git_lineage=git_lineage,
                lifecycle=(
                    "cancelled" if child_status == ExecutionStatus.CANCELLED.value else
                    "failed" if child_status != ExecutionStatus.SUCCEEDED.value or rc != 0 else
                    str(lifecycle_actions.get(step.step_id) or "executed")
                ),
                artifact_inputs=dict(artifact_inputs),
                placement=placement,
                delegation_link=delegation_link.as_dict(),
                terminal_reconciliation=terminal.as_dict(),
            )
            outcomes[step.step_id] = child
            children.append(child)
            if child_status == ExecutionStatus.CANCELLED.value or rc == 130:
                parent_status = ExecutionStatus.CANCELLED.value
                parent_exit = 130
                break
            elif child_status != ExecutionStatus.SUCCEEDED.value or rc != 0:
                parent_status = ExecutionStatus.FAILED.value
                parent_exit = rc or 1
    except KeyboardInterrupt:
        if active_child_run_id:
            try:
                request_child_cancel(active_child_run_id)
            except Exception:
                pass
            snapshot = snapshot_lookup(active_child_run_id)
            persisted_lineage = dict(getattr(snapshot, "run_lineage", {}) or {}) if snapshot is not None else {}
            cancelled_git_lineage: dict[str, Any] = {}
            if active_step is not None:
                try:
                    active_resolution = resolve_federated_repository(parent, active_step.repository)
                    planned = dict(active_lineage.get("git_lineage") or {})
                    if active_resolution.available and planned:
                        cancelled_git_lineage = _finalize_child_git_lineage(
                            active_resolution, planned, terminal_status=ExecutionStatus.CANCELLED.value, publish=publish
                        )
                except Exception as exc:
                    cancelled_git_lineage = _cancelled_git_lineage_fallback(
                        dict(active_lineage.get("git_lineage") or {}), exc, publish=publish
                    )
            publish(NodeFinished(
                run_id=run_id,
                node_id=active_parent_node,
                status=ExecutionStatus.CANCELLED.value,
                exit_code=130,
                duration_seconds=max(0.0, time.monotonic() - active_child_started),
                source="federation",
                transition_reason=f"child run {active_child_run_id} cancelled by interrupt",
                metadata={
                    "child_run_id": active_child_run_id,
                    "run_lineage": persisted_lineage or active_lineage,
                    "git_lineage": cancelled_git_lineage,
                },
            ))
            if active_step is not None:
                cancelled_child = FederatedChildRun(
                    active_step.step_id, active_step.repository, _step_display_workflow(active_step), active_target,
                    active_child_run_id, active_parent_node, ExecutionStatus.CANCELLED.value, 130,
                    dict(active_identity), persisted_lineage or dict(active_lineage), {},
                    federation_ref=str(active_lineage.get("federation_ref") or ""),
                    requested_ref=str(active_lineage.get("requested_ref") or active_step.ref),
                    child_contract=str(active_lineage.get("child_contract") or ""),
                    contract_ref=str(active_lineage.get("contract_ref") or ""),
                    git_lineage=cancelled_git_lineage, lifecycle="cancelled",
                    placement=(dict(schedule_nodes[active_step.step_id].as_dict().get("placement") or {}) if active_step else {}),
                )
                outcomes[active_step.step_id] = cancelled_child
                children.append(cancelled_child)
            active_child_run_id = ""
        parent_status = ExecutionStatus.CANCELLED.value
        parent_exit = 130
    except BaseException:
        parent_status = ExecutionStatus.FAILED.value
        parent_exit = 1
        publish(RunFinished(
            run_id=run_id,
            status=parent_status,
            exit_code=parent_exit,
            duration_seconds=time.monotonic() - started,
            source="federation",
        ))
        raise

    if parent_status == ExecutionStatus.CANCELLED.value:
        for pending in ordered_steps:
            if pending.step_id in outcomes:
                continue
            publish(NodeBlocked(
                run_id=run_id,
                node_id=node_ids[pending.step_id],
                blocked_by=(),
                reason="federation cancelled before scheduling",
                source="federation",
            ))
            blocked = FederatedChildRun(
                pending.step_id, pending.repository, _step_display_workflow(pending), _step_target(plan, pending), "",
                node_ids[pending.step_id], ExecutionStatus.BLOCKED.value, 130,
                _step_identity(plan, pending), {}, {},
                federation_ref=_step_federation_ref(plan, pending), requested_ref=pending.ref,
                child_contract=_step_child_contract(pending), contract_ref=_step_contract_ref(plan, pending), lifecycle="blocked",
                placement=dict(schedule_nodes[pending.step_id].as_dict().get("placement") or {}),
            )
            outcomes[pending.step_id] = blocked
            children.append(blocked)

    publish(RunFinished(
        run_id=run_id,
        status=parent_status,
        exit_code=parent_exit,
        duration_seconds=time.monotonic() - started,
        source="federation",
    ))
    return FederatedRunReport(name, run_id, parent_status, parent_exit, tuple(children), plan)



ParallelChildExecutor = Callable[[list[str], dict[str, str]], int]


def _safe_state_component(value: str) -> str:
    text = str(value or "").strip()
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in text) or "run"


def _parallel_child_state_directory(parent_run_id: str, child_run_id: str) -> Path:
    # Parallel federation children must never share the singleton HUD/tasker
    # directory: each child owns independent durable EXO projection state.
    from .tasker import DEFAULT_TASKER_DIR, tasker_dir_from_text

    root = tasker_dir_from_text(os.environ.get("DEVTOOL_TASKER_DIR") or DEFAULT_TASKER_DIR)
    return (
        root
        / "federation-children"
        / _safe_state_component(parent_run_id)
        / _safe_state_component(child_run_id)
    )


def _persisted_execution_snapshot(directory: Path, child_run_id: str) -> Any | None:
    path = directory / "execution.json"
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    payload = document.get("execution") if isinstance(document, dict) else None
    if not isinstance(payload, dict):
        payload = document if isinstance(document, dict) else {}
    if str(payload.get("run_id") or "") != child_run_id:
        return None

    def rows(name: str) -> tuple[Any, ...]:
        raw = payload.get(name)
        if not isinstance(raw, list):
            return ()
        return tuple(SimpleNamespace(**dict(item)) for item in raw if isinstance(item, dict))

    return SimpleNamespace(
        run_id=child_run_id,
        status=str(payload.get("status") or ""),
        run_lineage=dict(payload.get("run_lineage") or {}),
        repository_identity=dict(payload.get("repository_identity") or {}),
        artifacts=rows("artifacts"),
        diagnostics=rows("diagnostics"),
        processes=rows("processes"),
        metrics=dict(payload.get("metrics") or {}),
    )


def _default_parallel_child_executor(argv: list[str], env: dict[str, str]) -> int:
    """Run one federation child in an isolated Python process.

    Parallel recursive ``main()`` calls cannot safely share ``os.environ``.
    A child process gives each federation child authoritative run/lineage
    environment without serializing the scheduler behind a global env lock.
    """

    child_env = dict(os.environ)
    child_env.update({str(key): str(value) for key, value in env.items() if str(value)})
    child_run_id = str(child_env.get("DEVTOOL_RUN_ID") or "").strip()
    if child_run_id:
        child_env["DEVTOOL_MANAGED_RUN_HANDOFF"] = create_managed_run_handoff(child_run_id)
    completed = subprocess.run(
        [sys.executable, "-m", "devtool", *argv],
        env=child_env,
        check=False,
    )
    return int(completed.returncode)


def _run_federated_workflow_parallel(
    parent: ProjectConfig,
    workflow_name: str,
    *,
    reenter: Callable[[list[str]], int],
    publish: Callable[[Any], None] = publish_event,
    snapshot_lookup: Callable[[str], Any] | None = None,
    parent_run_id: str = "",
    request_child_cancel: Callable[[str], Any] | None = None,
    max_parallel: int = 0,
    child_executor: ParallelChildExecutor | None = None,
    drift_policy: str = "fail",
) -> FederatedRunReport:
    name = str(workflow_name or "").strip()
    federation_group = name.split(":", 1)[1] if name.startswith("group:") else ""
    workflow = parent.federation.workflows.get(name)
    if workflow is None:
        available = ", ".join(sorted(parent.federation.workflows)) or "<none>"
        raise FederationExecutionError(f"Unknown federation workflow {name!r}. Available workflows: {available}")
    workflow = project_capability_dependencies(parent, workflow)

    plan = build_federated_plan(parent, name)
    plan, source_verification = _resolve_frozen_execution_plan(parent, plan, drift_policy=drift_policy)
    policy = parent.workspace.scheduler
    effective_max = max(1, int(max_parallel or policy.max_parallel_nodes or 4))
    providers = _provider_capabilities_for_workflow(parent, workflow, plan)
    schedule = build_federated_schedule(
        workflow,
        plan,
        parallel_requested=True,
        max_parallel=effective_max,
        max_gradle=max(0, int(policy.max_gradle_nodes)),
        max_dotnet=max(0, int(policy.max_dotnet_nodes)),
        provider_capabilities=providers,
        provider_budgets=policy.provider_budgets,
        resource_budgets=policy.resource_budgets,
        max_local_gradle=policy.max_local_gradle,
        max_ssh_oci_gradle=policy.max_ssh_oci_gradle,
        max_tablet_gradle=policy.max_tablet_gradle,
        max_android_device=policy.max_android_device,
    )
    schedule_nodes = schedule.by_step
    wave_for_step = {
        step_id: wave.index
        for wave in schedule.waves
        for step_id in wave.nodes
    }

    run_id = str(parent_run_id or RunId.new(prefix="federation"))
    owner_identity = parent.repository_identity.as_dict() if parent.repository_identity else {}
    owner_fingerprint = str(owner_identity.get("fingerprint") or owner_identity.get("root") or parent.repo_root)
    started = time.monotonic()
    snapshot_lookup = snapshot_lookup or get_execution_store().snapshot
    request_child_cancel = request_child_cancel or (
        lambda child_run_id: request_federated_child_cancellation(child_run_id)
    )
    executor_fn = child_executor or _default_parallel_child_executor

    parent_lineage = {
        "kind": "federation",
        "relationship": "coordinator",
        "child_run_id": run_id,
        "federation_workflow": name,
        "source_repository": owner_fingerprint,
        "effective_repository": owner_fingerprint,
    }
    outer_parent = str(os.environ.get("DEVTOOL_FEDERATION_PARENT_RUN_ID") or "").strip()
    if outer_parent:
        parent_lineage.update({
            "relationship": "nested-federation",
            "parent_run_id": outer_parent,
            "parent_node_id": str(os.environ.get("DEVTOOL_FEDERATION_PARENT_NODE_ID") or ""),
            "target": str(os.environ.get("DEVTOOL_FEDERATION_TARGET") or ""),
            "child_workflow": str(os.environ.get("DEVTOOL_FEDERATION_CHILD_WORKFLOW") or ""),
            "federation_ref": str(os.environ.get("DEVTOOL_FEDERATION_REF") or ""),
            "requested_ref": str(os.environ.get("DEVTOOL_FEDERATION_REQUESTED_REF") or ""),
            "child_contract": str(os.environ.get("DEVTOOL_FEDERATION_CHILD_CONTRACT") or ""),
            "contract_ref": str(os.environ.get("DEVTOOL_FEDERATION_CONTRACT_REF") or ""),
            "hierarchy_depth": int(os.environ.get("DEVTOOL_FEDERATION_HIERARCHY_DEPTH") or "0") + 1,
        })
    if federation_group:
        parent_lineage["federation_group"] = federation_group
    publish(RunStarted(
        run_id=run_id,
        command=(
            f"federation run --group {federation_group} --parallel"
            if federation_group
            else f"federation run {name} --parallel"
        ),
        project=str(parent.repo_root),
        source="federation",
        metadata={
            "repository_identity": owner_identity,
            "run_lineage": parent_lineage,
            "federation_scheduler": schedule.as_dict(),
            "federation_source_lock": dict(plan.source_lock),
            "federation_source_verification": dict(source_verification),
        },
    ))

    ordered_steps = _ordered_steps(workflow)
    steps_by_id = {step.step_id: step for step in ordered_steps}
    node_ids = {step.step_id: _parent_node_id(name, step.step_id) for step in ordered_steps}
    for step in ordered_steps:
        scheduler_node = schedule_nodes[step.step_id]
        publish(NodeDiscovered(
            run_id=run_id,
            node_id=node_ids[step.step_id],
            label=_step_federation_ref(plan, step),
            kind="federation-child",
            depends_on=tuple(node_ids[dep] for dep in step.depends_on),
            source="federation",
            metadata={
                "federation_workflow": name,
                "federation_step": step.step_id,
                "repository": step.repository,
                "target": _step_target(plan, step),
                "workflow": _step_display_workflow(step),
                "contract_type": step.contract_type,
                "child_contract": _step_child_contract(step),
                "contract_ref": _step_contract_ref(plan, step),
                "federation_ref": _step_federation_ref(plan, step),
                "requested_ref": step.ref,
                "repository_identity": _step_identity(plan, step),
                "scheduler_state": "queued",
                "scheduler_wave": wave_for_step.get(step.step_id, 0),
                "scheduler": scheduler_node.as_dict(),
            },
        ))
    publish(GraphSealed(
        run_id=run_id,
        reason="federation child workflow set resolved with FRX-Y01 scheduling plan",
        source="federation",
    ))

    active_lock = threading.RLock()
    active_children: dict[str, str] = {}
    outcomes: dict[str, FederatedChildRun] = {}
    cancelled = False
    failed = False

    def cancel_active(*, except_run_id: str = "") -> None:
        with active_lock:
            run_ids = sorted(active_children)
        for child_run_id in run_ids:
            if child_run_id == except_run_id:
                continue
            try:
                request_child_cancel(child_run_id)
            except Exception:
                pass

    def execute_step(step: FederatedWorkflowStep) -> FederatedChildRun:
        resolution = resolve_federated_repository(parent, step.repository)
        if not resolution.available:
            raise FederationExecutionError(
                f"Federation repository {step.repository!r} is unavailable at execution time"
            )
        target = _step_target(plan, step)
        federation_ref = _step_federation_ref(plan, step)
        identity = _step_identity(plan, step)
        _assert_planned_resolution_identity(
            repository=step.repository,
            planned_identity=identity,
            resolution=resolution,
        )
        effective_fingerprint = str(identity.get("fingerprint") or identity.get("root") or resolution.root)
        scheduler_node = schedule_nodes[step.step_id]
        placement = dict(scheduler_node.as_dict().get("placement") or {})
        artifact_inputs, artifact_env = _resolved_artifact_inputs(
            step, outcomes, consumer_root=resolution.root, parent_run_id=run_id
        )
        resume_checkpoint = capture_federated_resume_checkpoint(resolution)
        child_run_id = str(RunId.new(prefix=f"fed-{step.repository}"))
        parent_node = node_ids[step.step_id]
        invocation_command = f"{name}:{step.step_id}"
        planned_git_lineage = _step_planned_git_lineage(
            plan, step, parent_run_id=run_id, parent_node_id=parent_node, child_run_id=child_run_id, target=target
        )
        lineage = {
            "kind": "federation",
            "relationship": "child",
            "parent_run_id": run_id,
            "parent_node_id": parent_node,
            "child_run_id": child_run_id,
            "federation_workflow": name,
            "federation_step": step.step_id,
            "repository": step.repository,
            "target": target,
            "child_workflow": _step_display_workflow(step),
            "child_contract": _step_child_contract(step),
            "contract_ref": _step_contract_ref(plan, step),
            "federation_ref": federation_ref,
            "requested_ref": step.ref,
            "source_repository": owner_fingerprint,
            "requested_repository": step.repository,
            "effective_repository": effective_fingerprint,
            "execution_environment": scheduler_node.execution_environment,
            "provider_placement": placement,
            "frozen_source_lock_digest": str(plan.source_lock.get("lock_digest") or ""),
            "frozen_source_equivalence_digest": str(plan.source_lock.get("equivalence_digest") or ""),
            "git_lineage": planned_git_lineage,
            "artifact_inputs": {key: value for key, value in artifact_inputs.items() if key != "__manifest__"},
        }
        from .federation_transport import build_repository_attestation, federation_attestation_environment
        attestation = build_repository_attestation(
            resolution.root,
            identity=RepositoryIdentity.from_dict(identity),
            config_path=resolution.config_path or None,
            lineage=lineage,
        )
        delegation_link = DelegationLink.from_federation(
            parent_run_id=run_id,
            parent_node_id=parent_node,
            child_run_id=child_run_id,
            lineage=lineage,
            repository_identity=identity,
            provider_identity=placement,
            attestation=attestation.as_dict(),
        )
        lineage["delegation_id"] = delegation_link.delegation_id
        lineage["delegation_link"] = delegation_link.as_dict()
        from .distributed_provider_control import DelegationRoute, record_delegation_relation
        dist_route = DelegationRoute.from_json(os.environ.get("DEVTOOL_DIST_ROUTE")).append(delegation_link)
        record_delegation_relation(parent.repo_root, delegation_link, delivery_health="starting")
        if federation_group:
            lineage["federation_group"] = federation_group

        child_state_dir = _parallel_child_state_directory(run_id, child_run_id)
        argv = (
            _child_argv(
                root=resolution.root,
                target=target,
                workflow=step.workflow,
                invocation_command=invocation_command,
                provider_profile=scheduler_node.provider_profile,
                execution_environment=scheduler_node.execution_environment,
            )
            if step.contract_type == "workflow"
            else [step.ref]
        )
        env = {
            "DEVTOOL_RUN_ID": child_run_id,
            "DEVTOOL_FEDERATION_RELATIONSHIP": "child",
            "DEVTOOL_FEDERATION_PARENT_RUN_ID": run_id,
            "DEVTOOL_FEDERATION_PARENT_NODE_ID": parent_node,
            "DEVTOOL_FEDERATION_WORKFLOW": name,
            "DEVTOOL_FEDERATION_STEP": step.step_id,
            "DEVTOOL_FEDERATION_REPOSITORY": step.repository,
            "DEVTOOL_FEDERATION_TARGET": target,
            "DEVTOOL_FEDERATION_CHILD_WORKFLOW": _step_display_workflow(step),
            "DEVTOOL_FEDERATION_CHILD_CONTRACT": _step_child_contract(step),
            "DEVTOOL_FEDERATION_CONTRACT_REF": _step_contract_ref(plan, step),
            "DEVTOOL_FEDERATION_REF": federation_ref,
            "DEVTOOL_FEDERATION_REQUESTED_REF": step.ref,
            "DEVTOOL_FEDERATION_SOURCE_REPOSITORY": owner_fingerprint,
            "DEVTOOL_FEDERATION_REQUESTED_REPOSITORY": step.repository,
            "DEVTOOL_FEDERATION_EFFECTIVE_REPOSITORY": effective_fingerprint,
            "DEVTOOL_FEDERATION_EXECUTION_ENVIRONMENT": scheduler_node.execution_environment,
            "DEVTOOL_FEDERATION_SOURCE_LOCK_DIGEST": str(plan.source_lock.get("lock_digest") or ""),
            "DEVTOOL_FEDERATION_SOURCE_EQUIVALENCE_DIGEST": str(plan.source_lock.get("equivalence_digest") or ""),
            "DEVTOOL_DELEGATION_LINK_ID": delegation_link.delegation_id,
            "DEVTOOL_DIST_DELEGATION_ID": delegation_link.delegation_id,
            "DEVTOOL_DIST_ATTESTATION_DIGEST": delegation_link.attestation_digest,
            "DEVTOOL_DIST_ROUTE": dist_route.to_json(),
            "DEVTOOL_FEDERATION_HIERARCHY_DEPTH": str(int(os.environ.get("DEVTOOL_FEDERATION_HIERARCHY_DEPTH") or "0") + (1 if step.hierarchical else 0)),
            # Keep each parallel child's HUD/EXO files isolated while
            # suppressing visible auto-HUD windows for internal children.
            "DEVTOOL_TASKER_DIR": str(child_state_dir),
            "DEVTOOL_NO_AUTO_HUD": "1",
            "DEVTOOL_FEDERATION_CHILD_STATE_DIR": str(child_state_dir),
        }
        if not scheduler_node.provider_profile and scheduler_node.execution_environment not in {"", "auto"}:
            env["DEVTOOL_PROJECT_EXECUTION_ENVIRONMENT"] = scheduler_node.execution_environment
        env.update(git_lineage_environment(planned_git_lineage))
        _attach_federation_lineage_handoff(env, lineage)
        env.update(artifact_env)
        if federation_group:
            env["DEVTOOL_FEDERATION_GROUP"] = federation_group
        env.update(federation_attestation_environment(attestation))

        live_forwarder: AsyncJournalForwarder | None = None
        live_transport_error = ""
        if publish is publish_event:
            parent_bus = get_event_bus()
            adapter = distributed_provider_adapter(
                "federation",
                child_state_dir,
                provider_identity=placement,
            )
            cursor_store = adapter.cursor_store(delegation_link)
            durable_cursor = cursor_store.load(link=delegation_link)
            receiver = DelegatedEventReceiver.from_cursor(
                ExternalEventIngestor(parent_bus),
                link=delegation_link,
                cursor=durable_cursor,
            )
            loopback = LocalLoopbackTransport(receiver)
            live_forwarder = AsyncJournalForwarder(
                link=delegation_link,
                journal=adapter.origin_journal(delegation_link),
                cursor_store=cursor_store,
                send_frame=loopback.send,
                protocol=loopback.negotiated,
                queue_capacity=64,
                poll_interval=0.05,
            )
            # Parallel child execution is a distinct process/EventBus, so the
            # parent live bridge tails the child's canonical RUN journal. The
            # same forwarder can attach directly to an in-process child bus for
            # local-subprocess delegation without changing semantic protocol.
            live_forwarder.start()

        child_started = time.monotonic()
        with active_lock:
            active_children[child_run_id] = step.step_id
        publish(NodeStarted(
            run_id=run_id,
            node_id=parent_node,
            label=federation_ref,
            kind="federation-child",
            argv=tuple(argv),
            source="federation",
            metadata={
                "child_run_id": child_run_id,
                "repository": step.repository,
                "target": target,
                "workflow": _step_display_workflow(step),
                "contract_type": step.contract_type,
                "child_contract": _step_child_contract(step),
                "contract_ref": _step_contract_ref(plan, step),
                "federation_ref": federation_ref,
                "requested_ref": step.ref,
                "run_lineage": lineage,
                "delegation_link": delegation_link.as_dict(),
                "resume_checkpoint": resume_checkpoint,
                "artifact_inputs": {key: value for key, value in artifact_inputs.items() if key != "__manifest__"},
                "scheduler_state": "running",
                "scheduler_wave": wave_for_step.get(step.step_id, 0),
                "scheduler": scheduler_node.as_dict(),
            },
        ))
        try:
            try:
                if step.contract_type == "workflow":
                    rc = int(executor_fn(argv, env))
                else:
                    rc = int(_execute_existing_child_contract(
                        parent,
                        step,
                        child_run_id=child_run_id,
                        lineage=lineage,
                        reenter=lambda child_argv: executor_fn(child_argv, env),
                        publish=publish,
                        provider_profile=scheduler_node.provider_profile,
                        execution_environment=scheduler_node.execution_environment,
                    ))
            except KeyboardInterrupt:
                try:
                    request_child_cancel(child_run_id)
                except Exception:
                    pass
                rc = 130

            if live_forwarder is not None:
                try:
                    live_forwarder.stop(drain=True, timeout=2.0)
                except Exception as exc:
                    # Delivery is secondary to canonical child execution. The
                    # durable child journal remains available for recovery
                    # import and the receipt records degraded transport health.
                    live_transport_error = str(exc)

            child_snapshot = snapshot_lookup(child_run_id)
            snapshot_terminal = bool(
                child_snapshot is not None
                and str(getattr(child_snapshot, "status", "") or "")
                in {
                    ExecutionStatus.SUCCEEDED.value,
                    ExecutionStatus.FAILED.value,
                    ExecutionStatus.BLOCKED.value,
                    ExecutionStatus.SKIPPED.value,
                    ExecutionStatus.CANCELLED.value,
                }
            )
            if (child_snapshot is None or not snapshot_terminal) and publish is publish_event:
                child_snapshot = import_child_run_journal(
                    child_state_dir,
                    child_run_id=child_run_id,
                    link=delegation_link,
                    bus=get_event_bus(),
                )
            if child_snapshot is None and child_executor is None and publish is publish_event:
                raise FederationExecutionError(
                    f"Parallel federation child {child_run_id!r} produced no canonical RUN journal to import"
                )
            if child_snapshot is None:
                # Compatibility only for injected/custom executors that predate
                # DIST-X01. Production parallel federation no longer treats the
                # lossy execution.json helper as authoritative child truth.
                child_snapshot = _persisted_execution_snapshot(child_state_dir, child_run_id)
            persisted_lineage = _assert_child_snapshot_authority(
                repository=step.repository,
                child_run_id=child_run_id,
                expected_identity=identity,
                expected_parent_run_id=run_id,
                expected_target=target,
                expected_child_workflow=_step_display_workflow(step),
                expected_federation_ref=federation_ref,
                expected_requested_ref=step.ref,
                expected_child_contract=(_step_child_contract(step) if step.contract_type != "workflow" else ""),
                expected_contract_ref=(_step_contract_ref(plan, step) if step.contract_type != "workflow" else ""),
                expected_federation_group=federation_group,
                snapshot=child_snapshot,
            ) if child_snapshot is not None else {}
            operational = collect_federated_operational_snapshot(
                resolution,
                child_run_id=child_run_id,
                execution_snapshot=child_snapshot,
                resume_checkpoint=resume_checkpoint,
            ).as_dict()
            terminal = reconcile_terminal_truth(transport_exit_code=rc, child_snapshot=child_snapshot)
            child_status = terminal.authoritative_status
            rc = terminal.authoritative_exit_code
            delivery_receipt = {}
            if live_forwarder is not None:
                receipt = live_forwarder.receipt(
                    child_snapshot=child_snapshot,
                    transport_exit_code=rc,
                )
                delivery_receipt = receipt.as_dict()
                if live_transport_error:
                    delivery_receipt["secondary_transport_error"] = live_transport_error
            git_lineage = _finalize_child_git_lineage_preserving_primary_failure(
                resolution,
                planned_git_lineage,
                terminal_status=child_status,
                terminal_exit_code=rc,
                publish=publish,
            )
            publish(NodeFinished(
                run_id=run_id,
                node_id=parent_node,
                status=child_status,
                exit_code=rc,
                duration_seconds=time.monotonic() - child_started,
                source="federation",
                transition_reason=f"child run {child_run_id}; scheduler wave {wave_for_step.get(step.step_id, 0)}",
                metadata={
                    "child_run_id": child_run_id,
                    "repository": step.repository,
                    "target": target,
                    "workflow": _step_display_workflow(step),
                    "contract_type": step.contract_type,
                    "child_contract": _step_child_contract(step),
                    "contract_ref": _step_contract_ref(plan, step),
                    "federation_ref": federation_ref,
                    "requested_ref": step.ref,
                    "run_lineage": persisted_lineage or lineage,
                    "delegation_link": delegation_link.as_dict(),
                    "terminal_reconciliation": terminal.as_dict(),
                    "delegated_execution_receipt": delivery_receipt,
                    "operational_ownership": operational,
                    "scheduler_state": child_status,
                    "scheduler_wave": wave_for_step.get(step.step_id, 0),
                    "scheduler": scheduler_node.as_dict(),
                    "git_lineage": git_lineage,
                    "artifact_inputs": {key: value for key, value in artifact_inputs.items() if key != "__manifest__"},
                },
            ))
            return FederatedChildRun(
                step.step_id,
                step.repository,
                _step_display_workflow(step),
                target,
                child_run_id,
                parent_node,
                child_status,
                rc,
                identity,
                persisted_lineage or lineage,
                operational,
                federation_ref=federation_ref,
                requested_ref=step.ref,
                child_contract=_step_child_contract(step),
                contract_ref=_step_contract_ref(plan, step),
                git_lineage=git_lineage,
                lifecycle=(
                    "cancelled" if child_status == ExecutionStatus.CANCELLED.value else
                    "failed" if child_status != ExecutionStatus.SUCCEEDED.value or rc != 0 else
                    "executed"
                ),
                artifact_inputs=dict(artifact_inputs),
                placement=placement,
                delegation_link=delegation_link.as_dict(),
                terminal_reconciliation=terminal.as_dict(),
                delivery_receipt=delivery_receipt,
            )
        finally:
            if live_forwarder is not None:
                try:
                    live_forwarder.stop(drain=False, timeout=0.5)
                except Exception:
                    pass
            with active_lock:
                active_children.pop(child_run_id, None)

    try:
        for wave in schedule.waves:
            if cancelled:
                break
            runnable: list[FederatedWorkflowStep] = []
            for step_id in wave.nodes:
                step = steps_by_id[step_id]
                blocked_by = [
                    dep
                    for dep in step.depends_on
                    if outcomes.get(dep) and outcomes[dep].status != ExecutionStatus.SUCCEEDED.value
                ]
                if blocked_by:
                    publish(NodeBlocked(
                        run_id=run_id,
                        node_id=node_ids[step.step_id],
                        blocked_by=tuple(node_ids[dep] for dep in blocked_by),
                        reason="federation dependency did not succeed",
                        source="federation",
                    ))
                    outcomes[step.step_id] = FederatedChildRun(
                        step.step_id,
                        step.repository,
                        _step_display_workflow(step),
                        _step_target(plan, step),
                        "",
                        node_ids[step.step_id],
                        ExecutionStatus.BLOCKED.value,
                        1,
                        _step_identity(plan, step),
                        {},
                        {},
                        federation_ref=_step_federation_ref(plan, step),
                        requested_ref=step.ref,
                        child_contract=_step_child_contract(step),
                        contract_ref=_step_contract_ref(plan, step),
                        lifecycle="blocked",
                        placement=dict(schedule_nodes[step.step_id].as_dict().get("placement") or {}),
                    )
                    failed = True
                else:
                    runnable.append(step)

            if not runnable:
                continue
            def record_child(child: FederatedChildRun) -> None:
                nonlocal cancelled, failed
                outcomes[child.step_id] = child
                if child.status == ExecutionStatus.CANCELLED.value or child.exit_code == 130:
                    cancelled = True
                    # Cancellation is terminal for the federation run. Request
                    # cancellation of still-running siblings immediately rather
                    # than waiting for the whole wave to drain.
                    cancel_active(except_run_id=child.run_id)
                elif child.status != ExecutionStatus.SUCCEEDED.value or child.exit_code != 0:
                    failed = True

            if len(runnable) == 1:
                record_child(execute_step(runnable[0]))
            else:
                with ThreadPoolExecutor(
                    max_workers=min(schedule.max_parallel, len(runnable)),
                    thread_name_prefix="devtool-federation",
                ) as pool:
                    futures = {pool.submit(execute_step, step): step.step_id for step in runnable}
                    for future in as_completed(futures):
                        # Record each terminal child as soon as it finishes so a
                        # cancelled child can stop active siblings deterministically.
                        record_child(future.result())
    except KeyboardInterrupt:
        cancelled = True
        cancel_active()
    except BaseException:
        cancel_active()
        publish(RunFinished(
            run_id=run_id,
            status=ExecutionStatus.FAILED.value,
            exit_code=1,
            duration_seconds=time.monotonic() - started,
            source="federation",
        ))
        raise

    if cancelled:
        for step in ordered_steps:
            if step.step_id in outcomes:
                continue
            publish(NodeFinished(
                run_id=run_id,
                node_id=node_ids[step.step_id],
                status=ExecutionStatus.CANCELLED.value,
                exit_code=130,
                duration_seconds=0.0,
                source="federation",
                transition_reason="federation cancelled before scheduling",
                metadata={
                    "scheduler_state": ExecutionStatus.CANCELLED.value,
                    "scheduler_wave": wave_for_step.get(step.step_id, 0),
                    "scheduler": schedule_nodes[step.step_id].as_dict(),
                },
            ))
            outcomes[step.step_id] = FederatedChildRun(
                step.step_id,
                step.repository,
                _step_display_workflow(step),
                _step_target(plan, step),
                "",
                node_ids[step.step_id],
                ExecutionStatus.CANCELLED.value,
                130,
                _step_identity(plan, step),
                {},
                {},
                federation_ref=_step_federation_ref(plan, step),
                requested_ref=step.ref,
                child_contract=_step_child_contract(step),
                contract_ref=_step_contract_ref(plan, step),
                lifecycle="cancelled",
                placement=dict(schedule_nodes[step.step_id].as_dict().get("placement") or {}),
            )

    parent_status = (
        ExecutionStatus.CANCELLED.value
        if cancelled
        else ExecutionStatus.FAILED.value
        if failed
        else ExecutionStatus.SUCCEEDED.value
    )
    parent_exit = 130 if cancelled else 1 if failed else 0
    publish(RunFinished(
        run_id=run_id,
        status=parent_status,
        exit_code=parent_exit,
        duration_seconds=time.monotonic() - started,
        source="federation",
    ))
    ordered_children = tuple(
        outcomes[step.step_id]
        for step in ordered_steps
        if step.step_id in outcomes
    )
    return FederatedRunReport(
        name,
        run_id,
        parent_status,
        parent_exit,
        ordered_children,
        plan,
        schedule=schedule.as_dict(),
    )


def run_federated_workflow(
    parent: ProjectConfig,
    workflow_name: str,
    *,
    reenter: Callable[[list[str]], int],
    publish: Callable[[Any], None] = publish_event,
    snapshot_lookup: Callable[[str], Any] | None = None,
    parent_run_id: str = "",
    request_child_cancel: Callable[[str], Any] | None = None,
    parallel: bool = False,
    max_parallel: int = 0,
    child_executor: ParallelChildExecutor | None = None,
    drift_policy: str = "fail",
    reuse_children: Mapping[str, FederatedChildRun] | None = None,
    lifecycle_actions: Mapping[str, str] | None = None,
    resumed_from: str = "",
    resume_mode: str = "run",
) -> FederatedRunReport:
    """Execute one federation workflow, optionally through FRX-Y01 safe waves.

    Parallelism is deliberately opt-in.  The legacy sequential coordinator is
    used unchanged unless ``parallel`` or ``max_parallel`` requests the new
    scheduler.
    """

    requested = bool(parallel or int(max_parallel or 0) > 0)
    if reuse_children and requested:
        raise FederationExecutionError("Federation resume/retry currently uses dependency-safe sequential replay; omit --parallel")
    if not requested:
        report = _run_federated_workflow_sequential(
            parent,
            workflow_name,
            reenter=reenter,
            publish=publish,
            snapshot_lookup=snapshot_lookup,
            parent_run_id=parent_run_id,
            request_child_cancel=request_child_cancel,
            drift_policy=drift_policy,
            reuse_children=reuse_children,
            lifecycle_actions=lifecycle_actions,
        )
    else:
        report = _run_federated_workflow_parallel(
            parent,
            workflow_name,
            reenter=reenter,
            publish=publish,
            snapshot_lookup=snapshot_lookup,
            parent_run_id=parent_run_id,
            request_child_cancel=request_child_cancel,
            max_parallel=max_parallel,
            child_executor=child_executor,
            drift_policy=drift_policy,
        )
    from .federation_resume import checkpoint_report
    lifecycle = {
        "mode": str(resume_mode or "run"),
        "resumed_from": str(resumed_from or ""),
        "provider_placements": {
            child.step_id: dict(child.placement) for child in report.children if child.placement
        },
        "counts": {
            key: sum(1 for child in report.children if child.lifecycle == key)
            for key in ("reused", "retried", "rerun_due_to_drift", "blocked", "failed", "cancelled")
        },
    }
    workflow = parent.federation.workflows.get(workflow_name)
    if workflow is not None:
        from .git_federation_integration import publish_barrier_evidence
        lifecycle["publish_barrier"] = publish_barrier_evidence(workflow, report.children)
        lifecycle["integration_policy"] = workflow.integration_policy
    if report.status == ExecutionStatus.CANCELLED.value:
        from .federation_recovery import cancellation_recovery_evidence
        lifecycle["cancellation_recovery"] = cancellation_recovery_evidence(report)
    report = replace(report, lifecycle=lifecycle)
    from .git_execution_notes import GitExecutionNoteError, persist_report_execution_notes
    try:
        note_writes = persist_report_execution_notes(parent, report)
    except GitExecutionNoteError as exc:
        raise FederationExecutionError(f"Unable to persist GIT-FRX-11 execution note evidence: {exc}") from exc
    if note_writes:
        lifecycle = dict(report.lifecycle)
        lifecycle["git_execution_notes"] = [dict(item) for item in note_writes]
        report = replace(report, lifecycle=lifecycle)
    checkpoint_report(parent, report, resumed_from=resumed_from, mode=resume_mode)
    return report



def run_federated_group(
    parent: ProjectConfig,
    group_name: str,
    *,
    reenter: Callable[[list[str]], int],
    publish: Callable[[Any], None] = publish_event,
    snapshot_lookup: Callable[[str], Any] | None = None,
    parent_run_id: str = "",
    request_child_cancel: Callable[[str], Any] | None = None,
    parallel: bool = False,
    max_parallel: int = 0,
    child_executor: ParallelChildExecutor | None = None,
    drift_policy: str = "fail",
) -> FederatedRunReport:
    """Execute an executable federation group through the normal FRX workflow coordinator."""

    scoped, workflow = project_with_federation_group(parent, group_name)
    return run_federated_workflow(
        scoped,
        workflow.name,
        reenter=reenter,
        publish=publish,
        snapshot_lookup=snapshot_lookup,
        parent_run_id=parent_run_id,
        request_child_cancel=request_child_cancel,
        parallel=parallel,
        max_parallel=max_parallel,
        child_executor=child_executor,
        drift_policy=drift_policy,
    )


def run_federated_direct_child(
    parent: ProjectConfig,
    repository: str,
    *,
    operation: str,
    invocation_argv: Iterable[str],
    execute: Callable[[str, dict[str, Any]], int],
    target: str = "",
    child_contract: str = "",
    publish: Callable[[Any], None] = publish_event,
    snapshot_lookup: Callable[[str], Any] | None = None,
    parent_run_id: str = "",
    request_child_cancel: Callable[[str], Any] | None = None,
) -> FederatedChildRun:
    """Run one explicitly selected federated child contract with EXO lineage.

    FRX-07 uses this for wrapper and target-local-job bridges.  The callback is
    responsible for the child execution itself; this helper owns only the
    federation coordinator run/node, lineage environment, and parent/child
    correlation.
    """

    repo_name = str(repository or "").strip()
    if not repo_name:
        raise FederationExecutionError("Federated direct execution requires a repository name")
    resolution = resolve_federated_repository(parent, repo_name)
    if not resolution.available:
        raise FederationExecutionError(f"Federation repository {repo_name!r} is unavailable")
    identity = resolution.repository_identity.as_dict() if resolution.repository_identity else {}
    owner_identity = parent.repository_identity.as_dict() if parent.repository_identity else {}
    owner_fingerprint = str(owner_identity.get("fingerprint") or owner_identity.get("root") or parent.repo_root)
    effective_fingerprint = str(identity.get("fingerprint") or identity.get("root") or resolution.root)
    resume_checkpoint = capture_federated_resume_checkpoint(resolution)
    parent_id = str(parent_run_id or RunId.new(prefix="federation-exec"))
    child_run_id = str(RunId.new(prefix=f"fed-{repo_name}"))
    node_id = f"federation:exec:{repo_name}:{operation}"
    started = time.monotonic()
    snapshot_lookup = snapshot_lookup or get_execution_store().snapshot
    request_child_cancel = request_child_cancel or (lambda run_id: request_federated_child_cancellation(run_id))
    argv = tuple(str(value) for value in invocation_argv)
    direct_target = str(target or "").strip()
    direct_contract = str(child_contract or operation or "").strip()
    contract_ref = f"{repo_name}::{direct_target}::{direct_contract}" if direct_target and direct_contract else ""
    planned_git_lineage = direct_federated_git_lineage(
        resolution.root,
        repository=repo_name,
        parent_run_id=parent_id,
        parent_node_id=node_id,
        child_run_id=child_run_id,
        target=direct_target,
        contract_type=(direct_contract.split(":", 1)[0] if ":" in direct_contract else "direct"),
        contract_name=direct_contract,
        contract_ref=contract_ref,
        federation_step=operation,
    )
    lineage = {
        "kind": "federation",
        "relationship": "child",
        "parent_run_id": parent_id,
        "parent_node_id": node_id,
        "child_run_id": child_run_id,
        "federation_workflow": "@direct",
        "federation_step": operation,
        "repository": repo_name,
        "target": direct_target,
        "child_contract": direct_contract,
        "contract_ref": contract_ref,
        "source_repository": owner_fingerprint,
        "requested_repository": repo_name,
        "effective_repository": effective_fingerprint,
        "execution_environment": "auto",
        "git_lineage": planned_git_lineage,
    }
    publish(RunStarted(
        run_id=parent_id,
        command=f"federation exec {repo_name} {operation}",
        project=str(parent.repo_root),
        source="federation",
        metadata={
            "repository_identity": owner_identity,
            "run_lineage": {
                "kind": "federation",
                "relationship": "coordinator",
                "child_run_id": parent_id,
                "federation_workflow": "@direct",
                "federation_step": operation,
                "source_repository": owner_fingerprint,
                "effective_repository": owner_fingerprint,
            },
        },
    ))
    publish(NodeDiscovered(
        run_id=parent_id,
        node_id=node_id,
        label=f"{repo_name}::{operation}",
        kind="federation-child",
        source="federation",
        metadata={
            "repository": repo_name,
            "target": direct_target,
            "child_contract": direct_contract,
            "contract_ref": contract_ref,
            "repository_identity": identity,
        },
    ))
    publish(GraphSealed(run_id=parent_id, reason="direct federation child resolved", source="federation"))
    publish(NodeStarted(
        run_id=parent_id,
        node_id=node_id,
        label=f"{repo_name}::{operation}",
        kind="federation-child",
        argv=argv,
        source="federation",
        metadata={
            "child_run_id": child_run_id,
            "repository": repo_name,
            "target": direct_target,
            "child_contract": direct_contract,
            "contract_ref": contract_ref,
            "run_lineage": lineage,
            "resume_checkpoint": resume_checkpoint,
        },
    ))
    env = {
        "DEVTOOL_RUN_ID": child_run_id,
        "DEVTOOL_FEDERATION_RELATIONSHIP": "child",
        "DEVTOOL_FEDERATION_PARENT_RUN_ID": parent_id,
        "DEVTOOL_FEDERATION_PARENT_NODE_ID": node_id,
        "DEVTOOL_FEDERATION_WORKFLOW": "@direct",
        "DEVTOOL_FEDERATION_STEP": operation,
        "DEVTOOL_FEDERATION_REPOSITORY": repo_name,
        "DEVTOOL_FEDERATION_TARGET": direct_target,
        "DEVTOOL_FEDERATION_CHILD_CONTRACT": direct_contract,
        "DEVTOOL_FEDERATION_CONTRACT_REF": contract_ref,
        "DEVTOOL_FEDERATION_SOURCE_REPOSITORY": owner_fingerprint,
        "DEVTOOL_FEDERATION_REQUESTED_REPOSITORY": repo_name,
        "DEVTOOL_FEDERATION_EFFECTIVE_REPOSITORY": effective_fingerprint,
        "DEVTOOL_FEDERATION_EXECUTION_ENVIRONMENT": "auto",
    }
    env.update(git_lineage_environment(planned_git_lineage))
    _attach_federation_lineage_handoff(env, lineage)
    from .federation_transport import build_repository_attestation, federation_attestation_environment
    env.update(federation_attestation_environment(build_repository_attestation(
        resolution.root,
        identity=RepositoryIdentity.from_dict(identity),
        config_path=resolution.config_path or None,
        lineage=lineage,
    )))
    child_started = time.monotonic()
    try:
        with _federation_child_environment(env):
            rc = int(execute(child_run_id, lineage))
        snapshot = snapshot_lookup(child_run_id)
        persisted_lineage = _assert_child_snapshot_authority(
            repository=repo_name,
            child_run_id=child_run_id,
            expected_identity=identity,
            expected_parent_run_id=parent_id,
            snapshot=snapshot,
        ) if snapshot is not None else {}
        operational = collect_federated_operational_snapshot(
            resolution,
            child_run_id=child_run_id,
            execution_snapshot=snapshot,
            resume_checkpoint=resume_checkpoint,
        ).as_dict()
        status = (
            str(getattr(snapshot, "status", "") or "")
            if snapshot is not None
            else (ExecutionStatus.SUCCEEDED.value if rc == 0 else ExecutionStatus.CANCELLED.value if rc == 130 else ExecutionStatus.FAILED.value)
        )
        if rc == 130 or status == ExecutionStatus.CANCELLED.value:
            status = ExecutionStatus.CANCELLED.value
            rc = 130
        git_lineage = _finalize_child_git_lineage(
            resolution, planned_git_lineage, terminal_status=status, publish=publish
        )
        publish(NodeFinished(
            run_id=parent_id,
            node_id=node_id,
            status=status,
            exit_code=rc,
            duration_seconds=time.monotonic() - child_started,
            source="federation",
            transition_reason=f"child run {child_run_id}",
            metadata={
                "child_run_id": child_run_id,
                "run_lineage": persisted_lineage or lineage,
                "operational_ownership": operational,
                "git_lineage": git_lineage,
            },
        ))
        publish(RunFinished(
            run_id=parent_id,
            status=status,
            exit_code=rc,
            duration_seconds=time.monotonic() - started,
            source="federation",
        ))
        return FederatedChildRun(
            operation, repo_name, operation, direct_target, child_run_id, node_id, status, rc,
            identity, persisted_lineage or lineage, operational,
            child_contract=direct_contract, contract_ref=contract_ref,
            git_lineage=git_lineage,
        )
    except KeyboardInterrupt:
        try:
            request_child_cancel(child_run_id)
        except Exception:
            pass
        snapshot = snapshot_lookup(child_run_id)
        persisted_lineage = dict(getattr(snapshot, "run_lineage", {}) or {}) if snapshot is not None else {}
        try:
            cancelled_git_lineage = _finalize_child_git_lineage(
                resolution, planned_git_lineage, terminal_status=ExecutionStatus.CANCELLED.value, publish=publish
            )
        except Exception as exc:
            cancelled_git_lineage = _cancelled_git_lineage_fallback(
                dict(planned_git_lineage), exc, publish=publish
            )
        publish(NodeFinished(
            run_id=parent_id,
            node_id=node_id,
            status=ExecutionStatus.CANCELLED.value,
            exit_code=130,
            duration_seconds=time.monotonic() - child_started,
            source="federation",
            transition_reason=f"child run {child_run_id} cancelled by interrupt",
            metadata={
                "child_run_id": child_run_id,
                "run_lineage": persisted_lineage or lineage,
                "git_lineage": cancelled_git_lineage,
            },
        ))
        publish(RunFinished(
            run_id=parent_id,
            status=ExecutionStatus.CANCELLED.value,
            exit_code=130,
            duration_seconds=time.monotonic() - started,
            source="federation",
        ))
        raise
    except BaseException:
        publish(RunFinished(
            run_id=parent_id,
            status=ExecutionStatus.FAILED.value,
            exit_code=1,
            duration_seconds=time.monotonic() - started,
            source="federation",
        ))
        raise


__all__ = [
    "FederationExecutionError",
    "FederatedChildRun",
    "FederatedRunReport",
    "run_federated_workflow",
    "run_federated_group",
    "run_federated_direct_child",
]
