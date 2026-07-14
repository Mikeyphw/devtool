# Multi-project UI integration

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
