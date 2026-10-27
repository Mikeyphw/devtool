# Devtool complete guide for humans

This is the comprehensive operator guide for the Devtool source tree in this repository. It is intended to answer two questions without requiring source-code archaeology: **what can Devtool do?** and **how do I use each capability safely?** The final appendix is generated directly from the current CLI parser so the public command surface is covered command-by-command and option-by-option.

> Start here for normal interactive use. If you are building an autonomous agent, CI wrapper, or another Devtool frontend, use [`DEVTOOL_AI_AGENT_GUIDE.md`](DEVTOOL_AI_AGENT_GUIDE.md) alongside this guide.

## 1. Mental model

Devtool is a repository-aware orchestration layer. It can discover projects, select one or many configured targets, execute native project runners, apply and validate overlay artifacts, coordinate durable validation sessions, manage Android toolchains/devices/signing, expose machine APIs to frontends, and retain structured diagnostics/history. It does **not** replace Gradle, pytest, Cargo, Go, npm, or other build systems; it gives them a common target/configuration/validation/artifact model.

The common shape is:

```bash
devtool [global selection/execution options] COMMAND [command options]
```

A repository normally has `.devtool.toml`. Global defaults may live in Devtool's user config. Selection such as `--target`, `--group`, `--all-targets`, or affected-target modes determines *what* is operated on. Execution settings determine *where/how*. The command determines *what operation* is requested.

## 2. First-use workflow

From a repository root, start with non-mutating discovery:

```bash
devtool status
devtool inspect
devtool doctor
devtool targets
devtool plan validate
```

If no `.devtool.toml` exists, use `devtool init-config` / `devtool init` and then review the generated configuration. `devtool detect` helps identify runner/project characteristics. `devtool config` and the global-config commands inspect effective settings and migration state. Shell completion is exposed through `devtool completion` and `devtool completion-state`.

For a configured repository, a normal edit/validation loop is:

```bash
devtool --target app build
devtool --target app test
devtool --target app lint
devtool --target app validate
```

Use `--dry-run` and planning/inspection commands before unfamiliar destructive or expensive operations.

## 3. Repository, target, group, and affected selection

Devtool can model a monorepo as multiple first-class targets. Prefer an explicit target when a repository has more than one candidate:

```bash
devtool --target android_app validate
devtool --group mobile validate
devtool --all-targets validate-all
devtool affected
devtool plan validate --affected
```

Targets encapsulate roots, runner kind, execution environment, validation tasks, artifact policy, resource policy, and Android-specific behavior. Groups provide reusable target sets. Jobs and workflows compose higher-level operations. Affected-target ownership maps changed paths to the smallest safe target set; graph preflight expands required dependencies and catches invalid or cyclic plans before execution.

See [`CONFIGURATION.md`](CONFIGURATION.md), [`AFFECTED_TARGETS.md`](AFFECTED_TARGETS.md), [`WORKSPACE_GROUPS.md`](WORKSPACE_GROUPS.md), and [`POLYGLOT_TARGETS_AND_WORKFLOWS.md`](POLYGLOT_TARGETS_AND_WORKFLOWS.md).

## 4. Core project lifecycle commands

The core runner operations are `restore`, `build`, `test`, `lint`, `validate`, `validate-all`, `run`, `package`, `release`, and `clean`. Devtool resolves the selected target's runner and converts the generic operation into native commands.

- **restore**: fetch/prepare dependencies or runner prerequisites.
- **build**: compile/build the selected target using its runner and configured optimization policy.
- **test**: run tests, optionally with selectors or explicit native tasks.
- **lint**: run static analysis / linting.
- **validate**: execute the target's validation contract and diagnostics pipeline.
- **validate-all**: validate the configured multi-target scope.
- **run**: launch a runnable target.
- **package**: produce package/distribution outputs.
- **release**: execute release-oriented tasks and release policy.
- **clean**: remove build outputs according to the runner.

`wine-validate` is the Windows/Wine-specific validation path. `pytest` exposes Devtool's Python-suite inspection/profiling helpers. Test selection and native task overrides are described in [`TEST_SELECTION.md`](TEST_SELECTION.md) and runner behavior in [`PROJECT_RUNNERS.md`](PROJECT_RUNNERS.md).

## 5. Project runners and languages

Devtool detects or configures first-class runners for the project kinds implemented by the repository. The runner layer covers Python, Gradle/Android, Go, Rust/Cargo, Java/Maven or JVM layouts, Node/package-manager projects, .NET, CMake/native projects, and custom command/layout cases represented by configuration. Detection is conservative: explicit repository configuration wins over heuristic discovery.

Runner-specific build optimizations are centralized so task deduplication, dependency-aware task planning, caching/configuration-cache policy, parallelism, and artifact discovery are consistent across CLI, overlays, and frontends. Use `inspect`, `detect`, `doctor`, and `plan` when you need to see the resolved runner rather than guessing.

See [`PROJECT_RUNNERS.md`](PROJECT_RUNNERS.md), [`PYTHON_PROJECTS.md`](PYTHON_PROJECTS.md), and [`BUILD_OPTIMIZATION.md`](BUILD_OPTIMIZATION.md).

## 6. Validation modes

### One-shot validation

`devtool validate` performs the configured target validation. Explicit native tasks may be supplied where supported. `--collect-all`/failure-policy controls decide whether independent checks continue after a failure. Resource and execution flags can be overridden per invocation.

### Durable sequential / resumable validation

Long Android or multi-phase validations can use a persistent session. `start-validation` and `resume-validation` are compatibility entry points; the canonical `validate` control surface provides start/continue/run/status/plan/checkpoint/events/report/pause/stop/recover/reset/finalize/abort and plan-change acceptance operations exposed by the parser appendix below.

A durable session stores a revisioned phase graph, prerequisite relationships, attempt history, fingerprints, event log, rollback point, and resource/environment failure classification. Successful phases are reusable only while their relevant inputs still match. A stale frontend mutation is rejected by revision guard rather than silently changing a newer session.

Typical flow:

```bash
devtool --target app validate start
devtool --target app validate status --json
devtool --target app validate continue
devtool --target app validate report
devtool --target app validate finalize
```

Use `validation-menu` / `vm` for the guided terminal cockpit. It can compose recipes, phase scopes, matrix values, resource profiles, test selectors, explicit Gradle tasks, HUD/output modes, and editable commands without creating a second executor.

See [`VALIDATION_MENU.md`](VALIDATION_MENU.md) and [`VALIDATION_EXECUTION_AUDIT.md`](VALIDATION_EXECUTION_AUDIT.md).

## 7. Overlay, snapshot, and bundle workflow

Overlays are self-describing repository-relative change artifacts. Schema-v2 artifacts can include a strict inventory, source hashes for modified paths, final hashes/sizes, atomic apply policy, commit metadata, validation policy, and controlled hooks. Snapshots represent broader repository state. Bundles package multiple artifacts with ordering/dependency metadata.

Key operations include:

```bash
devtool validate-overlay change.tar.gz
devtool apply-overlay change.tar.gz
devtool apply-overlays one.tar.gz two.tar.gz
devtool apply artifact.tar.gz
devtool overlay inspect artifact.tar.gz
devtool overlay preflight artifact.tar.gz
devtool inspect-artifact artifact.tar.gz
devtool bundle ...
```

`--copy` does **not** stage or relocate an input artifact. It copies the selected Devtool run summary or prompt output to the system clipboard. Apply transactions validate artifact paths and source-state expectations before mutation, journal changes, and can roll back on validation failure according to policy. Embedded preflight/validation/finalization hooks have explicit capability boundaries; they are not arbitrary implicit shell execution.

For exact manifest fields, bundle semantics, hooks, history, and deferred validation see [`ARTIFACTS.md`](ARTIFACTS.md) and [`OVERLAY_VALIDATION.md`](OVERLAY_VALIDATION.md).

## 8. Commits, history, rollback, and handoff

`commit` records repository changes through Devtool's repository-aware flow. Operation history records artifact and validation transactions. `history` inspects prior actions and `rollback` restores eligible transactions. `handoff` creates/prints transfer context for moving work between operators or sessions. `ai-prompt` produces agent-oriented prompts from resolved repository state.

Use Devtool's transaction/history commands instead of manually deleting backup directories. `cleanup` archives/prunes historical backup state under explicit retention controls.

## 9. Artifact registry and output discovery

Build/package/release operations register produced artifacts so callers do not need to scrape console text. `artifact` commands inspect and maintain that registry, while `inspect-artifact` reads overlay/bundle manifest metadata. Artifact discovery can be configured with explicit files, directories, patterns, recursive rules, and runner defaults.

`--machine`, JSON output controls, machine-file output, and structured logs are preferred for integrations. Human summaries remain useful at the terminal, but integrations should consume stable structured fields.

## 10. Execution environments

A target can execute on the host or through supported isolated/remote environments, including Termux native execution, PRoot/chroot policy, and SSH-oriented execution when configured. Path translation and environment allowlists are part of the execution contract; do not assume a repository host path is valid inside a chroot.

Important operator tools:

```bash
devtool doctor
devtool termux ...
devtool bootstrap ...
devtool detect
devtool compatibility
```

See [`EXECUTION_ENVIRONMENTS.md`](EXECUTION_ENVIRONMENTS.md), [`ANDROID_NDK_HOST_FALLBACK.md`](ANDROID_NDK_HOST_FALLBACK.md), and [`TERMUX_PROOT_PROMISE_AUDIT.md`](TERMUX_PROOT_PROMISE_AUDIT.md).

## 11. Android toolchains and build providers

Android handling is centralized rather than scattered across individual runners. Devtool resolves Java, Gradle, Android SDK/platform/build-tools, AAPT2, apksigner, zipalign, NDK, CMake, and ADB providers with provenance and compatibility diagnostics. Termux-specific provider paths are supported, including host/build-tool substitutions where ordinary desktop Android tooling is incompatible.

Use:

```bash
devtool --target app android capabilities
devtool --target app android doctor
devtool --target app android devices
devtool --target app android devices doctor --provider termux-adb
```

Resource flags cover worker count, CPU budget, Gradle heap/metaspace, memory guard, parallel/build-cache/daemon behavior, and resource profiles. Task planning deduplicates Gradle tasks, recognizes connected-device tasks, and avoids an ADB probe when no requested task actually needs a device.

Gradle task execution telemetry distinguishes a task execution from Gradle's repeated `--console=plain` context headers during parallel output. In the default non-verbose human stream, a repeated bare `> Task :path` header for an already-started task is collapsed as resumed output; outcome-bearing headers such as `FAILED`, `UP-TO-DATE`, `FROM-CACHE`, `SKIPPED`, and `NO-SOURCE` remain visible. The raw captured/evidence stream is preserved, and `--verbose` deliberately shows the uncollapsed child stream. Progress/HUD state records requested-task duplicates, unique task executions/completions, per-task resume-header counts, terminal outcomes, and structured duplicate-start anomalies. The terminal summary line reports `requested`, `duplicates`, `executions`, `completed`, `resumed_headers`, and `duplicate_execution_starts`, so repeated headers are no longer evidence of repeated execution by themselves.

See [`ANDROID_PROJECTS.md`](ANDROID_PROJECTS.md) and [`ANDROID_OPERATIONS.md`](ANDROID_OPERATIONS.md).

## 12. Android devices, ADB, instrumentation, and app lifecycle

The Android device manager provides deterministic device selection and provider diagnostics. Providers include normal SDK/path ADB and Termux-oriented transports such as `termux-adb` when installed/configured. Device state distinguishes missing devices, authorization problems, offline devices, and transport/provider failures.

Device operations include inventory, install/replace/uninstall, launch/stop/restart, clear-data, permission grant/revoke, shell, push/pull, logcat, screenshot, and lifecycle smoke scenarios. Instrumentation can be disabled, opportunistic (`when-device-present`), or required. Selectors can target classes/methods/annotations/sizes and pass instrumentation runner arguments.

Connected tests and instrumentation are not silently treated like host unit tests. When the policy requires a device, missing/unauthorized device state is surfaced explicitly.

## 13. Android signing and release

`android signing` manages release identities without storing secret password values in ordinary project config or resumable-session state. Subcommands create/list/inspect/verify/compare/import/export certificate material. Release-producing Gradle tasks can receive signing injection at the final native-process boundary; debug/lint/compile/unit-test tasks remain signing-independent.

`release` and Android release policy can combine signing, packaging, APK verification, optional previous-release comparison, install/upgrade/smoke gates, and evidence capture. Use `android signing inspect` and `verify` before a release rather than learning about a bad keystore after an expensive build.

See the AOPS03/AOPS04 sections of [`ANDROID_OPERATIONS.md`](ANDROID_OPERATIONS.md) and [`FINAL_RELEASE_SEAL.md`](FINAL_RELEASE_SEAL.md).

## 14. Workflows, jobs, scheduler, and preflight

Repository workflows are executable DAGs. They can express target steps, dependencies, independent parallel work, failure boundaries, artifacts, and resource scheduling. `preflight` checks graph validity and prerequisites without doing the full work. `plan` exposes the resolved execution graph. The safe scheduler enforces resource constraints and dependency order rather than launching every possible task at once.

Use workflow/job commands when a release or validation spans multiple targets. Use direct core commands when operating on one target is clearer.

See [`WORKFLOW_PREFLIGHT.md`](WORKFLOW_PREFLIGHT.md) and [`WORKFLOW_SCHEDULING_AND_PLANNING.md`](WORKFLOW_SCHEDULING_AND_PLANNING.md).

## 15. Terminal UI, Desktop HUD, Android HUD, and Control Center

Devtool has multiple frontends over the same underlying execution/control state:

- `tui` provides a responsive terminal dashboard.
- `validation-menu` / `menu validation` provides the validation cockpit.
- Desktop GTK/HUD integration displays one live run and control state.
- The Android Devtool Live HUD / Validation Workbench consumes the stable validation/HUD bridge rather than reimplementing validation.
- `control-center` and IPC commands expose the native control surface where available.

UI preferences/tokens, accessibility rules, icon/font behavior, layout contracts, and regression fixtures are documented separately. Frontends must not invent their own run/session state machine.

See [`UI_INTEGRATION.md`](UI_INTEGRATION.md), [`UI_UX_CONTRACT.md`](UI_UX_CONTRACT.md), [`UI_PREFERENCES_AND_TOKENS.md`](UI_PREFERENCES_AND_TOKENS.md), [`UI_ACCESSIBILITY.md`](UI_ACCESSIBILITY.md), [`TUI_NERD_FONT_ICONS.md`](TUI_NERD_FONT_ICONS.md), and [`UI_REGRESSION.md`](UI_REGRESSION.md).

## 16. Configuration and profiles

`.devtool.toml` is the repository contract: targets, groups, runner types, roots, execution environments, validation tasks/phases, artifact discovery, workflows/jobs, Android policy, and resource policy can be declared there. Global config is for user/machine defaults that should not be committed to each repository. UI preferences and resource profiles can also be shared globally and overridden per invocation.

Inspect effective configuration before editing it:

```bash
devtool config ...
devtool status
devtool inspect
devtool completion-state ...
```

See [`CONFIGURATION.md`](CONFIGURATION.md) for the field-level examples.

## 17. Diagnostics, logs, and warning scopes

Devtool parses native runner output into structured diagnostics while keeping the original log. Errors are always failures. Warnings have **two scopes**:

1. **Target warnings** are emitted by the project/toolchain for target code or validation semantics (for example Kotlin/Java/compiler/lint/test/deprecation warnings). They count toward artifact `maximum_warnings` and can make an expected `pass` outcome become `warning`.
2. **Infrastructure warnings** describe the Devtool host/execution environment (for example low free storage, ADB probe notices, Gradle host/native-platform compatibility notices, or metaspace pressure notices). They remain visible in diagnostics/logs and can inform retry/resource decisions, but they do **not** consume the target warning budget.

This separation prevents a clean target from failing an artifact contract merely because the host reports low storage or Gradle says a native host integration is unavailable. An actual target warning still fails a zero-warning contract.

`logs` provides structured-log discovery/retention operations. Use the run ID and emitted log path when reporting failures. See [`PYTHON_VALIDATION_DIAGNOSTICS.md`](PYTHON_VALIDATION_DIAGNOSTICS.md) and [`WARNING_SCOPE_ACCOUNTING_REPORT.md`](../WARNING_SCOPE_ACCOUNTING_REPORT.md).

## 18. Bootstrap, doctor, cleanup, compatibility, and release-seal tools

- `bootstrap` provisions supported dependencies/tooling under explicit policy.
- `doctor` diagnoses repository, environment, and runner/toolchain problems.
- `detect` reports detected project/platform identity.
- `cleanup` manages historical backup/storage retention.
- `compatibility` audits legacy interfaces and migration paths.
- `final-seal` runs repository-wide release-seal auditing.
- `tag` and `release-notes` support release bookkeeping.
- `release-notes`/qualification/seal documents describe the release evidence model.

See [`BACKWARD_COMPATIBILITY.md`](BACKWARD_COMPATIBILITY.md), [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md), [`RELEASE_QUALIFICATION.md`](RELEASE_QUALIFICATION.md), and [`FINAL_RELEASE_SEAL.md`](FINAL_RELEASE_SEAL.md).

## 19. Machine output, automation, IPC, and output verification

Use JSON/machine output whenever another program consumes Devtool. Durable validation control uses a versioned response envelope and revision guards. IPC/HUD consumers should poll/subscribe to canonical state rather than scrape terminal rendering. The Android and desktop frontends use the same principle.

`--output-debug` (or `DEVTOOL_OUTPUT_DEBUG=1`) turns on one correlated debug stream for Devtool's final human output. It saves a plain rendered equivalent of the terminal summary with byte count and SHA-256 under `evidence/<run-id>/output-debug/`. If `--copy` or an interactive copy action also runs, Devtool saves the exact **sanitized** clipboard payload passed to the backend, its backend/result, size and SHA-256, and records whether those bytes match the terminal-summary payload. `--no-output-debug` explicitly overrides the environment variable. Debug evidence is private (`0700` directory / `0600` files), correlated with the run log, and never bypasses Devtool secret redaction. Output-debug prints only correlation metadata (IDs, size, SHA-256, saved path, and match result); it never echoes the captured terminal or clipboard payload body back to the terminal.

For scripted mutation, pair machine-readable inspection with explicit target selection, `--dry-run` where supported, and transaction-safe Devtool operations. Avoid relying on colors, line positions, or incidental warning text.

## 20. Practical recipes

### Apply an overlay without immediately running its full validation

```bash
devtool --copy --target app apply-overlay /sdcard/Download/change.tar.gz --no-validate
```

### Run only explicit Gradle verification tasks

```bash
devtool --target app validate --task :app:testDebugUnitTest --task :app:lintDebug
```

### Diagnose Android provider/device state

```bash
devtool --target app android capabilities
devtool --target app android devices doctor --provider termux-adb --format json
```

### Inspect a long validation session

```bash
devtool --target app validate status --json
devtool --target app validate plan --json
devtool --target app validate events --json
```

### Preflight an artifact before applying it

```bash
devtool overlay inspect change.tar.gz
devtool overlay preflight change.tar.gz
devtool validate-overlay change.tar.gz
```

## 21. Canonical deep-dive documentation map

This guide intentionally explains the whole product surface, while these focused documents contain implementation/policy depth:

- [`CONFIGURATION.md`](CONFIGURATION.md): repository/global config, targets, workflows, Android policy.
- [`PROJECT_RUNNERS.md`](PROJECT_RUNNERS.md): runner architecture and native command mapping.
- [`BUILD_OPTIMIZATION.md`](BUILD_OPTIMIZATION.md): build/task optimization policy.
- [`TEST_SELECTION.md`](TEST_SELECTION.md): selectors and test execution.
- [`ARTIFACTS.md`](ARTIFACTS.md): overlay schema, hooks, bundles, history.
- [`OVERLAY_VALIDATION.md`](OVERLAY_VALIDATION.md): overlay validation lifecycle.
- [`EXECUTION_ENVIRONMENTS.md`](EXECUTION_ENVIRONMENTS.md): host/chroot/remote execution.
- [`ANDROID_PROJECTS.md`](ANDROID_PROJECTS.md): Android builds/toolchains.
- [`ANDROID_OPERATIONS.md`](ANDROID_OPERATIONS.md): devices, ADB, signing, instrumentation, release.
- [`POLYGLOT_TARGETS_AND_WORKFLOWS.md`](POLYGLOT_TARGETS_AND_WORKFLOWS.md): monorepo target/workflow model.
- [`WORKFLOW_PREFLIGHT.md`](WORKFLOW_PREFLIGHT.md) and [`WORKFLOW_SCHEDULING_AND_PLANNING.md`](WORKFLOW_SCHEDULING_AND_PLANNING.md): DAG planning/execution.
- [`VALIDATION_MENU.md`](VALIDATION_MENU.md): terminal validation cockpit.
- [`PYTHON_VALIDATION_DIAGNOSTICS.md`](PYTHON_VALIDATION_DIAGNOSTICS.md): diagnostic normalization.
- UI documents listed in section 15: frontend contracts and accessibility.

The appendix below is the authoritative inventory of the public parser surface in this revision.

## Complete CLI reference (generated from the parser)

This appendix is generated from `devtool.cli.build_parser()`. It is deliberately exhaustive: every canonical command/subcommand, alias, positional argument, and non-hidden option exposed by this source tree appears here. Hidden compatibility switches remain documented in the compatibility/migration material rather than being promoted as normal usage.

### Global invocation options

These options are accepted at the root `devtool` invocation and shape selection, execution, output, UI/HUD integration, and Android/Termux toolchains.

| Syntax | Meaning |
| --- | --- |
| `-r, --repo REPO` | Repository root |
| `-c, --config CONFIG` | Config file path |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group from [workspace.groups]; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--affected` | Select workspace targets affected by current Git changes; may be combined with --group/--all-targets as a candidate filter |
| `--affected-base AFFECTED_BASE` | Git base revision for --affected selection (default: staged/unstaged/untracked working tree) |
| `--affected-head AFFECTED_HEAD` | Git head revision for --affected selection; with --affected-base uses base...head |
| `--variant VARIANT` | Build/test variant within the selected target |
| `--solution SOLUTION` | Exact solution path for restore/build/test |
| `--project PROJECT` | Exact project path for run/validation |
| `--runner, --project-type PROJECT_TYPE` | Override the project runner for this run; --project-type remains a compatibility alias Choices: auto, dotnet, android, gradle, python, node, rust, go, maven, cmake, meson, make, command, unknown |
| `-v, --verbose` | Stream full command output |
| `-q, --quiet` | Suppress normal human output; errors still print |
| `--trace` | Enable architecture event tracing and full exception tracebacks |
| `-y, --yes` | Assume yes for confirmations |
| `-f, --force` | Force dangerous actions |
| `--allow-pending-validation` | Allow a new operation while another artifact remains pending validation; rollback state is preserved |
| `--dry-run` | Print commands without running them |
| `--no-build` | For test: pass --no-build to dotnet test |
| `-k, --continue-on-error` | Continue with the next independent step after an error |
| `--fail-fast` | Stop at the first failed step |
| `--performance` | Write a JSON performance report for this run |
| `--performance-file PERFORMANCE_FILE` | Performance report path (default: devtool state directory) |
| `--stdout-payload STDOUT_PAYLOAD` | Select stdout content: normal UI, concise summary, or raw command output Choices: auto, summary, raw |
| `--machine-file MACHINE_FILE` | Write the machine envelope to this file |
| `-p, --copy` | Copy the selected run summary or prompt output to the clipboard |
| `--no-clipboard` | Disable clipboard prompts and copy behavior |
| `--copy-via COPY_VIA` | Prefer a clipboard backend family Choices: auto, socket, fifo, termux, x11, wayland |
| `--output-debug` | Persist the rendered terminal summary and any --copy clipboard payload for debugging without echoing captured payload bodies (also DEVTOOL_OUTPUT_DEBUG=1) |
| `--no-output-debug` | Disable terminal/clipboard output debugging even when DEVTOOL_OUTPUT_DEBUG is enabled |
| `--theme THEME` | Summary theme Choices: auto, plain |
| `--log-dir LOG_DIR` | Override the directory used for command logs (also DEVTOOL_LOG_DIR) |
| `--ui-profile UI_PROFILE` | UI profile: auto, minimal, standard, developer, debug, or a configured [ui.profiles.*] name |
| `--ui-theme UI_THEME` | Override the shared UI theme preference for this invocation Choices: auto, light, dark |
| `--ui-density UI_DENSITY` | Override shared information density for this invocation Choices: dense, compact, comfortable, detailed |
| `--ui-icons UI_ICONS` | Override semantic icon rendering mode Choices: auto, nerd, unicode, ascii, native |
| `--ui-motion UI_MOTION` | Override shared motion preference Choices: auto, full, reduced |
| `--ui-verbosity UI_VERBOSITY` | Override shared information verbosity Choices: compact, normal, detailed, debug |
| `--auto-hud, --hud-auto` | Auto-start or reuse the selected HUD while progress is emitted (default) |
| `--no-hud` | Do not publish live HUD progress for this command |
| `--hud-mode HUD_MODE` | HUD backend: desktop, android-bridge, desktop-window (GTK4), web, studio, or terminal |
| `--hud-overlay` | On Android, also show the run in the user-enabled floating overlay HUD |
| `--hud-no-overlay` | On Android, keep the update in the app without opening the floating overlay |
| `--host` | Force command phases to run in devtool's current host environment |
| `--chroot CHROOT` | Run command phases inside a named chroot via chrootctl exec |
| `--ssh, --ssh-host HOST` | Use HOST as the SSH execution target (whole devtool or command-phase mode) |
| `--ssh-profile NAME` | Use a named SSH worker from [ssh.hosts.NAME]; explicit SSH flags override profile fields |
| `--proot-distro DISTRO` | Run command phases inside a PRoot-Distro container; guest does not need devtool |
| `--execution-environment EXECUTION_ENVIRONMENT` | Execution backend: auto, native-termux, chroot, proot-distro, desktop-linux, ssh, android-device, or external-device Choices: auto, native-termux, chroot, desktop-linux, desktop-macos, desktop-windows, host, android-device, external-device, ssh, proot-distro |
| `--ssh-port PORT` | SSH port (default: 22 or [ssh].port) |
| `--ssh-user USER` | SSH login user |
| `--ssh-identity PATH` | SSH private-key identity file |
| `--ssh-session NAME` | tmux session name; stable names can be reattached |
| `--ssh-cwd PATH` | Working directory on the SSH host; omitted means the remote login default |
| `--ssh-tool COMMAND` | Remote devtool executable used by --ssh-mode devtool (default: devtool) |
| `--ssh-mode SSH_MODE` | SSH mode: delegate the whole devtool run, or keep devtool local and run command phases remotely Choices: devtool, commands |
| `--ssh-stage-dir PATH` | Remote staging root for copied overlays/helper files |
| `--ssh-no-artifact-copy` | Disable automatic SSH/SCP artifact transfer |
| `--ssh-detached` | Start/reuse the remote tmux session without attaching (whole-run devtool mode) |
| `--proot-user USER` | PRoot-Distro guest user (default: root or [proot].user) |
| `--proot-cwd PATH` | Initial absolute working directory inside the PRoot guest |
| `--proot-repo-mount PATH` | Absolute guest mount point for the current repository |
| `--proot-bind HOST:GUEST` | Additional PRoot bind; repeatable, guest destination must be absolute |
| `--proot-env NAME=VALUE` | Explicit environment variable passed to the PRoot guest; repeatable |
| `--proot-no-auto-bind-repo` | Do not automatically bind the repository into the PRoot guest |
| `--proot-shared-home` | Ask PRoot-Distro to share the host home with the guest |
| `--proot-shared-tmp` | Ask PRoot-Distro to share Termux tmp with the guest |
| `--proot-isolated` | Use PRoot-Distro isolated mode (Termux hosts only) |
| `--proot-minimal` | Use PRoot-Distro minimal mode (Termux hosts only) |
| `--proot-stage-dir PATH` | Host staging directory bound into the PRoot guest |
| `--termux-resource-profile TERMUX_RESOURCE_PROFILE` | Termux resource defaults for workers/JVM/native concurrency; off/none bypasses automatic Termux resource defaults Choices: auto, automatic, conservative, balanced, performance, off, none |
| `--no-termux-resource-profile` | Bypass automatic Termux resource defaults for this invocation |
| `--termux-wake-lock` | Acquire a Termux wake lock while command phases run |
| `--no-termux-wake-lock` | Disable Termux wake-lock integration for this run |
| `--shared-storage-policy SHARED_STORAGE_POLICY` | Policy for repositories on Android shared storage Choices: allow, warn, deny |
| `--allow-shared-storage-build` | Allow a build from Android shared storage for this run |
| `--artifact-export DIR` | Copy verified artifacts to DIR after successful local verification |
| `--no-auto-chroot` | On Termux, do not auto-route command phases through chrootctl |
| `--no-bootstrap-downloads` | Never download or install missing toolchain components |
| `--bootstrap-downloads BOOTSTRAP_DOWNLOADS` | Override receipt/config toolchain provisioning policy for this run Choices: auto, missing, force, never |
| `--deny-network` | Deny Devtool-managed network downloads for this run |
| `--offline` | Alias for --no-bootstrap-downloads --deny-network and Gradle offline mode |
| `--toolchain-profile TOOLCHAIN_PROFILE` | Chroot/toolchain profile name from global or repo config |
| `--android-sdk-root ANDROID_SDK_ROOT` | Android SDK root for Android/Gradle projects |
| `--android-sdk-provider ANDROID_SDK_PROVIDER` | Android SDK provider: auto, TermuxVoid, HomuHomu ARM64 Android SDK, or managed Google SDK Choices: auto, homuhomu, managed, termuxvoid |
| `--gradle-command GRADLE_COMMAND` | Gradle or gradlew executable for Android/Gradle projects |
| `--gradle-provider GRADLE_PROVIDER` | Gradle provider for Android/Gradle projects: auto, wrapper, termux, system, or download Choices: auto, download, system, termux, wrapper |
| `--java-home JAVA_HOME` | JDK home for Android/Gradle projects |
| `--java-provider JAVA_PROVIDER` | Java provider for Android/Gradle projects: auto, termux, or system Choices: auto, system, termux |
| `--gradle-user-home GRADLE_USER_HOME` | GRADLE_USER_HOME for Android/Gradle projects |
| `--native-provider NATIVE_PROVIDER` | Native provider for Android/native projects: auto, termux-sysroot, ndk, or system Choices: auto, ndk, system, termux-sysroot |
| `--native-cache NATIVE_CACHE` | Native compiler cache provider: auto, ccache, or off Choices: auto, ccache, off |
| `--android-build-tools-provider ANDROID_BUILD_TOOLS_PROVIDER` | Android native build-tools provider: auto, termux, sdk, commit451, or none Choices: auto, commit451, none, sdk, termux |
| `--aapt2-provider AAPT2_PROVIDER` | AAPT2 provider for Android builds Choices: auto, termux, sdk, revanced, none |
| `--ui JOB_UI` | Job display: compact Textual (default), full Textual or plain stream Choices: compact, full, plain |

### `devtool gradle`

Inspect and control canonical Gradle execution

**Subcommands:** `takeover`, `real`

### `devtool gradle takeover`

Manage optional Gradle takeover/interception

**Subcommands:** `status`, `doctor`, `explain`, `disable`, `enable`, `intercept`

### `devtool gradle takeover status`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit structured JSON |

### `devtool gradle takeover doctor`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit structured JSON |

### `devtool gradle takeover explain`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit structured JSON |

### `devtool gradle takeover disable`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit structured JSON |

### `devtool gradle takeover enable`

| Argument / option | Meaning |
| --- | --- |
| `--mode MODE` | Choices: observe, manage, strict |
| `--scope SCOPE` | Choices: devtool, shell, both |
| `--json` | Emit structured JSON |

### `devtool gradle takeover intercept`

==SUPPRESS==

| Argument / option | Meaning |
| --- | --- |
| `--origin ORIGIN` | Choices: shell, devtool, wrapper, invoke, workflow, validation, testspec, artifact, android, ssh, federation |
| `--intent INTENT` | Choices: work, probe, control |
| `--requested-launcher REQUESTED_LAUNCHER` | Accepted by this command. |
| `GRADLE_ARGS` | Accepted by this command. |

### `devtool gradle real`

Run the authoritative real Gradle launcher, bypassing takeover planning for diagnostics

| Argument / option | Meaning |
| --- | --- |
| `--launcher LAUNCHER` | auto, wrapper, system, gradle, or an explicit launcher path |
| `GRADLE_ARGS` | Accepted by this command. |

### `devtool restore`

**Aliases:** `devtool rs`

| Argument / option | Meaning |
| --- | --- |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--affected` | Select dependency-aware targets affected by Git changes |
| `--affected-base AFFECTED_BASE` | Git base revision for affected selection |
| `--affected-head AFFECTED_HEAD` | Git head revision for affected selection |
| `--variant VARIANT` | Variant within the selected workspace target(s) |
| `--workflow-parallel` | Run independent workflow DAG nodes concurrently only when dependency, read/write-scope, and resource checks prove it safe |
| `--no-workflow-parallel` | Force workflow DAG nodes to execute sequentially |
| `--workflow-max-workers COUNT` | Maximum concurrent safe workflow nodes; 0 uses workspace policy/automatic default |
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
| `--max-workers COUNT` | Override validation worker count for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--cpu-limit COUNT` | Override validation CPU limit for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--gradle-heap-mb MIB` | Override the Gradle validation heap for this invocation; explicit CLI value wins over Android-safe/project phase defaults |
| `--gradle-metaspace-mb, --metaspace-guard-mb MIB` | Override the Gradle validation max metaspace; 0 disables Devtool's explicit MaxMetaspaceSize cap |
| `--no-metaspace-guard` | Do not set an explicit Gradle MaxMetaspaceSize for this validation invocation |
| `--memory-guard-mb MIB` | Override the validation free-memory guard for this invocation; 0 disables it and explicit CLI value wins over phase defaults |
| `--low-priority` | Run validation commands at low scheduler priority |
| `--normal-priority` | Do not lower validation process priority |
| `--parallel` | Enable Gradle parallel execution for validation |
| `--no-parallel` | Disable Gradle parallel execution for validation |
| `--build-cache` | Enable the Gradle build cache for validation |
| `--no-build-cache` | Disable the Gradle build cache for validation |
| `--daemon` | Allow the Gradle daemon during validation |
| `--no-daemon` | Disable the Gradle daemon during validation |

