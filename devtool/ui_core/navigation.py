from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .state import UINavigationState


@dataclass(frozen=True, slots=True)
class NavigationSpec:
    id: str
    label: str
    short_label: str
    icon: str
    order: int
    section: str = "primary"
    routes: tuple[tuple[str, str], ...] = ()
    shortcuts: tuple[tuple[str, str], ...] = ()

    def route_for(self, surface: str) -> str:
        lookup = dict(self.routes)
        return str(lookup.get(surface) or lookup.get("*") or "")

    def present(self, surface: str) -> UINavigationState | None:
        route = self.route_for(surface)
        if not route:
            return None
        return UINavigationState(
            id=self.id,
            label=self.label,
            short_label=self.short_label,
            icon=self.icon,
            route=route,
            section=self.section,
            order=self.order,
        )


NAVIGATION_REGISTRY: tuple[NavigationSpec, ...] = (
    NavigationSpec("dashboard", "Overview", "Overview", "nav.dashboard", 10, routes=(("desktop", "overview"), ("tui", "dashboard"), ("android", "overview"), ("machine", "dashboard")), shortcuts=(("tui", "d"),)),
    NavigationSpec("operations", "Active runs", "Runs", "nav.operations", 20, routes=(("desktop", "runs"), ("machine", "operations"))),
    NavigationSpec("history", "History", "History", "nav.history", 30, routes=(("desktop", "history"), ("machine", "history"))),
    NavigationSpec("artifacts", "Artifacts", "Artifacts", "nav.artifacts", 40, routes=(("desktop", "artifacts"), ("machine", "artifacts"))),
    NavigationSpec("diagnostics", "Diagnostics", "Issues", "nav.diagnostics", 50, routes=(("desktop", "diagnostics"), ("machine", "diagnostics"))),
    NavigationSpec("projects", "Repository", "Repo", "nav.projects", 60, routes=(("desktop", "repositories"), ("android", "repos"), ("machine", "projects"))),
    NavigationSpec("targets", "Targets", "Targets", "nav.targets", 70, routes=(("desktop", "targets"), ("machine", "targets"))),
    NavigationSpec("validation", "Run", "Run", "nav.validation", 80, routes=(("desktop", "run"), ("android", "run"), ("machine", "validation"))),
    NavigationSpec("workflows", "Workflows", "Workflows", "nav.workflows", 90, routes=(("machine", "workflows"),)),
    NavigationSpec("logs", "Logs", "Logs", "nav.logs", 100, routes=(("android", "logs"), ("machine", "logs"))),
    NavigationSpec("environment", "Environment", "Environment", "nav.environment", 110, routes=(("machine", "environment"),)),
    NavigationSpec("settings", "Settings", "Settings", "nav.settings", 120, section="secondary", routes=(("desktop", "settings"), ("android", "settings"), ("machine", "settings"))),
    NavigationSpec("build", "Build", "Build", "nav.build", 130, routes=(("tui", "build"),), shortcuts=(("tui", "b"),)),
    NavigationSpec("overlays", "Overlays", "Overlays", "nav.overlays", 140, routes=(("tui", "overlays"),), shortcuts=(("tui", "o"),)),
)


_SURFACE_LABEL_OVERRIDES = {
    ("tui", "dashboard"): "Dashboard",
}

_SURFACE_ICON_FALLBACKS = {
    "overview": "⌂",
    "runs": "▶",
    "history": "↺",
    "artifacts": "◇",
    "diagnostics": "!",
    "repositories": "⌘",
    "targets": "◎",
    "run": "+",
    "settings": "⚙",
    "dashboard": "⌂",
    "build": "▶",
    "overlays": "◇",
}


def navigation_for_surface(surface: str) -> list[UINavigationState]:
    normalized = str(surface or "machine").strip().lower() or "machine"
    result: list[UINavigationState] = []
    for spec in sorted(NAVIGATION_REGISTRY, key=lambda item: item.order):
        item = spec.present(normalized)
        if item is not None:
            result.append(item)
    return result


def navigation_route_map(surface: str) -> dict[str, str]:
    return {item.route: item.label for item in navigation_for_surface(surface)}


def navigation_icon_map(surface: str) -> dict[str, str]:
    return {
        item.route: _SURFACE_ICON_FALLBACKS.get(item.route, item.icon)
        for item in navigation_for_surface(surface)
    }


def navigation_label_for_route(surface: str, route: str, *, default: str = "") -> str:
    normalized_surface = str(surface or "machine").strip().lower() or "machine"
    override = _SURFACE_LABEL_OVERRIDES.get((normalized_surface, route))
    if override:
        return override
    return navigation_route_map(normalized_surface).get(route, default or str(route or "").replace("-", " ").title())


def navigation_shortcut_map(surface: str) -> dict[str, str]:
    normalized = str(surface or "").strip().lower()
    result: dict[str, str] = {}
    for spec in NAVIGATION_REGISTRY:
        shortcut = dict(spec.shortcuts).get(normalized)
        route = spec.route_for(normalized)
        if shortcut and route:
            result[str(shortcut)] = route
    return result


def navigation_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "short_label": spec.short_label,
            "icon": spec.icon,
            "order": spec.order,
            "section": spec.section,
            "routes": dict(spec.routes),
            "shortcuts": dict(spec.shortcuts),
        }
        for spec in NAVIGATION_REGISTRY
    ]
