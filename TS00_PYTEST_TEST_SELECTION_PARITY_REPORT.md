# TS00 — Pytest Test Selection Parity

## Scope

TS00 makes exact pytest selection a first-class Devtool validation capability,
with the same core promise users already expect from explicit Gradle task
selection: when tests are explicitly chosen, Devtool does not silently broaden
the pytest run back to the full configured or affected suite.

## Delivered behavior

- Repeatable `--pytest-test` supports files, classes, functions, methods, and
  parametrized pytest node IDs.
- Explicit pytest-test mode suppresses default/configured/affected pytest
  targets unless `--pytest-include-defaults` is supplied.
- `--pytest-marker`, `--pytest-k`, and repeatable `--pytest-arg` remain
  structured argv and never require shell command construction.
- `[python.pytest]` repository defaults and profile-level pytest settings feed
  the same selection model.
- Schema-v2 artifact validation accepts structured pytest selection fields;
  deferred validation persists them and explicit CLI fields retain precedence.
- PythonRunner owns pytest collection/execution while the generic selector
  layer owns normalized selection state.
- Native `pytest --collect-only -q` discovery preserves executable and
  parametrized node IDs, with source-catalog fallback if runtime collection is
  unavailable.
- The terminal validation cockpit can refresh collection, multi-select exact
  pytest nodes, configure marker/keyword/argv filters, and deliberately opt
  back into configured/default targets.
- The GTK command model and Control Center expose dedicated pytest fields using
  the same CLI semantics.
- Completion, generated configuration guidance, README, manual, and dedicated
  test-selection documentation cover the new interface.
- Structured diagnostics distinguish missing pytest selectors and no-test
  collection from generic command failures.

## Compatibility

Existing generic selectors (`--id`, `--class`, `--method`, `--path`, profiles,
changed/failed selection) remain supported. Existing projects that do not use
new pytest-specific selectors retain their normal configured/default pytest
behavior.

## Validation target

TS00 is intended to be applied with `--no-validate` as the first overlay in the
larger workspace/UI modernization campaign. Focused Devtool regression tests
are then run explicitly; full repository validation remains reserved for the
final seal overlay in the campaign.

## Regression evidence

The completed TS00 implementation passed the focused and adjacent regression
surface in two non-overlapping groups:

- 229 tests + 8 subtests: CLI, validation sessions/control, and interactive
  validation cockpit compatibility.
- 201 tests + 32 subtests: TS00 contracts, generic test selection, config,
  diagnostics, Python support, selection/detection contracts, validation
  resource precedence, completion/manpage, desktop command model, and artifact
  v2/inspection behavior.

Total validated scope: **430 tests + 40 subtests passed**.

A native unmocked pytest collection smoke check also collected parametrized
node IDs (`test_p[1]`, `test_p[2]`) through the configured Python runner.
