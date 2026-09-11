# UI07/UI08 confirmation, notification, and terminal migration report

## Scope

UI07 centralizes confirmation and notification policy in `devtool.ui_core`. UI08 completes the TUI/terminal live-state migration onto the shared semantic UI contract while preserving terminal-native rendering and existing compatibility fields.

## Delivered

- `ui_core.confirmations`: versioned `never|smart|always` policy, smart context evaluation, action-to-policy mapping, prompt/default/reason metadata, and machine catalog.
- `ui_core.notifications`: semantic `passive|info|important|critical|success` attention levels, explicit delivery/bell decisions, action metadata, and machine catalog.
- `[ui.confirmations]` and `[ui.notifications]` validation/defaults in global/repository UI configuration; legacy confirmation booleans remain compatibility shims.
- `UIActionState` now carries confirmation mode, requirement, reason, prompt, and default decision.
- `UISnapshot` publishes confirmation/notification policy plus current semantic notification decisions additively.
- Artifact manifest, snapshot overwrite, and dirty-tree confirmation paths use the shared confirmation engine while retaining existing non-interactive safety errors and `--yes/--force` behavior.
- Apply notifications consult shared delivery/attention/bell policy before Termux/Android/desktop posting.
- Tasker/IPC notification state adds `attention`, `should_notify`, `bell`, semantic token, and action IDs while preserving legacy title/text/progress fields.
- Shared `request_run_cancel()` IPC helper is used by GTK HUD and TUI rather than duplicating protocol logic.
- TUI live cards derive operation status, progress hierarchy, structured diagnostics, semantic icons/copy and actions from `UISnapshot`; existing d/b/o/r/q navigation remains intact.
- TUI action adapters provide confirmation-aware graceful cancellation, log display, clipboard summary/diagnostic copy, workflow inspection, and conservative retry/resume command hints.
- Terminal HUD derives status/copy/icons, progress, diagnostics and actions from UI Core and emits policy-controlled one-shot terminal bells.
- `ui-contract` exposes confirmation and notification schema/catalog/policy.
- Config templates, README, UI contract/integration docs, architecture docs, completion/help text, changelog, and regression contracts were updated.

## Compatibility

- UI schema remains additive and version 1.
- Existing HUD outer schema/state files remain unchanged.
- Existing Tasker/IPC notification fields remain available.
- Existing TUI routes and navigation shortcuts remain unchanged.
- Existing `confirm_destructive`/`confirm_cancel` settings remain supported.
- Existing non-interactive artifact/snapshot/dirty-tree safety behavior remains fail-closed.

## Validation evidence

- UI07/UI08 dedicated contracts: 8 passed.
- UI/config/TUI/HUD/Tasker/IPC/Android/CLI gate: 346 tests + 45 subtests passed.
- MP01-MP10/TS00/runtime gate: 291 tests passed.
- Validation/desktop/UI regression gate: 178 tests passed.
- Non-overlapping focused total: **815 tests + 45 subtests, 0 failures**.
- `python -m compileall -q devtool`: passed.
- `git diff --check`: passed.
- generated/static zsh completion synchronization: passed.
