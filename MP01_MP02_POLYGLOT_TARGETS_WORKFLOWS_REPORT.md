# MP01 + MP02 Polyglot Target Identity and Reusable Workflows — Implementation Report

## Scope

This combined overlay implements the complete MP01 and MP02 roadmap slices on
top of TS00. It does not implement MP03 graph execution; MP03 consumes the
normalized configuration model delivered here.

## MP01 delivered

- Canonical `TargetConfig` replaces the misleading language-specific target
  model name; `DotnetTarget` remains a strict compatibility alias.
- Project and target identity now separate `runner` from `platform`.
- `ProjectConfig.runner` is canonical while `ProjectConfig.kind` remains
  synchronized for compatibility with existing runner/domain code.
- `[project].runner` and `[project].platform` are first-class configuration.
- `[project].kind`, target `type`, and target `kind` remain supported runner
  aliases.
- Conflicting canonical and legacy runner declarations fail early instead of
  silently selecting one.
- Target platform is an independent normalized slug and can differ from runner
  (`platform = "tv"`, `runner = "android"`).
- Auto-generated configuration prefers canonical runner/platform fields.
- Artifact-created .NET target registration now emits canonical target identity.
- Runner registry resolution consumes canonical runner identity.
- Config provenance records canonical, legacy, target, and detected runner
  sources.
- Doctor surfaces optional migration diagnostics without making legacy config
  unhealthy.
- Status, inspect, handoff, resolved config, doctor JSON, desktop project state,
  and GTK target cards expose the new identity.

## MP02 delivered

- Added `JobConfig`, `WorkflowConfig`, and `WorkflowStepRef`.
- Added top-level `[jobs.*]` parsing.
- Reusable jobs support:
  - runner ownership
  - root
  - one native runner action **or** structured argv **or** explicit shell command
  - env
  - execution environment
  - chroot/device metadata
  - input/output declarations
  - metadata
  - runner-specific option tables
- `command = "..."` is rejected with guidance to use structured argv or
  `shell_command`, preventing accidental shell semantics.
- Added `[targets.<name>.workflows]` parsing.
- Supported normalized references:
  - `job:<name>`
  - `runner:<runner>#<action>`
  - `target:<target>#<action>`
- Unknown/malformed jobs, runners, targets, actions, and references fail during
  config loading.
- Cross-target references are representable now; MP05 later supplies their
  runtime dependency semantics.
- Added `devtool jobs` / `jb`, `devtool workflows` / `wf`, and richer
  `devtool targets` / `tg`, including structured `--json` output.
- `devtool inspect` reports selected target workflows and reusable job execution
  modes.
- `config resolve` serializes the complete job/workflow model.
- GTK Control Center target cards show platform, runner, and workflows.
- Zsh completion includes the new inspection commands.

## Compatibility contract

- Existing repositories using `kind` and `type` continue to load.
- Existing `config.kind` consumers continue to see the effective primary runner.
- Existing runner planning is unchanged for repositories with no workflow
  declarations.
- Workflow declarations are not executed by legacy lifecycle calls in this
  overlay; this is the documented MP02/MP03 boundary, not a silent partial
  implementation.
- TS00 exact pytest selection remains unchanged and composes with the future
  workflow runtime.

## Documentation

Updated/added:

- `README.md`
- `CHANGELOG.md`
- `docs/CONFIGURATION.md`
- `docs/PROJECT_RUNNERS.md`
- `docs/POLYGLOT_TARGETS_AND_WORKFLOWS.md`
- config template comments
- zsh completion
- man page

## Tests

New contract suite:

`tests/test_mp01_mp02_target_job_workflow_contracts.py`

It covers canonical/legacy identity, platform-runner independence, reusable job
modes and metadata, workflow normalization and reference errors, machine
inspection, doctor migration diagnostics, desktop parity, and canonical template
generation.

The overlay manifest requests focused validation for MP01/MP02 plus TS00 and the
main config/CLI/runner/desktop/completion/artifact compatibility suites.

## Validation evidence

Focused MP01/MP02 integration and compatibility suites pass with **369 tests and 40 subtests** across configuration, runner resolution, CLI, completion/manpage, desktop target-model, TS00 selection, IPC, Tasker, and validation surfaces.

A broader deterministic regression sweep additionally completed groups 1–5 with **1,319 tests and 20 subtests passing** and no failures. The monolithic/split full-suite process contains inherited long-running teardown/hang cases in later groups in this container environment, so this intermediate overlay does not claim an FS03-style repository-wide seal. Full-suite qualification remains reserved for the roadmap final seal.
