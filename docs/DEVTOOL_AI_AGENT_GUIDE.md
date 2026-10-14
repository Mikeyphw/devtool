# Devtool complete guide for AI agents

This is the comprehensive operating contract for AI agents and automation that inspect, modify, build, test, validate, or release repositories through Devtool. It covers the same public feature surface as the human guide, but emphasizes determinism, safety, machine-readable state, artifact integrity, and recovery. The final appendix is generated directly from the live CLI parser so every current public command/subcommand and option is represented.

> Human-facing explanations live in [`DEVTOOL_HUMAN_GUIDE.md`](DEVTOOL_HUMAN_GUIDE.md). Agents should treat this file plus repository `.devtool.toml` as operating context, not as permission to skip inspection.

## 1. Agent invariants

1. **Resolve the repository and target explicitly.** In multi-target repositories, do not guess the default target when a named target/group is available.
2. **Inspect before mutation.** Use `status`, `inspect`, `plan`, `detect`, artifact inspection/preflight, and validation status as applicable.
3. **Prefer Devtool transactions over ad-hoc mutation orchestration.** Overlay apply, history, rollback, commit, artifact registry, and durable validation encode repository policy that raw shell steps do not.
4. **Use machine output.** Prefer `--json`, `--machine`, `--machine-file`, and stable control APIs over parsing human terminal output.
5. **Preserve secrets.** Never serialize signing passwords/tokens into overlays, logs, resumable state, prompts, or command text when Devtool has a secret-aware boundary.
6. **Do not classify host/resource warnings as target regressions.** Use structured diagnostic code/scope. Target warnings still matter.
7. **Honor source hashes and inventory.** Never silently force an overlay across a source-state conflict unless the operator explicitly chose a supported rebase/conflict workflow.
8. **Do not invent device availability.** Connected tests/signing/install/release gates must follow declared Android policy and actual device/provider state.
9. **Keep validation evidence attached to the operation.** Run IDs, log paths, phase/report state, artifact hashes, and commit IDs are part of a complete result.
10. **Recover through the session/transaction model.** On interruption, inspect/recover/resume; do not delete state files as a first response.

## 2. Discovery sequence before work

A robust agent starts with a small, non-mutating discovery set appropriate to the task:

```bash
devtool -r REPO status --json
devtool -r REPO inspect --json
devtool -r REPO targets
devtool -r REPO doctor
devtool -r REPO plan validate --target TARGET
```

For Android work, add `android capabilities` and device inventory/doctor only when the requested operation can require Android tools or a device. For overlay work, inspect/preflight the artifact before applying. For a pre-existing durable validation session, inspect `validate status` and `validate plan` before starting another execution path.

Do not run heavyweight probes solely to collect trivia. Devtool task/device planners intentionally avoid ADB checks when no requested task needs a device.

## 3. Selection contract

Selection controls the mutation/validation scope. Prefer, in order:

- an explicitly requested `--target`;
- an explicitly configured group/workflow/job named by the task;
- affected-target resolution when the task is explicitly change-scoped;
- a repository default only when it is unambiguous and configured.

Never broaden a one-target request into `--all-targets` merely because it is available. Conversely, do not validate only one leaf target when the repository workflow/preflight declares required dependents or release gates.

Use `targets`, `groups`, `jobs`, `workflows`, `affected`, `preflight`, and `plan` to expose the resolved graph.

## 4. Runner and lifecycle contract

Generic lifecycle operations (`restore`, `build`, `test`, `lint`, `validate`, `run`, `package`, `release`, `clean`) are routed through the resolved runner. Do not replace them with guessed Gradle/pytest/Cargo/npm commands unless the task explicitly requires bypassing Devtool or a Devtool feature is absent.

Explicit native task selectors are appropriate when the operator requests a narrow gate. Preserve repeated task selectors exactly rather than collapsing semantically different tasks. Respect runner task deduplication/dependency planning instead of manually starting multiple overlapping Gradle invocations.

## 5. Validation strategy

### One-shot

Use ordinary `validate` for a bounded validation gate. Preserve repository-configured lint/test/build policy unless the user explicitly narrows it.

### Durable/resumable

Use durable validation for long or interruption-prone plans. Treat the session as a revisioned state machine. Read status/plan, then mutate with the current revision guard where the API exposes one. A stale-revision result means refresh state; it does not mean retry the old command indefinitely.

States such as blocked, recovery-required, paused, stopped, and plan-drift require the matching control operation (`recover`, `reset`, `accept-plan-change`, `finalize`, `abort`, etc.) rather than filesystem edits.

A failed target test is not equivalent to an infrastructure interruption. Retry policy depends on structured classification.

## 6. Warning accounting: target vs infrastructure

Artifact contracts may declare `expected = pass` and `maximum_warnings = 0`. Devtool evaluates that budget against **target warnings**, not every warning-shaped line in the operation log.

Infrastructure categories include current structured codes for low-storage notices, ADB-probe warnings, Gradle host/native-platform environment notices, Gradle infrastructure notices, and metaspace-pressure notices. These warnings remain in diagnostics/logs and may drive resource/retry recommendations, but they do not consume the target warning budget.

Compiler/linter/test/project warnings are target warnings and still count. Errors always fail. Agents must report both classes when relevant; do not hide infrastructure warnings merely because they are excluded from the artifact warning limit.

Machine metadata exposes target and ignored-infrastructure warning counts separately for validation expectation results.

## 7. Overlay construction and integrity

For schema-v2 overlays:

- paths are repository-relative and traversal-safe;
- every payload path is inventoried;
- modified paths include `source_sha256` so stale source state fails closed;
- final file `sha256` and size identify payload content;
- added/modified/deleted actions are explicit;
- apply policy controls atomicity, conflict behavior, cleanliness, commit policy, and validation;
- embedded hooks use declared capabilities and phases;
- artifacts should contain only intended payload plus manifest/hook content.

