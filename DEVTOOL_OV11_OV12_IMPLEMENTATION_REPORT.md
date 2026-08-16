# Devtool OV-11/OV-12 Implementation Report

## Scope

This overlay seals the next combined remediation round after OV-09/OV-10:

- **OV-11 — Android command/state/repository workflows**: MR-029, MR-030, MR-031, MR-032, MR-033, MR-034, MR-035
- **OV-12 — Android root bridge, Termux, Tasker, and notifications**: MR-036, MR-037, MR-038, MR-039, MR-040

The post-OV-10 repository already contained the baseline phase 29 through phase 40 static contract implementation. This overlay therefore focuses on the remaining seal gaps found during promise auditing.

## Implemented fixes

### Live-run notifications

`NotificationController` now treats notification delivery as a permission-gated and manager-gated operation:

- avoids channel creation when `NotificationManager` is unavailable;
- skips live-run posts when Android notification permission is denied;
- skips summary notifications when permission is unavailable;
- keeps cancellation safe when called with null or blank run IDs.

This aligns the live-run HUD notification surface with the already guarded debug/repository notification surfaces.

### Tasker result delivery confinement

Tasker completion result dispatch now reuses a shared trusted-package predicate and confines async completion delivery to known Tasker package identities:

- service completion targets must use trusted Tasker packages;
- broadcast completion targets with explicit package/component must use trusted Tasker packages;
- implicit completion broadcasts are pinned to the modern Tasker package;
- Tasker receiver bundles are assigned the app class loader before value reads.

### Repository path repair authorization

Repository metadata path repair now revalidates the repair action against the current resolver decision before mutation:

- stale repair action IDs are rejected;
- kind-mismatched repair actions are rejected;
- only current resolver-authorized metadata actions are applied;
- command/file-system destructive repair paths remain absent from the repair body.

## Tests added

Added `tests/test_ov11_ov12_android_workflow_seal.py` covering:

- Android 13+ notification permission checks and NotificationManager guards;
- Tasker trusted package confinement and class-loader restoration;
- repository path repair reauthorization before metadata mutation.

## Verification performed

Focused OV-11/OV-12 validation:

```bash
python3 -m pytest -q \
  tests/test_ov11_ov12_android_workflow_seal.py \
  tests/test_android_validation_workbench_phase29_31.py \
  tests/test_phase31_1_validation_completion.py \
  tests/test_validation_reports_phase32.py \
  tests/test_android_debug_phase34.py \
  tests/test_android_debug_phase35.py \
  tests/test_android_debug_phase36.py \
  tests/test_android_debug_phase37.py \
  tests/test_android_debug_phase38.py \
  tests/test_android_debug_phase39.py \
  tests/test_android_debug_phase40.py \
  tests/test_android_repositories_phase2.py \
  tests/test_android_repositories_phase3.py \
  tests/test_android_repositories_phase4.py \
  tests/test_android_repositories_phase5.py \
  tests/test_android_repositories_phase6.py \
  tests/test_android_repositories_phase7.py \
  tests/test_android_security_audit.py \
  tests/test_tasker.py \
  tests/test_tasker_diagnostics_payload.py \
  tests/test_apply_notifications.py
```

Result: **120 passed**.

Compile sanity:

```bash
python3 -m compileall -q devtool tests
```

Result: **passed**.

## Campaign note

Apply this overlay with `--no-validate` to preserve the campaign rule that full validation is deferred until the final release-seal overlay.
