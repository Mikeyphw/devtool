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
- Android HUD rendering uses the native companion notification bridge or Termux:API fallback. A separate floating overlay is available only when explicitly enabled.
- The notification shows a live text and graphical progress bar, phase/repository/target context, tests, warnings, errors, duration, commit hash, and copy/dismiss actions.
- Legacy `android-gui`, `termux-gui`, and `android-overlay` mode names remain accepted and map to `android-notify`.
- Clipboard output is skipped for missing-artifact failures and interrupted/stale-transaction recovery guards.

Validated with 209 tests.

## Built-in Android Live HUD source

The complete Android app source is included under:

```text
android/devtool-live-hud/
```

Build it from the repository root with:

```bash
./build-android-hud.sh
```

The wrapper uses the bundled app path while `android/devtool-live-hud/build.sh` retains the standalone defaults supplied for Ubuntu amd64. Every build default can be overridden by a command-line flag; run either script with `--help`.

When the app is installed and its token is saved at `~/.config/devtool/android-live-hud.token`, `android-notify` uses Android 16 `Notification.ProgressStyle` through the companion app. If the bridge is unavailable, devtool falls back to `termux-notification`. Desktop HUD flags still translate automatically on Termux.

The companion app also provides an optional proper `TYPE_APPLICATION_OVERLAY` HUD. Grant display-over-apps permission, enable the overlay service in the app, then add:

```bash
--hud-overlay
```

Notification-only remains the default. Use `--hud-no-overlay` to state that explicitly.

