# FC08 HOST-R1R17 — Windows MSBuild environment isolation audit

**Scope:** Exact source `3ec30a4eaf2ebfc2fa18274fc47bf1c0496bfa83` (tree `c31b0c93c1d09d7b5bca4716d2f36933367ce9b3`). Run 40987672093 fails in Windows job 123842504852. Linux, macOS and DAG succeeded.

## Promise-by-promise

| Promise | Status | Implementation / evidence |
| --- | --- | --- |
| Diagnose the observed Windows failure | Delivered | `MSB4126`: `Debug|x64` is not configured on the generated SDK solution. This is a restore-stage failure, not the previous TRX failure. |
| Limit changes to the Windows .NET fixture | Delivered | `dotnet_must` and selected-solution Devtool CLI strip only `Platform` and `Configuration` (case-insensitive) from inherited environment. |
| Do not alter native MSVC environment | Delivered locally | Existing CMake/MSVC child has `drop_msbuild_env=False`; regression asserts `Platform=x64` remains. |
| Keep authentic workload checks | Delivered in source | All eight named Windows checks retained; TRX/Cobertura and package checks unchanged. |
| Negative/regression coverage | Delivered locally | New R1R17 test suite mocks a Windows-style env for direct and nested CLI; complete FC08 subset run separately. |
| Real Windows success | Open | Requires a new hosted run on post-R1R17 commit SHA with actual artifact generation and full consolidated summary. |
| Release/consumer/device qualification | Open | Never inferred from local tests or this remediation. |

## Operational constraints

Source must be clean and at exact commit/tree above before applying. Retain all WIP, stashes, and existing Git remotes; create a new commit only after tests pass. If the source differs, fail closed rather than replaying the patch. Do not manufacture receipts or bypass real workloads.
