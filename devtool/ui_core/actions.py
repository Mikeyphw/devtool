from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

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
    ) -> UIActionState:
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
            disabled_reason=disabled_reason,
            shortcut=self.shortcut,
            payload=dict(payload or {}),
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


ACTION_REGISTRY: dict[str, ActionSpec] = {
    spec.id: spec
    for spec in (
        ActionSpec("operation.cancel", "Cancel operation", "Cancel", "Stop", "action.stop", "operation", "cancel", True, "Ctrl+C", "Request cancellation of the active run."),
        ActionSpec("operation.retry", "Retry operation", "Retry", "Retry", "action.retry", "operation", "retry", False, "", "Retry the failed or cancelled operation."),
        ActionSpec("operation.rerun", "Run again", "Run again", "Run again", "action.rerun", "operation", "rerun", False, "", "Repeat the completed operation with the same selection."),
        ActionSpec("logs.open", "Open log", "Open log", "Open log", "action.logs", "logs", "open-log", False, "L", "Open the run log."),
        ActionSpec("summary.copy", "Copy summary", "Copy", "Copy summary", "action.copy", "sharing", "copy-summary", False, "", "Copy the canonical run summary."),
        ActionSpec("diagnostics.copy", "Copy diagnostics", "Copy issues", "Copy issues", "action.copy-diagnostics", "diagnostics", "copy-diagnostics", False, "", "Copy canonical diagnostics for the run."),
        ActionSpec("artifact.apply", "Apply artifact", "Apply", "Apply", "action.apply", "artifact", "apply-artifact", False, "", "Apply the selected overlay or snapshot."),
        ActionSpec("artifact.rollback", "Rollback changes", "Rollback", "Rollback", "action.rollback", "artifact", "rollback", True, "", "Restore the repository from the active apply transaction."),
        ActionSpec("validation.retry_failed", "Retry failed validation", "Retry failed", "Retry failed", "action.retry-failed", "validation", "validation-retry-failed", False, "", "Retry only failed validation phases."),
        ActionSpec("validation.resume", "Resume validation", "Resume", "Resume", "action.resume", "validation", "validation-resume", False, "", "Resume a paused or resumable validation session."),
        ActionSpec("target.build", "Build target", "Build", "Build", "action.build", "target", "target-build"),
        ActionSpec("target.test", "Test target", "Tests", "Tests", "action.test", "target", "target-test"),
        ActionSpec("target.validate", "Validate target", "Validate", "Validate", "action.validate", "target", "target-validate"),
        ActionSpec("target.package", "Package target", "Package", "Package", "action.package", "target", "target-package"),
        ActionSpec("workflow.inspect", "Inspect workflow", "Inspect", "Inspect", "action.inspect", "workflow", "workflow-inspect"),
        ActionSpec("workflow.retry_node", "Retry workflow node", "Retry node", "Retry node", "action.retry-node", "workflow", "workflow-retry-node"),
        ActionSpec("tests.select", "Select tests", "Select tests", "Select tests", "action.tests", "tests", "tests-select"),
        ActionSpec("tests.clear_selection", "Clear test selection", "Clear tests", "Clear", "action.clear", "tests", "tests-clear-selection"),
        ActionSpec("tests.refresh_collection", "Refresh test collection", "Refresh tests", "Refresh", "action.refresh", "tests", "tests-refresh-collection"),
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


def actions_for_context(context: ActionContext) -> list[UIActionState]:
    payload = _payload(context)
    return [action_spec(action_id).present(payload=payload) for action_id in available_action_ids(context)]




def action_text(action_id: str, variant: str = "label") -> str:
    spec = action_spec(action_id)
    if variant == "short":
        return spec.short_label
    if variant == "notification":
        return spec.notification_label
    return spec.label

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
