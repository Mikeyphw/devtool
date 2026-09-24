# WRAP-02 — Generic Invoke CLI / Arbitrary-Action Plan Parity

## Scope

WRAP-02 exposes the generic action capability that already existed below the CLI without adding a new runner or execution engine.

Delivered:

- `devtool invoke ACTION`
- target/group/all-target/affected/variant selection on `invoke`
- workflow scheduler flags on `invoke`
- arbitrary repository-defined actions accepted by `devtool plan ACTION`
- canonical execution through existing `build_specs` / `build_workspace_specs`
- scoped single-run reservation derivation for `invoke ACTION`
- normal live progress/HUD eligibility for `invoke`
- Termux auto-chroot eligibility for `invoke`
- explicit preservation of the existing no-raw-passthrough contract for generic invocation
- dynamic zsh completion that merges standard actions with repository-declared workflow actions

## Architecture

No new executor was introduced.

```
devtool invoke codegen
        |
        v
existing project config / workspace selection
        |
        v
build_specs / build_workspace_specs
        |
        v
existing workflow DAG + scheduler
        |
        v
CommandSpec
        |
        v
existing EXO-backed execution path
```

Native commands remain native. WRAP-02 does not redirect `devtool build`, `test`, `validate`, etc. through `invoke`.

## Plan parity

The fixed `argparse` choice list on `plan ACTION` was removed. The planner can now expose the arbitrary workflow/action support already implemented by `plan_workspace_targets()`.

Examples:

```bash
devtool --target android plan codegen
devtool --target android invoke codegen
devtool --group services invoke regenerate --workflow-parallel
```

## Passthrough boundary

`invoke` is intentionally not added to `RUNNER_PASSTHROUGH_COMMANDS`.

Therefore:

```bash
devtool invoke codegen -- --stacktrace
```

is rejected rather than ambiguously distributing raw arguments over a possibly multi-node/multi-runner workflow.

Existing native passthrough remains unchanged, e.g. `devtool build -- --stacktrace`.

## Audit loop

### Pass 1 — execution semantics

Found and fixed a real safety delta: `invoke run --group ...` initially bypassed the existing multi-target long-running-process guard because the guard checked the literal CLI command (`invoke`) instead of the resolved execution action (`run`). The guard now uses the canonical action.

### Pass 2 — completion parity

Found and fixed a completion regression: the initial dynamic workflow completion replaced the previous standard action choices. Completion now merges the standard actions with repository-declared workflow actions and deduplicates them.

### Pass 3 — baseline regression comparison

A broad compatibility slice reported three failures in legacy `tests/test_cli.py` mocks whose `_maybe_commit_after_apply` lambdas do not accept the already-current `tasker_progress` keyword argument. The exact same three tests fail on the WRAP-01 v2 baseline before WRAP-02 changes, so they are inherited test debt rather than WRAP-02 regressions.

The final compatibility run deselected only those three proven-baseline failures.

## Verification

Focused WRAP-02 tests:

- 11 passed

Selected compatibility/audit suite:

- 269 passed
- 3 deselected (all three independently reproduced on the WRAP-01 v2 baseline)
- 8 subtests passed

Covered areas include:

- WRAP-01 repository wrapper schema
- target/job/workflow contracts
- workflow graph/runtime
- cross-target workspace groups
- safe scheduler and reservation planning
- CLI behavior
- Termux/chroot routing
- native runner passthrough compatibility

Static checks:

- `python -m py_compile` — PASS
- modified-file review — PASS
- no new runner/executor — PASS
- no generic secret/environment binding — PASS
- no shell expansion/eval introduced — PASS

## Validation cadence

WRAP-02 is not a roadmap validation gate. The overlay therefore keeps artifact validation disabled and is intended to be applied with `--no-validate`.

The before-commit hook still refreshes the installed Devtool with `pip install -e "$HOME/Code/devtool"`, while snapshotting/restoring `devtool.egg-info` so setuptools-generated metadata cannot violate Devtool's write boundary.
