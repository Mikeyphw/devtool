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
    def ok(cls, **kwargs: Any) -> "CommandResult":
        return cls(success=True, **kwargs)

    @classmethod
    def fail(cls, message: str, **kwargs: Any) -> "CommandResult":
        return cls(success=False, errors=[message], exit_code=1, **kwargs)