Before apply:

```bash
devtool overlay inspect ARTIFACT
devtool overlay preflight ARTIFACT
devtool validate-overlay ARTIFACT
```

For multiple overlays, use bundle/multi-apply features when ordering/dependencies matter. Do not concatenate tarballs or hand-copy payload paths to emulate Devtool.

On source-state mismatch, stop and inspect/rebase. Do not silently remove `source_sha256` as a workaround.

## 8. Overlay application, commits, rollback, and history

Use `apply-overlay`, `apply-overlays`, `apply-snapshot`, or the unified `apply` surface. `--copy` copies the selected run summary/prompt output to the clipboard; it does not move the source artifact. Apply transactions journal mutations. Validation can be deferred with explicit policy when the workflow calls for it; do not describe a deferred apply as validated.

Use Devtool `commit`/artifact commit policy when the repository workflow expects Devtool-generated commit evidence. `history` and `rollback` are the canonical recovery path for prior Devtool operations. `cleanup` manages backup retention; do not manually delete transaction data while an operation may need recovery.

## 8.1 Terminal and clipboard output verification

Global `--copy` means copy the selected Devtool run summary or prompt output to the clipboard; it is not an artifact-staging flag. When diagnosing output correctness, use `--output-debug` or `DEVTOOL_OUTPUT_DEBUG=1`. The same switch captures the final terminal summary and any clipboard payload produced by `--copy`. Devtool persists/hash-correlates both with `DEVTOOL_RUN_ID`, stores an `output-debug/timeline.jsonl`, and records `matches_terminal_summary` for clipboard captures when a terminal summary exists. `--no-output-debug` overrides the environment variable. Compare the recorded hashes/content instead of guessing from terminal interleaving or clipboard behavior. Captured payload bodies stay in evidence files and are not echoed to the terminal.

## 9. Artifact and build-output evidence

Use the produced-artifact registry rather than grepping logs for `*.apk`, `*.zip`, etc. `artifact` manages/inspects built outputs. `inspect-artifact` reads overlay/bundle metadata. For completion reports, include the canonical produced paths/hashes and validation state returned by Devtool.

If artifact discovery is incomplete, inspect the target's configured artifact paths/patterns/directories before inventing a filename.

## 10. Execution environments and path handling

A repository may run natively, under Termux/chroot/PRoot policy, or through a remote/SSH environment. Respect the configured backend. Do not assume the host repository path is valid inside the executor. Let Devtool perform path/environment translation.

If environment/toolchain resolution fails, use `doctor`, `termux`, `bootstrap`, `detect`, and capabilities commands. Treat network/bootstrap policy as explicit; do not silently download large toolchains when policy forbids it.

## 11. Android toolchain contract

Use `android capabilities` to resolve Java/Gradle/SDK/platform/build-tools/AAPT2/apksigner/zipalign/NDK/CMake/ADB provenance. Termux builds may need platform-specific providers or host fallbacks; desktop assumptions are unsafe.

Resource controls are part of execution policy. Preserve explicitly requested worker/CPU/heap/metaspace/memory-guard/parallel/cache/daemon values. Avoid starting parallel Gradle invocations that defeat Devtool's planner or exceed the declared resource budget.

The Gradle task planner recognizes ordinary checks, connected/instrumented checks, release gates, and artifact-producing tasks. Only device-requiring task classes should trigger device probes.

Do not infer duplicate Gradle execution from repeated plain-console `> Task :path` headers. Parallel Gradle output can re-emit a bare task header when that task resumes producing output after another task. Prefer Devtool's Gradle execution telemetry: `task_execution_count`, `task_completed_count`, `task_resume_count`, `task_resume_counts`, `requested_duplicate_count`, `requested_duplicate_tasks`, `task_outcomes`, and `duplicate_execution_start_count`. A nonzero `task_resume_count` is presentation/interleaving telemetry; a nonzero `duplicate_execution_start_count` is the execution anomaly signal. Default human output collapses resumed bare headers, while raw evidence and `--verbose` retain the complete stream.

## 12. Android ADB/device contract

Before a device-required operation, resolve the provider and device policy. Distinguish:

- no device present;
- unauthorized device;
- offline device;
- provider executable missing/broken;
- Termux USB/provider integration failure;
- a valid selected device.

Use the canonical device manager for serial/preferred/first-physical selection. `termux-adb` is a distinct provider path and Devtool does not imply that a third-party package/repository may be installed without policy/consent.

For optional instrumentation (`when-device-present`), absence may be a classified skip. For `required`, absence is a failure. Do not reinterpret one policy as the other.

## 13. Android signing and secret handling

Use `android signing create|list|inspect|verify|compare|import|export-certificate` for identity management. Do not persist passwords in `.devtool.toml`, overlays, test fixtures with real values, or resumable validation state. Signing secrets are intended to reach the native signing/build process at the last safe boundary.

Debug/build/lint/unit-test tasks should not be made signing-dependent. Release-producing tasks and release gates may require signing policy. Verify keystore/certificate identity before an expensive release.

## 14. Android instrumentation and lifecycle operations

Use Devtool's instrumentation selectors and lifecycle operations for install/replace/uninstall, launch/stop/restart/clear-data, permissions, shell/push/pull/logcat/screenshot, and smoke scenarios. When a request asks for install/upgrade/process-death/restart evidence, use the specific lifecycle/smoke operations instead of claiming equivalence from a successful `assemble`.

## 15. Workflows, scheduler, jobs, and affected graphs

Workflows are DAGs, not shell aliases. Inspect/preflight the graph. Respect prerequisites and resource-aware scheduling. Do not schedule the same mutation target concurrently unless the workflow declares it safe. Affected-target expansion should use ownership/dependency data, not filename intuition.

