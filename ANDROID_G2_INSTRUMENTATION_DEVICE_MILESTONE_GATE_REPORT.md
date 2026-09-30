# ANDROID-G2 — Instrumentation/device lifecycle milestone gate

**Campaign position:** Android gate-window overlay **2 of 2**; full-plan overlay **46 of 70**.  
**Base:** qualified ANDROID-X03 instrumentation lifecycle convergence.  
**State:** `PREPARED_LOCAL_QUALIFIED_PENDING_NATIVE_TERMUX`.  
**Next:** DIST-X03 — 47/70. Android resumes at ANDROID-X04 — 51/70.

## Gate audit window

ANDROID-G2 audits ANDROID-X03 and its retained X01/X02/G1 authority rather than creating a second Android model. The gate explicitly rechecks canonical Android environment/device truth, exact ArtifactPair lineage, stable DeviceLease ownership, instrumentation intent/plan/receipt semantics, LIFE cleanup ownership, and failure classification.

## Gate-owned gaps found and folded

1. **Project-runner instrumentation dropped canonical Gradle producer lineage.** The Gradle build and device instrumentation are separate CommandSpecs. The build emitted a `GradleExecutionReceipt`, but the following instrumentation subprocess reconstructed no receipt and therefore could not bind that available producer evidence into ArtifactPair/snapshot truth. G2 now recovers receipts from the authoritative current RUN execution journal across the process boundary.
2. **Project-runner instrumentation reconstructed a stripped Android config.** Multi-module instrumentation lane/module/target and destination-sensitive configuration could drift from the planner that produced the APKs. The canonical subprocess now receives the selected target and reloads the real repository target config before resolving instrumentation semantics.
3. **Destination identity was hard-coded to `local`.** ResolvedInstrumentationPlan, AndroidExecutionSnapshot and InstrumentationExecutionReceipt now consume the canonical AndroidTransport destination identity. Compatibility/injected seams without a destination explicitly default to `local`.
4. **One retained ENV-X03 fixture encoded the pre-receipt constructor signature.** It now accepts the canonical `gradle_receipts=` bridge rather than rejecting current behavior.

## Sealed lifecycle authority

A successful native-Termux G2 qualification seals:

- **ANDROID-P017** — immutable semantic `InstrumentationIntent`;
- **ANDROID-P018** — `ResolvedInstrumentationPlan` as the sole executable `am instrument` semantic argv;
- **ANDROID-P019** — immutable `InstrumentationExecutionReceipt` linked to exact plan/artifact/device/destination/snapshot identities;
- **ANDROID-P022** — one stable DeviceLease across install, runner discovery, execution, evidence and cleanup, completing the instrumentation remainder left partial at G1.

The gate also re-audits G1's environment/device authority so X03/G2 cannot introduce a parallel SDK, transport, artifact or device truth model.

## Failure and cleanup semantics

- `INSTRUMENTATION_STATUS_CODE -2` plus `Process crashed` remains a setup/process failure with **0 executed and 0 failed tests**.
- Genuine `-2` test completions remain test failures with failed test identity.
- Destination-side package cleanup revalidates the exact stable DeviceLease serial immediately before uninstall.
- LIFE ownership/protection roots refresh immediately before destructive cleanup.
- Cleanup/evidence/snapshot/receipt failures remain secondary when instrumentation already has an authoritative primary failure; successful instrumentation fails closed if required finalization fails.

## Qualification

`./devtoolw android-g2` construction qualification passed:

- **241 passed, 0 failed, 0 skipped**;
- **5/5 stages passed**;
- **0 warnings, 0 errors**.

The fresh-process stage proves project-runner target preservation, current-RUN Gradle receipt recovery, destination-bound plan identity, and process-crash classification in a separate Python process. Production target remains `native-termux`; the desktop target override and `DEVTOOL_ANDROID_G2_ALLOW_TEST_HOST=1` were construction-only.

## Native-Termux validation remediation v2

The first production candidate reached the retained runner-parity stage on native Termux and exposed two host-dependent test expectations in `tests/test_android_runner.py`. Those tests were verifying Gradle passthrough/configuration-cache behavior but inherited the process `GRADLE_USER_HOME` and hard-coded `-Dorg.gradle.vfs.watch=true`. On the production host that cache domain is shared Android storage, where the canonical planner correctly forces VFS watching off. Candidate v2 makes the tests hermetic by pinning a private synthetic Gradle user home for the two cases that intentionally prove the VFS-compatible branch. The dedicated shared-storage planner regression remains unchanged and still proves the fail-safe `vfs.watch=false` path. Production planner policy is unchanged.

## Closure rule

This report does not close G2 by itself. ANDROID-G2 closes only after the production artifact applies on native Termux with required `./devtoolw android-g2` validation and zero diagnostics. On success, advance to **DIST-X03 — 47/70**.
