# Devtool Live HUD

Android companion for devtool. Version 1.2 adds direct Termux command execution, a native Tasker/Locale action plug-in, app settings, optional Android Monet colors, and the same icon/palette used by the desktop GTK HUD.

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
4. Open Devtool Live HUD and tap **Grant Termux command permission**. Android lists it as **Run commands in Termux environment** under additional permissions.
5. Configure the command and working directory in the app.

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

## Tasker plug-in

In Tasker:

1. Add an action.
2. Choose **Plug-in**.
3. Choose **Devtool Live HUD** → **Run devtool in Termux**.
4. Configure command, working directory, background mode and overlay preference.
5. Save the action once after installing or upgrading the app. The editor adds a private per-install authorization token to Tasker’s saved plug-in bundle, preventing other apps from using the exported Locale receiver as a Termux command proxy.

The action uses the standard Locale/Tasker edit and fire intents. Tasker waits for Termux completion and receives:

```text
%devtool_execution_id
%devtool_exit_code
%devtool_stdout
%devtool_stderr
%devtool_error
```

A non-zero command exit or Termux internal error returns a failed Tasker plug-in result.

## Secure live HUD bridge

The existing Termux-to-app live update bridge remains token protected.

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
