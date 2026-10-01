from __future__ import annotations

"""Canonical end-of-run lifecycle finalization for EXO-backed CLI execution."""

from dataclasses import dataclass
from typing import Any, Callable

from .execution_contract import ExecutionStatus, canonical_execution_status
from .events.bus import EventBus
from .events.events import GraphSealed, NodeBlocked, NodeFinished, RunFinished
from .events.runtime import get_event_bus


class ExecutionLifecycleError(RuntimeError):
    """Raised when a CLI run cannot be sealed as authoritative terminal truth."""


@dataclass(frozen=True, slots=True)
class ExecutionLifecycleAudit:
    run_id: str
    status: str
    graph_sealed: bool
    nonterminal_nodes: tuple[str, ...]
    active_processes: tuple[str, ...]

    @property
    def terminal(self) -> bool:
        return self.status in {"succeeded", "failed", "blocked", "skipped", "cancelled"}

    @property
    def clean(self) -> bool:
        return self.terminal and self.graph_sealed and not self.nonterminal_nodes and not self.active_processes


def audit_execution_lifecycle(run_id: str, *, bus: EventBus | None = None) -> ExecutionLifecycleAudit:
    target = bus or get_event_bus()
    snapshot = target.execution_store.snapshot(run_id)
    if snapshot is None:
        return ExecutionLifecycleAudit(
            run_id=str(run_id),
            status="planned",
            graph_sealed=False,
            nonterminal_nodes=(),
            active_processes=(),
        )
    terminal = {"succeeded", "failed", "blocked", "skipped", "cancelled"}
    nonterminal_nodes = tuple(node.node_id for node in snapshot.nodes if str(node.status) not in terminal)
    active_processes = tuple(process.process_id for process in snapshot.processes if str(process.status) == "running")
    return ExecutionLifecycleAudit(
        run_id=snapshot.run_id,
        status=snapshot.status,
        graph_sealed=bool(snapshot.graph_sealed),
        nonterminal_nodes=nonterminal_nodes,
        active_processes=active_processes,
    )


def _reconcile_nonterminal_nodes(
    run_id: str,
    *,
    requested: ExecutionStatus,
    bus: EventBus,
    publish: Callable[[Any], None],
) -> tuple[ExecutionStatus, tuple[str, ...]]:
    """Terminalize dangling execution nodes without fabricating success.

    This is a final lifecycle safety net for orchestration paths that have
    already stopped discovering/executing work but failed to emit the last
    node terminal event (for example worker exceptions, fail-fast exits, or
    historical interrupted runs).  Observed terminal truth is never rewritten.
    A requested successful run with unresolved required work fails closed.
    """
    snapshot = bus.execution_store.snapshot(run_id)
    if snapshot is None:
        return requested, ()
    terminal = {"succeeded", "failed", "blocked", "skipped", "cancelled"}
    unresolved = [node for node in snapshot.nodes if node.status not in terminal]
    if not unresolved:
        return requested, ()

    effective = requested
    fail_closed_from_success = requested is ExecutionStatus.SUCCEEDED
    if fail_closed_from_success:
        # Never convert unobserved work into success merely to make accounting
        # close.  Persist a failed run instead and let the CLI return non-zero.
        effective = ExecutionStatus.FAILED

    existing_failed = any(node.status == "failed" for node in snapshot.nodes)
    primary_failure_assigned = existing_failed
    reconciled: list[str] = []
    for node in unresolved:
        status = str(node.status or "planned")
        reason = (
            f"execution finalization reconciled dangling {status} node after "
            f"terminal run outcome {effective.value}"
        )
        if effective is ExecutionStatus.CANCELLED:
            publish(NodeFinished(
                run_id=str(run_id),
                node_id=node.node_id,
                parent_id=node.parent_id,
                status="cancelled",
                exit_code=130,
                failure_kind="lifecycle_reconciliation",
                source="execution-finalizer",
                transition_origin="lifecycle_finalizer_inferred",
                transition_reason=reason,
            ))
        elif effective in {ExecutionStatus.FAILED, ExecutionStatus.BLOCKED}:
            if status == "running":
                inferred = "cancelled"
                exit_code = 130
                if effective is ExecutionStatus.FAILED and not primary_failure_assigned:
                    inferred = "failed"
                    exit_code = 1
                    primary_failure_assigned = True
                publish(NodeFinished(
                    run_id=str(run_id),
                    node_id=node.node_id,
                    parent_id=node.parent_id,
                    status=inferred,
                    exit_code=exit_code,
                    failure_kind="lifecycle_reconciliation",
                    source="execution-finalizer",
                    transition_origin="lifecycle_finalizer_inferred",
                    transition_reason=reason,
                ))
            else:
                if fail_closed_from_success and not primary_failure_assigned:
                    publish(NodeFinished(
                        run_id=str(run_id),
                        node_id=node.node_id,
                        parent_id=node.parent_id,
                        status="failed",
                        exit_code=1,
                        failure_kind="unobserved_required_work",
                        source="execution-finalizer",
                        transition_origin="lifecycle_finalizer_inferred",
                        transition_reason=reason,
                    ))
                    primary_failure_assigned = True
                else:
                    blocked_by = tuple(
                        dep for dep in node.depends_on
                        if (snapshot.node(dep) is not None and snapshot.node(dep).status in {"failed", "blocked", "cancelled"})
                    )
                    publish(NodeBlocked(
                        run_id=str(run_id),
                        node_id=node.node_id,
                        parent_id=node.parent_id,
                        blocked_by=blocked_by,
                        reason=reason,
                        source="execution-finalizer",
                    ))
        else:
            publish(NodeFinished(
                run_id=str(run_id),
                node_id=node.node_id,
                parent_id=node.parent_id,
                status="skipped",
                exit_code=0,
                failure_kind="lifecycle_reconciliation",
                source="execution-finalizer",
                transition_origin="lifecycle_finalizer_inferred",
                transition_reason=reason,
            ))
        reconciled.append(node.node_id)
    return effective, tuple(reconciled)


