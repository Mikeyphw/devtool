# DN01 — RM-09 .NET compatibility and selected-project locks

**Baseline:** `Mikeyphw/devtool`, `main`, commit `b164463db51e2cffd12f753043b9e55a08cc43ae`.

## Compatibility contract

RM-09 may statically recognize `net6.0` and `net6.0-windows` target frameworks. This is distinct from the SDK used to build them: `global.json` must continue to pin a reviewed stable .NET 8, 9, or 10 SDK. The provider must not claim that a .NET 6 runtime is installed or that .NET 6 is maintained or supported.

For a native job selecting any project in the transitive project-reference closure that targets .NET 6, opt in explicitly in its **job metadata**:

```toml
[targets.app.jobs.build.metadata]
ci_dotnet_allow_eol = true
```

The provider records `legacy_tfms`, `eol_opt_in`, and `required_runtime_families`. `required_runtime_families = ["6.0"]` describes a future host provisioning requirement, **not verification that the runtime exists**. Runtime installation, host validation, and evidence binding belong to DN02/DN03.

## Source review

RM-09 locates the nearest `Directory.Build.props` for each tracked `.csproj`, following MSBuild's implicit nearest-file behavior. This file must be tracked, non-symlinked and syntactically static, without imports, conditional property groups, MSBuild interpolation or executable directives. Relevant literal values can provide framework, runtime, Windows and package identity defaults. Inline project declarations take precedence. The exact `.props` bytes become source-bound evidence.

Tracked `packages.lock.json` files are now required and parsed for the **selected** project or solution and its transitive `ProjectReference` closure, rather than for unrelated projects. An unselected project's missing or malformed lockfile cannot invalidate a job for an independent selected project. Windows-style relative `ProjectReference` paths are normalized; absolute paths, wildcards, property expressions, UNC paths and directory escapes remain forbidden.

## Explicit limits

- The static graph still inventories and validates other tracked projects/solutions, including their TFM declarations. Only the **lockfile** requirement is selection-scoped in DN01.
- This is not an MSBuild evaluator; conditional imports/props require separate qualification.
- No extra CLI aliases, custom environments, host provisioning, or generated hosted workflow runtime installation is introduced yet.
- No release qualification is asserted. Local source tests and a hosted GitHub acceptance run remain necessary.

## Regression commands

```sh
python3 -m pytest -q tests/test_rm09_dotnet.py tests/test_dn01_dotnet_compat.py
python3 -m pytest -q tests/test_fc06_dotnet_shared_restore.py tests/test_rm06_rm09_consolidated_closure.py
git diff --check
```
