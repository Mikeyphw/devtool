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

Validated with 240 tests.

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

When the app is installed, open **Overview** or **Settings** and use the **Termux bridge token** tile. Its action changes from **Install token** to **Update token** or **Verify** according to the live pairing state. The app writes `~/.config/devtool/android-live-hud.token` through Termux, applies mode `600`, verifies the value, and then marks the bridge **Connected**. If the bridge is unavailable, devtool falls back to `termux-notification`. Desktop HUD flags still translate automatically on Termux.

The companion app also provides an optional proper `TYPE_APPLICATION_OVERLAY` HUD. Grant display-over-apps permission, enable the overlay service in the app, then add:

```bash
--hud-overlay
```

Notification-only remains the default. Use `--hud-no-overlay` to state that explicitly.


## Android frontend 1.5.0

The bundled Android app now follows the portrait-first mock: a compact Overview, state-driven setup tiles, visual Run builder, persistent History, advanced Settings, and bottom navigation. Permission and integration tiles show their actual state and change actions dynamically between **Grant access**, **Manage**, **Enable**, **Disable**, **Install token**, **Update token**, and **Verify**. App-launched runs share a stable run ID with devtool and direct Termux `android-bridge` mode automatically starts the native notification renderer, fixing frozen “Waiting for devtool progress” notifications and HUD cards.

The desktop GTK HUD also isolates events by the active run ID, retains the original failing phase when a recovery rollback runs, labels successful rollback as recovery, and suppresses duplicate generic error lines. During .NET validation, build is invoked with `--no-restore` after restore and test with `--no-build` after build, including explicit recognizable dotnet commands.

### Clipboard command import

The Android **Run** page can paste a complete shell command from the clipboard and map it into the visual builder. The importer accepts multiline commands with `\` continuations, a leading `cd ... &&`, environment assignments, quoted `$HOME` paths, direct `devtool` or `devtool-androidos` invocation, and normal global/subcommand flag ordering. Desktop HUD modes are converted to `android-bridge`; unknown flags are retained in advanced argument fields. The raw runner in Settings also has an exact paste action.


## Optional KernelSU/Magisk root bridge

Rootless Termux `RUN_COMMAND` remains the default and the Android app works without root. For devices using KernelSU, KernelSU Next, Magisk, or APatch, the repository also includes an optional non-Zygisk module under:

```text
root-module/devtool-bridge/
```

Build its installable ZIP with:

```bash
./build-root-bridge-module.sh
```

The Android app embeds the same module ZIP. Open **Overview** or **Settings**, tap **Devtool Root Bridge**, then:

1. **Check availability**.
2. If missing, choose **Export module ZIP**, install it in the root manager, and reboot.
3. Grant Devtool Live HUD root permission.
4. Tap **Pair root bridge**.
5. Enable **Use optional root bridge** for generated workflows or Tasker actions.

The module does not inject into Zygote or applications. It installs no `/system` files, declares `skip_mount`, uses `service.sh` only to initialize a protected wrapper under `/data/adb/devtool-bridge`, and preserves the rootless Termux backend as fallback. The wrapper accepts authenticated `devtool` and `devtool-androidos` commands only, records logs and PID files, and supports cancellation.

## Applied overlay names in success summaries

Successful apply summaries now identify the actual overlay or ordered overlay set. This applies to automatic clipboard output, `--copy-summary`, desktop/Android HUD copy actions, history, notifications backed by the completion payload, and Tasker stdout/results. Single applies show `Applied overlay: <name>`; bundles list each overlay in execution order with target and artifact path when available.
