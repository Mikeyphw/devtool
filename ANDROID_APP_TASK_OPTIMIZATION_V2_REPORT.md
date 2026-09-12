# Android Devtool App Task Optimization V2

## Scope

This overlay optimizes Devtool's own Android Live HUD build/validation task path without removing validation coverage. It targets repeated Gradle startups, redundant resumable phases, repeated toolchain verification work, and unnecessary ADB probes.

## Changes

- Normal `android_hud` validation now coalesces build, unit-test, and lint tasks into one Gradle invocation (`split_gradle = false`) so Gradle can share configuration and the common task graph.
- Durable/resumable validation explicitly disables the separate `dependencies`, `compile`, and `resources` phases for this app. Package, unit-test, and lint still execute and transitively cover those component tasks.
- `verifyStableToolchainBaseline` declares concrete inputs plus a stamp output, allowing Gradle to mark it up-to-date when the pinned baseline files and toolchain identity are unchanged.
- `verifyReleaseSigning` and `verifyReleaseApk` now have verification outputs; signing still fails closed if credentials or the keystore are unavailable.
- Added `devtoolDebugValidation` and `devtoolReleaseValidation` aggregate lifecycle tasks for one-invocation local/CI validation.
- The shared task classifier distinguishes plain `check` from `connectedCheck`. Plain `check` no longer triggers an ADB probe; connected tests, `connectedCheck`, release/device gates, install/device operations retain device-aware behavior.
- Resumable validation now treats an explicitly configured empty phase list (or `false`/`off`/`disabled`/`none`/`skip`) as an intentional phase disable instead of silently restoring default work.

## Safety invariants

The optimizer does not remove unit tests, lint, package assembly, signing gates, release APK verification, publication tasks, connected Android tests, or unknown custom tasks. The phase reductions are target-specific to Devtool Live HUD and rely on Gradle's normal dependency graph.

## Recommended validation

```bash
PYTHONPATH=. python3 -m pytest -q \
  tests/test_gradle_android_task_optimization.py \
  tests/test_android_app_source.py \
  tests/test_android_connected_task_skip.py \
  tests/test_android_runner.py \
  tests/test_no_device_config_cache_and_gradle_deprecations.py
```
