from __future__ import annotations

"""FRX-06 federated execution coordinator and EXO parent/child lineage.

The coordinator owns one parent EXO run.  Each configured federation workflow
step re-enters canonical Devtool with a distinct child run id.  Child work
therefore remains independently inspectable and never shares the coordinator's
authoritative run identity.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
import os
from pathlib import Path
import time
from typing import Any, Callable, Iterable

from .core.execution_contract import ExecutionStatus, RunId
from .core.events import (
    GraphSealed,
    NodeBlocked,
    NodeDiscovered,
    NodeFinished,
    NodeStarted,
    RunFinished,
    RunStarted,
    get_execution_store,
    publish_event,
)
from .federation_discovery import resolve_federated_repository
from .federation_plan import FederatedPlanGraph, build_federated_plan
from .models import FederatedWorkflowConfig, FederatedWorkflowStep, ProjectConfig


class FederationExecutionError(RuntimeError):
    pass


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
        }


@dataclass(frozen=True, slots=True)
class FederatedRunReport:
    workflow: str
    parent_run_id: str
    status: str
    exit_code: int
    children: tuple[FederatedChildRun, ...]
    plan: FederatedPlanGraph

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.status == ExecutionStatus.SUCCEEDED.value

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "workflow": self.workflow,
            "parent_run_id": self.parent_run_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "children": [child.as_dict() for child in self.children],
            "plan": self.plan.as_dict(),
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


def _step_identity(plan: FederatedPlanGraph, step: FederatedWorkflowStep) -> dict[str, Any]:
    for node in plan.nodes:
        if node.synthetic or str(node.metadata.get("federation_step") or "") != step.step_id:
            continue
        identity = node.metadata.get("repository_identity")
        if isinstance(identity, dict):
            return dict(identity)
    return {}


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
) -> list[str]:
    return [
        "--repo", root,
        "--internal-invocation-source", "federation",
        "--internal-invocation-command", invocation_command,
        "invoke", workflow,
        "--target", target,
    ]


def run_federated_workflow(
    parent: ProjectConfig,
    workflow_name: str,
    *,
    reenter: Callable[[list[str]], int],
    publish: Callable[[Any], None] = publish_event,
    snapshot_lookup: Callable[[str], Any] | None = None,
    parent_run_id: str = "",
) -> FederatedRunReport:
    """Execute a configured federation workflow sequentially with EXO lineage.

    Scheduling is deliberately conservative in FRX-06: dependency-ready child
    workflow invocations execute one at a time.  Parallel federation scheduling
    can be layered later without changing the run-lineage contract.
    """

    name = str(workflow_name or "").strip()
    workflow = parent.federation.workflows.get(name)
    if workflow is None:
        available = ", ".join(sorted(parent.federation.workflows)) or "<none>"
        raise FederationExecutionError(f"Unknown federation workflow {name!r}. Available workflows: {available}")

    plan = build_federated_plan(parent, name)
    run_id = str(parent_run_id or RunId.new(prefix="federation"))
    owner_identity = parent.repository_identity.as_dict() if parent.repository_identity else {}
    owner_fingerprint = str(owner_identity.get("fingerprint") or owner_identity.get("root") or parent.repo_root)
    started = time.monotonic()
    snapshot_lookup = snapshot_lookup or get_execution_store().snapshot

    parent_lineage = {
        "kind": "federation",
        "relationship": "coordinator",
        "child_run_id": run_id,
        "federation_workflow": name,
        "source_repository": owner_fingerprint,
        "effective_repository": owner_fingerprint,
    }
    publish(RunStarted(
        run_id=run_id,
        command=f"federation run {name}",
        project=str(parent.repo_root),
        source="federation",
        metadata={"repository_identity": owner_identity, "run_lineage": parent_lineage},
    ))

    ordered_steps = _ordered_steps(workflow)
    node_ids = {step.step_id: _parent_node_id(name, step.step_id) for step in ordered_steps}
    for step in ordered_steps:
        publish(NodeDiscovered(
            run_id=run_id,
            node_id=node_ids[step.step_id],
            label=f"{step.repository}::{step.workflow}",
            kind="federation-child",
            depends_on=tuple(node_ids[dep] for dep in step.depends_on),
            source="federation",
            metadata={
                "federation_workflow": name,
                "federation_step": step.step_id,
                "repository": step.repository,
                "workflow": step.workflow,
                "repository_identity": _step_identity(plan, step),
            },
        ))
    publish(GraphSealed(run_id=run_id, reason="federation child workflow set resolved", source="federation"))

    outcomes: dict[str, FederatedChildRun] = {}
    children: list[FederatedChildRun] = []
    parent_status = ExecutionStatus.SUCCEEDED.value
    parent_exit = 0

    try:
        for step in ordered_steps:
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
                    step.step_id, step.repository, step.workflow, _step_target(plan, step), "",
                    node_ids[step.step_id], ExecutionStatus.BLOCKED.value, 1,
                    _step_identity(plan, step), {},
                )
                outcomes[step.step_id] = blocked
                children.append(blocked)
                parent_status = ExecutionStatus.FAILED.value
                if parent_exit == 0:
                    parent_exit = 1
                continue

            resolution = resolve_federated_repository(parent, step.repository)
            if not resolution.available:
                raise FederationExecutionError(f"Federation repository {step.repository!r} is unavailable at execution time")
            target = _step_target(plan, step)
            identity = _step_identity(plan, step)
            effective_fingerprint = str(identity.get("fingerprint") or identity.get("root") or resolution.root)
            child_run_id = str(RunId.new(prefix=f"fed-{step.repository}"))
            parent_node = node_ids[step.step_id]
            invocation_command = f"{name}:{step.step_id}"
            lineage = {
                "kind": "federation",
                "relationship": "child",
                "parent_run_id": run_id,
                "parent_node_id": parent_node,
                "child_run_id": child_run_id,
                "federation_workflow": name,
                "federation_step": step.step_id,
                "repository": step.repository,
                "source_repository": owner_fingerprint,
                "requested_repository": step.repository,
                "effective_repository": effective_fingerprint,
                "execution_environment": "auto",
            }
            publish(NodeStarted(
                run_id=run_id,
                node_id=parent_node,
                label=f"{step.repository}::{step.workflow}",
                kind="federation-child",
                argv=tuple(_child_argv(root=resolution.root, target=target, workflow=step.workflow, invocation_command=invocation_command)),
                source="federation",
                metadata={"child_run_id": child_run_id, "run_lineage": lineage},
            ))

            env = {
                "DEVTOOL_RUN_ID": child_run_id,
                "DEVTOOL_FEDERATION_RELATIONSHIP": "child",
                "DEVTOOL_FEDERATION_PARENT_RUN_ID": run_id,
                "DEVTOOL_FEDERATION_PARENT_NODE_ID": parent_node,
                "DEVTOOL_FEDERATION_WORKFLOW": name,
                "DEVTOOL_FEDERATION_STEP": step.step_id,
                "DEVTOOL_FEDERATION_REPOSITORY": step.repository,
                "DEVTOOL_FEDERATION_SOURCE_REPOSITORY": owner_fingerprint,
                "DEVTOOL_FEDERATION_REQUESTED_REPOSITORY": step.repository,
                "DEVTOOL_FEDERATION_EFFECTIVE_REPOSITORY": effective_fingerprint,
                "DEVTOOL_FEDERATION_EXECUTION_ENVIRONMENT": "auto",
            }
            child_started = time.monotonic()
            with _federation_child_environment(env):
                rc = int(reenter(_child_argv(
                    root=resolution.root,
                    target=target,
                    workflow=step.workflow,
                    invocation_command=invocation_command,
                )))
            child_snapshot = snapshot_lookup(child_run_id)
            persisted_lineage = dict(getattr(child_snapshot, "run_lineage", {}) or {}) if child_snapshot is not None else {}
            child_status = (
                str(getattr(child_snapshot, "status", "") or "")
                if child_snapshot is not None
                else (ExecutionStatus.SUCCEEDED.value if rc == 0 else ExecutionStatus.CANCELLED.value if rc == 130 else ExecutionStatus.FAILED.value)
            )
            if child_snapshot is not None and persisted_lineage.get("parent_run_id") != run_id:
                raise FederationExecutionError(
                    f"Child run {child_run_id!r} did not persist parent federation lineage {run_id!r}"
                )
            publish(NodeFinished(
                run_id=run_id,
                node_id=parent_node,
                status=child_status,
                exit_code=rc,
                duration_seconds=time.monotonic() - child_started,
                source="federation",
                transition_reason=f"child run {child_run_id}",
                metadata={"child_run_id": child_run_id, "run_lineage": persisted_lineage or lineage},
            ))
            child = FederatedChildRun(
                step.step_id, step.repository, step.workflow, target, child_run_id,
                parent_node, child_status, rc, identity, persisted_lineage or lineage,
            )
            outcomes[step.step_id] = child
            children.append(child)
            if child_status == ExecutionStatus.CANCELLED.value or rc == 130:
                parent_status = ExecutionStatus.CANCELLED.value
                parent_exit = 130
            elif child_status != ExecutionStatus.SUCCEEDED.value or rc != 0:
                parent_status = ExecutionStatus.FAILED.value
                parent_exit = rc or 1
    except KeyboardInterrupt:
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

    publish(RunFinished(
        run_id=run_id,
        status=parent_status,
        exit_code=parent_exit,
        duration_seconds=time.monotonic() - started,
        source="federation",
    ))
    return FederatedRunReport(name, run_id, parent_status, parent_exit, tuple(children), plan)


def run_federated_direct_child(
    parent: ProjectConfig,
    repository: str,
    *,
    operation: str,
    invocation_argv: Iterable[str],
    execute: Callable[[str, dict[str, Any]], int],
    publish: Callable[[Any], None] = publish_event,
    snapshot_lookup: Callable[[str], Any] | None = None,
    parent_run_id: str = "",
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
    parent_id = str(parent_run_id or RunId.new(prefix="federation-exec"))
    child_run_id = str(RunId.new(prefix=f"fed-{repo_name}"))
    node_id = f"federation:exec:{repo_name}:{operation}"
    started = time.monotonic()
    snapshot_lookup = snapshot_lookup or get_execution_store().snapshot
    argv = tuple(str(value) for value in invocation_argv)
    lineage = {
        "kind": "federation",
        "relationship": "child",
        "parent_run_id": parent_id,
        "parent_node_id": node_id,
        "child_run_id": child_run_id,
        "federation_workflow": "@direct",
        "federation_step": operation,
        "repository": repo_name,
        "source_repository": owner_fingerprint,
        "requested_repository": repo_name,
        "effective_repository": effective_fingerprint,
        "execution_environment": "auto",
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
        metadata={"repository": repo_name, "repository_identity": identity},
    ))
    publish(GraphSealed(run_id=parent_id, reason="direct federation child resolved", source="federation"))
    publish(NodeStarted(
        run_id=parent_id,
        node_id=node_id,
        label=f"{repo_name}::{operation}",
        kind="federation-child",
        argv=argv,
        source="federation",
        metadata={"child_run_id": child_run_id, "run_lineage": lineage},
    ))
    env = {
        "DEVTOOL_RUN_ID": child_run_id,
        "DEVTOOL_FEDERATION_RELATIONSHIP": "child",
        "DEVTOOL_FEDERATION_PARENT_RUN_ID": parent_id,
        "DEVTOOL_FEDERATION_PARENT_NODE_ID": node_id,
        "DEVTOOL_FEDERATION_WORKFLOW": "@direct",
        "DEVTOOL_FEDERATION_STEP": operation,
        "DEVTOOL_FEDERATION_REPOSITORY": repo_name,
        "DEVTOOL_FEDERATION_SOURCE_REPOSITORY": owner_fingerprint,
        "DEVTOOL_FEDERATION_REQUESTED_REPOSITORY": repo_name,
        "DEVTOOL_FEDERATION_EFFECTIVE_REPOSITORY": effective_fingerprint,
        "DEVTOOL_FEDERATION_EXECUTION_ENVIRONMENT": "auto",
    }
    child_started = time.monotonic()
    try:
        with _federation_child_environment(env):
            rc = int(execute(child_run_id, lineage))
        snapshot = snapshot_lookup(child_run_id)
        persisted_lineage = dict(getattr(snapshot, "run_lineage", {}) or {}) if snapshot is not None else {}
        status = (
            str(getattr(snapshot, "status", "") or "")
            if snapshot is not None
            else (ExecutionStatus.SUCCEEDED.value if rc == 0 else ExecutionStatus.CANCELLED.value if rc == 130 else ExecutionStatus.FAILED.value)
        )
        if snapshot is not None and persisted_lineage.get("parent_run_id") != parent_id:
            raise FederationExecutionError(
                f"Child run {child_run_id!r} did not persist direct federation lineage {parent_id!r}"
            )
        publish(NodeFinished(
            run_id=parent_id,
            node_id=node_id,
            status=status,
            exit_code=rc,
            duration_seconds=time.monotonic() - child_started,
            source="federation",
            transition_reason=f"child run {child_run_id}",
            metadata={"child_run_id": child_run_id, "run_lineage": persisted_lineage or lineage},
        ))
        publish(RunFinished(
            run_id=parent_id,
            status=status,
            exit_code=rc,
            duration_seconds=time.monotonic() - started,
            source="federation",
        ))
        return FederatedChildRun(
            operation, repo_name, operation, "", child_run_id, node_id, status, rc,
            identity, persisted_lineage or lineage,
        )
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
    "run_federated_direct_child",
]
