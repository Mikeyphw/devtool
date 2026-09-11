# UI11 + UI12 implementation report

## Scope delivered

UI11 completes the external semantic-state boundary. Universal machine output remains backward compatible while carrying additive machine schema v2 state for workspace/selection, operation/workflow/test selection, hierarchical progress, structured diagnostics, actions/navigation, notifications, presentation, capabilities and responsive intent. Tasker keeps its legacy outer state while publishing stable schema-v2 semantic fields, and IPC advertises protocol v2 with generic semantic action dispatch while retaining legacy cancel/run interoperability.

UI12 completes discoverability and configuration UX. `completion-state` is the canonical config-aware discovery API for targets, groups, UI profiles, test profiles, variants, jobs, workflows and pytest node selectors; zsh prefers that API and retains the old parser only as fallback. `config resolve --provenance`, `config explain`, and conservative `config migrate --check|--write` expose configuration origin, canonical target identity and safe legacy `kind`/`type` migration with backup-on-write behavior. Completion, manpage, README, configuration/UI integration docs and architecture documentation are updated with the new contracts.

## Compatibility

- Universal machine/API outer envelopes remain schema v1 where existing consumers depend on them; semantic `machine` payloads are independently schema v2.
- Tasker keeps legacy fields/state while adding schema-v2 `machine`, `tasker`, and flat semantic Tasker-variable aliases.
- IPC keeps legacy cancel/run messages; protocol v2 adds metadata and generic semantic `action`/`action_ack` messages.
- `operation.cancel` through generic IPC actions interoperates with the legacy cancellation callback/path.
- Static shell parsing remains as a fallback if `completion-state` cannot be queried.
- Config migration is opt-in. `--check` is non-mutating, `--write` creates a backup, canonical `runner` wins over redundant legacy aliases, and unrelated TOML is preserved.
- Existing UI Core, HUD, TUI, GTK/Desktop and Android semantic contracts remain additive and unchanged.

## Validation contract

The deterministic focused gate is split into two non-overlapping selections to stay below the packaging sandbox command limit: 712 core/UI/external-state tests + 45 subtests and 186 validation/desktop/final-seal tests, for **898 tests + 45 subtests total with zero failures**. Dedicated UI11/UI12 contracts cover machine schema v2, Tasker semantic variables, IPC protocol/action compatibility, config-aware completion, pytest selector discovery and safe config migration. Python compilation, `git diff --check`, and generated/static zsh completion synchronization also pass.

The final overlay embeds the focused TS00 selector set in its validation manifest, so the user can keep validation enabled on the same apply command without retyping individual pytest selectors.
