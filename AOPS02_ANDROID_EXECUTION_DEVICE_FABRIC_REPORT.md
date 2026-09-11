# AOPS02 — Android Execution + Device Fabric report

## Scope delivered

AOPS02 delivers AO01–AO04 as one subsystem:

- AO01: canonical `AndroidExecutionContext` and structured tool resolution.
- AO02: first-class `auto | sdk | termux | termux-adb | path | none` ADB providers.
- AO03: canonical Android device inventory, enrichment, and deterministic selection.
- AO04: native Termux USB/Termux:API-aware transport diagnostics.

## Architecture

`devtool/android/capabilities.py` is the host-side capability model.
`devtool/termux_toolchains.py` owns the low-level ADB provider resolver because
that file is already staged beside the standalone `build-apk` runner; host
Devtool and staged/chroot Android builds therefore share provider semantics.

`devtool/android/devices.py` owns device parsing, selection and transport doctor
semantics. `devtool/android/operations.py` binds the execution context and
manager into one facade. `devtool/android/cli.py` exposes stable JSON payloads.

The existing Android instrumentation runner, artifact verifier, doctor, release
qualification path, Android integration layer, and connected-test handling were
migrated to consume the canonical resolver/device services where those
capabilities apply.

## Provider behavior

`auto` is probe-driven. When a project-resolved SDK root supplies an executable
`platform-tools/adb`, that project-scoped candidate is probed first so Devtool
does not escape into an unrelated ambient ADB. On native Termux, an unavailable
or incompatible SDK ADB falls through to `termux-adb`, which must pass both its
binary probe and Termux USB readiness checks, and then to ordinary Termux ADB.
When no project SDK ADB exists, the native Termux providers remain the preferred
automatic path. On desktop/chroot environments SDK/PATH ADB are considered by
the canonical provider order. Explicit `path` and `none` are deterministic.

No code in AOPS02 adds or installs the third-party termux-adb repository.

## Device model

The normalized device contract records ADB state, serial/transport ID,
product/model/device, connection type, emulator/physical identity, and optional
runtime metadata. Selection policies are exactly-one, serial, preferred,
first-physical, first-emulator, and all. `ANDROID_SERIAL` is retained as a
selection input.

## CLI

```text
devtool android capabilities
devtool android devices
devtool android devices doctor
devtool android doctor
```

All support ADB provider overrides and JSON output. Device listing additionally
supports enrichment and deterministic selection.

## Compatibility boundaries

- Existing Android builder task behavior remains intact.
- Existing instrumentation CLI semantics remain intact.
- Existing `ANDROID_SERIAL` behavior is retained.
- Existing Termux `android-tools` ADB remains supported.
- `termux-adb` is additive and explicitly modeled.
- The standalone Android builder still owns build-time tool provisioning; it
  consumes the shared staged ADB resolver rather than importing host Devtool.

## Validation contract

`tests/test_aops02_android_execution_device_fabric.py` covers provider choice,
Termux USB readiness, device parsing/state, deterministic selection,
`ANDROID_SERIAL`, execution-context resolution, doctor distinctions, builder
argument propagation, CLI exposure, and consumer-convergence contracts.

The final AOPS02 artifact should be validated with this contract plus the
existing Android connected-test, instrumentation, artifacts, doctor, Termux
provider, Android runner, completion, CLI, and release-seal regressions.

## Completed validation

The completed AOPS02 v2 tree was collected as 2,313 pytest tests. All 2,313 tests passed, plus 69 subtests, with zero failures. `devtool final-seal --strict` remained green at 13/13 checks. Python byte-compilation of the Devtool package also completed successfully.
