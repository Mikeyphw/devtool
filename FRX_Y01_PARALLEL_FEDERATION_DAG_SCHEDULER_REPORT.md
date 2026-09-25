# FRX-Y01 — Parallel federation DAG scheduler

## Scope

FRX-Y01 is implemented as a standalone non-gate overlay. It is intentionally not merged with GIT-FRX-02/GIT-FRX-03 because those items introduce durable Git recovery/transaction state and isolated-worktree semantics, which are a separate high-risk ownership and fault-isolation boundary.

The implementation keeps the existing sequential federation coordinator as the default. Parallel federation execution is opt-in through `devtool federation run <workflow> --parallel`, `devtool federation run --group <group> --parallel`, or `--max-parallel N` (which implies parallel execution).

## Roadmap promise traceability

- **Reuse the existing scheduler instead of creating a second policy engine.** `workflow_scheduler.build_safe_schedule_waves()` is now the shared dependency/scope/resource scheduling kernel. Existing local workflow planning delegates to it; the new federation scheduler compiles child workflow scopes into `WorkflowScheduleNode` values and delegates to the same kernel.
- **Run only dependency-ready children.** Federation steps retain `depends_on` edges and are emitted in deterministic waves. A join is not scheduled until both prerequisite children reach terminal success.
- **Conservative cross-repository resource classes.** Federation scheduling projects `cpu-heavy`, `gradle-heavy`, `dotnet-heavy`, `device-exclusive`, `network-heavy`, and `generic-read-only`. Legacy `gradle`/`dotnet` remain compatible aliases.
- **Global reservation safety.** Explicit child `global_reservations` are preserved. `device-exclusive` and `network-heavy` receive global resource reservations. Identical/nested enrolled repository roots receive a shared repository-overlap reservation. Conflicting reservations cannot share a wave.
- **Bound unknown child writes by repository.** Unknown child write sets reserve the enrolled repository root rather than globally serializing unrelated repositories.
- **Deterministic cancellation/failure truth.** A cancelled child requests cancellation of active siblings immediately and unscheduled dependants become terminal `cancelled`. Failed prerequisites leave dependants terminal `blocked` without invoking them.
- **Parent EXO scheduler truth.** Parent federation nodes persist `queued`, `running`, and terminal scheduler states, wave identity, and the compiled scheduler node contract in node metadata.
- **Parallel child state isolation.** Each child subprocess receives a unique `DEVTOOL_TASKER_DIR` under `federation-children/<parent>/<child>` and `DEVTOOL_NO_AUTO_HUD=1`, preventing concurrent children from clobbering singleton HUD/tasker projection files. Persisted child `execution.json` state is reloaded for lineage/identity authority checks.
- **Scheduling decisions as machine evidence.** `FederatedRunReport.schedule` exports the scheduler nodes, waves, resource/reservation decisions, limits, and serialization notes. `federation run --json` exposes this report.
- **Workflow and group parity.** Executable federation groups use the same federation workflow scheduler path; there is no separate group scheduler.
- **Backward compatibility.** Without `--parallel`/`--max-parallel`, `run_federated_workflow()` uses the existing sequential implementation.
- **CLI/completion contract.** `federation run` exposes `--parallel` and positive `--max-parallel N`; the checked-in zsh completion mirror is synchronized with the runtime completion constant while retaining the existing group wording contract.

## Native roadmap contract

`.devtool.toml` now declares first-class test `roadmap-frx-y01` with unique JUnit evidence at `.devtool/reports/roadmap/roadmap-frx-y01.xml`.

`roadmap-frx-y01` runs:

1. `job:source-syntax`
2. `test:roadmap-frx-baseline`
3. `test:roadmap-frx-y01`
4. `test:roadmap-workflow-exo-baseline`

`roadmap-current` also contains `test:roadmap-frx-y01`. The ROADMAP-00 machine-contract regression has been advanced to require the new declared test and six-node current roadmap graph.

## Audit loop

The implementation was audited in bounded groups so a future regression has an identifiable failure surface.

- Source compilation: `python -m compileall -q devtool tests` — PASS.
- FRX-01..FRX-08 regression group — **74 passed**.
- FRX-09..FRX-X03 regression group — **80 passed**.
- FRX-X04 + FRX-Y01 + shared scheduler regression group — **63 passed**.
- ROADMAP-00 native contract + checked-in completion synchronization — **5 passed**.
- Total across those disjoint regression/contract groups — **222 passed**.
- The declared `roadmap-frx-y01` test was independently replayed with its configured JUnit destination — **18 passed**, JUnit produced.

The first broad audit found one backward-compatibility failure: the new zsh completion changed the existing `--group` help wording. The overlay restores the old `execute/plan a federation group` wording while retaining the new parallel options. The affected group/completion tests pass after the fix.

Native `roadmap-frx-y01` plan construction succeeds and exports four nodes/four sequential roadmap-validation waves. Native preflight and named workflow invocation on the audit container fail closed only because the Devtool target requires `native-termux` while the audit host is `desktop-linux`; the target/environment contract was not weakened to make container validation pass.

## Gate status

FRX-Y01 is **not** a validation gate. The overlay has no finalize hook and does not reinstall Devtool. Apply it with `--no-validate` under the campaign rule that Devtool is reinstalled only at validation gates.
