# Devtool native notification and toast controls

Defaults: **auto** — on Android, positively detecting the installed Devtool Live HUD package (debug or release) suppresses duplicate `termux-notification`, `termux-toast`, and Devtool bridge fallback toasts. If no companion is confirmed, existing notifications remain enabled. On desktop, notification output stays enabled by default. Discovery is read-only and never invokes `su`.

Global configuration at `~/.config/devtool/config.toml` (also respects `XDG_CONFIG_HOME` / `DEVTOOL_GLOBAL_CONFIG`):

```toml
[cli.notifications]
native_notifications = "off" # auto | on | off
native_toasts = "off"        # auto | on | off
```

Per-command or shell-wide environment (takes precedence):

```sh
export DEVTOOL_NOTIFICATIONS=off
export DEVTOOL_TOASTS=off
# or =on to force native output even with app installed; =auto restores default
```

These settings control **Devtool-produced OS notifications and toasts**, not its event journal or the Android Live HUD app's separately managed ongoing run notifications. Existing `DEVTOOL_APPLY_NOTIFICATION=0` and `--no-apply-notification` continue to disable all apply-completion output.
