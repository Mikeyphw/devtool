# Polyglot targets, reusable jobs, and executable workflow DAGs (MP01–MP08)

MP01–MP08 separate **what a repository component is** from **which tool executes
one piece of work**, then turn target workflows into a validated dependency DAG
that can compose multiple first-class runners in one lifecycle action.

The central rule is:

```text
Target = product/component
Platform = what that product represents
Runner = tool-aware command planner for one node
Execution environment = where that node executes
Workflow = dependency graph that composes nodes
```

A Python server target can therefore run Node code generation before pytest, or
an Android target can run a Python metadata generator before Gradle, without
teaching PythonRunner about npm or AndroidRunner about Python.

## Target identity: platform is not runner

Use `runner` for the target's primary Devtool runner and `platform` for the
product surface:

```toml
[project]
runner = "python"
platform = "server"

[targets.tv]
runner = "android"
platform = "tv"
root = "apps/tv"

[targets.desktop]
runner = "node"
platform = "desktop"
root = "apps/desktop"
```

`platform` is an open lowercase slug (`server`, `web`, `android`, `tv`,
`desktop`, `shared`, and repository-specific slugs are valid). `runner` must
resolve to a first-class Devtool runner.

Legacy `kind` and target `type`/`kind` identities remain supported for existing repositories, but new configuration should use `runner` and `platform`. Conflicting canonical and legacy values are rejected instead of silently choosing one. See [`BACKWARD_COMPATIBILITY.md`](BACKWARD_COMPATIBILITY.md) and use `devtool config migrate --check` for migration examples.

## Reusable jobs

A job is runner-owned work that can be referenced by one or more target
workflows.

### Structured argv

```toml
[jobs.generate-python-client]
runner = "node"
root = "tools/openapi"
command = ["npm", "run", "generate:python"]
inputs = ["schema/**", "package.json", "package-lock.json"]
outputs = ["services/api/generated/**"]

[jobs.generate-python-client.env]
NODE_ENV = "production"
```

Structured argv is preferred. A string in `command` is rejected so quoting does
not silently change across native Termux, chroot, PRoot, SSH, or desktop
execution.

### Explicit shell

Use shell syntax only when it is intentional:

```toml
[jobs.legacy-pack]
runner = "command"
shell_command = "npm run bundle && python3 tools/finalize.py"
```

Devtool executes this through an explicit shell boundary. Set
`DEVTOOL_WORKFLOW_SHELL` to override the default `sh` executable.

### Native runner action

A job may delegate to a first-class runner action:

```toml
[jobs.web-build]
runner = "node"
root = "web"
action = "build"
```

This is not converted into a generic shell command. NodeRunner still owns npm
script discovery, progress parsing, tool metadata, and Node-specific behavior.

Exactly one of `action`, `command`, or `shell_command` is required.

Jobs may also declare:

```text
execution_environment
chroot_name
device_serial
inputs
outputs
env
metadata
runner-specific option tables
```

MP04 applies the job's runner options and execution-environment request at the
node boundary. A CLI environment override (`--host`, `--chroot`, `--ssh`,
`--proot-distro`, or `--execution-environment`) remains authoritative.

The `inputs`/`outputs` declarations are already attached to graph-node metadata;
MP09 later consumes them for write-conflict reservations and safe parallel
scheduling.

## Sequential workflows

The MP02 ordered-list syntax remains valid and is now executable:

```toml
[targets.api]
runner = "python"
platform = "server"
root = "services/api"

[targets.api.workflows]
validate = [
  "job:generate-python-client",
  "runner:python#validate",
]
```

A plain list preserves its historical meaning: each step depends on the
previous step.

Running:

```bash
devtool --target api validate
```

plans the Node job first and then the Python validation node. PythonRunner never
learns npm semantics; the workflow runtime asks NodeRunner and PythonRunner to
plan their own commands and then composes the resulting `CommandSpec` objects.

TS00 selection remains native inside the Python node:

```bash
devtool --target api validate \
  --pytest-test tests/test_auth.py::test_login \
  --pytest-test tests/test_api.py::TestUsers::test_create
```

The prerequisite Node job still runs, while the Python pytest command receives
only the explicit TS00 selectors.

## Branching and joining DAGs

MP03 adds inline step tables with stable IDs and explicit dependencies:

```toml
[targets.api.workflows]
validate = [
  { id = "generate", ref = "job:generate-python-client", depends_on = [] },
  { id = "lint", ref = "runner:python#lint", depends_on = ["generate"] },
  { id = "test", ref = "runner:python#test", depends_on = ["generate"] },
  { id = "package", ref = "runner:python#package", depends_on = ["lint", "test"] },
]
```

`depends_on` refers to **step IDs**, not raw reference strings.

Important compatibility rules:

- omitting `depends_on` keeps sequential-list behavior;
- `depends_on = []` explicitly creates a root node;
- a step that declares `depends_on` must declare an `id`;
- duplicate IDs, unknown dependencies, self-dependencies, and cycles fail while
  loading configuration, before any command is launched;
- topological ordering is stable by declaration order when multiple nodes are
  ready at the same time.

The runtime remains sequential through MP06 even when the graph exposes independent
branches. MP09 is the roadmap step that enables conflict-safe parallel graph
scheduling. The DAG is still meaningful now because failure propagation and
persistent validation dependencies follow the real graph rather than list
position.

## Workflow references

Three normalized reference forms are supported:

```text
job:<job-name>
runner:<runner-name>#<action>
target:<target-name>#<action>
```

