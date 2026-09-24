# Repository wrappers (`devtoolw`)

Devtool repository wrappers let a repository expose its own stable command vocabulary while still executing through Devtool's normal, audited CLI and EXO pipeline.

A generated `devtoolw` launcher is intentionally thin. It resolves the repository root and delegates to:

```sh
devtool --repo "$repo_root" wrapper exec "$@"
```

Wrapper execution therefore does not create a second scheduler or a second run store. The wrapper resolves repository-facing arguments into canonical Devtool argv, forwards WRAP-04 invocation provenance, and re-enters the existing CLI path.

## Configure commands

Wrapper configuration lives in repository-local `.devtool.toml` only.

```toml
[wrapper]
schema = 1
default_command = "build"

[wrapper.commands.build]
description = "Build the Android app"
devtool = "build"
target = "android"
allow_passthrough = true

[wrapper.commands.codegen]
description = "Regenerate generated sources"
workflow = "codegen"
target = "android"
```

A command must declare exactly one of `devtool` or `workflow`. Native `devtool` commands compile to commands such as `build`, `test`, or `validate`. Workflow commands compile to `invoke <workflow>`.

## Discover and inspect

Use these commands from the repository root:

```sh
devtool wrapper describe
devtool wrapper describe --json
devtool wrapper discover
devtool wrapper discover --json
devtool wrapper resolve build -- --stacktrace
devtool wrapper resolve --json build -- --stacktrace
```

The generated launcher supports polished help directly:

```sh
./devtoolw --help
./devtoolw build --help
./devtoolw --version
```

## Install launchers

```sh
devtool wrapper init
devtool wrapper status
devtool wrapper verify
devtool wrapper update
```

`init` refuses to overwrite unmanaged or drifted files unless `--force` is supplied. `update` refreshes only Devtool-managed launchers in place; it also refuses unmanaged files and symlinks unless `--force` is explicit. Forced replacement removes a launcher symlink itself instead of following it.

## Completion

`completion-state` exposes wrapper data for shell/editor integrations:

```sh
devtool completion-state --kind wrapper-commands
devtool completion-state --kind wrapper-aliases
devtool completion-state --kind wrapper-options
devtool completion-state --kind all --format json
```

## Safety model

Wrapper bindings are declarative argv transformations, not shell snippets. Devtool rejects unsafe wrapper binding attempts such as repository routing controls, including attached spellings like `--target=value`, `--repo=value`, `-rPATH`, and `-cPATH`, internal provenance flags, nested `--` separators, and NUL/newline values. Dynamic `{value}` expansion is checked again after expansion so an otherwise-generic binding cannot turn user input into a routing control.

Sensitive-looking wrapper options such as `token`, `secret`, `password`, `credential`, `api_key`, and `auth` are redacted in human and machine resolution surfaces. The in-process `WrapperResolution` retains raw argv only for execution; JSON/machine serialization redacts sensitive option values and all argv projections.

## Final seal

The wrapper final seal verifies that all wrapper-facing surfaces agree with the same repository contract:

```sh
devtool wrapper seal
devtool wrapper seal --json
```

The seal checks launcher status, wrapper documentation, discovery metadata, completion state, help rendering, non-interactive resolution, and doctor integration. It is read-only and returns a non-zero exit code when any required wrapper surface is missing or inconsistent.
