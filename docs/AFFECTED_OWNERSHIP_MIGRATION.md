# U01 — Diagnosing ignored affected ownership metadata

Devtool's current canonical loader accepts `root-and-inputs` (the backward-
compatible default) and `inputs-only` (explicit opt-in requiring target inputs)
when declared directly under `[targets.NAME]`.

The old metadata value `affected_ownership = "explicit"` in
`[targets.NAME.metadata]` is ignored. A repository-root target (`root = "."`)
then owns *every* changed file regardless of narrow declared inputs.

## Read-only check

    python3 -m devtool.affected_ownership_diagnostics --repo /path/to/consumer --json --strict

`NEEDS_MIGRATION` exits 2 only with `--strict`; without strict it is an
informational diagnostic. The report provides source and effective modes,
legacy declarations, root-wildcard findings and a suggested migration.
No target is silently changed and no repository is edited.

## Consumer migration

    [targets.example]
    runner = "python"
    root = "."
    affected_ownership = "inputs-only"
    inputs = ["src/**", "tests/**", ".devtool.toml"]

Remove only the stale metadata key; preserve other metadata. Review target
inputs, direct-job inputs and cross-target references. Test changed paths
against Devtool's actual config loader and affected selector, including
unrelated paths, shared paths and one unknown/unowned sentinel.

Unknown sources still select the entire candidate target scope. Existing
consumers retain root-and-inputs by default. Source checks cannot qualify
hosted CI, a device or a signed release.