### `job:<name>`

Executes a reusable job through its declared runner, command mode, root,
environment, and node-specific execution settings.

### `runner:<runner>#<action>`

Runs a first-class runner action at the selected target root. Examples:

```text
runner:python#test
runner:node#build
runner:android#package
runner:go#test
```

`validate` and `wine-validate` invoke the runner's native validation planner
rather than pretending validation is one generic command.

### `target:<target>#<action>`

MP05 makes cross-target references executable. `target:<name>#<action>` expands
into the referenced target's declared workflow when present, or into that
target's primary runner action as a fallback. Expansion happens inside one
workspace DAG; Devtool never shells out to a recursive `devtool` process. The
referenced target keeps its own root, runner options, environment, execution
environment, and compatible variant, while command-line runtime overrides such
as TS00 pytest selection remain inherited. Repeated references to the same
`(target, action)` invocation are deduplicated, and cross-target cycles are
rejected during config loading with the complete target/action cycle path.

MP06 adds `[workspace]` and `[workspace.groups]`. Groups are ordered target sets,
may nest other groups using `@name` or `group:name`, and may use `*` to include
every configured target. Repeated `--group` selections are unioned with stable
deduplication; `--all-targets` selects the whole workspace. Group execution
plans one shared graph, so common prerequisites execute once.

## Failure and dependency semantics

Workflow command specs carry stable node identity and dependency metadata.
During execution Devtool tracks node state and distinguishes:

```text
ready
running
passed
failed
blocked
skipped
cancelled
```

If a node fails:

- dependent nodes are marked `blocked` and are not launched;
- with continue-on-error behavior, independent branches may continue;
- with fail-fast behavior, independent not-yet-started nodes are marked
  `skipped` while dependents remain `blocked`;
- cancellation prevents later graph commands from launching and reports
  cancelled state rather than ordinary failure.

A runner action may expand to several commands. The node becomes `passed` only
when its final command succeeds. Later commands within the same node are not
run after an earlier command in that node fails.

## Persistent validation-session bridge

Workflow DAGs also populate Devtool's durable validation dependency metadata.
When a node expands to several commands:

```text
node A: part 1 -> part 2
node B depends on A
```

Devtool records:

```text
A part 2 depends on A part 1
B first part depends on A final part
```

so resumable validation, retry, dependency closure, and plan fingerprints retain
the same dependency truth as direct workflow execution.

An explicit `[targets.<name>.workflows].validate` workflow is authoritative.
The legacy `--skip-restore`, `--skip-build`, and `--skip-test` flags are not
allowed to silently rewrite that explicit DAG; Devtool asks the user to select a
different workflow/profile or remove the skip flag instead.

## Per-node execution environments

Jobs can choose different execution environments in one target workflow:

```toml
[jobs.generate]
runner = "node"
execution_environment = "native-termux"
command = ["npm", "run", "generate"]

[jobs.linux-package]
runner = "python"
execution_environment = "chroot"
chroot_name = "androidos"
action = "package"
```

The CLI execution-preparation layer scopes toolchain/environment preparation per
workflow command while preserving graph order. Existing path translation,
chroot/PRoot/SSH preflight, toolchain wrapping, and native-Termux safety checks
are reused instead of duplicated in Workflow Core.

## Inspection and machine state

Use:

```bash
devtool targets
devtool jobs
devtool workflows
devtool workflows --json
devtool inspect
devtool config resolve
```

`devtool workflows --json` uses schema version 3 and exposes both declared workflows and their expanded workspace graph:

```text
step reference
stable step id
dependency keys
roots
leaves
stable topological order
full node metadata
```

`config resolve` keeps the MP02 raw `workflows` lists for compatibility and adds
`workflow_graphs` with the normalized DAG representation.

Executed command/result metadata includes:

```text
workflow_target
workflow_name
workflow_node_key
workflow_node_id
workflow_depends_on
workflow_runner
workflow_action
workflow_execution_environment
workflow_inputs
workflow_outputs
workflow_node_state
```

This metadata is available to machine summaries and later UI Core work without
requiring log scraping.

## Compatibility

Repositories without a workflow declaration keep their existing runner behavior
unchanged. A workflow only overrides the lifecycle action with the same name.
For example, a target that declares only `build` still uses its primary runner's
normal `test`, `run`, and `package` behavior.

Runner capabilities also treat an explicitly declared target workflow as
support for that lifecycle action, even if the target's primary runner alone
would not advertise it.

## Roadmap boundary after MP08

MP01–MP08 now deliver target identity, reusable jobs, executable cross-runner/cross-target DAGs, workspace groups, dependency-aware affected selection, and aggregate graph-aware preflight.

Delivered in this stage:

- **MP07:** target-root/input/job-input ownership, Git working-tree or base/head range selection, reverse dependency propagation, conservative unknown-path handling, `--affected`, and `affected --explain`.
- **MP08:** expanded-graph runner/tool/environment aggregation, root/output-scope checks, destination-aware tool availability, `preflight`, and `doctor.workflow_preflight`.

Target declarations may use `inputs` for source ownership outside their root and `outputs` for generated/write scopes. Reusable-job inputs participate in MP07 ownership automatically.

Still intentionally later:

- **MP09:** input/output-aware safe parallel scheduling and reservations
- **MP10:** richer graph planning/inspection UX

The architectural boundary remains strict: runners plan native commands, Workflow Core composes them, affected selection chooses workspace roots from repository changes, preflight derives requirements from the resulting expanded graph, and execution environments remain independent from runner identity.
