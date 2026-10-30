## G1-CURRENT-CONTRACT-REMEDIATION-20261026 — Hosted failures, real apply/rebase cleanup and EXO lineage

- **Status:** Source-bound candidate implemented; targeted local regression passed, disposable overlay installation/forced-rollback acceptance and independent 24-shard GitHub rerun must be recorded separately. G1/FC09 and release qualification **OPEN**.
- **Evidence:** GitHub run [40257036036](https://github.com/Mikeyphw/devtool/actions/runs/40257036036), source `64283e6ea3a404a1afed18fcd9d55ad6cda5c4de`, 39 failures in 6,758 tests. Local current-contract tests and classification documented in `docs/G1_CURRENT_CONTRACT_REMEDIATION_20261026.md`.
- **Defects and fixes:** Post-apply consumed-source LIFE cleanup was invoked for read-only diff and rebased temporary input without a qualifying apply receipt; protect both paths and preserve original ZIP. Multi-command workflow parent emitted duplicate pseudo-terminal `NodeFinished(status=running)`; replace intermediate event with typed `NodeProgressChanged` while accumulating reuse metadata.
- **Test-only obsolete material:** Remove superseded G8 post-G7 pinned amendment executable tests, rewrite historic source-SHA assertions, align registry/completion/guides with the active parser, and isolate fixture `GITHUB_SHA`. Do not resurrect retired behavior or weaken production source pins.
- **Hosted prerequisites:** Explicit Python 3.13 `setuptools`, `wheel`, and `coverage` preinstallation for offline wheel/coverage test prerequisites. Hosted result not confirmed until the workflow runs.
- **Destructive review:** 16 newly inventoried production modules / 30 primitive occurrences individually reviewed and expression-count pinned to disallow silent additions. A08 export's exception cleanup still deserves concurrency-focused inode-replacement testing/hardening; classification is *not* proof against an adversary swapping the output name after exclusive create. RM19 stays CANCELLED.
- **Next:** clean source-bound overlay acceptance and 24-shard G1 rerun. Preserve all actual failures and evidence; FC09 remains blocked until current tests are green.

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

## RM-03 — Canonical EXO cross-boundary lineage implementation (2026-10-21)

- **Scope:** DT-006/DT-023 Devtool core. No Cinderbox A11 configuration work.
- **Implemented:** source/config/attempt-bound workflow node identity, conservative EXO event lifecycle correction for multi-part jobs, three local typed evidence-verifier target jobs, read-only `evidence lineage` query and strict source/run/attempt/artifact digest correlation.
- **Safety:** no synthetic remote trust, no GitHub dispatch or retry, no inferred authority from green CI/checks or local files. Absent or inconsistent event journals remain unknown/incomplete; invalid bindings block.
- **Evidence:** `tests/test_rm03_exo_lineage.py` and existing EXO/workflow tests; comprehensive checklist in `docs/RM03_PROMISE_AUDIT.md`.
- **Limit:** Authenticated remote provider verification and full producer→consumer artifact graph remain separate RM-05/RM-06 work. Native workflow retry is RM-04.
- **Status:** implemented and locally test-verified; actual Termux apply to be confirmed by operator.


## RM-04 native node retry/recovery (2026-10-21)

Implementation: durable source/graph/host/input/output fingerprints; canonical CLI retry plan and explicit safe replay; EXO terminal reconciliation; per-node checkpoints and output-verified resume. `docs/RM04_PROMISE_AUDIT.md` retains the 23-item promise checklist and tests. RM-05 format-aware artifacts deliberately remains separate.

## RM-05 — Unified typed artifact integrity and deterministic test evidence

- **Status:** Implemented in RM-05 source-bound overlay; native Termux/device qualification not claimed.
- **Work:** New version-2 producer→artifact→consumer receipt reader, SHA-256/format-specific archive checks, optional caller-pinned Ed25519 attestation verification, SBOM integrity, deterministic JUnit/TRX/Go/Cargo shard aggregation, hash-bound optional EXO journal correlation, read-only CLI entries.
- **Safety:** No receipt/registry field promotes GitHub, signing, device or release authority. Claimed Android package identities without a trusted compiled-manifest parser fail closed. Declared consumers are not executed; RM-06 will own hosted handoff admission.
- **Validation:** Adversarial fixtures for traversal/duplicates/zip bombs, tampering, signatures, reports, wrong identities and EXO inconsistency; canonical CLI fixed point; mandatory target validation and rollback.
- **Promise ledger:** `docs/RM05_PROMISE_AUDIT.md` and `docs/RM05_ARTIFACT_TEST_CONVERGENCE.md`.

## RM-06 — Hosted multi-job DAG exporter and remaining capability boundaries (2026-10-22)

- **Root cause/evidence:** The existing Go/Python hosted provider emitted a single Ubuntu `native-dag` job, losing independent job-level scheduling and native per-node dependency edges. It lacked per-node SHA-digested artifact handoffs. A source-bound RM-06 compiler now projects target-owned native job DAG nodes to uniquely named GitHub jobs with exact `needs` and verifies graph/source, producer identity and transferred bytes before downstream execution. The existing typed artifact and EXO trust boundary remains intact.
- **Implemented:** `devtool/actions_graph_export.py`, `actions_language_providers.py`, `actions_cli.py` and canonical CLI surfaces; behavioral tests `tests/test_rm06_graph_export.py`. Source-locked review/write/verify; full-SHA GitHub actions; target-local native run specs; strict file output and transfer constraints; no credential persistence or permission elevation.
- **Remaining improvement (OPEN, RM-07/RM-20):** Multi-cell OS/arch/toolchain-version matrices, semantic matrix fanout/host incompatibility policies, verified dependency caching and source-ownership-proof affected-target selection. Current RM-06 fallback explicitly runs full DAG on the pinned Ubuntu profile without caching and refuses unsupported graph/host cases. Treat this as partially delivered RM-06, not finished roadmap scope.
- **Trust limitations:** Content hash + a `receipt.json` cannot independently authenticate an upstream GitHub producer. Device, signing, external consumer, release, hosted run and platform qualification require external evidence and are not asserted by local tests. No remote side effects from CLI review/export.
- **Status:** Locally implemented; direct-job mock and real one-test Python execution passed, zero-test refused; adversarial and disposable source-bound transaction passed. User Termux installation and independent hosted acceptance pending.

## RM-07 — Reviewed host matrix, toolchain capability doctor and compiler cache (2026-10-22)

- **DT-033 / DT-055 (PARTIAL, source implementation):** Existing NEXT-03/ RM-06 exporter assumed a single Ubuntu host; no explicit separate local-versus-hosted profile registry, no per-OS DAG admission, no pinned opt-in dependency build cache and no unified installed-version doctor. RM-07 adds `toolchain_profiles.py` with immutable Ubuntu/macOS/Windows/Termux declarations, local probe statuses `installed/missing/unknown`, pinned setup action metadata, explicit requested toolchain versions, and bounded hosted profile fanout.
- **Safety evidence:** `tests/test_rm07_toolchain_profiles.py` covers duplicate/injected/unsupported profile refusal, isolated node dependencies across Linux/macOS, missing/symlinked/untracked Go lock refusal, dynamic SHA cache key, mismatched hosted runner environment, doctor state and CLI generation. Hosted Windows and Termux profiles *deliberately refuse export*. Go cache is opt-in, fixed to public compiler cache paths, and does not save modules or credential folders; Python cache is disabled.
- **RM-06 follow-up (PARTIAL):** OS matrix and opt-in Go cache pieces are now source-implemented; ownership-backed changed-target selection remains **OPEN**, with full-DAG fallback mandatory. No evidence supports labeling RM-06 or RM-07 fully closed across all platforms/languages. Additional toolchain profiles and authenticated hosted acceptance are not yet achieved.
- **Files / next:** `docs/RM07_TOOLCHAIN_MATRIX_DOCTOR.md`; extend reviewed Windows bridge/toolchain/version support, add safe affected-target plan backed by source ownership, revisit Python pinned dependency caches in RM-08. Keep external host/device/signing/release gates separate.

## RM-08 — Go/Python hosted-provider behavioral implementation (2026-10-22)

- **Merge window (RM-08/RM-09):** Inspected both roadmap promises and `actions_language_providers.py`, native graph compiler, local dotnet runners, typed distributions and profile registry. **Do not merge**: RM-09 requires .NET-specific solution/RID/Windows restore semantics independent from RM-08. RM-09 remains open; defer no .NET promise by claiming it here.
- **DT-043 / Go (PARTIAL):** Existing provider falsely required a root `go.mod` and pinned hosted `setup-go` to it. Added `actions_rm08_projects.go_workspace_manifest` for Git-tracked nested module/`go.work` discovery, exact `go -C` selection, source-digest inclusion, explicit refusing external replaces, symlinks and mismatched versions. Both exporters pin the selected `go.mod`; hosted Go disables implicit toolchain downloads; `test -race` requires Linux amd64 with CGO enabled. Nested real Go binary contract now verifies selected module, actual format/arch/buildinfo and source drift. No broad toolchain/target/version fanout, offline cross compiler provenance, or repeat-build reproducibility attestation.
- **DT-044 / Python (PARTIAL):** Existing Python 3.12-only host profile now permits source-declared bounded 3.11–3.13 per job, with static workflow setup. Opt-in reviewed backend/lock approval requires one tracked lock and hash-pinned pip plus backend closure; uv/Poetry/PDM are discoverable but execution refuses. Python package build nodes install hashed requirements; opted-in one-wheel/one-entrypoint offline virtualenv smoke runs before artifact staging. A bounded coverage.py XML merge helper detects overlap and DTD, but auto-collection/multi-version matrix/sdist install smoke are still open.
- **DT-031/DT-033 / inherited architecture (PARTIAL):** Native DAG and source/run/attempt graph/handoff semantics reused. RM-06 ownership-backed affected-target pruning and RM-07 hosted Windows/Python cache remain outstanding; full DAG and cache-disabled are the supported safe fallbacks. No GitHub/consumer/device/signing/release authority inferred from local source tests.
- **Behavioral falsification:** `tests/test_rm08_projects.py` validates the generated selected module pin, workspace escape/replace refusal, race host incompatibility, real Go executable receipt and source drift, unknown Python backend/mixed locks/pins, real isolated offline wheel install and tampered package refusal, opt-in native job ownership and report aggregation. Compare `docs/RM08_PROMISE_AUDIT.md` rather than equating file existence to completion.


### RM-08R1 — Termux host-target ambiguity in real Go distribution regression (2026-10-22)

- **Evidence:** Real Termux `apply-overlay` run `eac6d0e8ee0b47f6a2888e66d67d269b` reached `test_nested_real_go_build_distribution_receipt_and_wrong_source` and raised `DistributionError: Go ELF architecture/format differs from declared target`; 7 preceding tests passed. The test declared `linux/amd64` but invoked `go build` with only `CGO_ENABLED` and `GOTOOLCHAIN` overridden, leaking Android/ARM64 Go defaults into the fixture.
- **Affected scope:** `tests/test_rm08_projects.py` integration regression, RM-08 source-bound installation; **not** evidence that production ELF verification should be loosened.
- **Change:** Explicit Linux/amd64 no-CGO cross-compilation for the fixture, with real-binary negative checks for false ARM64 and CGO claims. Keep strict verifier and deterministic contract unchanged. Source-bound RM-08R1 replacement package is based on original clean post-RM-07 tree, not a stacked repair of a failed transaction.
- **Status:** **FIXED IN RM-08R1 PACKAGE; DEVICE REVALIDATION PENDING**. Verify auto-rollback/clean source state before rerun. The original 93-pass Linux regression cannot be treated as Android coverage; a device pass is needed to close this environment regression.
- **Follow-up:** Add host-target poisoning to continuous tests, including Android/ARM64 or a simulated ambient GOOS/GOARCH shell. Consider a separate optimization to reduce the expensive isolated-wheel test, without relaxing wheel verification or writing shared mutable caches.


## RM-08R2 consolidated source-bound Go/Python completion follow-up (2026-10-22)

- **Root cause:** RM-08/R1 passed limited tests but did not satisfy the advertised full real-project matrix/workspace/lock/smoke scope. Original RM-08 Termux acceptance also exposed ambient GOOS/GOARCH leakage in the regression fixture (`eac6d0e8ee0b47f6a2888e66d67d269b`).
- **Delivered in replacement (not stacked):** Reviewed source-local Go module dependency graph with cycle and escape refusal, explicit native DAG dependency-edge check, per-provider bounded multi-version hosted fanout with independent dependency/artifact identities (Go versions >= selected module minimum, Python 3.11–3.13), per-cell pinned setup actions and fanout caps; opt-in deterministic Linux/CGO-disabled executable repeat-build verification; reviewed uv/Poetry/PDM lock identity with matched hash-pinned pip installation export; native coverage-run/report admission and coverage XML typed readout; optional offline sdist install-and-entrypoint smoke from SHA-locked local wheelhouse; flexible bounded wheel-entrypoint args. RM-08R1 GOOS=linux GOARCH=amd64 CGO_ENABLED=0 test repair retained unchanged, verifier strict.
- **Behavioral evidence:** `tests/test_rm08_completion.py` exercises real compiler output isolation, matrix refusal, Go dependency and cycle safety, manager lock/export source drift, coverage target output and unsafe command refusal, *real* deterministic Go candidate reconstruction including tampering, and real isolated PEP517 sdist offline install with changed-wheel hash rejection. Original `tests/test_rm08_projects.py` and RM-05/06/07 suites remain mandatory.
- **Remaining engineering limitations:** Native uv/Poetry/PDM frontends are not bootstrapped/executed; instead a *reviewed, exact-hash* pip export is required. Go workspace graph is enforced against declared native DAG edges, not dynamically rewritten into another scheduler; unsupported remote/unpinned replacements refuse. Coverage instrumentation must be explicitly declared in native job argv, not silently injected into arbitrary Python runs. Opt-in sdist requires tracked hash-pinned dependencies and an already-provisioned offline wheelhouse. Python dependency cache remains disabled, Windows hosted unsupported, RM-06 affected-target pruning outstanding. No GHA/Android/signing/remote acceptance inferred from unit tests.
- **Status:** **IMPLEMENTED/TESTED BOUNDED SUBSET**; see `docs/RM08_COMPLETION_PROMISES.md` for detailed obligation checklist. Device acceptance pending; do not mark DT-043/DT-044 fully closed until unsupported breadth and authoritative hosted behavior are resolved. Never treat this row as qualification.


### RM-08R3 — Legacy NEXT-04 distribution fixture host-target isolation (2026-10-22)

- **Evidence:** Real Termux RM-08R2 transaction `30d1780ea95e49c6bad13230d7be4cff` passed 65 tests then failed in `tests/test_next04_distributions.py::test_real_go_build_and_verification_with_source_pin` with `Go ELF architecture/format differs from declared target`. Contract requires `linux/amd64`; helper `build_go()` set only `CGO_ENABLED=0`, implicitly inheriting Termux `android/arm64`. The R1/R2 repair fixed `tests/test_rm08_projects.py` but failed to cover this older mandatory regression fixture.
- **Change:** Fix the remaining build helper to pin `GOOS=linux`, `GOARCH=amd64`, `CGO_ENABLED=0`, `GOTOOLCHAIN=local`. The real receipt test injects `GOOS=android` and `GOARCH=arm64` itself as a regression check, even on Linux x86_64. The verifier still rejects wrong binary format, OS, architecture, and CGO declarations; no production security policy changed.
- **Scope/rollback:** Source-bound *replacement* overlay for clean post-RM-07, not stacked on failed R2. Keep the all-promise RM-08 mandatory validator (including NEXT-04); exact outer and inner hashes; full rollback on errors.
- **Status:** Fixture correction implemented; independent Termux transaction acceptance **PENDING**. The separate RM-08 native uv/Poetry/PDM and auto-coverage promises remain open, and hosted/release qualification is not asserted.
- **Process gap:** Prior local tests did not poison ambient GOOS/GOARCH across all mandatory real-build tests. Continue scanning all required test selections for implicit host-target assumptions whenever cross-target binaries are compared.


### RM-08R4 — Native-owner automatic pytest coverage (2026-10-22)

- **Evidence:** RM-08R3 verified `python -m coverage xml`, but only after an operator separately configured coverage instrumentation and report jobs. A normal native pytest job lacked automatic collection and could be incorrectly described as complete coverage.
- **Change:** Opt-in `ci_python_auto_coverage=true` on a reviewed, hash-locked Python `-m pytest` target-local job with exclusive `outputs = ["dist/coverage.xml"]`. Multi-job compiler binds the option in the frozen DAG identity; hosted `run-node` executes the original native job's specs through coverage.py, then invokes the native report command. Coverage data resides outside the source tree, XML bytes are checked by the existing strict verifier and staged through the original node's SHA-256 artifact handoff. Single-job exporter refuses the unsupported option. Failed pytest jobs never generate accepted coverage.
- **Behavioral tests:** Actual native job via `run_node` passes an instrumented pytest fixture, stages checked coverage XML, refuses preexisting output and missing/forged coverage lock, rejects non-pytest and single-job declarations. Full regression selection is required in the overlay validator; no hosted remote/release authority is implied.
- **Residual:** Native uv/Poetry/PDM *front-end execution* remains optional extension/open because only reviewed lock-policy plus hash-bound pip-export execution is established. Do not conflate a recognized lock with an executed manager command. RM-06 affected pruning and RM-07 Windows/Python caching remain separately open.
- **Status:** Locally delivered pending source-bound Termux acceptance; repeat after actual installation.

## RM-09 — .NET solution graph and native hosted provider (2026-10-22)

- **Merge decision:** RM-09/.NET and RM-10/Rust remain separate source-bound installs; shared infrastructure is already delivered by RM-05–RM-07, while domain semantics differ. Do not create redundant job schedulers or auth authorities.
- **DT-036 / DT-045 (PARTIAL):** Added immutable `dotnet` provider descriptor/registry, static tracked `.sln`/`.slnx`/`.csproj` graph parsing and DAG dependency checks, pinned SDK setup for Ubuntu hosted workflow and exact native direct-job execution. Git-tracked `global.json` / per-project NuGet lockfiles bound to planning. Windows-only SDK/API profiles, dynamic condition/evaluation and authenticated custom feeds fail closed.
- **Cross-job trap:** Native DAG `needs` creates order, *not shared NuGet/obj filesystem*. RM-09 executes locked restore again within the consumer native job before reviewed `--no-restore` build/test/pack/publish; it does not pretend to transfer restored package directories or inferred dependency provenance.
- **Artifact and TRX behaviors:** Strict TRX failure/duplicate/mismatched-count refusal; real NuGet ZIP/nuspec/TMF/RID/source-limited verification before native output handoff. Neither SHA nor local package format proves signing/release authority. The runtime verifier handles `.nupkg` and TRX through existing target-owned nodes.
- **Known gaps (OPEN):** Windows hosted bridge, .NET coverage instrumentation, verified publish/portable RID receipts, rich TFM/configuration matrix and real multi-solution hosted SDK execution. See `docs/RM09_PROMISE_AUDIT.md` for precise status and test evidence.
- **Follow-up:** RM-10 Rust separate; RM-09 completion debt remains cumulative, not silently closed. No external GitHub/device/release gate is counted toward engineering completion.

## RM10-RUST-001 — Native Rust/Cargo hosted provider and bounded source graph

- **Status:** Implemented bounded source contract in RM-10 overlay, awaiting Termux and real hosted Rust compiler acceptance; outstanding features remain OPEN in `docs/RM10_PROMISE_AUDIT.md`.
- **Evidence:** Real tracked Rust workspace fixture, local `path` dependency cycle/traversal checks, lock/member matching, pinned toolchain+component/target profile, reviewed direct `cargo` command templates, native DAG projection, architecture and archive rejection fixtures, source/run/attempt-bound byte staging.
- **Security boundaries:** No arbitrary Cargo subcommands, shell, network install or unreviewed `.cargo/config*` and build.rs; `cargo --offline --locked` for dependency-bearing tasks; runner proves preinstalled exact pin or refuses. Crate archive structural verification and ELF header checks do **not** authenticate signing or release readiness.
- **Gap:** Real Cargo CI runtime absent in local host, unsupported full feature-set matrices and cross compilers, no automatic libtest JSON extraction/normalization, third-party build script provenance unverified.
- **Recommendation:** Extend from source-reviewed job/byte contracts only after provisioning a genuine Rust environment; add integration tests for full cargo fmt/clippy/test/build/package, all output digests, strict feature graph and executor-attested compiler JSON. Keep RM-11 Node provider a separate merge-window installation boundary.

### RM-10R1 source-bound Cargo provider closure review (2026-10-22)
- **Root cause:** First RM-10 overlay reported *Partial* for feature matrices, AArch64 target/verifier, Cargo JSON execution integration, inherited workspace/patch graphs, and compiler/source/attempt metadata despite satisfying only isolated nominal fixtures.
- **Affected:** `actions_rm10_cargo.py`, `actions_graph_export.py`, Devtool native owned Rust jobs, Rust consumer workflows.
- **Remedy:** Replace original RM-10 (post-RM-09 source lock) with feature × toolchain matrix job isolation, safe inherited dependency/patch graph, provisioned-only Linux AArch64 cross linker, strict ELF verification, optional native Cargo JSON/libtest output, run/attempt/argv/flag identity, additional negative tests. Harden ambient Rust flags. No floating toolchain installation or broader host privilege grant.
- **Status:** **Implementation delivered for reviewed bounded policies; real Rust/hosted execution remains UNQUALIFIED**, documented in `docs/RM10_PROMISE_AUDIT.md`. Original RM-10 ZIP superseded; do not apply both. No evidence of actual external Rust acceptance yet. 

### RM-11 — Node/JS/TS hosted provider closure (2026-10-22)
- **Root cause (DT-047):** Devtool had no trusted, locked npm/pnpm/Yarn/bun workspace adapter, direct native-JS job review, manager-version setup, cache key, JS result normalization or web/tarball artifact verifier. Node work previously could only enter through unreviewed shell/command paths.
- **Implementation:** `actions_rm11_node.py` validates Git-tracked workspace graph and affected dependents, exact manager and lock identities, reviewed script templates, literal workspace selectors, controlled Node 20/22/24 host/version matrix, opt-in manager content-store caching and frozen lifecycle-disabled install. `actions_graph_export.py` reuses native owned DAG execution, validates unchanged tracked source prior to install, pins setup-node/Corepack/setup-bun, and verifies JUnit/Jest results and manifest/packed archive bytes before SHA-bound handoff.
- **Behavioral evidence:** `tests/test_rm11_node.py` uses four manager fixtures, a real Node.js JUnit test and npm offline native install/package, negative script injection/hook/source/lock/cycle/symlink/output tests, native handoff, six-cell version/host isolation and cache key invalidation. RM-06–RM-10 compatibility tests retained. See `docs/RM11_PROMISE_AUDIT.md` for the exact promise checklist.
- **Status:** RM-11 bounded engineering acceptance implemented. Independent hosted npm/pnpm/Yarn/bun execution, source-side third-party config authority, authentic producer provenance, and release-signing qualification remain **unqualified external evidence**. RM-12 JVM is a separate merge-window package. Outstanding RM-06/07/08/09 earlier-scope gaps are not closed by RM-11 and remain in the cumulative ledger.


### RM-11R1 — Host-version leak in mocked Node runner test (2026-10-22)

- **Evidence:** On Android/Termux, `tests/test_rm11_node.py::test_nodes_compile_versions_report_artifact_handoff` failed with `RM-11 installed manager version differs from reviewed pin` when the host's npm version differed from the mock fixture's `npm@10.9.2`.
- **Cause:** The fixture mocked `runners.run_specs` but allowed `actions_graph_export.run_node` to execute an actual host `npm --version` probe, introducing a hidden host dependency.
- **Correction:** Mocked acceptance uses a temporary pinned-version CLI shim; real npm acceptance derives and commits its temporary project's pin from the installed npm. A new negative test proves manager-version drift remains a hard refusal before installation. Production manager admission is unchanged.
- **Status:** Engineering repair implemented and under local acceptance; real Termux transaction remains a separate gate.


### RM-11R2 — Host-dependent offline npm lock generation in mandatory test (2026-10-22)

- **Evidence:** Termux RM11R1 stopped after 9 passing tests at
  `test_real_native_npm_install_node_execution_report`, where host npm
  `install --package-lock-only --offline` failed with exit code 1 and a debug
  log path (the actual npm error cause was not in the supplied transcript).
- **Scope:** test fixture setup only; not a production npm provider failure.
- **Correction:** deterministic source-derived npm v3 workspace lockfile,
  validated exact local links/dependency metadata, isolated empty cache/npmrc;
  preserve actual frozen `npm ci --offline`, Node execution and attested JUnit.
- **Status:** R2 code prepared; local and Termux device acceptance recorded
  separately. Never claim R1 was accepted on device.


### RM-11R3 — Native npm smoke installation still failed on Termux (2026-10-22)

- **Evidence:** RM11R2 Termux validator passed 9 tests, then the real native
  test `test_real_native_npm_install_node_execution_report` failed because
  `npm ci` exited nonzero. The captured output supplied only an npm debug-log
  path; its specific cause is unknown. Earlier lockfile generation was already
  removed, so R2 still had a device-dependent acceptance fixture.
- **Correction:** Require a real, fully offline `npm ci` with an exact local
  manager pin on a self-contained zero-dependency package fixture. Remove
  unneeded workspace symlink/linker requirements from this one smoke; retain
  independent mandatory workspace dependency, source and lockfile graph tests.
  Route the native runner store to external `RUNNER_TEMP`, isolate user/global
  npmrc, and verify real Node/JUnit artifact transfer. Add explicit refused
  failed-install test before native command/handoff.
- **Production policy:** unchanged; frozen install and exact installed-manager
  pin remain strict, with no silent fallback, no network expansion and no
  acceptance of a failed install as a passed job.
- **Status:** local validation and device acceptance recorded separately.

### RM-12 — JVM/Java/Kotlin Gradle and Maven standalone hosted provider (2026-10-22)

- **Root issue DT-048:** No source-owned Maven reactor/Gradle multi-project read-only parser, JVM host toolchain/version matrix, Java JUnit normalization, signed/unsigned JAR verifier, desktop distribution bytes or independent Android plugin refusal within the existing hosted provider DAG.
- **Architecture:** Reviewed immutable `jvm` provider in `contracts_v2` and `actions_language_providers`; only literal direct `mvn -B -o -ntp` or `gradle --offline --no-daemon` commands, with tracked module/config catalog provenance. Reuses RM-06 DAG node ownership and SHA/run-attempt-bound byte handoff. Node-specific output normalization executed *after* successful native job; no alternative scheduler.
- **Host and security:** Exact `javac` major and build-tool version checked before running native jobs; review pins JDK 17/21/25, Maven 3.9.x, Gradle 8/9; setup-java and Gradle actions immutable Git commits, two reviewed POSIX runners and opt-in content-only, version/source-bound dependency caches. Disallow arbitrary settings evaluation, Android-only build plugins, JDK auto-provision, unsafe XML/ZIP or classpath and uncontrolled build outputs. Neither JAR ZIP validity nor jarsigner structure promotes signer trust.
- **Behavioral evidence:** Dedicated `test_rm12_jvm.py` with real javac/JAR, Kotlin JVM Gradle plugin, Gradle/Maven reactor positive/negative, version/OS matrix, source drift, JUnit failure/duplicate, native SHA handoff, desktop distribution and wrong-tool refuse-before-run. See `docs/RM12_PROMISE_AUDIT.md` for each promise and qualification boundaries.
- **Merge window:** RM-12 and RM-13 remain separate: JVM vs native ABI/compiler semantics. RM-13 next. RM-06/07/08/09 earlier promise gaps remain independently open; do not relabel them closed.
- **Status:** Engineering complete within explicitly supported JVM contracts; exact source-bound ZIP positive/negative transaction passed locally, independent Termux/GitHub execution evidence pending.

## RM-15 / BKX D1–D5 consolidated Android integration (2026-10-22)

- **Source context:** clean post-RM-12 tree `4c5ad4697dcbb6e7ad43c41089bd3fd6e391b404`; RM-13/R1 installation failed on real Termux and is deliberately **not** marked complete or made a prerequisite.
- **Evidence of original limitations:** `actions_unsigned.py` initially owned only `Mikeyphw/devtool` / `io.github.mikeceead.devtoolhud` and used 4 KiB `zipalign`; Android planning accepted only `debug`/`release`. Existing LIFE credential management and ACTIONS-06 DAG/GitHub infrastructure were already present and must not be replaced.
- **Implemented remediation:** registered external consumer/variant policy with immutable source/attempt/artifact/contract hashes, two independent import/sign lanes, existing LIFE identity and cert binding, generic pre/post-sign required/forbidden APK member rules, 16 KiB ZIP/ELF checks, bounded Gradle/Kotlin/Groovy model discovery, signed custom-variant output receipts and safe JVM options repair.
- **Affected projects:** Devtool HUD (regression preserved), BKX (consumer registration required with true metadata), Skybind (independent validation owned by BKX, not Devtool), Studyflow/Relatório/MMRL (source-model acceptance test fixtures only until real repos are run).
- **Validation policy:** mandatory RM15 behavior and HUD compatibility tests, source-bound fail-closed validation, wrong/dirty baseline refusal, exact rollback after forced validator failure and no signing secrets inside GitHub. See `docs/RM15_ANDROID_BKX_PROMISE_AUDIT.md`.
- **Disposition:** bounded engineering implementation; BKX real build/sign/device evidence PENDING EXTERNAL. RM13 real-device failure remains OPEN. Earlier RM06–RM09 gaps remain OPEN and are not overwritten by this milestone.

## RM-14 Shell/Termux hosted CI — 2026-10-22

- **Disposition:** Implemented bounded engineering contract in RM-14 source-bound delivery; external Termux/GitHub toolchain validation not yet authoritative.
- **Source:** `devtool/actions_rm14_shell.py`, `actions_language_providers.py`, `actions_graph_export.py`, `toolchain_profiles.py`, `contracts_v2.py`; behavioral suite `tests/test_rm14_shell.py`, promise report `docs/RM14_SHELL_TERMUX_PROMISE_AUDIT.md`.
- **Change:** Immutable `shell-ci` provider for Bash 5 / POSIX-sh / Zsh 5, exact syntax/ShellCheck/shfmt/Bats argv, tracked UTF-8 source-hash review, fail-closed Tasker/Termux-native selection and no hosted parity claims, JUnit integrity and fully qualified failure attribution to native DAG nodes. Zsh ShellCheck and Ubuntu 24.04 shfmt 3.8 Zsh formatting are intentionally excluded because their packaged versions lack the dialect; Zsh syntax uses `zsh -n`. Generated hosted jobs install reviewed Bats 1.10.0-1, shfmt 3.8.0-1 or Zsh 5.9-6ubuntu2 through signed, version-pinned Ubuntu noble apt packages as necessary. Arbitrary runtime installer scripts remain forbidden.
- **Evidence:** Tests exercise native Bash and POSIX syntax execution, hostile quoted argv, symlink/traversal/source drift, Linux/Android host confusion, fake native ShellCheck and Bats CLI exit/report paths, zero/failing/tampered JUnit, exact transfer ownership, and previous provider/exporter compatibility. A fake Bats shim is *not* a live Bats tool qualification.
- **Open external gates:** Native Termux execution with installed ShellCheck, shfmt, Zsh and Bats; independent authenticated GitHub hosted runs. Release authority not implied by local job success.
- **Preserved prior findings:** RM-13 failed-on-Termux remains unresolved. RM-06–RM-09 engineering debt and RM-15 consumer/device signing evidence still require their own closure; this change does not claim to close them.

### RM15-HF01 — Android release candidates must never depend on GitHub signing secrets (2026-10-23)

- **Evidence:** RM15 `.github/workflows/devtool-android-variants.yml` required GitHub keystore secrets and a protected ref for every `*Release`, conflicting with registered ACTIONS-10 unsigned download and local LIFE signing. ACTIONS-05 also refused unknown static variants without consulting canonical Android variant profiles; CLI choices were limited to `debug`/`release`.
- **Affected:** Devtool ACTIONS-05/RM15, `github_actions.py` dispatch, checked-in Android Actions workflow, canonical CLI schema/surfaces; BKX Embedded/Lite hosted Android candidates and other registered Android consumers.
- **Remediation:** default `--signing-mode local`, optional explicit protected `github` signing, unqualified unsigned artifact receipt with source/attempt/hash, tracked reviewed profile override through canonical Android resolver, independent retained ACTIONS-10 LIFE signer/consumer manifest boundary.
- **Status:** implemented and behaviorally tested locally; real authenticated BKX workflow, locally authorized signing identity and Skybind device evidence remain separate qualification.
- **Caution:** generic variant artifact is NOT an ACTIONS-10 import manifest; BKX must use its dedicated registered unsigned producer workflow and exact two-member manifest. Do not weaken package/signature checks or quietly turn GitHub secrets back into mandatory dependencies.

### RM19 disposition — cancelled by user (2026-10-23)

- **Decision:** Do not implement repository splitting, code extraction, dependency relocation or extraction-oriented cleanup in RM19. RM17/RM18/RM20 remain focused on existing repositories and in-place adoption; do not reintroduce extraction as a hidden dependency. Status: **CANCELLED BY USER**.

- **Unsigned producer guard:** custom-variant local release explicitly passes `-Pdevtool.unsignedReleaseCandidate=true`; the registered consumer must honor that property and emit a valid unsigned package. The workflow refuses unexpectedly signed APK bytes instead of weakening the local LIFE import contract.

### RM15-HF02 — custom Gradle dimensions/types, trusted convention execution and unified release commands (2026-10-23)

- **Original deficiency:** RM15-HF01 still inferred only `*Release`/`*Debug` in the generic GitHub workflow, used one static flavor dimension, and had separate ACTIONS-05 and ACTIONS-10 user-facing entry points. Arbitrary convention plugin evaluation was unavailable. The original source used static DSL inspection by design.
- **Implementation:** bounded static multidimensional product and custom build-type matrix, explicit source-pinned `gradlew` execution for trusted convention plugin evaluation (`--discovery-mode gradle`), exact custom build-type dispatch/artifact manifest, custom non-debug registered consumer support and `release-plan/run/import/sign` facade delegating to ACTIONS-05 or unchanged ACTIONS-10 trust kernel as appropriate.
- **Important limitation:** arbitrary Gradle execution is intentionally **not a sandbox**. It requires explicit user authorization and executes plugin code with the caller's privilege. Never opt into it merely for convenience or on untrusted source. Offline Gradle does not prevent arbitrary build logic from opening network sockets.
- **Preserved:** unsigned-first local LIFE route with no GitHub signing secrets, protected explicit hosted signing, original signer/certificate/16-KiB/integrity tests, Devtool HUD behavior, no unregistered import promotion, no release/device qualification inference.
- **Evidence and status:** `tests/test_rm15_hf02_release_unification.py`; `docs/RM15_HF02_ANDROID_MODEL_RELEASE_UNIFICATION_AUDIT.md`. Real convention-plugin/Android-hosted/BKX device acceptance remains external. RM-19 extraction cancelled; RM-13 device failure and RM-06–RM-09 engineering debt remain open.

### RM-16 and Android batch HF01 — 2026-10-23

- **Rebased baseline:** exact post-RM15-HF02 source tree `557f6a39b587b6ac5c2d0737959b4ad5f580406a`. Paused RM-16 Actions WIP was rechecked and reworked on that tree, not applied against pre-HF02 source.
- **RM-16:** complete reviewed typed Actions wrappers; opt-in source/workflow/ref/inputs-bound dispatch deduplication; read-only 40-column overview and capabilities inventory; ErrorInfoV2 with safe identity and remediation. Concurrency/uncertainty must never POST twice, and wrappers cannot smuggle shell argv or alternate remote repository.
- **Small Android batch add-on:** one registered unsigned producer workflow, independent Embedded/Lite artifact IDs, full preflight of every import before mutation, separate existing LIFE sign operations, same authorized signer/run/source, explicit `--yes`, production producer checkout supplied via `--source-root`, no GitHub signing secrets or release qualification promotion. Code: `actions_android_release.py`, `actions_unsigned.py`, `github_actions.py`, CLI schema. Tests: `test_rm16_batch.py`, `test_rm16_controls.py`. Details in `docs/RM16_BATCH_PROMISE_AUDIT.md`.
- **Partial-batch caveat:** imports and signatures have immutable per-candidate receipts, **not** cross-candidate atomic rollback. If the second operation fails, the first stays on disk and the user must reconcile its exact registry ID. This is intentional and not represented as a successful batch.
- **External evidence:** real BKX registered unsigned workflow producing two artifacts, authenticated GitHub correlation, existing LIFE certificate on Termux, and Skybind device qualification still pending. RM-13 Termux failure and RM-06–RM-09 earlier gaps remain open; RM-19 stays cancelled.

## RM16-DR01 — Real dispatch count propagation (2026-10-23)

- Evidence: `unsigned_dispatch` previously returned `dispatched=True` on `execute=True` even when `dispatch_workflow` returned `dispatched=False`, `resume_existing=True`. `release_batch_build` overreported `dispatch_count=1` for a reused request.
- Scope: ACTIONS-10/RM-16 Android batch dispatch receipt.
- Fix: use the underlying dispatcher's verified `dispatched` boolean and preserve explicit resume identity; reject malformed results. Keep the one-workflow dispatch and dispatch journal unchanged.
- Behavioral gate: `test_batch_resume_propagates_real_dispatch_count` exercises actual unsigned dispatch routing for resumed/new/plan results; native batch and ACTIONS-02 regression tests remain required.
- State: source implemented; local regression and Termux transaction acceptance separate.


## Consolidated RM06–RM09 + RM16-DR01 engineering closure (2026-10-23)

- RM06: Conservative source-ownership affected-target selector/compiled target set
  now uses `devtool.affected` target and dependent closure. Ambiguous, shared,
  unowned and empty change lists fall back to full configured workflow scope;
  never an optimistic empty hosted plan. Explicit target-local native DAG intact.
- RM07: Windows Actions bridge is opt-in only for reviewed .NET; runtime uses
  Git Bash and `Scripts/python.exe`. Other providers remain rejected. Python
  wheel-content and NuGet package-content cache paths are fixed under runner.temp
  with reviewed tracked source/lock hashes in keys. Read-only doctor unchanged.
- RM08: Native *uv* opt-in with exact executable version preflight, source/lock
  hash equality, frozen+offline sync, no interpreter/bootstrap download, and
  failure before native target execution/handoff. Pip-export default is retained.
  **OPEN:** Poetry/PDM native installers still require enforceable no-network
  policy; existing pip export is not their native execution.
- RM09: Reviewed Windows host eligibility, cross-host RID validation, explicit
  multi-TFM selection, native collector Cobertura promotion with node identity,
  exact TRX ownership, byte-hashed portable publish receipts, and collision-safe
  selected-solution/lock-bound NuGet caches. Hosted Windows proof remains external.
- RM16-DR01: Propagates actual dispatched flag and accurately reports resume.

Qualification boundaries: local regressions and source-bound transaction checks
are engineering evidence; remote Windows Actions, authenticated artifacts, real
.NET workloads and device/release qualification must be separately established.

## RM06–RM09 remaining source promises — final supplement (2026-10-23)

- RM06 source-owner affected selection was API-only despite the roadmap CLI promise. Added source-bound `actions provider-export --affected --affected-base/--affected-head` with conservative complete fallback, multi-target immutable graph compilation, plan/verify and all-destination preflight. Status: engineering implemented, hosted invocation not authenticated.
- RM08 native uv code was accidentally provisioned twice inside `run_node`, causing an apparently successful native Python job to fail afterward on pre-existing environment. Folded into one pre-native provisioning function. `test_uv_success_runs_one_native_job_and_one_sync` verifies 1 install and 1 native execution. Status: locally passed.
- RM08 Poetry/PDM native installation was absent. Added opt-in exact preinstalled binary pin + source/lock SHA review, Linux network-namespace-enforced sync, isolated venv and all-failure refusal; remote sources/plugins denied. No automatic bootstraps. Limit: an unprivileged host without a working network namespace refuses, rather than silently reaching PyPI or dropping to pip export. Status: local policy/negative tests passed, real capable-host sync evidence pending.
- RM07 and RM09 source-level Windows bridge/coverage/publish work from the previous consolidated overlay retained without promoting mock/local results to hosted proof. Status: live authenticated Windows GitHub runs and .NET collector evidence still pending independent authority.
- CLI mutation also regenerated canonical machine help/completions/reference/manpage from the checked-in canonical parser schema. Drift tests retained.

Complete matrix and truthful outstanding external gates: `docs/RM06_RM09_ENGINEERING_CLOSURE_20261023.md`.

## RM06–RM09 supplement Termux regression portability hotfix (2026-10-23)

- **Evidence:** User Termux `apply-overlay` run `d695ca6bea794abf8c6d261dfe27e701` failed in `test_poetry_pdm_native_network_denial_and_fail_closed[poetry]`: expected `offline provisioning failed`, received `RM-08 Poetry/PDM require supported Linux network namespace`. Preflight passed; the validator aborted at the second test.
- **Root cause:** The test assumed its Linux-specific positive capability gate held on any pytest host, so the Termux host refusal short-circuited the intentionally mocked `unshare` denial branch. Runtime rejection itself was correct.
- **Fix:** Introduce a narrow host-gate seam; explicitly force only that seam to `True` for the mocked Linux namespace command test and to `False` for Android/unsupported-host refusal checks. Never replace the real policy, skip tests, or weaken network isolation.
- **Affected:** Devtool RM08 Python native manager and RM06–RM09 overlay validator.
- **Status:** Source fixed and local validation pending; device must re-run the corrected full source-bound overlay. No claims of real Poetry/PDM hosted qualification.

## RM17/RM18 merged staged adoption — 2026-10-23

- **Problem:** existing `adopt-batch apply` committed independently during traversal, so later repo failures left partial work; composite ownership was limited to overlay, wrapper and legacy config; changed-source overlay upgrades and source-safe recovery guidance lacked a unified path. A hypothetical cross-repository "atomic Git commit" would be false.
- **Remedy:** `adopt-batch review-staged|upgrade-review|prepare|publish|resume-staged|recovery-plan|recover` with read-only preflight-all, reviewed extension-preserving migration, wrapper schema upgrade, strict generated-only ignore repair and offline built-in preflights. Detached Git tree/commit staging via private index + namespaced Git refs precedes guarded target-branch update-ref with expected-old lease; fsynced external journal reports PARTIAL_COMMIT and blocks compensating rollback if a HEAD advanced. The old sequential API is retained for compatibility, not advertised as globally atomic.
- **Evidence:** `tests/test_rm17_rm18_staged.py`, `tests/test_next05_adoption_batch.py`, `docs/RM17_RM18_STAGED_ADOPTION_PROMISE_AUDIT_20261023.md` and qualified outer overlay receipt.
- **Status:** implementation and local disposables tested; user Termux installation, real Telegram/Studyflow fixture acceptance and multi-repository production qualification remain external. Source-changed upgrade applies only after manual review/rebind; no unreviewed automatic rebase. RM19 remains cancelled and RM13 device acceptance unrelated.

## RM20 whole-repository onboarding — 2026-10-23

- **Scope/status:** Engineering implemented, local acceptance pending packaged Devtool transaction, external consumer/Termux qualification open. Existing repositories are **not** extracted/split (RM19 remains cancelled).
- **Prior gap:** RM06–RM09 and RM17/RM18 provided native affected-target selection and staged adoption only after authored configs/source-locked overlay manifests; no whole-repo in-place discovery, review digest, or operator inventory existed.
- **Changes:** `devtool/onboarding.py`, canonical `devtool onboard inventory|plan|apply`, generated CLI, native owned/unowned/shared path inventory, conservative manifest-root inference, exact source and reviewed digest guards, one-config Git commit/rollback, dependency/affected integration.
- **Safety/evidence:** `tests/test_rm20_onboarding.py`, `docs/RM20_WHOLE_REPO_ONBOARDING_PROMISE_AUDIT_20261023.md`. Original user request prohibits repository extraction; unknown or nested owners refuse false certainty. External real-consumer and on-device receipts remain unqualified. Maintain this entry until independent acceptance, not by deleting outstanding gates.

## RM-13/R1 post-RM20 integration and native Termux/hosted-compiler qualification (2026-10-23)

- **Evidence and affected components:** The historical RM13/R1 overlay (`DEVTOOL_RM13R1_CPP_TERMUX_PORTABILITY_SOURCE_BOUND_20261022.zip`) was refused on real ARM64 Termux during its original x86-64-only compiled-fixture assertion, and was intentionally never installed. Its source expected post-RM12 tree `4c5ad4697dcbb6e7ad43c41089bd3fd6e391b404`, while production had advanced through RM14–RM20. Direct extraction would regress the newer provider, Windows .NET, shell, uv/Poetry/PDM, and source/host policies. Files: `devtool/actions_graph_export.py`, `actions_language_providers.py`, `contracts_v2.py`, `toolchain_profiles.py`, tests and CLI-adjacent native DAG code.
- **Engineering action:** Genuine three-way merge from exact RM12 Git ancestor `f983ac0` to the applied post-RM20 tree `5fde0ecf3b310398d75f676c9b38fd16d7a523f5`. Restore the original RM13/R1 CMake/Meson, ELF/PE/Mach-O, GCC/Clang/MSVC and archive/symbol implementations **without** replacing later RM14–RM20 additions. Admit mixed Windows/Linux profiles **only** for compiler-matrix-only C++ DAGs; keep other mixed-profile export fail-closed. Maintain both Windows .NET and Windows MSVC admission. Keep native ELF test fixtures derived from `gcc/g++ -dumpmachine` and force wrong-ABI refusal. Repair generated hosted Meson version probe to inspect `meson`, not `cmake`.
- **Local evidence:** focused RM13/R1/provider/toolchain 52 passed; RM06/RM09/RM14 63 passed; broad Python/Rust/Node/JVM/RM17/RM18/RM20/generated CLI 110 reported passing (process shutdown took longer than bounded runner). Positive native Devtool transaction, source/dirty refusals and rollback recorded separately in package acceptance reports.
- **Status:** SOURCE ENGINEERING INTEGRATED, subject to native transaction acceptance and independent Android ARM64 real-toolchain replay. **External pending:** real user's Termux compiler run, independently authenticated hosted Windows MSVC execution, real Meson SDK/native builds where unavailable, device/release/signing authority. Do not promote those gates from Linux CI or synthetic ABI tests.


## RM13/R1 Termux CMake/CTest install failure (2026-10-23)

- **Evidence:** real Android Termux `apply-overlay` run `ecb3f32150854fb2a2888ae25cc6b570` passed source preflight but failed in `test_real_ctest_report_generated_by_cmake` at `cmake --preset ci`; `CalledProcessError` lacked captured stdout/stderr. Devtool did not qualify installation.
- **Root-cause scope:** host CMake compiler configuration not yet confirmed because the original test suppressed diagnostics. Termux native toolchain has a documented GCC/Android-GNU ABI detection incompatibility. Do not infer which precise error occurred without the real stderr.
- **Remediation:** for real Termux CTest fixture, pin installed native `clang++` in `cmake --preset ci`; preserve separate real GCC ABI/ELF checks; print bounded native command stdout/stderr on failure; add supported/unsupported-host regressions. Never skip real CTest when prerequisites are present or modify source-host acceptance gates.
- **Status:** source fix implemented; local positive/rollback acceptance to be recorded in the overlay; real ARM64 Termux retry still pending.

## RM13/R1 Termux Clang C++ driver symlink mismatch (2026-10-23)

- **Evidence:** native Android/Termux `apply-overlay` run `8f4f31589a8249e0ba6c8af50d9900cb` passed source preflight, then failed at `test_native_ctest_termux_clang_selection`: expected `/data/data/com.termux/files/usr/bin/clang++`, observed `/data/data/com.termux/files/usr/bin/clang-21`.
- **Root cause:** `Path(shutil.which('clang++')).resolve()` dereferenced the installed `clang++ -> clang-21` link. The test incorrectly expected the unresolved name, but the underlying path rewriting could also change compiler-driver semantics.
- **Fix:** use `os.path.abspath()` for the selected executable to keep the symlink invocation name; retain the existing real CMake/CTest test and add a synthetic `clang++ -> clang-21` regression independent of host files. Preserve refusal when native Clang is missing and the bounded diagnostics on failed real CTest.
- **Status:** replacement overlay locally validated; independent Android Termux acceptance remains pending. This failure is not evidence of a passing CMake/CTest run on the user's device.

## RM13/R1 Termux CMake 4.4 Android host API detection (2026-10-23)

- **Evidence:** real Termux run `8f0f08c9f2534b1580403cc6946224b5` (`tests/test_rm13_cpp.py::test_real_ctest_report_generated_by_cmake`): native CMake 4.4 detects Clang 21.1.8, then `CMakeDetermineSystem.cmake:40` fails to read `/usr/include/android/api-level.h`. It ran plain `cmake --preset ci` without native compiler override, showing the environment-only Termux check had also failed.
- **Root cause:** upstream CMake 4.4 reads `$PREFIX/include/android/api-level.h` if `CMAKE_SYSTEM_VERSION` is undefined, and `$PREFIX` was not available to this execution path. The RM13 fixture also recognized Termux only via the environment variables `PREFIX`/`TERMUX_VERSION`, not native Python interpreter path.
- **Repair:** detect native Termux via Python prefix/interpreter as well as environment; invoke native `clang++` without dereferencing its driver-name symlink; derive the compile API from Clang's unique numeric `__ANDROID_API__` predefinition and explicitly set `-DCMAKE_SYSTEM_VERSION=<compiler API>` for native CMake. No fake header, `/usr` write, hardcoded API level, cross compilation, or skipping real CTest.
- **Verification:** deterministic environmentless Termux simulation, malformed/missing API negative tests, native CMake flag integration and full mandatory CTest. Source qualification is separate from user-device acceptance.
- **Status:** pending actual Termux device acceptance; external MSVC remains separately gated.

## RM13/R1 Termux CMake host-SDK detection follow-up (2026-10-23)

- **Evidence:** Device run `a4c34152af6c460bbf7a5f3f229fb740` failed at `tests/test_rm13_cpp.py::test_real_ctest_report_generated_by_cmake` after 30 passing tests. The test demanded a unique compiler `__ANDROID_API__` macro, but native Clang 21 did not report one in the queried mode. Devtool correctly refused the transaction.
- **Cause:** The preceding source fix assumed that the native Clang driver always defines the selected Android API as a preprocessor macro. This is not guaranteed for host-native Termux Clang.
- **Correction:** Keep native `clang++` (symlink name preserved) and source `CMAKE_SYSTEM_VERSION` from the Android platform's `getprop ro.build.version.sdk`. This is the exact host property already queried by CMake 4.4 `CMakeDetermineSystem.cmake`; prefer `/system/bin/getprop` over PATH, validate one numeric API (1–200), refuse missing, malformed or failing property queries, preserve mandatory CMake/CTest. No fabricated SDK, hardcoded API, compiler macro requirement, filesystem write or skip.
- **Verification:** Added deterministic mocks of Android system property values and invalid paths, symlink preservation and real local native CMake/CTest; device acceptance remains pending a successful user run.
- **Status:** Local fix in progress; Android ARM64 device and independent hosted MSVC qualification remain external.

## RM13/R1 Termux simulated-Meson compiler admission portability (2026-10-24)

- **Evidence:** User-device Devtool `apply-overlay` run `bd78a485385d48e49aabc90e93b4cc50`: native CMake/CTest progressed successfully, then `tests/test_rm13_cpp.py::test_meson_test_job_admission_and_generated_junit_handoff` failed at `devtool/actions_graph_export.py:697` with `ProviderError: RM-13 preinstalled compiler unavailable`. The host lacks the simulated job's `gcc-14` executable; the test already mocked the Meson 1.9 version but mistakenly probed real GCC 14. Receipt: 38 passed, 2 skipped, 1 failed (no completed transaction).
- **Affected project:** Devtool RM13 C++ provider regression fixture, not the production compiler refusal path. The failing Meson test is intentionally a mocked report handoff, not proof of running a real Meson/GCC toolchain.
- **Fix:** Mock **only** exact `['gcc-14','--version']` and `['meson','--version']` probes in that simulated test, keep and assert exact `CC=gcc-14`, `CXX=g++-14`, `MESON_WRAP_MODE=nodownload` and `CCACHE_DISABLE=1`; require both probe calls. Add two independent negative regressions demonstrating production `run_node` refuses missing GCC 14 or mismatched GCC version **before** running any job or creating output.
- **Status:** Engineering fixture fix and mandatory source tests locally passing. Final native transaction/rollback evidence recorded in the replacement bundle. Android Termux successful application and real Meson/native GCC 14 and hosted MSVC execution remain **unqualified** pending receipts. No provider admission relaxation, host fallback or skipped test is introduced.


## FC-01 RM01 output ownership and crash recovery closure (2026-10-24)

- **Audited defects:** `cleanup_owned_temp` checked inode then called
  pathname `shutil.rmtree`; `hash_owned_output` validated pathname parents
  and opened the final file by pathname. Deterministic path-replacement tests
  reproduced foreign-data deletion and foreign-file hashing. Process SIGKILL
  left no durable ownership record, preventing safe recovery.
- **Affected:** `devtool/output_scopes.py`, `workflow_scheduler.py`, RM01 docs,
  `tests/test_fc01_safety_closure.py`; consumer workflows producing run-temp
  files (Devtool and configured consumers).
- **Remedy:** anchored dirfd no-follow lifecycle and hashing, fsynced exclusive
  lease journal, `flock` live owner guard, explicit orphan enumeration and
  conservative post-crash reclamation, cleanup result metadata and positive /
  negative adversarial tests. No foreign inode may be hashed as owned or
  deleted through a replacement path.
- **Boundary:** unregistered partial-provision roots and malformed/replaced
  records are preserved for operator review; not forcibly garbage-collected.
  The owned scope is not a sandbox for an untrusted job process. Linux local
  tests do not establish Termux device or independent consumer qualification.
- **Status:** implementation candidate; local source-bound transaction,
  rollback and actual Termux evidence tracked in FC-01 acceptance report.

## FC-01 Termux ancestor-descriptor permission regression (2026-10-24)

- **Evidence:** Real ARM64 Termux `apply-overlay` run `aea717e2f5c54eaeb2ac0f5446593c56` passed exact-tree preflight and overlay extraction, then failed before its first safety test: `PermissionError: [Errno 13] Permission denied: '/'` from `_open_dir()` opening `/` with `O_RDONLY|O_DIRECTORY|O_NOFOLLOW`.
- **Root cause:** On Android, application access may permit pathname traversal but deny directory *read* access to `/` and system ancestors. Descriptor anchoring had wrongly required read permissions for every ancestor. This is an execution portability bug, not grounds to revert to pathname-based recursive deletion.
- **Fix:** Walk ancestors using `O_PATH|O_DIRECTORY|O_NOFOLLOW` on supporting Linux/Android hosts, open only the final owned directory with `O_RDONLY|O_DIRECTORY|O_NOFOLLOW`. Retain no-follow protection, private-owner checks, inode verification, receipt/refusal semantics and fsynced journal. Other hosts retain safe read descriptors and fail closed if permission/capability is insufficient.
- **Tests:** `tests/test_fc01_safety_closure.py` includes simulated Android denial of `O_RDONLY` on `/`/ancestors, real descriptor-open functionality, symlink refusal during search-only traversal and hashing/recovery/cleanup with root read denied.
- **Status:** local regression evidence confirmed; real device retry of the source-bound replacement is still required. Preserve the failed device receipt in history.

## FC-01 Termux Python 3.14 hardlink test portability (2026-10-24)

- **Evidence:** real Android Termux `apply-overlay` run `882477d5e9e84fff926e2b640fef892b` passed source preflight and descriptor path tests, then failed in `tests/test_fc01_safety_closure.py::test_hash_rejects_root_replacement_and_hardlink`: `pathlib.UnsupportedOperation: PosixPath.hardlink_to() is unsupported on this system`. One test passed; no source transaction was committed.
- **Cause:** mandatory adversarial fixture assumed every host Python exposes hardlink creation; Android/Termux Python 3.14 can disallow it. The failure occurred before the hardlink rejection implementation was exercised and is **not** evidence that the verifier accepted a hardlinked file.
- **Correction:** split hardlink refusal from root replacement checks. Model only `fstat().st_nlink=2` on the actual opened regular-file descriptor, assert exact `unsupported/not_private_regular_file` response and prove the descriptor was inspected; keep root replacement as an independent mandatory physical symlink race test. No skip and no production policy relaxation, with explicit distinction between simulated hardlink metadata and independently untested physical hardlink creation on the ARM64 host.
- **Qualification boundary:** local simulation cannot establish on-device physical hardlink creation support. Native Termux completion remains pending; preserve the failed receipt.

## FC-02 — 2026-10-24 — RM17/RM18/RM20 guarded publication and recovery

**Status:** Source engineering candidate; require qualified source-bound
installation and real-consumer evidence before release closure.

**Evidence / root causes:** RM18's `_materialize` performed a pathname-based
check followed by Git `read-tree --reset -u`, which could overwrite a late edit.
A `PREPARING` journal could become stranded, while a locally writable prepared
ref and matching altered journal could change approved bytes. RM20 staged an
exclusive config via an unchecked `os.replace`, then made a regular Git commit
without a source HEAD compare-and-swap. All were reproduced in disposable Git
repositories during the 2026-10-24 audit.

**Resolution:** Shared `devtool/guarded_git.py` implements isolated-index
candidate construction, approved blob/tree verification, expected-ref CAS and
no-follow, escrow-preserving worktree reconciliation. RM18 independently derives
approved changes from source Git objects and pinned archives on publication;
`PREPARING` can resume or abort, and an interrupted escrow is handled
conservatively. RM20 now publishes a pinned candidate with exclusive
`.devtool.toml` creation and branch lease. Tests cover the original race and
journal tamper counterexamples and independent negative control.

**Affected projects:** Devtool producer, RM17 upgrade path, RM18 multi-repo
adopters, RM20 onboarding consumers (Telegram/Studyflow remain unqualified).
**Follow-up:** FC-03/FC-05 classification and proposal completeness; FC-08
real-consumer runs and FC-09 repository-wide seal. Do not silently erase
historical failures or treat selected local tests as hosted authority.


## FC-02 Termux late-edit escrow restoration portability — 2026-10-24

- **Device evidence:** `apply-overlay` run `053020117aec429d9253dd43ec180411` accepted the clean post-FC-01 tree and extracted the overlay; its native test `test_late_edit_after_check_moved_to_escrow_not_destroyed` failed with `FileNotFoundError` on the root `README` after escrow handling (1 passed, 1 failed). The transaction was not committed.
- **Defect:** a late modification moved to durable escrow was restored via `os.link()`. This assumes filesystem/runtime hardlink availability, already known to be restricted on Termux Python 3.14. An unavailable hardlink left the worktree path absent during a `PARTIAL_COMMIT` failure. The receipt itself does not include the low-level `os.link` failure; that causal explanation follows from the source path and regression reproduction.
- **Correction:** replace hardlink restoration with verified regular-file bytes read from the pinned escrow file descriptor and exclusive `O_CREAT|O_EXCL|O_NOFOLLOW` output through the pinned worktree parent descriptor. Retain the original escrow copy and preserve competing new files. Never use destructive `os.replace` or unlink a concurrent worktree entry. Preserve partial states for explicit recovery.
- **Tests:** force `os.link()` unavailable and verify byte-identical worktree restoration and persisted escrow; adversarially create a competing file between escrow rename and restoration and require both byte sequences survive. Include existing staged/adoption/onboarding regressions, exact source refusal and rollback tests.
- **Status:** source correction locally verified; actual ARM64 Termux installation and real-consumer publication remain separate qualification gates.

## FC-02 Termux missing `os.link` regression fixture — 2026-10-24

- **Device evidence:** native `apply-overlay` run `67f3c95290fc4effb918441054e756f0` passed exact clean post-FC-01 preflight and six-path manifest verification, then failed in `test_late_edit_after_check_moved_to_escrow_not_destroyed` while executing `monkeypatch.setattr(staged.gg.os, 'link', hardlink_forbidden)`: `AttributeError: module os has no attribute link`. One test passed; the source transaction did not commit.
- **Root cause:** the hardlink-free production fix was correct to remove the `os.link()` requirement, but its regression fixture still implicitly required the `os.link` attribute. Termux's Python 3.14 may omit that API entirely, rather than exposing a callable which fails.
- **Correction:** parametrize the adversarial late-edit/escrow preservation test for **both** an absent `os.link` attribute and a present-but-denied `os.link` callable. Use `monkeypatch.delattr(..., raising=False)` and `monkeypatch.setattr(..., raising=False)` respectively. All original assertions remain: partial-publication refusal, live worktree bytes restored, retained escrow original and single-link file metadata. No production worktree, Git CAS, journal, recovery, or safety policy was weakened.
- **Acceptance:** focused and full RM17/RM18/RM20/NEXT-05 source regressions locally pass, while native device retry is still required. Keep this failed receipt in the cumulative audit.

## FC-03 — RM11/RM12/RM20 conservative discovery and affected selection

- **Status:** Engineering implemented; native user Termux and consumer admission evidence pending.
- **Previous audit:** RM11 root-level JS/TS changes silently selected zero workspace packages. RM12 Android plugin version-catalog aliases were admitted as JVM. RM20 assumed every Gradle file was Android and omitted .NET, CMake, Meson, Maven, and shell-only repositories; mixed Go+.NET+CMake inventory could incorrectly claim READY.
- **Affected paths:** `devtool/actions_rm11_node.py`, `devtool/actions_rm12_jvm.py`, `devtool/onboarding.py`, `devtool/gradle_identity.py` and `tests/test_fc03_provider_selection.py`.
- **Implementation:** Unknown or root-owned changed paths now select the complete reviewed JS/TS workspace (never unjustified empty selection); literal version-catalog aliases resolve to static plugin IDs with tracked, symlink-refusing and size-bounded catalog reads; Android aliases route away from RM12, pure-JVM roots stay Gradle/JVM, unknown/dynamic convention/plugin references demand review. RM20 explicitly inventories CMake, Meson, .NET projects/solutions, Maven and shell-only candidates and does not offer a READY plan when it sees unrecognized or overlapping roots. No Gradle script, shell project hook or plugin is executed during discovery.
- **Boundary:** Complete job, wrapper, Actions and resource proposals remain FC-05; this only repairs conservative discovery and affected selection. Mixed projects cannot claim release qualification from static identification. Future conditional Gradle logic, unreviewed convention plugin resolution or unknown build markers still require manual review. No RM19 extraction is reintroduced.
- **Acceptance:** `tests/test_fc03_provider_selection.py` plus existing RM11/RM12/RM20 and FC-02 suites; source-bound native transaction checks. Do not call the user-device gate passed without a Termux receipt.


### FC03 Termux npm live-smoke gate / 2026-10-24
- **Evidence**: Devtool apply run `bcafe53cb7044141b364d4ae1a1fa749`; 28 tests passed before a real npm `ci` fixture failed with `RM-11 frozen dependency install failed`. The original test did not propagate the npm debug error, so the external cause is unknown. Same fixture passed with npm 10.9.2 under a Linux test host.
- **Affected**: RM11 Node native smoke, FC03 package validation on Android Termux.
- **Remedy**: On Termux, keep host-dependent live npm-ci qualification separate from deterministic source installation; explicitly print `PENDING_RM11_TERMUX_NATIVE_NPM_CI`; ship a network-free npm diagnostic probe. On normal hosts, run full mandatory native suite. No fallback from npm-ci to npm install and no weakened production admission.
- **Status**: Source installation gate remediation locally tested; RM11 Termux npm native CI **OPEN** awaiting a genuine device result and npm diagnostics.

## FC03-TERMUX-NPM-PACK-002 — Native npm pack and source-gate conflation

- **Status:** Source-regression separation implemented; actual Termux npm pack root cause and runtime qualification **OPEN**.
- **Evidence:** User device Devtool run `7e6fe3acae754861a82ad30363515c5a` failed after 33 passes, 1 deselected; `tests/test_rm11_node.py::test_real_npm_pack_and_tar_traversal_refusal` invoked real `npm pack --offline`, which exited 1 and reported only a private npm debug-log path. Earlier `npm ci` failure in `bcafe53cb7044141b364d4ae1a1fa749` remains open. No npm debug log was supplied, so no root cause is established.
- **Change:** Preserve real npm tests as independent native qualification; ensure mandatory archive verifier regression uses stdlib tar construction and tests valid identity, traversal and symlink refusal even on hosts with broken npm. The Termux source-install validator reports both external gates explicitly; diagnostic probe captures both commands plus npm log tails. Production lock/manager/output verification is not weakened.
- **Next:** Run optional offline device probe and attach diagnostic evidence; qualify actual npm operations and consumer workflows independently.

## FC-04 — RM12/RM13 artifact verification truth (2026-10-25)

- **Baseline:** clean post-FC-03 tree `d1efec65f5c35afaf8e640e33e5d8262bb952e72` (reported user HEAD `2ab62056602446d03754a922061c0f4cf8015f76`).
- **Prior defects:** `RM12-T3` desktop nested JAR verifier accepted bad `.class` bytes that `verify_jar()` refused; `RM13-T3` ZIP archive verifier accepted text-only ZIPs with `abi_verified_member_count=0` and described them as structurally verified for an ABI.
- **Changes:** Reuse bounded nested/standalone JAR structure/signature admission; check actual class constant-pool/member framing and desktop launcher identity/permissions; refuse empty ABI ZIPs, mixed/wrong architectures and opaque purported native members; verify optional native byte-receipt ABI/digest before issuing `ABI_VERIFIED` status; preserve no release trust claim.
- **Tests:** `tests/test_fc04_artifact_integrity.py`, `tests/test_rm12_jvm.py`, `tests/test_rm13_cpp.py`, and FC-02/FC-03 compatibility. This entry is a planned candidate until the source-bound install receipt is attached; do not promote native Termux or hosted gates from local tests.
- **External gates:** Actual Termux ARM64 re-verification, hosted compiler target matrices and real consumer archive contracts remain pending independent execution receipts.

## FC-05 — RM20 review-completeness and launcher/CI authority (2026-10-25)

- **Original defect:** RM20 generated only target stubs, labelled discoverable repositories `READY`, and could commit those stubs without job DAGs, typed outputs, wrapper launchers, full host/toolchain review or explicit CI retention. Real Studyflow/BKX consumers could not be called qualified.
- **Repair:** All automatic target-only proposals are now `REVIEW_REQUIRED`; `--review-spec` binds complete human-reviewed TOML, target identities, resource-scoped jobs, nonempty DAG workflows, launcher byte identities, tracked Actions hashes, host/toolchain declarations, signing/device/release authority and unowned/shared source inventory to the original Git source. A native scratch-root parser/planner verifies configuration without executing project code. Apply uses FC-02 isolated commit/CAS/index lock, guarded exclusive destination creation and never touches existing CI or project files. Empty/unknown/ambiguous projects still fail closed.
- **Verification:** New `tests/test_fc05_onboarding_closure.py` covers mixed Go/.NET/CMake, unchanged custom CI, audited review SHA, source drift, launcher retention and creation, competing writer preservation, incomplete scopes, unexpected plugin roots and read-only parsing. Updated RM20/FC02/FC03 tests preserve previous source drift and race coverage; generated CLI surfaces regenerated.
- **Status:** Source engineering tested locally; Termux user installation and real Studyflow/BKX/hosted Actions integrations remain **OPEN** until independent consumer receipts. Real new-hosted-workflow export requires separate review and is not silently generated. Historical audits remain archived, not rewritten.

## FC-06 / RM09-A1 — explicitly reviewed same-job NuGet restore reuse (2026-10-25)

- **Original defect:** RM09 native jobs independently repeated `dotnet restore --locked-mode`; GitHub `needs` never transferred filesystem state. The original requirement to share restored dependencies across solutions was not satisfied.
- **Affected source:** `devtool/actions_rm09_dotnet.py`, `devtool/actions_graph_export.py`, `tests/test_fc06_dotnet_shared_restore.py`; see `docs/FC06_DOTNET_SHARED_RESTORE_CLOSURE.md`.
- **Remediation:** opt-in two-or-more reviewed solution restore, single isolated NuGet store and SDK pin, source/lock/TFM/RID hashing, package ID+version+SHA-512 consistency, post-execution metadata **and actual restored `.nupkg` archive-byte hashing** with a bounded receipt, independent fallback left truthfully labelled.
- **Negative evidence:** incompatible host, non-shared identities, conflicting SHA-512 for same id/version, changed lock, tampered package store, SDK mismatch, and failed restore block downstream acceptance. Stub native execution is *not* actual hosted SDK evidence.
- **Status:** engineering implementation tested locally; native actual two-solution SDK acceptance and hosted producer/consumer/source/attempt authority `OPEN_EXTERNAL_GATE`, no release promotion.

## FC-07 — source hygiene and regression authority (2026-10-25)

- **RM00 regression failure:** `tests/test_rm00_shared_contracts.py` historically froze two providers despite six subsequently source-reviewed adapters. Replace with explicit eight-provider equality, immutability and unknown/reflection refusal. Do not treat the original failed test as an actual registry compromise.
- **Full-suite gap:** Prior 154/162/336 selected regression runs did not inventory all tracked tests. Add deterministic exact-once test enumeration and bounded native pytest collection/shards with JSON exit/skip/timeout details. Any unfinished/failed shard remains a gate; do not rewrite as PASS.
- **Authority drift:** Historical RM06–RM09 and other audits contain source-accurate conclusions at the time but were overtaken by repairs. Keep them immutable; add `docs/RM_CURRENT_STATUS.md`/JSON and the original 168-obligation planning ledger as a reconciliation source. All hosted/consumer/release gates remain outside local source evidence.
- **Affected:** RM00, G0/G1 and FC-09 handoff. **Acceptance:** `tests/test_fc07_regression_authority.py`, RM00 originals, generated CLI surfaces, real bounded test inventory and native Devtool overlay rollback.

### FC07-BROAD-001 — Broad shard results and remaining Surefire interaction

- **Evidence:** Full pytest collection stalled; 12 bounded shards collected all 6,737 cases successfully. First execution shard originally had six failures; RM11 host-message fixtures, EXO output union and federation CLI import bug were repaired, with 46 targeted passes. Repeated shard printed 562 passed/1 failed/5 skipped without a final wrapper exit receipt; Surefire structured-evidence test passes in isolation but failed when run with other test modules.
- **Status:** PARTIAL — broad execution gate remains open, **not silently skipped**. Source fix/test inventory and RM00 review are implemented; full suite and live host/device authority pending.
- **Affected:** `devtool/cli.py`, `devtool/runners.py`, `tests/test_actions_r02_host_review.py`, `tests/test_exec_x02_reuse_qualification.py`, `tests/test_frx_x03_executable_federation_groups.py`, `tests/test_tsys02_adapters.py`.
- **Recommendation:** Run 12 bounded pytest execution shards independently and investigate report timestamp/fixture interaction without loosening stale-report trust policy.

### FC-08 — G1 Maven Surefire evidence provenance and real-authority collection (2026-10-25)

**Observed:** FC-07 broad shard reported 562 passed, 5 skipped and one Maven Surefire mismatch: a generated current XML report sometimes lost authority to a contradictory stdout summary. Wall-time freshness used `mtime` with 1 ms tolerance, which does not robustly distinguish new, unchanged, and modified reports across Android/overlay/coarse timestamp clocks. An attempted full-shard rerun did not finish within the bounded execution window and **does not count as passed**.

**Remediation:** `devtool/gradle_reports.py` and `devtool/runners.py` record a pre-invocation XML identity snapshot, require the report to be new/changed before accepting it as current, and preserve stdout fallback only when no current structured result is present. The new FC-08 tests cover old timestamps on newly generated reports, unchanged earlier reports, and overwritten same-path XML.

**Evidence tooling:** `.github/workflows/devtool-fc08-host-evidence.yml` is manually dispatched and does read-only host/tool observation; `tools/fc08_evidence.py` provides a seven-lane plan, fail-closed authenticated GitHub metadata verification, and actual ARM64 Termux probing. All lane qualification stays open without independent, source-bound raw workloads and authority checks. No mocking or version output may be promoted to hosted/device/release PASS.

**Open:** G1 all-shard zero-exit execution, hosted Windows/Linux/macOS end-to-end tests, real Termux/Android instrumentation, signed artifact authority, real Telegram/Studyflow/BKX cross-consumer acceptance, FC-09 source/evidence seal. See `docs/FC08_REAL_HOST_CONSUMER_QUALIFICATION.md`.


### FC-08 Termux host-observation test portability (2026-10-25)

- **Evidence:** Actual Termux ARM64 FC-08 installer run `46539946c9e747d58592e465587967c0` stopped at `test_host_probe_rejects_wrong_os_and_never_promotes_tools`: the test requested hosted `Linux` on Android, and production `host_probe` properly raised `wrong physical OS for selected FC-08 host lane`. The prior 10 checks passed; validator exited before commit.
- **Root cause:** The test asserted physical Linux admission rather than isolating the simulated host environment. This is a test portability defect, **not** permission to classify Android as a GitHub Linux runner.
- **Fix:** Simulate Linux/Darwin/Android only within scoped `monkeypatch` unit tests. Assert wrong-host and mismatched-runner claims still refuse before executing probes; verify the accepted simulated result remains `OBSERVED_NOT_EXTERNALLY_VERIFIED` and never release qualified. Production `host_probe` and authenticated evidence verification remain unchanged.
- **Status:** Source-test correction locally qualified; actual Termux overlay acceptance remains pending. Real hosted workloads, full G1 and consumer/device qualification remain OPEN.

## G1 native R1 acceptance regression — batch dry-run host isolation (2026-10-26)

- **Evidence:** Termux native Devtool run `4d87cfbdde03419c8bd4770b7e4e221b`, validating `G1_CURRENT_CONTRACT_REMEDIATION_R1_20261026.overlay.zip` on exact FC08 source, failed `tests/test_cli.py::CliTests::test_batch_dry_run` with `1 failed, 42 passed`. The nested dry-run planner had no explicit execution selector and auto-detected the diagnostic `host-android` substrate from its sanitized Android environment. The public selector normalizer deliberately refuses `host-android`. The displayed receipt stops at `BLOCKED_G1_MANDATORY_TESTS:1`; rollback/source restoration must be established by the final run receipt and Git status before applying the replacement.
- **Classification:** CURRENT_TEST_FIXTURE_BUG; the test intends to verify batch dry-run planning, not unsupported host-substrate routing. Production selector rejection must not be relaxed; `host-android` is not an Android-device backend and not evidence of native-Termux eligibility.
- **Remediation:** Preserve the current production resolver. The current-contract batch test explicitly chooses the supported `desktop-linux` simulated planning environment, while both observed-substrate functions are set to `host-android` during the call. This makes the reported regression deterministic on GitHub Linux and native Termux, without inventing a Termux prefix or changing system environment variables.
- **Scope:** R1R2 replacement is **rebased onto FC08**, not layered on an uncommitted failed R1. Only this fixture and the cumulative finding change relative to R1. Continue to require full R1 mandatory source validation; full 24-shard GitHub G1 and FC09 seal remain OPEN.
- **Status:** Implemented candidate; native Termux acceptance and hosted regression evidence pending.


## G1 R1R2 native validation timeout — R1R3 remediation (2026-10-26)

- **Evidence:** Native Termux run `ddf54a2fb05c4da384dbf22cd3f0b042` progressed through the current-contract mandatory suite without an assertion failure in its displayed output, then emitted `BLOCKED_G1_MANDATORY_TESTS:124`. Its R1R2 artifact validator imposed a 280-second `Popen.wait` deadline, explicitly sent SIGKILL to the pytest process group on expiry, and reported exit 124; the manifest's 390-second outer cap would independently reject some slow native Termux runs.
- **Classification:** `VALIDATOR_EXECUTION_BUDGET_BUG`. Native host resource/speed variance is not a current-contract test failure, and no test should be dropped or downgraded merely to fit a Linux CI clock budget. The user's output is not a completed native validation receipt; transaction/rollback confirmation and source cleanliness remain required before a replacement apply.
- **Correction:** Run the same 10 modules and pytest flags with output isolated in a temporary log; remove the premature inner kill, report elapsed time and bounded pytest-log progress every 30 seconds, return the complete test transcript after exit, forward interruption/timeout signals to the child process group, and raise the source-bound outer transaction timeout to 3600 seconds. Keep `-x` fail-fast and native rollback-on-failure. No test, fixture, CLI routing, or production safety behavior is relaxed relative to R1R2.
- **Acceptance:** Exact-source bundle validation, disposable clone positive apply with all 238 mandatory checks, forced-validator-failure rollback, and refusal of dirty/wrong source must be separately evidenced. Actual Termux pass and 24-shard hosted full regression remain open until independently observed.


## G1-R1R4-PY314-HELP-COLOR — Golden help must ignore SGR styling, never option identity (2026-10-26)

- **Evidence:** Native Termux apply `a20dfbe470c144838a735e344221c3fc` completed 223 tests and 40 subtests before `tests/test_arch_final_seal.py::test_golden_cli_help_contract` failed: actual help starts with `\x1b[1;34musage: ...` but the current golden comparison expected `usage: devtool ...`.
- **Affected:** Active golden help comparison on Python 3.14/Termux where argparse provides styled output, not canonical parser command identity.
- **Recommendation / implementation:** In the test fixture only, remove SGR sequences from `format_help()` output before asserting current command synopsis and exact stable help body; retain the unstyled golden fixture and add a colored-help regression. Do not disable operator color globally or relax canonical CLI schema testing.
- **Status:** R1R4 source candidate; local architecture tests (16) and mandatory selection (239 tests, 40 subtests) passed on Python 3.13 with injected ANSI regression. Native Termux and hosted full 24-shard acceptance **OPEN**.

## FC08 real-host observation and G5 coverage follow-up (2026-10-26 local / 2026-10-27 UTC)

- Evidence: GitHub run `40425019752`, attempt 1, source `e09057529dc9016bc514ccfb43e45b0b978625b7`, tree `337d4ef8bb06a8a700bce469a869c6bcba256dc3`; four independent job/artifact identities; raw JSON SHA-256 hashes included in `FC08_AUDIT.json`.
- Status: HOST_OBSERVATION_VERIFIED, REAL_HOST_WORKLOAD_QUALIFICATION_OPEN. The current reports deliberately set `execution_semantics_qualified=false` and `release_qualified=false`.
- Gap: `meson` unavailable in observed Linux environment. Fix via explicit setup in future hosted-workload workflow, not by skipping the Meson provider.
- Gap: Windows `where cl` exited 1 without Visual Studio developer environment. Use VsDevCmd/VS setup and real compilation; do not equate PATH absence to MSVC unavailability.
- Gap: real Linux uv/Poetry/PDM, Node, Rust, Java, CMake/Meson ABI/negative cases and macOS native provider execution, Windows .NET shared restore/coverage/TRX/publish are still unqualified. Existing G5 real fixture matrix is useful bounded evidence, not complete Devtool provider acceptance.
- Gap: hosted G1 `test_real_ed25519_trusted_key_only` skipped from missing optional `cryptography`. Add to future CI test dependencies and assert it executes; do not recast historical pass as security qualification.
- Open separate authorities: native Termux real workloads, installed signing, consumer adoption and owner approvals. RM19 remains cancelled.
- Status of this file: staging appendix only; append to canonical `docs/DEVTOOL_IMPROVEMENTS.md` when delivering the next source-bound Devtool overlay; no remote repo mutation is claimed.

## FC08-HOST-R1 native hosted workloads (2026-10-26)

- **Source baseline:** exact G1 R1R4 `e09057529dc9016bc514ccfb43e45b0b978625b7` / `337d4ef8bb06a8a700bce469a869c6bcba256dc3`.
- **G5 evidence:** run `40426743366` eight of eight real Python/Go/Rust/Java Ubuntu/Windows fixture jobs succeeded, source unchanged. This is narrowed G5 fixture evidence, not full provider or signing release authority.
- **Gap:** FC08 observation `40425019752` proved four hosts but not real native provider runs; missing Linux Meson and Windows VS developer env.
- **Host R1 candidate:** read-only four-lane GHA DAG/Linux/Windows/macOS execution with real Devtool native build/test fixture invocations, negative refusal, locked .NET cold/warm NuGet restore, TRX/coverage, NuGet pack, publish and MSVC native compilation; explicit Meson and Visual Studio setup; fail-closed source-tree/run-id/attempt and artifact payload verifier. No release/pass promotion; hosted execution and independent review PENDING.
- **G1 cryptography:** one Ed25519 signature acceptance test skipped in run `40399195875` only because the hosted dependency was absent. Bounded `cryptography` is now installed for the full Python regression; new source must rerun G1 before test skip can be closed. This is not a signing credential.
- **Limitations:** Repeated/failed/permission-denied GitHub dispatch, Linux lock/ABI adversarial, native Termux/Android, consumers, signing, and FC09 160-active/8-cancelled promises stay OPEN.
- **Status:** Source-bound implementation candidate; local deterministic contract tests PASS, Linux CMake/Go CLI smoke PASS; no live hosted workload gates yet.


## FC08-HOST-R1R2 native namespace collision: repo-local `tools` imports (2026-10-26)

- **Observed evidence:** native Termux Devtool run `5843698efef74683919f8e955fff8abc` stopped at collection of `tests/test_fc08_host_workload_contract.py`. `from tools import fc08_host_workloads as h` resolved `tools` to `/data/data/com.termux/files/usr/share/hermes-agent/tools/__init__.py`, not Devtool's repository-local `tools/`. The native validator returned an import error before running any tests.
- **Classification:** current-test import-path isolation bug, not an FC08 hosted provider execution or system capability failure. No source apply success or rollback receipt follows from the truncated operator log.
- **Correction:** the contract test prepends the *resolved repository-local* `tools/` directory, imports the three named sibling modules directly (never via a top-level `tools` package), and asserts their `__file__` origins. This is independent of an already-imported third-party `tools` package and remains compatible with Python 3.14 in Termux. Production workload scripts, GitHub workflow, negative-case checks, execution authority and evidence schema are unchanged.
- **Installation:** complete R1R2 replacement on the same exact **clean** R1R4 commit/tree, not layered onto R1. Devtool must provide successful current-contract native validation, one clean commit, or automatic rollback.
- **Status:** source candidate; operator native acceptance, new-source hosted real workloads, and Ed25519 G1 repeat remain OPEN. RM19 still CANCELLED.


## FC08-HOST-R1R3 — failed-run diagnostic fidelity and native portability blocker (2026-10-27)

- **Evidence:** GitHub Actions [40444483038](https://github.com/Mikeyphw/devtool/actions/runs/40444483038) is an authentic completed FAILURE on exact source `9d8392598ed9a306d4551eaaa7c023d115625a0e` (tree `030ee0a2525d33f1f0ccf7ffc2f03b135c9fe10f`). DAG and macOS succeeded; Linux and Windows failed; the fail-closed summary refused incomplete evidence. Capture correctly returned `WRONG_SOURCE_BRANCH_OR_RUN_RESULT` (broad status error despite correct source).
- **Runner observability defect:** `must()` copied only the last 300 characters of subprocess stderr. Devtool writes many actionable diagnostics to stdout. Both failing lanes lost their root-cause output, and no `*.json` success or failure artifact was produced. Restore bounded, redacted stdout/stderr diagnostics, exit code and hashes; emit `*.failed.json` on failure; artifact upload must include failures while the summary continues to reject them as qualification.
- **Windows portability:** `devtool/cli.py` unconditionally imports POSIX-only `resource`; other runtime-critical modules rely on POSIX-only `fcntl` and `flock`. Do **not** replace locks with no-ops, emulate Windows by relabeling Linux, or mark the Windows provider PASS until genuinely implemented/adversarially qualified. This is a separate cross-platform architectural remediation.
- **Python fixture portability:** A stand-alone Python unittest fixture should execute via Devtool's explicit Python test adapter rather than accidentally triggering a uv-managed PEP-517 distribution build based on a synthetic pyproject. Separate native uv/Poetry/PDM operations remain mandatory.
- **Status:** diagnostic/fixture patch candidate only; still requires native installation and hosted Linux/Windows independent evidence. Windows qualification explicitly OPEN.


## FC08 HOST R1R3 native validator regression — nested Termux subprocess identity (2026-10-27)

- **Evidence:** Actual operator Termux run `78cdd90c257c47a9937054023277add3` on clean HOST-R1R2 passed 16 active tests but failed `HostR1R3FailureEvidence.test_python_fixture_uses_real_devtool_provider_with_no_distribution_install`: the nested Python CLI, spawned inside the sanitized artifact validator, detected diagnostic `host-android` (not a supported execution environment). This is an observed current-contract test harness issue, not a genuine Python provider failure or evidence of a successful apply.
- **Affected:** `tools/fc08_host_workloads.py::devtool` and its negative-execution sibling when launched from sanitized actual Termux Python; no general change to `devtool.execution_environments` or trust preflight.
- **Fix:** Derive Termux identity only when the running Python prefix and the real resolved interpreter both reside in the canonical Termux-owned Python prefix. Restore the physically observed `PREFIX` in the *nested fixture subprocess environment*; do not inject `TERMUX_VERSION`, fabricate an OS, normalize `host-android` as a supported selector, or map native Android to desktop Linux. Apply the same exact correction to the negative exit test.
- **Regression:** Simulated sanitized Termux interpreter path, foreign/desktop interpreter refusal, scoped fixture subprocess arguments, negative real-exit evidence and unchanged full 17-test fixture suite. This is a **complete R1R4 replacement** from original HOST-R1R2 source because R1R3 did not produce a passing native apply.
- **Status:** Candidate only pending true Termux installation; full Linux/Windows hosted workload qualification, fresh G1, Android device/signing, consumer evidence and FC09 remain OPEN. RM19 remains CANCELLED.

## BKX API-03 R3 Go `WaitDelay` transport / inherited-stdout evidence (2026-10-27)

- **Affected:** `devtool/runners.py` Go JSON process-live I/O and `devtool/go_test.py` event completeness, not `GoRunner.build_validation_specs()` or BKX production code.
- **Historical evidence:** BKX R2 receipt `af27fac12cd44663ae2b5dc20e753ccf`, R3 receipt `dd90c585b20a4ca993e5b57a4d41fcea`, and disposable artifact `BKX-DEVTOOL-EXACT-20261027T041137Z.zip` reported `exec: WaitDelay expired before I/O complete` with two package I/O failures while independent staged Go assertions passed. Actual cause is not yet established: slow output handling and a descendant holding stdout/stderr open are competing explanations.
- **Defect:** Existing process-reader generator returns control to synchronous Go JSON parsing/logging/UI before reading the next child chunk, exposing the Go pipe to output backpressure. The existing generic reader also has different post-exit incomplete-EOF semantics, so blindly moving all supervision into a worker is unsafe.
- **Source candidate:** Independent producer drains Go stdout into a private disk-backed, fixed-capacity spool. Main-thread consumer preserves all GoTestJSONStream and UI semantics; capacity/decoding/producer/EOF failure returns nonzero and records `go_output_transport` counters and timestamps. The complete restore/build/test/vet Go validation graph stays intact. Go JSON incompleteness is never promoted to passing merely because a subset of events passed.
- **Local evidence:** Dedicated slow renderer, racing concurrent reader/writer, quota exceeded, inherited descriptor, cancellation, consumer exception and incomplete JSON tests; existing Go failure reporting and process hang tests. Real small Go module test also passed with authoritative package/test counts and transport bytes. See `docs/DEVTOOL_GO_OUTPUT_SPOOL_BKX_R3_20261027.md` and acceptance transcripts in the installation bundle.
- **Status:** **SOURCE CANDIDATE**. Real Termux validation, whole Devtool source regression, exact BKX API-03 R3 standalone suite, BKX disposable Devtool transaction, transaction receipt and source rollback evidence require independent completion. No no-op validation, skipped package errors or altered Go WaitDelay settings. RM19 remains cancelled.

### 2026-10-27 — GO-SPOOL-R2 post-squash P1/P2 boundary remediation

- **Authority and source:** GitHub `main` squash commit `dc23352e8f9d3ce3370ce82b88e067bd6b10deae`, tree `d400d6cb2ea8a9677e4b18997a32eced65f3270c`, retaining the feature-tree Go bounded output spool. The previously applied Go spool source was not the final BKX API-03 R3 acceptance.
- **P1 verified defect:** A child could exit zero with an empty or output-only JSON stream; absent malformed records and unfinished packages, the completeness gate accepted zero completed Go packages. **Fix:** `devtool/runners.py::_check_go_stream_complete` now demands `GoTestJSONStream.report().packages_total > 0`; no-test-file packages remain valid when a terminal `skip/pass` Go event is received. Empty streams fail with `incomplete_go_json` and `completed_package_count=0`, with transport error metadata and nonzero code.
- **P2 verified defect:** `Popen` occurred before `capacity_from_env()`/`GoOutputSpool` allocation, so invalid config could raise with an already-running child outside process shutdown scope. **Fix:** allocate and validate spool **before** starting the child; on `Popen` failure close the spool in the original fallback path, and on exceptional spawn paths close it before re-raising. No child can exist when spool setup throws. Existing Go cancellation/supervisor semantics otherwise unchanged.
- **Reproduction and regression:** `tests/test_go_output_spool.py` adds empty and output-only zero-exit streams, real-shape no-tests terminal skip, invalid capacity rejected before `Popen`, failed private-spool initialization before `Popen`, and Popen failure cleans temporary spool storage. Entire Go/spool/process targeted suite: 98 pytest PASS + 7 subtests, zero fail/skip. Real `go test -json` on no-tests package and Devtool spool path: PASS locally on Linux. Rollback and target device qualify separately.
- **Scope:** Go-only transport. No Go DAG stage removed, no bypass of `go test -json`, no change to default spool limit, security or permissions. RM19 remains cancelled. Whole-repo G1 on squash, native Termux hotfix apply, exact staged BKX R3 standalone+disposable Devtool transaction, Android/device/consumer FC08 and FC09 evidence **OPEN**. Do not mark release-qualified.

### FC08 HOST-R1R7 — rebased npm/Windows qualification, source-main 2026-10-27

- **Evidence:** HOST-R1R6 native Termux validation `c298fa2f315d4f87852938e37f558a82` rolled back after real npm `compile_failure`, exit 1. The test printed only the tail pointing to the npm debug log; the actual npm failure cause was **not captured**, so attributing the event solely to shell selection remains a hypothesis.
- **New correction:** Rebased HOST-R1R6 POSIX/resource/windows byte-range locking and npm execution lifecycle changes onto the squashed `main`, preserving the Go output spool P1/P2 fix. The Termux Node environment now chooses an executable canonical `$PREFIX/bin/sh` only on an observed physical native-Termux host and only absent operator npm script-shell settings. This addresses a documented npm `/bin/sh` portability hazard without ignoring script failures.
- **Tests/evidence:** Native npm build script through Devtool, managed Node spec, invalid/fake prefixes and operator override. This source was locally validated in an isolated desktop clone; Termux and hosted Windows remain EXTERNAL OPEN until actual runs.
- **Next:** Native Termux apply, inspect full npm failure diagnostics if still red, run fresh hosted Linux/Windows/macOS + G1 against the committed source. No release/final gate promotion.

- **Additional rebased-source correction:** HOST capture identity now binds the canonical `main` branch and the squash integration ancestor `dc23352…`; the old feature-branch-only capture guard would refuse even a good main run. Remote source/tree and SHA checks remain mandatory.


## FC08 HOST-R1R8 — Termux npm update-notifier execution boundary (2026-10-27)

- **User-provided native evidence:** HOST-R1R7 apply run `5a7c59bdd38b46ac8f45cc9282b49a73` failed only at `NpmLifecycleContract.test_actual_node_cli_returns_zero_and_seals` (31 passed; one failed), then rolled back. `Devtool-FC08-NPM-DIAGNOSTIC-20261027T205242Z.json` preserved npm 11.21.1 / Node 26.12.1, script exit 1, and npm debug log with no script-level error. Both direct npm projects passed, while the same package builds failed when driven by Devtool in `Devtool-FC08-NPM-EXACT-FIXTURE-20261028T010545Z.json`; broad environment isolation was not the sole explanation.
- **Decisive controlled change:** On the exact unchanged R1R7 disposable source, the *same* native unittest was rerun with only `npm_config_update_notifier=false` and `DEVTOOL_EXECUTION_PRESENTATION=plain`, and returned `Ran 1 test ... OK`. The plain presentation setting is not a demonstrated root cause; the separate native command reproduces this valid workaround. Underlying npm notifier implementation failure is still not independently characterized; do not claim all npm exits are explained.
- **Production remediation candidate:** `optimized_build_env(kind='node')` defaults `npm_config_update_notifier=false` only when both the requested plan and detected executing host are native Termux, and only absent explicit `npm_config_update_notifier` / `NPM_CONFIG_UPDATE_NOTIFIER` from project or ambient environment. Retains isolated cache, audit/fund settings, real `npm run build/test`, process exit authority and EXO lifecycle. Do not mutate global npm configuration or override user choice.
- **Regression:** Test native plan environment, desktop/chroot/refusal, ambient/project uppercase/lowercase precedence, no parent environment mutation, and unchanged restore/build/test DAG, plus the existing *real* Node CLI test (which must pass on operator Termux). Ledger status `SOURCE_FIX_CANDIDATE`, not native or hosted qualification.
- **Acceptance still open:** New R1R8 atomic native Termux apply, updated `main` commit, fresh FC08 Linux/Windows/macOS/DAG hosted acceptance, 24-shard G1, exact BKX R3 full Devtool transaction and FC09 promise-seal. RM19 remains permanently cancelled.

## FC08 HOST-R1R9 — Windows AF_UNIX and G1 destructive-call inventory (2026-10-28)

- **Concrete GitHub evidence:** FC08 real-host run [40625517193](https://github.com/Mikeyphw/devtool/actions/runs/40625517193) at source `4699bd031290c3756ae6184326b0f4aed2f1de4e`, tree `67b4bc102535c741ae6e0e56a5a1a6fcf4d2c1e1`: Linux polyglot PASS, macOS native PASS, Github DAG PASS, Windows .NET/MSVC FAIL in Devtool pre-workload execution with `Internal devtool error: module 'socket' has no attribute 'AF_UNIX'`. Windows raw failure receipt uploaded; consolidated evidence correctly FAIL. The current summary diagnostic misleadingly says `FAILED_OR_MISSING_GITHUB_DAG_JOB` for **any** failed dependency despite DAG PASS; this reporting-detail issue remains open and must not change the strict summary predicate.
- **Windows root-cause repair candidate:** `devtool/ipc.py` publishes RUN/task state over Unix-domain sockets. `IpcPublisher.connect` allocates `socket.AF_UNIX` before checking transport availability; Windows-hosted Python may not provide this capability and raises AttributeError. Guard every direct IPC entrypoint with an explicit runtime capability check, refuse unavailable broker/action/cancel instead of inventing an acknowledged operation; preserve Unix behavior, live state files, canonical exit codes and evidence. Desktop IPC and clipboard socket backends likewise reject missing Unix sockets instead of raising. Do **not** pretend this implements a Windows IPC broker, release UI or cancellation transport; cross-host control still requires independently qualified Windows transport design.
- **G1 independent fail:** [full 24-shard run 40625521285](https://github.com/Mikeyphw/devtool/actions/runs/40625521285) also failed at the same source: 23/24 shards passed; shard 20 `test_r03b_destructive_fixed_point_has_no_unclassified_production_hits` found new `devtool/go_output_spool.py`. Source review: its `os.unlink(filename)` runs only after a private tempfile allocation fails partway through descriptor setup, while `self._path.unlink(missing_ok=True)` runs after producer termination and both owned descriptors close; all within that exclusively created spool file. The R03B audit should register **both exact source expressions** and a rationale, not skip the test or relax destructive-operation scanning. Preserve Go spool 0600, hard capacity limit and fail-closed pipe handling.
- **Validation status:** Hosted Windows and whole G1 still FAILED at the recorded source. Proposed local tests simulate absent `AF_UNIX` and verify no socket creation, false broker availability, negative cancel/action acknowledgments, and harmless CLI/clipboard/desktop responses; existing Unix IPC, tasker and R03B tests must pass. Full hosted Windows and 24-shard G1 **remain OPEN** until fresh CI run on the new exact commit. FC08 consumer/device, BKX R3 full workflow and FC09 stay open. RM19 cancelled.


### FC08 HOST-R1R10 — Native Termux Android HUD fixture isolation (2026-10-28)

- **Evidence:** Native Devtool `apply-overlay` run `b194823ffb7d4aeca11fd41880481c69` failed after 36 passing cases in `tests/test_tasker.py::test_direct_termux_android_bridge_autostarts_native_renderer`: the mocked positive launch returned `False`. The preceding HOST-R1R9 transaction rolled back; do not count it as installed or host-qualified.
- **Root-cause boundary reproduced:** `start_auto_hud` intentionally refuses launch under `DEVTOOL_NO_HUD=1`, `DEVTOOL_NO_AUTO_HUD=1`, or `DEVTOOL_AUTO_HUD_CHILD=1`. The positive and unavailable-companion test cases used `patch.dict(..., clear=False)`, so an outer artifact validator's inherited HUD-disable policy can short-circuit the intended branch. The original test reproduced the same failure when the three flags were injected; correcting its mocks removed it.
- **Fix:** Explicitly set all three disable switches to `0` inside the two Android bridge test fixtures; additionally test that a positive test temporarily overrides all three inherited true switches and restores their state. Retain the separate test enforcing real `DEVTOOL_NO_HUD` refusal; **no production HUD policy, IPC implementation, Windows runtime, or release authority is modified** by this specific R1R10 correction.
- **Verification:** Targeted IPC, clipboard, Tasker and G1/R03B suites passed with all three flags inherited as `1` (45 tests on a synthetic source assembled for verification). Native Termux apply and new hosted Windows/24-shard G1 results remain pending.
- **Disposition:** Source candidate / qualification OPEN. R1R10 completely replaces the failed R1R9 source-bound artifact and includes its original Windows IPC / G1 destructive-inventory repairs.

## FC08 R1R11 — hosted Windows solution target and G1 RM13 symbol inspection (2026-10-28)

- **Observed on exact source:** `a007a7c38d26cc919574eb59e95e889492f957ac` (tree `8f9d8aaaaec1c5f84f8addbf33331df789268515`). FC08 hosted run `40711125377`: Linux, macOS and DAG succeed; Windows returns `No dotnet target resolved` after previously failing on `socket.AF_UNIX`. G1 run `40711131824`: 23 of 24 shards succeed; shard 18 fails `test_real_native_gcc_binary_arch_and_symbols` with `RM-13 symbol inspection tool unavailable` after about 12 seconds. Both summary jobs fail closed.
- **Actual source boundary:** `tools/fc08_host_workloads.py` generates and tracks `FC08.sln`, but the Windows real CLI build did not select it via `--solution`. `devtool/actions_rm13_cpp.py::verify_binary` placed `nm` inside a 10-second timeout and collapsed timeout and missing executable into the same misleading error.
- **R1R11 remediation:** Windows real-host fixture explicitly checks that `FC08.sln` is the exact tracked local solution before passing `--solution FC08.sln` to Devtool's real CLI. The production RM13 `nm` subprocess remains required, uses a bounded 30-second timeout, reports timeout separately from unavailable tool, and preserves nonzero-exit, symbol-presence and ABI refusals. No workload is mocked in hosted CI, and no release-qualified claim is made.
- **Acceptance to collect:** Native R1R11 source-bound transactional apply; fresh FC08 four-host workload receipts (including real Windows .NET/MSVC build, tests, pack and publish); all 24 G1 shards and final summary; independent consumer/device and BKX R3 evidence; FC09 final seal. Status **SOURCE_CORRECTED_PENDING_HOSTED_ACCEPTANCE**. If `nm` still times out, do not bypass: inspect runner pressure and tool identity.

## FC08 R1R12 — prevent foreign `tools` package hijacking native overlay tests (2026-10-28)

- **Observed evidence:** R1R11 exact-source preflight passed on native Termux, and Devtool verified 5 touched files, but `validate.sh` failed during pytest collection before any targeted test: `from tools import fc08_host_workloads as fc08` resolved `tools` to `/data/data/com.termux/files/usr/share/hermes-agent/tools/__init__.py`, raising `ImportError`. No successful native apply/commit was observed. R1R11 was not qualified; confirm transaction rollback before retrying.
- **Cause and scope:** `tools/` in the Devtool repository is a namespace directory; an installed regular package named `tools` can win import resolution, especially when already present in `sys.modules`. The failure belongs to the **new R1R11 regression test**, not the Windows solution selection or RM13 native `nm` code. Adjusting `PYTHONPATH` alone may not repair a cached foreign package.
- **R1R12 remediation:** The targeted test loads `tools/fc08_host_workloads.py` exclusively from the test file's reviewed repository root via `importlib.util.spec_from_file_location`, never through the ambiguous global package. An additional behavioral check verifies the loaded module's exact path and real Devtool/Windows entrypoints. All existing positive and negative fixture/symbol tests remain active. No production paths, policies, toolchain tests, or hosted-gate outcomes are waived.
- **Acceptance:** Linux-based isolated and hostile-`tools` pytest collection and a source-pinned transactional apply are local only. Native Termux overlay acceptance, hosted Windows and all 24 G1 shards, BKX R3, FC09 and consumer/device/release qualification remain **OPEN**. Source pin remains `a007a7c38d26cc919574eb59e95e889492f957ac` / `8f9d8aaaaec1c5f84f8addbf33331df789268515`.


## FC08 HOST-R1R13 — Windows named-target solution ignored despite explicit CLI flag (2026-10-28)

- **Observed hosted evidence:** FC08 run `40725311281` at source `275f5c69ea0b357e4cf203c59b98ca080d746bf5`, tree `cfba88065af84d15edb227c8f5a0e694af7d41fa`: DAG, Linux polyglot and macOS native passed; Windows .NET/MSVC failed with `No dotnet target resolved` before a real managed build. The consolidated evidence verifier appropriately failed closed, albeit with an imprecise generic DAG error. The same commit's G1 full regression **passed 24/24 shards** in run `40725317664`.
- **Root cause, reproduced through actual source:** `tools/fc08_host_workloads.py` passed `--target app --solution FC08.sln build`. In `devtool/config.py::_resolve_targets`, the selected target branch returns `TargetConfig.effective_build_target()` before inspecting top-level `explicit_solution`; `[targets.app]` contained only `runner` and `root`, leaving the target empty. The previous regression inspected only CLI argv and therefore missed the failure. The configuration loader and `DotnetRunner.build_specs` reproduced the exact `SystemExit` even when the solution file was tracked and `--solution` supplied.
- **Fix candidate:** Declare `solution="FC08.sln"` directly in the reviewed Windows `[targets.app]` TOML and reuse the same literal fixture config in tests. Preserve explicit CLI `--solution` and the existing tracked-solution verification, locked cold/warm restores, real .NET build, TRX/coverage, pack, publish, MSVC and nonzero negative test. No general product change to precedence, no bypass and no fake hosted receipts.
- **Regression:** Exercise the real `load_project_config(target_name="app", solution_override="FC08.sln")` + `DotnetRunner.build_specs` with the actual named fixture for restore/build/test; prove undeclared solution still fails when only the CLI flag is present. This closes the test's false-coverage gap.
- **Qualification:** Source correction only pending native transactional apply and genuine Windows host acceptance. Linux/macOS/DAG previously passed; full G1 24-shard evidence is **PASS for commit 275f5c69ea0b357e4cf203c59b98ca080d746bf5**, not automatically transferable to the next source commit. Consolidated FC08 gate, BKX R3, device/consumer FC08, FC09 and release qualification remain **OPEN**.


### FC08 HOST-R1R14 — Windows UTF-8 child stdout and truthful four-lane failure diagnostics (2026-10-29)

- **Evidence:** GitHub Actions run 40735674610, commit `7ab73e150662a5fd92f61c22c97dabd0cb872874`, Windows lane failed after the real `FC08.sln` .NET build reported 0 warnings and 0 errors. The Devtool CLI subsequently emitted `Internal devtool error: 'charmap' codec can't encode character '\u2713'`, because the Windows Python captured stdout could not encode a success glyph. Linux, macOS and DAG lanes passed. The full 24-shard G1 run 40735681180 passed at the same commit.
- **Windows correction:** `tools/fc08_host_workloads.py` now sets `PYTHONIOENCODING=utf-8` and `PYTHONUTF8=1` for nested real Windows Devtool CLI commands (including negative-exit cases) only. The native .NET/MSVC execution and failure propagation remain unchanged. No global or user Python configuration is modified.
- **Evidence correction:** `tools/fc08_host_summary.py` preserves the four-lane, source-bound fail-closed gate but names the actual failed lane(s), instead of claiming that an otherwise passing DAG lane failed. The same module loads its sibling reviewed workload by absolute path to avoid the previously observed Hermes Agent `tools` import collision.
- **Regression:** `tests/test_fc08_r1r14_windows_utf8_and_host_summary_20261029.py` checks Windows-only subprocess encoding, a real cp1252 failure/UTF-8 success, CLI propagation, four-host rejection, complete valid receipt acceptance, malformed receipt rejection, and import-shadow isolation.
- **Status:** Source remedy prepared; hosted Windows native and four-lane summary re-qualification **OPEN** until a subsequent GitHub run passes. BKX R3, FC08 device/consumer and FC09 remain **OPEN**. The prior R1R13 G1 success is evidence only for its exact source commit; new-source G1 is **PENDING**.


### FC08 HOST-R1R15 — stale legacy assertion after truthful host diagnostics (2026-10-29)

- **Source/evidence:** R1R14 HEAD `70650d4433e4cd0446b04c024c7bf3fa17bbeb6d`, tree `4f6d1944cf02701c18c1e31fd3caae55ef624e68`; FC08 hosted Windows failure was observed only at earlier R1R13 commit `7ab73e1` (run `40735674610`). R1R14 encodes Devtool CLI child stdout as UTF-8 on Windows and reports exact failed lanes, but was not yet run in hosted Windows.
- **New source-test defect:** The legacy `test_parent_dag_failures_rejected_even_with_good_artifacts` still expects `GITHUB_DAG`, while the correct R1R14 summary returns `FAILED_OR_MISSING_GITHUB_HOST_JOB:dag`. Locally reproduced in a four-file suite: 48 PASS, 1 FAIL. The old expectation—not the summary policy—was stale.
- **R1R15 correction:** Align the legacy assertion with the truthful DAG diagnostic and assert that a real Windows-only job failure emits the Windows diagnostic. No production code, .NET/MSVC execution, hosted job, artifact schema, or fail-closed gate is modified. Source-pinned overlay with a transactional validation script and audit added at `docs/FC08_R1R15_HOSTED_TEST_CONTRACT_ALIGNMENT_AUDIT_20261029.md`.
- **Status:** Source hotfix prepared; 49 focused tests PASS and 77 FC08 source tests PASS locally with extracted-archive Git ownership accommodated. Native overlay apply, fresh exact-commit hosted Windows and all-four-lane receipts, full G1 shards, BKX R3, FC08 consumers/devices and FC09 independent qualification remain **OPEN**. No release qualification.

### FC08 HOST-R1R16 — Windows real .NET test evidence absent after UTF-8 correction (2026-10-29)

- **Observed regression / authority:** FC08 hosted real-workload [40959330051](https://github.com/Mikeyphw/devtool/actions/runs/40959330051), source `a709fed9234e6b6277cb4d23a98ae77fff5ffcd4`, tree `8291c48b86101538b4140b6f9292e2621f77baa8`: DAG/Linux/macOS passed; Windows `123747841290` failed `MISSING_TRX_OR_COVERAGE`. Windows failure diagnostic uploaded; fail-closed host summary refused `FAILED_OR_MISSING_GITHUB_HOST_JOB:windows`, so missing `host-summary.json` was expected. The Unicode encoding failure seen previously did not recur.
- **Observed design gap:** The fixture ran `dotnet test` with TRX/Coverlet options but accepted process exit 0 before discovering the TRX/Cobertura files. It suppressed successful command stdout (which is required to distinguish no test discovery versus misplaced reports). Windows GitHub runner offered SDKs 8, 9 and 10 with no fixture SDK pin. **The precise reason for the missing reports remains unproven until fresh hosted evidence.** VSTest can return 0 when no tests are discovered, per Microsoft .NET CLI documentation.
- **Source remedy R1R16:** Track `global.json` in throwaway .NET fixture to select installed .NET 8 SDK by compatible feature-band roll-forward. Generate the `.sln` with the .NET 8-supported `dotnet new sln` syntax. Track a `.runsettings` file requiring an error for zero tests. Retain tightly bounded, redacted stdout/stderr of *only* the completed real `dotnet test` when needing to attribute missing reports. Require exactly one TRX with nonzero fully passing executed-test counters and exactly one well-formed Cobertura coverage with positive valid-source line count. Continue all real restores, Devtool build, pack, publish, MSVC native build and negative exit tests, with unchanged host/source identity and fail-closed summary.
- **Evidence:** 10 dedicated R1R16 regression tests PASS; combined FC08 source suite **87 PASS** in local Linux validation. No Windows host, device, cross-platform release or consumer acceptance inferred. Source promises and open requirements: `docs/FC08_R1R16_WINDOWS_VSTEST_EVIDENCE_AUDIT_20261029.md`.
- **Status:** **SOURCE FIX CANDIDATE, HOSTED WINDOWS ACCEPTANCE OPEN**. Apply exact-source overlay with clean tree; push; repeat real 4-lane host workflow and 24-shard G1 at resulting commit. If Windows still fails, inspect new counted and bounded `dotnet test` diagnostics instead of claiming success or loosening receipt requirements. BKX R3, device/consumer FC08 and FC09 remain OPEN; RM19 cancelled.

### FC08 HOST-R1R17 — Windows MSVC architecture leaked into .NET solution restore (2026-10-30)

- **Status:** Source fix candidate; real Windows host acceptance **OPEN**. No release or consumer promotion.
- **Evidence:** GitHub Actions [40987672093](https://github.com/Mikeyphw/devtool/actions/runs/40987672093), head `3ec30a4eaf2ebfc2fa18274fc47bf1c0496bfa83`, Windows `123842504852`: `dotnet restore FC08.sln --use-lock-file` exited 1 with `MSB4126` and invalid solution configuration `Debug|x64`. DAG/Linux/macOS passed. This is **not** a missing TRX problem; restore fails before VSTest. Earlier run 40985988250 ran before R1R16 and failed for different reasons.
- **Cause:** MSVC tooling sets inherited Windows `Platform=x64`. The disposable SDK .NET solution defines `Debug|Any CPU`; MSBuild reads `Platform` from its process environment. Global cleanup would damage native C++ verification.
- **Change:** Scope case-insensitive removal of `Platform` and `Configuration` to `dotnet_must` subprocesses and the explicitly selected .NET Devtool CLI child only. Preserve all other environment values (including `VSCMD_ARG_TGT_ARCH` and `NUGET_PACKAGES`), MSVC subprocesses and all R1R16 TRX/Cobertura validity checks. Refuse non-dotnet invocations of the .NET helper. Add R1R17 regressions for environment isolation and audit of managed call sites.
- **Remaining:** Real Windows must complete locked restore, Devtool build, nonzero passing TRX, well-formed Cobertura, pack/publish, actual MSVC build and negative exit propagation. A complete source-bound four-host summary and independent review are still required; FC09, BKX R3 and device/consumer gates remain open. Source-unit tests are not Windows evidence.

### FC08 HOST-R1R18 — duplicated Coverlet output must be bound to VSTest (2026-10-30)

- **Observed:** Real FC08 [40989543287](https://github.com/Mikeyphw/devtool/actions/runs/40989543287), source `9cceb55b7ae9abab56f1e5aacfd57efab82fbb00` (tree `76955386d1725c38a8e7c5f923626127066a2c66`), DAG/Linux/macOS jobs PASS; Windows `123848464546` ran a real .NET 8 test successfully (`Passed=1`, `Failed=0`, one TRX, one Coverlet attachment logged) but failed because the recursive scan found *two* `coverage.cobertura.xml` paths. The fail-closed summary refused Windows; do not label that a successful four-lane run. The second file's origin/content was not logged.
- **Gap and change:** Previous `len(cov)!=1` conflated file uniqueness with test-run attachment identity. R1R18 requires a unique VSTest-reported coverage attachment when multiple XML files are found; rejects missing/multiple/outside-root/nonexistent references; parses all extra candidates; validates the selected Cobertura has positive `lines-valid`; preserves all candidate SHA-256 digests in the success receipt. Zero-test, failed TRX and duplicate TRX remain forbidden. No native workload or consolidated summary bypass.
- **Tests/audit:** Additional `tests/test_fc08_r1r18_attached_coverage_provenance_20261030.py` tests positive+negative, with prior R1R16/R1R17 contracts retained. Promise-by-promise audit: `docs/FC08_R1R18_VSTEST_ATTACHMENT_PROVENANCE_AUDIT_20261030.md`.
- **Status:** Source remediation prepared. New hosted real Windows, four-host summary and same-SHA 24-shard regression still **OPEN**. BKX R3, consumer/device and FC09 release qualifications remain **OPEN**.


### FC08 HOST-R1R19 — Independently reviewed real hosted evidence and current-source G1 (2026-10-30)

- **Authority/source:** `641009b8859e00a9c10cc264e2a0a41492b68d0c`, tree `01dd82e57eebb61aaabd0f937edc365a1dcaf8b3`. Real FC08 GitHub run `41068348030`, full G1 run `41068356185` on the same exact source/attempt.
- **Independently rechecked:** Four lane receipt JSONs + consolidated host summary (all five GitHub ZIP SHA-256 archive digests, canonical checks hashes, raw receipt hashes and identity), all 24 G1 shard ZIPs + summary (GitHub SHA-256 archive digests), 600 unique primary JUnit/log module records, 6,855 primary pytest tests and 75 passed subtests, 0 errors/failures/skips. G1 summary and all 25 relevant jobs passed.
- **Scoped closure:** `HOSTED_REAL_WORKLOAD_EVIDENCE_INTEGRITY_REVIEW=PASS`; `G1_FULL_SOURCE_24_SHARDS=PASS` for this exact source. Hosted Windows now passes all eight checks including VSTest and MSVC. This supersedes *only* earlier statements that new hosted Windows, four-lane capture and same-SHA G1 evidence were missing; preserve R1R14–R1R18 incident history as it occurred.
- **Still OPEN:** adversarial DAG retry/failure/permission, Linux negative lock/ABI, signed Windows authority and raw build/test artifacts, native Termux ARM64, real Android signer/installation/instrumentation, source-pinned real consumer adoption and rollback, FC09 cross-promise acceptance, formal release. JSON-only hosted receipts do not grant device/consumer/release qualification.
- **Artifacts/review:** `docs/FC08_R1R19_INDEPENDENT_HOST_G1_AUDIT_20261030.md` and `docs/FC08_R1R19_EVIDENCE_REVIEW.json` (complete original ZIPs plus SHA-256 manifest retained in evidence bundle, not repository). **`fc08_overall_qualified=false`, `formal_release_qualified=false`.**

### FC08 HOST-R1R20 — combined hosted DAG verifier negatives and Linux lock/ABI refusal (2026-10-30)

- **Source authority:** Post-R1R19 GitHub `main` `67ba4ea1f9226056600b51c0ef591b1eb5c8782f` / tree `52cc3b2525ba4402493cea27495992c93739eb90`; previous real host success `41068348030` and full 24-shard G1 `41068356185` belong to older source `641009b8859e00a9c10cc264e2a0a41492b68d0c`.
- **Merged implementation:** Add actual uv offline stale-lock refusal with manifest restoration and unchanged lock hash; verify a real CMake-built ELF with RM-13 x86-64 ABI acceptance and aarch64 rejection; invoke actual hosted summary verifier with bounded synthetic failed DAG/Windows jobs, incomplete `needs` and wrong-attempt lane receipts, requiring nonzero exits and no summary artifact. All previously required lane checks stay mandatory; DAG 3→4, Linux 11→13, Windows and macOS unchanged. No new token, hosted permission, workflow trigger, dispatch side effect, or release gate waiver.
- **Qualification truth:** Hosted source execution and full G1 on the **new** commit **PENDING**; live repeated/failed GitHub dispatch and real token permission denial are **OPEN** (not replaced by synthetic inputs); other lock/ABI matrix negatives, native Termux, Android signing, BKX R3/real consumers, FC09 and release remain **OPEN**.
- **Tests and traceability:** 115 FC08 tests PASS locally, real uv stale-lock check and real CMake ELF wrong-ABI refusal PASS; `tests/test_fc08_r1r20_hosted_adversarial_merge_20261030.py` and `docs/FC08_R1R20_DAG_AND_LINUX_ADVERSARIAL_PROMISE_AUDIT_20261030.md` document exact promises, completed/partial/open statuses and next evidence.

### FC08 HOST-R1R21 — real restricted GitHub dispatch denial and Cargo frozen lock negative (2026-10-30)

- **Reviewed authority:** GitHub `main` `be46c12b3dcd57568fda3ef2300cdc80d2956113`, tree `100fc5147433f7840df7c3d699de0ee28f429d28`. FC08 run [41084486679](https://github.com/Mikeyphw/devtool/actions/runs/41084486679) passed all five jobs (Linux 13 checks, DAG 4); 24-shard G1 run [41084493660](https://github.com/Mikeyphw/devtool/actions/runs/41084493660) passed 6,867 tests, no failures/errors/skips at the *same source*. This supersedes R1R20 source-only pending statements for that commit; historic incidents remain unchanged.
- **Two-window improvement:** Add a single real GitHub API dispatch refusal using the DAG runner's read-only `GITHUB_TOKEN` (`contents:read`, explicit `actions:none`) against the separate observation workflow, requiring authenticated source GET at exact SHA and an actual HTTP 403 on workflow dispatch. Reject 200/204/401/404/unexpected statuses; no retries, personal PATs, raw secrets, or claim that live dispatch retry/dedup was tested. In Linux, run Cargo `metadata --frozen` before/after an owned disposable manifest drift, require genuine nonzero, unchanged lock bytes and successful restoration. DAG 4→5, Linux 13→14; other platform checks unchanged.
- **Evidence:** 129 FC08 tests PASS locally with mock GitHub HTTP/Cargo commands plus preserved real uv/CMake negative cases. Live restricted GitHub HTTP and real Cargo on hosted runner are **PENDING** until new source-pin CI; 24-shard G1 likewise **PENDING** on post-R1R21 SHA. Local tests do not qualify external operation.
- **Still OPEN:** Successful/failed/repeated live dispatch lifecycle, independent/privileged permission audit, other negative manager/ABI cases, signed Windows authority, Termux ARM64, Android signer/installed instrumentation, BKX R3 and real consumers, FC09 and formal release. No `fc08_overall_qualified` or `release_qualified` promotion. Full promise-by-promise audit in `docs/FC08_R1R21_RESTRICTED_DISPATCH_AND_CARGO_FROZEN_AUDIT_20261030.md`.

### FC08 HOST-R1R22 — Cargo frozen lock graph negative was not exercised (2026-10-30)

- **Observed failure:** R1R21 GitHub Actions [41090982926](https://github.com/Mikeyphw/devtool/actions/runs/41090982926), commit `2f11fb507c8e21d5b36f8a48d9ef73a80426eda2` / tree `2268aeadddff12ed72d7c29cb90574a0a769c5a4`, Linux job `124184751554`: `FC08_CARGO_STALE_FROZEN_LOCK_ACCEPTED`. R1R21 used `cargo metadata --no-deps --frozen` after a package-version edit, but the real command succeeded. DAG/Windows/macOS passed. Fail-closed final summary appropriately refused Linux. Same-commit G1 [41090989020](https://github.com/Mikeyphw/devtool/actions/runs/41090989020) passed 24 shards and 6,881 tests. The live read-only-token GitHub dispatch denial HTTP 403 was recorded in the DAG receipt, with only that narrow permission-boundary qualified.
- **Root cause and fix:** Metadata without dependencies does not reliably force Cargo's lock graph to update. The correction runs a real baseline `cargo build --frozen`, adds a valid owned local path dependency only to a disposable Rust package, requires `cargo build --frozen` to refuse due specifically to a stale lockfile, preserves the original `Cargo.lock` SHA-256, restores source and local fixture, and proves a final frozen build passes. Reject both zero-exit and unrelated nonzero errors. No external dependency/network, workflow permissions change, release promotion or weakening of other 13 Linux checks.
- **Verification and status:** Existing R1R21 Cargo tests updated for the new command and fixture, new 10-case R1R22 negative suite added. **139/139 FC08 tests PASS locally** under temporary Git safe-directory scope; real hosted Linux and four-host summary **OPEN** until post-R1R22 run. Existing DAG 5, Linux 14, Windows 8, macOS 3 and same-SHA G1 must pass again. Broader GitHub dispatch, native Termux, Android, BKX R3, consumers, FC09, formal release **OPEN**. Full promise audit: `docs/FC08_R1R22_CARGO_FROZEN_LOCK_GRAPH_AUDIT_20261030.md`.