### `devtool build`

**Aliases:** `devtool b`

| Argument / option | Meaning |
| --- | --- |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--affected` | Select dependency-aware targets affected by Git changes |
| `--affected-base AFFECTED_BASE` | Git base revision for affected selection |
| `--affected-head AFFECTED_HEAD` | Git head revision for affected selection |
| `--variant VARIANT` | Variant within the selected workspace target(s) |
| `--workflow-parallel` | Run independent workflow DAG nodes concurrently only when dependency, read/write-scope, and resource checks prove it safe |
| `--no-workflow-parallel` | Force workflow DAG nodes to execute sequentially |
| `--workflow-max-workers COUNT` | Maximum concurrent safe workflow nodes; 0 uses workspace policy/automatic default |
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
| `--max-workers COUNT` | Override validation worker count for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--cpu-limit COUNT` | Override validation CPU limit for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--gradle-heap-mb MIB` | Override the Gradle validation heap for this invocation; explicit CLI value wins over Android-safe/project phase defaults |
| `--gradle-metaspace-mb, --metaspace-guard-mb MIB` | Override the Gradle validation max metaspace; 0 disables Devtool's explicit MaxMetaspaceSize cap |
| `--no-metaspace-guard` | Do not set an explicit Gradle MaxMetaspaceSize for this validation invocation |
| `--memory-guard-mb MIB` | Override the validation free-memory guard for this invocation; 0 disables it and explicit CLI value wins over phase defaults |
| `--low-priority` | Run validation commands at low scheduler priority |
| `--normal-priority` | Do not lower validation process priority |
| `--parallel` | Enable Gradle parallel execution for validation |
| `--no-parallel` | Disable Gradle parallel execution for validation |
| `--build-cache` | Enable the Gradle build cache for validation |
| `--no-build-cache` | Disable the Gradle build cache for validation |
| `--daemon` | Allow the Gradle daemon during validation |
| `--no-daemon` | Disable the Gradle daemon during validation |
| `-o, --artifact-output-dir, --output-dir ARTIFACT_OUTPUT_DIR` | Copy produced artifacts here (default: .devtool/build-artifacts) |
| `-n, --artifact-output-name, --output-name ARTIFACT_OUTPUT_NAME` | Base filename for copied artifacts |
| `--artifact-collision ARTIFACT_COLLISION` | Managed artifact collision policy (default: unique) Choices: unique, replace, fail |
| `--verify-artifacts` | Verify produced artifacts before registration/export |
| `--no-verify-artifacts` | Collect artifacts without content verification |

### `devtool test`

**Aliases:** `devtool t`

| Argument / option | Meaning |
| --- | --- |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--affected` | Select dependency-aware targets affected by Git changes |
| `--affected-base AFFECTED_BASE` | Git base revision for affected selection |
| `--affected-head AFFECTED_HEAD` | Git head revision for affected selection |
| `--variant VARIANT` | Variant within the selected workspace target(s) |
| `--workflow-parallel` | Run independent workflow DAG nodes concurrently only when dependency, read/write-scope, and resource checks prove it safe |
| `--no-workflow-parallel` | Force workflow DAG nodes to execute sequentially |
| `--workflow-max-workers COUNT` | Maximum concurrent safe workflow nodes; 0 uses workspace policy/automatic default |
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
| `--max-workers COUNT` | Override validation worker count for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--cpu-limit COUNT` | Override validation CPU limit for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--gradle-heap-mb MIB` | Override the Gradle validation heap for this invocation; explicit CLI value wins over Android-safe/project phase defaults |
| `--gradle-metaspace-mb, --metaspace-guard-mb MIB` | Override the Gradle validation max metaspace; 0 disables Devtool's explicit MaxMetaspaceSize cap |
| `--no-metaspace-guard` | Do not set an explicit Gradle MaxMetaspaceSize for this validation invocation |
| `--memory-guard-mb MIB` | Override the validation free-memory guard for this invocation; 0 disables it and explicit CLI value wins over phase defaults |
| `--low-priority` | Run validation commands at low scheduler priority |
| `--normal-priority` | Do not lower validation process priority |
| `--parallel` | Enable Gradle parallel execution for validation |
| `--no-parallel` | Disable Gradle parallel execution for validation |
| `--build-cache` | Enable the Gradle build cache for validation |
| `--no-build-cache` | Disable the Gradle build cache for validation |
| `--daemon` | Allow the Gradle daemon during validation |
| `--no-daemon` | Disable the Gradle daemon during validation |
| `TEST_NAMES...` | First-class [[test]] IDs from .devtool.toml |
| `--all` | Run all first-class [[test]] declarations |
| `--no-build` | Pass --no-build to dotnet test |
| `--list-tests, --list` | Discover and list tests without executing them; pytest uses native --collect-only discovery |
| `--pytest-test NODEID` | Run exactly this pytest file/node ID; repeatable. Suppresses default/affected pytest targets unless --pytest-include-defaults is set. |
| `--pytest-marker EXPR` | Add a pytest -m marker expression; repeatable expressions are combined with AND. |
| `--pytest-k EXPR` | Apply a pytest -k expression in addition to explicit pytest tests/selectors. |
| `--pytest-arg ARG` | Pass one structured argument directly to pytest; repeatable (for example --pytest-arg=--maxfail=1). |
| `--pytest-include-defaults` | Keep configured/affected default pytest targets in addition to explicit --pytest-test selectors. |
| `--profile PROFILE` | Saved test profile from .devtool.toml |
| `--select SELECT` | Select a type, suite, tag, class, or name; repeatable |
| `--id TEST_IDS` | Select an exact framework test ID; repeatable |
| `--tag TAG` | Include tests with this tag/trait; repeatable |
| `--exclude-tag EXCLUDE_TAG` | Exclude tests with this tag/trait; repeatable |
| `--path TEST_PATHS` | Select tests below a source path; repeatable |
| `--class, --tests TEST_CLASSES` | Select a test class; repeatable |
| `--method TEST_METHODS` | Select a test method; repeatable |
| `--filter TEST_FILTER` | Framework-native test filter |
| `--task TEST_TASK` | Gradle test task override |
| `--failed` | Rerun tests that failed in the previous structured run |
| `--changed` | Select tests related to changed files |
| `--changed-from CHANGED_FROM` | Git base for --changed |
| `--instrumented` | Run Android instrumentation tests on a device |

### `devtool lint`

| Argument / option | Meaning |
| --- | --- |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--affected` | Select dependency-aware targets affected by Git changes |
| `--affected-base AFFECTED_BASE` | Git base revision for affected selection |
| `--affected-head AFFECTED_HEAD` | Git head revision for affected selection |
| `--variant VARIANT` | Variant within the selected workspace target(s) |
| `--workflow-parallel` | Run independent workflow DAG nodes concurrently only when dependency, read/write-scope, and resource checks prove it safe |
| `--no-workflow-parallel` | Force workflow DAG nodes to execute sequentially |
| `--workflow-max-workers COUNT` | Maximum concurrent safe workflow nodes; 0 uses workspace policy/automatic default |
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
| `--max-workers COUNT` | Override validation worker count for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--cpu-limit COUNT` | Override validation CPU limit for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--gradle-heap-mb MIB` | Override the Gradle validation heap for this invocation; explicit CLI value wins over Android-safe/project phase defaults |
| `--gradle-metaspace-mb, --metaspace-guard-mb MIB` | Override the Gradle validation max metaspace; 0 disables Devtool's explicit MaxMetaspaceSize cap |
| `--no-metaspace-guard` | Do not set an explicit Gradle MaxMetaspaceSize for this validation invocation |
| `--memory-guard-mb MIB` | Override the validation free-memory guard for this invocation; 0 disables it and explicit CLI value wins over phase defaults |
| `--low-priority` | Run validation commands at low scheduler priority |
| `--normal-priority` | Do not lower validation process priority |
| `--parallel` | Enable Gradle parallel execution for validation |
| `--no-parallel` | Disable Gradle parallel execution for validation |
| `--build-cache` | Enable the Gradle build cache for validation |
| `--no-build-cache` | Disable the Gradle build cache for validation |
| `--daemon` | Allow the Gradle daemon during validation |
| `--no-daemon` | Disable the Gradle daemon during validation |
| `--format, --fix` | Autofix Python formatter output during lint/validation; default is check-only |

### `devtool validate`

**Aliases:** `devtool v`

| Argument / option | Meaning |
| --- | --- |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--affected` | Select dependency-aware targets affected by Git changes |
| `--affected-base AFFECTED_BASE` | Git base revision for affected selection |
| `--affected-head AFFECTED_HEAD` | Git head revision for affected selection |
| `--variant VARIANT` | Variant within the selected workspace target(s) |
| `--workflow-parallel` | Run independent workflow DAG nodes concurrently only when dependency, read/write-scope, and resource checks prove it safe |
| `--no-workflow-parallel` | Force workflow DAG nodes to execute sequentially |
| `--workflow-max-workers COUNT` | Maximum concurrent safe workflow nodes; 0 uses workspace policy/automatic default |
| `--format, --fix` | Autofix Python formatter output during lint/validation; default is check-only |
| `--skip-restore` | Skip restore during validation |
| `--skip-build` | Skip build during validation |
| `--skip-test` | Skip test during validation |
| `--test DECLARED_TESTS` | Run a first-class [[test]] declaration during validation; repeatable |
| `[VALIDATE_SCOPE]` | Optional artifact validation scope: overlay or overlays |
| `[VALIDATE_ARTIFACT]` | Artifact archive to validate when scope is overlay/overlays |
| `--fail-on-conflict` | For overlay bundles, fail if multiple nested overlays write the same file |
| `--ignore-checksum` | Skip SHA-256 checksum validation while retaining all other artifact manifest checks |
| `--watch` | Rerun validation when files change |
| `--watch-interval WATCH_INTERVAL` | Seconds between watch polls |
| `--matrix MATRIX` | Comma-separated runtime IDs, target names, or variants to validate |
| `--matrix-mode MATRIX_MODE` | Choices: rid, target, variant |
| `--test-profile TEST_PROFILE` | Saved test profile used by validation |
| `--pytest-test NODEID` | Run exactly this pytest file/node ID; repeatable. Suppresses default/affected pytest targets unless --pytest-include-defaults is set. |
| `--pytest-marker EXPR` | Add a pytest -m marker expression; repeatable expressions are combined with AND. |
| `--pytest-k EXPR` | Apply a pytest -k expression in addition to explicit pytest tests/selectors. |
| `--pytest-arg ARG` | Pass one structured argument directly to pytest; repeatable (for example --pytest-arg=--maxfail=1). |
| `--pytest-include-defaults` | Keep configured/affected default pytest targets in addition to explicit --pytest-test selectors. |
| `--select SELECT` | Select tests by type, suite, tag, class, or name during validation; repeatable |
| `--id TEST_IDS` | Select an exact framework test ID during validation; repeatable |
| `--tag TAG` | Include tests with this tag/trait/annotation during validation; repeatable |
| `--exclude-tag EXCLUDE_TAG` | Exclude tests with this tag/trait/annotation during validation; repeatable |
| `--path TEST_PATHS` | Select tests below a source path or package during validation; repeatable |
| `--class, --tests TEST_CLASSES` | Select a test class during validation; repeatable |
| `--method TEST_METHODS` | Select a test method during validation; repeatable |
| `--filter TEST_FILTER` | Framework-native test filter used during validation |
| `--task, --test-task TEST_TASK` | Exclusive native validation unit list; repeatable. Android/Gradle values are Gradle tasks; Python/Node/Rust/etc. values are supported validation actions such as test, lint, build, package-check, or Python test targets. |
| `--failed` | Rerun tests that failed in the previous structured run during validation |
| `--changed` | Select tests related to changed files during validation |
| `--changed-from CHANGED_FROM` | Git base for --changed during validation |
| `--instrumented` | Run Android instrumentation tests during validation |
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
| `--max-workers COUNT` | Override validation worker count for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--cpu-limit COUNT` | Override validation CPU limit for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--gradle-heap-mb MIB` | Override the Gradle validation heap for this invocation; explicit CLI value wins over Android-safe/project phase defaults |
| `--gradle-metaspace-mb, --metaspace-guard-mb MIB` | Override the Gradle validation max metaspace; 0 disables Devtool's explicit MaxMetaspaceSize cap |
| `--no-metaspace-guard` | Do not set an explicit Gradle MaxMetaspaceSize for this validation invocation |
| `--memory-guard-mb MIB` | Override the validation free-memory guard for this invocation; 0 disables it and explicit CLI value wins over phase defaults |
| `--low-priority` | Run validation commands at low scheduler priority |
| `--normal-priority` | Do not lower validation process priority |
| `--parallel` | Enable Gradle parallel execution for validation |
| `--no-parallel` | Disable Gradle parallel execution for validation |
| `--build-cache` | Enable the Gradle build cache for validation |
| `--no-build-cache` | Disable the Gradle build cache for validation |
| `--daemon` | Allow the Gradle daemon during validation |
| `--no-daemon` | Disable the Gradle daemon during validation |
| `--sequential` | Use a persistent Android/Gradle validation session |
| `--resume` | Resume the active validation session |
| `--restart-session` | Replace an existing non-transactional validation session |
| `--retry-failed` | Retry the cached failed phase and every dependent phase |
| `--next` | Run only the next pending phase (the default for `validate continue`) |
| `--phase PHASE` | Run a named phase and every still-pending prerequisite; repeat with --scope selected |
| `--phase-only PHASE_ONLY` | Run only one named pending phase without automatically including prerequisites |
| `--through THROUGH` | Run pending phases through this phase |
| `--complete` | Run every remaining phase |
| `--from-phase, --from FROM_PHASE` | Start this invocation at a named phase |
| `--pause-after PAUSE_AFTER` | Pause after this phase (alias for --through) |
| `--max-phases MAX_PHASES` | Maximum phase boundaries to cross in this invocation |
| `--max-runtime MAX_RUNTIME` | Stop before the next phase after this runtime budget, such as 15m or 1h |
| `--require-complete` | Return a failure code when validation pauses incomplete |
| `--stop-below-battery PERCENT` | Pause before a phase when battery is below this percentage |
| `--require-charging` | Pause before a phase unless the Android device is charging |
| `--stop-above-temperature CELSIUS` | Pause before a phase when device temperature exceeds this value |
| `--stop-below-disk-mb MIB` | Pause a running validation phase when repository free space falls below this threshold; 0 disables |
| `--stop-gradle-after-phase` | Stop Gradle daemons after each completed phase |
| `--keep-gradle-daemon` | Keep Gradle daemons available between phase invocations |
| `--profile VALIDATION_WORKFLOW_PROFILE` | Named validation workflow profile (minimal, default, android-full, release, termux, architecture-final, final-seal, or a repo-defined profile) |
| `--max-infrastructure-retries COUNT` | Automatically retry environment/tooling interruptions this many times per phase |
| `--max-resource-retries COUNT` | Automatically retry corroborated resource interruptions this many times per phase, using the recommended safe profile when available |
| `--no-auto-retry` | Disable automatic infrastructure and resource retries for this session |
| `--rollback-on-failure` | Immediately roll back an attached apply transaction when a validation phase fails |
| `--failure-action FAILURE_ACTION` | Action after a sequential validation failure; pause preserves the tree and rollback point Choices: pause, rollback, keep, prompt |
| `--keep-changes` | When aborting through `validate abort`, keep the applied repository changes |
| `--session SESSION` | Specific validation session ID |
| `--scope VALIDATION_RUN_SCOPE` | Stable validation run scope Choices: next, through, selected, remaining |
| `--if-revision IF_REVISION` | Apply the control request only when the session revision matches |
| `--after-phase` | For validate pause, finish the active phase before pausing |
| `--cancel` | For validate pause, cancel a pending pause request |
| `--rollback` | For validate abort, explicitly roll back the attached transaction |
| `--events` | Include durable events in validation status JSON |
| `--after-event AFTER_EVENT` | Return events after this event ID |
| `--event-limit, --limit EVENT_LIMIT` | Maximum event records to return |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |

### `devtool validate-all`

| Argument / option | Meaning |
| --- | --- |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--affected` | Select dependency-aware targets affected by Git changes |
| `--affected-base AFFECTED_BASE` | Git base revision for affected selection |
| `--affected-head AFFECTED_HEAD` | Git head revision for affected selection |
| `--variant VARIANT` | Variant within the selected workspace target(s) |
| `--workflow-parallel` | Run independent workflow DAG nodes concurrently only when dependency, read/write-scope, and resource checks prove it safe |
| `--no-workflow-parallel` | Force workflow DAG nodes to execute sequentially |
| `--workflow-max-workers COUNT` | Maximum concurrent safe workflow nodes; 0 uses workspace policy/automatic default |
| `--format, --fix` | Autofix Python formatter output during lint/validation; default is check-only |
| `--skip-restore` | Skip restore during validation |
| `--skip-build` | Skip build during validation |
| `--skip-test` | Skip test during validation |
| `--watch` | Rerun validation when files change |
| `--watch-interval WATCH_INTERVAL` | Seconds between watch polls |
| `--matrix MATRIX` | Comma-separated runtime IDs, target names, or variants to validate |
| `--matrix-mode MATRIX_MODE` | Choices: rid, target, variant |
| `--test-profile TEST_PROFILE` | Saved test profile used by validation |
| `--pytest-test NODEID` | Run exactly this pytest file/node ID; repeatable. Suppresses default/affected pytest targets unless --pytest-include-defaults is set. |
| `--pytest-marker EXPR` | Add a pytest -m marker expression; repeatable expressions are combined with AND. |
| `--pytest-k EXPR` | Apply a pytest -k expression in addition to explicit pytest tests/selectors. |
| `--pytest-arg ARG` | Pass one structured argument directly to pytest; repeatable (for example --pytest-arg=--maxfail=1). |
| `--pytest-include-defaults` | Keep configured/affected default pytest targets in addition to explicit --pytest-test selectors. |
| `--select SELECT` | Select tests by type, suite, tag, class, or name during validation; repeatable |
| `--id TEST_IDS` | Select an exact framework test ID during validation; repeatable |
| `--tag TAG` | Include tests with this tag/trait/annotation during validation; repeatable |
| `--exclude-tag EXCLUDE_TAG` | Exclude tests with this tag/trait/annotation during validation; repeatable |
| `--path TEST_PATHS` | Select tests below a source path or package during validation; repeatable |
| `--class, --tests TEST_CLASSES` | Select a test class during validation; repeatable |
| `--method TEST_METHODS` | Select a test method during validation; repeatable |
| `--filter TEST_FILTER` | Framework-native test filter used during validation |
| `--task, --test-task TEST_TASK` | Exclusive native validation unit list; repeatable. Android/Gradle values are Gradle tasks; Python/Node/Rust/etc. values are supported validation actions such as test, lint, build, package-check, or Python test targets. |
| `--failed` | Rerun tests that failed in the previous structured run during validation |
| `--changed` | Select tests related to changed files during validation |
| `--changed-from CHANGED_FROM` | Git base for --changed during validation |
| `--instrumented` | Run Android instrumentation tests during validation |
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
| `--max-workers COUNT` | Override validation worker count for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--cpu-limit COUNT` | Override validation CPU limit for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--gradle-heap-mb MIB` | Override the Gradle validation heap for this invocation; explicit CLI value wins over Android-safe/project phase defaults |
| `--gradle-metaspace-mb, --metaspace-guard-mb MIB` | Override the Gradle validation max metaspace; 0 disables Devtool's explicit MaxMetaspaceSize cap |
| `--no-metaspace-guard` | Do not set an explicit Gradle MaxMetaspaceSize for this validation invocation |
| `--memory-guard-mb MIB` | Override the validation free-memory guard for this invocation; 0 disables it and explicit CLI value wins over phase defaults |
| `--low-priority` | Run validation commands at low scheduler priority |
| `--normal-priority` | Do not lower validation process priority |
| `--parallel` | Enable Gradle parallel execution for validation |
| `--no-parallel` | Disable Gradle parallel execution for validation |
| `--build-cache` | Enable the Gradle build cache for validation |
| `--no-build-cache` | Disable the Gradle build cache for validation |
| `--daemon` | Allow the Gradle daemon during validation |
| `--no-daemon` | Disable the Gradle daemon during validation |

### `devtool wine-validate`

**Aliases:** `devtool wv`

| Argument / option | Meaning |
| --- | --- |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--affected` | Select dependency-aware targets affected by Git changes |
| `--affected-base AFFECTED_BASE` | Git base revision for affected selection |
| `--affected-head AFFECTED_HEAD` | Git head revision for affected selection |
| `--variant VARIANT` | Variant within the selected workspace target(s) |
| `--workflow-parallel` | Run independent workflow DAG nodes concurrently only when dependency, read/write-scope, and resource checks prove it safe |
| `--no-workflow-parallel` | Force workflow DAG nodes to execute sequentially |
| `--workflow-max-workers COUNT` | Maximum concurrent safe workflow nodes; 0 uses workspace policy/automatic default |
| `--format, --fix` | Autofix Python formatter output during lint/validation; default is check-only |
| `--skip-restore` | Skip restore during validation |
| `--skip-build` | Skip build during validation |
| `--skip-test` | Skip test during validation |
| `--watch` | Rerun validation when files change |
| `--watch-interval WATCH_INTERVAL` | Seconds between watch polls |
| `--matrix MATRIX` | Comma-separated runtime IDs, target names, or variants to validate |
| `--matrix-mode MATRIX_MODE` | Choices: rid, target, variant |
| `--test-profile TEST_PROFILE` | Saved test profile used by validation |
| `--pytest-test NODEID` | Run exactly this pytest file/node ID; repeatable. Suppresses default/affected pytest targets unless --pytest-include-defaults is set. |
| `--pytest-marker EXPR` | Add a pytest -m marker expression; repeatable expressions are combined with AND. |
| `--pytest-k EXPR` | Apply a pytest -k expression in addition to explicit pytest tests/selectors. |
| `--pytest-arg ARG` | Pass one structured argument directly to pytest; repeatable (for example --pytest-arg=--maxfail=1). |
| `--pytest-include-defaults` | Keep configured/affected default pytest targets in addition to explicit --pytest-test selectors. |
| `--select SELECT` | Select tests by type, suite, tag, class, or name during validation; repeatable |
| `--id TEST_IDS` | Select an exact framework test ID during validation; repeatable |
| `--tag TAG` | Include tests with this tag/trait/annotation during validation; repeatable |
| `--exclude-tag EXCLUDE_TAG` | Exclude tests with this tag/trait/annotation during validation; repeatable |
| `--path TEST_PATHS` | Select tests below a source path or package during validation; repeatable |
| `--class, --tests TEST_CLASSES` | Select a test class during validation; repeatable |
| `--method TEST_METHODS` | Select a test method during validation; repeatable |
| `--filter TEST_FILTER` | Framework-native test filter used during validation |
| `--task, --test-task TEST_TASK` | Exclusive native validation unit list; repeatable. Android/Gradle values are Gradle tasks; Python/Node/Rust/etc. values are supported validation actions such as test, lint, build, package-check, or Python test targets. |
| `--failed` | Rerun tests that failed in the previous structured run during validation |
| `--changed` | Select tests related to changed files during validation |
| `--changed-from CHANGED_FROM` | Git base for --changed during validation |
| `--instrumented` | Run Android instrumentation tests during validation |
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
| `--max-workers COUNT` | Override validation worker count for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--cpu-limit COUNT` | Override validation CPU limit for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--gradle-heap-mb MIB` | Override the Gradle validation heap for this invocation; explicit CLI value wins over Android-safe/project phase defaults |
| `--gradle-metaspace-mb, --metaspace-guard-mb MIB` | Override the Gradle validation max metaspace; 0 disables Devtool's explicit MaxMetaspaceSize cap |
| `--no-metaspace-guard` | Do not set an explicit Gradle MaxMetaspaceSize for this validation invocation |
| `--memory-guard-mb MIB` | Override the validation free-memory guard for this invocation; 0 disables it and explicit CLI value wins over phase defaults |
| `--low-priority` | Run validation commands at low scheduler priority |
| `--normal-priority` | Do not lower validation process priority |
| `--parallel` | Enable Gradle parallel execution for validation |
| `--no-parallel` | Disable Gradle parallel execution for validation |
| `--build-cache` | Enable the Gradle build cache for validation |
| `--no-build-cache` | Disable the Gradle build cache for validation |
| `--daemon` | Allow the Gradle daemon during validation |
| `--no-daemon` | Disable the Gradle daemon during validation |

### `devtool run`

**Aliases:** `devtool rn`

| Argument / option | Meaning |
| --- | --- |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--affected` | Select dependency-aware targets affected by Git changes |
| `--affected-base AFFECTED_BASE` | Git base revision for affected selection |
| `--affected-head AFFECTED_HEAD` | Git head revision for affected selection |
| `--variant VARIANT` | Variant within the selected workspace target(s) |

### `devtool package`

**Aliases:** `devtool pkg`

| Argument / option | Meaning |
| --- | --- |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--affected` | Select dependency-aware targets affected by Git changes |
| `--affected-base AFFECTED_BASE` | Git base revision for affected selection |
| `--affected-head AFFECTED_HEAD` | Git head revision for affected selection |
| `--variant VARIANT` | Variant within the selected workspace target(s) |
| `--workflow-parallel` | Run independent workflow DAG nodes concurrently only when dependency, read/write-scope, and resource checks prove it safe |
| `--no-workflow-parallel` | Force workflow DAG nodes to execute sequentially |
| `--workflow-max-workers COUNT` | Maximum concurrent safe workflow nodes; 0 uses workspace policy/automatic default |
| `-o, --artifact-output-dir, --output-dir ARTIFACT_OUTPUT_DIR` | Copy produced artifacts here (default: .devtool/build-artifacts) |
| `-n, --artifact-output-name, --output-name ARTIFACT_OUTPUT_NAME` | Base filename for copied artifacts |
| `--artifact-collision ARTIFACT_COLLISION` | Managed artifact collision policy (default: unique) Choices: unique, replace, fail |
| `--verify-artifacts` | Verify produced artifacts before registration/export |
| `--no-verify-artifacts` | Collect artifacts without content verification |

### `devtool release`

**Aliases:** `devtool rel`

| Argument / option | Meaning |
| --- | --- |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--affected` | Select dependency-aware targets affected by Git changes |
| `--affected-base AFFECTED_BASE` | Git base revision for affected selection |
| `--affected-head AFFECTED_HEAD` | Git head revision for affected selection |
| `--variant VARIANT` | Variant within the selected workspace target(s) |
| `--workflow-parallel` | Run independent workflow DAG nodes concurrently only when dependency, read/write-scope, and resource checks prove it safe |
| `--no-workflow-parallel` | Force workflow DAG nodes to execute sequentially |
| `--workflow-max-workers COUNT` | Maximum concurrent safe workflow nodes; 0 uses workspace policy/automatic default |
| `--module RELEASE_MODULE` | Release module lane override |
| `--device-tests DEVICE_TESTS` | Release device/instrumentation policy Choices: disabled, when-device-present, required |
| `--previous-apk PREVIOUS_APK` | Previous APK used by upgrade-smoke |
| `--package RELEASE_PACKAGE` | Application package override for smoke gates |
| `--activity RELEASE_ACTIVITY` | Launch activity override for smoke gates |
| `--evidence-output EVIDENCE_OUTPUT` | Explicit immutable release-evidence JSON path |
| `--no-unit-tests` | Accepted by this command. |
| `--no-lint` | Accepted by this command. |
| `--no-install-smoke` | Accepted by this command. |
| `--no-upgrade-smoke` | Accepted by this command. |
| `--no-verify-signature` | Accepted by this command. |
| `--no-verify-certificate-identity` | Accepted by this command. |
| `--format FORMAT` | Choices: text, json |

### `devtool clean`

**Aliases:** `devtool cl`

| Argument / option | Meaning |
| --- | --- |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--affected` | Select dependency-aware targets affected by Git changes |
| `--affected-base AFFECTED_BASE` | Git base revision for affected selection |
| `--affected-head AFFECTED_HEAD` | Git head revision for affected selection |
| `--variant VARIANT` | Variant within the selected workspace target(s) |
| `--project-clean, --project-outputs` | Clean project-owned outputs through the selected project runner; this is the default when no cleanup scope flag is given |
| `--caches` | Prune repository-scoped Devtool-managed caches and transient state without implicitly running project clean |
| `--space` | Prune Devtool-managed Android/toolchain caches without touching user-global caches |
| `--global-caches` | Explicitly include user-global Android download cache cleanup |
| `--gradle-user-cache` | Explicitly include old version-specific ~/.gradle caches and build-cache-1; modules/wrapper caches are preserved |
| `--space-target-free-mb SPACE_TARGET_FREE_MB` | With --space, stop after reaching this much free space; 0 removes all safe candidates |
| `--managed-gradle-retention MANAGED_GRADLE_RETENTION` | With --space, keep this many newest Devtool-managed Gradle distributions |

### `devtool inspect`

**Aliases:** `devtool info`

| Argument / option | Meaning |
| --- | --- |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--affected` | Select dependency-aware targets affected by Git changes |
| `--affected-base AFFECTED_BASE` | Git base revision for affected selection |
| `--affected-head AFFECTED_HEAD` | Git head revision for affected selection |
| `--variant VARIANT` | Variant within the selected workspace target(s) |

### `devtool status`

**Aliases:** `devtool st`

| Argument / option | Meaning |
| --- | --- |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--affected` | Select dependency-aware targets affected by Git changes |
| `--affected-base AFFECTED_BASE` | Git base revision for affected selection |
| `--affected-head AFFECTED_HEAD` | Git head revision for affected selection |
| `--variant VARIANT` | Variant within the selected workspace target(s) |
| `--json` | Emit structured project, execution, repository identity, and federation boundary status |

### `devtool doctor`

**Aliases:** `devtool dr`

| Argument / option | Meaning |
| --- | --- |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--affected` | Select dependency-aware targets affected by Git changes |
| `--affected-base AFFECTED_BASE` | Git base revision for affected selection |
| `--affected-head AFFECTED_HEAD` | Git head revision for affected selection |
| `--variant VARIANT` | Variant within the selected workspace target(s) |
| `--fix` | Create safe devtool state/cache directories |
| `--explain` | Explain Termux provider selection and rejection reasons |
| `--json` | Emit structured runtime, Android, device, project, and expanded-workflow health checks |
| `--workflow WORKFLOW` | Workflow/action to analyze across the selected target scope; default: all declared workflows |

### `devtool variants`

List Android variant execution profiles for the selected target

| Argument / option | Meaning |
| --- | --- |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--affected` | Select dependency-aware targets affected by Git changes |
| `--affected-base AFFECTED_BASE` | Git base revision for affected selection |
| `--affected-head AFFECTED_HEAD` | Git head revision for affected selection |
| `--variant VARIANT` | Variant within the selected workspace target(s) |

### `devtool start-validation`

Start a persistent sequential validation session

**Aliases:** `devtool sv`

