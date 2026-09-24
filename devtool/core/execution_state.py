from __future__ import annotations

"""Canonical mutable execution state and immutable UI/API snapshots.

EXO-03 is the first live-truth layer in the execution convergence roadmap.
The workflow DAG remains planning truth; these structures hold runtime state
for those same node identities plus dynamically discovered child operations.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable
import math

from .execution_contract import ExecutionStatus, canonical_execution_status


@dataclass(slots=True)
class ExecutionProgressState:
    current: float | None = None
    total: float | None = None
    percent: float | None = None
    unit: str = ""
    indeterminate: bool = False
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "current": self.current,
            "total": self.total,
            "percent": self.percent,
            "unit": self.unit,
            "indeterminate": self.indeterminate,
            "detail": self.detail,
        }


@dataclass(slots=True)
class ExecutionProcessState:
    process_id: str
    node_id: str = ""
    pid: int | None = None
    argv: tuple[str, ...] = ()
    cwd: str = ""
    status: str = "running"
    exit_code: int | None = None
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    stdout_events: int = 0
    stderr_events: int = 0
    last_output: str = ""
    failure_kind: str = ""
    log_path: str = ""
    evidence: tuple[str, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionDiagnosticState:
    diagnostic_id: str
    node_id: str = ""
    severity: str = "warning"
    code: str = ""
    message: str = ""
    file: str = ""
    line: int | None = None
    column: int | None = None
    process_id: str = ""
    failure_kind: str = ""
    log_path: str = ""
    evidence: tuple[str, ...] = ()
    timestamp: str = ""
    source: str = ""


@dataclass(slots=True)
class ExecutionArtifactState:
    artifact_id: str
    node_id: str = ""
    path: str = ""
    sha256: str = ""
    size: int | None = None
    artifact_type: str = ""
    producer_task: str = ""
    producer_node_id: str = ""
    process_id: str = ""
    verification_status: str = ""
    registry_artifact_id: str = ""
    timestamp: str = ""
    source: str = ""


@dataclass(slots=True)
class ExecutionNodeState:
    node_id: str
    parent_id: str = ""
    label: str = ""
    kind: str = "operation"
    status: ExecutionStatus = ExecutionStatus.PLANNED
    depends_on: tuple[str, ...] = ()
    blocked_by: tuple[str, ...] = ()
    argv: tuple[str, ...] = ()
    progress: ExecutionProgressState = field(default_factory=ExecutionProgressState)
    process_ids: list[str] = field(default_factory=list)
    diagnostic_ids: list[str] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    discovered_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    exit_code: int | None = None
    duration_seconds: float = 0.0
    failure_kind: str = ""
    failure_process_id: str = ""
    primary_failure_diagnostic_id: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    blocked_reason: str = ""
    transition_origin: str = ""
    transition_reason: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionCounts:
    total: int = 0
    planned: int = 0
    pending: int = 0
    ready: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    blocked: int = 0
    skipped: int = 0
    cancelled: int = 0
    terminal: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "planned": self.planned,
            "pending": self.pending,
            "ready": self.ready,
            "running": self.running,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "blocked": self.blocked,
            "skipped": self.skipped,
            "cancelled": self.cancelled,
            "terminal": self.terminal,
        }


@dataclass(slots=True)
class ExecutionRunState:
    run_id: str
    status: ExecutionStatus = ExecutionStatus.PLANNED
    command: str = ""
    project: str = ""
    invocation_source: str = ""
    invocation_command: str = ""
    invocation_metadata: dict[str, Any] = field(default_factory=dict)
    nodes: dict[str, ExecutionNodeState] = field(default_factory=dict)
    processes: dict[str, ExecutionProcessState] = field(default_factory=dict)
    diagnostics: dict[str, ExecutionDiagnosticState] = field(default_factory=dict)
    artifacts: dict[str, ExecutionArtifactState] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    exit_code: int | None = None
    duration_seconds: float = 0.0
    last_sequence: int = -1
    last_event_id: str = ""
    applied_event_ids: set[str] = field(default_factory=set)
    graph_sources: list[dict[str, Any]] = field(default_factory=list)
    graph_sealed: bool = False
    graph_sealed_at: str = ""
    graph_seal_reason: str = ""

    def counts(self) -> ExecutionCounts:
        values = list(self.nodes.values())
        by_status = {status: 0 for status in ExecutionStatus}
        for node in values:
            by_status[node.status] += 1
        terminal = sum(1 for node in values if node.status.terminal)
        return ExecutionCounts(
            total=len(values),
            planned=by_status[ExecutionStatus.PLANNED],
            pending=by_status[ExecutionStatus.PENDING],
            ready=by_status[ExecutionStatus.READY],
            running=by_status[ExecutionStatus.RUNNING],
            succeeded=by_status[ExecutionStatus.SUCCEEDED],
            failed=by_status[ExecutionStatus.FAILED],
            blocked=by_status[ExecutionStatus.BLOCKED],
            skipped=by_status[ExecutionStatus.SKIPPED],
            cancelled=by_status[ExecutionStatus.CANCELLED],
            terminal=terminal,
        )


@dataclass(frozen=True, slots=True)
class ExecutionMeasuredProgress:
    """Measured work progress, deliberately separate from lifecycle resolution.

    ``measured_percent`` is computed only from explicit progress telemetry. A
    failed/blocked/skipped terminal node is never rewritten to 100% merely
    because its lifecycle resolved. ``coverage_percent`` tells consumers how
    much of the selected scope has measurable telemetry.
    """

    measured_percent: float | None = None
    coverage_percent: float | None = None
    resolution_percent: float | None = None
    measured_nodes: int = 0
    indeterminate_nodes: int = 0
    unmeasured_nodes: int = 0
    determinate: bool = False
    total_weight: float = 0.0
    measured_weight: float = 0.0
    graph_sealed: bool = False
    total_is_final: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "measured_percent": self.measured_percent,
            "coverage_percent": self.coverage_percent,
            "resolution_percent": self.resolution_percent,
            "measured_nodes": self.measured_nodes,
            "indeterminate_nodes": self.indeterminate_nodes,
            "unmeasured_nodes": self.unmeasured_nodes,
            "determinate": self.determinate,
            "total_weight": self.total_weight,
            "measured_weight": self.measured_weight,
            "graph_sealed": self.graph_sealed,
            "total_is_final": self.total_is_final,
        }


@dataclass(frozen=True, slots=True)
class ExecutionAccountingScope:
    scope: str
    parent_id: str
    counts: ExecutionCounts
    progress: ExecutionMeasuredProgress

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "parent_id": self.parent_id,
            "counts": self.counts.to_dict(),
            "progress": self.progress.to_dict(),
        }


def _counts_for_nodes(nodes: Iterable[ExecutionNodeState]) -> ExecutionCounts:
    values = list(nodes)
    by_status = {status: 0 for status in ExecutionStatus}
    for node in values:
        by_status[node.status] += 1
    terminal = sum(1 for node in values if node.status.terminal)
    return ExecutionCounts(
        total=len(values),
        planned=by_status[ExecutionStatus.PLANNED],
        pending=by_status[ExecutionStatus.PENDING],
        ready=by_status[ExecutionStatus.READY],
        running=by_status[ExecutionStatus.RUNNING],
        succeeded=by_status[ExecutionStatus.SUCCEEDED],
        failed=by_status[ExecutionStatus.FAILED],
        blocked=by_status[ExecutionStatus.BLOCKED],
        skipped=by_status[ExecutionStatus.SKIPPED],
        cancelled=by_status[ExecutionStatus.CANCELLED],
        terminal=terminal,
    )


def _progress_weight(node: ExecutionNodeState) -> float:
    raw = node.metadata.get("progress_weight", 1.0)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(value) or value <= 0:
        return 1.0
    return value


def _explicit_percent(node: ExecutionNodeState) -> float | None:
    progress = node.progress
    if progress.percent is not None:
        return max(0.0, min(100.0, float(progress.percent)))
    if progress.current is not None and progress.total is not None and float(progress.total) > 0:
        return max(0.0, min(100.0, float(progress.current) / float(progress.total) * 100.0))
    return None


def scoped_execution_nodes(
    run: ExecutionRunState,
    *,
    scope: str = "recursive",
    parent_id: str = "",
) -> list[ExecutionNodeState]:
    """Select direct, recursive, or leaf work units without implicit double counting."""

    mode = str(scope or "recursive").strip().lower()
    if mode not in {"direct", "recursive", "leaf"}:
        raise ValueError(f"Unknown execution accounting scope: {scope!r}")

    children: dict[str, list[ExecutionNodeState]] = {}
    for node in run.nodes.values():
        children.setdefault(str(node.parent_id or ""), []).append(node)

    if parent_id:
        roots = list(children.get(parent_id, ()))
    else:
        roots = list(children.get("", ()))
        # Legacy/planned workflow nodes can all be root-level. If no explicit
        # roots exist, retain all known nodes rather than returning an empty
        # summary for a partially migrated producer.
        if not roots and run.nodes:
            roots = list(run.nodes.values())

    if mode == "direct":
        return roots

    selected: list[ExecutionNodeState] = []
    seen: set[str] = set()
    stack = list(reversed(roots))
    while stack:
        node = stack.pop()
        if node.node_id in seen:
            continue
        seen.add(node.node_id)
        selected.append(node)
        stack.extend(reversed(children.get(node.node_id, ())))

    if not parent_id and len(selected) < len(run.nodes):
        # Preserve disconnected dynamic nodes in whole-run recursive/leaf views.
        for node in run.nodes.values():
            if node.node_id not in seen:
                selected.append(node)
                seen.add(node.node_id)

    if mode == "leaf":
        return [node for node in selected if not children.get(node.node_id)]
    return selected


def execution_accounting_scope(
    run: ExecutionRunState,
    *,
    scope: str = "recursive",
    parent_id: str = "",
) -> ExecutionAccountingScope:
    nodes = scoped_execution_nodes(run, scope=scope, parent_id=parent_id)
    counts = _counts_for_nodes(nodes)
    total_weight = sum(_progress_weight(node) for node in nodes)
    measured_weight = 0.0
    weighted_sum = 0.0
    measured_nodes = 0
    indeterminate_nodes = 0

    for node in nodes:
        pct = _explicit_percent(node)
        weight = _progress_weight(node)
        if pct is not None:
            measured_nodes += 1
            measured_weight += weight
            weighted_sum += pct * weight
        elif node.progress.indeterminate or node.status is ExecutionStatus.RUNNING:
            indeterminate_nodes += 1

    measured_percent = weighted_sum / measured_weight if measured_weight > 0 else None
    coverage_percent = measured_weight / total_weight * 100.0 if total_weight > 0 else None
    resolution_percent = counts.terminal / counts.total * 100.0 if counts.total > 0 else None
    progress = ExecutionMeasuredProgress(
        measured_percent=measured_percent,
        coverage_percent=coverage_percent,
        resolution_percent=resolution_percent,
        measured_nodes=measured_nodes,
        indeterminate_nodes=indeterminate_nodes,
        unmeasured_nodes=max(0, counts.total - measured_nodes),
        determinate=bool(counts.total > 0 and measured_weight >= total_weight and indeterminate_nodes == 0),
        total_weight=total_weight,
        measured_weight=measured_weight,
        graph_sealed=bool(run.graph_sealed),
        total_is_final=bool(run.graph_sealed),
    )
    return ExecutionAccountingScope(scope=scope, parent_id=parent_id, counts=counts, progress=progress)


@dataclass(frozen=True, slots=True)
class ExecutionAccountingSnapshot:
    direct: ExecutionAccountingScope
    recursive: ExecutionAccountingScope
    leaf: ExecutionAccountingScope

    @classmethod
    def from_state(cls, state: ExecutionRunState) -> "ExecutionAccountingSnapshot":
        return cls(
            direct=execution_accounting_scope(state, scope="direct"),
            recursive=execution_accounting_scope(state, scope="recursive"),
            leaf=execution_accounting_scope(state, scope="leaf"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "direct": self.direct.to_dict(),
            "recursive": self.recursive.to_dict(),
            "leaf": self.leaf.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ExecutionProgressSnapshot:
    current: float | None
    total: float | None
    percent: float | None
    unit: str
    indeterminate: bool
    detail: str

    @classmethod
    def from_state(cls, state: ExecutionProgressState) -> "ExecutionProgressSnapshot":
        return cls(
            current=state.current,
            total=state.total,
            percent=state.percent,
            unit=state.unit,
            indeterminate=state.indeterminate,
            detail=state.detail,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "current": self.current,
            "total": self.total,
            "percent": self.percent,
            "unit": self.unit,
            "indeterminate": self.indeterminate,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ExecutionNodeSnapshot:
    node_id: str
    parent_id: str
    label: str
    kind: str
    status: str
    depends_on: tuple[str, ...]
    blocked_by: tuple[str, ...]
    argv: tuple[str, ...]
    progress: ExecutionProgressSnapshot
    process_ids: tuple[str, ...]
    diagnostic_ids: tuple[str, ...]
    artifact_ids: tuple[str, ...]
    metrics: dict[str, Any]
    metadata: dict[str, Any]
    discovered_at: str
    started_at: str
    finished_at: str
    exit_code: int | None
    duration_seconds: float
    failure_kind: str
    failure_process_id: str
    primary_failure_diagnostic_id: str
    evidence_refs: tuple[str, ...]
    blocked_reason: str
    transition_origin: str
    transition_reason: str

    @classmethod
    def from_state(cls, state: ExecutionNodeState) -> "ExecutionNodeSnapshot":
        return cls(
            node_id=state.node_id,
            parent_id=state.parent_id,
            label=state.label,
            kind=state.kind,
            status=state.status.value,
            depends_on=tuple(state.depends_on),
            blocked_by=tuple(state.blocked_by),
            argv=tuple(state.argv),
            progress=ExecutionProgressSnapshot.from_state(state.progress),
            process_ids=tuple(state.process_ids),
            diagnostic_ids=tuple(state.diagnostic_ids),
            artifact_ids=tuple(state.artifact_ids),
            metrics=deepcopy(state.metrics),
            metadata=deepcopy(state.metadata),
            discovered_at=state.discovered_at,
            started_at=state.started_at,
            finished_at=state.finished_at,
            exit_code=state.exit_code,
            duration_seconds=state.duration_seconds,
            failure_kind=state.failure_kind,
            failure_process_id=state.failure_process_id,
            primary_failure_diagnostic_id=state.primary_failure_diagnostic_id,
            evidence_refs=tuple(state.evidence_refs),
            blocked_reason=state.blocked_reason,
            transition_origin=state.transition_origin,
            transition_reason=state.transition_reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "parent_id": self.parent_id,
            "label": self.label,
            "kind": self.kind,
            "status": self.status,
            "depends_on": list(self.depends_on),
            "blocked_by": list(self.blocked_by),
            "argv": list(self.argv),
            "progress": self.progress.to_dict(),
            "process_ids": list(self.process_ids),
            "diagnostic_ids": list(self.diagnostic_ids),
            "artifact_ids": list(self.artifact_ids),
            "metrics": deepcopy(self.metrics),
            "metadata": deepcopy(self.metadata),
            "discovered_at": self.discovered_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "failure_kind": self.failure_kind,
            "failure_process_id": self.failure_process_id,
            "primary_failure_diagnostic_id": self.primary_failure_diagnostic_id,
            "evidence_refs": list(self.evidence_refs),
            "blocked_reason": self.blocked_reason,
            "transition_origin": self.transition_origin,
            "transition_reason": self.transition_reason,
        }


@dataclass(frozen=True, slots=True)
class ExecutionProcessSnapshot:
    process_id: str
    node_id: str
    pid: int | None
    argv: tuple[str, ...]
    cwd: str
    status: str
    exit_code: int | None
    started_at: str
    finished_at: str
    duration_seconds: float
    stdout_events: int
    stderr_events: int
    last_output: str
    failure_kind: str
    log_path: str
    evidence: tuple[str, ...]
    provenance: dict[str, Any]

    @classmethod
    def from_state(cls, state: ExecutionProcessState) -> "ExecutionProcessSnapshot":
        return cls(**{name: getattr(state, name) for name in cls.__dataclass_fields__})

    def to_dict(self) -> dict[str, Any]:
        return {
            "process_id": self.process_id,
            "node_id": self.node_id,
            "pid": self.pid,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "status": self.status,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "stdout_events": self.stdout_events,
            "stderr_events": self.stderr_events,
            "last_output": self.last_output,
            "failure_kind": self.failure_kind,
            "log_path": self.log_path,
            "evidence": list(self.evidence),
            "provenance": deepcopy(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class ExecutionDiagnosticSnapshot:
    diagnostic_id: str
    node_id: str
    severity: str
    code: str
    message: str
    file: str
    line: int | None
    column: int | None
    process_id: str
    failure_kind: str
    log_path: str
    evidence: tuple[str, ...]
    timestamp: str
    source: str

    @classmethod
    def from_state(cls, state: ExecutionDiagnosticState) -> "ExecutionDiagnosticSnapshot":
        return cls(**{name: getattr(state, name) for name in cls.__dataclass_fields__})

    def to_dict(self) -> dict[str, Any]:
        payload = {name: getattr(self, name) for name in self.__dataclass_fields__}
        payload["evidence"] = list(self.evidence)
        return payload


@dataclass(frozen=True, slots=True)
class ExecutionArtifactSnapshot:
    artifact_id: str
    node_id: str
    path: str
    sha256: str
    size: int | None
    artifact_type: str
    producer_task: str
    producer_node_id: str
    process_id: str
    verification_status: str
    registry_artifact_id: str
    timestamp: str
    source: str

    @classmethod
    def from_state(cls, state: ExecutionArtifactState) -> "ExecutionArtifactSnapshot":
        return cls(**{name: getattr(state, name) for name in cls.__dataclass_fields__})

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class ExecutionSnapshot:
    schema_version: int
    run_id: str
    status: str
    command: str
    project: str
    invocation_source: str
    invocation_command: str
    invocation_metadata: dict[str, Any]
    counts: ExecutionCounts
    accounting: ExecutionAccountingSnapshot
    nodes: tuple[ExecutionNodeSnapshot, ...]
    processes: tuple[ExecutionProcessSnapshot, ...]
    diagnostics: tuple[ExecutionDiagnosticSnapshot, ...]
    artifacts: tuple[ExecutionArtifactSnapshot, ...]
    metrics: dict[str, Any]
    started_at: str
    finished_at: str
    exit_code: int | None
    duration_seconds: float
    last_sequence: int
    last_event_id: str
    graph_sources: tuple[dict[str, Any], ...]
    graph_sealed: bool
    graph_sealed_at: str
    graph_seal_reason: str

    @classmethod
    def from_state(cls, state: ExecutionRunState) -> "ExecutionSnapshot":
        return cls(
            schema_version=6,
            run_id=state.run_id,
            status=state.status.value,
            command=state.command,
            project=state.project,
            invocation_source=state.invocation_source,
            invocation_command=state.invocation_command,
            invocation_metadata=deepcopy(state.invocation_metadata),
            counts=state.counts(),
            accounting=ExecutionAccountingSnapshot.from_state(state),
            nodes=tuple(ExecutionNodeSnapshot.from_state(item) for item in state.nodes.values()),
            processes=tuple(ExecutionProcessSnapshot.from_state(item) for item in state.processes.values()),
            diagnostics=tuple(ExecutionDiagnosticSnapshot.from_state(item) for item in state.diagnostics.values()),
            artifacts=tuple(ExecutionArtifactSnapshot.from_state(item) for item in state.artifacts.values()),
            metrics=deepcopy(state.metrics),
            started_at=state.started_at,
            finished_at=state.finished_at,
            exit_code=state.exit_code,
            duration_seconds=state.duration_seconds,
            last_sequence=state.last_sequence,
            last_event_id=state.last_event_id,
            graph_sources=tuple(deepcopy(item) for item in state.graph_sources),
            graph_sealed=bool(state.graph_sealed),
            graph_sealed_at=state.graph_sealed_at,
            graph_seal_reason=state.graph_seal_reason,
        )

    def node(self, node_id: str) -> ExecutionNodeSnapshot | None:
        return next((node for node in self.nodes if node.node_id == node_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "status": self.status,
            "command": self.command,
            "project": self.project,
            "invocation_source": self.invocation_source,
            "invocation_command": self.invocation_command,
            "invocation_metadata": deepcopy(self.invocation_metadata),
            "counts": self.counts.to_dict(),
            "accounting": self.accounting.to_dict(),
            "nodes": [item.to_dict() for item in self.nodes],
            "processes": [item.to_dict() for item in self.processes],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "artifacts": [item.to_dict() for item in self.artifacts],
            "metrics": deepcopy(self.metrics),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "last_sequence": self.last_sequence,
            "last_event_id": self.last_event_id,
            "graph_sources": [deepcopy(item) for item in self.graph_sources],
            "graph_sealed": self.graph_sealed,
            "graph_sealed_at": self.graph_sealed_at,
            "graph_seal_reason": self.graph_seal_reason,
        }


def status_from_value(value: object, *, default: ExecutionStatus) -> ExecutionStatus:
    return canonical_execution_status(value, default=default)


__all__ = [
    "ExecutionProgressState",
    "ExecutionProcessState",
    "ExecutionDiagnosticState",
    "ExecutionArtifactState",
    "ExecutionNodeState",
    "ExecutionRunState",
    "ExecutionCounts",
    "ExecutionMeasuredProgress",
    "ExecutionAccountingScope",
    "ExecutionAccountingSnapshot",
    "scoped_execution_nodes",
    "execution_accounting_scope",
    "ExecutionProgressSnapshot",
    "ExecutionNodeSnapshot",
    "ExecutionProcessSnapshot",
    "ExecutionDiagnosticSnapshot",
    "ExecutionArtifactSnapshot",
    "ExecutionSnapshot",
]
