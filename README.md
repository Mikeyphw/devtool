# Devtool

Devtool applies project overlays, validates builds and tests, records transactions, produces handoff summaries, and can publish one live run to the desktop HUD or the Android Devtool Frontend.


## Interactive validation cockpit

Open the validation-only terminal UI with either command:

```bash
devtool -r /path/to/repo menu validation
devtool -r /path/to/repo vm
```

The cockpit uses `fzf` for state-aware selection and `bat` for command, plan,
event, and dependency previews. A numbered terminal fallback remains available
with `--no-fzf`. It reads the same revisioned status and plan APIs used by the
Android Validation Workbench, then compiles choices into canonical Devtool
arguments. It never creates a second validation executor.

The landing screen adapts to the active session and offers next-phase,
through-checkpoint, selected-phase, complete, retry, safe retry, pause, stop,
recover, reset-from-phase, accept-plan-change, finalize, report, refresh, and
abort actions. Every run or mutation compiled from an active session carries its
current revision guard. The
full wizard covers one-shot, collect-all, durable sequential, watch, matrix,
Wine, overlay/bundle integrity, and apply-time validation. Pipeline skips, test
selectors, Gradle tasks, resource profiles, one-run resource overrides, device
guards, failure policy, HUD/output controls, dry-run, copy, editable command
review, stable pretty/compact JSON, and reusable global or repository recipes
that preserve explicit invocation context are all supported.

```bash
# Jump directly into the guided wizard.
devtool -r /path/to/repo validation-menu --wizard

# Print a recipe command without opening the UI.
devtool -r /path/to/repo vm --recipe android-battery-safe --print-command

# Discover the complete Gradle task catalog while opening the cockpit.
devtool -r /path/to/repo vm --runtime-tasks

# Expand the built-in target-matrix recipe noninteractively.
devtool -r /path/to/repo vm --recipe target-matrix \
  --matrix-values app,android_hud --print-command
```

Custom resource controls are ephemeral and override the selected resource
profile for only that invocation:

```bash
devtool -r /path/to/repo validate continue \
  --max-workers 1 --cpu-limit 2 \
  --gradle-heap-mb 1536 --memory-guard-mb 640 \
  --low-priority --no-parallel --build-cache --no-daemon
```

Saved recipes live in
`~/.config/devtool/validation-recipes.toml` and
`REPOSITORY/.devtool/validation-recipes.toml`. See
[`docs/VALIDATION_MENU.md`](docs/VALIDATION_MENU.md) for the complete interaction
model and key bindings.


## Deferred validation reliability

Deferred and sequential validation use revisioned schema-v4 sessions with a
durable dependency graph and attempt history. A test, compile, lint, or
validator failure blocks the session and preserves the patched working tree plus
rollback point by default. Interruptions and verified resource/environment
failures remain retryable rather than being mislabeled as bad code.

Phase selection follows declared prerequisites, input fingerprints invalidate
only affected graph branches, and a phase success is discarded when relevant
inputs change while it is running. Unsafe plan drift requires an explicit
audited acceptance.

```bash
devtool -r /path/to/repo validate continue
devtool -r /path/to/repo validate continue --retry-failed
devtool -r /path/to/repo validate recover
devtool -r /path/to/repo validate reset --from-phase compile
devtool -r /path/to/repo validate accept-plan-change --reason "Reviewed plan update"
devtool -r /path/to/repo validate abort --keep-changes
devtool -r /path/to/repo validate abort
```

Use `--failure-action rollback` only when immediate restoration is desired.
Rollback is journaled per path, staged and hash-verified, and can resume after an
interruption. `validate status`, `validate checkpoint`, and validation
`--dry-run` previews do not mutate session state. Resource and infrastructure
interruptions have bounded automatic retries; use `--no-auto-retry` for fully
manual retry control.

### Stable validation control API

Phase 28 adds a versioned machine surface for the Android app and other
frontends. Read operations are lock-free and non-mutating; control requests use
optimistic concurrency so an old notification cannot alter a newer session.

