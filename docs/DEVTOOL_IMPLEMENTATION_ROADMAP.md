# Devtool — Consolidated Implementation Roadmap

**Roadmap revision:** 2026-10-20 (source-audited, planning-only)  
**Repository:** `Mikeyphw/devtool`  
**Uploaded archive:** `devtool-20261020-103541-573598.tar.gz`  
**Branch at archive:** `feature/rmp01-security-remediation`  
**Exact baseline HEAD:** `6f7b1de146838dca2458eaa35d54cc8dd4445f70`  
**Working tree in supplied archive:** clean  
**Primary ledger:** `docs/DEVTOOL_IMPROVEMENTS.md` (382 lines)  
**Secondary inherited list:** `DEVTOOL_IMPROVEMENT_BACKLOG.md` (DT-001–DT-023, prior Library copy; statuses in that copy are not current-source proof)

## 1. Executive decision

**Extend existing owners; do not build parallel execution systems.** Devtool already contains the canonical EXO/DAG planner and scheduler, GitHub Actions provider and pipelines, local Actions workspace, reviewed adoption generator, source-bound remote review/collection, RUN-V3/LIFE artifacts, SSH provider, Android device/runtime interfaces, and final-seal checking. The roadmap consolidates them into an operator-facing product and closes concrete defects, consumer integration gaps, and evidence qualification debts.

**Do not classify every historical `Open` paragraph as currently open.** The append-only ledger records earlier observations followed by later implementation or bounded closure. Reconcile each issue against the *latest code plus source-specific evidence*; leave historical text intact. A merged commit is implementation evidence, **not** native/hosted/device/release qualification.

**Security boundaries that must survive every phase:** no guessed GitHub run correlation or retry after uncertain POST; no qualification from a CI green alone; no hidden release-signing, installation, root, wipe, uninstall, publication, or reboot; immutable historical receipts; read-only default; source/run/attempt/archive hash identity; fail-closed missing evidence; no unrelated RMP requalification by assertion. Termux/PRoot on the same phone cannot count as independent device/worker substrates.

## 2. Corrected status snapshot

| Finding / campaign | Evidence observed in supplied source | Roadmap disposition |
|---|---|---|
| QW-ERG-001 and QW-SRC-002 | `qualification_workflows.py` has `WAITING_REMOTE` observation and `actions_dispatch.py` enforces `expected_source_sha` before POST | **Implemented in source**; test status semantics, then close historical entries by addendum, not duplicate code |
| QW-PERF-005 / Phase E02 | Stage durations exist; `final_seal._workspace_backup_paths()` invokes `discover_backup_candidates(..., managed_only=True)` | **Optimization present**; measure native Termux latency; profile before further edits |
| RS11 sync fixed point | `ssh_workspace.py` excludes private/cache state and emits `--omit-dir-times` | **Implemented in source**; still needs real fixed-point and transfer-inventory acceptance where not already recorded |
| RMP-C02, RMP-C03, RMP-H0-B01/C01 | Native bounded closure records at `ec6ed8a`, `36e7db6`, `3f98789` respectively | **Protected CLOSED in those exact scopes**; regression only, do not reopen by default |
| RMP-H1/H2 | Multiple source patches for N02/C04/C05/C06 and Q01; the ledger states independent N01/U01/TEST-A11/Android HUD and other external gates remain | **Partially remediated**; independent acceptance and formal closure still separate |
| UX01 / UX02 / A03 / A04 | Local Actions UI, `review`, `collect`, adoption generator and corrected `--machine-file invoke` exist | **Source implemented**; verify current-source native and hosted consumer acceptance before marking complete |
| A06 | `pyproject.toml` includes `core_cli_schema.json`; wheel test present | **Source fixed**; preserve isolated-wheel regression and hosted installation smoke |
| A08 | `actions_private_handoff.py` + local evidence checks present | **Source implemented**; real consented local handoff/install/export not implied by source tests |
| A09 | HEAD `6f7b1de`; Go host admission and pinned setup-go present, `tests/test_actions_a09_ci_host_contract.py` | **Source implemented, native and second-consumer hosted qualification pending** |
| SKY-ACTIONS-APK-META-012/013 | `_inspect_apk()` still requires nonempty `versionCode` and reads only `badging` | **True Devtool core gap**; implement role-aware test APK inspection |
| SKY-ACTIONS-COLLECT-008 | `actions_android.py` has `_WORKFLOW = "devtool-android-build.yml"` | **True Devtool core gap**; make explicit workflow identity verifiable |
| ACTIONS-A08 G8 G2 provenance | Ledger records an inherited frozen-hash mismatch | **OPEN**; explicit, narrow source-history amendment, no broad pin rewrite |
| A04 RUN-V3/LIFE local self-update | `artifact_cleanup_worker.py` can refuse uncorrelated durable catalog with documented post-commit issue | **OPEN**; fix metadata continuity rather than suppress cleanup refusal |
| Six external audit requirements | `ENV-A06`, `ANDROID-A16`, `DIST-A01`, `DIST-A12`, `DIST-A13`, `TEST-A11` | **OPEN** until independent required real-substrate receipts exist |

