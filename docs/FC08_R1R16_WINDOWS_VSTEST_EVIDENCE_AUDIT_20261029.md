# FC08 HOST-R1R16 — Windows VSTest/TRX and Coverlet evidence contract

**Review scope:** `Mikeyphw/devtool` `main`, source commit `a709fed9234e6b6277cb4d23a98ae77fff5ffcd4`, tree `8291c48b86101538b4140b6f9292e2621f77baa8`; GitHub Actions run [40959330051](https://github.com/Mikeyphw/devtool/actions/runs/40959330051) (2026-10-29).

## Evidence and diagnosis

- **OBSERVED:** Real DAG/Linux/macOS host jobs succeeded; real Windows job `123747841290` failed after a successfully returned .NET test command with `MISSING_TRX_OR_COVERAGE`. The Windows step's failure receipt was uploaded (artifact `12935980222`). It does not contain whether TRX or Cobertura was missing; previous source omitted successful test stdout from failure diagnostics.
- **OBSERVED:** The final summary job `123748494163` rejected the failed Windows job with `FAILED_OR_MISSING_GITHUB_HOST_JOB:windows` and did not issue a qualifying host summary. Correct fail-closed behavior. No evidence of a completed MSVC stage, pack, publish or negative-exit check from that failed run.
- **SUSPECTED, NOT PROVEN:** The `windows-2022` runner had .NET SDK 8, 9 and 10 installed. Without `global.json`, the fixture selected a floating newest SDK (10.0.403 at run time), and VSTest on `dotnet test` may have returned 0 with no discovered tests / missing results. The old source did not retain bounded stdout for successful test commands, so no claim of an established zero-test root cause is made.

## Promise checklist

| Promise | R1R16 change | State |
|---|---|---|
| Fix SDK drift in real Windows host fixture | Tracked disposable `global.json` specifies `8.0.100` with `latestFeature` (allows installed compatible 8.0.427 but not SDK 9/10); uses `dotnet new sln` without newer `--format` option | Source DELIVERED; real Windows OPEN |
| Fail explicitly if tests are not discovered | Tracked `.runsettings` requires `TreatNoTestsAsError=true`; still runs real xUnit test command with TRX logger and XPlat Coverlet collector | Source DELIVERED; real Windows OPEN |
| Reject missing / duplicate / invalid reports | One TRX and one Cobertura file required; namespace-aware TRX counters must prove at least one executed test, all passing; XML Cobertura must have positive valid-source lines | Source DELIVERED; fixture tests PASS; real Windows OPEN |
| Make the next failure explain why | Only the actual `dotnet test` command retains a bounded, redacted stdout/stderr tail on success; if files are missing, error includes report counts and bounded diagnostics; successful receipt retains only hashes | Source DELIVERED; hosted diagnosis OPEN |
| Preserve real dependency restore, build, pack, publish and MSVC | No replacement of real toolchain with mocks; all success-stage requirements and negative-exit propagation remain in `windows()` | Source DELIVERED; full real-host completion OPEN |
| Preserve identity and no false qualification | Existing checked SHA/tree, four jobs, source identity, checks SHA, no release/device-qualification and fail-closed summary untouched | Source DELIVERED; independent review OPEN |
| Protect against test regressions | Dedicated `tests/test_fc08_r1r16_windows_evidence_contract_20261029.py` plus existing FC08 tests; 87 targeted PASS locally | LOCAL PASS; native and G1 OPEN |

## Out of scope / remaining gates

- This artifact does **not** claim that SDK selection was the sole observed reason for missing files. The next real Windows job must prove that empirically. If it still fails, use its explicit `trx_count` / `coverage_count` and bounded `stdout_tail` / `stderr_tail` as the next causal evidence, without weakening requirements.
- Full 24-shard G1 on the new exact source commit, native Termux transactional application, BKX R3, device/consumer FC08, FC09 and release qualification remain OPEN. RM19 remains cancelled.
- The overlay is exact-source, clean-tree only, with preimage checks and transaction rollback on test failure; never reuse it on an unrelated commit.