```bash
devtool -r /path/to/repo validate status --json
devtool -r /path/to/repo validate plan --json
devtool -r /path/to/repo validate events --after-event EVENT_ID --json

devtool -r /path/to/repo validate run --scope next --if-revision 12 --json
devtool -r /path/to/repo validate run --scope through --through package --if-revision 13 --json
devtool -r /path/to/repo validate run --scope selected --phase compile --phase lint --if-revision 14 --json
devtool -r /path/to/repo validate run --scope remaining --if-revision 15 --json

devtool -r /path/to/repo validate pause --after-phase --if-revision 16 --json
devtool -r /path/to/repo validate stop --if-revision 17 --json
```

Every response uses `api_version: 1`, returns the current session revision, and
includes stable `state`, `effective_state`, `reason`, `message`, and `data`
fields. Accepted mutations advance the revision. Heartbeats prove liveness
without advancing it. Durable JSONL events support cursor polling without
replaying the entire log. Exit codes are `0` accepted, `2` invalid request, `3`
stale revision, `4` blocked session, `5` recovery required, and `6` control
unavailable. Use `--machine compact` for compact JSON or `--machine-file PATH`
to mirror the exact envelope to a file.

## Android Validation Workbench

Phases 29–31 add a dedicated validation surface inside the Android Run destination. The app consumes the Phase 28 API-v1 envelopes, persists sessions/phases/attempts in SQLite, and sends revision-guarded controls through the original Termux or root execution environment.

The Workbench can run the next phase, all remaining phases, selected phases plus their dependency closure, or every phase through a named checkpoint. It exposes pause-after-phase, immediate stop, cancel-pause, retry and safe retry, graph-aware reset, finalization, keep-changes abandonment, and confirmed journaled rollback. Validation failure continues to preserve the patched tree and rollback point.

The normal custom notification now has a validation presentation with overall phase progress, previous/current/next phase rows, and state-specific actions. Old notification actions cannot mutate a newer session because every action includes the session revision, repository identity, and active attempt identity. Equal-revision telemetry snapshots are ordered by the authoritative session timestamp. Rollback remains available only inside the confirmed Workbench danger section.

Phase 31.1 adds structured executor identity, immutable Android attempt evidence, live current-phase percentages, explicit recovery and safe retry, a cached evidence viewer, and rollback confirmation with journal/backup/dirty-tree context. See `PHASE_31_1_VALIDATION_COMPLETION.md`.

See `PHASE_29_31_ANDROID_VALIDATION_WORKBENCH.md`.

## Durable validation reports

Completed, blocked, recovered, and aborted validation sessions write a durable validation report under `.devtool/reports/validation/`. Reports include the complete phase plan, immutable attempt evidence, original and final Git heads, diagnostics, resource-profile changes, rollback outcome, commit outcome, and final verdict.

```bash
devtool validate report --json
python3 tools/validate-validation-release.py
```

Android Logs displays validation sessions as distinct validation history cards with an **Open report** action. Finalization refuses stale inputs or incomplete required checkpoints. Release candidates must run the Phase 32–33 validation seal and the Android toolchain gate described in [`docs/RELEASE_QUALIFICATION.md`](docs/RELEASE_QUALIFICATION.md).

## Devtool 1.1.0: native release qualification

Slice 5 adds executable release gates for every distributable. The canonical
version contract lives in `release/versions.json`; drift between Python, GTK desktop,
Android, Debian, and manual-page metadata fails qualification.

The GTK4 Control Center now has headless smoke and package verification tests,
and its Debian package is timestamp-normalized, checksummed, and payload-
verified. Android release builds require an explicit keystore, run local JVM
and connected smoke tests, and are verified with `apksigner`. Deterministic
source archives can be rebuilt and compared byte for byte.

Run the portable gate with `python3 tools/release-qualify.py --core-only`, or
use `tools/make-release-candidate.sh` on a machine with the native toolchains.
The GitHub Actions matrix independently validates Python 3.11–3.13, GTK4 desktop packaging,
Android lint/unit/release, emulator instrumentation, and final packaging.