## 16. Frontend/UI control surfaces

TUI, validation menu, Desktop HUD/Control Center, Android Live HUD/Validation Workbench, and IPC clients consume canonical state. An agent integrating a frontend must not implement a competing validation state machine.

Use stable validation API envelopes/revisions/events and HUD bridge/control-center IPC commands. Do not scrape ANSI terminal rendering when structured state exists.

## 17. Configuration editing

Repository `.devtool.toml` is versioned project policy. User global config is machine/operator policy. Keep machine-specific paths/secrets out of repo config unless the schema explicitly defines portable behavior.

After modifying configuration:

1. inspect/parse it through Devtool;
2. run focused contract tests if this is Devtool itself;
3. run `plan`/`preflight` for changed workflow/target logic;
4. update docs/examples when the public contract changed.

## 18. Logs and diagnostic triage

Always retain/report the run ID and log path for a failed Devtool operation. Prefer structured issues with code/severity/path/line when available. Avoid presenting the last console line as the root cause when Devtool already parsed a more specific error.

The `logs` surface provides retention/discovery operations. Diagnostic classification should be extended at the parser/normalization layer, not by filtering arbitrary substrings only in one frontend.

## 19. Recovery decision tree

When an operation fails:

1. Read structured failure reason and diagnostics.
2. If it is a durable validation session, read `validate status/plan/events`.
3. If resource/environment classified and retryable, correct resources/environment and use safe retry/recover/continue.
4. If target code/test failed, fix the target and rerun the affected gate; do not loop an unchanged failing command indefinitely.
5. If overlay source hash conflicts, inspect/rebase; do not force-copy.
6. If apply transaction is partially interrupted, use transaction recovery/rollback rather than manual deletion.
7. If plan drift is intentional, explicitly accept it with reason where required.
8. Finalize only when the session/report state says finalization is valid.

## 20. Agent completion criteria

A task is not complete merely because a patch was written. Report, as applicable:

- artifact/overlay path and hash;
- exact target(s) changed;
- source-state/manifest validation result;
- tests/validation tasks run and counts;
- warning/error counts with target vs infrastructure distinction;
- commit ID if a commit was actually created;
- produced APK/package/artifact identity for build/release work;
- known unvalidated portions and why they were not run.

Do not claim a commit, device test, signing verification, or release artifact that the tool did not produce.

## 21. Preferred machine-oriented feature map

Use these command families rather than reinventing behavior:

- **Discovery/config:** `status`, `inspect`, `doctor`, `detect`, `config`, `completion-state`, `targets`, `groups`, `jobs`, `workflows`.
- **Planning:** `affected`, `preflight`, `plan`, workflow scheduling commands.
- **Lifecycle:** `restore`, `build`, `test`, `lint`, `validate`, `validate-all`, `run`, `package`, `release`, `clean`, `wine-validate`, `pytest`.
- **Durable validation:** `start-validation`, `resume-validation`, `validate ...`, `validation-menu` plus status/plan/events/report/control subcommands.
- **Artifacts/mutation:** `apply-overlay`, `apply-overlays`, `apply-snapshot`, `apply`, `validate-overlay`, `validate-overlays`, `overlay`, `bundle`, `inspect-artifact`, `artifact`, `commit`, `history`, `rollback`, `cleanup`.
- **Android:** `android`, `instrumentation`, signing/permission/device/toolchain/release operations, `android-hud-bridge`.
- **Execution/tooling:** `termux`, `bootstrap`, `compatibility`, `final-seal`, `handoff`, `ai-prompt`.
- **UI/integration:** `tui`, `menu`, `validation-menu`, HUD/control-center/IPC commands, UI contract/profile commands.
- **Release bookkeeping:** `tag`, `release-notes`.

The generated reference below is the canonical inventory of public parser commands/options in this revision.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool gradle takeover doctor`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit structured JSON |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool gradle takeover explain`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit structured JSON |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool gradle takeover disable`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit structured JSON |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool gradle takeover enable`

| Argument / option | Meaning |
| --- | --- |
| `--mode MODE` | Choices: observe, manage, strict |
| `--scope SCOPE` | Choices: devtool, shell, both |
| `--json` | Emit structured JSON |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

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

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

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

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

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

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

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

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

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

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

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

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

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

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

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

**Subcommands:** `check`, `status`, `rebase`

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool overlay status`

Classify overlay patch equivalence against the current repository

| Argument / option | Meaning |
| --- | --- |
| `ARTIFACT` | Accepted by this command. |
| `--json` | Emit structured patch-equivalence JSON |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool overlay rebase`

Three-way rebase an overlay against the current repository

| Argument / option | Meaning |
| --- | --- |
| `ARTIFACT` | Accepted by this command. |
| `-o, --output OUTPUT` | Output archive; defaults to <name>.rebased.<ext> |
| `--json` | Emit structured rebase JSON |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool plugin doctor`

Run plugin-specific health checks

| Argument / option | Meaning |
| --- | --- |
| `NAME` | Accepted by this command. |
| `--json` | Emit plugin doctor JSON |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool artifact inspect`

Inspect a registered build artifact

**Aliases:** `devtool artifact show`

| Argument / option | Meaning |
| --- | --- |
| `SELECTOR` | Artifact id, path, or filename |
| `--json` | Emit artifact JSON |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool artifact verify`

Verify existence and SHA-256 of a registered artifact

| Argument / option | Meaning |
| --- | --- |
| `SELECTOR` | Artifact id, path, or filename |
| `--json` | Emit verification JSON |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool artifact prune`

Prune old registry entries

