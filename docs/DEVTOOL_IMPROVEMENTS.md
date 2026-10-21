# Devtool — Cumulative qualification workflow improvements

Updated: 2026-10-16; expanded in QW03 performance repair. Protected audit anchor: `Mikeyphw/devtool`, branch `feature/g3r-current-source-evidence` at `f25196a297d42bc3a2ff36a03aa0411f89fe9d46`.

This is the cumulative in-repository improvements ledger. Keep existing findings even after a fix; source changes require a fresh current-commit qualification before being treated as verified on Termux.

## QW-ERG-001 — Remote WAITING state is misclassified as a failed test

- **Status:** Open; diagnostic confirmed, fix not yet implemented.
- **Affected:** `devtool/qualification_workflows.py::main` (`ci24-resume` exit handling), Devtool wrapper/EXO status mapping; `./devtoolw ci24-resume`.
- **Evidence:** Devtool run `65594ec23b72441684c9305e9d5ffc22` returned the valid machine result `state: WAITING_REMOTE`, `ci_verified: false`, `verification.complete: false`, with `run_id: 38843257546`, but then returned exit code 2. Outer Devtool presented `failure kind: test_failure`. GitHub subsequently completed the same run successfully with all 25 jobs successful and 25 artifacts (24 shards plus summary); exact source `f25196a297d42bc3a2ff36a03aa0411f89fe9d46`.
- **Proposed:** Represent `WAITING_REMOTE` as a nonterminal/pending state at the canonical workflow and EXO layers, preserving `ci_verified: false` and blocking `qualify-seal`. Do not turn an incomplete run into a passing qualification merely to suppress a red CLI status. Add pending-state contract and negative tests for failed, missing, tampered, wrong-source, and incomplete GitHub runs.

## QW-SRC-002 — Pre-dispatch source preflight and durable receipt transition ordering

- **Status:** Open; source-inspected, fix not yet implemented.
- **Affected:** `devtool/qualification_workflows.py::ci24_start`; `devtool/actions_dispatch.py::dispatch_workflow`.
- **Evidence:** QW01 implementation creates `DISPATCH_OUTCOME_UNKNOWN` high-level receipt before GitHub preflight; dispatch flow resolves mutable GitHub ref, and the wrapper checks `pre_dispatch_sha` only after dispatch returns. A preflight-only definitive failure can therefore leave a receipt that blocks future attempts, and remote-source drift can create an unnecessary dispatch before detecting mismatch.
- **Proposed:** Perform canonical source/remote identity and workflow preflight **before** dispatch POST; make preflight failures explicitly non-dispatched with auditable receipt transitions, while persisting a durable uncertain record before the non-idempotent POST. Never automatically repeat an uncertain POST or infer correlation from timestamp/branch/SHA alone. Preserve uncorrelated 204 behavior and source-bound fail-closed verification.

## QW-FS03-003 — Nested Devtool final-seal hang under Termux

- **Status:** Fixed and verified on native Termux for commit `f25196a297d42bc3a2ff36a03aa0411f89fe9d46`; full automated seal returned PASS on 2026-10-16.
- **Affected:** QW01 `qualify-seal` and FS03 delegation.
- **Evidence:** Devtool run `407dca103cf24a139ce59779fe34ac92` hit 600-second timeout when spawning `python -m devtool ... final-seal --strict` inside a Devtool invoke. QW01R2 replaced nested process with in-process FS03 audit and passed disposable validation.
- **Follow-up:** Confirmed native run `5da76acd571b4e6eb78d14578d83f387` returned `PASS_AUTOMATED_MILESTONE_ONLY`, FS03 16/16, exit 0, no warnings/errors. Performance investigation separated into QW-PERF-005; maintain native and negative regression coverage.

## QW-PERF-005 — Excessive `qualify-seal` latency and missing stage progress

- **Status:** Open; performance regression/observability gap confirmed, root cause not established. No source repair applied.
- **Affected:** `devtool/qualification_workflows.py::qualify_seal`, `_ci24_evaluate`, `g3r_verify`, `_fs03_strict_in_process`; relevant CI artifact verification and FS03 internal scans; Termux Devtool `test:qual-seal` frontend.
- **Evidence:** Native Termux Devtool run `5da76acd571b4e6eb78d14578d83f387` (2026-10-16) completed successfully on source `f25196a297d42bc3a2ff36a03aa0411f89fe9d46`, with `PASS_AUTOMATED_MILESTONE_ONLY`, CI24 `38843257546` (24 shards), native G3R 13/13, and FS03 16/16. Yet `test:qual-seal` took **850.01 seconds** (total Devtool 861.583 s), emitting no child output and triggering progressless heartbeats roughly once/minute. This run confirms QW01R2 eliminated the previous **600-second nested CLI timeout** but does not establish where the new latency occurs.
- **Source observations:** `qualify_seal()` sequentially calls `ci24_resume()` (remote HTTP + artifact digest verification), `g3r_verify()` (exact-source archive checks), and `_fs03_strict_in_process()` (canonical 16-check audit). FS03 includes target seals and repository cleanup discovery, which can walk a large worktree; none of these are yet timed separately in the wrapper. Their cost remains **hypothetical**, not a diagnosed bottleneck.
- **Proposed:** Record monotonic per-stage durations (`ci24`, `g3r`, `fs03`, total), structured EXO events, progress heartbeats and a clear `last_stage` while retaining exact-source verification, independent rehashing and fail-closed strict checks. Record timing for expensive FS03 sub-checks, and constrain/optimize traversal only after profiling and proving invariant parity. Add synthetic slow-stage tests, timeout/cancellation tests, and Termux-narrow-terminal display coverage. Do **not** bypass evidence verification or treat recorded status alone as authority.
- **Impact:** Reduces opaque 14-minute checks and makes genuine stalls distinguishable from slow work without triggering redundant CI or native G3R.

## QW-TEST-004 — Host-dependent G3R empty-lanes assertion

- **Status:** Fixed in QW01R1 commit `6cf546a472c4f71ee58a362397d0af20d2168d50`.
- **Affected:** `tests/test_g3r_current_source_evidence.py`.
- **Evidence:** Devtool run `13f2b09a848d4f199f6fd2df5f429255` failed on native Termux because `test_missing_native_lanes_fail_closed` assumed the error had to contain `native Termux`, while correct earlier rejection stated `Native/semantic lanes incomplete`. Rollback was successful.
- **Follow-up:** Keep host-independent negative assertions and explicit Termux-emulated coverage.

## Current qualification evidence boundary

