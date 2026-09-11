# Workspace groups and cross-target execution

MP05 and MP06 make a multi-platform repository a first-class Devtool workspace.
A target is still an independently configured product/component, but one command
can now plan several targets and their shared prerequisites as one execution DAG.

## Canonical workspace configuration

```toml
[workspace]
default_target = "api"

[workspace.groups]
mobile = ["android", "tv"]
frontend = ["web", "desktop"]
release = ["api", "@frontend", "@mobile"]
everything = ["*"]
```

`[workspace].default_target` is canonical. Legacy `[project].default_target`
remains supported; if both are present they must name the same target.

Group members are evaluated in declaration order and deduplicated stably:

- `target-name` adds one configured target.
- `@group` or `group:group` expands another group.
- `*` adds every configured target in target declaration order.

Group names cannot collide with target names. Empty groups, unknown members,
and nested-group cycles are configuration errors and fail before execution.

## Selecting a workspace scope

```bash
devtool --group release validate
devtool --group mobile --group frontend build
devtool --all-targets test
devtool groups
devtool groups --json
devtool targets --json
```

Repeated `--group` values are unioned with stable deduplication. `--target`,
`--all-targets`, and target-matrix selection are intentionally mutually
exclusive so every command has one unambiguous target-selection model.

A multi-target `run` is rejected because the first long-running process could
block every later target. Build/test/lint/validate/package/clean-style actions
can use workspace scopes.

## Cross-target workflow references

```toml
[targets.protocol]
runner = "python"
platform = "shared"
root = "packages/protocol"

[targets.protocol.workflows]
build = ["runner:python#build"]

[targets.api]
runner = "python"
platform = "server"
root = "services/api"

[targets.api.workflows]
build = [
  "target:protocol#build",
  "runner:python#build",
]
```

`target:protocol#build` does not launch another Devtool process. MP05 recursively
expands the referenced target action into the current workspace graph. If the
referenced target declares that workflow, its DAG is inserted; otherwise its
primary runner handles the requested action.

Each referenced target keeps its own target configuration: root, runner,
runner-specific options, environment, execution environment, chroot/device
selection, and a compatible variant. Runtime CLI overrides intentionally remain
inherited. This is what lets TS00 `--pytest-test` selectors apply to every
Python target selected by a group without leaking one target's static config
into another.

## Deduplication and cycles

A target/action pair is one logical invocation inside a workspace graph. If API
and Web both depend on `target:protocol#build`, protocol build is expanded once
and both downstream nodes depend on the same leaves.

Cycles are rejected at config load time:

```text
Cross-target workflow cycle detected: api#build -> protocol#build -> api#build
```

Local workflow cycles and cross-target cycles therefore fail before a command
can partially modify the repository.

## Inspection and machine output

`devtool workflows --json` emits schema version 3. Each workflow keeps its
locally declared `graph` and adds `expanded_graph`, which contains the actual
cross-target graph that execution will use. `devtool targets --json` includes
workspace membership and default-target status. `devtool groups --json` returns
both declared group members and fully expanded target lists.

HUD/progress metadata uses each graph node's `workflow_target`; grouped root
selection is also published so frontends can represent workspace operations
without inferring targets from command text.

## Affected group scope

MP07 allows a workspace group to act as the candidate scope for dependency-aware Git selection:

```bash
devtool validate --group frontend --affected
devtool affected --group mobile --explain
```

Ownership is still computed across the repository before the final candidate filter, so a changed shared dependency outside the group can select an in-group consumer. Unknown/unowned paths conservatively select every target in the candidate group.
