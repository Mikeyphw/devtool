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
- `NodeRunner`
- `RustRunner`
- `GoRunner`
- `MavenRunner`
- `CMakeRunner`
- `MesonRunner`
- `MakeRunner`
- `GenericCommandRunner` (fallback)

The public compatibility functions `build_specs()` and `build_validation_specs()` remain available from `devtool.runners`. Existing callers do not need to change.

## Capabilities

Each runner reports only features currently connected to devtool through `RunnerCapabilities`. `devtool status` and `devtool doctor` show the selected runner and enabled capabilities.

A capability remains false until the corresponding behavior is implemented. Android/Gradle now exposes restore, build, local and instrumentation tests, selective tests, lint, full validation, cleanup, inspection, doctor diagnostics, artifact collection, and artifact verification. Python exposes manager-aware restore, selective tests, configured lint/type checks, PEP 517 packaging, and package verification.

## Validation optimization

`DotnetRunner` owns desktop/.NET validation optimization. When the build and
test targets resolve to the same solution or project, it plans one command:

```text
dotnet test <target>
```

The .NET CLI restores and builds that target before running tests, so separate
restore and build processes add no validation coverage. When targets differ,
devtool keeps a build and test command and only adds `--no-build` when the
targets are known to be identical. `--skip-restore` now also disables implicit
restore by adding `--no-restore`.

Custom commands preserve their original ordering. Set these target options when
a workflow requires the legacy shape or aggressive memory cleanup:

```toml
[dotnet]
coalesce_validation = false
explicit_restore = true
stop_after_run = true
```

Shared MSBuild/Roslyn servers are retained by default so later local or chroot
builds can reuse them. Standalone `devtool test` behavior remains unchanged.

Other first-class runners apply equivalent safe optimizations:

- Cargo and Go coalesce compile/test validation into one test invocation.
- Maven uses one parallel `verify` reactor.
- CMake and Meson retain out-of-tree build state and omit warm configure/setup passes.
- Node uses incremental prefer-offline installs rather than destructive clean installs by default.
- Make uses detected targets and memory-aware parallelism.
- Python reuses an existing managed virtual environment instead of recreating it.

All runners receive the shared desktop/Termux/chroot resource policy described
in [`BUILD_OPTIMIZATION.md`](BUILD_OPTIMIZATION.md).

## Variants

Targets select the project or app; variants select the build/test flavor inside
that target.

```bash
devtool --target mobile --variant release build
devtool --target mobile validate --matrix debug,release --matrix-mode variant
```

Variants are declared per target and merge into the same runner option sections
used by normal configuration:

```toml
[targets.mobile]
type = "android"
root = "apps/android"

[targets.mobile.variants.release.android]
variant = "release"

[targets.api]
type = "dotnet"
solution = "src/App.sln"

[targets.api.variants.release.dotnet]
configuration = "Release"
framework = "net9.0"
```

For .NET, built-in `debug` and `release` variants map to `-c Debug` and
`-c Release` when no explicit .NET variant configuration is provided. Android
variants feed existing Gradle task inference, such as `assembleRelease`,
`testReleaseUnitTest`, and `lintRelease`.

## Adding a runner

1. Subclass `ProjectRunner`.
2. Declare a stable `kind` and `RunnerCapabilities`.
3. Implement `build_specs()`.
4. Override validation optimization hooks only when the toolchain supports them safely.
5. Register the runner in `DEFAULT_RUNNER_REGISTRY`.
6. Add command-planning regression tests before connecting runner-specific execution or progress parsing.

## Execution environments

Runner selection is independent from execution-environment selection. Every first-class
runner can execute in native Termux, a chroot, or
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
