## 2026-09-10 - MP01 + MP02 polyglot target identity and reusable workflows

- Separated canonical target `runner` identity from independent `platform` identity while preserving `[project].kind` and target `type`/`kind` as compatibility aliases.
- Replaced the language-specific target model name with `TargetConfig` and retained `DotnetTarget` as a strict compatibility alias.
- Added reusable `[jobs.*]` declarations with runner ownership, structured argv or explicit shell execution, native runner actions, roots, environments, inputs/outputs, metadata, and runner-specific options.
- Added `[targets.<name>.workflows]` with validated `job:<name>`, `runner:<runner>#<action>`, and `target:<target>#<action>` references, ready for MP03 graph execution.
- Added `jobs`/`workflows` inspection commands, richer target/inspect/config/doctor/machine output, GTK target-card parity, migration diagnostics, canonical config-template output, completion, manpage, and documentation coverage.
- Preserved all existing runner execution behavior when workflows are absent; MP03 remains the explicit boundary where declarations become an executable DAG.

## 2026-09-10 - TS00 full pytest test-selection parity

- Added repeatable `--pytest-test` exact-node selection to test, validation, persistent validation, and artifact-validation command paths, with file/class/function/method/parametrized-node support.
- Exact pytest selection is exclusive by default: configured/default/affected pytest targets are suppressed unless `--pytest-include-defaults` is explicitly requested.
- Added structured `--pytest-marker`, `--pytest-k`, and repeatable `--pytest-arg` handling without shell-string construction.
- Added `[python.pytest]` defaults, profile-level pytest selection, schema-v2 artifact pytest-selection metadata, CLI-over-artifact precedence, and deferred-validation persistence.
- Added native `pytest --collect-only -q` discovery with source-catalog fallback, terminal multi-select/refresh controls, GTK command-model fields, completion, machine metadata, and structured pytest selection/empty-collection diagnostics.
- Added TS00 regression coverage and expanded test-selection documentation.


## Devtool validation continue-by-default hotfix

- Default validation now continues through independent phases/tasks so Android and Gradle runs collect as many diagnostics as possible in one pass.
- `--fail-fast`, `[validation].fail_fast = true`, or `DEVTOOL_GRADLE_FAIL_FAST=true` still opt back into stop-on-first-failure behavior.
- Android validation no longer injects `--fail-fast` unless an explicit fail-fast policy requests it.

## 2026-09-10 - Transactional artifact preflight hooks

- Added optional schema-v2 `preflight` scripts that run before or after extraction using the hardened embedded artifact-script runner.
- Added declared `preflight.write_paths` to artifact previews, transaction backup, and scoped path reservations so hook side effects participate in rollback and concurrent-operation conflict protection.
- Added checksum-boundary enforcement: declared hook writes may intentionally transform inventory source/payload files, undeclared checksum changes fail and roll back, and preflight receives a private artifact copy so the source archive remains immutable.
- Fixed the private preflight artifact copy to use owner-only read/write mode (`0600`) rather than read-only `0400`, allowing hooks to mutate their disposable copy on Termux while preserving the original archive.
- Added bundle and nested-overlay preflight sequencing, checksum-bypass propagation, environment/timeout/runner validation, and dedicated regression coverage.


## 2026-09-09 - Memory guard default and legacy commit-policy compatibility

- Disabled implicit Android/Termux memory guard defaults; the guard now remains off unless CLI, environment, project config, or a phase profile explicitly sets a positive `memory_guard_mb`.
- Updated Devtool's own Android HUD validation profile to keep the guard disabled by default across phases.
- Extended artifact commit policy parsing to honor legacy top-level `commit`, legacy `git.commit`, and `commit_message` manifests after the canonical `apply.commit` field.
- Added regression tests covering old-style overlay commit manifests and no-guard Android/Termux validation defaults.


