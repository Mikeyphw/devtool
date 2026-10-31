# FC08 R1R28-H1 — hosted G1 assertion and consolidated matrix source-clean repair

## Exact reviewed source and incident facts

- **Source to patch:** `Mikeyphw/devtool` commit `90cae7eded75e474834778e00ea2ec7a641c5412`, Git tree `64cbd4d38a962f224875bb64ee638e4f0c24f6fb` (R1R28). R1R27's passing evidence is historical; it is not fresh H1 qualification.
- **Four-host FC08:** GitHub run [41151152705](https://github.com/Mikeyphw/devtool/actions/runs/41151152705), five jobs `success` including Linux, Windows, macOS, DAG and fail-closed inventory. **PASS on old R1R28 source only.**
- **G1:** GitHub [41151157709](https://github.com/Mikeyphw/devtool/actions/runs/41151157709), 24/24 shards and 609/609 modules executed; 7,000 tests, 1 failure, 0 errors/skips. Shard 13 `tests/test_rmp_c02_consumed_source_authority.py::test_worker_rejects_replaced_file_before_unlink` failed because it asserted the original text `identity changed`. The worker correctly refused deletion and instead exposed `metadata.source_failure_diagnostics[0].category == identity-drift` and `safe_to_retry_unlink == false`. G1 correctly failed closed. **No claim of G1 success at R1R28 SHA.**
- **Consolidated opt-in matrix:** GitHub [41151169191](https://github.com/Mikeyphw/devtool/actions/runs/41151169191) `failure`, child success [41151195237](https://github.com/Mikeyphw/devtool/actions/runs/41151195237) `success`, deliberate failure [41151214308](https://github.com/Mikeyphw/devtool/actions/runs/41151214308) `failure` by design. Matrix log printed `BLOCKED_FC08_R1R28_MATRIX_SCENARIO_FAILED_REJECTION_RETRY`. Downloaded original matrix artifact `13023445797` contained **only** `r1r28-matrix-cycle.json`, proving 2 POSTs / 0 duplicate POSTs and both expected remote conclusions. The rejection/retry child's failed receipt was not uploaded, so its original cause cannot be independently determined from the hosted run.

## Root cause of matrix failure, bound to source behavior

`tools/fc08_r1r28_matrix.py` originally wrote each successful child JSON to the repository's `out/fc08/` directory **before launching the next child**. The existing `.gitignore` does not ignore `out/`. Every R1R25–R1R27 scenario runner's `fc08_dispatch_cycle.source_identity()` checks `git status --porcelain --untracked-files=all` and refuses a dirty source. Thus the matrix itself dirtied the repository before the second child, a direct source-level cause consistent with failure immediately after cycle. Because its old subprocess wrapper discarded stdout/stderr and only uploaded success receipts, the precise child category was **not** directly observed in that original hosted run. H1 does not claim an externally observed child diagnostic that was never published.

## H1 implementation and safety invariants

1. Keep the existing manually authorized, `actions:write` job-scoped workflow. **No modification of token privileges or trigger conditions.**
2. Retain each intermediate JSON only under `TemporaryDirectory` outside the tracked working tree until every selected child has finished. Publish original bounded, validated successful child JSONs and SHA-256 references **after** all child source-identity preflights. This fixes the self-dirtying sequence while preserving the source-validation guard.
3. On a failed or timed-out child, stop scheduling further scenarios and publish a fail-closed parent summary plus an **explicit allowlist-sanitized child failure receipt**. Include bounded reason, safe child error category, scenario, SHA-256 of original receipt bytes (if any), and false qualification flags. Never publish child stdout/stderr, raw GitHub bodies, credentials, arbitrary exception text, or source file paths.
4. Fix `test_worker_rejects_replaced_file_before_unlink` to assert structured `identity-drift`, `safe_to_retry_unlink=false`, `source_deleted=false`, no policy relaxation, and preservation of the substituted file. Retain all filesystem authority checks, including inode/time/dir-fd gates. **No loosened cleanup or alternate deletion path.**
5. Add 5 full-matrix lifecycle cases in existing R1R28 test module: no intermediate checkout files for all three children, valid artifact SHA-256 and coverage, allowlist failure, forged secret suppression, missing receipt and timeout. Update old source contract to assert delayed publication rather than an exact variable name.
6. Keep `fc08_overall_qualified=false`, `release_qualified=false`, genuine transport timeout/5xx `OPEN`, native Termux gate `OPEN`, Android/consumer/BKX R3/FC09 `OPEN`.

## Promise-by-promise status

| Promise | Locally supported evidence | New-source external status |
|---|---|---|
| Exact R1R28 source, clean tree, source hashes, atomic overlay | ZIP manifest and before-extract preflight | Termux apply OPEN |
| No repository output before later child source preflights | Three-scenario fake subprocess test | Real hosted matrix OPEN |
| Failure yields bounded, allowlisted, non-sensitive receipt | Error, injected secret, malformed/missing/timeout tests | Real hosted failure behavior OPEN |
| Valid success receipt and correct SHA-256 mapping | Multi-scenario receipt/summary test | Hosted archive audit OPEN |
| Legacy cleanup expectation matches structured worker result | RMP-C02 actual fresh-process worker tests | Full hosted G1 OPEN |
| Fail-closed no duplicate POST / uncertain journal | Existing R1R25–R1R28 regression suite preserved | Historical hosted R1R25–R1R27 PASS, new-source retest OPEN |
| Real 5xx/timeout, native ARM64, Windows signing, Android, consumers, FC09 | No new authority | OPEN |

## Requalification order

1. Apply **only** against exact SHA/tree `90cae7e` / `64cbd4d` with clean worktree; use `--keep-artifact` on Termux.
2. Push the H1 commit. Run FC08 host workloads and full G1 24-shard regression on **that exact commit**; if one fails, inspect logs and preserve failure receipts.
3. Explicitly opt into the consolidated matrix `scenario=all` on the H1 commit. Verify exactly three retained child JSON receipts with digests, controller summary, bounded real dispatch counts, correct remote identities and successful controller conclusion. The intentionally failing remote probe is not itself a controller failure.
4. Run native Termux ARM64 acceptance separately; never infer ARM64 success from Ubuntu actions.
5. Independently review same-SHA run artifacts and record genuine qualification evidence in cumulative ledger. No release promotion by H1 source-only tests.
