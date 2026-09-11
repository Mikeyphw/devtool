from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .copy import copy_text
from .confirmations import confirmation_decision
from .state import UIActionState


@dataclass(frozen=True, slots=True)
class ActionSpec:
    id: str
    label: str
    short_label: str
    notification_label: str
    icon: str
    category: str
    intent: str
    destructive: bool = False
    shortcut: str = ""
    description: str = ""

    def present(
        self,
        *,
        enabled: bool = True,
        disabled_reason: str = "",
        payload: Mapping[str, Any] | None = None,
        confirmation_policy: Mapping[str, Any] | None = None,
        confirmation_context: Mapping[str, Any] | None = None,
    ) -> UIActionState:
        decision = confirmation_decision(self.id, confirmation_policy, context={"destructive": self.destructive, **dict(confirmation_context or {})})
        return UIActionState(
            id=self.id,
            label=self.label,
            short_label=self.short_label,
            notification_label=self.notification_label,
            icon=self.icon,
            category=self.category,
            intent=self.intent,
            enabled=enabled,
            destructive=self.destructive,
            confirmation_mode=decision.mode,
            requires_confirmation=decision.required,
            confirmation_reason=decision.reason,
            confirmation_prompt=decision.prompt,
            confirmation_default=decision.default,
            disabled_reason=disabled_reason,
            shortcut=self.shortcut,
            payload=dict(payload or {}),
            token="action.destructive" if self.destructive else "action.secondary",
        )


@dataclass(frozen=True, slots=True)
class ActionContext:
    status: str = "idle"
    command: str = ""
    run_id: str = ""
    log_path: str = ""
    target: str = ""
    has_diagnostics: bool = False
    has_artifact: bool = False
    has_test_selection: bool = False
    retryable: bool = True
    resumable: bool = False
    cancellable: bool = True
    payload: Mapping[str, Any] = field(default_factory=dict)

    @property
    def running(self) -> bool:
        return self.status in {"running", "waiting", "preparing", "rolling-back", "rolling_back"}

    @property
    def terminal(self) -> bool:
        return self.status in {"ok", "failed", "cancelled", "interrupted", "skipped", "rolled-back", "rolled_back"}

    @property
    def failed(self) -> bool:
        return self.status in {"failed", "cancelled", "interrupted"}

    @property
    def validation(self) -> bool:
        return self.command in {"validate", "validation", "test"} or "validation" in self.command


def _action(
    action_id: str,
    icon: str,
    category: str,
    intent: str,
    destructive: bool = False,
    shortcut: str = "",
) -> ActionSpec:
    return ActionSpec(
        action_id,
        copy_text(action_id),
        copy_text(action_id, "short"),
        copy_text(action_id, "notification"),
        icon,
        category,
        intent,
        destructive,
        shortcut,
        copy_text(action_id, "description"),
    )


ACTION_REGISTRY: dict[str, ActionSpec] = {
    spec.id: spec
    for spec in (
        _action("operation.cancel", "action.stop", "operation", "cancel", True, "Ctrl+C"),
        _action("operation.retry", "action.retry", "operation", "retry"),
        _action("operation.rerun", "action.rerun", "operation", "rerun"),
        _action("logs.open", "action.logs", "logs", "open-log", False, "L"),
        _action("summary.copy", "action.copy", "sharing", "copy-summary"),
        _action("diagnostics.copy", "action.copy-diagnostics", "diagnostics", "copy-diagnostics"),
        _action("artifact.apply", "action.apply", "artifact", "apply-artifact"),
        _action("artifact.rollback", "action.rollback", "artifact", "rollback", True),
        _action("validation.retry_failed", "action.retry-failed", "validation", "validation-retry-failed"),
        _action("validation.resume", "action.resume", "validation", "validation-resume"),
        _action("target.build", "action.build", "target", "target-build"),
        _action("target.test", "action.test", "target", "target-test"),
        _action("target.validate", "action.validate", "target", "target-validate"),
        _action("target.package", "action.package", "target", "target-package"),
        _action("workflow.inspect", "action.inspect", "workflow", "workflow-inspect"),
        _action("workflow.retry_node", "action.retry-node", "workflow", "workflow-retry-node"),
        _action("tests.select", "action.tests", "tests", "tests-select"),
        _action("tests.clear_selection", "action.clear", "tests", "tests-clear-selection"),
        _action("tests.refresh_collection", "action.refresh", "tests", "tests-refresh-collection"),
    )
}


def action_spec(action_id: str) -> ActionSpec:
    try:
        return ACTION_REGISTRY[action_id]
    except KeyError as exc:
        raise KeyError(f"Unknown UI action: {action_id}") from exc


def _payload(context: ActionContext) -> dict[str, Any]:
    value = dict(context.payload)
    if context.run_id:
        value.setdefault("run_id", context.run_id)
    if context.target:
        value.setdefault("target", context.target)
    if context.log_path:
        value.setdefault("log_path", context.log_path)
    return value


def available_action_ids(context: ActionContext) -> list[str]:
    result: list[str] = []
    if context.running and context.cancellable:
        result.append("operation.cancel")
    if context.failed and context.retryable:
        result.append("operation.retry")
    elif context.terminal:
        result.append("operation.rerun")
    if context.validation and context.failed:
        result.append("validation.retry_failed")
    if context.validation and context.resumable:
        result.append("validation.resume")
    if context.log_path:
        result.append("logs.open")
    if context.run_id or context.command:
        result.append("summary.copy")
    if context.has_diagnostics:
        result.append("diagnostics.copy")
    if context.target:
        result.append("workflow.inspect")
    return result


def actions_for_context(context: ActionContext, *, confirmation_policy: Mapping[str, Any] | None = None) -> list[UIActionState]:
    payload = _payload(context)
    confirmation_context = {
        "destructive_phase_started": context.running and context.command in {"apply", "apply-overlay", "apply-snapshot", "package"},
        "has_uncommitted_mutation": bool(context.payload.get("has_uncommitted_mutation")),
    }
    return [
        action_spec(action_id).present(
            payload=payload,
            confirmation_policy=confirmation_policy,
            confirmation_context=confirmation_context,
        )
        for action_id in available_action_ids(context)
    ]


def action_text(action_id: str, variant: str = "label") -> str:
    return copy_text(action_id, variant)


def action_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "label": item.label,
            "short_label": item.short_label,
            "notification_label": item.notification_label,
            "icon": item.icon,
            "category": item.category,
            "intent": item.intent,
            "destructive": item.destructive,
            "shortcut": item.shortcut,
            "description": item.description,
        }
        for item in ACTION_REGISTRY.values()
    ]
