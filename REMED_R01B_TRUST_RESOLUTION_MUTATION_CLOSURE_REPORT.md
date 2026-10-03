# REMED-R01B — Trust, Resolution & Mutation Closure

**Roadmap position:** gate-window overlay **2 of 8**; full-plan overlay **72 of 93**.  
**Implementation status:** production-adopted implementation candidate; authoritative scope requalification remains owned by the later G3R gates.  
**Base:** post-REMED-R01A v5 source, content-equivalent to the successfully applied device commit `7f7b6fd`.

## Canonical scope reconstruction

R01B is the second half of the original REMED-R01 authority/trust family after the remediation roadmap was split into two smaller implementation overlays. R01A delivered the identity/configuration half. R01B owns the seven remaining R01 findings plus the shared evidence-resolution substrate needed by later G3R gates:

- `EXEC-A03` — validation state must not persist secret-bearing argv/arbitrary secret environment values.
- `ENV-A03` — Go offline policy must change real Go/toolchain/module behavior rather than emit only a Devtool marker.
- `ENV-A04` — repository-controlled configuration must not self-authorize blocked direnv/trusted host controls.
- `ANDROID-A12` — sensitive instrumentation runner values must use private binding transport and be absent from ordinary argv/evidence.
- `LIFE-A01` — one qualified `ResolvedArtifactPlan` must be the mutation authority; mutation runners must not reparse/reinterpret artifact semantics.
- `LIFE-A02` — artifact trust must resolve a real identity and cryptographically verify the exact artifact bytes under monotonically merged policy.
- `LIFE-A05` — normal artifact execution must persist a RUN-linked `ArtifactExecutionRecord`/qualification evidence.
- Shared sealability foundation — evidence references must resolve to concrete executable/durable evidence and reject missing/stale hashes.

The current authoritative promise maxima inspected before implementation are **CORE-P084, EXEC-P087, ENV-P131, ANDROID-P111, and LIFE-P127**. R01B does not redefine those ranges; its remediation findings amend their production-truth requirements and invalidate historical G3 qualification until the corresponding G3R gates rerun.

## Source → current obligation → implementation → evidence mapping

