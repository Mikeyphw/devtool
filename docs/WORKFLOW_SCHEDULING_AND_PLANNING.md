# Safe workflow scheduling and planning (MP09 + MP10)

MP09 turns the expanded workspace DAG into a safely schedulable execution plan. Devtool remains sequential by default. Parallel workflow execution is opt-in and only overlaps ready nodes when dependency order, declared read/write scopes, and resource limits prove that overlap safe.

## Declaring scopes

Target and job `inputs` describe paths the node may read. `outputs` describe paths the node may write. Paths are relative to the target/job root and are normalized to repository-relative static prefixes for glob declarations.

```toml
[targets.web]
runner = "node"
root = "apps/web"
inputs = ["src/**", "../../packages/protocol/**"]
outputs = ["dist/**"]

[jobs.generate-protocol]
runner = "node"
root = "packages/protocol"
command = ["npm", "run", "generate"]
inputs = ["schema/**"]
outputs = ["generated/**"]
```

A node without explicit outputs has an unknown write set and is serialized. A node may instead declare `metadata.read_only = true` when it truly performs no repository writes. Paths escaping the repository fail closed for scheduling/reservation purposes.

Devtool prevents both write/write and write/read overlap. It does not assume two different target roots are independent when their declared scopes overlap.

## Scheduler policy

```toml
[workspace.scheduler]
parallel = false
max_parallel_nodes = 4
max_gradle_nodes = 1
max_dotnet_nodes = 1
```

`parallel = false` is the default. `max_parallel_nodes = 0` means Devtool selects a conservative runtime default. Gradle/Android/Maven nodes share the Gradle-heavy resource class; .NET has its own cap. Unknown writers remain fail-closed.

CLI overrides:

```sh
devtool build --workflow-parallel --workflow-max-workers 4
devtool build --no-workflow-parallel
```

For validation, existing `--parallel` still controls Gradle parallelism and also requests safe workflow-node parallelism unless `--no-workflow-parallel` explicitly overrides it.

## Cross-process reservations

When every executable node has a known write set, Devtool reserves the union of those output paths instead of the whole repository. Two Devtool processes may then coexist only when the existing reservation system proves their write scopes disjoint. If any write set is unknown, Devtool retains repository-exclusive reservation semantics.

## Plan without executing

`devtool plan` is the MP10 inspection surface:

```sh
devtool plan build --group release --workflow-parallel --explain --show-commands
devtool plan validate --affected --json
devtool plan build --dot > workflow.dot
```

The plan exposes:

- expanded workspace DAG and dependencies;
- resolved targets/runners/actions;
- safe scheduling waves;
- per-node read/write scopes and whether writes are known;
- resource classes and concurrency limits;
- operation reservation paths;
- resolved command argv and working directories;
- affected-target selection when requested.

`workflows --json` also includes the expanded graph and default schedule for every declared target workflow. `inspect` shows compact schedule/reservation summaries for the selected target.

## Runtime observability

Machine JSON summaries expose a top-level `workflow` object with targets, workflows, node terminal states, dependencies, durations, and scheduler metadata. Tasker/HUD progress uses workflow target identity so concurrent workspace nodes remain attributable to the correct target. Human result ordering stays deterministic even when nodes ran concurrently.

## Safety rules

1. DAG dependencies always win over concurrency.
2. Unknown writers serialize.
3. write/write and write/read overlap serialize.
4. Gradle/.NET-heavy caps apply independently of total concurrency.
5. Each workflow node is atomic from the scheduler's point of view; a node's multiple command specs remain sequential.
6. A failed dependency blocks dependents. With fail-fast, not-yet-started independent nodes are skipped after a failure; continue-on-error may continue independent branches.
7. Cross-process reservations are scoped only when the complete write set is known; otherwise Devtool fails closed to repository exclusivity.
