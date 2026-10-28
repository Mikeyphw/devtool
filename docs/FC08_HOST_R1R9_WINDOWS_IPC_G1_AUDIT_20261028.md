# FC08 HOST-R1R9 — Windows IPC compatibility and G1 R03B coverage

## Source and evidence

- Base: `main` `4699bd031290c3756ae6184326b0f4aed2f1de4e`, tree `67b4bc102535c741ae6e0e56a5a1a6fcf4d2c1e1`.
- FC08 run [40625517193](https://github.com/Mikeyphw/devtool/actions/runs/40625517193): DAG/Linux/macOS pass; Windows fails with `socket.AF_UNIX` attribute missing. Consolidated fail-closed summary FAIL.
- G1 [40625521285](https://github.com/Mikeyphw/devtool/actions/runs/40625521285): 23 of 24 shards PASS, shard 20 fails because a new Go spool file contains two unclassified destructive callsites.

## Promise-by-promise audit

| Promise | Status | Evidence / limitation |
|---|---|---|
| Runtime capability check when Unix sockets unavailable | Source implemented | Tests force `AF_UNIX=None`; real hosted Windows not yet rerun |
| No fabricated broker/IPC cancellation authority | Source implemented | Unsupported actions, cancel, broker and publish return false |
| Desktop IPC and clipboard socket absence safe | Source implemented | Explicit unavailable responses, no socket creation |
| Preserve Unix IPC broker/publisher behavior | Local tests passed | Existing `tests/test_ipc.py` runs, hosted regression open |
| Preserve tasker state-file and canonical run receipts | Source unchanged | Local tasker regression passed; Windows user observation still open |
| R03B spool-owned destructive calls classified exactly | Source implemented | Two exact expressions with scoped rationale; test remains fail-closed |
| No suppression of G1 failures | Source implemented | R03B still scans all production Python modules |
| Real Windows .NET and MSVC workloads | OPEN | Must rerun FC08 on new commit; do not claim results |
| Full G1 24-shard regression | OPEN | Must rerun on new commit; do not claim results |
| Device, consumer, BKX R3, FC09 | OPEN | Independent external authority required; RM19 cancelled |

The summary's label `FAILED_OR_MISSING_GITHUB_DAG_JOB` is misleading when Windows alone fails. Keep fail-closed summary semantics; separately repair reporting fidelity if desired.


## R1R10 native-transaction correction (2026-10-28)

Native Termux validation failed R1R9 in the **test fixture**, not in the proposed Windows IPC or Go spool production changes. The positive Android-bridge test had no explicit control over inherited `DEVTOOL_NO_HUD`, `DEVTOOL_NO_AUTO_HUD`, and `DEVTOOL_AUTO_HUD_CHILD`. Its mocks of Android availability did not override production's intentionally stronger hard-disable guard. The absence of environment isolation allowed a real outer validator to short-circuit this positive path.

R1R10 tests force each guard **off** only inside the two mocked Android bridge cases, preserve the production disable test, and add regression coverage with all guards intentionally true in the parent. Local negative-case reproduction and corrected suite are documented in the bundle. Existing R1R9 implementation/claims are retained. Genuine Windows hosted execution, the 24-shard G1 run, native Termux acceptance and FC09 remain **OPEN**.
