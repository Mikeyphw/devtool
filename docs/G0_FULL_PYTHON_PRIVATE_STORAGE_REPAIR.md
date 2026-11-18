# G0 — Full Python regression: private RUN evidence writers

Source-bound target: `Mikeyphw/devtool`, draft PR #29, `feature/g0-native-validation-phase-contract-20261117`, commit `577cedb8d442160ad062802c185244f1a0b97e19`.

This native overlay repairs identified RUN-V3 XDG writer regressions without weakening `0700` authority checks. The canonical Android release evidence producer now calls `ensure_private_storage_dir()` for canonical RUN evidence before writing; a separately requested export remains an export, not a RUN authority. The repository-pack receipt writer also initializes private ancestry before persisting, and uses a `0600` temporary file created by `mkstemp()` instead of writing a briefly public temp file and chmod-ing afterwards. The synthetic cleanup fixture uses the same private-path authority, ensuring negative cleanup assertions are reached instead of failing during setup.

**Important:** This addresses one shared root-cause group in the 117-failure 24-shard run. It does not claim that every remaining independent failure is resolved: session fixtures, outdated XDG path assertions, Gradle takeover expectations, destructive-operation checks and presentation tests still require source-level remediation. Tests that originally fail on insecure existing directories must continue to fail closed. No local Gradle tasks.

## Local/native validation

The artifact declares a required checksummed Python-only shell validation script under `.devtool-artifact/`. Set `restore=false`, `build=false`, and `test=false` to suppress built-in Android phases while running explicitly declared Python suites. Before apply, run native `devtool validate-overlay` and `devtool overlay check` on a clean, exact-HEAD target. Native apply and remote changes are the operator's separate approved steps. An immutable exact-tree manifest must fail if the target changes.

## Evidence

Historical full 24-shard run: <https://github.com/Mikeyphw/devtool/actions/runs/44175763793>. Result: 117 failures; 3 passing shards, 21 failing shards, 0 skipped. Source-file Git blob IDs and full replacement payload hashes are declared in the archive inventory and exact-tree base contract. A new hosted full regression is needed after source-bound apply; a green PR check does not substitute for all 24 shards and fail-closed summary.
