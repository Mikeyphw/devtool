# Devtool Gradle + Android Task Optimization Report

## Scope

This overlay optimizes the Gradle and Android app task path after the Android Operations roadmap. It keeps the standalone `build-apk` runtime as a thin compatibility launcher, but moves task semantics into shared Android task planning so host Devtool and staged Termux/chroot builds do not drift.

## Implemented changes

- Added a conservative `GradleTaskPlan` with normalized task entries, task classifications, removed/implied tasks, and a side-effect-free connected-device probe decision.
- Added shared task classification for unit tests, lint, assemble/bundle release, connected Android tests, install, publish, release gates, aggregate checks, Android component tasks, and unknown custom tasks.
- Added stable optimization that removes duplicate task requests and prunes only Android component tasks already implied by an explicit `assembleVariant` task in the same Gradle project.
- Fixed package-task variant parsing so `packageDebug`/`packageRelease` are recognized as assemble-implied component work instead of surviving optimization accidentally.
- Added a connected-device probe cache keyed by SDK root, ADB provider/path, PATH, and timeout, with `DEVTOOL_ANDROID_DEVICE_PROBE_CACHE=0` for debugging.
- Avoided ADB probing for ordinary app task sets such as `assembleDebug`, `lintDebug`, and `testDebugUnitTest`.
- Preserved release/qualification/aggregate behavior where tasks can pull connected Android tests transitively.
- Fixed the no-probe path so ordinary app tasks no longer create the no-device connected-test Gradle init script merely because no ADB probe was run.
- Migrated host-side Android builder argument generation to consume the same task planner before constructing `--task` arguments.

## Standalone build-apk status

The standalone `build-apk` file remains necessary as a portability boundary for staged Termux/chroot/bootstrap workflows, but this overlay narrows its role. It is no longer a place for independent Android task policy; it consumes the same task planner and probe cache exported through `devtool.android.tasks`.

## Safety rules

The optimizer is intentionally conservative:

- It never removes tests.
- It never removes lint.
- It never removes connected Android tests.
- It never removes signing/release gates.
- It never removes publication tasks.
- It never removes unknown custom tasks.
- It only removes Android component tasks that are implied by an assemble task for the same Gradle project path and variant.

## Validation

Focused validation run:

```text
145 tests passed
0 failures
strict slow threshold enabled for the focused gate
```

Covered suites:

- `tests/test_gradle_android_task_optimization.py`
- `tests/test_android_connected_task_skip.py`
- `tests/test_android_staged_runtime.py`
- `tests/test_android_runner.py`
- `tests/test_android_instrumentation.py`
- `tests/test_aops04_android_release_evidence_final_seal.py`
- `tests/test_pytest_revitalization.py`

## Expected effect

Android validation and app builds should perform fewer unnecessary ADB probes, avoid repeated connected-device checks within one process, and pass fewer redundant component tasks to Gradle when assemble tasks already imply them. This is especially useful on native Termux, where every subprocess and ADB probe is more expensive.
