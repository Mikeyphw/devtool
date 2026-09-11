# MP05 + MP06 Cross-Target Workspace Graphs and Target Groups

## Scope

This overlay completes MP05 and MP06 as one feature slice on top of MP03+MP04.
It promotes `target:<name>#<action>` from a validated placeholder to executable
workspace orchestration and adds canonical workspace target groups for persistent
multi-platform operations.

## MP05 — cross-target execution

- `target:<name>#<action>` recursively expands the referenced target action into
  the same execution graph.
- A referenced target's declared workflow is expanded when present; otherwise
  its primary runner is used as the action fallback.
- No recursive Devtool subprocess is used.
- Each referenced target preserves its own root, runner, target environment,
  execution environment, chroot/device settings, runner options, and compatible
  variant configuration.
- CLI-time runtime overrides remain inherited, including TS00 explicit pytest
  selectors.
- `(target, action)` invocations are cached inside the workspace planner so
  shared prerequisites are expanded/executed once.
- Dependencies from every call site are joined onto the shared invocation,
  preserving ordering while avoiding duplicate work.
- Cross-target action cycles fail during config loading with the complete
  target/action path.
- Workspace graph node/validation identities remain stable and feed the MP03/04
  dependency-aware execution/session machinery.

## MP06 — workspace groups

Canonical configuration:

```toml
[workspace]
default_target = "api"

[workspace.groups]
mobile = ["android", "tv"]
frontend = ["web", "desktop"]
release = ["api", "@frontend", "@mobile"]
everything = ["*"]
```

- `[workspace].default_target` is canonical.
- Legacy `[project].default_target` remains compatible; if both are present,
  they must be equal.
- Group members support direct targets, nested `@group`, `group:name`, and `*`.
- Expansion preserves configured order and stably deduplicates targets.
- Unknown targets/groups, empty groups, group/target name collisions, and group
  cycles fail before execution.
- Repeated `--group` values union their targets; `--all-targets` selects every
  configured target.
- `--target`, group/all-target selection, and target matrices are explicitly
  mutually exclusive scopes.
- Multi-target `run` is rejected; lifecycle operations that can be planned as a
  finite graph use one shared workspace graph.

## UI / inspection / machine integration

- Added `groups|gr` text/JSON inspection.
- `targets` output exposes workspace group membership and default-target status.
- `workflows --json` is schema v3 and contains both the local declared `graph`
  and executable `expanded_graph`.
- Resolved config machine output contains canonical workspace metadata plus
  per-target expanded workflow graphs.
- GTK Control Center target summaries expose groups/default membership and can
  open a multi-target repo with no single default target.
- Live HUD target labeling publishes the resolved grouped target set.
- Zsh completion includes group names, `--group`, `--all-targets`, and the new
  `groups` command.
- README, generated config template, configuration/runner/polyglot docs,
  dedicated workspace docs, manpage, and changelog are synchronized.

## Compatibility

- Repositories without `[workspace]` continue to work.
- Legacy `[project].default_target` behavior remains accepted.
- Single-target runner actions still take the existing non-workspace path when
  no group/all-target scope is requested.
- MP03/04 sequential-list and explicit DAG syntax remains unchanged.
- MP07 affected-target analysis, MP08 aggregate doctor/preflight, and MP09 safe
  graph parallel scheduling remain later roadmap slices.

## Validation evidence

Focused MP05/06 acceptance and regression gate:

- 627 pytest tests passed
- 45 unittest subtests passed
- 0 failures

The gate covers MP05/06, MP03/04, MP01/02, TS00, configuration, CLI routing,
completion/manpage, project runners, validation sessions, execution
environments, Termux/chroot/SSH adapters, TUI/interactive validation, HUD,
summary, IPC, Tasker, validation control/progress/resource precedence, GTK
Control Center/HUD, and global configuration.
