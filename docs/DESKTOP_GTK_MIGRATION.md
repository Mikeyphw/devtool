# Desktop GTK4 migration

The desktop migration is now sealed: GTK4/libadwaita is the only maintained desktop app and desktop HUD path. The removed Qt/QML and GTK3 implementations are no longer shipped, tested, or used as fallbacks.

## Current entry points

```bash
devtool control-center
devtool control-center --backend gtk4
devtool control-center --once
devtool control-center --backend gtk4 --page run
devtool hud --mode desktop-window --follow-active
devtool hud --mode gtk4-window --follow-active
devtool-gtk-control-center
devtool-gtk-hud
```

`desktop-window` and `gtk4-window` both resolve to the GTK4 compact HUD. `control-center` launches the GTK4 Control Center. `gtk-window` is accepted as a deprecated alias that maps to the GTK4 HUD. Qt-specific modes such as `qt`, `qt5`, `qt6`, `qt-window`, and `qt-control-center` now fail with a migration hint.

## Phase 1: shared desktop model extraction

Reusable desktop logic lives under `devtool.desktop`:

- `command_model` builds reviewed argv-first `devtool` commands and renders shell text only for review/copy.
- `hud_store` loads live HUD snapshots, active-run pointers, event tails, and phase highlights.
- `ipc_client` reads the local Control Center IPC broker.
- `history_store`, `repository_store`, and `artifact_store` wrap existing history, Git, and artifact inspection primitives.

## Phase 2: GTK4 compact HUD

The GTK4 HUD consumes the same state files as the terminal, web, notification, and Android bridge HUDs. `--once` works headlessly and prints a GTK4-tagged JSON snapshot.

Suggested Ubuntu/Debian dependencies:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
```

## Phase 3: GTK4 Control Center read-only shell

The Control Center renders Overview, Active runs, History, Diagnostics, Repository, Targets, Run, and Settings from the shared model layer. `--once` prints the model as JSON without requiring a display.

## Phase 4: GTK4 Run workflow

The Run page is powered by `devtool.desktop.run_workflow` and `DevtoolCommandDraft`, so it builds an argv list first. It supports operation selection, target/variant/environment options, artifact apply controls, test selectors, environment assignments, extra args, clipboard/HUD options, command preview, dry-run preview, and guarded launch.

## Phase 5: GTK4 packaging flip

The desktop package stages the GTK4 launchers, `devtool.desktop` Python modules, freedesktop launcher, AppStream metadata, hicolor icons, and GTK4/libadwaita dependencies.

```bash
desktop/gtk-control-center/package-deb.sh --verify
```

## Phase 6: legacy desktop deletion

Removed from the repository:

- `desktop/devtool-control-center`
- Qt/CMake/QML Control Center sources
- Qt compact HUD launcher backend
- GTK3 Python desktop HUD backend
- legacy desktop source-contract tests
- release and package references to the old desktop tree

Release gates now guard against reintroducing the removed tree, Qt/QML runtime dependencies, or GTK3 HUD imports.
