# UI03 + UI04 implementation report

## Scope

UI03/UI04 complete the next shared-UI roadmap slice on top of UI01/UI02. The implementation adds one central preference/profile resolver and one semantic presentation vocabulary for all Devtool frontends without replacing native GTK, terminal, or Android rendering.

## UI03 — centralized preferences and profiles

Implemented `devtool.ui_core.preferences` with:

- built-in `minimal`, `standard`, `developer`, and `debug` profiles;
- `profile = "auto"` compatibility resolving to `standard`;
- custom `[ui.profiles.<name>]` profiles with `extends` inheritance;
- inheritance-cycle detection and strict value/type validation;
- canonical `machine`, `desktop`, `tui`, `hud`, and `android` surfaces;
- surface aliases for GTK/terminal/live-HUD callers;
- per-surface defaults;
- provenance for every resolved field;
- global user config loading with cache invalidation by mtime/size;
- environment and explicit override layers.

Resolution order is:

```text
built-in defaults
selected profile
surface defaults
merged global/repository [ui]
[ui.<surface>]
DEVTOOL_UI_* environment overrides
explicit CLI overrides
```

Supported configuration includes theme, density, icon mode, motion, verbosity, timestamps, duration formatting, destructive/cancel confirmation preferences, completed-step visibility, environment/command/diagnostic visibility, sidebar state, mouse support, HUD maximum steps, and Android notification detail.

Repository `[ui]` deep-merges over user-global `[ui]`. `config resolve` exposes both raw merged UI config and all five resolved surface presentations. `config doctor --global` validates UI config and reports the effective profile/density/icon mode.

Added per-run CLI overrides:

- `--ui-profile`
- `--ui-theme`
- `--ui-density`
- `--ui-icons`
- `--ui-motion`
- `--ui-verbosity`

The global and repository config templates document the shared UI layer and custom-profile syntax.

## UI04 — semantic tokens, icons, and copy

Implemented three UI Core registries:

- `tokens.py`: status, severity, action, text, and surface semantic tokens;
- `icons.py`: semantic icon IDs with Nerd Font, Unicode, ASCII, and native/symbolic variants;
- `copy.py`: shared action/navigation/status wording with long, short, notification, and surface-specific variants.

Stable UI01/UI02 machine IDs are preserved. For example, navigation remains `dashboard`, `workflows`, etc.; `nav.dashboard` is only the copy/icon lookup key. Existing IPC, Tasker, Android, TUI, and GTK clients therefore do not need an ID migration.

Action definitions now source labels and descriptions from the central copy registry. Navigation definitions source copy and icon identity centrally. `UISnapshot` operation/progress/diagnostic/action state carries semantic token IDs while retaining schema version 1 for compatibility.

## Frontend/runtime integration

The shared presentation policy is consumed rather than merely declared:

- TUI terminal profiles read shared density and TUI icon selection honors the shared icon mode while preserving legacy Nerd Font environment controls.
- HUD status wording uses central status copy and event icons use the semantic icon registry.
- GTK Control Center resolves central theme/density/motion, preserves legacy desktop-local preference compatibility, and shows the effective shared profile/presentation in Settings.
- Desktop semantic snapshots use the merged repository UI configuration.
- Tasker/IPC live state carries resolved per-surface presentations so separately running HUD/Android consumers retain repository/profile choices across process boundaries.
- HUD and Android semantic snapshots rehydrate transported resolved preferences rather than reparsing raw configuration.
- Android HUD bridge extras include token/icon/copy schema versions and serialized presentation metadata.

Existing outer HUD schema version 5, UI snapshot schema version 1, IPC/Tasker legacy fields, GTK routes, TUI shortcuts, and Android destinations remain compatible.

## Inspection and completion

`ui-contract|uic` now reports:

- UI schema version;
- token/icon/copy schema versions;
- resolved surface preferences with provenance;
- built-in and custom profile catalog;
- semantic token registry;
- icon registry;
- copy registry;
- action registry;
- navigation registry.

Zsh completion includes all common UI flags and dynamically discovers custom `[ui.profiles.*]` names from both the global config and repository config.

## Documentation

Updated/added:

- `README.md`
- `CHANGELOG.md`
- `docs/CONFIGURATION.md`
- `docs/UI_PREFERENCES_AND_TOKENS.md`
- `docs/UI_UX_CONTRACT.md`
- `docs/UI_INTEGRATION.md`
- `docs/TUI_NERD_FONT_ICONS.md`
- `docs/architecture/ui-core.md`
- `docs/architecture/overview.md`
- `share/man/man1/devtool.1`

## Validation evidence

Focused UI/config/CLI integration gate:

```text
359 tests passed
45 subtests passed
0 failures
```

Full TS00 + MP01-MP10 + UI01-UI04 focused overlay gate:

```text
797 tests passed
45 subtests passed
0 failures
```

Dedicated UI03/UI04 suite after completion additions:

```text
15 tests passed
```

Additional static checks:

- all changed Python modules compile;
- `git diff --check` passes;
- generated zsh completion exactly matches `completions/_devtool`;
- UI snapshot schema remains version 1;
- UI01/UI02 action/navigation machine IDs remain stable.

## Roadmap boundary

UI03/UI04 centralize presentation preferences and semantic vocabulary. UI05/UI06 remain responsible for capability negotiation/responsive policy and the richer shared progress/diagnostic presentation model. No renderer-specific responsive behavior is prematurely moved into this overlay.