| Argument / option | Meaning |
| --- | --- |
| `--format, --fix` | Autofix Python formatter output during lint/validation; default is check-only |
| `--skip-restore` | Skip restore during validation |
| `--skip-build` | Skip build during validation |
| `--skip-test` | Skip test during validation |
| `--pytest-test NODEID` | Run exactly this pytest file/node ID; repeatable. Suppresses default/affected pytest targets unless --pytest-include-defaults is set. |
| `--pytest-marker EXPR` | Add a pytest -m marker expression; repeatable expressions are combined with AND. |
| `--pytest-k EXPR` | Apply a pytest -k expression in addition to explicit pytest tests/selectors. |
| `--pytest-arg ARG` | Pass one structured argument directly to pytest; repeatable (for example --pytest-arg=--maxfail=1). |
| `--pytest-include-defaults` | Keep configured/affected default pytest targets in addition to explicit --pytest-test selectors. |
| `--select SELECT` | Select tests by type, suite, tag, class, or name during validation; repeatable |
| `--id TEST_IDS` | Select an exact framework test ID during validation; repeatable |
| `--tag TAG` | Include tests with this tag/trait/annotation during validation; repeatable |
| `--exclude-tag EXCLUDE_TAG` | Exclude tests with this tag/trait/annotation during validation; repeatable |
| `--path TEST_PATHS` | Select tests below a source path or package during validation; repeatable |
| `--class, --tests TEST_CLASSES` | Select a test class during validation; repeatable |
| `--method TEST_METHODS` | Select a test method during validation; repeatable |
| `--filter TEST_FILTER` | Framework-native test filter used during validation |
| `--task, --test-task TEST_TASK` | Exclusive native validation unit list; repeatable. Android/Gradle values are Gradle tasks; Python/Node/Rust/etc. values are supported validation actions such as test, lint, build, package-check, or Python test targets. |
| `--failed` | Rerun tests that failed in the previous structured run during validation |
| `--changed` | Select tests related to changed files during validation |
| `--changed-from CHANGED_FROM` | Git base for --changed during validation |
| `--instrumented` | Run Android instrumentation tests during validation |
| `--test-profile TEST_PROFILE` | Saved test profile used by validation |
| `--matrix MATRIX` | Comma-separated runtime IDs, targets, or variants |
| `--matrix-mode MATRIX_MODE` | Choices: rid, target, variant |
| `--sequential` | Use a persistent Android/Gradle validation session |
| `--resume` | Resume the active validation session |
| `--restart-session` | Replace an existing non-transactional validation session |
| `--retry-failed` | Retry the cached failed phase and every dependent phase |
| `--next` | Run only the next pending phase (the default for `validate continue`) |
| `--phase PHASE` | Run a named phase and every still-pending prerequisite; repeat with --scope selected |
| `--phase-only PHASE_ONLY` | Run only one named pending phase without automatically including prerequisites |
| `--through THROUGH` | Run pending phases through this phase |
| `--complete` | Run every remaining phase |
| `--from-phase, --from FROM_PHASE` | Start this invocation at a named phase |
| `--pause-after PAUSE_AFTER` | Pause after this phase (alias for --through) |
| `--max-phases MAX_PHASES` | Maximum phase boundaries to cross in this invocation |
| `--max-runtime MAX_RUNTIME` | Stop before the next phase after this runtime budget, such as 15m or 1h |
| `--require-complete` | Return a failure code when validation pauses incomplete |
| `--stop-below-battery PERCENT` | Pause before a phase when battery is below this percentage |
| `--require-charging` | Pause before a phase unless the Android device is charging |
| `--stop-above-temperature CELSIUS` | Pause before a phase when device temperature exceeds this value |
| `--stop-below-disk-mb MIB` | Pause a running validation phase when repository free space falls below this threshold; 0 disables |
| `--stop-gradle-after-phase` | Stop Gradle daemons after each completed phase |
| `--keep-gradle-daemon` | Keep Gradle daemons available between phase invocations |
| `--profile VALIDATION_WORKFLOW_PROFILE` | Named validation workflow profile (minimal, default, android-full, release, termux, architecture-final, final-seal, or a repo-defined profile) |
| `--max-infrastructure-retries COUNT` | Automatically retry environment/tooling interruptions this many times per phase |
| `--max-resource-retries COUNT` | Automatically retry corroborated resource interruptions this many times per phase, using the recommended safe profile when available |
| `--no-auto-retry` | Disable automatic infrastructure and resource retries for this session |
| `--rollback-on-failure` | Immediately roll back an attached apply transaction when a validation phase fails |
| `--failure-action FAILURE_ACTION` | Action after a sequential validation failure; pause preserves the tree and rollback point Choices: pause, rollback, keep, prompt |
| `--keep-changes` | When aborting through `validate abort`, keep the applied repository changes |
| `--session SESSION` | Specific validation session ID |
| `--scope VALIDATION_RUN_SCOPE` | Stable validation run scope Choices: next, through, selected, remaining |
| `--if-revision IF_REVISION` | Apply the control request only when the session revision matches |
| `--after-phase` | For validate pause, finish the active phase before pausing |
| `--cancel` | For validate pause, cancel a pending pause request |
| `--rollback` | For validate abort, explicitly roll back the attached transaction |
| `--events` | Include durable events in validation status JSON |
| `--after-event AFTER_EVENT` | Return events after this event ID |
| `--event-limit, --limit EVENT_LIMIT` | Maximum event records to return |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
| `--max-workers COUNT` | Override validation worker count for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--cpu-limit COUNT` | Override validation CPU limit for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--gradle-heap-mb MIB` | Override the Gradle validation heap for this invocation; explicit CLI value wins over Android-safe/project phase defaults |
| `--gradle-metaspace-mb, --metaspace-guard-mb MIB` | Override the Gradle validation max metaspace; 0 disables Devtool's explicit MaxMetaspaceSize cap |
| `--no-metaspace-guard` | Do not set an explicit Gradle MaxMetaspaceSize for this validation invocation |
| `--memory-guard-mb MIB` | Override the validation free-memory guard for this invocation; 0 disables it and explicit CLI value wins over phase defaults |
| `--low-priority` | Run validation commands at low scheduler priority |
| `--normal-priority` | Do not lower validation process priority |
| `--parallel` | Enable Gradle parallel execution for validation |
| `--no-parallel` | Disable Gradle parallel execution for validation |
| `--build-cache` | Enable the Gradle build cache for validation |
| `--no-build-cache` | Disable the Gradle build cache for validation |
| `--daemon` | Allow the Gradle daemon during validation |
| `--no-daemon` | Disable the Gradle daemon during validation |

### `devtool resume-validation`

Resume a persistent sequential validation session

**Aliases:** `devtool rv`

| Argument / option | Meaning |
| --- | --- |
| `--format, --fix` | Autofix Python formatter output during lint/validation; default is check-only |
| `--skip-restore` | Skip restore during validation |
| `--skip-build` | Skip build during validation |
| `--skip-test` | Skip test during validation |
| `--pytest-test NODEID` | Run exactly this pytest file/node ID; repeatable. Suppresses default/affected pytest targets unless --pytest-include-defaults is set. |
| `--pytest-marker EXPR` | Add a pytest -m marker expression; repeatable expressions are combined with AND. |
| `--pytest-k EXPR` | Apply a pytest -k expression in addition to explicit pytest tests/selectors. |
| `--pytest-arg ARG` | Pass one structured argument directly to pytest; repeatable (for example --pytest-arg=--maxfail=1). |
| `--pytest-include-defaults` | Keep configured/affected default pytest targets in addition to explicit --pytest-test selectors. |
| `--select SELECT` | Select tests by type, suite, tag, class, or name during validation; repeatable |
| `--id TEST_IDS` | Select an exact framework test ID during validation; repeatable |
| `--tag TAG` | Include tests with this tag/trait/annotation during validation; repeatable |
| `--exclude-tag EXCLUDE_TAG` | Exclude tests with this tag/trait/annotation during validation; repeatable |
| `--path TEST_PATHS` | Select tests below a source path or package during validation; repeatable |
| `--class, --tests TEST_CLASSES` | Select a test class during validation; repeatable |
| `--method TEST_METHODS` | Select a test method during validation; repeatable |
| `--filter TEST_FILTER` | Framework-native test filter used during validation |
| `--task, --test-task TEST_TASK` | Exclusive native validation unit list; repeatable. Android/Gradle values are Gradle tasks; Python/Node/Rust/etc. values are supported validation actions such as test, lint, build, package-check, or Python test targets. |
| `--failed` | Rerun tests that failed in the previous structured run during validation |
| `--changed` | Select tests related to changed files during validation |
| `--changed-from CHANGED_FROM` | Git base for --changed during validation |
| `--instrumented` | Run Android instrumentation tests during validation |
| `--test-profile TEST_PROFILE` | Saved test profile used by validation |
| `--matrix MATRIX` | Comma-separated runtime IDs, targets, or variants |
| `--matrix-mode MATRIX_MODE` | Choices: rid, target, variant |
| `--sequential` | Use a persistent Android/Gradle validation session |
| `--resume` | Resume the active validation session |
| `--restart-session` | Replace an existing non-transactional validation session |
| `--retry-failed` | Retry the cached failed phase and every dependent phase |
| `--next` | Run only the next pending phase (the default for `validate continue`) |
| `--phase PHASE` | Run a named phase and every still-pending prerequisite; repeat with --scope selected |
| `--phase-only PHASE_ONLY` | Run only one named pending phase without automatically including prerequisites |
| `--through THROUGH` | Run pending phases through this phase |
| `--complete` | Run every remaining phase |
| `--from-phase, --from FROM_PHASE` | Start this invocation at a named phase |
| `--pause-after PAUSE_AFTER` | Pause after this phase (alias for --through) |
| `--max-phases MAX_PHASES` | Maximum phase boundaries to cross in this invocation |
| `--max-runtime MAX_RUNTIME` | Stop before the next phase after this runtime budget, such as 15m or 1h |
| `--require-complete` | Return a failure code when validation pauses incomplete |
| `--stop-below-battery PERCENT` | Pause before a phase when battery is below this percentage |
| `--require-charging` | Pause before a phase unless the Android device is charging |
| `--stop-above-temperature CELSIUS` | Pause before a phase when device temperature exceeds this value |
| `--stop-below-disk-mb MIB` | Pause a running validation phase when repository free space falls below this threshold; 0 disables |
| `--stop-gradle-after-phase` | Stop Gradle daemons after each completed phase |
| `--keep-gradle-daemon` | Keep Gradle daemons available between phase invocations |
| `--profile VALIDATION_WORKFLOW_PROFILE` | Named validation workflow profile (minimal, default, android-full, release, termux, architecture-final, final-seal, or a repo-defined profile) |
| `--max-infrastructure-retries COUNT` | Automatically retry environment/tooling interruptions this many times per phase |
| `--max-resource-retries COUNT` | Automatically retry corroborated resource interruptions this many times per phase, using the recommended safe profile when available |
| `--no-auto-retry` | Disable automatic infrastructure and resource retries for this session |
| `--rollback-on-failure` | Immediately roll back an attached apply transaction when a validation phase fails |
| `--failure-action FAILURE_ACTION` | Action after a sequential validation failure; pause preserves the tree and rollback point Choices: pause, rollback, keep, prompt |
| `--keep-changes` | When aborting through `validate abort`, keep the applied repository changes |
| `--session SESSION` | Specific validation session ID |
| `--scope VALIDATION_RUN_SCOPE` | Stable validation run scope Choices: next, through, selected, remaining |
| `--if-revision IF_REVISION` | Apply the control request only when the session revision matches |
| `--after-phase` | For validate pause, finish the active phase before pausing |
| `--cancel` | For validate pause, cancel a pending pause request |
| `--rollback` | For validate abort, explicitly roll back the attached transaction |
| `--events` | Include durable events in validation status JSON |
| `--after-event AFTER_EVENT` | Return events after this event ID |
| `--event-limit, --limit EVENT_LIMIT` | Maximum event records to return |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
| `--max-workers COUNT` | Override validation worker count for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--cpu-limit COUNT` | Override validation CPU limit for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--gradle-heap-mb MIB` | Override the Gradle validation heap for this invocation; explicit CLI value wins over Android-safe/project phase defaults |
| `--gradle-metaspace-mb, --metaspace-guard-mb MIB` | Override the Gradle validation max metaspace; 0 disables Devtool's explicit MaxMetaspaceSize cap |
| `--no-metaspace-guard` | Do not set an explicit Gradle MaxMetaspaceSize for this validation invocation |
| `--memory-guard-mb MIB` | Override the validation free-memory guard for this invocation; 0 disables it and explicit CLI value wins over phase defaults |
| `--low-priority` | Run validation commands at low scheduler priority |
| `--normal-priority` | Do not lower validation process priority |
| `--parallel` | Enable Gradle parallel execution for validation |
| `--no-parallel` | Disable Gradle parallel execution for validation |
| `--build-cache` | Enable the Gradle build cache for validation |
| `--no-build-cache` | Disable the Gradle build cache for validation |
| `--daemon` | Allow the Gradle daemon during validation |
| `--no-daemon` | Disable the Gradle daemon during validation |

### `devtool validation-menu`

Open the interactive validation menu and wizard

**Aliases:** `devtool vm`

| Argument / option | Meaning |
| --- | --- |
| `--no-fzf` | Use the numbered terminal fallback even when fzf is installed |
| `--runtime-tasks` | Query Gradle for the full task catalog when the menu opens |
| `--wizard` | Start directly in the guided validation wizard |
| `--recipe RECIPE` | Open a built-in or saved validation recipe directly |
| `--artifact ARTIFACT` | Artifact path used to expand a recipe placeholder |
| `--matrix-values MATRIX_VALUES` | Comma-separated matrix values used to expand a recipe placeholder |
| `--print-command` | Print the generated command instead of opening the review screen |

### `devtool menu`

Open an interactive Devtool menu

**Subcommands:** `validation`

### `devtool menu validation`

Open the validation menu and wizard

**Aliases:** `devtool menu validate`, `devtool menu v`

| Argument / option | Meaning |
| --- | --- |
| `--no-fzf` | Use the numbered terminal fallback even when fzf is installed |
| `--runtime-tasks` | Query Gradle for the full task catalog when the menu opens |
| `--wizard` | Start directly in the guided validation wizard |
| `--recipe RECIPE` | Open a built-in or saved validation recipe directly |
| `--artifact ARTIFACT` | Artifact path used to expand a recipe placeholder |
| `--matrix-values MATRIX_VALUES` | Comma-separated matrix values used to expand a recipe placeholder |
| `--print-command` | Print the generated command instead of opening the review screen |

### `devtool ai-prompt`

**Aliases:** `devtool ap`

| Argument / option | Meaning |
| --- | --- |
| `--repo-name REPO_NAME` | Human-friendly repo name for the generated prompt Required. |
| `--runner, --project-type PROJECT_TYPE` | Choices: auto, dotnet, android, gradle, python, node, rust, go, maven, cmake, meson, make, command, unknown |
| `--build-target BUILD_TARGET` | Accepted by this command. |
| `--run-target RUN_TARGET` | Accepted by this command. |
| `--target-name TARGET_NAME` | Named monorepo target/profile to reference |
| `--solution-target SOLUTION_TARGET` | Exact solution target to reference |
| `--delivery-mode DELIVERY_MODE` | Choices: overlay, snapshot |
| `--artifact-type ARTIFACT_TYPE` | Choices: zip, tar, tar.gz, tgz, tar.xz, txz, tar.bz2, tbz2, tar.zst, tzst |
| `--overlay-count OVERLAY_COUNT` | Accepted by this command. |
| `--commit-message COMMIT_MESSAGE` | Accepted by this command. |
| `--wine` | Prefer wine-validate wording in the generated prompt |
| `-o, --output OUTPUT` | Write the generated prompt to a file |
| `--format FORMAT` | Choices: markdown, text |

### `devtool commit`

**Aliases:** `devtool ci`

| Argument / option | Meaning |
| --- | --- |
| `MESSAGE` | Accepted by this command. |
| `--signing SIGNING` | Git SSH signing policy for this commit; default comes from [git].signing Choices: off, if-configured, required |

### `devtool apply-overlay`

**Aliases:** `devtool ao`

| Argument / option | Meaning |
| --- | --- |
| `ARTIFACT` | Accepted by this command. |
| `--validate` | Run validation after applying the artifact; required artifact validation is enabled automatically |
| `--format, --fix` | Autofix Python formatter output during lint/validation; default is check-only |
| `--defer-validate` | Apply now, preserve rollback state, and create a resumable validation session without running a phase |
| `--static-validate` | Apply artifact and run only artifact/static validation scripts without Gradle validation phases |
| `--no-validate` | Disable validation requested or required by the artifact manifest |
| `--test-profile TEST_PROFILE` | Saved test profile used by artifact validation |
| `--pytest-test NODEID` | Run exactly this pytest file/node ID; repeatable. Suppresses default/affected pytest targets unless --pytest-include-defaults is set. |
| `--pytest-marker EXPR` | Add a pytest -m marker expression; repeatable expressions are combined with AND. |
| `--pytest-k EXPR` | Apply a pytest -k expression in addition to explicit pytest tests/selectors. |
| `--pytest-arg ARG` | Pass one structured argument directly to pytest; repeatable (for example --pytest-arg=--maxfail=1). |
| `--pytest-include-defaults` | Keep configured/affected default pytest targets in addition to explicit --pytest-test selectors. |
| `--select SELECT` | Select tests by type, suite, tag, class, or name during validation; repeatable |
| `--id TEST_IDS` | Select an exact framework test ID during validation; repeatable |
| `--tag TAG` | Include tests with this tag/trait/annotation during validation; repeatable |
| `--exclude-tag EXCLUDE_TAG` | Exclude tests with this tag/trait/annotation during validation; repeatable |
| `--path TEST_PATHS` | Select tests below a source path or package during validation; repeatable |
| `--class, --tests TEST_CLASSES` | Select a test class during validation; repeatable |
| `--method TEST_METHODS` | Select a test method during validation; repeatable |
| `--filter TEST_FILTER` | Framework-native test filter used during validation |
| `--task, --test-task TEST_TASK` | Exclusive native validation unit list; repeatable. Android/Gradle values are Gradle tasks; Python/Node/Rust/etc. values are supported validation actions such as test, lint, build, package-check, or Python test targets. |
| `--failed` | Rerun tests that failed in the previous structured run during validation |
| `--changed` | Select tests related to changed files during validation |
| `--changed-from CHANGED_FROM` | Git base for --changed during validation |
| `--instrumented` | Run Android instrumentation tests during validation |
| `--validate-only` | Skip apply and only run validation |
| `--skip-restore` | Skip restore during validation |
| `--skip-build` | Skip build during validation |
| `--skip-test` | Skip test during validation |
| `--sequential` | Use a persistent Android/Gradle validation session |
| `--resume` | Resume the active validation session |
| `--restart-session` | Replace an existing non-transactional validation session |
| `--retry-failed` | Retry the cached failed phase and every dependent phase |
| `--next` | Run only the next pending phase (the default for `validate continue`) |
| `--phase PHASE` | Run a named phase and every still-pending prerequisite; repeat with --scope selected |
| `--phase-only PHASE_ONLY` | Run only one named pending phase without automatically including prerequisites |
| `--through THROUGH` | Run pending phases through this phase |
| `--complete` | Run every remaining phase |
| `--from-phase, --from FROM_PHASE` | Start this invocation at a named phase |
| `--pause-after PAUSE_AFTER` | Pause after this phase (alias for --through) |
| `--max-phases MAX_PHASES` | Maximum phase boundaries to cross in this invocation |
| `--max-runtime MAX_RUNTIME` | Stop before the next phase after this runtime budget, such as 15m or 1h |
| `--require-complete` | Return a failure code when validation pauses incomplete |
| `--stop-below-battery PERCENT` | Pause before a phase when battery is below this percentage |
| `--require-charging` | Pause before a phase unless the Android device is charging |
| `--stop-above-temperature CELSIUS` | Pause before a phase when device temperature exceeds this value |
| `--stop-below-disk-mb MIB` | Pause a running validation phase when repository free space falls below this threshold; 0 disables |
| `--stop-gradle-after-phase` | Stop Gradle daemons after each completed phase |
| `--keep-gradle-daemon` | Keep Gradle daemons available between phase invocations |
| `--profile VALIDATION_WORKFLOW_PROFILE` | Named validation workflow profile (minimal, default, android-full, release, termux, architecture-final, final-seal, or a repo-defined profile) |
| `--max-infrastructure-retries COUNT` | Automatically retry environment/tooling interruptions this many times per phase |
| `--max-resource-retries COUNT` | Automatically retry corroborated resource interruptions this many times per phase, using the recommended safe profile when available |
| `--no-auto-retry` | Disable automatic infrastructure and resource retries for this session |
| `--rollback-on-failure` | Immediately roll back an attached apply transaction when a validation phase fails |
| `--failure-action FAILURE_ACTION` | Action after a sequential validation failure; pause preserves the tree and rollback point Choices: pause, rollback, keep, prompt |
| `--keep-changes` | When aborting through `validate abort`, keep the applied repository changes |
| `--session SESSION` | Specific validation session ID |
| `--scope VALIDATION_RUN_SCOPE` | Stable validation run scope Choices: next, through, selected, remaining |
| `--if-revision IF_REVISION` | Apply the control request only when the session revision matches |
| `--after-phase` | For validate pause, finish the active phase before pausing |
| `--cancel` | For validate pause, cancel a pending pause request |
| `--rollback` | For validate abort, explicitly roll back the attached transaction |
| `--events` | Include durable events in validation status JSON |
| `--after-event AFTER_EVENT` | Return events after this event ID |
| `--event-limit, --limit EVENT_LIMIT` | Maximum event records to return |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
| `--max-workers COUNT` | Override validation worker count for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--cpu-limit COUNT` | Override validation CPU limit for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--gradle-heap-mb MIB` | Override the Gradle validation heap for this invocation; explicit CLI value wins over Android-safe/project phase defaults |
| `--gradle-metaspace-mb, --metaspace-guard-mb MIB` | Override the Gradle validation max metaspace; 0 disables Devtool's explicit MaxMetaspaceSize cap |
| `--no-metaspace-guard` | Do not set an explicit Gradle MaxMetaspaceSize for this validation invocation |
| `--memory-guard-mb MIB` | Override the validation free-memory guard for this invocation; 0 disables it and explicit CLI value wins over phase defaults |
| `--low-priority` | Run validation commands at low scheduler priority |
| `--normal-priority` | Do not lower validation process priority |
| `--parallel` | Enable Gradle parallel execution for validation |
| `--no-parallel` | Disable Gradle parallel execution for validation |
| `--build-cache` | Enable the Gradle build cache for validation |
| `--no-build-cache` | Disable the Gradle build cache for validation |
| `--daemon` | Allow the Gradle daemon during validation |
| `--no-daemon` | Disable the Gradle daemon during validation |
| `--workflow-parallel` | Run independent workflow DAG nodes concurrently only when dependency, read/write-scope, and resource checks prove it safe |
| `--no-workflow-parallel` | Force workflow DAG nodes to execute sequentially |
| `--workflow-max-workers COUNT` | Maximum concurrent safe workflow nodes; 0 uses workspace policy/automatic default |
| `--preview` | Preview archive changes without applying |
| `--diff` | Show a repo-relative unified diff of artifact changes |
| `--interactive` | Show preview and ask before applying |
| `--no-manifest-check` | Disable devtool artifact manifest validation |
| `--ignore-checksum` | Skip SHA-256 checksum validation while retaining all other artifact manifest checks |
| `--keep-artifact` | Keep the source artifact and sidecars after a successful apply (failures are always preserved by default) |
| `--delete-artifact-on-failure` | Explicitly delete the source artifact after a failed apply (default: preserve it for retry and diagnosis) |
| `--no-rollback-on-failure` | Keep successfully applied changes when post-apply validation or commands fail; atomic extraction/preflight failures still roll back for repository integrity |
| `--no-apply-notification` | Do not show the final apply notification/toast |
| `--commit COMMIT` | Commit with this message after apply and optional validation |
| `--commit-mode COMMIT_MODE` | Git result policy: new, amend, fixup, or none (default inferred from artifact/flags) Choices: new, amend, fixup, none |
| `--amend` | Safely amend HEAD after apply; alias for --commit-mode amend |
| `--no-commit` | Disable a commit requested by the artifact manifest; alias for --commit-mode none |
| `--fixup-target REV` | Create a fixup! commit targeting REV (defaults to HEAD in fixup mode) |
| `--reset-author` | With amend mode, reset commit author to the current Git identity |
| `--allow-merge-amend` | Allow amending a merge commit (refused by default) |
| `--allow-published-rewrite` | Allow amending HEAD when reachable from a protected upstream/remote ref; never pushes |
| `--preserve-dirty` | Explicitly request transactional preservation/restoration of staged, unstaged, untracked, symlink, mode, and index state |
| `--signing SIGNING` | Git SSH signing policy for the result commit; default comes from [git].signing Choices: off, if-configured, required |
| `--isolation ISOLATION` | Apply in an isolated Devtool-owned Git worktree when supported (default: worktree); use direct for legacy in-place mutation Choices: worktree, direct |
| `--no-artifact-script` | Skip embedded artifact validation scripts while keeping built-in validation |
| `--rebase` | Rebase a single overlay inventory against the current repository before applying |

### `devtool apply-overlays`

**Aliases:** `devtool aos`

| Argument / option | Meaning |
| --- | --- |
| `ARTIFACT...` | Overlay bundle archive, or multiple overlay archives to bundle/apply transactionally |
| `--validate` | Run validation after applying the artifact; required artifact validation is enabled automatically |
| `--format, --fix` | Autofix Python formatter output during lint/validation; default is check-only |
| `--defer-validate` | Apply now, preserve rollback state, and create a resumable validation session without running a phase |
| `--static-validate` | Apply artifact and run only artifact/static validation scripts without Gradle validation phases |
| `--no-validate` | Disable validation requested or required by the artifact manifest |
| `--test-profile TEST_PROFILE` | Saved test profile used by artifact validation |
| `--pytest-test NODEID` | Run exactly this pytest file/node ID; repeatable. Suppresses default/affected pytest targets unless --pytest-include-defaults is set. |
| `--pytest-marker EXPR` | Add a pytest -m marker expression; repeatable expressions are combined with AND. |
| `--pytest-k EXPR` | Apply a pytest -k expression in addition to explicit pytest tests/selectors. |
| `--pytest-arg ARG` | Pass one structured argument directly to pytest; repeatable (for example --pytest-arg=--maxfail=1). |
| `--pytest-include-defaults` | Keep configured/affected default pytest targets in addition to explicit --pytest-test selectors. |
| `--select SELECT` | Select tests by type, suite, tag, class, or name during validation; repeatable |
| `--id TEST_IDS` | Select an exact framework test ID during validation; repeatable |
| `--tag TAG` | Include tests with this tag/trait/annotation during validation; repeatable |
| `--exclude-tag EXCLUDE_TAG` | Exclude tests with this tag/trait/annotation during validation; repeatable |
| `--path TEST_PATHS` | Select tests below a source path or package during validation; repeatable |
| `--class, --tests TEST_CLASSES` | Select a test class during validation; repeatable |
| `--method TEST_METHODS` | Select a test method during validation; repeatable |
| `--filter TEST_FILTER` | Framework-native test filter used during validation |
| `--task, --test-task TEST_TASK` | Exclusive native validation unit list; repeatable. Android/Gradle values are Gradle tasks; Python/Node/Rust/etc. values are supported validation actions such as test, lint, build, package-check, or Python test targets. |
| `--failed` | Rerun tests that failed in the previous structured run during validation |
| `--changed` | Select tests related to changed files during validation |
| `--changed-from CHANGED_FROM` | Git base for --changed during validation |
| `--instrumented` | Run Android instrumentation tests during validation |
| `--validate-only` | Skip apply and only run validation |
| `--skip-restore` | Skip restore during validation |
| `--skip-build` | Skip build during validation |
| `--skip-test` | Skip test during validation |
| `--sequential` | Use a persistent Android/Gradle validation session |
| `--resume` | Resume the active validation session |
| `--restart-session` | Replace an existing non-transactional validation session |
| `--retry-failed` | Retry the cached failed phase and every dependent phase |
| `--next` | Run only the next pending phase (the default for `validate continue`) |
| `--phase PHASE` | Run a named phase and every still-pending prerequisite; repeat with --scope selected |
| `--phase-only PHASE_ONLY` | Run only one named pending phase without automatically including prerequisites |
| `--through THROUGH` | Run pending phases through this phase |
| `--complete` | Run every remaining phase |
| `--from-phase, --from FROM_PHASE` | Start this invocation at a named phase |
| `--pause-after PAUSE_AFTER` | Pause after this phase (alias for --through) |
| `--max-phases MAX_PHASES` | Maximum phase boundaries to cross in this invocation |
| `--max-runtime MAX_RUNTIME` | Stop before the next phase after this runtime budget, such as 15m or 1h |
| `--require-complete` | Return a failure code when validation pauses incomplete |
| `--stop-below-battery PERCENT` | Pause before a phase when battery is below this percentage |
| `--require-charging` | Pause before a phase unless the Android device is charging |
| `--stop-above-temperature CELSIUS` | Pause before a phase when device temperature exceeds this value |
| `--stop-below-disk-mb MIB` | Pause a running validation phase when repository free space falls below this threshold; 0 disables |
| `--stop-gradle-after-phase` | Stop Gradle daemons after each completed phase |
| `--keep-gradle-daemon` | Keep Gradle daemons available between phase invocations |
| `--profile VALIDATION_WORKFLOW_PROFILE` | Named validation workflow profile (minimal, default, android-full, release, termux, architecture-final, final-seal, or a repo-defined profile) |
| `--max-infrastructure-retries COUNT` | Automatically retry environment/tooling interruptions this many times per phase |
| `--max-resource-retries COUNT` | Automatically retry corroborated resource interruptions this many times per phase, using the recommended safe profile when available |
| `--no-auto-retry` | Disable automatic infrastructure and resource retries for this session |
| `--rollback-on-failure` | Immediately roll back an attached apply transaction when a validation phase fails |
| `--failure-action FAILURE_ACTION` | Action after a sequential validation failure; pause preserves the tree and rollback point Choices: pause, rollback, keep, prompt |
| `--keep-changes` | When aborting through `validate abort`, keep the applied repository changes |
| `--session SESSION` | Specific validation session ID |
| `--scope VALIDATION_RUN_SCOPE` | Stable validation run scope Choices: next, through, selected, remaining |
| `--if-revision IF_REVISION` | Apply the control request only when the session revision matches |
| `--after-phase` | For validate pause, finish the active phase before pausing |
| `--cancel` | For validate pause, cancel a pending pause request |
| `--rollback` | For validate abort, explicitly roll back the attached transaction |
| `--events` | Include durable events in validation status JSON |
| `--after-event AFTER_EVENT` | Return events after this event ID |
| `--event-limit, --limit EVENT_LIMIT` | Maximum event records to return |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
| `--max-workers COUNT` | Override validation worker count for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--cpu-limit COUNT` | Override validation CPU limit for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--gradle-heap-mb MIB` | Override the Gradle validation heap for this invocation; explicit CLI value wins over Android-safe/project phase defaults |
| `--gradle-metaspace-mb, --metaspace-guard-mb MIB` | Override the Gradle validation max metaspace; 0 disables Devtool's explicit MaxMetaspaceSize cap |
| `--no-metaspace-guard` | Do not set an explicit Gradle MaxMetaspaceSize for this validation invocation |
| `--memory-guard-mb MIB` | Override the validation free-memory guard for this invocation; 0 disables it and explicit CLI value wins over phase defaults |
| `--low-priority` | Run validation commands at low scheduler priority |
| `--normal-priority` | Do not lower validation process priority |
| `--parallel` | Enable Gradle parallel execution for validation |
| `--no-parallel` | Disable Gradle parallel execution for validation |
| `--build-cache` | Enable the Gradle build cache for validation |
| `--no-build-cache` | Disable the Gradle build cache for validation |
| `--daemon` | Allow the Gradle daemon during validation |
| `--no-daemon` | Disable the Gradle daemon during validation |
| `--workflow-parallel` | Run independent workflow DAG nodes concurrently only when dependency, read/write-scope, and resource checks prove it safe |
| `--no-workflow-parallel` | Force workflow DAG nodes to execute sequentially |
| `--workflow-max-workers COUNT` | Maximum concurrent safe workflow nodes; 0 uses workspace policy/automatic default |
| `--preview` | Preview archive changes without applying |
| `--diff` | Show a repo-relative unified diff of artifact changes |
| `--interactive` | Show preview and ask before applying |
| `--no-manifest-check` | Disable devtool artifact manifest validation |
| `--ignore-checksum` | Skip SHA-256 checksum validation while retaining all other artifact manifest checks |
| `--keep-artifact` | Keep the source artifact and sidecars after a successful apply (failures are always preserved by default) |
| `--delete-artifact-on-failure` | Explicitly delete the source artifact after a failed apply (default: preserve it for retry and diagnosis) |
| `--no-rollback-on-failure` | Keep successfully applied changes when post-apply validation or commands fail; atomic extraction/preflight failures still roll back for repository integrity |
| `--no-apply-notification` | Do not show the final apply notification/toast |
| `--commit COMMIT` | Commit with this message after apply and optional validation |
| `--commit-mode COMMIT_MODE` | Git result policy: new, amend, fixup, or none (default inferred from artifact/flags) Choices: new, amend, fixup, none |
| `--amend` | Safely amend HEAD after apply; alias for --commit-mode amend |
| `--no-commit` | Disable a commit requested by the artifact manifest; alias for --commit-mode none |
| `--fixup-target REV` | Create a fixup! commit targeting REV (defaults to HEAD in fixup mode) |
| `--reset-author` | With amend mode, reset commit author to the current Git identity |
| `--allow-merge-amend` | Allow amending a merge commit (refused by default) |
| `--allow-published-rewrite` | Allow amending HEAD when reachable from a protected upstream/remote ref; never pushes |
| `--preserve-dirty` | Explicitly request transactional preservation/restoration of staged, unstaged, untracked, symlink, mode, and index state |
| `--signing SIGNING` | Git SSH signing policy for the result commit; default comes from [git].signing Choices: off, if-configured, required |
| `--isolation ISOLATION` | Apply in an isolated Devtool-owned Git worktree when supported (default: worktree); use direct for legacy in-place mutation Choices: worktree, direct |
| `--no-artifact-script` | Skip embedded artifact validation scripts while keeping built-in validation |
| `--rebase` | Rebase a single overlay inventory against the current repository before applying |