**Secondary backlog reality check:** DT-004's requested `config doctor/migrate` partly exists today; DT-019's named DAG planning has a supported current `devtool plan --target <target> <workflow> --json` route in generated CI; DT-009 has a source graph/schedule model; DT-014 overlaps finished RMP-C02 security fixes. Retest current behavior and target only the remaining UX/API gaps, rather than adding duplicate commands.

## 3. Dependency order and delivery strategy

```text
R0 ledger+current-source evidence reconciliation
  ├─> R1 reliability/packaging/provenance blockers
  │     ├─> R2 real A09 + cross-repo CI admission
  │     ├─> R3 Android Actions/installed-APK contract
  │     └─> R4 canonical lineage + qualification taxonomy
  │              ├─> R5 Actions operator experience
  │              └─> R6 DAG/adoption/overlay operator experience
  ├─> R7 SSH/Termux/qualification performance (parallel after R1)
  └─> R8 RMP H1/H2 external acceptance (independent of UX)
R2 + R3 + R4 + R5 + R6 + R7 + R8 -> R9 system acceptance and release-readiness evidence
```

Ship **one consolidated, source-pinned overlay per coherent root cause** (not three tiny patches every turn); batch only compatible changes with a shared boundary. A single larger overlay must still have: original SHA and required preimages, transaction/rollback plan, focused and adversarial tests, clean disposable apply with real CLI, automatic intended Git commit, machine-readable receipts, and narrow Termux rendering. Never apply a generated patch to an unknown newer HEAD. Avoid rerunning expensive CI24/G3R/FS03 for *documentation-only* or already qualified old commits; run complete qualification on each release candidate or changed trust-gate source as dictated by its acceptance policy.

## 4. Implementation milestones

### R0 — Evidence-first ledger reconciliation and baseline inventory (P0; smallest independent patch)

**Goal:** Create one authoritative current-source issue map while preserving the cumulative history.

**Work:**
1. Add `docs/DEVTOOL_IMPLEMENTATION_ROADMAP.md` to the repo once reviewed. Add a machine-readable traceability index, e.g. `docs/DEVTOOL_IMPROVEMENT_INDEX.json`, with stable IDs, `historical_status`, `source_status`, `qualification_status`, `affected_paths`, `test_paths`, dependencies and evidence refs.
2. Import DT-001–DT-023 as **secondary** backlog and map overlaps (no new duplicate issues). Add superseding ledger entries for QW-ERG/SRC/PERF, FS03, RS11 and packaged A06.
3. Inventory Git source HEAD, exact workflow/CLI surface, protected gate tests, registered action commands, installed wheel contents, original 308 RMP issues/crosswalk, local and remote receipts; mark *unknown* explicitly.
4. Establish a disposable baseline fixture + current-source test selection and a JSON status generator. Test stale/forged/other-SHA receipts and clean-tree/no-mutation guarantees.

**Exit:** Every actionable ledger issue has one current disposition (`DONE_SOURCE`, `NATIVE_QUALIFIED`, `HOSTED_QUALIFIED`, `DEVICE_QUALIFIED`, `BLOCKED_EXTERNAL`, `OPEN_CODE`, or `HISTORICAL`) and an evidence pointer. Nothing is retroactively promoted or erased.

**Likely files:** `docs/DEVTOOL_IMPROVEMENTS.md` (append only), new index, tests for index schema and continuity.  
**Dependencies:** exact `6f7b1de...` baseline or independently refreshed HEAD.

### R1 — Trust-critical reliability consolidation (P0; first code overlay)

**Goal:** Make installed Devtool, self-updates, and frozen source evidence reliably usable before generating more consumers.

**Work:**
1. Audit/fix **repository-local RUN-V3 catalog self-update**: persistence of artifact execution, after-commit process continuity, source-identical LIFE cleanup, idempotent read-only recovery, uncertain-vs-failed-vs-receipted outcomes. Do **not** erase source on missing receipt or bypass catalog authority. Retain the external `DEVTOOL_XDG_DIR` working path as an acceptance comparator.
2. Verify current wheel includes and loads `core_cli_schema.json` after real `pip wheel` / clean `pip install`; exercise `plan`, `preflight`, `invoke --machine-file`, wrapper alias, and Actions command dispatch via *installed* wheel, not local `PYTHONPATH`.
3. Resolve the separate A08-era **G8 G2 `actions_pipeline.py` source hash drift** with immutable ancestor verification and a narrowly scoped amendment; do not rewrite original G8 evidence.
4. Close A04 nested Termux fixture/substrate ambiguity using real `sys.executable`/`sys.base_prefix` identity and non-Termux Android negative fixtures. Validate A08 local-handoff preview and signer mutation guards independently of any install.
5. Improve actionable diagnostics for the observed `--auth auto` vs `--auth gh`/resource-404 ambiguity, without exposing tokens or auto-redispatching.

**Exit:** Native Termux source-bound apply+commit/rollback PASS; local and clean wheel CLI PASS; RUN-V3 local and external-root cleanup truth agrees under kill/timeout/reused-path tests; G8 source amendment passes strict negatives; no CI or release status promotion.

