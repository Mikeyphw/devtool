"""Shared UI semantics for every Devtool frontend.

UI Core owns semantic state, action meaning, navigation meaning, presentation
preferences, semantic tokens, icons, and user-facing terminology. It does not
render GTK widgets, terminal controls, Android views, colors, typography, or
layout; those remain frontend responsibilities.
"""

from .capabilities import CAPABILITY_SCHEMA_VERSION, CAPABILITY_NAMES, CapabilityNegotiation, UICapabilities, capability_catalog, capabilities_for_surface, negotiate_capabilities
from .diagnostics import DIAGNOSTIC_SCHEMA_VERSION, UIDiagnosticPresentation, UIDiagnosticSummary, diagnostic_presentations, summarize_diagnostics
from .confirmations import CONFIRMATION_SCHEMA_VERSION, ConfirmationDecision, confirmation_catalog, confirmation_decision, normalize_confirmation_policy
from .notifications import NOTIFICATION_SCHEMA_VERSION, UINotification, notification_catalog, notification_for_event, normalize_notification_policy
from .progress import PROGRESS_SCHEMA_VERSION, UIProgressSummary, UIProgressTreeNode, build_progress_tree, summarize_progress
from .responsive import RESPONSIVE_SCHEMA_VERSION, ResponsiveLayout, resolve_responsive_layout, responsive_policy_catalog
from .actions import ACTION_REGISTRY, ActionContext, ActionSpec, action_catalog, action_spec, action_text, actions_for_context
from .copy import COPY_REGISTRY, COPY_SCHEMA_VERSION, CopySpec, copy_catalog, copy_spec, copy_text
from .icons import ICON_REGISTRY, ICON_SCHEMA_VERSION, IconSpec, icon_catalog, icon_spec, resolve_icon
from .navigation import NAVIGATION_REGISTRY, NavigationSpec, navigation_catalog, navigation_for_surface, navigation_icon_map, navigation_shortcut_map
from .shortcuts import TUI_NAV_SHORTCUTS, TUI_PAGE_ALIASES, TUI_PAGE_CHOICES, TUI_PAGES, assert_tui_shortcuts_collision_free, normalize_tui_page, shortcut_rows, textual_control_center_bindings
from .preferences import (
    UI_PROFILE_NAMES,
    UI_SURFACES,
    UIPreferences,
    UIPreferencesError,
    normalize_surface,
    load_user_ui_config,
    profile_catalog,
    resolve_ui_preferences,
    resolve_user_ui_preferences,
    user_ui_config_path,
    validate_ui_config,
)
from .state import (
    UI_SCHEMA_VERSION,
    UIActionState,
    UIDiagnosticState,
    UINavigationState,
    UIOperationState,
    UIPresentationState,
    UIProgressState,
    UISelectionState,
    UISnapshot,
    UIWorkspaceState,
)
from .tokens import TOKEN_REGISTRY, TOKEN_SCHEMA_VERSION, SemanticToken, token_catalog, token_for_severity, token_for_status
from .viewmodels import runtime_state_to_ui_snapshot

__all__ = [
    "ACTION_REGISTRY", "COPY_REGISTRY", "ICON_REGISTRY", "NAVIGATION_REGISTRY", "TOKEN_REGISTRY",
    "CAPABILITY_SCHEMA_VERSION", "DIAGNOSTIC_SCHEMA_VERSION", "PROGRESS_SCHEMA_VERSION", "RESPONSIVE_SCHEMA_VERSION", "CONFIRMATION_SCHEMA_VERSION", "NOTIFICATION_SCHEMA_VERSION",
    "CAPABILITY_NAMES", "UICapabilities", "CapabilityNegotiation", "ResponsiveLayout", "UIProgressSummary", "UIProgressTreeNode", "UIDiagnosticSummary", "UIDiagnosticPresentation", "ConfirmationDecision", "UINotification",
    "COPY_SCHEMA_VERSION", "ICON_SCHEMA_VERSION", "TOKEN_SCHEMA_VERSION", "UI_SCHEMA_VERSION",
    "UI_PROFILE_NAMES", "UI_SURFACES",
    "ActionContext", "ActionSpec", "CopySpec", "IconSpec", "NavigationSpec", "SemanticToken",
    "UIActionState", "UIDiagnosticState", "UINavigationState", "UIOperationState", "UIPresentationState",
    "UIProgressState", "UISelectionState", "UISnapshot", "UIWorkspaceState", "UIPreferences", "UIPreferencesError",
    "action_catalog", "action_spec", "action_text", "actions_for_context",
    "copy_catalog", "copy_spec", "copy_text", "icon_catalog", "icon_spec", "resolve_icon",
    "navigation_catalog", "navigation_for_surface", "navigation_icon_map", "navigation_shortcut_map",
    "TUI_NAV_SHORTCUTS", "TUI_PAGE_ALIASES", "TUI_PAGE_CHOICES", "TUI_PAGES", "assert_tui_shortcuts_collision_free", "normalize_tui_page", "shortcut_rows", "textual_control_center_bindings",
    "load_user_ui_config", "normalize_surface", "profile_catalog", "resolve_ui_preferences", "resolve_user_ui_preferences", "user_ui_config_path", "validate_ui_config",
    "runtime_state_to_ui_snapshot", "token_catalog", "token_for_severity", "token_for_status",
    "capability_catalog", "capabilities_for_surface", "negotiate_capabilities", "resolve_responsive_layout", "responsive_policy_catalog",
    "build_progress_tree", "summarize_progress", "diagnostic_presentations", "summarize_diagnostics",
    "confirmation_catalog", "confirmation_decision", "normalize_confirmation_policy", "notification_catalog", "notification_for_event", "normalize_notification_policy",
]

from .machine import (
    IPC_PROTOCOL_VERSION, MACHINE_SCHEMA_VERSION, TASKER_SCHEMA_VERSION, MachineState,
    ipc_contract, machine_state_from_runtime, tasker_fields,
)
