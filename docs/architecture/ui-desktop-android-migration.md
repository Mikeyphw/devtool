# UI09/UI10 — Desktop and Android UI Core migration

UI09 and UI10 complete the renderer migrations that began with UI01. The key rule remains:

> Devtool Core produces runtime facts; UI Core produces semantic presentation; GTK and Android render those semantics natively.

## GTK/Desktop

`devtool.desktop.ui_semantics` is the renderer boundary for GTK. It converts the versioned `UISnapshot` into a deterministic desktop view containing:

- operation identity/status/detail,
- resolved presentation and responsive mode,
- supported capabilities,
- navigation routes,
- enabled semantic actions and confirmation metadata,
- hierarchical progress,
- structured diagnostic presentations,
- semantic notifications.

`DesktopControlCenterModel` publishes both the original `ui` snapshot and the normalized `desktop_ui` view. The GTK Control Center uses the semantic view for overview decisions, diagnostics and settings inspection. GTK HUD prefers semantic operation/progress/diagnostic/action state and falls back to the legacy HUD fields when reading older state files.

No GTK widget implementation lives in UI Core. GTK keeps responsibility for widgets, CSS, window sizing, clipboard/open-file behavior and native dialogs.

## Android Live HUD

The Termux bridge now sends additive UI Core extras alongside the established bridge-v1 fields:

- `ui_operation_json`
- `ui_presentation_json`
- `ui_capabilities_json`
- `ui_capability_negotiation_json`
- `ui_responsive_json`
- `ui_progress_summary_json`
- `ui_progress_tree_json`
- `ui_diagnostic_summary_json`
- `ui_diagnostics_json`
- `ui_actions_json`
- `ui_navigation_json`
- `ui_confirmation_policy_json`
- `ui_notification_policy_json`
- `ui_notifications_json`

The payload intentionally does **not** duplicate a complete snapshot JSON blob. This keeps `am broadcast` arguments smaller while retaining each stable semantic section.

The native `UiSemanticState` parser consumes the extras. `HudUpdate` prefers semantic operation title/detail/phase/progress and semantic progress-tree steps. `NotificationController` prefers enabled semantic action IDs when it can map them to native `PendingIntent` actions. `OverlayHudService` consumes structured diagnostics. Legacy fields remain fallback truth for older bridge producers.

## Compatibility

The Android bridge protocol remains version 1. UI09/UI10 are additive schema evolution, not a transport break. A new sender can talk to an older companion using the old fields, and a new companion can render an older sender through fallback state.

The outer HUD state schema, Tasker fields, IPC envelopes, GTK routes and Android bridge actions are unchanged.
