from __future__ import annotations

"""Typed execution event protocol.

EXO-02 turns the original coarse event classes into a versioned execution
protocol while preserving their public constructors and class names.  The
EventBus completes the envelope (sequence/event id and legacy identity
promotion) at publication time; later overlays can therefore reduce both new
and legacy events into one execution store without a flag-day migration.
"""

from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, ClassVar

from ..execution_contract import EventId, RunId, canonical_status_value, ExecutionStatus

EXECUTION_EVENT_SCHEMA_VERSION = 2
_DEFAULT_RUN_ID = str(RunId.current(create=True) or RunId.new(prefix="run"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _metadata_text(metadata: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = metadata.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


@dataclass(slots=True)
class DevtoolEvent:
    """Common envelope carried by every Devtool execution event.

    ``metadata`` remains only as a compatibility/extensions bag.  Identity,
    ordering, source and the fields required to understand canonical events
    are first-class properties and must not be hidden solely in metadata.
    """

    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = EXECUTION_EVENT_SCHEMA_VERSION
    run_id: str = ""
    event_id: str = ""
    sequence: int = -1
    timestamp: str = field(default_factory=_utc_now)
    node_id: str = ""
    parent_id: str = ""
    source: str = "devtool"

    protocol_type: ClassVar[str] = "execution.event"

    def promote_legacy_identity(self) -> None:
        """Promote old metadata identity fields into the typed envelope."""
        metadata = self.metadata if isinstance(self.metadata, dict) else {}
        if not self.run_id:
            self.run_id = _metadata_text(metadata, "run_id")
        if not self.node_id:
            self.node_id = _metadata_text(metadata, "node_id", "workflow_node_key")
        if not self.parent_id:
            self.parent_id = _metadata_text(metadata, "parent_id", "workflow_parent_id")
        if not self.source or self.source == "devtool":
            explicit_source = _metadata_text(metadata, "event_source", "source")
            if explicit_source:
                self.source = explicit_source

    def complete_envelope(self, *, sequence: int) -> None:
        self.promote_legacy_identity()
        self.sequence = int(sequence)
        if self.sequence < 0:
            raise ValueError("event sequence cannot be negative")
        if not self.timestamp:
            self.timestamp = _utc_now()
        if not self.run_id:
            self.run_id = str(RunId.current(create=False) or _DEFAULT_RUN_ID)
        expected_event_id = str(EventId.for_sequence(self.run_id, self.sequence))
        if self.event_id and self.event_id != expected_event_id:
            raise ValueError(
                f"event_id {self.event_id!r} does not match run/sequence identity {expected_event_id!r}"
            )
        self.event_id = expected_event_id


@dataclass(slots=True)
class RunStarted(DevtoolEvent):
    command: str = ""
    project: str = ""

    protocol_type: ClassVar[str] = "run.started"


@dataclass(slots=True)
class RunFinished(DevtoolEvent):
    status: str = ExecutionStatus.SUCCEEDED.value
    exit_code: int = 0
    duration_seconds: float = 0.0

    protocol_type: ClassVar[str] = "run.finished"

    def __post_init__(self) -> None:
        self.status = canonical_status_value(
            self.status,
            default=ExecutionStatus.SUCCEEDED if int(self.exit_code or 0) == 0 else ExecutionStatus.FAILED,
        )


@dataclass(slots=True)
class GraphSealed(DevtoolEvent):
    reason: str = ""

    protocol_type: ClassVar[str] = "graph.sealed"


@dataclass(slots=True)
class NodeDiscovered(DevtoolEvent):
    label: str = ""
    kind: str = "operation"
    depends_on: tuple[str, ...] = ()

    protocol_type: ClassVar[str] = "node.discovered"

    def __post_init__(self) -> None:
        self.depends_on = tuple(str(value) for value in (self.depends_on or ()))


@dataclass(slots=True)
class NodeStarted(DevtoolEvent):
    label: str = ""
    kind: str = "operation"
    argv: tuple[str, ...] = ()

    protocol_type: ClassVar[str] = "node.started"

    def __post_init__(self) -> None:
        self.argv = tuple(str(value) for value in (self.argv or ()))


@dataclass(slots=True)
class NodeProgressChanged(DevtoolEvent):
    current: float | None = None
    total: float | None = None
    percent: float | None = None
    unit: str = ""
    indeterminate: bool = False
    detail: str = ""

    protocol_type: ClassVar[str] = "node.progress"

    def __post_init__(self) -> None:
        if self.percent is not None:
            self.percent = max(0.0, min(100.0, float(self.percent)))
        if self.current is not None:
            self.current = float(self.current)
        if self.total is not None:
            self.total = float(self.total)


@dataclass(slots=True)
class NodeFinished(DevtoolEvent):
    status: str = ExecutionStatus.SUCCEEDED.value
    exit_code: int = 0
    duration_seconds: float = 0.0
    failure_kind: str = ""

    protocol_type: ClassVar[str] = "node.finished"

    def __post_init__(self) -> None:
        fallback = ExecutionStatus.SUCCEEDED if int(self.exit_code or 0) == 0 else ExecutionStatus.FAILED
        self.status = canonical_status_value(self.status, default=fallback)


@dataclass(slots=True)
class NodeBlocked(DevtoolEvent):
    blocked_by: tuple[str, ...] = ()
    reason: str = ""

    protocol_type: ClassVar[str] = "node.blocked"

    def __post_init__(self) -> None:
        self.blocked_by = tuple(str(value) for value in (self.blocked_by or ()))


@dataclass(slots=True)
class ProcessStarted(DevtoolEvent):
    process_id: str = ""
    pid: int | None = None
    argv: tuple[str, ...] = ()
    cwd: str = ""

    protocol_type: ClassVar[str] = "process.started"

    def __post_init__(self) -> None:
        self.argv = tuple(str(value) for value in (self.argv or ()))
        if self.pid is not None:
            self.pid = int(self.pid)


@dataclass(slots=True)
class ProcessOutputReceived(DevtoolEvent):
    process_id: str = ""
    stream: str = "stdout"
    text: str = ""

    protocol_type: ClassVar[str] = "process.output"


@dataclass(slots=True)
class ProcessExited(DevtoolEvent):
    process_id: str = ""
    exit_code: int = 0
    duration_seconds: float = 0.0

    protocol_type: ClassVar[str] = "process.exited"


@dataclass(slots=True)
class DiagnosticEmitted(DevtoolEvent):
    severity: str = "warning"
    code: str = ""
    message: str = ""
    file: str = ""
    line: int | None = None
    column: int | None = None
    evidence: tuple[str, ...] = ()

    protocol_type: ClassVar[str] = "diagnostic.emitted"

    def __post_init__(self) -> None:
        self.severity = str(self.severity or "warning").strip().lower()
        if self.line is not None:
            self.line = int(self.line)
        if self.column is not None:
            self.column = int(self.column)
        self.evidence = tuple(str(value) for value in (self.evidence or ()))


@dataclass(slots=True)
class ArtifactProduced(DevtoolEvent):
    path: str = ""
    artifact_id: str = ""
    sha256: str = ""
    size: int | None = None
    artifact_type: str = ""
    producer_task: str = ""

    protocol_type: ClassVar[str] = "artifact.produced"

    def __post_init__(self) -> None:
        if self.size is not None:
            self.size = int(self.size)


@dataclass(slots=True)
class MetricChanged(DevtoolEvent):
    name: str = ""
    value: int | float | str | bool | None = None
    unit: str = ""

    protocol_type: ClassVar[str] = "metric.changed"


# ---------------------------------------------------------------------------
# Compatibility event names.  They are typed subclasses of the canonical
# protocol so old listeners/JSON clients keep working while new consumers can
# subscribe to RunStarted/NodeStarted/etc. and receive them polymorphically.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BuildStarted(RunStarted):
    protocol_type: ClassVar[str] = "run.started"


@dataclass(slots=True)
class BuildFinished(RunFinished):
    success: bool = True

    protocol_type: ClassVar[str] = "run.finished"

    def __post_init__(self) -> None:
        metadata_status = _metadata_text(self.metadata, "execution_status", "status")
        if metadata_status:
            self.status = canonical_status_value(
                metadata_status,
                default=ExecutionStatus.SUCCEEDED if self.success else ExecutionStatus.FAILED,
            )
        elif str(self.status or "").strip() and (str(self.status).strip().lower() != ExecutionStatus.SUCCEEDED.value or self.success):
            self.status = canonical_status_value(
                self.status,
                default=ExecutionStatus.SUCCEEDED if self.success else ExecutionStatus.FAILED,
            )
        else:
            self.status = (ExecutionStatus.SUCCEEDED if self.success else ExecutionStatus.FAILED).value
        if not self.success and int(self.exit_code or 0) == 0:
            self.exit_code = int(self.metadata.get("exit_code") or 1)
        RunFinished.__post_init__(self)


@dataclass(slots=True)
class TaskStarted(NodeStarted):
    task: str = ""

    protocol_type: ClassVar[str] = "node.started"

    def __post_init__(self) -> None:
        if not self.label:
            self.label = str(self.task or "")
        if not self.task:
            self.task = str(self.label or "")
        if not self.argv and isinstance(self.metadata, dict):
            self.argv = tuple(str(value) for value in (self.metadata.get("argv") or ()))
        NodeStarted.__post_init__(self)


@dataclass(slots=True)
class TaskFinished(NodeFinished):
    task: str = ""
    success: bool = True

    protocol_type: ClassVar[str] = "node.finished"

    def __post_init__(self) -> None:
        metadata_status = _metadata_text(
            self.metadata,
            "execution_status",
            "workflow_state",
            "workflow_node_state",
        )
        if metadata_status:
            self.status = canonical_status_value(
                metadata_status,
                default=ExecutionStatus.SUCCEEDED if self.success else ExecutionStatus.FAILED,
            )
        elif str(self.status or "").strip() and (str(self.status).strip().lower() != ExecutionStatus.SUCCEEDED.value or self.success):
            self.status = canonical_status_value(
                self.status,
                default=ExecutionStatus.SUCCEEDED if self.success else ExecutionStatus.FAILED,
            )
        else:
            self.status = (ExecutionStatus.SUCCEEDED if self.success else ExecutionStatus.FAILED).value
        if int(self.exit_code or 0) == 0 and isinstance(self.metadata, dict):
            self.exit_code = int(self.metadata.get("returncode") or 0)
        if not self.failure_kind and isinstance(self.metadata, dict):
            self.failure_kind = str(self.metadata.get("failure_kind") or "")
        if not self.duration_seconds and isinstance(self.metadata, dict):
            self.duration_seconds = float(self.metadata.get("duration_seconds") or 0.0)
        NodeFinished.__post_init__(self)


@dataclass(slots=True)
class ArtifactCreated(ArtifactProduced):
    protocol_type: ClassVar[str] = "artifact.produced"

    def __post_init__(self) -> None:
        if isinstance(self.metadata, dict):
            self.artifact_id = self.artifact_id or str(self.metadata.get("id") or "")
            self.sha256 = self.sha256 or str(self.metadata.get("sha256") or "")
            if self.size is None and self.metadata.get("size") not in (None, ""):
                self.size = int(self.metadata["size"])
            self.artifact_type = self.artifact_type or str(self.metadata.get("type") or "")
            self.producer_task = self.producer_task or str(self.metadata.get("producer_task") or "")
        ArtifactProduced.__post_init__(self)


@dataclass(slots=True)
class WarningDetected(DiagnosticEmitted):
    protocol_type: ClassVar[str] = "diagnostic.emitted"

    def __post_init__(self) -> None:
        self.severity = "warning"
        if isinstance(self.metadata, dict):
            self.code = self.code or str(self.metadata.get("code") or "")
            self.file = self.file or str(self.metadata.get("file") or "")
            if self.line is None and self.metadata.get("line") not in (None, ""):
                self.line = int(self.metadata["line"])
        DiagnosticEmitted.__post_init__(self)


@dataclass(slots=True)
class OverlayApplied(DevtoolEvent):
    artifact: str = ""
    commit: str = ""
    validation_status: str = ""

    protocol_type: ClassVar[str] = "overlay.applied"

    def __post_init__(self) -> None:
        if isinstance(self.metadata, dict):
            self.commit = self.commit or str(self.metadata.get("commit") or "")
            self.validation_status = self.validation_status or str(self.metadata.get("validation_status") or "")


@dataclass(slots=True)
class UnknownExecutionEvent(DevtoolEvent):
    raw_event_type: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)

    protocol_type: ClassVar[str] = "execution.unknown"


_EVENT_CLASSES = (
    RunStarted,
    RunFinished,
    GraphSealed,
    NodeDiscovered,
    NodeStarted,
    NodeProgressChanged,
    NodeFinished,
    NodeBlocked,
    ProcessStarted,
    ProcessOutputReceived,
    ProcessExited,
    DiagnosticEmitted,
    ArtifactProduced,
    MetricChanged,
    BuildStarted,
    BuildFinished,
    TaskStarted,
    TaskFinished,
    ArtifactCreated,
    WarningDetected,
    OverlayApplied,
)
_EVENT_BY_CLASS_NAME = {event_type.__name__: event_type for event_type in _EVENT_CLASSES}
# Canonical classes intentionally win stable protocol names over compatibility
# subclasses that share the same protocol_type.
_EVENT_BY_PROTOCOL_TYPE: dict[str, type[DevtoolEvent]] = {}
for _event_type in _EVENT_CLASSES:
    _EVENT_BY_PROTOCOL_TYPE.setdefault(_event_type.protocol_type, _event_type)

_TUPLE_FIELDS = {"argv", "depends_on", "blocked_by", "evidence"}


def event_from_dict(payload: dict[str, Any], *, allow_unknown: bool = True) -> DevtoolEvent:
    """Decode a JSON-safe event payload into a typed event.

    Both the compatibility class name in ``type`` and the stable machine
    ``event_type`` are accepted.  Unknown future events can be retained as an
    ``UnknownExecutionEvent`` rather than breaking older clients.
    """
    if not isinstance(payload, dict):
        raise TypeError("execution event payload must be an object")
    class_name = str(payload.get("type") or "").strip()
    protocol_name = str(payload.get("event_type") or "").strip()
    event_type = _EVENT_BY_CLASS_NAME.get(class_name) or _EVENT_BY_PROTOCOL_TYPE.get(protocol_name)
    if event_type is None:
        if not allow_unknown:
            raise ValueError(f"Unknown execution event type: {class_name or protocol_name or '<missing>'}")
        common_names = {item.name for item in fields(DevtoolEvent)}
        common = {name: payload[name] for name in common_names if name in payload}
        return UnknownExecutionEvent(
            **common,
            raw_event_type=protocol_name or class_name,
            raw_payload={str(key): value for key, value in payload.items()},
        )

    field_names = {item.name for item in fields(event_type)}
    values = {name: payload[name] for name in field_names if name in payload}
    for name in _TUPLE_FIELDS.intersection(values):
        value = values[name]
        if isinstance(value, list):
            values[name] = tuple(value)
    return event_type(**values)


__all__ = [
    "EXECUTION_EVENT_SCHEMA_VERSION",
    "DevtoolEvent",
    "RunStarted",
    "RunFinished",
    "GraphSealed",
    "NodeDiscovered",
    "NodeStarted",
    "NodeProgressChanged",
    "NodeFinished",
    "NodeBlocked",
    "ProcessStarted",
    "ProcessOutputReceived",
    "ProcessExited",
    "DiagnosticEmitted",
    "ArtifactProduced",
    "MetricChanged",
    "BuildStarted",
    "BuildFinished",
    "TaskStarted",
    "TaskFinished",
    "ArtifactCreated",
    "WarningDetected",
    "OverlayApplied",
    "UnknownExecutionEvent",
    "event_from_dict",
]