| Argument / option | Meaning |
| --- | --- |
| `--older-than OLDER_THAN` | Age threshold such as 30d, 12h, or an integer day count |
| `--delete-files` | Also delete matching files inside .devtool/build-artifacts only |
| `--json` | Emit prune result JSON |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool repo-pack list`

List durable repository-pack lifecycle records

**Aliases:** `devtool repo-pack ls`

| Argument / option | Meaning |
| --- | --- |
| `--all` | include tombstoned packs |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool repo-pack status`

Verify repository-pack bytes and retention state

**Aliases:** `devtool repo-pack show`

| Argument / option | Meaning |
| --- | --- |
| `SELECTOR` | pack id, receipt id, or output path |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool repo-pack pin`

Pin a repository pack against lifecycle GC

| Argument / option | Meaning |
| --- | --- |
| `SELECTOR` | Accepted by this command. |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool repo-pack unpin`

Release an explicit repository-pack pin

| Argument / option | Meaning |
| --- | --- |
| `SELECTOR` | Accepted by this command. |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool repo-pack prune`

Garbage-collect unpinned policy packs through LIFE cleanup

| Argument / option | Meaning |
| --- | --- |
| `--older-than-days OLDER_THAN_DAYS` | minimum pack age in days |
| `--dry-run` | Accepted by this command. |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--dry-run`, `--json`.

### `devtool signing-identity`

Manage private non-secret signing identity references

**Subcommands:** `list`, `register`

### `devtool signing-identity list`

**Aliases:** `devtool signing-identity ls`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--dry-run`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool wrapper help`

Show repository wrapper help

| Argument / option | Meaning |
| --- | --- |
| `[COMMAND_NAME]` | Optional wrapper command to describe |
| `--json` | Emit wrapper discovery metadata as JSON |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool wrapper discover`

Discover wrapper commands, launchers, and docs

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit wrapper discovery metadata as JSON |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool wrapper resolve`

Resolve wrapper argv without executing it

**Aliases:** `devtool wrapper plan`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit the resolved command as JSON |
| `WRAPPER_ARGS` | Repository wrapper argv to resolve |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool wrapper update`

Refresh repository devtoolw launchers

| Argument / option | Meaning |
| --- | --- |
| `--force` | Explicitly replace unmanaged launcher files |
| `--json` | Emit launcher status as JSON |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool wrapper verify`

Verify repository devtoolw launchers are current

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit launcher status as JSON |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool wrapper status`

Show repository devtoolw launcher status

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit launcher status as JSON |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool wrapper migrate`

Migrate a schema-1 repository wrapper contract to canonical schema 2

| Argument / option | Meaning |
| --- | --- |
| `--check` | Report whether wrapper schema migration is needed without changing files |
| `--write` | Atomically update only the [wrapper] schema declaration |
| `--json` | Emit secret-safe migration metadata as JSON |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool wrapper seal`

Run wrapper cross-surface final seal checks

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit wrapper final seal as JSON |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool final-seal`

Run the FS03 repository-wide final release seal audit

**Aliases:** `devtool seal`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit the versioned FS03 seal report as JSON |
| `--strict` | Exit non-zero when any final-seal check fails |
| `--target TARGET` | Emit the final seal for one configured target instead of the whole repository |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool pytest inventory`

Inspect stable Devtool test/check identity and lane/resource policy

| Argument / option | Meaning |
| --- | --- |
| `--lane LANE` | Filter inventory by semantic lane Choices: fast, integration, external, system, full |
| `--group GROUP` | Filter by stable check group or alias |
| `--explain CHECK` | Explain one durable check ID or historical alias |
| `--json` | Emit canonical inventory JSON |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool audit delta`

Compare two persisted audit passes

| Argument / option | Meaning |
| --- | --- |
| `OLD_PASS` | Accepted by this command. |
| `NEW_PASS` | Accepted by this command. |
| `--campaign CAMPAIGN` | Disambiguate pass identifiers within a campaign |
| `--json` | Emit machine-readable audit delta |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool audit status`

Show audit freshness for a campaign

| Argument / option | Meaning |
| --- | --- |
| `--campaign CAMPAIGN` | Campaign identifier Required. |
| `--json` | Emit machine-readable audit status |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool git`

Inspect Devtool's authoritative Git repository state

**Subcommands:** `state`, `doctor`, `rerere`, `bisect`, `notes`, `campaign`

### `devtool git state`

Show the authoritative repository Git-state snapshot

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable Git state |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool git doctor`

Diagnose Git/Devtool repository state

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable Git doctor evidence |
| `--repair-safe` | Apply only conservative, non-source-mutating repairs |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool git rerere`

Inspect Devtool-owned scoped conflict memory

**Subcommands:** `suggest`, `capture`, `record`, `apply`

### `devtool git rerere suggest`

Suggest an exact repository/campaign conflict resolution

| Argument / option | Meaning |
| --- | --- |
| `--campaign CAMPAIGN` | Campaign context for conflict memory |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool git rerere capture`

Capture the exact unresolved conflict preimage before manual resolution

| Argument / option | Meaning |
| --- | --- |
| `--campaign CAMPAIGN` | Campaign context for conflict memory |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool git rerere record`

Record a validated manual resolution for the captured exact conflict

| Argument / option | Meaning |
| --- | --- |
| `--campaign CAMPAIGN` | Campaign context for conflict memory |
| `--path RERERE_PATHS` | Resolved conflict path; repeatable Required. |
| `--test RERERE_TESTS` | Repository-declared Devtool test ID used to validate the resolution; repeatable Required. |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool git rerere apply`

Apply an exact stored resolution according to the configured rerere policy and rerun validation

| Argument / option | Meaning |
| --- | --- |
| `--campaign CAMPAIGN` | Campaign context for conflict memory |
| `--mode MODE` | Override [git].rerere for this invocation Choices: off, suggest, validated-auto |
| `--test RERERE_TESTS` | Repository-declared Devtool test ID rerun after reuse; required for validated-auto |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool git bisect`

