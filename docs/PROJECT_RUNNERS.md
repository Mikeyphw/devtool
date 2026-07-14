# Project runner architecture

Devtool separates project command planning from process execution.

- `devtool.project_runners` selects a project-type runner and converts an action into `CommandSpec` objects.
- `devtool.runners` executes those specifications, streams progress, writes logs, and performs transactional artifact operations.
- `ProjectConfig.kind` selects the runner. Unknown or mixed project kinds fall back to explicitly configured commands.

Registered runners are:

- `DotnetRunner`
- `AndroidGradleRunner`
- `JvmGradleRunner`
- `PythonRunner`
- `GenericCommandRunner` (fallback)

The public compatibility functions `build_specs()` and `build_validation_specs()` remain available from `devtool.runners`. Existing callers do not need to change.

## Capabilities

Each runner reports only features currently connected to devtool through `RunnerCapabilities`. `devtool status` and `devtool doctor` show the selected runner and enabled capabilities.

A capability remains false until the corresponding behavior is implemented. Android/Gradle now exposes restore, build, local and instrumentation tests, selective tests, lint, full validation, cleanup, inspection, doctor diagnostics, artifact collection, and artifact verification. Python exposes manager-aware restore, selective tests, configured lint/type checks, PEP 517 packaging, and package verification.

## Validation optimization

`DotnetRunner` owns the existing optimized validation pipeline:

```text
dotnet restore <target>
dotnet build --no-restore <target>
dotnet test --no-build <target>
```

Recognizable explicit `dotnet build` and `dotnet test` commands receive the same validation-only optimization. Standalone `devtool test` behavior is unchanged.

## Adding a runner

1. Subclass `ProjectRunner`.
2. Declare a stable `kind` and `RunnerCapabilities`.
3. Implement `build_specs()`.
4. Override validation optimization hooks only when the toolchain supports them safely.
5. Register the runner in `DEFAULT_RUNNER_REGISTRY`.
6. Add command-planning regression tests before connecting runner-specific execution or progress parsing.

## Execution environments

Runner selection is independent from execution-environment selection. A .NET,
Python, or custom-command runner can execute in native Termux, a chroot, or
desktop Linux through the shared model documented in
[`EXECUTION_ENVIRONMENTS.md`](EXECUTION_ENVIRONMENTS.md).

Validation command specifications carry pipeline position and execution
metadata into logs, machine summaries, the desktop app, and the Android HUD.

## Android backend

Android and Gradle runners share the self-contained builder documented in [`ANDROID_PROJECTS.md`](ANDROID_PROJECTS.md). The compatibility `build-apk` command is only a frontend to that shared implementation.

## Test catalog and Python runner

The common test catalog and selector translation are documented in
[`TEST_SELECTION.md`](TEST_SELECTION.md). Python environment, lint, package, and
validation behavior is documented in [`PYTHON_PROJECTS.md`](PYTHON_PROJECTS.md).
