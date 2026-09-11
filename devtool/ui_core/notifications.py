from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .tokens import token_for_severity, token_for_status

NOTIFICATION_SCHEMA_VERSION = 1
ATTENTION_LEVELS = ("passive", "info", "important", "critical", "success")

DEFAULT_NOTIFICATION_POLICY: dict[str, bool] = {
    "started": False,
    "phase_changes": False,
    "success": True,
    "warnings": True,
    "failures": True,
    "requires_action": True,
    "bell_on_failure": True,
    "bell_on_requires_action": False,
}

@dataclass(frozen=True, slots=True)
class UINotification:
    id: str
    event: str
    level: str
    title: str
    body: str
    token: str
    should_emit: bool
    bell: bool = False
    actions: tuple[str, ...] = field(default_factory=tuple)
    schema_version: int = NOTIFICATION_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["actions"] = list(self.actions)
        return value


def normalize_notification_policy(value: Mapping[str, Any] | None) -> dict[str, bool]:
    result = dict(DEFAULT_NOTIFICATION_POLICY)
    for key, raw in (value or {}).items():
        name = str(key or "").strip().lower().replace("-", "_")
        if name not in DEFAULT_NOTIFICATION_POLICY:
            raise ValueError(f"Unknown notification policy: {key}")
        if isinstance(raw, bool):
            result[name] = raw
            continue
        text = str(raw or "").strip().lower()
        if text in {"1", "true", "yes", "on"}:
            result[name] = True
        elif text in {"0", "false", "no", "off"}:
            result[name] = False
        else:
            raise ValueError(f"Invalid notification boolean for {key}: {raw!r}")
    return result


def _classification(event: str, status: str, severity: str, requires_action: bool) -> tuple[str, str]:
    e = event.lower().replace("-", "_")
    s = status.lower().replace("-", "_")
    sev = severity.lower()
    if sev == "error" or s in {"failed", "cancelled", "interrupted", "rollback_failed"} or e in {"failed", "rollback_failed"}:
        return "critical", "failures"
    if requires_action:
        return "important", "requires_action"
    if sev == "warning" or s == "warning" or e in {"warning", "warnings"}:
        return "important", "warnings"
    if s in {"ok", "success", "succeeded", "complete", "completed"} or e in {"success", "completed", "finished"}:
        return "success", "success"
    if e in {"started", "operation_started"}:
        return "info", "started"
    if e in {"phase_changed", "phase_started", "progress"}:
        return "passive", "phase_changes"
    return "info", "started"


def notification_for_event(
    event: str,
    *,
    status: str = "",
    severity: str = "",
    title: str = "Devtool",
    body: str = "",
    requires_action: bool = False,
    actions: tuple[str, ...] | list[str] = (),
    policy: Mapping[str, Any] | None = None,
    notification_id: str = "",
) -> UINotification:
    normalized = normalize_notification_policy(policy)
    level, policy_key = _classification(event, status, severity, requires_action)
    should_emit = bool(normalized.get(policy_key, False))
    bell = bool(level == "critical" and normalized["bell_on_failure"] or requires_action and normalized["bell_on_requires_action"])
    token = token_for_severity("error" if level == "critical" else "warning" if level == "important" else "info")
    if level == "success":
        token = token_for_status("ok")
    return UINotification(
        id=notification_id or f"notification:{event}", event=event, level=level,
        title=title, body=body, token=token, should_emit=should_emit, bell=bell,
        actions=tuple(str(item) for item in actions if str(item).strip()),
    )


def notification_catalog(policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": NOTIFICATION_SCHEMA_VERSION,
        "attention_levels": list(ATTENTION_LEVELS),
        "policy": normalize_notification_policy(policy),
        "events": {
            "started": {"level": "info", "policy": "started"},
            "phase_changed": {"level": "passive", "policy": "phase_changes"},
            "warning": {"level": "important", "policy": "warnings"},
            "requires_action": {"level": "important", "policy": "requires_action"},
            "failed": {"level": "critical", "policy": "failures"},
            "rollback_failed": {"level": "critical", "policy": "failures"},
            "success": {"level": "success", "policy": "success"},
        },
    }