Find the first bad revision using Devtool-managed detached worktrees

| Argument / option | Meaning |
| --- | --- |
| `--good GOOD` | Known-good Git revision Required. |
| `--bad BAD` | Known-bad Git revision Required. |
| `--task TASK` | Devtool workflow/test/action to evaluate for each candidate Required. |
| `--json` | Emit machine-readable managed-bisect evidence |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool git notes`

Inspect Devtool execution evidence stored in Git notes

**Subcommands:** `show`

### `devtool git notes show`

Show refs/notes/devtool execution evidence for a commit

| Argument / option | Meaning |
| --- | --- |
| `[REVISION]` | Commit/revision to inspect (default: HEAD) |
| `--json` | Emit machine-readable execution-note evidence |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool git campaign checkpoint`

Record the next campaign generation

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `[STEP]` | Roadmap/campaign step name recorded for this generation |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool git campaign rollback`

Move only Devtool private campaign tip refs back to a prior generation/step

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `SELECTOR` | Generation number or checkpoint step name |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool git campaign supersede`

Mark one campaign generation superseded by another and move private campaign tips

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `OLD_GENERATION` | Accepted by this command. |
| `NEW_GENERATION` | Accepted by this command. |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool git campaign range-diff`

Compare two campaign generations with git range-diff

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `--old OLD_GENERATION` | Required. |
| `--new NEW_GENERATION` | Required. |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool git campaign status`

Show persisted campaign generations and identities

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool git campaign restore-dirty`

Restore GIT-FRX-13 dirty-tree state captured at campaign start

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `--repository REPOSITORY` | Restore only one enrolled repository |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool git campaign seal`

Create and verify the GIT-FRX-18 federated provenance seal

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `--signing SIGNING` | Override [git].signing for the final campaign tag Choices: off, if-configured, required |
| `--tag` | Create/update the optional devtool/<campaign>/<generation> annotated tag in the control repository |
| `--verify` | Verify an existing final seal without creating a new one |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool campaign`

Manage multi-repository Git campaign generations (compatibility surface for git campaign)

**Subcommands:** `status`, `checkpoint`, `rollback`, `range-diff`, `supersede`

### `devtool campaign status`

Show persisted campaign generations and identities

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool campaign checkpoint`

Record a named campaign checkpoint generation

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `STEP` | Accepted by this command. |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool campaign rollback`

Roll Devtool private campaign tips back to a generation/step without mutating user branches

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `SELECTOR` | Accepted by this command. |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool campaign range-diff`

Compare two campaign generations

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `OLD_GENERATION` | Accepted by this command. |
| `NEW_GENERATION` | Accepted by this command. |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool campaign supersede`

Mark one generation superseded by another

| Argument / option | Meaning |
| --- | --- |
| `CAMPAIGN_ID` | Accepted by this command. |
| `OLD_GENERATION` | Accepted by this command. |
| `NEW_GENERATION` | Accepted by this command. |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation`

Inspect explicitly enrolled federated repositories

**Aliases:** `devtool fed`

**Subcommands:** `list`, `inspect`, `preflight`, `gate2`, `final-seal`, `doctor`, `status`, `capabilities`, `diff`, `artifacts`, `logs`, `failures`, `interfaces`, `conformance`, `affected`, `why`, `plan`, `lock`, `bisect`, `run`, `resume`, `retry`, `recover`, `rollback`, `integrate`, `bootstrap`, `sync`, `exec`

### `devtool federation list`

List enrolled repositories and resolved ownership boundaries

**Aliases:** `devtool federation ls`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable federation discovery state |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation inspect`

Inspect one enrolled repository and its child-owned resolution

**Aliases:** `devtool federation show`

| Argument / option | Meaning |
| --- | --- |
| `REPOSITORY` | Federation repository name |
| `--json` | Emit machine-readable repository boundary state |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation preflight`

Run the FRX-04 repository identity and boundary validation gate

**Aliases:** `devtool federation gate1`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit the machine-readable FRX-04 gate report |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation gate2`

Run the FRX-08 cross-repository execution and contract validation gate

**Aliases:** `devtool federation execution-preflight`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit the machine-readable FRX-08 gate report |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation final-seal`

Run the FRX-12 federation final release qualification

**Aliases:** `devtool federation seal`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit the machine-readable FRX-12 final seal report |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation doctor`

Diagnose federation Git lineage, refs, and participating repositories

| Argument / option | Meaning |
| --- | --- |
| `[SUBJECT]` | Optional run or campaign identifier |
| `--json` | Emit machine-readable federation Git doctor evidence |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation status`

Show one coherent federation observability snapshot

**Aliases:** `devtool federation summary`

| Argument / option | Meaning |
| --- | --- |
| `--run RUN` | Limit run observations to one parent or child EXO run ID |
| `--json` | Emit the machine-readable federation observability snapshot |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation capabilities`

Show the declared repository capability/dependency graph

**Aliases:** `devtool federation graph`

| Argument / option | Meaning |
| --- | --- |
| `--group GROUP` | Limit graph projection to one federation group |
| `--json` | Emit the machine-readable capability graph |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation diff`

Compare two persisted federation parent-run checkpoints

| Argument / option | Meaning |
| --- | --- |
| `OLD_RUN` | Accepted by this command. |
| `NEW_RUN` | Accepted by this command. |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation artifacts`

Show producer/consumer artifact evidence for one federation run

| Argument / option | Meaning |
| --- | --- |
| `RUN_ID` | Accepted by this command. |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation logs`

Show canonical execution/evidence log paths for one federation run

