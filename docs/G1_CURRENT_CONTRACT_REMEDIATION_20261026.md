# G1 current-contract remediation — source-bound candidate (2026-10-26)

## Authority and disposition

- Supplied exact base: `64283e6ea3a404a1afed18fcd9d55ad6cda5c4de`, tree `2df8e72f68ec81c19d2992b8bd11295efbc1e342`.
- GitHub Actions run [40257036036](https://github.com/Mikeyphw/devtool/actions/runs/40257036036) returned 592 collected modules, 6,758 tests, 39 failures, no errors and one skip. This is **failed G1 evidence**, not a qualification receipt.
- FC01–FC08 have been applied at the source level; FC09 remains **not eligible** until fresh 24-shard evidence is complete and green. RM19 splitting/extraction campaign remains **cancelled**; preexisting NEXT-06 extraction code is not a mandate to resurrect RM19.
- Test policy: **current contracts only**. Historic pins are past evidence; do not use them to force production code backward. Active rollback, SHA-256 source validation, no-clobber semantics, safe-cleanup authority and run terminal identity remain protected.

## Root cause / correction / verification

| Failure family | Disposition | Evidence and remaining work |
|---|---|---|
| CLI `apply-overlay --diff` / preview | Real lifecycle defect: read-only diff path incorrectly invoked the post-apply LIFE cleanup worker without an actual apply receipt. | Gate cleanup on real apply, not preview/diff/validation-only/rebase/already-applied. Preview ZIP-preservation regression added. Focused local tests pass. |
| FRX06 rebase | Real lifecycle defect: internally generated temporary overlay was already consumed/removed inside rebase, but outer cleanup tried deleting it again. | Do not treat the caller's original rebase ZIP as an ordinary consumed apply artifact. Keep exact rebase safety and original-input retention regressions. |
| RM03 EXO lineage | Real structured-receipt defect: multi-command parent emitted interim `NodeFinished(status=running)` before its one genuine terminal event. | Use `NodeProgressChanged` for intermediate evidence, reduce typed fingerprints/output identities into parent metadata and retain single terminal. Targeted real workflow tests pass. |
| CLI synthetic fixtures | Tests invented successful `OperationResult`s with no actual cleanup receipt while requesting default consumed-source cleanup. | Keep default source-consumption contract; use `--keep-artifact` for synthetic mocks, and require preservation of unowned ZIP sidecars. Test no-clobber and preview behavior. |
| G8 post-G7 pinned amendments | Runnable historical-blob and post-G7 source-qualification assertions are obsolete after FC01–FC08. | Remove two stale G8 amendment test modules; rewrite live checks to show legacy source pins **fail closed** and current G8 report remains read-only. No historical receipt mutations and no false release qualification. |
| CLI help, generated guides and completion | Source parser gained real supported commands/options, golden/manual surfaces lagged. | Regenerate CLI help and both feature guides from current parser; test current structured help parity instead of requiring a frozen legacy ZSH table to cover every new command. |
| Wrapper and migration authority | Old test expected native registry alone; active `actions` wrapper is explicitly native. Adoption migration tool parses pre/post TOML as inert input, not authority for executing a project. | Correct the active test expectations; retain trust/authority tests. |
| DIST historical source SHA | Exported historical issue checksum erroneously treated as current source hash. | Retain historical checksum receipt, but do not pin today's module bytes to it. Candidate editable binding test is **not weakened**; tested with local real editable installation. |
| Disposable language-provider repos | CI `GITHUB_SHA` leaked from Devtool checkout into independent fixture Git repositories. | Strip host SHA only in fixture tests, preserving production HEAD/pin checks. |
| Hosted wheel and coverage | Hosted job omitted its own `setuptools`, `wheel`, and `coverage` build/coverage prerequisites; `--no-build-isolation` intentionally requires an installed build backend. | Install bounded dependencies in the 24-shard workflow. Local real offline wheel+coverage test passed; hosted fix **requires GitHub rerun**. |
| R03B destructive inventory | Current source introduced 16 files with 30 previously unreviewed primitive occurrences. | Reviewed every source occurrence; add explicit rationale and *exact expression/occurrence-count* inventories. Future added/changed calls cannot inherit the G1 approval. This is classification only, not an arbitrary-path deletion permit. Potential A08 concurrent pathname swap is a separately recorded hardening question. |

## Local evidence, bounded scope

- Broad focused regression after source/test updates: **263 passed, 1 failed** where the local inspection interpreter lacked editable-distribution `direct_url.json` metadata. This was an environment setup issue; installing this exact checkout with `pip install --no-build-isolation --no-deps -e .` changed the individual candidate runtime checks to **6 passed** without any production/test assertion weakening. The broad run preceded the final destructive-inventory and ZIP-retention regressions.
- Additional offline A06 wheel and RM08 coverage group: **15 passed**.
- Rebase/diff/current destructive-inventory dedicated cases: **7 passed**.
- This does **not** replace the required full 24-shard independent GitHub evidence. No device, remote provider, signing or release-qualification success claimed.

## Acceptance after source-bound installation

1. Apply exactly once on the expected clean committed tree using the bundled installer; it validates SHA-256 inventory, runs the native validator and creates one repository commit, or rolls back.
2. Trigger the manual 24-shard workflow on the resulting commit and retrieve the fail-closed consolidated summary for all shards. Do **not** suppress or relabel any current regression as historical without a written contract review.
3. If green, audit FC09/G1 against every remaining original promise and existing evidence gate. Hosted evidence never substitutes for native Android/Termux, provider, release or signing qualification.

## Candidate limitations

- This ZIP has **not** been applied on the user's Termux repo and has no independent hosted CI receipt.
- The G8 historical frozen pins remain available as documentary artifacts but are not executable current-source qualification.
- RM19 is cancelled; source that predates cancellation is not automatically deletion-safe simply because this remediation inventory recognizes it.

## R1 native validator follow-up: R1R2 replacement (2026-10-26)

- The first operator Termux attempt (`4d87cfbdde03419c8bd4770b7e4e221b`) reached validation, passed 42 tests, and failed `CliTests.test_batch_dry_run` because native validation's sanitized host identity became `host-android`. The output provided ends at `BLOCKED_G1_MANDATORY_TESTS:1`; it is **not a successful apply/commit receipt**, and rollback must be confirmed by the final receipt and actual Git state.
- The batch fixture now pins the supported **simulated** `desktop-linux` planner for a read-only dry run, and mocks the observed host identity to `host-android` on all platforms. This proves the fix handles the original failure without modifying production environment normalization or inventing Termux identity.
- The replacement artifact **restarts from the same exact clean FC08 commit/tree** and includes all R1 changes rather than requiring a partially applied or committed R1; it remains blocked until its full validator and the 24-shard GitHub run both pass.


## G1 R1R2 native validation timeout — R1R3 remediation (2026-10-26)

- **Evidence:** Native Termux run `ddf54a2fb05c4da384dbf22cd3f0b042` progressed through the current-contract mandatory suite without an assertion failure in its displayed output, then emitted `BLOCKED_G1_MANDATORY_TESTS:124`. Its R1R2 artifact validator imposed a 280-second `Popen.wait` deadline, explicitly sent SIGKILL to the pytest process group on expiry, and reported exit 124; the manifest's 390-second outer cap would independently reject some slow native Termux runs.
- **Classification:** `VALIDATOR_EXECUTION_BUDGET_BUG`. Native host resource/speed variance is not a current-contract test failure, and no test should be dropped or downgraded merely to fit a Linux CI clock budget. The user's output is not a completed native validation receipt; transaction/rollback confirmation and source cleanliness remain required before a replacement apply.
- **Correction:** Run the same 10 modules and pytest flags with output isolated in a temporary log; remove the premature inner kill, report elapsed time and bounded pytest-log progress every 30 seconds, return the complete test transcript after exit, forward interruption/timeout signals to the child process group, and raise the source-bound outer transaction timeout to 3600 seconds. Keep `-x` fail-fast and native rollback-on-failure. No test, fixture, CLI routing, or production safety behavior is relaxed relative to R1R2.
- **Acceptance:** Exact-source bundle validation, disposable clone positive apply with all 238 mandatory checks, forced-validator-failure rollback, and refusal of dirty/wrong source must be separately evidenced. Actual Termux pass and 24-shard hosted full regression remain open until independently observed.


## G1 R1R3 native argparse help styling — R1R4 remediation (2026-10-26)

- **Operator evidence:** Native Termux `a20dfbe470c144838a735e344221c3fc` reached 223 passes and 40 passing subtests, then `tests/test_arch_final_seal.py::test_golden_cli_help_contract` failed on the first `usage:` assertion because actual Python argparse help began with ANSI SGR styling (`\x1b[1;34musage:`).  This is NOT a passing native apply receipt, even though R1R3 correctly removed the earlier 280-second timeout.
- **Classification:** `CURRENT_TEST_FIXTURE_BUG`, cross-Python presentation variance. The contract is that the canonical Devtool schema emits the expected CLI commands, options and readable help body; terminal color is presentation, not a new CLI token. Python 3.14 can color argparse output by default under terminal color policy; a 2026 Linux/Python 3.13 fixture cannot assume that Termux emits unstyled text.
- **Correction:** Normalize *only ANSI SGR terminal styling* on rendered help in the active golden comparison, retaining exact body comparison and `usage: devtool` assertion. Add an executable ANSI-colored fixture regression. The production parser and generated golden are unchanged. All original R1R3 fixes, source hashes, cleanup/rollback guards, and ten-module mandatory selection are preserved.
- **Local acceptance:** 16 architecture final-seal tests passed; full mandatory selection 239 passed plus 40 passing subtests, no failures/skips, without pytest `-x`. The R1R4 native Termux validator and 24-shard hosted G1 gate remain OPEN until independently run.
