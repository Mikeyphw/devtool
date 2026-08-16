# Devtool Cross-Project `--task` Selector Fix

## Problem

`devtool validate --task ...` was documented and implemented as an exclusive native validation selector, but the planner rejected every non-Android/Gradle target with:

```text
devtool: error: Validation --task is an exclusive Android/Gradle task selector. Project type 'python' does not provide native Gradle tasks.
```

That made `--task` work for Gradle task lists but fail for Python and other supported project types.

## Fix

- Kept Android/Gradle behavior unchanged: `--task` values are still exact Gradle tasks and still replace the built-in validation pipeline.
- Added runner-aware non-Gradle planning:
  - Python: `--task test`, `--task lint`, `--task build`, `--task package-check`, plus pytest/unittest test targets such as `--task tests` or `--task tests/test_cli.py`.
  - Node: action names such as `test`/`lint`, plus package-script test selectors such as `--task unit`.
  - Other non-Gradle runners: safe native validation action names are accepted through the existing runner planner.
- Preserved exclusive semantics: no restore/build/test/lint/package phase is appended unless explicitly selected.
- Added option-token rejection so values such as `--task=-Pfoo=true` produce a clear error instead of being mistaken for a validation unit.
- Prevented action names from leaking into framework test selection; for example `--task test --task lint` runs Node `test` once and Node `lint` once, not `test` plus `lint` as test-script selectors.

## Tests

Focused tests cover Python path targets, Python named validation actions, Python build-only selection, Python test/lint action leakage, Node script selectors, Node action/test-selection leakage, Android/Gradle option-token rejection, Rust native action planning, completion/manpage documentation, artifact validation task compatibility, and release-seal regressions.


## Re-audit update

A follow-up promise audit found that the implementation behavior was correct for the main Python/Android/Node paths, but the test suite did not explicitly cover every promised class of behavior. The v2 artifact adds targeted tests for Python `build`, Python `test` + `lint` action leakage, Android/Gradle option-token rejection, and Rust native `test` planning.
