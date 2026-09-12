#!/usr/bin/env python3
"""Generate exhaustive Devtool feature guides from the live argparse surface.

The explanatory sections are curated. The command/option appendix is derived from
``devtool.cli.build_parser`` so every public CLI command, alias, positional, and
option is represented in both the human and agent guides.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from devtool.cli import build_parser  # noqa: E402

HUMAN_PATH = ROOT / "docs" / "DEVTOOL_HUMAN_GUIDE.md"
AI_PATH = ROOT / "docs" / "DEVTOOL_AI_AGENT_GUIDE.md"


@dataclass(frozen=True)
class CommandSpec:
    path: tuple[str, ...]
    aliases: tuple[str, ...]
    parser: argparse.ArgumentParser
    help_text: str


def _md(text: object) -> str:
    value = str(text or "").strip()
    value = value.replace("|", "\\|").replace("\n", " ")
    return re.sub(r"\s+", " ", value)


def _choice_help(action: argparse._SubParsersAction, name: str) -> str:
    for pseudo in action._choices_actions:
        if pseudo.dest == name:
            return str(pseudo.help or "")
    return ""


def _group_subparsers(action: argparse._SubParsersAction) -> list[tuple[str, tuple[str, ...], argparse.ArgumentParser, str]]:
    groups: dict[int, list[str]] = {}
    parsers: dict[int, argparse.ArgumentParser] = {}
    for name, parser in action.choices.items():
        groups.setdefault(id(parser), []).append(name)
        parsers[id(parser)] = parser
    ordered: list[tuple[str, tuple[str, ...], argparse.ArgumentParser, str]] = []
    seen: set[int] = set()
    for name, parser in action.choices.items():
        ident = id(parser)
        if ident in seen:
            continue
        seen.add(ident)
        names = groups[ident]
        canonical = names[0]
        ordered.append((canonical, tuple(names[1:]), parser, _choice_help(action, canonical)))
    return ordered


def command_specs(root: argparse.ArgumentParser) -> list[CommandSpec]:
    specs: list[CommandSpec] = []

    def walk(parser: argparse.ArgumentParser, path: tuple[str, ...]) -> None:
        for action in parser._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            for canonical, aliases, child, help_text in _group_subparsers(action):
                child_path = (*path, canonical)
                specs.append(CommandSpec(child_path, aliases, child, help_text))
                walk(child, child_path)

    walk(root, ())
    return specs


def _action_syntax(action: argparse.Action) -> str:
    if action.option_strings:
        left = ", ".join(action.option_strings)
        if action.nargs == 0:
            return left
        metavar = action.metavar
        if isinstance(metavar, tuple):
            value = " ".join(str(item) for item in metavar)
        elif metavar:
            value = str(metavar)
        else:
            value = action.dest.upper().replace("-", "_")
        if action.nargs == "?":
            value = f"[{value}]"
        elif action.nargs in ("*", "+"):
            value = f"{value}..."
        return f"{left} {value}"
    metavar = action.metavar or action.dest.upper()
    if isinstance(metavar, tuple):
        value = " ".join(str(item) for item in metavar)
    else:
        value = str(metavar)
    if action.nargs == "?":
        value = f"[{value}]"
    elif action.nargs in ("*", "+"):
        value = f"{value}..."
    return value


def _action_help(action: argparse.Action) -> str:
    help_text = "" if action.help is argparse.SUPPRESS else str(action.help or "")
    if "%(default)" in help_text:
        help_text = help_text.replace("%(default)s", str(action.default))
    chunks = [help_text.strip()]
    choices = getattr(action, "choices", None)
    if choices and not isinstance(action, argparse._SubParsersAction):
        try:
            chunks.append("Choices: " + ", ".join(str(item) for item in choices))
        except TypeError:
            pass
    if action.required and action.option_strings:
        chunks.append("Required.")
    return " ".join(chunk for chunk in chunks if chunk)


def _public_actions(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    out: list[argparse.Action] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            continue
        if isinstance(action, argparse._HelpAction):
            continue
        if action.help is argparse.SUPPRESS:
            continue
        out.append(action)
    return out


def _child_names(parser: argparse.ArgumentParser) -> list[str]:
    names: list[str] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.extend(item[0] for item in _group_subparsers(action))
    return names


def _option_reference(parser: argparse.ArgumentParser, specs: Iterable[CommandSpec], *, audience: str) -> str:
    lines: list[str] = []
    lines.append("## Complete CLI reference (generated from the parser)")
    lines.append("")
    lines.append(
        "This appendix is generated from `devtool.cli.build_parser()`. It is deliberately exhaustive: "
        "every canonical command/subcommand, alias, positional argument, and non-hidden option exposed "
        "by this source tree appears here. Hidden compatibility switches remain documented in the "
        "compatibility/migration material rather than being promoted as normal usage."
    )
    lines.append("")

    lines.append("### Global invocation options")
    lines.append("")
    lines.append("These options are accepted at the root `devtool` invocation and shape selection, execution, output, UI/HUD integration, and Android/Termux toolchains.")
    lines.append("")
    lines.append("| Syntax | Meaning |")
    lines.append("| --- | --- |")
    for action in _public_actions(parser):
        lines.append(f"| `{_md(_action_syntax(action))}` | {_md(_action_help(action)) or 'Argument accepted by the root parser.'} |")
    lines.append("")

    for spec in specs:
        command = "devtool " + " ".join(spec.path)
        lines.append(f"### `{command}`")
        lines.append("")
        description = spec.help_text or spec.parser.description or spec.parser.epilog or ""
        if description:
            lines.append(_md(description))
            lines.append("")
        if spec.aliases:
            alias_prefix = " ".join(spec.path[:-1])
            rendered = ["devtool " + ((alias_prefix + " ") if alias_prefix else "") + alias for alias in spec.aliases]
            lines.append("**Aliases:** " + ", ".join(f"`{item}`" for item in rendered))
            lines.append("")
        children = _child_names(spec.parser)
        if children:
            lines.append("**Subcommands:** " + ", ".join(f"`{item}`" for item in children))
            lines.append("")
        actions = _public_actions(spec.parser)
        if actions:
            lines.append("| Argument / option | Meaning |")
            lines.append("| --- | --- |")
            for action in actions:
                help_text = _action_help(action)
                if not help_text:
                    help_text = "Accepted by this command."
                lines.append(f"| `{_md(_action_syntax(action))}` | {_md(help_text)} |")
            lines.append("")
        elif not children:
            lines.append("This command has no additional public command-local arguments.")
            lines.append("")
        if audience == "ai" and actions:
            machine_options = [opt for action in actions for opt in action.option_strings if opt in {"--json", "--machine", "--machine-file", "--dry-run"}]
            if machine_options:
                lines.append("Agent note: machine-friendly controls on this command include " + ", ".join(f"`{item}`" for item in machine_options) + ".")
                lines.append("")
    return "\n".join(lines)


HUMAN_BODY = r'''# Devtool complete guide for humans

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
'''

AI_BODY = r'''# Devtool complete guide for AI agents

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

Use `apply-overlay`, `apply-overlays`, `apply-snapshot`, or the unified `apply` surface. `--copy` is useful when the source artifact is on transient/shared storage. Apply transactions journal mutations. Validation can be deferred with explicit policy when the workflow calls for it; do not describe a deferred apply as validated.

Use Devtool `commit`/artifact commit policy when the repository workflow expects Devtool-generated commit evidence. `history` and `rollback` are the canonical recovery path for prior Devtool operations. `cleanup` manages backup retention; do not manually delete transaction data while an operation may need recovery.

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
'''


def generate() -> tuple[str, str]:
    parser = build_parser()
    specs = command_specs(parser)
    human = HUMAN_BODY.rstrip() + "\n\n" + _option_reference(parser, specs, audience="human") + "\n"
    ai = AI_BODY.rstrip() + "\n\n" + _option_reference(parser, specs, audience="ai") + "\n"
    return human, ai


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    check = "--check" in args
    human, ai = generate()
    expected = ((HUMAN_PATH, human), (AI_PATH, ai))
    if check:
        stale = []
        for path, content in expected:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(path.relative_to(ROOT).as_posix())
        if stale:
            print("stale generated guide(s): " + ", ".join(stale), file=sys.stderr)
            return 1
        print(f"complete feature guides are current ({len(command_specs(build_parser()))} command parsers)")
        return 0
    for path, content in expected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
