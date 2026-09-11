# Canonical Devtool examples

The files under `examples/` are small, copyable examples using only current canonical configuration. Migration-only syntax is intentionally kept out of them.

## Python service

Start with [`examples/python-service.devtool.toml`](../examples/python-service.devtool.toml) for a single Python service. It demonstrates canonical `runner`/`platform` identity, Python environment configuration, target ownership, and a focused validation profile.

## Polyglot workspace

[`examples/polyglot-workspace.devtool.toml`](../examples/polyglot-workspace.devtool.toml) demonstrates Python, Node, and Android targets in one workspace; target groups; reusable jobs; cross-target workflow dependencies; affected-target ownership; and the safe workflow scheduler.

Useful commands:

```sh
devtool targets --json
devtool groups --json
devtool plan validate --group release --explain
devtool --group release validate
devtool --affected validate
```

## UI profile

[`examples/ui-profile.devtool.toml`](../examples/ui-profile.devtool.toml) shows a repository profile layered over user-global preferences, per-surface overrides, confirmations, and notifications. Inspect the resolved result with:

```sh
devtool ui-contract --surface desktop --json
devtool config resolve --provenance
devtool config explain --json
```

## Release validation

[`examples/release-validation.devtool.toml`](../examples/release-validation.devtool.toml) demonstrates a named test profile and conservative release-facing UI policy. Artifact overlays can embed their own focused TS00 selectors; callers do not need to duplicate the selection when the manifest already declares it.

## Migrating older examples

Do not copy old `kind`, target `type`, `[project].default_target`, or legacy UI confirmation booleans into new files. Audit and migrate an existing repository instead:

```sh
devtool compatibility --active-only
devtool config migrate --check
devtool config migrate --write
```

See [`BACKWARD_COMPATIBILITY.md`](BACKWARD_COMPATIBILITY.md) and [`MIGRATION_1.1.md`](MIGRATION_1.1.md) for the compatibility contract.
