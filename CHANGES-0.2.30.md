# Devtool 0.2.30 changes

## Optional root bridge

- Added an optional non-Zygisk KernelSU/Magisk/APatch module under `root-module/devtool-bridge`.
- Added `build-root-bridge-module.sh` and an identical module ZIP embedded in the Android app.
- Added a dynamic **Devtool Root Bridge** tile with check, export, pair, update, verify, disabled, denied, and connected states.
- Added protected token matching, module version reporting, enabled-state detection, Termux UID detection, per-run logs/PIDs, and cancellation.
- Added root-backed execution for generated Android workflows and Tasker actions while preserving rootless Termux as the default.
- Raw arbitrary commands are not accepted by the root bridge; both app and module enforce `devtool`/`devtool-androidos` command gating.
- No Zygisk code, `/system` overlay, or metamodule dependency is used.

## Completion summaries

- Successful single-overlay summaries now state the applied overlay name and artifact path.
- Successful bundles list overlays in execution order with target and artifact metadata.
- Added overlay metadata to the stable HUD completion payload.
- Android history now stores the rich completion summary and exposes **Copy success summary**.

## Validation

- 240 Python tests pass.
- 23 Android Java sources parse successfully.
- Root-module and build scripts pass shell syntax checks.
