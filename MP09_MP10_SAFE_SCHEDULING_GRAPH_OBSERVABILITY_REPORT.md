# MP09 + MP10 Safe Workflow Scheduling and Graph Observability

## Scope

This overlay completes roadmap MP09 and MP10 on top of the validated MP07+MP08 workspace graph foundation.

MP09 adds conservative, real in-process DAG scheduling. Workflow execution remains sequential by default. When parallel scheduling is requested, Devtool only overlaps dependency-ready nodes whose declared read/write scopes prove the overlap safe and whose resource classes fit configured concurrency limits.

MP10 adds one inspectable execution truth source: `devtool plan` exposes the same expanded graph, scope model, scheduler waves, resource limits, reservation paths, and resolved command specs used by runtime. Existing workflow inspection, JSON summaries, HUD/Tasker state, desktop state, completion, docs, and configuration templates consume that model.

## MP09 implementation

- Added `WorkflowSchedulerConfig` under `[workspace.scheduler]` with sequential default, maximum concurrent nodes, Gradle-heavy cap, and .NET-heavy cap.
- Added workflow scope analysis from target/job `inputs` and `outputs`.
- Nodes without explicit outputs are treated as unknown writers and remain serialized.
- Explicit read-only metadata is supported for truly non-writing nodes.
- Repository-escaping input/output scopes fail closed for concurrency/reservation decisions.
- Added write/write and write/read collision detection.
- Added resource classes; Android/Gradle/Maven share the conservative Gradle-heavy limit and .NET has an independent limit.
- Added deterministic DAG scheduling waves preserving dependency order.
- Added real parallel node execution with `ThreadPoolExecutor`; a node's multiple command specs remain sequential/atomic from the graph scheduler's perspective.
- Added failure, dependency blocking, fail-fast skip, cancellation, and continue-on-error propagation across scheduling waves.
- Added concurrency-safe progress callback/HUD routing with per-node phase identity.
- Added deterministic result/raw-output ordering after concurrent execution.
- Extended normal run reservations: when every graph node has a complete known write set, the operation reserves the union of those output paths; otherwise it retains repository-exclusive fail-closed reservation.
- Added `--workflow-parallel`, `--no-workflow-parallel`, and `--workflow-max-workers`. Validation's existing `--parallel` also requests safe workflow-node concurrency unless explicitly overridden.

## MP10 implementation

- Added `devtool plan|pl [ACTION]` for restore/build/test/lint/package/run/validate/wine-validate.
- `plan` accepts normal workspace scopes: target, repeated groups, all targets, and dependency-aware affected selection.
- Human plans show selected targets, concurrency mode, wave count, reservation safety, and every wave.
- `--explain` shows each node's runner, resource class, read/write scopes, write-set certainty, and serialization rationale.
- `--show-commands` shows resolved cwd/argv without execution.
- `--json` exposes graph, schedule, affected-selection payload, and command specs.
- `--dot` emits Graphviz DAG output.
- `workflows --json` remains schema v3 for compatibility and gains additive per-workflow scheduling metadata.
- `inspect` gains compact workflow schedule/reservation summaries.
- Runtime JSON summaries gain a top-level `workflow` object with targets, workflow names, node dependencies/states/durations, and scheduler metadata.
- Tasker/HUD target resolution prefers workflow target identity so concurrent workspace nodes stay attributable.
- Desktop Control Center project state exposes workspace scheduler policy.
- Generated `.devtool.toml`, zsh completion, packaged completion, manpage, README, configuration docs, runner docs, and architecture docs cover the new behavior.

## Safety invariants

1. Dependencies are never bypassed for parallelism.
2. Unknown writers never overlap another node.
3. write/write and write/read intersections serialize.
4. Runner-specific resource caps apply in addition to total concurrency.
5. Cross-process scoped reservations are only used when all writes are known.
6. Any uncertainty falls back to serial and/or repository-exclusive behavior.
7. Runner-specific execution environment preparation, diagnostics, progress parsers, and TS00 pytest selection continue to execute through the native runner path.

## Compatibility

- Existing projects remain sequential unless they opt into workflow parallelism.
- Existing `--parallel` Gradle behavior is retained; workflow scheduling is an additional safe layer for workflow validation.
- `workflows --json` keeps schema version 3; scheduling fields are additive.
- Repositories without target workflows continue through the legacy ordinary `run_specs` path.
- Existing reservation behavior remains repository-exclusive when output scopes are incomplete.

## Validation

Dedicated MP09/MP10 contracts cover scheduler configuration, generated config, independent parallel execution, unknown-writer serialization, write/read collisions, Gradle caps, failure/block/fail-fast behavior, scoped versus exclusive reservations, JSON/DOT planning, workflow schedule inspection, top-level workflow JSON summaries, and CLI scheduler parsing.

The combined focused regression gate passed **701 tests + 45 subtests** before packaging, spanning TS00 and MP01 through MP10 impact surfaces, configuration, CLI, runners, persistent validation, execution environments, Termux/chroot/SSH, TUI, interactive validation, HUD, summary, IPC, Tasker, desktop GTK, completion/manpage, and global configuration.
