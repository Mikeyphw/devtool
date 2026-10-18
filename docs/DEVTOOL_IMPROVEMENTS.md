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
