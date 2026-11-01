# R39-R2 — Legacy/native bounded serial parity

## Scope and exact execution authority

This stage tests real subprocess behavior using *the same fixed Python command
specifications* through `devtool.runners.run_specs` (legacy sequential path)
and `devtool.workflow_scheduler.run_workflow_specs` (native dependency-ready
scheduler). Native executes via the worker route (`parallel_requested=True`,
`max_parallel_nodes=1`), **not** its direct legacy fallback. No runtime routing
is changed. All commands execute in short-lived, lane-separated directories;
no toolchains, devices, services or remote repositories are invoked.

The manually dispatched `devtool-r39-execution-parity.yml` validates both the
source SHA and a complete evidence matrix, and uploads machine-readable JSON.
Use `python -m devtool.workflow_parity --json` for an identical local report.

## Five source-controlled scenarios

| Scenario | Legacy expected A/B | Proved subprocess side-effects |
| --- | --- | --- |
| `pass_chain` | passed / passed | a and b |
| `dependency_block` | failed / blocked | a only |
| `independent_fail_fast` | failed / skipped | a only |
| `continue_on_error` | failed / passed | a and b |
| `pre_cancelled` | cancelled / cancelled | neither |

Evidence compares *ordered* discovered/start/terminal events, actual exit codes,
workflow states, failure kinds, skipped flags, stdout, and actual filesystem
side effects. Durations, log paths, run IDs and platform-dependent metadata
are deliberately excluded. Missing cases, missing lanes, incorrect scenario
outcomes, unexecuted or extra subprocesses, malformed event sequences,
source mismatch (in CI), or unsupported qualification all fail closed.

## Non-equivalence outside this matrix

Native **parallel** workers can legitimately launch an independent node before
a sibling fails; a sequential legacy runner cannot. That difference is not
a defect or evidence of parallel parity. The matrix does **not** compare
multi-part nodes, historical reuse/materialization, different provider or
execution environment behavior, signal delivery/orphan-process recovery,
Gradle/Android host runs, device access, network/SSH, signing or releases.

This is evidence **toward** migration, not sufficient evidence to retire the
legacy runner, enable native scheduling by default, qualify release, or remove
rollback paths. Those decisions require separate qualification and explicit
retirement gates under R39-R3/R39-R4 and NEXT-11.

## Acceptance

1. Source-pinned overlay transaction passes targeted test script with zero warnings.
2. Full manual 24-shard regression passes against the pushed new Git SHA.
3. Manual R39-R2 parity workflow passes on that same SHA and uploads JSON.
4. JSON retains all `qualification` boundaries, especially
   `legacy_retirement_authorized: false`.

Only (2) and (3) can close the corresponding hosted evidence gates; the native
transaction test alone cannot.
