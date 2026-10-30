# FC08 HOST-R1R23 — version-aware Cargo frozen-lockfile refusal

## Source and observed failure

- **Exact base:** `Mikeyphw/devtool` main `8a356a9990e65f7c8ed6475bce3e4be204aab36e`, tree `928d3d01d32b7cae206301bb770b60b14e2dbed8`.
- **Real hosted evidence:** [FC08 run 41105648320](https://github.com/Mikeyphw/devtool/actions/runs/41105648320), Linux job `124234835614`, failed with `FC08_CARGO_REFUSAL_NOT_LOCKFILE_DRIFT`. DAG, Windows and macOS passed. The four-host inventory correctly rejected Linux (`FAILED_OR_MISSING_GITHUB_HOST_JOB:linux`).
- **G1 same source:** [run 41105654867](https://github.com/Mikeyphw/devtool/actions/runs/41105654867), 24/24 shards, 6,891 tests, 0 failures/errors/skips.
- **Diagnosis:** R1R22 required the literal English phrase `needs to be updated` in Cargo's refusal. Recent Cargo versions can use `cannot update the lock file ... because --locked/--frozen was passed to prevent this` instead. The original stderr was not included in the failure receipt, so the *precise* hosted text has not been independently recovered. Reference: https://latchkey.dev/learn/failures/cargo-lock-needs-update-in-ci ; official semantics: https://doc.rust-lang.org/cargo/commands/cargo-build.html.

## Promise-by-promise audit

| Promise | R1R23 implementation | Evidence / disposition |
|---|---|---|
| Accept legitimate old and new Cargo lockfile refusal diagnostics | Strict anchored classification for `the lock file ... needs to be updated but --locked/--frozen was passed` and `cannot update the lock file ... because --locked/--frozen was passed`. Supports original older simplified fixtures and harmless ANSI codes. | **Implemented / regression PASS; real post-R1R23 Cargo OPEN** |
| Reject other failures, not just any nonzero Cargo exit | Requires exactly one Cargo `error:` line matching an explicit lockfile-update refusal and named restrictive flag; compilation, manifest parse, permission, registry/network, fabricated or multiple errors rejected. | **Implemented / negative regression PASS** |
| Preserve native lock/source boundaries | Keeps R1R22's real `cargo build --frozen` before/after, new owned local path dependency, nonzero refusal requirement, unchanged original lock SHA, manifest restoration, temporary fixture cleanup. | **R1R22 behavior retained / hosted OPEN** |
| Provide useful safe diagnostics when an unrecognized error appears | Failure includes limited primary Cargo error with path and secret redaction, failure category, exit code and content digests; success includes stable diagnostic category and digest only, no raw stderr or paths. | **Implemented / negative redaction tests PASS** |
| Avoid changing unrelated provider and workflow policy | Only Cargo diagnostic logic, new tests, this audit and cumulative ledger changed. DAG (5), Linux (14), Windows (8), macOS (3) remain exact; no token/permission, workflow, release or receipt gate changes. | **Source retained** |
| Validate local regressions and transactional application | All `tests/test_fc08_*.py` pass locally; exact-base Devtool installer required with clean source and lifecycle validation; rollback on failure. | **Local tests: 158 PASS independently. Devtool installer runs the R1R21/R1R22/R1R23 Cargo contract subset to avoid unrelated slow host-fixture tests stalling a transaction; hosted full G1 remains mandatory.** |
| Final hosted qualification | Need a new run on the post-R1R23 source with all four lanes + fail-closed inventory passing and a same-SHA 24-shard G1. | **OPEN until GitHub evidence** |
| Broader formal qualification | Native Termux/ARM64, Android signer/device, real BKX/consumer, repeated/successful dispatch, independent FC09/release authority. | **OPEN; no promotion** |

**No claims of a live Cargo fix or a release seal from local mocks.** If GitHub still rejects, inspect the newly bounded, sanitized failure excerpt and refuse ambiguous causes rather than expanding the accepted diagnostic vocabulary speculatively. The overlay must not rewrite existing user changes, stashes or consumer repositories.
