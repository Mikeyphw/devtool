from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .results import CommandResult


def result_from_exception(exc: Exception, *, metadata: dict[str, Any] | None = None) -> CommandResult:
    return CommandResult(
        success=False,
        exit_code=1,
        stdout="",
        stderr=str(exc),
        duration=0.0,
        warnings=[],
        errors=[str(exc)],
        metadata=metadata or {},
    )


def result_to_dict(result: CommandResult) -> dict[str, Any]:
    return asdict(result)
