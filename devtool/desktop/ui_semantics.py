from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from ..ui_core.actions import action_spec
from ..ui_core.copy import copy_text
from ..ui_core.navigation import navigation_label_for_route

DESKTOP_UI_SCHEMA_VERSION = 1


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list_of_mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


@dataclass(slots=True)
class DesktopSemanticView:
    operation: dict[str, Any] = field(default_factory=dict)
    presentation: dict[str, Any] = field(default_factory=dict)
    responsive: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    navigation: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    progress_summary: dict[str, Any] = field(default_factory=dict)
    progress_tree: list[dict[str, Any]] = field(default_factory=list)
    diagnostic_summary: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    notifications: list[dict[str, Any]] = field(default_factory=list)
    schema_version: int = DESKTOP_UI_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_desktop_semantic_view(ui_snapshot: Mapping[str, Any] | None) -> DesktopSemanticView:
    ui = _mapping(ui_snapshot)
    operation = _mapping(ui.get("operation"))
    presentation = _mapping(ui.get("presentation"))
    responsive = _mapping(ui.get("responsive"))
    capabilities = _mapping(ui.get("capabilities"))
    navigation = _list_of_mappings(ui.get("navigation"))
    actions = _list_of_mappings(ui.get("actions"))
    progress_summary = _mapping(ui.get("progress_summary"))
    progress_tree = _list_of_mappings(ui.get("progress_tree"))
    diagnostic_summary = _mapping(ui.get("diagnostic_summary"))
    diagnostics = _list_of_mappings(ui.get("diagnostic_presentations"))
    notifications = _list_of_mappings(ui.get("notifications"))

    # Make renderer-facing fields explicit and deterministic. Renderers should
    # never need to reconstruct action semantics or terminology themselves.
    for item in navigation:
        route = str(item.get("route") or "")
        item.setdefault("label", navigation_label_for_route("desktop", route, default=route))
    for item in actions:
        action_id = str(item.get("id") or "")
        if not action_id:
            continue
        try:
            spec = action_spec(action_id)
        except KeyError:
            continue
        item.setdefault("label", spec.label)
        item.setdefault("short_label", spec.short_label)
        item.setdefault("intent", spec.intent)
        item.setdefault("destructive", spec.destructive)

    return DesktopSemanticView(
        operation=operation,
        presentation=presentation,
        responsive=responsive,
        capabilities=capabilities,
        navigation=navigation,
        actions=actions,
        progress_summary=progress_summary,
        progress_tree=progress_tree,
        diagnostic_summary=diagnostic_summary,
        diagnostics=diagnostics,
        notifications=notifications,
    )


def desktop_action(view: Mapping[str, Any] | DesktopSemanticView, action_id: str) -> dict[str, Any]:
    payload = view.as_dict() if isinstance(view, DesktopSemanticView) else _mapping(view)
    for item in _list_of_mappings(payload.get("actions")):
        if str(item.get("id") or "") == action_id:
            return item
    return {}


def primary_desktop_action(view: Mapping[str, Any] | DesktopSemanticView) -> dict[str, Any]:
    payload = view.as_dict() if isinstance(view, DesktopSemanticView) else _mapping(view)
    actions = _list_of_mappings(payload.get("actions"))
    for preferred in (
        "operation.cancel", "validation.retry_failed", "operation.retry",
        "validation.resume", "logs.open", "workflow.inspect",
    ):
        for item in actions:
            if str(item.get("id") or "") == preferred and bool(item.get("enabled", True)):
                return item
    for item in actions:
        if bool(item.get("enabled", True)):
            return item
    return {
        "id": "",
        "label": copy_text("nav.dashboard"),
        "short_label": copy_text("nav.dashboard", "short"),
        "enabled": False,
    }
