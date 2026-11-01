# R37 MMRL OfficialDebug real-device smoke — adapter, not a replacement

## Existing authorities reused

- R35 `_verified_or_download` and ACTIONS-04 GitHub ZIP digest verification.
- R36 native DAG app artifact and its original hosted plan, independent verification, and native receipt.
- ACTIONS-05 `android_collect`: both APKs independently inspected with Android Build Tools.
- Canonical artifact registry (`register_paths`, `verify_record`, immutable receipt).
- AOPS02 device transport/selection via `AndroidDeviceOperations.from_config`, exactly-one policy.
- AOPS03 `devtool.android_instrumentation`, invoked with `--target mmrl_android --skip-build` and only the reviewed R37 smoke class; retains its own DeviceLease, receipt, crash/screenshot capture, and cleanup authority.

The R36-R2 code and original workflow are **untouched**. A paired R37 import is a new reviewed consumer profile, not a general-purpose arbitrary APK import mechanism.

## Gate semantics

The importer refuses an expired artifact, wrong run or attempt, unexpected workflow, mismatching source SHA, unapproved ZIP entry, APK/hash/signer mismatch, or unexpected package/runner. Local receipts are reverified against both registry identities and local APK bytes each time. Device operation is prohibited without an authenticated imported pair.

Installed `com.mikeyphw.mmrl.debug` and `.test` are checked before install. An existing installation makes the device step fail closed unless the operator explicitly supplies `--allow-replace`. The adapter never uninstalls existing production MMRL (`com.mikeyphw.mmrl`), clears root state, touches LSPosed databases, changes enabled modules, or invokes local Gradle. It stages two temporary APK copies in reviewed Gradle output paths for the existing `--skip-build` AOPS03 locator, and removes only its own byte-matching copies afterward. AOPS03 remains the cleanup/rollback authority for installed test packages.

The device smoke is PASS only when all three selected test methods actually complete and pass, AOPS03 returns successful receipt on exactly the selected serial, target installation is observed, and a fresh post-marker logcat sample is free of target crash/ANR evidence. If logcat/marker is missing, status cannot be PASS. A test never executed is `NOT_RUN` and **not** a failed test.

`debug_device_smoke_qualified` can become true only with real evidence. `device_qualified`, `debug_device_qualified`, `consumer_qualified`, `release_qualified` remain false until expanded root/LSPosed and UI/runtime acceptance has its own executed workloads. Remaining coverage `NOT_RUN`: root/su integration, LSPosed provider read-only state, module list, repository display, filesystem, and navigation.

## Local command boundary

Never use Gradle locally. After installing Python dependencies, use:

`python -m devtool.actions_r37_mmrl_device plan|import|inspect|device --repo "$MMRL" --run-id "$RUN_ID" --attempt "$ATTEMPT" --source-sha "$SOURCE_SHA" --expected-app-sha256 "$APP_SHA" --expected-test-sha256 "$TEST_SHA" --expected-signer-sha256 "$SIGNER"`

`device` requires exactly one authorized device. `--allow-replace` is an explicit, opt-in mutation of existing debug/test packages. The device run produces an append-only `device-acceptance.json` under `.devtool/artifacts/r37-imports/device-mmrl-run.../attempt-NNNN/`; AOPS03's raw JSON/diagnostic artifacts are preserved alongside it. No device acceptance is reported until this command has actually executed.

## Known limitations

Source-level tests cannot verify physical-device behavior, Android 16/KernelSU/LSPosed integration, or whether a particular ROM supports the runner's activity lifecycle. CI is allowed to fail during first execution; preserve its logs and adjust the reviewed source instead of weakening verifiers. Existing Devtool artifact download/cache storage must support archive roles and the new GitHub workflow name (the source negative tests cannot establish runtime compatibility).
