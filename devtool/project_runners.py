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

from .build_optimization import optimized_build_env, optimized_validation_env, resolve_android_validation_phase_policy, resolve_android_validation_policy, resolve_build_policy
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

    def _build_resumable_validation_specs(
        self,
        config: ProjectConfig,
        *,
        skip_restore: bool,
        skip_build: bool,
        skip_test: bool,
    ) -> list[CommandSpec]:
        from .android_integration import (
            android_builder_arguments_for_tasks,
            android_project_root,
            android_validation_change_scope,
            android_validation_lint_tasks,
            configured_tasks,
            inspect_android_project,
            plan_android_command,
        )
        from .test_selection import apply_test_selection

        validation = config.runner_options.get("validation", {})
        validation = dict(validation) if isinstance(validation, dict) else {}
        phase_config = validation.get("phases", {})
        phase_config = dict(phase_config) if isinstance(phase_config, dict) else {}
        android_options = config.runner_options.get("android", {})
        android_options = dict(android_options) if isinstance(android_options, dict) else {}
        variant = str(config.variant or android_options.get("variant") or "debug").strip()
        variant_title = variant[:1].upper() + variant[1:]
        project = inspect_android_project(config)
        project_root = android_project_root(config)
        lint_tasks, lint_scope, lint_modules = android_validation_lint_tasks(config)
        change_scope = android_validation_change_scope(config)
        allowed_by_scope = {
            "full": {"preflight", "dependencies", "compile", "resources", "unit-test", "package", "artifact-check", "lint", "instrumentation", "finalize"},
            "source": {"compile", "unit-test", "package", "artifact-check", "lint", "instrumentation", "finalize"},
            "resources": {"resources", "package", "artifact-check", "lint", "finalize"},
            "tests": {"unit-test", "lint", "instrumentation", "finalize"},
            "docs": {"finalize"},
        }
        allowed_phases = allowed_by_scope.get(change_scope, allowed_by_scope["full"])

        def phase_enabled(name: str) -> bool:
            return name in allowed_phases

        def task_values(value: object) -> list[str]:
            if isinstance(value, str):
                return [item.strip() for item in value.replace(",", " ").split() if item.strip()]
            if isinstance(value, (list, tuple)):
                return [str(item).strip() for item in value if str(item).strip()]
            return []

        def phase_tasks(name: str, defaults: list[str]) -> list[str]:
            configured = task_values(phase_config.get(name) or phase_config.get(name.replace("-", "_")))
            return configured if configured else defaults

        def gradle_phase(
            phase: str,
            tasks: list[str],
            *,
            report_parsers: list[str] | None = None,
            scan_artifacts: bool = False,
            collect_artifacts: bool = False,
            verify_artifacts: bool = False,
        ) -> CommandSpec:
            unique_tasks = list(dict.fromkeys(item for item in tasks if item))
            plan_action = "build" if phase in {"compile", "resources", "package"} else "test" if phase == "unit-test" else "lint" if phase == "lint" else "restore"
            plan = plan_android_command(config, plan_action)
            policy = resolve_android_validation_phase_policy(config, phase)
            parsers = list(report_parsers or [])
            metadata = {
                "runner": self.kind,
                "project_type": self.kind,
                "gradle_tasks": unique_tasks,
                "android_builder": str(plan.builder),
                "project_root": str(project_root),
                "collect_artifacts": collect_artifacts,
                "verify_artifacts": verify_artifacts,
                "progress_parser": "gradle",
                "report_parser": parsers[0] if parsers else "",
                "report_parsers": parsers,
                "validation_components": [phase],
                "validation_phase_name": phase,
                "validation_resumable": True,
                "validation_resource_profile": f"android-{phase}",
                "validation_max_workers": policy.max_workers,
                "validation_cpu_limit": policy.cpu_limit,
                "validation_gradle_heap_mb": policy.gradle_heap_mb,
                "validation_memory_guard_mb": policy.memory_guard_mb,
                "validation_low_priority": policy.low_priority,
                "lint_scope": lint_scope if phase == "lint" else "not-applicable",
                "lint_modules": list(lint_modules) if phase == "lint" else [],
                "validation_change_scope": change_scope,
            }
            spec = CommandSpec(
                f"{self.kind}-validation-{phase}",
                [
                    *plan.argv_prefix,
                    *android_builder_arguments_for_tasks(
                        config,
                        unique_tasks,
                        scan_artifacts=scan_artifacts,
                        validation=True,
                        validation_phase=phase,
                    ),
                ],
                env=optimized_validation_env(config, self.kind),
                cwd=str(plan.project_root),
                metadata=metadata,
            )
            if phase == "unit-test":
                spec = apply_test_selection(config, spec)
            return spec

        def internal_phase(phase: str, message: str, *, artifact_check: bool = False) -> CommandSpec:
            policy = resolve_android_validation_phase_policy(config, phase)
            return CommandSpec(
                f"{self.kind}-validation-{phase}",
                [sys.executable, "-c", f"print({message!r})"],
                env=optimized_validation_env(config, self.kind),
                cwd=str(project_root),
                metadata={
                    "runner": self.kind,
                    "project_type": self.kind,
                    "project_root": str(project_root),
                    "gradle_tasks": configured_tasks(config, "build") if artifact_check else [],
                    "collect_artifacts": artifact_check,
                    "verify_artifacts": artifact_check,
                    "progress_parser": "generic",
                    "validation_components": [phase],
                    "validation_phase_name": phase,
                    "validation_resumable": True,
                    "validation_resource_profile": f"android-{phase}",
                    "validation_max_workers": policy.max_workers,
                    "validation_cpu_limit": policy.cpu_limit,
                    "validation_gradle_heap_mb": policy.gradle_heap_mb,
                    "validation_memory_guard_mb": policy.memory_guard_mb,
                    "validation_low_priority": policy.low_priority,
                    "validation_change_scope": change_scope,
                },
            )

        specs: list[CommandSpec] = []
        if not skip_restore and phase_enabled("preflight"):
            specs.append(gradle_phase("preflight", phase_tasks("preflight", ["help"])))
        if not skip_restore and phase_enabled("dependencies"):
            specs.append(gradle_phase("dependencies", phase_tasks("dependencies", ["dependencies"])))
        if not skip_build and phase_enabled("compile"):
            compile_defaults = [f"compile{variant_title}Sources"] if project.android else ["classes"]
            specs.append(gradle_phase("compile", phase_tasks("compile", compile_defaults)))
        if not skip_build and project.android and phase_enabled("resources"):
            specs.append(gradle_phase("resources", phase_tasks("resources", [f"process{variant_title}Resources"])))
        if not skip_test and phase_enabled("unit-test"):
            specs.append(gradle_phase("unit-test", phase_tasks("unit-test", configured_tasks(config, "test")), report_parsers=["gradle-junit"]))
        if not skip_build and phase_enabled("package"):
            package_tasks = phase_tasks("package", configured_tasks(config, "build"))
            specs.append(gradle_phase("package", package_tasks, scan_artifacts=True))
        if not skip_build and phase_enabled("artifact-check"):
            specs.append(internal_phase("artifact-check", "Verifying produced Android/Gradle artifacts", artifact_check=True))
        if lint_tasks and phase_enabled("lint"):
            specs.append(gradle_phase("lint", phase_tasks("lint", lint_tasks), report_parsers=["android-lint"]))

        instrumentation_policy = str(android_options.get("instrumentation") or "never").strip().lower()
        run_instrumentation = not skip_test and (
            instrumentation_policy in {"required", "always"}
            or (instrumentation_policy == "when-device-present" and (shutil.which("pm") or shutil.which("adb")))
        )
        if run_instrumentation and phase_enabled("instrumentation"):
            selection_options = dict(config.runner_options.get("test_selection", {}) or {})
            selection_options["instrumented"] = True
            original = config.runner_options.get("test_selection")
            config.runner_options["test_selection"] = selection_options
            try:
                instrumentation_specs = self.build_specs(config, "instrumentation")
            finally:
                if original is None:
                    config.runner_options.pop("test_selection", None)
                else:
                    config.runner_options["test_selection"] = original
            for spec in instrumentation_specs:
                policy = resolve_android_validation_phase_policy(config, "instrumentation")
                spec.name = f"{self.kind}-validation-instrumentation"
                spec.metadata.update({
                    "validation_components": ["instrumentation"],
                    "validation_phase_name": "instrumentation",
                    "validation_resumable": True,
                    "validation_resource_profile": "android-instrumentation",
                    "validation_max_workers": policy.max_workers,
                    "validation_cpu_limit": policy.cpu_limit,
                    "validation_gradle_heap_mb": policy.gradle_heap_mb,
                    "validation_memory_guard_mb": policy.memory_guard_mb,
                    "validation_change_scope": change_scope,
                })
                specs.append(spec)

        if phase_enabled("finalize"):
            specs.append(internal_phase("finalize", "Finalizing validation evidence and session state"))
        if not specs:
            raise SystemExit("Validation would run no Android/Gradle commands. Clear a skip flag.")
        total = len(specs)
        for index, spec in enumerate(specs, start=1):
            spec.metadata.update({
                "validation_pipeline": True,
                "validation_phase_index": index,
                "validation_phase_total": total,
                "validation_scope": "android-resumable",
                "validation_change_scope": change_scope,
            })
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
            android_validation_lint_tasks,
            configured_tasks,
            plan_android_command,
        )
        from .test_selection import apply_test_selection

        validation_options = config.runner_options.get("validation", {})
        validation_options = dict(validation_options) if isinstance(validation_options, dict) else {}
        if bool(validation_options.get("resumable", False)):
            return self._build_resumable_validation_specs(
                config,
                skip_restore=skip_restore,
                skip_build=skip_build,
                skip_test=skip_test,
            )

        # Explicit shell commands may carry action-specific setup or side
        # effects, so preserve the legacy multi-command pipeline for them.
        has_explicit_commands = any((
            not skip_restore and bool(config.restore_command),
            not skip_build and bool(config.build_command),
            not skip_test and bool(config.test_command),
        ))
        specs: list[CommandSpec] = []
        resource = resolve_android_validation_policy(config)
        lint_tasks, lint_scope, lint_modules = android_validation_lint_tasks(config)
        if has_explicit_commands:
            if not skip_restore:
                specs.extend(self.build_specs(config, "restore"))
            if not skip_build:
                specs.extend(self.build_specs(config, "build"))
            if not skip_test:
                specs.extend(self.build_specs(config, "test"))
            if lint_tasks:
                lint_plan = plan_android_command(config, "lint")
                lint_policy = resolve_android_validation_phase_policy(config, "lint")
                specs.append(CommandSpec(
                    "lint",
                    [
                        *lint_plan.argv_prefix,
                        *android_builder_arguments_for_tasks(
                            config,
                            lint_tasks,
                            scan_artifacts=False,
                            validation=True,
                            validation_phase="lint",
                        ),
                    ],
                    env=optimized_validation_env(config, self.kind),
                    cwd=str(lint_plan.project_root),
                    metadata={
                        "runner": self.kind,
                        "project_type": self.kind,
                        "gradle_tasks": list(lint_tasks),
                        "android_builder": str(lint_plan.builder),
                        "project_root": str(lint_plan.project_root),
                        "progress_parser": "gradle",
                        "report_parser": "android-lint",
                        "report_parsers": ["android-lint"],
                        "validation_components": ["lint"],
                        "validation_phase_name": "lint",
                        "lint_scope": lint_scope,
                        "lint_modules": list(lint_modules),
                        "validation_resource_profile": "android-lint",
                        "validation_max_workers": lint_policy.max_workers,
                        "validation_cpu_limit": lint_policy.cpu_limit,
                        "validation_gradle_heap_mb": lint_policy.gradle_heap_mb,
                        "validation_memory_guard_mb": lint_policy.memory_guard_mb,
                        "validation_low_priority": lint_policy.low_priority,
                    },
                ))
            if resource.enabled:
                validation_env = optimized_validation_env(config, self.kind)
                for spec in specs:
                    spec.env = dict(validation_env)
                    phase_name = str(spec.metadata.get("validation_phase_name") or "").strip()
                    phase_policy = (
                        resolve_android_validation_phase_policy(config, phase_name)
                        if phase_name
                        else resource
                    )
                    spec.metadata.update({
                        "validation_resource_profile": f"android-{phase_name}" if phase_name else "android-safe",
                        "validation_max_workers": phase_policy.max_workers,
                        "validation_cpu_limit": phase_policy.cpu_limit,
                        "validation_gradle_heap_mb": phase_policy.gradle_heap_mb,
                        "validation_memory_guard_mb": phase_policy.memory_guard_mb,
                        "validation_low_priority": phase_policy.low_priority,
                    })
        else:
            groups: list[tuple[str, list[str], list[str], str]] = []
            if not skip_restore:
                restore_tasks = configured_tasks(config, "restore")
                if restore_tasks and restore_tasks != ["help"]:
                    groups.append(("restore", restore_tasks, ["restore"], "dependencies"))
            if not skip_build:
                groups.append(("build", configured_tasks(config, "build"), ["build"], "package"))
            if not skip_test:
                groups.append(("test", configured_tasks(config, "test"), ["test"], "unit-test"))
            if lint_tasks:
                groups.append(("lint", lint_tasks, ["lint"], "lint"))

            if resource.split_gradle:
                seen_task_ids: set[str] = set()
                filtered_reversed: list[tuple[str, list[str], list[str], str]] = []
                for action, group_tasks, group_components, validation_phase in reversed(groups):
                    kept_reversed: list[str] = []
                    for task in reversed(group_tasks):
                        task_id = task.lstrip(":")
                        if task_id in seen_task_ids:
                            continue
                        seen_task_ids.add(task_id)
                        kept_reversed.append(task)
                    kept = list(reversed(kept_reversed))
                    if kept:
                        filtered_reversed.append((action, kept, group_components, validation_phase))
                groups = list(reversed(filtered_reversed))
            else:
                tasks: list[str] = []
                components: list[str] = []
                for _, group_tasks, group_components, _ in groups:
                    tasks.extend(group_tasks)
                    components.extend(group_components)
                groups = [("validation", tasks, components, "")]

            for action, tasks, components, validation_phase in groups:
                unique_tasks = list(dict.fromkeys(task for task in tasks if task))
                if not unique_tasks:
                    continue
                plan_action = "build" if "build" in components else components[0]
                plan = plan_android_command(config, plan_action)
                phase_policy = (
                    resolve_android_validation_phase_policy(config, validation_phase)
                    if validation_phase
                    else resource
                )
                report_parsers: list[str] = []
                if "test" in components:
                    report_parsers.append("gradle-junit")
                if "lint" in components:
                    report_parsers.append("android-lint")
                validation_components = list(components)
                if validation_phase:
                    validation_components = (
                        ["package", "artifact-check"]
                        if validation_phase == "package" and "build" in components
                        else [validation_phase]
                    )
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
                    "validation_components": validation_components,
                    "validation_phase_name": validation_phase or "validation",
                    "contains_tests": "test" in components,
                    "coalesced_gradle_validation": not resource.split_gradle,
                    "validation_resource_profile": (
                        f"android-{validation_phase}"
                        if validation_phase and phase_policy.enabled
                        else "android-safe" if phase_policy.enabled else "standard"
                    ),
                    "validation_max_workers": phase_policy.max_workers,
                    "validation_cpu_limit": phase_policy.cpu_limit,
                    "validation_gradle_heap_mb": phase_policy.gradle_heap_mb,
                    "validation_memory_guard_mb": phase_policy.memory_guard_mb,
                    "validation_low_priority": phase_policy.low_priority,
                    "lint_scope": lint_scope if "lint" in components else "not-applicable",
                    "lint_modules": list(lint_modules) if "lint" in components else [],
                }
                name = (
                    ("android-validation" if self.kind == "android" else "gradle-validation")
                    if action == "validation"
                    else f"{self.kind}-validation-{action}"
                )
                spec = CommandSpec(
                    name,
                    [
                        *plan.argv_prefix,
                        *android_builder_arguments_for_tasks(
                            config,
                            unique_tasks,
                            scan_artifacts="build" in components,
                            validation=True,
                            validation_phase=validation_phase,
                        ),
                    ],
                    env=optimized_validation_env(config, self.kind),
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
            pipeline_scope = (
                "android-affected"
                if lint_scope in {"affected-modules", "skipped-non-code-changes"}
                else "android-full"
            )
            metadata.update({
                "validation_pipeline": True,
                "validation_phase_index": index,
                "validation_phase_total": total,
                "validation_scope": pipeline_scope,
                "lint_scope": metadata.get("lint_scope", lint_scope),
                "lint_modules": metadata.get("lint_modules", list(lint_modules)),
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

    def _list_option(self, config: ProjectConfig, name: str) -> list[str]:
        value = self._options(config).get(name)
        if isinstance(value, str):
            return [item.strip() for item in re.split(r"[,\s]+", value) if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    def _info(self, config: ProjectConfig):
        from .python_support import DEPENDENCY_FILES, PythonProjectError, inspect_python_project
        options = self._options(config)
        root = self._root(config)
        signatures: list[tuple[str, int, int]] = []
        for name in DEPENDENCY_FILES:
            path = root / name
            try:
                stat = path.stat()
            except OSError:
                continue
            signatures.append((name, stat.st_mtime_ns, stat.st_size))
        key = (
            str(root), str(options.get("manager") or "auto"),
            str(options.get("test_framework") or "auto"), tuple(signatures),
        )
        cache = getattr(self, "_python_info_cache", None)
        if isinstance(cache, dict) and key in cache:
            return cache[key]
        try:
            info = inspect_python_project(root, key[1], key[2])
        except PythonProjectError as exc:
            raise SystemExit(str(exc)) from exc
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_python_info_cache", cache)
        cache.clear()
        cache[key] = info
        return info

    def _environment_path(self, config: ProjectConfig) -> str:
        options = self._options(config)
        value = str(options.get("environment") or "").strip()
        if not value:
            name = config.selected_target or "python"
            value = f".devtool/venvs/{name}"
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self._root(config) / path
        return str(path.resolve())

    def _plan(self, config: ProjectConfig):
        from .python_support import resolve_environment
        info = self._info(config)
        environment = self._environment_path(config) if info.manager == "venv" else str(self._options(config).get("environment") or "")
        key = (
            str(info.root), info.manager, config.python_executable, environment,
            tuple(self._list_option(config, "groups")), tuple(self._list_option(config, "extras")),
            config.execution_environment, info.requires_python, info.dependency_files,
        )
        cache = getattr(self, "_python_plan_cache", None)
        if isinstance(cache, dict) and key in cache:
            return cache[key]
        plan = resolve_environment(
            info,
            configured_python=config.python_executable,
            environment=environment,
            groups=key[4],
            extras=key[5],
            execution_environment=config.execution_environment,
        )
        if not plan.compatible:
            raise SystemExit(plan.compatibility_detail)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_python_plan_cache", cache)
        cache.clear()
        cache[key] = plan
        return plan

    def _env(self, config: ProjectConfig, *, isolated: bool = False) -> dict[str, str]:
        from .python_support import python_cache_env
        env = _optimized_env(config, self.kind)
        env.update(python_cache_env(self._root(config), config.execution_environment))
        options = self._options(config)
        if isolated or bool(options.get("isolation", False)):
            home = self._root(config) / ".devtool" / "tmp" / "python-home"
            tmp = self._root(config) / ".devtool" / "tmp" / "python"
            home.mkdir(parents=True, exist_ok=True)
            tmp.mkdir(parents=True, exist_ok=True)
            for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV"):
                env.pop(key, None)
            env.update({
                "HOME": str(home),
                "TMPDIR": str(tmp),
                "TZ": str(options.get("timezone") or "UTC"),
                "LC_ALL": str(options.get("locale") or "C.UTF-8"),
                "LANG": str(options.get("locale") or "C.UTF-8"),
                "PYTHONUTF8": "1",
                "PYTHONHASHSEED": str(options.get("hash_seed") or "random"),
                "DEVTOOL_NETWORK_DISABLED": "1" if bool(options.get("disable_network", False)) else "0",
            })
        return env

    def _network_wrap(self, config: ProjectConfig, argv: list[str]) -> list[str]:
        if not bool(self._options(config).get("disable_network", False)):
            return argv
        unshare = shutil.which("unshare")
        if not unshare:
            raise SystemExit("Python network isolation was requested, but 'unshare' is unavailable.")
        return [unshare, "--net", "--", *argv]

    def _module(self, config: ProjectConfig, module: str, *args: str) -> list[str]:
        from .python_support import python_module_argv
        return python_module_argv(self._plan(config), module, *args)

    def _script(self, config: ProjectConfig, script: str, *args: str) -> list[str]:
        from .python_support import python_script_argv
        return python_script_argv(self._plan(config), script, *args)

    def _executable(self, config: ProjectConfig, executable: str, *args: str) -> list[str]:
        from .python_support import python_executable_argv
        return python_executable_argv(self._plan(config), executable, *args)

    def inspect_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        info = self._info(config)
        plan = self._plan(config)
        return [
            ("project_root", str(info.root)),
            ("project_type", "Python"),
            ("package", info.package_name),
            ("environment_manager", info.manager),
            ("environment", plan.environment_root or "manager-owned"),
            ("environment_fingerprint", plan.fingerprint[:16]),
            ("lock_file", info.lock_file or "none"),
            ("requires_python", info.requires_python or "unspecified"),
            ("interpreter", plan.python),
            ("interpreter_compatible", "yes" if plan.compatible else "no"),
            ("test_framework", info.framework),
            ("build_backend", info.build_backend),
            ("source_roots", ", ".join(info.source_roots) or "."),
            ("import_packages", ", ".join(info.import_packages) or "none detected"),
            ("console_scripts", ", ".join(info.console_script_map) or "none"),
            ("workspace_members", ", ".join(info.workspace_members) or "none"),
            ("native_backend", info.native_backend or "none"),
            ("generated_sources", ", ".join(info.generated_tools) or "none detected"),
            ("service_frameworks", ", ".join(info.service_frameworks) or "none detected"),
            ("database_tools", ", ".join(info.database_tools) or "none detected"),
            ("notebooks", str(len(info.notebooks))),
        ]

    def doctor_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        info = self._info(config)
        plan = self._plan(config)
        rows = [("python_project", "READY" if info.root.is_dir() else "MISSING")]
        tools = [plan.python, info.manager if info.manager != "venv" else ""]
        native_tools = {
            "maturin": ["cargo", "rustc"],
            "setuptools-rust": ["cargo", "rustc"],
            "scikit-build-core": ["cmake", "ninja"],
            "meson-python": ["meson", "ninja"],
            "cython": ["cc"],
            "pybind11": ["c++"],
            "cffi": ["cc"],
        }
        tools.extend(native_tools.get(info.native_backend, []))
        for tool in dict.fromkeys(value for value in tools if value):
            path = Path(tool)
            executable = str(path) if path.is_absolute() and path.exists() else shutil.which(tool)
            rows.append((tool, f"READY: {executable}" if executable else "MISSING"))
        rows.append(("requires_python", plan.compatibility_detail))
        return rows

    def _validation_scope(self, config: ProjectConfig):
        from .python_support import python_validation_scope
        validation = config.runner_options.get("validation", {})
        changed = validation.get("changed_paths", []) if isinstance(validation, dict) else []
        values = [str(item) for item in changed] if isinstance(changed, (list, tuple, set)) else []
        return python_validation_scope(config.repo_root, values, self._info(config))

    def _pytest_spec(self, config: ProjectConfig, *, targets: Iterable[str] = ()) -> CommandSpec:
        from .test_selection import apply_test_selection
        root = self._root(config)
        options = self._options(config)
        report = root / ".devtool" / "reports" / "pytest.xml"
        coverage = options.get("coverage", {})
        coverage = dict(coverage) if isinstance(coverage, dict) else {}
        argv = self._module(config, "pytest", f"--junitxml={report}")
        target_values = list(dict.fromkeys(str(item) for item in targets if str(item)))
        argv.extend(target_values)
        if coverage:
            source = str(coverage.get("source") or ".")
            argv.extend([f"--cov={source}", "--cov-report=term-missing", f"--cov-report=xml:{root / '.devtool' / 'reports' / 'coverage.xml'}"])
            minimum = coverage.get("minimum_total")
            if minimum is not None:
                argv.append(f"--cov-fail-under={minimum}")
        retries = int(options.get("flaky_retries") or 0)
        if retries > 0:
            argv.extend(["--reruns", str(retries)])
        argv = self._network_wrap(config, argv)
        spec = CommandSpec(
            "test",
            argv,
            env=self._env(config, isolated=True),
            cwd=str(root),
            metadata={
                "runner": self.kind,
                "project_type": self.kind,
                "project_root": str(root),
                "report_parser": "pytest-junit",
                "report_parsers": ["pytest-junit", *( ["python-coverage"] if coverage else [])],
                "test_report": str(report),
                "coverage_report": str(root / ".devtool" / "reports" / "coverage.xml"),
                "test_framework": "pytest",
                "affected_test_targets": target_values,
            },
        )
        return apply_test_selection(config, spec)

    def _unittest_spec(self, config: ProjectConfig, *, targets: Iterable[str] = ()) -> CommandSpec:
        values = list(dict.fromkeys(str(item) for item in targets if str(item)))
        if values:
            modules = [value[:-3].replace("/", ".") if value.endswith(".py") else value for value in values]
            argv = self._module(config, "unittest", *modules)
        else:
            argv = self._module(config, "unittest", "discover")
        argv = self._network_wrap(config, argv)
        return CommandSpec(
            "test", argv, env=self._env(config, isolated=True), cwd=str(self._root(config)),
            metadata=_tool_metadata(self.kind, self._root(config), test_framework="unittest"),
        )

    def _framework_test_specs(self, config: ProjectConfig, *, targets: Iterable[str] = ()) -> list[CommandSpec]:
        info = self._info(config)
        if info.framework == "pytest":
            return [self._pytest_spec(config, targets=targets)]
        if info.framework == "unittest":
            return [self._unittest_spec(config, targets=targets)]
        if info.framework == "tox":
            selection = config.runner_options.get("test_selection", {})
            selection = dict(selection) if isinstance(selection, dict) else {}
            environments = selection.get("tasks") or selection.get("task") or self._list_option(config, "tox_environments")
            values = [environments] if isinstance(environments, str) else list(environments or [])
            argv = self._executable(config, "tox")
            if values:
                argv.extend(["-e", ",".join(str(item) for item in values)])
            argv = self._network_wrap(config, argv)
            return [CommandSpec("test", argv, env=self._env(config, isolated=True), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), test_framework="tox"))]
        if info.framework == "nox":
            selection = config.runner_options.get("test_selection", {})
            selection = dict(selection) if isinstance(selection, dict) else {}
            sessions = selection.get("tasks") or selection.get("task") or self._list_option(config, "nox_sessions")
            values = [sessions] if isinstance(sessions, str) else list(sessions or [])
            argv = self._executable(config, "nox")
            if values:
                argv.extend(["-s", *[str(item) for item in values]])
            if bool(self._options(config).get("nox_reuse", True)):
                argv.append("--reuse-existing-virtualenvs")
            argv = self._network_wrap(config, argv)
            return [CommandSpec("test", argv, env=self._env(config, isolated=True), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), test_framework="nox"))]
        raise SystemExit(f"Unsupported Python test framework: {info.framework}")

    def _lint_tools(self, config: ProjectConfig) -> list[str]:
        configured = self._options(config).get("lint")
        if isinstance(configured, str):
            return [item.strip() for item in configured.replace(",", " ").split() if item.strip()]
        if isinstance(configured, (list, tuple)):
            return [str(item).strip() for item in configured if str(item).strip()]
        try:
            data = (self._root(config) / "pyproject.toml").read_text(encoding="utf-8")
        except OSError:
            data = ""
        return [
            name for name, marker in (
                ("ruff", "[tool.ruff"), ("mypy", "[tool.mypy"),
                ("black", "[tool.black"), ("isort", "[tool.isort"),
                ("pylint", "[tool.pylint"), ("pyright", "[tool.pyright"),
                ("bandit", "[tool.bandit"),
            ) if marker in data
        ]

    def _lint_specs(self, config: ProjectConfig, *, targets: Iterable[str] = ()) -> list[CommandSpec]:
        root = self._root(config)
        target_values = list(dict.fromkeys(str(item) for item in targets if str(item))) or ["."]
        plan = self._plan(config)
        specs: list[CommandSpec] = []
        for configured_tool in self._lint_tools(config):
            tool, *configured_args = shlex.split(configured_tool)
            if tool == "ruff":
                tool_args = ["check", *configured_args, *target_values]
            elif tool == "ruff-format":
                tool, tool_args = "ruff", ["format", "--check", *configured_args, *target_values]
            elif tool == "black":
                tool_args = ["--check", *configured_args, *target_values]
            elif tool == "isort":
                tool_args = ["--check-only", *configured_args, *target_values]
            elif tool == "bandit":
                tool_args = ["-r", *configured_args, *target_values]
            else:
                tool_args = [*configured_args, *target_values]
            helper = [
                sys.executable, "-m", "devtool.python_support", "run-tool",
                "--manager", self._info(config).manager,
                "--framework", self._info(config).framework,
                "--python", config.python_executable,
                "--environment", plan.environment_root,
                str(root), tool, *tool_args,
            ]
            specs.append(CommandSpec(
                f"lint:{configured_tool}", helper, env=self._env(config), cwd=str(root),
                metadata=_tool_metadata(
                    self.kind, root, lint_tool=tool, report_parser="python-tool",
                    affected_lint_targets=target_values,
                ),
            ))
        return specs

    def _restore_specs(self, config: ProjectConfig) -> list[CommandSpec]:
        from .python_support import build_sync_command
        if config.restore_command:
            return [CommandSpec("restore", shlex.split(config.restore_command), env=self._env(config), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), explicit=True))]
        info = self._info(config)
        plan = self._plan(config)
        options = self._options(config)
        groups = self._list_option(config, "groups")
        extras = self._list_option(config, "extras")
        commands = build_sync_command(
            info, plan,
            groups=groups,
            extras=extras,
            locked=bool(options.get("locked", True)),
            offline=bool(options.get("offline", False)),
            install_project=bool(options.get("install_project", True)),
            constraints=str(options.get("constraints") or ""),
        )
        specs: list[CommandSpec] = []
        for command in commands:
            name = "venv" if len(command) >= 3 and command[1:3] == ["-m", "venv"] else "restore"
            specs.append(CommandSpec(
                name, command, env=self._env(config), cwd=str(self._root(config)),
                metadata=_tool_metadata(self.kind, self._root(config), manager=info.manager),
            ))
        validator_dependencies = self._list_option(config, "validator_dependencies")
        if validator_dependencies and info.manager == "venv":
            specs.append(CommandSpec("validator-dependencies", self._module(config, "pip", "install", *validator_dependencies), env=self._env(config), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config))))
        snapshot = [
            sys.executable, "-m", "devtool.python_support", "environment-snapshot",
            str(self._root(config)), "--manager", info.manager, "--framework", info.framework,
            "--python", config.python_executable, "--environment", plan.environment_root,
            "--execution-environment", config.execution_environment,
        ]
        for group in groups:
            snapshot.extend(["--group", group])
        for extra in extras:
            snapshot.extend(["--extra", extra])
        specs.append(CommandSpec("python-environment", snapshot, env=self._env(config), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), environment_fingerprint=plan.fingerprint)))
        return specs

    def _support_command_specs(self, config: ProjectConfig, option: str, name: str) -> list[CommandSpec]:
        value = self._options(config).get(option)
        commands = [value] if isinstance(value, str) else list(value or []) if isinstance(value, (list, tuple)) else []
        return [CommandSpec(f"{name}:{index}", shlex.split(str(command)), env=self._env(config, isolated=True), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), validation_component=name)) for index, command in enumerate(commands, 1) if str(command).strip()]

    def _precommit_spec(self, config: ProjectConfig, targets: Iterable[str]) -> CommandSpec | None:
        options = self._options(config)
        enabled = options.get("pre_commit", "auto")
        if enabled is False or (enabled == "auto" and not (self._root(config) / ".pre-commit-config.yaml").is_file()):
            return None
        values = list(dict.fromkeys(str(item) for item in targets if str(item)))
        argv = self._executable(config, "pre-commit", "run")
        if values:
            argv.extend(["--files", *values])
        else:
            argv.append("--all-files")
        return CommandSpec("pre-commit", argv, env=self._env(config), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), validation_component="pre-commit"))

    def _import_isolation_specs(self, config: ProjectConfig) -> list[CommandSpec]:
        if not bool(self._options(config).get("source_import_isolation", False)):
            return []
        plan = self._plan(config)
        specs: list[CommandSpec] = []
        for module in self._info(config).import_packages:
            command = [plan.python, "-I", "-c", f"import {module}"] if plan.manager == "venv" else [*plan.run_prefix, "python", "-I", "-c", f"import {module}"]
            specs.append(CommandSpec(f"import:{module}", command, env=self._env(config, isolated=True), cwd=str(self._root(config).parent), metadata=_tool_metadata(self.kind, self._root(config), validation_component="import-isolation", import_module=module)))
        return specs

    def _cli_contract_specs(self, config: ProjectConfig) -> list[CommandSpec]:
        if not bool(self._options(config).get("cli_contract", False)):
            return []
        specs: list[CommandSpec] = []
        for script in self._info(config).console_script_map:
            for flag in ("--help", "--version"):
                specs.append(CommandSpec(f"cli:{script}:{flag[2:]}", self._executable(config, script, flag), env=self._env(config, isolated=True), cwd=str(self._root(config).parent), metadata=_tool_metadata(self.kind, self._root(config), validation_component="cli-contract", console_script=script)))
        return specs

    def _source_audit_spec(self, config: ProjectConfig) -> CommandSpec:
        info = self._info(config)
        argv = [
            sys.executable, "-m", "devtool.python_support", "source-audit",
            str(self._root(config)), "--manager", info.manager, "--framework", info.framework,
        ]
        return CommandSpec(
            "python-source-audit", argv, env=self._env(config), cwd=str(self._root(config)),
            metadata=_tool_metadata(self.kind, self._root(config), report_parser="python-audit", validation_component="source-audit"),
        )

    def _diff_coverage_spec(self, config: ProjectConfig) -> CommandSpec | None:
        coverage = self._options(config).get("coverage", {})
        coverage = dict(coverage) if isinstance(coverage, dict) else {}
        minimum = float(coverage.get("minimum_changed_lines") or 0)
        if minimum <= 0:
            return None
        selection = config.runner_options.get("test_selection", {})
        selection = dict(selection) if isinstance(selection, dict) else {}
        base = str(selection.get("changed_from") or "HEAD")
        report = self._root(config) / ".devtool" / "reports" / "coverage.xml"
        argv = [
            sys.executable, "-m", "devtool.python_support", "diff-coverage",
            str(self._root(config)), str(report), "--base", base, "--minimum", str(minimum),
        ]
        return CommandSpec(
            "python-diff-coverage", argv, env=self._env(config), cwd=str(self._root(config)),
            metadata=_tool_metadata(self.kind, self._root(config), validation_component="diff-coverage"),
        )

    def _sbom_spec(self, config: ProjectConfig) -> CommandSpec | None:
        options = self._options(config)
        security = options.get("security", {})
        security = dict(security) if isinstance(security, dict) else {}
        if not bool(options.get("sbom", False) or security.get("sbom", False)):
            return None
        info = self._info(config)
        plan = self._plan(config)
        argv = [
            sys.executable, "-m", "devtool.python_support", "sbom",
            str(self._root(config)), "--manager", info.manager, "--framework", info.framework,
            "--python", config.python_executable, "--environment", plan.environment_root,
        ]
        return CommandSpec("python-sbom", argv, env=self._env(config), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), validation_component="sbom"))

    def _reproducible_spec(self, config: ProjectConfig) -> CommandSpec | None:
        package = self._options(config).get("package", {})
        package = dict(package) if isinstance(package, dict) else {}
        if not bool(package.get("reproducible", False)):
            return None
        info = self._info(config)
        plan = self._plan(config)
        argv = [
            sys.executable, "-m", "devtool.python_support", "reproducible-build",
            str(self._root(config)), "--manager", info.manager, "--framework", info.framework,
            "--python", config.python_executable, "--environment", plan.environment_root,
        ]
        return CommandSpec("python-reproducible-build", argv, env=self._env(config), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), validation_component="reproducible-build"))

    def _notebook_spec(self, config: ProjectConfig) -> CommandSpec | None:
        info = self._info(config)
        notebook_options = self._options(config).get("notebooks", {})
        notebook_options = dict(notebook_options) if isinstance(notebook_options, dict) else {}
        if not info.notebooks or notebook_options.get("validate", False) is False:
            return None
        plan = self._plan(config)
        argv = [
            sys.executable, "-m", "devtool.python_support", "notebook-check",
            str(self._root(config)), "--manager", info.manager, "--framework", info.framework,
            "--python", config.python_executable, "--environment", plan.environment_root,
        ]
        for notebook in info.notebooks:
            argv.extend(["--notebook", notebook])
        if bool(notebook_options.get("execute", False)):
            argv.append("--execute")
        return CommandSpec("python-notebooks", argv, env=self._env(config, isolated=True), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), validation_component="notebooks"))

    def _service_lifecycle_spec(self, config: ProjectConfig) -> CommandSpec | None:
        service = self._options(config).get("service", {})
        service = dict(service) if isinstance(service, dict) else {}
        start = str(service.get("start") or "").strip()
        if not start or service.get("enabled", True) is False:
            return None
        argv = [
            sys.executable, "-m", "devtool.python_support", "service-check",
            str(self._root(config)), "--start", start,
            "--timeout", str(int(service.get("timeout") or 60)),
        ]
        for key, flag in (("url", "--url"), ("host", "--host"), ("check", "--check")):
            value = str(service.get(key) or "").strip()
            if value:
                argv.extend([flag, value])
        if int(service.get("port") or 0) > 0:
            argv.extend(["--port", str(int(service["port"]))])
        return CommandSpec("python-service-lifecycle", argv, env=self._env(config, isolated=True), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), validation_component="service-lifecycle"))

    def _database_specs(self, config: ProjectConfig) -> list[CommandSpec]:
        database = self._options(config).get("database", {})
        database = dict(database) if isinstance(database, dict) else {}
        enabled = database.get("enabled", "auto")
        if enabled is False:
            return []
        commands = database.get("commands")
        if isinstance(commands, (list, tuple)):
            return [CommandSpec(f"database:{index}", shlex.split(str(command)), env=self._env(config, isolated=True), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), validation_component="database")) for index, command in enumerate(commands, 1) if str(command).strip()]
        specs: list[CommandSpec] = []
        root = self._root(config)
        if (root / "alembic.ini").is_file():
            specs.append(CommandSpec("database:alembic-check", self._executable(config, "alembic", "check"), env=self._env(config, isolated=True), cwd=str(root), metadata=_tool_metadata(self.kind, root, validation_component="database")))
        if (root / "manage.py").is_file():
            specs.append(CommandSpec("database:django-migrations", self._script(config, str(root / "manage.py"), "makemigrations", "--check", "--dry-run"), env=self._env(config, isolated=True), cwd=str(root), metadata=_tool_metadata(self.kind, root, validation_component="database")))
        return specs

    def _security_specs(self, config: ProjectConfig) -> list[CommandSpec]:
        options = self._options(config)
        security = options.get("security", {})
        if security is True:
            tools = ["pip_audit", "bandit"]
        elif isinstance(security, dict) and bool(security.get("enabled", False)):
            tools = [str(value) for value in security.get("tools", ["pip_audit", "bandit"])]
        else:
            return []
        specs: list[CommandSpec] = []
        info = self._info(config)
        plan = self._plan(config)
        for tool in tools:
            args = ["-r", "."] if tool == "bandit" else []
            argv = [
                sys.executable, "-m", "devtool.python_support", "run-tool",
                "--manager", info.manager, "--framework", info.framework,
                "--python", config.python_executable, "--environment", plan.environment_root,
                str(self._root(config)), tool, *args,
            ]
            specs.append(CommandSpec(f"security:{tool}", argv, env=self._env(config), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), report_parser="python-tool", validation_component="security")))
        return specs

    def _matrix_specs(self, config: ProjectConfig) -> list[CommandSpec]:
        options = self._options(config)
        raw = options.get("matrix")
        values = list(raw) if isinstance(raw, (list, tuple)) else []
        if not values or str(options.get("matrix_mode") or "active").lower() not in {"full", "all"}:
            return []
        info = self._info(config)
        if info.framework in {"tox", "nox"}:
            return []
        specs: list[CommandSpec] = []
        for version in values:
            version_text = str(version).removeprefix("python")
            if info.manager == "uv" and shutil.which("uv"):
                argv = ["uv", "run", "--python", version_text, "python", "-m", "pytest"]
            else:
                executable = shutil.which(f"python{version_text}")
                if not executable:
                    specs.append(CommandSpec(
                        f"test:python-{version_text}:skipped",
                        [sys.executable, "-c", f"print('Python {version_text} is unavailable; matrix entry skipped')"],
                        env=self._env(config, isolated=True), cwd=str(self._root(config)),
                        metadata=_tool_metadata(self.kind, self._root(config), validation_component="python-matrix", python_version=version_text, skipped=True),
                    ))
                    continue
                argv = [executable, "-m", info.framework if info.framework in {"pytest", "unittest"} else "pytest"]
                if info.framework == "unittest":
                    argv.append("discover")
            argv = self._network_wrap(config, argv)
            specs.append(CommandSpec(f"test:python-{version_text}", argv, env=self._env(config, isolated=True), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), validation_component="python-matrix", python_version=version_text)))
        return specs

    def build_specs(self, config: ProjectConfig, action: str) -> list[CommandSpec]:
        root = self._root(config)
        info = self._info(config)
        options = self._options(config)
        if action == "restore":
            return self._restore_specs(config)
        if action == "build":
            if config.build_command:
                return [CommandSpec("build", shlex.split(config.build_command), env=self._env(config), cwd=str(root), metadata=_tool_metadata(self.kind, root, explicit=True))]
            outdir = root / "dist"
            specs: list[CommandSpec] = []
            if bool(options.get("fresh_dist", True)):
                specs.append(CommandSpec(
                    "prepare-dist",
                    [sys.executable, "-m", "devtool.python_support", "prepare-dist", str(root)],
                    env=self._env(config), cwd=str(root),
                    metadata=_tool_metadata(self.kind, root, validation_component="package-preparation"),
                ))
            argv = self._module(config, "build", "--outdir", str(outdir))
            specs.append(CommandSpec("build", argv, env=self._env(config), cwd=str(root), metadata=_tool_metadata(self.kind, root, collect_artifacts=True, verify_artifacts=True)))
            return specs
        if action in {"test", "wine-test"}:
            if config.test_command:
                custom_argv = self._network_wrap(config, shlex.split(config.test_command))
                spec = CommandSpec("test", custom_argv, env=self._env(config, isolated=True), cwd=str(root), metadata=_tool_metadata(self.kind, root, explicit=True, test_framework=str(options.get("test_adapter") or "custom")))
                if str(options.get("test_adapter") or "").lower() == "pytest":
                    from .test_selection import apply_test_selection
                    spec = apply_test_selection(config, spec)
                return [spec]
            return self._framework_test_specs(config)
        if action == "lint":
            specs = self._lint_specs(config)
            if not specs:
                raise SystemExit("No Python lint tools are configured or declared in pyproject.toml.")
            return specs
        if action == "package-check":
            report = root / ".devtool" / "reports" / "python-packages.json"
            package = options.get("package", {})
            package = dict(package) if isinstance(package, dict) else {}
            argv = [sys.executable, "-m", "devtool.python_support", "package-check", str(root), "--report", str(report)]
            flags = {
                "import_smoke": ("--import-smoke", True),
                "install_dependencies": ("--install-dependencies", False),
                "rebuild_sdist": ("--rebuild-sdist", True),
                "verify_cli": ("--verify-cli", True),
                "reproducible": ("--reproducible", False),
                "offline": ("--offline", False),
            }
            for key, (flag, default) in flags.items():
                if bool(package.get(key, options.get(key, default))):
                    argv.append(flag)
            if package.get("verify_all_wheels", True) is False:
                argv.append("--first-wheel-only")
            for module in package.get("import_modules", []) if isinstance(package.get("import_modules"), list) else []:
                argv.extend(["--import-module", str(module)])
            for script in package.get("console_scripts", []) if isinstance(package.get("console_scripts"), list) else []:
                argv.extend(["--console-script", str(script)])
            return [CommandSpec("package-check", argv, env=self._env(config), cwd=str(root), metadata=_tool_metadata(self.kind, root, report_parser="python-package", package_report=str(report)))]
        if action == "clean":
            categories = self._list_option(config, "clean_categories") or ["artifacts", "caches"]
            argv = [sys.executable, "-m", "devtool.python_support", "clean", str(root)]
            for category in categories:
                argv.extend(["--category", category])
            return [CommandSpec("clean", argv, env=self._env(config), cwd=str(root), metadata=_tool_metadata(self.kind, root))]
        if action == "run":
            if config.run_command:
                return [CommandSpec("run", shlex.split(config.run_command), env=self._env(config), cwd=str(root), metadata=_tool_metadata(self.kind, root, explicit=True))]
            if config.run_module:
                return [CommandSpec("run", self._module(config, config.run_module), env=self._env(config), cwd=str(root), metadata=_tool_metadata(self.kind, root))]
            if config.run_target:
                return [CommandSpec("run", self._script(config, config.resolve_repo_relative(config.run_target)), env=self._env(config), cwd=str(root), metadata=_tool_metadata(self.kind, root))]
            raise SystemExit("No Python run target configured. Set run_command, run_module, or run_target.")
        if action == "package":
            if config.package_command:
                return [CommandSpec("package", shlex.split(config.package_command), env=self._env(config), cwd=str(root), metadata=_tool_metadata(self.kind, root, explicit=True))]
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
        scope = self._validation_scope(config)
        specs: list[CommandSpec] = []
        if scope.scope in {"python-docs-only", "python-unaffected", "python-non-code"}:
            message = "; ".join(scope.reasons) or scope.scope
            specs.append(CommandSpec("python-validation-scope", [sys.executable, "-c", f"print({message!r})"], env=self._env(config), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), validation_component="scope")))
            return _validation_metadata(specs, scope.scope)

        if not skip_restore:
            specs.extend(self._restore_specs(config))
        specs.append(self._source_audit_spec(config))
        specs.extend(self._support_command_specs(config, "generated_commands", "generated"))
        if not skip_test:
            test_targets = () if scope.full_tests else scope.test_targets
            specs.extend(self._framework_test_specs(config, targets=test_targets))
            diff_coverage = self._diff_coverage_spec(config)
            if diff_coverage is not None:
                specs.append(diff_coverage)
            specs.extend(self._matrix_specs(config))
        lint_targets = () if scope.full_lint else scope.lint_targets
        specs.extend(self._lint_specs(config, targets=lint_targets))
        precommit = self._precommit_spec(config, scope.changed_paths)
        if precommit is not None:
            specs.append(precommit)
        specs.extend(self._database_specs(config))
        specs.extend(self._support_command_specs(config, "database_commands", "database"))
        notebook = self._notebook_spec(config)
        if notebook is not None:
            specs.append(notebook)
        specs.extend(self._support_command_specs(config, "notebook_commands", "notebooks"))
        service = self._service_lifecycle_spec(config)
        if service is not None:
            specs.append(service)
        specs.extend(self._support_command_specs(config, "service_commands", "service"))
        specs.extend(self._support_command_specs(config, "security_commands", "security"))
        specs.extend(self._security_specs(config))
        sbom = self._sbom_spec(config)
        if sbom is not None:
            specs.append(sbom)
        specs.append(CommandSpec("dependency-check", self._module(config, "pip", "check"), env=self._env(config), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), validation_component="dependency-integrity")))
        specs.extend(self._import_isolation_specs(config))
        specs.extend(self._cli_contract_specs(config))
        if not skip_build and scope.run_build:
            specs.extend(self.build_specs(config, "build"))
            specs.extend(self.build_specs(config, "package-check"))
            reproducible = self._reproducible_spec(config)
            if reproducible is not None:
                specs.append(reproducible)
        if not specs:
            raise SystemExit("Validation would run no Python commands. Clear a skip flag.")
        for spec in specs:
            spec.metadata.update({
                "python_validation_scope": scope.scope,
                "python_changed_paths": list(scope.changed_paths),
                "python_scope_reasons": list(scope.reasons),
                "environment_fingerprint": self._plan(config).fingerprint,
            })
        return _validation_metadata(specs, scope.scope)


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
            selective_tests=True,
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
            spec = CommandSpec(action, shlex.split(explicit), env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, explicit=True))
            if action in {"test", "wine-test"}:
                from .test_selection import apply_test_selection
                spec = apply_test_selection(config, spec)
            return [spec]
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
            selected_scripts = [script]
            if action in {"test", "wine-test"}:
                from .test_selection import apply_test_selection, resolve_test_selection
                selected = resolve_test_selection(config)
                if selected.tasks:
                    selected_scripts = selected.tasks
            specs: list[CommandSpec] = []
            for selected_script in selected_scripts:
                if selected_script not in scripts:
                    raise SystemExit(f"package.json has no '{selected_script}' script.")
                spec = CommandSpec(action.replace("wine-", ""), self._script_argv(config, selected_script), env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, node_script=selected_script))
                if action in {"test", "wine-test"}:
                    spec = apply_test_selection(config, spec)
                specs.append(spec)
            return specs
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
    capabilities = RunnerCapabilities(restore=True, build=True, test=True, selective_tests=True, validate=True, lint=True, run=True, package=True, clean=True, inspect=True, doctor=True)

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
        spec = CommandSpec(action.replace("wine-", ""), argv, env=env, cwd=str(root), destructive=action == "clean", metadata=_tool_metadata(self.kind, root))
        if action in {"test", "wine-test"}:
            from .test_selection import apply_test_selection
            spec = apply_test_selection(config, spec)
        return [spec]

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
    capabilities = RunnerCapabilities(restore=True, build=True, test=True, selective_tests=True, validate=True, lint=True, run=True, package=True, clean=True, inspect=True, doctor=True)

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
        spec = CommandSpec(action.replace("wine-", ""), argv, env=env, cwd=str(root), destructive=action == "clean", metadata=_tool_metadata(self.kind, root))
        if action in {"test", "wine-test"}:
            from .test_selection import apply_test_selection
            spec = apply_test_selection(config, spec)
        return [spec]

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
    capabilities = RunnerCapabilities(restore=True, build=True, test=True, selective_tests=True, validate=True, run=False, package=True, clean=True, inspect=True, doctor=True)

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
        spec = CommandSpec(action.replace("wine-", ""), [*self._base(config), *goals], env=env, cwd=str(root), destructive=action == "clean", metadata=_tool_metadata(self.kind, root))
        if action in {"test", "wine-test", "verify"}:
            from .test_selection import apply_test_selection
            spec = apply_test_selection(config, spec)
        return [spec]

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
    capabilities = RunnerCapabilities(restore=True, build=True, test=True, selective_tests=True, validate=True, run=False, package=True, clean=True, inspect=True, doctor=True)

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
            spec = CommandSpec("test", argv, env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, build_directory=str(build_dir)))
            from .test_selection import apply_test_selection
            return [apply_test_selection(config, spec)]
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
    capabilities = RunnerCapabilities(restore=True, build=True, test=True, selective_tests=True, validate=True, run=False, package=True, clean=True, inspect=True, doctor=True)

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
            spec = CommandSpec("test", ["meson", "test", "-C", str(build_dir), "--num-processes", str(workers), "--print-errorlogs"], env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, build_directory=str(build_dir)))
            from .test_selection import apply_test_selection
            return [apply_test_selection(config, spec)]
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
            test="test" in targets or bool(config.test_command), selective_tests=True, validate=True,
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
            from .test_selection import resolve_test_selection
            selected = resolve_test_selection(config)
            requested_targets = selected.tasks or ["test"]
            specs: list[CommandSpec] = []
            for requested_target in requested_targets:
                if requested_target and requested_target not in targets and not config.test_command:
                    raise SystemExit(f"Makefile has no '{requested_target}' target.")
                argv = ["make", "-j", str(workers), *( [requested_target] if requested_target else [])]
                specs.append(CommandSpec("test", argv, env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, make_target=requested_target)))
            return specs
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
