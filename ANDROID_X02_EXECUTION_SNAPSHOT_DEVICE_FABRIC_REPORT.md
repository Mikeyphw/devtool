# ANDROID-X02 — Exact artifact/device qualification + execution snapshot

**Campaign position:** gate-window overlay 2 of 3; full-plan overlay 38 of 70.  
**Base boundary:** qualified ANDROID-X01 (user device commit `5127425`; construction-equivalent base uses touched-file contracts).  
**Next:** ANDROID-G1 — 39/70 (separate authoritative environment/device milestone gate).

## Merge-window decision

The inspected window is ANDROID-X01 37/70 → ANDROID-X02 38/70 → ANDROID-G1 39/70. X02 stays separate from both X01 and the gate. No X03 instrumentation-intent/result migration is pulled forward.

## Delivered authority

ANDROID-X02 adds one compositional execution authority rather than another truth store:

- `AndroidExecutionSnapshot` composes the resolved Android environment, Gradle execution receipts/plans/context/JDK/real-launcher identity, exact produced artifact lineage, DeviceLease, RUN/RER/EXO refs, source/config and signing identity.
- `ArtifactPair` verifies exact materialized APK bytes and Gradle producer lineage. RUN attachment IDs, registry IDs and path-shaped receipt refs are preserved as evidence/provenance but do not become the sole semantic artifact identity.
- Android environment identity is componentized (`android.platform`, `android.build-tools`, `android.native`, `android.gradle`, `android.lanes`). Request/qualification/environment-view/path provenance does not fragment equivalent resolved environments. `android_environment_reuse_components()` lets RER consumers fingerprint only the domains they actually consume.
- `DeviceRequirements`, `AndroidTransport` and `DeviceLease` separate declarative device constraints, transport qualification and one stable selected device. AndroidDeviceOperations honors a lease without reselecting a serial mid-lifecycle.
- Production release adopts exact ArtifactPair + private RUN AndroidExecutionSnapshot and uses DeviceLease for device smoke work. Build-only release remains transport-free.
- Root-loopback adbd mutation is represented as a typed lease/receipt and delegated to an injected Host/ENV mutator/restorer; Android code does not run raw privilege escalation.
- Destination bootstrap status returns a destination-generated Android environment snapshot. Semantic identity excludes absolute paths while preserving actual destination path provenance and structured network evidence.
- Bootstrap receipts now retain per-capability freshness evidence; local receipt probing rejects stale provisioned capabilities. Android inspect/qualify/repair/requalify records the provisioning receipt and does not acquire cleanup ownership.
- The authoritative standalone Android runtime and OCI staged copy are mechanically sealed identical.

## Planning boundary correction

A broader retained audit found that the first X02 draft failed closed during *spec planning* when no SDK was provisioned. That broke dry-run and resumable-validation planning. The corrected contract records an `unqualified-local` Android environment request/qualification without inventing an RER domain; execution/release remains fail-closed at the owned preflight. Remote/OCI/chroot planning similarly records destination bootstrap authority and never fingerprints controller Android state.

## Termux provider remediation

The first on-device X02 qualification attempt (`20c907de4df54870849614f2c1fe0c6e`) correctly rolled back after one focused test failed. The failure was test-environment leakage, not an implementation defect: the test removed `aidl` only from its synthetic SDK, while native Termux intentionally prefers a valid `$PREFIX/bin/aidl` provider. On the device, that real helper made the first environment qualification succeed, so repair correctly did not run.

The remediation makes provider availability hermetic by injecting a synthetic Termux runtime/prefix. It now proves both contracts explicitly: repair runs when neither the Termux-prefix nor SDK helper exists, and repair is skipped when a valid Termux helper already qualifies the capability. This prevents host package state from changing the test verdict while preserving the canonical provider policy.

A second on-device qualification attempt (`c2ef5d59fa7647d68aae1683ebf3a17b`) correctly rolled back in the retained AOPS02 suite. That older test asserted `apksigner.provider == "sdk"` while running on native Termux, even though the canonical capability policy intentionally prefers a qualified `$PREFIX/bin/apksigner` before the SDK helper. The implementation again behaved correctly; the retained test mixed an SDK-specific premise with ambient runtime provider selection.

The v3 remediation makes that retained case explicitly host-runtime hermetic, so it continues to prove SDK Build Tools + configured SDK ADB without ambient Termux substitution. A separate X02 regression now forces a synthetic Termux runtime and proves the complementary canonical rule: a qualified Termux `apksigner` wins over the SDK copy. The tests therefore verify both provider contracts instead of weakening the assertion to a permissive provider set.

## Validation

After the required editable-install hook, the Devtool-native implementation workflow `./devtoolw android-x02` was run with a temporary construction-host execution-environment override only; the repository was restored to `native-termux` afterward.

- X02 focused contracts: 16 passed.
- Retained X01/AOPS02/AOPS04/toolchain/bootstrap contracts in the wrapper workflow: 94 passed.
- Devtool-native workflow total after both Termux-provider remediations: **110 passed, 0 failed, 0 skipped, 0 warnings, 0 errors**.
- Expanded Android planner/resumable-validation audit after the planning fix: **149 passed, 0 failed**.
- Additional retained Android/signing/device/project-runner/bootstrap audit: 123 passed; three instrumentation-planning tests fail identically on the X01 base because those fixtures contain no Gradle project markers. They are recorded as pre-existing gate audit obligations, not hidden or counted as X02 regressions.
- `python -m compileall`, TOML parse, `git diff --check`, and standalone mechanical equality are clean.

Real Gradle execution, real device/ADB lifecycle and destination/remote qualification are intentionally deferred to ANDROID-G1.

## Promise ledger

Delivered here: ANDROID-P016, P020, P021, P023, P024, P029, P031 and P048. ANDROID-P022 is partial: DeviceLease authority/stable-release adoption is present, while instrumentation install/run/evidence/cleanup adoption remains X03-owned. P017–P019 remain X03-owned.
