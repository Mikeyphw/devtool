# Devtool concurrent run safety

This overlay adds path-aware concurrent execution. Independent runs can proceed in parallel when their read/write scopes do not overlap; parent/child path overlaps, snapshots, unknown whole-repo commands, and Git index/ref operations remain serialized.

Key behavior:

- Dotnet solution/project scopes include project source directories, project references, shared Directory.Build/Packages/NuGet files, and generated `bin`, `obj`, and `TestResults` paths.
- Overlay scopes come from artifact previews; snapshots claim the whole repository.
- Leases include PID/start-token liveness, heartbeats, stale cleanup, hierarchical path matching, `--lock-timeout`, `--no-wait`, and `--concurrency off`.
- Apply transactions use per-run active markers so one live run is never rolled back as another run's stale transaction.
- Artifact history is append-only under `.devtool/artifacts.d/`, with `.devtool/artifacts.json` retained as a generated compatibility index.
- Git add/commit uses a short dedicated lease after parallel validation finishes.
- HUD state keeps per-run files and summarizes multiple active runs instead of clearing another process's state.
- `--hud-no-overlay` and `devtool hud --no-overlay` force Termux:GUI to use a normal Activity.
- Clipboard output is skipped for missing-artifact failures and interrupted/stale-transaction recovery guards.

Validated with 190 tests.
