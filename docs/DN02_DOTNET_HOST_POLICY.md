# DN02 — reviewed native .NET host and job policy

Baseline: `Mikeyphw/devtool` at `3ea6b54470beccffdd00407b9fe8126d1ea16ed1` (DN01).

This compatibility phase keeps DN01's explicit opt-in for out-of-support .NET 6 projects and separates four identities:

1. Target framework (`net6.0` / `net6.0-windows` when reviewed).
2. Build SDK (`global.json`, exact SDK 8/9/10 pin, for example `10.0.100`).
3. Execution runtime (`Microsoft.NETCore.App` **6.0.36**, verified after setup).
4. Hosted qualification (NOT automatic: actual run and evidence required).

GitHub's `actions/setup-dotnet` installs SDKs rather than a standalone runtime. For an explicitly opted-in .NET 6 project, the generated workflow additionally installs **exact SDK 6.0.428** via the existing SHA-pinned `setup-dotnet` action; that archive contains .NET runtime 6.0.36. It does **not** become the build SDK: `global.json` still controls `dotnet --version`, checked independently before execution. Windows Desktop workloads additionally require the exact `Microsoft.WindowsDesktop.App` runtime; absent runtimes fail closed.

The native inspector now accepts reviewed CLI aliases `-c`, `-f`, `-r`, `-o` and records a canonical argv to compare against the actual native job. Dangerous MSBuild property injections, missing locked restore, shell interpolation, missing TRX declarations and duplicate switches remain rejected. Arbitrary environment variables remain blocked; only `DOTNET_CLI_TELEMETRY_OPTOUT=1`, `DOTNET_NOLOGO=1`, `DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1` and `NUGET_XMLDOC_MODE=skip` can appear in job `env` for .NET jobs. Host runtime evidence records run, attempt, node and source commit identifiers; it is local execution evidence, not independently authenticated provenance.

Important limitations:
- .NET 6 has been **unsupported since November 12, 2024**; do not use compatibility mode for new projects.
- Legacy runtime setup downloads a separate EOL *SDK* because the pinned `setup-dotnet` action does not provide a runtime-only installation flag. This trade-off should be revisited as a hash-pinned runtime-only installation in DN03.
- This patch intentionally does not enable arbitrary nested job roots, dynamic MSBuild imports, free-form environment variables, cross-platform SDK resolution, or free-form CLI arguments.
- No GitHub workflow was dispatched, no .NET executable was run during source-only patch creation, and no release claim is made. Existing hosted Actions gates remain mandatory after commit.
