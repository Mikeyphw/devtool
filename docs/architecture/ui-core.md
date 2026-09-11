# UI Core architecture

UI01/UI02 establish a shared semantic UI layer for Devtool. The objective is consistency of meaning across TUI, terminal HUD, GTK desktop, Android HUD/notification bridges, IPC, Tasker, and machine clients without forcing those surfaces to share widgets or layouts.

## Boundary

```text
Domain/runtime facts
       |
       v
   UI Core
  /   |   \
state actions navigation
  \   |   /
   semantic snapshot
       |
  +----+-----+---------+---------+
  |          |         |         |
 TUI/HUD    GTK     Android    IPC/Tasker
```

Domain/runtime code owns execution truth. UI Core owns presentation semantics. Surface adapters own rendering.

## Versioned state

`UISnapshot` currently uses schema version 1 and contains:

- workspace and selection identity;
- operation lifecycle, runner, target, environment, timestamps, and log path;
- hierarchical progress rows with status/count/percent metadata;
- normalized diagnostics;
- context-resolved semantic actions;
- surface-resolved navigation entries.

`runtime_state_to_ui_snapshot()` converts existing runtime/Tasker state without deleting legacy fields, allowing incremental frontend migration. The semantic snapshot is therefore additive during the UI roadmap.

## Action registry

`ACTION_REGISTRY` assigns stable IDs such as `operation.cancel`, `operation.retry`, `logs.open`, `validation.resume`, `target.validate`, and `workflow.inspect`. Each action may define long/short/notification labels, semantic icon token, category, intent, destructive state, shortcut, and description.

`actions_for_context()` resolves which actions are meaningful for the current operation state. Surfaces decide how to render an available action; they do not invent a different action meaning.

## Navigation registry

`NAVIGATION_REGISTRY` assigns stable destinations and per-surface routes. Desktop preserves its existing Overview/Runs/History/Artifacts/Diagnostics/Repositories/Targets/Run/Settings structure; the TUI preserves Dashboard/Build/Overlays; Android resolves Overview/Repos/Run/Logs/Settings. Machine consumers can inspect all canonical destinations.

The registry may include routes not visible on every surface. Capability/responsive policy is deliberately deferred to UI05; UI01/UI02 only establish identity and shared metadata.

## Current consumers

- Tasker state publishes `ui` and IPC transports it unchanged.
- Terminal HUD snapshots publish `ui`.
- Desktop HUD storage preserves the semantic snapshot.
- Desktop Control Center state builds a desktop-resolved snapshot.
- GTK navigation/page icons and common operation labels source their semantics from the registries.
- TUI page labels and primary navigation shortcuts source their semantics from the registry.
- The Android HUD bridge receives UI schema/action/navigation JSON as additive bridge extras for later UI10 rendering migration.

## Inspection

```bash
devtool ui-contract --surface machine --json
devtool ui-contract --surface desktop --json
devtool ui-contract --surface tui
devtool ui-contract --surface android --json
```

The command reports the registry schema; it does not require project configuration.

## Compatibility policy

UI Core must remain additive until a consuming frontend has migrated. Existing HUD snapshot versions, IPC fields, Tasker variables, GTK page routes, Android destinations, and TUI shortcuts therefore remain compatible. Schema changes to `UISnapshot` require an explicit schema-version change; registry additions are additive when existing IDs retain meaning.
