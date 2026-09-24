# WRAP-04 EXO Invocation-Origin Contract Report

## Scope

Implements the run-level EXO invocation origin contract required before wrapper execution is enabled.

## Delivered

- Added persistent run-level invocation fields to `ExecutionRunState` and `ExecutionSnapshot`:
  - `invocation_source`
  - `invocation_command`
  - `invocation_metadata`
- Bumped execution snapshot schema from 5 to 6.
- Reduced invocation origin from `RunStarted.source` and/or explicit `RunStarted.metadata`.
- Added hidden internal CLI provenance transport:
  - `--internal-invocation-source`
  - `--internal-invocation-command`
  - `--internal-wrapper-schema`
- Preserved ordinary CLI runs as `invocation_source = cli` when emitted through the CLI path.
- Preserved canonical command identity separately from wrapper-facing invocation intent.
- Added focused tests for reducer persistence, backward-compatible empty legacy state, hidden CLI transport, and unsafe-token rejection.

## Audit loop

- WRAP-04 focused tests: 4/4 passed.
- WRAP-01 + WRAP-02 + WRAP-03 + WRAP-04 with rebased EXO P0 hotfix: 58/58 passed.
- EXO cross-surface regression slice: 102/102 passed.
- Broader config/workflow/completion/chroot slice: 258 passed, with 3 inherited `test_cli.py` mock-signature failures reproduced from the current baseline and unrelated to WRAP-04.
- `py_compile` passed for touched modules.

## Boundary

WRAP-04 does not enable wrapper execution. It only establishes persisted execution truth and safe non-secret transport so WRAP-05 can dispatch wrapper-resolved canonical commands without losing provenance across remote delegation.
