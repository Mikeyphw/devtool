# Overlay 11 of 15 — Android devices, ADB, instrumentation, signing and release gates

## Scope

This overlay closes the S11 Android device/signing/release-gate bucket. It focuses on places where Android validation previously trusted configured booleans, loose APK pairing, best-effort cleanup, release evidence, signing metadata, or successful process exit too much.

## Findings closed

- S11-01 — Instrumentation config string `"false"` no longer enables cleanup/capture flags.
- S11-02 — Android runner instrumentation CLI string booleans are parsed strictly.
- S11-03 — Malformed instrumentation wait timeouts now raise controlled `InstrumentationError` diagnostics.
- S11-04 — Instrumentation app/test APK discovery now requires same-module pairing.
- S11-05 — Configured instrumentation runners must be installed and must match target package.
- S11-06 — Required instrumentation cannot pass with zero selected/discovered tests.
- S11-07 — Configured test APK package cleanup still runs when runner discovery fails after install.
- S11-08 — Install smoke now requires process-state verification.
- S11-09 — Upgrade smoke verifies old/new APK package identity before install.
- S11-10 — Android release evidence cannot pass with zero gates.
- S11-11 — A package phase without valid release artifacts adds a failed release-artifacts gate.
- S11-12 — Invalid Devtool-managed signing configuration blocks release when verification is requested.
- S11-13 — Preferred device policy now requires an explicit preferred value and never silently falls back.
- S11-14 — Termux USB permission failures surface as `usb-permission-not-granted`.
- S11-15 — Signing prompt/stdin booleans parse string `"false"` as false.
- S11-16 — Release signing task detection no longer matches non-producer tasks like `releaseNotes`.
- S11-R2-01 — Release/install smoke explicitly avoids `adb install -t`.
- S11-R2-02 — Requested cleanup failure fails install smoke.
- S11-R2-03 — Process-death smoke requires `am kill` success.
- S11-R2-04 — Release phases with zero command specs add failed gates.
- S11-R2-05 — Error/fatal diagnostics fail Android release phase gates even with return code 0.
- S11-R2-06 — Release artifact filtering treats `valid = "false"` as false.
- S11-R2-07 — Preferred device policy without a preferred value no longer becomes exactly-one.
- S11-R2-08 — Malformed instrumentation runner args fail request/spec creation safely.
- S11-R2-09 — Malformed signing secret commands raise controlled `AndroidSigningError`.
- S11-R2-10 — Legacy `require_for_release = "true"` is honored.
- S11-R2-11 — Screenshots must be valid PNG files before they are recorded as passed.
- S11-R2-12 — Failure evidence logcat/package dumps are redacted before being written.

## Implementation notes

- Canonical instrumentation discovery now keys APK pairs by module path before `build/`, preventing accidental cross-module app/test APK pairing.
- Required instrumentation now treats zero discovered tests as a release-blocking failure, including cases where Android emits only a completion status code.
- Device smoke operations now explicitly distinguish test APK installs from release/install smoke installs.
- Android release orchestration now treats gates as the source of truth: no gates, empty gate specs, package gates without artifacts, error diagnostics, and invalid release signing all produce failed release evidence.
- Termux/ADB diagnostics preserve no-permission semantics instead of collapsing them into generic no-device state.

## Validation run locally

- `15 passed` — focused Overlay 11 S11 suite.
- `220 passed` — S11 + AOPS02/AOPS03/AOPS04 + Android runner/artifact/toolchain regression sweep.
- `356 passed` — broader Android workflow/toolchain/device/signing/release regression sweep.
- `158 passed` — remediation Overlay 3–11 regression suite.

## Apply mode

This is an intermediate roadmap overlay and should be applied with `--no-validate`. Final validation remains reserved for later gate/final-seal overlays.
