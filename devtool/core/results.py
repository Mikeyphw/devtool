from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from .execution_contract import ExecutionResult, ExecutionStatus


@dataclass(slots=True)
class CommandResult:
    """Structured command result retained as a compatibility facade.

    EXO-01 establishes :class:`ExecutionResult` as the canonical execution
    result vocabulary while preserving this public shape for existing callers.
    """

    success: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_execution_result(self, *, name: str = "") -> ExecutionResult:
        return ExecutionResult.from_command(self, name=name)

    @classmethod
    def from_execution_result(cls, result: ExecutionResult) -> "CommandResult":
        metadata = dict(result.metadata)
        metadata.setdefault("name", result.name)
        metadata.setdefault("argv", list(result.argv))
        metadata.setdefault("skipped", result.skipped)
        metadata.setdefault("execution_status", result.status.value)
        metadata.setdefault("run_id", result.run_id)
        metadata.setdefault("node_id", result.node_id)
        metadata.setdefault("parent_id", result.parent_id)
        metadata.setdefault("log_path", result.log_path)
        metadata.setdefault("failure_kind", result.failure_kind)
        return cls(
            success=result.success,
            exit_code=int(result.exit_code),
            stdout=result.stdout,
            stderr=result.stderr,
            duration=float(result.duration_seconds),
            warnings=list(result.warnings),
            errors=list(result.errors),
            metadata=metadata,
        )

    @classmethod
    def from_operation(cls, operation: Any) -> "CommandResult":
        return cls.from_execution_result(ExecutionResult.from_operation(operation))

    @classmethod
    def from_completed_process(cls, process: Any, *, duration: float = 0.0, metadata: dict[str, Any] | None = None) -> "CommandResult":
        # Preserve the historical facade exactly; callers can opt into the
        # canonical contract through ``to_execution_result``.
        returncode = int(getattr(process, "returncode", 1) or 0)
        return cls(
            success=returncode == 0,
            exit_code=returncode,
            stdout=str(getattr(process, "stdout", "") or ""),
            stderr=str(getattr(process, "stderr", "") or ""),
            duration=float(duration),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def ok(cls, **kwargs: Any) -> "CommandResult":
        return cls(success=True, **kwargs)

    @classmethod
    def fail(cls, message: str, **kwargs: Any) -> "CommandResult":
        return cls(success=False, errors=[message], exit_code=1, **kwargs)
