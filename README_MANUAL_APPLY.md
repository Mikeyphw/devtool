# Manual apply

Extract this archive at the root of the devtool source repository:

```bash
cd "$HOME/Code/devtool"
unzip -o "$HOME/Downloads/devtool_concurrent_runs_overlay_manual.zip"
PYTHONPATH=. python -m pytest -q
```

Useful flags:

```bash
# Wait up to five minutes for overlapping file scopes (default)
devtool --lock-timeout 300 ...

# Fail immediately on a conflicting run
devtool --no-wait ...

# Disable path-aware concurrency for one invocation
devtool --concurrency off ...

# Android HUD through Termux:API notification
devtool --auto-hud --hud-mode android-notify ...
devtool hud --mode android-notify --follow-active

# Existing desktop/legacy Android mode names auto-translate on Termux
devtool --auto-hud --hud-mode desktop-window ...
devtool --auto-hud --hud-mode android-gui ...  # compatibility alias
