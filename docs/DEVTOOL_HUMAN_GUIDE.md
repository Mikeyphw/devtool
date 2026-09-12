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

Use `--copy` when the input artifact should first be copied to Devtool-controlled storage. Apply transactions validate paths and source-state expectations before mutation, journal changes, and can roll back on validation failure according to policy. Embedded preflight/validation/finalization hooks have explicit capability boundaries; they are not arbitrary implicit shell execution.

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

## 19. Machine output, automation, and IPC

Use JSON/machine output whenever another program consumes Devtool. Durable validation control uses a versioned response envelope and revision guards. IPC/HUD consumers should poll/subscribe to canonical state rather than scrape terminal rendering. The Android and desktop frontends use the same principle.

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
| `--proot-distro DISTRO` | Run command phases inside a PRoot-Distro container; guest does not need devtool |
| `--execution-environment EXECUTION_ENVIRONMENT` | Execution backend: auto, native-termux, chroot, proot-distro, desktop-linux, ssh, android-device, or external-device Choices: auto, native-termux, chroot, desktop-linux, android-device, external-device, ssh, proot-distro |
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
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
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
| `--caches` | Also prune repository-scoped devtool-managed caches and transient state |
| `--space` | Prune safe global Android/toolchain caches managed by Devtool |
| `--gradle-user-cache` | With --space, also prune old version-specific ~/.gradle caches and build-cache-1; modules/wrapper caches are preserved |
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
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
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
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
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
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
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
| `--no-rollback-on-failure` | Disable rollback when an apply or its validation fails |
| `--no-apply-notification` | Do not show the final apply notification/toast |
| `--commit COMMIT` | Commit with this message after apply and optional validation |
| `--no-commit` | Disable a commit requested by the artifact manifest |
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
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
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
| `--no-rollback-on-failure` | Disable rollback when an apply or its validation fails |
| `--no-apply-notification` | Do not show the final apply notification/toast |
| `--commit COMMIT` | Commit with this message after apply and optional validation |
| `--no-commit` | Disable a commit requested by the artifact manifest |
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
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
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
| `--no-rollback-on-failure` | Disable rollback when an apply or its validation fails |
| `--no-apply-notification` | Do not show the final apply notification/toast |
| `--commit COMMIT` | Commit with this message after apply and optional validation |
| `--no-commit` | Disable a commit requested by the artifact manifest |
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
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
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
| `--no-rollback-on-failure` | Disable rollback when an apply or its validation fails |
| `--no-apply-notification` | Do not show the final apply notification/toast |
| `--commit COMMIT` | Commit with this message after apply and optional validation |
| `--no-commit` | Disable a commit requested by the artifact manifest |
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
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
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
| `--no-rollback-on-failure` | Disable rollback when an apply or its validation fails |
| `--no-apply-notification` | Do not show the final apply notification/toast |
| `--commit COMMIT` | Commit with this message after apply and optional validation |
| `--no-commit` | Disable a commit requested by the artifact manifest |
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
| `--resource-profile VALIDATION_RESOURCE_PROFILE` | Validation resource profile Choices: automatic, android-low-power, standard |
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
| `--no-rollback-on-failure` | Disable rollback when an apply or its validation fails |
| `--no-apply-notification` | Do not show the final apply notification/toast |
| `--commit COMMIT` | Commit with this message after apply and optional validation |
| `--no-commit` | Disable a commit requested by the artifact manifest |
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

**Subcommands:** `check`, `rebase`

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

### `devtool overlay rebase`

Regenerate overlay inventory source hashes against the current repository

| Argument / option | Meaning |
| --- | --- |
| `ARTIFACT` | Accepted by this command. |
| `-o, --output OUTPUT` | Output archive; defaults to <name>.rebased.<ext> |
| `--json` | Emit structured rebase JSON |

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

### `devtool tui`

Open the responsive terminal dashboard

| Argument / option | Meaning |
| --- | --- |
| `--page PAGE` | Initial terminal dashboard page Choices: dashboard, build, overlays |
| `--artifact ARTIFACT` | Overlay artifact to preflight on the overlays page |
| `--once` | Render one frame and exit |
| `--width WIDTH` | Override terminal width for rendering/debugging |

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
| `SHELL` | Choices: zsh |

### `devtool completion-state`

Emit config-aware values for shell/editor completion

| Argument / option | Meaning |
| --- | --- |
| `--kind KIND` | Choices: targets, groups, ui-profiles, test-profiles, variants, jobs, workflows, pytest-tests, all |
| `--format FORMAT` | Choices: lines, json |

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

### `devtool pytest`

Inspect and profile Devtool's pytest suite

**Subcommands:** `profile`

### `devtool pytest profile`

Run pytest with Devtool slow-test reporting enabled

| Argument / option | Meaning |
| --- | --- |
| `PYTEST_ARGS...` | Optional pytest files, node IDs, or arguments; defaults to tests |
| `--threshold THRESHOLD` | Slow-test threshold in seconds |
| `--top TOP` | Maximum number of slow entries to show |
| `--strict` | Fail when unmarked tests exceed the slow threshold |
| `--json` | Emit a structured pytest-profile report |

### `devtool config`

**Subcommands:** `path`, `show`, `defaults`, `init`, `edit`, `resolve`, `explain`, `migrate`, `doctor`

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

**Subcommands:** `capabilities`, `devices`, `doctor`, `signing`, `install`, `uninstall`, `launch`, `stop`, `restart`, `clear-data`, `shell`, `push`, `pull`, `logcat`, `screenshot`, `permission`, `release`, `instrumentation`, `smoke`

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
| `--class INSTRUMENTATION_CLASSES` | Accepted by this command. |
| `--method INSTRUMENTATION_METHODS` | Accepted by this command. |
| `--annotation INSTRUMENTATION_ANNOTATIONS` | Accepted by this command. |
| `--not-annotation INSTRUMENTATION_NOT_ANNOTATIONS` | Accepted by this command. |
| `--size INSTRUMENTATION_SIZE` | Choices: small, medium, large |
| `--runner-arg INSTRUMENTATION_RUNNER_ARGS` | Accepted by this command. |
| `--skip-build` | Accepted by this command. |
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

List reusable polyglot jobs declared under [jobs.*]

**Aliases:** `devtool jb`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit structured reusable-job metadata |

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
| `[ACTION]` | Workflow/action to plan (default: validate) Choices: restore, build, test, lint, package, run, validate, wine-validate |
| `--json` | Emit the complete graph and scheduling plan as JSON |
| `--dot` | Emit Graphviz DOT instead of the human plan |
| `--explain` | Show read/write scope and serialization reasons for every node |
| `--show-commands` | Include resolved command argv/cwd in the human plan |

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