**Likely files:** `devtool/artifact_cleanup_worker.py`, `devtool/cli.py`, RUN-V3 catalog/receipts, `devtool/actions_pipeline.py` provenance tests, `pyproject.toml`, `tests/test_actions_a04_runtime.py`, `tests/test_actions_a06_wheel_distribution.py`, cleanup tests. **Inspect first and touch only proven defects.**

### R2 — Hosted Actions adoption and platform admission (P0; consumer-qualified)

**Goal:** Prove A09's generated CI actually works end-to-end on more than one real consumer, without repeating the existing G5 polyglot matrix.

**Work:**
1. Complete A09 native Termux qualification at the *current* Devtool SHA. Preserve byte-for-byte historical A03 `python-shell` generated workflow when applicable; `Go` remains explicitly `ci_host_profile=go` with pinned `setup-go` and `go-version-file`.
2. Use a review-only, clean/source-pinned **Skybind Go `native`** consumer adoption; verify generated two-file delta and the selected `foundation-go` job. Explicit user-reviewed write/commit and hosted workflow dispatch only as a separate authorized action. Verify exact source/attempt/job/artifact evidence via existing Actions review/collect, preserving `source_attested:false` unless separately authenticated.
3. Reconcile **Cinderbox A07/A10** historical hosted native and legacy-fallback results. For A10's native-default conversion, preserve complete original PR/push workflow triggers and all M3–M24 regression/instrumentation coverage before retirement of redundant legacy implementation; never infer that success of one lane proves trigger parity.
4. Add **typed host profiles only from proven consumer requirements** (e.g. safe Java/Android-hosted build prerequisites where applicable), reusing existing ACTIONS-05/G5/Gradle owners. Explicitly refuse unknown SDK, root, signer, device, privileged, opaque or cross-workflow jobs; do not create another scheduler.
5. Test three directions: previously accepted ordinary Python-shell YAML unchanged, valid Go host provisioned, unsupported host rejected before workflow creation.

**Exit:** A09 native PASS, one independently real hosted Go consumer with source/run/attempt/artifact receipt, Cinderbox native/legacy acceptance carried without false equivalence, and actionable blocked reasons for unsupported host profiles.

**Likely files:** `devtool/actions_adoption.py`, `tests/test_actions_a09_ci_host_contract.py`, generated workflow tests, existing Actions-03/04/06 verifier tests. Consumer source files only after explicit review.

### R3 — Android artifacts, workflow identity, and safe device adoption (P0/P1)

**Goal:** Remove Skybind's one-off instrumentation APK metadata compatibility workaround by upgrading the shared Devtool provider.

**Work:**
1. Add role-aware Android package inspection: `application | instrumentation`. Regular app must have valid positive numeric `versionCode`; an instrumentation APK may have empty `versionCode` only in explicit test-role context. Read compiled Android manifest (`aapt dump xmltree` / `aapt2` equivalent) for exactly one instrumentation declaration and a matching target package. Require correct test package, one signer/certificate identity, genuine signature verification, and original source/ZIP/member hashes. Reject duplicate or ambiguous fields.
2. Parameterize the Actions Android workflow identity instead of `_WORKFLOW = "devtool-android-build.yml"` as the only acceptable source. Verify caller-approved **exact workflow ID** together with GitHub repo, run ID/attempt, ref, SHA, registered artifact and manifest; avoid string-only acceptance.
3. Provide reusable read-only Android PackageManager observation: disambiguate `pm path` exit 1 and empty output via independent exact package listing under the same user/transport; fail closed on service exceptions and contradictions. Do not treat shell UID output as privileged broker attestation.
4. Add optional **explicitly consented** device-artifact adoption with unique ADB transport, app/test signer continuity, installed byte or platform evidence and immutable receipt. Never install, uninstall, clear, replace, or request root by default. Keep A08's local handoff distinctly **signed-unqualified**.
5. Exercise real Termux `aapt/aapt2`, `apksigner` and Android test APK positive/negative fixtures; verify against the existing Skybind paired artifact *without re-dispatching its already completed hosted build merely for parser compatibility*.

**Exit:** Native upstream `inspect_android_zip` handles legitimate `androidTest` APKs; wrong target, spoofed manifest, bad version, signer mismatch, tampered ZIP, wrong run/workflow and ambiguous package state all block. Real device evidence is reported in a **separate** gate; no cross-UID broker or release qualification assumed.

**Likely files:** `devtool/actions_android.py`, Actions-05/04 tests, shared Android device helpers, CLI schema, narrow Skybind adopter tests. Traces: `SKY-ACTIONS-APK-META-012/013`, `SKY-ACTIONS-COLLECT-008`, `SKY-B1C-PM-014`, `SKY-B1C-DEVICE-013`, DT-002/003/010/017.

### R4 — One execution/evidence lineage contract (P1; prerequisite for full UX)

**Goal:** Allow an operator to follow source -> local DAG node/EXO -> Actions request -> authenticated remote run/attempt -> archive digest -> native/device gate -> qualification, **without a second scheduler/store**.

