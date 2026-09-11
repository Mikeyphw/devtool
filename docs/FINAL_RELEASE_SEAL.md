# FS03 final release seal

FS03 is the repository-wide release gate after MP01–MP10, UI01–UI12, and FS01–FS02.
It is intentionally deterministic and side-effect free: it audits the repository contracts while `devtool validate` and artifact validation execute the actual build/test pipelines.

Run:

```sh
devtool final-seal
devtool final-seal --json
devtool final-seal --strict
```

`--strict` exits non-zero when any seal check fails. The versioned report verifies canonical repository configuration, compatibility-registry integrity, explicit schema versions, roadmap reports and contract suites, canonical TOML examples, completion synchronization, the `final-seal` validation profile, tracked-backup hygiene, target roots, and release-document discoverability.

The release validation profile is available separately:

```sh
devtool validate --profile final-seal
```

The profile enables the release/architecture contracts, requires artifacts and external artifact verification, and keeps Android instrumentation conditional on device availability. Artifact manifests may still embed narrower pytest selectors and target-specific Android tasks for reproducible overlay validation.

## FS03 acceptance

A production-ready tree must satisfy all of the following:

1. `devtool compatibility --strict` reports no active repository migration.
2. `devtool config migrate --check` reports canonical configuration.
3. `devtool final-seal --strict` passes.
4. The focused TS00/roadmap regression gate passes with zero failed/skipped contract tests unless an explicitly documented device-only phase is unavailable.
5. Android HUD unit/lint/package validation runs through the configured `android_hud` target when the artifact declares it.
6. Artifact source hashes, payload hashes, atomic apply/rollback, and post-apply expectations pass.
7. `git diff --check`, Python compilation, completion synchronization, documentation/example checks, and final repository status are clean.

FS03 does not remove compatibility aliases. FS01 defined those as deliberate public contracts; the seal verifies they remain inventoried and that new producers emit canonical forms.
