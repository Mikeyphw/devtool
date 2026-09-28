# TEST-G1 — Isolation / Trust Milestone

## Purpose

TEST-G1 is the first authoritative test-system gate after TEST-X01. It does not add TEST-X02 inventory/fixture/selection work. It proves that the failure-trustworthiness foundation can be relied on before wider fixture sharing, sharding, parallelism, or broad campaign interpretation.

## Gate boundary

The gate consumes TEST-X01 as a prerequisite and validates only the Python/pytest, Devtool CLI/artifact, validation-session, and failure-reporting boundaries changed by TEST-X01.

No Gradle-producing or Gradle-executing implementation landed in TEST-X01, so there is no deferred Gradle validation belonging to this checkpoint. TEST-G1 therefore intentionally runs no Gradle task.

## Authoritative proofs

1. **Deliberate leak attribution at source** — a child pytest run intentionally leaks a `DEVTOOL_*` environment variable. The TEST-X01 autouse `ProcessStateGuard` must identify the exact leaking test and fail that test's teardown.
2. **State restored after a failed/leaking test** — the next test in the same child pytest process must pass and observe the leaked variable as absent.
3. **Validation family order independence** — the validation truth/session and validation-report families must pass in both forward and reversed file order in fresh child processes.
4. **CLI/artifact poisoning eliminated** — the representative sequence that reproduced the TEST-X01 V2 Termux failure (repeated in-process CLI, snapshot runner, isolated transaction worktree, then validation-session/report tests) must pass in one process.
5. **Generated drift has one root cluster** — two generated-surface failures must map to one `generated-surface:cli-contract` root cluster.
6. **Raw outcomes remain queryable** — clustering must not remove any of the three synthetic raw failed test outcomes.
7. **Stop→pause remains independently observable** — the roadmap originally expected this regression to remain failing until separately fixed. TEST-X01 proved the symptom was itself caused by process/repository-root contamination and restored the typed `control_unavailable` behavior. G1 therefore preserves it as a dedicated standalone regression check and also verifies that it remains an independent cluster beside generated-surface drift.

## Fresh-process validation layout

The overlay executes separate required validation processes for:

- the TEST-G1 proof module;
- the TEST-X01 trust + transaction boundary;
- validation truth/reporting order and state isolation;
- the dedicated stop→pause typed-control regression.

The gate uses Devtool-native required validation with rollback on failure. The editable-install refresh remains a separate `before_commit` finalizer so the committed runtime matches the candidate that passed the gate.

## Closure condition

TEST-G1 is closed only when all required gate validations pass from the transaction candidate. A failure rolls back the gate overlay. Passing TEST-G1 permits the implementation order to advance to TEST-X02; it does not constitute TEST-G2 or the final TEST-G3 seal.
