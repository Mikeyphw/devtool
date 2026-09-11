# UI11/UI12 — Machine Contract, IPC/Tasker, Completion and Config UX

## Machine schema v2

Devtool preserves historical outer machine/HUD/Tasker envelopes and adds a canonical `machine` object. Schema v2 is renderer-neutral and contains:

- workspace and current selection;
- operation identity/lifecycle;
- current workflow/node and effective target/runner/environment;
- explicit test selection;
- progress summary/tree/nodes;
- diagnostic summary/presentations/items;
- semantic actions and navigation;
- notification decisions;
- resolved presentation, capabilities, and responsive intent.

Normal command machine summaries, live Tasker state, and IPC run snapshots all expose this contract.

## IPC protocol v2

The Unix-socket broker announces `protocol_version = 2` and `machine_schema_version = 2`. Existing `subscribe`, `register`, `publish`, `cancel`, `remove_run`, and `ping` messages remain valid. UI11 adds generic semantic `action` / `action_ack` messages keyed by UI Core action ID. `operation.cancel` remains compatible with the existing producer cancellation callback.

## Tasker contract v2

The existing state file is retained. Additive fields are:

```json
{
  "machine": {"schema_version": 2},
  "tasker": {"schema_version": 2, "fields": {}},
  "tasker_fields": {}
}
```

Tasker automations should read stable `DEVTOOL_*` values instead of parsing notification titles/bodies.

## Completion-state API

Zsh completion queries Devtool itself:

```sh
devtool completion-state --kind targets
devtool completion-state --kind groups
devtool completion-state --kind ui-profiles
devtool completion-state --kind pytest-tests
```

The static completion keeps lightweight TOML parsing as a fallback for bootstrap situations where Devtool cannot be invoked.

## Config migration

`config migrate --check` is non-mutating. `--write` creates a backup and performs only safe alias migration. Canonical `runner` remains authoritative; `platform` is a separate product identity and is materialized explicitly only when absent.