- GitHub run [38843257546](https://github.com/Mikeyphw/devtool/actions/runs/38843257546) is `workflow_dispatch`, attempt 1, completed `success`, with all 24 shard jobs and summary job successful and 25 artifacts for SHA `f25196a297d42bc3a2ff36a03aa0411f89fe9d46`.
- Local `ci24-resume` completed independent summary archive verification, and `qualify-status` reported `PASS_CI24_VERIFIED` for the same SHA. Native `g3r-verify` run `91c1793d843e4efcada4da79b85fda3b` accepted 13/13 lanes. Native `qualify-seal` run `5da76acd571b4e6eb78d14578d83f387` returned the authoritative `PASS_AUTOMATED_MILESTONE_ONLY` (FS03 16/16).
- Automated milestone for SHA `f25196a297d42bc3a2ff36a03aa0411f89fe9d46` is **VERIFIED**; formal release qualification remains **false**, carrying `ENV-A06`, `ANDROID-A16`, `DIST-A01`, `DIST-A12`, `DIST-A13`, and `TEST-A11`. Do not rerun CI24/G3R merely to resolve latency or UX issues.


## QW02 / Phase D findings and current dispositions

- **QW-ERG-001 — Implemented, pending native qualification:** `ci24-resume` returns successful observation with `WAITING_REMOTE`, `observation_completed=true`, `qualification_complete=false`, and `ci_verified=false`; failed/mismatched runs still block. Important limitation: outer Devtool test step may show `ok` for a pending observation; use the machine qualification fields, not the exit status, for CI promotion.
- **QW-SRC-002 — Implemented, pending native qualification:** read-only GitHub preflight before dispatch receipt, and `expected_source_sha` enforcement before POST in ACTIONS-02. True transport uncertainty is still sticky; pre-POST source drift has audited retry semantics. Negative mock tests ensure no POST or journal on mismatch.
- **QW-PERF-005 — Instrumented, bottleneck unconfirmed:** flushed progress events and monotonic CI24/G3R/FS03 timings; no shortening or caching of trust gates. Capture timing receipts from the next native qualification before optimizing whichever stage is slow.
- **DIST-SOURCE-006 — Implemented, pending CI:** DIST-G3 SSH/FRX historical-substrate closure uses 14 explicit test-node AST contracts with SHA-256s and original crosswalk disposition evidence. Full test execution remains CI24's authority; static AST checks alone do not certify a successful test run. Historical reports are not runtime gate evidence.
- **RPT-PHASE-D-007 — Four limited retirements:** AOPS02, AOPS03, HROPS01, ROADMAP DELIVERY reports had no literal inbound references in the V110 audit. Their original hashes, lengths, and protected baseline are retained in `docs/HISTORICAL_REPORT_PHASE_D_PROVENANCE.json`, with recovery via immutable Git history. Unknown external consumers are *not* ruled out; the other 53 reports and all 24 compatibility entries remain.
- **Remaining debt:** Full 24-shard CI + native 13-lane G3R, independent verifier, and FS03 seal must execute on the *new* overlay commit. The six real-substrate obligations remain explicitly open. No local disposable test is a claim of remote/native qualification.

## QW03 / QW-PERF-005 — Duplicate CI remote reads and FS03 source-scoped instrumentation

- **Status:** Implemented in source-pinned QW03 overlay against `fe3e215430908de727618ea0bcba3ed92a75487f`; local regression and transaction verification pending final application. **850-second native device latency not yet remeasured.**
- **Affected:** `devtool/qualification_workflows.py` (`_ci24_evaluate`, `_RunScopedClient`, `_ci24_assert_same_remote_run`, `_fs03_strict_in_process`); `devtool/final_seal.py` (`final_seal_report` optional callback), qualification regression tests.
- **Evidence:** Previous CI24 path re-fetched the same GitHub run from `artifact_list`, `download_artifact`, and `verify_download`, despite already verifying that run in `_ci24_evaluate`. This can multiply remote API latency and is unnecessary inside one bounded operation. The current patch makes at most two *direct* remote run identity reads on the verified path (initial and final), retaining downstream existing checks on a detached defensive-copy snapshot. CI jobs, artifact metadata, individual artifact details, registered-archive SHA-256 and ZIP member checks, summary contract, native G3R and FS03 remain required.
- **Negative controls:** Changed run attempt/status/source/workflow on the final authenticated read must block; scoped snapshot cannot be used for a different run/repo; mutated snapshot returned to consumers cannot poison the independent original; missing/bad/tampered artifacts remain fail-closed. Real FS03 generates one progress receipt per completed check; callback exceptions block.
- **Measurement needed:** On native Termux after installing and qualifying *the new SHA*, inspect `stage_durations_seconds` and `ci24.*` / `fs03.*` progress timings. If 850 seconds persists in `g3r`, profile G3R source-manifest file hashing; if within a specific FS03 check, optimize that check without excluding files or weakening the audit; if `ci24`, inspect network latency and artifact registry locks. No release promotion until all six real-substrate obligations are discharged.


## CI24-G8-001 — Frozen G1 source pin needs explicit QW02 amendment

- **Status:** fixed in proposed QW04, pending hosted CI requalification.
- **Evidence:** GitHub run 38852350297, shard 12 `tests/test_actions_g08_g03_amendment.py` and shard 16 `tests/test_actions_g08_qualification.py`: `G8 source contract does not match the pinned post-G7 snapshot`. Original G1 SHA in frozen G8 matrix predates QW02's fail-closed pre-dispatch source check.
- **Resolution:** retain frozen matrix/ledger; accept only checksum-pinned `actions_dispatch.py` with the QW02 source-preflight regression and explicit amendment document. Mutation, symlink, omission and unrelated-source tests reject. No live proof promotion.

## CI24-PROV-002 — Phase D test cannot retrieve ancestor in shallow CI checkout

- **Status:** fixed in proposed QW04, pending hosted CI requalification.
- **Evidence:** GitHub run 38852350297 shard 16: `fatal: path 'AOPS02_ANDROID_EXECUTION_DEVICE_FABRIC_REPORT.md' does not exist in 'f25196a...'`. The report exists in ancestor history but `actions/checkout@v4` default fetch-depth is 1.
- **Resolution:** use `fetch-depth: 0` in hosted shard checkout; preserve SHA-256 and size verification with `git show` against immutable ancestor. Missing ancestor remains a test failure; never replace provenance with nominal filename/hash-only success.


## PHASE-E-008 — OV report-as-proof test migration and nine conservative retirements

- **Status:** implemented in proposed source-bound Phase E overlay; local qualification pending, hosted/native qualification required on resulting commit.
- **Baseline:** `1bb1053f29ba33bf5283244b2badf3f3dccf4581`; historical-report inventory: 53, selected retirements: 9, retained: 44.
- **Root cause:** OV11/12, OV13/14 and OV15/16 tests asserted historical Markdown phrases and obsolete pass counts rather than only checking current executable source. PRES-X02/X03/X05/X06/X07/X08 reports had no literal inbound references and their corresponding independent source proof checks already exist.
- **Resolution:** migrate three tests to inspect the current implementation and supporting tests; preserve tests for real runtime behavior; checksum-pin retired bytes to the immutable baseline and demand Git-ancestor retrieval in CI; preserve all remaining reports/ledgers.
- **Evidence:** `docs/HISTORICAL_REPORT_PHASE_E_AUDIT.json`, `docs/HISTORICAL_REPORT_PHASE_E_PROVENANCE.json`, `tests/test_historical_report_phase_e.py`. Dynamic/external consumers unverified.
- **Follow-up:** maintain 24 public compatibility contracts; run full 24-shard CI, native G3R, FS03 after application. Never infer formal release from source proofs.

## PHASE-E-PERF-009 — Seal latency follow-up pending actual native stage timings

- **Status:** measurement pending; no new speculative speed optimization. QW03 added CI24 network-read deduplication, FS03 check timings, and stage durations.
- **Evidence:** former Termux 850.01-second success on `f25196a`; newest qualified `1bb1053` confirmation provided without the phase-timing output. No defensible numeric before/after comparison is possible yet.
- **Follow-up:** capture `stage_durations_seconds` plus `ci24.*` and `fs03.*` lines from next native seal. Only optimize identified bottleneck; preserve independent archive validation and fail-closed checks.

## RS08-SSH-RETURN-001 — Remote HOME must be resolved before rsync evidence return

- **Status:** Patch prepared for baseline `0165432df075` (not yet qualified on physical worker).
- **Evidence:** On 2026-10-16, `ssh qualify mom --no-bootstrap` exited 2; rsync exit 23 because `~/.cache/devtool/ssh-runs/qualification/q-44a0053b74a6` was treated as literal under `/data/data/com.termux/files/home/~/`.
- **Affected:** `devtool/ssh_qualification.py::_retrieve_probe_artifact`; SSH worker return stage on Termux; `devtool/ssh_returns.py::build_return_rsync_argv` requires an absolute literal operand when called with `--protect-args`.
- **Recommendation:** Resolve HOME from the live worker probe, validate absolute path and reject unsafe unresolved forms at return boundary. Keep the canonical transfer unchanged; add tests for both `~/` and `$HOME/` and negative controls.
- **Follow-up:** Inspect existing remote workspace and zero-deletion sync plan, then repeat qualification only after intentional patched source installation; test actual worker on new SHA. General return-source callers should adopt the same absolute-path invariant.

## RS08-LIFECYCLE-002 — Intermediate SSH qualification stages lack durable failure receipts

- **Status:** Open.
- **Evidence:** The full qualification did not yield `checks` or `performance` after return failure, despite a preceding source sync and tmux probe; the `finally` block attempted cleanup of remote probe state.
- **Recommendation:** Source-bound stage receipts after sync, tmux, return, and second plan; write-ahead provenance, explicit resume/recover of failed return before cleanup, and clear distinction between `PARTIAL`, `FAILED`, and `QUALIFIED`. Never rerun or delete evidence automatically after interruption.

## RS11-MANAGED-SYNC-FIXED-POINT — Generated state and directory timestamps

- **Evidence:** `proot-worker` passed live SSH transport/tmux/return (exit 0, total 5.527 seconds), then full `ssh qualify` reached the second-sync clean check but failed (1 pending change). A subsequent read-only plan showed three pending items: `./` and `.devtool/private/run-v3/` with `.d..t......` (directory mtime only), and `.devtool/private/run-v3/catalog.json` with `<f.st......` (file size/mtime). These are not tracked application source changes. No prior qualification receipt should be reclassified as PASS.
- **Root causes:** `ssh_workspace._rsync_argv` uses rsync `-a` to preserve directory mtimes; managed metadata files alter the root directory mtime after a sync. Default SSH source exclusions omit `.devtool/private/`, `.devtool/tmp/` and Python-generated caches, allowing mutation-sensitive/private state into remote mirrors.
- **Remediation:** add anchored private/tmp exclusions and common generated caches to `_DEFAULT_EXCLUDES` (shared by rsync and source/archive stats), and add `--omit-dir-times` to source-sync rsync argv. Preserve `--delete-delay`, path/ownership/lease guards, and normal file timestamp checks.
- **Scope:** SSH source synchronization for managed/existing/ephemeral workspaces; development from Termux to Ubuntu PRoot as a same-phone integration worker, but applicable to genuine SSH workers too. Do not claim independent physical qualification.
- **Privacy:** excludes prevent future transfer but do not delete any already-mirrored private runtime files, because rsync excludes protect destination files. Inspect and separately authorize cleanup of any pre-existing copies if needed.
- **Verification status:** targeted automated tests included and run on archived baseline module. Live post-patch rsync fixed point and full worker qualification **PENDING**.
- **Follow-ups:** fail-closed source revision/snapshot binding across whole qualification; preserve structured per-phase receipts and pending change codes on failures; source-only transfer inventory and default exclusion audit.

## RS12-PROOT-RELIABILITY-PERFORMANCE — Native Termux supervisor lifecycle, sync scan and actionable failure paths

- **Evidence (17 October 2026):** Same-phone Ubuntu PRoot worker `proot-worker` completed a source-bound RS11 SSH qualification (transport, bootstrap, sync, tmux, return and second clean plan all `pass`). Receipt `proot-rs11-final-20261017T152718Z.json`: `total_seconds=30.149`, `sync_seconds=17.353` for 3 file changes, `second_plan_seconds=6.844` with 0 changes, `return_seconds=0.404`. No timing improvements have yet been measured after RS12. Earlier SSH refusal occurred when localhost port 2223's native Termux bridge was absent.
- **Targeted fix A (service ergonomics):** `devtool-proot-worker status|start|ensure|stop|restart|exec` manages only RS10 tmux sessions by exact name, checks pinned localhost SSH/keys, auto-starts missing agent/bridge sessions on explicit invocation, and refuses stop/restart if guest tmux sessions exist or are unknowable unless explicitly forced. Does not change the bridge, Devtool SSH profiles, Ubuntu guest or unrelated Termux sessions. `stop`/`restart` require `--yes`. No background auto-restart daemon installed.
- **Targeted fix B (source performance):** `ssh_transport.source_stats` combines nested Git-repository boundary discovery and file/stat inventory into a single directory walk, excluding already-excluded subtrees. `ssh_workspace._rsync_argv` accepts source-bound nested-Git exclusions from `seed_plan`; plan+seed share the same inventory within one dry-run. Rsync still validates each included file, workspace ownership, deletion boundaries, canonical roots, and ordinary `--delete-delay` behavior. No cross-run mutable cache or content-skip shortcut.
- **Targeted fix C (diagnostics):** Full SSH qualification reports bounded itemized rsync code/path pairs when second sync or project-validation clean check fails; previous output only provided a count. Existing error classifications and refusal behavior preserved. Receipt path details are local filenames; no file contents included.
- **Scope:** Devtool SSH workers, especially Termux -> native SSH bridge -> Ubuntu PRoot development backend. This PRoot worker shares the Android kernel and is not independent device qualification evidence. Historical PASS and FAIL receipts remain unchanged.
- **Verification status:** Unit/regression suite executed on preserved source baseline; live performance improvement and Termux-specific startup acceptance **pending user execution**. Compare median warm-sync measurements, not single timings, before claiming speed gains.
- **Follow-ups:** Distinguish native Android Termux from Linux PRoot based on real guest OS instead of inherited Termux markers; add optional opt-in Termux:Boot integration; instrument remote RPC and local scanning timings separately; reduce SSH round trips only where ownership and fail-closed checks remain intact.

## RS12-R1-SERVICE-SOURCE-LAYOUT

- **Status:** Fixed, subject to target-device regression rerun.
- **Evidence:** RS12 source-bound installation and verification succeeded, but 2 of 27 target tests failed with `FileNotFoundError` for `scripts/devtool-proot-worker.py`. RS12 had copied the manager only to `~/.local/bin/devtool-proot-worker`, while the repository's `test_ssh12_proot_runtime_speed_diagnostics.py` imports from `scripts/`.
- **Affected projects:** Devtool source tree and Termux RS10/RS12 `proot-worker` integration. Ubuntu guest, bridge, and credential material are unaffected.
- **Root cause:** RS12 installer only installed the operational user-bin copy and omitted the source-controlled repository copy referenced by its own tests.
- **Correction:** RS12-R1 adds byte-identical `scripts/devtool-proot-worker.py` to the repository, sourced from the already installed, checksum-verified RS12 manager. Retains `~/.local/bin/devtool-proot-worker` unchanged. Installer uses atomic installation, strict RS12 source identity checks, refuses nonmatching files and symlinks, and preserves ledger backup.
- **Regression:** Existing `test_service_manager_refuses_stopping_unknown_guest_jobs` and `test_service_manager_refuses_live_sessions` must pass. Test again after applying; record device result separately. No SSH/network execution is performed by the hotfix.
- **Follow-up:** Future service-manager installers must keep source-controlled entrypoints and installed entrypoints in sync. Validate a clean simulated installation *before* publishing a bundle, not only source-staged tests.


## RMP-C02-P2 — Original artifact path and durable cleanup source identity (2026-10-18)

- **Status:** Candidate implemented; 54 focused offline regressions passed. User has not applied this phase to Termux; no H0 closure or formal release qualification claimed.
- **Affected:** artifact resolver (`devtool/artifacts.py`, `devtool/console_utils.py`), runner (`devtool/runners.py`), CLI (`devtool/cli.py`), cleanup worker (`devtool/artifact_cleanup_worker.py`), source-bound apply and ad-hoc multi-overlay workflows.
- **Evidence:** the phase-1 worker accepted a valid apply result paired with unrelated operation metadata as a possible deletion source; the persisted `ArtifactExecutionRecord` had no consumed-source inventory to independently bind its source to an external deletion request. Symlinked CLI input lost literal leaf identity when resolving archive variants.
- **Correction:** preserve the literal source leaf and reject symlinks; constrain candidate sources to qualifying apply results; persist an independent RUN-V3 source-provenance attachment whose verified path and stat identity must match the private worker capability, and fail closed if any evidence is absent or inconsistent.
- **Proof:** `tests/test_rmp_c02_consumed_source_authority.py` plus the retained cleanup/history/variant regression suite; real disposable apply path covered. Retain original protected qualifications and avoid rerunning them.
- **Remaining:** native Termux adoption, ad-hoc bundle owner exercise, related external caller audit and H0 terminal evidence. Report: `docs/RMP_C02_PHASE2_DURABLE_PROVENANCE.md`.

## RMP-C02-P3 — Cleanup timeout and recovery must not report unreceipted deletion as success (2026-10-18)

- **Status:** Phase-3 candidate implemented, pending independent on-device application/evidence; 58 focused offline tests passed. Phase 2 installation was user-reported successful; its source-bound result is not an RMP-H0 formal closure receipt.
- **Affected:** `devtool/cli.py::_delete_original_artifact_after_apply`, `devtool/artifact_cleanup_worker.py::_cleanup_external_apply_sources`, fresh-worker LIFE receipt integration and RMP-C02 cleanup regressions.
- **Evidence:** On worker timeout, the previous parent path returned exit 0 when requested source files were absent, even if the worker terminated between unlink and persistence of the LIFE cleanup receipt. A new unrelated file at the same path could also be confused with a prior path-only cleanup receipt.
- **Correction:** Include the phase-2 source inode/parent/mtime snapshot in the bounded external cleanup plan identity; require a matching successful LIFE receipt for all external cleanup success responses; perform bounded, read-only receipt reconciliation after timeouts, checking exact path set, repository identity, terminal statuses, source absence and RUN attachment digest when available. Without a matching receipt, classify outcome as `artifact_cleanup_outcome_unknown` even if the files are absent. Do not relaunch a cleanup worker or delete again automatically.
- **Regression proof:** `tests/test_rmp_c02_cleanup_recovery.py` exercises post-unlink/no-receipt timeout, successful receipt reconciliation, fabricated success, and reused path identities. Existing timeout tests now require truthful non-success without the receipt; previous worker and path containment regressions remain intact.
- **Boundaries:** No changes to CI24/G3R, SSH/RS13, Android qualification or release sealing; prior receipts are not modified. This is crash/recovery classification, not protection against code with the same OS user permissions. Receipt absence remains an unresolved outcome requiring read-only investigation; C02 is not formally closed.

## RMP-C02-P4 — Ambiguous fresh worker exits and malformed protocol frames (2026-10-18)

- **Status:** Offline candidate; not applied or verified on user's Termux checkout. C02 H0 formal closure remains open.
- **Evidence:** A killed fresh cleanup worker returning no stdout with exit `-9` was previously classified as generic failure even if its source had been removed; an empty stdout was parsed as `{}` by `json.loads(proc.stdout or "{}")`, losing the missing-frame distinction.
- **Affected:** `devtool/cli.py::_delete_original_artifact_after_apply`, bounded external post-apply source cleanup, LIFE receipt reconciliation, RUN-V3 source provenance.
- **Remediation:** Treat malformed/missing worker frames as unproven; accept completion only when the exact source-identity-bound LIFE receipt is verified; never replay deletions automatically. Retain ordinary failure semantics for a controlled nonzero worker exit with all source files still present.
- **Verification:** `tests/test_rmp_c02_worker_exit_recovery.py` plus the existing caller, receipt, worker, artifact, and lifecycle regressions. Fresh native Termux and final audit acceptance remain separate.


## RMP-C02 Phase 5 — Native artifact-commit UX and local acceptance (2026-10-18)

- **Finding:** The combined Phase 3+4 overlay validated successfully but did not commit because `.devtool-artifact.json` omitted the native `apply.commit` declaration. This required manual staging and caused preventable user friction.
- **Affected:** Artifact authors, Devtool overlay CLI, RMP-C02 qualification handoff and all future remediation bundles.
- **Correct contract:** `apply.commit.enabled=true`, `strategy=single`, and a nonempty message. The CLI may override with `--no-commit`; fail closed if commit fails or Git identity/policy blocks it.
- **Change:** Add a fixed-inventory, disposable local C02 acceptance gate and negative parent-symlink/caller tests; require an end-to-end disposable overlay apply that proves the Git HEAD actually moves by one correctly scoped commit.
- **Status:** Implementation candidate; local tests and disposable apply required; no native Termux/formal RMP closure claim.

## RMP-C02 formal source-bound security closure (2026-10-18)

- Status: **CLOSED at native Termux source-bound cleanup scope**, conditional on the linked local evidence receipt and exact qualified source commit `ec6ed8af8754de490601fe503624702e56c2475c`.
- Evidence digest: `86b33f7c08f80e6e8bc492f6d233832cdd7e58b6967eb2608cf50bc120380bf6`; both real disposable caller paths passed, plus 67 historical source-pinned local acceptance tests reused without rerun.
- Original external cleanup vulnerability, hostile same-UID limitations and unrelated RMP/audit/release obligations remain explicitly separated.
- Closure gate compatibility issue: native Android CPython reports `sys.platform=android`; corrected overly strict Linux-only detection while retaining Termux binary/base-prefix path verification.

## RMP-C03 — Bounded asynchronous acknowledgement integrity (2026-10-18)

- **Status:** Source-bound remediation candidate, not a formal H0 closure or release qualification.
- **Affected:** `devtool/distributed_transport.py`, asynchronous journal forwarder, delegated event receiver, persisted delivery cursor, DIST federation consumers.
- **Evidence:** Matching-sequence four-field `DeliveryAck` without receiver frame/provenance proof advanced a disposable cursor from zero to one at the actual `AsyncJournalForwarder.pump_once` API. Original risk: `async_ack_cursor_underbinding` from retained V89/V108 RMP history.
- **Correction:** Require exact frame digest, complete delegated session-context digest, and attestation digest along with delegation, child, event and integer sequence before cursor persistence. Reject coercions and unbound legacy replies; retain valid reconnect/replay.
- **Proof:** `tests/test_rmp_c03_ack_context.py`, `tests/test_r03_distributed_ack_binding.py`, DIST-X01–X04, DIST-G1–G3. 89 focused tests passed on disposable source snapshot. Native auto-commit application and final C03 closure are independently gated.
- **Remaining:** Native Termux installation, source-bound JSON acceptance receipt, provider-path receipt qualification, explicit H0 evidence reconciliation and formal closure. C02 formal closure and unrelated qualification state are unchanged.

## RMP-C03 formal native source-bound closure (2026-10-18)

- Status: **CLOSED** within the async acknowledgement cursor-integrity scope on commit `36e7db6f29bc8e6d2fde62c4eb7ab8bc73191e47`.
- Reused prior **89/89 PASS** C03 acceptance without replay; receipt SHA-256 `a5ad0bb6bbf0012df5d18e820a45f636bd07b8d8b9840d39fd22a83884118bb6`.
- Native seven-case durable ACK, substitution denial and reconnection receipt SHA-256 `f9d6f20fb92e1ae41787df6753ae4a496a3c3501730cc9db67543eea6380f0f5`.
- No claim of cryptographic peer authentication, hostile same-user process isolation, remote execution requalification or release seal.
- Native overlay commit includes exactly the C03 closure gate, closure document and this cumulative ledger update.

## UX-NOTIFY-01 — Native Android duplicate alerts and user-global muting (2026-10-18)

- **Evidence:** Devtool apply emitted both a native Termux notification and toast even when the Android Live HUD companion supplied run notifications. Historical `DEVTOOL_HUD_BRIDGE_DIR` checked only partial state and did not suppress the toast. Desktop `notify-send` lacked a global per-output switch.
- **Affected:** Devtool apply CLI, Termux/chroot notification forwarding, desktop HUD notifications; Android companion event persistence and app-managed notification settings deliberately unchanged.
- **Remediation:** privilege-passive positive package discovery (`pm path` / `cmd package path`, no `su`), defaults `auto` for independent notification/toast outputs, environment `DEVTOOL_NOTIFICATIONS` and `DEVTOOL_TOASTS`, user-global `[cli.notifications] native_notifications/native_toasts` preferences, explicit `on/off` precedence over auto.
- **Regression correction:** Original Android notification tests inherited the host's `DEVTOOL_NOTIFICATIONS`, `DEVTOOL_TOASTS`, global configuration, and real package-manager detection state. On Termux the expected mock calls were skipped, producing 2 failures despite the intended policy behavior. The fixtures now bind `auto` explicitly, isolate global config, and mock an absent companion; separate tests retain positive companion detection and `off/on/auto` coverage.
- **Status:** revised implementation candidate after first native validator failed (114 passed, 2 failed); new native qualification still required.

## RMP-H0-B01-C01 — G3R lane provenance and Android execution completeness (2026-10-18)

- **Evidence:** Existing release gate checked the count of executor results, but could accept duplicated/substituted command results; existing G3R v3 log verifier checked log checksums without binding every log filename to the originating lane, allowing borrowed output to masquerade as another lane.
- **Affected:** Android release phase-to-command identity and skip semantics; G3R native/semantic lane log references and PASS log completeness.
- **Remediation:** Compare ordered command identities, reject unintended skips in mandatory phases, enforce exact unique producer-defined G3R log identity, reject empty PASS logs and incorrect exit-code types. Preserve historical one-command phase aliases, existing source roots, 24-shard/13-lane evidence, and C02/C03 closure receipts.
- **Proof:** 104 nearby release/G3R/qualification tests PASS on offline source, plus disposable source-bound apply and native commit check when available; local acceptance `python3 -m devtool.rmp_h0_acceptance`.
- **Status:** implementation candidate only; B01 and C01 **NOT formally closed**, no release qualification, no historical campaign rerun.

## RMP-H0-B01-C01 formal native bounded consumer closure (2026-10-18)

- **Status:** B01 G3R lane-consumer identity and C01 Android release result-completeness **CLOSED within bounded consumer-security scope** at `3f987899c44809433dcfbb01e450a2b40761a2ab`.
- Native 47/47 acceptance reused without retest; receipt SHA-256 `b029eef3a03110fa26607f7938cd1ddc260e7aa52489956753a644a025752b02`.
- Independent disposable 8-case G3R and 6-case Android negative/positive proof SHA-256 `a2d8277e6fad713e58afc766dbed633cb97b6eec8982232b88b8a2770555e4a4`.
- Original 308 findings otherwise preserved; no historical native G3R, Android, CI24, SSH or RS13 requalification. Six independent audit obligations remain open.
- This does not establish cryptographic peer attestation, genuine Android execution, or release seal.

## RMP-H1 — Consolidated privacy, receipt and presentation consumer hardening (2026-10-18)

- **Evidence:** Reproduced four H1 consumer gaps from the H0-closed source: environment-composition identity changes with secret-value SHA, unknown plaintext secrets in nested validation metadata, forged completed TEST shard summaries lacking per-case bindings, and EXO projections hiding a late failure outside the rendered limit. The N01 retained 308 mapping matches the pinned current-source digest but is not independently signed.
- **Affected:** `devtool/environment_composition.py`, `devtool/validation_sessions.py`, `devtool/test_runtime_optimization.py`, `devtool/presentation_surfaces.py`, their ENV/RUN/TEST/EXO consumers; RMP-N01, N02, C04, C05, C06.
- **Remediation:** Secret-neutral identity, nested metadata credential references, per-shard/case terminal bindings, untruncated terminal truth, immutable retained inventory crosswalk and executable JSON acceptance runner.
- **Validation:** Source-bound focused tests and installer-native `apply.commit` with exact scope; local acceptance receipt follows installation, not inferred from offline test results.
- **Status:** `IMPLEMENTED_PENDING_NATIVE_ACCEPTANCE`. **No H1 family formally closed**. N01 requires authoritative retained roadmap provenance; N02 persistence-reader review, C04 TEST-A11, C05 native HUD, and C06 consumer identity audit remain explicit.
- **Guardrails:** Preserve all four H0 closure commits, six independent open audit obligations, 308 historic assignments and previously verified external campaigns. `release_qualified=false`. No automatic push or release.

## RMP-H1 Phase 2 — Persisted consumer trust and selection binding (2026-10-18)

- **N02 — read-side credential validation gap**. Evidence: H1 `serialize_spec()` refused inline credentials, while `deserialize_spec()` reconstructed `CommandSpec` from persisted metadata and `stored_specs()` skipped non-dict entries. A modified saved plan could bypass the write-time guard. Affected: Devtool validation session load/resume. Fix: strict read types, safe re-serialization, and fail-closed stored-plan handling. Status: patched; complete persistence/history audit remains open.
- **C04 — detached expected-case inventory**. Evidence: `run_attachment_manifest()` verified unique attachment IDs and totals, but without comparing their IDs to the aggregator's original selected set. A count-preserving substituted case could appear complete. Affected: TEST-X03 shard manifest and TEST-G3 consumer. Fix: carry original expected case identities and demand exact matching. Status: patched; remote-run provenance and TEST-A11 remain open.
- **Provenance/ergonomics**. This consolidated overlay reuses the prior H1 native 212-test receipt by matching its source HEAD, Git tree, lane hashes and guardrails; the supplemental JSON receipt is independent and *not* a formal-closure certificate. Native Devtool `apply.commit` required and validated. Status: implemented, user-device qualification pending.

## RMP-H1 Phase 3 — Session-load and selection authority (2026-10-18)

- **N02 — full session-load plan validation**. Evidence: after Phase 2,
  `stored_specs()` rejected malformed saved plans, but `_session_from_dict()`
  silently replaced a malformed non-list `plan_specs` with an empty list and
  did not verify each list element before status/recovery consumers could
  handle the session. Affected: native validation session load/recovery.
  Remediation: distinguish missing historical plans from explicitly malformed
  plans; validate all present items with `deserialize_spec()` at load time;
  reject corrupt or secret-bearing entries. Status: source patched; native
  Phase 3 acceptance pending.
- **C04 — external test selection binding**. Evidence: a mutable shard aggregate
  could substitute both the attachment case list and its own expected case IDs,
  preserving valid totals. Affected: TEST-G3 selection and artifact consumer.
  Remediation: optional external, independent selected-case inventory passed
  from TEST-G3's canonical `sel.resolved_check_ids`; mismatch cannot claim
  completion. Legacy callers without external authority remain unqualified.
  Status: source patched; remote TEST-A11 obligation open.
- **Evidence / limitation**. Source-pinned Phase 3 producer verifies the native
  Phase 2 receipt against the parent Git tree, accepts only 130+ local focused
  passing tests, and records N01's *unsigned* 308-row retained crosswalk.
  N01, N02, C04, C05 and C06 are **not formally closed**; release qualification
  and the six independent obligations remain open. No older campaign rerun.

## RMP-H1 Phase 4 — Cross-consumer truth and secret-safe identities (2026-10-18)

- **Status:** Code remediated, offline consumer regressions passed; native Phase 4
  acceptance and individual H1 formal closure still pending.
- **Affected projects:** Devtool ENV/direnv, EXO/PRES dashboard and Android HUD
  projection consumers; reused H1 history, including RMP-C05 and RMP-C06.
- **Evidence:** Post-Phase-3 source audit reproduced `compose_dashboard` masking
  a blocked/unknown child behind a passing child when the UI projection was
  truncated. Negative test `test_dashboard_cannot_turn_non_success_into_passed`
  failed on the baseline. `environment_composition._direnv_probe` hashed raw
  credential values in `overlay_identity`, and accepted nested status config
  values in `global_identity`, creating a secret-rotation identity oracle.
- **Repair:** Aggregate all child statuses before truncation, with failed,
  blocked/cancelled, running, unknown and skipped never outranked by passing
  projections. Fingerprint direnv exports using credential-value exclusion;
  recursively exclude credential-named status-config values while retaining
  nonsecret semantic differences. Source and negative regressions are in
  `tests/test_rmp_h1_phase4_cross_consumers.py`.
- **Residual:** No claim of cryptographic attestation, real Android HUD test,
  independently authenticated N01 roadmap, remote TEST-A11 gate or formal H1
  closure. Existing release obligations and H0 closure source records preserved.

## RMP final H1/H2 consolidation — source hardening and honest external gates (2026-10-19)

- **Evidence and scope:** H1 Phase 1/2/3 passed 212/119/134 native Termux regressions;
  Phase 4 remains an independently verifiable source-pinned prerequisite, not a
  historical requalification. All four H0 closure records remain authoritative
  at their recorded source commits.
- **Findings:** N02 presentation JSON redactor applied only shallow handling:
  nested `api_token`/`password` fields persisted into event detail exports.
  C05 aggregate dashboard trusted a green projection even if its own unseen
  event was failed or blocked. C06 env identity sanitized mapping secret keys
  but missed `TOKEN=value` entries inside nested arrays.
- **Change:** recursive nested redaction, event-level status truth before UI
  truncation, and semantic secret exclusion for env array entries. Negative
  tests cover secret rotation, nonsecret drift, adverse status hiding and
  nested JSON export paths. Existing Q01 canonical cleanup confinement and
  historical contract evidence remain protected without broadening deletion.
- **H2 status:** Q01 source-bound cleanup negatives tested, no claim that all
  lifecycle callers are formally qualified. Q02 historical 20-probe archive
  retained verbatim (six original mismatches); the 11 older test contracts are
  not silently declared fixed or requalified. U01 and six independent real-
  substrate obligations remain OPEN. N01 lacks a signed authoritative original
  roadmap; other H1 external-consumer formal acceptance also remains OPEN.
- **Disposition:** source remediation and local tests only; `release_qualified`
  false, formal H1/H2 closed false. GitHub Actions overlays may proceed after
  native final source acceptance without reopening completed security work.

## ACTIONS-UX02 — Exact remote handoff and pre-POST DAG identity

- **Evidence:** ACTIONS-06 previously checked the remote source SHA *after* a
  non-idempotent dispatch. UX01 could inspect only local journals; operators
  otherwise had to compose several low-level run and archive commands.
- **Affected projects:** Devtool core GitHub Actions provider and consuming
  Devtool-wrapped repositories (Android/Go/Python/web, as applicable).
- **Fix:** Compare the frozen source SHA in ACTIONS-06 before the dispatch POST;
  distinguish definitive preflight failure from uncertain dispatch. Add
  explicit exact-request `actions review` and `actions collect`, reusing the
  ACTIONS-03/04 source, attempt, artifact and canonical registry contracts.
- **Validation:** disposable reviewed-run and archive negative cases,
  ACTIONS-02/03/04/06 regressions, canonical CLI registry, and Textual UX01
  tests. Native source-bound acceptance is recorded separately.
- **Status:** Implemented, native acceptance pending. Not formal security or
  release qualification. No automatic remote dispatch in acceptance.


## ACTIONS-A03 — GitHub Actions repository-adoption authority (2026-10-19)
- **Evidence:** UX01 native Termux 79/79 at `f8f71cfc`; UX02 native 90/90 at `95ed8f774b5729396a3dae0acb389853e90dd95e`. ACTIONS-A03 adds independently tested source-bound consumer adoption generation.
- **Affected projects:** Devtool as provider; consumer repositories adopting target-local jobs, native workflows and `devtoolw` (e.g. Studyflow, Skybind, Cinderbox-Companion). Project-specific external workflow adoption is not yet claimed.
- **Fix:** Add `actions adopt`: review-only plan, exact immutable source ref, new workflow generation, native wrapper alias, minimal permissions, pinned GitHub actions, existing Devtool DAG invocation, and evidence-only archival. Refuse unconfigured targets, unknown workflows, mutable refs, dirty worktrees, symlink destinations, and overwrites.
- **Status:** Source implementation ready for native A03 acceptance. Does not dispatch GitHub, sign/release artifacts, close RMP H1/H2, or certify Android/Go toolchain setup. EXO/runtime evidence remains owned by Devtool's existing graph execution.


## ACTIONS-A04 — Source-bound promise audit and generated CI runtime hotfix (2026-10-19)

- **Evidence:** UX01 79/79, A02 90/90, A03 47/47 native source tests; A03 generated `invoke --json` yet canonical `devtool invoke` exposes no `--json` option (real subprocess exit code 2). The same target-local workflow succeeds with global `--machine-file` and emits an EXO-aware runner JSON envelope.
- **Impact:** All A03-generated GitHub Actions workflows would fail at invocation even when configured target-local jobs were healthy. The A03 acceptance did not exercise generated invocations.
- **Remediation:** One source-bound A04 overlay changes generated workflow to use `--machine-file` before `invoke`, adds actual CLI/alias tests and full promise audit. Historical ledger appended, not rewritten; no remote dispatch.
- **Outstanding:** Actual hosted workflow runs, download/verification of evidence artifacts, repository-specific runners and deployments, formal H1/H2 plus release qualification remain OPEN and separately tracked.
- **Status:** Source fix candidate; only native A04 acceptance can establish qualified local implementation.

- **A04 installer qualification finding:** In a disposable self-update with repository-local RUN-V3, every source validation and Git commit stage passed but cleanup refused: `durable run catalog does not attest this artifact execution`. That is an actual post-commit evidence persistence defect (OPEN), not permission to bypass LIFE cleanup. A complete replay with documented external `DEVTOOL_XDG_DIR` and bytecode writes disabled passed all six apply stages, 32 embedded tests, source cleanup, and final worktree integrity. A04 installer isolates RUN-V3 externally only for its own apply process, retaining an existing configured XDG root. Fixing default repository-local RUN-V3 durability is separate future work; do not claim it closed.
- **GitHub 404 updated evidence:** `actions diagnose --auth gh` for `Mikeyphw/devtool` succeeded with repository accessible and seven workflows visible, proving the authenticated GitHub CLI and repository-wide Actions discovery work. The prior 404 is still unlocalized to a specific resource or the `auto` credential path.

- **A04 Termux validator remediation (2026-10-19):** Native run `b50b6ec21739438886184db2fdf7b295` failed 3/32 checks because nested CLI fixture subprocesses inherited non-canonical diagnostic `host-android` execution selector from the artifact-validation host. Git rollback verified six paths and preserved failure evidence. Corrected test-only fixture explicitly selects the supported `host` backend and adds a regression injecting `host-android` into both relevant environment variables. The generated Ubuntu Actions YAML is unchanged. Native A04 acceptance remains pending.

- **A04 second native failed validation:** 30 passed / 3 failed, rollback
  successful. `host` remained an alias for the observed substrate and thus
  still resolved to `host-android` under sanitized Termux environment.
  Corrected with independently executable-path-derived native-Termux test
  substrate, child-local PREFIX restoration, and an explicit negative proof
  that unsupported Android hosts do not receive an invented Termux identity.
  Formal native qualification remains open until the replacement passes.

- **A04 third Termux attempt, `64ba8185f62044a7a43d9314ab447977`:** validator 35 passed, one negative-test failure; native rollback verified. The `test_a04_unknown_android_host_must_not_fake_termux` mock changed `sys.executable` but failed to replace the actual Termux `sys.base_prefix`. The fixture legitimately used its independently observed canonical Termux base prefix, invalidating the negative test expectation. Corrected the mock to model both executable **and** base-prefix identity and explicitly demonstrated why leaving the Termux base prefix would qualify. The production substrate router and GitHub workflow remain untouched; native A04 acceptance still pending.

## ACTIONS-A06 — Canonical CLI schema missing from installed wheel (2026-10-19)

- **Evidence:** Cinderbox GitHub Actions run `39296491876`, job `118210275751` installed frozen Devtool commit `08eb13991b5d1c623b64fdd8f9524418ee27207e`, but `devtool plan` immediately failed with `FileNotFoundError: .../site-packages/devtool/core_cli_schema.json`. The source checkout had the file; `pyproject.toml` had an explicit package-data allowlist omitting it. The incident left a deliberately incomplete A05 artifact, not a qualified run.
- **Affected:** Devtool `pip wheel` / `pip install` CLI, generated cross-repo GitHub Actions adopters, Cinderbox A05, future Studyflow/Skybind consumers.
- **Fix:** Explicitly include `core_cli_schema.json` in setuptools package data. An offline, isolated full-wheel integration regression checks the archive entry, source byte identity and import using `python -S` after installation to a temporary target. Preserve fail-closed schema resolution; no fallback to an arbitrary mutable source path.
- **Evidence boundaries:** Source-only Cinderbox A05 verified run `39301086093` via interim frozen-source import. This does not qualify the producer wheel or Android/release lanes. Producer wheel local acceptance and hosted wheel smoke evidence must be recorded separately. **Status: implemented; native acceptance pending.**


## ACTIONS-A08 — Free/private signed-unqualified handoff hardening (2026-10-19)

- **Evidence:** A07 real hosted native debug build qualified on Cinderbox run `39336899057`, commit `cb218cbae3e5eed5769ba784dd5d1fc747f40dd1`; no release/device promotion. Devtool ACTIONS-G6 already provides LIFE-backed local signature and registered parent/child evidence verification, but the final unsigned parent check compared a file's *current* digest with itself after an external signer inspection. A parent mutation during inspection could escape detection. Existing local evidence checks are read-only; no dedicated explicitly consented install/export action existed.
- **Affected:** `devtool/actions_local_evidence.py`, `devtool/actions_private_handoff.py`, canonical CLI/command adapter and tests.
- **Fix:** Snapshot the unsigned-parent digest before external signer inspection and compare after; expose `actions release local-handoff` with mandatory independent source SHA, exact registry ID, separately opted-in `--yes --allow-unqualified`, fresh unique destination or explicit adb serial, frozen/hashed verified bytes and no overwrite/uninstall. Preview stays read-only. Locally signed artifact is always `source_attested:false`, `qualified_release:false`, `published:false`. No GitHub dispatch, secrets access, release CI claims or protected-branch feature requirements.
- **Status:** SOURCE-TESTED when overlay suite passes; native Termux acceptance, actual explicitly approved device install/export, and optional cryptographic source attestation remain separately open. Never substitute source tests for real device/release qualification.

- **Separate inherited G8 limitation (OPEN):** the frozen G8 G2 `devtool/actions_pipeline.py` hash `22b4708e…` does not match the existing A06-baseline file `12b60b30…` even before A08. A08 does not rewrite this historical pin or claim its `strict_source` gate passes; its amendment tracks G6 drift only. Resolve G2 with a separate source-bound provenance transition, not by weakening `strict_source` or overwriting old evidence.

## ACTIONS-A09 — Cross-repository CI host admission and Go toolchain safety (2026-10-20)

- **Evidence:** Cinderbox A07 real hosted debug DAG `39336899057` required explicit JDK/Android prerequisites. Existing A03 generated workflows installed only the frozen Devtool wheel, then ran selected target-local jobs without proving host toolchain coverage. Skybind main `91fe24de5b89e7a562f9c18873f44d7f3983518a` includes target-local `foundation-go` (`bash scripts/devtool/check_native.sh`, `go.mod` as input, `read_only=true`); blindly adopting its `native` workflow through A03 could fail on missing Go/toolchain. Existing ACTIONS G5 polyglot matrix is already real-green (GitHub run `38437173945`, eight cells); do not duplicate it.
- **Affected:** Devtool `actions_adoption.py`, generated cross-project Actions YAML, Skybind/Go source-only adoption, Cinderbox/Android safety boundaries, optional Studyflow consumers, A03 generated workflow backward compatibility.
- **Change:** Resolve selected native job inputs and metadata, fail closed on unsupported hosts or privileged/device/signer jobs, pin and provision Go with `actions/setup-go@d35c59abb061a4a6fb18e82ac0862c26744d6ab5`, preserve historical A03 Python-shell generated bytes, and report `ci_host_profile` plus truthful source/release qualifications. Add source, negative, preservation and generated-YAML regression coverage.
- **Status:** SOURCE_TESTED pending native Termux installation and an actual second-consumer GitHub hosted run. No device, release or protected-ref gate closed.


## DEVTOOL-R01 — Consolidated ledger foundation and G2/RUN-V3 remediation (2026-10-20)

- **Baseline:** `6f7b1de146838dca2458eaa35d54cc8dd4445f70`; local disposable source tests only until actual installation and current-SHA qualification.
- **RUN-V3:** Outer CLI finalization now reopens the RUN catalog before writing, merges only its own terminal/projection fields and retains independently persisted artifact plan, qualification and execution record attachments. This addresses the stale-read/overwrite mechanism; full repository-local self-update remains an explicit native acceptance gate. The fresh LIFE worker still rejects absent, corrupted, mismatched or unregistered evidence.
- **G8 G2:** The immutable frozen digest `22b4708e...` matches `7aa0141`; the successor digest `12b60b30...` matches `95ed8f7`. An exact-byte source amendment and independent JSON provenance preserve both identities without editing the historical matrix, weakening `strict_source`, or promoting remote/release gates.
- **A06/A04/A09:** Preserve current packaged schema, local handoff, host-admission behavior and negative tests. Isolated wheel and source-bound integration smoke are provided; Termux installed-wheel, full self-update and second-consumer hosted CI are not inferred from Linux tests.
- **R0 index:** `docs/DEVTOOL_IMPROVEMENT_INDEX.json` reconciles superseding issue status without erasing earlier ledger entries. The six external real-substrate obligations and RMP-H1/H2 independent authorities remain open.


## DEVTOOL-R02 — Cross-repository A09 source-bound host review (2026-10-20)

- **Baseline:** R01 accepted Termux installation (run `bc397eb35a314149ad34d7223892778a`, commit reported as `70c4f96`); exact post-R01 Git tree is the immutable R02 preflight identity.
- **Implementation:** Extend the existing A03/A09 adoption generator, never create another CI scheduler. Read-only review now identifies the actual consumer commit/tree, selected jobs and toolchain declarations, argv/input digests, a canonical review hash, and the distinction between source declaration and actual hosted toolchain evidence. Write rechecks source/config/wrapper/selected jobs, refuses dirty source, and preserves atomic rollback. Reject unsupported/contradictory `ci_toolchain` values before generating workflows.
- **Compatibility:** Historical `python-shell` generated YAML remains byte-identical; Go keeps pinned `setup-go`, `go-version-file`, and no hidden Android/Gradle provider. Existing generated workflows are not silently modified.
- **Acceptance:** Local focused regression and real Devtool disposable overlay execution must pass, then current-source native Termux A09 review. The real Skybind GitHub hosted consumer **requires separately approved remote adoption/dispatch**. Cinderbox historical native/legacy hosted successes are not proof of automatic PR/push trigger parity.
- **Release boundary:** `source_attested=false`, `release_qualified=false`, `device_qualified=false`; no external GitHub write, signed artifact, root action, device install, or remote execution by this overlay.

## DEVTOOL-R03 — Role-aware Android artifacts and source-bound custom workflow collection (2026-10-20)

- **Source boundary:** The native R02 acceptance receipt reports exact Devtool tree `e6165d2226173d163fae11bdc34eb1ba821ab7f3`, local `34 passed`, no hosted/device/release qualification. R03 is prepared against that immutable tree and does not rewrite R02 historical evidence.
- **SKY-ACTIONS-APK-META-012/013 (remediation in Devtool source):** `actions_android._inspect_apk` now treats application and instrumentation roles separately. Application APKs continue to require positive numeric version codes; only explicit debug `instrumentation` role may carry genuinely empty `versionCode` and `versionName`. Test package and target package are required pins. AAPT/AAPT2 compiled AndroidManifest `xmltree` must expose exactly one instrumentation element under the manifest, one test package and an unambiguous Java runner and matching target. Cryptographic `apksigner verify`, package archive SHA-256/member checks and optional pinned signer certificates remain mandatory. Malformed/duplicated metadata, wrong target and fake app versions fail closed.
- **SKY-ACTIONS-COLLECT-008 (remediation in Devtool source):** `devtool actions android verify|collect` supports `--workflow`, `--expected-source-sha`, `--expected-run-attempt`, `--apk-role`, and `--expected-target-package`. Custom workflow selection requires both independently supplied source SHA and run attempt; GitHub's authenticated exact workflow ID still must equal the selected remote run workflow ID. SOURCE VERIFIED never implies `device_qualified` or `qualified_release`. ACTIONS-08 release policy continues to own release qualification and passes its existing pinned workflow and source identity to the shared collector; no arbitrary workflow becomes release-authorized.
- **SKY-B1C-PM-014 (read-only shared helper):** `android_package_presence.query_package_presence` requires one explicit ADB transport/user and independently compares `pm path` with exact `pm list packages`, classifying only an empty exit-1 path + successful absent list as `absent`. Any service error, contradictory package state or unexpected output blocks. It neither installs nor changes the device, and never claims shell PackageManager output as OS-trusted broker identity.
- **Canonical CLI and distribution:** The checked-in CORE CLI schema and generated help/completion/manpage/reference surfaces were updated together. Existing default Android workflow, application APK, AAB, and release CLI paths remain intact. Unit and source tests run without Android installation, GitHub dispatch or release publication.
- **Qualification boundary:** Offline/source regression and disposable Devtool application must be independently recorded. Native Termux `aapt`/`aapt2` with a *real* instrumentation APK, actual verified GitHub run/archive and Android PackageManager observations are still PENDING. No new real-device or release qualification is claimed. The historical Skybind hosted APK pair can be inspected by Devtool only with a separately supplied exact run/workflow/attempt/source, never by guessing recent runs.
- **Still open:** An opt-in installed app/test APK adoption gate, OS-trusted package identity attestation, full cross-UID test evidence, advanced custom Gradle variant allowlists (`DT-003`) and convention/multiline Gradle detection (`DT-002`). Do not weaken their existing source/physical-host requirements to call this scoped R03 remediation complete.


## DEVTOOL-R01–R03 — Scoped milestone closure reconciliation (2026-10-20)

- **Evidence owner:** `docs/actions/DEVTOOL_R01_R03_SCOPED_CLOSURE_20261020.json` records source trees, the exact user-provided native R01 apply run `bc397eb35a314149ad34d7223892778a`, R02 acceptance source SHA `6b1823e1e0b282a9aefe9cb1b98578478776ff72`, test counts, and separated unqualified obligations. This addendum supersedes historical *pending local acceptance* notes without erasing or rewriting them.
- **R01 — CLOSED in local Termux scope:** native overlay/validator returned exit 0, 56 passed, 11 paths verified, fresh LIFE cleanup succeeded; previous interrupted apply was safely rolled back. Source and isolated-wheel/G8 test scopes closed. Remote, device and formal release qualifications remain out of scope.
- **R02 — CLOSED in local Termux scope:** source-bound `PASS_R02_LOCAL_SOURCE_ONLY` accepted 34 tests, zero skips, exact post-R02 tree `e6165d2226173d163fae11bdc34eb1ba821ab7f3`; receipt explicitly records `remote_dispatch=false`, `hosted_qualified=false`, `source_attested=false`, and `release_qualified=false`. Hosted Skybind Go consumer is still OPEN.
- **R03 — SOURCE VERIFIED; native installation REPORTED, acceptance evidence OPEN:** 101 tests and a 15-path clean disposable apply passed; operator confirmed R03 applied. The actual native overlay/validator log has not been reviewed; do not mark Termux independently verified until its receipt is supplied or newly executed. Real APK tool output, independent hosted run/archive, installed device and trusted broker identity are OPEN.
- **Index discipline:** `docs/DEVTOOL_IMPROVEMENT_INDEX.json` changes statuses for the specific repaired items only. Immutable G8 source pins, older evidence, original issue IDs, RMP H1/H2, six real-substrate obligations and all unqualified milestones remain unchanged. No new GitHub dispatch, Android install/uninstall, root, signing or release operation is authorized.
- **Next:** Obtain R03 native overlay-validation machine evidence, do real tool/hosted acceptance only with separate authorization, and continue R04 lineage improvements. Consolidated R09 cross-campaign acceptance remains mandatory; it does not reopen these already-accepted bounded local results without new regression evidence.


## CORE-X04-NATIVE-PERF-001 — Generated surface projection exceeded native Termux budget (2026-10-20)

- **Status:** Source-optimized in the consolidated R01–R03 closure replacement; native acceptance **PENDING**. The earlier closure overlay is not qualified.
- **Exact observed evidence:** Termux run `19b01fdc284a46dbab059c8ae7e58090` (user-supplied truncated live output) passed 73 tests before `tests/test_core_x04_generated_surfaces.py::test_core_generated_surface_build_stays_inside_declared_budget` failed: 5630.083948ms against the canonical 5000ms budget. Pytest stopped at one failure. This is **not** evidence of a complete rollback, commit, cleanup or native success.
- **Source root cause:** `devtool/core_surfaces.py::_option_rows` rescanned every canonical option for each command; profiling on Linux showed ~2.45 million per-option path element comparisons and the scan dominated generated help projection. A single invocation-local index groups option metadata by canonical path and preserves option order, spelling, hidden help and canonical parser parity. No persistent cache or schema relaxation.
- **Safety:** Retain `PERFORMANCE_BUDGETS_MS["generated_surfaces"] = 5000`; do not skip or xfail the original timing test. Independent slow-oracle parity regression checks every generated command and option. The existing checked-in generated surface drift regression stays mandatory.
- **Qualification:** Repair tested locally on Linux, not on the user's device. The replacement closure archive targets the unchanged post-R03 tree only, requires verified clean rollback/source state, and must run its complete embedded validator on native Termux before scoped closure can be accepted. Hosted, device, RMP and release gates remain OPEN.


## R04-LINEAGE-001 — Offline, exact-identity DAG/Actions/artifact projection (2026-10-20)

- **Status:** Implemented in R04 source overlay; local Linux regression passed; native Termux acceptance and full end-to-end authenticated remote evidence **PENDING**. This is a read-only source-bound PROJECTION, not a new scheduler, run catalog, authenticated GitHub verifier, or release seal.
- **Baseline:** User-provided Devtool post-closure Git HEAD `52ad3d2b8269155570394357b8181b23b02bac64` / tree `1e82f63a30742b5c31055be9f13ab72df286ce4f`. Existing ACTIONS-06 pipeline journals already persist source/graph/node/request/run/artifact identities; ACTIONS-02 request receipts persist original workflow/source correlation and optional ACTIONS-03 observations. R04 does **not** silently infer an EXO run ID, remote attempt, signer proof, or release/device qualification when they are missing.
- **Implementation:** `devtool/actions_lineage.py` reads an **explicit** ACTIONS-06 pipeline ID and repository/worktree, checks existing bounded private journals with their canonical readers, cross-binds request/source/workflow/run IDs, optionally resolves multiple dispatch edges from the exact unmodified manifest digest, validates recorded attempts, and returns an immutable machine-facing status view. `devtool actions lineage --pipeline-id ID [--github-repo owner/repo] [--json]` uses canonical CLI schema, plain text or JSON; generated CORE surfaces updated together.
- **Local archive proof:** An exact attempt-bound repository artifact registry can provide *local* archive-byte SHA-256 and ZIP member/CRC verification. The R04 reader never creates registry locks, backups or cache paths, extracts a ZIP, executes downloaded content, or makes a GitHub POST. All local SHA checks are explicitly labeled `ARCHIVE_LOCAL_VERIFIED`, **not** authenticated remote artifact/run or signing evidence. Producer-specific acceptance remains owned by its original subsystem.
- **State taxonomy:** `NOT_STARTED`, `LOCAL_OBSERVED`, `PREFLIGHT_BLOCKED`, `DISPATCH_UNCERTAIN`, `WAITING_REMOTE`, `CI_FAILED`, `CI_PASS_UNVERIFIED`, `ARCHIVE_NOT_VERIFIED`, `ARCHIVE_LOCAL_VERIFIED`, `UNKNOWN`; the qualification fields are `not_authenticated_in_offline_projection`, `NOT_RUN`, and `BLOCKED_NOT_QUALIFIED`. Never promote a journal's `success` or an exit code to release/device eligibility.
- **Negative coverage:** foreign repo/worktree, symlinked or malformed journal/registry, absent request correlation, wrong source/workflow/run/attempt, stale/nonterminal observation, duplicate/foreign registry evidence, tampered ZIP, path traversal, missing archive, unbounded registry, rendered secret-bearing `detail`, and incomplete manifest. No automatic uncertain POST retry.
- **Deferred R04 obligations:** Existing ACTIONS-06 `local-invoke` does not retain the child EXO run ID. No authenticated current remote API GET is performed by this offline projection. R04 phase 2 must add canonical EXO run correlation and optional separately authorized live reviewed provider evidence; do not synthesize identity or treat read-only artifacts as formal source attestation. Preserve RMP H1/H2 and six independent real-substrate/release obligations.

## LIFE-CLEANUP-CTIME-20261020 — Closure transaction succeeded through commit but failed at cleanup

- **Status:** Historical failed closure transaction preserved; no cleanup security relaxation applied in R04. Termux run `ac0b291eba434cbf8981b5f019c0a10a` executed **131 passing tests**, applied seven files, committed `52ad3d2`, verified changed paths and then returned `artifact_cleanup_failure`. Native test pass and source commit survive; transaction itself did **not** pass.
- **Read-only diagnostic evidence:** RUN-V3 execution/source attachments and catalog identities matched; original ZIP remained. Its recorded source snapshot versus current metadata differed only in `ctime_ns`. Two separate `cleanup-receipt` attachments were `partial`, protecting repository candidates due to an old validation session. These partial receipts did not identify the deletion failure of the external Downloads ZIP. A subsequent `artifact-check` SHA mismatch came from rechecking against changed post-apply sources, and is **not** proof of the original cleanup cause.
- **Disposition:** Preserve original historical failure and ZIP; do not delete or replay old uncertain side effects. R04 is installed as an independent fresh source-bound overlay and will provide another sample of fresh-process LIFE cleanup. If it fails at the same boundary, collect the exact worker refusal before proposing a narrowly proven source-identity fix; do not weaken digest/path/run/attempt guards.


## CORE-X04-R04-TERMUX-COLDSTART-001 — Native wrapper cold-start smoke timeout during R04 (2026-10-20)

- **Native evidence:** R04 first installation, Devtool run `b8c882f722c34d2a99e50233767312b6`: preflight and archive verification passed, then `77 passed / 1 failed` at `test_devtool_repo_dogfoods_schema2_wrapper_and_source_aware_launchers`. The source-aware `devtoolw --help` subprocess did not finish before the test's fixed **20-second wall-clock timeout** under Termux Python 3.14. The run excerpt contains no further stack trace or terminal rollback receipt; do not claim launcher correctness or completed R04 installation from this attempt.
- **Scope:** CORE-X04 wrapper self-test and adjacent config status/diff CLI subprocess smoke. This is a full cold Python import/CLI parser invocation, **not** the separately budgeted in-process generated-surface computation. The same launcher finished in about 3.1 seconds on Linux, which does not establish native startup speed or exclude a Termux runtime hang.
- **Candidate repair:** Independently test launcher `--repo ... wrapper exec` argument forwarding via a minimal executable probe with a 5-second timeout, so quoting/path behavior cannot be hidden by slow parser startup. Retain real end-to-end help and config smoke assertions, with bounded platform-sensitive timeouts (20s ordinary hosts, 60s native Termux). Do not skip the real CLI, disable its timeout, modify CLI data, touch the canonical CORE-X04 generation budget (5000ms), or promote any hosted/device/release evidence.
- **Acceptance:** Source regression and disposable Devtool apply/commit/cleanup against the **same** pre-R04 tree, then fresh native Termux validation. If the full import continues exceeding 60 seconds or returns a nonzero status, preserve the failure as a genuine runtime issue and profile startup instead of repeatedly raising limits. Preserve the historical failed R04 run and its intact artifact until transactional state is verified. **Status: proposed source-bound R04-R1 replacement; native acceptance pending.**


## R04-LINEAGE-002 — RUN-V3 child correlation and opt-in API observation (2026-10-20)

- **Status:** R04B source implementation with Linux regression/disposable acceptance (native Termux and remote live-run acceptance PENDING). This extends `R04-LINEAGE-001`; it does not introduce a separate coordinator, release qualifier or credential store.
- **New native evidence for R04-R1 foundation:** User-provided Termux Devtool run `078eb17498bf4cbc8b37e697e9c18a5a` completed `88 passed / 0 failed`, Git commit `244d6d7`, fifteen verified paths, exit 0, and LIFE artifact cleanup `ok`. Exact recorded upstream tree for this R04B source-bound candidate: `9d8d779f5e7725d68e1237cb89c3a4e4ac552e4f` (commit timestamps/identities may differ between disposable and native installations). This DOES NOT erase the earlier LIFE failure `ac0b291eba434cbf8981b5f019c0a10a`; both records remain.
- **ACTIONS-06 producer:** `local-invoke` mints a cryptographically unpredictable RUN ID and uses existing one-use, parent-PID-bound `create_managed_run_handoff` for the immediate Devtool child. It checks the exact finalized RUN-V3 catalog entry (`command_id=invoke`, `status=succeeded`, `exit_code=0`) before persisting `exo_run_id` in its existing pipeline journal. Missing/failed child evidence denies node success; interruption remains `uncertain` and never autoreruns the child. Ambient `DEVTOOL_RUN_ID` is not accepted as identity. No new scheduler or run database.
- **R04 consumer:** The read-only lineage projection now resolves recorded `exo_run_id` against the same canonical RUN-V3 catalog, refusing wrong/missing/unfinished identities. Historical journals without child references are displayed as unknown, never backfilled from timestamps or latest-run heuristics.
- **Read-only live review:** `devtool actions lineage --pipeline-id ID --github-repo owner/repo --live --auth auto --json` is an explicit opt-in to credentialed GitHub GET. Each correlated run is checked against the exact originating dispatch receipt, repository, workflow, source SHA, event, and attempt (if durably pinned). Missing historical attempt may be *observed* from the API, but is never written to receipts or borrowed for a local archive check. `CI_PASS_REMOTE_OBSERVED` means an API observation, not source attestation, signing, device qualification or release qualification. No network call occurs in ordinary offline lineage. GitHub POST, artifact download, filesystem mutation and automatic remote retries are absent.
- **Negative coverage:** forged RUN ID, unfinalized or failed child, missing catalog, wrong remote workflow/ref/attempt/event, unauthenticated --live, source drift, uncorrelated dispatch, failed GitHub result, missing local attempt/registry, legacy journals, and no source mutation. Preserve all R02 hosted consumer, R03 Android tool/device, RMP-H1/H2 and release qualification obligations.
- **Remaining R04 acceptance:** Native overlay acceptance of this phase, at least one separately authorized real GitHub GET with exact correlated source/workflow/run/attempt, and original producer-owned artifact/source attestation. Treat R04B source as bounded; no claim of broad hosted/device/release closure.


## ACTIONS-R05-OPERATOR-001 — Unified Textual Actions review/follow/lineage/collection (2026-10-20)

- **Implementation status:** R05 source candidate. Real Textual 8.2.8 wheel supplied by user and installed into isolated Linux test environment, not bundled with overlay or installed on device. Native Termux acceptance remains pending, hosted claims remain unqualified.
- **Upstream accepted R04B:** User-provided native run `7524f3b5b2f54e519036682a0331dd05`: exact source preflight, 113/113 tests passed (no skips), sixteen source paths committed at `f4d5532`, index verified, successful LIFE fresh-process cleanup, final exit 0. This verifies the local R04B scope only; separately authenticated real GitHub GET and producer-owned source/artifact attestation are still OPEN. R04-R1 native 88/88 acceptance and the historical R03 LIFE failure remain recorded unchanged.
- **Affected source:** `devtool/actions_workspace.py`, new `devtool/actions_operator.py`, and `tests/test_actions_r05_operator.py`. Extends existing `actions ui`, retaining its canonical local journal and the UX01/UX02/Actions-R04B consumers. No second journal, execution controller, replay scheduler, credential storage, implicit POST or release qualifier.
- **Explicit operations:** `v` exact request GET review; `f` bounded (12 polls max, 5-second interruptible intervals) exact request follow; `l` offline exact pipeline lineage; `w` read-only GitHub workflow GET; `c` prepare collection. None auto-executes on mount/refresh. Remote work runs off the UI thread and is bound to the selected kind, repository, and identity; changed selection/cancel invalidates late results. Plain mode and JSON are unaffected.
- **Collection safety:** Correlated dispatch only; type a safe *exact artifact name*, submit with Enter, then separate `y` confirmation. Changing text invalidates confirmation; `Esc`/`x` cancel prior to handoff. Canonical UX02 collector independently rechecks exact request, repo, workflow, source, attempt, and artifact digest; selection or an old GET view cannot authorize a download. After confirmation, cancelling the *display* does not claim to undo in-flight collection or remote execution.
- **Terminal and falsification coverage:** Real Textual `run_test` at 40 columns: refresh/selection, on-demand review/GET, artifact focus, no accidental `y` activation during typing, submit/confirm, cancellation, stale async completion suppression; wrong repository/kind/ID, uncorrelated dispatch, release-provenance forgery and untrusted ANSI escape display. Optional Textual availability should be verified in native Termux, not inferred from a Linux wheel.
- **Outstanding:** Native source-bound R05 transaction + interactive Termux smoke; real authorized remote GitHub observation/collector exercised separately with explicitly authorized credentials and proof; broader R09 campaign. Historical run `ac0b291eba434cbf8981b5f019c0a10a` remains a cleanup failure, not retroactively repaired by subsequent successful cleanup.


## ACTIONS-R05A-STATE-002 — Canonical correlated dispatch blocked Actions operator startup (2026-10-20)

- **Native evidence:** User reports R05 applied but `devtool actions ui` exits before the Textual screen with `GitHub Actions: Dispatch receipt state is invalid`. The exact offending private receipt has not been inspected; no claim of real remote run qualification is made.
- **Proven source defect:** `GitHubActionsClient.dispatch()` returns `state=accepted-correlated` when GitHub HTTP 200 supplies a run ID, and `actions_dispatch.dispatch_workflow()` persists that canonical state. However, `actions_workspace._ALLOWED_DISPATCH` omitted `accepted-correlated`, causing `local_recent()` (and thus `actions ui` and `actions recent`) to reject valid persisted history. This is an internal producer/consumer contract mismatch, not evidence the user must discard or rewrite journals.
- **Targeted correction:** Add that exact producer state to the workspace allowlist and enforce a valid positive remote run ID for correlated receipts. Preserve original strict symlink, identity, source SHA, repository and malformed-state refusals; do not silently suppress corrupted records, choose a recent run, auto-GET, download, dispatch or infer release qualification.
- **Testing/acceptance:** Linux direct canonical-receipt regression and real Textual startup at narrow terminal width, original UX01/R05 controls and source-bound disposable apply/commit/cleanup. Native Termux `actions ui` startup and overlay run validation remain PENDING until the operator confirms; Github hosted/source/release gates unchanged.
- **Status:** R05A source remediation; R05 was reportedly installed but interactive startup smoke FAILED. Do not mark R05 fully locally accepted based only on successful overlay application.


## R06A-DAG-PLAN-001 — Named DAG projections and truthful static blockers (2026-10-20)

- **Status:** IMPLEMENTED_SOURCE; native Termux R06A overlay acceptance PENDING. User confirmed R05A `devtool actions ui` works on Termux; exact R05A native overlay transaction receipt was not provided. Preserve the distinction between user-observed usability and formal native suite qualification.
- **Evidence/root cause:** `devtool plan --target T WORKFLOW --json` and `--dot` already existed and the MP09 scheduler was already authoritative. What was missing was a lossless narrow-screen view, Mermaid projection and machine-readable differentiation of declared DAG prerequisites from conservative wave serialization and unknown output reservations. Source: `devtool/cli.py::_plan`, `devtool/workflow_scheduler.py`, `devtool/workflow_graph.py`.
- **Repair:** Add `--ascii` and `--mermaid` to the existing `plan` canonical CLI schema (not a new DAG engine). ASCII preserves all machine IDs by wrapping at 40 columns; Mermaid aliases IDs and renders only actual dependency edges, never scheduling/resource conflict edges. JSON adds `analysis` with exact `depends_on`, canonical scheduled waves, immutable IDs, scope reasons, and the longest DAG chain measured exclusively in *edges* (no inferred execution time). Existing DOT output now escapes hostile labels. Keep parser/help/completions/manpage generated parity. Reject conflicting new output flags.
- **Safety:** No execution, remote calls, state mutation, source attestation, GitHub qualification or release promotion. Do not relax MP09 collision/exclusive-reservation/resource limits or infer a node's runtime state from its static plan. Preserve existing `--dot --json` compatibility. `--ascii` and `--mermaid` require a configured named workflow; current no-target path remains an informational no-op.
- **Verification:** `tests/test_workflow_r06_plan_views.py` (branch/join graph, no fake resource dependency edges, 40-column lossless IDs, safe source-derived JSON, conflicting formats, label escaping) plus MP03/MP09/ACTIONS-A03 and CORE-X04 regressions and exact-tree Devtool disposable apply. No user Termux R06A evidence yet.
- **Still open:** R06B source-aware overlay plan/recover/equivalence UX and review-only adoption doctor; R06C producer→consumer DAG contracts and external evidence. DT-008, DT-005, DT-002/003, and hosted/device/release gates remain unqualified. Historical R03 LIFE failure remains recorded separately; R04 and R04B native cleanup successes do not retroactively erase it.


## R06B-ADOPTION-OVERLAY-002 — Read-only adoption diagnostics and overlay lifecycle planning (2026-10-20)

- **Source status:** IMPLEMENTED_SOURCE; native Termux acceptance PENDING. User reports R06A applied without providing its complete native run receipt. R06A exact source tree is `bffe4de8744e83872d61c557cdc30f44dd040ebd`; no claim of native R06A complete qualification is made from that message alone.
- **Adoption problem:** A03/A09 already enforce target-local native DAG job selection and strict Ubuntu host admissibility, but operators had to try adoption per target/workflow to discover every blocker. New `devtool actions adoption-doctor [--target T] --json` inventories configured workflows and reuses A03/A09 `_selection`, distinguishing `locally_eligible`, `review_required` (existing workflow/alias or dirty worktree), and `blocked` (privileged, unsupported or missing host/toolchain requirements). It does not create YAML, alter `.devtool.toml`, dispatch, open the network or qualify a hosted run.
- **Overlay problem/repair:** Existing `overlay check` and `overlay status` offered fragmented checks; neither gave a conservative actionable no-write disposition. New `devtool overlay plan ARTIFACT --json` composes the canonical structural/source checksum preflight with existing GIT-FRX patch equivalence, a strict full-inventory SHA-256 fallback for legacy overlays without base trees, conflict detection and Git worktree status. It returns `candidate_only`, `already_present`, `manual_review`, or `refuse`, always sets `safe_to_auto_apply=false` and `safe_to_auto_recover=false`, and never executes rebase, apply, rollback, cleanup, Git mutation or implicit retry. The original source-bound apply and rollback remain independently authoritative.
- **Newly found existing defect:** `devtool/overlay/conflicts.py::detect_inventory_conflicts` called an unimported `sha256_file` for a pre-existing add path. This could cause `NameError` rather than report a conflict. Use the existing source-path SHA contract; also detect symlinks (including dangling links) and never flag symlink destinations as automatically rebaseable. Explicit real regression added.
- **Acceptance:** New positive/negative tests cover safe target selection, refused `adb` host job, manual review for dirty/existing/symlink adoption, no source writes, actual conflict hashes, legacy installed-file hash presence, partial/divergent/symlink refusal and fail-closed overlay status; existing ACTIONS-A03/A09, GIT-FRX, CORE-X04, R06A, and LIFE controls must pass in a source-bound disposable Devtool transaction before native installation.
- **Unfinished:** Transaction-specific rollback-failed recovery, automatic installation plan/resume and producer→consumer adoption contracts are NOT implemented in R06B; treat them as subsequent R06C/R06D obligations. Keep DT-008 partial, historical LIFE failure, all hosted/device/release and RMP H1/H2 evidence gates open. No force, ignore-checksum, guessed GitHub authenticity or automatic deletion is introduced.

### R06B regression boundary: pre-existing OV06 rebase cleanup fixture failure

- During R06B validation in this Linux environment, `tests/test_arch_ov06_07_overlay_plugins_json.py::test_apply_overlay_rebase_applies_stale_inventory` failed because the disposable rebase file disappeared before LIFE cleanup; `artifact_cleanup_authority_missing` was reported. The exact same test was separately run against the **unmodified R06A** source and failed the same way. This is a reproduced *pre-existing* baseline failure, not evidence R06B altered the apply/rebase logic. Preserve the regression as OPEN_DIAGNOSIS rather than silently declaring the broader suite passed. The R06B acceptance selection does not include this known-baseline failing test; still require full native qualification later.


## R06C-FRX-DAG-CONTRACT-003 — Child-owned producer→consumer graph evidence (2026-10-20)

- **Source status:** IMPLEMENTED_SOURCE; user reports applying R06B, but has not
  provided its native machine receipt. R06C native acceptance is independently PENDING.
- **Underlying gap:** FRX05 and FRX-Y08 already resolve real cross-repository
  child-owned public contracts, DAG dependencies, typed artifact inputs, capability
  edges and frozen Git source identities. Operators lacked a compact standalone
  producer→consumer contract view and an explicit opt-in strict frozen-lock gate.
- **Change:** Add `devtool federation dag-contract [WORKFLOW|--group G] [--json]
  [--require-frozen]`, an immutable projection of existing FRX planner identities,
  dependencies, source-lock digests and artifact declarations. The digest is a
  source-based fingerprint, NOT a remote attestation. No new scheduler, execution,
  Git mutation, automatic freeze, network, data migration or receiver configuration.
- **Rejection and safety:** Refuse invalid or ambiguous subject, missing public
  child identity, duplicate repository, mismatched frozen subject or any exact
  source/graph drift when `--require-frozen` is present. Maintain original FRX
  ownership and artifact byte verification as independently authoritative.
- **Acceptance:** `tests/test_federation_r06c_dag_contract.py`, FRX05/Y04/Y06/Y08,
  full canonical CLI/help/manpage/completion parity, source-bound Devtool overlay
  transaction and LIFE cleanup, then native Termux verification.
- **Open:** Real Devtool→Skybind/Cinderbox/BKX/chrootctl/Studyflow consumer source
  configuration adoption, typed artifact transfer receipts, hosted and device
  provenance, R06D specific rollback-failed recovery, all existing RMP/FS03 gates.

## R06D-LIFE-RECOVERY-004 — Nonmutating apply/rollback and LIFE evidence plan (2026-10-20)

- **Status:** IMPLEMENTED_SOURCE; user reports R06C applied without a detailed native run receipt; R06D Linux disposable and native Termux qualification are independent.
- **Problem:** `transactions.find_active_transactions()` and its `rollback --list-transactions` consumer delete missing, malformed or stale active markers as a housekeeping side effect. A recovery *inspection* should never erase markers that may be the last forensic breadcrumbs of `rollback-failed`, interrupted apply, failed validation, or partial LIFE artifact cleanup. Also, a failed cleanup ZIP must not be retried or deleted based on the source commit or later successful apply.
- **Change:** `devtool overlay recovery-plan [--transaction EXACT_ID] [--limit N] --json` is an explicit, no-write observation path. It reads exact repository-scoped active journal pointers without following arbitrary foreign paths or marker symlinks; reports corruption/dangling pointers without removing them. For journaled rollback phases it uses existing transaction guards to assess target resume checkpoint, original-file backup existence, live PID ownership, and Git HEAD/index guards. A clear result only means `clear_at_observation_only`, never a durable authorization or automatic resume. Actual rollback remains owned by the canonical `devtool rollback --transaction ID` command, whose checks must run again at execution time.
- **LIFE boundary:** Opens RUN-V3 with `create=False`; surfaces bounded historical failed runs and cleanup-receipt counts, including explicit `artifact_cleanup_failure` when present. Does not rehash/delete a previously failed ZIP, synthesize an authority, rewrite a catalog, acquire a repository lock, or touch transaction/history markers. The ordinary Devtool CLI invocation can still emit its usual RUN/command telemetry; the recovery projection itself introduces no recovery journal or mutation. Prior Termux `ac0b291eba434cbf8981b5f019c0a10a` remains a failed cleanup despite native R04/R04B LIFE success.
- **Tests:** Real no-state CLI and JSON route, corrupted/symlink/foreign markers, missing rollback checkpoint, missing backup, altered user target, live PID, pending validation, exact ID selector, historical LIFE failure and no-files-created checks. Keep CORE-X04 generated CLI parity and existing LIFE/GIT transaction tests.
- **Remaining:** Automatic repair of incomplete LIFE cleanup and crash-window transaction replay is **NOT** implemented in this bounded R06D source increment; it would require fresh original ZIP evidence and a separate fail-closed durable authorization contract. Preserve historical OV06 cleanup-fixture issue, R02 hosted, R03 device, FRX real consumer and formal release/security gates.

## R06D-R1-TERMUX-RECOVERY-005 — Native rollback-result logging and async child reaping (2026-10-20)

- **Native failure evidence:** User-provided Termux Devtool apply run `ba1c2342b39444e6a8fc7eee993aba75` failed during the R06D validator after 55 tests had passed. The single failure was `tests/test_transactions.py::TransactionalApplyTests::test_stale_apply_transaction_is_restored_before_next_command`: Python 3.14 reported two `ResourceWarning('subprocess ... is still running')` instances in a forced `gc.collect()` assertion. Captured stderr also exposed `UnboundLocalError: cannot access local variable 'results' where it is not associated with a value`. The run did not reach commit/LIFE cleanup and cannot be called native accepted; preserve its failed transaction receipt. The heartbeat was nonfatal.
- **Root-cause code audit:** The stale transaction auto-rollback branch in `devtool/cli.py::main` used `results=results` when emitting post-rollback summary before ever assigning `results`. The separate pending-validation refusal branch did the same. This could emit an internal error after a correct, otherwise fail-closed rollback/refusal, obscuring the actual result and weakening operator diagnostics. Independently, `devtool/log_retention_worker.py::schedule_retention` spawned detached asynchronous rotation workers with `subprocess.Popen` but dropped the handle without waiting/reaping. Python 3.14 can collect a live Popen object and surface `ResourceWarning`, especially when a transaction test forces GC.
- **R1 corrective implementation:** Supply `[rollback_result]` and `[pending_result]` directly to log summary at the two early-return sites. Retain the original asynchronous, durable log-write and worker-lock design, but start a daemon *waiter thread* holding each spawned `Popen` handle until the worker exits. This reaps the process without waiting on the foreground command, performing repository cleanup, or changing LIFE deletion/transaction authorization. No original run or worker-receipt rewriting.
- **Regression contract:** Extend `tests/test_transactions.py` to prove the exact structured rollback and pending results reach the summary (preventing another hidden `UnboundLocalError`), while preserving checked restored paths and no accidental transaction deletion; extend `tests/test_git_log_retention_latency_v3.py` with a waiter/nonblocking acceptance check; run original R06D, LIFE-X01, GIT-FRX02, R06C, R06B and CORE generated-surface tests under the normal source-bound Devtool validator. Real Termux Python 3.14 rerun remains required.
- **Qualification:** Replacement R06D-R1 is source-verified only until a complete native apply run passes its validator, Git commit, verification, and LIFE cleanup. **Do not stack on the failed R06D transaction**. Confirm failed transaction has finished rollback to exact R06C tree before applying; no `--force`, cleanup bypass, or warning suppression.



## R07A-SSH-RETURN-EVIDENCE-001 — Project-validation success required complete, identity-matched return evidence (2026-10-20)

- **Status:** SOURCE_IMPLEMENTED; native Termux and independent worker acceptance PENDING. User reports R06D-R1 applied; its detailed receipt was not reviewed.
- **Reproduced source gap:** `ssh_qualification._run_project_validation` counted new `manifest.json` paths after a successful child exit and marked a project-validation pass without checking the adjacent `transfer.status`, declared return destination/profile/source-sync, matching RUN/SESSION ownership, or the manifest's producer-identity digest. A partial rsync return could therefore count as qualifying source-only evidence even if the manifest was merely created. An independently valid producer checksum also did not by itself bind duplicate outer manifest source-sync fields.
- **Correction:** Require owned return path, no manifest/status symlink, canonical `_read_manifest` producer checksum, matching producer-versus-outer run/session/profile/destination/source-sync, valid local mirror boundary, nonempty sync ID, and exact `state=complete` / `returncode=0`. Record SHA-256 of manifest/status bytes and source-sync ID in the existing project pass projection. Preserve original failed/partial records for explicit `ssh recover`, with no deletion or false release promotion. Snapshots preserve original manifest path rather than resolving an alias ahead of symlink inspection.
- **Tests:** `tests/test_ssh_r07a_qualification_returns.py` covers success, incomplete/failed/pending/ambiguous status, missing status, tampered source-sync, foreign worker, symlinked/foreign manifest, duplicate status, and exit-zero remote child with failed rsync. Selection also includes SSHW06, SSH-02/03/04, RS11/RS12 and existing SSH qualification. All checks are local mocks/source tests; no independent hardware evidence.
- **Open follow-ups:** live native Termux/worker source-bound receipt; stage-level durable write-ahead failure receipts and interrupted return handoff; fixed-point and private-exclusion live metrics; PRoot detection and guarded service supervision; FS03 performance, UX-NOTIFY-01, and final R09 acceptance.


## R07-CONSOLIDATED-20261020 — Fold R07B/C/D into R07A before native installation

- **Disposition:** SOURCE_IMPLEMENTED; 197 selected SSH tests PASS and source-bound disposable apply/commit/LIFE cleanup PASS; NATIVE_TERMUX/REMOTE_WORKER/FS03/UX-NOTIFY NOT_QUALIFIED. This bundle **replaces** `DEVTOOL_SSH_RETURN_EVIDENCE_R07A.zip`; it is source-bound to the same exact accepted R06D-R1 tree, not a patch to apply after standalone R07A.
- **R07B, stage receipts and returns:** Historical SSH recovery/returns mechanisms already existed. `ssh qualify` additionally persists fsynced, atomically replaced private stage evidence of interrupted/failing/completed qualification without recording SSH command output, secrets, or error strings. A bounded read-only return inventory in worker doctor checks declared producer identity, manifest ownership, transfer-state consistency and digest, refuses symlinked files, and does not automatically sync, recover or delete any data. The inventory is a **snapshot**, not a credentialed host authority; incomplete bundles remain recoverable only through separate explicit commands.
- **R07C, runtime/supervisor:** Doctor reports a conservative native-Termux login classification and separately reports PRoot capability. `guest_execution_attested=false` remains fixed until there is independent guest evidence. Existing PRoot worker supervision continues to refuse uninspectable/live guest tmux jobs and requires confirmation; the consolidated overlay does not add a parallel manager or kill operation.
- **R07D, benchmark/notification:** Report actual measured SSH source-sync/tmux/return/plan/cold+warm duration and ratio, without inventing a zero baseline or interpreting local timing as a release/FS03 pass. `fs03_qualified=false` and `notification_qualified=false` until an independent real-worker or Android notification acceptance supplies evidence. No automatic Android notification or additional supervisor side effects.
- **Tests:** `tests/test_ssh_r07_consolidated_reliability.py` validates atomic permission-restricted stage evidence, failure retention, redaction, source/provenance inventory, symlink and invalid status refusal, native shell classification, no synthetic performance qualification. Existing SSHW-01/02/04/05/06, SSH-01/02/03, RS11/RS12, project return and CORE tests are included in the source-bound validator.
- **Outstanding:** Confirm native Termux overlay acceptance; real configured worker run with exact sync/return and cold/warm performance; fs03 fixed-point latency threshold and notification delivery acceptance; R08 security and R09 system qualification. Missing external receipts remain open and are not claimed closed by the consolidated ZIP.


## R07-CONSOLIDATED-AUDIT-20261021 — Promise audit before native install

- **Evidence:** Review of the packaged eight-path R07A replacement, R07A–D contract and real source identified six local proof/behavior gaps: orphan transfer statuses were invisible to `return_recovery_inventory`, unsafe directory entries bypassed its record limit, a symlinked `.devtool` parent was not rejected, qualification-stage evidence lacked an exact local Git commit/tree, failed stages had no explicit terminal stage marker, and native Termux could be inferred from only one inherited Termux path under PRoot.
- **Change:** Retain the consolidated single-overlay delivery; repair the inventory boundary, add fail-closed private staged receipts with locally observed Git identity, record explicit stage failures (including KeyboardInterrupt), and require corroborating HOME/PREFIX for native classification. Regression coverage includes both positive and adversarial cases. Existing SSH return verification and independent supervisor safety retain authority; no auto-recovery or deletion.
- **Honest scope:** Stage receipts are specific to `ssh qualify`; normal SSH operations still use preexisting journals. Source hash/commit is local provenance, not remote attestation. R07D provides measured local phases only; real FS03 benchmark, Android UX-NOTIFY-01 acceptance, independent worker and device/release qualification remain **OPEN**. Do not close them from 197 or newer local test counts.
- **Status:** AUDIT_REPAIR_SOURCE_CANDIDATE; native/real-worker external receipts pending. This is a replacement for the uninstalled consolidated ZIP, pinned to the same R06D-R1 tree.


## R08-SECURITY-AUTHORITY-20261021 — Current-HEAD H1/H2 independent acceptance preparation

- **R07 baseline:** Operator reported audited R07A applied; a complete native apply/pytest/LIFE receipt was not provided. R08 source guard requires the exact audited R07A Git tree, not a claimed run result.
- **Gap:** Historical `rmp_final_hardening.py` requires an old exact security-hardening commit and cannot be executed against current R07 HEAD to provide a valid new acceptance. Neither the 308-row SHA match nor a locally reported Phase4 test count is independent authority. The original six substrate requirements and eleven Q02 historical contracts remain open.
- **R08 implementation:** Add the read-only `python3 -m devtool.rmp_r08_assessment` source/authority matrix for current HEAD. Pin tracked H1/H2 source and historical CSVs to actual Git blobs, require a clean worktree, reject missing/symlinked/dirty evidence, recheck N01 exact 308 assignment/owner reconciliation and Q02 exactly 20 probes/six historical mismatches. Accept an optional historical Phase4 receipt only with explicit full ancestor commit and validated source/tree/blob hashes; label that result *locally correlated, unsigned*. `--run-tests` runs focused current-source security negatives and hashes raw output without persisting it. Optional `--output` uses exclusive private (0600) creation; default performs no file write. Formal H1/H2, U01 and release flags are **always false**.
- **Formal disposition:** N01/N02/C04/C05/C06/Q01/Q02/U01 remain OPEN per issue and independent family owner. ENV-A06, ANDROID-A16, DIST-A01, DIST-A12, DIST-A13 and TEST-A11 remain BLOCKED_EXTERNAL, along with true Android HUD, independent worker, native N02 persistence, Q02 eleven historical contracts and signed N01 original. No old C02/C03/B01/C01 receipts or reported release decision rewritten.
- **Acceptance:** Source/disposable regression and negative tests are required before R08 implementation can be called source-qualified. Native Termux and independent external authority require separate receipts. Successful source assessment means only `PASS_R08_SOURCE_INVENTORY_EXTERNAL_GATES_OPEN`; it is **not** `PASS_R08_FORMAL_SECURITY_CLOSURE`.


## R09-CROSS-CAMPAIGN-20261021 — Current-source, separately gated final acceptance

- **R08 native evidence:** Operator supplied current-source R08 assessment for commit `6620bd553277020a91ab0495dcbcf94239760f6a`, tree `82edc9467365060832415027994edef9b16590a7`, with **103 passing selected tests**, `PASS_R08_SOURCE_INVENTORY_EXTERNAL_GATES_OPEN`, 308 retained N01 rows and 20 historical probes/six mismatches. This confirms the bounded local security inventory; no signed N01 authority, native RMP formal closure, hosted/device or release qualification was supplied. Preserve historical receipt distinctions.
- **R09 gap:** Prior campaign milestones each had independent source tests and partial native receipts, but no **single exact-current-HEAD matrix** separating source, local Termux, hosted CI, consumer execution, real device/worker, independent security and formal release decision. User-supplied JSON files may assert arbitrary `release_qualified` booleans; an unhashed or foreign-source receipt is not an accepted gate.
- **R09 implementation:** `python3 -m devtool.rmp_r09_campaign` constructs an exact-Git-HEAD, clean-worktree acceptance index, validates the tracked R09 contract and invokes R08's current-source 308/20/6 assessment. `--run-tests` runs a bounded, current-HEAD R01–R09 focused selection; `--full-tests` can explicitly run the larger current tree's pytest corpus. `--evidence` or `--evidence-dir` imports bounded JSON snapshots as SHA-256-addressed, source-compared **untrusted observations**, with symlink/path/size/directory limits and fail-closed classifications. Outputs are read-only by default, with opt-in new 0600 JSON output and a Termux-friendly text mode.
- **Qualification boundary:** `source_pass` requires observed current-source regressions. `native_pass` requires the tests executed in a corroborated native Termux runtime and an actual installed `devtool --help` smoke, and means **local/native scope only**. `hosted_pass`, `consumer_pass`, `device_pass`, `security_formal_pass`, `formal_release_qualified` remain false without independently verified authorities; imported JSON cannot promote them. `--require-full` returns an explicit blocked exit code even when local checks pass. R09 never starts CI, installs to Android, calls root, rewrites receipts or authorizes a release.
- **Outstanding formal external gates:** CI24/24 hosted shards, G3R 13 native lanes, FS03, A09 Skybind Go and exact Actions run/attempt/artifact, configured Skybind/Cinderbox/BKX/chrootctl consumers, Android installed pair/Trusted Broker and instrumentation, independent SSH worker/notifications, all N01/N02/C04/C05/C06/Q01/Q02/U01 families, six exact substrate obligations, and independent release-owner decision. Further evidence requires separate verified, source-bound procedures, not edits to the R09 matrix.
- **Status:** SOURCE_CANDIDATE; update to SOURCE_DISPOSABLE_VERIFIED only after a complete disposable Devtool apply/commit/cleanup; native Termux and formal gates stay independently pending.

### R09-LEGACY-TEST-COMPAT-20261021 — Historical anchor verification versus moving HEAD

- **Current-source evidence:** The broad R09 suite initially exposed two obsolete assumptions in legacy R01/R03 tests: `test_g2_immutable_ancestry_exact_in_source_checkout` and the G8 strict-source fixture treated the October 20 R01 accepted `actions_pipeline.py` blob as though later R04B source updates were forbidden; R03's frozen 44-issue baseline and exact `github_actions.py` hash similarly ignored legitimate later additions. R03's native CORE timing issue had also advanced from `SOURCE_TESTED_NATIVE_PENDING` to `NATIVE_131_TESTS_PASS_TRANSACTION_CLEANUP_FAILED` after actual native evidence. These were legacy test expectations, **not** proof that source rollbacks or historical proof objects were corrupted.
- **Correction:** R01's historical fixture now reads its immutable Git-tree objects from the accepted `70c4f96` commit; R03 verifies the exact original accepted R03 Git tree `8f61a285...` blobs while confirming the current working files match current HEAD. R03 index checks preserve required historic issues while allowing subsequent appended ones; the adversarial erasure case deletes a *protected* issue. The CORE-X04 historical failure remains an accepted negative historical observation, never rewritten to success. The original G8 and R01–R03 witness files are not modified.
- **Qualification:** Local source regression passes do not imply original signed provenance or current hosted source attestation. R09's independent external matrix remains open.
- **Final R09 hardening:** Historical R01/R03 tests resolve immutable accepted **Git tree IDs** (not incidental disposable/native commit hashes), so identical qualified contents remain testable on Termux. Source and local-native `pass` require zero skipped tests; absent Textual coverage cannot silently claim complete acceptance.


## NEXT-00 and NEXT-01 source work — 2026-10-21

- **DT-056 / NEXT-00 — Candidate:** Reconciled all DT-001–DT-056 in the
  canonical machine traceability index. Historical R01 and R08/R09 rows are
  retained without retroactive evidence promotion.
- **DT-013 / NEXT-00 — Candidate:** pipeline example uses tracked
  `docs/ci/actions-pipeline.json`, verified by a disposable Git fixture.
- **DT-014 / NEXT-00 — Already supported:** existing CLI
  `--keep-artifact` suppresses normal post-success source ZIP cleanup. Failure
  preserves the input; repeat/retry requires `--keep-artifact` on successful
  execution. Do not add a redundant `--keep-input` synonym.
- **DT-028 / NEXT-00 — Candidate:** `python -m devtool.bootstrap_doctor`
  runs in a standard-library parent with isolated child import, full traceback
  and cause categorization, without an automatic source mutation.
- **DT-020 / DT-029 / DT-030 / NEXT-01 — Candidate:** added strict inspect,
  explicit retire escrow, reversible unretire for superseded preflight-only
  rollback failures. Existing normal rollback/abandon integration unchanged.
  Native Termux and actual MMRL historical journal authority remain pending.
- **Acceptance status:** no independent native/hosted/device/security/release
  closure implied by local source or disposable tests.

## 2026-10-21 — NEXT-02 typed evidence source candidate

**DT-006, DT-010, DT-017, DT-023, DT-051, DT-052 and DT-054:** Implemented a conservative shared *source-level* evidence/host-admission kernel, without promoting the historical proposal labels to externally validated.

- **Source:** `devtool/evidence_kernel.py`, `devtool/evidence_lineage.py`, `devtool/artifact_evidence.py`, existing `actions_adoption.py`, canonical CLI schema and generated surfaces.
- **Contracts:** versioned typed per-domain source/run/attempt/test/artifact claims; shape/identity validation; no implicit `PASS` trust; genuine test report normalization (JUnit/pytest, TRX, Go, Cargo, JS JUnit); no-write registry rehash and exact source/run/attempt join; host refusal for OS/architecture/privilege/signing/device/toolchain; R04 EXO/Actions + R06C federation read-only join.
- **Tests:** focused NEXT-02 falsification test module and A03/A09 parser/generated CLI regressions, with separate native/hosted/consumer/device/authority evidence intentionally NOT_RUN.
- **Remaining work:** real hosted producer attestation, comprehensive language-specific artifact verification (NEXT-03/04/07/08), in-owner EXO report ingestion and independently collected R09 formal release evidence. The file digest is **not** a signature. See `docs/DEVTOOL_NEXT02_TYPED_EVIDENCE.md`.
- **Status:** source implementation candidate; only local tests described by the included delivery receipt, no user-device installation claim.

## 2026-10-21 — NEXT-03 hosted provider framework candidate

- **DT-041 (P0, partial):** Introduced `devtool/actions_language_providers.py` versioned allowlisted provider protocol and two independent adapters (`go`, `python-shell`), with strict static argv, host/toolchain, tracked source/lockfile admission. Does not grant arbitrary third-party/dynamic plugin execution.
- **DT-035 / DT-053 (partial):** Added source-bound single-hosted-job DAG export (`actions provider-plan/export`) using existing native DAG invocation and NEXT-02 admission. Generation includes bounded matrix, checksum revalidation and provenance-only artifacts. General DAG-node-to-GitHub-needs, selective CI, secure lock-aware caches and cross-host jobs remain open.
- **DT-042 (partial):** G5 fixture lane unchanged; no independent real-project Go/Python hosted consumer acceptance is claimed from this synthetic source qualification. Requires actual consumers and authenticated GitHub run/attempt/artifact.
- **DT-055 (partial):** `actions provider-doctor` surfaces per-workflow provider eligibility and blockers without mutating source or asserting host toolchain presence.
- **Evidence:** `tests/test_next03_language_providers.py` and existing NEXT-02/A03/A09/G5/core surfaces regressions; source-bound overlay acceptance receipt required before status can advance to disposable verified. **Native/hosted/consumer/device/security/release** authority remains separately pending.
- **Files:** `devtool/actions_language_providers.py`, `devtool/actions_cli.py`, `devtool/core_cli_schema.json`, `share/core-generated/*`, `docs/DEVTOOL_NEXT03_LANGUAGE_PROVIDERS.md`, associated tests and roadmap/ledger changes. No CI dispatch or release publication performed.

### 2026-10-21 — NEXT-04 source candidate and outstanding acceptance

- **DT-031/033/043/044**: Go and Python provider source paths extended with reviewed distribution manifest, exact DAG producer ownership, basic native versus cross-target admission, local binary/package verification, Python `--no-isolation` and hash-pinned requirements. Existing NEXT-03 provider export and NEXT-02 evidence rules are reused. Source tests include actual local Go compilation and adversarial wheel/sdist fixtures. **Partial-source / no independent hosted/consumer qualification** until authentic xbsctl/BKX and dotcli/mike-cli-suite runs are collected; do not mark all provider promises completed.
- **DT-010/017/023/051/052/054**: Candidate verification is read-only, makes no signed/release claim and does not promote non-authenticated results. Exact source/run/attempt receipt and substantive real test reports remain external or broader later gates. Missing/failing package bytes block upload rather than becoming a green pass.
- **New design nuance**: `dist/` must be an explicit tracked contract, never uploaded using a blanket directory glob; reject source mutation, symlink/absolute/unsafe output names, pre-existing outputs, wrong Go module/arch and Python metadata/RECORD drift. Python `requirements.txt` needs a full hash-locked build/backend closure; syntactic test locks are not a real consumer dependency proof.
- **Deferred**: signed release production, Go Android/Termux cross-build provenance, real install-wheel smoke, Go race/CGO matrix, Python coverage/substantive tests and independently authenticated consumer-hosted receipts. See `docs/DEVTOOL_NEXT04_DISTRIBUTION_PROVIDERS.md`. No live GitHub dispatch/device/signing performed in this implementation.

## 2026-10-21 — NEXT-05 source implementation: multi-repository adoption

- **DT-024/027/034 (source-level partial):** `devtool/adoption_batch.py` implements a source/tree/branch/remote/overlay-hash pinned plan, a global prepare barrier, durable per-repo `FRESH` / `APPLIED_UNCOMMITTED` / `COMMITTED` / `PARTIAL` / `DRIFTED` / `BLOCKED` dispositions, explicit batch apply/resume, no cross-repo automatic rollback, and honest committed predecessor receipts. It refuses transaction markers and unrelated index/worktree edits. A renamed checkout is admitted only when its declared Git identity actually matches.
- **DT-025/026 (source-level partial):** Supported safe legacy TOML transformations and generated `devtoolw`, `devtoolw.cmd`, `.devtool-wrapper.lock` can join an allowlisted generated overlay and one native repository commit. Unsupported concurrent config edits, unsafe migrations, unmanaged launchers and symlinks block; the native Devtool per-repo transaction remains the owner of the `FRESH` apply.
- **DT-004/008/011/021/032:** Retain **partial/open** status where appropriate: arbitrary extension migration, wizard-generated target/job adoption, generalized rebase/reconstruction and third-party legacy launcher conversion are not complete. The coordinator does not silently claim these features.
- **Evidence:** `tests/test_next05_adoption_batch.py`, `docs/DEVTOOL_NEXT05_MULTI_REPO_ADOPTION.md`, canonical `adopt-batch` CLI schema/generated surfaces. Disposable Git fixtures are source tests only, not six live consumer adoption receipts.
- **Release:** independent native Termux, hosted, consumer, device, security and formal release gates remain `NOT_RUN` / unqualified. Existing R0–R9 assertions are not rewritten.

## 2026-10-21 — NEXT-06 source implementation: two-repository extraction

- **DT-037 (partial source):** New `devtool migration` preview/prepare/execute/resume/status operates on two exact Git HEAD/tree/branch/remote locks, verifies a global precommit safety barrier, commits destination before source pruning and keeps durable partial receipts. **Not** a cross-repository atomic transaction, remote/production attestation, or automatic history graft.
- **DT-038 (partial source):** Transferred Go module and workspace manifests reject unresolved `../..` assumptions and accept exact, reviewed replacements. Existing modern target-local `runner="command"` declarations are not implicitly coerced to legacy syntax. Actual XBS-Go workflow qualification remains outside this lane.
- **DT-039 (partial source):** Explicit exact-once source file rewrites plus forbidden-identifier scanning guard dependent target/CI/wrapper/installer references. They do not discover or rewrite arbitrary dependents without an operator-provided reviewed inventory, so automatic dependency-graph pruning remains open.
- **DT-040 (partial source):** Source-tracked vs ignored generated vs unknown source inventory; ignored files including `local.properties` are SHA-256 escrowed externally prior to changing either checkout. Hash-tampered or unbacked source, symlinks, nested repositories and unexpected untracked source fail closed. By default ignored outputs are not deleted; exact optional cleanup follows both commits.
- **Evidence:** `devtool/repository_extraction.py`, `tests/test_next06_repository_extraction.py`, `docs/DEVTOOL_NEXT06_REPOSITORY_EXTRACTION.md`, canonical `migration` CLI + generated surfaces. Real current-source consumer artifacts and independently qualified builds are **NOT_RUN**. Preserve historical DT proposals and R09 gates.

## 2026-10-21 — NEXT-00–06 cumulative source audit and NEXT-04 integrity hotfix

- **DT-057 / Go candidate provenance (P1, reproduced):** Go module/path/header were checked, but the binary `go version -m` build settings `CGO_ENABLED`, `GOOS`, `GOARCH` were ignored. A real CGO-enabled build incorrectly satisfied a contract declaring CGO disabled. Hotfix verifies the actual recorded settings without executing the binary. Local falsification test added; independent hosted proof remains NOT_RUN.
- **DT-058 / Python wheel consistency (P1, reproduced):** `WHEEL` tags only needed to exist and declared console-script names were not checked against their tracked script targets. Hotfix verifies filename-vs-WHEEL compatibility and exact `pyproject.toml` entrypoint target. Valid wheels still pass; signed release remains false.
- **Cross-campaign audit:** `docs/DEVTOOL_POST_NEXT06_ROADMAP_AUDIT.md` and its machine JSON separate completed bounded source work from open feature promises, consumer acceptance, source regression, native, hosted, Android device, formal security and release. No historical R08/R09 evidence was overwritten or falsely closed.

## 2026-10-21 — RM-00 shared contracts (source-bound implementation)

- **DT-017 / DT-041 / DT-051:** Added `devtool/contracts_v2.py` with immutable reviewed Go/Python provider descriptors, exact contract-feature negotiation, fail-closed explicit host requirement checks, and a legacy-compatible typed evidence reader that never elevates reported `PASS` to authenticated authority.
- **Integration:** Existing V1 provider adapters remain intact and registered by a source-reviewed allowlist; `graph_host_admission()` now enforces optional typed `ci_host_requirements` on resolved target-local DAG nodes, and `actions provider-doctor` returns reviewed provider capability descriptors.
- **Target validation:** Every RM overlay requires explicit `devtool` target selection, source-bound Git/asset checks, the native `plan validate` target contract and focused RM regressions. Native compile/build/device or GitHub acceptance are not implicitly claimed.
- **Next work:** RM-01 adds owned temporary output scopes, RM-02 ready-queue scheduling. Later RM-06/07 implement multi-job hosted export and expanded provider API behavior. No non-built-in provider can be dynamically loaded by RM-00.

## 2026-10-21 — RM-02 Termux concurrency regression (source implementation)

- **Issue RM02-TERMUX-02 (P1, reproduced):** The inherited MP09/MP10 concurrency test required two nominal 0.45-second subprocesses and all startup/dispatch overhead to finish in under 0.82 seconds. On Termux it reported `1.0314296409997041` and failed post-apply validation without proving serialization. The source-bound overlay rolled back and retained RM-01.
- **Change:** Replace runtime-threshold concurrency proof with a two-process peer-marker handshake. Real subprocesses must be simultaneously in progress to pass. Serialized execution causes an explicit timeout/failure, while slow concurrent execution remains valid. No regression suite is removed, and RM-02's prior fail-fast scheduler fix remains intact.
- **Evidence:** `tests/test_mp09_mp10_safe_scheduler_plan.py::ParallelExecutionTests::test_safe_independent_nodes_really_execute_concurrently`, `tests/test_rm02_dependency_ready.py` and `docs/RM02_PROMISE_AUDIT.md`. Post-apply native `devtool` target validation and transaction rollback remain mandatory.
- **Status:** implementation repaired in source-bound RM-02 overlay; genuine Termux installed result remains pending the user's validation. This is separate from source and remote qualification gates.
