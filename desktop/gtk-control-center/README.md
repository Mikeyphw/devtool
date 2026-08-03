# Devtool GTK4 Control Center

This is the GTK4/libadwaita desktop Control Center and compact HUD.

The desktop track includes:

- Overview
- Active runs
- History
- Artifacts
- Diagnostics
- Repository
- Targets
- Settings
- Run workflow

The Run page builds a typed `DevtoolCommandDraft`, previews argv and shell text,
and launches only after the command is visible. Runtime execution is argv-first,
not `sh -lc`, and the page streams live output with cancel and force-cancel.

## Run

```bash
devtool control-center --backend gtk4
devtool control-center --backend gtk4 --page run
# or
devtool-control-center
```

The compact HUD can be launched directly:

```bash
devtool-control-center-hud --follow-active
```

## Model snapshot versus real startup

`--once` is a model snapshot test. It does not prove that GTK mapped a window:

```bash
devtool control-center --backend gtk4 --once
```

Use startup tests for real GTK launch coverage. These map a window, render one
frame, record diagnostics, and close:

```bash
xvfb-run -a devtool control-center --backend gtk4 --startup-test
xvfb-run -a devtool-control-center-hud --startup-test --state-dir .devtool/gtk-hud-smoke
```

The release gate fails on uncaught exceptions, GTK/Adwaita warnings, and layout
overflow warnings such as a `GtkBox` requesting more height than the available
window.

## Logs and recovery

Startup and refresh failures are written to:

```text
~/.local/state/devtool/control-center.log
~/.local/state/devtool/hud.log
```

Startup error dialogs expose copy-details and open-log actions. Normal surfaces
also include Open log actions so packaging and launcher problems are discoverable
without starting the app from a terminal.

## Shortcuts

- `Ctrl+R`: refresh Control Center data
- `Ctrl+Q`: close the Control Center
- `Esc`: close dialogs or the HUD
- `Ctrl+C`: copy HUD summary
- `Ctrl+D`: copy HUD diagnostics
- `Ctrl+L`: open HUD log

## Dependencies on Ubuntu/Debian

```bash
sudo apt install python3 python3-gi python3-rich \
  gir1.2-gtk-4.0 gir1.2-adw-1 libgtk-4-1 libadwaita-1-0 xvfb
```

## Packaging

`devtool-control-center` and `devtool-control-center-hud` are GTK4 entry points.
The package builder stages the Python desktop modules, launchers, desktop
metadata, metainfo, and icons:

```bash
./package-deb.sh --verify
```

Verification now extracts the generated package and imports PyGObject, GTK4,
libadwaita, Rich, and Devtool through `/usr/bin/python3`, matching the shebang
used by the installed launchers. The desktop entry uses stable absolute launchers
under `/usr/bin`.

The installer refuses the deprecated clipboard-monitor service path instead of
writing a broken systemd unit for an unsupported CLI flag.
