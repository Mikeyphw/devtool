# UI Core architecture

UI01-UI06 establish a shared semantic UI layer for Devtool. The objective is consistency of meaning across TUI, terminal HUD, GTK desktop, Android HUD/notification bridges, IPC, Tasker, and machine clients without forcing those surfaces to share widgets or layouts.

## Boundary

```text
Domain/runtime facts
       |
       v
   UI Core
  /   |   \
state actions navigation
preferences tokens icons copy
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
- surface-resolved navigation entries;
- resolved presentation preferences and provenance;
- semantic status/severity/action tokens;
- frontend capability negotiation and declared fallbacks;
- renderer-neutral responsive layout intent;
- a canonical hierarchical progress tree plus aggregate progress summary;
- structured diagnostic presentations with what/why/where/next-action/details sections.

`runtime_state_to_ui_snapshot()` converts existing runtime/Tasker state without deleting legacy fields, allowing incremental frontend migration. The semantic snapshot is therefore additive during the UI roadmap.


## Presentation preferences and profiles

UI03 resolves one renderer-independent presentation policy for `machine`, `desktop`, `tui`, `hud`, and `android`. Resolution is layered: built-in defaults, selected profile, built-in surface defaults, merged global/repository `[ui]`, surface-specific `[ui.<surface>]`, environment overrides, then explicit CLI overrides. Built-ins are `minimal`, `standard`, `developer`, and `debug`; custom profiles may inherit other profiles and are cycle-checked.

Resolved presentation is stored in `UISnapshot.presentation`. Live state also transports resolved per-surface presentation so a separate HUD/Android process sees repository overrides without reparsing raw project configuration.

## Semantic tokens, icons, and copy

UI04 centralizes status/severity/action/text tokens, semantic icon identities with Nerd Font/Unicode/ASCII/native fallbacks, and reusable action/navigation/status copy. Stable machine IDs remain independent from displayed wording. Renderers map semantic tokens into their native colors/widgets rather than UI Core prescribing literal CSS or Android resources.

## Action registry

`ACTION_REGISTRY` assigns stable IDs such as `operation.cancel`, `operation.retry`, `logs.open`, `validation.resume`, `target.validate`, and `workflow.inspect`. Each action may define long/short/notification labels, semantic icon token, category, intent, destructive state, shortcut, and description.

`actions_for_context()` resolves which actions are meaningful for the current operation state. Surfaces decide how to render an available action; they do not invent a different action meaning.

## Navigation registry

`NAVIGATION_REGISTRY` assigns stable destinations and per-surface routes. Desktop preserves its existing Overview/Runs/History/Artifacts/Diagnostics/Repositories/Targets/Run/Settings structure; the TUI preserves Dashboard/Build/Overlays; Android resolves Overview/Repos/Run/Logs/Settings. Machine consumers can inspect all canonical destinations.

The registry may include routes not visible on every surface. UI05 adds a capability matrix for `machine`, `desktop`, `tui`, `hud`, and `android`, together with explicit fallback semantics when a surface cannot satisfy a requested interaction. Renderers may negotiate capabilities without branching domain code on frontend names.

## Capability negotiation and responsive intent

`capabilities_for_surface()` reports potential frontend capabilities such as interactivity, tree/table rendering, notifications, dialogs, keyboard/touch/mouse input, clipboard, native icons, resize events, and multi-pane layout. `negotiate_capabilities()` reports unsupported requirements together with the canonical fallback (`tree_view -> indented flat list`, `dialogs -> inline confirmation`, and so on).

`resolve_responsive_layout()` turns surface + viewport + presentation preferences into renderer-neutral layout intent. The current policy uses single/two/three-pane modes for TUI, desktop, and Android; HUD remains at most two-pane; machine output remains semantic-only. Frontends remain responsible for their actual widgets and exact breakpoints are versioned by the responsive policy.

The terminal dashboard and GTK navigation now consume this policy directly; live snapshots publish it for HUD/Android/IPC consumers.

## Hierarchical progress and diagnostic presentation

UI06 keeps the existing flat `progress` list for compatibility, but also publishes `progress_summary` and `progress_tree`. The tree normalizes the canonical hierarchy `Operation -> Workflow -> Target -> Phase/Step`, preserves explicit parent links, aggregates percent/state for synthetic workflow/target nodes, and carries runner/environment/timing/issue/test metrics.

Diagnostics keep legacy severity/message/code/file fields and add stable IDs, cause, normalized location, related workflow node, semantic next-action IDs, technical details, and evidence. `diagnostic_presentations` renders the canonical sections **What happened**, **Why**, **Where**, **What you can do**, and **Technical details** without forcing any renderer layout. `diagnostic_summary` provides blocking/error/warning/info counts and a stable primary diagnostic ID.

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
devtool ui-contract --surface tui --width 70
devtool ui-contract --surface android --width 420 --json
devtool ui-contract --surface machine --require-capability dialogs --require-capability tree_view --json
```

The command reports resolved global presentation plus capability/responsive/progress/diagnostic contracts and profile/token/icon/copy/action/navigation registries; it does not require project configuration. Repository-merged presentation is available through resolved config and live semantic snapshots.

## Compatibility policy

UI Core must remain additive until a consuming frontend has migrated. Existing HUD snapshot versions, IPC fields, Tasker variables, GTK page routes, Android destinations, and TUI shortcuts therefore remain compatible. Schema changes to `UISnapshot` require an explicit schema-version change; registry additions are additive when existing IDs retain meaning.
