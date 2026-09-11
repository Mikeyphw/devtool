# Polyglot targets and reusable workflows (MP01 + MP02)

MP01 and MP02 separate **what a repository component is** from **which tool
executes work for it** and add a declarative, reusable workflow configuration
model. These releases intentionally stop before graph execution: MP03 will turn
the normalized declarations into a dependency DAG.

## Target identity: platform is not runner

Use `runner` for the primary Devtool runner and `platform` for the product
surface:

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
`desktop`, `shared`, and repository-specific slugs are all valid). `runner`
must resolve to a first-class Devtool runner.

The older forms remain supported:

```toml
[project]
kind = "python"

[targets.api]
type = "python"
```

`kind` and target `type`/`kind` are compatibility aliases for `runner`. New
configuration should use `runner`. Declaring conflicting canonical and legacy
values is rejected instead of silently choosing one.

`devtool doctor` reports optional migration hints when a loaded repository still
uses legacy identity aliases.

## Reusable jobs

A job is runner-owned work that can be referenced by one or more target
workflows.

```toml
[jobs.generate-python-client]
runner = "node"
root = "tools/openapi"
command = ["npm", "run", "generate:python"]
inputs = ["schema/**", "package.json", "package-lock.json"]
outputs = ["services/api/generated/**"]

[jobs.generate-python-client.env]
NODE_ENV = "production"

[jobs.generate-python-client.metadata]
purpose = "OpenAPI client generation"
```

Structured argv is the default command representation. A string in `command`
is rejected so quoting cannot silently change across native Termux, chroot,
PROot, SSH, or desktop execution.

Use an explicit shell command only when shell syntax is required:

```toml
[jobs.legacy-pack]
runner = "command"
shell_command = "npm run bundle && python3 tools/finalize.py"
```

A job may instead delegate to a native runner action:

```toml
[jobs.python-unit]
runner = "python"
action = "test"
```

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

The input/output declarations are configuration truth in MP02. MP09 later uses
them for conflict-safe scheduling and reservations.

## Target workflows

A named target can declare lifecycle workflows:

```toml
[targets.api]
runner = "python"
platform = "server"
root = "services/api"

[targets.api.workflows]
build = [
  "job:generate-python-client",
  "runner:python#build",
]

test = [
  "job:generate-python-client",
  "runner:python#test",
]

validate = [
  "job:generate-python-client",
  "runner:python#validate",
]
```

Three normalized reference forms are accepted:

```text
job:<job-name>
runner:<runner-name>#<action>
target:<target-name>#<action>
```

All references are validated when configuration loads. Unknown jobs, targets,
runners, malformed actions, and malformed references fail early with an
actionable configuration error.

Cross-target references are representable now so configuration does not need to
change again. MP05 adds their dependency execution semantics; MP03 adds the
generic graph runtime that consumes all workflow declarations.

## Inspection

Configuration can be inspected before graph execution exists:

```bash
devtool targets
devtool targets --json

devtool jobs
devtool jobs --json

devtool workflows
devtool workflows --json

devtool inspect
devtool config resolve
```

`targets` reports platform, primary runner, configured workflow names, and the
resolved target root. `jobs` reports runner, execution mode, and root.
`workflows` prints normalized step order. JSON output uses Devtool's normal
machine-output envelope when machine output is enabled by the CLI.

The GTK Control Center target cards consume the same normalized model and show
platform, primary runner, and configured workflows instead of treating project
`kind` as both product type and execution tool.

## Compatibility examples

A legacy repository remains valid:

```toml
[project]
kind = "python"

[targets.api]
type = "python"
root = "services/api"
```

The canonical equivalent is:

```toml
[project]
runner = "python"
platform = "server"

[targets.api]
runner = "python"
platform = "server"
root = "services/api"
```

Both currently produce `config.kind == "python"` for compatibility. New code
should prefer `config.runner`; `config.kind` remains synchronized until the
legacy API can be retired in a future major compatibility window.

## Boundary of this overlay

MP01 + MP02 deliver the complete **configuration and identity** layer. They do
not execute target workflows yet. That is intentional rather than partial
implementation:

- MP03: generic dependency graph and ordering
- MP04: workflow-node runner composition
- MP05: cross-target dependency execution and deduplication
- MP08: aggregate toolchain preflight from the resolved graph
- MP09: input/output-aware safe scheduling

This sequencing keeps runner behavior backward-compatible while the graph
runtime is introduced behind a fully normalized configuration contract.
