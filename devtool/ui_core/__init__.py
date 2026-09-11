"""Shared UI semantics for every Devtool frontend.

UI Core owns semantic state, action meaning, and navigation meaning. It does not
render GTK widgets, terminal controls, Android views, colors, typography, or
layout. Those remain frontend responsibilities.
"""

from .actions import ACTION_REGISTRY, ActionContext, ActionSpec, action_catalog, action_spec, action_text, actions_for_context
from .navigation import NAVIGATION_REGISTRY, NavigationSpec, navigation_catalog, navigation_for_surface, navigation_shortcut_map
from .state import (
    UI_SCHEMA_VERSION,
    UIActionState,
    UIDiagnosticState,
    UINavigationState,
    UIOperationState,
    UIProgressState,
    UISelectionState,
    UISnapshot,
    UIWorkspaceState,
)
from .viewmodels import runtime_state_to_ui_snapshot

__all__ = [
    "ACTION_REGISTRY",
    "NAVIGATION_REGISTRY",
    "UI_SCHEMA_VERSION",
    "ActionContext",
    "ActionSpec",
    "NavigationSpec",
    "UIActionState",
    "UIDiagnosticState",
    "UINavigationState",
    "UIOperationState",
    "UIProgressState",
    "UISelectionState",
    "UISnapshot",
    "UIWorkspaceState",
    "action_catalog",
    "action_spec",
    "action_text",
    "actions_for_context",
    "navigation_catalog",
    "navigation_for_surface",
    "navigation_shortcut_map",
    "runtime_state_to_ui_snapshot",
]
