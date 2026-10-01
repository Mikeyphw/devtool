from __future__ import annotations

"""Canonical terminal-control shortcut authority.

PRES-R03 collapses the historically competing Textual, legacy TUI and UI-Core
shortcut dictionaries into one import-safe registry.  The registry owns only
presentation intents; domain execution remains in the existing action/runtime
authorities.
"""

from dataclasses import dataclass
from typing import Iterable

TUI_PAGES: tuple[str, ...] = (
    "dashboard",
    "runs",
    "logs",
    "diagnostics",
    "history",
    "targets",
    "workers",
    "validation",
    "artifacts",
    "overlays",
    "evidence",
)

# Compatibility aliases accepted at the CLI/runtime boundary.  ``build`` was
# the historical Rich TUI page; the concrete Textual replacement presents the
# same operational work under Runs.
TUI_PAGE_ALIASES: dict[str, str] = {
    "build": "runs",
    "run": "runs",
    "log": "logs",
    "diagnostic": "diagnostics",
    "worker": "workers",
    "ssh": "workers",
    "artifact": "artifacts",
    "overlay": "overlays",
}

TUI_PAGE_CHOICES: tuple[str, ...] = (*TUI_PAGES, *TUI_PAGE_ALIASES.keys())

TUI_NAV_SHORTCUTS: dict[str, str] = {
    "d": "dashboard",
    "u": "runs",
    "l": "logs",
    "g": "diagnostics",
    "h": "history",
    "t": "targets",
    "w": "workers",
    "v": "validation",
    "s": "artifacts",
    "o": "overlays",
    "e": "evidence",
}

# Textual action names are intentionally presentation intents, not domain calls.
TUI_GLOBAL_SHORTCUTS: dict[str, tuple[str, str]] = {
    "q": ("quit_control_center", "Quit"),
    "escape": ("back_or_quit", "Back"),
    "?": ("show_help", "Help"),
    "ctrl+p": ("show_command_palette", "Commands"),
}

TUI_RUN_ACTION_SHORTCUTS: dict[str, tuple[str, str]] = {
    "c": ("cancel", "Cancel"),
    "y": ("retry", "Retry"),
    "r": ("rerun", "Rerun"),
    "m": ("resume", "Resume"),
}

# Ctrl+C is a second, explicit cancellation gesture.  It intentionally maps to
# the same domain action as ``c`` while remaining a distinct key.
TUI_CANCEL_SHORTCUT = "ctrl+c"

# Legacy semantic action ids are derived from the same key authority.  They are
# filtered by availability at runtime and therefore do not create a second map.
TUI_UI_ACTION_SHORTCUTS: dict[str, str] = {
    "operation.cancel": "c",
    "operation.retry": "y",
    "operation.rerun": "r",
    "validation.retry_failed": "f",
    "validation.resume": "m",
    "diagnostics.copy": "x",
    "workflow.inspect": "i",
}


@dataclass(frozen=True, slots=True)
class ShortcutRow:
    key: str
    intent: str
    label: str
    scope: str

    def as_dict(self) -> dict[str, str]:
        return {"key": self.key, "intent": self.intent, "label": self.label, "scope": self.scope}


def normalize_tui_page(page: str | None) -> tuple[str, str]:
    """Return ``(canonical_page, compatibility_notice)``.

    Unknown values fail closed to dashboard for import-safe/runtime callers.
    Argparse surfaces normally reject unknown values before this boundary.
    """

    raw = str(page or "dashboard").strip().lower() or "dashboard"
    if raw in TUI_PAGES:
        return raw, ""
    alias = TUI_PAGE_ALIASES.get(raw)
    if alias:
        return alias, f"Legacy TUI page '{raw}' is now '{alias}'."
    return "dashboard", f"Unknown TUI page '{raw}'; opened dashboard."


def textual_control_center_bindings() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for key, (action, label) in TUI_GLOBAL_SHORTCUTS.items():
        rows.append((key, action, label))
    for key, page in TUI_NAV_SHORTCUTS.items():
        rows.append((key, f"page('{page}')", page.title()))
    for key, (action_id, label) in TUI_RUN_ACTION_SHORTCUTS.items():
        rows.append((key, f"run_action('{action_id}')", label))
    rows.append((TUI_CANCEL_SHORTCUT, "cancel_selected", "Cancel selected run"))
    return rows


def shortcut_rows() -> tuple[ShortcutRow, ...]:
    rows: list[ShortcutRow] = []
    for key, (action, label) in TUI_GLOBAL_SHORTCUTS.items():
        rows.append(ShortcutRow(key, action, label, "global"))
    for key, page in TUI_NAV_SHORTCUTS.items():
        rows.append(ShortcutRow(key, f"page:{page}", page.title(), "navigation"))
    for key, (action_id, label) in TUI_RUN_ACTION_SHORTCUTS.items():
        rows.append(ShortcutRow(key, f"run:{action_id}", label, "run-action"))
    rows.append(ShortcutRow(TUI_CANCEL_SHORTCUT, "run:cancel", "Cancel selected run", "run-action"))
    for action_id, key in TUI_UI_ACTION_SHORTCUTS.items():
        # Do not duplicate semantic run actions already represented above.
        if action_id in {"operation.cancel", "operation.retry", "operation.rerun", "validation.resume"}:
            continue
        rows.append(ShortcutRow(key, action_id, action_id.replace(".", " ").title(), "legacy-action"))
    return tuple(rows)


def shortcut_collisions(rows: Iterable[ShortcutRow] | None = None) -> dict[str, list[ShortcutRow]]:
    by_key: dict[str, list[ShortcutRow]] = {}
    for row in rows or shortcut_rows():
        key = row.key.strip().casefold()
        if not key:
            continue
        by_key.setdefault(key, []).append(row)
    return {key: values for key, values in by_key.items() if len(values) > 1}


def assert_tui_shortcuts_collision_free() -> None:
    collisions = shortcut_collisions()
    if collisions:
        detail = "; ".join(
            f"{key}: {', '.join(row.intent for row in rows)}"
            for key, rows in sorted(collisions.items())
        )
        raise AssertionError(f"TUI shortcut collision(s): {detail}")


def shortcut_for_ui_action(action_id: str) -> str:
    return str(TUI_UI_ACTION_SHORTCUTS.get(str(action_id), ""))


# Fail at import/test time if future edits create incompatible definitions.
assert_tui_shortcuts_collision_free()


__all__ = [
    "ShortcutRow",
    "TUI_CANCEL_SHORTCUT",
    "TUI_GLOBAL_SHORTCUTS",
    "TUI_NAV_SHORTCUTS",
    "TUI_PAGE_ALIASES",
    "TUI_PAGE_CHOICES",
    "TUI_PAGES",
    "TUI_RUN_ACTION_SHORTCUTS",
    "TUI_UI_ACTION_SHORTCUTS",
    "assert_tui_shortcuts_collision_free",
    "normalize_tui_page",
    "shortcut_collisions",
    "shortcut_for_ui_action",
    "shortcut_rows",
    "textual_control_center_bindings",
]