**Work:**
1. Build an additive **read-only projection** over canonical existing receipts, with explicit source SHA, repo, target/workflow, EXO ID, dispatch request, run/attempt, artifact ID/digest and qualification authority. Existing systems remain owners.
2. Normalize nonterminal/terminal states across projections: `NOT_STARTED`, `PREFLIGHT_BLOCKED`, `DISPATCH_UNCERTAIN`, `WAITING_REMOTE`, `CI_FAILED`, `CI_PASS_UNVERIFIED`, `ARTIFACT_VERIFIED`, `DEVICE_NOT_RUN`, `DEVICE_QUALIFIED`, `RELEASE_BLOCKED`, `RELEASE_QUALIFIED` (or a compatible machine-enum map). Pending observation **never** becomes passing qualification from exit code zero or a UI badge.
3. Derive a common typed artifact evidence view across Go/Android/ZIP/web outputs where supported; keep producer-specific signing/package conditions and explicit `not_applicable`/`not_verified` fields.
4. Add cross-repo source and attempt mismatch, empty log, unknown receipt, forged-case selection, truncated UI failure and duplicate-ID negatives. Use RMP-H1 secure redaction and expected-case authority rather than replicating its code.
5. Expose JSON, compact CLI and renderer-neutral UI-core projection from the same reducer. Reuse canonical `core_cli_schema.json`/wrapper constraints.

**Exit:** End-to-end read-only lineage fixture works after process restart, survives missing remote data as `UNKNOWN/PENDING`, preserves original evidence identity, and cannot falsely promote a failed or source-mismatched run.

**Likely files:** existing `devtool/actions_*`, EXO machine state, `devtool/workflow_graph.py`, `devtool/presentation_surfaces.py`, `devtool/ui_core/*`; new projection helper + contract tests. Traces: DT-006/010/017/023, QW-ERG-001, ACTIONS-UX02, RMP-C04/C05/C06.

### R5 — One cohesive Actions operator journey (P1; based on R4)

**Goal:** A user can discover a workflow, preview it, intentionally dispatch, follow the exact run, inspect errors, and collect verified artifacts without manually carrying multiple IDs.

**Work:**
1. Extend **existing** UX01 `actions ui` and UX02 `actions review/collect`; don't replace their journal or provider. TUI panels: Workflows, Reviewed plan, Requests, Remote runs, Jobs, Artifacts and Evidence. Default to read-only; display which parts are local-only versus authenticated remote status.
2. Add a bounded, resumable **exact-request follow** operation which always reuses retained request/run ID and attempt, surfaces token/source/permission errors and pauses on uncorrelated 204. Never select a run by temporal proximity and never auto-retry an uncertain POST.
3. Type-check form inputs against GitHub workflow schema; show immutable source, permissions, side effects and confirmation immediately before dispatch/installation/collection. Provide JSON/non-TTY fallback with no forced Textual dependency.
4. Make actionable but secret-safe 40–60-column Termux views and copyable IDs. Avoid duplicate Android HUD/toast alerts via shared user-global notification policy once native acceptance for UX-NOTIFY-01 is confirmed.
5. Optional reviewed wrapper aliases and desktop/HUD projections should reuse the same exact-context reducer and action catalog.

**Exit:** One documented operator session completes review -> authorized dispatch -> source-correlated remote follow -> digest-verified collection. Adversarial uncertain/missing/failed/tampered/foreign-repo runs never become green. Ctrl-C/restart leaves recoverable journal status and no duplicated POST.

**Likely files:** `devtool/actions_cli.py`, `devtool/actions_pipeline_textual.py`, `devtool/textual_control_center_runtime.py`, `devtool/ui_core/*`, wrapper schema, tests for narrow displays and non-TTY. Traces: DT-001/007/015/018, ACTIONS-UX02 and local ACTIONS-UX audit.

### R6 — DAG, repo adoption and transactional overlay ergonomics (P1/P2)

**Goal:** Make existing DAGs and source-bound Devtool migrations easy to understand and maintain; no invented replacement DAG engine.

**Work:**
1. Revalidate DT-019 against *current* `devtool plan --target T WORKFLOW --json` and its current `--dot`/schedule implementation. Add missing first-class named-graph UX only if a gap is reproduced. Show dependency blockers, safe waves, resource reservations, source boundary, node status, measured bottleneck and plan-vs-runtime parity.
2. Add readable DOT/ASCII/Mermaid/JSON views as additive projections, with deterministic 40-column Termux fallback and complete machine IDs. Do not loosen `workflow_scheduler.py` read/write collisions to gain artificial speed.
3. Integrate review-only adoption doctor: existing `config doctor/migrate`, current capability matrices, legacy Gradle config compatibility, custom variant allowlist and common generated-path `.gitignore` guidance. Use existing `actions adopt` rather than a new one-off project bootstrapper.
4. Offer source-aware overlay **plan/upgrade/recover** around RUN-V3/LIFE: preimage drift and already-applied equivalence, no silent rebase, hash-pinned manual reconciliation, safe resume from `rollback-failed`, deterministic bundle lifecycle/cleanup. Do not auto-delete original user ZIP or retained evidence outside explicitly receipted policy.
5. Add true producer->consumer DAG contracts (Devtool -> Cinderbox/Skybind/BKX/chrootctl/Studyflow) with minimal per-repo configuration, rather than maintaining duplicated shell DAGs.

