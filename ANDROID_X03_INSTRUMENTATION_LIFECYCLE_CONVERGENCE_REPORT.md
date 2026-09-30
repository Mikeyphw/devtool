# ANDROID-X03 — Canonical instrumentation lifecycle convergence

**Campaign position:** Android gate-window overlay **1 of 2**; full-plan overlay **45 of 70**.  
**Base:** qualified LIFE-G2 / Android-G1 environment-device authority.  
**State:** `PREPARED_LOCAL_QUALIFIED_PENDING_NATIVE_TERMUX`.  
**Next:** ANDROID-G2 — 46/70 (separate authoritative gate).

## Merge-window decision

The next-three positions are ANDROID-X03 45/70 → ANDROID-G2 46/70 → DIST-X03 47/70. They remain separate. X03 owns instrumentation semantics and lifecycle adoption; G2 is the authoritative Android instrumentation/device gate; DIST-X03 is a different distributed-provider boundary.

## Delivered semantic authority

ANDROID-X03 closes the implementation ownership exported by ANDROID-G1:

- **ANDROID-P017 — `InstrumentationIntent`:** immutable semantic intent for lane, policy, requested runner/target, selectors, runner arguments, cleanup policy and failure-evidence policy. Identity excludes transient path/process state.
- **ANDROID-P018 — `ResolvedInstrumentationPlan`:** the sole command semantic plan after exact environment, `ArtifactPair`, `DeviceLease`, destination, resolved runner/target/test package and selector command are known. The actual `am instrument` argv is consumed from this plan rather than rebuilt independently.
- **ANDROID-P019 — `InstrumentationExecutionReceipt`:** immutable receipt linking intent/plan, exact artifact/device identities, install operations, execution classification, evidence, cleanup results/authority and Android execution snapshot identity.
- **ANDROID-P022 remainder:** instrumentation install/run/evidence/cleanup is now stable-`DeviceLease` bound. Device cleanup revalidates on the exact destination serial immediately before uninstall; lifecycle cleanup refreshes LIFE ownership/protection roots immediately before destructive mutation.

## X03 convergence changes

1. Production instrumentation reuses the canonical Android environment → transport → `DeviceLease` chain from release. No device re-selection occurs between install, run, evidence and cleanup.
2. Exact app/test APKs are bound as an X02 `ArtifactPair`. The dedicated `devtool android instrumentation` path threads its in-process Gradle execution receipt into artifact lineage and the final Android execution snapshot.
3. The canonical instrumentation command is generated once from `InstrumentationIntent`, frozen into `ResolvedInstrumentationPlan`, then executed from that plan.
4. Final execution evidence is written as `InstrumentationExecutionReceipt`; when a Devtool RUN is active the private receipt is registered as a RUN attachment. Android execution snapshot composition reuses the X02 snapshot authority.
5. Instrumentation report/failure-evidence resources are projected as LIFE `GeneratedOutputDescriptor`s and the live `CleanupOwnershipView` is refreshed immediately before package cleanup. Android package cleanup remains destination-qualified and does not introduce filename/suffix deletion authority.
6. Cleanup failures are secondary evidence if test/setup execution already failed. A cleanup failure becomes primary only when instrumentation otherwise succeeded.
7. The known Android instrumentation crash misclassification is fixed: `INSTRUMENTATION_STATUS_CODE -2` accompanied by **Process crashed** is classified as setup/process failure with **0 executed / 0 failed tests**. The instrumentation action still fails through `failure_kind=setup-crash` and non-zero return status.
8. Structured report collection distinguishes setup/device/runner/snapshot/receipt/cleanup failures from genuine failed tests instead of reporting “0 failed tests” as the failure explanation.
9. Project-runner metadata now declares the instrumentation semantic contract and the `DeviceLease`/`ArtifactPair` authorities explicitly.

## Targeted qualification

The production `./devtoolw android-x03` workflow has four ordered stages:

1. source syntax;
2. X03 intent/plan/receipt and lifecycle contract;
3. retained Android instrumentation/device/release/runner regressions;
4. LIFE-G2 cleanup ownership/protection regression.

Construction-host qualification passed **210 tests, 0 failed, 0 skipped, 0 warnings, 0 errors; 4/4 stages**. The retained instrumentation/device boundary also passed **176/176** after final Gradle-receipt lineage threading. `git diff --check` and Python compile checks are clean. Production target remains `native-termux`; the desktop-only target override was used only for local construction validation and restored before packaging.

## ANDROID-G2 exported proof obligations

ANDROID-G2 must prove the complete Android-X03 lifecycle on native Termux/device authority: exact Gradle-receipt artifact lineage, one stable DeviceLease, plan-command parity, RUN receipt/snapshot linkage, process-crash classification, destination-side cleanup revalidation, LIFE protection-root refresh, and primary-failure preservation. It must also re-audit X01/X02/G1 environment/device authority so X03 cannot introduce a parallel Android truth model.

## Closure rule

This report does not close X03. ANDROID-X03 closes only after the production artifact applies on native Termux with required `./devtoolw android-x03` validation and zero errors. On success, advance to the separate **ANDROID-G2 46/70** gate.
