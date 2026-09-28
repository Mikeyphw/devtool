# TEST-X01 — Failure Trustworthiness / Process-State Isolation

## Scope

TEST-X01 establishes trustworthy failure attribution before the wider test-system campaign. It fixes production transaction/validation repository-root leakage, adds a reusable process-state guard and isolated in-process CLI harness, introduces concise structured pytest/JUnit summaries and failure-cluster foundations, moves marker policy to static strict pytest configuration, and preserves raw failure evidence while linking generated-surface drift to a root diagnostic.

This is an implementation overlay, not TEST-G1. It therefore uses bounded targeted **non-Gradle** validation only. No Gradle task is pulled forward from the milestone gate.

## Delivered

- Added `devtool.process_state` with:
  - `ProcessStateGuard` capture/diff/restore semantics;
  - selected Devtool/XDG/Python/environment tracking;
  - cwd, runtime log-dir override, and selected signal-handler restoration;
  - secret-safe process-state diagnostics;
  - nested environment-key preservation for transaction/validation repository roots.
- Wrapped `devtool.cli.main()` so repeated in-process invocations restore process-global Devtool state like independent processes do.
- Wrapped direct overlay, overlay-bundle, and snapshot apply entry points so transaction/validation repository-root variables cannot poison later operations.
- Stopped final-log rendering from creating a persistent `DEVTOOL_RUN_ID` merely to print a summary.
- Added a canonical autouse pytest process-state guard that attributes leaks to the creating test, restores state before the next test, and preserves an already-failing product assertion as the primary failure.
- Added an isolated `devtool_cli` fixture for repeated in-process CLI calls.
- Added concise pytest defaults (`--tb=short -ra`) and static strict marker/config policy in `pyproject.toml`.
- Added `devtool.test_reporting` foundations for:
  - bounded repr/diff helpers;
  - structured JUnit-derived summaries;
  - failure clustering;
  - generated-surface root-drift linkage;
  - machine-readable `DEVTOOL_TEST_SUMMARY` output.
- Integrated structured pytest summary metadata/output into runner result handling.
- Updated the existing transaction-worktree contract test so repository-root state is expected to be scoped rather than leaked globally.
- Updated the existing pytest revitalization source contract to treat `pyproject.toml` as the fail-closed marker/config authority.

## Stop → pause causal finding

The roadmap carried the stop→pause `control_unavailable` failure as an independent product regression after earlier cascade cleanup. Replaying it on the untouched base still produced the generic exit-1 failure. With TEST-X01 process-state isolation applied, the same control sequence returns the expected stable exit code and typed reason.

That means the old attribution was incomplete: the stop→pause symptom was itself caused by in-process repository/run-state contamination. TEST-X01 does not add a separate control-plane feature fix; it removes the poisoning root cause and locks the recovered behavior with a regression test.

A second baseline failure, `ExclusiveValidationTaskTests::test_sequential_resume_restores_exclusive_task_list`, is likewise recovered by the isolation boundary.

## V3 artifact-transaction isolation correction

The V2 artifact applied its code successfully but its targeted tests ran inside Devtool's own live artifact transaction. Pytest inherited `DEVTOOL_TRANSACTION_REPO_ROOT`, `DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT`, and `DEVTOOL_VALIDATION_STATE_REPO_ROOT` from that outer apply. Temporary-repository validation tests were therefore redirected into one shared live validation-session namespace, producing cascading `already active` failures; TEST-X01 assertions that expected transaction-root variables to be absent were affected by the same parent context.

V3 makes the pytest runtime boundary explicit: ordinary tests mask outer artifact transaction/validation roots (plus their transaction-mode companions) while retaining the production transaction environment outside pytest. A dedicated nested-pytest regression launches with synthetic outer transaction roots and proves separate temporary validation sessions remain isolated.

## Targeted validation evidence

Focused non-Gradle TEST-X01/affected-family checks under a simulated outer artifact transaction:

- TEST-X01 + isolated-worktree trust boundary: **28 passed**.
- transactional-safety / transactions / archive-rollback / implicit-bundle boundary: **58 passed**.
- validation-session / validation-reporting / pytest-policy boundary: **25 passed**.
- Total packaged targeted boundary: **111 passed**.
- Explicit validation-control stop→pause checks: **2 passed**.
- `python -m py_compile` for all changed Python modules/tests: passed.
- `git diff --check`: passed.

Full `tests/test_cli.py` delta comparison:

- TEST-X01 candidate: **133 passed, 4 failed, 8 subtests passed**.
- Untouched `064d21e` baseline: **132 passed, 5 failed, 8 subtests passed**.
- The four candidate failures reproduce on the untouched baseline with the same pre-existing causes (`tasker_progress` mock signature and interactive-copy/config bug); TEST-X01 introduces no new CLI failure.
- The one baseline-only failure is the validation-session contamination recovered by TEST-X01.

## Validation policy

The packaged overlay requests only targeted Python/pytest validation. It intentionally does not request Gradle/Android validation; that remains reserved for TEST-G1 and later authoritative gates under the V18 policy.

## Next boundary

Apply TEST-X01, then run **TEST-G1** as a separate isolation/trust milestone. TEST-X02 remains after that gate per the authoritative dependency-driven implementation order.