| Argument / option | Meaning |
| --- | --- |
| `RUN_ID` | Accepted by this command. |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation failures`

Show failed/cancelled/blocked child evidence for one federation run

| Argument / option | Meaning |
| --- | --- |
| `RUN_ID` | Accepted by this command. |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation interfaces`

Show declared federation interface/conformance contracts

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit the machine-readable interface catalog |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation conformance`

Check child repositories against a federation interface contract

**Aliases:** `devtool federation interface-check`

| Argument / option | Meaning |
| --- | --- |
| `--interface INTERFACE` | Check all groups bound to this interface |
| `--group GROUP` | Check the interface bound to one federation group |
| `--json` | Emit machine-readable conformance evidence |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation affected`

Compute dependency-aware affected federation repositories/workflows/groups

| Argument / option | Meaning |
| --- | --- |
| `--since SINCE` | Detect direct repository changes since this Git revision |
| `--repository REPOSITORY` | Seed one directly changed federation repository; repeatable |
| `--json` | Emit machine-readable impact/reason evidence |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation why`

Explain why a federation repository/node/workflow/group is affected

| Argument / option | Meaning |
| --- | --- |
| `SUBJECT` | Repository, repository::node, workflow, or group to explain |
| `--since SINCE` | Detect direct repository changes since this Git revision |
| `--repository REPOSITORY` | Seed one directly changed federation repository; repeatable |
| `--json` | Emit machine-readable reason evidence |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation plan`

Build a deterministic planning-only cross-repository workflow/group DAG

| Argument / option | Meaning |
| --- | --- |
| `[WORKFLOW]` | Federation workflow name |
| `--group GROUP` | Executable federation group from [federation.groups] |
| `--freeze` | Persist the plan's frozen multi-repository source lock |
| `--drift-policy DRIFT_POLICY` | Drift policy recorded with --freeze (default: fail) Choices: fail, replan, allow-if-equivalent |
| `--json` | Emit the machine-readable federated plan graph |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation lock`

Inspect or explicitly update the persisted frozen federation source lock

| Argument / option | Meaning |
| --- | --- |
| `--update REPOSITORY` | Refresh exactly one participating repository in the persisted lock |
| `--json` | Emit the machine-readable lock and verification state |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation bisect`

Bisect one federation repository while every other participant remains pinned

| Argument / option | Meaning |
| --- | --- |
| `--repo NAME=GOOD..BAD` | Vary exactly one repository over GOOD..BAD Required. |
| `--pin NAME=SHA` | Override one non-varying repository pin; repeatable |
| `--task TASK` | Federated workflow name, workflow:NAME, or group:NAME Required. |
| `--json` | Emit machine-readable candidate combinations and results |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation resume`

Resume a prior federation parent run using dependency-aware checkpoint reuse

| Argument / option | Meaning |
| --- | --- |
| `RUN_ID` | Prior federation parent run ID |
| `--drift-policy DRIFT_POLICY` | Frozen source drift policy for re-executed children Choices: fail, replan, allow-if-equivalent |
| `--json` | Emit machine-readable reuse/invalidation decisions and run result |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation retry`

Retry a prior federation parent run while reusing still-valid successful children

| Argument / option | Meaning |
| --- | --- |
| `RUN_ID` | Prior federation parent run ID |
| `--failed` | Retry failed/cancelled/blocked children only; fail closed if a successful child is invalidated |
| `--drift-policy DRIFT_POLICY` | Frozen source drift policy for re-executed children Choices: fail, replan, allow-if-equivalent |
| `--json` | Emit machine-readable reuse/invalidation decisions and run result |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation recover`

Inspect persisted child/result/rollback recovery state for a federation run

| Argument / option | Meaning |
| --- | --- |
| `RUN_ID` | Accepted by this command. |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation rollback`

Explicitly roll integrated repository pointer(s) back to their validated start commit

| Argument / option | Meaning |
| --- | --- |
| `RUN_ID` | Accepted by this command. |
| `--repo ROLLBACK_REPOSITORY` | Accepted by this command. |
| `--integration-group ROLLBACK_INTEGRATION_GROUP` | Accepted by this command. |
| `--dry-run` | Preflight rollback without moving branch pointers |
| `--json` | Accepted by this command. |

Agent note: machine-friendly controls on this command include `--dry-run`, `--json`.

### `devtool federation integrate`

Integrate validated federation result commits into user branch pointers

| Argument / option | Meaning |
| --- | --- |
| `RUN_ID` | Completed federation parent run ID |
| `--policy POLICY` | Override the workflow integration policy Choices: per-repo, dependency-group, all-or-nothing-pointer-update |
| `--apply` | Apply branch pointer integration; without this flag the command is a dry-run |
| `--json` | Emit machine-readable integration plan/evidence |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation bootstrap`

Preview or materialize repositories from the frozen federation source lock

| Argument / option | Meaning |
| --- | --- |
| `--repo REPOSITORY` | Limit source management to one frozen repository; repeatable |
| `--apply` | Apply the lock-driven bootstrap; without this flag the command is a preview |
| `--json` | Emit machine-readable bootstrap intent/evidence |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation sync`

Preview or update existing repositories to the frozen federation source lock

| Argument / option | Meaning |
| --- | --- |
| `--repo REPOSITORY` | Limit source management to one frozen repository; repeatable |
| `--apply` | Apply the lock-driven sync; without this flag the command is a preview |
| `--json` | Emit machine-readable sync intent/evidence |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool federation exec`

Execute one enrolled child wrapper or target-local-job public contract

| Argument / option | Meaning |
| --- | --- |
| `REPOSITORY` | Federation repository name |
| `--plan` | Resolve the child public contract without executing it |
| `--json` | Emit machine-readable bridge resolution or execution state |
| `CONTRACT_ARGV` | Use -- wrapper <args...> or -- job <reference> |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool core runtime`