**Exit:** A named workflow has an accurate static plan and actual EXO receipts; cycles and collisions cannot be misrepresented as parallel; upgrade/recovery is transactional and reversible; existing working config and 24 public compatibility contracts remain intact.

**Likely files:** `devtool/workflow_graph.py`, `devtool/workflow_scheduler.py`, `devtool/cli.py`, config migration/doctor, overlay runner and transaction modules, adoption templates, current source tests. Traces: DT-002/003/004/005/008/009/011/012/013/014/016/019/020/021/022.

### R7 — SSH/Termux worker reliability and seal performance (P1; independently parallelizable)

**Goal:** Close remaining operational gaps without converting PRoot into a fictional independent host.

**Work:**
1. Keep RS08 absolute, pre-probed remote HOME and `--protect-args` rsync return guarantees; add durable source-bound receipts at sync, tmux, return and clean second-plan stages, explicit interrupted-return recovery and no automatic deletion of failed-run evidence.
2. Measure RS11/RS12 fixed-point sync on real Termux/PRoot: warm sync median, source scan, SSH round trips, return time, second plan changes; confirm anchored private exclusions and no stale sensitive mirror paths. Avoid timed speed claims without native measurements.
3. Improve native Termux vs guest PRoot detection and opt-in RS12 supervisor integration, refusing unsafe stop/restart when remote tmux jobs may be present. No always-on daemon unless separately authorized.
4. Benchmark QW-PERF on real current source with per-check FS03 data; verify managed-only scan remains complete. Optimize only a measured new bottleneck; fail closed on scan timeout and keep independent archive hashing.
5. Qualify UX-NOTIFY-01 with explicit `on|off|auto` and source-controlled Android package detection, no double toast/notification and no environment-leaking test fixtures.

**Exit:** Read-only SSH plan fixed point, durable interrupted-operation receipts, no secret transfer, bounded source-scoped performance evidence, successful narrow-terminal feedback and no weakened FS03/remote-source gate.

**Likely files:** `devtool/ssh_qualification.py`, `devtool/ssh_transport.py`, `devtool/ssh_workspace.py`, `devtool/ssh_returns.py`, `scripts/devtool-proot-worker.py`, `devtool/final_seal.py`, `devtool/apply_notifications.py`. Traces: RS08/11/12, QW-PERF-005, UX-NOTIFY-01.

### R8 — Remaining RMP H1/H2 formalization and external-authority gates (P0 for security; separate acceptance track)

**Goal:** Make the risk register truthful and close only findings with independently authoritative evidence.

**Work:**
1. Treat C02, C03, B01 and C01 closure commits as immutable and test continuing compatibility; do not rerun their historical acceptance solely to fill roadmap checkboxes.
2. Reconcile H1 Phase 4's reported native 206-test success against its exact SHA and receipt; complete formal individual N02, C04, C05, C06 assessments including persistence read paths, original TEST-G3 expected-case authority, Android HUD on-device behavior, and nested secret-safe projection invariants.
3. **N01:** Acquire genuine authoritative original 308-finding roadmap evidence before formal historical provenance closure; hash equality of the retained unsigned copy alone is insufficient.
4. **H2/Q02:** Preserve historical 20 probes/six mismatches and eleven older test contracts; repair and requalify reproducible live defects one by one, not by editing old reports. Q01 must keep bounded cleanup confinement; U01 remains open until independently covered.
5. Discharge the six exact real-substrate obligations (`ENV-A06`, `ANDROID-A16`, `DIST-A01`, `DIST-A12`, `DIST-A13`, `TEST-A11`) only through proper native Android, independent SSH worker, hosted remote-run, or other required authority and fresh source-bound negative cases. Identify unavailable substrate as `BLOCKED_EXTERNAL` rather than failing all unrelated milestones.
6. Preserve no-secret/no-release/default-deny behavior and explicit `release_qualified=false` until the formal owner gate authorizes promotion.

**Exit:** Per-issue immutable source+receipt evidence; no orphaned assignments, no false H1/H2 blanket closure, and independently verified satisfaction (or honest pending status) for all formal blockers.

**Likely files:** `docs/RMP_FINAL_HARDENING.md`, H1 phase docs and 308 crosswalk, real-substrate gate test modules, `devtool/validation_sessions.py`, `devtool/presentation_surfaces.py`, `devtool/environment_composition.py`, `devtool/test_runtime_optimization.py`.

### R9 — Cross-campaign acceptance and controlled consolidation (final milestone)

**Goal:** Validate the **resulting** source commit and externally adopted consumers, not a patchwork of unrelated old greens.

**Acceptance ladder (independently reported):**

1. **Source/disposable:** clean install, stable public CLI/schema, full focused regressions, adversarial mutations, overlay transaction/rollback, no unintended files.
2. **Native Termux:** exact-HEAD installation and genuine `devtool` CLI tests, wheel, worker, Textual narrow UI, receipts and bounded stress/performance checks. Ubuntu PRoot may validate Linux guest support but must be recorded as same-phone.
3. **Hosted GitHub:** source-bound CI24/24-shard summary and registered archive verification; native 13-lane G3R and FS03 on the *same* resulting SHA as required; consumer Actions A09/Android tests with source, workflow, run, attempt and artifact identities.
4. **Real device/independent worker:** separately authorized Android test or independent trusted SSH worker gates; explicit NOT_RUN where missing. No implicit installation, root, signing or deployment.
5. **Formal release/security:** require all six independent obligations, RMP external authorities and any project-specific device/signer policy. Only then let the original release owner decide promotion. Preserve all original receipts and open limitations.

