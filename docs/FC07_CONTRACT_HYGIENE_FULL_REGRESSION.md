# FC-07 — Registry authority and repository-wide regression evidence

**Scope:** RM00 exact reviewed eight-provider registry, provider admission negatives, global test discovery, 168-original-promise current-status authority, and triage of regressions discovered during broad execution. FC-08 real runner/consumer and FC-09 full seal remain separate.

## Delivered engineering changes

- RM00's two-provider stale exact-set test now asserts **all eight specifically reviewed IDs**, disallows unknown/reflection injection, and retains mapping immutability. This is not a self-referential set check.
- A native sharded inventory tool enumerates all tracked `tests/test_*.py` exactly once, runs pytest collection and individual bounded execution shards, records real exit codes and JUnit cases, records `TIMEOUT`/`FAILED`/`INCOMPLETE`, kills timed-out test process groups, and never elevates selected tests into whole-repository or hosted release authority.
- Root host-admission test expectations now reflect the actual secure refusal (`CI toolchain not provisioned by host`). Admission remains fail-closed.
- Fixed a local import shadow in `cli.py` which could turn federation usage errors into `UnboundLocalError` instead of an actionable refusal.
- Multi-spec EXO nodes now collect each successful part's finalized output proof in the parent before the final terminal aggregation; previously only the final part survived.
- Added a status cross-reference for RM00–RM20, retaining historical source audit files and an exact 168-promise planning ledger. RM19 remains cancelled, never reported as missing.

## Actual validation, not blanket full-suite PASS

- Full single-process 589-module collection stalled on the test host: **incomplete**, not PASS.
- Twelve independently bounded collection shards **all exited 0**; total **589 tracked test modules and 6,737 pytest items collected**.
- Focused RM00+FC07: **25 passed**.
- FC01–FC06 compatibility + generated CLI: **123 passed**.
- Previously failing RM11 host-admission, EXO multi-spec and federation CLI group: **46 passed**.
- Execution shard 0, before triage: **557 passed, 6 failed, 5 skipped**. The six were four outdated host-message expectations, one multi-spec output aggregation defect, and one federation CLI shadowing defect.
- Execution shard 0 after those repairs **printed** **562 passed, 1 failed, 5 skipped**, but its wrapper did not return a final machine exit receipt (INCOMPLETE). Remaining `test_run_specs_prefers_maven_report_over_conflicting_stdout` emitted `stdout` despite a current Surefire fixture. It **passes alone**, so further suite-interaction/time-bound evidence is required. This is NOT marked resolved.
- Execution shards 1–11 are **NOT_RUN** as test execution; they have passing *collection* evidence only. Test collection and execution are distinct gates.
- All output is source-bound to the local candidate and an exact input Git tree. **Linux test success does not establish Termux/Windows/macOS/consumer/device/release trust.**

## Further action

1. Finish executing all twelve shard receipts on stable CI/Termux, triaging any failures instead of disabling them. Include documented optional dependency and OS skips.
2. Investigate Surefire report/source timing under long-running shard conditions without widening trust to accept stale reports; preserve negative stale-report tests.
3. FC-08 gathers actual external provider/consumer evidence. FC-09 applies full promise-by-promise final gating, not inferred from this report.
