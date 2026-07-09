# Devtool Live HUD

Android companion for devtool. Version 1.5.0 is a complete Android frontend for devtool. It includes a visual workflow builder, live run dashboard, persistent history, direct Termux/chroot execution, a native Tasker/Locale action plug-in, optional Android Monet colors, and the same icon/palette used by the desktop GTK HUD.

## Android frontend

The portrait-first launcher now has four pages with persistent bottom navigation:

- **Overview** shows a readiness summary, dynamic permission/integration tiles, the latest live run, recent runs, and quick status/validation actions.
- **Run** builds devtool commands visually for overlay, bundle, build, test, validate, restore, status, doctor, commit, targets, history, and custom operations.
- **History** stores recent progress, commands, completion output, errors, timestamps, copy actions, and rerun actions.
- **Settings** contains appearance, dynamic permissions, overlay controls, the advanced raw-command runner, and secure bridge token management.

Permission and integration tiles react to the current device state. For example, notification access changes from **Grant access** to **Manage**, the floating HUD changes between **Enable** and **Disable**, and bridge pairing changes between **Install token**, **Update token**, and **Verify**.

App and Tasker launches export one stable `DEVTOOL_RUN_ID` plus the current bridge token. Direct Termux `android-bridge` runs automatically start the native notification renderer, so background notifications and floating HUD cards update from the first devtool event through completion without a separate bridge command.


## Paste and import commands

On the **Run** page, tap **Paste and import command**. The app parses terminal/chat-friendly commands such as:

```bash
cd "$HOME/Code/sims3tools" && \
DEVTOOL_HUD_SINGLETON=kill devtool \
  --copy --auto-hud --hud-mode desktop-window --yes \
  -r "$HOME/Code/sims3tools" \
  --target s3atelier_avalonia_S3Atelier_Modern \
  apply-overlay "$HOME/Downloads/example_overlay.zip" \
  --validate --commit "Apply overlay"
```

The importer fills repository, working directory, target, operation, artifact, validation, commit message, environment, chroot, clipboard and HUD settings. `desktop-window` is translated to `android-bridge`, because the command will run through the Android frontend. Unknown global and operation arguments remain visible under **Advanced** instead of being dropped.

For commands that should remain untouched, use **Settings → Command runner → Paste command from clipboard**.

## Presentation

- Native progress notifications on Android 8–15.
- Android 16 progress-style Live Updates, with promoted-ongoing requests where supported.
- Optional user-enabled `TYPE_APPLICATION_OVERLAY` HUD.
- Multiple concurrent runs, draggable/collapsible cards, diagnostics, tests and commit state.
- Notifications remain the fallback when the overlay cannot run.

The built-in palette matches GTK:

```text
background  #0B0F14
surface     #121923
text        #EEF4FF
muted       #8EA0B4
green       #5EE49B
cyan        #68D8FF
yellow      #FFD166
red         #FF6978
```

Enable **Use Android Monet colors** in the app to derive the primary surfaces and accents from the wallpaper on Android 12 or newer.

## Termux setup

1. Install Termux and open it at least once.
2. Add this to `~/.termux/termux.properties`:

```properties
allow-external-apps=true
```

3. Restart Termux.
4. Open Devtool Live HUD → **Settings**. In the **Termux command access** tile, tap **Grant access**. Android lists it as **Run commands in Termux environment** under additional permissions.
5. In the **Termux bridge token** tile, tap **Install token**. The app writes and verifies the secure token in Termux automatically; the tile changes to **Connected** when pairing succeeds.
6. Configure a workflow on the **Run** page, or use the raw command runner under **Settings**.

Commands are sent explicitly to:

```text
com.termux/com.termux.app.RunCommandService
```

The app uses `com.termux.permission.RUN_COMMAND`, a one-shot mutable `PendingIntent`, and the Termux result bundle. Background mode is enabled by default so stdout and stderr are returned separately.