def finalize_execution_run(
    run_id: str,
    *,
    status: str | ExecutionStatus,
    exit_code: int,
    duration_seconds: float = 0.0,
    source: str = "cli",
    seal_reason: str = "CLI execution discovery complete",
    bus: EventBus | None = None,
    publish: Callable[[Any], None] | None = None,
) -> Any:
    """Seal and terminalize one EXO run exactly once.

    The CLI must not report completion while canonical EXO truth still contains
    unresolved nodes or live processes. Graph sealing is emitted before the
    terminal run event so durable subscribers can persist a final execution
    snapshot and history record. Repeated calls are idempotent when they ask
    for the same terminal outcome.
    """
    target = bus or get_event_bus()
    publisher = publish or target.publish
    requested = canonical_execution_status(status)
    if not requested.terminal:
        raise ExecutionLifecycleError(
            f"execution finalization requires a terminal status, got {requested.value!r}"
        )

    snapshot = target.execution_store.snapshot(run_id)
    if snapshot is None:
        # Publisher interception is used by protocol tests and embedders. Keep
        # the lifecycle envelope observable even when that publisher deliberately
        # does not reduce into the process-global store.
        publisher(GraphSealed(
            run_id=str(run_id),
            reason=str(seal_reason or "CLI execution discovery complete"),
            source=source,
        ))
        publisher(RunFinished(
            run_id=str(run_id),
            status=requested.value,
            exit_code=int(exit_code),
            duration_seconds=max(0.0, float(duration_seconds or 0.0)),
            source=source,
        ))
        return None

    audit = audit_execution_lifecycle(run_id, bus=target)
    if audit.active_processes:
        # A live process is materially different from missing terminal node
        # bookkeeping: never infer process exit while the process may still be
        # running.  The caller must stop/reap it first.
        raise ExecutionLifecycleError(
            "cannot finalize execution run with active process(es): " + ", ".join(audit.active_processes)
        )

    requested, _reconciled = _reconcile_nonterminal_nodes(
        run_id, requested=requested, bus=target, publish=publisher
    )
    if requested is ExecutionStatus.FAILED and int(exit_code or 0) == 0:
        exit_code = 1
    audit = audit_execution_lifecycle(run_id, bus=target)
    if audit.nonterminal_nodes:
        raise ExecutionLifecycleError(
            "cannot finalize execution run after lifecycle reconciliation; nonterminal node(s): "
            + ", ".join(audit.nonterminal_nodes)
        )

    snapshot = target.execution_store.snapshot(run_id)
    assert snapshot is not None
    if not snapshot.graph_sealed:
        publisher(
            GraphSealed(
                run_id=str(run_id),
                reason=str(seal_reason or "CLI execution discovery complete"),
                source=source,
            )
        )
        snapshot = target.execution_store.snapshot(run_id)
        assert snapshot is not None

    if snapshot.status in {"succeeded", "failed", "blocked", "skipped", "cancelled"}:
        if snapshot.status != requested.value:
            raise ExecutionLifecycleError(
                f"execution run {run_id!r} already ended as {snapshot.status!r}, "
                f"cannot finalize as {requested.value!r}"
            )
    else:
        publisher(
            RunFinished(
                run_id=str(run_id),
                status=requested.value,
                exit_code=int(exit_code),
                duration_seconds=max(0.0, float(duration_seconds or 0.0)),
                source=source,
            )
        )

    final = audit_execution_lifecycle(run_id, bus=target)
    if not final.clean:
        raise ExecutionLifecycleError(
            f"execution run {run_id!r} did not reach sealed terminal truth: "
            f"status={final.status!r} graph_sealed={final.graph_sealed!r} "
            f"nonterminal_nodes={list(final.nonterminal_nodes)!r} "
            f"active_processes={list(final.active_processes)!r}"
        )
    return target.execution_store.snapshot(run_id)


__all__ = [
    "ExecutionLifecycleAudit",
    "ExecutionLifecycleError",
    "audit_execution_lifecycle",
    "finalize_execution_run",
]
