from __future__ import annotations

"""Canonical execution identity, status, and result primitives.

EXO-01 intentionally stops short of owning live execution state.  It defines
stable vocabulary and identity helpers that the later execution store/event
roadmap can adopt without another compatibility migration.
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
import hashlib
import os
import re
import uuid
from typing import Any, Iterable

_ID_SLUG_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


class _ExecutionId(str):
    """Non-empty string identity that remains JSON/legacy-string compatible."""

    __slots__ = ()

    def __new__(cls, value: object) -> "_ExecutionId":
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{cls.__name__} cannot be empty")
        return str.__new__(cls, text)


class RunId(_ExecutionId):
    @classmethod
    def new(cls, *, prefix: str = "") -> "RunId":
        token = uuid.uuid4().hex
        clean_prefix = _slug(prefix)
        return cls(f"{clean_prefix}-{token}" if clean_prefix else token)

    @classmethod
    def current(cls, *, create: bool = True) -> "RunId | None":
        value = os.environ.get("DEVTOOL_RUN_ID", "").strip()
        if value:
            return cls(value)
        return cls.new() if create else None


class NodeId(_ExecutionId):
    @classmethod
    def workflow(cls, value: object) -> "NodeId":
        """Preserve an existing workflow DAG key exactly as its node identity."""
        return cls(value)

    @classmethod
    def dynamic(
        cls,
        *,
        parent_id: object,
        kind: object,
        key: object,
        instance: object = "",
    ) -> "NodeId":
        """Build a deterministic child id scoped beneath a parent operation.

        The human-readable slug is not the identity.  A digest over the exact
        components prevents collisions after slug normalization. ``instance``
        distinguishes repeated operations that intentionally share a label/key.
        """
        parent = str(parent_id or "root").strip() or "root"
        node_kind = str(kind or "operation").strip().lower() or "operation"
        local_key = str(key or node_kind).strip() or node_kind
        occurrence = str(instance or "").strip()
        source = "\x1f".join((parent, node_kind, local_key, occurrence))
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
        label = _slug(local_key)[:48] or _slug(node_kind) or "operation"
        return cls(f"{parent}/{_slug(node_kind) or 'operation'}:{label}@{digest}")


class EventId(_ExecutionId):
    @classmethod
    def for_sequence(cls, run_id: object, sequence: int) -> "EventId":
        run = RunId(run_id)
        seq = int(sequence)
        if seq < 0:
            raise ValueError("event sequence cannot be negative")
        return cls(f"{run}:event:{seq}")


class ProcessId(_ExecutionId):
    @classmethod
    def for_pid(cls, run_id: object, pid: int) -> "ProcessId":
        run = RunId(run_id)
        process_id = int(pid)
        if process_id <= 0:
            raise ValueError("pid must be positive")
        return cls(f"{run}:pid:{process_id}")


class ExecutionStatus(str, Enum):
    PLANNED = "planned"
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.SKIPPED,
            ExecutionStatus.CANCELLED,
        }


_STATUS_ALIASES: dict[str, ExecutionStatus] = {
    "planned": ExecutionStatus.PLANNED,
    "pending": ExecutionStatus.PENDING,
    "queued": ExecutionStatus.PENDING,
    "waiting": ExecutionStatus.PENDING,
    "ready": ExecutionStatus.READY,
    "running": ExecutionStatus.RUNNING,
    "active": ExecutionStatus.RUNNING,
    "in-progress": ExecutionStatus.RUNNING,
    "in_progress": ExecutionStatus.RUNNING,
    "ok": ExecutionStatus.SUCCEEDED,
    "success": ExecutionStatus.SUCCEEDED,
    "succeeded": ExecutionStatus.SUCCEEDED,
    "pass": ExecutionStatus.SUCCEEDED,
    "passed": ExecutionStatus.SUCCEEDED,
    "complete": ExecutionStatus.SUCCEEDED,
    "completed": ExecutionStatus.SUCCEEDED,
    "validated": ExecutionStatus.SUCCEEDED,
    "failed": ExecutionStatus.FAILED,
    "failure": ExecutionStatus.FAILED,
    "error": ExecutionStatus.FAILED,
    "blocked": ExecutionStatus.BLOCKED,
    "skipped": ExecutionStatus.SKIPPED,
    "skip": ExecutionStatus.SKIPPED,
    "not-applicable": ExecutionStatus.SKIPPED,
    "not_applicable": ExecutionStatus.SKIPPED,
    "cancelled": ExecutionStatus.CANCELLED,
    "canceled": ExecutionStatus.CANCELLED,
    "interrupted": ExecutionStatus.CANCELLED,
    "aborted": ExecutionStatus.CANCELLED,
}


def canonical_execution_status(
    value: object,
    *,
    default: ExecutionStatus | None = None,
) -> ExecutionStatus:
    """Normalize legacy surface statuses into the execution vocabulary.

    Unknown values fail closed unless a caller deliberately supplies a default;
    this prevents new state spellings from being silently counted as success.
    """
    if isinstance(value, ExecutionStatus):
        return value
    key = str(value or "").strip().lower()
    status = _STATUS_ALIASES.get(key)
    if status is not None:
        return status
    if default is not None:
        return default
    raise ValueError(f"Unknown execution status: {value!r}")


def canonical_status_value(value: object, *, default: ExecutionStatus | None = None) -> str:
    return canonical_execution_status(value, default=default).value


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    """Shared identity carried by operation/event/process contracts."""

    run_id: RunId
    node_id: NodeId | None = None
    parent_id: NodeId | None = None

    @classmethod
    def from_values(
        cls,
        *,
        run_id: object,
        node_id: object = "",
        parent_id: object = "",
    ) -> "ExecutionIdentity":
        return cls(
            run_id=RunId(run_id),
            node_id=NodeId(node_id) if str(node_id or "").strip() else None,
            parent_id=NodeId(parent_id) if str(parent_id or "").strip() else None,
        )


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Canonical result contract shared by legacy Operation/Command results."""

    name: str
    status: ExecutionStatus
    exit_code: int = 0
    duration_seconds: float = 0.0
    argv: tuple[str, ...] = ()
    stdout: str = ""
    stderr: str = ""
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    run_id: str = ""
    node_id: str = ""
    parent_id: str = ""
    log_path: str = ""
    failure_kind: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status in {ExecutionStatus.SUCCEEDED, ExecutionStatus.SKIPPED}

    @property
    def skipped(self) -> bool:
        return self.status is ExecutionStatus.SKIPPED

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["argv"] = list(self.argv)
        payload["warnings"] = list(self.warnings)
        payload["errors"] = list(self.errors)
        payload["success"] = self.success
        return payload

    @classmethod
    def from_operation(cls, operation: Any) -> "ExecutionResult":
        issues = list(getattr(operation, "issues", []) or [])
        warnings = tuple(
            str(getattr(item, "message", item))
            for item in issues
            if str(getattr(item, "severity", "")).strip().lower() == "warning"
        )
        errors = tuple(
            str(getattr(item, "message", item))
            for item in issues
            if str(getattr(item, "severity", "")).strip().lower() in {"error", "fatal"}
        )
        exit_code = int(getattr(operation, "returncode", 1) or 0)
        skipped = bool(getattr(operation, "skipped", False))
        metadata = dict(getattr(operation, "metadata", {}) or {})
        declared_status = str(metadata.get("execution_status") or metadata.get("workflow_node_state") or "").strip()
        if declared_status:
            try:
                status = canonical_execution_status(declared_status)
            except ValueError:
                status = ExecutionStatus.SKIPPED if skipped else (ExecutionStatus.FAILED if exit_code != 0 or errors else ExecutionStatus.SUCCEEDED)
        elif str(getattr(operation, "failure_kind", "") or "").strip() == "dependency_blocked":
            status = ExecutionStatus.BLOCKED
        elif exit_code == 130:
            status = ExecutionStatus.CANCELLED
        elif skipped:
            status = ExecutionStatus.SKIPPED
        elif exit_code != 0 or errors:
            status = ExecutionStatus.FAILED
        else:
            status = ExecutionStatus.SUCCEEDED
        return cls(
            name=str(getattr(operation, "name", "") or ""),
            status=status,
            exit_code=exit_code if not (errors and exit_code == 0) else 1,
            duration_seconds=float(getattr(operation, "duration_seconds", 0.0) or 0.0),
            argv=tuple(str(part) for part in (getattr(operation, "argv", []) or [])),
            stdout=str(getattr(operation, "output", "") or ""),
            stderr="",
            warnings=warnings,
            errors=errors,
            run_id=_metadata_identity(metadata, "run_id", env_fallback="DEVTOOL_RUN_ID"),
            node_id=_metadata_identity(metadata, "node_id", fallback_keys=("workflow_node_key",)),
            parent_id=_metadata_identity(metadata, "parent_id", fallback_keys=("workflow_parent_id",)),
            log_path=str(getattr(operation, "log_path", "") or ""),
            failure_kind=str(getattr(operation, "failure_kind", "") or ""),
            metadata=metadata,
        )

    @classmethod
    def from_command(cls, command: Any, *, name: str = "") -> "ExecutionResult":
        metadata = dict(getattr(command, "metadata", {}) or {})
        success = bool(getattr(command, "success", False))
        status_value = metadata.get("execution_status")
        if status_value:
            status = canonical_execution_status(status_value)
        elif bool(metadata.get("skipped", False)):
            status = ExecutionStatus.SKIPPED
        else:
            status = ExecutionStatus.SUCCEEDED if success else ExecutionStatus.FAILED
        return cls(
            name=str(name or metadata.get("name") or ""),
            status=status,
            exit_code=int(getattr(command, "exit_code", 0) or 0),
            duration_seconds=float(getattr(command, "duration", 0.0) or 0.0),
            argv=tuple(str(part) for part in metadata.get("argv", []) or []),
            stdout=str(getattr(command, "stdout", "") or ""),
            stderr=str(getattr(command, "stderr", "") or ""),
            warnings=tuple(str(item) for item in (getattr(command, "warnings", []) or [])),
            errors=tuple(str(item) for item in (getattr(command, "errors", []) or [])),
            run_id=_metadata_identity(metadata, "run_id", env_fallback="DEVTOOL_RUN_ID"),
            node_id=_metadata_identity(metadata, "node_id", fallback_keys=("workflow_node_key",)),
            parent_id=_metadata_identity(metadata, "parent_id", fallback_keys=("workflow_parent_id",)),
            log_path=str(metadata.get("log_path") or ""),
            failure_kind=str(metadata.get("failure_kind") or ""),
            metadata=metadata,
        )


def legacy_phase_node_id(
    *,
    name: object,
    target: object = "",
    workspace: object = "",
    workflow: object = "",
    parent_id: object = "",
    instance: object = "",
) -> NodeId:
    """Deterministic compatibility identity for a phase lacking a DAG key.

    New structured callers should always supply ``node_id``/``workflow_node_key``.
    The instance discriminator keeps repeated same-label phases distinct inside
    a known plan instead of collapsing them by presentation text.
    """
    key = "|".join(
        (
            str(workspace or "").strip(),
            str(target or "").strip(),
            str(workflow or "").strip(),
            str(name or "operation").strip() or "operation",
        )
    )
    parent = str(parent_id or "run").strip() or "run"
    return NodeId.dynamic(parent_id=parent, kind="phase", key=key, instance=instance)


def _metadata_identity(
    metadata: dict[str, Any],
    primary: str,
    *,
    fallback_keys: Iterable[str] = (),
    env_fallback: str = "",
) -> str:
    for key in (primary, *fallback_keys):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    if env_fallback:
        return os.environ.get(env_fallback, "").strip()
    return ""


def _slug(value: object) -> str:
    text = _ID_SLUG_RE.sub("-", str(value or "").strip()).strip("-_.").lower()
    return text or ""