- Fix Python source-audit dependency ownership: project dependencies are enforced for package/project automation by default, while heterogeneous auxiliary scripts and captured pip console launchers are inventoried without being misclassified as package requirements; strict repository-wide enforcement remains opt-in via `[tool.devtool.python-audit] dependency-scope = "repository"`.
## 2026-08-29 - Go validation diagnostics and recovery reporting
- Fixed generic Gradle/JVM `devtool build` launcher selection so `provider = "auto"` prefers an installed Gradle that exactly matches the configured/wrapper version instead of forcing `GradleWrapperMain` to download the same distribution; Gradle runtime flags are now honored and wrapper connect timeouts are classified as `network_timeout`.


- Replaced coalesced Go validation with separate restore, real executable build/artifact verification, structured `go test -json`, and vet stages.
- Added typed Go test/package diagnostics with package totals, exact test/file/line/message extraction, focused rerun commands, and human-readable durable logs without JSON envelope noise.
- Fixed failure summaries and clipboard output so Go test failures no longer degrade to `unknown failure` or hide the root cause behind tail truncation.
- Counted rollback/cleanup as recovery rather than successful workflow stages and synchronized artifact-validation/evidence messages into the durable run log.
- Tightened the generic warning/error fallback to diagnostic-prefixed `warning:` / `error:` lines so source/test text containing severity words is not reported as a false warning.

## 2026-08-28 - Go executable build outputs

- Go builds now discover `package main` targets, emit verified binaries for `devtool build`, support workspace-aware target discovery/output-name overrides, reject basename collisions, and persist `go version -m` provenance.
- `packages` remains the test/vet scope; `build_target`/`build_targets` independently control executable discovery, with `./...` as the default build scope.
- Library-only Go repositories retain `go build ./...` compile-check behavior, while `devtool package` requires at least one executable target.

## 2026-08-28 - Build/package promise audit seal
- Go builds now discover `package main` targets, emit verified binaries for `devtool build`, support workspace-aware target discovery/output-name overrides, reject basename collisions, and persist `go version -m` provenance.

- Closed post-delivery gaps found by an 18-item acceptance trace: immutable verification events, append-only stale-save protection, live JVM Gradle artifact routing, richer artifact summaries/JSON, promoted registry provenance, and verified release-asset provenance.
- Added durable `fsync` before atomic artifact rename, isolated deterministic root-module staging, Maven reactor artifact discovery, plain Gradle tar verification, `.NET pack` variant coverage, and current-run-only Android wrapper artifact resolution.
- Added `docs/BUILD_PACKAGE_PROMISE_AUDIT.md` with implementation and regression evidence for every promised build/package improvement.

## 2026-08-28 - Build/package artifact pipeline overhaul

- Unified build/package artifact collection, verification, managed copying, export, and immutable registry provenance across first-class runners.
- Added first-class Android/JVM Gradle packaging, native `dotnet pack`, verified Node/Cargo/Go/Maven/CMake/Meson package outputs, and Python package registration after deep wheel/sdist verification.
- Fixed Android release/resumable validation so the package phase actually collects and verifies required artifacts, including external verification policy.
- Added cross-runner `build`/`package` artifact output naming, collision policy, and verification CLI controls plus declared artifact paths/directories for custom output layouts.
- Replaced mutable path-keyed artifact history with schema-v2 append-only production events, process-safe registry locking, atomic durable writes, corruption preservation, and failed-build non-registration.
- Added structured Gradle task artifact-output provenance, hash-checked atomic artifact copies, produced/reused/preexisting state, and detailed artifact summaries.
- Made source/root-module release archives deterministic from `SOURCE_DATE_EPOCH` or Git commit time, and unified source/Python/root/desktop/Android release assets under one manifest and checksum set.
- Updated Android wrapper packaging to consume Devtool's verified artifact registry instead of hard-coded Gradle output paths.

## 2026-08-25 - Android metaspace and Termux resource-control hotfix

- Promote real Gradle metaspace OOMs to the primary fatal resource diagnostic.
- Add `--gradle-metaspace-mb 0`, `--no-metaspace-guard`, and `--no-termux-resource-profile`.
- Report effective Gradle JVM memory provenance more truthfully.
- Structure low-storage and ADB probe warnings and reject ADB error text as device state.
- Preserve child-process exit codes from the standalone Android builder.

