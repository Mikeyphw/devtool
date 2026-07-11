# Devtool

Devtool applies project overlays, validates builds and tests, records transactions, produces handoff summaries, and can publish one live run to the desktop HUD or the Android Devtool Frontend.

## Devtool 0.3.3 behavior

Automatic artifact commits include the complete devtool-managed history registry (`.devtool/artifacts.json` and `.devtool/artifacts.d/`) so prior history records do not leave the repository dirty. Unrelated working-tree changes remain excluded.

### One active run

Devtool no longer supports concurrent operations inside the same repository. A repository has one simple run lock:

- A second operation fails immediately with a clear “another devtool run is already active” message.
- There are no path scopes, lease modes, wait policies, or concurrency flags.
- Apply, validate, rollback, commit, and tag operations are serialized.
- The Android frontend displays one active run. Completed runs remain available in History.

Separate repositories retain separate locks, so an unrelated repository is not blocked.

### Android integration belongs to the app

The old standalone Android notification and CLI Tasker paths have been removed:

- No `termux-notification` fallback.
- No `android-notify` HUD mode.
- No `--notify`, `--notify-progress`, or related notification flags.
- No `--tasker`, `--tasker-progress`, or `--tasker-broadcast` flags.
- No CLI broadcast integration for Tasker.

Use the Android Devtool Frontend for:

- The single live Android notification.
- The optional floating HUD.
- Visual command building and history.
- Native Tasker plug-in actions.
- Rootless Termux `RUN_COMMAND` execution.
- The optional KernelSU/Magisk root bridge.

The foreground-service notification required by Android while the floating overlay is running remains part of the app.


## Self-contained artifacts

Artifact schema v2 can carry the repository identity, monorepo workspace, target, solution, file inventory, validation policy, disposable validation script, and final commit message. A normal apply can therefore be reduced to:

```bash
DEVTOOL_HUD_SINGLETON=kill devtool \
  --copy --auto-hud --hud-mode desktop-window --yes \
  -r "$HOME/Code/sims3tools" \
  apply-overlay "$HOME/Downloads/example_overlay.zip"
```

CLI options still override artifact values. Use `--no-validate`, `--no-commit`, or `--no-artifact-script` for explicit opt-outs. See [`docs/ARTIFACTS.md`](docs/ARTIFACTS.md) for the complete overlay and bundle formats.

## Typical Android command

After pairing the secure bridge in the app, commands launched directly from Termux use:

```bash
cd "$HOME/Code/sims3tools"

DEVTOOL_HUD_SINGLETON=kill devtool \
  --copy \
  --copy-via termux \
  --auto-hud \
  --hud-mode android-bridge \
  --yes \
  -r "$HOME/Code/sims3tools" \
  --target s3atelier_avalonia_S3Atelier_Modern \
  apply-overlay "$HOME/Downloads/example_overlay.zip" \
  --validate \
  --commit "Apply example overlay"
```

`--auto-hud --hud-mode android-bridge` starts the app forwarder. The app decides whether to show only its live notification or also the floating overlay. Use `--hud-overlay` to request the overlay for one command or `--hud-no-overlay` to suppress it.

## Desktop HUD

Use:

```bash
devtool --auto-hud --hud-mode desktop-window ...
```

The desktop HUD binds to the current run before rendering, filters events by run ID, suppresses duplicate generic errors, and reports a successful rollback as recovery rather than as the cause of failure.

The GTK desktop window starts in a compact, pipeline-focused mode. It shows separate live operation segments for extract, restore, build, test, artifact validation, commit, and rollback as applicable. Each segment has its own pending/running/succeeded/failed/skipped state and progress. Live **ERRORS** and **WARNINGS** totals remain visible beside the active operation, along with Copy summary/Open log/Close actions. Select **Expand** to reveal recent events and diagnostic details; the choice is preserved for the lifetime of that HUD window. Set `DEVTOOL_DESKTOP_HUD_START_EXPANDED=1` to launch expanded.

## Validation sequence

For .NET targets, validation uses:

```bash
dotnet restore ...
dotnet build --no-restore ...
dotnet test --no-build ...
```

The same normalization is applied to recognizable explicit .NET build and test commands.

## Android frontend

Source is under:

```text
android/devtool-live-hud/
```

Build from the repository root:

```bash
./build-android-hud.sh
```

The portrait-first app includes the same structured operation pipeline in its active-run card, floating overlay, and Android 16 progress notification. Warning/error totals update from the current event stream rather than waiting for final completion.

The portrait-first app includes:

- Overview, Run, History, and Settings pages.
- State-aware permission and integration tiles.
- One-button Termux token installation, update, and verification.
- GTK colors by default and optional Monet colors.
- Clipboard command import into the visual builder.
- Native Tasker plug-in support configured inside the app.
- One active live run, with stale abandoned entries ignored automatically.

## Optional root bridge

Rootless Termux execution remains the default. An optional non-Zygisk KernelSU/Magisk/APatch module is under:

```text
root-module/devtool-bridge/
```

Build it with:

```bash
./build-root-bridge-module.sh
```

The module provides authenticated generated-devtool execution, chroot access, logs, and cancellation. It does not make root mandatory and does not expose a general root shell through the frontend.

## Successful apply summaries

Clipboard, HUD, history, completion payloads, and app results name the applied overlay or ordered overlay set:

```text
Applied overlay: example_overlay.zip
```

or:

```text
Applied overlays: 2
Overlay 1/2: first_overlay.zip
Overlay 2/2: second_overlay.zip
```

## Install the CLI

```bash
cd "$HOME/Code/devtool"
./install.sh
```

Verify:

```bash
devtool --version
devtool --help
```

## Removed option migration

Replace older commands as follows:

```text
--hud-mode android-notify     → --hud-mode android-bridge
--notify / --notify-progress  → configure notifications in the Android app
--tasker* flags               → configure the native Tasker plug-in action in the app
--concurrency / --lock-timeout / --no-wait → removed; one run is enforced
```
