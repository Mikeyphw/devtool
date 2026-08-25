# Overlay intelligence, plugins, and universal JSON

Architecture overlays 06 and 07 make artifact application preflightable and expose stable extension/API surfaces.

## Overlay intelligence

`devtool overlay check <artifact>` runs structural manifest validation and repository-state validation separately, reports per-path conflicts, and marks whether every conflict can be safely rebased.

`devtool overlay rebase <artifact>` regenerates inventory source hashes against the current repository and recalculates payload hashes/sizes. A stale `add` becomes `modify` when a file now exists; a stale `modify` becomes `add` when its source disappeared. Missing delete sources fail closed.

A single overlay can be applied with `--rebase`. Rebase is intentionally rejected for snapshots, multi-overlay bundles, and deferred validation because those modes need a persistent immutable source artifact.

## Plugin API

`devtool.plugins` defines `PluginDescriptor`, `PluginContext`, and plugin-specific doctor checks. Devtool ships Android, Python, Rust, and Magisk/KernelSU descriptors and discovers third-party plugins through the `devtool.plugins` Python entry-point group. External plugin load failures are isolated and surfaced by `plugin list` instead of preventing devtool startup.

Commands:

- `devtool plugin list`
- `devtool plugin doctor android`

## Universal JSON mode

`--json` is accepted in any position before runner passthrough (`--`). It wraps the invocation in a schema-versioned envelope with command, repository, exit code, duration, structured data when stdout itself is JSON, and captured stdout/stderr otherwise.

Universal JSON disables live HUD publication for that invocation so machine output remains deterministic.