# Changelog

## 1.1.0 Termux integration doctor and final seal

- Added `devtool termux doctor`, `termux packages`, and `termux explain` with text/JSON output, detailed native Termux provider readiness, selection/rejection explanations, read-only package recommendations, and optional installed PRoot guest tool discovery.
- Extended generic `doctor --explain` with the same provider-selection reasoning while keeping the dedicated Termux diagnostics host-only so auto-chroot cannot relocate them.
- Finalized configuration, completion, manpage, and documentation contracts for Termux Java/Gradle/native/Android-tool providers, runtime hardening, PRoot execution, backend-scoped caches, and package recommendations.
- Added final integration regression coverage proving guest probes do not require devtool, package advice never installs/upgrades packages, and the CLI/documentation/configuration surfaces stay synchronized.
- Final promise audit closed remaining integration gaps: dynamic read-only OpenJDK package advice, per-tool Android/NDK and SSH/chroot backend doctor rows, validation-control live-progress totals compatibility, explicit-XDG log isolation, cleanup of SDK/HTTP probe streams, and exact Git HEAD/index restoration when an atomic apply rolls back after staging or committing.
- Added `docs/TERMUX_PROOT_PROMISE_AUDIT.md`, a 38-item acceptance matrix tied to regression evidence; the complete non-Android-app Python suite is part of the final seal.
- Hardened the final host validator against native Android leakage by scrubbing `ANDROID_*` markers, isolating Tasker/phone-log defaults when `DEVTOOL_XDG_DIR` is explicit, and placing full-suite temporary files under a short private root so AF_UNIX IPC tests cannot exceed Android socket-path limits.
- Corrected normal native-Termux `devtool validate` isolation after the final seal: Python validator HOME/TMPDIR now live outside the repository (using private `$PREFIX/tmp` on native Termux), inherited Devtool test-state variables are scrubbed instead of forcing a phone-log mirror, PTY suites auto-restore `pexpect` when imported but undeclared, and AF_UNIX regression fixtures honor the configured temporary directory.

## 1.1.0 Termux runtime hardening and PRoot-Distro backend

- Added shared-storage safety policy, backend-isolated native Termux caches, resource profiles, optional wake-lock lifecycle, verified-artifact export, runtime/network failure classification, shebang adaptation, tmux visibility, and non-fatal Gradle quiet-period diagnostics.
- Added first-class PRoot-Distro command-phase execution with explicit host/guest mappings, automatic repository binds, user/work-dir/bind/env controls, helper staging and cleanup, environment hygiene, progress preservation, and installed-container preflight.
- Scoped provider discovery to the selected execution backend so host Termux tools are not embedded in PRoot guest command plans, including Termux package launcher scripts; reserved backend environment state cannot be overridden by guest configuration.
- Added pre-execution ELF architecture/libc checks for native Termux, kept automatic caches off shared storage, and prevented phone-local resource defaults from leaking into SSH jobs that execute on independent remote hosts.
- Re-rooted host-only Python/native/package cache variables into the bound `/var/cache/devtool` PRoot cache so guest commands never require explicit binds for host devtool cache paths.
- Kept PRoot-Distro available on ordinary Linux hosts when installed.

## 1.1.0 Termux native and Android tool providers

- Added first-class Termux clang/lld/ndk-sysroot, CMake, Ninja, and ccache capability providers without pretending ndk-sysroot is a complete Android NDK.
- Added capability classification so Android external native builds continue to require a real NDK while compiler-only native work can use Termux-native tooling.
- Added Termux Android tool discovery, AAPT2 platform capability probing, and Termux-first ARM64 aidl/zipalign/split-select repair with runtime execution checks; native Termux/Bionic now rejects Commit451's glibc binaries while ARM64 glibc hosts retain the verified fallback.
- Added Commit451 release-availability resolution and kept the 35.0.0 -> 35.0.1 compatibility alias so unavailable tags fail before asset downloads.
- Tightened Android native classification so unrelated host-side CMakeLists.txt files do not trigger a full NDK, while ndkVersion and ndk-build makefiles do.
- Added explicit native/cache/build-tools/AAPT2 provider controls across CLI, config, doctor output, completion, manpage, and the staged standalone Android builder.

