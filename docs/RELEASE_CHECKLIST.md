# Devtool 1.1 release-candidate checklist

## Automated blockers

## FS01/FS02 compatibility and documentation blockers

- [ ] `devtool compatibility --strict` passes for the release repository.
- [ ] `devtool config migrate --check` reports no active repository migrations.
- [ ] `devtool config migrate --global --check` reports no active user-config migrations on the release host.
- [ ] `python3 -m pytest tests/test_fs01_fs02_compatibility_docs.py` passes.
- [ ] Every canonical TOML under `examples/` parses and uses `runner`/`platform` rather than legacy `kind`/`type`.
- [ ] Generated/static completion parity and CLI/manpage contracts pass.
- [ ] `docs/BACKWARD_COMPATIBILITY.md`, `docs/MIGRATION_1.1.md`, `docs/EXAMPLES.md`, README, and CHANGELOG agree on supported aliases and migration commands.
- [ ] Machine/IPC/Tasker/Android compatibility shims remain additive; no legacy field is removed without an explicit migration boundary.

- [ ] `python3 tools/release-qualify.py --require-qt --require-android --device-tests` passes.
- [ ] GTK4 Control Center and HUD smoke/package gates pass.
- [ ] Debian package metadata and checksum verification pass.
- [ ] Android lint and local JVM tests pass.
- [ ] Release APK is signed by the intended certificate and `apksigner verify` passes.
- [ ] Emulator instrumentation installs and launches the debug application.
- [ ] `tools/build-release.py --verify-reproducible` produces identical archives and verified schema v2 artifact manifests.
- [ ] GitHub Actions release-qualification workflow is green and uploads desktop, Android, emulator, and source evidence artifacts.

## Desktop manual matrix

Exercise 640, 900, and 1440 px widths at 100% and 200% scaling. Verify Run,
History, Artifacts, Diagnostics, Repositories, Settings, and compact HUD with
keyboard-only navigation. Confirm tray reopen, IPC reconnect, clipboard monitor,
window-state restore, and clean exit. Install the `.deb`, upgrade from 0.3.8,
launch both binaries, then uninstall and verify user data is retained.

## Android manual matrix

Use a phone, landscape phone, and tablet or resizable emulator. Repeat with
font size and display size at their largest supported values. Verify:

- TalkBack order, labels, state announcements, and 48 dp touch targets;
- notification permission denial/grant and grouped simultaneous-run updates;
- overlay permission denial/grant, rotation, resize, and completed-run linger;
- foreground Termux execution, background execution, cancellation, and rollback;
- process death while a run is active followed by app relaunch and state recovery;
- Tasker configuration/run/result variables and root-bridge opt-in behavior;
- install, upgrade from 1.6.1, signature mismatch rejection, and uninstall.

Record device/API level, build hash, result, screenshots, and log location for
every failed row. A manual failure blocks the release candidate even when the
automated matrix is green.

## Publication

- [ ] Review `CHANGELOG.md` and `docs/MIGRATION_1.1.md`.
- [ ] Confirm `release/versions.json` matches every distributable.
- [ ] Run `tools/make-release-candidate.sh --require-native --desktop --android`.
- [ ] Verify `SHA256SUMS.all`, `release-artifacts.json`, and the `.final.json` release qualification evidence hash from a second machine.
- [ ] Create an annotated `v1.1.0-rc.3` tag only after all automated and manual rows pass.

## Phase 32–33 validation seal

- [ ] `python3 tools/validate-validation-release.py` passes the 16-scenario validation failure matrix.
- [ ] A generated validation report contains the source artifact, original/final Git heads, every phase and attempt, diagnostics, rollback outcome, commit outcome, and final verdict.
- [ ] Android Logs renders command runs and validation sessions as distinct cards; Open report shows phase and attempt evidence.
- [ ] Finalization rejects stale repository inputs, incomplete required phases, and an unsatisfied minimum checkpoint.
- [ ] `python3 tools/release-qualify.py --require-android --device-tests` passes on the release host.
- [ ] The Android Gradle build completes lint, JVM tests, signed release APK verification, and connected instrumentation when device tests are enabled.
- [ ] Record the release report, Android SDK/build-tools versions, device/API level, APK certificate digest, and Git commit.

- The complete Python regression inventory runs in four deterministic balanced pytest groups to isolate hangs while preserving complete coverage.

Sealed final reports use a content hash and a write-once `.final.json` path; later report requests cannot overwrite the final evidence.
