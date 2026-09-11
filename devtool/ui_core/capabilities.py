from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .preferences import normalize_surface

CAPABILITY_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class UICapabilities:
    surface: str
    interactive: bool = False
    rich_text: bool = False
    tables: bool = False
    tree_view: bool = False
    notifications: bool = False
    persistent_navigation: bool = False
    dialogs: bool = False
    hyperlinks: bool = False
    keyboard: bool = False
    touch: bool = False
    mouse: bool = False
    clipboard: bool = False
    native_icons: bool = False
    resize_events: bool = False
    multi_pane: bool = False
    schema_version: int = CAPABILITY_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def enabled(self) -> tuple[str, ...]:
        result: list[str] = []
        for name in CAPABILITY_NAMES:
            if bool(getattr(self, name)):
                result.append(name)
        return tuple(result)


@dataclass(frozen=True, slots=True)
class CapabilityNegotiation:
    surface: str
    requested: tuple[str, ...] = ()
    supported: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    fallbacks: dict[str, str] = field(default_factory=dict)
    schema_version: int = CAPABILITY_SCHEMA_VERSION

    @property
    def satisfied(self) -> bool:
        return not self.missing

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"satisfied": self.satisfied}


CAPABILITY_NAMES = (
    "interactive",
    "rich_text",
    "tables",
    "tree_view",
    "notifications",
    "persistent_navigation",
    "dialogs",
    "hyperlinks",
    "keyboard",
    "touch",
    "mouse",
    "clipboard",
    "native_icons",
    "resize_events",
    "multi_pane",
)

_CAPABILITY_MATRIX: dict[str, dict[str, bool]] = {
    "machine": {
        "tables": True,
        "tree_view": True,
        "hyperlinks": True,
    },
    "desktop": {
        "interactive": True,
        "rich_text": True,
        "tables": True,
        "tree_view": True,
        "notifications": True,
        "persistent_navigation": True,
        "dialogs": True,
        "hyperlinks": True,
        "keyboard": True,
        "mouse": True,
        "clipboard": True,
        "native_icons": True,
        "resize_events": True,
        "multi_pane": True,
    },
    "tui": {
        "interactive": True,
        "rich_text": True,
        "tables": True,
        "tree_view": True,
        "persistent_navigation": True,
        "dialogs": True,
        "hyperlinks": True,
        "keyboard": True,
        "mouse": True,
        "clipboard": True,
        "resize_events": True,
        "multi_pane": True,
    },
    "hud": {
        "interactive": True,
        "rich_text": True,
        "tables": True,
        "notifications": True,
        "hyperlinks": True,
        "keyboard": True,
        "mouse": True,
        "clipboard": True,
        "resize_events": True,
    },
    "android": {
        "interactive": True,
        "rich_text": True,
        "tables": True,
        "tree_view": True,
        "notifications": True,
        "persistent_navigation": True,
        "dialogs": True,
        "hyperlinks": True,
        "touch": True,
        "clipboard": True,
        "native_icons": True,
        "resize_events": True,
        "multi_pane": True,
    },
}

_CAPABILITY_FALLBACKS: dict[str, str] = {
    "interactive": "read-only state",
    "rich_text": "plain text",
    "tables": "key/value rows",
    "tree_view": "indented flat list",
    "notifications": "status event",
    "persistent_navigation": "explicit route/action",
    "dialogs": "inline confirmation",
    "hyperlinks": "plain URI/path",
    "keyboard": "action list",
    "touch": "action list",
    "mouse": "keyboard/action navigation",
    "clipboard": "print/copyable text",
    "native_icons": "semantic Unicode/ASCII icon",
    "resize_events": "static layout",
    "multi_pane": "single-pane drill-down",
}


def capabilities_for_surface(surface: str) -> UICapabilities:
    normalized = normalize_surface(surface)
    values = {name: False for name in CAPABILITY_NAMES}
    values.update(_CAPABILITY_MATRIX.get(normalized, {}))
    return UICapabilities(surface=normalized, **values)


def negotiate_capabilities(surface: str, requested: Iterable[str] = ()) -> CapabilityNegotiation:
    capabilities = capabilities_for_surface(surface)
    normalized: list[str] = []
    for value in requested:
        key = str(value or "").strip().lower().replace("-", "_")
        if key and key not in normalized:
            normalized.append(key)
    unknown = [item for item in normalized if item not in CAPABILITY_NAMES]
    if unknown:
        raise ValueError(f"Unknown UI capability: {', '.join(unknown)}")
    supported = tuple(item for item in normalized if bool(getattr(capabilities, item)))
    missing = tuple(item for item in normalized if not bool(getattr(capabilities, item)))
    fallbacks = {item: _CAPABILITY_FALLBACKS[item] for item in missing}
    return CapabilityNegotiation(
        surface=capabilities.surface,
        requested=tuple(normalized),
        supported=supported,
        missing=missing,
        fallbacks=fallbacks,
    )


def capability_catalog() -> dict[str, Any]:
    return {
        "schema_version": CAPABILITY_SCHEMA_VERSION,
        "capabilities": list(CAPABILITY_NAMES),
        "surfaces": {surface: capabilities_for_surface(surface).as_dict() for surface in _CAPABILITY_MATRIX},
        "fallbacks": dict(_CAPABILITY_FALLBACKS),
    }