### `devtool apply-snapshot`

**Aliases:** `devtool as`

| Argument / option | Meaning |
| --- | --- |
| `ARTIFACT` | Accepted by this command. |
| `--validate` | Run validation after applying the artifact; required artifact validation is enabled automatically |
| `--format, --fix` | Autofix Python formatter output during lint/validation; default is check-only |
| `--defer-validate` | Apply now, preserve rollback state, and create a resumable validation session without running a phase |
| `--static-validate` | Apply artifact and run only artifact/static validation scripts without Gradle validation phases |
| `--no-validate` | Disable validation requested or required by the artifact manifest |
| `--test-profile TEST_PROFILE` | Saved test profile used by artifact validation |
| `--pytest-test NODEID` | Run exactly this pytest file/node ID; repeatable. Suppresses default/affected pytest targets unless --pytest-include-defaults is set. |
| `--pytest-marker EXPR` | Add a pytest -m marker expression; repeatable expressions are combined with AND. |
| `--pytest-k EXPR` | Apply a pytest -k expression in addition to explicit pytest tests/selectors. |
| `--pytest-arg ARG` | Pass one structured argument directly to pytest; repeatable (for example --pytest-arg=--maxfail=1). |
| `--pytest-include-defaults` | Keep configured/affected default pytest targets in addition to explicit --pytest-test selectors. |
| `--select SELECT` | Select tests by type, suite, tag, class, or name during validation; repeatable |
| `--id TEST_IDS` | Select an exact framework test ID during validation; repeatable |
| `--tag TAG` | Include tests with this tag/trait/annotation during validation; repeatable |
| `--exclude-tag EXCLUDE_TAG` | Exclude tests with this tag/trait/annotation during validation; repeatable |
| `--path TEST_PATHS` | Select tests below a source path or package during validation; repeatable |
| `--class, --tests TEST_CLASSES` | Select a test class during validation; repeatable |
| `--method TEST_METHODS` | Select a test method during validation; repeatable |
| `--filter TEST_FILTER` | Framework-native test filter used during validation |
| `--task, --test-task TEST_TASK` | Exclusive native validation unit list; repeatable. Android/Gradle values are Gradle tasks; Python/Node/Rust/etc. values are supported validation actions such as test, lint, build, package-check, or Python test targets. |
| `--failed` | Rerun tests that failed in the previous structured run during validation |
| `--changed` | Select tests related to changed files during validation |
| `--changed-from CHANGED_FROM` | Git base for --changed during validation |
| `--instrumented` | Run Android instrumentation tests during validation |
| `--validate-only` | Skip apply and only run validation |
| `--skip-restore` | Skip restore during validation |
| `--skip-build` | Skip build during validation |
| `--skip-test` | Skip test during validation |
| `--sequential` | Use a persistent Android/Gradle validation session |
| `--resume` | Resume the active validation session |
| `--restart-session` | Replace an existing non-transactional validation session |
| `--retry-failed` | Retry the cached failed phase and every dependent phase |
| `--next` | Run only the next pending phase (the default for `validate continue`) |
| `--phase PHASE` | Run a named phase and every still-pending prerequisite; repeat with --scope selected |
| `--phase-only PHASE_ONLY` | Run only one named pending phase without automatically including prerequisites |
| `--through THROUGH` | Run pending phases through this phase |
| `--complete` | Run every remaining phase |
| `--from-phase, --from FROM_PHASE` | Start this invocation at a named phase |
| `--pause-after PAUSE_AFTER` | Pause after this phase (alias for --through) |
| `--max-phases MAX_PHASES` | Maximum phase boundaries to cross in this invocation |
| `--max-runtime MAX_RUNTIME` | Stop before the next phase after this runtime budget, such as 15m or 1h |
| `--require-complete` | Return a failure code when validation pauses incomplete |
| `--stop-below-battery PERCENT` | Pause before a phase when battery is below this percentage |
| `--require-charging` | Pause before a phase unless the Android device is charging |
| `--stop-above-temperature CELSIUS` | Pause before a phase when device temperature exceeds this value |
| `--stop-below-disk-mb MIB` | Pause a running validation phase when repository free space falls below this threshold; 0 disables |
| `--stop-gradle-after-phase` | Stop Gradle daemons after each completed phase |
| `--keep-gradle-daemon` | Keep Gradle daemons available between phase invocations |
| `--profile VALIDATION_WORKFLOW_PROFILE` | Named validation workflow profile (minimal, default, android-full, release, termux, architecture-final, final-seal, or a repo-defined profile) |
| `--max-infrastructure-retries COUNT` | Automatically retry environment/tooling interruptions this many times per phase |
| `--max-resource-retries COUNT` | Automatically retry corroborated resource interruptions this many times per phase, using the recommended safe profile when available |
| `--no-auto-retry` | Disable automatic infrastructure and resource retries for this session |
| `--rollback-on-failure` | Immediately roll back an attached apply transaction when a validation phase fails |
| `--failure-action FAILURE_ACTION` | Action after a sequential validation failure; pause preserves the tree and rollback point Choices: pause, rollback, keep, prompt |
| `--keep-changes` | When aborting through `validate abort`, keep the applied repository changes |
| `--session SESSION` | Specific validation session ID |
| `--scope VALIDATION_RUN_SCOPE` | Stable validation run scope Choices: next, through, selected, remaining |
| `--if-revision IF_REVISION` | Apply the control request only when the session revision matches |
| `--after-phase` | For validate pause, finish the active phase before pausing |
| `--cancel` | For validate pause, cancel a pending pause request |
| `--rollback` | For validate abort, explicitly roll back the attached transaction |
| `--events` | Include durable events in validation status JSON |
| `--after-event AFTER_EVENT` | Return events after this event ID |
| `--event-limit, --limit EVENT_LIMIT` | Maximum event records to return |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
| `--max-workers COUNT` | Override validation worker count for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--cpu-limit COUNT` | Override validation CPU limit for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--gradle-heap-mb MIB` | Override the Gradle validation heap for this invocation; explicit CLI value wins over Android-safe/project phase defaults |
| `--gradle-metaspace-mb, --metaspace-guard-mb MIB` | Override the Gradle validation max metaspace; 0 disables Devtool's explicit MaxMetaspaceSize cap |
| `--no-metaspace-guard` | Do not set an explicit Gradle MaxMetaspaceSize for this validation invocation |
| `--memory-guard-mb MIB` | Override the validation free-memory guard for this invocation; 0 disables it and explicit CLI value wins over phase defaults |
| `--low-priority` | Run validation commands at low scheduler priority |
| `--normal-priority` | Do not lower validation process priority |
| `--parallel` | Enable Gradle parallel execution for validation |
| `--no-parallel` | Disable Gradle parallel execution for validation |
| `--build-cache` | Enable the Gradle build cache for validation |
| `--no-build-cache` | Disable the Gradle build cache for validation |
| `--daemon` | Allow the Gradle daemon during validation |
| `--no-daemon` | Disable the Gradle daemon during validation |
| `--workflow-parallel` | Run independent workflow DAG nodes concurrently only when dependency, read/write-scope, and resource checks prove it safe |
| `--no-workflow-parallel` | Force workflow DAG nodes to execute sequentially |
| `--workflow-max-workers COUNT` | Maximum concurrent safe workflow nodes; 0 uses workspace policy/automatic default |
| `--preview` | Preview archive changes without applying |
| `--diff` | Show a repo-relative unified diff of artifact changes |
| `--interactive` | Show preview and ask before applying |
| `--no-manifest-check` | Disable devtool artifact manifest validation |
| `--ignore-checksum` | Skip SHA-256 checksum validation while retaining all other artifact manifest checks |
| `--keep-artifact` | Keep the source artifact and sidecars after a successful apply (failures are always preserved by default) |
| `--delete-artifact-on-failure` | Explicitly delete the source artifact after a failed apply (default: preserve it for retry and diagnosis) |
| `--no-rollback-on-failure` | Keep successfully applied changes when post-apply validation or commands fail; atomic extraction/preflight failures still roll back for repository integrity |
| `--no-apply-notification` | Do not show the final apply notification/toast |
| `--commit COMMIT` | Commit with this message after apply and optional validation |
| `--commit-mode COMMIT_MODE` | Git result policy: new, amend, fixup, or none (default inferred from artifact/flags) Choices: new, amend, fixup, none |
| `--amend` | Safely amend HEAD after apply; alias for --commit-mode amend |
| `--no-commit` | Disable a commit requested by the artifact manifest; alias for --commit-mode none |
| `--fixup-target REV` | Create a fixup! commit targeting REV (defaults to HEAD in fixup mode) |
| `--reset-author` | With amend mode, reset commit author to the current Git identity |
| `--allow-merge-amend` | Allow amending a merge commit (refused by default) |
| `--allow-published-rewrite` | Allow amending HEAD when reachable from a protected upstream/remote ref; never pushes |
| `--preserve-dirty` | Explicitly request transactional preservation/restoration of staged, unstaged, untracked, symlink, mode, and index state |
| `--signing SIGNING` | Git SSH signing policy for the result commit; default comes from [git].signing Choices: off, if-configured, required |
| `--isolation ISOLATION` | Apply in an isolated Devtool-owned Git worktree when supported (default: worktree); use direct for legacy in-place mutation Choices: worktree, direct |
| `--no-artifact-script` | Skip embedded artifact validation scripts while keeping built-in validation |
| `--rebase` | Rebase a single overlay inventory against the current repository before applying |

### `devtool apply`

Apply an overlay, overlay bundle, or snapshot

**Subcommands:** `overlay`, `overlays`, `snapshot`

### `devtool apply overlay`

| Argument / option | Meaning |
| --- | --- |
| `ARTIFACT` | Accepted by this command. |
| `--validate` | Run validation after applying the artifact; required artifact validation is enabled automatically |
| `--format, --fix` | Autofix Python formatter output during lint/validation; default is check-only |
| `--defer-validate` | Apply now, preserve rollback state, and create a resumable validation session without running a phase |
| `--static-validate` | Apply artifact and run only artifact/static validation scripts without Gradle validation phases |
| `--no-validate` | Disable validation requested or required by the artifact manifest |
| `--test-profile TEST_PROFILE` | Saved test profile used by artifact validation |
| `--pytest-test NODEID` | Run exactly this pytest file/node ID; repeatable. Suppresses default/affected pytest targets unless --pytest-include-defaults is set. |
| `--pytest-marker EXPR` | Add a pytest -m marker expression; repeatable expressions are combined with AND. |
| `--pytest-k EXPR` | Apply a pytest -k expression in addition to explicit pytest tests/selectors. |
| `--pytest-arg ARG` | Pass one structured argument directly to pytest; repeatable (for example --pytest-arg=--maxfail=1). |
| `--pytest-include-defaults` | Keep configured/affected default pytest targets in addition to explicit --pytest-test selectors. |
| `--select SELECT` | Select tests by type, suite, tag, class, or name during validation; repeatable |
| `--id TEST_IDS` | Select an exact framework test ID during validation; repeatable |
| `--tag TAG` | Include tests with this tag/trait/annotation during validation; repeatable |
| `--exclude-tag EXCLUDE_TAG` | Exclude tests with this tag/trait/annotation during validation; repeatable |
| `--path TEST_PATHS` | Select tests below a source path or package during validation; repeatable |
| `--class, --tests TEST_CLASSES` | Select a test class during validation; repeatable |
| `--method TEST_METHODS` | Select a test method during validation; repeatable |
| `--filter TEST_FILTER` | Framework-native test filter used during validation |
| `--task, --test-task TEST_TASK` | Exclusive native validation unit list; repeatable. Android/Gradle values are Gradle tasks; Python/Node/Rust/etc. values are supported validation actions such as test, lint, build, package-check, or Python test targets. |
| `--failed` | Rerun tests that failed in the previous structured run during validation |
| `--changed` | Select tests related to changed files during validation |
| `--changed-from CHANGED_FROM` | Git base for --changed during validation |
| `--instrumented` | Run Android instrumentation tests during validation |
| `--validate-only` | Skip apply and only run validation |
| `--skip-restore` | Skip restore during validation |
| `--skip-build` | Skip build during validation |
| `--skip-test` | Skip test during validation |
| `--sequential` | Use a persistent Android/Gradle validation session |
| `--resume` | Resume the active validation session |
| `--restart-session` | Replace an existing non-transactional validation session |
| `--retry-failed` | Retry the cached failed phase and every dependent phase |
| `--next` | Run only the next pending phase (the default for `validate continue`) |
| `--phase PHASE` | Run a named phase and every still-pending prerequisite; repeat with --scope selected |
| `--phase-only PHASE_ONLY` | Run only one named pending phase without automatically including prerequisites |
| `--through THROUGH` | Run pending phases through this phase |
| `--complete` | Run every remaining phase |
| `--from-phase, --from FROM_PHASE` | Start this invocation at a named phase |
| `--pause-after PAUSE_AFTER` | Pause after this phase (alias for --through) |
| `--max-phases MAX_PHASES` | Maximum phase boundaries to cross in this invocation |
| `--max-runtime MAX_RUNTIME` | Stop before the next phase after this runtime budget, such as 15m or 1h |
| `--require-complete` | Return a failure code when validation pauses incomplete |
| `--stop-below-battery PERCENT` | Pause before a phase when battery is below this percentage |
| `--require-charging` | Pause before a phase unless the Android device is charging |
| `--stop-above-temperature CELSIUS` | Pause before a phase when device temperature exceeds this value |
| `--stop-below-disk-mb MIB` | Pause a running validation phase when repository free space falls below this threshold; 0 disables |
| `--stop-gradle-after-phase` | Stop Gradle daemons after each completed phase |
| `--keep-gradle-daemon` | Keep Gradle daemons available between phase invocations |
| `--profile VALIDATION_WORKFLOW_PROFILE` | Named validation workflow profile (minimal, default, android-full, release, termux, architecture-final, final-seal, or a repo-defined profile) |
| `--max-infrastructure-retries COUNT` | Automatically retry environment/tooling interruptions this many times per phase |
| `--max-resource-retries COUNT` | Automatically retry corroborated resource interruptions this many times per phase, using the recommended safe profile when available |
| `--no-auto-retry` | Disable automatic infrastructure and resource retries for this session |
| `--rollback-on-failure` | Immediately roll back an attached apply transaction when a validation phase fails |
| `--failure-action FAILURE_ACTION` | Action after a sequential validation failure; pause preserves the tree and rollback point Choices: pause, rollback, keep, prompt |
| `--keep-changes` | When aborting through `validate abort`, keep the applied repository changes |
| `--session SESSION` | Specific validation session ID |
| `--scope VALIDATION_RUN_SCOPE` | Stable validation run scope Choices: next, through, selected, remaining |
| `--if-revision IF_REVISION` | Apply the control request only when the session revision matches |
| `--after-phase` | For validate pause, finish the active phase before pausing |
| `--cancel` | For validate pause, cancel a pending pause request |
| `--rollback` | For validate abort, explicitly roll back the attached transaction |
| `--events` | Include durable events in validation status JSON |
| `--after-event AFTER_EVENT` | Return events after this event ID |
| `--event-limit, --limit EVENT_LIMIT` | Maximum event records to return |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
| `--max-workers COUNT` | Override validation worker count for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--cpu-limit COUNT` | Override validation CPU limit for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--gradle-heap-mb MIB` | Override the Gradle validation heap for this invocation; explicit CLI value wins over Android-safe/project phase defaults |
| `--gradle-metaspace-mb, --metaspace-guard-mb MIB` | Override the Gradle validation max metaspace; 0 disables Devtool's explicit MaxMetaspaceSize cap |
| `--no-metaspace-guard` | Do not set an explicit Gradle MaxMetaspaceSize for this validation invocation |
| `--memory-guard-mb MIB` | Override the validation free-memory guard for this invocation; 0 disables it and explicit CLI value wins over phase defaults |
| `--low-priority` | Run validation commands at low scheduler priority |
| `--normal-priority` | Do not lower validation process priority |
| `--parallel` | Enable Gradle parallel execution for validation |
| `--no-parallel` | Disable Gradle parallel execution for validation |
| `--build-cache` | Enable the Gradle build cache for validation |
| `--no-build-cache` | Disable the Gradle build cache for validation |
| `--daemon` | Allow the Gradle daemon during validation |
| `--no-daemon` | Disable the Gradle daemon during validation |
| `--workflow-parallel` | Run independent workflow DAG nodes concurrently only when dependency, read/write-scope, and resource checks prove it safe |
| `--no-workflow-parallel` | Force workflow DAG nodes to execute sequentially |
| `--workflow-max-workers COUNT` | Maximum concurrent safe workflow nodes; 0 uses workspace policy/automatic default |
| `--preview` | Preview archive changes without applying |
| `--diff` | Show a repo-relative unified diff of artifact changes |
| `--interactive` | Show preview and ask before applying |
| `--no-manifest-check` | Disable devtool artifact manifest validation |
| `--ignore-checksum` | Skip SHA-256 checksum validation while retaining all other artifact manifest checks |
| `--keep-artifact` | Keep the source artifact and sidecars after a successful apply (failures are always preserved by default) |
| `--delete-artifact-on-failure` | Explicitly delete the source artifact after a failed apply (default: preserve it for retry and diagnosis) |
| `--no-rollback-on-failure` | Keep successfully applied changes when post-apply validation or commands fail; atomic extraction/preflight failures still roll back for repository integrity |
| `--no-apply-notification` | Do not show the final apply notification/toast |
| `--commit COMMIT` | Commit with this message after apply and optional validation |
| `--commit-mode COMMIT_MODE` | Git result policy: new, amend, fixup, or none (default inferred from artifact/flags) Choices: new, amend, fixup, none |
| `--amend` | Safely amend HEAD after apply; alias for --commit-mode amend |
| `--no-commit` | Disable a commit requested by the artifact manifest; alias for --commit-mode none |
| `--fixup-target REV` | Create a fixup! commit targeting REV (defaults to HEAD in fixup mode) |
| `--reset-author` | With amend mode, reset commit author to the current Git identity |
| `--allow-merge-amend` | Allow amending a merge commit (refused by default) |
| `--allow-published-rewrite` | Allow amending HEAD when reachable from a protected upstream/remote ref; never pushes |
| `--preserve-dirty` | Explicitly request transactional preservation/restoration of staged, unstaged, untracked, symlink, mode, and index state |
| `--signing SIGNING` | Git SSH signing policy for the result commit; default comes from [git].signing Choices: off, if-configured, required |
| `--isolation ISOLATION` | Apply in an isolated Devtool-owned Git worktree when supported (default: worktree); use direct for legacy in-place mutation Choices: worktree, direct |
| `--no-artifact-script` | Skip embedded artifact validation scripts while keeping built-in validation |
| `--rebase` | Rebase a single overlay inventory against the current repository before applying |

### `devtool apply overlays`

| Argument / option | Meaning |
| --- | --- |
| `ARTIFACT...` | Overlay bundle archive, or multiple overlay archives to bundle/apply transactionally |
| `--validate` | Run validation after applying the artifact; required artifact validation is enabled automatically |
| `--format, --fix` | Autofix Python formatter output during lint/validation; default is check-only |
| `--defer-validate` | Apply now, preserve rollback state, and create a resumable validation session without running a phase |
| `--static-validate` | Apply artifact and run only artifact/static validation scripts without Gradle validation phases |
| `--no-validate` | Disable validation requested or required by the artifact manifest |
| `--test-profile TEST_PROFILE` | Saved test profile used by artifact validation |
| `--pytest-test NODEID` | Run exactly this pytest file/node ID; repeatable. Suppresses default/affected pytest targets unless --pytest-include-defaults is set. |
| `--pytest-marker EXPR` | Add a pytest -m marker expression; repeatable expressions are combined with AND. |
| `--pytest-k EXPR` | Apply a pytest -k expression in addition to explicit pytest tests/selectors. |
| `--pytest-arg ARG` | Pass one structured argument directly to pytest; repeatable (for example --pytest-arg=--maxfail=1). |
| `--pytest-include-defaults` | Keep configured/affected default pytest targets in addition to explicit --pytest-test selectors. |
| `--select SELECT` | Select tests by type, suite, tag, class, or name during validation; repeatable |
| `--id TEST_IDS` | Select an exact framework test ID during validation; repeatable |
| `--tag TAG` | Include tests with this tag/trait/annotation during validation; repeatable |
| `--exclude-tag EXCLUDE_TAG` | Exclude tests with this tag/trait/annotation during validation; repeatable |
| `--path TEST_PATHS` | Select tests below a source path or package during validation; repeatable |
| `--class, --tests TEST_CLASSES` | Select a test class during validation; repeatable |
| `--method TEST_METHODS` | Select a test method during validation; repeatable |
| `--filter TEST_FILTER` | Framework-native test filter used during validation |
| `--task, --test-task TEST_TASK` | Exclusive native validation unit list; repeatable. Android/Gradle values are Gradle tasks; Python/Node/Rust/etc. values are supported validation actions such as test, lint, build, package-check, or Python test targets. |
| `--failed` | Rerun tests that failed in the previous structured run during validation |
| `--changed` | Select tests related to changed files during validation |
| `--changed-from CHANGED_FROM` | Git base for --changed during validation |
| `--instrumented` | Run Android instrumentation tests during validation |
| `--validate-only` | Skip apply and only run validation |
| `--skip-restore` | Skip restore during validation |
| `--skip-build` | Skip build during validation |
| `--skip-test` | Skip test during validation |
| `--sequential` | Use a persistent Android/Gradle validation session |
| `--resume` | Resume the active validation session |
| `--restart-session` | Replace an existing non-transactional validation session |
| `--retry-failed` | Retry the cached failed phase and every dependent phase |
| `--next` | Run only the next pending phase (the default for `validate continue`) |
| `--phase PHASE` | Run a named phase and every still-pending prerequisite; repeat with --scope selected |
| `--phase-only PHASE_ONLY` | Run only one named pending phase without automatically including prerequisites |
| `--through THROUGH` | Run pending phases through this phase |
| `--complete` | Run every remaining phase |
| `--from-phase, --from FROM_PHASE` | Start this invocation at a named phase |
| `--pause-after PAUSE_AFTER` | Pause after this phase (alias for --through) |
| `--max-phases MAX_PHASES` | Maximum phase boundaries to cross in this invocation |
| `--max-runtime MAX_RUNTIME` | Stop before the next phase after this runtime budget, such as 15m or 1h |
| `--require-complete` | Return a failure code when validation pauses incomplete |
| `--stop-below-battery PERCENT` | Pause before a phase when battery is below this percentage |
| `--require-charging` | Pause before a phase unless the Android device is charging |
| `--stop-above-temperature CELSIUS` | Pause before a phase when device temperature exceeds this value |
| `--stop-below-disk-mb MIB` | Pause a running validation phase when repository free space falls below this threshold; 0 disables |
| `--stop-gradle-after-phase` | Stop Gradle daemons after each completed phase |
| `--keep-gradle-daemon` | Keep Gradle daemons available between phase invocations |
| `--profile VALIDATION_WORKFLOW_PROFILE` | Named validation workflow profile (minimal, default, android-full, release, termux, architecture-final, final-seal, or a repo-defined profile) |
| `--max-infrastructure-retries COUNT` | Automatically retry environment/tooling interruptions this many times per phase |
| `--max-resource-retries COUNT` | Automatically retry corroborated resource interruptions this many times per phase, using the recommended safe profile when available |
| `--no-auto-retry` | Disable automatic infrastructure and resource retries for this session |
| `--rollback-on-failure` | Immediately roll back an attached apply transaction when a validation phase fails |
| `--failure-action FAILURE_ACTION` | Action after a sequential validation failure; pause preserves the tree and rollback point Choices: pause, rollback, keep, prompt |
| `--keep-changes` | When aborting through `validate abort`, keep the applied repository changes |
| `--session SESSION` | Specific validation session ID |
| `--scope VALIDATION_RUN_SCOPE` | Stable validation run scope Choices: next, through, selected, remaining |
| `--if-revision IF_REVISION` | Apply the control request only when the session revision matches |
| `--after-phase` | For validate pause, finish the active phase before pausing |
| `--cancel` | For validate pause, cancel a pending pause request |
| `--rollback` | For validate abort, explicitly roll back the attached transaction |
| `--events` | Include durable events in validation status JSON |
| `--after-event AFTER_EVENT` | Return events after this event ID |
| `--event-limit, --limit EVENT_LIMIT` | Maximum event records to return |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
| `--max-workers COUNT` | Override validation worker count for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--cpu-limit COUNT` | Override validation CPU limit for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--gradle-heap-mb MIB` | Override the Gradle validation heap for this invocation; explicit CLI value wins over Android-safe/project phase defaults |
| `--gradle-metaspace-mb, --metaspace-guard-mb MIB` | Override the Gradle validation max metaspace; 0 disables Devtool's explicit MaxMetaspaceSize cap |
| `--no-metaspace-guard` | Do not set an explicit Gradle MaxMetaspaceSize for this validation invocation |
| `--memory-guard-mb MIB` | Override the validation free-memory guard for this invocation; 0 disables it and explicit CLI value wins over phase defaults |
| `--low-priority` | Run validation commands at low scheduler priority |
| `--normal-priority` | Do not lower validation process priority |
| `--parallel` | Enable Gradle parallel execution for validation |
| `--no-parallel` | Disable Gradle parallel execution for validation |
| `--build-cache` | Enable the Gradle build cache for validation |
| `--no-build-cache` | Disable the Gradle build cache for validation |
| `--daemon` | Allow the Gradle daemon during validation |
| `--no-daemon` | Disable the Gradle daemon during validation |
| `--workflow-parallel` | Run independent workflow DAG nodes concurrently only when dependency, read/write-scope, and resource checks prove it safe |
| `--no-workflow-parallel` | Force workflow DAG nodes to execute sequentially |
| `--workflow-max-workers COUNT` | Maximum concurrent safe workflow nodes; 0 uses workspace policy/automatic default |
| `--preview` | Preview archive changes without applying |
| `--diff` | Show a repo-relative unified diff of artifact changes |
| `--interactive` | Show preview and ask before applying |
| `--no-manifest-check` | Disable devtool artifact manifest validation |
| `--ignore-checksum` | Skip SHA-256 checksum validation while retaining all other artifact manifest checks |
| `--keep-artifact` | Keep the source artifact and sidecars after a successful apply (failures are always preserved by default) |
| `--delete-artifact-on-failure` | Explicitly delete the source artifact after a failed apply (default: preserve it for retry and diagnosis) |
| `--no-rollback-on-failure` | Keep successfully applied changes when post-apply validation or commands fail; atomic extraction/preflight failures still roll back for repository integrity |
| `--no-apply-notification` | Do not show the final apply notification/toast |
| `--commit COMMIT` | Commit with this message after apply and optional validation |
| `--commit-mode COMMIT_MODE` | Git result policy: new, amend, fixup, or none (default inferred from artifact/flags) Choices: new, amend, fixup, none |
| `--amend` | Safely amend HEAD after apply; alias for --commit-mode amend |
| `--no-commit` | Disable a commit requested by the artifact manifest; alias for --commit-mode none |
| `--fixup-target REV` | Create a fixup! commit targeting REV (defaults to HEAD in fixup mode) |
| `--reset-author` | With amend mode, reset commit author to the current Git identity |
| `--allow-merge-amend` | Allow amending a merge commit (refused by default) |
| `--allow-published-rewrite` | Allow amending HEAD when reachable from a protected upstream/remote ref; never pushes |
| `--preserve-dirty` | Explicitly request transactional preservation/restoration of staged, unstaged, untracked, symlink, mode, and index state |
| `--signing SIGNING` | Git SSH signing policy for the result commit; default comes from [git].signing Choices: off, if-configured, required |
| `--isolation ISOLATION` | Apply in an isolated Devtool-owned Git worktree when supported (default: worktree); use direct for legacy in-place mutation Choices: worktree, direct |
| `--no-artifact-script` | Skip embedded artifact validation scripts while keeping built-in validation |
| `--rebase` | Rebase a single overlay inventory against the current repository before applying |

### `devtool apply snapshot`

