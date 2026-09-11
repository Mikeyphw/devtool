# UI01 + UI02 — Shared Semantic UI State and Central Action/Navigation Registries

## Scope

This overlay implements UI01 and UI02 from the Devtool modernization roadmap on top of the completed MP01–MP10 workspace/runtime architecture.

UI01 establishes a renderer-independent semantic UI state layer. UI02 establishes one authoritative action registry and one authoritative navigation registry, then migrates existing terminal, desktop, Android-bridge, Tasker, IPC, and HUD producers/consumers far enough that the new contracts are live infrastructure rather than unused abstractions.

## Shared semantic state

`devtool.ui_core.state` introduces UI schema version 1 and typed semantic state for:

- workspace identity and selected targets/groups;
- current selection/variant/operation;
- operation lifecycle, phase, runner, execution environment, timestamps, and log path;
- hierarchical progress rows with status/count/percent metadata;
- normalized diagnostics;
- available semantic actions;
- surface-resolved navigation.

`UISnapshot.as_dict()` is the common serializable representation. The UI schema is versioned independently from existing HUD/IPC envelopes, so UI Core can evolve without forcing unrelated transport-version changes.

`runtime_state_to_ui_snapshot()` adapts existing live state into the common model while preserving all legacy fields. This is deliberately additive during migration.

## Central action registry

`devtool.ui_core.actions` defines stable action IDs and their shared presentation semantics. The initial registry covers:

- operation cancel/retry/rerun;
- log/summary/diagnostic actions;
- artifact apply/rollback;
- validation retry/resume;
- target build/test/validate/package;
- workflow inspect/retry-node;
- test selection/clear/refresh.

Each action owns its long, short, and notification labels, semantic icon token, category, intent, destructive status, optional shortcut, and description. `ActionContext` plus `actions_for_context()` resolves the actions that are meaningful for a runtime state.

This keeps action *meaning* centralized while leaving actual buttons, menus, notifications, keyboard handling, and layout native to each frontend.

## Central navigation registry

`devtool.ui_core.navigation` defines stable navigation IDs and surface routes for desktop, TUI, Android, HUD/machine consumers. The existing frontend information architecture is preserved:

- desktop: Overview, Active runs, History, Artifacts, Diagnostics, Repository, Targets, Run, Settings;
- TUI: Dashboard, Build, Overlays with the existing d/b/o shortcuts;
- Android: Overview, Repos, Run, Logs, Settings;
- machine consumers: the broader canonical destination set including workflows and environment.

GTK page order/routes and TUI shortcuts therefore retain compatibility while no longer being independently hard-coded sources of truth.

## Frontend/runtime integration

### Tasker and IPC

`TaskerProgressSink` publishes an additive `ui` snapshot in every authoritative state. The same state is published through the existing IPC path, so IPC consumers receive the semantic contract without a second transport implementation.

### Terminal HUD

HUD snapshot schema version 5 remains unchanged for compatibility. An additive top-level `ui` field carries UI schema version 1 state. Keeping the transport and semantic schema versions independent prevents unnecessary client breakage.

### Android HUD bridge

The Android app bridge receives additive `ui_schema_version`, `ui_actions_json`, and `ui_navigation_json` fields derived once from the Android-resolved semantic snapshot. Existing Android bridge fields remain untouched; later UI10 work can consume these semantics without scraping text.

### Desktop HUD and Control Center

Desktop HUD storage preserves the semantic snapshot. `DesktopControlCenterSnapshot` exposes a desktop-resolved UI snapshot enriched with configured workspace targets and selection. GTK Control Center page keys/icons and common Run/HUD action labels now source their semantics from the registries while preserving the existing GTK layout.

### TUI

The terminal UI sources primary page labels and d/b/o navigation shortcuts from the navigation registry while preserving its existing pages and local refresh/quit controls.

## CLI inspection

`devtool ui-contract` / `devtool uic` exposes the semantic registry contract without needing project configuration:

```bash
devtool ui-contract --surface machine --json
devtool ui-contract --surface desktop --json
devtool ui-contract --surface tui
devtool ui-contract --surface android --json
```

The command is covered by zsh completion and the manpage.

## Documentation

Updated:

- `README.md`
- `CHANGELOG.md`
- `docs/UI_UX_CONTRACT.md`
- `docs/UI_INTEGRATION.md`
- `docs/architecture/overview.md`
- `docs/architecture/core-runtime.md`
- `share/man/man1/devtool.1`
- generated/static zsh completion

Added:

- `docs/architecture/ui-core.md`
- this implementation report

## Compatibility guarantees

This overlay intentionally does **not** introduce UI03 preferences/profiles, UI04 tokens/copy system, UI05 capability negotiation, or later renderer migrations. Those remain separate roadmap slices.

Compatibility preserved in UI01/UI02:

- HUD outer snapshot stays version 5;
- legacy Tasker/IPC state fields remain present;
- desktop route order and route names remain unchanged;
- TUI Dashboard/Build/Overlays labels and d/b/o navigation remain unchanged;
- Android Overview/Repos/Run/Logs/Settings destinations remain unchanged;
- frontend-native layout/widget code remains frontend-owned.

## Validation evidence

Dedicated UI01/UI02 contracts validate:

- registry ID uniqueness and catalog stability;
- context-aware action resolution;
- desktop and TUI navigation compatibility;
- runtime-state conversion and JSON serializability;
- Tasker semantic-state publication;
- HUD semantic-state publication;
- desktop Control Center semantic state;
- Android destination parity and bridge payload metadata;
- TUI shortcut centralization;
- `ui-contract` CLI machine schema.

Focused cross-surface UI regression gate: **342 tests + 45 subtests passed**.

Full selected overlay regression gate spanning UI01/UI02, TS00, MP01–MP10, CLI/config, completion/manpage, runners/environments, validation sessions, TUI/HUD, Android bridge, IPC/Tasker, GTK desktop, and existing UI/UX contracts: **773 tests + 45 subtests passed**.

Additional gates:

- `python -m compileall -q devtool`: passed;
- `git diff --check`: passed;
- generated/static zsh completion: byte-synchronized.
