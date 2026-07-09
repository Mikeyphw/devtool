# Devtool Live HUD

Android companion app for devtool progress. It provides two presentation modes:

1. **Native progress notification** — always available after notification permission. Android 16 uses `Notification.ProgressStyle` and requests promoted ongoing treatment.
2. **Floating overlay HUD** — optional, draggable, top-right HUD displayed over other apps after the user grants display-over-apps permission and enables the overlay service.

The notification remains the default and fallback. Overlay updates are sent only when devtool is run with `--hud-overlay`.

## Features

- Android 16 segmented progress notification with restore/build/test/commit milestones.
- Standard native progress notification on Android 8–15.
- Optional `TYPE_APPLICATION_OVERLAY` HUD with multiple concurrent run cards.
- Draggable top-right window with collapse and disable controls.
- Per-run progress, phase, project/target, tests, diagnostics and commit hash.
- Ten-second completion/failure linger before a finished overlay card is removed.
- One notification and one overlay card per concurrent devtool run.
- Token-protected explicit broadcast receiver.
- Notification-only fallback if overlay permission/service is unavailable.
- No third-party Android runtime libraries.

## Build on Ubuntu amd64

The included `build.sh` uses these defaults:

- app directory: `$HOME/Code/devtool-live-hud`
- SDK directory: `$ANDROID_HOME` or `$HOME/android-sdk`
- command-line tools: `commandlinetools-linux-13114758_latest.zip`
- JDK package: `openjdk-17-jdk`
- Java home: `/usr/lib/jvm/java-17-openjdk-amd64`
- platform: `android-36`
- build tools: `36.0.0`
- Gradle tasks: `clean`, then `assembleDebug`

All defaults can be overridden by flags:

```bash
./build.sh --help
```

Typical standalone checkout:

```bash
./build.sh
```

From the combined devtool repository:

```bash
./build-android-hud.sh
```

Example custom build:

```bash
./build.sh \
  --app-dir "$HOME/Code/devtool/android/devtool-live-hud" \
  --sdk-dir "$HOME/Android/Sdk" \
  --java-home /usr/lib/jvm/temurin-17-jdk-amd64 \
  --platform android-36 \
  --build-tools 36.0.0 \
  --assemble-task assembleDebug
```

The default APK is written to:

```text
app/build/outputs/apk/debug/app-debug.apk
```

## Install and configure

1. Install the APK and open **Devtool Live HUD**.
2. Tap **Allow notifications**.
3. On Android 16, optionally enable promoted Live Updates.
4. Copy the bridge token into Termux:

```bash
mkdir -p ~/.config/devtool
printf '%s\n' 'PASTE_TOKEN_HERE' > ~/.config/devtool/android-live-hud.token
chmod 600 ~/.config/devtool/android-live-hud.token
install -m 755 tools/devtool-live-hud "$PREFIX/bin/devtool-live-hud"
```

Test notification-only mode:

```bash
devtool-live-hud test
```

## Enable the floating overlay

1. Open the app.
2. Tap **Allow display over other apps** and grant the special permission.
3. Return to the app and tap **Enable overlay HUD**.
4. Test it with **Send test overlay** or:

```bash
devtool-live-hud test-overlay
```

Use it from devtool:

```bash
devtool \
  --auto-hud \
  --hud-mode desktop-window \
  --hud-overlay \
  -r "$HOME/Code/sims3tools" \
  validate
```

Force notification-only mode:

```bash
devtool \
  --auto-hud \
  --hud-mode desktop-window \
  --hud-no-overlay \
  -r "$HOME/Code/sims3tools" \
  validate
```

The overlay service does not start until the user explicitly enables it in the app. `--hud-overlay` requests overlay presentation for a run; it does not silently grant permission or enable the service.

## Broadcast API

Receiver:

```text
io.github.mikeceead.devtoolhud/.BridgeReceiver
```

Actions:

```text
io.github.mikeceead.devtoolhud.UPDATE
io.github.mikeceead.devtoolhud.COMPLETE
io.github.mikeceead.devtoolhud.FAIL
io.github.mikeceead.devtoolhud.CANCEL
```

Every update requires `--es token TOKEN`. Pass `--ez overlay true` to include the run in the floating overlay. The helper script exposes this as `--overlay` and `--no-overlay`.
