from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(slots=True)
class CommandResult:
    """Structured result shared by devtool subsystems."""

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


    @classmethod
    def from_operation(cls, operation: Any) -> "CommandResult":
        issues = list(getattr(operation, "issues", []) or [])
        warnings = [str(getattr(item, "message", item)) for item in issues if str(getattr(item, "severity", "")).lower() == "warning"]
        errors = [
            str(getattr(item, "message", item))
            for item in issues
            if str(getattr(item, "severity", "")).lower() in {"error", "fatal"}
        ]
        returncode = int(getattr(operation, "returncode", 1) or 0)
        if errors and returncode == 0:
            returncode = 1
        metadata = dict(getattr(operation, "metadata", {}) or {})
        metadata.setdefault("name", str(getattr(operation, "name", "") or ""))
        metadata.setdefault("argv", list(getattr(operation, "argv", []) or []))
        metadata.setdefault("skipped", bool(getattr(operation, "skipped", False)))
        metadata.setdefault("log_path", str(getattr(operation, "log_path", "") or ""))
        metadata.setdefault("failure_kind", str(getattr(operation, "failure_kind", "") or ""))
        return cls(
            success=returncode == 0,
            exit_code=returncode,
            stdout=str(getattr(operation, "output", "") or ""),
            stderr="",
            duration=float(getattr(operation, "duration_seconds", 0.0) or 0.0),
            warnings=warnings,
            errors=errors,
            metadata=metadata,
        )

    @classmethod
    def from_completed_process(cls, process: Any, *, duration: float = 0.0, metadata: dict[str, Any] | None = None) -> "CommandResult":
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
