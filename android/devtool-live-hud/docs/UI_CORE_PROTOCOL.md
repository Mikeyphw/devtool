# UI Core semantic bridge

Devtool Live HUD accepts the original bridge protocol fields plus optional UI Core semantic extras. The bridge transport remains protocol version 1; the UI semantic schema is versioned independently by `ui_schema_version`.

The native app treats the semantic fields as preferred presentation truth and keeps all legacy fields as compatibility fallbacks. It does not parse Devtool log text to discover actions, navigation, progress hierarchy or diagnostic meaning.

## Semantic sections

`ui_operation_json` provides title, status, detail, phase and target context. `ui_progress_summary_json` and `ui_progress_tree_json` provide hierarchical progress. `ui_diagnostics_json` contains structured diagnostic presentations. `ui_actions_json` contains stable action IDs, labels, enabled/destructive/confirmation state and intents. `ui_navigation_json` carries the Android routes. Presentation, capability/responsive, confirmation and notification policy sections carry the same UI Core decisions used by terminal and GTK renderers.

`UiSemanticState` is the single native parser for these fields. `HudUpdate`, notifications and floating HUD rendering consume that model.

## Size and compatibility

The sender transmits semantic sections individually instead of sending a second full snapshot copy. This avoids unnecessary binder/shell argument growth. Unknown/missing semantic fields are ignored and legacy fields remain valid.