The command is executed as:

```bash
$PREFIX/bin/bash -lc '<configured command>'
```

When **Request floating HUD** is enabled, the app exports `DEVTOOL_HUD_OVERLAY=1`; devtool treats that like `--hud-overlay` unless `--hud-no-overlay` is explicitly supplied.



## Optional root bridge

The app remains fully functional without root. An optional non-Zygisk KernelSU/Magisk module can bypass Termux `RunCommandService` for generated devtool workflows, continue to use the same live HUD token, retain per-run logs, and cancel active root-backed runs.

From **Overview** or **Settings**, open the **Devtool Root Bridge** tile:

- **Check availability** requests root and detects the module.
- **Export module ZIP** writes the embedded `devtool_root_bridge_module.zip` to a user-selected location.
- After installing the ZIP in KernelSU/Magisk/APatch and rebooting, **Pair root bridge** stores the app token in the protected module state.
- **Update pairing** appears automatically after the app token changes.
- **Verify** checks module version, enabled state, Termux UID, and token match.

Once connected, enable **Use optional root bridge** on the Run page or in a Tasker action. Raw command mode remains rootless by design; the root wrapper accepts only generated `devtool` or `devtool-androidos` commands. Root runs can be cancelled from History. Rootless Termux execution remains available at all times.


## Tasker plug-in

In Tasker:

1. Add an action.
2. Choose **Plug-in**.
3. Choose **Devtool Live HUD** → **Run devtool**.
4. Configure command, working directory, background mode and overlay preference.
5. Save the action once after installing or upgrading the app. The editor adds a private per-install authorization token to Tasker’s saved plug-in bundle, preventing other apps from using the exported Locale receiver as a Termux command proxy.

The action uses the standard Locale/Tasker edit and fire intents. Tasker can use either the rootless Termux backend or the optional paired root bridge. It waits for completion and receives:

```text
%devtool_execution_id
%devtool_exit_code
%devtool_stdout
%devtool_stderr
%devtool_error
```

A non-zero command exit or backend error returns a failed Tasker plug-in result.

## Secure live HUD bridge

The Termux-to-app live update bridge remains token protected. The recommended setup is now one tap:

1. Open **Overview** or **Settings**.
2. Find **Termux bridge token**.
3. Tap **Install token**.
4. Wait for the tile to become **Connected**.

The app writes the token through Termux RunCommandService, sets mode `600`, verifies the file contents and records the verified pairing state. If the app token is regenerated, the tile changes to **Update needed** and offers **Update token**. A connected tile offers **Verify**.

Manual recovery remains available:

```bash
mkdir -p ~/.config/devtool
printf '%s\n' 'PASTE_TOKEN_HERE' > ~/.config/devtool/android-live-hud.token
chmod 600 ~/.config/devtool/android-live-hud.token
install -m 755 tools/devtool-live-hud "$PREFIX/bin/devtool-live-hud"
```

Test it:

```bash
devtool-live-hud test
devtool-live-hud test-overlay
```

The bridge receiver accepts only explicit app-targeted updates with the current token. It does not expose a command-execution broadcast.

## Overlay lifecycle

The overlay is opt-in. Grant display-over-other-apps permission and enable it inside the app.

Android 15 and later can reject foreground-service starts from a background receiver when no overlay is visible. The bridge therefore stores the pending run and adds a **Show HUD** action to its progress notification. Tapping that action starts the overlay through a user-initiated path and displays the pending state.

## Build on Ubuntu amd64

From this directory:

```bash
./build.sh
```

From the combined devtool repository:

```bash
./build-android-hud.sh
```

Default requirements and targets:

- JDK 17+
- Android platform 36
- Build Tools 36.0.0
- Gradle tasks `clean assembleDebug`

The debug APK is written to:

```text
app/build/outputs/apk/debug/app-debug.apk
```

Use `./build.sh --help` for SDK, JDK, Gradle and output overrides.
