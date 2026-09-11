from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

TOKEN_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class SemanticToken:
    id: str
    category: str
    meaning: str


TOKEN_REGISTRY: dict[str, SemanticToken] = {
    token.id: token
    for token in (
        SemanticToken("status.success", "status", "Successful or completed operation"),
        SemanticToken("status.running", "status", "Active operation"),
        SemanticToken("status.pending", "status", "Pending work"),
        SemanticToken("status.failed", "status", "Failed operation"),
        SemanticToken("status.warning", "status", "Completed with warnings"),
        SemanticToken("status.skipped", "status", "Skipped work"),
        SemanticToken("status.cancelled", "status", "Cancelled or interrupted work"),
        SemanticToken("status.blocked", "status", "Blocked by dependency or policy"),
        SemanticToken("severity.info", "severity", "Informational diagnostic"),
        SemanticToken("severity.warning", "severity", "Warning diagnostic"),
        SemanticToken("severity.error", "severity", "Error diagnostic"),
        SemanticToken("action.primary", "action", "Primary recommended action"),
        SemanticToken("action.secondary", "action", "Secondary action"),
        SemanticToken("action.destructive", "action", "Potentially destructive action"),
        SemanticToken("text.primary", "text", "Primary readable content"),
        SemanticToken("text.secondary", "text", "Secondary explanatory content"),
        SemanticToken("text.muted", "text", "De-emphasized metadata"),
        SemanticToken("surface.default", "surface", "Default application surface"),
        SemanticToken("surface.raised", "surface", "Raised panel/card surface"),
    )
}

_STATUS_ALIASES = {
    "ok": "status.success", "success": "status.success", "passed": "status.success", "complete": "status.success", "completed": "status.success",
    "running": "status.running", "waiting": "status.running", "preparing": "status.running", "rolling-back": "status.running", "rolling_back": "status.running",
    "pending": "status.pending", "ready": "status.pending", "idle": "status.pending",
    "failed": "status.failed", "failure": "status.failed", "error": "status.failed",
    "warning": "status.warning", "warnings": "status.warning",
    "skipped": "status.skipped", "cached": "status.skipped",
    "cancelled": "status.cancelled", "canceled": "status.cancelled", "interrupted": "status.cancelled",
    "blocked": "status.blocked",
}


def token_for_status(status: str) -> str:
    return _STATUS_ALIASES.get(str(status or "").strip().lower(), "status.pending")


def token_for_severity(severity: str) -> str:
    normalized = str(severity or "info").strip().lower()
    if normalized in {"fatal", "failure", "error"}:
        return "severity.error"
    if normalized in {"warn", "warning"}:
        return "severity.warning"
    return "severity.info"


def token_catalog() -> list[dict[str, Any]]:
    return [asdict(item) for item in TOKEN_REGISTRY.values()]
