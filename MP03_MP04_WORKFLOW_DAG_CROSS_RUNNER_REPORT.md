# MP03 + MP04 — Executable Workflow DAG and Cross-Runner Composition

## Scope

This overlay implements the complete MP03/MP04 roadmap slice on top of TS00 and MP01/MP02. MP03 promotes target workflows from normalized declarations to a generic dependency graph. MP04 makes those graphs executable by dispatching every node to its own first-class Devtool runner and through the existing execution-environment pipeline.

Cross-target `target:<name>#<action>` orchestration remains the explicit MP05 boundary. Such references are parsed, validated, serialized, and inspectable now, but MP03/MP04 refuse to execute them recursively.

## Delivered contracts

### Generic DAG

- Added `devtool.workflow_graph.ExecutionGraph` and `WorkflowNode`.
- Plain MP02 string lists retain sequential semantics.
- Inline `{ id, ref, depends_on }` steps support explicit roots, branches, and joins.
- Stable declaration-order topological sorting is deterministic.
- Duplicate IDs, unknown dependencies, self-dependencies, and cycles fail during config loading/planning.
- Dependency and dependent closure APIs are available for later affected/scheduler work.
- `workflows --json` is schema v2 and exposes roots, leaves, node dependencies, and stable order.
- Resolved config preserves the legacy raw `workflows` list and adds `workflow_graphs`.

### Cross-runner composition

- Matching selected-target lifecycle workflows automatically replace the single-runner plan for `build`, `test`, `validate`, `package`, and `run` actions.
- `job:` nodes execute their declared runner action, structured argv, or explicit shell command.
- `runner:<runner>#<action>` nodes invoke that runner directly without recursive Devtool subprocesses.
- Python, Node, Gradle/Android, Go, Rust, Maven, CMake, Meson, Make, command, and other registered runners retain their own native planning semantics.
- TS00 exact pytest selectors flow into Python workflow nodes unchanged.
- Workflow jobs preserve target environment plus job overrides, inputs/outputs, execution environment, chroot/device, runner options, and native progress-parser metadata.

### Dependency-aware runtime

- A failed node blocks dependents.
- `continue_on_error` may continue independent branches.
- Fail-fast failures mark independent not-yet-started nodes skipped while dependents become blocked.
- Cancellation is terminal and later nodes become cancelled without executing.
- Multi-command native runner nodes remain `running` until their final command succeeds.
- Synthetic blocked/skipped/cancelled results carry workflow state and publish a terminal progress snapshot for HUD/Tasker/other progress consumers.

### Persistent validation integration

- Every expanded command receives a stable validation phase key.
- Multi-command nodes are chained internally.
- A node's first command depends on the last command of every prerequisite node.
- Existing validation-session dependency closure/resume/retry machinery therefore sees the workflow DAG rather than a flattened command list.

### Execution-environment integration

- Workflow specs are prepared individually through the existing execution planner.
- A workflow may therefore mix native Termux, chroot, PRoot, desktop Linux, SSH, Android-device, or external-device execution according to existing backend rules.
- Explicit CLI environment selections remain authoritative.
- No new backend-specific subprocess path was added.

### Matrix correctness

- Matrix expansion namespaces workflow node identity and validation phase identity per lane.
- Dependency keys are rewritten within the same matrix lane.
- RID/target/variant lanes therefore cannot accidentally satisfy, block, or overwrite each other's graph state.

### Compatibility

- Targets without a workflow for the requested action keep the legacy single-runner path.
- MP02 plain sequential workflow declarations execute without migration.
- Legacy `kind`/`type` runner aliases remain unaffected.
- Explicit validate workflows reject legacy `--skip-restore`, `--skip-build`, and `--skip-test` rewriting rather than silently changing the configured DAG.

## Validation evidence

Dedicated MP03/MP04 contracts cover graph construction, branches/joins, closure, cycle/unknown-dependency rejection, cross-runner planning, TS00 selector propagation, explicit-shell/structured jobs, target-reference boundary behavior, validation-session bridging, failure/block/skip state, multi-command nodes, per-node execution preparation, machine graph output, matrix isolation, and terminal progress for synthetic graph states.

Observed local results on the implementation tree:

- `19 passed` — dedicated MP03/MP04 contract module.
- `328 passed, 40 subtests passed` — MP03/MP04 + MP01/MP02 + TS00 + validation sessions + config + runners + CLI + completion/manpage.
- `153 passed` — execution environments + Termux/chroot/SSH runners + IPC + Tasker + validation resource/control/progress regressions.
- `163 passed, 5 subtests passed` — TUI/interactive/HUD/summary/doctor/desktop/completion/global-config compatibility sweep.

A Devtool-driven `--pytest-test` integration attempt reached the selected validation path but could not provision the managed project environment in this isolated container because PyPI DNS/network access was unavailable while fetching `rich`. That infrastructure limitation is not counted as a feature test failure; the same selected test files passed through the already-provisioned local Python environment as listed above.

## Next roadmap boundary

MP05 adds cross-target graph expansion/deduplication. MP06 adds named workspace groups. MP03/MP04 deliberately do not emulate MP05 by recursively invoking Devtool for `target:` references.
