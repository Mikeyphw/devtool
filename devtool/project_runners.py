from __future__ import annotations

"""Project-type command planning for devtool.

This module intentionally owns *planning* only: it turns a ``ProjectConfig``
plus an action into ``CommandSpec`` objects. Process execution, progress
parsing, logging, transactions, and rollback remain in :mod:`devtool.runners`.
Keeping that boundary small lets new project types be added without changing
the execution engine.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from pathlib import Path
import json
import os
import re
import shlex
import shutil
import sys
from typing import Iterable

from .build_optimization import optimized_build_env, resolve_build_policy
from .execution_environments import detect_current_environment
from .models import CommandSpec, ProjectConfig


@dataclass(frozen=True, slots=True)
class RunnerCapabilities:
    """Features currently implemented by a project runner.

    Capabilities describe devtool behavior, not merely features offered by an
    underlying toolchain. A runner should only report a capability after it is
    wired through devtool's command planning and execution flow.
    """

    restore: bool = False
    build: bool = False
    test: bool = False
    selective_tests: bool = False
    validate: bool = False
    lint: bool = False
    run: bool = False
    package: bool = False
    clean: bool = False
    inspect: bool = False
    doctor: bool = False
    collect_artifacts: bool = False
    verify_artifacts: bool = False
    instrumentation: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {item.name: bool(getattr(self, item.name)) for item in fields(self)}

    def supported(self) -> tuple[str, ...]:
        return tuple(name for name, enabled in self.as_dict().items() if enabled)

    def unsupported(self) -> tuple[str, ...]:
        return tuple(name for name, enabled in self.as_dict().items() if not enabled)




def _optimized_env(config: ProjectConfig, kind: str) -> dict[str, str]:
    return optimized_build_env(config, kind, config.env)


def _project_root(config: ProjectConfig) -> Path:
    return Path(config.project_root or config.repo_root).expanduser().resolve()


def _validation_metadata(specs: list[CommandSpec], scope: str) -> list[CommandSpec]:
    total = len(specs)
    for index, spec in enumerate(specs, start=1):
        metadata = dict(spec.metadata)
        metadata.update({
            "validation_pipeline": True,
            "validation_phase_index": index,
            "validation_phase_total": total,
            "validation_scope": scope,
        })
        spec.metadata = metadata
    return specs


def _tool_metadata(kind: str, root: Path, **extra: object) -> dict[str, object]:
    metadata: dict[str, object] = {
        "runner": kind,
        "project_type": kind,
        "project_root": str(root),
        "progress_parser": "generic",
        "progress_tool": kind,
    }
    metadata.update(extra)
    return metadata


class ProjectRunner(ABC):
    """Language-neutral project command planner."""

    kind = "unknown"
    display_name = "Custom commands"
    capabilities = RunnerCapabilities()

    def capabilities_for(self, config: ProjectConfig) -> RunnerCapabilities:
        return self.capabilities

    def supports(self, config: ProjectConfig, capability: str) -> bool:
        values = self.capabilities_for(config).as_dict()
        return bool(values.get(capability, False))

    def inspect_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        return []

    def doctor_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        return []

    @abstractmethod
    def build_specs(self, config: ProjectConfig, action: str) -> list[CommandSpec]:
        """Plan one action without executing it."""

    def build_validation_specs(
        self,
        config: ProjectConfig,
        *,
        wine: bool = False,
        skip_restore: bool = False,
        skip_build: bool = False,
        skip_test: bool = False,
    ) -> list[CommandSpec]:
        specs: list[CommandSpec] = []
        ran_restore = False
        ran_build = False

        if not skip_restore:
            restore_specs = self.build_specs(config, "restore")
            specs.extend(restore_specs)
            ran_restore = bool(restore_specs)

        if not skip_build:
            build_specs = self.build_specs(config, "build")
            build_specs = self.optimize_build_after_restore(
                config,
                build_specs,
                restore_ran=ran_restore,
            )
            specs.extend(build_specs)
            ran_build = bool(build_specs)

        if not skip_test:
            test_specs = self.build_specs(config, "wine-test" if wine else "test")
            test_specs = self.optimize_test_after_build(
                config,
                test_specs,
                build_ran=ran_build,
            )
            specs.extend(test_specs)

        if not specs:
            raise SystemExit("Validation would run no commands. Clear at least one skip flag.")
        total = len(specs)
        for index, spec in enumerate(specs, start=1):
            metadata = dict(spec.metadata)
            metadata["validation_pipeline"] = True
            metadata["validation_phase_index"] = index
            metadata["validation_phase_total"] = total
            spec.metadata = metadata
        return specs

    def optimize_build_after_restore(
        self,
        config: ProjectConfig,
        specs: list[CommandSpec],
        *,
        restore_ran: bool,
    ) -> list[CommandSpec]:
        return specs

    def optimize_test_after_build(
        self,
        config: ProjectConfig,
        specs: list[CommandSpec],
        *,
        build_ran: bool,
    ) -> list[CommandSpec]:
        return specs


def _maybe_explicit(command_name: str, text: str, env: dict[str, str] | None = None) -> list[CommandSpec]:
    if not text:
        return []
    return [
        CommandSpec(
            command_name,
            shlex.split(text),
            env=env or {},
            metadata={"explicit": True},
        )
    ]


def _dotnet_verb_index(argv: Iterable[str], verb: str) -> int | None:
    values = list(argv)
    for index, value in enumerate(values):
        if Path(str(value)).name == "dotnet" and index + 1 < len(values):
            if str(values[index + 1]).lower() == verb:
                return index + 1
    return None


def is_dotnet_build_argv(argv: list[str]) -> bool:
    return _dotnet_verb_index(argv, "build") is not None


def is_dotnet_test_argv(argv: list[str]) -> bool:
    return _dotnet_verb_index(argv, "test") is not None


def add_no_restore_to_dotnet_build_spec(
    spec: CommandSpec,
    *,
    allow_explicit: bool = False,
) -> CommandSpec:
    if spec.metadata.get("explicit") and not allow_explicit:
        return spec
    verb_index = _dotnet_verb_index(spec.argv, "build")
    if verb_index is None or "--no-restore" in spec.argv:
        return spec

    argv = list(spec.argv)
    argv.insert(verb_index + 1, "--no-restore")
    metadata = dict(spec.metadata)
    metadata["validation_no_restore"] = True
    return CommandSpec(
        spec.name,
        argv,
        env=spec.env,
        cwd=spec.cwd,
        destructive=spec.destructive,
        metadata=metadata,
    )


def add_no_restore_to_dotnet_test_spec(
    spec: CommandSpec,
    *,
    allow_explicit: bool = False,
) -> CommandSpec:
    if spec.metadata.get("explicit") and not allow_explicit:
        return spec
    verb_index = _dotnet_verb_index(spec.argv, "test")
    if verb_index is None or "--no-restore" in spec.argv or "--no-build" in spec.argv:
        return spec

    argv = list(spec.argv)
    argv.insert(verb_index + 1, "--no-restore")
    metadata = dict(spec.metadata)
    metadata["validation_no_restore"] = True
    return CommandSpec(
        spec.name,
        argv,
        env=spec.env,
        cwd=spec.cwd,
        destructive=spec.destructive,
        metadata=metadata,
    )


def add_no_build_to_dotnet_test_spec(
    spec: CommandSpec,
    *,
    allow_explicit: bool = False,
) -> CommandSpec:
    if spec.metadata.get("explicit") and not allow_explicit:
        return spec
    verb_index = _dotnet_verb_index(spec.argv, "test")
    if verb_index is None or "--no-build" in spec.argv:
        return spec

    argv = list(spec.argv)
    argv.insert(verb_index + 1, "--no-build")
    metadata = dict(spec.metadata)
    metadata["validation_no_build"] = True
    return CommandSpec(
        spec.name,
        argv,
        env=spec.env,
        cwd=spec.cwd,
        destructive=spec.destructive,
        metadata=metadata,
    )


def add_no_build_to_dotnet_test_specs(
    specs: list[CommandSpec],
    *,
    allow_explicit: bool = True,
) -> tuple[list[CommandSpec], int]:
    """Add ``--no-build`` to recognizable ``dotnet test`` commands."""

    updated: list[CommandSpec] = []
    changed = 0
    for spec in specs:
        new_spec = add_no_build_to_dotnet_test_spec(spec, allow_explicit=allow_explicit)
        if new_spec.argv != spec.argv:
            changed += 1
            metadata = dict(new_spec.metadata)
            metadata["user_no_build"] = True
            new_spec = CommandSpec(
                new_spec.name,
                new_spec.argv,
                env=new_spec.env,
                cwd=new_spec.cwd,
                destructive=new_spec.destructive,
                metadata=metadata,
            )
        updated.append(new_spec)
    return updated, changed


def _dotnet_variant_options(config: ProjectConfig) -> dict[str, str]:
    options = config.runner_options.get("dotnet", {})
    options = dict(options) if isinstance(options, dict) else {}
    values = {
        "configuration": str(options.get("configuration") or "").strip(),
        "framework": str(options.get("framework") or "").strip(),
        "runtime": str(options.get("runtime") or "").strip(),
    }
    variant = str(config.variant or "").strip().lower()
    if variant and not values["configuration"] and variant in {"debug", "release"}:
        values["configuration"] = variant[:1].upper() + variant[1:]
    return {key: value for key, value in values.items() if value}


def _dotnet_insert_option(argv: list[str], verb_index: int, flags: tuple[str, ...], flag: str, value: str) -> list[str]:
    if any(item in argv for item in flags):
        return argv
    updated = list(argv)
    updated[verb_index + 1:verb_index + 1] = [flag, value]
    return updated


def apply_dotnet_variant_to_spec(config: ProjectConfig, spec: CommandSpec) -> CommandSpec:
    values = _dotnet_variant_options(config)
    if not values:
        return spec

    argv = list(spec.argv)
    metadata = dict(spec.metadata)
    for verb in ("restore", "build", "test", "run", "clean"):
        verb_index = _dotnet_verb_index(argv, verb)
        if verb_index is None:
            continue
        if values.get("runtime"):
            argv = _dotnet_insert_option(argv, verb_index, ("-r", "--runtime"), "-r", values["runtime"])
        if verb in {"build", "test", "run", "clean"} and values.get("configuration"):
            argv = _dotnet_insert_option(argv, verb_index, ("-c", "--configuration"), "-c", values["configuration"])
        if verb in {"build", "test", "run"} and values.get("framework"):
            argv = _dotnet_insert_option(argv, verb_index, ("-f", "--framework"), "-f", values["framework"])
        metadata["variant"] = config.variant
        metadata["dotnet_variant"] = values
        break
    return CommandSpec(
        spec.name,
        argv,
        env=spec.env,
        cwd=spec.cwd,
        destructive=spec.destructive,
        metadata=metadata,
    )


class DotnetRunner(ProjectRunner):
    kind = "dotnet"
    display_name = ".NET"
    capabilities = RunnerCapabilities(
        restore=True,
        build=True,
        test=True,
        selective_tests=True,
        validate=True,
        clean=True,
        run=True,
        package=True,
    )

    def build_specs(self, config: ProjectConfig, action: str) -> list[CommandSpec]:
        env = _optimized_env(config, self.kind)
        restore_target = config.restore_target or config.build_target
        build_target = config.build_target or restore_target
        test_target = config.test_target or build_target
        run_target = config.resolve_repo_relative(config.run_target) if config.run_target else build_target

        if not any([restore_target, build_target, test_target, run_target]) and action in {
            "restore",
            "build",
            "test",
            "wine-test",
            "run",
        }:
            raise SystemExit(
                "No dotnet target resolved. Use --target, --solution, --project, or configure .devtool.toml."
            )

        if action == "restore":
            specs = _maybe_explicit("restore", config.restore_command, env) or [
                CommandSpec("restore", ["dotnet", "restore", restore_target], env=env)
            ]
            return [apply_dotnet_variant_to_spec(config, spec) for spec in specs]
        if action == "build":
            specs = _maybe_explicit("build", config.build_command, env) or [
                CommandSpec("build", ["dotnet", "build", build_target], env=env)
            ]
            return [apply_dotnet_variant_to_spec(config, spec) for spec in specs]
        if action in {"test", "wine-test"}:
            from .test_selection import apply_test_selection
            name = "wine-test" if action == "wine-test" else "test"
            specs = _maybe_explicit(name, config.test_command, env) or [
                CommandSpec(name, ["dotnet", "test", test_target], env=env)
            ]
            return [apply_test_selection(config, apply_dotnet_variant_to_spec(config, spec)) for spec in specs]
        if action == "run":
            specs = _maybe_explicit("run", config.run_command, env) or [
                CommandSpec("run", ["dotnet", "run", "--project", run_target], env=env)
            ]
            return [apply_dotnet_variant_to_spec(config, spec) for spec in specs]
        if action == "clean":
            return [apply_dotnet_variant_to_spec(config, CommandSpec("clean", ["dotnet", "clean", build_target], env=env))]
        if action == "package":
            if config.package_command:
                return [CommandSpec("package", shlex.split(config.package_command), env=env)]
            if (config.repo_root / "scripts" / "package-release.sh").exists():
                return [CommandSpec("package", ["bash", "./scripts/package-release.sh"], env=env)]
            raise SystemExit("No package command configured.")
        raise SystemExit(f"Unsupported action for dotnet: {action}")

    @staticmethod
    def _same_target(config: ProjectConfig, left: str, right: str) -> bool:
        if not left or not right:
            return False
        left_path = Path(left).expanduser()
        right_path = Path(right).expanduser()
        if not left_path.is_absolute():
            left_path = config.repo_root / left_path
        if not right_path.is_absolute():
            right_path = config.repo_root / right_path
        try:
            return left_path.resolve() == right_path.resolve()
        except OSError:
            return os.path.normcase(os.path.normpath(str(left_path))) == os.path.normcase(os.path.normpath(str(right_path)))

    @staticmethod
    def _dotnet_options(config: ProjectConfig) -> dict[str, object]:
        value = config.runner_options.get("dotnet", {})
        return dict(value) if isinstance(value, dict) else {}

    def build_validation_specs(
        self,
        config: ProjectConfig,
        *,
        wine: bool = False,
        skip_restore: bool = False,
        skip_build: bool = False,
        skip_test: bool = False,
    ) -> list[CommandSpec]:
        options = self._dotnet_options(config)
        explicit_restore = bool(options.get("explicit_restore", False))
        coalesce = bool(options.get("coalesce_validation", True))
        has_explicit_commands = any((config.restore_command, config.build_command, config.test_command))

        # Preserve custom command ordering and side effects. Direct dotnet
        # commands still receive the legacy --no-restore/--no-build flags.
        if has_explicit_commands or explicit_restore:
            return super().build_validation_specs(
                config,
                wine=wine,
                skip_restore=skip_restore,
                skip_build=skip_build,
                skip_test=skip_test,
            )

        restore_target = config.restore_target or config.build_target
        build_target = config.build_target or restore_target
        test_target = config.test_target or build_target
        specs: list[CommandSpec] = []

        # dotnet test already restores and builds the supplied solution/project.
        # When build and test use the same target, one process is equivalent to
        # the previous restore + build + test pipeline and preserves compiler,
        # MSBuild, and NuGet state in a single invocation.
        if (
            coalesce
            and not skip_build
            and not skip_test
            and self._same_target(config, build_target, test_target)
        ):
            spec = self.build_specs(config, "wine-test" if wine else "test")[0]
            if skip_restore:
                spec = add_no_restore_to_dotnet_test_spec(spec, allow_explicit=True)
            metadata = dict(spec.metadata)
            metadata.update({
                "coalesced_dotnet_validation": True,
                "validation_components": [
                    *([] if skip_restore else ["restore"]),
                    "build",
                    "test",
                ],
                "contains_build": True,
                "contains_tests": True,
                "implicit_restore": not skip_restore,
            })
            spec = CommandSpec(
                "dotnet-validation",
                spec.argv,
                env=spec.env,
                cwd=spec.cwd,
                destructive=spec.destructive,
                metadata=metadata,
            )
            specs.append(spec)
        else:
            build_ran = False
            if not skip_build:
                build_spec = self.build_specs(config, "build")[0]
                if skip_restore:
                    build_spec = add_no_restore_to_dotnet_build_spec(build_spec, allow_explicit=True)
                else:
                    build_spec.metadata["implicit_restore"] = True
                build_spec.metadata["validation_components"] = [
                    *([] if skip_restore else ["restore"]),
                    "build",
                ]
                specs.append(build_spec)
                build_ran = True
            elif not skip_restore and skip_test:
                specs.extend(self.build_specs(config, "restore"))

            if not skip_test:
                test_spec = self.build_specs(config, "wine-test" if wine else "test")[0]
                if build_ran and self._same_target(config, build_target, test_target):
                    test_spec = add_no_build_to_dotnet_test_spec(test_spec, allow_explicit=True)
                elif skip_restore:
                    test_spec = add_no_restore_to_dotnet_test_spec(test_spec, allow_explicit=True)
                else:
                    test_spec.metadata["implicit_restore"] = True
                test_spec.metadata["contains_tests"] = True
                specs.append(test_spec)

        if not specs:
            raise SystemExit("Validation would run no commands. Clear at least one skip flag.")
        total = len(specs)
        for index, spec in enumerate(specs, start=1):
            metadata = dict(spec.metadata)
            metadata.update({
                "validation_pipeline": True,
                "validation_phase_index": index,
                "validation_phase_total": total,
                "validation_scope": "dotnet-desktop",
            })
            spec.metadata = metadata
        return specs

    def optimize_build_after_restore(
        self,
        config: ProjectConfig,
        specs: list[CommandSpec],
        *,
        restore_ran: bool,
    ) -> list[CommandSpec]:
        if not restore_ran:
            return specs
        return [
            add_no_restore_to_dotnet_build_spec(spec, allow_explicit=True)
            for spec in specs
        ]

    def optimize_test_after_build(
        self,
        config: ProjectConfig,
        specs: list[CommandSpec],
        *,
        build_ran: bool,
    ) -> list[CommandSpec]:
        if not build_ran:
            return specs
        return [
            add_no_build_to_dotnet_test_spec(spec, allow_explicit=True)
            for spec in specs
        ]


class AndroidGradleRunner(ProjectRunner):
    kind = "android"
    display_name = "Android/Gradle"
    capabilities = RunnerCapabilities(
        restore=True,
        build=True,
        test=True,
        selective_tests=True,
        validate=True,
        lint=True,
        clean=True,
        inspect=True,
        doctor=True,
        collect_artifacts=True,
        verify_artifacts=True,
        instrumentation=True,
    )

    def inspect_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        from .android_integration import android_inspect_rows
        return android_inspect_rows(config)

    def doctor_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        from .android_integration import android_doctor_rows
        return android_doctor_rows(config)

    def build_specs(self, config: ProjectConfig, action: str) -> list[CommandSpec]:
        from .android_integration import android_builder_arguments, android_project_root, configured_tasks, plan_android_command
        from .test_selection import apply_test_selection, resolve_test_selection

        selection = resolve_test_selection(config) if action in {"test", "instrumentation"} else None
        if action == "test" and selection is not None and selection.instrumented:
            action = "instrumentation"
        if action == "instrumentation":
            android = config.runner_options.get("android", {})
            android = dict(android) if isinstance(android, dict) else {}
            root = android_project_root(config)
            argv = [
                sys.executable, "-m", "devtool.android_instrumentation", str(root),
                "--variant", str(config.variant or android.get("variant") or "debug"),
                "--device-serial", str(config.device_serial or android.get("device_serial") or ""),
                "--install-command", str(android.get("install_command") or "sudo pm install -r -t"),
                "--am-command", str(android.get("am_command") or "sudo am instrument"),
            ]
            runner = str(android.get("test_runner") or "").strip()
            target_package = str(android.get("target_package") or "").strip()
            if runner:
                argv.extend(["--runner", runner])
            if target_package:
                argv.extend(["--target-package", target_package])
            if bool(android.get("keep_installed", False)):
                argv.append("--keep-installed")
            if selection is not None:
                for value in selection.classes:
                    argv.extend(["--class", value])
                for value in selection.methods:
                    argv.extend(["--method", value])
                for value in selection.tags:
                    argv.extend(["--annotation", value])
                for value in selection.exclude_tags:
                    argv.extend(["--not-annotation", value])
            spec = CommandSpec(
                "instrumentation", argv, env=_optimized_env(config, self.kind), cwd=str(root),
                metadata={
                    "runner": self.kind,
                    "project_type": self.kind,
                    "project_root": str(root),
                    "progress_parser": "gradle",
                    "report_parser": "android-instrumentation",
                    "execution_environment": "android-device",
                },
            )
            return [apply_test_selection(config, spec)]

        explicit = {
            "restore": config.restore_command,
            "build": config.build_command,
            "test": config.test_command,
        }.get(action, "")
        if explicit:
            specs = _maybe_explicit(action, explicit, _optimized_env(config, self.kind))
            for spec in specs:
                spec.metadata.update({
                    "runner": self.kind,
                    "project_type": self.kind,
                    "progress_parser": "gradle",
                    "report_parser": "gradle-junit" if action == "test" else "",
                })
            return [apply_test_selection(config, spec) for spec in specs] if action == "test" else specs

        if action not in {"restore", "build", "test", "lint", "clean"}:
            raise SystemExit(f"Unsupported Android/Gradle action: {action}")

        tasks = configured_tasks(config, action)
        if action == "lint" and not tasks:
            raise SystemExit(
                "No Gradle lint task was discovered. Configure [tasks].lint for this target."
            )
        plan = plan_android_command(config, action)
        argv = [*plan.argv_prefix, *android_builder_arguments(config, action)]
        report_parser = ""
        if action == "test":
            report_parser = "gradle-junit"
        elif action == "lint":
            report_parser = "android-lint"
        specs = [
            CommandSpec(
                action,
                argv,
                env=_optimized_env(config, self.kind),
                cwd=str(plan.project_root),
                metadata={
                    "runner": self.kind,
                    "project_type": self.kind,
                    "gradle_tasks": list(plan.default_tasks),
                    "android_builder": str(plan.builder),
                    "project_root": str(plan.project_root),
                    "collect_artifacts": action == "build",
                    "verify_artifacts": action == "build",
                    "progress_parser": "gradle",
                    "report_parser": report_parser,
                },
            )
        ]
        if action == "test":
            specs = [apply_test_selection(config, spec) for spec in specs]
        return specs

    def build_validation_specs(
        self,
        config: ProjectConfig,
        *,
        wine: bool = False,
        skip_restore: bool = False,
        skip_build: bool = False,
        skip_test: bool = False,
    ) -> list[CommandSpec]:
        from .android_integration import (
            android_builder_arguments_for_tasks,
            android_project_root,
            configured_tasks,
            plan_android_command,
        )
        from .test_selection import apply_test_selection

        # Explicit shell commands may carry action-specific setup or side
        # effects, so preserve the legacy multi-command pipeline for them.
        has_explicit_commands = any((
            not skip_restore and bool(config.restore_command),
            not skip_build and bool(config.build_command),
            not skip_test and bool(config.test_command),
        ))
        specs: list[CommandSpec] = []
        if has_explicit_commands:
            if not skip_restore:
                specs.extend(self.build_specs(config, "restore"))
            if not skip_build:
                specs.extend(self.build_specs(config, "build"))
            if not skip_test:
                specs.extend(self.build_specs(config, "test"))
            if configured_tasks(config, "lint"):
                specs.extend(self.build_specs(config, "lint"))
        else:
            tasks: list[str] = []
            components: list[str] = []

            if not skip_restore:
                restore_tasks = configured_tasks(config, "restore")
                # The default restore task is Gradle `help`, which performs a
                # full configuration pass but contributes no validation once
                # real build/test/lint tasks run in the same invocation.
                if restore_tasks and restore_tasks != ["help"]:
                    tasks.extend(restore_tasks)
                    components.append("restore")
            if not skip_build:
                tasks.extend(configured_tasks(config, "build"))
                components.append("build")
            if not skip_test:
                tasks.extend(configured_tasks(config, "test"))
                components.append("test")
            lint_tasks = configured_tasks(config, "lint")
            if lint_tasks:
                tasks.extend(lint_tasks)
                components.append("lint")

            unique_tasks = list(dict.fromkeys(task for task in tasks if task))
            if unique_tasks:
                plan_action = "build" if "build" in components else components[0]
                plan = plan_android_command(config, plan_action)
                report_parsers: list[str] = []
                if "test" in components:
                    report_parsers.append("gradle-junit")
                if "lint" in components:
                    report_parsers.append("android-lint")
                metadata = {
                    "runner": self.kind,
                    "project_type": self.kind,
                    "gradle_tasks": unique_tasks,
                    "android_builder": str(plan.builder),
                    "project_root": str(android_project_root(config)),
                    "collect_artifacts": "build" in components,
                    "verify_artifacts": "build" in components,
                    "progress_parser": "gradle",
                    "report_parser": report_parsers[0] if report_parsers else "",
                    "report_parsers": report_parsers,
                    "validation_components": components,
                    "contains_tests": "test" in components,
                    "coalesced_gradle_validation": True,
                }
                spec = CommandSpec(
                    "android-validation" if self.kind == "android" else "gradle-validation",
                    [
                        *plan.argv_prefix,
                        *android_builder_arguments_for_tasks(
                            config,
                            unique_tasks,
                            scan_artifacts="build" in components,
                        ),
                    ],
                    env=_optimized_env(config, self.kind),
                    cwd=str(plan.project_root),
                    metadata=metadata,
                )
                if "test" in components:
                    spec = apply_test_selection(config, spec)
                specs.append(spec)

        android_options = config.runner_options.get("android", {})
        android_options = dict(android_options) if isinstance(android_options, dict) else {}
        instrumentation_policy = str(android_options.get("instrumentation") or "never").strip().lower()
        if not skip_test and instrumentation_policy in {"required", "always"}:
            selection_options = dict(config.runner_options.get("test_selection", {}) or {})
            selection_options["instrumented"] = True
            original = config.runner_options.get("test_selection")
            config.runner_options["test_selection"] = selection_options
            try:
                specs.extend(self.build_specs(config, "instrumentation"))
            finally:
                if original is None:
                    config.runner_options.pop("test_selection", None)
                else:
                    config.runner_options["test_selection"] = original
        elif not skip_test and instrumentation_policy == "when-device-present":
            if shutil.which("pm") or shutil.which("adb"):
                selection_options = dict(config.runner_options.get("test_selection", {}) or {})
                selection_options["instrumented"] = True
                original = config.runner_options.get("test_selection")
                config.runner_options["test_selection"] = selection_options
                try:
                    specs.extend(self.build_specs(config, "instrumentation"))
                finally:
                    if original is None:
                        config.runner_options.pop("test_selection", None)
                    else:
                        config.runner_options["test_selection"] = original

        if not specs:
            raise SystemExit("Validation would run no Android/Gradle commands. Clear a skip flag.")
        total = len(specs)
        for index, spec in enumerate(specs, start=1):
            metadata = dict(spec.metadata)
            metadata.update({
                "validation_pipeline": True,
                "validation_phase_index": index,
                "validation_phase_total": total,
                "validation_scope": "android-full",
            })
            spec.metadata = metadata
        return specs



class JvmGradleRunner(AndroidGradleRunner):
    kind = "gradle"
    display_name = "Gradle/JVM"
    capabilities = RunnerCapabilities(
        restore=True,
        build=True,
        test=True,
        selective_tests=True,
        validate=True,
        lint=True,
        clean=True,
        inspect=True,
        doctor=True,
        collect_artifacts=True,
        verify_artifacts=True,
        instrumentation=False,
    )

    def build_specs(self, config: ProjectConfig, action: str) -> list[CommandSpec]:
        specs = super().build_specs(config, action)
        for spec in specs:
            spec.metadata["runner"] = self.kind
            spec.metadata["project_type"] = "gradle"
        return specs


class PythonRunner(ProjectRunner):
    kind = "python"
    display_name = "Python"
    capabilities = RunnerCapabilities(
        restore=True,
        build=True,
        test=True,
        selective_tests=True,
        validate=True,
        lint=True,
        run=True,
        package=True,
        clean=True,
        inspect=True,
        doctor=True,
        collect_artifacts=True,
        verify_artifacts=True,
    )

    def _root(self, config: ProjectConfig) -> Path:
        return Path(config.project_root or config.repo_root).resolve()

    def _options(self, config: ProjectConfig) -> dict[str, object]:
        value = config.runner_options.get("python", {})
        return dict(value) if isinstance(value, dict) else {}

    def _info(self, config: ProjectConfig):
        from .python_support import inspect_python_project
        return inspect_python_project(self._root(config), str(self._options(config).get("manager") or "auto"))

    def _venv_python(self, config: ProjectConfig) -> str:
        options = self._options(config)
        value = str(options.get("environment") or "").strip()
        if not value:
            name = config.selected_target or "python"
            value = f".devtool/venvs/{name}"
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = config.repo_root / path
        return str(path.resolve() / "bin" / "python")

    def _run_prefix(self, config: ProjectConfig) -> list[str]:
        info = self._info(config)
        if info.manager == "uv":
            return ["uv", "run"]
        if info.manager == "poetry":
            return ["poetry", "run"]
        if info.manager == "pdm":
            return ["pdm", "run"]
        if info.manager == "pipenv":
            return ["pipenv", "run"]
        if info.manager == "hatch":
            return ["hatch", "run"]
        return [self._venv_python(config)]

    def inspect_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        info = self._info(config)
        return [
            ("project_root", str(info.root)),
            ("project_type", "Python"),
            ("package", info.package_name),
            ("environment_manager", info.manager),
            ("lock_file", info.lock_file or "none"),
            ("test_framework", info.framework),
            ("build_backend", info.build_backend),
            ("python", config.python_executable),
        ]

    def doctor_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        info = self._info(config)
        tools = [config.python_executable, info.manager if info.manager != "venv" else ""]
        rows = [("python_project", "READY" if info.root.is_dir() else "MISSING")]
        for tool in tools:
            if not tool:
                continue
            executable = shutil.which(tool) if not Path(tool).is_absolute() else (tool if Path(tool).exists() else None)
            rows.append((tool, f"READY: {executable}" if executable else "MISSING"))
        return rows

    def _pytest_spec(self, config: ProjectConfig) -> CommandSpec:
        from .test_selection import apply_test_selection
        root = self._root(config)
        report = root / ".devtool" / "reports" / "pytest.xml"
        prefix = self._run_prefix(config)
        argv = [*prefix, "-m", "pytest", f"--junitxml={report}"] if Path(prefix[0]).name.startswith("python") else [*prefix, "pytest", f"--junitxml={report}"]
        spec = CommandSpec(
            "test",
            argv,
            env=_optimized_env(config, self.kind),
            cwd=str(root),
            metadata={
                "runner": self.kind,
                "project_type": self.kind,
                "project_root": str(root),
                "report_parser": "pytest-junit",
                "test_report": str(report),
            },
        )
        return apply_test_selection(config, spec)

    def _lint_specs(self, config: ProjectConfig) -> list[CommandSpec]:
        options = self._options(config)
        configured = options.get("lint")
        if isinstance(configured, str):
            tools = [item.strip() for item in configured.replace(",", " ").split() if item.strip()]
        elif isinstance(configured, (list, tuple)):
            tools = [str(item).strip() for item in configured if str(item).strip()]
        else:
            text = (self._root(config) / "pyproject.toml").read_text(encoding="utf-8", errors="replace") if (self._root(config) / "pyproject.toml").is_file() else ""
            tools = [name for name, marker in (("ruff", "[tool.ruff"), ("mypy", "[tool.mypy"), ("black", "[tool.black"), ("pylint", "[tool.pylint")) if marker in text]
        prefix = self._run_prefix(config)
        specs: list[CommandSpec] = []
        for tool in tools:
            if tool == "ruff":
                command = ["ruff", "check", "."]
            elif tool == "ruff-format":
                command = ["ruff", "format", "--check", "."]
            elif tool == "black":
                command = ["black", "--check", "."]
            elif tool == "isort":
                command = ["isort", "--check-only", "."]
            elif tool in {"mypy", "pyright", "pylint", "bandit"}:
                command = [tool, "."]
            else:
                command = shlex.split(tool)
            argv = [*prefix, *command]
            specs.append(CommandSpec(
                f"lint:{tool}", argv, env=_optimized_env(config, self.kind), cwd=str(self._root(config)),
                metadata={"runner": self.kind, "project_type": self.kind, "lint_tool": tool},
            ))
        return specs

    def build_specs(self, config: ProjectConfig, action: str) -> list[CommandSpec]:
        env = _optimized_env(config, self.kind)
        root = self._root(config)
        info = self._info(config)
        prefix = self._run_prefix(config)

        if action == "restore":
            if config.restore_command:
                return [CommandSpec("restore", shlex.split(config.restore_command), env=env, cwd=str(root))]
            if info.manager == "uv":
                return [CommandSpec("restore", ["uv", "sync", *( ["--frozen"] if info.lock_file else [])], env=env, cwd=str(root))]
            if info.manager == "poetry":
                return [CommandSpec("restore", ["poetry", "install"], env=env, cwd=str(root))]
            if info.manager == "pdm":
                return [CommandSpec("restore", ["pdm", "sync", *( ["--frozen-lockfile"] if info.lock_file else [])], env=env, cwd=str(root))]
            if info.manager == "pipenv":
                return [CommandSpec("restore", ["pipenv", "sync", "--dev"], env=env, cwd=str(root))]
            if info.manager == "hatch":
                return [CommandSpec("restore", ["hatch", "env", "create"], env=env, cwd=str(root))]
            venv_python = self._venv_python(config)
            venv_root = str(Path(venv_python).parent.parent)
            specs: list[CommandSpec] = []
            if not Path(venv_python).is_file():
                specs.append(CommandSpec("venv", [config.python_executable, "-m", "venv", venv_root], env=env, cwd=str(root)))
            if (root / "requirements-dev.txt").exists():
                install = [venv_python, "-m", "pip", "install", "-r", "requirements-dev.txt"]
            elif (root / "requirements.txt").exists():
                install = [venv_python, "-m", "pip", "install", "-r", "requirements.txt"]
            else:
                install = [venv_python, "-m", "pip", "install", "-e", ".[test]"]
            specs.append(CommandSpec("restore", install, env=env, cwd=str(root)))
            return specs
        if action == "build":
            if config.build_command:
                return [CommandSpec("build", shlex.split(config.build_command), env=env, cwd=str(root))]
            argv = [*prefix, "-m", "build"] if Path(prefix[0]).name.startswith("python") else [*prefix, "python", "-m", "build"]
            return [CommandSpec("build", argv, env=env, cwd=str(root), metadata={"collect_artifacts": True, "verify_artifacts": True, "project_type": "python"})]
        if action in {"test", "wine-test"}:
            if config.test_command:
                from .test_selection import apply_test_selection
                return [apply_test_selection(config, CommandSpec("test", shlex.split(config.test_command), env=env, cwd=str(root), metadata={"project_type": "python"}))]
            if info.framework == "pytest":
                return [self._pytest_spec(config)]
            return [CommandSpec("test", [*prefix, "-m", "unittest", "discover"] if Path(prefix[0]).name.startswith("python") else [*prefix, "python", "-m", "unittest", "discover"], env=env, cwd=str(root), metadata={"project_type": "python"})]
        if action == "lint":
            specs = self._lint_specs(config)
            if not specs:
                raise SystemExit("No Python lint tools are configured or declared in pyproject.toml.")
            return specs
        if action == "package-check":
            report = root / ".devtool" / "reports" / "python-packages.json"
            argv = [sys.executable, "-m", "devtool.python_support", "package-check", str(root), "--report", str(report)]
            if bool(self._options(config).get("import_smoke", False)):
                argv.append("--import-smoke")
            return [CommandSpec("package-check", argv, env=env, cwd=str(root), metadata={"report_parser": "python-package", "package_report": str(report), "project_type": "python"})]
        if action == "clean":
            return [CommandSpec("clean", [sys.executable, "-m", "devtool.python_support", "clean", str(root)], env=env, cwd=str(root))]
        if action == "run":
            if config.run_command:
                return [CommandSpec("run", shlex.split(config.run_command), env=env, cwd=str(root))]
            if config.run_module:
                argv = [*prefix, "-m", config.run_module] if Path(prefix[0]).name.startswith("python") else [*prefix, "python", "-m", config.run_module]
                return [CommandSpec("run", argv, env=env, cwd=str(root))]
            if config.run_target:
                return [CommandSpec("run", [*prefix, config.resolve_repo_relative(config.run_target)], env=env, cwd=str(root))]
            raise SystemExit("No Python run target configured. Set run_command, run_module, or run_target.")
        if action == "package":
            return self.build_specs(config, "build")
        raise SystemExit(f"Unsupported action for python: {action}")

    def build_validation_specs(
        self,
        config: ProjectConfig,
        *,
        wine: bool = False,
        skip_restore: bool = False,
        skip_build: bool = False,
        skip_test: bool = False,
    ) -> list[CommandSpec]:
        specs: list[CommandSpec] = []
        if not skip_restore:
            specs.extend(self.build_specs(config, "restore"))
        if not skip_test:
            specs.extend(self.build_specs(config, "test"))
        try:
            specs.extend(self.build_specs(config, "lint"))
        except SystemExit:
            pass
        if not skip_build:
            specs.extend(self.build_specs(config, "build"))
            specs.extend(self.build_specs(config, "package-check"))
        if not specs:
            raise SystemExit("Validation would run no Python commands. Clear a skip flag.")
        total = len(specs)
        for index, spec in enumerate(specs, start=1):
            spec.metadata.update({
                "validation_pipeline": True,
                "validation_phase_index": index,
                "validation_phase_total": total,
                "validation_scope": "python-full",
            })
        return specs



class NodeRunner(ProjectRunner):
    kind = "node"
    display_name = "Node.js"

    def _root(self, config: ProjectConfig) -> Path:
        return _project_root(config)

    def _options(self, config: ProjectConfig) -> dict[str, object]:
        value = config.runner_options.get("node", {})
        return dict(value) if isinstance(value, dict) else {}

    def _package(self, config: ProjectConfig) -> dict[str, object]:
        path = self._root(config) / "package.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return dict(value) if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _scripts(self, config: ProjectConfig) -> dict[str, str]:
        scripts = self._package(config).get("scripts", {})
        return {str(k): str(v) for k, v in scripts.items()} if isinstance(scripts, dict) else {}

    def _manager(self, config: ProjectConfig) -> str:
        options = self._options(config)
        configured = str(options.get("manager") or "auto").strip().lower()
        if configured != "auto":
            return configured
        package_manager = str(self._package(config).get("packageManager") or "").split("@", 1)[0].strip().lower()
        if package_manager in {"npm", "pnpm", "yarn", "bun"}:
            return package_manager
        root = self._root(config)
        for filename, manager in (
            ("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"),
            ("bun.lockb", "bun"), ("bun.lock", "bun"), ("package-lock.json", "npm"),
        ):
            if (root / filename).exists():
                return manager
        return "npm"

    def _script_argv(self, config: ProjectConfig, script: str) -> list[str]:
        manager = self._manager(config)
        if manager == "npm":
            return ["npm", "run", script]
        return [manager, "run", script]

    def capabilities_for(self, config: ProjectConfig) -> RunnerCapabilities:
        scripts = self._scripts(config)
        return RunnerCapabilities(
            restore=True,
            build="build" in scripts or bool(config.build_command),
            test="test" in scripts or bool(config.test_command),
            validate=bool(scripts) or any((config.restore_command, config.build_command, config.test_command)),
            lint=any(name in scripts for name in ("lint", "check")),
            run=any(name in scripts for name in ("start", "dev")) or bool(config.run_command),
            package=True,
            clean="clean" in scripts,
            inspect=True,
            doctor=True,
        )

    def inspect_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        package = self._package(config)
        return [
            ("project_root", str(self._root(config))),
            ("project_type", "Node.js"),
            ("package_manager", self._manager(config)),
            ("package", str(package.get("name") or "")),
            ("scripts", ", ".join(sorted(self._scripts(config))) or "none"),
        ]

    def doctor_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        manager = self._manager(config)
        return [(manager, f"READY: {shutil.which(manager)}" if shutil.which(manager) else "MISSING")]

    def build_specs(self, config: ProjectConfig, action: str) -> list[CommandSpec]:
        root = self._root(config)
        env = _optimized_env(config, self.kind)
        scripts = self._scripts(config)
        manager = self._manager(config)
        options = self._options(config)
        explicit = {
            "restore": config.restore_command, "build": config.build_command,
            "test": config.test_command, "wine-test": config.test_command,
            "run": config.run_command, "package": config.package_command,
        }.get(action, "")
        if explicit:
            return [CommandSpec(action, shlex.split(explicit), env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, explicit=True))]
        if action == "restore":
            if manager == "npm":
                clean = bool(options.get("clean_install", False)) and (root / "package-lock.json").exists()
                argv = ["npm", "ci" if clean else "install", "--prefer-offline", "--no-audit", "--no-fund"]
            elif manager == "pnpm":
                argv = ["pnpm", "install", "--prefer-offline"]
                if (root / "pnpm-lock.yaml").exists():
                    argv.append("--frozen-lockfile")
            elif manager == "yarn":
                argv = ["yarn", "install"]
                if (root / "yarn.lock").exists():
                    argv.append("--immutable" if (root / ".yarnrc.yml").exists() else "--frozen-lockfile")
            elif manager == "bun":
                argv = ["bun", "install"]
                if (root / "bun.lockb").exists() or (root / "bun.lock").exists():
                    argv.append("--frozen-lockfile")
            else:
                raise SystemExit(f"Unsupported Node package manager: {manager}")
            return [CommandSpec("restore", argv, env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, package_manager=manager))]
        script = {"build": "build", "test": "test", "wine-test": "test", "lint": "lint", "clean": "clean"}.get(action)
        if action == "lint" and "lint" not in scripts and "check" in scripts:
            script = "check"
        if script:
            if script not in scripts:
                raise SystemExit(f"package.json has no '{script}' script.")
            return [CommandSpec(action.replace("wine-", ""), self._script_argv(config, script), env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, node_script=script))]
        if action == "run":
            script = "start" if "start" in scripts else "dev" if "dev" in scripts else ""
            if not script:
                raise SystemExit("package.json has no start/dev script and no run_command is configured.")
            return [CommandSpec("run", self._script_argv(config, script), env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, node_script=script))]
        if action == "package":
            argv = [manager, "pack"] if manager != "yarn" else ["yarn", "pack"]
            return [CommandSpec("package", argv, env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root))]
        raise SystemExit(f"Unsupported Node action: {action}")

    def build_validation_specs(self, config: ProjectConfig, *, wine: bool = False, skip_restore: bool = False, skip_build: bool = False, skip_test: bool = False) -> list[CommandSpec]:
        specs: list[CommandSpec] = []
        if not skip_restore:
            specs.extend(self.build_specs(config, "restore"))
        if not skip_build and self.supports(config, "build"):
            specs.extend(self.build_specs(config, "build"))
        if not skip_test and self.supports(config, "test"):
            specs.extend(self.build_specs(config, "test"))
        if self.supports(config, "lint"):
            specs.extend(self.build_specs(config, "lint"))
        if not specs:
            raise SystemExit("Validation would run no Node commands. Clear a skip flag or configure scripts.")
        return _validation_metadata(specs, "node-incremental")


class RustRunner(ProjectRunner):
    kind = "rust"
    display_name = "Rust/Cargo"
    capabilities = RunnerCapabilities(restore=True, build=True, test=True, validate=True, lint=True, run=True, package=True, clean=True, inspect=True, doctor=True)

    def _root(self, config: ProjectConfig) -> Path:
        return _project_root(config)

    def _options(self, config: ProjectConfig) -> dict[str, object]:
        value = config.runner_options.get("rust", {})
        return dict(value) if isinstance(value, dict) else {}

    def _base(self, config: ProjectConfig) -> list[str]:
        root = self._root(config)
        cargo = root / "cargo"
        return [str(cargo)] if cargo.is_file() else ["cargo"]

    def _common(self, config: ProjectConfig) -> list[str]:
        root = self._root(config)
        argv: list[str] = []
        if (root / "Cargo.lock").exists():
            argv.append("--locked")
        if str(config.variant or self._options(config).get("profile") or "").lower() == "release":
            argv.append("--release")
        return argv

    def build_specs(self, config: ProjectConfig, action: str) -> list[CommandSpec]:
        root = self._root(config)
        env = _optimized_env(config, self.kind)
        workers = resolve_build_policy(config, self.kind).max_workers
        explicit = {"restore": config.restore_command, "build": config.build_command, "test": config.test_command, "wine-test": config.test_command, "run": config.run_command, "package": config.package_command}.get(action, "")
        if explicit:
            return [CommandSpec(action, shlex.split(explicit), env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, explicit=True))]
        base = self._base(config)
        common = self._common(config)
        if action == "restore":
            argv = [*base, "fetch", *( ["--locked"] if (root / "Cargo.lock").exists() else [])]
        elif action == "build":
            argv = [*base, "build", *common, "--jobs", str(workers)]
        elif action in {"test", "wine-test"}:
            argv = [*base, "test", "--all-targets", *common, "--jobs", str(workers)]
        elif action == "lint":
            argv = [*base, "clippy", "--all-targets", "--all-features", *common, "--jobs", str(workers), "--", "-D", "warnings"]
        elif action == "run":
            argv = [*base, "run", *common, "--jobs", str(workers)]
        elif action == "package":
            argv = [*base, "package", *( ["--locked"] if (root / "Cargo.lock").exists() else [])]
        elif action == "clean":
            argv = [*base, "clean"]
        else:
            raise SystemExit(f"Unsupported Rust action: {action}")
        return [CommandSpec(action.replace("wine-", ""), argv, env=env, cwd=str(root), destructive=action == "clean", metadata=_tool_metadata(self.kind, root))]

    def build_validation_specs(self, config: ProjectConfig, *, wine: bool = False, skip_restore: bool = False, skip_build: bool = False, skip_test: bool = False) -> list[CommandSpec]:
        options = self._options(config)
        explicit = any((config.restore_command, config.build_command, config.test_command))
        if explicit:
            return super().build_validation_specs(config, wine=wine, skip_restore=skip_restore, skip_build=skip_build, skip_test=skip_test)
        specs: list[CommandSpec] = []
        if not skip_test and (not skip_build and bool(options.get("coalesce_validation", True))):
            spec = self.build_specs(config, "test")[0]
            spec.name = "cargo-validation"
            spec.metadata.update({"validation_components": ["restore", "build", "test"], "contains_build": True, "contains_tests": True})
            specs.append(spec)
        else:
            if not skip_build:
                specs.extend(self.build_specs(config, "build"))
            elif not skip_restore:
                specs.extend(self.build_specs(config, "restore"))
            if not skip_test:
                specs.extend(self.build_specs(config, "test"))
        if bool(options.get("lint", False)):
            specs.extend(self.build_specs(config, "lint"))
        if not specs:
            raise SystemExit("Validation would run no Rust commands. Clear a skip flag.")
        return _validation_metadata(specs, "rust-incremental")

    def inspect_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        return [("project_root", str(self._root(config))), ("project_type", "Rust/Cargo"), ("lock_file", "Cargo.lock" if (self._root(config) / "Cargo.lock").exists() else "none")]

    def doctor_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        return [("cargo", f"READY: {shutil.which('cargo')}" if shutil.which("cargo") else "MISSING")]


class GoRunner(ProjectRunner):
    kind = "go"
    display_name = "Go"
    capabilities = RunnerCapabilities(restore=True, build=True, test=True, validate=True, lint=True, run=True, package=True, clean=True, inspect=True, doctor=True)

    def _root(self, config: ProjectConfig) -> Path:
        return _project_root(config)

    def _options(self, config: ProjectConfig) -> dict[str, object]:
        value = config.runner_options.get("go", {})
        return dict(value) if isinstance(value, dict) else {}

    def _packages(self, config: ProjectConfig) -> list[str]:
        value = self._options(config).get("packages", ["./..."])
        if isinstance(value, str):
            return shlex.split(value) or ["./..."]
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value if str(item).strip()] or ["./..."]
        return ["./..."]

    def build_specs(self, config: ProjectConfig, action: str) -> list[CommandSpec]:
        root = self._root(config)
        env = _optimized_env(config, self.kind)
        workers = resolve_build_policy(config, self.kind).max_workers
        explicit = {"restore": config.restore_command, "build": config.build_command, "test": config.test_command, "wine-test": config.test_command, "run": config.run_command, "package": config.package_command}.get(action, "")
        if explicit:
            return [CommandSpec(action, shlex.split(explicit), env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, explicit=True))]
        packages = self._packages(config)
        if action == "restore":
            argv = ["go", "mod", "download"]
        elif action == "build":
            argv = ["go", "build", "-p", str(workers), *packages]
        elif action in {"test", "wine-test"}:
            argv = ["go", "test", "-p", str(workers), *packages]
        elif action == "lint":
            argv = ["go", "vet", *packages]
        elif action == "run":
            target = str(self._options(config).get("run_target") or config.run_target or ".")
            argv = ["go", "run", target]
        elif action == "package":
            argv = ["go", "build", "-p", str(workers), *packages]
        elif action == "clean":
            argv = ["go", "clean", "-cache", "-testcache"]
        else:
            raise SystemExit(f"Unsupported Go action: {action}")
        return [CommandSpec(action.replace("wine-", ""), argv, env=env, cwd=str(root), destructive=action == "clean", metadata=_tool_metadata(self.kind, root))]

    def build_validation_specs(self, config: ProjectConfig, *, wine: bool = False, skip_restore: bool = False, skip_build: bool = False, skip_test: bool = False) -> list[CommandSpec]:
        options = self._options(config)
        explicit = any((config.restore_command, config.build_command, config.test_command))
        if explicit:
            return super().build_validation_specs(config, wine=wine, skip_restore=skip_restore, skip_build=skip_build, skip_test=skip_test)
        specs: list[CommandSpec] = []
        if not skip_test and not skip_build and bool(options.get("coalesce_validation", True)):
            spec = self.build_specs(config, "test")[0]
            spec.name = "go-validation"
            spec.metadata.update({"validation_components": ["restore", "build", "test"], "contains_build": True, "contains_tests": True})
            specs.append(spec)
        else:
            if not skip_build:
                specs.extend(self.build_specs(config, "build"))
            elif not skip_restore and (self._root(config) / "go.mod").exists():
                specs.extend(self.build_specs(config, "restore"))
            if not skip_test:
                specs.extend(self.build_specs(config, "test"))
        if bool(options.get("vet", True)):
            specs.extend(self.build_specs(config, "lint"))
        if not specs:
            raise SystemExit("Validation would run no Go commands. Clear a skip flag.")
        return _validation_metadata(specs, "go-incremental")

    def inspect_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        return [("project_root", str(self._root(config))), ("project_type", "Go"), ("workspace", "go.work" if (self._root(config) / "go.work").exists() else "go.mod")]

    def doctor_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        return [("go", f"READY: {shutil.which('go')}" if shutil.which("go") else "MISSING")]


class MavenRunner(ProjectRunner):
    kind = "maven"
    display_name = "Maven"
    capabilities = RunnerCapabilities(restore=True, build=True, test=True, validate=True, run=False, package=True, clean=True, inspect=True, doctor=True)

    def _root(self, config: ProjectConfig) -> Path:
        return _project_root(config)

    def _options(self, config: ProjectConfig) -> dict[str, object]:
        value = config.runner_options.get("maven", {})
        return dict(value) if isinstance(value, dict) else {}

    def _base(self, config: ProjectConfig) -> list[str]:
        root = self._root(config)
        wrapper = root / "mvnw"
        command = str(wrapper) if wrapper.is_file() else "mvn"
        workers = resolve_build_policy(config, self.kind).max_workers
        args = [command, "--no-transfer-progress", "-T", str(workers)]
        profiles = self._options(config).get("profiles", [])
        if isinstance(profiles, str) and profiles.strip():
            args.extend(["-P", profiles.strip()])
        elif isinstance(profiles, (list, tuple)) and profiles:
            args.extend(["-P", ",".join(str(item) for item in profiles if str(item).strip())])
        return args

    def build_specs(self, config: ProjectConfig, action: str) -> list[CommandSpec]:
        root = self._root(config)
        env = _optimized_env(config, self.kind)
        explicit = {"restore": config.restore_command, "build": config.build_command, "test": config.test_command, "wine-test": config.test_command, "package": config.package_command}.get(action, "")
        if explicit:
            return [CommandSpec(action, shlex.split(explicit), env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, explicit=True))]
        goals = {
            "restore": ["dependency:go-offline"], "build": ["package", "-DskipTests"],
            "test": ["test"], "wine-test": ["test"], "package": ["package"],
            "clean": ["clean"], "verify": ["verify"],
        }.get(action)
        if goals is None:
            raise SystemExit(f"Unsupported Maven action: {action}")
        return [CommandSpec(action.replace("wine-", ""), [*self._base(config), *goals], env=env, cwd=str(root), destructive=action == "clean", metadata=_tool_metadata(self.kind, root))]

    def build_validation_specs(self, config: ProjectConfig, *, wine: bool = False, skip_restore: bool = False, skip_build: bool = False, skip_test: bool = False) -> list[CommandSpec]:
        options = self._options(config)
        explicit = any((config.restore_command, config.build_command, config.test_command))
        if explicit:
            return super().build_validation_specs(config, wine=wine, skip_restore=skip_restore, skip_build=skip_build, skip_test=skip_test)
        specs: list[CommandSpec] = []
        if not skip_build and not skip_test and bool(options.get("coalesce_validation", True)):
            spec = self.build_specs(config, "verify")[0]
            spec.name = "maven-validation"
            spec.metadata.update({"validation_components": ["restore", "build", "test"], "contains_build": True, "contains_tests": True})
            specs.append(spec)
        else:
            if not skip_build:
                specs.extend(self.build_specs(config, "build"))
            elif not skip_restore:
                specs.extend(self.build_specs(config, "restore"))
            if not skip_test:
                specs.extend(self.build_specs(config, "test"))
        if not specs:
            raise SystemExit("Validation would run no Maven commands. Clear a skip flag.")
        return _validation_metadata(specs, "maven-reactor")

    def inspect_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        return [("project_root", str(self._root(config))), ("project_type", "Maven"), ("wrapper", "yes" if (self._root(config) / "mvnw").exists() else "no")]

    def doctor_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        command = str(self._root(config) / "mvnw") if (self._root(config) / "mvnw").exists() else shutil.which("mvn")
        return [("maven", f"READY: {command}" if command else "MISSING")]


class CMakeRunner(ProjectRunner):
    kind = "cmake"
    display_name = "CMake"
    capabilities = RunnerCapabilities(restore=True, build=True, test=True, validate=True, run=False, package=True, clean=True, inspect=True, doctor=True)

    def _root(self, config: ProjectConfig) -> Path:
        return _project_root(config)

    def _options(self, config: ProjectConfig) -> dict[str, object]:
        value = config.runner_options.get("cmake", {})
        return dict(value) if isinstance(value, dict) else {}

    def _build_dir(self, config: ProjectConfig) -> Path:
        value = str(self._options(config).get("build_directory") or ".devtool/build/cmake")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self._root(config) / path
        variant = str(config.variant or self._options(config).get("configuration") or "Debug")
        if "{variant}" in str(path):
            path = Path(str(path).replace("{variant}", variant))
        return path.resolve()

    def _configure_spec(self, config: ProjectConfig) -> CommandSpec:
        root = self._root(config)
        build_dir = self._build_dir(config)
        options = self._options(config)
        argv = ["cmake", "-S", str(root), "-B", str(build_dir)]
        policy = resolve_build_policy(config, self.kind)
        native_tool_detection = policy.environment == detect_current_environment()
        generator = str(options.get("generator") or ("Ninja" if native_tool_detection and shutil.which("ninja") else "")).strip()
        if generator:
            argv.extend(["-G", generator])
        configuration = str(config.variant or options.get("configuration") or "Debug")
        argv.extend([f"-DCMAKE_BUILD_TYPE={configuration}", "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"])
        if bool(options.get("ccache", True)) and native_tool_detection and shutil.which("ccache"):
            argv.extend(["-DCMAKE_C_COMPILER_LAUNCHER=ccache", "-DCMAKE_CXX_COMPILER_LAUNCHER=ccache"])
        extra = options.get("configure_args", [])
        if isinstance(extra, str):
            argv.extend(shlex.split(extra))
        elif isinstance(extra, (list, tuple)):
            argv.extend(str(item) for item in extra)
        return CommandSpec("configure", argv, env=_optimized_env(config, self.kind), cwd=str(root), metadata=_tool_metadata(self.kind, root, build_directory=str(build_dir)))

    def build_specs(self, config: ProjectConfig, action: str) -> list[CommandSpec]:
        root = self._root(config)
        build_dir = self._build_dir(config)
        env = _optimized_env(config, self.kind)
        workers = resolve_build_policy(config, self.kind).max_workers
        explicit = {"restore": config.restore_command, "build": config.build_command, "test": config.test_command, "wine-test": config.test_command, "package": config.package_command}.get(action, "")
        if explicit:
            return [CommandSpec(action, shlex.split(explicit), env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, explicit=True))]
        configured = (build_dir / "CMakeCache.txt").exists()
        if action == "restore":
            return [self._configure_spec(config)]
        if action == "build":
            specs = [] if configured else [self._configure_spec(config)]
            argv = ["cmake", "--build", str(build_dir), "--parallel", str(workers)]
            configuration = str(config.variant or self._options(config).get("configuration") or "Debug")
            argv.extend(["--config", configuration])
            specs.append(CommandSpec("build", argv, env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, build_directory=str(build_dir))))
            return specs
        if action in {"test", "wine-test"}:
            argv = ["ctest", "--test-dir", str(build_dir), "--parallel", str(workers), "--output-on-failure"]
            return [CommandSpec("test", argv, env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, build_directory=str(build_dir)))]
        if action == "package":
            return [CommandSpec("package", ["cpack", "--config", str(build_dir / "CPackConfig.cmake")], env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, build_directory=str(build_dir)))]
        if action == "clean":
            return [CommandSpec("clean", ["cmake", "--build", str(build_dir), "--target", "clean", "--parallel", str(workers)], env=env, cwd=str(root), destructive=True, metadata=_tool_metadata(self.kind, root, build_directory=str(build_dir)))]
        raise SystemExit(f"Unsupported CMake action: {action}")

    def build_validation_specs(self, config: ProjectConfig, *, wine: bool = False, skip_restore: bool = False, skip_build: bool = False, skip_test: bool = False) -> list[CommandSpec]:
        specs: list[CommandSpec] = []
        configured = (self._build_dir(config) / "CMakeCache.txt").exists()
        if not configured and (not skip_restore or not skip_build or not skip_test):
            specs.append(self._configure_spec(config))
        if not skip_build:
            build_specs = self.build_specs(config, "build")
            specs.extend(spec for spec in build_specs if spec.name != "configure")
        if not skip_test:
            specs.extend(self.build_specs(config, "test"))
        if not specs:
            raise SystemExit("Validation would run no CMake commands. Clear a skip flag.")
        return _validation_metadata(specs, "cmake-incremental")

    def inspect_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        return [("project_root", str(self._root(config))), ("project_type", "CMake"), ("build_directory", str(self._build_dir(config))), ("configured", "yes" if (self._build_dir(config) / "CMakeCache.txt").exists() else "no")]

    def doctor_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        return [(tool, f"READY: {shutil.which(tool)}" if shutil.which(tool) else "MISSING") for tool in ("cmake", "ctest")]


class MesonRunner(ProjectRunner):
    kind = "meson"
    display_name = "Meson"
    capabilities = RunnerCapabilities(restore=True, build=True, test=True, validate=True, run=False, package=True, clean=True, inspect=True, doctor=True)

    def _root(self, config: ProjectConfig) -> Path:
        return _project_root(config)

    def _options(self, config: ProjectConfig) -> dict[str, object]:
        value = config.runner_options.get("meson", {})
        return dict(value) if isinstance(value, dict) else {}

    def _build_dir(self, config: ProjectConfig) -> Path:
        value = str(self._options(config).get("build_directory") or ".devtool/build/meson")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self._root(config) / path
        return path.resolve()

    def _configured(self, config: ProjectConfig) -> bool:
        build_dir = self._build_dir(config)
        return (build_dir / "build.ninja").exists() or (build_dir / "meson-private" / "coredata.dat").exists()

    def _setup(self, config: ProjectConfig) -> CommandSpec:
        root = self._root(config)
        build_dir = self._build_dir(config)
        argv = ["meson", "setup", str(build_dir), str(root)]
        buildtype = str(config.variant or self._options(config).get("buildtype") or "debug").lower()
        argv.extend(["--buildtype", buildtype])
        return CommandSpec("configure", argv, env=_optimized_env(config, self.kind), cwd=str(root), metadata=_tool_metadata(self.kind, root, build_directory=str(build_dir)))

    def build_specs(self, config: ProjectConfig, action: str) -> list[CommandSpec]:
        root = self._root(config)
        build_dir = self._build_dir(config)
        env = _optimized_env(config, self.kind)
        workers = resolve_build_policy(config, self.kind).max_workers
        explicit = {"restore": config.restore_command, "build": config.build_command, "test": config.test_command, "wine-test": config.test_command, "package": config.package_command}.get(action, "")
        if explicit:
            return [CommandSpec(action, shlex.split(explicit), env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, explicit=True))]
        if action == "restore":
            return [self._setup(config)]
        if action == "build":
            specs = [] if self._configured(config) else [self._setup(config)]
            specs.append(CommandSpec("build", ["meson", "compile", "-C", str(build_dir), "-j", str(workers)], env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, build_directory=str(build_dir))))
            return specs
        if action in {"test", "wine-test"}:
            return [CommandSpec("test", ["meson", "test", "-C", str(build_dir), "--num-processes", str(workers), "--print-errorlogs"], env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, build_directory=str(build_dir)))]
        if action == "package":
            return [CommandSpec("package", ["meson", "dist", "-C", str(build_dir), "--no-tests"], env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, build_directory=str(build_dir)))]
        if action == "clean":
            return [CommandSpec("clean", ["meson", "compile", "-C", str(build_dir), "--clean"], env=env, cwd=str(root), destructive=True, metadata=_tool_metadata(self.kind, root, build_directory=str(build_dir)))]
        raise SystemExit(f"Unsupported Meson action: {action}")

    def build_validation_specs(self, config: ProjectConfig, *, wine: bool = False, skip_restore: bool = False, skip_build: bool = False, skip_test: bool = False) -> list[CommandSpec]:
        specs: list[CommandSpec] = []
        if not self._configured(config) and (not skip_restore or not skip_build or not skip_test):
            specs.append(self._setup(config))
        if not skip_build:
            specs.extend(spec for spec in self.build_specs(config, "build") if spec.name != "configure")
        if not skip_test:
            specs.extend(self.build_specs(config, "test"))
        if not specs:
            raise SystemExit("Validation would run no Meson commands. Clear a skip flag.")
        return _validation_metadata(specs, "meson-incremental")

    def inspect_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        return [("project_root", str(self._root(config))), ("project_type", "Meson"), ("build_directory", str(self._build_dir(config))), ("configured", "yes" if self._configured(config) else "no")]

    def doctor_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        return [(tool, f"READY: {shutil.which(tool)}" if shutil.which(tool) else "MISSING") for tool in ("meson", "ninja")]


class MakeRunner(ProjectRunner):
    kind = "make"
    display_name = "Make"

    def _root(self, config: ProjectConfig) -> Path:
        return _project_root(config)

    def _options(self, config: ProjectConfig) -> dict[str, object]:
        value = config.runner_options.get("make", {})
        return dict(value) if isinstance(value, dict) else {}

    def _targets(self, config: ProjectConfig) -> set[str]:
        root = self._root(config)
        path = next((root / name for name in ("Makefile", "makefile", "GNUmakefile") if (root / name).is_file()), None)
        if path is None:
            return set()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return set()
        return {match.group(1) for match in re.finditer(r"^(?![.#%\t ])([A-Za-z0-9_.-]+)\s*:(?!=)", text, re.MULTILINE)}

    def capabilities_for(self, config: ProjectConfig) -> RunnerCapabilities:
        targets = self._targets(config)
        return RunnerCapabilities(
            restore=bool(config.restore_command), build=True,
            test="test" in targets or bool(config.test_command), validate=True,
            lint=any(name in targets for name in ("lint", "check")),
            run=bool(config.run_command), package=any(name in targets for name in ("package", "dist")) or bool(config.package_command),
            clean="clean" in targets, inspect=True, doctor=True,
        )

    def build_specs(self, config: ProjectConfig, action: str) -> list[CommandSpec]:
        root = self._root(config)
        env = _optimized_env(config, self.kind)
        workers = resolve_build_policy(config, self.kind).max_workers
        explicit = {"restore": config.restore_command, "build": config.build_command, "test": config.test_command, "wine-test": config.test_command, "run": config.run_command, "package": config.package_command}.get(action, "")
        if explicit:
            return [CommandSpec(action, shlex.split(explicit), env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, explicit=True))]
        targets = self._targets(config)
        target = ""
        if action == "build":
            target = str(self._options(config).get("build_target") or "")
        elif action in {"test", "wine-test"}:
            target = "test"
        elif action == "lint":
            target = "lint" if "lint" in targets else "check"
        elif action == "package":
            target = "package" if "package" in targets else "dist"
        elif action == "clean":
            target = "clean"
        else:
            raise SystemExit(f"Unsupported Make action: {action}")
        if target and target not in targets and action != "build":
            raise SystemExit(f"Makefile has no '{target}' target.")
        argv = ["make", "-j", str(workers), *( [target] if target else [])]
        return [CommandSpec(action.replace("wine-", ""), argv, env=env, cwd=str(root), destructive=action == "clean", metadata=_tool_metadata(self.kind, root, make_target=target))]

    def build_validation_specs(self, config: ProjectConfig, *, wine: bool = False, skip_restore: bool = False, skip_build: bool = False, skip_test: bool = False) -> list[CommandSpec]:
        specs: list[CommandSpec] = []
        if not skip_restore and config.restore_command:
            specs.extend(self.build_specs(config, "restore"))
        if not skip_build:
            specs.extend(self.build_specs(config, "build"))
        if not skip_test and self.supports(config, "test"):
            specs.extend(self.build_specs(config, "test"))
        if self.supports(config, "lint"):
            specs.extend(self.build_specs(config, "lint"))
        if not specs:
            raise SystemExit("Validation would run no Make commands. Clear a skip flag.")
        return _validation_metadata(specs, "make-incremental")

    def inspect_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        return [("project_root", str(self._root(config))), ("project_type", "Make"), ("targets", ", ".join(sorted(self._targets(config))) or "unknown")]

    def doctor_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        return [("make", f"READY: {shutil.which('make')}" if shutil.which("make") else "MISSING")]


class GenericCommandRunner(ProjectRunner):
    kind = "command"
    display_name = "Custom commands"

    _ACTION_FIELDS = {
        "restore": "restore_command",
        "build": "build_command",
        "test": "test_command",
        "wine-test": "test_command",
        "run": "run_command",
        "package": "package_command",
    }

    def capabilities_for(self, config: ProjectConfig) -> RunnerCapabilities:
        restore = bool(config.restore_command)
        build = bool(config.build_command)
        test = bool(config.test_command)
        return RunnerCapabilities(
            restore=restore,
            build=build,
            test=test,
            validate=restore or build or test,
            run=bool(config.run_command),
            package=bool(config.package_command),
        )

    def build_specs(self, config: ProjectConfig, action: str) -> list[CommandSpec]:
        attribute = self._ACTION_FIELDS.get(action)
        text = str(getattr(config, attribute, "") or "") if attribute else ""
        if not text:
            raise SystemExit(f"No command configured for action '{action}'")
        return [CommandSpec(
            action,
            shlex.split(text),
            env=_optimized_env(config, "command"),
            cwd=str(_project_root(config)),
            metadata={"explicit": True, "runner": "command", "project_type": "command", "progress_parser": "generic"},
        )]


class RunnerRegistry:
    """Registry mapping project kinds to command planners."""

    def __init__(self, runners: Iterable[ProjectRunner] = (), *, fallback: ProjectRunner | None = None):
        self._runners: dict[str, ProjectRunner] = {}
        self._fallback = fallback or GenericCommandRunner()
        for runner in runners:
            self.register(runner)

    def register(self, runner: ProjectRunner, *, replace: bool = False) -> None:
        key = str(runner.kind).strip().lower()
        if not key:
            raise ValueError("Runner kind cannot be empty")
        if key in self._runners and not replace:
            raise ValueError(f"Runner already registered for kind '{key}'")
        self._runners[key] = runner

    def resolve(self, config_or_kind: ProjectConfig | str) -> ProjectRunner:
        kind = config_or_kind.kind if isinstance(config_or_kind, ProjectConfig) else str(config_or_kind)
        return self._runners.get(kind.strip().lower(), self._fallback)

    def registered_kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self._runners))


DEFAULT_RUNNER_REGISTRY = RunnerRegistry(
    [
        DotnetRunner(), AndroidGradleRunner(), JvmGradleRunner(), PythonRunner(),
        NodeRunner(), RustRunner(), GoRunner(), MavenRunner(), CMakeRunner(),
        MesonRunner(), MakeRunner(),
    ],
    fallback=GenericCommandRunner(),
)


def get_project_runner(config_or_kind: ProjectConfig | str) -> ProjectRunner:
    return DEFAULT_RUNNER_REGISTRY.resolve(config_or_kind)
