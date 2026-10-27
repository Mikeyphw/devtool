# RM-02 promise-by-promise source and behavioral audit

Target: `devtool`; baseline: exact post-RM-01 Git tree
`cbc5613c2b665077475d226405dca301b940ced1`.

The word **PASS** means a runtime assertion, not simply a source path existing.
No external qualification gate is used as a pass criterion.

| # | Promised implementation behavior | Verification | Status |
|---|---|---|---|
| 1 | Remove static wave barriers during execution | `test_successor_starts_before_unrelated_long_sibling_finishes` asserts C's subprocess starts before still-running B finishes | PASS |
| 2 | Bound active workers, retain deterministic admission order | `test_safe_independent_nodes_really_execute_concurrently` uses two real subprocesses and a peer-marker barrier; a serial executor fails. Actual start order is A,B,C in #1; `max_parallel_nodes=2` | PASS |
| 3 | Honor dependency outcomes, no failed-dependent execution | `test_continue_on_error_runs_independent_but_blocks_descendant` asserts C blocked and no start marker | PASS |
| 4 | Preserve fail-fast while draining in-flight work | `test_fail_fast_drains_running_and_blocks_unscheduled` uses explicit A→B-start→C-blocked→B-release synchronization; `test_unharvested_failure_is_processed_before_new_admissions` forces a stale wait snapshot; `test_fail_fast_does_not_revoke_a_node_admitted_before_failure` tests the complementary legal ordering | PASS |
| 5 | Preserve continue-on-error independent progress | `test_continue_on_error_runs_independent_but_blocks_descendant`: B passes despite A failure | PASS |
| 6 | Prevent conflicting write/write and read/write overlap | `test_shared_output_reservation_prevents_overtaking_active_writer`; preexisting `test_mp09_mp10_safe_scheduler_plan.py` | PASS |
| 7 | Retain shared/global reservations and tool resource caps | `test_reserved_global_resource_serializes_disjoint_writers`, `test_gradle_resource_cap_blocks_overlap_without_path_collision` | PASS |
| 8 | Cancellation stops admission and produces synthetic states | `test_cancellation_prevents_unstarted_commands`, `test_cancel_during_running_node_prevents_later_successor` | PASS |
| 9 | Worker errors become terminal, not queue deadlocks | `test_worker_exception_is_terminal_and_does_not_deadlock` | PASS |
| 10 | Preserve dry-run/no-op determinism | `test_dry_run_remains_sequential_and_non_mutating` | PASS |
| 11 | Preserve stable ordered result and raw-output projection | `test_successor_starts_before_unrelated_long_sibling_finishes` checks result order; preexisting scheduler regressions | PASS |
| 12 | Show actual order separately from static plan waves | #1 checks actual start/finish and `runtime_scheduling.mode`; pre-existing plan tests assert waves unchanged | PASS |
| 13 | Retain RM-01 typed output and cleanup functionality | `tests/test_rm01_output_scopes.py` exercises temp provisioning, hashing, cleanup and reservation conflicts | PASS |
| 14 | Retain RM-00 provider and host contract functionality | `tests/test_rm00_shared_contracts.py` and native in-process `devtool` target graph validation | PASS |
| 15 | Source-bound, clean-branch target-only overlay and transactional rollback | Exact Git tree + SHA-256 preflight, native artifact validator, disposable installation, wrong-tree rejection | Installer validation required |

**Boundary:** RM-03's remote correlation/EXO identity unification is **not**
claimed by RM-02. Running commands can still have undeclared filesystem side
effects; reservations are not an operating-system sandbox. This audit does not
claim live device, remote runner, or end-to-end hosted CI qualification.

## Termux incident and adversarial proof (2026-10-21)

The first RM-02 artifact failed mandatory validation on Termux because A
(`sleep(.10)`, failure) did not necessarily finish before B (`sleep(.30)`,
success). D's `passed` result was permitted if D was admitted after B but
before A's failure was observable. That test was a timing assumption, not a
valid assertion of the fail-fast contract.

The repaired tests use file-based handshakes to establish both causal orders.
A third test injects an out-of-date `wait(FIRST_COMPLETED)` snapshot in which
B is harvested first and A is terminal but unharvested. On the original
scheduler this test demonstrably FAILS (D incorrectly admitted after terminal
A); on the repair it PASSES (A is harvested first, D skipped). The patched
engine now checks for completed workers before admitting new jobs and while
scanning the queue. This is a behavioral engine fix, not a loosened assertion.

The source baseline remains the exact clean post-RM-01 tree; failed RM-02
transaction rollback is a supported starting point. Mandatory target-plan
validation and the same broad regression matrix remain enabled.

## Termux incident 2: eliminate fragile elapsed-time concurrency assertions

- **Observed on device:** the pre-existing MP09/MP10 test failed at 1.031s
  because it required a full run (including process startup) under 0.82s;
  both subprocesses printed successfully and the test did not establish
  whether they overlapped. RM-02 was rolled back cleanly.
- **Root cause of the assertion:** the test interpreted wall-clock total duration
  of two nominal `sleep 0.45` jobs as proof of concurrent scheduling. Termux
  startup, worker admission, and host contention are not bounded to 0.82s.
- **Repair:** replace the threshold with two actual child Python processes,
  each writing an `out/started` marker and requiring the other's marker before
  completion. If the scheduler serializes, the first process cannot complete
  successfully and the test fails after a bounded 20s deadline. No global
  runtime threshold is used.
- **Unchanged engine:** the prior repaired dependency-ready scheduler,
  completed-future fail-fast harvesting, reservation arbitration and results
  are identical to the previous RM-02 race-repair release.
- **Proof:** repeated peer-barrier executions; mandatory MP09/MP10 and RM-02
  suites; native in-process `devtool` target validation; exact-tree preflight,
  deliberate negative source rejection and transactional disposable installation.

This is an implementation/test correctness fix, not an external qualification
claim or relaxation of target validation.
