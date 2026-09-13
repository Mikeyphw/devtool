from __future__ import annotations

"""Stable machine control surface for durable validation sessions."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .validation_sessions import (
    ValidationSession,
    ValidationSessionError,
    read_control_request,
    read_events,
    required_checkpoint_satisfied,
    status_payload,
)

API_VERSION = 1
EXIT_OK = 0
EXIT_INVALID_REQUEST = 2
EXIT_STALE_REVISION = 3
EXIT_SESSION_BLOCKED = 4
EXIT_RECOVERY_REQUIRED = 5
EXIT_CONTROL_UNAVAILABLE = 6
SUPPORTED_ACTIONS = (
    "start", "run", "resume", "retry", "status", "plan", "events",
    "pause", "stop", "reset", "finalize", "abort", "recover", "report",
    "accept-plan-change",
)
SUPPORTED_SCOPES = ("next", "through", "selected", "remaining")


@dataclass(slots=True)
class ValidationControlError(ValidationSessionError):
    reason: str
    message: str
    exit_code: int = EXIT_INVALID_REQUEST
    data: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


def _safe_revision(value: Any, *, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def require_revision(session: ValidationSession, expected: int | None) -> int:
    current = _safe_revision(session.revision, default=0) or 0
    expected_value = _safe_revision(expected, default=None)
    if expected is None or expected_value is None or expected_value < 0:
        return current
    if expected_value != current:
        raise ValidationControlError(
            "stale_revision",
            f"Validation session revision is stale: expected {expected}, current {session.revision}.",
            EXIT_STALE_REVISION,
            {"expected_revision": expected_value, "current_revision": current},
        )
    return current


def response(
    action: str,
    *,
    accepted: bool,
    session: ValidationSession | None = None,
    message: str = "",
    reason: str = "",
    previous_revision: int | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "api_version": API_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "accepted": bool(accepted),
        "action": str(action or ""),
        "reason": str(reason or ""),
        "message": str(message or ""),
        "session_id": session.session_id if session is not None else "",
        "previous_revision": previous_revision,
        "revision": _safe_revision(session.revision, default=0) if session is not None else None,
        "state": session.status if session is not None else "",
        "effective_state": session.status if session is not None else "",
        "data": dict(data or {}),
    }
    if session is not None:
        control = read_control_request(session)
        action_name = str(control.get("action") or "")
        if session.status == "running" and action_name == "pause-after-phase":
            payload["effective_state"] = "pausing"
        elif session.status == "running" and action_name == "stop-now":
            payload["effective_state"] = "stopping"
    return payload


def error_response(action: str, error: ValidationControlError, session: ValidationSession | None = None) -> dict[str, Any]:
    return response(
        action,
        accepted=False,
        session=session,
        reason=error.reason,
        message=error.message,
        data=dict(error.data or {}),
    )


def plan_payload(session: ValidationSession) -> dict[str, Any]:
    phases = []
    for phase in session.phases:
        phases.append({
            "key": phase.key,
            "name": phase.name,
            "label": phase.label,
            "index": phase.index,
            "total": phase.total,
            "status": phase.status,
            "depends_on": list(phase.depends_on),
            "input_domains": list(phase.input_domains),
            "required": bool(phase.required),
            "scope": phase.scope,
            "supports_progress": bool(phase.supports_progress),
            "attempts": int(phase.attempts),
            "last_verdict": phase.last_verdict,
        })
    return {
        "session_id": session.session_id,
        "revision": _safe_revision(session.revision, default=0) or 0,
        "state": session.status,
        "required_checkpoint_name": session.required_checkpoint_name,
        "required_checkpoint_keys": list(session.required_checkpoint_keys),
        "required_checkpoint_satisfied": required_checkpoint_satisfied(session),
        "supported_scopes": list(SUPPORTED_SCOPES),
        "phases": phases,
    }


def status_api_payload(session: ValidationSession, *, include_events: bool = False, after_event_id: str = "", event_limit: int = 100) -> dict[str, Any]:
    payload = status_payload(session)
    payload["control_api"] = {
        "api_version": API_VERSION,
        "supported_actions": list(SUPPORTED_ACTIONS),
        "supported_scopes": list(SUPPORTED_SCOPES),
        "exit_codes": {
            "ok": EXIT_OK,
            "invalid_request": EXIT_INVALID_REQUEST,
            "stale_revision": EXIT_STALE_REVISION,
            "session_blocked": EXIT_SESSION_BLOCKED,
            "recovery_required": EXIT_RECOVERY_REQUIRED,
            "control_unavailable": EXIT_CONTROL_UNAVAILABLE,
        },
    }
    if include_events:
        events = read_events(session, after_event_id=after_event_id, limit=event_limit)
        payload["events"] = events
        payload["event_cursor"] = {
            "after_event_id": after_event_id,
            "returned": len(events),
            "next_event_id": str(events[-1].get("event_id") or "") if events else after_event_id,
        }
    return payload


def exit_code_for_session(session: ValidationSession) -> int:
    if session.status == "blocked":
        return EXIT_SESSION_BLOCKED
    if session.status in {"recovery-required", "rollback-failed"}:
        return EXIT_RECOVERY_REQUIRED
    return EXIT_OK


def reason_for_session(session: ValidationSession) -> str:
    if session.status == "blocked":
        return "session_blocked"
    if session.status in {"recovery-required", "rollback-failed"}:
        return "recovery_required"
    return ""
