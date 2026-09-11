# MP07 + MP08 Affected Targets and Graph-Aware Toolchain Preflight

## Scope

This overlay completes MP07 and MP08 as one feature slice on top of MP05+MP06.
It makes Git changes a first-class dependency-aware workspace scope and makes
`doctor`/`preflight` reason about the expanded executable workflow graph rather
than the selected target's primary runner alone.

## MP07 — affected-target analysis

- Added first-class `--affected` to lifecycle workspace operations, with
  `--affected-base`/`--affected-head` for CI range analysis.
- Added `affected|aff` with text/JSON output, `--base`, `--head`, and
  `--explain` reason tracing.
- Working-tree selection unions staged, unstaged, and untracked files.
- Range selection uses `base...head`; a head-only selection compares the
  revision to its first parent.
- Target roots own their descendant paths automatically.
- New target `inputs` patterns extend ownership outside a target root.
- Reusable-job `inputs` extend ownership to every directly referencing target.
- Reverse cross-target workflow dependencies propagate affected state to
  consumers, so a shared protocol/schema target can select server/web/mobile
  dependents.
- Candidate filtering for `--group`/`--all-targets` is applied after dependency
  propagation, so an out-of-group dependency can still select an in-group
  consumer.
- Changed paths with no configured owner conservatively select every target in
  the candidate scope rather than silently omitting validation.
- A no-change `--affected` lifecycle invocation is a successful no-op and never
  falls back to the default target.
- Workspace scope flags are accepted after normal lifecycle subcommands as well
  as before them.

## MP08 — expanded-graph toolchain aggregation

- Added `preflight|pf` for graph-aware requirement and safety checks.
- `doctor` now embeds the same report as `workflow_preflight` and includes its
  health in structured doctor output.
- Analysis expands real target workflows, reusable jobs, and cross-target
  dependencies before deriving requirements.
- Reports every runner and concrete command executable actually used by the
  expanded graph, rather than assuming one toolchain per target.
- Reports effective execution environments, target/job roots, selected
  target/action invocations, and expanded graph nodes.
- Checks missing target/job roots and declared target/job output scopes that
  escape the repository.
- Structured argv jobs expose their concrete executables; explicit shell jobs
  are marked opaque because inner tools cannot be inferred reliably.
- Local tools are resolved against the effective command PATH. Tools that run
  on SSH/chroot/PRoot/other destinations are marked deferred instead of being
  falsely reported missing from the host.
- Reusable jobs with `execution_environment = "auto"` inherit their owning
  target's effective execution environment, chroot, and device metadata.
- Python managed-venv interpreters created by an earlier `ensure-venv` spec are
  marked `planned`, not falsely reported missing before execution.
- Repeated environment/root checks are deduplicated in the final report.

## Configuration / UI / machine integration

Target ownership and generated/write scope are now canonical target metadata:

```toml
[targets.api]
runner = "python"
root = "services/api"
inputs = ["../../packages/protocol/**", "../../schemas/**"]
outputs = ["build/**", "dist/**"]
```

Integration includes:

- resolved configuration `inputs`/`outputs` metadata;
- `targets --json` ownership metadata and counts in human output;
- GTK Control Center target summaries with ownership/output fields;
- affected-selection metadata on executed workspace specs;
- structured `affected --json`, `preflight --json`, and
  `doctor --json.workflow_preflight` reports;
- generated config-template examples;
- zsh completion for new commands/options and post-subcommand workspace scope;
- manpage, README, configuration, runner, workspace-group, and dedicated MP07/
  MP08 documentation.

## Compatibility and safety

- Existing repositories need no `inputs`/`outputs`; target-root ownership is
  automatic.
- Existing non-affected lifecycle behavior is unchanged.
- `--target` remains explicit and is mutually exclusive with lifecycle
  `--affected`; groups/all-targets can narrow affected candidate scope.
- Target matrices remain distinct from workspace affected selection.
- Destination-only tools do not generate host-PATH false failures.
- Missing/error/incompatible/unsafe/unsupported preflight results make the
  report unhealthy; deferred/planned/opaque informational states do not.
- MP09 remains the boundary for using declared/inferred input/output scopes to
  schedule graph nodes in parallel and reserve writes safely.

## Validation evidence

Broad MP07/08 focused acceptance/regression gate:

- 683 pytest tests passed
- 45 unittest subtests passed
- 0 failures

Dedicated MP07/08 contracts after the final surface/provisioning additions:

- 18 pytest tests passed
- 0 failures

The gate covers MP07/08, MP05/06, MP03/04, MP01/02, TS00, configuration, CLI,
completion/manpage, project runners, validation sessions, execution
environments, Termux/chroot/SSH adapters, TUI/interactive validation, HUD,
summary, IPC, Tasker, validation control/progress/resource precedence, GTK
Control Center/HUD, doctor/profile/artifact architecture, and global
configuration. `git diff --check`, Python compile checks, and completion-source
synchronization also pass.