**Exit:** A current-source evidence index and written acceptance matrix with `source_pass`, `native_pass`, `hosted_pass`, `device_pass`, `formal_release_qualified` **separately**; no ambiguity about what is completed or still blocked.

## 5. Traceability: legacy DT backlog to milestones

| Original IDs | Roadmap destination | Important caution |
|---|---|---|
| DT-001, DT-007, DT-015, DT-018 | R4/R5 | Reuse existing UX01/UX02 journals, no uncertain POST replay |
| DT-002, DT-003, DT-005 | R3/R6 | Fail closed on Gradle unknowns and custom variants |
| DT-004 | R0/R6 | `config doctor/migrate` already exists; verify remaining compatibility |
| DT-006, DT-010, DT-017, DT-023 | R4 | One read-only lineage/evidence projection, not a new store |
| DT-008, DT-014, DT-020, DT-021 | R1/R6 | Preserve RUN-V3/LIFE and explicit source authority |
| DT-009, DT-012, DT-019 | R6 | Named DAG plan and safe wave logic partly exist |
| DT-011, DT-016 | R2/R6 | Extend existing adoption generator/capabilities |
| DT-013, DT-022 | R6 | Tracked pipeline manifests vs private generated paths |

## 6. Implementation packaging and control loop

For each R1–R8 implementation campaign:

- **Inspect:** Read current HEAD + touched file checksums; enumerate provider and consumer contracts. No patch against the 2026-10-20 SHA after unrelated user commits without rebasing under guard.
- **Design:** A single root cause, minimal coherent files, explicit producer->consumer interface, test-first negatives and backwards compatibility. Keep original issue IDs.
- **Build:** One consolidated overlay ZIP with machine manifest, commit declaration, preimage SHA, bounded validation and a rollback procedure; avoid force flags and source substitution.
- **Validate:** Local disposable full installer; current-source Termux validation; test *installed-wheel* CLI where relevant; no unapproved side effects.
- **Accept:** Native source-bound JSON receipt, hosted/device evidence only as genuinely executed, follow-up ledger append and machine index update; mark `DONE_SOURCE` separately from `QUALIFIED`.
- **Transfer:** Provide **one** ZIP and **one** Termux command for ordered apply only when all included overlays have explicit compatible source transitions; clean up consumed bundle according to user-authorized rules and retain receipts.

**Stop / investigate conditions:** SHA drift, untrusted remote run or 204 ambiguity, missing LIFE cleanup receipt after source removal, unsupported host/toolchain, incomplete instrumentation, mismatch in test-case identity, unexpected Android device transport, private-state transfer, evidence corruption, or failed rollback.

## 7. Immediate first consolidated batch recommendation

**Start with R0 + R1 diagnostics + R2 acceptance plumbing; hold major new UI/DAG redesign until the contract is stable.** Suggested first deliverable name: `DEVTOOL_LEDGER_FOUNDATION_R01`.

**In scope:** append-only current-source index, regression confirming wheel/CLI schema, reproduction and narrow repair of local RUN-V3 self-update catalog continuity (only if reproducible on disposable source), source-pinned G8 G2 reconciliation, native A09 ready-check/host-profile receipt and focused Termux fixture acceptance, plus one consolidated regression plan for existing A04/A06/A08/UX02. Keep A09 generated YAML backward compatible.

**Out of scope:** unrelated RMP formal closure, Android installation, release signing/publication, whole DAG scheduler rewrite, new asynchronous remote dispatcher, blanket Python/Go/Android cross-product test matrix, and a broad historical CI rerun.

**Acceptance for R01:** real clean disposable apply + commit and rollback test; focused trust-negative tests; current-source native Termux acceptance; installed-wheel smoke; receipt-bound status reconciliation; nonmutating Go consumer preview and explicit hosted-consumer follow-up plan. The resulting qualified SHA becomes the baseline for R2/R3/R4.

## 8. Current source audit notes (reproducible)

- Git HEAD: `6f7b1de146838dca2458eaa35d54cc8dd4445f70`; latest commit title `feat(actions): admit Go source DAGs in cross-repo CI A09`; no Git status changes in the uploaded worktree.
- `devtool/actions_adoption.py` and `tests/test_actions_a09_ci_host_contract.py` implement Go host-provisioning source and negative fixtures, with the source docs in `docs/actions/ACTIONS_A09_CROSS_REPO_CI_HOSTS.md`.
- `devtool/actions_android.py` line near `_inspect_apk` uses `versionCode='([^']+)'` and has constant `_WORKFLOW = "devtool-android-build.yml"` — visible core holes, not merely stale ledger reports.
- `devtool/final_seal.py` uses `discover_backup_candidates(..., managed_only=True)`; `devtool/ssh_workspace.py` includes `--omit-dir-times` and anchored private exclusions.
- `pyproject.toml` explicitly includes `core_cli_schema.json` in `devtool` package data.
- `docs/DEVTOOL_ACTIONS_WORKSPACE.md`, `docs/DEVTOOL_ACTIONS_EXACT_HANDOFF.md`, and `docs/DEVTOOL_ACTIONS_ADOPTION.md` document the shipped UX01/A02/A03-A04 operations. Their current presence alone does not prove live hosted consumer behavior.

