## Android 1.14.0

- Added sparse user global configuration with `devtool config path/show/defaults/init/edit/doctor --global`, editor launch that prefers micro, CLI preference defaults, and Android/Termux chroot project-kind policy.
- Finalized the UI/UX contract after the HUD, notification, typography, navigation, Overview, and Run-flow polish stack, including documentation and focused regression gates for all accepted surfaces.
- Polished Android notification accessibility: 7dp progress rails, dominant chip-style state labels, content descriptions for progress/status/phase/workspace/action surfaces, and contract tests for 48dp action targets.
- Polished detailed desktop and Android HUD layouts without reducing information density: clearer current-run/status/workspace/diagnostics regions, Android floating HUD position persistence, drag/display clamping, and rotation/font-scale bounds handling.
- Polished Overview and Run workflow topography: action-first Overview pages, wrapping desktop quick actions, dynamic no-test steppers, and progressive Run advanced/danger sections on desktop and Android.
- Polished Devtool Control Center and Android companion typography/navigation: compact headers, stronger selected navigation, sentence-case metrics, Logs copy, and human readiness labels.
- Added redacted Android support bundles for Logs, Repositories, root bridge, retention and Git-operation diagnostics.
- Added Settings controls to copy or share a support bundle.
- Added an opt-in switch for including redacted recent commands; commands remain disabled by default.
- Added reusable redaction for bridge tokens, HTTPS credentials, Authorization headers, common secret assignments and private-key blocks.

# Changelog
- Fixed successful Android validation being rolled back when Gradle infrastructure notices were counted against an artifact's zero-warning policy; expectation failures are now printed explicitly, and lint receives a bounded 512 MiB metaspace cap.

## Unreleased

- Added Android repository Phase 6 mobile hardening: adaptive navigation rails and split panes for tablets/foldables, large-text-safe vertical actions, live Git queue timing and reordering, pinned-run retention, configurable indexed-data cleanup, and optional SQLite compaction.
- Added passive credential-remediation guidance with copyable commands, explicit host-fingerprint warnings, and no automatic remote probes or secret display.
- Added Phase 6 accessibility instrumentation for responsive profiles, labeled controls, and 48 dp touch targets; upgraded the Android indexed database to schema 6.
- Bumped Devtool Live HUD to 1.13.0 (`versionCode 26`).

- Added Android repository Phase 5 cross-feature integration: durable per-repository Git operation queues, cancellation, root-process markers, orphan detection/termination, synthetic structured Git runs, completion notifications, and repository-filtered saved Logs views.
- Added passive SSH/credential diagnostics and local incoming/outgoing commit previews without contacting remotes or exposing secrets.
- Linked Git operations bidirectionally across Repos, Logs, runs, commits, and artifacts, including exact operation metadata in run details and recovery of interrupted operations after Android process death.
- Bumped Devtool Live HUD to 1.12.0 (`versionCode 25`).

- Added Android repository Phase 4 full Git parity: staged commit creation, local and remote branch checkout, branch creation, fetch with prune/tags, fast-forward-only pull, non-force push with automatic first-upstream setup, stash/pop including untracked files, and strongly confirmed tracked-only hard reset.
- Added local/remote branch and stash views backed by NUL-safe Git records, per-repository operation serialization, owner-aware Termux/chroot execution, bounded network operations with interactive prompts disabled, credential redaction, and structured repository activity events.
- Bumped Devtool Live HUD to 1.11.0 (`versionCode 24`).

- Added the Android repository working-tree and commit-history phase: NUL-safe porcelain-v2 parsing, grouped staged/modified/untracked/deleted/renamed/conflicted files, working and staged diffs, file and bulk stage/unstage, guarded tracked-file discard, commit details, remote links, and Devtool run/artifact correlation.
- Added owner-aware Git mutation serialization and repository activity records for the Phase 3 path-scoped operations.
- Bumped Devtool Live HUD to 1.10.0 (`versionCode 23`).

