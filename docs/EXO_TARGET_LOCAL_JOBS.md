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