**Scope note:** This roadmap is a planning artifact. No repository source, ledger or remote resource has been changed; no CI, device, worker, signing or release operation has been executed as part of authoring it.


## R02 implementation addendum — 2026-10-20

R02 source work is consolidated in `devtool/actions_adoption.py`, the local
acceptance module `devtool/actions_r02_acceptance.py`, the review matrix in
`docs/actions/ACTIONS_R02_CONSUMER_REVIEW.md` and R02 regressions. Immutable
source-bound consumer fingerprints augment A09's existing Go host admission.
Go host support is declarative until independent remote source/run/attempt
verification; Cinderbox hosted Android acceptance remains historically scoped.
No Skybind or Cinderbox files are mutated, no Actions run is dispatched, and
R02 exit remains pending native Termux and real hosted Go consumer evidence.

## R03 implementation addendum — 2026-10-20

The R03 source-bound provider repair adds native Android instrumentation APK metadata
inspection and selected custom GitHub workflow verification without introducing a
parallel archive registry, CI scheduler, broker identity oracle or release policy.
Explicit test APK and target package pins plus a real compiled manifest check
replace the earlier one-off Skybind H3/H4 format workaround. The default
`devtool-android-build.yml` collection retains its historical CLI behavior;
a custom workflow additionally requires independent source SHA and attempt pins.
The separate read-only ADB PackageManager observer distinguishes true absence
from errors using a second exact-user query but does not qualify device identity.

**Acceptance:** focused source and canonical generated-surface regressions,
then actual Devtool disposable overlay application and native Termux current-source
validation. Real APK/`aapt` and hosted source/run/archive acceptance are independent;
installed-pair adoption, broker identity, DT-002 convention plugins, and DT-003 custom
variants remain scoped follow-ups. Existing R02 hosted Go consumer remains open.


## R01–R03 milestone closure update — 2026-10-20

- R01: `CLOSED_LOCAL_TERMUX` for scoped native overlay, 56 regressions, RUN-V3 recovery, G8 amendment and isolated wheel; no remote/device/release promotion.
- R02: `CLOSED_LOCAL_TERMUX` for hosted-adoption planning/review and 34 source tests; the independently authenticated second-consumer hosted Go run is still pending.
- R03: `SOURCE_VERIFIED_NATIVE_APPLY_REPORTED` after 101 source/disposable tests and operator-reported native installation. Native machine receipt and real Android APK/host/device acceptance remain separately pending.
- The machine-readable ground truth for these statuses is `docs/actions/DEVTOOL_R01_R03_SCOPED_CLOSURE_20261020.json`. This closure does not grant formal release qualification, does not assume a CI success from local tests and does not retire the independent RMP or six real-substrate gates.
- Start R04 only after preserving the R03 source identity; R04 must not silently promote the remaining R02/R03 external gates.


### Native closure installer timing repair (2026-10-20)

An attempted R01–R03 closure installation failed a pre-existing CORE-X04 5-second generated-surface budget at 5630ms after 73 passing tests. Treat the closure as **NOT ACCEPTED** until the replacement passes on Termux. Retain the original 5000ms budget and optimize the source projection by indexing option rows once; do not bypass tests or modify the independent remote/device/release obligations. A failed transaction must finish and restore a clean post-R03 source before the replacement overlay is attempted.


### R04 — Offline evidence-lineage foundation (candidate source, 2026-10-20)

The source-bound R04 overlay adds `devtool actions lineage --pipeline-id` with plain-text and machine JSON, projecting canonical local ACTIONS-06 pipeline journal + ACTIONS-02 dispatch receipt and optional local artifact-registry hash/ZIP proof. Explicit unknown and pending states remain when remote API authority, exact EXO child identity, or device/release receipts are unavailable. Source-only regression and disposable installation are NOT equivalent to native acceptance. **R04 full closure remains OPEN** until second-phase EXO correlation and independently authenticated run/attempt/artifact verification pass. R05 can use this projection after current-commit Termux acceptance; no background scheduler or new journal was introduced.

Original closure run `ac0b291eba434cbf8981b5f019c0a10a`: 131 native tests passed, commit `52ad3d2` and seven-path verification succeeded, but LIFE artifact cleanup failed. Preserve that historical failure and avoid speculative cleanup changes; R04's own fresh transaction will provide evidence of whether it recurs. Do not mark the closure transaction passed merely because its source was committed.


### R04 native retry — launcher cold-start test timeout (2026-10-20)

R04 attempt `b8c882f722c34d2a99e50233767312b6` reached 77 passing tests before the pre-existing CORE-X04 wrapper self-test timed out waiting 20 seconds for the source-aware `devtoolw --help` subprocess. This does not demonstrate a parser/source failure, and is not accepted as R04 native qualification. Replace R04 (not stack on top) with the R04-R1 variant based on unchanged `52ad3d2` tree. It separates fast wrapper argv validation from the slower real parser smoke, retains a finite Termux bound and existing independent generated-surface performance limits. Require confirmed fail-closed rollback/clean tree before applying and do not infer LIFE success from prior Linux success.