Resolve the CORE-X02 effective config and private run envelope

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable runtime envelope JSON |
| `--format FORMAT` | Requested frontend output format Choices: human, json, copy |
| `--stdout-payload STDOUT_PAYLOAD` | Requested stdout payload semantics Choices: auto, summary, raw |
| `--assume-yes` | Resolve a non-interactive interaction policy |
| `ARGV` | Command argv to lower into the runtime envelope; prefix with -- when needed |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool core run-store`

Inspect the CORE-X03 private run catalog/doctor state

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable run catalog state |
| `--root ROOT` | Override the run catalog root; defaults to <repo>/.devtool/private/run-v3 |
| `--doctor` | Emit storage doctor/GC state instead of the full catalog |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool core surfaces`

Generate or verify CORE-X04 machine help, schema, completion, and reference projections

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit the generated-surface manifest as JSON |
| `--write DIR` | Write generated projections atomically to DIR |
| `--check DIR` | Fail when generated projections in DIR drift from canonical sources |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool core help`

Search canonical machine help without scraping argparse text

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit canonical machine-help JSON |
| `--search SEARCH` | Filter by command ID, path, alias, domain, or summary |
| `--aliases` | Include compatibility aliases |
| `--advanced` | Include hidden/internal parser options |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool config migrate`

Check or rewrite legacy kind/type aliases to canonical runner/platform syntax

| Argument / option | Meaning |
| --- | --- |
| `--check` | Check and report migration opportunities without changing files |
| `--write` | Rewrite safe legacy aliases in place and create a .bak backup |
| `--json` | Emit machine-readable migration report |
| `--global` | Migrate legacy UI preferences in the user global config |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool config doctor`

| Argument / option | Meaning |
| --- | --- |
| `--global` | Diagnose the user global config |

### `devtool config schema`

Emit the generated CORE configuration schema/reference contract

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable schema JSON |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool config status`

Show canonical effective configuration, migration, and provenance status

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable status JSON |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool config diff`

Show effective fields whose provenance differs from built-in/default authority

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable provenance diff JSON |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--dry-run`.

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

Agent note: machine-friendly controls on this command include `--dry-run`.

### `devtool ssh bootstrap`

Install missing minimum worker tools (python, tmux, rsync, git)

| Argument / option | Meaning |
| --- | --- |
| `[PROFILE]` | Named [ssh.hosts.*] profile; defaults to [ssh].profile/default_profile |
| `--format FORMAT` | Choices: text, json |
| `--dry-run` | Show the remote package command without modifying the worker |
| `--force` | Override bootstrap.policy=never for this explicit request |

Agent note: machine-friendly controls on this command include `--dry-run`.

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

Agent note: machine-friendly controls on this command include `--dry-run`.

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

Agent note: machine-friendly controls on this command include `--dry-run`.

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

Agent note: machine-friendly controls on this command include `--dry-run`.

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

Agent note: machine-friendly controls on this command include `--dry-run`.

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

Agent note: machine-friendly controls on this command include `--dry-run`.

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

Agent note: machine-friendly controls on this command include `--dry-run`.

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

Agent note: machine-friendly controls on this command include `--dry-run`.

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

Agent note: machine-friendly controls on this command include `--dry-run`.

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

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

### `devtool validation-plan`

Show the durable validation phase plan

**Aliases:** `devtool vplan`

| Argument / option | Meaning |
| --- | --- |
| `--session SESSION` | Specific validation session ID |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

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

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

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

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

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

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

### `devtool validation-report`

Show the durable validation report

**Aliases:** `devtool vreport`

| Argument / option | Meaning |
| --- | --- |
| `--session SESSION` | Specific validation session ID |
| `--json` | Emit a stable validation-control JSON response |
| `--machine MACHINE` | Machine response formatting; implies --json Choices: pretty, compact |
| `--machine-file MACHINE_FILE` | Also write the validation-control JSON envelope to this file |

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

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

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

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

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

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

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

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

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

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

Agent note: machine-friendly controls on this command include `--json`, `--machine`, `--machine-file`.

### `devtool targets`

List configured targets with platform, runner, group, and workflow metadata

**Aliases:** `devtool tg`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit structured target metadata |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool groups`

List workspace target groups and their expanded targets

**Aliases:** `devtool gr`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit structured workspace-group metadata |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool jobs`

List reusable polyglot jobs declared under [jobs.*] and target-local jobs

**Aliases:** `devtool jb`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit structured reusable-job metadata |
| `--target TARGET` | Limit target-local jobs to one target while keeping repository jobs visible |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool target-local-jobs`

Report target-local job adoption, workflow wiring, and migration commands

**Aliases:** `devtool tlj`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit structured target-local adoption metadata |
| `--target TARGET` | Limit the adoption report to one target |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool workflows`

List configured target workflows without executing them

**Aliases:** `devtool wf`

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit structured workflow metadata |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

**Subcommands:** `baseline`, `plan`, `run`, `status`, `workflow`, `workflows`, `cancel`, `diagnose`, `jobs`, `logs`, `rerun`, `resume`, `runs`, `view`, `watch`, `artifacts`, `download`, `verify`, `android`, `pipeline`, `release`, `ci`, `extensions`, `qualification`

### `devtool actions baseline`

Print read-only ACTIONS-00/01 capability baseline

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions status`

Read repository and authenticated viewer metadata

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions workflow`

Inspect a workflow by numeric ID or YAML filename

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `WORKFLOW_ID` | Workflow ID or YAML filename |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions workflows`

List workflows without dispatching them

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions diagnose`

