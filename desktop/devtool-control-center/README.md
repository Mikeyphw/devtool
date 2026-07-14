# Devtool Control Center — Qt 6

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

Build a Debian package:

```bash
./package-deb.sh
```

Build, install, and delete the generated `.deb` only after installation succeeds:

```bash
./package-deb.sh --install --delete-after-install
```

The package is written to `dist/devtool-control-center_0.4.0_<arch>.deb`. Set `VERSION`, `ARCH`, `BUILD_DIR`, or `OUT_DIR` to override defaults.

## Native protocol

Version 0.4.0 connects to devtool 0.5.9 through a per-user Unix-domain socket. It receives snapshots and incremental run updates without polling, reconnects automatically, supports multiple clients, and can cancel the exact active run. If IPC is unavailable, the existing state-file watcher remains active as a recovery fallback.


## Qt 6.4 compatibility

Version 0.4.0 builds on Qt 6.4 by avoiding APIs introduced in Qt 6.5, using explicit QVariant fallbacks, and loading the embedded entry point from `qrc:/qt/qml/Devtool/ControlCenter/qml/Main.qml`.


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

## Standby and background efficiency

When the window is hidden, Control Center releases Qt Quick graphics resources,
unloads page content immediately, and destroys the complete QML engine after a
short grace period. The tray icon, activation socket, IPC state, and lightweight
run model remain available so reopening recreates the interface while preserving
window geometry and the selected page. File fallback polling slows down in
standby, IPC reconnects use exponential backoff, history loads on demand, and
repository Git inspection is asynchronous and deferred until needed.

Process output is delivered to QML through a bounded 2,000-line tail and batched
at 75 ms intervals. The full output remains in the normal devtool log.

Clipboard monitoring is event-driven through `QClipboard::dataChanged`. It
requires a recognized, case-sensitive devtool invocation and rejects copied
result reports such as `Devtool failed.` or `Devtool completed successfully.`.


## Workflow runner and actionable History

The Run page uses four stages—Project, Operation, Tests, and Review—with a
sticky action bar. It covers the complete common desktop lifecycle: apply
single overlay, apply bundle, validate, restore, build, test, lint, run,
package, clean, inspect, commit, status, and doctor.

The Tests stage calls `devtool test --list-tests`, parses structured or plain
output, and provides search plus exact test selection. Apply operations verify
the artifact manifest by default; an advanced, visibly warned control maps to
`--no-manifest-check` when recovery work requires bypassing that check.

History can be filtered by text, status, and repository. Run details expose
copy, rerun, open-log, and open-artifact actions. State-changing runs and
clear-all History require explicit confirmation.

## Accessibility and narrow layouts

Shared controls expose accessible roles, names, descriptions, tooltips, and
keyboard focus. The shell switches to compact navigation below 900 px and keeps
Run, History, Artifacts, Diagnostics, and Repositories usable down to the 640 px
window minimum. The compact HUD is constrained to the available screen and uses
more opaque surfaces for reliable contrast. Keyboard shortcuts and the manual
200% scaling checklist are documented in `../../docs/UI_ACCESSIBILITY.md`.

## UI regression contract

Slice 4 adds golden terminal/SVG visual-state snapshots, explicit multi-run and
rollback-state fixtures, and terminology checks shared with Python and Android.
History maintains a case-folded in-memory search index so filtering remains
linear without rebuilding a combined string for every query. Performance guards
cover 20,000 History records, 25,000 diagnostics, and 12,000 test IDs. See
`../../docs/UI_REGRESSION.md`.


## 0.4.0 release qualification

Both executables support `--version`. Configure with `-DDEVTOOL_ENABLE_TESTS=ON`
and run `ctest --test-dir <build> --output-on-failure` to execute isolated
offscreen QML launch tests. `package-deb.sh --verify` validates metadata, both
installed binaries, and its SHA-256 sidecar. `SOURCE_DATE_EPOCH` controls the
normalized package timestamp for reproducible release builds.