### R04B — Source-bound native EXO and optional hosted observation (2026-10-20)

User-provided R04-R1 Termux installation `078eb17498bf4cbc8b37e697e9c18a5a` completed 88/88 tests, exit 0, commit `244d6d7`, and clean LIFE cleanup. The historical closure cleanup failure remains a distinct recorded failure. New R04B overlay extends R04's existing journal to record a one-use, parent-verified child RUN-V3 ID for fresh `local-invoke` nodes; projection verifies exact finalized child identity and keeps historical gaps unfilled. Credentialed `actions lineage --live` queries exact request-bound GitHub runs via GET only and labels live attempt observations without treating them as persistent pins or artifact/release authorization. Source/disposable validation is separate from pending native-hosted and R09 formal gates. R05 integrated UI should consume these fields without inventing new scheduling or authentication semantics.


### R05 — Integrated Actions operator UI (candidate 2026-10-20)

Native R04B acceptance from run `7524f3b5b2f54e519036682a0331dd05`: 113/113 tests, native commit `f4d5532`, sixteen verified paths and fresh-process LIFE cleanup PASS. R04B local implementation may be scoped accepted, with real authenticated hosted observation and producer-signed artifact qualification still outstanding. R05 extends the existing `devtool actions ui` and source-bound Actions journal: on-demand exact GET review, bounded follow, offline R04B lineage, GET workflow inventory, and explicit two-step artifact collection (name + Enter + `y`). No automatic network on mount/refresh, dispatch, replay, remote mutation, or release promotion. Read-only terminal fallback and existing CLI commands remain. Source/real Textual Linux tests and disposable transaction are prerequisites but cannot substitute for native Termux R05 and real authorized external acceptance. R06 remains DAG planning/adoption ergonomics after R05 acceptance.


### R05A — Native Actions UI startup producer/consumer state repair (2026-10-20)

User reports successful R05 installation but `devtool actions ui` fails during offline workspace inventory with `Dispatch receipt state is invalid`. The R05 workspace allowlist omitted the canonical `accepted-correlated` state emitted by the existing GitHub 200/`workflow_run_id` dispatch writer; this source mismatch can make a legitimate history unusable. R05A adds the exact state and fail-closed correlated run-ID validation, with real Textual 40-column startup and existing corrupt-receipt refusal tests. Native Termux `actions ui` acceptance is still required, and user private receipts have not been examined. Preserve R05 local-only vs separately authorized remote/hosted and release qualification boundaries. R06 follows once native R05A usability is checked.


### R06A — Named DAG projection and static scheduler explanation (source candidate 2026-10-20)

User confirmed R05A fixed `devtool actions ui` startup on actual Termux. The R05A source tree is `31aff2b28ff0eb78c7b8a6fcb82fd2ad67ebab4b` (disposable HEAD `bd179267`); exact native R05A validator/cleanup report was not provided. R06A extends *existing* `devtool plan` via `--ascii` and `--mermaid`, preserves DOT and JSON, and adds bounded static blockers and longest dependency-hop path. The scheduler remains the sole source of reservation-safe waves. R06A is a **partial R06 deliverable**; adoption doctor, transactional plan/recover and cross-consumer contracts remain planned (R06B/C), not closed. Native Termux acceptance is separately required.


### R06B — Read-only adoption doctor and overlay lifecycle readiness (2026-10-20)

Source candidate built atop R06A tree `bffe4de8744e83872d61c557cdc30f44dd040ebd`. `actions adoption-doctor` uses existing A03/A09 eligibility, with per-workflow blockers and zero configuration/remote writes; `overlay plan` composes canonical preimage validation, conflicts, GIT-FRX patch equivalence (or bounded full-inventory file hashes for legacy overlays), and Git cleanliness without a duplicate apply/rollback engine. Also fixes undefined `sha256_file` in the add-existing conflict detector, including symlink safety. R06B source and disposable tests do not establish native/hosted qualification. Native Termux R06A was reported applied without its full machine receipt. R06C producer/consumer contracts and transaction-specific recovery remain open. Preserve DT-008 as PARTIAL_SOURCE, not fully closed.


### R06C — Read-only producer/consumer FRX DAG contracts (2026-10-20)

An additive `federation dag-contract` projection now surfaces the actual FRX
child-owned producer/consumer nodes and cross-repository dependency edges,
including typed artifact intent and exact local source-lock equivalence.
It reuses the existing FRX planner, capability graph, Git owner and lock
comparison, preserves the existing EXO scheduler, and performs no mutations.
`--require-frozen` is opt-in and requires a matching prior strict-local lock;
no remote/source attestation is inferred. Synthetic local multi-repository
regression tests and real-consumer configuration review remain distinct.
R06D rollback-failed transaction recovery and all hosted/device/release
qualification remain OPEN. The user reported R06B applied without its detailed
Termux receipt; native R06C acceptance is pending.
