from __future__ import annotations

"""Canonical mutable execution state and immutable UI/API snapshots.

EXO-03 is the first live-truth layer in the execution convergence roadmap.
The workflow DAG remains planning truth; these structures hold runtime state
for those same node identities plus dynamically discovered child operations.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

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
    blocked_reason: str = ""


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
    blocked_reason: str

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
            blocked_reason=state.blocked_reason,
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
            "blocked_reason": self.blocked_reason,
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
    counts: ExecutionCounts
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

    @classmethod
    def from_state(cls, state: ExecutionRunState) -> "ExecutionSnapshot":
        return cls(
            schema_version=1,
            run_id=state.run_id,
            status=state.status.value,
            command=state.command,
            project=state.project,
            counts=state.counts(),
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
            "counts": self.counts.to_dict(),
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
    "ExecutionProgressSnapshot",
    "ExecutionNodeSnapshot",
    "ExecutionProcessSnapshot",
    "ExecutionDiagnosticSnapshot",
    "ExecutionArtifactSnapshot",
    "ExecutionSnapshot",
]
