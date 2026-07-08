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

# Use a normal Termux:GUI Activity instead of an overlay
devtool --hud-no-overlay --auto-hud --hud-mode android-gui ...
devtool hud --mode android-gui --no-overlay
```
