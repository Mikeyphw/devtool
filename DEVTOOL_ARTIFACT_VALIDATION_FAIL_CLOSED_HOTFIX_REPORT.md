# Devtool artifact validation fail-closed hotfix

Date: 2026-09-24
Base: post-FRX-X04 (`devtool_frx_x04_real_repo_adoption_final_reseal_v1` applied)

## Trigger

FRX-X04 was a required-validation overlay whose manifest declared:

- `validation.tasks = ["test"]`
- `validation.restore = false`
- `validation.build = false`
- `validation.test = false`
- a populated `validation.pytest_tests` selection

Single-overlay apply converted the phase booleans into skip flags before honoring the explicit task plan. The resulting apply performed no repository validation checks and then continued to the before-commit editable-install finalizer. The run summary consequently claimed tests completed successfully despite having no test-count evidence.

## Corrected contract

`validation.tasks` is the complete exclusive validation plan, matching the existing documented contract and bundle behavior. When a required artifact auto-enables validation and supplies non-empty manifest tasks, manifest phase-disable booleans no longer become skip flags that cancel the task plan. Explicit user CLI skip flags are still preserved and rejected by the existing task/skip conflict validator.

`validation.pytest_*` selectors remain invalid with `validation.test=false` only when there is no non-empty explicit task plan to own test execution.

`validation.tests` is now recognized as a valid standalone executable validation surface for required artifacts with built-in phases disabled.

## Fail-closed runtime guard

Immediate overlay apply and per-overlay bundle validation now emit a terminal failure with:

- result: `artifact-validation-required`
- failure kind: `required_validation_not_executed`
- issue code: `VALIDATION_NOT_EXECUTED`

when `validation.required=true` but the resolved lifecycle executes zero repository checks and zero artifact validation checks. Commit therefore cannot occur after a silent empty validation plan.

Reused shared validation in bundles remains valid evidence via its `shared_validation` metadata.

## Declarative `validation.tests` repair

A successful tests-only artifact previously had no success-primary result in `_artifact_script_result` and could reach an assertion. The aggregator now treats successful `validation.tests` results as real validation evidence and selects the last test result as the primary result when no script/finalize result supersedes it.

## Run-summary truth

When no parsable test totals exist:

- if structured result metadata proves a test action ran, summary says `Tests: completed successfully (counts unavailable)`;
- otherwise summary says `Tests: not run`.

The old unconditional `Tests: completed successfully` fallback is removed.

## Documentation

`docs/OVERLAY_VALIDATION.md` now explicitly records:

- manifest tasks supersede built-in phase-disable defaults;
- explicit CLI skip flags still conflict with an exclusive task plan;
- `validation.tests` may be the sole required validation path;
- required validation fails closed on zero executed checks.

## New regression coverage

`tests/test_artifact_validation_fail_closed_hotfix.py` covers:

1. the exact FRX-X04 manifest shape: disabled built-in phases + `tasks=["test"]` + pytest selectors;
2. tests-only required validation schema acceptance;
3. zero-check runtime guard failure;
4. runtime guard acceptance after a real repository check;
5. successful tests-only artifact aggregation;
6. truthful `Tests: not run` summary fallback.

## Validation evidence

Focused final gate:

- 89 tests passed
- 32 subtests passed

Broader artifact/validation lifecycle audit:

- 301 tests passed
- 32 subtests passed
- 1 test excluded only after reproducing the same failure on the untouched post-X04 baseline: `tests/test_validation_phase28.py::test_stop_control_targets_active_attempt_and_conflicts_are_safe` (existing expected-exit-code mismatch, unrelated to this hotfix)

`python -m compileall -q devtool`: PASS

## Hotfix artifact design

The hotfix overlay itself intentionally uses a manifest-driven test lifecycle:

- no `.devtool-artifact/validate.sh`;
- `validation.test = true`;
- focused `validation.pytest_tests` in the manifest;
- before-commit finalize retains the existing `pip install -e "$HOME/Code/devtool"` refresh with `devtool.egg-info` restoration.

`validation.tests` is not used by the hotfix artifact itself because the pre-hotfix process applying this overlay contains the successful-tests-only aggregation assertion being repaired. Dogfooding that exact surface before the new code is loaded would make the repair artifact self-blocking. `python -m compileall -q devtool` is therefore construction/replay evidence for this one bootstrap overlay; subsequent artifacts may safely use standalone `validation.tests`.

## Artifact replay

The packaged overlay was replayed against an exact fresh post-FRX-X04 source tree:

- artifact schema/inventory validation: PASS (when replay root is named `devtool`);
- source SHA-256 preconditions: 5/5;
- post-apply payload SHA-256 checks: 5/5;
- packaged manifest pytest gate: 89 passed + 32 subtests;
- `python -m compileall -q devtool`: PASS;
- ZIP integrity: PASS.

A full pre-hotfix-process `apply-overlay` simulation in the Linux sandbox reached Devtool's Python environment bootstrap but the bootstrap attempted to resolve build requirements from PyPI; DNS/network access is unavailable in this container. That environment limitation is not counted as successful lifecycle evidence. The user device's Termux environment already has the project toolchain and is the authoritative apply environment for the final artifact.
