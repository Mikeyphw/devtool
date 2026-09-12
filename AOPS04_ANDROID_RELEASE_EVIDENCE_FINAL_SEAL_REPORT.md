# AOPS04 Android Release Evidence Final Seal Report

## Scope

AOPS04 completes AO11-AO12 and seals the consolidated HROPS01/AOPS02-AOPS04 roadmap.

## Delivered

- Added `AndroidReleaseOrchestrator` as the single high-level Android release pipeline.
- Added first-class `devtool --target <target> release` and `devtool android release` surfaces.
- Release execution reuses `AndroidGradleRunner`, `AndroidExecutionContext`, signing policy/injection, `AndroidDeviceOperations`, instrumentation, artifact verification, and the artifact registry.
- Added immutable `AndroidReleaseEvidenceSession` reports under `.devtool/reports/android-release/`, including source Git state, Devtool version, target/variant, resolved toolchain/providers, signing identity policy, selected device, gate outcomes, verified artifact hashes, and final verdict.
- Evidence reports are SHA-256 sealed and registered in the existing artifact registry.
- Release policy supports unit/lint/device-test/install/upgrade/signature/identity gates and explicit `disabled | when-device-present | required` device-test semantics.
- `release_qualification.py` is now a compatibility consumer of the canonical Android release orchestrator rather than an alternate raw Gradle/ADB/signing implementation.
- The FS03 strict seal now includes an Android Operations convergence audit and requires HROPS01/AOPS02/AOPS03/AOPS04 reports/tests.

## Architectural invariants

1. Release does not discover ADB independently.
2. Release does not own a separate raw Gradle execution path.
3. Signing secrets remain outside planned command specs, durable evidence, argv, and reports.
4. Artifact verification and registry registration are reused instead of reimplemented.
5. Device lifecycle/smoke operations route through `AndroidDeviceOperations`.
6. Release qualification consumes the canonical release service.
7. Final evidence is immutable once sealed.

## Configuration

```toml
[targets.app.android.release]
variant = "release"
unit_tests = true
lint = true
device_tests = "required"
install_smoke = true
upgrade_smoke = true
verify_signature = true
verify_certificate_identity = true
# previous_apk = "path/to/previous-release.apk"
```

## CLI

```bash
devtool --target app release
devtool --target app release --device-tests required
devtool --target app android release --format json
```

## Final seal

`devtool final-seal --strict` now audits Android Operations convergence in addition to the existing FS03 repository contracts.

## Validation evidence

Final post-fix validation on the completed AOPS04 tree:

- Full repository inventory: **2,356 tests passed + 69 subtests**, 0 failures.
- Focused AOPS02-AOPS04/release/config/CLI/final-seal gate: **259 tests passed + 40 subtests**, 0 failures.
- AOPS04 architecture and edge-case contracts include AAB-only signing, cross-artifact signer consistency, optional upgrade-smoke skip, and AAB-only device-smoke behavior.
- `devtool final-seal --strict`: **14/14 PASS**.