## 1.1.0 Termux provider foundation

- Added first-class Termux runtime, OpenJDK, and Gradle provider discovery with wrapper-safe selection and explicit provider controls.
- Added read-only Termux package metadata probing, Android/Bionic runtime capability reporting, and actionable provider diagnostics.
- Preserved explicit host execution for devtool-side validation so repository tooling does not implicitly route through the Android chroot.

## Desktop launcher cleanup

- Removed the obsolete `devtool-gtk-control-center` and `devtool-gtk-hud` compatibility executables.
- Kept `devtool-control-center` and `devtool-control-center-hud` as the only standalone desktop commands.

## Desktop HUD and Control Center phase 6-7

- Replaced the Control Center Run page shell launch with a direct argv process launcher that assigns a stable desktop run id, captures live stdout/stderr, writes redacted per-run logs, and supports graceful cancel plus force-cancel through the process group.
- Added persisted desktop preferences for window size, selected Control Center page, appearance, and reduced motion.
- Added Adwaita appearance hooks, semantic accent colors, keyboard shortcuts, accessibility labels, and reduced-motion handling for the GTK Control Center and HUD.

## Chroot Gradle graph transport and completion fallback

- Preserved `DEVTOOL_RUN_ID` explicitly through the Termux-host `sudo/chrootctl` toolchain wrapper so structured Gradle graph events are accepted by the host progress tracker.
- Counted unique fallback `> Task` lines, parsed Gradle actionable-task summaries, and inferred a successful final total when structured events are unavailable.
- Prevented completed explicit-task runs from remaining at `N/? tasks • discovering dependencies`; final output now reports requested and dependency counts.


## Android Live HUD bridge variant protocol fix

- Derived bridge action namespaces from the resolved debug or release application ID instead of pairing a debug component with release actions.
- Added protocol versioning, ordered-broadcast acknowledgements, rejection parsing, and local unsupported-action guards.
- Made the bundled Termux helper consume pairing package/component markers and added executable debug/release protocol regression coverage.


## 1.1.0-rc.3 desktop GTK4 packaging flip

- Promoted GTK4 Control Center packaging and removed brittle historical PHASE_* Markdown release-test requirements.


## Desktop GTK4 migration phase 1-2

- Extracted reusable desktop command, HUD snapshot, IPC, history, repository, and artifact models under `devtool.desktop`.
- Wired the legacy Qt Run page advanced fields so environment assignments, extra global args, extra operation args, and the `--yes` checkbox affect generated commands.
- Added a GTK4/libadwaita compact HUD and made `desktop-window` prefer it, with `qt-window` retained as the explicit legacy Qt fallback and `gtk-window` kept as a deprecated GTK3 compatibility path.
- Added `devtool-gtk-hud` as a direct executable for the GTK4 HUD and preserved `--once` JSON snapshots without requiring a display.


## Interactive validation cockpit promise-completion fixes

- Added revision guards to every active-session continuation, retry, exact-phase, through-phase, and selected-phase command.
- Replaced cached Ctrl-R repainting with real catalog reloads and added an explicit refresh action to the numbered fallback.
- Resolved pre-session phase choices from the real project runner plan instead of a hardcoded Android/generic list.
- Added reset-from-phase, stable pretty/compact JSON output selection, generic supported safe-retry profiles, and combined multi-phase dependency previews.
- Preserved repository, target, variant, environment, HUD, clipboard, and output arguments in saved recipes.
- Added multi-setting context/resource pages, detected-context warnings, a deprecation warning for validation `--profile`, and pseudo-terminal fallback coverage.
- Fixed duplicate `--hud-overlay` generation.

## Interactive validation cockpit and wizard