| Argument / option | Meaning |
| --- | --- |
| `ARTIFACT` | Accepted by this command. |
| `--validate` | Run validation after applying the artifact; required artifact validation is enabled automatically |
| `--format, --fix` | Autofix Python formatter output during lint/validation; default is check-only |
| `--defer-validate` | Apply now, preserve rollback state, and create a resumable validation session without running a phase |
| `--static-validate` | Apply artifact and run only artifact/static validation scripts without Gradle validation phases |
| `--no-validate` | Disable validation requested or required by the artifact manifest |
| `--test-profile TEST_PROFILE` | Saved test profile used by artifact validation |
| `--pytest-test NODEID` | Run exactly this pytest file/node ID; repeatable. Suppresses default/affected pytest targets unless --pytest-include-defaults is set. |
| `--pytest-marker EXPR` | Add a pytest -m marker expression; repeatable expressions are combined with AND. |
| `--pytest-k EXPR` | Apply a pytest -k expression in addition to explicit pytest tests/selectors. |
| `--pytest-arg ARG` | Pass one structured argument directly to pytest; repeatable (for example --pytest-arg=--maxfail=1). |
| `--pytest-include-defaults` | Keep configured/affected default pytest targets in addition to explicit --pytest-test selectors. |
| `--select SELECT` | Select tests by type, suite, tag, class, or name during validation; repeatable |
| `--id TEST_IDS` | Select an exact framework test ID during validation; repeatable |
| `--tag TAG` | Include tests with this tag/trait/annotation during validation; repeatable |
| `--exclude-tag EXCLUDE_TAG` | Exclude tests with this tag/trait/annotation during validation; repeatable |
| `--path TEST_PATHS` | Select tests below a source path or package during validation; repeatable |
| `--class, --tests TEST_CLASSES` | Select a test class during validation; repeatable |
| `--method TEST_METHODS` | Select a test method during validation; repeatable |
| `--filter TEST_FILTER` | Framework-native test filter used during validation |
| `--task, --test-task TEST_TASK` | Exclusive native validation unit list; repeatable. Android/Gradle values are Gradle tasks; Python/Node/Rust/etc. values are supported validation actions such as test, lint, build, package-check, or Python test targets. |
| `--failed` | Rerun tests that failed in the previous structured run during validation |
| `--changed` | Select tests related to changed files during validation |
| `--changed-from CHANGED_FROM` | Git base for --changed during validation |
| `--instrumented` | Run Android instrumentation tests during validation |
| `--validate-only` | Skip apply and only run validation |
| `--skip-restore` | Skip restore during validation |
| `--skip-build` | Skip build during validation |
| `--skip-test` | Skip test during validation |
| `--sequential` | Use a persistent Android/Gradle validation session |
| `--resume` | Resume the active validation session |
| `--restart-session` | Replace an existing non-transactional validation session |
| `--retry-failed` | Retry the cached failed phase and every dependent phase |
| `--next` | Run only the next pending phase (the default for `validate continue`) |
| `--phase PHASE` | Run a named phase and every still-pending prerequisite; repeat with --scope selected |
| `--phase-only PHASE_ONLY` | Run only one named pending phase without automatically including prerequisites |
| `--through THROUGH` | Run pending phases through this phase |
| `--complete` | Run every remaining phase |
| `--from-phase, --from FROM_PHASE` | Start this invocation at a named phase |
| `--pause-after PAUSE_AFTER` | Pause after this phase (alias for --through) |
| `--max-phases MAX_PHASES` | Maximum phase boundaries to cross in this invocation |
| `--max-runtime MAX_RUNTIME` | Stop before the next phase after this runtime budget, such as 15m or 1h |
| `--require-complete` | Return a failure code when validation pauses incomplete |
| `--stop-below-battery PERCENT` | Pause before a phase when battery is below this percentage |
| `--require-charging` | Pause before a phase unless the Android device is charging |
| `--stop-above-temperature CELSIUS` | Pause before a phase when device temperature exceeds this value |
| `--stop-below-disk-mb MIB` | Pause a running validation phase when repository free space falls below this threshold; 0 disables |
| `--stop-gradle-after-phase` | Stop Gradle daemons after each completed phase |
| `--keep-gradle-daemon` | Keep Gradle daemons available between phase invocations |
| `--profile VALIDATION_WORKFLOW_PROFILE` | Named validation workflow profile (minimal, default, android-full, release, termux, architecture-final, final-seal, or a repo-defined profile) |
| `--max-infrastructure-retries COUNT` | Automatically retry environment/tooling interruptions this many times per phase |
| `--max-resource-retries COUNT` | Automatically retry corroborated resource interruptions this many times per phase, using the recommended safe profile when available |
| `--no-auto-retry` | Disable automatic infrastructure and resource retries for this session |
| `--rollback-on-failure` | Immediately roll back an attached apply transaction when a validation phase fails |
| `--failure-action FAILURE_ACTION` | Action after a sequential validation failure; pause preserves the tree and rollback point Choices: pause, rollback, keep, prompt |
| `--keep-changes` | When aborting through `validate abort`, keep the applied repository changes |
| `--session SESSION` | Specific validation session ID |
| `--scope VALIDATION_RUN_SCOPE` | Stable validation run scope Choices: next, through, selected, remaining |
| `--if-revision IF_REVISION` | Apply the control request only when the session revision matches |
| `--after-phase` | For validate pause, finish the active phase before pausing |
| `--cancel` | For validate pause, cancel a pending pause request |
| `--rollback` | For validate abort, explicitly roll back the attached transaction |
| `--events` | Include durable events in validation status JSON |
| `--after-event AFTER_EVENT` | Return events after this event ID |
| `--event-limit, --limit EVENT_LIMIT` | Maximum event records to return |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
| `--max-workers COUNT` | Override validation worker count for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--cpu-limit COUNT` | Override validation CPU limit for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits |
| `--gradle-heap-mb MIB` | Override the Gradle validation heap for this invocation; explicit CLI value wins over Android-safe/project phase defaults |
| `--gradle-metaspace-mb, --metaspace-guard-mb MIB` | Override the Gradle validation max metaspace; 0 disables Devtool's explicit MaxMetaspaceSize cap |
| `--no-metaspace-guard` | Do not set an explicit Gradle MaxMetaspaceSize for this validation invocation |
| `--memory-guard-mb MIB` | Override the validation free-memory guard for this invocation; 0 disables it and explicit CLI value wins over phase defaults |
| `--low-priority` | Run validation commands at low scheduler priority |
| `--normal-priority` | Do not lower validation process priority |
| `--parallel` | Enable Gradle parallel execution for validation |
| `--no-parallel` | Disable Gradle parallel execution for validation |
| `--build-cache` | Enable the Gradle build cache for validation |
| `--no-build-cache` | Disable the Gradle build cache for validation |
| `--daemon` | Allow the Gradle daemon during validation |
| `--no-daemon` | Disable the Gradle daemon during validation |
| `--workflow-parallel` | Run independent workflow DAG nodes concurrently only when dependency, read/write-scope, and resource checks prove it safe |
| `--no-workflow-parallel` | Force workflow DAG nodes to execute sequentially |
| `--workflow-max-workers COUNT` | Maximum concurrent safe workflow nodes; 0 uses workspace policy/automatic default |
| `--preview` | Preview archive changes without applying |
| `--diff` | Show a repo-relative unified diff of artifact changes |
| `--interactive` | Show preview and ask before applying |
| `--no-manifest-check` | Disable devtool artifact manifest validation |
| `--ignore-checksum` | Skip SHA-256 checksum validation while retaining all other artifact manifest checks |
| `--keep-artifact` | Keep the source artifact and sidecars after a successful apply (failures are always preserved by default) |
| `--delete-artifact-on-failure` | Explicitly delete the source artifact after a failed apply (default: preserve it for retry and diagnosis) |
| `--no-rollback-on-failure` | Keep successfully applied changes when post-apply validation or commands fail; atomic extraction/preflight failures still roll back for repository integrity |
| `--no-apply-notification` | Do not show the final apply notification/toast |
| `--commit COMMIT` | Commit with this message after apply and optional validation |
| `--commit-mode COMMIT_MODE` | Git result policy: new, amend, fixup, or none (default inferred from artifact/flags) Choices: new, amend, fixup, none |
| `--amend` | Safely amend HEAD after apply; alias for --commit-mode amend |
| `--no-commit` | Disable a commit requested by the artifact manifest; alias for --commit-mode none |
| `--fixup-target REV` | Create a fixup! commit targeting REV (defaults to HEAD in fixup mode) |
| `--reset-author` | With amend mode, reset commit author to the current Git identity |
| `--allow-merge-amend` | Allow amending a merge commit (refused by default) |
| `--allow-published-rewrite` | Allow amending HEAD when reachable from a protected upstream/remote ref; never pushes |
| `--preserve-dirty` | Explicitly request transactional preservation/restoration of staged, unstaged, untracked, symlink, mode, and index state |
| `--signing SIGNING` | Git SSH signing policy for the result commit; default comes from [git].signing Choices: off, if-configured, required |
| `--isolation ISOLATION` | Apply in an isolated Devtool-owned Git worktree when supported (default: worktree); use direct for legacy in-place mutation Choices: worktree, direct |
| `--no-artifact-script` | Skip embedded artifact validation scripts while keeping built-in validation |
| `--rebase` | Rebase a single overlay inventory against the current repository before applying |

### `devtool validate-overlay`

**Aliases:** `devtool vo`

| Argument / option | Meaning |
| --- | --- |
| `ARTIFACT` | Accepted by this command. |
| `--fail-on-conflict` | For bundles, fail if multiple nested overlays write the same file |
| `--ignore-checksum` | Skip SHA-256 checksum validation while retaining all other artifact manifest checks |

### `devtool validate-overlays`

**Aliases:** `devtool vos`

| Argument / option | Meaning |
| --- | --- |
| `ARTIFACT...` | Overlay bundle archive, or multiple overlay archives to validate as a bundle |
| `--fail-on-conflict` | Fail if multiple nested overlays write the same file |
| `--ignore-checksum` | Skip SHA-256 checksum validation while retaining all other artifact manifest checks |

### `devtool overlay`

Inspect, preflight, and rebase overlay artifacts

**Subcommands:** `check`, `plan`, `rebase`, `recovery-plan`, `status`

### `devtool overlay check`

Preflight an overlay against the current repository

| Argument / option | Meaning |
| --- | --- |
| `ARTIFACT` | Accepted by this command. |
| `--diff` | Include a repository-relative unified diff |
| `--diff-context DIFF_CONTEXT` | Accepted by this command. |
| `--max-diff-lines MAX_DIFF_LINES` | Accepted by this command. |
| `--ignore-checksum` | Accepted by this command. |
| `--json` | Emit structured overlay preflight JSON |

### `devtool overlay plan`

Review overlay source, patch presence, and safe lifecycle next step (read-only)

| Argument / option | Meaning |
| --- | --- |
| `ARTIFACT` | Accepted by this command. |
| `--json` | Emit read-only overlay plan JSON |

### `devtool overlay rebase`

Three-way rebase an overlay against the current repository

| Argument / option | Meaning |
| --- | --- |
| `ARTIFACT` | Accepted by this command. |
| `-o, --output OUTPUT` | Output archive; defaults to <name>.rebased.<ext> |
| `--json` | Emit structured rebase JSON |

### `devtool overlay recovery-plan`

Read-only transaction rollback and LIFE cleanup recovery evidence

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit read-only recovery evidence JSON |
| `--transaction TRANSACTION` | Inspect only this exact active transaction ID |
| `--limit LIMIT` | Maximum recent RUN-V3 LIFE failures to show (1-50) |

### `devtool overlay status`

Classify overlay patch equivalence against the current repository

| Argument / option | Meaning |
| --- | --- |
| `ARTIFACT` | Accepted by this command. |
| `--json` | Emit structured patch-equivalence JSON |

### `devtool plugin`

Discover and diagnose devtool plugins

**Aliases:** `devtool plugins`

**Subcommands:** `list`, `doctor`

### `devtool plugin list`

List built-in and entry-point plugins

**Aliases:** `devtool plugin ls`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit plugin discovery JSON |

### `devtool plugin doctor`

Run plugin-specific health checks

| Argument / option | Meaning |
| --- | --- |
| `NAME` | Accepted by this command. |
| `--json` | Emit plugin doctor JSON |

### `devtool inspect-artifact`

Print machine-readable artifact, bundle, inventory, validation, and diff metadata

**Aliases:** `devtool ia`

| Argument / option | Meaning |
| --- | --- |
| `ARTIFACT` | Accepted by this command. |
| `--diff` | Include a repository-relative unified diff |
| `--diff-context DIFF_CONTEXT` | Accepted by this command. |
| `--max-diff-lines MAX_DIFF_LINES` | Accepted by this command. |

### `devtool bundle`

**Subcommands:** `overlays`

### `devtool bundle overlays`

| Argument / option | Meaning |
| --- | --- |
| `OVERLAYS...` | Accepted by this command. |
| `--name NAME` | Accepted by this command. |
| `--description DESCRIPTION` | Accepted by this command. |
| `--targets TARGETS` | Comma-separated targets aligned with the overlay arguments; use '-' to infer |
| `--repository-name REPOSITORY_NAME` | Repository identity stored in the bundle manifest |
| `--repository-remote REPOSITORY_REMOTE` | Optional expected Git remote |
| `--repository-branch REPOSITORY_BRANCH` | Optional expected Git branch |
| `--commit-message COMMIT_MESSAGE` | Embed one final atomic bundle commit message |
| `--validate` | Require validation when the bundle is applied |
| `--validation-strategy VALIDATION_STRATEGY` | Choices: per-overlay, after-all, both |
| `--validation-script VALIDATION_SCRIPT` | Embed a disposable bundle validation script |
| `--validation-runner VALIDATION_RUNNER` | Choices: bash, sh, python3 |
| `--validation-timeout VALIDATION_TIMEOUT` | Embedded validation script timeout in seconds |
| `--validation-task, --task VALIDATION_TASKS` | Gradle task to write into bundle validation.tasks; repeatable |
| `--validation-tasks VALIDATION_TASKS_CSV` | Comma-separated Gradle tasks to write into bundle validation.tasks; pass an empty value to declare an explicit empty task list |
| `-o, --out OUT` | Output bundle archive; extension selects format Required. |

### `devtool artifact`

Inspect and maintain the produced build-artifact registry

**Subcommands:** `list`, `inspect`, `verify`, `prune`

### `devtool artifact list`

List registered build artifacts

**Aliases:** `devtool artifact ls`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit registry JSON |

### `devtool artifact inspect`

Inspect a registered build artifact

**Aliases:** `devtool artifact show`

| Argument / option | Meaning |
| --- | --- |
| `SELECTOR` | Artifact id, path, or filename |
| `--json` | Emit artifact JSON |

### `devtool artifact verify`

Verify existence and SHA-256 of a registered artifact

| Argument / option | Meaning |
| --- | --- |
| `SELECTOR` | Artifact id, path, or filename |
| `--json` | Emit verification JSON |

### `devtool artifact prune`

Prune old registry entries

| Argument / option | Meaning |
| --- | --- |
| `--older-than OLDER_THAN` | Age threshold such as 30d, 12h, or an integer day count |
| `--delete-files` | Also delete matching files inside .devtool/build-artifacts only |
| `--json` | Emit prune result JSON |

### `devtool delivery`

Resolve and qualify canonical delivery artifacts

**Subcommands:** `plan`

### `devtool delivery plan`

Resolve one artifact into the canonical LIFE delivery plan

| Argument / option | Meaning |
| --- | --- |
| `ARTIFACT` | Accepted by this command. |
| `--public-runner` | Enforce public-runner fail-closed assurance |
| `--isolation ISOLATION` | Choices: worktree, direct, none |
| `--unsafe-authorize CODE[:REASON]` | Accepted by this command. |
| `--json` | Emit canonical plan JSON |

### `devtool repo-pack`

Plan and atomically publish canonical repository packs

**Subcommands:** `plan`, `create`, `list`, `status`, `pin`, `unpin`, `prune`

### `devtool repo-pack plan`

Plan a repository pack without writing it

| Argument / option | Meaning |
| --- | --- |
| `REPOSITORIES...` | Repository roots; defaults to --repo/current repository |
| `-o, --output OUTPUT` | Required. |
| `--mode MODE` | Choices: worktree, tracked, commit, handoff |
| `--compression COMPRESSION` | Choices: tar.gz, tar.zst, tar, zip |
| `--commit COMMIT` | Accepted by this command. |
| `--git-bundle` | Accepted by this command. |
| `--pin` | Accepted by this command. |
| `--retention RETENTION` | Accepted by this command. |
| `--json` | Accepted by this command. |

### `devtool repo-pack create`

Create, verify, and atomically publish a repository pack

| Argument / option | Meaning |
| --- | --- |
| `REPOSITORIES...` | Repository roots; defaults to --repo/current repository |
| `-o, --output OUTPUT` | Required. |
| `--mode MODE` | Choices: worktree, tracked, commit, handoff |
| `--compression COMPRESSION` | Choices: tar.gz, tar.zst, tar, zip |
| `--commit COMMIT` | Accepted by this command. |
| `--git-bundle` | Accepted by this command. |
| `--pin` | Accepted by this command. |
| `--retention RETENTION` | Accepted by this command. |
| `--json` | Accepted by this command. |

### `devtool repo-pack list`

List durable repository-pack lifecycle records

**Aliases:** `devtool repo-pack ls`

| Argument / option | Meaning |
| --- | --- |
| `--all` | include tombstoned packs |
| `--json` | Accepted by this command. |

### `devtool repo-pack status`

Verify repository-pack bytes and retention state

**Aliases:** `devtool repo-pack show`

| Argument / option | Meaning |
| --- | --- |
| `SELECTOR` | pack id, receipt id, or output path |
| `--json` | Accepted by this command. |

### `devtool repo-pack pin`

Pin a repository pack against lifecycle GC

| Argument / option | Meaning |
| --- | --- |
| `SELECTOR` | Accepted by this command. |
| `--json` | Accepted by this command. |

### `devtool repo-pack unpin`

Release an explicit repository-pack pin

| Argument / option | Meaning |
| --- | --- |
| `SELECTOR` | Accepted by this command. |
| `--json` | Accepted by this command. |

### `devtool repo-pack prune`

Garbage-collect unpinned policy packs through LIFE cleanup

| Argument / option | Meaning |
| --- | --- |
| `--older-than-days OLDER_THAN_DAYS` | minimum pack age in days |
| `--dry-run` | Accepted by this command. |
| `--json` | Accepted by this command. |

### `devtool signing-identity`

Manage private non-secret signing identity references

**Subcommands:** `list`, `register`

### `devtool signing-identity list`

**Aliases:** `devtool signing-identity ls`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Accepted by this command. |

### `devtool signing-identity register`

| Argument / option | Meaning |
| --- | --- |
| `IDENTITY_ID` | Accepted by this command. |
| `--provider PROVIDER` | Required. |
| `--key-reference KEY_REFERENCE` | Required. |
| `--alias ALIAS` | Accepted by this command. |
| `--certificate-sha256 CERTIFICATE_SHA256` | Accepted by this command. |
| `--purpose PURPOSE` | Accepted by this command. |
| `--variant VARIANT` | Accepted by this command. |
| `--secret-provider SECRET_PROVIDER` | Choices: local, keyring, private-file, fd, stdin, external-command |
| `--secret-reference SECRET_REFERENCE` | Accepted by this command. |
| `--json` | Accepted by this command. |

### `devtool tui`

Open the responsive terminal dashboard

| Argument / option | Meaning |
| --- | --- |
| `--page PAGE` | Initial Textual control-center page (legacy aliases remain accepted) Choices: dashboard, runs, logs, diagnostics, history, targets, workers, validation, artifacts, overlays, evidence, build, run, log, diagnostic, worker, ssh, artifact, overlay |
| `--artifact ARTIFACT` | Overlay artifact to preflight on the overlays page |
| `--once` | Render one frame and exit |
| `--width WIDTH` | Override terminal width for rendering/debugging |
| `--backend BACKEND` | Textual dashboard backend (auto or textual) Choices: auto, textual |
| `--require-textual` | Fail instead of falling back when Textual cannot be launched |
| `--no-tui-fallback` | Require Textual; the removed Rich dashboard has no fallback |
| `--explain-backend` | Print the resolved TUI backend selection and exit |

### `devtool cleanup`

Archive historical repository backups and optionally prune old backup history

| Argument / option | Meaning |
| --- | --- |
| `--dry-run` | Show what would be archived or pruned without changing files |
| `--prune-history-days DAYS` | Delete archived backup batches older than this many days; 0 keeps all history |

### `devtool batch`

| Argument / option | Meaning |
| --- | --- |
| `STEPS...` | Steps like validate or apply-overlay=/path.zip |

### `devtool init-config`

**Aliases:** `devtool init`

| Argument / option | Meaning |
| --- | --- |
| `[REPO_ROOT]` | Accepted by this command. |
| `--force` | Accepted by this command. |

### `devtool logs`

**Subcommands:** `latest`, `list`, `open`, `tail`

| Argument / option | Meaning |
| --- | --- |
| `--print` | Print the latest log; kept for compatibility |

### `devtool logs latest`

| Argument / option | Meaning |
| --- | --- |
| `--print` | Accepted by this command. |

### `devtool logs list`

**Aliases:** `devtool logs ls`

| Argument / option | Meaning |
| --- | --- |
| `--limit LIMIT` | Accepted by this command. |

### `devtool logs open`

**Aliases:** `devtool logs show`

| Argument / option | Meaning |
| --- | --- |
| `[SELECTOR]` | Log path, 'latest', or empty for interactive selection |
| `--pager PAGER` | Choices: auto, bat, cat |
| `--fzf` | Accepted by this command. |
| `--no-fzf` | Accepted by this command. |

### `devtool logs tail`

**Aliases:** `devtool logs follow`

| Argument / option | Meaning |
| --- | --- |
| `--lines LINES` | Accepted by this command. |
| `--no-follow` | Print the selected active log tail and exit |
| `--fzf` | Accepted by this command. |
| `--no-fzf` | Accepted by this command. |

### `devtool runs`

Query canonical CORE RUN V3 history

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable run query results |
| `--root ROOT` | Override RUN V3 catalog root |
| `--run-id RUN_ID` | Filter by exact run ID |
| `--command-id COMMAND_ID` | Filter by canonical command ID |
| `--status STATUS` | Filter by projected run status |
| `--provider PROVIDER` | Filter by execution provider |
| `--name NAME` | Case-insensitive run name/label filter |
| `--attachment-kind ATTACHMENT_KIND` | Require an available attachment kind |
| `--include-tombstoned` | Include tombstoned attachment metadata |
| `--offset OFFSET` | Pagination offset |
| `--limit LIMIT` | Pagination limit (1..1000) |
| `--storage-status` | Show canonical storage/ownership/doctor status |
| `--migration-plan` | Show read-only legacy run/log migration classification |
| `--migrate-legacy` | Explicitly adopt readable legacy run/log/history files into RUN V3 metadata without deleting source bytes |

### `devtool tag`

| Argument / option | Meaning |
| --- | --- |
| `NAME` | Accepted by this command. |
| `-m, --message MESSAGE` | Accepted by this command. |

### `devtool release-notes`

| Argument / option | Meaning |
| --- | --- |
| `--from-ref FROM_REF` | Accepted by this command. |
| `--to-ref TO_REF` | Accepted by this command. |

### `devtool completion`

| Argument / option | Meaning |
| --- | --- |
| `SHELL` | Choices: zsh, bash, fish |
| `--canonical` | Generate completion from the CORE command registry instead of the legacy checked-in Zsh projection |

### `devtool completion-state`

Emit config-aware values for shell/editor completion

| Argument / option | Meaning |
| --- | --- |
| `--kind KIND` | Choices: targets, groups, ui-profiles, test-profiles, variants, jobs, target-jobs, target-job-keys, target-job-refs, workflows, wrapper-commands, wrapper-aliases, wrapper-options, pytest-tests, all |
| `--format FORMAT` | Choices: lines, json |

### `devtool wrapper`

Resolve, describe, and execute repository wrapper commands

**Subcommands:** `describe`, `help`, `discover`, `resolve`, `exec`, `init`, `update`, `verify`, `status`, `migrate`, `seal`

### `devtool wrapper describe`

Describe the repository wrapper contract

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit the wrapper contract as JSON |

### `devtool wrapper help`

Show repository wrapper help

| Argument / option | Meaning |
| --- | --- |
| `[COMMAND_NAME]` | Optional wrapper command to describe |
| `--json` | Emit wrapper discovery metadata as JSON |

### `devtool wrapper discover`

Discover wrapper commands, launchers, and docs

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit wrapper discovery metadata as JSON |

### `devtool wrapper resolve`

Resolve wrapper argv without executing it

**Aliases:** `devtool wrapper plan`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit the resolved command as JSON |
| `WRAPPER_ARGS` | Repository wrapper argv to resolve |

### `devtool wrapper exec`

Resolve wrapper argv and execute the canonical Devtool command

| Argument / option | Meaning |
| --- | --- |
| `WRAPPER_ARGS` | Repository wrapper argv to execute |

### `devtool wrapper init`

Create repository devtoolw launchers

| Argument / option | Meaning |
| --- | --- |
| `--force` | Explicitly replace drifted or unmanaged launcher files |
| `--json` | Emit launcher status as JSON |

### `devtool wrapper update`

Refresh repository devtoolw launchers

| Argument / option | Meaning |
| --- | --- |
| `--force` | Explicitly replace unmanaged launcher files |
| `--json` | Emit launcher status as JSON |

### `devtool wrapper verify`

Verify repository devtoolw launchers are current

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit launcher status as JSON |

### `devtool wrapper status`

Show repository devtoolw launcher status

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit launcher status as JSON |

### `devtool wrapper migrate`

Migrate a schema-1 repository wrapper contract to canonical schema 2

| Argument / option | Meaning |
| --- | --- |
| `--check` | Report whether wrapper schema migration is needed without changing files |
| `--write` | Atomically update only the [wrapper] schema declaration |
| `--json` | Emit secret-safe migration metadata as JSON |

### `devtool wrapper seal`

Run wrapper cross-surface final seal checks

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit wrapper final seal as JSON |

### `devtool compatibility`

Audit retained legacy interfaces and migration paths

**Aliases:** `devtool compat`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit the versioned compatibility report as JSON |
| `--active-only` | Show only compatibility entries active in the selected config |
| `--category CATEGORY` | Filter compatibility entries by category Choices: config, cli, environment, python-api, machine, ipc, tasker, android, artifact, validation, state, workflow |
| `--strict` | Exit non-zero when the selected config still needs migration |
| `--global` | Audit the user global config instead of the repository config |

### `devtool final-seal`

Run the FS03 repository-wide final release seal audit

**Aliases:** `devtool seal`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit the versioned FS03 seal report as JSON |
| `--strict` | Exit non-zero when any final-seal check fails |
| `--target TARGET` | Emit the final seal for one configured target instead of the whole repository |

### `devtool pytest`

Inspect and profile Devtool's pytest suite

**Subcommands:** `profile`, `inventory`, `select`

### `devtool pytest profile`

Run pytest with Devtool slow-test reporting enabled

| Argument / option | Meaning |
| --- | --- |
| `PYTEST_ARGS...` | Optional pytest files, node IDs, or arguments; defaults to tests |
| `--threshold THRESHOLD` | Slow-test threshold in seconds |
| `--top TOP` | Maximum number of slow entries to show |
| `--strict` | Fail when unmarked tests exceed the slow threshold |
| `--json` | Emit a structured pytest-profile report |

### `devtool pytest inventory`

Inspect stable Devtool test/check identity and lane/resource policy

| Argument / option | Meaning |
| --- | --- |
| `--lane LANE` | Filter inventory by semantic lane Choices: fast, integration, external, system, full |
| `--group GROUP` | Filter by stable check group or alias |
| `--explain CHECK` | Explain one durable check ID or historical alias |
| `--json` | Emit canonical inventory JSON |

### `devtool pytest select`

Resolve an immutable deterministic test selection

| Argument / option | Meaning |
| --- | --- |
| `--lane LANE` | Select a semantic lane; repeatable Choices: fast, integration, external, system, full |
| `--check CHECK` | Select a durable check ID or alias; repeatable |
| `--group GROUP` | Select a stable check group or alias; repeatable |
| `--shards COUNT` | Produce a deterministic immutable shard plan |
| `--qualify` | Qualify exact required tools/capabilities and the declared test environment |
| `--json` | Emit canonical selection JSON |

### `devtool audit`

Record and compare federation-aware source audit passes

**Subcommands:** `record`, `delta`, `status`

### `devtool audit record`

Persist one campaign audit pass

| Argument / option | Meaning |
| --- | --- |
| `--campaign CAMPAIGN` | Campaign identifier Required. |
| `--roadmap ROADMAP` | Inspected roadmap range, for example GIT-FRX-01..17 |
| `--evidence EVIDENCE` | Evidence file to hash into the audit pass; repeatable |
| `--gaps GAPS` | Material gap count for this audit pass |
| `--pass-id PASS_ID` | Optional stable audit pass identifier |
| `--json` | Emit machine-readable audit pass |

### `devtool audit delta`

Compare two persisted audit passes

| Argument / option | Meaning |
| --- | --- |
| `OLD_PASS` | Accepted by this command. |
| `NEW_PASS` | Accepted by this command. |
| `--campaign CAMPAIGN` | Disambiguate pass identifiers within a campaign |
| `--json` | Emit machine-readable audit delta |

### `devtool audit status`

Show audit freshness for a campaign

| Argument / option | Meaning |
| --- | --- |
| `--campaign CAMPAIGN` | Campaign identifier Required. |
| `--json` | Emit machine-readable audit status |

### `devtool git`

Inspect Devtool's authoritative Git repository state

**Subcommands:** `state`, `doctor`, `rerere`, `bisect`, `notes`, `campaign`

### `devtool git state`

Show the authoritative repository Git-state snapshot

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable Git state |

### `devtool git doctor`

Diagnose Git/Devtool repository state

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable Git doctor evidence |
| `--repair-safe` | Apply only conservative, non-source-mutating repairs |

### `devtool git rerere`

Inspect Devtool-owned scoped conflict memory

**Subcommands:** `suggest`, `capture`, `record`, `apply`

### `devtool git rerere suggest`

Suggest an exact repository/campaign conflict resolution

| Argument / option | Meaning |
| --- | --- |
| `--campaign CAMPAIGN` | Campaign context for conflict memory |
| `--json` | Accepted by this command. |

### `devtool git rerere capture`

Capture the exact unresolved conflict preimage before manual resolution

| Argument / option | Meaning |
| --- | --- |
| `--campaign CAMPAIGN` | Campaign context for conflict memory |
| `--json` | Accepted by this command. |

### `devtool git rerere record`

Record a validated manual resolution for the captured exact conflict

| Argument / option | Meaning |
| --- | --- |
| `--campaign CAMPAIGN` | Campaign context for conflict memory |
| `--path RERERE_PATHS` | Resolved conflict path; repeatable Required. |
| `--test RERERE_TESTS` | Repository-declared Devtool test ID used to validate the resolution; repeatable Required. |
| `--json` | Accepted by this command. |

### `devtool git rerere apply`

Apply an exact stored resolution according to the configured rerere policy and rerun validation

| Argument / option | Meaning |
| --- | --- |
| `--campaign CAMPAIGN` | Campaign context for conflict memory |
| `--mode MODE` | Override [git].rerere for this invocation Choices: off, suggest, validated-auto |
| `--test RERERE_TESTS` | Repository-declared Devtool test ID rerun after reuse; required for validated-auto |
| `--json` | Accepted by this command. |

### `devtool git bisect`

Find the first bad revision using Devtool-managed detached worktrees

| Argument / option | Meaning |
| --- | --- |
| `--good GOOD` | Known-good Git revision Required. |
| `--bad BAD` | Known-bad Git revision Required. |
| `--task TASK` | Devtool workflow/test/action to evaluate for each candidate Required. |
| `--json` | Emit machine-readable managed-bisect evidence |

### `devtool git notes`

Inspect Devtool execution evidence stored in Git notes

**Subcommands:** `show`

### `devtool git notes show`

Show refs/notes/devtool execution evidence for a commit

| Argument / option | Meaning |
| --- | --- |
| `[REVISION]` | Commit/revision to inspect (default: HEAD) |
| `--json` | Emit machine-readable execution-note evidence |

### `devtool git campaign`

Manage multi-repository Git campaign generations

**Subcommands:** `start`, `checkpoint`, `rollback`, `supersede`, `range-diff`, `status`, `restore-dirty`, `seal`

### `devtool git campaign start`

Start a frozen multi-repository campaign generation

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `--workflow WORKFLOW` | Federation workflow to campaign |
| `--group GROUP` | Executable federation group to campaign |
| `--json` | Accepted by this command. |

### `devtool git campaign checkpoint`

Record the next campaign generation

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `[STEP]` | Roadmap/campaign step name recorded for this generation |
| `--json` | Accepted by this command. |

### `devtool git campaign rollback`

Move only Devtool private campaign tip refs back to a prior generation/step

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `SELECTOR` | Generation number or checkpoint step name |
| `--json` | Accepted by this command. |

### `devtool git campaign supersede`

Mark one campaign generation superseded by another and move private campaign tips

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `OLD_GENERATION` | Accepted by this command. |
| `NEW_GENERATION` | Accepted by this command. |
| `--json` | Accepted by this command. |

### `devtool git campaign range-diff`

Compare two campaign generations with git range-diff

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `--old OLD_GENERATION` | Required. |
| `--new NEW_GENERATION` | Required. |
| `--json` | Accepted by this command. |

### `devtool git campaign status`

Show persisted campaign generations and identities

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `--json` | Accepted by this command. |

### `devtool git campaign restore-dirty`

Restore GIT-FRX-13 dirty-tree state captured at campaign start

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `--repository REPOSITORY` | Restore only one enrolled repository |
| `--json` | Accepted by this command. |

### `devtool git campaign seal`

Create and verify the GIT-FRX-18 federated provenance seal

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `--signing SIGNING` | Override [git].signing for the final campaign tag Choices: off, if-configured, required |
| `--tag` | Create/update the optional devtool/<campaign>/<generation> annotated tag in the control repository |
| `--verify` | Verify an existing final seal without creating a new one |
| `--json` | Accepted by this command. |

### `devtool campaign`

Manage multi-repository Git campaign generations (compatibility surface for git campaign)

**Subcommands:** `status`, `checkpoint`, `rollback`, `range-diff`, `supersede`

### `devtool campaign status`

Show persisted campaign generations and identities

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `--json` | Accepted by this command. |

### `devtool campaign checkpoint`

Record a named campaign checkpoint generation

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `STEP` | Accepted by this command. |
| `--json` | Accepted by this command. |

### `devtool campaign rollback`

Roll Devtool private campaign tips back to a generation/step without mutating user branches

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `SELECTOR` | Accepted by this command. |
| `--json` | Accepted by this command. |

### `devtool campaign range-diff`

Compare two campaign generations

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `OLD_GENERATION` | Accepted by this command. |
| `NEW_GENERATION` | Accepted by this command. |
| `--json` | Accepted by this command. |

### `devtool campaign supersede`

Mark one generation superseded by another

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `OLD_GENERATION` | Accepted by this command. |
| `NEW_GENERATION` | Accepted by this command. |
| `--json` | Accepted by this command. |

### `devtool federation`

Inspect explicitly enrolled federated repositories

**Aliases:** `devtool fed`

**Subcommands:** `list`, `inspect`, `preflight`, `gate2`, `final-seal`, `doctor`, `status`, `capabilities`, `diff`, `artifacts`, `logs`, `failures`, `interfaces`, `conformance`, `affected`, `why`, `dag-contract`, `plan`, `lock`, `bisect`, `run`, `resume`, `retry`, `recover`, `rollback`, `integrate`, `bootstrap`, `sync`, `exec`

### `devtool federation list`

List enrolled repositories and resolved ownership boundaries

**Aliases:** `devtool federation ls`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable federation discovery state |

### `devtool federation inspect`

Inspect one enrolled repository and its child-owned resolution

**Aliases:** `devtool federation show`

| Argument / option | Meaning |
| --- | --- |
| `REPOSITORY` | Federation repository name |
| `--json` | Emit machine-readable repository boundary state |

### `devtool federation preflight`

Run the FRX-04 repository identity and boundary validation gate

**Aliases:** `devtool federation gate1`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit the machine-readable FRX-04 gate report |

### `devtool federation gate2`

Run the FRX-08 cross-repository execution and contract validation gate

**Aliases:** `devtool federation execution-preflight`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit the machine-readable FRX-08 gate report |

### `devtool federation final-seal`

Run the FRX-12 federation final release qualification

**Aliases:** `devtool federation seal`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit the machine-readable FRX-12 final seal report |

### `devtool federation doctor`

Diagnose federation Git lineage, refs, and participating repositories

| Argument / option | Meaning |
| --- | --- |
| `[SUBJECT]` | Optional run or campaign identifier |
| `--json` | Emit machine-readable federation Git doctor evidence |

### `devtool federation status`

Show one coherent federation observability snapshot

**Aliases:** `devtool federation summary`

| Argument / option | Meaning |
| --- | --- |
| `--run RUN` | Limit run observations to one parent or child EXO run ID |
| `--json` | Emit the machine-readable federation observability snapshot |

### `devtool federation capabilities`

Show the declared repository capability/dependency graph

**Aliases:** `devtool federation graph`

| Argument / option | Meaning |
| --- | --- |
| `--group GROUP` | Limit graph projection to one federation group |
| `--json` | Emit the machine-readable capability graph |

### `devtool federation diff`

Compare two persisted federation parent-run checkpoints

| Argument / option | Meaning |
| --- | --- |
| `OLD_RUN` | Accepted by this command. |
| `NEW_RUN` | Accepted by this command. |
| `--json` | Accepted by this command. |

### `devtool federation artifacts`

Show producer/consumer artifact evidence for one federation run

| Argument / option | Meaning |
| --- | --- |
| `RUN_ID` | Accepted by this command. |
| `--json` | Accepted by this command. |

### `devtool federation logs`

Show canonical execution/evidence log paths for one federation run

| Argument / option | Meaning |
| --- | --- |
| `RUN_ID` | Accepted by this command. |
| `--json` | Accepted by this command. |

### `devtool federation failures`

Show failed/cancelled/blocked child evidence for one federation run

| Argument / option | Meaning |
| --- | --- |
| `RUN_ID` | Accepted by this command. |
| `--json` | Accepted by this command. |

### `devtool federation interfaces`

Show declared federation interface/conformance contracts

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit the machine-readable interface catalog |

### `devtool federation conformance`

Check child repositories against a federation interface contract

**Aliases:** `devtool federation interface-check`

| Argument / option | Meaning |
| --- | --- |
| `--interface INTERFACE` | Check all groups bound to this interface |
| `--group GROUP` | Check the interface bound to one federation group |
| `--json` | Emit machine-readable conformance evidence |

### `devtool federation affected`

Compute dependency-aware affected federation repositories/workflows/groups

| Argument / option | Meaning |
| --- | --- |
| `--since SINCE` | Detect direct repository changes since this Git revision |
| `--repository REPOSITORY` | Seed one directly changed federation repository; repeatable |
| `--json` | Emit machine-readable impact/reason evidence |

### `devtool federation why`

Explain why a federation repository/node/workflow/group is affected

| Argument / option | Meaning |
| --- | --- |
| `SUBJECT` | Repository, repository::node, workflow, or group to explain |
| `--since SINCE` | Detect direct repository changes since this Git revision |
| `--repository REPOSITORY` | Seed one directly changed federation repository; repeatable |
| `--json` | Emit machine-readable reason evidence |

### `devtool federation dag-contract`

Read-only cross-repository producer/consumer DAG contract

| Argument / option | Meaning |
| --- | --- |
| `[WORKFLOW]` | Federation workflow name |
| `--group GROUP` | Executable federation group from [federation.groups] |
| `--json` | Emit the machine-readable federated plan graph |
| `--require-frozen` | Require a matching persisted strict-local federation source lock |

### `devtool federation plan`

Build a deterministic planning-only cross-repository workflow/group DAG

| Argument / option | Meaning |
| --- | --- |
| `[WORKFLOW]` | Federation workflow name |
| `--group GROUP` | Executable federation group from [federation.groups] |
| `--freeze` | Persist the plan's frozen multi-repository source lock |
| `--drift-policy DRIFT_POLICY` | Drift policy recorded with --freeze (default: fail) Choices: fail, replan, allow-if-equivalent |
| `--json` | Emit the machine-readable federated plan graph |

### `devtool federation lock`

Inspect or explicitly update the persisted frozen federation source lock

| Argument / option | Meaning |
| --- | --- |
| `--update REPOSITORY` | Refresh exactly one participating repository in the persisted lock |
| `--json` | Emit the machine-readable lock and verification state |

### `devtool federation bisect`

Bisect one federation repository while every other participant remains pinned

| Argument / option | Meaning |
| --- | --- |
| `--repo NAME=GOOD..BAD` | Vary exactly one repository over GOOD..BAD Required. |
| `--pin NAME=SHA` | Override one non-varying repository pin; repeatable |
| `--task TASK` | Federated workflow name, workflow:NAME, or group:NAME Required. |
| `--json` | Emit machine-readable candidate combinations and results |

### `devtool federation run`

Execute a federated workflow or group with distinct parent/child EXO runs

| Argument / option | Meaning |
| --- | --- |
| `[WORKFLOW]` | Federation workflow name |
| `--group GROUP` | Executable federation group from [federation.groups] |
| `--parallel` | Run dependency-ready federation children concurrently when reservations allow |
| `--max-parallel N` | Limit concurrent federation children; implies --parallel |
| `--affected` | Run only dependency-aware affected workflow repositories |
| `--since SINCE` | With --affected, detect direct changes since this Git revision |
| `--repository REPOSITORY` | With --affected, seed one directly changed repository; repeatable |
| `--drift-policy DRIFT_POLICY` | Frozen source drift policy (default: fail) Choices: fail, replan, allow-if-equivalent |
| `--json` | Emit the machine-readable federated run, scheduler, and lineage report |

### `devtool federation resume`

Resume a prior federation parent run using dependency-aware checkpoint reuse

| Argument / option | Meaning |
| --- | --- |
| `RUN_ID` | Prior federation parent run ID |
| `--drift-policy DRIFT_POLICY` | Frozen source drift policy for re-executed children Choices: fail, replan, allow-if-equivalent |
| `--json` | Emit machine-readable reuse/invalidation decisions and run result |

### `devtool federation retry`

Retry a prior federation parent run while reusing still-valid successful children

| Argument / option | Meaning |
| --- | --- |
| `RUN_ID` | Prior federation parent run ID |
| `--failed` | Retry failed/cancelled/blocked children only; fail closed if a successful child is invalidated |
| `--drift-policy DRIFT_POLICY` | Frozen source drift policy for re-executed children Choices: fail, replan, allow-if-equivalent |
| `--json` | Emit machine-readable reuse/invalidation decisions and run result |

### `devtool federation recover`

Inspect persisted child/result/rollback recovery state for a federation run

| Argument / option | Meaning |
| --- | --- |
| `RUN_ID` | Accepted by this command. |
| `--json` | Accepted by this command. |

### `devtool federation rollback`

Explicitly roll integrated repository pointer(s) back to their validated start commit

| Argument / option | Meaning |
| --- | --- |
| `RUN_ID` | Accepted by this command. |
| `--repo ROLLBACK_REPOSITORY` | Accepted by this command. |
| `--integration-group ROLLBACK_INTEGRATION_GROUP` | Accepted by this command. |
| `--dry-run` | Preflight rollback without moving branch pointers |
| `--json` | Accepted by this command. |

### `devtool federation integrate`

Integrate validated federation result commits into user branch pointers

| Argument / option | Meaning |
| --- | --- |
| `RUN_ID` | Completed federation parent run ID |
| `--policy POLICY` | Override the workflow integration policy Choices: per-repo, dependency-group, all-or-nothing-pointer-update |
| `--apply` | Apply branch pointer integration; without this flag the command is a dry-run |
| `--json` | Emit machine-readable integration plan/evidence |

### `devtool federation bootstrap`

Preview or materialize repositories from the frozen federation source lock

| Argument / option | Meaning |
| --- | --- |
| `--repo REPOSITORY` | Limit source management to one frozen repository; repeatable |
| `--apply` | Apply the lock-driven bootstrap; without this flag the command is a preview |
| `--json` | Emit machine-readable bootstrap intent/evidence |

### `devtool federation sync`

Preview or update existing repositories to the frozen federation source lock

| Argument / option | Meaning |
| --- | --- |
| `--repo REPOSITORY` | Limit source management to one frozen repository; repeatable |
| `--apply` | Apply the lock-driven sync; without this flag the command is a preview |
| `--json` | Emit machine-readable sync intent/evidence |

### `devtool federation exec`

Execute one enrolled child wrapper or target-local-job public contract

| Argument / option | Meaning |
| --- | --- |
| `REPOSITORY` | Federation repository name |
| `--plan` | Resolve the child public contract without executing it |
| `--json` | Emit machine-readable bridge resolution or execution state |
| `CONTRACT_ARGV` | Use -- wrapper <args...> or -- job <reference> |

### `devtool core`

Inspect canonical Devtool core registries and trust-boundary metadata

**Subcommands:** `registry`, `runtime`, `run-store`, `surfaces`, `help`

### `devtool core registry`

Emit the canonical CLI/config/env registry

**Aliases:** `devtool core reg`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable registry JSON |
| `--parity` | Include parser/registry parity evidence |

### `devtool core runtime`

Resolve the CORE-X02 effective config and private run envelope

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable runtime envelope JSON |
| `--format FORMAT` | Requested frontend output format Choices: human, json, copy |
| `--stdout-payload STDOUT_PAYLOAD` | Requested stdout payload semantics Choices: auto, summary, raw |
| `--assume-yes` | Resolve a non-interactive interaction policy |
| `ARGV` | Command argv to lower into the runtime envelope; prefix with -- when needed |

### `devtool core run-store`

Inspect the CORE-X03 private run catalog/doctor state

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable run catalog state |
| `--root ROOT` | Override the run catalog root; defaults to <repo>/.devtool/private/run-v3 |
| `--doctor` | Emit storage doctor/GC state instead of the full catalog |

### `devtool core surfaces`

Generate or verify CORE-X04 machine help, schema, completion, and reference projections

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit the generated-surface manifest as JSON |
| `--write DIR` | Write generated projections atomically to DIR |
| `--check DIR` | Fail when generated projections in DIR drift from canonical sources |

### `devtool core help`

Search canonical machine help without scraping argparse text

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit canonical machine-help JSON |
| `--search SEARCH` | Filter by command ID, path, alias, domain, or summary |
| `--aliases` | Include compatibility aliases |
| `--advanced` | Include hidden/internal parser options |

### `devtool registry`

==SUPPRESS==

This command has no additional public command-local arguments.

### `devtool config`

**Subcommands:** `path`, `show`, `defaults`, `init`, `edit`, `resolve`, `explain`, `migrate`, `doctor`, `schema`, `status`, `diff`

### `devtool config path`

Print the repo or global config path

| Argument / option | Meaning |
| --- | --- |
| `--global` | Use the user global config path |

### `devtool config show`

Print repo or global config

| Argument / option | Meaning |
| --- | --- |
| `--global` | Show ~/.config/devtool/config.toml |

### `devtool config defaults`

Print a config template

| Argument / option | Meaning |
| --- | --- |
| `--global` | Print the sparse global config template |
| `--minimal` | Prefer the minimal sparse template |

### `devtool config init`

Create a config file

| Argument / option | Meaning |
| --- | --- |
| `--global` | Create ~/.config/devtool/config.toml |
| `--minimal` | Write the sparse global preference template |
| `--force` | Overwrite an existing config |

### `devtool config edit`

Open config in an editor

| Argument / option | Meaning |
| --- | --- |
| `--global` | Edit ~/.config/devtool/config.toml |
| `--editor EDITOR` | Editor command; defaults to DEVTOOL_EDITOR, VISUAL, EDITOR, then micro |

### `devtool config resolve`

Print the fully resolved repository configuration

| Argument / option | Meaning |
| --- | --- |
| `--provenance` | Include a top-level provenance explanation |

### `devtool config explain`

Explain canonical target/runner/UI configuration and provenance

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable explanation JSON |

### `devtool config migrate`

Check or rewrite legacy kind/type aliases to canonical runner/platform syntax

| Argument / option | Meaning |
| --- | --- |
| `--check` | Check and report migration opportunities without changing files |
| `--write` | Rewrite safe legacy aliases in place and create a .bak backup |
| `--json` | Emit machine-readable migration report |
| `--global` | Migrate legacy UI preferences in the user global config |

### `devtool config doctor`

| Argument / option | Meaning |
| --- | --- |
| `--global` | Diagnose the user global config |

### `devtool config schema`

Emit the generated CORE configuration schema/reference contract

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable schema JSON |

### `devtool config status`

Show canonical effective configuration, migration, and provenance status

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable status JSON |

### `devtool config diff`

Show effective fields whose provenance differs from built-in/default authority

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable provenance diff JSON |

### `devtool ssh`

Manage, qualify, and operate named SSH build workers

**Subcommands:** `profiles`, `add`, `remove`, `default`, `qualify`, `doctor`, `env`, `prepare`, `bootstrap`, `sessions`, `workspace`, `sync`, `retrieve`, `runs`, `output`, `exec`, `resume`, `cancel`, `recover`, `gc`

### `devtool ssh profiles`

List effective global and repository SSH worker profiles

**Aliases:** `devtool ssh list`

| Argument / option | Meaning |
| --- | --- |
| `--format FORMAT` | Choices: text, json |

### `devtool ssh add`

Create or replace a reusable named SSH worker profile

| Argument / option | Meaning |
| --- | --- |
| `PROFILE` | Worker profile name |
| `--host HOST` | SSH host name or address Required. |
| `--port PORT` | SSH port (Termux sshd commonly uses 8022) |
| `--user USER` | Remote SSH user |
| `--identity IDENTITY` | Local SSH identity file |
| `--cwd CWD` | Existing remote checkout path; required with --workspace-mode existing |
| `--workspace-mode WORKSPACE_MODE` | Choices: managed, existing, ephemeral |
| `--workspace-root WORKSPACE_ROOT` | Accepted by this command. |
| `--bootstrap-policy BOOTSTRAP_POLICY` | Choices: missing, never |
| `--execution-provider EXECUTION_PROVIDER` | Worker execution provider; OCI-01 accepts proot-oci configuration but does not execute it yet Choices: native, ssh-native, proot-oci, ssh-proot-oci |
| `--host-key-policy HOST_KEY_POLICY` | Choices: accept-new, strict, system |
| `--min-free-mb MIN_FREE_MB` | Accepted by this command. |
| `--no-default` | Do not make this the default SSH worker |
| `--force` | Replace an existing profile in the selected scope |
| `--format FORMAT` | Choices: text, json |
| `--global` | Manage the reusable user-global SSH worker profile (default) |
| `--repo-local` | Manage the worker only in this repository .devtool.toml |

### `devtool ssh remove`

Remove a named SSH worker profile

**Aliases:** `devtool ssh rm`

| Argument / option | Meaning |
| --- | --- |
| `PROFILE` | Worker profile name |
| `--format FORMAT` | Choices: text, json |
| `--global` | Manage the reusable user-global SSH worker profile (default) |
| `--repo-local` | Manage the worker only in this repository .devtool.toml |

### `devtool ssh default`

Set the default named SSH worker profile

| Argument / option | Meaning |
| --- | --- |
| `PROFILE` | Worker profile name |
| `--format FORMAT` | Choices: text, json |
| `--global` | Manage the reusable user-global SSH worker profile (default) |
| `--repo-local` | Manage the worker only in this repository .devtool.toml |

### `devtool ssh qualify`

End-to-end qualify a named managed SSH worker

**Aliases:** `devtool ssh seal`

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named worker; defaults to the configured default profile |
| `--format FORMAT` | Choices: text, json |
| `--dry-run` | Plan bootstrap/sync/qualification without modifying the worker |
| `--no-bootstrap` | Do not install missing minimum worker tools |
| `--no-sync` | Qualify transport/tmux/return only; skip repository sync checks |
| `--project-validation` | Also run the repository's real managed SSH validation path |
| `--target SSH_QUALIFY_TARGET` | Target/profile used by --project-validation |
| `--task SSH_QUALIFY_TASKS` | Validation task used by --project-validation; repeatable |
| `--single-validation` | Run one project validation instead of first + warm/incremental validation |

### `devtool ssh doctor`

Read-only capability, host identity, lifecycle, and target remediation discovery for a named SSH worker

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named [ssh.hosts.*] profile; defaults to [ssh].profile/default_profile |
| `--target SSH_DOCTOR_TARGET` | Optional repository target for target-aware toolchain remediation evidence |
| `--format FORMAT` | Choices: text, json |

### `devtool ssh env`

Inspect the remote-owned worker environment and toolchain provenance

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named [ssh.hosts.*] profile; defaults to [ssh].profile/default_profile |
| `--target SSH_ENV_TARGET` | Target name to associate with environment evidence |
| `--format FORMAT` | Choices: text, json |

### `devtool ssh prepare`

Prepare missing worker/runtime and target toolchain pieces without changing repository source

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named [ssh.hosts.*] profile; defaults to [ssh].profile/default_profile |
| `--target SSH_PREPARE_TARGET` | Target to prepare |
| `--dry-run` | Classify required preparation without installing anything |
| `--format FORMAT` | Choices: text, json |

### `devtool ssh bootstrap`

Install missing minimum worker tools (python, tmux, rsync, git)

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named [ssh.hosts.*] profile; defaults to [ssh].profile/default_profile |
| `--format FORMAT` | Choices: text, json |
| `--dry-run` | Show the remote package command without modifying the worker |
| `--force` | Override bootstrap.policy=never for this explicit request |

### `devtool ssh sessions`

List Devtool-owned tmux sessions on a named worker

**Aliases:** `devtool ssh ls`

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named [ssh.hosts.*] profile; defaults to [ssh].profile/default_profile |
| `--format FORMAT` | Choices: text, json |

### `devtool ssh workspace`

Inspect the repository workspace mapping on a named worker

**Aliases:** `devtool ssh ws`

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named [ssh.hosts.*] profile; defaults to [ssh].profile/default_profile |
| `--format FORMAT` | Choices: text, json |

### `devtool ssh sync`

Plan or synchronize the local repository into the worker workspace

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named [ssh.hosts.*] profile; defaults to [ssh].profile/default_profile |
| `--format FORMAT` | Choices: text, json |
| `--dry-run` | Show the exact rsync delta without modifying the worker |

### `devtool ssh retrieve`

Retry retrieval of reports/artifacts from a prior SSH worker run

**Aliases:** `devtool ssh return`

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named [ssh.hosts.*] profile; defaults to [ssh].profile/default_profile |
| `--format FORMAT` | Choices: text, json |
| `--run-id RUN_ID` | Restrict recovery to one local Devtool run id |
| `--session SESSION` | Restrict recovery to one SSH tmux phase session |

### `devtool ssh runs`

List canonical persisted SSH runs grouped across remote phases

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named [ssh.hosts.*] profile; defaults to [ssh].profile/default_profile |
| `--format FORMAT` | Choices: text, json |

### `devtool ssh output`

Read or follow persisted stdout/stderr for a canonical SSH run

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named [ssh.hosts.*] profile; defaults to [ssh].profile/default_profile |
| `--run-id RUN_ID` | Select a canonical Devtool RunId |
| `--session SESSION` | Select one persisted SSH phase session |
| `--latest` | Select the newest persisted phase |
| `--follow` | Follow persisted output without attaching to tmux |
| `--stream SSH_OUTPUT_STREAM` | Choices: both, stdout, stderr |
| `--format FORMAT` | Choices: text, json |

### `devtool ssh exec`

Run an arbitrary command through managed SSH execution/history/recovery

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named [ssh.hosts.*] profile; defaults to [ssh].profile/default_profile |
| `--target SSH_EXEC_TARGET` | Repository target context |
| `--cwd SSH_EXEC_CWD` | Repository-relative working directory |
| `--detached` | Launch and return while the remote tmux phase continues |
| `--follow` | Launch detached, then follow persisted output to completion |
| `--attach` | Foreground attach mode (default) |
| `--shell SSH_EXEC_SHELL` | Explicit shell-text command; argv mode is used otherwise |
| `--format FORMAT` | Choices: text, json |
| `SSH_EXEC_ARGV...` | Command argv after --; argument boundaries are preserved |

### `devtool ssh resume`

Reattach to an active remote tmux phase or report its persisted status

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named [ssh.hosts.*] profile; defaults to [ssh].profile/default_profile |
| `--run-id RUN_ID` | Resume a specific Devtool SSH run; defaults to the newest run |
| `--session SESSION` | Resume a specific tmux phase session |
| `--node-id NODE_ID` | Resume the remote phase that owns this canonical provider node |
| `--format FORMAT` | Choices: text, json |

### `devtool ssh cancel`

Cancel Devtool-owned tmux phases for a remote run

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named [ssh.hosts.*] profile; defaults to [ssh].profile/default_profile |
| `--run-id RUN_ID` | Cancel a specific run; defaults to the newest run |
| `--session SESSION` | Cancel only a specific tmux phase session |
| `--node-id NODE_ID` | Cancel only the remote phase that owns this canonical provider node |
| `--format FORMAT` | Choices: text, json |

### `devtool ssh recover`

Reconcile stale remote run records after interrupted SSH sessions

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named [ssh.hosts.*] profile; defaults to [ssh].profile/default_profile |
| `--run-id RUN_ID` | Restrict stale-run recovery to one run |
| `--node-id NODE_ID` | Restrict stale-run recovery to one canonical provider node |
| `--format FORMAT` | Choices: text, json |

### `devtool ssh gc`

Safely prune terminal Devtool-owned SSH run history using age/count/size policies

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named [ssh.hosts.*] profile; defaults to [ssh].profile/default_profile |
| `--dry-run` | Plan cleanup without deleting anything (default) |
| `--apply` | Apply the safe GC plan; active/recovery-sensitive runs remain protected |
| `--max-age-days SSH_GC_MAX_AGE_DAYS` | Prune eligible terminal runs at least this old; 0 allows age selection immediately |
| `--keep-last SSH_GC_KEEP_LAST` | Always protect this many newest runs |
| `--max-mib SSH_GC_MAX_MIB` | Target maximum SSH run-state size after safe cleanup |
| `--format FORMAT` | Choices: text, json |

### `devtool oci`

Manage PRoot-Distro OCI runtime state on named SSH workers

**Subcommands:** `definition`, `build`, `gate1`, `workspace`, `validate-image`, `image-status`, `update`, `prune`, `diagnostics`, `final-seal`, `doctor`, `images`, `containers`, `bootstrap`, `install`, `remove`

### `devtool oci definition`

Show the canonical deterministic Android ARM64 OCI image contract

| Argument / option | Meaning |
| --- | --- |
| `--format FORMAT` | Choices: text, json |

### `devtool oci build`

Build the canonical Android ARM64 OCI image through PRoot-Distro

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named SSH worker; defaults to the configured default profile |
| `--tag OCI_TAG` | Local image tag/reference; defaults to the canonical contract reference |
| `--install-as OCI_INSTALL_AS` | Install the built image as this PRoot-Distro container and validate it |
| `--no-cache` | Disable PRoot-Distro build cache for this image build |
| `--dry-run` | Accepted by this command. |
| `--format FORMAT` | Choices: text, json |

### `devtool oci gate1`

Run OCI-03 Validation Gate 1 as one durable canonical EXO execution

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named SSH worker; defaults to the configured default profile |
| `--container OCI_GATE1_CONTAINER` | Installed PRoot-Distro container name; defaults to a RunId-derived Gate-1 name |
| `--tag OCI_TAG` | Image tag/reference; defaults to the canonical contract reference |
| `--format FORMAT` | Choices: text, json |

### `devtool oci workspace`

Prepare OCI-04 managed workspace binds, persistent caches, and environment allowlist evidence

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named SSH worker; defaults to the configured default profile |
| `--dry-run` | Validate workspace/cache paths without creating cache directories |
| `--format FORMAT` | Choices: text, json |

### `devtool oci validate-image`

Validate an installed container against the canonical Android OCI image contract

| Argument / option | Meaning |
| --- | --- |
| `PROFILE` | Named SSH worker |
| `CONTAINER` | Installed PRoot-Distro container name |
| `--format FORMAT` | Choices: text, json |

### `devtool oci image-status`

Show canonical Android OCI image/container status without mutating the worker

**Aliases:** `devtool oci status`

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named SSH worker; defaults to the configured default profile |
| `--format FORMAT` | Choices: text, json |

### `devtool oci update`

Plan or apply immutable canonical Android OCI image refresh

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named SSH worker; defaults to the configured default profile |
| `--apply` | Execute the planned build/install/validate update |
| `--force` | Rebuild/reinstall even when cached image and container are present |
| `--no-cache` | Disable PRoot-Distro build cache when --apply is used |
| `--format FORMAT` | Choices: text, json |

### `devtool oci prune`

Plan or apply removal of non-canonical cached OCI images

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named SSH worker; defaults to the configured default profile |
| `--apply` | Remove non-canonical cached OCI images |
| `--format FORMAT` | Choices: text, json |

### `devtool oci diagnostics`

Show redacted OCI diagnostics and HUD visibility summary

**Aliases:** `devtool oci diag`

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named SSH worker; defaults to the configured default profile |
| `--format FORMAT` | Choices: text, json |

### `devtool oci final-seal`

Run the local OCI-10 final roadmap seal without mutating workers

**Aliases:** `devtool oci seal`

| Argument / option | Meaning |
| --- | --- |
| `--format FORMAT` | Choices: text, json |
| `--strict` | Exit non-zero when any OCI-10 final seal check fails |

### `devtool oci doctor`

Discover PRoot-Distro, proot, containers, and cached OCI images

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named SSH worker; defaults to the configured default profile |
| `--format FORMAT` | Choices: text, json |

### `devtool oci images`

List cached OCI image references on a worker

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named SSH worker; defaults to the configured default profile |
| `--format FORMAT` | Choices: text, json |

### `devtool oci containers`

List installed PRoot-Distro containers on a worker

**Aliases:** `devtool oci list`

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named SSH worker; defaults to the configured default profile |
| `--format FORMAT` | Choices: text, json |

### `devtool oci bootstrap`

Install or repair PRoot-Distro on a Termux SSH worker

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named SSH worker; defaults to the configured default profile |
| `--dry-run` | Accepted by this command. |
| `--force` | Accepted by this command. |
| `--format FORMAT` | Choices: text, json |

### `devtool oci install`

Install a registry image or local OCI archive on a worker

| Argument / option | Meaning |
| --- | --- |
| `PROFILE` | Named SSH worker |
| `SOURCE` | OCI registry reference or local OCI/rootfs archive path |
| `--name OCI_NAME` | Explicit local container name |
| `--architecture OCI_ARCHITECTURE` | Target architecture, e.g. aarch64 or linux/arm64 |
| `--dry-run` | Accepted by this command. |
| `--format FORMAT` | Choices: text, json |

### `devtool oci remove`

Remove an installed container or cached OCI image

**Aliases:** `devtool oci rm`

| Argument / option | Meaning |
| --- | --- |
| `PROFILE` | Named SSH worker |
| `TARGET` | Container name or cached image reference |
| `--image` | Remove a cached OCI image instead of a container |
| `--dry-run` | Accepted by this command. |
| `--format FORMAT` | Choices: text, json |

### `devtool bootstrap`

Provision reusable destination-specific build toolchains

**Subcommands:** `install`, `status`, `remove`

### `devtool bootstrap install`

Provision and seal a reusable toolchain for a destination

| Argument / option | Meaning |
| --- | --- |
| `--destination DESTINATION` | current, termux, linux-arm64, linux-amd64, chroot:<name>, chroot-arm64:<name>, or chroot-amd64:<name> |
| `--format FORMAT` | Choices: text, json |
| `--java-version BOOTSTRAP_JAVA_VERSION` | Accepted by this command. |
| `--gradle-version BOOTSTRAP_GRADLE_VERSION` | Accepted by this command. |
| `--android-platform BOOTSTRAP_ANDROID_PLATFORM` | Accepted by this command. |
| `--build-tools BOOTSTRAP_BUILD_TOOLS` | Accepted by this command. |
| `--ndk-version BOOTSTRAP_NDK_VERSION` | Accepted by this command. |
| `--cmake-version BOOTSTRAP_CMAKE_VERSION` | Accepted by this command. |
| `--sdk-provider BOOTSTRAP_SDK_PROVIDER` | Choices: auto, homuhomu, managed, termuxvoid |
| `--aapt2-provider BOOTSTRAP_AAPT2_PROVIDER` | Choices: auto, termux, sdk, revanced, none |
| `--arm-build-tools-provider BOOTSTRAP_ARM_BUILD_TOOLS_PROVIDER` | Choices: auto, commit451, none, sdk, termux |
| `--native-provider BOOTSTRAP_NATIVE_PROVIDER` | Choices: auto, ndk, system, termux-sysroot |
| `--gradle-provider BOOTSTRAP_GRADLE_PROVIDER` | Choices: auto, download, system, termux, wrapper |
| `--java-provider BOOTSTRAP_JAVA_PROVIDER` | Choices: auto, system, termux |
| `--sdk-root BOOTSTRAP_SDK_ROOT` | Accepted by this command. |
| `--gradle-user-home BOOTSTRAP_GRADLE_USER_HOME` | Accepted by this command. |
| `--cache-dir BOOTSTRAP_CACHE_DIR` | Accepted by this command. |
| `--repair` | Re-run provisioning and repair missing/corrupt components |
| `--upgrade` | Force refresh of Devtool-managed toolchain downloads |
| `--dry-run` | Print/probe provisioning without writing a receipt |

### `devtool bootstrap status`

Verify the provisioning receipt for a destination

| Argument / option | Meaning |
| --- | --- |
| `--destination DESTINATION` | current, termux, linux-arm64, linux-amd64, chroot:<name>, chroot-arm64:<name>, or chroot-amd64:<name> |
| `--format FORMAT` | Choices: text, json |

### `devtool bootstrap remove`

Remove the destination receipt without uninstalling shared system packages

| Argument / option | Meaning |
| --- | --- |
| `--destination DESTINATION` | current, termux, linux-arm64, linux-amd64, chroot:<name>, chroot-arm64:<name>, or chroot-amd64:<name> |
| `--format FORMAT` | Choices: text, json |

### `devtool android`

Inspect Android capabilities, ADB providers, and connected devices

**Subcommands:** `environment`, `env`, `qualification`, `capabilities`, `devices`, `doctor`, `signing`, `install`, `uninstall`, `launch`, `stop`, `restart`, `clear-data`, `shell`, `push`, `pull`, `logcat`, `screenshot`, `permission`, `release`, `instrumentation`, `smoke`

### `devtool android environment`

Explain the canonical Android environment identity and qualification

| Argument / option | Meaning |
| --- | --- |
| `--action ACTION` | Choices: build, package, release, test, lint, instrumentation, doctor, explain |
| `--format FORMAT` | Choices: text, json |

### `devtool android env`

Explain or diff canonical Android environment identities

**Subcommands:** `explain`, `diff`

### `devtool android env explain`

Explain the production Android environment resolver

| Argument / option | Meaning |
| --- | --- |
| `--action ACTION` | Choices: build, package, release, test, lint, instrumentation, doctor, explain |
| `--format FORMAT` | Choices: text, json |

### `devtool android env diff`

Diff canonical component identities across Android actions

| Argument / option | Meaning |
| --- | --- |
| `--left-action LEFT_ACTION` | Choices: build, package, release, test, lint, instrumentation, doctor, explain |
| `--right-action RIGHT_ACTION` | Choices: build, package, release, test, lint, instrumentation, doctor, explain |
| `--format FORMAT` | Choices: text, json |

### `devtool android qualification`

Inspect canonical Android qualification truth

**Subcommands:** `show`

### `devtool android qualification show`

Show qualification freshness, diagnostics, and component identities

| Argument / option | Meaning |
| --- | --- |
| `--action ACTION` | Choices: build, package, release, test, lint, instrumentation, doctor, explain |
| `--format FORMAT` | Choices: text, json |

### `devtool android capabilities`

Show the resolved Android execution context and tool providers

| Argument / option | Meaning |
| --- | --- |
| `--provider PROVIDER` | ADB provider override Choices: auto, sdk, termux, termux-adb, path, none |
| `--adb-path ADB_PATH` | Explicit ADB executable path |
| `--format FORMAT` | Choices: text, json |

### `devtool android devices`

List connected Android devices or diagnose the active device transport

| Argument / option | Meaning |
| --- | --- |
| `--provider PROVIDER` | ADB provider override Choices: auto, sdk, termux, termux-adb, path, none |
| `--adb-path ADB_PATH` | Explicit ADB executable path |
| `--format FORMAT` | Choices: text, json |
| `[DEVICE_ACTION]` | Choices: list, doctor |
| `--enrich` | Query usable devices for API, ABI, battery, screen, and boot metadata |
| `--policy POLICY` | Select devices using a deterministic policy Choices: exactly-one, serial, preferred, first-physical, first-emulator, all |
| `--serial SERIAL` | Select a device serial or transport ID (ANDROID_SERIAL remains supported) |
| `--preferred PREFERRED` | Preferred serial/model/device for --policy preferred |
| `--include-unusable` | Allow offline/unauthorized devices in explicit selection |

### `devtool android doctor`

Diagnose Android tools and the active ADB/device transport

| Argument / option | Meaning |
| --- | --- |
| `--provider PROVIDER` | ADB provider override Choices: auto, sdk, termux, termux-adb, path, none |
| `--adb-path ADB_PATH` | Explicit ADB executable path |
| `--format FORMAT` | Choices: text, json |
| `--for ANDROID_DOCTOR_FOR` | Diagnose only capabilities required by this Android action Choices: build, package, release, test, lint, instrumentation, doctor, explain, install, smoke |

### `devtool android signing`

Create, inspect, verify, compare, import, and export Android signing identities

**Subcommands:** `create`, `list`, `inspect`, `verify`, `compare`, `import`, `export-certificate`

### `devtool android signing create`

Create a Devtool-managed Android signing keystore

| Argument / option | Meaning |
| --- | --- |
| `--keystore KEYSTORE` | Accepted by this command. |
| `--alias ALIAS` | Accepted by this command. |
| `--store-password-env STORE_PASSWORD_ENV` | Accepted by this command. |
| `--key-password-env KEY_PASSWORD_ENV` | Accepted by this command. |
| `--dname DNAME` | Accepted by this command. |
| `--overwrite` | Accepted by this command. |
| `--format FORMAT` | Choices: text, json |

### `devtool android signing list`

List aliases and certificate identities in a signing keystore

| Argument / option | Meaning |
| --- | --- |
| `--keystore KEYSTORE` | Accepted by this command. |
| `--store-password-env STORE_PASSWORD_ENV` | Accepted by this command. |
| `--format FORMAT` | Choices: text, json |

### `devtool android signing inspect`

Inspect configured keystore alias and certificate identity

| Argument / option | Meaning |
| --- | --- |
| `--keystore KEYSTORE` | Accepted by this command. |
| `--alias ALIAS` | Accepted by this command. |
| `--store-password-env STORE_PASSWORD_ENV` | Accepted by this command. |
| `--format FORMAT` | Choices: text, json |

### `devtool android signing verify`

Verify the configured identity or a signed APK/AAB

| Argument / option | Meaning |
| --- | --- |
| `[ARTIFACT]` | Accepted by this command. |
| `--keystore KEYSTORE` | Accepted by this command. |
| `--alias ALIAS` | Accepted by this command. |
| `--store-password-env STORE_PASSWORD_ENV` | Accepted by this command. |
| `--format FORMAT` | Choices: text, json |

### `devtool android signing compare`

Compare signer certificate identities of two APK/AAB artifacts

| Argument / option | Meaning |
| --- | --- |
| `ARTIFACT` | Accepted by this command. |
| `OTHER_ARTIFACT` | Accepted by this command. |
| `--format FORMAT` | Choices: text, json |

### `devtool android signing import`

Import/migrate a keystore, including JKS to PKCS12

| Argument / option | Meaning |
| --- | --- |
| `SOURCE` | Accepted by this command. |
| `DESTINATION` | Accepted by this command. |
| `--alias ALIAS` | Accepted by this command. |
| `--source-password-env SOURCE_PASSWORD_ENV` | Required. |
| `--destination-password-env DESTINATION_PASSWORD_ENV` | Required. |
| `--overwrite` | Accepted by this command. |
| `--format FORMAT` | Choices: text, json |

### `devtool android signing export-certificate`

Export configured signing certificate as PEM

| Argument / option | Meaning |
| --- | --- |
| `--destination DESTINATION` | Required. |
| `--keystore KEYSTORE` | Accepted by this command. |
| `--alias ALIAS` | Accepted by this command. |
| `--store-password-env STORE_PASSWORD_ENV` | Accepted by this command. |
| `--format FORMAT` | Choices: text, json |

### `devtool android install`

Install an APK on the selected Android device

| Argument / option | Meaning |
| --- | --- |
| `APK` | Accepted by this command. |
| `--serial SERIAL` | Android device serial/transport ID |
| `--policy POLICY` | Device selection policy Choices: exactly-one, serial, preferred, first-physical, first-emulator |
| `--format FORMAT` | Choices: text, json |
| `--no-replace` | Accepted by this command. |
| `--downgrade` | Accepted by this command. |

### `devtool android uninstall`

Uninstall a package from the selected device

| Argument / option | Meaning |
| --- | --- |
| `PACKAGE` | Accepted by this command. |
| `--serial SERIAL` | Android device serial/transport ID |
| `--policy POLICY` | Device selection policy Choices: exactly-one, serial, preferred, first-physical, first-emulator |
| `--format FORMAT` | Choices: text, json |
| `--keep-data` | Accepted by this command. |

### `devtool android launch`

Launch an Android package

| Argument / option | Meaning |
| --- | --- |
| `PACKAGE` | Accepted by this command. |
| `--activity ACTIVITY` | Accepted by this command. |
| `--serial SERIAL` | Android device serial/transport ID |
| `--policy POLICY` | Device selection policy Choices: exactly-one, serial, preferred, first-physical, first-emulator |
| `--format FORMAT` | Choices: text, json |

### `devtool android stop`

Force-stop an Android package

| Argument / option | Meaning |
| --- | --- |
| `PACKAGE` | Accepted by this command. |
| `--serial SERIAL` | Android device serial/transport ID |
| `--policy POLICY` | Device selection policy Choices: exactly-one, serial, preferred, first-physical, first-emulator |
| `--format FORMAT` | Choices: text, json |

### `devtool android restart`

Force-stop and relaunch an Android package

| Argument / option | Meaning |
| --- | --- |
| `PACKAGE` | Accepted by this command. |
| `--activity ACTIVITY` | Accepted by this command. |
| `--serial SERIAL` | Android device serial/transport ID |
| `--policy POLICY` | Device selection policy Choices: exactly-one, serial, preferred, first-physical, first-emulator |
| `--format FORMAT` | Choices: text, json |

### `devtool android clear-data`

Clear an Android package's app data

| Argument / option | Meaning |
| --- | --- |
| `PACKAGE` | Accepted by this command. |
| `--serial SERIAL` | Android device serial/transport ID |
| `--policy POLICY` | Device selection policy Choices: exactly-one, serial, preferred, first-physical, first-emulator |
| `--format FORMAT` | Choices: text, json |

### `devtool android shell`

Run an adb shell command on the selected device

| Argument / option | Meaning |
| --- | --- |
| `SHELL_ARGS` | Accepted by this command. |
| `--serial SERIAL` | Android device serial/transport ID |
| `--policy POLICY` | Device selection policy Choices: exactly-one, serial, preferred, first-physical, first-emulator |
| `--format FORMAT` | Choices: text, json |

### `devtool android push`

Push a file/directory to the selected device

| Argument / option | Meaning |
| --- | --- |
| `SOURCE` | Accepted by this command. |
| `DESTINATION` | Accepted by this command. |
| `--serial SERIAL` | Android device serial/transport ID |
| `--policy POLICY` | Device selection policy Choices: exactly-one, serial, preferred, first-physical, first-emulator |
| `--format FORMAT` | Choices: text, json |

### `devtool android pull`

Pull a file/directory from the selected device

| Argument / option | Meaning |
| --- | --- |
| `SOURCE` | Accepted by this command. |
| `DESTINATION` | Accepted by this command. |
| `--serial SERIAL` | Android device serial/transport ID |
| `--policy POLICY` | Device selection policy Choices: exactly-one, serial, preferred, first-physical, first-emulator |
| `--format FORMAT` | Choices: text, json |

### `devtool android logcat`

Capture recent logcat from the selected device

| Argument / option | Meaning |
| --- | --- |
| `--lines LINES` | Accepted by this command. |
| `--serial SERIAL` | Android device serial/transport ID |
| `--policy POLICY` | Device selection policy Choices: exactly-one, serial, preferred, first-physical, first-emulator |
| `--format FORMAT` | Choices: text, json |

### `devtool android screenshot`

Capture a PNG screenshot from the selected device

| Argument / option | Meaning |
| --- | --- |
| `DESTINATION` | Accepted by this command. |
| `--serial SERIAL` | Android device serial/transport ID |
| `--policy POLICY` | Device selection policy Choices: exactly-one, serial, preferred, first-physical, first-emulator |
| `--format FORMAT` | Choices: text, json |

### `devtool android permission`

Grant or revoke a runtime permission

**Subcommands:** `grant`, `revoke`

### `devtool android permission grant`

| Argument / option | Meaning |
| --- | --- |
| `PACKAGE` | Accepted by this command. |
| `PERMISSION` | Accepted by this command. |
| `--serial SERIAL` | Android device serial/transport ID |
| `--policy POLICY` | Device selection policy Choices: exactly-one, serial, preferred, first-physical, first-emulator |
| `--format FORMAT` | Choices: text, json |

### `devtool android permission revoke`

| Argument / option | Meaning |
| --- | --- |
| `PACKAGE` | Accepted by this command. |
| `PERMISSION` | Accepted by this command. |
| `--serial SERIAL` | Android device serial/transport ID |
| `--policy POLICY` | Device selection policy Choices: exactly-one, serial, preferred, first-physical, first-emulator |
| `--format FORMAT` | Choices: text, json |

### `devtool android release`

Run the canonical Android release pipeline and seal evidence

| Argument / option | Meaning |
| --- | --- |
| `--variant RELEASE_VARIANT` | Release variant lane override |
| `--module RELEASE_MODULE` | Release module lane override |
| `--device-tests DEVICE_TESTS` | Choices: disabled, when-device-present, required |
| `--previous-apk PREVIOUS_APK` | Accepted by this command. |
| `--package RELEASE_PACKAGE` | Accepted by this command. |
| `--activity RELEASE_ACTIVITY` | Accepted by this command. |
| `--evidence-output EVIDENCE_OUTPUT` | Accepted by this command. |
| `--no-unit-tests` | Accepted by this command. |
| `--no-lint` | Accepted by this command. |
| `--no-install-smoke` | Accepted by this command. |
| `--no-upgrade-smoke` | Accepted by this command. |
| `--no-verify-signature` | Accepted by this command. |
| `--no-verify-certificate-identity` | Accepted by this command. |
| `--format FORMAT` | Choices: text, json |

### `devtool android instrumentation`

Run Android instrumentation through the canonical device orchestrator

| Argument / option | Meaning |
| --- | --- |
| `--variant INSTRUMENTATION_VARIANT` | Instrumentation variant lane override |
| `--module INSTRUMENTATION_MODULE` | Instrumentation module lane override |
| `--class INSTRUMENTATION_CLASSES` | Accepted by this command. |
| `--method INSTRUMENTATION_METHODS` | Accepted by this command. |
| `--annotation INSTRUMENTATION_ANNOTATIONS` | Accepted by this command. |
| `--not-annotation INSTRUMENTATION_NOT_ANNOTATIONS` | Accepted by this command. |
| `--size INSTRUMENTATION_SIZE` | Choices: small, medium, large |
| `--runner-arg INSTRUMENTATION_RUNNER_ARGS` | Accepted by this command. |
| `--skip-build` | Accepted by this command. |
| `--plan` | Print the ANDROID-X04 semantic plan without mutation |
| `--explain` | Include ANDROID-X04 authority/migration explanation |
| `--shards SHARDS` | Typed AndroidJUnitRunner shard count |
| `--retry-failed-attempts RETRY_FAILED_ATTEMPTS` | Export failed-only retry policy to EXO |
| `--quarantine QUARANTINE` | Explicit evidence-only quarantined testcase ID |
| `--orchestrator-qualification ORCHESTRATOR_QUALIFICATION` | Qualified Android Test Orchestrator capability receipt ID |
| `--orchestrator-target ORCHESTRATOR_TARGET` | Target instrumentation component for qualified Orchestrator mode |
| `--ui-automator` | Use the typed UI Automator/cross-app execution profile |
| `--cross-app` | Declare cross-app execution profile semantics |
| `--format FORMAT` | Choices: text, json |

### `devtool android smoke`

Run reusable Android install/upgrade/lifecycle/signature smoke gates

| Argument / option | Meaning |
| --- | --- |
| `GATE` | Choices: install, upgrade, cold-start, restart, restore-restart, process-death, signed-artifact |
| `--apk APK` | Accepted by this command. |
| `--old-apk OLD_APK` | Accepted by this command. |
| `--package PACKAGE` | Accepted by this command. |
| `--activity ACTIVITY` | Accepted by this command. |
| `--expected-cert-sha256 EXPECTED_CERT_SHA256` | Accepted by this command. |
| `--serial SERIAL` | Android device serial/transport ID |
| `--policy POLICY` | Device selection policy Choices: exactly-one, serial, preferred, first-physical, first-emulator |
| `--format FORMAT` | Choices: text, json |

### `devtool instrumentation`

Run Android instrumentation for the selected target

| Argument / option | Meaning |
| --- | --- |
| `--class INSTRUMENTATION_CLASSES` | Accepted by this command. |
| `--method INSTRUMENTATION_METHODS` | Accepted by this command. |
| `--annotation INSTRUMENTATION_ANNOTATIONS` | Accepted by this command. |
| `--not-annotation INSTRUMENTATION_NOT_ANNOTATIONS` | Accepted by this command. |
| `--size INSTRUMENTATION_SIZE` | Choices: small, medium, large |
| `--runner-arg INSTRUMENTATION_RUNNER_ARGS` | Accepted by this command. |
| `--skip-build` | Accepted by this command. |
| `--format FORMAT` | Choices: text, json |

### `devtool termux`

Inspect native Termux providers, packages, and PRoot guests

**Subcommands:** `doctor`, `packages`, `explain`

### `devtool termux doctor`

Run the detailed Termux/provider doctor

| Argument / option | Meaning |
| --- | --- |
| `--distro DISTRO` | Probe an installed PRoot-Distro guest by name |
| `--explain` | Include provider selection and rejection reasons |
| `--format FORMAT` | Choices: text, json |

### `devtool termux packages`

Show read-only Termux package recommendations

| Argument / option | Meaning |
| --- | --- |
| `--format FORMAT` | Choices: text, json |

### `devtool termux explain`

Explain Termux tool-provider decisions

| Argument / option | Meaning |
| --- | --- |
| `--format FORMAT` | Choices: text, json |

### `devtool detect`

| Argument / option | Meaning |
| --- | --- |
| `--format FORMAT` | Choices: text, json |

### `devtool handoff`

| Argument / option | Meaning |
| --- | --- |
| `-o, --output OUTPUT` | Accepted by this command. |

### `devtool hud`

| Argument / option | Meaning |
| --- | --- |
| `--state-dir TASKER_DIR` | Directory containing devtool live HUD state |
| `--mode MODE` | Run HUD renderer: Android app bridge, terminal, web, studio, or GTK4 desktop Choices: auto, android-gui, android-bridge, termux-gui, terminal, web, studio, desktop, desktop-window, gtk4-window, gtk-window, desktop-notify, v3, v4, v5, v6, console, dashboard, all, unix, gnome |
| `--host HOST` | Host/IP for HUD web/studio modes |
| `--port PORT` | Port for HUD web/studio modes |
| `--poll-interval POLL_INTERVAL` | Seconds between HUD refreshes |
| `--recent-events RECENT_EVENTS` | Recent JSONL events to show/serve |
| `--once` | Render/update once and exit |
| `--follow-active` | Follow the global active HUD directory written by auto-HUD |
| `--overlay` | Also publish runs to the enabled Android floating overlay HUD |
| `--no-overlay` | Do not open the floating overlay |

### `devtool android-hud-bridge`

Display chroot/proot Android HUD bridge state from the Termux host

**Aliases:** `devtool termux-hud-bridge`

| Argument / option | Meaning |
| --- | --- |
| `--state-dir STATE_DIR` | Shared bridge state directory; defaults to ~/.local/state/devtool-android-hud on Termux; delegated backends receive an explicit shared bridge path |
| `--poll-interval POLL_INTERVAL` | Seconds between HUD refreshes |
| `--recent-events RECENT_EVENTS` | Recent events to show |
| `--once` | Render/update once and exit |
| `--follow-active` | Follow active HUD pointer in the shared bridge directory |
| `--no-follow-active` | Read the shared bridge directory directly |
| `--overlay` | Also publish runs to the enabled Android floating overlay HUD |
| `--no-overlay` | Do not open the floating overlay |

### `devtool history`

| Argument / option | Meaning |
| --- | --- |
| `--limit LIMIT` | Accepted by this command. |

### `devtool rollback`

| Argument / option | Meaning |
| --- | --- |
| `[COMMIT]` | Accepted by this command. |
| `--last` | Accepted by this command. |
| `--transaction ID_OR_PATH` | Resume/perform rollback for an active apply transaction |
| `--failed` | Resume the newest rollback-failed transaction |
| `--list-transactions` | List active apply transactions and their recovery state |

### `devtool validation-status`

Show the active resumable validation session

**Aliases:** `devtool vs`

| Argument / option | Meaning |
| --- | --- |
| `--session SESSION` | Specific validation session ID |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |
| `--events` | Include durable validation events |
| `--after-event AFTER_EVENT` | Return events after this event ID |
| `--event-limit EVENT_LIMIT` | Maximum event records to return |

### `devtool validation-plan`

Show the durable validation phase plan

**Aliases:** `devtool vplan`

| Argument / option | Meaning |
| --- | --- |
| `--session SESSION` | Specific validation session ID |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |

### `devtool validation-pause`

Pause a validation session at the next phase boundary

**Aliases:** `devtool vpause`

| Argument / option | Meaning |
| --- | --- |
| `--session SESSION` | Specific validation session ID |
| `--if-revision IF_REVISION` | Apply only when the session revision matches |
| `--after-phase` | Finish the current phase before pausing |
| `--cancel` | Cancel a pending pause-after-phase request and keep validation running |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |

### `devtool validation-stop`

Stop the current validation phase and leave it retryable

**Aliases:** `devtool vstop`

| Argument / option | Meaning |
| --- | --- |
| `--session SESSION` | Specific validation session ID |
| `--if-revision IF_REVISION` | Apply only when the session revision matches |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |

### `devtool validation-events`

Read durable validation session events

**Aliases:** `devtool vevents`

| Argument / option | Meaning |
| --- | --- |
| `--session SESSION` | Specific validation session ID |
| `--after-event AFTER_EVENT` | Return events after this event ID |
| `--limit LIMIT` | Maximum event records to return |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |

### `devtool validation-report`

Show the durable validation report

**Aliases:** `devtool vreport`

| Argument / option | Meaning |
| --- | --- |
| `--session SESSION` | Specific validation session ID |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |

### `devtool validation-abort`

Abort a resumable validation session

**Aliases:** `devtool va`

| Argument / option | Meaning |
| --- | --- |
| `--session SESSION` | Specific validation session ID |
| `--keep-changes` | Do not roll back a pending artifact transaction |
| `--rollback` | Explicitly roll back a pending artifact transaction |
| `--if-revision IF_REVISION` | Apply only when the session revision matches |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |

### `devtool validation-finalize`

Finalize a completed resumable validation session

**Aliases:** `devtool vf`

| Argument / option | Meaning |
| --- | --- |
| `--session SESSION` | Specific validation session ID |
| `--if-revision IF_REVISION` | Apply only when the session revision matches |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |

### `devtool validation-recover`

Recover an interrupted validation attempt or resume a failed rollback

**Aliases:** `devtool vrec`

| Argument / option | Meaning |
| --- | --- |
| `--session SESSION` | Specific validation session ID |
| `--if-revision IF_REVISION` | Apply only when the session revision matches |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |

### `devtool validation-reset`

Reset one phase and its dependents, or all validation progress

**Aliases:** `devtool vreset`

| Argument / option | Meaning |
| --- | --- |
| `--session SESSION` | Specific validation session ID |
| `--phase PHASE` | Reset this phase and every dependent phase |
| `--from-phase FROM_PHASE` | Alias for --phase |
| `--all` | Reset all validation verdicts while retaining attempt evidence |
| `--if-revision IF_REVISION` | Apply only when the session revision matches |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |

### `devtool validation-accept-plan-change`

Explicitly accept an incompatible validation plan change

**Aliases:** `devtool vapc`

| Argument / option | Meaning |
| --- | --- |
| `--session SESSION` | Specific validation session ID |
| `--reason REASON` | Audit reason for accepting the plan change Required. |
| `--if-revision IF_REVISION` | Apply only when the session revision matches |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |

### `devtool targets`

List configured targets with platform, runner, group, and workflow metadata

**Aliases:** `devtool tg`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit structured target metadata |

### `devtool groups`

List workspace target groups and their expanded targets

**Aliases:** `devtool gr`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit structured workspace-group metadata |

### `devtool jobs`

List reusable polyglot jobs declared under [jobs.*] and target-local jobs

**Aliases:** `devtool jb`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit structured reusable-job metadata |
| `--target TARGET` | Limit target-local jobs to one target while keeping repository jobs visible |

### `devtool target-local-jobs`

Report target-local job adoption, workflow wiring, and migration commands

**Aliases:** `devtool tlj`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit structured target-local adoption metadata |
| `--target TARGET` | Limit the adoption report to one target |

### `devtool workflows`

List configured target workflows without executing them

**Aliases:** `devtool wf`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit structured workflow metadata |

### `devtool plan`

Plan the expanded workflow DAG, safe scheduling waves, scopes, and commands without executing them

**Aliases:** `devtool pl`

| Argument / option | Meaning |
| --- | --- |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--affected` | Select dependency-aware targets affected by Git changes |
| `--affected-base AFFECTED_BASE` | Git base revision for affected selection |
| `--affected-head AFFECTED_HEAD` | Git head revision for affected selection |
| `--variant VARIANT` | Variant within the selected workspace target(s) |
| `--workflow-parallel` | Run independent workflow DAG nodes concurrently only when dependency, read/write-scope, and resource checks prove it safe |
| `--no-workflow-parallel` | Force workflow DAG nodes to execute sequentially |
| `--workflow-max-workers COUNT` | Maximum concurrent safe workflow nodes; 0 uses workspace policy/automatic default |
| `[ACTION]` | Workflow/action to plan, including repository-defined workflow names (default: validate) |
| `--json` | Emit the complete graph and scheduling plan as JSON |
| `--dot` | Emit Graphviz DOT instead of the human plan |
| `--explain` | Show read/write scope and serialization reasons for every node |
| `--show-commands` | Include resolved command argv/cwd in the human plan |
| `--ascii` | Emit 40-column lossless ASCII dependency and safe-wave plan |
| `--mermaid` | Emit Mermaid dependency graph; resource waves are not edges |

