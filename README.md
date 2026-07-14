# Devtool

Devtool applies project overlays, validates builds and tests, records transactions, produces handoff summaries, and can publish one live run to the desktop HUD or the Android Devtool Frontend.


## Devtool 1.1.0: native release qualification

Slice 5 adds executable release gates for every distributable. The canonical
version contract lives in `release/versions.json`; drift between Python, Qt,
Android, Debian, and manual-page metadata fails qualification.

The Qt 6 Control Center now has CTest version and offscreen QML launch tests,
and its Debian package is timestamp-normalized, checksummed, and payload-
verified. Android release builds require an explicit keystore, run local JVM
and connected smoke tests, and are verified with `apksigner`. Deterministic
source archives can be rebuilt and compared byte for byte.

Run the portable gate with `python3 tools/release-qualify.py --core-only`, or
use `tools/make-release-candidate.sh` on a machine with the native toolchains.
The GitHub Actions matrix independently validates Python 3.11–3.13, Qt 6,
Android lint/unit/release, emulator instrumentation, and final packaging.

See [`docs/RELEASE_QUALIFICATION.md`](docs/RELEASE_QUALIFICATION.md),
[`docs/RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md), and
[`docs/MIGRATION_1.1.md`](docs/MIGRATION_1.1.md).

## Devtool 0.15.0: verified Android artifacts, device tests, selective tests, and Python pipelines

Android and Gradle builds now verify newly produced APK, AAB, AAR, and JVM
artifacts after the build. Verification includes archive integrity, required
members, SHA-256, variant/module metadata, APK badging and signatures when the
Android SDK tools are available, and rejection of empty, stale, corrupt, or
explicitly unsigned release outputs.

Rooted Termux installations can run Android instrumentation tests on the
physical device:

```bash
devtool -r "$HOME/Code/android-app" test --instrumented
devtool -r "$HOME/Code/android-app" test --instrumented --class demo.UiTest
devtool -r "$HOME/Code/android-app" test --instrumented --method demo.UiTest.opensEditor
```

The runner builds the app and androidTest APKs, installs both through `pm` (or
ADB for an external serial), resolves AndroidJUnitRunner, emits structured test
results, and removes the temporary test package unless configured otherwise.

Test selection is now shared by .NET, Gradle, Android instrumentation, and
Python:

```bash
devtool test --list-tests
devtool test --profile quick
devtool test --select parser --exclude-tag slow
devtool test --failed
devtool test --changed --changed-from origin/main
```

Python projects now receive manager-aware restore, test, lint, PEP 517 build,
package verification, clean-install smoke testing, and optional import smoke
testing. Existing uv, Poetry, PDM, Pipenv, and Hatch metadata is respected.

See [`docs/ANDROID_PROJECTS.md`](docs/ANDROID_PROJECTS.md),
[`docs/TEST_SELECTION.md`](docs/TEST_SELECTION.md), and
[`docs/PYTHON_PROJECTS.md`](docs/PYTHON_PROJECTS.md).

## Devtool 0.6.0: runner and execution foundations

Devtool now separates project command planning from execution-environment
selection. The current .NET, Python, and custom-command runners can execute in
the current Linux environment, native Termux, or a configured chroot. Use:

```bash
devtool --execution-environment native-termux -r /path/to/repo validate
devtool --chroot androidos -r /path/to/repo validate
```

Configuration can set the default independently of project type:

```toml
[execution]
environment = "auto"
chroot_name = "androidos"
```

`.NET` validation reuses prior phases rather than performing redundant work:
`restore`, `build --no-restore`, then `test --no-build`. Recognizable explicit
`dotnet build` and `dotnet test` commands receive the same validation-only
optimization, while arbitrary wrapper scripts are left unchanged. See
[`docs/PROJECT_RUNNERS.md`](docs/PROJECT_RUNNERS.md) and
[`docs/EXECUTION_ENVIRONMENTS.md`](docs/EXECUTION_ENVIRONMENTS.md).

## Termux chroot repository paths

When a repository exists only inside a chroot, devtool now detects that the path is not visible to the Termux host and delegates the full process automatically. Repositories mirrored from Termux home are also probed so mounts under `/home/mike`, `/home/termux`, or another chroot home resolve without manual path-map flags. The Android frontend always launches chroot workflows from a Termux-visible work directory.

## Devtool 0.3.3 behavior

Automatic artifact commits include the complete devtool-managed history registry (`.devtool/artifacts.json` and `.devtool/artifacts.d/`) so prior history records do not leave the repository dirty. Unrelated working-tree changes remain excluded.

### One active run

Devtool no longer supports concurrent operations inside the same repository. A repository has one simple run lock:

- A second operation fails immediately with a clear “another devtool run is already active” message.
- There are no path scopes, lease modes, wait policies, or concurrency flags.
- Apply, validate, rollback, commit, and tag operations are serialized.
- The Android frontend displays one active run. Completed runs remain available in History.

Separate repositories retain separate locks, so an unrelated repository is not blocked.

### Android integration belongs to the app

The old standalone Android notification and CLI Tasker paths have been removed:

- No `termux-notification` fallback.
- No `android-notify` HUD mode.
- No `--notify`, `--notify-progress`, or related notification flags.
- No `--tasker`, `--tasker-progress`, or `--tasker-broadcast` flags.
- No CLI broadcast integration for Tasker.

Use the Android Devtool Frontend for:

- The single live Android notification.
- The optional floating HUD.
- Visual command building and history.
- Native Tasker plug-in actions.
- Rootless Termux `RUN_COMMAND` execution.
- The optional KernelSU/Magisk root bridge.

The foreground-service notification required by Android while the floating overlay is running remains part of the app.



## Artifact inspection API

Devtool 0.4.1 exposes a machine-readable inspector used by the Qt Control Center:

```bash
devtool -r /path/to/repo inspect-artifact overlay-or-bundle.zip --diff
```

The JSON result includes artifact identity, repository/workspaces, validation, inventory, bundle dependencies, preview counts, manifest text, and an optional repository-relative diff.

## Self-contained artifacts

Artifact schema v2 can carry the repository identity, monorepo workspace, target, solution, file inventory, validation policy, disposable validation script, and final commit message. A normal apply can therefore be reduced to:

```bash
devtool \
  --copy --auto-hud --hud-mode desktop-window --yes \
  -r "$HOME/Code/sims3tools" \
  apply-overlay "$HOME/Downloads/example_overlay.zip"
```

CLI options still override artifact values. Use `--no-validate`, `--no-commit`, or `--no-artifact-script` for explicit opt-outs. See [`docs/ARTIFACTS.md`](docs/ARTIFACTS.md) for the complete overlay and bundle formats.

## Typical Android command

After pairing the secure bridge in the app, commands launched directly from Termux use:

```bash
cd "$HOME/Code/sims3tools"

devtool \
  --copy \
  --copy-via termux \
  --auto-hud \
  --hud-mode android-bridge \
  --yes \
  -r "$HOME/Code/sims3tools" \
  --target s3atelier_avalonia_S3Atelier_Modern \
  apply-overlay "$HOME/Downloads/example_overlay.zip" \
  --validate \
  --commit "Apply example overlay"
```

`--auto-hud --hud-mode android-bridge` starts the app forwarder. The app decides whether to show only its live notification or also the floating overlay. Use `--hud-overlay` to request the overlay for one command or `--hud-no-overlay` to suppress it.

## Desktop HUD

Use:

```bash
devtool --auto-hud --hud-mode desktop-window ...
```

The desktop HUD binds to the current run before rendering, filters events by run ID, suppresses duplicate generic errors, and reports a successful rollback as recovery rather than as the cause of failure.

The GTK desktop window starts in a compact, pipeline-focused mode. It shows separate live operation segments for extract, restore, build, test, artifact validation, commit, and rollback as applicable. Restore/build/test segments advance from parsed command output while the command is still running. Multi-workspace artifacts keep independent target-scoped segments such as `Build · s3atelier` and `Build · s3pi-base`, wrapped into rows of four. Live **ERRORS** and **WARNINGS** totals come directly from the active parser and remain visible beside the current operation, along with Copy summary/Open log/Close actions. Select **Expand** to reveal recent events and diagnostic details; the choice is preserved for the lifetime of that HUD window. Set `DEVTOOL_DESKTOP_HUD_START_EXPANDED=1` to launch expanded.

Set `DEVTOOL_NO_HUD=1` to hard-disable all HUD publication and rendering. This environment override wins over `--auto-hud`, `--hud-mode`, `--hud-overlay`, and direct `devtool hud` invocations, making it suitable for CI, headless shells, and commands that must never open or update a HUD.

## Validation sequence

For .NET targets, validation uses:

```bash
dotnet restore ...
dotnet build --no-restore ...
dotnet test --no-build ...
```

The same normalization is applied to recognizable explicit .NET build and test commands.

## Android frontend

Source is under:

```text
android/devtool-live-hud/
```

Build from the repository root:

```bash
./build-android-hud.sh
```

The portrait-first app includes the same target-scoped operation pipeline in its active-run card, floating overlay, and Android 16 progress notification. Long multi-target pipelines wrap into rows of four, step progress advances while command output is parsed, and warning/error totals update from the current event stream rather than waiting for final completion. The active state also carries an authoritative per-operation progress snapshot, so desktop and Android bars continue updating even when a poll skips intermediate event-log records.

The portrait-first app includes:

- Overview, Run, History, and Settings pages.
- State-aware permission and integration tiles.
- One-button Termux token installation, update, and verification.
- GTK colors by default and optional Monet colors.
- Clipboard command import into the visual builder.
- Native Tasker plug-in support configured inside the app.
- One active live run, with stale abandoned entries ignored automatically.

## Optional root bridge

Rootless Termux execution remains the default. An optional non-Zygisk KernelSU/Magisk/APatch module is under:

```text
root-module/devtool-bridge/
```

Build it with:

```bash
./build-root-bridge-module.sh
```

The module provides authenticated generated-devtool execution, chroot access, logs, and cancellation. It does not make root mandatory and does not expose a general root shell through the frontend.

## Successful apply summaries

Clipboard, HUD, history, completion payloads, and app results name the applied overlay or ordered overlay set:

```text
Applied overlay: example_overlay.zip
```

or:

```text
Applied overlays: 2
Overlay 1/2: first_overlay.zip
Overlay 2/2: second_overlay.zip
```

## Install or update Devtool

```bash
cd "$HOME/Code/devtool"
./install.sh
```

The installer detects desktop Linux versus Android/Termux and offers to build
and install the matching native app. On desktop it packages and installs the
Control Center, then removes the temporary `.deb` only after a successful
installation. On Android it rebuilds the stable tracked APK only when sources
are newer and installs it with `sudo pm install -r`.

The desktop flow also offers to install the clipboard monitor service or update
the existing user service. Non-interactive examples:

```bash
./install.sh --yes --with-app --with-clipboard-service
./install.sh --app android --with-app
./install.sh --without-app --without-clipboard-service
```

Generated Android builds are copied to
`android/devtool-live-hud/devtool-live-hud-debug.apk`. That stable APK path is
intentionally allowed by `.gitignore`, so a real generated APK can be versioned.

Named targets are never selected implicitly. Legacy `default_target` and
`[project].target` values are ignored; use `--target`, `--solution`, or embed the
target in artifact metadata.

Verify:

```bash
devtool --version
devtool --help
```

## Removed option migration

Replace older commands as follows:

```text
--hud-mode android-notify     → --hud-mode android-bridge
--notify / --notify-progress  → configure notifications in the Android app
--tasker* flags               → configure the native Tasker plug-in action in the app
--concurrency / --lock-timeout / --no-wait → removed; one run is enforced
```


## Qt 6 Control Center

The native Linux frontend lives in `desktop/devtool-control-center`. It provides Overview, Run, History, Settings, tray integration, and a detachable compact HUD.

```bash
cd desktop/devtool-control-center
./build.sh
./build/devtool-control-center
```

Runs started by the app use `--hud-mode control-center`, which keeps live state publishing enabled without opening the legacy GTK HUD. Launch an installed build with `devtool control-center`.

## Control Center native IPC

Devtool 0.5.2 uses a per-user Unix socket for live Control Center updates. The default is
`$XDG_RUNTIME_DIR/devtool/control-center.sock`, falling back to
`$XDG_STATE_HOME/devtool/control-center.sock` or `~/.local/state/devtool/control-center.sock`.

```bash
devtool ipc-server                 # start/reuse the detached broker
devtool ipc-server --foreground    # diagnostic foreground mode
DEVTOOL_IPC_SOCKET=/path/to.sock devtool ...
```

The broker supports multiple repositories, producers, and Control Center clients. State files remain available as a reconnect and compatibility fallback. Cancellation from the Control Center is delivered to the matching run and causes the active command process group to terminate transactionally.


## Devtool Control Center native operation

The Qt application uses the native IPC broker for normal live operation, reconnect snapshots, concurrent repositories, and per-run cancellation. File-state monitoring remains available when the broker is unavailable.


### Qt Control Center packaging safety

The Qt Control Center 0.3.2 builds outside the source tree by default and uses a clean dedicated build for Debian packages. The build launcher also normalizes source timestamps that are ahead of the local clock, preventing Ninja from endlessly printing `Re-running CMake...` after an archive extraction or source update.

## Qt Control Center clipboard monitor

The Linux Control Center can install an optional per-user clipboard monitoring service from **Settings → Clipboard monitoring**. It recognizes local devtool artifact archives and copied `devtool` commands, ignores unrelated clipboard data, and never runs detected content automatically. The service can be enabled, disabled, reinstalled, or removed from the UI.


## Project runner architecture

Project command planning is now runner-based. See [`docs/PROJECT_RUNNERS.md`](docs/PROJECT_RUNNERS.md) for the .NET, Python, and custom-command runner contract and capability model.


## Native frontend workflow Slice 2

The Qt Control Center and Android frontend now use the same four-stage Run flow:
**Project → Operation → Tests → Review**. Navigation and execution actions stay
fixed at the bottom of the screen, so large test catalogs and advanced options
do not push the Run button off-screen.

The desktop frontend covers apply overlay, apply bundle, validate, restore,
build, test, lint, run, package, clean, inspect, commit, status, and doctor. Its
Tests stage discovers the real catalog with `devtool test --list-tests`, supports
search and exact selection, and keeps manual profile/filter controls available.
Apply workflows expose an explicitly warned advanced `--no-manifest-check`
override.

History is searchable and filterable. Stored commands can be copied or rerun;
desktop entries can open their log and artifact paths, while Android entries can
be inspected or removed individually. Clearing all saved history and launching
state-changing operations require confirmation.

## Native frontend accessibility and responsiveness — Slice 3

The Qt Control Center now exposes semantic accessible roles/names, visible focus
states, tooltips, full keyboard navigation, narrow page layouts, and a
screen-bound compact HUD with safer opacity. The Android frontend now enforces
48 dp targets, connects labels to fields, supplies TalkBack descriptions, and
stacks dense actions at large system font sizes. See
`docs/UI_ACCESSIBILITY.md` for shortcuts and the 200% scaling/TalkBack manual
verification matrix.

## UI regression protection — Slice 4

The terminal, Qt Control Center/HUD, and Android frontend now share a regression
contract for runner/status terminology, concurrent-run states, rollback states,
and large-data performance budgets. Golden Rich terminal snapshots and portable
SVG visual-state snapshots are stored under `tests/snapshots/ui/`; update them
with `python3 tools/update-ui-snapshots.py` only after reviewing the diff.

History search now keeps a case-folded in-memory index, Android test-catalog
items cache their search key and avoid one giant concatenated output buffer, and
the diagnostics parser routes common diagnostic families before the generic
regular-expression chain. See `docs/UI_REGRESSION.md`.