- Added the Android Repositories destination as a root-aware read-only port of the desktop Repo page, including explicit and discovered repositories, temporary live entries, environment-aware identity, status/branch/remote inspection, active run and artifact correlation, rooted browsing, repository-owner shell launch, and Overview/Activity/Runs/Metadata detail views.
- Replaced Android SharedPreferences run history with a SQLite-backed runs, structured logs, repositories, activity, source, and cursor model; added legacy migration and exact-run Timeline/Problems/Raw/Metadata viewing.
- Added durable resumable Android/Gradle validation sessions with preflight, dependency, compile, resource, unit-test, package, artifact verification, lint, instrumentation, and finalization checkpoints. Sessions survive process death, preserve pending apply transactions, support phase/range/runtime/power controls, cache failures, invalidate only affected downstream phases, and report overall phase progress to terminal, machine output, desktop HUD, and Android HUD.
- Added changed-file phase planning, per-phase Android resource profiles, optional Gradle daemon shutdown after each phase, explicit retry/finalize/abort/status commands, and paused-apply notifications that no longer claim an incomplete overlay was fully applied.

- Made native-app installation fingerprint-aware: desktop packages rebuild only when relevant sources or the packaged version change, while Android APKs rebuild through the repo-owned `android_hud` chroot target and reinstall only when the installed APK hash differs.
- Added an optional smart KernelSU root-bridge module flow. The module is built directly under `$TMPDIR`, installed with `su -c ksud install`, and skipped when its source fingerprint and installed version are current. Android APK installation likewise stages under `$TMPDIR` before `su -c pm install -r`.
- Added a repo-owned `.devtool.toml` with an explicit Android HUD target pinned to the `androidos` chroot and constrained Gradle settings.

- Added bounded Devtool log rotation with age, count, total-size, and per-file retention controls.
- Added machine-readable `devtool logs list/read/prune/clear` operations for companion and automation clients.
- Added independent structured JSONL logging for the Android companion app, including crash, bridge, Termux, lifecycle, and launch diagnostics.
- Added a five-tab Android log viewer with Devtool/App sources, search, level filters, bounded log tails, copy/share actions, and separate retention controls.

- Fixed Android companion HUD discovery for debug and release application IDs. Manual Termux runs now target the installed package with the fully qualified `BridgeReceiver` class, honor explicit component overrides, persist package/component pairing markers, include stopped packages in bridge broadcasts, and report bridge preflight failures instead of silently losing notifications.
- Rebuilt Python handling around strict project inspection, manager-aware reproducible environments, dependency fingerprints, project-environment artifact validators, framework-safe selection, affected-file validation, structured lint/type/coverage reporting, clean wheel and sdist verification, native/workspace/matrix support, service/database/notebook gates, SBOM and reproducible builds, and target-aware cleanup.
- Python package verification now validates wheel `RECORD` hashes and sizes, rechecks wheels rebuilt from sdists, clears stale distribution output only when execution begins, and defaults to clean installed-wheel import and console-script smoke tests.
- Python isolation now sanitizes ambient interpreter variables, can enforce a real Linux network namespace, installs PEP 735/Poetry/PDM validator groups into cached validator environments, executes notebooks from disposable copies, and safely terminates services before collecting output.

- Added artifact-aware Android lint planning: validation now lints only changed Gradle modules, skips lint for documentation-only overlays, falls back to full lint for shared build configuration changes, and enforces a 512 MiB lint metaspace floor even when the general validation cap is lower.
- Reworked Android validation memory protection to use `MemAvailable`, swap headroom, PSI stalls, and memory trend together; healthy KSP dips around 450 MiB no longer roll back, and duplicate parent/child memory watchdogs no longer reclaim caches twice.
- Made Android validation memory handling root-aware: hairline `MemAvailable` dips no longer abort builds, sustained and hard-emergency thresholds remain enforced, and Termux root may perform one conservative clean-page-cache reclaim before rollback.
- Added `--ignore-checksum` for checksum-only artifact recovery without disabling repository, inventory, size, conflict, or validation-policy checks.
- Added an Android-safe validation profile that splits Gradle validation phases, limits workers, logical CPUs, and heap, disables parallel/daemon-heavy behavior, lowers process priority, and aborts before critically low free memory can destabilize the phone.

## 1.1.0 — 2026-07-16

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
