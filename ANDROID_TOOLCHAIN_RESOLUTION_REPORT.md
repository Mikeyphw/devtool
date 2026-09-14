# Overlay 10 of 15 — Android Gradle, SDK, NDK and toolchain resolution

## Scope

This overlay closes the S10 Android toolchain remediation bucket. It hardens Gradle wrapper selection, Android project inspection, Java/Gradle environment parsing, SDK/Build Tools/NDK readiness checks, provider staging cleanup, managed Gradle offline reuse, no-device ADB probing, and Android runner boolean configuration handling.

## Findings closed

- S10-01 — `--gradle-version` no longer masks an incompatible wrapper Gradle version when the wrapper provider is selected.
- S10-02 — Android/Gradle config string `"false"` no longer enables boolean options.
- S10-03 — Gradle cleanup filters process ownership by project root instead of killing unrelated same-user Gradle builds.
- S10-04 — Active Android SDK provider staging directories are preserved.
- S10-05 — Wrapper cache detection no longer treats `distributionPath=.` as a valid cached Gradle distribution.
- S10-06 — Commented Android plugin IDs no longer make JVM projects look Android.
- S10-07 — Commented Java toolchain declarations no longer force an exact Java requirement.
- S10-08 — Truncated downloads are rejected by the low-level download primitive and their `.part` files are removed.
- S10-09 — Non-executable Java homes produce controlled `BuildError` failures instead of raw `PermissionError` crashes.
- S10-10 — Malformed Android environment integer values no longer crash argument parsing or `--help` paths.
- S10-11 — Managed Gradle offline mode can reuse a complete cached install without requiring archive checksum sidecars.
- S10-12 — Partial NDK installs are no longer classified ready unless revision, CMake toolchain, and host compiler payload are present.
- S10-R2-01 — Android SDK dry-run no longer mutates `local.properties`.
- S10-R2-02 — ADB no-device results are not cached, so newly connected devices are not hidden.
- S10-R2-03 — Partial Build Tools installs are not reported ready without the required executables and `d8.jar` payload.
- S10-R2-04 — `libs.versions.toml` Android plugin aliases do not reclassify JVM projects unless the plugin alias is actually active in Gradle script content.
- S10-R2-05 — Deeply nested Android modules keep application/library and AGP identity.
- S10-R2-06 — `.devtool` files are ignored during Android build inspection.
- S10-R2-07 — Gradle RC/milestone/preview/snapshot versions are no longer accepted as stable matches.
- S10-R2-08 — Invalid Gradle JVM environment settings fail safely instead of crashing after argparse.
- S10-R2-09 — Android download debris cleanup skips active/recent SDK temp/cache directories.
- S10-R2-10 — Disabling SDK components is case-normalized.

## Implementation notes

- `devtool/android/_standalone.py` now centralizes strict boolean parsing, integer environment fallback, SDK component override normalization, pre-release Gradle version rejection, active Android source discovery, Gradle wrapper cache validation, SDK download debris safety, project-scoped Gradle process cleanup, and dry-run-safe `local.properties` updates.
- `devtool/android_integration.py` and `devtool/project_runners.py` now parse Android and Gradle runner boolean options through strict option parsing instead of Python truthiness.
- `devtool/config.py` now strips comments from Gradle scripts before Android plugin detection and only honors version-catalog Android plugin aliases when the script actively references them.
- `tests/test_rem10_android_toolchain_resolution.py` adds one focused regression test for every S10 finding.

## Validation

Focused S10 validation:

```text
22 passed — tests/test_rem10_android_toolchain_resolution.py
```

Android toolchain regression sweep:

```text
254 passed — S10, Android runner, SDK providers, Termux/native Android providers, NDK host/native compatibility, no-device ADB cache, AAPT2 provider, and Gradle task optimization suites
```

Broader overlays 3–10 and Android runtime compatibility sweep:

```text
419 passed — S10 plus Android execution/device/runtime/security suites and rem03 through rem09 regression suites
```

## Apply policy

This is an intermediate remediation overlay. Apply with `--no-validate` and let Overlay 15 of 15 run the full repository qualification seal.
