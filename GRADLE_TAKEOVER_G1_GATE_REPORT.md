# GRADLE-TAKEOVER-G1 — authoritative takeover / recursion / convergence gate

Date: 2026-09-29  
Gate window: **3/3** (`ENV-X05 -> EXEC-X05 -> GRADLE-TAKEOVER-G1`)  
Gradle Takeover scope: **3/3**  
Full implementation plan: **36/70**  
Status in source artifact: **pending qualified native-Termux gate**

## Qualified inputs

This gate audits the complete Gradle Takeover implementation window rather than only its own files.

- **ENV-X05 — 34/70**: qualified Termux run `f8b477190c1c4b20abed776b109d03a0`, commit `14f849f1`.
- **EXEC-X05 — 35/70**: qualified Termux run `0da5823094d04d9e90d31046e895a9d6`, commit `8181fac`; **98 passed, 0 failed, 0 skipped, 0 warnings, 0 errors**.

A successful application of this gate overlay is the authority that closes GT-P001..GT-P034. The report intentionally does not claim that qualified-host closure before the artifact has run on native Termux.

## Gate audit findings folded into this gate

### 1. GT-P033 — takeover truth existed, but presentation projection was missing

RUN/EXO evidence already carried canonical `gradle_takeover` configuration, launcher and receipt truth, but the shared presentation substrate had no Gradle-takeover projection. That meant plain, Textual and machine consumers could not report takeover state from the canonical evidence boundary.

The gate adds `gradle_takeover_projection()` to the existing presentation substrate. It is projection-only: it references canonical receipt/semantic/destination/launcher identity, does not re-plan Gradle, does not execute Gradle, and does not create a new truth store.

### 2. GT-P034 — static completion drift made normal release qualification red

`completions/_devtool` had drifted from the canonical `ZSH_COMPLETION` generator after recent command-surface additions. The existing final-release verifier correctly rejected the mismatch. The gate refreshes the canonical completion surface, including current Gradle takeover, repository-pack/signing and Textual options, then regenerates `completions/_devtool` from the source constant.

The retained completion/release slice is green after the repair.

### 3. GT-P023 / GT-P031 — semantic identity fragmented under Devtool's own workflow environment

The window audit found a deeper execution-identity defect that standalone tests could hide: equivalent wrapper/workflow/TestSpec Gradle work could receive different `gradle_takeover_semantic_identity` values when free-memory evidence changed between sequential planner calls. The exact Gradle policy digest records adaptive resource evidence (including exact available memory), and the takeover semantic identity was incorrectly importing that volatile evidence.

The gate fixes the authority boundary rather than weakening the tests:

- exact adaptive Gradle resource policy remains in the canonical plan digest and RUN evidence;
- takeover semantic identity now models **logical Gradle work**: requested/optimized roots, user argument semantics, logical invocation roots/barriers, context, launcher/destination and takeover configuration;
- non-semantic execution tuning such as an adaptive Xmx/max-worker difference no longer fragments cross-surface RER identity.

A deterministic gate test proves that different host-memory samples may legitimately generate different physical policy argv while still producing one logical Gradle semantic identity. Three consecutive Devtool-native window replays then remained stable at 36/36 before the expected non-Termux real-process stop.

## Authoritative gate choreography

`./devtoolw gradle-takeover-g1` owns one ordered workflow:

1. source syntax/compile authority;
2. full ENV-X05 + EXEC-X05 + gate-window contract audit;
3. real-process takeover proof;
4. normal completion/release qualification.

The real-process stage is deliberately native-Termux authoritative. It requires `gradle` and `java` and proves:

- shim-first PATH resolves to a real non-shim Gradle executable;
- a real bare `gradle` process executes through the shell takeover path;
- real wrapper-requested work executes through canonical Devtool ingress without recursive interception;
- WORK, PROBE and CONTROL remain distinct, with CONTROL executing the real launcher directly;
- shell coverage truthfully claims bare-Gradle coverage while refusing to claim direct `./gradlew` or arbitrary nested-process interception.

Construction hosts may exercise the same choreography only with the explicit `DEVTOOL_GRADLE_TAKEOVER_G1_ALLOW_TEST_GRADLE=1` test switch plus a gate-owned fake Gradle. The authoritative path rejects that override.

## Promise reconciliation

### ENV-X05 — GT-P001..GT-P012

Retained ENV-X05 tests plus the real-process gate prove disabled-by-default behavior, real-launcher recursion stripping, wrapper/system identity, protected bypass capability, typed intents, shell lifecycle and truthful coverage, destination-aware identity, redacted-but-identity-significant takeover config, canonical planner reuse and deliberate real/bypass diagnostics.

### EXEC-X05 — GT-P013..GT-P026

Retained EXEC-X05 tests plus the gate prove pre-EXO/RER ingress convergence, wrapper/invoke/workflow/TestSpec/validation/artifact/Android convergence, typed no-double-planning, destination/origin preservation, already-planned recursion protection, argv/cwd/environment preservation, semantic identity convergence, strict opaque-shell fail-closed behavior, RUN/EXO receipt linkage and canonical cancellation/diagnostic truth.

### Gate-owned — GT-P027..GT-P034

- **GT-P027:** shim-first PATH recursion proof.
- **GT-P028:** bare Gradle and wrapper real-process proof.
- **GT-P029:** protected internal bypass forgery rejection.
- **GT-P030:** WORK/PROBE/CONTROL process distinction.
- **GT-P031:** representative surface convergence plus real-process receipt proof.
- **GT-P032:** strict opaque Gradle escapes fail closed.
- **GT-P033:** machine/plain/Textual projection from canonical evidence.
- **GT-P034:** existing completion and final-release qualification remains green.

## Construction-host qualification

Focused window after all gate-owned repairs:

- `tests/test_gradle_takeover_g1_gate.py`
- `tests/test_env_x05_gradle_takeover.py`
- `tests/test_exec_x05_gradle_ingress.py`
- **36 passed, 0 failed**.

Completion/release regression:

- `tests/test_completion_and_manpage.py`
- `tests/test_fs03_final_release_seal.py`
- `tests/test_release_verifier_final_integrity.py`
- `tests/test_ov15_ov16_release_final_seal.py`
- **36 passed + 32 subtests, 0 failed**.

Full Devtool-native gate workflow with the construction-only Gradle substitute reached **4/4 stages green, 72 passed, 0 failed, 0 skipped, 0 warnings, 0 errors** in the workflow report. Native-host replays without that substitute repeatedly pass the complete 36-test window and then fail closed at the intended native-Termux real-process boundary.

`python -m compileall -q devtool tests/test_gradle_takeover_g1_gate.py` and `git diff --check` are also required and clean before packaging.

## Closure and next position

On qualified native-Termux success:

- Gradle Takeover window is closed: **34/70 through 36/70**.
- Remaining overlays in Gradle Takeover: **0**.
- Next overlay: **ANDROID-X01 — 37/70**.
- Next major scope: **ANDROID**.

The previously planned `v1.2.0-rc.1` release tag is not created by the construction artifact. It becomes eligible only after this native-Termux gate and the existing release qualification complete successfully; tag creation remains an explicit release action rather than an implicit gate side effect.
