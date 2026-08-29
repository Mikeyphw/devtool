# Project runner architecture

Devtool separates project command planning from process execution.

- `devtool.project_runners` selects a project-type runner and converts an action into `CommandSpec` objects.
- `devtool.runners` executes those specifications, streams progress, writes logs, and performs transactional artifact operations.
- `ProjectConfig.kind` selects the runner. Ambiguous mixed repositories require an explicit kind or typed target; Devtool does not guess from nested projects.

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

## Build and package artifact contract

`build` and `package` share a runner-neutral artifact pipeline. `package` means a distributable output: the runner creates its native package, Devtool discovers the concrete output, verifies it, copies it atomically to the configured managed output directory when requested, and appends an immutable provenance event to `.devtool/artifacts/registry.json`. Python defers package registration until its wheel/sdist package check has completed. Android release validation uses the same verifier in its package phase instead of a separate artifact-only phase.

Common controls are available on both commands:

```bash
devtool build   -o dist/build -n app --artifact-collision unique --verify-artifacts
devtool package -o dist/release -n app --artifact-collision fail --verify-artifacts
```

Collision policy is `unique` by default; `replace` and `fail` are explicit alternatives. `--no-verify-artifacts` retains collection while disabling generic content verification when a workflow intentionally needs that behavior. For custom layouts, `[artifacts].paths` and `[artifacts].directories` declare outputs without adding runner-specific code; `[artifacts].patterns` and `[artifacts].recursive` refine directory discovery.

Native package defaults are used where the ecosystem defines one: PEP 517/uv for Python, Android/Gradle package tasks, `dotnet pack`, npm/pnpm/Yarn/Bun package tarballs, `cargo package`, Go binary packaging, Maven `package`, CPack, and `meson dist`. Gradle structured events also report concrete archive outputs from each producing task so the registry can distinguish produced outputs from reused/up-to-date ones.

### Go executable discovery

Go builds no longer use bare `go build ./...` as the executable-build contract.
For `devtool build` and `devtool package`, the Go runner performs `go list`
inside the selected execution environment, discovers every `package main`, and
builds each one with an explicit `-o` path. Development binaries go to
`.devtool/build/go/`; package binaries go to `.devtool/package/go/`. If no main
package exists, `build` falls back to a normal compile check while `package`
fails with an actionable error because there is no distributable executable.

The default build scope is `./...`. At a `go.work` root Devtool expands that
scope across every workspace main module before discovery. Duplicate binary
basenames are rejected instead of allowing Go's directory-output behavior to
overwrite one executable with another. Configure explicit targets/names when
needed:

```toml
[go]
packages = ["./..."]
build_targets = ["./cmd/server", "./cmd/worker"]
build_directory = ".devtool/build/go"
package_directory = ".devtool/package/go"
run_target = "./cmd/server"

[go.outputs]
"./cmd/server" = "my-server"
"./cmd/worker" = "my-worker"
```

`packages` remains the test/vet selection scope; `build_targets` controls only
executable discovery. Each produced binary is structurally verified and its
`go version -m` information is stored in the artifact registry, including the
module path/version, Go toolchain, GOOS, GOARCH, and VCS fields when Go embeds
them.

Go validation uses separate stages rather than treating `go test` compilation as a
substitute for a Devtool build. The default plan is restore → executable build →
`go test -json` → `go vet`. Devtool renders the JSON stream back into normal Go
test text in the durable log, while retaining package/test metadata for terminal,
JSON, and clipboard summaries. Failed tests are reported with package, test name,
`*_test.go:line`, message, package totals, and a focused rerun command instead of
falling through to `unknown_failure`. Rollback and cleanup are reported as recovery
actions and do not inflate successful workflow-stage counts.

The artifact registry is append-only at the production-event level. Rebuilding the same pathname creates a new record with its own run ID, Git commit, SHA-256, target, variant, producer task, verification state, and timestamp. Registry updates are locked and atomically replaced; malformed registry content is preserved as a corruption backup rather than silently discarded. Failed commands may expose outputs for diagnostics but never register those outputs as successful production events.

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

- Cargo coalesces compile/test validation where safe. Go deliberately keeps restore, real executable build/artifact verification, structured tests, and vet as separate validation stages so `validate` exercises the same executable-output contract as `build`.
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

Command targets can provide their own explicit lifecycle commands. This is
useful for mixed repositories where the default project has a first-class
runner, but individual packages use ad hoc packaging scripts:

```toml
[targets.browser_extension]
type = "command"
root = "extensions/browser-extension"
build_command = "python3 build.py"
test_command = "python3 -m pytest"
package_command = "python3 build.py --package"
```

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
