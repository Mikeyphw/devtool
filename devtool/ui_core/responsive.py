from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .capabilities import UICapabilities, capabilities_for_surface
from .preferences import UIPreferences, normalize_surface

RESPONSIVE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ResponsiveLayout:
    surface: str
    mode: str
    width: int | None
    height: int | None
    navigation: str
    inspector: str
    panes: int
    progress_detail: str
    diagnostic_detail: str
    effective_density: str
    reason: str
    schema_version: int = RESPONSIVE_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_BREAKPOINTS: dict[str, tuple[int, int]] = {
    "tui": (60, 110),
    "desktop": (980, 1400),
    "android": (600, 840),
    "hud": (520, 900),
    "machine": (0, 0),
}


def _default_width(surface: str) -> int | None:
    return {
        "tui": 100,
        "desktop": 1200,
        "android": 420,
        "hud": 520,
        "machine": None,
    }.get(surface)


def resolve_responsive_layout(
    surface: str,
    *,
    width: int | None = None,
    height: int | None = None,
    preferences: UIPreferences | None = None,
    capabilities: UICapabilities | None = None,
) -> ResponsiveLayout:
    normalized = normalize_surface(surface)
    caps = capabilities or capabilities_for_surface(normalized)
    actual_width = int(width) if width is not None and int(width) > 0 else _default_width(normalized)
    density = preferences.density if preferences is not None else "comfortable"

    if normalized == "machine":
        return ResponsiveLayout(normalized, "semantic", actual_width, height, "semantic", "semantic", 1, "all", "all", density, "machine contract is renderer-neutral")

    low, high = _BREAKPOINTS[normalized]
    if actual_width is None or actual_width < low:
        mode = "single-pane"
        panes = 1
        navigation = "collapsed" if normalized != "tui" else "overlay"
        inspector = "drill-down"
        progress_detail = "compact"
        diagnostic_detail = "summary"
        reason = f"width below {low}px/columns"
    elif actual_width < high or not caps.multi_pane:
        mode = "two-pane" if caps.multi_pane else "single-pane"
        panes = 2 if caps.multi_pane else 1
        navigation = "compact"
        inspector = "drawer"
        progress_detail = "normal"
        diagnostic_detail = "summary+location"
        reason = f"width between {low} and {high - 1}px/columns" if caps.multi_pane else "surface does not support multi-pane layout"
    else:
        mode = "three-pane"
        panes = 3
        navigation = "persistent"
        inspector = "visible"
        progress_detail = "detailed"
        diagnostic_detail = "full"
        reason = f"width at least {high}px/columns"

    if normalized == "hud":
        panes = min(panes, 2)
        if mode == "three-pane":
            mode = "two-pane"
        navigation = "actions-only"
        inspector = "expander"
    if normalized == "android":
        navigation = "bottom-or-rail" if panes == 1 else "rail"
        inspector = "detail-pane" if panes > 1 else "drill-down"

    return ResponsiveLayout(
        surface=normalized,
        mode=mode,
        width=actual_width,
        height=int(height) if height is not None and int(height) > 0 else None,
        navigation=navigation,
        inspector=inspector,
        panes=panes,
        progress_detail=progress_detail,
        diagnostic_detail=diagnostic_detail,
        effective_density=density,
        reason=reason,
    )


def responsive_policy_catalog() -> dict[str, Any]:
    return {
        "schema_version": RESPONSIVE_SCHEMA_VERSION,
        "breakpoints": {
            surface: {"compact_below": values[0], "expanded_at": values[1]}
            for surface, values in _BREAKPOINTS.items()
        },
        "modes": ["single-pane", "two-pane", "three-pane", "semantic"],
    }
