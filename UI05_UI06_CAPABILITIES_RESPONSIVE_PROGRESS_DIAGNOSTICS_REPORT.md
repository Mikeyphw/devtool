# UI05 + UI06 — Capability Negotiation, Responsive Policy, Progress and Diagnostics

## Scope

UI05/UI06 extend the UI01-UI04 semantic UI Core with frontend capability negotiation, renderer-neutral responsive layout intent, a canonical hierarchical progress model, and structured diagnostic/error presentation. The overlay remains additive: existing `progress`, `diagnostics`, HUD v5, Tasker/IPC fields, GTK routes, TUI navigation, and Android bridge fields remain available.

## UI05 — frontend capability negotiation

Added `devtool.ui_core.capabilities` with a versioned capability matrix for:

- `machine`
- `desktop`
- `tui`
- `hud`
- `android`

Capabilities cover interactivity, rich text, tables, tree views, notifications, persistent navigation, dialogs, hyperlinks, keyboard/touch/mouse input, clipboard, native icons, resize events, and multi-pane layout.

`negotiate_capabilities()` returns supported and missing capabilities plus named fallbacks such as:

- tree view -> indented flat list;
- dialogs -> inline confirmation;
- notifications -> status event;
- native icons -> semantic Unicode/ASCII icon;
- multi-pane -> single-pane drill-down.

## UI05 — responsive policy

Added `devtool.ui_core.responsive` with versioned layout intent. The policy resolves surface + viewport + presentation preferences into semantic layout state without prescribing native widgets.

- TUI: single/two/three-pane intent for narrow/medium/wide terminals.
- Desktop: stacked/two/three-pane intent.
- Android: phone/two-pane/tablet intent.
- HUD: compact one/two-pane intent.
- Machine: semantic-only layout.

The terminal dashboard now consumes this policy for its `compact`/`narrow`/`wide` profile. GTK Control Center navigation uses the same desktop policy instead of a separate hard-coded width rule, and Settings exposes the resolved layout/capabilities.

## UI06 — hierarchical progress

The compatibility flat `progress` list remains intact. UI06 adds:

- `progress_summary` with total/completed/running/pending/failed/blocked/skipped/cancelled counts, overall percent, and active IDs;
- `progress_tree` with canonical `Operation -> Workflow -> Target -> Phase/Step` hierarchy;
- explicit parent-link preservation;
- runner, execution environment, timing, dependency, issue-count, and test/parser metrics;
- aggregate synthetic workflow/target status and percent.

Tasker phase state now preserves workflow name, workflow node ID, runner, execution environment, dependencies, parent IDs, and timing so separately running HUD/Desktop/Android consumers can build the same hierarchy without reparsing logs.

## UI06 — diagnostics and error UX

The compatibility diagnostic fields remain intact. Diagnostics now also carry:

- stable IDs;
- normalized summary/cause/location;
- line/column;
- related workflow node;
- semantic next-action IDs;
- technical details;
- evidence metadata.

UI snapshots add:

- `diagnostic_summary` with blocking/error/warning/info totals and primary ID;
- `diagnostic_presentations` with renderer-neutral **What happened**, **Why**, **Where**, **What you can do**, and **Technical details** sections.

Live `phase_finished` results now publish structured diagnostics immediately instead of waiting for terminal operation completion.

## Cross-surface publication

The enriched semantic snapshot flows through existing Tasker/IPC/HUD state. Android bridge extras now include capability/responsive/progress/diagnostic schema versions plus serialized capability negotiation, responsive intent, progress summary/tree, and diagnostic summary/presentations.

`ui-contract|uic` now supports:

```bash
devtool ui-contract --surface tui --width 55 --height 24 --json
devtool ui-contract --surface machine --require-capability dialogs --require-capability tree_view --json
```

It reports capability/responsive/progress/diagnostic schema versions and contracts alongside the UI01-UI04 preferences/tokens/icons/copy/actions/navigation contract.

## Compatibility

Preserved:

- `UISnapshot.schema_version == 1` because all changes are additive;
- HUD outer snapshot version 5;
- existing `progress` and `diagnostics` arrays;
- Tasker/IPC legacy state fields;
- UI01/UI02 stable action/navigation IDs;
- UI03/UI04 profile/token/icon/copy semantics;
- existing Android destinations and GTK/TUI navigation identities.

## Validation evidence

Dedicated UI05/UI06 contracts:

```text
9 tests passed
0 failures
```

Focused compatibility/UI gate:

```text
170 tests passed
37 subtests passed
0 failures
```

Full TS00 + MP01-MP10 + UI01-UI06 focused overlay gate:

```text
806 tests passed
45 subtests passed
0 failures
```

Static checks:

- all Devtool Python modules compile;
- `git diff --check` passes;
- generated zsh completion exactly matches `completions/_devtool`;
- the UI snapshot remains additive and schema-version compatible.

## Documentation

Updated/added:

- `README.md`
- `CHANGELOG.md`
- `docs/UI_UX_CONTRACT.md`
- `docs/UI_INTEGRATION.md`
- `docs/architecture/ui-core.md`
- `docs/architecture/ui-capabilities-progress.md`
- `docs/architecture/overview.md`
- `share/man/man1/devtool.1`

## Roadmap boundary

UI05/UI06 own capability negotiation, responsive intent, and the shared progress/diagnostic presentation contract. UI07 remains responsible for centralized confirmation/notification policy. UI08+ remain responsible for deeper renderer-specific migrations while consuming these shared semantics.
