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
