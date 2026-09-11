# Shared UI preferences, profiles, tokens, icons, and copy

UI03/UI04 extend `devtool.ui_core` with one presentation policy shared by the terminal/TUI, live HUD, GTK desktop, Android bridge, IPC/Tasker state, and machine clients. The goal is consistent meaning and preference resolution without forcing every frontend to use the same widgets or layout.

## Resolution order

For each surface Devtool resolves UI preferences in this order:

```text
built-in UI defaults
  -> selected profile
  -> built-in surface defaults
  -> merged global/repository [ui]
  -> merged [ui.<surface>]
  -> DEVTOOL_UI_* environment overrides
  -> explicit per-run CLI overrides
```

Repository `[ui]` values override the user-global `[ui]` layer. Other repository build/test settings are unaffected.

Supported surfaces are `machine`, `desktop`, `tui`, `hud`, and `android`. Aliases such as `gtk` and `terminal` normalize to the canonical surface names.

## Built-in profiles

`profile = "auto"` resolves to `standard`. Built-in named profiles are:

- `minimal`: dense/compact presentation, hides completed steps, minimizes diagnostics and command/environment detail.
- `standard`: normal defaults with surface-specific density.
- `developer`: detailed presentation with environment context always visible.
- `debug`: debug verbosity, absolute timestamps, precise durations, commands/environment always visible, and all diagnostics expanded.

Custom profiles inherit another profile:

```toml
[ui]
profile = "focused"

[ui.profiles.focused]
extends = "developer"
density = "compact"
show_completed_steps = false
max_steps = 5
```

Inheritance cycles and unknown/invalid preference values fail during configuration loading rather than being silently ignored.

## Global and repository configuration

A user-global example:

```toml
[ui]
profile = "developer"
theme = "auto"
density = "comfortable"
icons = "auto"
motion = "auto"
verbosity = "normal"
timestamps = "relative"
duration_format = "human"
show_completed_steps = true
show_environment = "when-relevant"
show_commands = "on-error"
show_diagnostics = "expanded-on-error"
confirm_destructive = true
confirm_cancel = false

[ui.tui]
density = "compact"
sidebar = "auto"
mouse = true

[ui.hud]
density = "dense"
max_steps = 6

[ui.desktop]
sidebar = "expanded"

[ui.android]
density = "compact"
notification_detail = "normal"
```

A repository may override only what it needs:

```toml
[ui]
profile = "developer"

[ui.desktop]
theme = "dark"

[ui.hud]
max_steps = 8
```

## Per-run overrides

The common CLI overrides are:

```bash
devtool --ui-profile debug validate
devtool --ui-theme dark build
devtool --ui-density compact test
devtool --ui-icons ascii validate
devtool --ui-motion reduced run
devtool --ui-verbosity detailed validate
```

The corresponding environment overrides use the `DEVTOOL_UI_*` prefix. Additional environment preferences include timestamps, duration formatting, command/environment/diagnostic visibility, sidebar mode, notification detail, max steps, confirmations, completed-step visibility, and mouse support.

## Semantic tokens

UI04 defines renderer-independent tokens such as:

```text
status.success
status.running
status.pending
status.failed
status.warning
status.skipped
status.cancelled
status.blocked

severity.info
severity.warning
severity.error

action.primary
action.secondary
action.destructive

text.primary
text.secondary
text.muted
surface.default
surface.raised
```

Tokens express meaning, not literal colors. GTK, terminal, and Android renderers remain responsible for mapping a token into native visual treatment.

`UISnapshot.operation`, progress rows, diagnostics, and action states carry these semantic token IDs so machine clients never need to infer severity from display strings or colors.

## Icon registry and fallbacks

The icon registry maps one semantic icon ID to multiple renderable forms:

- Nerd Font glyph;
- ordinary Unicode;
- ASCII-safe fallback;
- native/symbolic frontend icon name where available.

`icons = "auto"` chooses conservatively from the environment. Explicit modes are `nerd`, `unicode`, `ascii`, and `native`.

Examples:

```text
status.success -> Nerd Font / ✓ / [OK]
status.failed  -> Nerd Font / ✗ / [ERR]
nav.settings   -> Nerd Font / ⚙ / [SET] / emblem-system-symbolic
```

The existing `DEVTOOL_NERD_FONT`, `DEVTOOL_NO_NERD_FONT`, and ASCII safety controls remain compatible. `DEVTOOL_UI_ICONS` is the shared higher-level preference.

## Shared copy and terminology

Action, navigation, and common status wording live in one copy registry. Stable machine IDs never depend on the displayed wording:

```text
operation.cancel -> "Cancel operation" / "Cancel" / notification "Stop"
nav.dashboard    -> desktop "Overview" / TUI "Dashboard"
status.success   -> general "Succeeded" / HUD "Complete"
```

This allows native surfaces to use deliberately shorter wording without redefining the action or status itself.

## Cross-process presentation

Live Tasker/IPC state now carries resolved per-surface presentation values in addition to the existing semantic `ui` snapshot. A separately running HUD or Android bridge therefore receives the same global/repository/profile result as the command that started the run. Raw repository UI configuration does not need to be reparsed by the renderer.

The outer HUD/Tasker schemas remain backward compatible; the presentation data is additive.

## Inspection

Use `ui-contract` to inspect the shared registries and effective global presentation:

```bash
devtool ui-contract --surface desktop --json
devtool --ui-profile debug --ui-icons ascii ui-contract --surface hud --json
```

The JSON contains schema versions, resolved preferences with provenance, profile catalog, semantic tokens, icon registry, copy registry, action registry, and navigation registry.

For repository-merged UI values, use resolved configuration inspection:

```bash
devtool config resolved --json
```

The resolved config contains the merged `[ui]` configuration and effective presentation for every supported surface.
