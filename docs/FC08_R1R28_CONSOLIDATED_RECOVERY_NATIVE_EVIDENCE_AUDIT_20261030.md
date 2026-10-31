# FC08 HOST-R1R28 — consolidated recovery, real native acceptance and independent evidence

## Source and actual authority

Source base: `Mikeyphw/devtool` `eba6658e7d98f730d92294dcced654e58fab2d58`, tree `0cd2cecab0de9457b436f9f843dc676752eb3533` (R1R27). A new R1R28 commit must independently pass FC08, G1 and manually opted-in experiments. Do not attribute old successes to the new source.

R1R27 hosted [FC08 41142398605](https://github.com/Mikeyphw/devtool/actions/runs/41142398605) five jobs PASS; [G1 41142403159](https://github.com/Mikeyphw/devtool/actions/runs/41142403159) 24 shards, 608 modules, 6,972 tests and zero failures/errors/skips; real injected response-loss [41142422768](https://github.com/Mikeyphw/devtool/actions/runs/41142422768) PASS and probe [41142452648](https://github.com/Mikeyphw/devtool/actions/runs/41142452648) success. The response-loss controller ZIP `13019171157` was downloaded and checked against GitHub's `sha256:958af7d5eff3f80c59c3c07d8555654d97352f1a2c48c05728ba4c75a812bc23`. This proves *injected* loss after one real accepted POST, original journal state `dispatch-outcome-unknown`, no run ID and zero extra POSTs. **Actual HTTP 5xx/network timeout is still unobserved.**

## Consolidated merge window

R1R28 merges work that shares source/journal/evidence identity rather than continuing one overlay per exception: dispatch recovery lock hardening + read-only uncertain-run review, one manually selected resilience-matrix workflow, independent four-host + G1 archive acquisition and digest audit, Termux native ARM64 execution runner, cleanup identity diagnostics, and FC09 readiness mapping. It intentionally excludes Android signing, BKX R3 mutation and consumer adoption because none can be verified by a Devtool-only GitHub run.

## Promise-by-promise delivery

| Promise | Implementation and acceptance | Status before host/device execution |
|---|---|---|
| Full journal-lock coverage of non-idempotent requests | `dispatch_workflow` locks **all** POST paths, not only `resume_if_existing=True`; non-resuming duplicate matching source/ref/inputs is refused | Source implemented; offline race/duplicate contracts; R1R28 host/G1 pending |
| Definitive 422 retry remains permitted | `rejected-by-api` entries continue to be excluded from matching pending receipts | Source + existing R1R26 422 proof, new-source regression pending |
| Unknown 5xx/timeout/204 never causes blind redispatch | Existing `dispatch-outcome-unknown`/`accepted-uncorrelated` preserved; all new execution paths use the lock | Offline cases PASS; **genuine 5xx/timeout proof OPEN** |
| Safe manual review of uncertain request | `review_uncertain_dispatch` and `tools/fc08_r1r28_review.py` verify *explicit* GET run ID, repo/workflow/ref/SHA/event/attempt and unchanged journal; no correlation or POST | Source implemented; externally observed compatible run **not attributed** |
| One opt-in resilience matrix | `devtool-fc08-resilience-matrix.yml` with `cycle`, `rejection-retry`, `lost-response`, `all`; `actions:write` at a single job, manual-only input, bounded scripts and no release | Source implemented; **opt-in hosted run OPEN** |
| Independent GitHub archive review | `tools/fc08_r1r28_evidence.py` checks same SHA/tree, jobs, attempts, artifact count, raw archive SHA-256 and safe ZIP membership for five FC08 and 25 G1 artifacts; cross-host bearer token stripped on redirect | Source implemented; **new-source original ZIP review OPEN** |
| G1 raw JUnit and Windows signed artifact provenance | Collector explicitly leaves `g1_junit_independently_recounted=false` and `raw_windows_trx_coverage_and_signed_binary_verified=false` | **PARTIAL / OPEN**; ZIP integrity alone is not semantic or signing attestation |
| Native ARM64 Termux CMake/CTest/Meson | `tools/fc08_r1r28_termux.py` checks actual Termux Python prefix, Linux aarch64, real native CTest/Meson compile/test, ELF machine=183, owned temp and symlink cleanup refusal; emits source-bound receipt | Implemented; **native-device run OPEN** (Ubuntu cannot qualify it) |
| External `/sdcard/Download` cleanup failure | `cleanup_source_failure_diagnostic` reports which exact identity fields drifted, errno, shared-storage path category, source-missing state, always `safe_to_retry_unlink=false`, no raw paths/secrets; refuses identity mismatch | Diagnostic implemented; **actual Android root cause/fix OPEN** |
| Hermes Agent `tools` collision | All new entry points import `devtool` by unique package; no `from tools import`; existing R1R25–27 imports stay file-bound | Implemented and locally testable; native validation OPEN |
| Mock HTTPError ResourceWarning | Existing test fixture warnings are tracked independently; no unsafe global warning suppression | **OPEN** |
| FC09 / BKX / Android readiness | Machine-readable crosswalk documents validated versus unproven capabilities without release promotion | Crosswalk implemented; independent FC09/consumer/device gates OPEN |
| Full R1R28 same-source regression | FC08 tests, exact-base Devtool transactional installer; new-source FC08+G1 jobs after push | Local/installer acceptance to be recorded; hosted OPEN |

## Operator acceptance lanes

**A — Source/transaction:** preflight exact SHA + tree, clean worktree, original-file hashes, new 25+ offline contracts, full `tests/test_fc08_*.py`, transaction rollback on any artifact validation failure. Do not strip stale code from normal CI.

**B — GitHub hosted:** after push run `.github/workflows/devtool-fc08-host-workloads.yml` and `devtool-full-repository-pytest.yml` on same commit; optionally run `devtool-fc08-resilience-matrix.yml` with `authorize_real_dispatch=true` and selected scenario. `all` deliberately initiates multiple real, bounded no-op probes including an intentional-failure workflow; it is not part of normal CI. Independently fetch and verify 5+25 original GitHub ZIPs using the `fc08_r1r28_evidence.py` tool on matching source. This is source-bound metadata/archive integrity, **not** release qualification.

**C — Native Termux:** run the ARM64 native runner **on the phone**. Require installed `clang`, `cmake`, `ctest`, `meson`, `ninja`; do not install them automatically. The runner operates only inside owned temporary directories and emits a JSON receipt. A successful result does not prove Android APK signer/install/instrumentation, a real rollback after forced failure, root permissions or consumers.

## Stop conditions and safety boundaries

- No release, signing, Android runtime or consumer repository mutations from the consolidated workflow.
- No automatic linking of remote run IDs to an unknown local request: even workflow/ref/SHA equality proves *compatibility*, not which POST created a run.
- Never allow a second POST for an uncertain or already accepted matching request. A definitively rejected HTTP 422 is the only separately observed allowed retry path in these exercises.
- `--keep-artifact` remains recommended for Termux until the shared-storage cleanup failure is diagnosed with new structured metadata.
- If an artifact cannot be fetched, a digest does not match, a workflow lacks a job, or an ARM64 build tool is unavailable: **BLOCKED**, not a simulated pass.
- `fc08_overall_qualified=false`, `release_qualified=false`, `consumer_qualified=false` and formal FC09 seal remain open.
