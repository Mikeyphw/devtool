# FC08 HOST-R1R13 — Hosted Windows target resolution promise audit

## Source and evidence

- Base: `275f5c69ea0b357e4cf203c59b98ca080d746bf5`; tree `cfba88065af84d15edb227c8f5a0e694af7d41fa`.
- G1: [run 40725317664](https://github.com/Mikeyphw/devtool/actions/runs/40725317664) **passed 24/24 shards at base**.
- FC08: [run 40725311281](https://github.com/Mikeyphw/devtool/actions/runs/40725311281) passed DAG/Linux/macOS and failed Windows with `No dotnet target resolved` before native build.

## Promise-by-promise disposition

| Promise | Status after patch | Evidence / boundary |
|---|---|---|
| Named Windows `.NET` app target declares its real solution | DELIVERED IN SOURCE | `windows_dotnet_fixture_config` includes `[targets.app] solution="FC08.sln"` |
| Preserve tracked-solution source guard and explicit CLI selection | DELIVERED IN SOURCE | Existing `devtool(... solution=...)` validates `git ls-files` and adds `--solution` |
| Prove effective restore/build/test commands address the solution | DELIVERED IN LOCAL TEST | `load_project_config` and real `DotnetRunner` planner; negative regression rejects argv-only configuration |
| Preserve actual Windows locked restores, TRX/coverage, pack, publish, MSVC | UNCHANGED, HOSTED OPEN | `windows()` still executes all stages; new hosted run required |
| Preserve real negative-exit propagation | UNCHANGED, HOSTED OPEN | `fail_negative` retained; new hosted run required |
| Preserve independently passing G1 regression | BASE PASSED, NEW SHA OPEN | Run 40725317664 passed; rerun 24 shards at new commit if closing gate |
| Validate native Termux install + transaction rollback | OPEN | Real native apply and source receipt required |
| Final hosted four-lane evidence summary | OPEN | Must include successful independent Windows execution and receipts; no synthetic completion |
| BKX R3, device/consumer FC08, FC09, release | OPEN | Independent qualification required |

## Root-cause reproduction

The previously shipped CLI flag alone did not work when a named target was selected. The resolver's `selected_target_name` branch takes `TargetConfig.effective_build_target()` before considering `explicit_solution`. A test restricted to CLI argument formation cannot detect this. No change to production target precedence is made by R1R13.