Diagnose GitHub repository identity, access and workflow discovery without mutation

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions jobs`

Inspect run and job/step states

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact remote GitHub workflow run ID |
| `--request-id REQUEST_ID` | Local ACTIONS-02 dispatch request ID (pinned to one repository and run) |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions logs`

Download remote workflow run logs as a bounded unextracted ZIP to private state

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact remote GitHub workflow run ID |
| `--request-id REQUEST_ID` | Local ACTIONS-02 dispatch request ID (pinned to one repository and run) |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions resume`

Resume a recorded dispatch by its exact request ID

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact remote GitHub workflow run ID |
| `--request-id REQUEST_ID` | Local ACTIONS-02 dispatch request ID (pinned to one repository and run) |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions runs`

List recent remote workflow runs from selected GitHub repository

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--limit LIMIT` | Maximum workflow runs (1..100) |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions view`

Inspect one remote workflow run

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact remote GitHub workflow run ID |
| `--request-id REQUEST_ID` | Local ACTIONS-02 dispatch request ID (pinned to one repository and run) |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions artifacts`

List artifacts associated with a specific GitHub workflow run

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--run-id RUN_ID` | Exact remote GitHub workflow run ID |
| `--request-id REQUEST_ID` | Local ACTIONS-02 dispatch request ID (pinned to one repository and run) |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions android`

Android builds on GitHub Actions and verified APK/AAB acquisition

**Subcommands:** `discover`, `plan`, `run`, `collect`, `verify`, `unsigned-plan`, `unsigned-run`, `unsigned-check`, `unsigned-import`, `unsigned-sign`, `aab-plan`, `aab-run`, `aab-check`, `aab-import`, `aab-sign`

### `devtool actions android discover`

Discover statically declared Android app modules

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--project-dir PROJECT_DIR` | Gradle project directory; auto-discovered if omitted |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions android plan`

Validate source parity and Android workflow inputs without dispatch

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--project-dir PROJECT_DIR` | Gradle project directory; auto-discovered if omitted |
| `--module MODULE` | Gradle application module, e.g. app |
| `--variant VARIANT` | Android build variant Choices: debug, release |
| `--format FORMAT` | Android output format Choices: apk, aab |
| `--ref REF` | Exact GitHub branch/tag to dispatch Required. |
| `--workflow WORKFLOW` | Selected Android workflow YAML filename |
| `--expected-package EXPECTED_PACKAGE` | Expected package/application ID, if dynamic Gradle sources |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions android run`

Dispatch an Android build only with explicit --yes

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--project-dir PROJECT_DIR` | Gradle project directory; auto-discovered if omitted |
| `--module MODULE` | Gradle application module, e.g. app |
| `--variant VARIANT` | Android build variant Choices: debug, release |
| `--format FORMAT` | Android output format Choices: apk, aab |
| `--ref REF` | Exact GitHub branch/tag to dispatch Required. |
| `--workflow WORKFLOW` | Selected Android workflow YAML filename |
| `--expected-package EXPECTED_PACKAGE` | Expected package/application ID, if dynamic Gradle sources |
| `--yes` | Explicitly authorize remote workflow dispatch |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions android unsigned-plan`

Preview source-bound, unsigned GitHub release candidate

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--ref REF` | Exact branch or tag to dispatch; source must match local HEAD Required. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions android unsigned-run`

Dispatch dedicated unsigned candidate workflow (requires --yes)

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--ref REF` | Exact branch or tag to dispatch; source must match local HEAD Required. |
| `--yes` | Explicitly authorize remote workflow dispatch |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions android aab-plan`

Preview unsigned Android AAB workflow with source pin

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--ref REF` | Exact branch or tag to dispatch; source must match local HEAD Required. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions android aab-run`

Dispatch unsigned AAB candidate (requires --yes)

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--ref REF` | Exact branch or tag to dispatch; source must match local HEAD Required. |
| `--yes` | Explicitly authorize remote workflow dispatch |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions pipeline plan`

Validate local Git and remote GitHub DAG without mutations

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--file FILE` | Checked-in repository-relative pipeline JSON Required. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions pipeline run`

Create durable pipeline journal and execute ready nodes

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--file FILE` | Checked-in repository-relative pipeline JSON Required. |
| `--yes` | Authorize sequential local and remote actions |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions pipeline status`

Read local durable pipeline states (no GitHub operations)

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--pipeline-id PIPELINE_ID` | Previously persisted pipeline ID Required. |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions pipeline resume`

Continue waiting pipeline nodes, never repeat uncertain dispatch

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--pipeline-id PIPELINE_ID` | Previously persisted pipeline ID Required. |
| `--yes` | Authorize pending pipeline work |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions release`

Independent-policy Android release attestation and qualification

**Subcommands:** `verify`, `setup`, `readiness`, `local-build`, `local-verify`, `local-evidence`

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions extensions runners`

G7 optional runners

| Argument / option | Meaning |
| --- | --- |
| `--github-repo GITHUB_REPO` | Explicit GitHub owner/repository slug |
| `--auth AUTH` | Credentials source: auto, env, gh, or none Choices: auto, env, gh, none |
| `--json` | Emit Actions response as JSON |
| `--trust-file TRUST_FILE` | Optional local JSON trusted runner profiles |

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

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

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions qualification`

G8 Actions qualification and historical evidence reconciliation

**Subcommands:** `report`, `check`

### `devtool actions qualification report`

G8 read-only qualification report

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit Actions response as JSON |

Agent note: machine-friendly controls on this command include `--json`.

### `devtool actions qualification check`

G8 read-only qualification check

| Argument / option | Meaning |
| --- | --- |
| `--json` | Emit Actions response as JSON |
| `--require-complete` | Fail if any required live/historical qualification is pending |

Agent note: machine-friendly controls on this command include `--json`.

