from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Iterable, Mapping

from ..federation_observability import runtime_federation_projection
from .actions import ActionContext, actions_for_context
from .capabilities import capabilities_for_surface, negotiate_capabilities
from .diagnostics import diagnostic_presentations, summarize_diagnostics
from .confirmations import confirmation_catalog
from .notifications import notification_catalog, notification_for_event
from .progress import build_progress_tree, summarize_progress
from .responsive import resolve_responsive_layout
from .navigation import navigation_for_surface
from .preferences import UIPreferences, resolve_ui_preferences, resolve_user_ui_preferences
from .tokens import token_for_severity, token_for_status
from .state import (
    UIDiagnosticState,
    UIOperationState,
    UIProgressState,
    UISelectionState,
    UISnapshot,
    UIWorkspaceState,
    UIPresentationState,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _integer(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
        if not math.isfinite(number):
            return None
        return int(round(number))
    except (TypeError, ValueError, OverflowError):
        return None


def _flag(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


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
        if isinstance(item, str):
            item = {"message": item}
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
        line = _integer(item.get("line"))
        column = _integer(item.get("column"))
        item_phase = _text(item.get("phase") or phase)
        location = _text(item.get("location"))
        if not location and file_name:
            location = file_name
            if line is not None:
                location += f":{line}"
            if column is not None:
                location += f":{column}"
        key = (severity, code, item_phase, location or file_name, re.sub(r"\s+", " ", message).casefold())
        if key in seen:
            return
        seen.add(key)
        digest = hashlib.sha1("\x1f".join(key).encode("utf-8", "replace")).hexdigest()[:12]
        summary = _text(item.get("summary")) or message.splitlines()[0][:240]
        cause = _text(item.get("cause") or item.get("reason") or item.get("failure_reason"))
        technical = _text(item.get("technical_details") or item.get("detail"))
        related_node = _text(item.get("related_node") or item.get("node_id") or item.get("workflow_node"))
        raw_actions = item.get("next_actions") or item.get("actions") or item.get("remediation") or []
        if isinstance(raw_actions, str):
            raw_actions = [raw_actions]
        next_actions = _unique(raw_actions if isinstance(raw_actions, (list, tuple)) else [])
        if not next_actions:
            if severity == "error":
                next_actions = ["logs.open", "operation.retry"]
                if related_node or item_phase:
                    next_actions.append("workflow.retry_node")
            elif severity == "warning":
                next_actions = ["logs.open"]
        evidence = item.get("evidence") if isinstance(item.get("evidence"), Mapping) else {}
        result.append(UIDiagnosticState(
            id=f"diag:{digest}", severity=severity, message=message,
            token=token_for_severity(severity), code=code, phase=item_phase,
            file=file_name, line=line, column=column, source=source,
            summary=summary, cause=cause, location=location,
            related_node=related_node, next_actions=next_actions,
            technical_details=technical or message,
            evidence=dict(evidence),
        ))

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
    command_result = raw.get("result") if isinstance(raw.get("result"), Mapping) else None
    if command_result is not None:
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
    seen_ids: dict[str, int] = {}
    phase_progress = raw.get("phase_progress") if isinstance(raw.get("phase_progress"), list) else []
    for index, item in enumerate(phase_progress):
        if not isinstance(item, Mapping):
            continue
        base_phase_id = _text(item.get("phase_id") or item.get("node_id") or item.get("key") or item.get("name") or item.get("command")) or f"step-{index + 1}"
        count = seen_ids.get(base_phase_id, 0) + 1
        seen_ids[base_phase_id] = count
        phase_id = base_phase_id if count == 1 else f"{base_phase_id}#{count}"
        current = _integer(item.get("current") if item.get("current") is not None else item.get("completed"))
        total = _integer(item.get("total"))
        percent = _percent(item.get("percent"), current, total)
        duration_ms = _integer(item.get("duration_ms"))
        if duration_ms is None and item.get("duration_s") is not None:
            try:
                duration_ms = int(round(float(item.get("duration_s")) * 1000))
            except (TypeError, ValueError):
                duration_ms = None
        metrics = item.get("metrics") if isinstance(item.get("metrics"), Mapping) else {}
        for metric_key in ("tests_passed", "tests_failed", "tests_skipped", "tests_total", "test_assemblies", "unit_label", "command"):
            if metric_key in item:
                metrics = {**dict(metrics), metric_key: item.get(metric_key)}
        if phase_id != base_phase_id:
            metrics = {**dict(metrics), "original_id": base_phase_id}
        blocked_by = item.get("blocked_by") or []
        dependencies = item.get("depends_on") or item.get("dependencies") or []
        if isinstance(blocked_by, str): blocked_by = [blocked_by]
        if isinstance(dependencies, str): dependencies = [dependencies]
        result.append(UIProgressState(
            id=phase_id,
            label=_text(item.get("display_name") or item.get("label") or item.get("name") or item.get("command")) or phase_id,
            status=_status(item.get("status") or "pending"),
            token=token_for_status(_status(item.get("status") or "pending")),
            kind=_text(item.get("kind") or ("step" if item.get("parent_id") else "phase")),
            parent_id=_text(item.get("parent_id") or item.get("parent")),
            current=current,
            total=total,
            percent=percent,
            detail=_text(item.get("detail") or item.get("message")),
            target=_text(item.get("target") or item.get("workspace")),
            workflow=_text(item.get("workflow") or item.get("workflow_name")),
            runner=_text(item.get("runner")),
            execution_environment=_text(item.get("execution_environment")),
            started_at=_text(item.get("started_at")),
            finished_at=_text(item.get("finished_at")),
            duration_ms=duration_ms,
            warnings=max(0, _integer(item.get("warnings")) or 0),
            errors=max(0, _integer(item.get("errors")) or 0),
            blocked_by=_unique(blocked_by if isinstance(blocked_by, (list, tuple)) else []),
            dependencies=_unique(dependencies if isinstance(dependencies, (list, tuple)) else []),
            metrics=dict(metrics),
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
        token=token_for_status(_status(raw.get("status") or "running")),
        kind=_text(phase.get("kind") or "phase"),
        current=current,
        total=total,
        percent=_percent(progress.get("percent") if progress else raw.get("percent"), current, total),
        detail=_text(progress.get("detail") or raw.get("message")),
        target=_text(progress.get("target") or phase.get("target") or raw.get("target")),
        workflow=_text(phase.get("workflow") or phase.get("workflow_name")),
        runner=_text(phase.get("runner") or raw.get("runner")),
        execution_environment=_text(phase.get("execution_environment") or raw.get("execution_environment")),
        started_at=_text(phase.get("started_at") or raw.get("started_at")),
        finished_at=_text(phase.get("finished_at") or raw.get("finished_at")),
        warnings=max(0, _integer(progress.get("warnings")) or 0),
        errors=max(0, _integer(progress.get("errors")) or 0),
    )]


def runtime_state_to_ui_snapshot(
    raw: Mapping[str, Any] | None,
    *,
    surface: str = "machine",
    workspace: Mapping[str, Any] | None = None,
    selection: Mapping[str, Any] | None = None,
    preferences: UIPreferences | Mapping[str, Any] | None = None,
    viewport: Mapping[str, Any] | None = None,
    requested_capabilities: Iterable[str] = (),
) -> UISnapshot:
    state = raw or {}
    workspace_override = workspace or {}
    selection_override = selection or {}
    if isinstance(preferences, UIPreferences):
        try:
            resolved_preferences = resolve_ui_preferences(preferences.as_dict(), surface=surface, env={})
        except Exception:
            resolved_preferences = resolve_ui_preferences({}, surface=surface, env={})
    elif preferences is None:
        resolved_preferences = resolve_user_ui_preferences(surface=surface)
    elif isinstance(preferences, Mapping) and "provenance" in preferences and "surface" in preferences:
        # Live-state producers may carry an already-resolved per-surface
        # presentation across IPC/files.  Treat that payload as untrusted input:
        # re-normalize public preference fields so stale IPC/state cannot smuggle
        # NaN/inf numbers or unsupported enum values into renderers.  Bad stale
        # preference snapshots fall back to safe defaults instead of crashing UI.
        preference_keys = {
            "profile", "theme", "density", "icons", "motion", "verbosity",
            "timestamps", "duration_format", "confirm_destructive",
            "confirm_cancel", "show_completed_steps", "show_environment",
            "show_commands", "show_diagnostics", "sidebar", "mouse",
            "max_steps", "notification_detail",
        }
        config_like = {key: preferences[key] for key in preference_keys if key in preferences}
        try:
            resolved_preferences = resolve_ui_preferences(config_like, surface=surface, env={})
        except Exception:
            resolved_preferences = resolve_ui_preferences({}, surface=surface, env={})
    else:
        try:
            resolved_preferences = resolve_ui_preferences(preferences, surface=surface)
        except Exception:
            resolved_preferences = resolve_ui_preferences({}, surface=surface, env={})
    raw_targets = state.get("targets") if isinstance(state.get("targets"), list) else []
    targets = _unique([*raw_targets, state.get("target")])
    selected_groups = _unique(selection_override.get("groups", []) if isinstance(selection_override.get("groups"), list) else [])
    selected_targets = _unique(selection_override.get("targets", []) if isinstance(selection_override.get("targets"), list) else targets)
    diagnostics = _diagnostics(state)
    progress_nodes = _progress_nodes(state)
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
        token=token_for_status(status),
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
    capability_requirements = list(requested_capabilities)
    if progress_nodes and "tree_view" not in capability_requirements:
        capability_requirements.append("tree_view")
    if diagnostics and "rich_text" not in capability_requirements:
        capability_requirements.append("rich_text")
    if str(surface or "machine") not in {"machine", "hud"} and "interactive" not in capability_requirements:
        capability_requirements.append("interactive")
    capabilities = capabilities_for_surface(surface)
    negotiation = negotiate_capabilities(surface, capability_requirements, strict=False)
    viewport_state = viewport or (state.get("ui_viewport") if isinstance(state.get("ui_viewport"), Mapping) else {}) or {}
    responsive = resolve_responsive_layout(
        surface,
        width=_integer(viewport_state.get("width")),
        height=_integer(viewport_state.get("height")),
        preferences=resolved_preferences,
        capabilities=capabilities,
    )
    progress_summary = summarize_progress(progress_nodes)
    diagnostic_summary = summarize_diagnostics(diagnostics)

    phase_state = state.get("phase") if isinstance(state.get("phase"), Mapping) else {}
    action_context = ActionContext(
        status=status,
        command=command,
        run_id=operation.run_id,
        node_id=_text(phase_state.get("node_id") or state.get("node_id")),
        log_path=operation.log_path,
        target=operation.target or (selected_targets[0] if len(selected_targets) == 1 else ""),
        has_diagnostics=bool(diagnostics),
        has_artifact=bool(state.get("artifact") or state.get("artifact_path") or state.get("overlay")),
        has_test_selection=bool(state.get("test_selection")),
        retryable=_flag(state.get("retryable"), True),
        resumable=_flag(state.get("resumable"), False) or status == "paused",
        cancellable=_flag(state.get("cancellable"), True),
        payload={"has_uncommitted_mutation": _flag(state.get("has_uncommitted_mutation"), False)},
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
        progress=progress_nodes,
        diagnostics=diagnostics,
        actions=actions_for_context(action_context, confirmation_policy=resolved_preferences.confirmation_policy),
        navigation=navigation_for_surface(surface),
        presentation=UIPresentationState(**{
            key: value for key, value in resolved_preferences.as_dict().items()
            if key not in {"surface"}
        }),
        capabilities=capabilities.as_dict(),
        capability_negotiation=negotiation.as_dict(),
        responsive=responsive.as_dict(),
        progress_summary=progress_summary.as_dict(),
        progress_tree=[item.as_dict() for item in build_progress_tree(progress_nodes, operation)],
        diagnostic_summary=diagnostic_summary.as_dict(),
        diagnostic_presentations=[item.as_dict() for item in diagnostic_presentations(diagnostics)],
        confirmation_policy=confirmation_catalog(resolved_preferences.confirmation_policy),
        notification_policy=notification_catalog(resolved_preferences.notification_policy),
        notifications=[notification_for_event(
            "failed" if status in {"failed", "cancelled", "interrupted"} else "success" if status == "ok" else "started" if status in {"running", "preparing"} else "phase_changed",
            status=status,
            severity="error" if diagnostic_summary.errors else "warning" if diagnostic_summary.warnings else "",
            title=operation.title,
            body=operation.detail,
            requires_action=bool(diagnostic_summary.errors and status not in {"ok", "idle"}),
            actions=tuple(item.id for item in actions_for_context(action_context, confirmation_policy=resolved_preferences.confirmation_policy)),
            policy=resolved_preferences.notification_policy,
            notification_id=f"run:{operation.run_id or command or 'devtool'}",
        ).as_dict()],
        federation=(dict(state.get("federation")) if isinstance(state.get("federation"), Mapping) else runtime_federation_projection(state.get("execution"))),
        surface=str(surface or "machine"),
    )
