# Multi-project UI integration

## Shared UI Core

UI01-UI06 add `devtool.ui_core` as the common semantic boundary between runtime facts and frontend rendering. `UISnapshot` normalizes workspace/selection, operation state, progress, diagnostics, actions, navigation, and resolved presentation preferences. `ACTION_REGISTRY` and `NAVIGATION_REGISTRY` are the authoritative sources for stable action/navigation IDs. UI03/UI04 add shared profile/preferences resolution plus semantic token, icon, and copy registries.

The boundary is intentionally not a universal widget toolkit. GTK remains GTK, the TUI remains terminal-native, and Android remains Android-native. Frontends may adapt density and layout, but they consume the same semantic state and do not independently redefine what “cancel”, “retry”, “open log”, or a navigation destination means.

`devtool ui-contract --surface ... --json` exposes the registry contract for debugging and external integrations. Runtime Tasker state and IPC snapshots include an additive `ui` object; HUD snapshots preserve the same object; the Android HUD bridge receives serialized action/navigation metadata; desktop snapshots expose the desktop-resolved semantic state.


### Capability and responsive transport

Every semantic snapshot publishes the selected surface capability matrix, capability negotiation result, and responsive layout intent. Terminal dashboard width handling and GTK navigation consume the shared responsive policy directly. Android/HUD/IPC/Tasker receive the same fields additively so later renderer migrations can consume them without changing runtime semantics.

### Progress and diagnostics transport

`progress` and `diagnostics` remain backward-compatible flat fields. UI06 adds `progress_summary`, `progress_tree`, `diagnostic_summary`, and `diagnostic_presentations`. Workflow metadata (workflow name, node ID, runner, execution environment, dependencies, timing) is preserved by Tasker phase snapshots so cross-process frontends can render a truthful hierarchy. Live phase-failure results now carry structured diagnostics immediately instead of only at terminal completion.

### Presentation transport

Live-state producers resolve all supported surfaces from the merged repository/global UI policy and publish those resolved values additively. IPC/Tasker/HUD/Android consumers therefore do not need their own TOML merge implementation. Existing outer state fields remain compatible.

Desktop legacy appearance/density/motion controls remain supported and are translated into explicit shared desktop overrides; geometry and high-contrast settings remain desktop-local. TUI icon selection honors the central `icons` policy while retaining the legacy Nerd Font environment switches.

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

## UI07/UI08 policy and terminal integration

UI07 adds confirmation/notification policy to the shared snapshot. Tasker/IPC notification payloads preserve old title/text/current/total fields and add semantic attention/delivery/action metadata. Apply notifications use the same policy before posting through Termux, Android bridge, root notification, or `notify-send`.

UI08 migrates TUI and terminal HUD live rendering onto the semantic snapshot. The TUI still owns terminal-native interaction and layout; it does not duplicate runtime state interpretation. Graceful cancellation is sent through the shared IPC helper used by desktop HUD, and terminal clipboard/log/workflow behavior remains a surface adapter over central action IDs.

## Desktop/Android renderer migration

UI09/UI10 complete GTK/Desktop and Android Live HUD migration onto UI Core. Renderers consume semantic actions/navigation, presentation, capabilities/responsive intent, hierarchical progress, structured diagnostics and notification policy. Platform widgets/layout remain native, and legacy machine/bridge fields remain compatibility fallbacks. See `docs/architecture/ui-desktop-android-migration.md`.

## UI11 external semantic contract

Live state keeps the historical outer state schema for compatibility and adds a canonical `machine` object with `schema_version = 2`. Machine v2 contains `workspace`, `selection`, `operation`, `workflow`, `test_selection`, `progress`, `diagnostics`, `actions`, `navigation`, `notifications`, `presentation`, `capabilities`, and `responsive`. IPC advertises protocol version 2 and machine schema version 2 in snapshots/acks while retaining legacy messages. Semantic `action` requests use stable UI Core action IDs; `operation.cancel` interoperates with the older cancel path.

Tasker state includes a structured `tasker = {schema_version, fields}` object and a flat `tasker_fields` compatibility alias. Stable variables include `DEVTOOL_RUN_ID`, `DEVTOOL_OPERATION`, `DEVTOOL_STATUS`, `DEVTOOL_TARGET`, `DEVTOOL_PHASE`, `DEVTOOL_WORKFLOW`, `DEVTOOL_WORKFLOW_NODE`, `DEVTOOL_PROGRESS`, `DEVTOOL_WARNINGS`, `DEVTOOL_ERRORS`, and `DEVTOOL_ACTIONS`. Consumers should prefer these fields over parsing human notification text.

## UI12 completion/config integration

`devtool completion-state --kind ...` is the supported fast machine source for dynamic shell/editor completion. It resolves the repository with Devtool's real config loader rather than reimplementing TOML semantics in the shell. Zsh uses it first and falls back to simple file parsing if Devtool is unavailable during bootstrap. `pytest-tests` intentionally uses the source test catalog so tab completion stays side-effect free.
