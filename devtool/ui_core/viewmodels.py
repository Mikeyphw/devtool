from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .actions import ActionContext, actions_for_context
from .navigation import navigation_for_surface
from .state import (
    UIDiagnosticState,
    UIOperationState,
    UIProgressState,
    UISelectionState,
    UISnapshot,
    UIWorkspaceState,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _integer(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _percent(value: Any, current: int | None = None, total: int | None = None) -> int | None:
    number = _integer(value)
    if number is None and current is not None and total is not None and total > 0:
        number = int(round(current / total * 100))
    if number is None:
        return None
    return max(0, min(100, number))


def _status(value: Any) -> str:
    normalized = _text(value).lower().replace("_", "-")
    aliases = {
        "success": "ok",
        "complete": "ok",
        "completed": "ok",
        "failure": "failed",
        "error": "failed",
        "canceled": "cancelled",
        "rolling-back": "rolling-back",
    }
    return aliases.get(normalized, normalized or "idle")


def _phase_name(raw: Mapping[str, Any]) -> str:
    phase = raw.get("phase") if isinstance(raw.get("phase"), Mapping) else {}
    return _text(phase.get("display_name") or phase.get("name") or raw.get("phase_name"))


def _unique(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        clean = _text(value)
        if clean and clean not in result:
            result.append(clean)
    return result


def _diagnostics(raw: Mapping[str, Any]) -> list[UIDiagnosticState]:
    result: list[UIDiagnosticState] = []
    seen: set[tuple[str, str, str, str]] = set()

    def append(item: Any, *, phase: str = "", source: str = "runtime", default_severity: str = "info") -> None:
        if not isinstance(item, Mapping):
            return
        message = _text(item.get("message") or item.get("detail") or item.get("text") or item.get("summary"))
        if not message:
            return
        severity = _text(item.get("severity") or item.get("level") or item.get("type") or default_severity).lower()
        if severity in {"warn"}:
            severity = "warning"
        elif severity in {"fatal", "failure"}:
            severity = "error"
        elif severity not in {"error", "warning", "info"}:
            severity = default_severity
        code = _text(item.get("code"))
        file_name = _text(item.get("file") or item.get("path"))
        item_phase = _text(item.get("phase") or phase)
        key = (severity, code, file_name, re.sub(r"\s+", " ", message).casefold())
        if key in seen:
            return
        seen.add(key)
        result.append(UIDiagnosticState(severity, message, code, item_phase, file_name, source))

    for item in raw.get("diagnostics", []) if isinstance(raw.get("diagnostics"), list) else []:
        append(item, phase=_phase_name(raw), source="state")
    for command_result in raw.get("results", []) if isinstance(raw.get("results"), list) else []:
        if not isinstance(command_result, Mapping):
            continue
        phase = _text(command_result.get("name") or command_result.get("phase"))
        for key in ("diagnostics", "issues", "errors", "warnings"):
            values = command_result.get(key)
            if isinstance(values, list):
                severity = "error" if key == "errors" else "warning" if key == "warnings" else "info"
                for item in values:
                    append(item, phase=phase, source="result", default_severity=severity)
    completion = raw.get("completion") if isinstance(raw.get("completion"), Mapping) else {}
    for item in completion.get("diagnostics", []) if isinstance(completion.get("diagnostics"), list) else []:
        append(item, phase=_text(completion.get("failure_phase") or "completion"), source="completion")
    for text in completion.get("warning_details", []) if isinstance(completion.get("warning_details"), list) else []:
        append({"severity": "warning", "message": text}, phase="completion", source="completion")
    return result


def _progress_nodes(raw: Mapping[str, Any]) -> list[UIProgressState]:
    result: list[UIProgressState] = []
    phase_progress = raw.get("phase_progress") if isinstance(raw.get("phase_progress"), list) else []
    for index, item in enumerate(phase_progress):
        if not isinstance(item, Mapping):
            continue
        phase_id = _text(item.get("phase_id") or item.get("node_id") or item.get("key") or item.get("name") or item.get("command")) or f"step-{index + 1}"
        current = _integer(item.get("current") if item.get("current") is not None else item.get("completed"))
        total = _integer(item.get("total"))
        percent = _percent(item.get("percent"), current, total)
        result.append(UIProgressState(
            id=phase_id,
            label=_text(item.get("display_name") or item.get("label") or item.get("name") or item.get("command")) or phase_id,
            status=_status(item.get("status") or "pending"),
            kind=_text(item.get("kind") or "phase"),
            parent_id=_text(item.get("parent_id") or item.get("parent")),
            current=current,
            total=total,
            percent=percent,
            detail=_text(item.get("detail") or item.get("message")),
            target=_text(item.get("target") or item.get("workspace")),
            runner=_text(item.get("runner")),
            execution_environment=_text(item.get("execution_environment")),
        ))
    if result:
        return result
    progress = raw.get("progress") if isinstance(raw.get("progress"), Mapping) else {}
    phase = raw.get("phase") if isinstance(raw.get("phase"), Mapping) else {}
    label = _text(phase.get("display_name") or phase.get("name") or raw.get("command"))
    if not label and not progress:
        return []
    current = _integer(raw.get("current") if raw.get("current") is not None else progress.get("current") or progress.get("completed"))
    total = _integer(raw.get("total") if raw.get("total") is not None else progress.get("total"))
    return [UIProgressState(
        id=_text(phase.get("phase_id") or phase.get("node_id") or phase.get("name") or raw.get("command")) or "current",
        label=label or "Current operation",
        status=_status(raw.get("status") or "running"),
        kind="phase",
        current=current,
        total=total,
        percent=_percent(progress.get("percent") if progress else raw.get("percent"), current, total),
        detail=_text(progress.get("detail") or raw.get("message")),
        target=_text(progress.get("target") or phase.get("target") or raw.get("target")),
        runner=_text(phase.get("runner") or raw.get("runner")),
        execution_environment=_text(phase.get("execution_environment") or raw.get("execution_environment")),
    )]


def runtime_state_to_ui_snapshot(
    raw: Mapping[str, Any] | None,
    *,
    surface: str = "machine",
    workspace: Mapping[str, Any] | None = None,
    selection: Mapping[str, Any] | None = None,
) -> UISnapshot:
    state = raw or {}
    workspace_override = workspace or {}
    selection_override = selection or {}
    raw_targets = state.get("targets") if isinstance(state.get("targets"), list) else []
    targets = _unique([*raw_targets, state.get("target")])
    selected_groups = _unique(selection_override.get("groups", []) if isinstance(selection_override.get("groups"), list) else [])
    selected_targets = _unique(selection_override.get("targets", []) if isinstance(selection_override.get("targets"), list) else targets)
    diagnostics = _diagnostics(state)
    status = _status(state.get("status"))
    command = _text(state.get("command"))
    completion = state.get("completion") if isinstance(state.get("completion"), Mapping) else {}
    title = _text(completion.get("headline") or state.get("title") or command) or "Devtool"
    detail = _text(completion.get("detail") or state.get("message") or state.get("text"))
    operation = UIOperationState(
        run_id=_text(state.get("run_id")),
        command=command,
        title=title,
        status=status,
        detail=detail,
        phase=_phase_name(state),
        target=_text(state.get("target")),
        runner=_text(state.get("runner") or state.get("project_type")),
        execution_environment=_text(state.get("execution_environment")),
        started_at=_text(state.get("started_at")),
        updated_at=_text(state.get("updated_at")),
        finished_at=_text(state.get("finished_at")),
        log_path=_text(state.get("log_path")),
    )
    action_context = ActionContext(
        status=status,
        command=command,
        run_id=operation.run_id,
        log_path=operation.log_path,
        target=operation.target or (selected_targets[0] if len(selected_targets) == 1 else ""),
        has_diagnostics=bool(diagnostics),
        has_artifact=bool(state.get("artifact") or state.get("artifact_path") or state.get("overlay")),
        has_test_selection=bool(state.get("test_selection")),
        retryable=bool(state.get("retryable", True)),
        resumable=bool(state.get("resumable") or status == "paused"),
        cancellable=bool(state.get("cancellable", True)),
    )
    return UISnapshot(
        workspace=UIWorkspaceState(
            name=_text(workspace_override.get("name") or state.get("repo_name")),
            root=_text(workspace_override.get("root") or state.get("repo_root")),
            targets=_unique(workspace_override.get("targets", []) if isinstance(workspace_override.get("targets"), list) else targets),
            selected_target=_text(workspace_override.get("selected_target") or state.get("target")),
            selected_groups=_unique(workspace_override.get("selected_groups", []) if isinstance(workspace_override.get("selected_groups"), list) else selected_groups),
        ),
        selection=UISelectionState(
            targets=selected_targets,
            groups=selected_groups,
            variant=_text(selection_override.get("variant") or state.get("variant")),
            operation=_text(selection_override.get("operation") or command),
        ),
        operation=operation,
        progress=_progress_nodes(state),
        diagnostics=diagnostics,
        actions=actions_for_context(action_context),
        navigation=navigation_for_surface(surface),
        surface=str(surface or "machine"),
    )
