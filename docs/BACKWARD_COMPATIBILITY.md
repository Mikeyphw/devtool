# Backward compatibility contract

Devtool 1.1 keeps compatibility deliberately, not accidentally. New code and new configuration should use the canonical interfaces in this document. Legacy interfaces listed here remain accepted until a future major-version migration explicitly removes them.

Use these commands to inspect a repository:

```sh
devtool compatibility
devtool compatibility --json
devtool compatibility --active-only
devtool compatibility --strict
```

`--strict` exits non-zero only when the selected config still contains a migratable legacy setting. It does **not** fail merely because Devtool still ships compatibility aliases for older clients.

## Configuration aliases

| Legacy interface | Canonical interface | Current contract | Migration |
|---|---|---|---|
| `[project].kind` | `[project].runner + platform` | supported alias | `devtool config migrate --write` |
| `[targets.*].kind` | `[targets.*].runner + platform` | supported alias | `devtool config migrate --write` |
| `[targets.*].type` | `[targets.*].runner + platform` | supported alias | `devtool config migrate --write` |
| `[project].default_target` | `[workspace].default_target` | supported alias | `devtool config migrate --write` |
| `[ui].confirm_destructive` | `[ui.confirmations]` | supported shim | `devtool config migrate --write [--global]` |
| `[ui].confirm_cancel` | `[ui.confirmations].cancel` | supported shim | `devtool config migrate --write [--global]` |

`config migrate --check` operates on raw TOML, so it can report contradictory legacy/canonical values even when normal config loading would reject the file. `--write` refuses unsafe reconciliation. Safe writes create a `.bak` file first.

The legacy UI booleans map exactly to their old behavior. `confirm_destructive = false` becomes `rollback = "never"`, `delete_artifact = "never"`, and `snapshot_overwrite = "never"`. `confirm_cancel = true` becomes `cancel = "always"`. The default/false forms can be removed because the canonical policy defaults already preserve their behavior.

## CLI aliases

`--runner` is the canonical one-run runner override. `--project-type` remains a supported compatibility alias with the same destination and accepted values.

`DEVTOOL_PROJECT_RUNNER` is the canonical environment override. `DEVTOOL_PROJECT_KIND` remains accepted for older automation; conflicting runner/kind values are rejected rather than ordered implicitly.

`hud --mode gtk-window` remains a deprecated alias for `hud --mode desktop-window` and prints a migration hint. Removed Qt HUD mode names continue to fail with an actionable GTK4 migration message instead of silently selecting a different renderer.

## Python API

`devtool.models.TargetConfig` is the canonical target model. `devtool.models.DotnetTarget` remains a public alias for callers and plugins written before the multi-runner target model.

## Machine, IPC, Tasker, and Android

Machine schema v2 is the canonical automation model. Existing outer machine/HUD state fields remain additive fallbacks so older consumers can continue reading the same files.

IPC protocol v2 adds semantic `action` / `action_ack` messages. Legacy `cancel` / `cancel_ack` remain supported and map to `operation.cancel`.

Tasker should prefer the structured `tasker.fields` / stable `DEVTOOL_*` values. The flat `tasker_fields` alias and existing notification-facing fields remain available.

The Android bridge intentionally remains protocol v1 because UI Core is carried as additive semantic extras. New companions prefer those semantic sections; older companions may keep using task/phase/status/progress extras.

## Workflow syntax

Ordered string workflow lists remain supported and are still the preferred concise form for a simple sequential workflow. Explicit DAG step tables are required only when branches, joins, or named dependencies are needed.

## What is not compatibility

Compatibility does not mean silently accepting ambiguity. Devtool still rejects conflicting canonical and legacy runner values, conflicting workspace defaults, invalid target references, workflow cycles, unsafe output paths, invalid UI profile inheritance, and incompatible execution environments.

FS03 treats this document and `devtool compatibility --json` as release-seal inputs. Adding a new compatibility shim requires adding it to the registry and regression coverage rather than hiding it in ad-hoc parser logic.
