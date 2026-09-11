# FS01/FS02 — compatibility audit and documentation consolidation

FS01 converts backward compatibility from scattered parser/config shims into a versioned registry (`devtool.compatibility`). The registry covers canonical replacements, migration commands, status, and retained fallbacks across config, CLI/environment, Python API, machine/IPC/Tasker, Android bridge, HUD, and workflow syntax.

`devtool compatibility [--json] [--active-only] [--strict] [--category ...]` is the human/machine inspection surface. Strict mode is intentionally config-focused: it fails when the selected config still needs migration, not simply because Devtool ships old aliases.

The config migrator is schema v2. It rewrites safe runner aliases, moves `[project].default_target` to `[workspace].default_target`, migrates the legacy UI confirmation booleans, supports the global user config, creates backups before writes, and refuses conflicting canonical/legacy workspace defaults. It operates on raw TOML so migration diagnostics remain available even when the normal resolved model would reject a conflict.

FS02 makes the canonical path copyable. New examples use `runner`, `platform`, `[workspace]`, UI policy tables, workspace groups, jobs/workflows, target ownership, scheduler policy, and test profiles. Legacy syntax is confined to migration/compatibility documentation.

## Persisted-format compatibility

The compatibility registry also covers external/persisted formats that cannot be removed just because new producers stopped emitting them: legacy artifact repository/commit/inventory fields, implicit/string-entry bundles, repository-local artifact history, validation-session schemas v1-v5, and the legacy transaction singleton marker. These paths are read-only compatibility inputs; all current producers emit the canonical formats.
