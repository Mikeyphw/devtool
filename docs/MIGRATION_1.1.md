# Migrating to Devtool 1.1

Devtool 1.1 keeps the 1.0 command/configuration surface readable while moving new projects to runner/platform identity, workspace-owned defaults, UI Core presentation policy, machine schema v2, and IPC protocol v2.

The recommended migration sequence is:

```sh
devtool compatibility --active-only
devtool config migrate --check
devtool config migrate --write
devtool config explain --json
devtool compatibility --strict
```

`config migrate --write` creates `.devtool.toml.bak` before changing the repository configuration. Use `--global` to migrate legacy user UI preferences in `~/.config/devtool/config.toml`.

## Project identity

Legacy:

```toml
[project]
kind = "python"
```

Canonical:

```toml
[project]
runner = "python"
platform = "server"
```

The same applies to named targets: `[targets.*].kind` and `[targets.*].type` migrate to `[targets.*].runner + platform`. `--runner` is the canonical CLI override; `--project-type` remains a compatibility alias.

## Workspace default target

Legacy:

```toml
[project]
default_target = "api"
```

Canonical:

```toml
[workspace]
default_target = "api"
```

If both forms exist with different values, Devtool reports the conflict and `config migrate --write` refuses to guess.

## UI confirmations

Legacy UI booleans remain readable but are no longer emitted by generated configuration.

```toml
[ui]
confirm_destructive = false
confirm_cancel = true
```

Canonical policy:

```toml
[ui.confirmations]
cancel = "always"
rollback = "never"
delete_artifact = "never"
snapshot_overwrite = "never"
```

The migrator preserves the legacy precedence semantics when a canonical confirmation table already exists.

## Machine and automation consumers

New automation should consume `machine.schema_version = 2`, semantic action IDs, and the structured `tasker.fields` values. Legacy outer machine/HUD fields, IPC `cancel`, and flat `tasker_fields` remain supported compatibility fallbacks.

The Android bridge remains protocol v1 with additive UI Core semantic extras. This is intentional: older companion versions remain interoperable while new companions can prefer semantic progress, diagnostics, actions, and notification policy.

## Desktop HUD names

`hud --mode desktop-window` is canonical. `gtk-window` remains a deprecated alias that maps to the GTK4 HUD. Removed Qt modes fail with a migration hint rather than silently choosing another renderer.

## Release/packaging compatibility

### Android

The frontend version is 1.13.0 (`versionCode 26`). Debug builds now use the `.debug` application ID suffix, so they can coexist with a signed release build. Release tasks require an explicit keystore and fail before compilation when the four `DEVTOOL_RELEASE_*` values are incomplete. Existing 1.6.1 release installs upgrade normally only when signed with the same certificate.

### Desktop

Control Center is 0.4.0. Packaging uses `SOURCE_DATE_EPOCH`, emits a `.sha256` file, and supports `--verify`. Both binaries support `--version`; the HUD also supports `--exit-when-idle` for isolated smoke testing. Existing user settings and history locations are unchanged.

### Release automation

Version edits begin in `release/versions.json` and propagate to Python, CMake, Gradle, Debian control metadata, and the manual page. The release qualifier rejects drift. CI-created Android keys are ephemeral test credentials and must never be used for published packages.

## Examples

All canonical examples are consolidated in [`EXAMPLES.md`](EXAMPLES.md) and `examples/*.devtool.toml`. Legacy syntax appears only in migration/compatibility documentation.
