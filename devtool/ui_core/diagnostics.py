from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .state import UIDiagnosticState

DIAGNOSTIC_SCHEMA_VERSION = 1


@dataclass(slots=True)
class UIDiagnosticSummary:
    total: int = 0
    errors: int = 0
    warnings: int = 0
    info: int = 0
    blocking: bool = False
    primary_id: str = ""
    schema_version: int = DIAGNOSTIC_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class UIDiagnosticPresentation:
    id: str
    severity: str
    token: str
    what_happened: str
    why: str = ""
    where: str = ""
    next_actions: list[str] = field(default_factory=list)
    technical_details: str = ""
    code: str = ""
    phase: str = ""
    source: str = "runtime"
    related_node: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    schema_version: int = DIAGNOSTIC_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize_diagnostics(items: Iterable[UIDiagnosticState]) -> UIDiagnosticSummary:
    rows = list(items)
    result = UIDiagnosticSummary(total=len(rows))
    for item in rows:
        if item.severity == "error":
            result.errors += 1
        elif item.severity == "warning":
            result.warnings += 1
        else:
            result.info += 1
    result.blocking = result.errors > 0
    if rows:
        primary = next((item for item in rows if item.severity == "error"), rows[0])
        result.primary_id = primary.id
    return result


def diagnostic_presentations(items: Iterable[UIDiagnosticState]) -> list[UIDiagnosticPresentation]:
    result: list[UIDiagnosticPresentation] = []
    for item in items:
        location = item.location
        if not location and item.file:
            location = item.file + (f":{item.line}" if item.line is not None else "")
        result.append(UIDiagnosticPresentation(
            id=item.id,
            severity=item.severity,
            token=item.token,
            what_happened=item.summary or item.message,
            why=item.cause,
            where=location,
            next_actions=list(item.next_actions),
            technical_details=item.technical_details or item.message,
            code=item.code,
            phase=item.phase,
            source=item.source,
            related_node=item.related_node,
            evidence=dict(item.evidence),
        ))
    return result