| Source obligation | Current promise/obligation mapping | Production implementation | Executable evidence | Disposition |
|---|---|---|---|---|
| `EXEC-A03` | remediation obligation `EXEC-A03`; retained EXEC protected-argv/private-validation families including `EXEC-P009` and `EXEC-P062..P066` | validation session persistence rejects raw credential argv and serializes credential environment only as protected binding references; durable attempt argv is redacted | `test_r01_validation_state_never_persists_raw_credentials` plus validation-session regressions | `IMPLEMENTED_AND_PRODUCTION_ADOPTED` pending EXEC-G3R |
| `ENV-A03` | remediation obligation `ENV-A03`; retained Go environment family `ENV-P057..P072` | `offline=true` lowers to `GOPROXY=off`, `GOSUMDB=off`, `GOTOOLCHAIN=local`, `GOAUTH=off`; the real Go subprocess receives the policy | `test_r01_go_offline_policy_reaches_real_command_environment` plus ENV-X02/Go regressions | `IMPLEMENTED_AND_PRODUCTION_ADOPTED` pending ENV-G3R |
| `ENV-A04` | remediation obligation `ENV-A04`; retained direnv/environment families `ENV-P030..P042`, `ENV-P107`, `ENV-P123` | host/user authorization variables are protected environment names and repository `[env]` overlays fail before composition | `test_r01_repository_cannot_self_authorize_direnv` plus ENV-X02 regressions | `IMPLEMENTED_AND_PRODUCTION_ADOPTED` pending ENV-G3R |
| `ANDROID-A12` | remediation obligation `ANDROID-A12`; retained instrumentation authority including `ANDROID-P017/P018`, secret-binding `ANDROID-P065`, permanent secret regression `ANDROID-P091` | raw sensitive runner args fail before `CommandSpec` construction; canonical intent stores only safe refs; secret values cross host→device via stdin at the execution edge; echoed secret bytes are redacted before result/evidence construction | `test_r01_sensitive_android_runner_binding_is_private`, `test_r01_sensitive_android_runner_config_fails_before_child_argv`, `test_r01_private_android_transport_redacts_echoed_secret` plus Android instrumentation/device regressions | `IMPLEMENTED_AND_PRODUCTION_ADOPTED` pending ANDROID-G3R |
| `LIFE-A01` | remediation obligation `LIFE-A01`; retained `LIFE-P013`, `P014`, `P018`, `P025`, `P104`, `P127` | CLI/direct apply lowers once into a qualified plan; transport bytes are rebound before mutation; overlay/bundle/snapshot mutation consumes plan mutations and immutable nested bundle descriptors; transaction deletion semantics are canonical-plan owned; obsolete alternate direct validator/delete-manifest mutation authorities are removed | `test_r01_supplied_resolved_artifact_plan_is_execution_authority`, `test_r01_resolved_plan_rejects_artifact_replacement_before_mutation`, `test_r01_bundle_runner_does_not_reparse_nested_semantics_after_plan`, LIFE-X01/X02 regressions | `IMPLEMENTED_AND_PRODUCTION_ADOPTED` pending LIFE-G3R |
| `LIFE-A02` | remediation obligation `LIFE-A02`; retained `LIFE-P005`, `P041`, `P048`, `P050`, `P101` | host+repository+artifact trust policy merges monotonically; registered identity is required when policy demands it; HMAC-SHA256 provider cryptographically verifies detached evidence against exact artifact bytes; fake/missing identity/signature fails closed; continuity fingerprint checks are enforced where declared | unknown/fake signature, valid-signature, and host-policy-cannot-be-weakened tests plus LIFE-X02 regressions | `IMPLEMENTED_AND_PRODUCTION_ADOPTED` pending LIFE-G3R |
| `LIFE-A05` | remediation obligation `LIFE-A05`; retained `LIFE-P003`, `P038`, `P101` | successful and failed normal artifact mutation results persist qualification receipt + `ArtifactExecutionRecord` as physical RUN attachments; unregistered ambient run IDs cannot self-authorize linkage | `test_r01_artifact_apply_persists_run_linked_execution_record` plus RUN catalog assertions | `IMPLEMENTED_AND_PRODUCTION_ADOPTED` pending LIFE-G3R |
| Shared evidence foundation | remediation shared sealability obligation; consumed later by all G3R scopes | `EvidenceResolver` resolves exact pytest nodes, canonical RUN attachments, source/report files and expected hashes; missing/stale references fail | `test_r01_evidence_resolver_rejects_missing_and_stale_references` | `IMPLEMENTED_AND_PRODUCTION_ADOPTED` as substrate; final evidence mapping remains R04B/G3R-owned |

## Production ingress paths changed

- Normal `devtool apply-overlay`, `apply-overlays`/bundle and snapshot mutation flow.
- Direct/library `apply_overlay`, `apply_overlay_bundle`, `apply_snapshot` entry points.
- Artifact qualification and trust-policy resolution.
- Artifact mutation transaction creation and RUN-linked evidence persistence.
- Validation session serialization/deserialization.
- Repository/global configuration environment qualification.
- Go project runner environment planning and real Go subprocess environment.
- Android project runner → instrumentation subprocess planning.
- Android instrumentation intent/plan → ADB/device execution boundary.
- Generated CORE config/help/reference surfaces for the new canonical trust keys.

## Authority inversion / duplicate-authority audit

Production mutation now follows:

`artifact bytes → ArtifactEnvelopeResolver → qualification receipt → immutable ResolvedArtifactPlan → transaction + plan mutations → RUN-linked execution record`

It no longer follows the invalid reverse pattern of “runner reparses archive → independently decides mutation → plan observes/duplicates it”. In particular:

