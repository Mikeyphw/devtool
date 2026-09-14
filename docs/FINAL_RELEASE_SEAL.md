# FS03 final release seal

FS03 is the repository-wide release gate after MP01–MP10, UI01–UI12, and FS01–FS02.
It is intentionally deterministic and side-effect free: it audits the repository contracts while `devtool validate` and artifact validation execute the actual build/test pipelines.

Run:

```sh
devtool final-seal
devtool final-seal --json
devtool final-seal --strict
devtool final-seal --target devtool --strict
```

`--target <name>` emits the deterministic final seal for one configured workspace target, which lets the last remediation step be shipped as one overlay per target while keeping the repository-wide FS03 gate intact.

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

## Target-scoped final overlays

Overlay 15 is split by configured Devtool target. The `devtool` target seal owns the Python CLI/runtime, release-qualification scripts, repository-wide seal entry point, docs, and tests. The Android HUD target seal owns the Android companion project and its Gradle/HUD release checks.

Use target-scoped seal commands during final qualification:

```sh
devtool final-seal --target devtool --strict
devtool final-seal --target android_hud --strict
devtool final-seal --strict
```

The target reports are side-effect-free. They verify that the selected target exists in `.devtool.toml`, matches its sealed runner/platform/root identity, owns the expected files and regression tests, and has a concrete target-specific overlay apply command.

## Final remediation coverage manifest

The repository-wide final seal also reads `FINAL_REMEDIATION_COVERAGE.json`. That manifest records the 15-overlay roadmap, confirms that Overlays 1–14 closed all 308 deduped findings, and lists the two Overlay 15 target artifacts: `devtool` and `android_hud`. The repository-wide seal fails if `.devtool.toml` contains a configured target without a matching final target seal.

## Hotfix v2 runnable final-seal profile

The `final-seal` validation profile now does more than set `final_seal_contracts=true`: every runner validation plan appends a target-scoped final-seal `CommandSpec` when that flag is active. The root target plans `python-final-seal`, and the Android HUD target plans `android-final-seal`.

`FINAL_REMEDIATION_COVERAGE.json` uses schema v2 and lists all 308 assigned finding IDs exactly once. The release seal rejects missing, duplicated, or out-of-range assignment evidence.