See [`docs/RELEASE_QUALIFICATION.md`](docs/RELEASE_QUALIFICATION.md),
[`docs/RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md), and
[`docs/MIGRATION_1.1.md`](docs/MIGRATION_1.1.md).

## Devtool 1.1.0-rc.6: optimized builds for every first-class project kind

Devtool now auto-detects and optimizes .NET, Android/Gradle, JVM Gradle,
Python, Node.js, Rust/Cargo, Go, Maven, CMake, Meson, and Make projects. A
shared CPU/memory-aware policy gives desktop builds higher safe parallelism
while limiting native Termux and Android-hosted chroots to avoid swapping,
thermal slowdown, and Android process reclamation.

Cargo, Go, Maven, .NET, and Gradle validation use coalesced lifecycle commands
where one invocation provides equivalent coverage. CMake and Meson retain warm
out-of-tree build state; Node and Python reuse incremental dependency and
environment caches. Chroot execution now exports those policies inside
`chrootctl`.

Go `build` is executable-aware: Devtool discovers every `package main` inside
the configured build scope, writes real binaries to `.devtool/build/go/`, and
registers verified Go artifacts with embedded module, Go version, GOOS/GOARCH,
and VCS build metadata. Library-only repositories still use `go build` as a
compile check. `go.work` roots expand the default `./...` scope across all
workspace modules, and executable-name collisions fail rather than silently
overwriting a binary.

```toml
[build]
max_workers = "auto"

[execution]
environment = "auto" # desktop-linux | native-termux | chroot
```

Override one run with `DEVTOOL_MAX_WORKERS=3`. See
[`docs/BUILD_OPTIMIZATION.md`](docs/BUILD_OPTIMIZATION.md) and
[`docs/PROJECT_RUNNERS.md`](docs/PROJECT_RUNNERS.md).

## Polyglot targets and reusable workflows

Devtool now distinguishes the **platform a target represents** from the **runner that executes its primary work**. A TV target can use the Android runner, a desktop target can use Node or .NET, and a server target can remain Python without conflating product identity with toolchain identity. Existing `kind` and target `type` declarations remain compatible, while new configuration should prefer `runner` and `platform`.

Reusable jobs can belong to any runner and be referenced by target workflows:

```toml
[project]
runner = "python"
platform = "server"

[workspace]
default_target = "api"

[workspace.groups]
backend = ["api", "worker"]
release = ["@backend", "web", "android"]

[jobs.generate-client]
runner = "node"
root = "tools/openapi"
command = ["npm", "run", "generate:python"]
inputs = ["schema/**"]
outputs = ["services/api/generated/**"]

[targets.api]
runner = "python"
platform = "server"
root = "services/api"

[targets.protocol]
runner = "python"
platform = "shared"
root = "packages/protocol"

[targets.api.workflows]
validate = [
  "target:protocol#build",
  "job:generate-client",
  "runner:python#validate",
]
```

MP05 expands `target:<name>#<action>` references into one workspace DAG. The referenced target keeps its own root, runner, options, environment, and variant configuration; shared target/action prerequisites are deduplicated. MP06 adds named workspace groups, nested `@group` references, and `*` for all configured targets. Use repeated `--group`, `--all-targets`, or `devtool groups` to select/inspect persistent workspace scopes. For example:

```bash
devtool --group release validate
devtool --all-targets build
devtool groups --json
```

Use `devtool targets`, `devtool groups`, `devtool jobs`, `devtool workflows`, and `devtool inspect` to inspect the normalized model. `devtool workflows --json` exposes both the declared local DAG and the expanded cross-target workspace DAG. Plain lists remain sequential, inline `{ id, ref, depends_on }` steps can branch/join, and each executable node is dispatched to its own first-class runner and execution environment. Failures block dependent nodes while preserving independent-branch semantics, and the same dependencies are bridged into resumable validation sessions. See [`docs/POLYGLOT_TARGETS_AND_WORKFLOWS.md`](docs/POLYGLOT_TARGETS_AND_WORKFLOWS.md) and [`docs/WORKSPACE_GROUPS.md`](docs/WORKSPACE_GROUPS.md).

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

Test selection is shared by .NET, Gradle, Android instrumentation, and
Python. Pytest additionally supports repeatable exact node selection, including
files, classes, functions, methods, and parametrized node IDs:

```bash
devtool test --list-tests
devtool test --profile quick
devtool test --select parser --exclude-tag slow
devtool test --failed
devtool test --changed --changed-from origin/main

devtool validate \
  --pytest-test tests/test_auth.py::test_login \
  --pytest-test tests/test_api.py::TestUsers::test_create
```

Exact `--pytest-test` selection suppresses configured/default/affected pytest
targets unless `--pytest-include-defaults` is explicitly supplied. Pytest
markers, `-k` expressions, and structured extra argv are available through
`--pytest-marker`, `--pytest-k`, and repeatable `--pytest-arg`. The validation
cockpit can refresh native `pytest --collect-only -q` results and multi-select
executable node IDs.

Python projects now receive reproducible manager-aware environments, strict
configuration parsing, affected-file tests and lint, validator dependency
resolution, coverage and source audits, native-extension diagnostics, verified
wheel/sdist parity, clean import and CLI checks, optional matrices, notebooks,
databases, service lifecycle checks, security scanning, SBOMs, and reproducible
build gates. Devtool-owned Python venvs, caches, and environment snapshots live
under devtool XDG storage by default; explicit `[python].environment` paths are
still honored. Existing uv, Poetry, PDM, Pipenv, Hatch, tox, and nox metadata is
respected.

See [`docs/ANDROID_PROJECTS.md`](docs/ANDROID_PROJECTS.md),
[`docs/TEST_SELECTION.md`](docs/TEST_SELECTION.md), and
[`docs/PYTHON_PROJECTS.md`](docs/PYTHON_PROJECTS.md).

## Devtool 0.6.0: runner and execution foundations

Devtool now separates project command planning from execution-environment
selection. Every first-class project runner can execute in the current Linux
environment, native Termux, or a configured chroot. Use:

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

Projects can optionally run planned build/test commands through
[`mise`](https://mise.jdx.dev/) without making it a devtool dependency:

```toml
[toolchain]
manager = "auto" # auto | mise | none
```

`auto` uses `mise exec -- ...` only when `mise` is available and the project
contains `mise.toml`, `.mise.toml`, or `.tool-versions`. `DEVTOOL_TOOLCHAIN`
can override the config for one run.

`.NET` validation avoids redundant process launches. When build and test use
the same solution/project, devtool runs one `dotnet test` command because it
already restores and builds that target. Different targets use an implicit-restore
`dotnet build` followed by the safest applicable test command. Recognizable
explicit commands preserve their ordering and receive only safe
`--no-restore`/`--no-build` normalization. Shared .NET build servers remain warm
by default; set `[dotnet].stop_after_run = true` only when memory reclamation is
more important than the next build. See
[`docs/PROJECT_RUNNERS.md`](docs/PROJECT_RUNNERS.md) and
[`docs/EXECUTION_ENVIRONMENTS.md`](docs/EXECUTION_ENVIRONMENTS.md).

## Termux runtime hardening and PRoot-Distro

Native Termux runs now get phone-aware resource defaults, backend-isolated caches, optional wake-lock protection, shared-storage safety checks, proactive ELF architecture/libc preflight, conventional Linux shebang adaptation, and Gradle quiet-period diagnostics that warn without killing a still-running dependency resolution. Verified Android/Gradle artifacts can be copied to shared storage only after local verification, for example with `--artifact-export /sdcard/Download`. Explicit worker/JVM/resource settings always override the Termux profile, and phone-sized resource defaults are not injected into SSH jobs that execute on an independent remote host.

PRoot-Distro is a first-class command execution backend. Devtool stays on the host and wraps only planned external commands, so the guest does not need devtool installed:

```bash
devtool --proot-distro ubuntu validate
devtool --proot-distro ubuntu --proot-user dev --proot-cwd /workspace/project validate
```

By default the repository is explicitly bound to `/workspace/project`; customize it with `--proot-repo-mount` or repeatable `--proot-bind HOST:GUEST`. Helper files outside mapped roots are staged into a host cache bound at `/run/devtool`, and the staging directory is removed after execution. Python support helpers are staged rather than requiring devtool to be installed in the guest, while persistent guest caches are isolated under `~/.cache/devtool/proot/<distro>` and bound at `/var/cache/devtool`. Host-specific `HOME`, `PATH`, `JAVA_HOME`, Gradle home, Android SDK/NDK variables, and other execution-backend state are not blindly propagated; devtool-managed backend variables cannot be overridden through PRoot environment settings. Commands selected from the Termux package prefix are resolved again by name inside the guest instead of copying a Bionic binary or Termux launcher into the Linux container. Use normal foreground execution so devtool retains phase ordering, progress and exit codes; for detachable local orchestration, wrap the whole devtool process in tmux instead of using PRoot-Distro detach mode.

### Termux provider and PRoot diagnostics

Use the final integration doctor to see native Termux runtime identity, provider readiness, selection/rejection reasons, package suggestions, and an optional PRoot guest tool probe:

```bash
devtool --host termux doctor --explain
devtool --host termux doctor --distro ubuntu --explain
devtool --host termux packages
devtool --host termux explain
devtool --host doctor --explain
```

`termux packages` is deliberately read-only: it can print a suggested `pkg install ...` command, but devtool never installs or upgrades Termux packages automatically. OpenJDK advice first inspects installed/available Termux package names instead of permanently assuming one JDK generation. The PRoot guest probe uses `proot-distro login` to inspect the guest's own Python/Java/Gradle/CMake/Ninja/clang/Git commands; it does not require devtool in the guest and does not substitute host Termux providers for guest providers. `termux doctor` also reports each Termux Android-tool candidate separately, Android full-NDK/SDK readiness for Android projects, and local SSH/chrootctl/tmux/PRoot availability. Text and JSON output are available for the dedicated `termux` diagnostics.

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

Devtool exposes a machine-readable inspector used by the GTK4 Control Center:

```bash
devtool -r /path/to/repo inspect-artifact overlay-or-bundle.zip --diff
```

The JSON result includes artifact identity, repository/workspaces, validation, inventory, bundle dependencies, preview counts, manifest text, and an optional repository-relative diff.

## Self-contained artifacts

Artifact schema v2 can carry the repository identity, monorepo workspace, target, solution, file inventory, an optional before/after-extraction preflight hook, validation policy, disposable validation script, and final commit message. A normal apply can therefore be reduced to:

```bash
devtool \
  --copy --auto-hud --hud-mode desktop-window --yes \
  -r "$HOME/Code/sims3tools" \
  apply-overlay "$HOME/Downloads/example_overlay.zip"
```

CLI options still override artifact values. Use `--no-validate`, `--no-commit`, or `--no-artifact-script` for explicit opt-outs. See [`docs/ARTIFACTS.md`](docs/ARTIFACTS.md) for the complete overlay and bundle formats.

## Resumable Android validation

Long Gradle validation can be checkpointed instead of keeping one Termux/chroot
process alive for the whole pipeline:

```bash
devtool -r "$HOME/Code/app" validate start
# Run this same command once per checkpoint:
devtool -r "$HOME/Code/app" validate continue
devtool -r "$HOME/Code/app" validate continue
```

`validate continue` runs exactly the next pending phase by default. Each run prints
`VALIDATION PHASE X OF Y` before work begins and an explicit phase-completed
banner afterward. At a checkpoint, Devtool reports the last completed phase and
the next pending phase. Once every phase succeeds, it prints a terminal
`VALIDATION COMPLETE` banner and confirms that no pending phases remain.
`--through`, `--phase`, and `--complete` remain available for advanced batching.
Selections include unmet dependencies automatically; `--phase-only` refuses an
unsafe prerequisite bypass. Ctrl+C and
Control Center cancellation return the current phase to `pending`; if Termux,
the chroot, Gradle, or Devtool is killed before recording the result, the next
`validate continue` automatically recovers the stale `running` phase and retries
it. The attached apply transaction remains available and is not rolled back for
an interruption.

The same controls work after `apply-overlay --validate --sequential`; the apply
transaction and rollback backup stay pending until validation is finalized. See
[`docs/ANDROID_PROJECTS.md`](docs/ANDROID_PROJECTS.md) for phase controls, power
guards, changed-file planning, and per-phase resource profiles.

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

The GTK4 desktop window starts in a compact, pipeline-focused mode. It shows separate live operation segments for extract, restore, build, test, artifact validation, commit, and rollback as applicable. Restore/build/test segments advance from parsed command output while the command is still running. Multi-workspace artifacts keep independent target-scoped segments such as `Build · s3atelier` and `Build · s3pi-base`, wrapped into rows of four. Live **ERRORS** and **WARNINGS** totals come directly from the active parser and remain visible beside the current operation, along with Copy summary/Open log/Close actions. Select **Expand** to reveal recent events and diagnostic details; the choice is preserved for the lifetime of that HUD window. Set `DEVTOOL_DESKTOP_HUD_START_EXPANDED=1` to launch expanded.

Set `DEVTOOL_NO_HUD=1` to hard-disable all HUD publication and rendering. This environment override wins over `--auto-hud`, `--hud-mode`, `--hud-overlay`, and direct `devtool hud` invocations, making it suitable for CI, headless shells, and commands that must never open or update a HUD.

## Validation sequence

For the common .NET case where build and test use the same target, validation uses:

```bash
dotnet test ...
```

`dotnet test` performs restore and build before executing tests. When build and
test targets differ, devtool keeps separate commands and avoids `--no-build`
unless the earlier build is known to cover the same target. Set
`[dotnet].coalesce_validation = false` or `[dotnet].explicit_restore = true` for
workflows that require separate phases, such as authenticated-feed CI jobs.

The GTK4 Control Center is Python-based and avoids native desktop build caches; package verification skips
an unnecessary warm CMake configure pass, uses `ccache` when available, and
compiles the common HUD/history/IPC implementation only once for both binaries.

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
and install the matching native app. It fingerprints build-relevant source
inputs rather than trusting timestamps. On desktop it rebuilds only when the
installed package is missing, its version differs, or relevant sources changed.
The temporary `.deb` is removed only after a successful installation.

On Termux, the Android companion is built through Devtool's repo-owned
`android_hud` target in the `androidos` chroot. The tracked APK is copied under
`$TMPDIR` before installation with `su -c pm install -r`. Installation is
skipped when the installed APK already has the same SHA-256 hash.

The Android flow also asks whether to create and install the optional KernelSU
root bridge module. Its ZIP is built directly under `$TMPDIR` and installed with
`su -c ksud install`; an unchanged installed module is skipped. The desktop flow
offers to install or update the clipboard monitor service.

Non-interactive examples:

```bash
./install.sh --yes --with-app --with-clipboard-service
./install.sh --app android --with-app --with-root-module
./install.sh --app android --with-app --without-root-module
./install.sh --app android --force-app-build --force-root-module-build
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


## Desktop Control Center and GTK4 HUD

The native Linux frontend lives in `desktop/gtk-control-center`. It provides Overview, Run, History, Diagnostics, Repository, Targets, Settings, and a detachable GTK4 compact HUD.

```bash
cd desktop/gtk-control-center
./build.sh
devtool control-center --backend gtk4
devtool hud --mode desktop-window --follow-active
```

Runs started by the app use `--hud-mode control-center`, which keeps live state publishing enabled without opening an extra compact HUD. `devtool hud --mode desktop-window` uses the GTK4 compact HUD. Launch an installed Control Center with `devtool control-center`.

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

The GTK4 application uses the native IPC broker for normal live operation, reconnect snapshots, concurrent repositories, and per-run cancellation. File-state monitoring remains available when the broker is unavailable.


### GTK4 Control Center packaging safety

The GTK4 Control Center package stages Python modules, launchers, freedesktop metadata, AppStream metadata, and icons through `desktop/gtk-control-center/package-deb.sh --verify`.

## GTK4 Control Center clipboard monitor

The Linux Control Center can install an optional per-user clipboard monitoring service from **Settings → Clipboard monitoring**. It recognizes local devtool artifact archives and copied `devtool` commands, ignores unrelated clipboard data, and never runs detected content automatically. The service can be enabled, disabled, reinstalled, or removed from the UI.


## Project runner architecture

Project command planning is now runner-based. See [`docs/PROJECT_RUNNERS.md`](docs/PROJECT_RUNNERS.md) for the .NET, Python, and custom-command runner contract and capability model.

Build and package outputs now use one verified artifact contract across first-class runners. Both `devtool build` and `devtool package` accept `-o/--output-dir`, `-n/--output-name`, `--artifact-collision {unique,replace,fail}`, and verification overrides. Successful package artifacts are hash-checked, copied atomically, and recorded as immutable production events in `.devtool/artifacts/registry.json`; failed commands never claim cached outputs as newly produced artifacts. Android release validation verifies its package phase through the same pipeline.


## Native frontend workflow Slice 2

The GTK4 Control Center and Android frontend now use the same four-stage Run flow:
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

The GTK4 Control Center exposes semantic accessible roles/names, visible focus
states, tooltips, full keyboard navigation, narrow page layouts, and a
screen-bound compact HUD with safer opacity. The Android frontend now enforces
48 dp targets, connects labels to fields, supplies TalkBack descriptions, and
stacks dense actions at large system font sizes. See
`docs/UI_ACCESSIBILITY.md` for shortcuts and the 200% scaling/TalkBack manual
verification matrix.

## UI regression protection — Slice 4

The terminal, desktop Control Center/HUD, and Android frontend now share a regression
contract for runner/status terminology, concurrent-run states, rollback states,
and large-data performance budgets. Golden Rich terminal snapshots and portable
SVG visual-state snapshots are stored under `tests/snapshots/ui/`; update them
with `python3 tools/update-ui-snapshots.py` only after reviewing the diff.

History search now keeps a case-folded in-memory index, Android test-catalog
items cache their search key and avoid one giant concatenated output buffer, and
the diagnostics parser routes common diagnostic families before the generic
regular-expression chain. See `docs/UI_REGRESSION.md`.

## Structured logs and retention

Devtool run logs are retained under the XDG state directory and rotated automatically. Defaults keep at most 120 logs for 30 days, cap retained data at 128 MiB, and bound an individual log to 12 MiB while preserving its beginning and newest output. Tune these limits with `DEVTOOL_LOG_MAX_FILES`, `DEVTOOL_LOG_MAX_AGE_DAYS`, `DEVTOOL_LOG_MAX_TOTAL_MB`, and `DEVTOOL_LOG_MAX_FILE_MB`.

The Android companion keeps a separate private structured app log. Its Logs page can browse either stream, search and filter entries, open bounded Devtool log tails through Termux RunCommand, copy/share visible output, rotate retained Devtool logs, or clear each stream independently.

Sealed final reports use a content hash and a write-once `.final.json` path; later report requests cannot overwrite the final evidence.


See `docs/DESKTOP_GTK_MIGRATION.md` for the Phase 1/2 desktop GTK4 migration notes.

## Destination bootstrap provisioning

Toolchains that were previously discovered or provisioned lazily during Android/Gradle runs can be installed deliberately once and registered for reuse:

```bash
devtool bootstrap install --destination termux
devtool bootstrap install --destination chroot-arm64:androidos
devtool bootstrap install --destination linux-amd64
```

The default bootstrap set uses Java 21, Gradle 9.7.1, Android platform 36, Build Tools 36.0.0, NDK auto-selection, and CMake 3.22.1. The same provider knobs used by the Android builder are available on `bootstrap install`. `--repair` re-runs provisioning without forcing newer downloads; `--upgrade` forces refresh of Devtool-managed downloads. `bootstrap status` verifies the destination receipt and known provisioned paths, while `bootstrap remove` only removes the Devtool receipt and deliberately leaves shared system packages/toolchains installed.

On native Termux, provisioning resolves missing Bionic-native Android helpers through the package manager using all currently enabled APT sources rather than assuming one repository owns them. The official `aapt` package is preferred for `aapt2`, `aidl`, and `zipalign`; exact-name packages from custom repositories are accepted when available. If a helper such as `split-select` still needs a native donor, Devtool prefers the TermuxVoid `android-sdk` APT package and its `$PREFIX/opt/android-sdk` tree before falling back to the existing provider release bundle. Package indexes are refreshed only when a required capability cannot be resolved from the current indexes.

A successful install writes a destination receipt under the Devtool XDG state directory. Termux receipts include the concrete Android build-tool paths and are accepted only after Bionic runtime probes succeed. Later Devtool runs targeting that destination automatically set Devtool-managed toolchain downloads to `never` **only while the receipt still probes cleanly**. If a recorded helper disappears or stops executing after a package update, the receipt is treated as stale and normal repair is re-enabled. Project dependency/network access is not disabled by the receipt. Override the seal for one invocation with `--bootstrap-downloads auto`, `--bootstrap-downloads missing`, or `--bootstrap-downloads force`.

`chroot:<name>` accepts the detected chroot architecture. Use `chroot-arm64:<name>` or `chroot-amd64:<name>` when the architecture must be verified explicitly. Chroot provisioning is launched from native Termux through the existing staged `build-apk` runtime, so Devtool itself does not need to be installed inside the guest.

## Termux Java and Gradle providers

Native Termux runs now distinguish Termux-packaged Java/Gradle from ordinary
Linux toolchains. A complete project Gradle Wrapper remains authoritative in
`auto` mode; if wrapper metadata is present but `gradle-wrapper.jar` or another
required wrapper file is missing, devtool reports the incomplete wrapper instead
of silently switching Gradle versions. Use `--java-provider` (`auto`, `termux`,
`system`) and `--gradle-provider` (`auto`, `wrapper`, `termux`, `system`,
`download`) for explicit selection. An explicit `--java-home` or
`--gradle-command` remains authoritative. For wrapperless Gradle projects, native
Termux `gradle` is eligible in `auto` mode; Android projects additionally require
a conservative AGP/Gradle major-generation match before an unpinned Termux
Gradle is selected. `doctor` reports the detected Termux Gradle/OpenJDK provider
paths and tool versions alongside read-only package metadata, without installing
packages. Native Termux runtime reporting uses the Android API environment value
when available and falls back to Android `getprop`; providers whose version probe
fails are shown as detected but unusable rather than as compatible tools.

Configuration can be stored as:

```toml
[android.java]
provider = "auto"

[gradle]
provider = "auto"
```

## Termux native and Android build-tool providers

Native Termux can use its packaged `clang`, `clang++`, `lld`, `llvm-ar`,
`llvm-ranlib`, `ndk-sysroot`, `cmake`, `ninja`, and `ccache` as explicit
providers. `ndk-sysroot` is treated only as a compiler/sysroot capability; an
Android project using `externalNativeBuild`, CMake/ndk-build integration, or an
NDK declaration still requires a complete Android NDK. This prevents native
helper builds from triggering an unnecessary NDK bootstrap without weakening
Android NDK reproducibility.

Termux Android tools are also discovered as providers. In `auto` mode, a native
Termux `aapt2` must pass a real compile/link capability probe against the selected
`android.jar` before it can override AGP. On native Termux/Bionic, incompatible SDK
`aidl`/`zipalign`/`split-select` binaries must be replaced by runnable Termux-native
executables; Commit451 artifacts are deliberately rejected there because upstream
publishes linux-glibc-arm64 binaries. On ARM64 glibc hosts/chroots, the verified
Commit451 provider remains the fallback and release availability is resolved before
asset URLs are downloaded.

```toml
[android.native]
provider = "auto" # auto | termux-sysroot | ndk | system
cache = "auto" # auto | ccache | off

[android.build_tools]
provider = "auto" # auto | termux | sdk | commit451 | none
aapt2_provider = "auto" # auto | termux | sdk | revanced | none

[cmake]
provider = "auto" # auto | termux | system
native_provider = "auto" # auto | termux-sysroot | system
cache = "auto" # auto | ccache | off
```

Equivalent one-run options are `--native-provider`, `--native-cache`,
`--android-build-tools-provider`, and `--aapt2-provider`. `doctor` reports the
Termux native compiler, CMake, Ninja, ccache, and Android-tool capability states.
Generic CMake projects may also set `[cmake].native_provider`; on native Termux, `auto` uses the complete clang/lld + `ndk-sysroot` provider when available and never treats it as a full Android NDK.

## Android NDK host fallback

For ARM64 Termux/chroot validation of Android projects with native code, devtool
can optionally use an unofficial ARM64-host NDK or wrap the official x86_64 NDK
with QEMU plus an auto-provisioned amd64 sysroot. See
`docs/ANDROID_NDK_HOST_FALLBACK.md`.


Final Termux/PRoot acceptance audit: `docs/TERMUX_PROOT_PROMISE_AUDIT.md`.
