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