### `devtool invoke`

Execute an arbitrary declared workflow action through the canonical workspace planner

| Argument / option | Meaning |
| --- | --- |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--affected` | Select dependency-aware targets affected by Git changes |
| `--affected-base AFFECTED_BASE` | Git base revision for affected selection |
| `--affected-head AFFECTED_HEAD` | Git head revision for affected selection |
| `--variant VARIANT` | Variant within the selected workspace target(s) |
| `--workflow-parallel` | Run independent workflow DAG nodes concurrently only when dependency, read/write-scope, and resource checks prove it safe |
| `--no-workflow-parallel` | Force workflow DAG nodes to execute sequentially |
| `--workflow-max-workers COUNT` | Maximum concurrent safe workflow nodes; 0 uses workspace policy/automatic default |
| `ACTION` | Declared workflow action to execute, such as codegen or regenerate |

### `devtool affected`

Show dependency-aware targets affected by Git changes

**Aliases:** `devtool aff`

| Argument / option | Meaning |
| --- | --- |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--variant VARIANT` | Variant within the selected workspace target(s) |
| `--base BASE` | Git base revision; default is the current working tree |
| `--head HEAD` | Git head revision; with --base uses base...head |
| `--explain` | Explain why every target is selected |
| `--json` | Emit structured affected-target metadata |

