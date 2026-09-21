from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .copy import copy_text
from .icons import resolve_icon
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
            label=copy_text(f"nav.{self.id}", surface=surface),
            short_label=copy_text(f"nav.{self.id}", "short", surface=surface),
            icon=self.icon,
            route=route,
            section=self.section,
            order=self.order,
        )


def _nav(nav_id: str, icon: str, order: int, *, section: str = "primary", routes: tuple[tuple[str, str], ...] = (), shortcuts: tuple[tuple[str, str], ...] = ()) -> NavigationSpec:
    copy_id = f"nav.{nav_id}"
    return NavigationSpec(nav_id, copy_text(copy_id), copy_text(copy_id, "short"), icon, order, section, routes, shortcuts)


NAVIGATION_REGISTRY: tuple[NavigationSpec, ...] = (
    _nav("dashboard", "nav.dashboard", 10, routes=(("desktop", "overview"), ("tui", "dashboard"), ("android", "overview"), ("machine", "dashboard")), shortcuts=(("tui", "d"),)),
    _nav("operations", "nav.operations", 20, routes=(("desktop", "runs"), ("machine", "operations"))),
    _nav("history", "nav.history", 30, routes=(("desktop", "history"), ("machine", "history"))),
    _nav("artifacts", "nav.artifacts", 40, routes=(("desktop", "artifacts"), ("machine", "artifacts"))),
    _nav("diagnostics", "nav.diagnostics", 50, routes=(("desktop", "diagnostics"), ("machine", "diagnostics"))),
    _nav("projects", "nav.projects", 60, routes=(("desktop", "repositories"), ("android", "repos"), ("machine", "projects"))),
    _nav("targets", "nav.targets", 70, routes=(("desktop", "targets"), ("machine", "targets"))),
    _nav("validation", "nav.validation", 80, routes=(("desktop", "run"), ("android", "run"), ("machine", "validation"))),
    _nav("workflows", "nav.workflows", 90, routes=(("machine", "workflows"),)),
    _nav("logs", "nav.logs", 100, routes=(("android", "logs"), ("machine", "logs"))),
    _nav("environment", "nav.environment", 110, routes=(("machine", "environment"),)),
    _nav("settings", "nav.settings", 120, section="secondary", routes=(("desktop", "settings"), ("android", "settings"), ("machine", "settings"))),
    _nav("build", "nav.build", 130, routes=(("tui", "build"),), shortcuts=(("tui", "b"),)),
    _nav("workers", "nav.environment", 135, routes=(("tui", "workers"),), shortcuts=(("tui", "w"),)),
    _nav("overlays", "nav.overlays", 140, routes=(("tui", "overlays"),), shortcuts=(("tui", "o"),)),
)


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


def navigation_icon_map(surface: str, *, mode: str = "auto") -> dict[str, str]:
    normalized = str(surface or "machine").strip().lower() or "machine"
    return {
        item.route: resolve_icon(item.icon, mode=mode, surface=normalized)
        for item in navigation_for_surface(normalized)
    }


def navigation_label_for_route(surface: str, route: str, *, default: str = "") -> str:
    return navigation_route_map(surface).get(route, default or str(route or "").replace("-", " ").title())


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