- Added `devtool menu validation`, `devtool validation-menu`, and `devtool vm` as a state-aware fzf/bat validation cockpit with a complete numbered fallback.
- Added guided flows for one-shot, collect-all, durable sequential, watch, matrix, Wine, artifact/bundle integrity, and apply-time validation.
- Added revision-guarded next, through, selected, remaining, safe retry, pause, stop, recover, reset, plan acceptance, finalize, report, live-log, and keep-or-rollback abort actions.
- Added canonical command review with run, dry-run, clipboard copy, editor handoff, live logs, and global or repository TOML recipes.
- Added one-run validation resource overrides for workers, CPUs, Gradle heap, memory guard, priority, parallelism, build cache, and daemon policy. Explicit overrides take precedence over resource profiles and persist in durable session options.
- Added Gradle task discovery, dependency-closure previews, complete structured test selection, runtime/device guards, shell completion, manual documentation, and regression coverage.

## Phase 32 validation reports / Phase 33 release seal

- Retained immutable validation reports with stable hashes, original and final Git heads, and distinct validation-history cards.
- Retained finalization-integrity gates, the 16 mandatory failure scenarios, and the Android toolchain release gate.

## Validation finalization history-path hotfix

- Fixed automatic finalization failing with Git exit 128 when `.devtool/artifacts.json` is absent and untracked.
- Artifact history paths are now staged only when they exist or are already tracked, while tracked deletions remain supported.
- Added regression coverage for absent, existing, ignored, and deleted tracked artifact-history registries.

## 47C-5/47C-6/47C-7

- Completed backup rule metadata for legacy Auto Backup plus Android 12+ data extraction.
- Added monochrome launcher icon support.
- Fixed overlay drag header accessibility performClick contract.
- Moved remaining lint-reported setText concatenations into string resources/plurals.
- Removed dead color/style/drawable resources.

## UI contract archival cleanup

- Finalized the UI/UX contract gate without requiring historical PHASE_* Markdown files.

## Post-seal validation build hotfix (2026-08-14)

- Fixed uv-managed Python builds so Devtool uses `uv build --out-dir ...` instead of `uv run python -m build --outdir ...`.
- This removes an accidental dependency on the separately-installed PyPA `build` frontend from ordinary `devtool validate` while preserving PEP 517 build-system isolation.
- Python projects managed by other environment managers retain the existing `python -m build` path.
- Native uv build planning preserves Devtool's Python `offline` option by forwarding `--offline`.

## Post-seal package-check hotfix (2026-08-14)

- Rebuilt uv-managed sdists with `uv build --wheel` during package verification instead of spawning a separate `pip wheel` build-isolation resolver.
- Made `verify_cli` validate installed/executable console-script wrappers without launching application entry points from a deliberately no-dependency wheel-check environment.
- Prevented interrupted package checks from being misreported as missing `DEVTOOL_PYTHON_PACKAGE_RESULT` payloads, and classified Ctrl-C results as interrupted control flow.

## Python validation diagnostics hotfix

- Make pytest/JUnit failures framework-aware instead of labelling Python tests `GRADLE_TEST`.
- Deduplicate raw pytest `FAILED` lines against authoritative JUnit testcases.
- Structure pytest warnings so HUD/JSON/clipboard counts agree.
- Classify Python dependency, import, platform, and assertion failures separately.
- Extend Python source audit to production Python/shebang scripts outside package roots, undeclared imports, and glibc-only `libc.so.6` loads.
- Render Python JUnit failure categories and canonical testcase details in failure summaries.

## 2026-09-08 - Devtool test log isolation follow-up

- Isolate Devtool's own pytest-created logs into pytest temporary directories so validation runs no longer pollute the user's retained command log list or phone mirror with nested synthetic logs.
- Reset the runtime log-dir override around every test and clear inherited XDG roots before installing test-local state/cache/data roots.
- Added regression coverage for nested Devtool log creation staying out of `~/.local/state/devtool/logs` and `/sdcard/.logs/devtool`.