### `devtool ui-contract`

Inspect shared UI preferences, capabilities, responsive policy, progress/diagnostics, confirmation/notification policy, tokens, actions, and navigation

**Aliases:** `devtool uic`

| Argument / option | Meaning |
| --- | --- |
| `--surface SURFACE` | Render one frontend surface contract Choices: machine, desktop, tui, android, hud |
| `--width WIDTH` | Viewport width/terminal columns for responsive-policy inspection |
| `--height HEIGHT` | Viewport height/terminal rows for responsive-policy inspection |
| `--require-capability REQUIRE_CAPABILITY` | Negotiate one required UI capability; repeatable |
| `--json` | Emit the complete machine-readable UI Core contract |

### `devtool preflight`

Check expanded workflow roots, environments, runners, tools, and output safety

**Aliases:** `devtool pf`

| Argument / option | Meaning |
| --- | --- |
| `--target TARGET` | Named target/profile from .devtool.toml |
| `--group GROUP` | Workspace target group; repeatable |
| `--all-targets` | Select every configured workspace target |
| `--affected` | Select dependency-aware targets affected by Git changes |
| `--affected-base AFFECTED_BASE` | Git base revision for affected selection |
| `--affected-head AFFECTED_HEAD` | Git head revision for affected selection |
| `--variant VARIANT` | Variant within the selected workspace target(s) |
| `--workflow WORKFLOW` | Workflow/action to inspect; default: all declared workflows |
| `--json` | Emit structured graph-aware preflight metadata |

### `devtool control-center`

Launch the Devtool desktop Control Center

**Aliases:** `devtool gui`

| Argument / option | Meaning |
| --- | --- |
| `--backend BACKEND` | Control Center backend; GTK4 is the only desktop backend Choices: auto, gtk4 |
| `--target TARGET` | Named target/profile from .devtool.toml (also accepted globally before the command) |
| `--variant VARIANT` | Build/test variant within the selected target (also accepted globally before the command) |
| `--state-dir STATE_DIR` | Directory containing devtool live HUD state |
| `--follow-active` | Follow the global active HUD directory |
| `--history-limit HISTORY_LIMIT` | Artifact history rows to load in the GTK4 shell |
| `--recent-events RECENT_EVENTS` | Recent HUD events to load in the GTK4 shell |
| `--poll-interval POLL_INTERVAL` | Seconds between GTK4 shell refreshes |
| `--page PAGE` | Initial GTK4 shell page Choices: overview, runs, history, artifacts, diagnostics, repositories, targets, run, settings |
| `--once` | Print a read-only GTK4 Control Center JSON snapshot and exit |
| `--startup-test` | Open one GTK frame, record startup diagnostics, then exit |

### `devtool ipc-server`

Run or start the local Control Center IPC broker

| Argument / option | Meaning |
| --- | --- |
| `--socket SOCKET` | Override the Unix socket path |
| `--state-dir STATE_DIR` | Load initial run snapshots from this state directory |
| `--foreground` | Run the broker in the foreground |

### `devtool actions`

GitHub Actions API: inspect, dispatch, monitor and explicitly control runs

**Subcommands:** `adopt`, `adoption-doctor`, `android`, `artifacts`, `baseline`, `cancel`, `ci`, `collect`, `detail`, `diagnose`, `download`, `extensions`, `jobs`, `lineage`, `logs`, `pipeline`, `plan`, `provider-distributions`, `provider-doctor`, `provider-export`, `provider-plan`, `qualification`, `recent`, `release`, `rerun`, `resume`, `review`, `run`, `runs`, `status`, `ui`, `verify`, `view`, `watch`, `workflow`, `workflows`, `overview`

### `devtool actions adopt`

Review/generate source-pinned GitHub Actions adoption

| Argument / option | Meaning |
| --- | --- |
| `--target ADOPT_TARGET` | Target with native target-local jobs Required. |
| `--workflow ADOPT_WORKFLOW` | Workflow under the target Required. |
| `--devtool-sha DEVTOOL_SHA` | Immutable Devtool runtime Git SHA Required. |
| `--yes` | Approve local generation of workflow and wrapper alias |
| `--verify` | Verify already generated adoption files |
| `--json` | Emit result as JSON |

### `devtool actions adoption-doctor`

Review A09 host eligibility for configured workflows without changing files

| Argument / option | Meaning |
| --- | --- |
| `--target ADOPT_TARGET` | Limit to a configured target |
| `--json` | Emit review-only JSON |

### `devtool actions android`

Android builds on GitHub Actions and verified APK/AAB acquisition

**Subcommands:** `discover`, `plan`, `run`, `collect`, `verify`, `unsigned-plan`, `unsigned-run`, `unsigned-check`, `unsigned-import`, `unsigned-sign`, `aab-plan`, `aab-run`, `aab-check`, `aab-import`, `aab-sign`, `options-repair`, `release-plan`, `release-run`, `release-import`, `release-sign`, `release-batch-plan`, `release-batch-run`, `release-batch-check`, `release-batch-import`, `release-batch-sign`

### `devtool actions android discover`

Discover statically declared Android app modules

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--project-dir PROJECT_DIR` | Gradle project directory; auto-discovered if omitted |
| `--discovery-mode DISCOVERY_MODE` | Static read-only model or explicit execution of reviewed Gradle project code Choices: static, gradle |

### `devtool actions android plan`

Validate source parity and Android workflow inputs without dispatch

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--project-dir PROJECT_DIR` | Gradle project directory; auto-discovered if omitted |
| `--module MODULE` | Gradle application module, e.g. app |
| `--variant VARIANT` | Reviewed Android variant, including registered flavorRelease |
| `--format FORMAT` | Android output format Choices: apk, aab |
| `--signing-mode SIGNING_MODE` | Release signing route: local unsigned LIFE (default) or explicit protected GitHub signing Choices: local, github |
| `--ref REF` | Exact GitHub branch/tag to dispatch Required. |
| `--workflow WORKFLOW` | Selected Android workflow YAML filename |
| `--expected-package EXPECTED_PACKAGE` | Expected package/application ID, if dynamic Gradle sources |
| `--discovery-mode DISCOVERY_MODE` | Static read-only model or explicit execution of reviewed Gradle project code Choices: static, gradle |

### `devtool actions android run`

Dispatch an Android build only with explicit --yes

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--project-dir PROJECT_DIR` | Gradle project directory; auto-discovered if omitted |
| `--module MODULE` | Gradle application module, e.g. app |
| `--variant VARIANT` | Reviewed Android variant, including registered flavorRelease |
| `--format FORMAT` | Android output format Choices: apk, aab |
| `--signing-mode SIGNING_MODE` | Release signing route: local unsigned LIFE (default) or explicit protected GitHub signing Choices: local, github |
| `--ref REF` | Exact GitHub branch/tag to dispatch Required. |
| `--workflow WORKFLOW` | Selected Android workflow YAML filename |
| `--expected-package EXPECTED_PACKAGE` | Expected package/application ID, if dynamic Gradle sources |
| `--yes` | Explicitly authorize remote workflow dispatch |
| `--discovery-mode DISCOVERY_MODE` | Static read-only model or explicit execution of reviewed Gradle project code Choices: static, gradle |

### `devtool actions android collect`

Fetch or verify and inspect an APK/AAB, registering output as unqualified

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact GitHub Actions run ID Required. |
| `--artifact-id ARTIFACT_ID` | Exact GitHub Actions artifact ID Required. |
| `--format FORMAT` | Android output format Choices: apk, aab |
| `--variant VARIANT` | Build variant Choices: debug, release |
| `--expected-package EXPECTED_PACKAGE` | Expected package/application ID |
| `--expected-cert-sha256 EXPECTED_CERT_SHA256` | Expected SHA-256 signing certificate (mandatory for release) |
| `--member MEMBER` | Exact APK/AAB member in ZIP if multiple |
| `--download` | Download the archive first, instead of verifying an existing archive |
| `--workflow WORKFLOW` | Exact authenticated GitHub Android workflow YAML name/path |
| `--apk-role APK_ROLE` | Explicit Android APK role; test APKs require target and test package pins Choices: application, instrumentation |
| `--expected-target-package EXPECTED_TARGET_PACKAGE` | Expected application package targeted by instrumentation APK |
| `--expected-source-sha EXPECTED_SOURCE_SHA` | Expected exact 40-hex GitHub source commit (required for custom workflow) |
| `--expected-run-attempt EXPECTED_RUN_ATTEMPT` | Expected GitHub run attempt (required for custom workflow) |

### `devtool actions android verify`

Verify a downloaded Android archive and inspect its package

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact GitHub Actions run ID Required. |
| `--artifact-id ARTIFACT_ID` | Exact GitHub Actions artifact ID Required. |
| `--format FORMAT` | Android output format Choices: apk, aab |
| `--variant VARIANT` | Build variant Choices: debug, release |
| `--expected-package EXPECTED_PACKAGE` | Expected package/application ID |
| `--expected-cert-sha256 EXPECTED_CERT_SHA256` | Expected SHA-256 signing certificate (mandatory for release) |
| `--member MEMBER` | Exact APK/AAB member in ZIP if multiple |
| `--workflow WORKFLOW` | Exact authenticated GitHub Android workflow YAML name/path |
| `--apk-role APK_ROLE` | Explicit Android APK role; test APKs require target and test package pins Choices: application, instrumentation |
| `--expected-target-package EXPECTED_TARGET_PACKAGE` | Expected application package targeted by instrumentation APK |
| `--expected-source-sha EXPECTED_SOURCE_SHA` | Expected exact 40-hex GitHub source commit (required for custom workflow) |
| `--expected-run-attempt EXPECTED_RUN_ATTEMPT` | Expected GitHub run attempt (required for custom workflow) |

### `devtool actions android unsigned-plan`

Preview source-bound, unsigned GitHub release candidate

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--ref REF` | Exact branch or tag to dispatch; source must match local HEAD Required. |
| `--consumer CONSUMER` | Exact registered Android consumer ID (required if repository has multiple variants) |

### `devtool actions android unsigned-run`

Dispatch dedicated unsigned candidate workflow (requires --yes)

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--ref REF` | Exact branch or tag to dispatch; source must match local HEAD Required. |
| `--yes` | Explicitly authorize remote workflow dispatch |
| `--consumer CONSUMER` | Exact registered Android consumer ID (required if repository has multiple variants) |

### `devtool actions android unsigned-check`

Verify exact GitHub unsigned archive and APK (read-only)

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact GitHub Actions run ID |
| `--artifact-id ARTIFACT_ID` | Exact GitHub artifact ID |
| `--expected-sha EXPECTED_SHA` | Expected GitHub commit SHA (40 hex characters) |
| `--consumer CONSUMER` | Exact registered Android consumer ID (required if repository has multiple variants) |

### `devtool actions android unsigned-import`

Download, validate and register unsigned GitHub APK

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact GitHub Actions run ID |
| `--artifact-id ARTIFACT_ID` | Exact GitHub artifact ID |
| `--expected-sha EXPECTED_SHA` | Expected GitHub commit SHA (40 hex characters) |
| `--download` | Download the archive first, instead of verifying an existing archive |
| `--consumer CONSUMER` | Exact registered Android consumer ID (required if repository has multiple variants) |

### `devtool actions android unsigned-sign`

Locally align, LIFE-sign and verify imported candidate (requires --yes)

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--registry-id REGISTRY_ID` | Exact imported unsigned registry ID |
| `--signing-identity SIGNING_IDENTITY` | Canonical registered LIFE signing identity (optional) |
| `--signing-target SIGNING_TARGET` | Devtool Android signing target |
| `--yes` | Explicitly authorize remote workflow dispatch |

