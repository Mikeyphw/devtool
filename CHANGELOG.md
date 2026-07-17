# Changelog

## 1.1.0-rc.6 — 2026-07-16

- Added optimized first-class runners for Node.js, Rust/Cargo, Go, Maven, CMake, Meson, and Make.
- Added one shared CPU/memory-aware worker policy for desktop Linux, native Termux, and Android-hosted chroots.
- Exported runner cache and resource variables inside `chrootctl` instead of leaving them only in the Termux host environment.
- Coalesced Cargo, Go, and Maven validation into their safest single-invocation lifecycle commands.
- Reused warm CMake, Meson/Ninja, Python virtual-environment, package-manager, compiler, and language caches.
- Added generic completed/total progress parsing for Ninja-style and Maven reactor output.
- Expanded auto-detection, named-target options, CLI project-type choices, generated configuration, status inspection, and Zsh completion for every first-class kind.

## 1.1.0-rc.5 — 2026-07-16

- Coalesced normal same-target .NET restore/build/test validation into one `dotnet test` invocation.
- Removed the standalone .NET restore process from safe split-target validation and corrected `--skip-restore` so it also disables implicit restore.
- Retained MSBuild/Roslyn build servers by default; shutdown is now opt-in through `[dotnet].stop_after_run = true`.
- Added `[dotnet].coalesce_validation` and `[dotnet].explicit_restore` compatibility controls.
- Compiled the Qt Control Center's shared HUD/history/IPC sources once instead of once per desktop executable.
- Skipped redundant warm CMake configuration and enabled `ccache` automatically when available.
- Humanized combined validation phase labels in terminal/shared HUD state, Android notification/app, and Qt desktop HUD/app.

## 1.1.0-rc.4 — 2026-07-16

- Coalesced normal Android/Gradle validation into one Gradle invocation instead of separate restore, build, test, and lint launches.
- Omitted the redundant inferred `help` task when real validation tasks are present.
- Enabled reusable Gradle daemons, persistent build cache, and parallel multi-module execution through target settings; daemon shutdown is now opt-in.
- Preserved both Gradle JUnit and Android Lint report collection from the combined invocation.
- Added explicit `completed/total tasks` progress to the terminal, Android notification, Android run history/app, and desktop HUD.
- Forced the first task-graph update through progress throttling so native frontends receive the total as soon as Gradle discovers it.
- Restored the legacy `generic` project-kind alias and synchronized the packaged Zsh completion with the runtime command set.
- Made artifact-scoped commits force-add exact declared ignored payloads while excluding ignored Devtool runtime history from the embedded commit.

## 1.1.0-rc.3 — 2026-07-14

- Whenever `devtool-events.init.gradle` is injected, also pass `--no-configuration-cache` to avoid Gradle configuration-cache incompatibility.
- Replaced the Android stock `InboxStyle` run notification with custom collapsed and expanded `RemoteViews` matching the approved mock.
- Added state rail/badges, workspace and phase cards, test/warning/error/elapsed metrics, and mode-specific actions.
- Added per-workspace HUD payloads so notification state updates independently of aggregate progress.
- Bumped Devtool Live HUD to 1.7.2 (`versionCode 20`).

## 1.1.0-rc.1 — 2026-07-14

### Release qualification

- Added a canonical cross-package version contract and consistency gate.
- Added native Qt compile/link and offscreen QML smoke tests through CTest.
- Added reproducible Debian staging, checksums, and package payload verification.
- Added Android JVM and connected instrumentation smoke tests.
- Enforced explicit Android release signing and added `apksigner` verification.
- Added deterministic source release creation with repeat-build comparison.
- Added a GitHub Actions matrix for Python, Qt, Android, emulator, and packaging.
- Added release-candidate, migration, and manual device/scale qualification guides.

### Versions

- Devtool CLI: 1.1.0
- Devtool Control Center: 0.4.0
- Devtool Live HUD: 1.7.0 (`versionCode 18`)
