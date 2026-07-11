# Devtool Control Center — Qt 6 Phase 3

Native Linux Qt 6/QML frontend modeled after the Android companion app and adapted for desktop workflows.

## Included

- Multi-run overview with live target-aware segmented pipelines
- Visual workflow runner using `QProcess`
- Artifact inspector backed by `devtool inspect-artifact`
- Overlay and bundle metadata, validation, inventory, manifest, and repository-relative diff views
- Bundle order/dependency visualization (`after`, `requires`, optional overlays)
- Persistent repository manager with Git branch, remote, cleanliness, last-use, and active-run state
- Searchable diagnostics browser with per-run selection, severity filters, file/phase context, and copy/open actions
- Persisted local run history
- Desktop completion/failure notifications and system tray integration
- Detachable always-on-top compact HUD
- Live `state.json`, `runs/*.json`, and `hud-active.json` monitoring
- Desktop file, icon, Debian metadata, and CMake install rules

## Build

Ubuntu 24.04 or newer:

```bash
sudo apt install cmake ninja-build g++ qt6-base-dev qt6-declarative-dev \
  qml6-module-qtqml qml6-module-qtqml-models \
  qml6-module-qtqml-workerscript qml6-module-qtquick \
  qml6-module-qtquick-window qml6-module-qtquick-templates \
  qml6-module-qtquick-controls qml6-module-qtquick-layouts \
  qml6-module-qtquick-dialogs
./build.sh
```

Install:

```bash
cmake --install build --prefix "$HOME/.local"
```

Runs started by the app use `--auto-hud --hud-mode control-center`. Artifact inspection uses the machine-readable `inspect-artifact` command added in devtool 0.4.1.

## Debian package

After a successful Qt build:

```bash
./package-deb.sh
```

The package is written to `dist/devtool-control-center_0.3.8_<arch>.deb`. Set `VERSION`, `ARCH`, `BUILD_DIR`, or `OUT_DIR` to override defaults.

## Phase 3 native protocol

Version 0.3.8 connects to devtool 0.5.9 through a per-user Unix-domain socket. It receives snapshots and incremental run updates without polling, reconnects automatically, supports multiple clients, and can cancel the exact active run. If IPC is unavailable, the existing state-file watcher remains active as a recovery fallback.


## Qt 6.4 compatibility

Version 0.3.8 builds on Qt 6.4 by avoiding APIs introduced in Qt 6.5, using explicit QVariant fallbacks, and loading the embedded entry point from `qrc:/qt/qml/Devtool/ControlCenter/qml/Main.qml`.


## Build-directory and clock-skew safety

The default native build now lives under `${XDG_CACHE_HOME:-$HOME/.cache}/devtool-control-center` rather than inside the source tree. `package-deb.sh` uses a clean dedicated packaging build, and `build.sh` normalizes source timestamps that are ahead of the local clock. These safeguards prevent Ninja from repeatedly reporting `Re-running CMake...` after an archive extraction, source replacement, or clock mismatch. Override the location with `BUILD_DIR`; set `KEEP_PACKAGE_BUILD=1` only when intentionally reusing the packaging cache.

## Packaging assets

The source includes the freedesktop launcher and scalable icon required by the CMake install rules. `package-deb.sh` validates these inputs before starting the native build, so an incomplete source archive fails immediately rather than after compilation.


## QML runtime dependencies

The Debian package explicitly depends on the QtQml base, Models, and WorkerScript modules plus the Qt Quick Window and Templates modules. Qt Quick imports these modules transitively at runtime, but Ubuntu packages them separately; declaring them prevents startup failures such as `module "QtQml.WorkerScript" is not installed`.

## System appearance

Control Center follows the active Qt 6 system palette, application font, accent color, display scaling, and Qt Quick Controls style. It no longer forces a private dark palette or the Fusion style. Use the standard `-style` argument or `QT_QUICK_CONTROLS_STYLE` environment variable to override the platform-selected controls style at startup. The custom cards and controls derive their colors from `SystemPalette`, so both light and dark system themes remain coherent.


## Optional clipboard monitoring service

Settings now includes an **Install & enable** button for a per-user systemd service. The service launches `devtool-control-center --clipboard-monitor` with the graphical session, watches only for local artifact archive paths and commands containing `devtool`, and ignores all other clipboard content. Detected content is never executed automatically. It is stored in the user-only Control Center settings, surfaced in Settings, and can be reviewed, copied, dismissed, or opened in the relevant page.

The service unit is written to `~/.config/systemd/user/devtool-control-center-clipboard.service` and can be enabled, disabled, reinstalled, or removed from the app. `notify-send` is used when available for passive desktop notifications and is only an optional package suggestion.

## Clipboard command review and execution

Clipboard monitoring still never auto-runs content. A detected devtool command appears on the Run page with a **Run command…** action. The app opens a modal review dialog showing the complete command; execution only begins after explicit confirmation. The command runs through the user's shell from the selected repository, or from the home directory when no repository is selected.

## Compact Qt HUD

`devtool --hud-mode desktop-window` now launches the Qt 6 compact HUD. The previous GTK renderer is retained only as the explicit legacy mode `--hud-mode gtk-window`; automatic desktop selection never falls back to GTK.
