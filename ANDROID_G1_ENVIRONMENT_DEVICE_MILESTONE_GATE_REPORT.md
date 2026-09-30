# ANDROID-G1 — Authoritative Android environment/device milestone gate

**Campaign position:** Android gate-window overlay **3 of 3**; full-plan overlay **39 of 70**.  
**Window audited:** ANDROID-X01 37/70 + ANDROID-X02 38/70.  
**State:** `PREPARED_LOCAL_QUALIFIED_PENDING_NATIVE_TERMUX_GATE_V3`.  
**Next after qualified success:** DIST-X01 — 40/70.  

## Merge-window decision

The next-three campaign positions are ANDROID-G1 39/70 → DIST-X01 40/70 → DIST-X02 41/70. They remain separate. ANDROID-G1 is an authoritative gate over the Android X01/X02 window; DIST begins a new ownership boundary and is not merged into this seal.

## Base truth

ANDROID-X01 is closed from native-Termux run `3f3d1caccd844e8eaa00821de79f0638`, commit `5127425`. The user reported the cumulative ANDROID-X02 v3 remediation artifact applied successfully; no X02 run ID/commit was supplied in the chat. This gate therefore re-audits the X01/X02 source and execution contracts instead of inferring closure from an unobserved X02 commit ID. Touched-file base contracts bind the gate artifact to the exact X02-v3 content.

## Gate audit loop

The gate did not merely wrap the X01/X02 test lists. Its audit loop found and folded the following gate-owned gaps before qualification:

1. **Canonical environment identity was over-specific.** `ResolvedAndroidEnvironment.environment_id` included action and workload Gradle-plan identity, so build/release/doctor/explain could never project one Android environment identity. Action/workload remain provenance and execution truth; the canonical environment ID is now environment-semantic. The linked `android.gradle` component still carries canonical Gradle context/JDK/launcher identity so dependent RER nodes invalidate when those inputs change.
2. **Doctor was still reconstructing legacy Android context truth.** Doctor now resolves and projects the same `ResolvedAndroidEnvironment` authority used by builder/release. `devtool android environment --action ...` is the canonical explain projection over that resolver.
3. **Instrumentation fixtures encoded pre-X01/X02 planning assumptions.** Fixtures now create real Gradle project markers, select the semantic device instrumentation spec instead of assuming list index 0, preserve the separate Gradle-owned build spec, and reject explicit instrumentation-lane/configured-task conflicts instead of silently overriding them.
4. **Gradle/device spec ownership is explicit.** Gradle arguments stay on the planner-owned instrumentation build spec. The device instrumentation spec is `--skip-build`, so device execution cannot silently perform a second Gradle build.
5. **Completion truth lagged the CLI.** Zsh completion now exposes the Android environment action and its `--action`, release/instrumentation `--module`, and packaged/runtime completion bytes are mechanically identical. A retained AOPS04 test was made semantic rather than freezing unrelated command ordering.

The first full workflow round exposed three additional stale instrumentation fixtures. A later retained completion/manpage check exposed the completion gap above. The audit loop continued until no gap remained.

The first native-Termux G1 replay (`e3286d8130cc4d43aa706833a7ff84a7`) then exposed two more **test hermeticity** gaps before any gate closure:

- the cross-surface environment-ID test allowed the live Termux provider set to influence one surface differently from another; and
- the incomplete-Build-Tools test could be rescued by a real Termux helper even though the fixture intended to prove an SDK-only failure.

Both are now runtime/provider-explicit. The identity test uses a synthetic Termux prefix with deterministic helper binaries across builder/doctor/explain/release, while the incomplete-package test pins a synthetic host runtime with an empty PATH. Production provider precedence is unchanged.

## Authoritative G1 proof surface

The final `./devtoolw android-g1` workflow has five ordered stages:

1. source syntax;
2. complete X01/X02 promise-window audit, including completion/public CLI parity;
3. instrumentation planner fixture reseal;
4. platform/native/bootstrap destination-authority regressions;
5. process-level Gradle/artifact/environment/device proof.

The process proof:

- requires native Termux for the production gate;
- forbids test-only Gradle/ADB/Java overrides during authoritative validation;
- invokes the host Gradle process through canonical Gradle ingress and requires a successful `GradleExecutionReceipt`;
- materializes and hashes an exact producer artifact, then builds canonical `ArtifactPair` lineage;
- resolves the canonical Android environment from explicit SDK/provider state and linked Gradle evidence;
- probes the host ADB binary/inventory separately from deterministic process-level device-topology proof;
- proves action-aware `AndroidTransport`, a stable `DeviceLease`, and readiness failure before install/device side effects;
- writes one `AndroidExecutionSnapshot` linking environment, Gradle/JDK/launcher, artifact and device authority; and
- mechanically seals the authoritative standalone Android runtime against the staged OCI copy.

On the construction host, only the final host-dependent process stage uses the explicit test-host escape plus deterministic Gradle/ADB executables. The production artifact remains `native-termux`; the authoritative apply on the user's device must use the real Termux Gradle/Java/ADB tools and cannot use those test-only overrides.

## G1 milestone promises

The gate authoritatively covers the roadmap milestone assertions:

- explicit environment cannot leak ambient SDK state;
- empty/incomplete Build Tools or required native packages cannot qualify;
- exact platform/helper/native requirements are honored;
- builder, doctor, explain and release project one canonical environment identity;
- missing/hash-drift release artifacts cannot pass;
- evidence collisions fail closed;
- failed device readiness blocks dependent side effects;
- direct/runner/release/validation consume one variant/module lane resolver;
- Host/Gradle identities are linked rather than reconstructed;
- device transport is action-aware and Host-owned; and
- destination child/bootstrap evidence returns destination-local Android environment identity rather than controller path reconstruction.

ANDROID-P001–P016, P020–P021, P023–P048 are sealed by successful G1 qualification. ANDROID-P017–P019 remain owned by ANDROID-X03. ANDROID-P022 remains intentionally partial: X02 delivered DeviceLease/stable release adoption, while the instrumentation install/run/evidence/cleanup lifecycle remains X03-owned.

## Construction qualification

After the required editable-install hook, the post-native-remediation Devtool-native gate workflow passed:

- window audit: **73 passed + 32 subtests**;
- instrumentation fixture reseal: **55 passed**;
- platform/native/bootstrap destination authority: **132 passed**;
- process proof: **passed**;
- workflow total: **260 passed, 0 failed, 0 skipped, 0 warnings, 0 errors; 5/5 stages passed**.

`python -m compileall -q devtool tests` is clean. The production target is restored to `native-termux` before packaging.

The prior v2 native-Termux apply reached validation and failed only in `roadmap-android-g1-window` with two provider-sensitive test assumptions; Devtool then verified transactional rollback to the X02-v3 state. No production Android resolver behavior was weakened by the remediation.

## Closure rule

This report is not itself gate closure. ANDROID-G1 closes only when the production artifact applies on native Termux with its required `./devtoolw android-g1` validation enabled and zero gate errors. On success, the Android X01/X02 window has zero remaining overlays and the campaign advances to **DIST-X01 — 40/70**.

## Packaging rehearsal

The v3 production artifact was applied against a pristine construction-equivalent ANDROID-X02-v3 base under canonical Devtool artifact handling. All **11 modified paths** qualified through exact inventory `source_sha256` drift protection, all **5 additions** passed conflict-fail qualification, and the 16-path overlay applied atomically without requiring construction-only Git objects. Validation then stopped only at the intentional production `native-termux` execution preflight on the desktop construction host. Devtool executed its configured rollback successfully; every modified path returned to its declared source hash and all added gate paths were removed.
