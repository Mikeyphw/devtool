# Manual update

Extract the updated repository over a clean devtool checkout, then reinstall the CLI and rebuild the Android app.

```bash
cd "$HOME/Code"
rm -rf devtool-new
mkdir devtool-new
unzip "$HOME/Downloads/devtool.zip" -d devtool-new
cd devtool-new
./install.sh
```

Build the Android frontend:

```bash
./build-android-hud.sh
```

After installing the APK, open the app and verify:

1. Notification permission.
2. Termux `RUN_COMMAND` permission.
3. **Termux bridge → Install token** or **Verify**.
4. Floating HUD permission only when the overlay is desired.

For a direct Termux run, use:

```bash
DEVTOOL_HUD_SINGLETON=kill devtool \
  --auto-hud \
  --hud-mode android-bridge \
  --yes \
  -r "$HOME/Code/sims3tools" \
  status
```

Do not use the removed `android-notify`, `--notify*`, `--tasker*`, or concurrency flags. Tasker actions are configured through **Tasker → Plugin → Devtool Live HUD**.
