# EXO Target-Local Jobs and Workflows

Devtool supports repository-wide jobs under `[jobs.<name>]` and target-local jobs under `[targets.<target>.jobs.<name>]`.

Target-local jobs are owned by one target. The target can reference them by short name from `[targets.<target>.workflows]`:

```toml
[targets.dustui_android]
runner = "android"
platform = "android"
root = "android/DustUI"

[targets.dustui_android.jobs.contracts]
runner = "command"
command = ["python3", "scripts/validate_ov01_contract.py", "--audit-loop"]
inputs = ["scripts/validate_ov01_contract.py"]
outputs = ["build/evidence/contracts.json"]

[targets.dustui_android.jobs.android_test]
gradle_tasks = [":app:testDebugUnitTest"]

[targets.dustui_android.workflows]
fast = ["job:contracts", "job:android_test"]
```

Resolution rules:

- A target workflow resolves `job:<name>` against that target's local jobs first.
- Repository-wide `[jobs.<name>]` remain available to every target.
- Target-local jobs are stored with an internal key of `<target>.<job>` for execution, scheduling, evidence, and machine output.
- Invalid schemas fail while loading configuration, before build/test execution begins.

Execution modes:

- `command = ["tool", "arg"]` declares structured argv.
- `shell_command = "..."` is explicit shell mode.
- `action = "test"` delegates to the job runner action.
- `gradle_tasks = [":app:testDebugUnitTest"]` is shorthand for a Gradle/Android command job and defaults the runner to `gradle` when no runner is declared.

Target-local command jobs without an explicit `root` execute from the target root. Repository-wide jobs without an explicit `root` execute from the repository root.

## Discovery and preflight truth

Target-local jobs appear in both repository-wide and per-target views:

```bash
devtool -r "$REPO" jobs --json
devtool -r "$REPO" targets --json
devtool -r "$REPO" workflows --json
devtool -r "$REPO" preflight --workflow fast --json
```

`jobs --json` exposes:

- `jobs`: all executable job keys, including target-local internal keys such as `dustui_android.contracts`.
- `target_jobs`: per-target local names such as `contracts`, mapped to the same normalized job payload.

Target-local jobs without an explicit `root` are planned, scheduled, and preflighted from the owning target root. Repository-wide jobs still default to the repository root. This keeps `inputs` and `outputs` scoped consistently across workflow execution, safe scheduling, and toolchain reports.


## Runtime provenance and scheduler identity

Target-local job references keep both the author-facing reference and the resolved execution identity:

- `workflow_ref` remains the configured value, such as `job:contracts`.
- `workflow_resolution_scope` is `target` for `[targets.<target>.jobs.<name>]` jobs and `repository` for `[jobs.<name>]` jobs.
- `workflow_resolution_target` contains the owning target for target-local jobs.
- `workflow_resolution_local_name` contains the local job name, such as `contracts`.
- `workflow_resolution_key` contains the executable key, such as `dustui_android.contracts`.

These fields are present in workflow graph JSON, command-spec metadata, and safe-scheduler plan nodes. That means logs, EXO events, execution plans, and machine clients can display `job:contracts` while still correlating it with the unambiguous executable key `dustui_android.contracts`.

## Execution affinity

Target-local jobs use the owning target as their execution context:

- Omitted `root` means the job runs from the target root.
- Relative explicit `root` values inside `[targets.<target>.jobs.<name>]` are resolved relative to that target root, not the repository root.
- `inputs` and `outputs` remain relative to the job's effective root for scheduler/preflight safety checks.
- A job without its own `[env]` inherits the target environment.
- An explicitly empty `[targets.<target>.jobs.<name>.env]` clears inherited environment values for that job.
- `gradle_tasks = [...]` target-local jobs run from the target root and keep their Gradle task list in runtime metadata.

Example:

```toml
[targets.dustui_android]
runner = "android"
root = "android/DustUI"

[targets.dustui_android.jobs.contracts]
runner = "command"
root = "tools/contracts" # resolves to android/DustUI/tools/contracts
command = ["python3", "check.py"]
outputs = ["out/contracts.json"]
```

Runtime command metadata includes `workflow_effective_cwd` plus normalized job identity fields such as `workflow_job_scope`, `workflow_job_target`, `workflow_job_local_name`, `workflow_job_key`, and `workflow_job_target_local`.

## Workflow ownership boundaries

Target-local jobs are discoverable by their executable key, but they are not a cross-target API. A workflow may reference its own target-local job by either the local name or the scoped key:

```toml
[targets.web.jobs.assets]
runner = "command"
command = ["python3", "assets.py"]

[targets.web.workflows]
short = ["job:assets"]
scoped = ["job:web.assets"]
```

Another target must not call that job directly with `job:web.assets`. Cross-target orchestration should call the owning target workflow instead:

```toml
[targets.api.workflows]
fast = ["target:web#fast"]
```

This keeps ownership, target root, environment inheritance, scheduler identity, and EXO provenance attached to the target that declared the job. Invalid foreign target-local job references fail during configuration load, before any command is executed.

## Completion and doctor surfaces

EXO TLJ-06 exposes target-local jobs to editor and shell tooling without making
foreign target-local jobs executable from the wrong owner. Use these read-only
completion categories:

```bash
devtool -r "$REPO" completion-state --kind target-jobs
devtool -r "$REPO" completion-state --kind target-job-keys
devtool -r "$REPO" completion-state --kind target-job-refs
```

The values have different purposes:

- `target-jobs` emits `target:local_name` pairs for human pickers.
- `target-job-keys` emits internal executable keys such as `dustui_android.contracts`.
- `target-job-refs` emits workflow reference snippets such as `job:contracts`,
  `job:dustui_android.contracts`, and `dustui_android:job:contracts`. The
  unqualified `job:*` values are valid only inside the owning target workflow;
  cross-target composition still goes through `target:<target>#<workflow>`.

`devtool jobs --json` now includes a `completion` object with the stable
`target_job_keys` and `target_job_refs` lists. `devtool doctor --json` includes
`target_jobs` and `target_job_keys`, and the text doctor reports whether the
repository uses target-local jobs/workflows.

## Job inspection and target filtering

EXO TLJ-07 makes `devtool jobs` truthful for repositories that use only
`[targets.<target>.jobs.*]` declarations. The human output now lists both
repository-wide jobs and target-local jobs, using the executable key for the row
name and the owning target/local name in the details.

```bash
devtool -r "$REPO" jobs
devtool -r "$REPO" jobs --target dustui_android
devtool -r "$REPO" jobs --target dustui_android --json
```

`--target <name>` filters only the target-local job view. Repository-wide jobs
remain visible because they are reusable by any target. The JSON payload uses
schema version 4 and includes:

- `selected_target`: the requested target filter, if any.
- `summary.repository_jobs`: repository-wide jobs only.
- `summary.target_local_jobs`: target-local jobs in the filtered view.
- `repository_jobs`: repository-wide job payloads.
- `target_jobs`: target-local payloads grouped by target.
- `completion.target_job_keys` and `completion.target_job_refs`: filtered to the
  selected target when `--target` is used.

Unknown target names fail before producing partial output so scripts do not
silently validate the wrong target-local job set.

## Plan export and dry-run identity

EXO TLJ-08 upgrades `devtool plan` machine output to schema version 2 for
workspace targets. The plan remains a dry-run operation, but every generated
command now carries the same normalized target-local job identity that runtime
commands use:

```bash
devtool -r "$REPO" plan fast --target dustui_android --json
devtool -r "$REPO" plan fast --target dustui_android --explain
devtool -r "$REPO" plan fast --target dustui_android --dot
```

The JSON payload includes a `summary` object with target-local and repository
job counts, the `target_local_jobs` entries involved in the plan, and the set of
`targets_with_target_local_jobs`. Each command entry now includes:

- `workflow_target`, `workflow_name`, and `workflow_ref`.
- `resolution.scope`, `resolution.target`, `resolution.local_name`, and
  `resolution.key` from workflow parsing.
- `job.scope`, `job.target`, `job.local_name`, `job.key`, and
  `job.target_local` from the execution job metadata.
- top-level `target_local_job` for simple shell/JQ filtering.

Human `--explain` output now includes the normalized job key beside each
scheduler node. DOT output labels target-local job nodes with `job:<target>.<job>`
so cross-target previews make the owning target visible before anything runs.

## Preflight export and toolchain identity

EXO TLJ-09 upgrades `devtool preflight --json` to schema version 2 for
workspace target analysis. Preflight now reports the target-local jobs involved
in the selected workflow before any command runs:

```bash
devtool -r "$REPO" preflight --target dustui_android --workflow fast --json
devtool -r "$REPO" preflight --target dustui_android --workflow fast
```

The JSON payload includes:

- `summary.target_local_job_count` and `summary.repository_job_count`.
- `summary.targets_with_target_local_jobs` for cross-target workflows.
- `target_local_jobs[]` entries with `key`, `target`, `local_name`, `runner`,
  `workflow`, and `node`.
- `repository_jobs[]` entries for reusable repository-level jobs reached by the
  same workflow.
- `job:<target>.<job>` readiness checks so diagnostics can point to the owning
  target-local job rather than only to the expanded workflow node.

Human preflight output also includes compact `target-local jobs` and
`repository jobs` rows. This keeps plan, preflight, doctor, jobs inspection, and
completion surfaces aligned around the same target-local job identity contract.