### `devtool actions android aab-plan`

Preview unsigned Android AAB workflow with source pin

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--ref REF` | Exact branch or tag to dispatch; source must match local HEAD Required. |

### `devtool actions android aab-run`

Dispatch unsigned AAB candidate (requires --yes)

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--ref REF` | Exact branch or tag to dispatch; source must match local HEAD Required. |
| `--yes` | Explicitly authorize remote workflow dispatch |

### `devtool actions android aab-check`

Inspect source-bound AAB with bundletool (read-only)

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact GitHub Actions run ID |
| `--artifact-id ARTIFACT_ID` | Exact GitHub artifact ID |
| `--expected-sha EXPECTED_SHA` | Expected GitHub commit SHA (40 hex characters) |

### `devtool actions android aab-import`

Download, verify and register unsigned AAB

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact GitHub Actions run ID |
| `--artifact-id ARTIFACT_ID` | Exact GitHub artifact ID |
| `--expected-sha EXPECTED_SHA` | Expected GitHub commit SHA (40 hex characters) |
| `--download` | Download the archive first, instead of verifying an existing archive |

### `devtool actions android aab-sign`

Locally sign imported AAB using LIFE (requires --yes)

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--registry-id REGISTRY_ID` | Exact imported unsigned registry ID |
| `--signing-identity SIGNING_IDENTITY` | Canonical registered LIFE signing identity (optional) |
| `--signing-target SIGNING_TARGET` | Devtool Android signing target |
| `--yes` | Explicitly authorize remote workflow dispatch |

### `devtool actions android options-repair`

Preview/apply exact protected Gradle JVM property migration

| Argument / option | Meaning |
| --- | --- |
| `--project-dir PROJECT_DIR` | Gradle project root, repository relative |
| `--policy-target POLICY_TARGET` | Canonical Devtool target owning the protected JVM policy |
| `--expected-sha256 EXPECTED_SHA256` | Exact preview SHA-256, required for apply |
| `--json` | Emit JSON |

### `devtool actions android release-plan`

Validate source parity and Android workflow inputs without dispatch

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--project-dir PROJECT_DIR` | Gradle project directory; auto-discovered if omitted |
| `--module MODULE` | Gradle application module, e.g. app |
| `--variant VARIANT` | Reviewed Android variant, including registered flavorRelease |
| `--format FORMAT` | Android output format Choices: apk, aab |
| `--ref REF` | Exact GitHub branch/tag to dispatch Required. |
| `--expected-package EXPECTED_PACKAGE` | Expected package/application ID, if dynamic Gradle sources |
| `--discovery-mode DISCOVERY_MODE` | Static read-only model or explicit execution of reviewed Gradle project code Choices: static, gradle |
| `--consumer CONSUMER` | Registered Android consumer (required to advance through LIFE) |

### `devtool actions android release-run`

Dispatch an Android build only with explicit --yes

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--project-dir PROJECT_DIR` | Gradle project directory; auto-discovered if omitted |
| `--module MODULE` | Gradle application module, e.g. app |
| `--variant VARIANT` | Reviewed Android variant, including registered flavorRelease |
| `--format FORMAT` | Android output format Choices: apk, aab |
| `--ref REF` | Exact GitHub branch/tag to dispatch Required. |
| `--expected-package EXPECTED_PACKAGE` | Expected package/application ID, if dynamic Gradle sources |
| `--yes` | Explicitly authorize remote workflow dispatch |
| `--discovery-mode DISCOVERY_MODE` | Static read-only model or explicit execution of reviewed Gradle project code Choices: static, gradle |
| `--consumer CONSUMER` | Registered Android consumer (required to advance through LIFE) |

### `devtool actions android release-import`

Download, validate and register unsigned GitHub APK

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact GitHub Actions run ID |
| `--artifact-id ARTIFACT_ID` | Exact GitHub artifact ID |
| `--expected-sha EXPECTED_SHA` | Expected GitHub commit SHA (40 hex characters) |
| `--download` | Download the archive first, instead of verifying an existing archive |
| `--consumer CONSUMER` | Exact registered Android consumer ID (required if repository has multiple variants) |

### `devtool actions android release-sign`

Locally align, LIFE-sign and verify imported candidate (requires --yes)

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--registry-id REGISTRY_ID` | Exact imported unsigned registry ID |
| `--signing-identity SIGNING_IDENTITY` | Canonical registered LIFE signing identity (optional) |
| `--signing-target SIGNING_TARGET` | Devtool Android signing target |
| `--yes` | Explicitly authorize remote workflow dispatch |
| `--consumer CONSUMER` | Exact imported consumer identity |

### `devtool actions android release-batch-plan`

One hosted unsigned build; verified per-variant local LIFE: release-batch-plan

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Source-locked Android batch github_repo; no GitHub signing keys |
| `--auth AUTH` | Source-locked Android batch auth; no GitHub signing keys |
| `--json` | Source-locked Android batch json; no GitHub signing keys |
| `--consumers CONSUMERS` | Source-locked Android batch consumers; no GitHub signing keys |
| `--ref REF` | Source-locked Android batch ref; no GitHub signing keys |
| `--source-root SOURCE_ROOT` | Source-locked Android batch source_root; no GitHub signing keys |

### `devtool actions android release-batch-run`

One hosted unsigned build; verified per-variant local LIFE: release-batch-run

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Source-locked Android batch github_repo; no GitHub signing keys |
| `--auth AUTH` | Source-locked Android batch auth; no GitHub signing keys |
| `--json` | Source-locked Android batch json; no GitHub signing keys |
| `--consumers CONSUMERS` | Source-locked Android batch consumers; no GitHub signing keys |
| `--ref REF` | Source-locked Android batch ref; no GitHub signing keys |
| `--source-root SOURCE_ROOT` | Source-locked Android batch source_root; no GitHub signing keys |
| `--yes` | Source-locked Android batch yes; no GitHub signing keys |

### `devtool actions android release-batch-check`

One hosted unsigned build; verified per-variant local LIFE: release-batch-check

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Source-locked Android batch github_repo; no GitHub signing keys |
| `--auth AUTH` | Source-locked Android batch auth; no GitHub signing keys |
| `--json` | Source-locked Android batch json; no GitHub signing keys |
| `--consumers CONSUMERS` | Source-locked Android batch consumers; no GitHub signing keys |
| `--run-id RUN_ID` | Source-locked Android batch run_id; no GitHub signing keys |
| `--expected-sha EXPECTED_SHA` | Source-locked Android batch expected_sha; no GitHub signing keys |
| `--artifact ARTIFACT` | Source-locked Android batch artifact; no GitHub signing keys |
| `--download` | Source-locked Android batch download; no GitHub signing keys |

### `devtool actions android release-batch-import`

One hosted unsigned build; verified per-variant local LIFE: release-batch-import

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Source-locked Android batch github_repo; no GitHub signing keys |
| `--auth AUTH` | Source-locked Android batch auth; no GitHub signing keys |
| `--json` | Source-locked Android batch json; no GitHub signing keys |
| `--consumers CONSUMERS` | Source-locked Android batch consumers; no GitHub signing keys |
| `--run-id RUN_ID` | Source-locked Android batch run_id; no GitHub signing keys |
| `--expected-sha EXPECTED_SHA` | Source-locked Android batch expected_sha; no GitHub signing keys |
| `--artifact ARTIFACT` | Source-locked Android batch artifact; no GitHub signing keys |
| `--download` | Source-locked Android batch download; no GitHub signing keys |
| `--yes` | Source-locked Android batch yes; no GitHub signing keys |

### `devtool actions android release-batch-sign`

One hosted unsigned build; verified per-variant local LIFE: release-batch-sign

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Source-locked Android batch github_repo; no GitHub signing keys |
| `--auth AUTH` | Source-locked Android batch auth; no GitHub signing keys |
| `--json` | Source-locked Android batch json; no GitHub signing keys |
| `--consumers CONSUMERS` | Source-locked Android batch consumers; no GitHub signing keys |
| `--registry REGISTRY` | Source-locked Android batch registry; no GitHub signing keys |
| `--yes` | Source-locked Android batch yes; no GitHub signing keys |

### `devtool actions artifacts`

List artifacts associated with a specific GitHub workflow run

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact remote GitHub workflow run ID |
| `--request-id REQUEST_ID` | Local ACTIONS-02 dispatch request ID (pinned to one repository and run) |

### `devtool actions baseline`

Print read-only ACTIONS-00/01 capability baseline

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |

### `devtool actions cancel`

Request cancellation of a remote workflow run (explicit confirmation)

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact remote GitHub workflow run ID |
| `--request-id REQUEST_ID` | Local ACTIONS-02 dispatch request ID (pinned to one repository and run) |
| `--yes` | Confirm remote run cancellation |

### `devtool actions ci`

Advanced polyglot GitHub CI: plan, run, check and collect

**Subcommands:** `plan`, `run`, `check`, `collect`

### `devtool actions ci plan`

G5 polyglot matrix plan

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--ref REF` | Branch or tag; remote must match local HEAD Required. |
| `--languages LANGUAGES` | Comma-separated subset: python,go,rust,java |
| `--systems SYSTEMS` | Comma-separated subset of ubuntu-latest,windows-latest |

### `devtool actions ci run`

G5 polyglot matrix run

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--ref REF` | Branch or tag; remote must match local HEAD Required. |
| `--yes` | Explicitly dispatch matrix workflow |
| `--languages LANGUAGES` | Comma-separated subset: python,go,rust,java |
| `--systems SYSTEMS` | Comma-separated subset of ubuntu-latest,windows-latest |

### `devtool actions ci check`

G5 polyglot matrix check

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact GitHub workflow run ID Required. |
| `--expected-sha EXPECTED_SHA` | Exact 40-hex GitHub source commit Required. |
| `--languages LANGUAGES` | Comma-separated subset: python,go,rust,java |
| `--systems SYSTEMS` | Comma-separated subset of ubuntu-latest,windows-latest |

### `devtool actions ci collect`

G5 polyglot matrix collect

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact GitHub workflow run ID Required. |
| `--expected-sha EXPECTED_SHA` | Exact 40-hex GitHub source commit Required. |
| `--download` | Download canonical GitHub artifact archives |
| `--languages LANGUAGES` | Comma-separated subset: python,go,rust,java |
| `--systems SYSTEMS` | Comma-separated subset of ubuntu-latest,windows-latest |

### `devtool actions collect`

Collect a uniquely named SHA-256 archive from verified exact dispatch

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--request-id REQUEST_ID` | Exact existing dispatch request ID (no guessing) Required. |
| `--artifact-name ARTIFACT_NAME` | Exact artifact name; never select first or latest Required. |
| `--yes` | Approve local verified artifact download |

### `devtool actions detail`

Inspect exact local dispatch/pipeline identity

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--json` | Emit Actions response as JSON |
| `--kind KIND` | Inspect exact local dispatch/pipeline identity Choices: dispatch, pipeline |
| `--identity IDENTITY` | Inspect exact local dispatch/pipeline identity Required. |

### `devtool actions diagnose`

Diagnose GitHub repository identity, access and workflow discovery without mutation

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |

### `devtool actions download`

Download, SHA-256 verify and register an unextracted Actions ZIP

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact remote GitHub workflow run ID |
| `--request-id REQUEST_ID` | Local ACTIONS-02 dispatch request ID (pinned to one repository and run) |
| `--artifact-id ARTIFACT_ID` | Exact GitHub Actions artifact ID Required. |

### `devtool actions extensions`

Optional runner, metrics, GitLab and draft publishing capabilities

**Subcommands:** `metrics`, `runners`, `gitlab-pipelines`, `publish-plan`, `publish`

### `devtool actions extensions metrics`

G7 optional metrics

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--limit LIMIT` | Recent runs, maximum 100 |

### `devtool actions extensions runners`

G7 optional runners

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--trust-file TRUST_FILE` | Optional local JSON trusted runner profiles |

### `devtool actions extensions gitlab-pipelines`

G7 optional gitlab-pipelines

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--gitlab-project-id GITLAB_PROJECT_ID` | Numeric GitLab.com project ID Required. |
| `--limit LIMIT` | Max 50 pipelines |
| `--enable-gitlab` | Explicitly opt in to independent GitLab read-only provider |

### `devtool actions extensions publish-plan`

G7 optional publish-plan

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--registry-id REGISTRY_ID` | Required. |
| `--tag TAG` | Required. |
| `--asset-name ASSET_NAME` | Required. |
| `--expected-sha EXPECTED_SHA` | Required. |
| `--format FORMAT` | APK or AAB format Choices: apk, aab |

### `devtool actions extensions publish`

G7 optional publish

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--registry-id REGISTRY_ID` | Required. |
| `--tag TAG` | Required. |
| `--asset-name ASSET_NAME` | Required. |
| `--expected-sha EXPECTED_SHA` | Required. |
| `--format FORMAT` | APK or AAB format Choices: apk, aab |
| `--allow-unqualified-upload` | Consent to upload signed-but-unqualified artifact |
| `--yes` | Explicit remote asset upload approval |

### `devtool actions jobs`

Inspect run and job/step states

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact remote GitHub workflow run ID |
| `--request-id REQUEST_ID` | Local ACTIONS-02 dispatch request ID (pinned to one repository and run) |

### `devtool actions lineage`

R04 read-only source/run/artifact lineage (offline; no remote qualification)

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--json` | Emit Actions lineage as JSON |
| `--pipeline-id PIPELINE_ID` | Exact local ACTIONS-06 pipeline ID (required) Required. |
| `--live` | Opt in to credentialed read-only GitHub run GET; no mutations or qualification |
| `--auth AUTH` | GitHub read credential source for --live: auto, env, gh, none Choices: auto, env, gh, none |

### `devtool actions logs`

Download remote workflow run logs as a bounded unextracted ZIP to private state

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact remote GitHub workflow run ID |
| `--request-id REQUEST_ID` | Local ACTIONS-02 dispatch request ID (pinned to one repository and run) |

### `devtool actions pipeline`

Cross-boundary local/remote DAG with frozen source and durable resume

**Subcommands:** `cancel`, `plan`, `run`, `status`, `resume`

### `devtool actions pipeline cancel`

Request durable pipeline stop; does not cancel remote GitHub jobs

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit Actions response as JSON |
| `--pipeline-id PIPELINE_ID` | Previously persisted pipeline ID Required. |
| `--yes` | Authorize pending pipeline work |

### `devtool actions pipeline plan`

Validate local Git and remote GitHub DAG without mutations

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--file FILE` | Checked-in repository-relative pipeline JSON Required. |

### `devtool actions pipeline run`

Create durable pipeline journal and execute ready nodes

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--file FILE` | Checked-in repository-relative pipeline JSON Required. |
| `--yes` | Authorize sequential local and remote actions |

### `devtool actions pipeline status`

Read local durable pipeline states (no GitHub operations)

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--pipeline-id PIPELINE_ID` | Previously persisted pipeline ID Required. |

### `devtool actions pipeline resume`

Continue waiting pipeline nodes, never repeat uncertain dispatch

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--pipeline-id PIPELINE_ID` | Previously persisted pipeline ID Required. |
| `--yes` | Authorize pending pipeline work |

### `devtool actions plan`

Validate workflow ref and inputs without dispatching

| Argument / option | Meaning |
| --- | --- |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--input INPUT` | Workflow input KEY=VALUE (repeatable) |
| `--inputs-json INPUTS_JSON` | Path to JSON object of workflow inputs |
| `--json` | Emit Actions response as JSON |
| `--ref REF` | GitHub branch or tag to dispatch (required) Required. |
| `WORKFLOW_ID` | Workflow ID or YAML filename |
| `--resume-if-existing` | Safely reuse an exact unresolved dispatch request; never POST twice |

### `devtool actions provider-distributions`

Review or verify typed Go/Python distribution outputs (read-only)

| Argument / option | Meaning |
| --- | --- |
| `--target ADOPT_TARGET` | Target declaring ci/devtool-distributions.json Required. |
| `--workflow ADOPT_WORKFLOW` | Workflow owning selected distribution outputs Required. |
| `--json` | Emit result as JSON |
| `--plan` | Review manifest and source policy without requiring produced output bytes |

### `devtool actions provider-doctor`

Inspect allowlisted hosted language providers per workflow

| Argument / option | Meaning |
| --- | --- |
| `--target ADOPT_TARGET` | Optional configured target filter |
| `--json` | Emit review-only JSON |
| `--all` | Include all reviewed hosted/local profiles and installed toolchain status |

### `devtool actions provider-export`

Review, write or verify source-bound provider-hosted workflow

| Argument / option | Meaning |
| --- | --- |
| `--affected` | Conservatively select all affected configured targets (multi-job only) |
| `--affected-base PROVIDER_AFFECTED_BASE` | Explicit Git merge base for affected source ownership |
| `--affected-head PROVIDER_AFFECTED_HEAD` | Git head for affected selection; with no base use head^..head |
| `--cache-locks` | Opt in to pinned cache of trusted Go compiler outputs only; multi-job only |
| `--devtool-sha DEVTOOL_SHA` | Immutable Devtool runtime Git SHA Required. |
| `--host-profiles PROVIDER_HOST_PROFILES` | Comma-separated reviewed hosted profile IDs (bounded fanout; multi-job only) |
| `--json` | Emit result as JSON |
| `--mode PROVIDER_MODE` | Export mode: native single-job fallback or source-bound per-node multi-job Choices: single-job, multi-job |
| `--plan` | Read-only reviewed export plan; incompatible with write/verify |
| `--target ADOPT_TARGET` | Required for one target; omit with --affected for workspace selection |
| `--verify` | Verify already generated adoption files |
| `--workflow ADOPT_WORKFLOW` | Workflow under the target Required. |
| `--yes` | Write new reviewed hosted workflow (no commit/dispatch) |

### `devtool actions provider-plan`

Review exact source-bound native DAG provider plan (never dispatches)

| Argument / option | Meaning |
| --- | --- |
| `--target ADOPT_TARGET` | Target with native target-local jobs Required. |
| `--workflow ADOPT_WORKFLOW` | Workflow under the target Required. |
| `--devtool-sha DEVTOOL_SHA` | Immutable Devtool runtime Git SHA Required. |
| `--json` | Emit result as JSON |
| `--expect-plan-sha EXPECT_PLAN_SHA` | Require exact reviewed DAG/provider SHA-256 |

### `devtool actions qualification`

G8 Actions qualification and historical evidence reconciliation

**Subcommands:** `report`, `check`

### `devtool actions qualification report`

G8 read-only qualification report

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit Actions response as JSON |

### `devtool actions qualification check`

G8 read-only qualification check

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit Actions response as JSON |
| `--require-complete` | Fail if any required live/historical qualification is pending |

### `devtool actions recent`

Read-only local dispatch and pipeline history

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--json` | Emit Actions response as JSON |
| `--limit LIMIT` | Read-only local dispatch and pipeline history |

### `devtool actions release`

Independent-policy Android release attestation and qualification

**Subcommands:** `verify`, `setup`, `readiness`, `local-build`, `local-verify`, `local-evidence`, `local-handoff`

### `devtool actions release verify`

Read-only qualification of already-registered signed release

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact GitHub Actions run ID Required. |
| `--artifact-id ARTIFACT_ID` | Exact GitHub Actions artifact ID Required. |
| `--member MEMBER` | Exact APK/AAB member in ZIP if multiple |
| `--policy POLICY` | Absolute path to private owner-controlled release policy JSON (0600) Required. |

### `devtool actions release setup`

Guided preview or --yes release provisioning

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--ref REF` | Exact protected release branch |
| `--policy POLICY` | Private external release policy path (default ~/.config/devtool/release-policy.json) |
| `--project-dir PROJECT_DIR` | Gradle project relative to repository |
| `--module MODULE` | Android Gradle application module |
| `--application-id APPLICATION_ID` | Package ID (default statically inferred) |
| `--format FORMAT` | Release package format: apk or aab |
| `--minimum-version-code MINIMUM_VERSION_CODE` | Minimum allowed release versionCode |
| `--yes` | Explicitly authorize provisioning |
| `--signing-identity SIGNING_IDENTITY` | Existing Devtool signing identity |
| `--signing-target SIGNING_TARGET` | Target with release signing profile |
| `--rotate-secrets` | Explicitly replace existing GitHub signing secrets from the Devtool signer |

### `devtool actions release readiness`

Check protected ref, signing secret names and private policy

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--ref REF` | Exact protected release branch |
| `--policy POLICY` | Private external release policy path (default ~/.config/devtool/release-policy.json) |
| `--signing-identity SIGNING_IDENTITY` | Accepted by this command. |
| `--signing-target SIGNING_TARGET` | Accepted by this command. |

### `devtool actions release local-build`

Preview or execute local signed release build without Github qualification

| Argument / option | Meaning |
| --- | --- |
| `--project-dir PROJECT_DIR` | Gradle project within checked-out repository |
| `--module MODULE` | Gradle application module |
| `--format FORMAT` | Local package format Choices: apk, aab |
| `--signing-identity SIGNING_IDENTITY` | Existing registered LIFE signing identity ID |
| `--signing-target SIGNING_TARGET` | Devtool Android signing target |
| `--application-id APPLICATION_ID` | Expected application ID (auto-discovered if omitted) |
| `--minimum-version-code MINIMUM_VERSION_CODE` | Minimum acceptable versionCode |
| `--json` | Print machine-readable JSON |
| `--yes` | Explicitly authorize local Gradle execution |

### `devtool actions release local-verify`

Verify local package manifest and pinned LIFE signing certificate

| Argument / option | Meaning |
| --- | --- |
| `--project-dir PROJECT_DIR` | Gradle project within checked-out repository |
| `--module MODULE` | Gradle application module |
| `--format FORMAT` | Local package format Choices: apk, aab |
| `--signing-identity SIGNING_IDENTITY` | Existing registered LIFE signing identity ID |
| `--signing-target SIGNING_TARGET` | Devtool Android signing target |
| `--application-id APPLICATION_ID` | Expected application ID (auto-discovered if omitted) |
| `--minimum-version-code MINIMUM_VERSION_CODE` | Minimum acceptable versionCode |
| `--json` | Print machine-readable JSON |
| `--artifact ARTIFACT` | Absolute path to APK/AAB to inspect Required. |

### `devtool actions release local-evidence`

Verify offline signed→unsigned registry lineage and LIFE identity without release promotion

| Argument / option | Meaning |
| --- | --- |
| `--registry-id REGISTRY_ID` | Exact locally signed artifact registry ID (12 hex) Required. |
| `--format FORMAT` | Local package format Choices: apk, aab |
| `--signing-identity SIGNING_IDENTITY` | Existing registered LIFE signing identity ID |
| `--signing-target SIGNING_TARGET` | Devtool Android signing target |
| `--json` | Print machine-readable JSON |
| `--expected-sha EXPECTED_SHA` | Optional exact 40-hex remote source SHA pin |

### `devtool actions release local-handoff`

Read-only preview or explicitly authorized private APK/AAB export and APK install

| Argument / option | Meaning |
| --- | --- |
| `--registry-id REGISTRY_ID` | Exact registered locally signed artifact (12 hex) Required. |
| `--format FORMAT` | Local package format Choices: apk, aab |
| `--signing-identity SIGNING_IDENTITY` | Existing registered LIFE signing identity ID |
| `--signing-target SIGNING_TARGET` | Devtool Android signing target |
| `--json` | Print machine-readable JSON |
| `--expected-sha EXPECTED_SHA` | Mandatory independent exact 40-hex source SHA pin Required. |
| `--operation OPERATION` | Local handoff: export to a new file, or install APK on explicitly named device Choices: export, install Required. |
| `--destination DESTINATION` | Absolute new file path for export; existing files never overwritten |
| `--serial SERIAL` | Exact adb device serial required for install |
| `--allow-unqualified` | Explicitly accept signed-but-unqualified artifact for this local action |
| `--yes` | Execute local file/device mutation after verification and consent; otherwise preview |

### `devtool actions rerun`

Request a new remote workflow attempt (explicit confirmation)

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact remote GitHub workflow run ID |
| `--request-id REQUEST_ID` | Local ACTIONS-02 dispatch request ID (pinned to one repository and run) |
| `--failed-only` | Rerun failed jobs only |
| `--yes` | Confirm remote run retry |

### `devtool actions resume`

Resume a recorded dispatch by its exact request ID

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact remote GitHub workflow run ID |
| `--request-id REQUEST_ID` | Local ACTIONS-02 dispatch request ID (pinned to one repository and run) |

### `devtool actions review`

Review exact dispatch remote source, attempt and jobs (read-only)

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--request-id REQUEST_ID` | Exact existing dispatch request ID (no guessing) Required. |

### `devtool actions run`

Dispatch workflow only with explicit --yes; return run ID if available

| Argument / option | Meaning |
| --- | --- |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--input INPUT` | Workflow input KEY=VALUE (repeatable) |
| `--inputs-json INPUTS_JSON` | Path to JSON object of workflow inputs |
| `--json` | Emit Actions response as JSON |
| `--ref REF` | GitHub branch or tag to dispatch (required) Required. |
| `--yes` | Explicitly authorize remote workflow dispatch |
| `WORKFLOW_ID` | Workflow ID or YAML filename |
| `--resume-if-existing` | Safely reuse an exact unresolved dispatch request; never POST twice |

### `devtool actions runs`

List recent remote workflow runs from selected GitHub repository

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--limit LIMIT` | Maximum workflow runs (1..100) |

### `devtool actions status`

Read repository and authenticated viewer metadata

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |

### `devtool actions ui`

Read-only Termux Textual GitHub Actions workspace

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--json` | Emit Actions response as JSON |
| `--limit LIMIT` | Read-only Termux Textual GitHub Actions workspace |

### `devtool actions verify`

Verify a previously downloaded registered GitHub artifact

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact remote GitHub workflow run ID |
| `--request-id REQUEST_ID` | Local ACTIONS-02 dispatch request ID (pinned to one repository and run) |
| `--artifact-id ARTIFACT_ID` | Exact GitHub Actions artifact ID Required. |

### `devtool actions view`

Inspect one remote workflow run

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact remote GitHub workflow run ID |
| `--request-id REQUEST_ID` | Exact existing dispatch request ID (no guessing) Required. |

### `devtool actions watch`

Poll a remote run in the foreground with finite limits (no daemon)

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact remote GitHub workflow run ID |
| `--request-id REQUEST_ID` | Local ACTIONS-02 dispatch request ID (pinned to one repository and run) |
| `--max-checks MAX_CHECKS` | Maximum status reads (1..120) |
| `--interval INTERVAL` | Seconds between status reads (1..300) |

### `devtool actions workflow`

Inspect a workflow by numeric ID or YAML filename

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `WORKFLOW_ID` | Workflow ID or YAML filename |

### `devtool actions workflows`

List workflows without dispatching them

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |

### `devtool actions overview`

Read-only Actions control center and safe next step

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub repository |
| `--live` | Opt in to credentialed read-only GitHub queries |
| `--width WIDTH` | Terminal width, minimum 40 |
| `--json` | Machine-readable state |

### `devtool transaction`

Inspect or explicitly escrow historic failed transaction markers

**Subcommands:** `inspect`, `retire`, `unretire`

### `devtool transaction inspect`

Read-only historical transaction retirement eligibility

| Argument / option | Meaning |
| --- | --- |
| `TRANSACTION_ID` | Exact transaction ID |
| `--json` | JSON result |

### `devtool transaction retire`

Escrow verified historic active markers; never roll back

| Argument / option | Meaning |
| --- | --- |
| `TRANSACTION_ID` | Exact transaction ID |
| `--preserve-current` | Explicitly preserve HEAD, index, worktree and backups |
| `--dry-run` | Preview only (default unless --yes) |
| `--yes` | Approve marker escrow after current-source checks |
| `--json` | JSON result |

### `devtool transaction unretire`

Restore exact escrowed active markers, without checkout mutation

| Argument / option | Meaning |
| --- | --- |
| `TRANSACTION_ID` | Exact transaction ID |
| `--yes` | Explicitly approve marker restoration |
| `--json` | JSON result |

### `devtool evidence`

Typed evidence and report validation (read-only)

**Subcommands:** `validate`, `normalize`, `lineage`, `merge-tests`

### `devtool evidence validate`

Check version, exact identity and untrusted reported gates

| Argument / option | Meaning |
| --- | --- |
| `RECEIPT` | Accepted by this command. |
| `--commit COMMIT` | Expected exact source commit |
| `--run-id RUN_ID` | Expected GitHub run ID |
| `--attempt ATTEMPT` | Expected run attempt |
| `--json` | Emit JSON |

### `devtool evidence normalize`

Parse an actual test report without claiming hosted authority

| Argument / option | Meaning |
| --- | --- |
| `REPORT` | Accepted by this command. |
| `--format FORMAT` | junit, pytest-junit, trx, go-json, cargo-json, js-junit Required. |
| `--json` | Emit JSON |

### `devtool evidence lineage`

Read-only canonical EXO node and remote evidence lineage

| Argument / option | Meaning |
| --- | --- |
| `--run-id RUN_ID` | run id Required. |
| `--remote-receipt REMOTE_RECEIPT` | remote receipt |
| `--json` | json |

### `devtool evidence merge-tests`

Read-only deterministic test shard merge

| Argument / option | Meaning |
| --- | --- |
| `--receipt RECEIPT` | Test-shard manifest file Required. |
| `--json` | Expected json |
| `--commit COMMIT` | Expected commit |
| `--run-id RUN_ID` | Expected run_id |
| `--attempt ATTEMPT` | Expected attempt |

### `devtool artifacts`

Read-only artifact integrity (does not update registry)

**Subcommands:** `verify`

### `devtool artifacts verify`

Rehash an existing registry record and inspect producer identity

| Argument / option | Meaning |
| --- | --- |
| `[SELECTOR]` | Accepted by this command. |
| `--repository REPOSITORY` | Expected remote repository |
| `--commit COMMIT` | Expected remote commit |
| `--run-id RUN_ID` | Expected remote run |
| `--attempt ATTEMPT` | Expected remote run attempt |
| `--json` | Emit JSON |
| `--receipt RECEIPT` | ArtifactReceiptV2 file (instead of registry selector) |

### `devtool adopt-batch`

Source-locked multi-repository adoption, staged commit publication and guarded recovery

| Argument / option | Meaning |
| --- | --- |
| `BATCH_OPERATION` | Operation (legacy plan/apply/resume/status or staged review/prepare/publish/resume-staged/recover/status-staged) Choices: plan, apply, resume, status, review-staged, prepare, publish, resume-staged, recover, status-staged, upgrade-review, recovery-plan |
| `BATCH_MANIFEST` | Absolute path to source-lock JSON manifest |
| `--state-dir STATE_DIR` | External durable batch-journal directory |
| `--yes` | Explicitly permit per-repo adoption commits |
| `--json` | Emit a stable JSON receipt |

### `devtool migration`

Review or execute a source-locked two-repository extraction

| Argument / option | Meaning |
| --- | --- |
| `MIGRATION_OPERATION` | Extraction operation Choices: plan, prepare, execute, resume, status |
| `MIGRATION_MANIFEST` | Absolute reviewed extraction manifest |
| `--state-dir STATE_DIR` | External durable backup and journal directory |
| `--yes` | Explicitly allow preparation or per-repository commits |
| `--json` | Emit JSON report |

### `devtool workflow`

Native-node recovery (source-locked and explicit)

**Subcommands:** `retry`

### `devtool workflow retry`

Native-node retry planning and guarded execution

| Argument / option | Meaning |
| --- | --- |
| `--run-id RUN_ID` | Original EXO workflow run ID Required. |
| `--failed` | Retry failed/blocked native workflow nodes |
| `--plan` | Read-only retry plan (the default) |
| `--yes` | Explicitly authorize safe local node replay |
| `--json` | Structured plan/result output |
| `--resume` | Explicitly resume interrupted safe local-node attempt |

### `devtool capabilities`

Read-only repo target/workflow/wrapper/Actions capability inventory

| Argument / option | Meaning |
| --- | --- |
| `--json` | Machine-readable capability report |

### `devtool onboard`

RM20 review-first whole-repository in-place onboarding and ownership inventory

| Argument / option | Meaning |
| --- | --- |
| `ONBOARD_OPERATION` | Read-only inventory, reviewed proposal, or guarded in-place application Choices: inventory, plan, apply |
| `--review-spec REVIEW_SPEC` | Reviewed JSON job/DAG/CI/ownership specification |
| `--reviewed-sha256 REVIEWED_SHA256` | Source-and-ownership-bound review SHA-256 from plan |
| `--yes` | Explicitly authorize single-file onboarding commit |
| `--json` | Emit structured JSON |

