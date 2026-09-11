# Multi-project UI integration

## Shared UI Core

UI01/UI02 add `devtool.ui_core` as the common semantic boundary between runtime facts and frontend rendering. `UISnapshot` normalizes workspace/selection, operation state, progress, diagnostics, actions, and navigation. `ACTION_REGISTRY` and `NAVIGATION_REGISTRY` are the authoritative sources for stable action/navigation IDs and presentation metadata.

The boundary is intentionally not a universal widget toolkit. GTK remains GTK, the TUI remains terminal-native, and Android remains Android-native. Frontends may adapt density and layout, but they consume the same semantic state and do not independently redefine what “cancel”, “retry”, “open log”, or a navigation destination means.

`devtool ui-contract --surface ... --json` exposes the registry contract for debugging and external integrations. Runtime Tasker state and IPC snapshots include an additive `ui` object; HUD snapshots preserve the same object; the Android HUD bridge receives serialized action/navigation metadata; desktop snapshots expose the desktop-resolved semantic state.

Devtool 1.0 exposes the runner, execution environment, validation profile, and
selective-test model in both native frontends. The frontends build the same CLI
command that is shown in the preview; they do not maintain a second execution
pipeline.

## Android frontend

The Run screen supports:

- project types: auto, .NET, Android, Gradle/JVM, Python, and custom command;
- execution environments: auto, native Termux, chroot, desktop Linux, Android
  device, and external device;
- validation/test profiles;
- selectors, tags, excluded tags, source path, class, and method filters;
- test discovery and Android instrumentation toggles;
- a generated command preview before execution;
- foreground Termux execution and persistent background-run logging.

Every field is persisted by `AppSettings` and is also understood by the command
importer, so a copied CLI command can repopulate the editor.

## Desktop Control Center

The Run page exposes the same common target and test-selection settings. The
command preview is produced by `AppController::buildWorkflowCommand`, and the
same method feeds `runWorkflow`, preventing preview/execution drift.

The desktop frontend keeps project-specific configuration in `.devtool.toml`;
its UI supplies per-run overrides only. An explicit UI choice therefore has the
same precedence as the corresponding CLI flag.

## Progress contract

Both frontends consume normalized devtool events. Overall pipeline progress,
local runner progress, and test progress remain separate values. Events carry a
run ID and increasing sequence number so output from an older run cannot replace
the current notification or HUD state.
