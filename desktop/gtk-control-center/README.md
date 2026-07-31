# Devtool GTK4 Control Center

This is the GTK4/libadwaita replacement track for the desktop Control Center.

Phase 4 ships the GTK4 Control Center shell plus the mutable typed Run workflow:

- Overview
- Active runs
- History
- Diagnostics
- Repository
- Targets
- Settings
- Run workflow

The Run page builds a typed `DevtoolCommandDraft`, previews argv and shell text,
and launches only after the command is visible. It covers validate/build/test,
artifact apply, test selectors, environment assignments, extra arguments, HUD,
clipboard, and `--yes` controls.

## Run

```bash
devtool control-center --backend gtk4
devtool control-center --backend gtk4 --page run
# or
devtool-gtk-control-center
```

Headless smoke test:

```bash
devtool control-center --once
```

Dependencies on Ubuntu/Debian:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
```

## Backend selection

```bash
devtool control-center --backend gtk4
devtool control-center --once
```

GTK4 is the only desktop backend. `--backend auto` and `--backend gtk4` both route to the GTK4 Control Center; Qt fallback support was removed in Phase 6.

## Phase 5 packaging flip

`devtool-control-center` and `devtool-control-center-hud` are now GTK4 entry
points. The package builder stages the Python desktop modules and GTK launchers
instead of compiling a native toolkit app:

```bash
./package-deb.sh --verify
```

The generated Debian package depends on GTK4/libadwaita packages and verifies
that no removed desktop runtime dependency is present in the GTK package metadata.