- runner-side `read_delete_manifest` destructive authority was removed;
- the unused alternate direct `validate_artifact` acceptance path was removed;
- the runner no longer imports the old bundle semantic materializer;
- bundle nested manifests/descriptors are captured by the canonical plan and consumed from that immutable plan;
- exact artifact transport SHA is checked again at execution so same-path replacement invalidates the plan.

`_extract_archive` remains only as a private archive-safety compatibility/test primitive used by historical archive regression tests; it has no production apply caller and is not used to decide artifact mutation semantics. Legacy snapshot/cleanup destructive helpers are not claimed closed here and remain explicitly owned by REMED-R03B.

The bundle/sequential CLI still reads nested manifests for **validation orchestration** after mutation planning. That surface is not mutation authority; convergence of validation execution/state onto the canonical validation authority is explicitly owned by REMED-R02B. R01B therefore does not mislabel that remaining validation reread as LIFE mutation closure.

## Trust and secret negative cases

R01B intentionally exercises hostile cases:

- raw validation token in argv → rejected;
- repository injects `DEVTOOL_DIRENV_AUTHORIZED=1` → rejected;
- Go dependency absent with offline policy → real Go subprocess cannot fetch;
- raw sensitive Android runner value → rejected before child argv creation;
- remote instrumentation echoes transported secret → returned stdout/stderr/detail are redacted;
- nonexistent signing identity → qualification fails;
- fake signature → qualification fails;
- artifact tries to weaken stronger host trust policy → stronger policy remains effective and qualification fails;
- artifact bytes replaced after plan construction → execution fails before mutation;
- bundle runner tries to regain nested semantic parsing → regression test fails;
- unregistered ambient RUN ID → cannot become artifact execution history authority;
- nonexistent/stale evidence ref/hash → evidence resolver fails.

## Validation

Direct construction-host targeted regression boundary after the fixed-point audit:

- **300 passed, 0 failed** across R01B, LIFE artifact trust/lifecycle, archive compatibility, validation sessions, ENV/Go, Android instrumentation/device, and generated CORE surfaces.
- The first-class repository validation workflow is `./devtoolw remed-r01b`. Its three nodes cover the 291 production-scope tests; archive compatibility tests are supplementary construction evidence.
- This construction host is desktop Linux, so `./devtoolw remed-r01b` correctly refuses the native-Termux environment preflight. The artifact validation script therefore requires the workflow to replay on the user's native Termux host after transactional apply.
- A pre-existing Gradle artifact-pipeline regression (`test_jvm_gradle_package_uses_structured_events_and_collects_jar_fallback`) was reproduced identically on the post-R01A base and is not counted as R01B evidence or silently reclassified as an R01B failure.

## Historical contract disposition

Historical LIFE tests that collapsed repeated bundle writes into only the final writer were amended to preserve the stronger executable-plan invariant: the mutation sequence retains ordered writes while `ExpectedFinalTree` retains last-writer final state. This is an explicit semantic correction, not silent obligation loss. Archive root-marker/payload-safety regressions remain retained and passing.

No historical G3 is treated as current qualification. The later **EXEC-G3R, ENV-G3R, ANDROID-G3R and LIFE-G3R** must rebind current source/evidence after all remediation overlays are complete.

## Remaining campaign work

R01B closes the implementation half of the original REMED-R01 family. It does **not** seal any scope.

- **Next overlay:** REMED-R02A — Execution, Scheduler & RUN Truth, full-plan **73/93**.
- **Remaining remediation implementation overlays before requalification:** **6**.
- **Next major phase:** eight G3R requalification gates beginning **CORE-G3R 79/93**.


## REM01 whole-scope audit correction

The later REM01 fixed-point audit corrected the Android promise maximum from `ANDROID-P099` to `ANDROID-P111`. The earlier value was stale paperwork; no historical device run evidence was rewritten.
