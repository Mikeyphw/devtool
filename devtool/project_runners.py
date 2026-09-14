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
from .execution_environments import NATIVE_TERMUX, detect_current_environment
from .models import CommandSpec, ProjectConfig
from .termux_toolchains import detect_runtime_identity, normalize_native_cache_provider, normalize_native_provider, termux_cmake_probe, termux_ninja_probe, termux_ccache_probe, termux_native_provider


def _bool_option(value: object, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled", "none", "null"}:
        return False
    return default


def _int_option(value: object, default: int, *, name: str, minimum: int | None = None) -> int:
    if value is None or value == "":
        result = default
    else:
        try:
            result = int(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"Invalid integer value for {name}: {value!r}") from exc
    if minimum is not None and result < minimum:
        raise SystemExit(f"Invalid integer value for {name}: {result}; expected >= {minimum}")
    return result


def _shell_split_option(value: object, *, name: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    try:
        return shlex.split(text)
    except ValueError as exc:
        raise SystemExit(f"Invalid shell-style command for {name}: {exc}") from exc



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


@dataclass(frozen=True, slots=True)
class ResolvedValidationPlan:
    """Immutable record of how validation was resolved for this invocation."""

    runner: str
    source: str
    tasks_declared: bool
    explicit_empty: bool
    declared_tasks: tuple[str, ...]
    planned_steps: tuple[str, ...]
    required: bool = False

    def metadata(self) -> dict[str, object]:
        return {
            "validation_plan_runner": self.runner,
            "validation_plan_source": self.source,
            "validation_tasks_declared": self.tasks_declared,
            "validation_tasks_explicit_empty": self.explicit_empty,
            "validation_declared_tasks": list(self.declared_tasks),
            "validation_planned_steps": list(self.planned_steps),
            "validation_contract_required": self.required,
        }


def resolve_validation_plan(
    config: ProjectConfig,
    specs: list[CommandSpec],
    *,
    source: str = "runner-default",
    tasks_declared: bool = False,
    declared_tasks: Iterable[str] = (),
    required: bool = False,
) -> ResolvedValidationPlan:
    tasks = tuple(dict.fromkeys(str(item).strip() for item in declared_tasks if str(item).strip()))
    steps = tuple(spec.name for spec in specs)
    return ResolvedValidationPlan(
        runner=str(config.runner or config.kind or "unknown"),
        source=source or "runner-default",
        tasks_declared=bool(tasks_declared),
        explicit_empty=bool(tasks_declared and not tasks),
        declared_tasks=tasks,
        planned_steps=steps,
        required=bool(required),
    )
def _optimized_env(config: ProjectConfig, kind: str) -> dict[str, str]:
    return optimized_build_env(config, kind, config.env)


def _android_env_for_tasks(config: ProjectConfig, tasks: list[str], *, validation: bool = False) -> dict[str, str]:
    """Return the non-secret planned Android child environment.

    AOPS03 intentionally defers Devtool-managed signing passwords until the
    final process execution boundary; planned ``CommandSpec`` objects must stay
    safe to serialize, wrap, inspect and resume.
    """
    base = optimized_validation_env(config, "android") if validation else _optimized_env(config, "android")
    from .android.signing import strip_configured_signing_source_values
    return strip_configured_signing_source_values(config, base)


def _android_signing_metadata(
    config: ProjectConfig,
    tasks: list[str],
    *,
    validation: bool = False,
) -> dict[str, object]:
    base = optimized_validation_env(config, "android") if validation else _optimized_env(config, "android")
    from .android.signing import signing_plan_metadata
    return dict(signing_plan_metadata(config, tasks, env={**os.environ, **base}))


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


def _final_seal_contract_enabled(config: ProjectConfig) -> bool:
    validation = config.runner_options.get("validation", {})
    validation = dict(validation) if isinstance(validation, dict) else {}
    return _bool_option(validation.get("final_seal_contracts"), False)


def _final_seal_contract_spec(config: ProjectConfig, *, kind: str, root: Path) -> CommandSpec:
    target = str(config.selected_target or "").strip()
    argv = [
        sys.executable,
        "-m",
        "devtool",
        "--repo",
        str(Path(config.repo_root).resolve()),
        "final-seal",
        "--strict",
    ]
    if target:
        argv.extend(["--target", target])
    return CommandSpec(
        f"{kind}-final-seal",
        argv,
        env=optimized_validation_env(config, kind),
        cwd=str(Path(config.repo_root).resolve()),
        metadata=_tool_metadata(
            kind,
            root,
            validation_component="final-seal",
            final_seal_contract=True,
            final_seal_target=target or "repository",
            report_parser="final-seal",
        ),
    )


def _append_final_seal_contract_spec(config: ProjectConfig, specs: list[CommandSpec], *, kind: str, root: Path) -> list[CommandSpec]:
    if _final_seal_contract_enabled(config):
        specs.append(_final_seal_contract_spec(config, kind=kind, root=root))
    return specs


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

        _append_final_seal_contract_spec(config, specs, kind=self.kind, root=_project_root(config))
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
            _shell_split_option(text, name=command_name),
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
    for verb in ("restore", "build", "test", "run", "clean", "pack"):
        verb_index = _dotnet_verb_index(argv, verb)
        if verb_index is None:
            continue
        if values.get("runtime"):
            argv = _dotnet_insert_option(argv, verb_index, ("-r", "--runtime"), "-r", values["runtime"])
        if verb in {"build", "test", "run", "clean", "pack"} and values.get("configuration"):
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
        collect_artifacts=True,
        verify_artifacts=True,
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
            "package",
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
                spec = CommandSpec(
                    "package", _shell_split_option(config.package_command, name="config.package_command"), env=env, cwd=str(config.repo_root),
                    metadata=_tool_metadata(self.kind, config.repo_root, collect_artifacts=True, verify_artifacts=True, artifact_required=True, explicit=True),
                )
                return [spec]
            target = build_target or restore_target
            spec = CommandSpec(
                "package", ["dotnet", "pack", target], env=env,
                metadata=_tool_metadata(self.kind, config.repo_root, collect_artifacts=True, verify_artifacts=True, artifact_required=True),
            )
            return [apply_dotnet_variant_to_spec(config, spec)]
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
        explicit_restore = _bool_option(options.get("explicit_restore"), False)
        coalesce = _bool_option(options.get("coalesce_validation"), True)
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
        package=True,
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
        from .android_integration import android_builder_arguments, android_builder_arguments_for_tasks, android_project_root, configured_tasks, plan_android_command
        from .test_selection import apply_test_selection, resolve_test_selection

        selection = resolve_test_selection(config) if action in {"test", "instrumentation"} else None
        if action == "test" and selection is not None and selection.instrumented:
            action = "instrumentation"
        if action == "instrumentation":
            android = config.runner_options.get("android", {})
            android = dict(android) if isinstance(android, dict) else {}
            root = android_project_root(config)
            variant_name = str(config.variant or android.get("variant") or "debug")
            variant_title = variant_name[:1].upper() + variant_name[1:]
            instrumentation_build_tasks = [f"assemble{variant_title}", f"assemble{variant_title}AndroidTest"]
            instrumentation_env = _android_env_for_tasks(config, instrumentation_build_tasks)
            argv = [
                sys.executable, "-m", "devtool.android_instrumentation", str(root),
                "--variant", variant_name,
                "--device-serial", str(config.device_serial or android.get("device_serial") or ""),
                "--install-command", str(android.get("install_command") or "sudo pm install -r -t"),
                "--am-command", str(android.get("am_command") or "sudo am instrument"),
                "--canonical",
            ]
            from .android.instrumentation import instrumentation_config, resolve_instrumentation_policy
            ins_cfg = instrumentation_config(config)
            adb_cfg = android.get("adb", {}) if isinstance(android.get("adb", {}), dict) else {}
            serial = str(config.device_serial or adb_cfg.get("serial") or android.get("device_serial") or "")
            device_policy = str(adb_cfg.get("device_policy") or ("serial" if serial else "exactly-one"))
            argv.extend(["--policy", resolve_instrumentation_policy(config), "--device-policy", device_policy])
            build_tools_value = android.get("build_tools", {})
            build_tools_options = build_tools_value if isinstance(build_tools_value, dict) else {}
            configured_aapt2 = str(android.get("aapt2") or "").strip()
            aapt2_provider = str(
                build_tools_options.get("aapt2_provider")
                or android.get("aapt2_provider")
                or "auto"
            ).strip().lower() or "auto"
            if configured_aapt2:
                argv.extend(["--aapt2", configured_aapt2])
            argv.extend(["--aapt2-provider", aapt2_provider])
            gradle_options = config.runner_options.get("gradle", {})
            gradle_options = dict(gradle_options) if isinstance(gradle_options, dict) else {}
            configured_gradle_args = gradle_options.get("args", [])
            if isinstance(configured_gradle_args, str):
                configured_gradle_args = [configured_gradle_args]
            if isinstance(configured_gradle_args, (list, tuple)):
                for value in configured_gradle_args:
                    item = str(value).strip()
                    if item:
                        argv.append(f"--gradle-arg={item}")
            # The generated init script contains only non-secret signing
            # configuration and reads passwords from the child environment.
            from .android.signing import signing_gradle_args_for_tasks
            for value in signing_gradle_args_for_tasks(config, instrumentation_build_tasks, env={**os.environ, **instrumentation_env}):
                argv.append(f"--gradle-arg={value}")
            runner = str(ins_cfg.get("test_runner") or "").strip()
            target_package = str(ins_cfg.get("target_package") or "").strip()
            if runner:
                argv.extend(["--runner", runner])
            if target_package:
                argv.extend(["--target-package", target_package])
            size = str(ins_cfg.get("size") or "").strip().lower()
            if size in {"small", "medium", "large"}:
                argv.extend(["--size", size])
            runner_args = ins_cfg.get("runner_args", {})
            if runner_args is None:
                runner_args = {}
            if not isinstance(runner_args, dict):
                raise SystemExit("android.instrumentation.runner_args must be a table/object of key/value pairs")
            for key, value in sorted(runner_args.items()):
                if not str(key).strip():
                    raise SystemExit("android.instrumentation.runner_args keys cannot be empty")
                argv.extend(["--runner-arg", f"{key}={value}"])
            if _bool_option(ins_cfg.get("keep_installed", False), False):
                argv.append("--keep-installed")
            if not _bool_option(ins_cfg.get("capture_logcat_on_failure", True), True):
                argv.append("--no-logcat-on-failure")
            if not _bool_option(ins_cfg.get("capture_screenshot_on_failure", True), True):
                argv.append("--no-screenshot-on-failure")
            if selection is not None:
                for value in selection.classes:
                    argv.extend(["--class", value])
                for value in selection.methods:
                    argv.extend(["--method", value])
                for value in selection.tags:
                    argv.extend(["--annotation", value])
                for value in selection.exclude_tags:
                    argv.extend(["--not-annotation", value])
            metadata: dict[str, object] = {
                "runner": self.kind,
                "project_type": self.kind,
                "project_root": str(root),
                "gradle_tasks": list(instrumentation_build_tasks),
                "progress_parser": "gradle",
                "report_parser": "android-instrumentation",
                "execution_environment": "android-device",
                "instrumentation_uses_canonical_build": True,
            }
            metadata.update(_android_signing_metadata(config, instrumentation_build_tasks))
            spec = CommandSpec(
                "instrumentation", argv, env=instrumentation_env, cwd=str(root), metadata=metadata
            )
            return [apply_test_selection(config, spec)]

        explicit = {
            "restore": config.restore_command,
            "build": config.build_command,
            "test": config.test_command,
            "package": config.package_command,
        }.get(action, "")
        if explicit:
            specs = _maybe_explicit(action, explicit, _optimized_env(config, self.kind))
            for spec in specs:
                spec.metadata.update({
                    "runner": self.kind,
                    "project_type": self.kind,
                    "progress_parser": "gradle",
                    "report_parser": "gradle-junit" if action == "test" else "",
                    "collect_artifacts": action in {"build", "package"},
                    "verify_artifacts": action in {"build", "package"},
                    "artifact_required": action == "package",
                })
            return [apply_test_selection(config, spec) for spec in specs] if action == "test" else specs

        if action not in {"restore", "build", "test", "lint", "package", "clean"}:
            raise SystemExit(f"Unsupported Android/Gradle action: {action}")

        tasks = configured_tasks(config, action)
        if action == "package" and not tasks:
            tasks = configured_tasks(config, "build")
        if action == "lint" and not tasks:
            raise SystemExit(
                "No Gradle lint task was discovered. Configure [tasks].lint for this target."
            )
        plan_action = "build" if action == "package" else action
        plan = plan_android_command(config, plan_action)
        if action == "package":
            argv = [
                *plan.argv_prefix,
                *android_builder_arguments_for_tasks(config, tasks, scan_artifacts=True),
            ]
        else:
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
                env=_android_env_for_tasks(config, list(tasks if action == "package" else plan.default_tasks)),
                cwd=str(plan.project_root),
                metadata={
                    "runner": self.kind,
                    "project_type": self.kind,
                    "gradle_tasks": list(tasks if action == "package" else plan.default_tasks),
                    "android_builder": str(plan.builder),
                    "project_root": str(plan.project_root),
                    "collect_artifacts": action in {"build", "package"},
                    "verify_artifacts": action in {"build", "package"},
                    "artifact_required": action == "package",
                    "progress_parser": "gradle",
                    "report_parser": report_parser,
                },
            )
        ]
        for spec in specs:
            planned_tasks = [str(item) for item in spec.metadata.get("gradle_tasks", []) or []]
            spec.metadata.update(_android_signing_metadata(config, planned_tasks))
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
            "full": {"preflight", "dependencies", "compile", "resources", "unit-test", "package", "lint", "instrumentation", "finalize"},
            "source": {"compile", "unit-test", "package", "lint", "instrumentation", "finalize"},
            "resources": {"resources", "package", "lint", "finalize"},
            "tests": {"unit-test", "lint", "instrumentation", "finalize"},
            "docs": {"finalize"},
        }
        allowed_phases = allowed_by_scope.get(change_scope, allowed_by_scope["full"])

        _phase_missing = object()

        def phase_value(name: str) -> object:
            if name in phase_config:
                return phase_config[name]
            alternate = name.replace("-", "_")
            if alternate in phase_config:
                return phase_config[alternate]
            return _phase_missing

        def phase_enabled(name: str) -> bool:
            if name not in allowed_phases:
                return False
            configured = phase_value(name)
            if configured is _phase_missing:
                return True
            if configured is False:
                return False
            if isinstance(configured, (list, tuple)) and not configured:
                return False
            if isinstance(configured, str) and configured.strip().lower() in {"off", "disabled", "none", "skip"}:
                return False
            return True

        def task_values(value: object) -> list[str]:
            if isinstance(value, str):
                return [item.strip() for item in value.replace(",", " ").split() if item.strip()]
            if isinstance(value, (list, tuple)):
                return [str(item).strip() for item in value if str(item).strip()]
            return []

        def phase_tasks(name: str, defaults: list[str]) -> list[str]:
            configured = phase_value(name)
            if configured is _phase_missing:
                return defaults
            return task_values(configured)

        def gradle_phase(
            phase: str,
            tasks: list[str],
            *,
            report_parsers: list[str] | None = None,
            scan_artifacts: bool = False,
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
                "collect_artifacts": phase == "package",
                "verify_artifacts": phase == "package",
                "artifact_required": phase == "package",
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
                env=_android_env_for_tasks(config, unique_tasks, validation=True),
                cwd=str(plan.project_root),
                metadata=metadata,
            )
            if phase == "unit-test":
                spec = apply_test_selection(config, spec)
            return spec

        def internal_phase(phase: str, message: str) -> CommandSpec:
            policy = resolve_android_validation_phase_policy(config, phase)
            return CommandSpec(
                f"{self.kind}-validation-{phase}",
                ["sh", "-c", 'printf "%s\\n" "$1"', "devtool", message],
                env=optimized_validation_env(config, self.kind),
                cwd=str(project_root),
                metadata={
                    "runner": self.kind,
                    "project_type": self.kind,
                    "project_root": str(project_root),
                    "gradle_tasks": [],
                    "collect_artifacts": False,
                    "verify_artifacts": False,
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
        if lint_tasks and phase_enabled("lint"):
            specs.append(gradle_phase("lint", phase_tasks("lint", lint_tasks), report_parsers=["android-lint"]))

        from .android.instrumentation import resolve_instrumentation_policy
        instrumentation_policy = resolve_instrumentation_policy(config)
        run_instrumentation = not skip_test and instrumentation_policy != "disabled"
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
            package_outputs_include_android_test = any(
                "androidtest" in task.lower()
                for task in phase_tasks(
                    "package",
                    configured_tasks(config, "build"),
                )
            )
            for spec in instrumentation_specs:
                if (
                    package_outputs_include_android_test
                    and "--skip-build" not in spec.argv
                ):
                    spec.argv.append("--skip-build")
                policy = resolve_android_validation_phase_policy(
                    config,
                    "instrumentation",
                )
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
        _append_final_seal_contract_spec(config, specs, kind=self.kind, root=project_root)
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
            android_gradle_task_execution_plan,
            android_project_root,
            android_validation_lint_tasks,
            configured_tasks,
            plan_android_command,
        )
        from .test_selection import apply_test_selection

        validation_options = config.runner_options.get("validation", {})
        validation_options = dict(validation_options) if isinstance(validation_options, dict) else {}
        if bool(validation_options.get("resumable", False)):
            from .android_integration import android_project_inspection_cache
            with android_project_inspection_cache(config):
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
                    gradle_tasks_for_spec = [str(item) for item in spec.metadata.get("gradle_tasks", [])] if isinstance(spec.metadata.get("gradle_tasks", []), (list, tuple)) else []
                    spec.env = _android_env_for_tasks(config, gradle_tasks_for_spec, validation=True) if gradle_tasks_for_spec else dict(validation_env)
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
                task_execution = android_gradle_task_execution_plan(
                    config,
                    unique_tasks,
                    validation=True,
                    validation_phase=validation_phase,
                )
                execution_tasks = list(task_execution.optimized)
                if not execution_tasks:
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
                    validation_components = [validation_phase]
                metadata = {
                    "runner": self.kind,
                    "project_type": self.kind,
                    "gradle_tasks": execution_tasks,
                    "requested_gradle_tasks": unique_tasks,
                    "gradle_workload_tasks": list(task_execution.workload_tasks),
                    "gradle_task_optimization": task_execution.as_dict(),
                    "android_builder": str(plan.builder),
                    "project_root": str(android_project_root(config)),
                    "collect_artifacts": "build" in components or validation_phase == "package",
                    "verify_artifacts": "build" in components or validation_phase == "package",
                    "artifact_required": validation_phase == "package",
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
                            scan_artifacts=("build" in components or validation_phase == "package"),
                            validation=True,
                            validation_phase=validation_phase,
                        ),
                    ],
                    env=_android_env_for_tasks(config, unique_tasks, validation=True),
                    cwd=str(plan.project_root),
                    metadata=metadata,
                )
                if "test" in components:
                    spec = apply_test_selection(config, spec)
                specs.append(spec)

        android_options = config.runner_options.get("android", {})
        android_options = dict(android_options) if isinstance(android_options, dict) else {}
        from .android.instrumentation import resolve_instrumentation_policy
        instrumentation_policy = resolve_instrumentation_policy(config)
        if not skip_test and instrumentation_policy == "required":
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
            # The orchestrator owns device availability. It emits a documented
            # skip when no usable target is present instead of planning based on PATH.
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

        _append_final_seal_contract_spec(config, specs, kind=self.kind, root=android_project_root(config))
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
            planned_tasks = [str(item) for item in metadata.get("gradle_tasks", []) or []]
            workload_tasks = [str(item) for item in metadata.get("gradle_workload_tasks", []) or []]
            if planned_tasks:
                metadata.update(_android_signing_metadata(config, workload_tasks or planned_tasks, validation=True))
            spec.metadata = metadata
        return specs



def _jvm_gradle_options(config: ProjectConfig) -> dict[str, object]:
    raw = config.runner_options.get("gradle", {})
    return dict(raw) if isinstance(raw, dict) else {}


def _jvm_gradle_launcher(
    config: ProjectConfig,
    root: Path,
    *,
    options: dict[str, object] | None = None,
) -> tuple[str, str, str | None]:
    """Resolve a Gradle/JVM launcher without blindly preferring ``gradlew``.

    Generic Gradle targets historically selected the project wrapper whenever
    a ``gradlew`` file existed.  On Termux that can force GradleWrapperMain to
    download a distribution even when the target pins the exact Gradle version
    already installed on PATH.  Android builds already avoid that needless
    bootstrap.  Keep the wrapper authoritative for the required version, but
    allow an installed Gradle with the *same* version to execute it.

    Returns ``(launcher, source, required_version)``.  Auto mode never downloads
    during planning: a mismatching/unavailable local Gradle falls back to the
    wrapper exactly as before.  Only an explicit ``provider=download`` delegates
    to the canonical managed resolver.  Explicit ``provider=termux|system`` is
    fail-closed instead of silently using another provider/version.
    """

    from . import android_build

    gradle = dict(options or _jvm_gradle_options(config))
    provider = str(gradle.get("provider") or "auto").strip().lower() or "auto"
    if provider not in {"auto", "wrapper", "termux", "system", "download"}:
        raise SystemExit(
            f"Unsupported Gradle provider for Gradle/JVM target: {provider}. "
            "Expected auto, wrapper, termux, system, or download."
        )

    wrapper = root / ("gradlew.bat" if os.name == "nt" else "gradlew")
    wrapper_version = android_build.wrapper_distribution_version(root)
    configured_version = str(gradle.get("version") or "").strip() or None
    required_version = configured_version or wrapper_version

    configured_command = str(gradle.get("command") or "").strip()
    if configured_command:
        candidate = Path(configured_command).expanduser()
        if not candidate.is_absolute():
            resolved = shutil.which(configured_command)
            if not resolved:
                raise SystemExit(f"Configured Gradle command not found: {configured_command}")
            candidate = Path(resolved)
        candidate = candidate.resolve(strict=False)
        actual_version = android_build.gradle_version(candidate)
        if required_version and not android_build.gradle_version_matches(actual_version, required_version):
            raise SystemExit(
                f"Configured Gradle command {candidate} reports {actual_version or 'an unknown version'}, "
                f"but {required_version} is required."
            )
        return str(candidate), "explicit", required_version

    if provider == "wrapper":
        if not wrapper.is_file():
            raise SystemExit(f"Gradle provider wrapper requested, but no wrapper exists at {wrapper}.")
        return str(wrapper), "wrapper", required_version

    # ``download`` is an explicit request for the managed provider.  Delegate
    # to the existing canonical resolver so its cache/offline semantics stay
    # identical to Android builds.
    if provider == "download":
        cache_root = android_build.devtool_cache_dir() / android_build.APP_NAME
        gradle_user_home_value = str(gradle.get("user_home") or "").strip()
        gradle_user_home = Path(gradle_user_home_value).expanduser() if gradle_user_home_value else None
        selected = android_build.select_gradle(
            root,
            explicit=None,
            version=configured_version,
            cache_root=cache_root,
            offline=_bool_option(gradle.get("offline"), False),
            force=False,
            bootstrap_downloads="missing",
            bootstrap_network="deny" if _bool_option(gradle.get("offline"), False) else "allow",
            gradle_user_home=gradle_user_home,
            provider="download",
            project=None,
        )
        return str(selected), "download", required_version

    local = shutil.which("gradle")
    if local:
        # Keep the PATH-visible launcher in command specs so tests, clipboard
        # output, and user logs identify the executable Devtool actually selected
        # from PATH.  Use the resolved target only for capability/version probes;
        # Termux often exposes /usr/bin/gradle as a symlink to /usr/opt/gradle.
        local_candidate = Path(local).expanduser()
        local_path = local_candidate.resolve(strict=False)
        local_launcher = str(local_candidate)
        actual_version = android_build.gradle_version(local_candidate)
        local_provider = android_build.executable_provider(local_candidate)
        provider_matches = provider == "auto" or provider == local_provider
        version_matches = (
            required_version is None
            or android_build.gradle_version_matches(actual_version, required_version)
        )
        # With a complete wrapper/version pin, auto mode may use the local
        # executable only when it is an exact version substitute.  For a
        # wrapperless/unpinned project, preserve the historical PATH behavior.
        if provider_matches and version_matches:
            return local_launcher, local_provider, required_version

        if provider in {"termux", "system"}:
            if not provider_matches:
                raise SystemExit(
                    f"Gradle provider {provider} requested, but PATH resolves to {local_provider} Gradle at {local_launcher}."
                )
            raise SystemExit(
                f"Gradle provider {provider} at {local_launcher} reports {actual_version or 'an unknown version'}, "
                f"but {required_version or 'a usable Gradle version'} is required."
            )
    elif provider in {"termux", "system"}:
        raise SystemExit(f"Gradle provider {provider} requested, but no gradle executable was found on PATH.")

    if wrapper.is_file():
        return str(wrapper), "wrapper", required_version
    if local:
        return str(Path(local).expanduser()), "path", required_version
    return "gradle", "path", required_version


def _configured_gradle_cli_args(options: dict[str, object]) -> list[str]:
    """Translate generic ``[gradle]`` options into direct Gradle CLI args."""

    argv: list[str] = []
    max_workers = options.get("max_workers")
    try:
        workers = int(max_workers) if max_workers not in {None, "", "auto"} else 0
    except (TypeError, ValueError):
        workers = 0
    if workers > 0:
        argv.append(f"--max-workers={workers}")
    if _bool_option(options.get("offline"), False):
        argv.append("--offline")
    if _bool_option(options.get("no_daemon"), False):
        argv.append("--no-daemon")
    if _bool_option(options.get("parallel"), False):
        argv.append("--parallel")
    if "build_cache" in options:
        if _bool_option(options.get("build_cache"), True):
            argv.append("--build-cache")
        else:
            argv.append("--no-build-cache")

    raw_args = options.get("args", [])
    if isinstance(raw_args, str):
        raw_args = [raw_args]
    if isinstance(raw_args, (list, tuple)):
        argv.extend(str(item).strip() for item in raw_args if str(item).strip())
    return argv


class JvmGradleRunner(ProjectRunner):
    """Plain JVM Gradle runner with no Android capability inheritance."""

    kind = "gradle"
    display_name = "Gradle/JVM"
    capabilities = RunnerCapabilities(
        restore=True, build=True, test=True, selective_tests=True, validate=True,
        lint=True, package=True, clean=True, collect_artifacts=True, verify_artifacts=True,
    )

    def _root(self, config: ProjectConfig) -> Path:
        target = Path(config.build_target).expanduser() if config.build_target else Path(config.project_root or config.repo_root)
        if target.is_file():
            target = target.parent
        return target.resolve()

    def _tasks(self, config: ProjectConfig, action: str) -> list[str]:
        options = config.runner_options.get("tasks", {})
        options = dict(options) if isinstance(options, dict) else {}
        raw = options.get(action)
        if isinstance(raw, str):
            values = [item for item in re.split(r"[\s,]+", raw.strip()) if item]
        elif isinstance(raw, (list, tuple)):
            values = [str(item).strip() for item in raw if str(item).strip()]
        else:
            values = []
        if values:
            return values
        defaults = {"restore": ["help"], "build": ["build"], "test": ["test"], "package": ["assemble"], "clean": ["clean"]}
        return list(defaults.get(action, []))

    def build_specs(self, config: ProjectConfig, action: str) -> list[CommandSpec]:
        from .test_selection import apply_test_selection
        explicit = {
            "restore": config.restore_command, "build": config.build_command, "test": config.test_command,
            "package": config.package_command,
        }.get(action, "")
        env = _optimized_env(config, self.kind)
        if explicit:
            specs = _maybe_explicit(action, explicit, env)
        else:
            if action not in {"restore", "build", "test", "lint", "package", "clean"}:
                raise SystemExit(f"Unsupported JVM/Gradle action: {action}")
            tasks = self._tasks(config, action)
            if not tasks:
                raise SystemExit(f"No Gradle {action} task is configured for this JVM project.")
            root = self._root(config)
            gradle_options = _jvm_gradle_options(config)
            gradle_user_home = str(gradle_options.get("user_home") or "").strip()
            if gradle_user_home:
                env["GRADLE_USER_HOME"] = str(Path(gradle_user_home).expanduser())
            gradle, launcher_source, required_version = _jvm_gradle_launcher(
                config, root, options=gradle_options
            )
            argv = [gradle, *tasks, *_configured_gradle_cli_args(gradle_options)]
            structured_events = _bool_option(gradle_options.get("structured_events"), True)
            if structured_events:
                # Plain JVM Gradle participates in the same exact-output
                # provenance contract as Android builds.  The generic Gradle
                # init script reports task outputs and UP-TO-DATE state.
                from .android_integration import stage_gradle_init_script
                argv.extend(["--init-script", str(stage_gradle_init_script())])
                if "--configuration-cache" not in argv and "--no-configuration-cache" not in argv:
                    argv.append("--no-configuration-cache")
            specs = [CommandSpec(
                action, argv, env=env, cwd=str(root),
                metadata=_tool_metadata(
                    self.kind, root, gradle_tasks=list(tasks), gradle_direct=True,
                    gradle_launcher=gradle, gradle_launcher_source=launcher_source,
                    gradle_required_version=required_version or "",
                    progress_parser="gradle" if structured_events else "generic",
                    report_parser="gradle-junit" if action == "test" else "",
                    collect_artifacts=action in {"build", "package"}, verify_artifacts=action in {"build", "package"},
                    artifact_required=action == "package",
                ),
            )]
        for spec in specs:
            spec.metadata.update({
                "runner": self.kind, "project_type": "gradle", "gradle_direct": True,
                "collect_artifacts": action in {"build", "package"},
                "verify_artifacts": action in {"build", "package"},
                "artifact_required": action == "package",
            })
        return [apply_test_selection(config, spec) for spec in specs] if action == "test" else specs

    def build_validation_specs(
        self, config: ProjectConfig, *, wine: bool = False, skip_restore: bool = False,
        skip_build: bool = False, skip_test: bool = False,
    ) -> list[CommandSpec]:
        return ProjectRunner.build_validation_specs(
            self, config, wine=wine, skip_restore=skip_restore, skip_build=skip_build, skip_test=skip_test
        )


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
        from .python_support import default_python_environment_root
        options = self._options(config)
        value = str(options.get("environment") or "").strip()
        if not value:
            normalized_environment = str(config.execution_environment or "").strip().lower().replace("_", "-")
            if normalized_environment in {"proot", "proot-distro"}:
                from .python_support import python_repo_key
                return str((self._root(config) / ".devtool" / "proot" / "python" / python_repo_key(self._root(config))).resolve())
            name = config.selected_target or "python"
            return str(default_python_environment_root(self._root(config), name).resolve())
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self._root(config) / path
        return str(path.resolve())

    def _configured_python(self, config: ProjectConfig) -> tuple[str, bool]:
        options = self._options(config)
        for key in ("executable", "interpreter", "python"):
            if key in options and str(options.get(key) or "").strip():
                return str(options[key]).strip(), True
        explicit = config.config_provenance.get("python_executable", "default") != "default"
        return str(config.python_executable or "python3").strip() or "python3", explicit

    def _plan(self, config: ProjectConfig):
        from .python_support import resolve_environment
        info = self._info(config)
        environment = self._environment_path(config) if info.manager == "venv" else str(self._options(config).get("environment") or "")
        configured_python, strict_python = self._configured_python(config)
        key = (
            str(info.root), info.manager, configured_python, environment,
            tuple(self._list_option(config, "groups")), tuple(self._list_option(config, "extras")),
            config.execution_environment, info.requires_python, info.dependency_files,
        )
        cache = getattr(self, "_python_plan_cache", None)
        if isinstance(cache, dict) and key in cache:
            return cache[key]
        plan = resolve_environment(
            info,
            configured_python=configured_python,
            environment=environment,
            groups=key[4],
            extras=key[5],
            execution_environment=config.execution_environment,
            strict_configured_python=strict_python,
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
        from .python_support import python_cache_env, python_repo_key
        env = _optimized_env(config, self.kind)
        env.update(python_cache_env(self._root(config), config.execution_environment))
        info = self._info(config)
        if info.manager == "uv":
            plan = self._plan(config)
            if plan.environment_root:
                env.setdefault("UV_PROJECT_ENVIRONMENT", plan.environment_root)
        options = self._options(config)
        if isolated or _bool_option(options.get("isolation"), False):
            project_root = self._root(config)
            execution = str(config.execution_environment or "auto").strip().lower().replace("_", "-")
            current = detect_current_environment()

            # Replacing HOME must not silently discard the user's pip index
            # policy.  This matters especially on Termux, where Android wheels
            # may come from a user-configured index rather than PyPI.  Preserve
            # an explicit PIP_CONFIG_FILE, or pin pip to the existing user
            # config before HOME is redirected to the isolated validation home.
            if "PIP_CONFIG_FILE" not in env:
                explicit_pip_config = os.environ.get("PIP_CONFIG_FILE")
                if explicit_pip_config:
                    env["PIP_CONFIG_FILE"] = explicit_pip_config
                else:
                    pip_config_candidates: list[Path] = []
                    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
                    original_home = os.environ.get("HOME")
                    if xdg_config_home:
                        pip_config_candidates.append(Path(xdg_config_home).expanduser() / "pip" / "pip.conf")
                    if original_home:
                        home_path = Path(original_home).expanduser()
                        pip_config_candidates.extend([
                            home_path / ".config" / "pip" / "pip.conf",
                            home_path / ".pip" / "pip.conf",
                        ])
                    for candidate in pip_config_candidates:
                        if candidate.is_file():
                            env["PIP_CONFIG_FILE"] = str(candidate)
                            break

            # Test/validator temp trees must not live under the repository.
            # Otherwise nested TemporaryDirectory projects inherit the parent
            # .devtool.toml/Git boundary and cease to be isolated. Native
            # Termux also needs its private $PREFIX/tmp rather than Android's
            # unwritable /tmp, and the shorter path keeps AF_UNIX fixtures safe.
            use_host_temp = execution not in {"chroot", "proot", "proot-distro", "ssh"}
            if current == NATIVE_TERMUX and use_host_temp:
                prefix = Path(os.environ.get("PREFIX") or "/data/data/com.termux/files/usr").expanduser()
                tmp = prefix / "tmp"
                home = tmp / f"dt-home-{python_repo_key(project_root).rsplit('-', 1)[-1][:8]}"
            elif use_host_temp:
                tmp = Path(os.environ.get("TMPDIR") or "/tmp").expanduser()
                try:
                    tmp.relative_to(project_root)
                except ValueError:
                    pass
                else:
                    tmp = Path("/tmp")
                home = tmp / f"devtool-home-{python_repo_key(project_root).rsplit('-', 1)[-1][:8]}"
            else:
                # Chroot/PRoot execution keeps the repository-mapped paths so
                # the guest always sees a writable location without assuming
                # host /tmp is mounted into the guest.
                home = project_root / ".devtool" / "tmp" / "python-home"
                tmp = project_root / ".devtool" / "tmp" / "python"

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
                "DEVTOOL_NETWORK_DISABLED": "1" if _bool_option(options.get("disable_network"), False) else "0",
            })
        return env

    def _network_wrap(self, config: ProjectConfig, argv: list[str]) -> list[str]:
        if not _bool_option(self._options(config).get("disable_network"), False):
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

    def _needs_python_environment_bootstrap(self, config: ProjectConfig) -> bool:
        info = self._info(config)
        if info.manager != "venv":
            return False
        plan = self._plan(config)
        normalized_environment = str(config.execution_environment or "").strip().lower().replace("_", "-")
        if normalized_environment in {"proot", "proot-distro"}:
            # The guest venv may contain glibc executables that must never be
            # executed as host-side probes on native Termux. Let the guest-side
            # idempotent bootstrap decide whether work is required.
            return True
        if plan.environment_root and not Path(plan.python).is_file():
            return True
        from .python_support import missing_auto_validator_dependencies
        return bool(missing_auto_validator_dependencies(info, plan))

    def _validation_scope(self, config: ProjectConfig):
        from .python_support import python_validation_scope
        validation = config.runner_options.get("validation", {})
        changed = validation.get("changed_paths", []) if isinstance(validation, dict) else []
        values = [str(item) for item in changed] if isinstance(changed, (list, tuple, set)) else []
        return python_validation_scope(config.repo_root, values, self._info(config))

    def build_test_collection_spec(self, config: ProjectConfig) -> CommandSpec:
        """Plan native pytest collection for interactive/list-test discovery."""
        info = self._info(config)
        if info.framework != "pytest":
            raise SystemExit(f"Native test collection is available only for pytest projects, not {info.framework or 'unknown'}.")
        root = self._root(config)
        argv = self._module(config, "pytest", "--collect-only", "-q")
        return CommandSpec(
            "test-collect",
            argv,
            env=self._env(config, isolated=True),
            cwd=str(root),
            metadata={
                **_tool_metadata(self.kind, root, test_framework="pytest"),
                "test_collection": True,
                "test_collection_provider": "pytest",
            },
        )

    def _pytest_spec(self, config: ProjectConfig, *, targets: Iterable[str] = ()) -> CommandSpec:
        from .test_selection import apply_test_selection, resolve_test_selection
        root = self._root(config)
        options = self._options(config)
        report = root / ".devtool" / "reports" / "pytest.xml"
        coverage = options.get("coverage", {})
        coverage = dict(coverage) if isinstance(coverage, dict) else {}
        argv = self._module(config, "pytest", f"--junitxml={report}")
        selection = resolve_test_selection(config)
        target_values = list(dict.fromkeys(str(item) for item in targets if str(item)))
        defaults_suppressed = bool(selection.exact_pytest() and not selection.pytest_include_defaults)
        if defaults_suppressed:
            target_values = []
        argv.extend(target_values)
        if coverage:
            source = str(coverage.get("source") or ".")
            argv.extend([f"--cov={source}", "--cov-report=term-missing", f"--cov-report=xml:{root / '.devtool' / 'reports' / 'coverage.xml'}"])
            minimum = coverage.get("minimum_total")
            if minimum is not None:
                argv.append(f"--cov-fail-under={minimum}")
        retries = _int_option(options.get("flaky_retries"), 0, name="python.flaky_retries", minimum=0)
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
                "pytest_defaults_suppressed": defaults_suppressed,
                "pytest_selection_mode": "explicit" if selection.exact_pytest() else "default",
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
            from .test_selection import resolve_test_selection
            if resolve_test_selection(config).active():
                raise SystemExit("Test selection is not implemented safely for unittest; refusing to run the full suite.")
            return [self._unittest_spec(config, targets=targets)]
        if info.framework == "tox":
            from .test_selection import resolve_test_selection
            resolved_selection = resolve_test_selection(config)
            unsupported = any((resolved_selection.selectors, resolved_selection.ids, resolved_selection.tags, resolved_selection.exclude_tags, resolved_selection.paths, resolved_selection.classes, resolved_selection.methods, resolved_selection.native_filter, resolved_selection.instrumented))
            if unsupported:
                raise SystemExit("tox supports Devtool selection only by environment/task; refusing unsupported selectors.")
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
            from .test_selection import resolve_test_selection
            resolved_selection = resolve_test_selection(config)
            unsupported = any((resolved_selection.selectors, resolved_selection.ids, resolved_selection.tags, resolved_selection.exclude_tags, resolved_selection.paths, resolved_selection.classes, resolved_selection.methods, resolved_selection.native_filter, resolved_selection.instrumented))
            if unsupported:
                raise SystemExit("nox supports Devtool selection only by session/task; refusing unsupported selectors.")
            selection = config.runner_options.get("test_selection", {})
            selection = dict(selection) if isinstance(selection, dict) else {}
            sessions = selection.get("tasks") or selection.get("task") or self._list_option(config, "nox_sessions")
            values = [sessions] if isinstance(sessions, str) else list(sessions or [])
            argv = self._executable(config, "nox")
            if values:
                argv.extend(["-s", *[str(item) for item in values]])
            if _bool_option(self._options(config).get("nox_reuse"), True):
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

    def _python_support_prefix(self, config: ProjectConfig) -> list[str]:
        normalized_environment = str(config.execution_environment or "").strip().lower().replace("_", "-")
        if normalized_environment in {"chroot", "proot", "proot-distro"}:
            plan = self._plan(config)
            return [plan.bootstrap_python or "python3", "-m", "devtool.python_support"]
        return [sys.executable, "-m", "devtool.python_support"]

    def _python_support_python_arg(self, config: ProjectConfig) -> str:
        normalized_environment = str(config.execution_environment or "").strip().lower().replace("_", "-")
        if normalized_environment in {"chroot", "proot", "proot-distro"}:
            plan = self._plan(config)
            return plan.bootstrap_python or "python3"
        return self._configured_python(config)[0]

    def _runtime_python_compatibility_spec(self, config: ProjectConfig) -> CommandSpec | None:
        from .python_support import runtime_requires_python_argv
        info = self._info(config)
        if not info.requires_python:
            return None
        execution = str(config.execution_environment or "").strip().lower().replace("_", "-")
        if execution not in {"chroot", "proot", "proot-distro"}:
            return None
        plan = self._plan(config)
        argv = runtime_requires_python_argv(plan.bootstrap_python or plan.python or "python3", info.requires_python)
        return CommandSpec(
            "python-runtime-compatibility", argv, env=self._env(config), cwd=str(self._root(config)),
            metadata=_tool_metadata(self.kind, self._root(config), validation_component="python-runtime-compatibility", requires_python=info.requires_python),
        )

    def _lint_specs(self, config: ProjectConfig, *, targets: Iterable[str] = ()) -> list[CommandSpec]:
        root = self._root(config)
        target_values = list(dict.fromkeys(str(item) for item in targets if str(item))) or ["."]
        plan = self._plan(config)
        format_fix = _bool_option(self._options(config).get("format_fix"), False)
        specs: list[CommandSpec] = []

        for configured_tool in self._lint_tools(config):
            tool, *configured_args = _shell_split_option(configured_tool, name="python lint tool")

            if tool == "ruff":
                fix_args = (
                    ["--fix"]
                    if format_fix and "--fix" not in configured_args
                    else []
                )
                tool_args = [
                    "check",
                    *fix_args,
                    *configured_args,
                    *target_values,
                ]

            elif tool == "ruff-format":
                configured_args = [
                    arg
                    for arg in configured_args
                    if not (format_fix and arg == "--check")
                ]
                tool, tool_args = "ruff", [
                    "format",
                    *([] if format_fix else ["--check"]),
                    *configured_args,
                    *target_values,
                ]

            elif tool == "black":
                configured_args = [
                    arg
                    for arg in configured_args
                    if not (format_fix and arg == "--check")
                ]
                tool_args = [
                    *([] if format_fix else ["--check"]),
                    *configured_args,
                    *target_values,
                ]

            elif tool == "isort":
                configured_args = [
                    arg
                    for arg in configured_args
                    if not (
                        format_fix
                        and arg in {"--check", "--check-only"}
                    )
                ]
                tool_args = [
                    *([] if format_fix else ["--check-only"]),
                    *configured_args,
                    *target_values,
                ]
            elif tool == "bandit":
                tool_args = ["-r", *configured_args, *target_values]
            else:
                tool_args = [*configured_args, *target_values]
            helper = [
                *self._python_support_prefix(config), "run-tool",
                "--manager", self._info(config).manager,
                "--framework", self._info(config).framework,
                "--python", self._python_support_python_arg(config),
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

    def _restore_phase_name(self, command: list[str]) -> str:
        if len(command) >= 3 and command[1:3] == ["-m", "venv"]:
            return "venv"
        if len(command) >= 4 and command[1:3] == ["-m", "devtool.python_support"] and command[3] == "ensure-venv":
            return "venv"
        if "-r" in command:
            return "restore:requirements"
        if "--no-build-isolation" in command and "-e" in command:
            return "restore:project"
        if "-e" in command:
            return "restore:project"
        install_index = -1
        for index, token in enumerate(command):
            if token == "install":
                install_index = index
                break
        packages = command[install_index + 1:] if install_index >= 0 else command
        option_values = {"-c", "--constraint", "--python", "--project", "--index-url", "--extra-index-url", "--only-binary", "--no-binary", "--find-links", "-f", "--only-binary", "--no-binary", "--find-links", "-f"}
        filtered: list[str] = []
        skip_next = False
        for item in packages:
            if skip_next:
                skip_next = False
                continue
            text = str(item)
            if text in option_values:
                skip_next = True
                continue
            if text.startswith("-"):
                continue
            filtered.append(text)
        packages = filtered
        normalized = {str(item).split("[", 1)[0].split("==", 1)[0].split(">=", 1)[0].split("<=", 1)[0].lower() for item in packages}
        if normalized and normalized <= {"pytest", "pytest-asyncio", "pexpect", "httpx"}:
            return "restore:test-tools"
        if any(item.startswith("setuptools") or item in {"wheel", "build", "hatchling", "flit_core", "poetry-core"} for item in normalized):
            return "restore:build-system"
        return "restore"

    def _restore_specs(self, config: ProjectConfig) -> list[CommandSpec]:
        from .python_support import build_sync_command, PythonProjectError, termux_pydantic_env, termux_pydantic_pip_workaround, termux_pydantic_env, termux_pydantic_pip_workaround
        compatibility = self._runtime_python_compatibility_spec(config)
        prefix = [compatibility] if compatibility is not None else []
        if config.restore_command:
            return [*prefix, CommandSpec("restore", _shell_split_option(config.restore_command, name="config.restore_command"), env=self._env(config), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), explicit=True))]
        info = self._info(config)
        plan = self._plan(config)
        options = self._options(config)
        groups = self._list_option(config, "groups")
        extras = self._list_option(config, "extras")
        try:
            commands = build_sync_command(
                info, plan,
                groups=groups,
                extras=extras,
                locked=_bool_option(options.get("locked"), True),
                offline=_bool_option(options.get("offline"), False),
                install_project=_bool_option(options.get("install_project"), True),
                constraints=str(options.get("constraints") or ""),
            )
        except PythonProjectError as exc:
            raise SystemExit(str(exc)) from exc
        restore_env = termux_pydantic_env(self._env(config))
        specs: list[CommandSpec] = list(prefix)
        for command in commands:
            command = termux_pydantic_pip_workaround(command)
            name = self._restore_phase_name(command)
            specs.append(CommandSpec(
                name, command, env=restore_env, cwd=str(self._root(config)),
                metadata=_tool_metadata(self.kind, self._root(config), manager=info.manager),
            ))
        validator_dependencies = self._list_option(config, "validator_dependencies")
        if validator_dependencies and info.manager == "venv":
            validator_command = termux_pydantic_pip_workaround(self._module(config, "pip", "install", *validator_dependencies))
            specs.append(CommandSpec("validator-dependencies", validator_command, env=restore_env, cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config))))
        snapshot = [
            *self._python_support_prefix(config), "environment-snapshot",
            str(self._root(config)), "--manager", info.manager, "--framework", info.framework,
            "--python", self._python_support_python_arg(config), "--environment", plan.environment_root,
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
        return [CommandSpec(f"{name}:{index}", _shell_split_option(str(command), name="runner command"), env=self._env(config, isolated=True), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), validation_component=name)) for index, command in enumerate(commands, 1) if str(command).strip()]

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
        if not _bool_option(self._options(config).get("source_import_isolation"), False):
            return []
        plan = self._plan(config)
        specs: list[CommandSpec] = []
        for module in self._info(config).import_packages:
            command = [plan.python, "-I", "-c", f"import {module}"] if plan.manager == "venv" else [*plan.run_prefix, "python", "-I", "-c", f"import {module}"]
            specs.append(CommandSpec(f"import:{module}", command, env=self._env(config, isolated=True), cwd=str(self._root(config).parent), metadata=_tool_metadata(self.kind, self._root(config), validation_component="import-isolation", import_module=module)))
        return specs

    def _cli_contract_specs(self, config: ProjectConfig) -> list[CommandSpec]:
        if not _bool_option(self._options(config).get("cli_contract"), False):
            return []
        specs: list[CommandSpec] = []
        for script in self._info(config).console_script_map:
            for flag in ("--help", "--version"):
                specs.append(CommandSpec(f"cli:{script}:{flag[2:]}", self._executable(config, script, flag), env=self._env(config, isolated=True), cwd=str(self._root(config).parent), metadata=_tool_metadata(self.kind, self._root(config), validation_component="cli-contract", console_script=script)))
        return specs

    def _source_audit_spec(self, config: ProjectConfig) -> CommandSpec:
        info = self._info(config)
        argv = [
            *self._python_support_prefix(config), "source-audit",
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
            *self._python_support_prefix(config), "diff-coverage",
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
        if not (_bool_option(options.get("sbom"), False) or _bool_option(security.get("sbom"), False)):
            return None
        info = self._info(config)
        plan = self._plan(config)
        argv = [
            *self._python_support_prefix(config), "sbom",
            str(self._root(config)), "--manager", info.manager, "--framework", info.framework,
            "--python", self._python_support_python_arg(config), "--environment", plan.environment_root,
        ]
        return CommandSpec("python-sbom", argv, env=self._env(config), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), validation_component="sbom"))

    def _reproducible_spec(self, config: ProjectConfig) -> CommandSpec | None:
        package = self._options(config).get("package", {})
        package = dict(package) if isinstance(package, dict) else {}
        if not _bool_option(package.get("reproducible"), False):
            return None
        info = self._info(config)
        plan = self._plan(config)
        argv = [
            *self._python_support_prefix(config), "reproducible-build",
            str(self._root(config)), "--manager", info.manager, "--framework", info.framework,
            "--python", self._python_support_python_arg(config), "--environment", plan.environment_root,
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
            *self._python_support_prefix(config), "notebook-check",
            str(self._root(config)), "--manager", info.manager, "--framework", info.framework,
            "--python", self._python_support_python_arg(config), "--environment", plan.environment_root,
        ]
        for notebook in info.notebooks:
            argv.extend(["--notebook", notebook])
        if _bool_option(notebook_options.get("execute"), False):
            argv.append("--execute")
        return CommandSpec("python-notebooks", argv, env=self._env(config, isolated=True), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), validation_component="notebooks"))

    def _service_lifecycle_spec(self, config: ProjectConfig) -> CommandSpec | None:
        service = self._options(config).get("service", {})
        service = dict(service) if isinstance(service, dict) else {}
        start = str(service.get("start") or "").strip()
        if not start or not _bool_option(service.get("enabled"), True):
            return None
        argv = [
            *self._python_support_prefix(config), "service-check",
            str(self._root(config)), "--start", start,
            "--timeout", str(_int_option(service.get("timeout"), 60, name="python.service.timeout", minimum=1)),
        ]
        for key, flag in (("url", "--url"), ("host", "--host"), ("check", "--check")):
            value = str(service.get(key) or "").strip()
            if value:
                argv.extend([flag, value])
        service_port = _int_option(service.get("port"), 0, name="python.service.port", minimum=0)
        if service_port > 0:
            argv.extend(["--port", str(service_port)])
        return CommandSpec("python-service-lifecycle", argv, env=self._env(config, isolated=True), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), validation_component="service-lifecycle"))

    def _dependency_check_spec(self, config: ProjectConfig) -> CommandSpec:
        info = self._info(config)
        plan = self._plan(config)
        if info.manager == "uv" and plan.environment_root:
            environment = Path(plan.environment_root)
            python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            argv = ["uv", "pip", "check", "--python", str(python)]
        else:
            argv = self._module(config, "pip", "check")
        return CommandSpec(
            "dependency-check",
            argv,
            env=self._env(config),
            cwd=str(self._root(config)),
            metadata=_tool_metadata(self.kind, self._root(config), validation_component="dependency-integrity"),
        )

    def _database_specs(self, config: ProjectConfig) -> list[CommandSpec]:
        database = self._options(config).get("database", {})
        database = dict(database) if isinstance(database, dict) else {}
        enabled = database.get("enabled", "auto")
        if enabled is False:
            return []
        commands = database.get("commands")
        if isinstance(commands, (list, tuple)):
            return [CommandSpec(f"database:{index}", _shell_split_option(str(command), name="runner command"), env=self._env(config, isolated=True), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), validation_component="database")) for index, command in enumerate(commands, 1) if str(command).strip()]
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
        elif isinstance(security, dict) and _bool_option(security.get("enabled"), False):
            tools = [str(value) for value in security.get("tools", ["pip_audit", "bandit"])]
        else:
            return []
        specs: list[CommandSpec] = []
        info = self._info(config)
        plan = self._plan(config)
        for tool in tools:
            args = ["-r", "."] if tool == "bandit" else []
            argv = [
                *self._python_support_prefix(config), "run-tool",
                "--manager", info.manager, "--framework", info.framework,
                "--python", self._python_support_python_arg(config), "--environment", plan.environment_root,
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
                argv = ["uv", "run", "--no-python-downloads"]
                if _bool_option(options.get("offline"), False):
                    argv.append("--offline")
                argv.extend(["--python", version_text, "python", "-m", "pytest"])
            else:
                executable = shutil.which(f"python{version_text}")
                if not executable:
                    raise SystemExit(
                        f"Requested Python matrix runtime {version_text} is unavailable; "
                        "full matrix execution cannot silently skip requested runtimes."
                    )
                argv = [executable, "-m", info.framework if info.framework in {"pytest", "unittest"} else "pytest"]
                if info.framework == "unittest":
                    argv.append("discover")
            argv = self._network_wrap(config, argv)
            specs.append(CommandSpec(f"test:python-{version_text}", argv, env=self._env(config, isolated=True), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), validation_component="python-matrix", python_version=version_text)))
        return specs

    def build_specs(self, config: ProjectConfig, action: str) -> list[CommandSpec]:
        root = self._root(config)
        options = self._options(config)
        if action == "restore":
            return self._restore_specs(config)
        if action == "build":
            if config.build_command:
                return [CommandSpec("build", _shell_split_option(config.build_command, name="config.build_command"), env=self._env(config), cwd=str(root), metadata=_tool_metadata(self.kind, root, explicit=True, collect_artifacts=True, verify_artifacts=True))]
            outdir = root / "dist"
            specs: list[CommandSpec] = []
            if _bool_option(options.get("fresh_dist"), True):
                specs.append(CommandSpec(
                    "prepare-dist",
                    [*self._python_support_prefix(config), "prepare-dist", str(root)],
                    env=self._env(config), cwd=str(root),
                    metadata=_tool_metadata(self.kind, root, validation_component="package-preparation"),
                ))
            info = self._info(config)
            build_env = self._env(config)
            if info.manager == "uv" and detect_current_environment() == NATIVE_TERMUX:
                # Termux commonly places uv's cache and the project/build
                # destination on filesystems where hardlinks cannot cross.
                # Select copy mode up front instead of letting uv emit its
                # hardlink-fallback warning on every successful build.
                build_env.setdefault("UV_LINK_MODE", "copy")
            if info.manager == "uv":
                # uv-managed projects should use uv's native PEP 517 build
                # frontend. `uv run python -m build` requires the separate
                # PyPA `build` package to be declared in the project
                # environment, which is not a runtime/project dependency.
                argv = ["uv", "build", "--out-dir", str(outdir)]
                if _bool_option(options.get("offline"), False):
                    argv.append("--offline")
            else:
                argv = self._module(config, "build", "--outdir", str(outdir))
            specs.append(CommandSpec("build", argv, env=build_env, cwd=str(root), metadata=_tool_metadata(self.kind, root, collect_artifacts=True, verify_artifacts=True)))
            return specs
        if action in {"test", "wine-test"}:
            if config.test_command:
                custom_argv = self._network_wrap(config, _shell_split_option(config.test_command, name="config.test_command"))
                adapter = str(options.get("test_adapter") or "custom").lower()
                spec = CommandSpec("test", custom_argv, env=self._env(config, isolated=True), cwd=str(root), metadata=_tool_metadata(self.kind, root, explicit=True, test_framework=adapter))
                from .test_selection import apply_test_selection, resolve_test_selection
                selection = resolve_test_selection(config)
                if adapter == "pytest":
                    spec = apply_test_selection(config, spec)
                elif selection.active():
                    raise SystemExit(
                        f"Test selection is not implemented safely for custom Python adapter '{adapter}'; "
                        "configure test_adapter='pytest' or remove the selection request."
                    )
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
            argv = [*self._python_support_prefix(config), "package-check", str(root), "--report", str(report)]
            info = self._info(config)
            if info.manager == "uv":
                argv.extend(["--rebuild-frontend", "uv"])
            flags = {
                "import_smoke": ("--import-smoke", True),
                "install_dependencies": ("--install-dependencies", False),
                "rebuild_sdist": ("--rebuild-sdist", True),
                "verify_cli": ("--verify-cli", True),
                "reproducible": ("--reproducible", False),
                "offline": ("--offline", False),
            }
            for key, (flag, default) in flags.items():
                if _bool_option(package.get(key, options.get(key, default)), default):
                    argv.append(flag)
            if not _bool_option(package.get("verify_all_wheels", True), True):
                argv.append("--first-wheel-only")
            for module in package.get("import_modules", []) if isinstance(package.get("import_modules"), list) else []:
                argv.extend(["--import-module", str(module)])
            for script in package.get("console_scripts", []) if isinstance(package.get("console_scripts"), list) else []:
                argv.extend(["--console-script", str(script)])
            return [CommandSpec(
                "package-check", argv, env=self._env(config), cwd=str(root),
                metadata=_tool_metadata(
                    self.kind, root, report_parser="python-package", package_report=str(report),
                    collect_artifacts=True, verify_artifacts=True, artifact_required=True,
                ),
            )]
        if action == "clean":
            categories = self._list_option(config, "clean_categories") or ["artifacts", "caches"]
            argv = [*self._python_support_prefix(config), "clean", str(root)]
            for category in categories:
                argv.extend(["--category", category])
            return [CommandSpec("clean", argv, env=self._env(config), cwd=str(root), metadata=_tool_metadata(self.kind, root))]
        if action == "run":
            if config.run_command:
                return [CommandSpec("run", _shell_split_option(config.run_command, name="config.run_command"), env=self._env(config), cwd=str(root), metadata=_tool_metadata(self.kind, root, explicit=True))]
            if config.run_module:
                return [CommandSpec("run", self._module(config, config.run_module), env=self._env(config), cwd=str(root), metadata=_tool_metadata(self.kind, root))]
            if config.run_target:
                return [CommandSpec("run", self._script(config, config.resolve_repo_relative(config.run_target)), env=self._env(config), cwd=str(root), metadata=_tool_metadata(self.kind, root))]
            raise SystemExit("No Python run target configured. Set run_command, run_module, or run_target.")
        if action == "package":
            if config.package_command:
                return [CommandSpec(
                    "package", _shell_split_option(config.package_command, name="config.package_command"), env=self._env(config), cwd=str(root),
                    metadata=_tool_metadata(
                        self.kind, root, explicit=True, collect_artifacts=True,
                        verify_artifacts=True, artifact_required=True,
                    ),
                )]
            build_specs = self.build_specs(config, "build")
            # Package is a verified distribution contract. Build artifacts are
            # collected for diagnostics, but immutable registration is deferred
            # until package-check has validated wheel/sdist metadata and clean
            # installation semantics.
            for spec in build_specs:
                if spec.name == "build":
                    spec.metadata["register_artifacts"] = False
                    spec.metadata["copy_artifacts"] = False
                    spec.metadata["artifact_stage"] = "package-build"
            return [*build_specs, *self.build_specs(config, "package-check")]
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
        if skip_restore:
            compatibility = self._runtime_python_compatibility_spec(config)
            if compatibility is not None:
                specs.append(compatibility)
        if scope.scope in {"python-docs-only", "python-unaffected", "python-non-code"}:
            message = "; ".join(scope.reasons) or scope.scope
            specs.append(CommandSpec("python-validation-scope", [sys.executable, "-c", f"print({message!r})"], env=self._env(config), cwd=str(self._root(config)), metadata=_tool_metadata(self.kind, self._root(config), validation_component="scope")))
            _append_final_seal_contract_spec(config, specs, kind=self.kind, root=self._root(config))
            return _validation_metadata(specs, scope.scope)

        if not skip_restore:
            specs.extend(self._restore_specs(config))
        elif self._needs_python_environment_bootstrap(config):
            bootstrap_specs = self._restore_specs(config)
            for spec in bootstrap_specs:
                spec.metadata["implicit_environment_bootstrap"] = True
                spec.metadata["restore_skipped_but_environment_missing"] = True
            specs.extend(bootstrap_specs)
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
        specs.append(self._dependency_check_spec(config))
        specs.extend(self._import_isolation_specs(config))
        specs.extend(self._cli_contract_specs(config))
        if not skip_build and scope.run_build:
            info = self._info(config)
            plan = self._plan(config)
            if info.manager == "venv":
                from .python_support import missing_build_frontend_dependency

                if missing_build_frontend_dependency(info, plan):
                    install_args = ["install"]
                    if _bool_option(self._options(config).get("offline"), False):
                        install_args.append("--no-index")
                    install_args.append("build")
                    specs.append(CommandSpec(
                        "build-frontend",
                        self._module(config, "pip", *install_args),
                        env=self._env(config),
                        cwd=str(self._root(config)),
                        metadata=_tool_metadata(
                            self.kind,
                            self._root(config),
                            validation_component="package-build-frontend",
                            implicit_environment_bootstrap=True,
                        ),
                    ))
            package_build_specs = self.build_specs(config, "build")
            for spec in package_build_specs:
                if spec.name == "build":
                    spec.metadata["register_artifacts"] = False
                    spec.metadata["artifact_stage"] = "validation-package-build"
            specs.extend(package_build_specs)
            specs.extend(self.build_specs(config, "package-check"))
            reproducible = self._reproducible_spec(config)
            if reproducible is not None:
                specs.append(reproducible)
        _append_final_seal_contract_spec(config, specs, kind=self.kind, root=self._root(config))
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

    def _metadata(self, config: ProjectConfig, root: Path, **extra: object) -> dict[str, object]:
        metadata = _tool_metadata(self.kind, root, package_manager=self._manager(config), **extra)
        metadata["progress_parser"] = "npm"
        return metadata

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
            collect_artifacts=True,
            verify_artifacts=True,
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
            spec = CommandSpec(
                action, _shell_split_option(explicit, name="explicit command"), env=env, cwd=str(root),
                metadata=self._metadata(
                    config, root, explicit=True,
                    collect_artifacts=action == "package", verify_artifacts=action == "package",
                    artifact_required=action == "package",
                ),
            )
            if action in {"test", "wine-test"}:
                from .test_selection import apply_test_selection
                spec = apply_test_selection(config, spec)
            return [spec]
        if action == "restore":
            if manager == "npm":
                clean = _bool_option(options.get("clean_install"), False) and (root / "package-lock.json").exists()
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
            return [CommandSpec("restore", argv, env=env, cwd=str(root), metadata=self._metadata(config, root))]
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
                spec = CommandSpec(action.replace("wine-", ""), self._script_argv(config, selected_script), env=env, cwd=str(root), metadata=self._metadata(config, root, node_script=selected_script))
                if action in {"test", "wine-test"}:
                    spec = apply_test_selection(config, spec)
                specs.append(spec)
            return specs
        if action == "run":
            script = "start" if "start" in scripts else "dev" if "dev" in scripts else ""
            if not script:
                raise SystemExit("package.json has no start/dev script and no run_command is configured.")
            return [CommandSpec("run", self._script_argv(config, script), env=env, cwd=str(root), metadata=self._metadata(config, root, node_script=script))]
        if action == "package":
            if manager == "bun":
                argv = ["bun", "pm", "pack", "--quiet"]
            else:
                argv = [manager, "pack"]
            if manager == "npm":
                argv.append("--json")
            return [CommandSpec(
                "package", argv, env=env, cwd=str(root),
                metadata=self._metadata(config, root, collect_artifacts=True, verify_artifacts=True, artifact_required=True),
            )]
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
    capabilities = RunnerCapabilities(restore=True, build=True, test=True, selective_tests=True, validate=True, lint=True, run=True, package=True, clean=True, inspect=True, doctor=True, collect_artifacts=True, verify_artifacts=True)

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
            return [CommandSpec(
                action, _shell_split_option(explicit, name="explicit command"), env=env, cwd=str(root),
                metadata=_tool_metadata(self.kind, root, explicit=True, collect_artifacts=action == "package", verify_artifacts=action == "package", artifact_required=action == "package"),
            )]
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
        spec = CommandSpec(
            action.replace("wine-", ""), argv, env=env, cwd=str(root), destructive=action == "clean",
            metadata=_tool_metadata(self.kind, root, collect_artifacts=action == "package", verify_artifacts=action == "package", artifact_required=action == "package"),
        )
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
        if not skip_test and (not skip_build and _bool_option(options.get("coalesce_validation"), True)):
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
        if _bool_option(options.get("lint"), False):
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
    capabilities = RunnerCapabilities(restore=True, build=True, test=True, selective_tests=True, validate=True, lint=True, run=True, package=True, clean=True, inspect=True, doctor=True, collect_artifacts=True, verify_artifacts=True)

    def _root(self, config: ProjectConfig) -> Path:
        return _project_root(config)

    def _options(self, config: ProjectConfig) -> dict[str, object]:
        value = config.runner_options.get("go", {})
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _string_list(value: object, default: list[str]) -> list[str]:
        if isinstance(value, str):
            return shlex.split(value) or list(default)
        if isinstance(value, (list, tuple)):
            items = [str(item).strip() for item in value if str(item).strip()]
            return items or list(default)
        return list(default)

    def _workspace_use_paths(self, config: ProjectConfig) -> list[str]:
        work_file = self._root(config) / "go.work"
        if not work_file.is_file():
            return []
        values: list[str] = []
        in_use_block = False
        try:
            lines = work_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("//"):
                continue
            if in_use_block:
                if line.startswith(")"):
                    in_use_block = False
                    continue
                token = line.split("//", 1)[0].strip()
                try:
                    parts = shlex.split(token)
                except ValueError:
                    parts = []
                if parts:
                    values.append(parts[0])
                continue
            if not line.startswith("use"):
                continue
            remainder = line[3:].strip()
            if remainder.startswith("("):
                in_use_block = True
                remainder = remainder[1:].strip()
                if not remainder:
                    continue
            remainder = remainder.split("//", 1)[0].strip()
            try:
                parts = shlex.split(remainder)
            except ValueError:
                parts = []
            if parts:
                values.append(parts[0])
        return list(dict.fromkeys(values))

    def _expand_workspace_packages(self, config: ProjectConfig, packages: list[str]) -> list[str]:
        modules = self._workspace_use_paths(config)
        if not modules or "./..." not in packages:
            return packages
        expanded: list[str] = []
        for package in packages:
            if package != "./...":
                expanded.append(package)
                continue
            for module in modules:
                normalized = module.rstrip("/\\")
                expanded.append(f"{normalized}/...")
        return list(dict.fromkeys(expanded)) or packages

    def _packages(self, config: ProjectConfig) -> list[str]:
        packages = self._string_list(self._options(config).get("packages", ["./..."]), ["./..."])
        return self._expand_workspace_packages(config, packages)

    def _build_targets(self, config: ProjectConfig) -> list[str]:
        options = self._options(config)
        value = options.get("build_targets")
        if value is None:
            value = options.get("build_target")
        return self._string_list(value, ["./..."])

    def _output_names(self, config: ProjectConfig) -> dict[str, str]:
        value = self._options(config).get("outputs", {})
        if not isinstance(value, dict):
            return {}
        result: dict[str, str] = {}
        for raw_target, raw_name in value.items():
            target = str(raw_target).strip()
            name = str(raw_name).strip()
            if not target or not name:
                continue
            invalid = (
                name in {".", ".."}
                or name.endswith((".", " "))
                or any(char in name for char in '/\\<>:"|?*')
                or any(ord(char) < 32 for char in name)
            )
            if invalid:
                raise SystemExit(
                    f"Invalid [go.outputs] name for {target}: {name!r}. "
                    "Use a portable executable basename without path separators or control characters."
                )
            result[target] = name
        return result

    def _build_output_dir(self, config: ProjectConfig, action: str) -> Path:
        options = self._options(config)
        key = "package_directory" if action == "package" else "build_directory"
        default = ".devtool/package/go" if action == "package" else ".devtool/build/go"
        path = Path(str(options.get(key) or default)).expanduser()
        if not path.is_absolute():
            path = self._root(config) / path
        return path.resolve()

    def _build_driver(self, config: ProjectConfig, action: str, workers: int) -> str:
        """Return a POSIX-shell Go executable discovery/build driver.

        Discovery happens inside the selected execution environment instead of
        while devtool plans the command. This is important for Termux/chroot and
        remote-compatible execution where the host's Go workspace/toolchain can
        differ from the environment that actually performs the build.
        """
        targets = self._build_targets(config)
        output_names = self._output_names(config)
        output_dir = self._build_output_dir(config, action)
        quoted_targets = " ".join(shlex.quote(item) for item in targets)
        output_map = "\n".join(
            f"resolve_output_name {shlex.quote(target)} {shlex.quote(name)}"
            for target, name in output_names.items()
        )
        package_mode = "1" if action == "package" else "0"
        return f"""set -eu
out={shlex.quote(str(output_dir))}
workers={int(workers)}
package_mode={package_mode}

tmp_base=\"${{TMPDIR:-${{TEMP:-/tmp}}}}/devtool-go-$$\"
candidates=\"$tmp_base.candidates\"
mappings=\"$tmp_base.mappings\"
names=\"$tmp_base.names\"
packages_seen=\"$tmp_base.packages\"
plan=\"$tmp_base.plan\"
staging=\"$tmp_base.out\"
: > \"$candidates\"
: > \"$mappings\"
: > \"$names\"
: > \"$packages_seen\"
: > \"$plan\"
mkdir -p \"$staging\"
trap 'rm -f \"$candidates\" \"$mappings\" \"$names\" \"$packages_seen\" \"$plan\"; rm -rf \"$staging\"' EXIT HUP INT TERM

resolve_output_name() {{
    target=$1
    output_name=$2
    case \"$output_name\" in
        ''|.|..|*/*|*\\\\*)
            echo \"devtool: invalid Go output name for $target: $output_name\" >&2
            exit 2
            ;;
    esac
    import_paths=$(go list -f '{{{{if eq .Name \"main\"}}}}{{{{.ImportPath}}}}{{{{end}}}}' \"$target\")
    import_path=\"\"
    import_count=0
    for candidate_import_path in $import_paths; do
        [ -n \"$candidate_import_path\" ] || continue
        import_count=$((import_count + 1))
        import_path=$candidate_import_path
    done
    if [ \"$import_count\" -ne 1 ]; then
        echo \"devtool: [go.outputs] target must resolve to exactly one package main: $target (found $import_count)\" >&2
        exit 2
    fi
    printf '%s\\t%s\\n' \"$import_path\" \"$output_name\" >> \"$mappings\"
}}

{output_map}

expand_target() {{
    target=$1
    if [ "$target" = "./..." ] && [ -f go.work ]; then
        go list -m -f '{{{{if .Main}}}}{{{{.Dir}}}}/...{{{{end}}}}' all
    else
        printf '%s\\n' "$target"
    fi
}}

for target in {quoted_targets}; do
    expand_target "$target" | while IFS= read -r expanded_target; do
        [ -n "$expanded_target" ] || continue
        go list -f '{{{{if eq .Name \"main\"}}}}{{{{.ImportPath}}}}{{{{\"\\t\"}}}}{{{{.Dir}}}}{{{{end}}}}' "$expanded_target" >> "$candidates"
    done
done

main_count=0
tab=$(printf '\\t')
goos=$(go env GOOS)
while IFS=\"$tab\" read -r pkg dir; do
    [ -n \"$pkg\" ] || continue
    if grep -Fqx -- \"$pkg\" \"$packages_seen\" 2>/dev/null; then
        continue
    fi
    printf '%s\\n' \"$pkg\" >> \"$packages_seen\"

    name=\"\"
    while IFS=\"$tab\" read -r mapped_pkg mapped_name; do
        if [ \"$mapped_pkg\" = \"$pkg\" ]; then
            name=$mapped_name
            break
        fi
    done < \"$mappings\"
    if [ -z \"$name\" ]; then
        name=${{pkg##*/}}
        module_info=$(go list -f '{{{{with .Module}}}}{{{{.Dir}}}}{{{{\"\t\"}}}}{{{{.Path}}}}{{{{end}}}}' \"$pkg\")
        module_dir=\"\"
        module_path=\"\"
        if [ -n \"$module_info\" ]; then
            IFS=\"$tab\" read -r module_dir module_path <<EOF_DEVTOOL_GO_MODULE
$module_info
EOF_DEVTOOL_GO_MODULE
        fi
        if [ -n \"$module_dir\" ] && [ \"$dir\" = \"$module_dir\" ] && [ -n \"$module_path\" ]; then
            name=${{module_path##*/}}
            version_tail=${{name#v}}
            if [ \"$version_tail\" != \"$name\" ] && [ -n \"$version_tail\" ]; then
                case \"$version_tail\" in
                    *[!0-9]*) ;;
                    *)
                        if [ \"$version_tail\" -ge 2 ]; then
                            module_parent=${{module_path%/*}}
                            if [ \"$module_parent\" != \"$module_path\" ]; then
                                name=${{module_parent##*/}}
                            fi
                        fi
                        ;;
                esac
            fi
        fi
    fi
    case \"$name\" in
        ''|.|..|*/*|*\\\\*)
            echo \"devtool: unsafe Go output name for $pkg: $name\" >&2
            exit 2
            ;;
    esac

    if [ \"$goos\" = windows ]; then
        case \"$name\" in
            *.exe|*.exE|*.eXe|*.eXE|*.Exe|*.ExE|*.EXe|*.EXE) ;;
            *) name=\"$name.exe\" ;;
        esac
    fi
    if grep -Fqx -- \"$name\" \"$names\" 2>/dev/null; then
        echo \"devtool: Go executable name collision: $name\" >&2
        echo \"devtool: configure [go.outputs] to give colliding main packages unique names\" >&2
        exit 2
    fi
    printf '%s\\n' \"$name\" >> \"$names\"
    printf '%s\\t%s\\n' \"$pkg\" \"$name\" >> \"$plan\"
    main_count=$((main_count + 1))
done < \"$candidates\"

if [ \"$main_count\" -eq 0 ]; then
    if [ \"$package_mode\" -eq 1 ]; then
        echo \"devtool: Go package found no package main targets; configure [go].build_targets for distributable executables\" >&2
        exit 2
    fi
    # Library-only repositories still receive a complete compilation check.
    if [ -f go.work ]; then
        set --
        for target in {quoted_targets}; do
            if [ "$target" = "./..." ]; then
                while IFS= read -r expanded_target; do
                    [ -n "$expanded_target" ] || continue
                    set -- "$@" "$expanded_target"
                done <<EOF_DEVTOOL_GO_TARGETS
$(expand_target "$target")
EOF_DEVTOOL_GO_TARGETS
            else
                set -- "$@" "$target"
            fi
        done
        exec go build -p "$workers" -v "$@"
    fi
    exec go build -p "$workers" -v {quoted_targets}
fi

# Build every planned executable outside the repository first. This prevents
# Devtool's managed output directory from influencing Go's embedded VCS dirty
# flag and prevents partial final outputs if a later target fails.
while IFS="$tab" read -r pkg name; do
    [ -n "$pkg" ] || continue
    temp_dest="$staging/$name"
    go build -p "$workers" -v -o "$temp_dest" "$pkg"
done < "$plan"

mkdir -p "$out"
publish="$out/.devtool-go-publish-$$"
rm -rf "$publish"
mkdir -p "$publish"
trap 'rm -f "$candidates" "$mappings" "$names" "$packages_seen" "$plan"; rm -rf "$staging" "$publish"' EXIT HUP INT TERM

# Copy all successfully built binaries onto the output filesystem first, then
# replace final paths with same-filesystem renames. Compilation still happens
# outside the repository, so generated outputs cannot make embedded VCS state
# dirty while the binaries are being built.
while IFS="$tab" read -r pkg name; do
    [ -n "$pkg" ] || continue
    cp -p "$staging/$name" "$publish/$name"
done < "$plan"

while IFS="$tab" read -r pkg name; do
    [ -n "$pkg" ] || continue
    dest="$out/$name"
    if [ -d "$dest" ]; then
        echo "devtool: Go output path is an existing directory: $dest" >&2
        exit 2
    fi
    mv -f "$publish/$name" "$dest"
    buildinfo=$(go version -m "$dest") || {{
        echo "devtool: unable to read Go build metadata from $dest" >&2
        exit 3
    }}
    compact_buildinfo=$(printf '%s\n' "$buildinfo" | awk -F '\t' '
        NR == 1 {{ line=$0; sub(/^.*: /, "", line); go_version=line }}
        $2 == "mod" {{ module_path=$3; module_version=$4 }}
        $2 == "build" {{
            split($3, pair, "=")
            key=pair[1]
            value=substr($3, length(key) + 2)
            if (key == "GOOS") goos=value
            else if (key == "GOARCH") goarch=value
            else if (key == "vcs") vcs=value
            else if (key == "vcs.revision") vcs_revision=value
            else if (key == "vcs.time") vcs_time=value
            else if (key == "vcs.modified") vcs_modified=value
        }}
        END {{ printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s", go_version, module_path, module_version, goos, goarch, vcs, vcs_revision, vcs_time, vcs_modified }}
    ')
    printf 'DEVTOOL_GO_ARTIFACT_V2\t%s\t%s\t%s\t%s\n' "$dest" "$pkg" "$name" "$compact_buildinfo"
done < "$plan"
"""

    def build_specs(self, config: ProjectConfig, action: str) -> list[CommandSpec]:
        root = self._root(config)
        env = _optimized_env(config, self.kind)
        workers = resolve_build_policy(config, self.kind).max_workers
        explicit = {"restore": config.restore_command, "build": config.build_command, "test": config.test_command, "wine-test": config.test_command, "run": config.run_command, "package": config.package_command}.get(action, "")
        if explicit:
            return [CommandSpec(
                action, _shell_split_option(explicit, name="explicit command"), env=env, cwd=str(root),
                metadata=_tool_metadata(self.kind, root, explicit=True, collect_artifacts=action == "package", verify_artifacts=action == "package", artifact_required=action == "package"),
            )]
        packages = self._packages(config)
        artifact_action = action in {"build", "package"}
        if action == "restore":
            workspace_modules = self._workspace_use_paths(config)
            if workspace_modules:
                commands = " && ".join(
                    f"(cd {shlex.quote(module)} && go mod download)"
                    for module in workspace_modules
                )
                argv = ["sh", "-c", commands or "true"]
            else:
                argv = ["go", "mod", "download"]
        elif action in {"build", "package"}:
            argv = ["sh", "-c", self._build_driver(config, action, workers)]
        elif action in {"test", "wine-test"}:
            argv = ["go", "test", "-json", "-p", str(workers), *packages]
        elif action == "lint":
            argv = ["go", "vet", *packages]
        elif action == "run":
            target = str(self._options(config).get("run_target") or config.run_target or ".")
            argv = ["go", "run", "-v", target]
        elif action == "clean":
            argv = ["go", "clean", "-cache", "-testcache"]
        else:
            raise SystemExit(f"Unsupported Go action: {action}")
        metadata = _tool_metadata(
            self.kind,
            root,
            collect_artifacts=artifact_action,
            verify_artifacts=artifact_action,
            artifact_required=action == "package",
            go_build_targets=self._build_targets(config) if artifact_action else [],
        )
        if artifact_action:
            metadata["artifact_directories"] = [str(self._build_output_dir(config, action))]
            metadata["artifact_allow_cached"] = False
        if action in {"test", "wine-test"}:
            metadata["progress_parser"] = "go-test-json"
            metadata["go_test_json"] = True
        spec = CommandSpec(action.replace("wine-", ""), argv, env=env, cwd=str(root), destructive=action == "clean", metadata=metadata)
        if action in {"test", "wine-test"}:
            from .test_selection import apply_test_selection
            spec = apply_test_selection(config, spec)
        return [spec]

    def build_validation_specs(self, config: ProjectConfig, *, wine: bool = False, skip_restore: bool = False, skip_build: bool = False, skip_test: bool = False) -> list[CommandSpec]:
        options = self._options(config)
        explicit = any((config.restore_command, config.build_command, config.test_command))
        if explicit:
            return super().build_validation_specs(config, wine=wine, skip_restore=skip_restore, skip_build=skip_build, skip_test=skip_test)

        # Keep validation stages honest and independently diagnosable.  A Go
        # test invocation compiles test packages, but it does not exercise the
        # executable discovery/output/artifact contract implemented by
        # ``devtool build``.  Validation therefore runs restore, real build,
        # tests, and vet as separate phases instead of claiming a coalesced
        # ``go test`` also validated build artifacts.
        specs: list[CommandSpec] = []
        has_module = (self._root(config) / "go.mod").exists() or (self._root(config) / "go.work").exists()
        if not skip_restore and has_module:
            specs.extend(self.build_specs(config, "restore"))
        if not skip_build:
            specs.extend(self.build_specs(config, "build"))
        if not skip_test:
            specs.extend(self.build_specs(config, "test"))
        if _bool_option(options.get("vet"), True):
            specs.extend(self.build_specs(config, "lint"))
        if not specs:
            raise SystemExit("Validation would run no Go commands. Clear a skip flag.")
        return _validation_metadata(specs, "go-incremental")

    def inspect_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        options = self._options(config)
        targets = ", ".join(self._build_targets(config))
        return [
            ("project_root", str(self._root(config))),
            ("project_type", "Go"),
            ("workspace", "go.work" if (self._root(config) / "go.work").exists() else "go.mod"),
            ("build_targets", targets),
            ("build_output", str(self._build_output_dir(config, "build"))),
            ("package_output", str(self._build_output_dir(config, "package"))),
            ("output_overrides", str(len(options.get("outputs", {}))) if isinstance(options.get("outputs", {}), dict) else "0"),
        ]

    def doctor_rows(self, config: ProjectConfig) -> list[tuple[str, str]]:
        return [("go", f"READY: {shutil.which('go')}" if shutil.which("go") else "MISSING")]

class MavenRunner(ProjectRunner):
    kind = "maven"
    display_name = "Maven"
    capabilities = RunnerCapabilities(restore=True, build=True, test=True, selective_tests=True, validate=True, run=False, package=True, clean=True, inspect=True, doctor=True, collect_artifacts=True, verify_artifacts=True)

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
            return [CommandSpec(
                action, _shell_split_option(explicit, name="explicit command"), env=env, cwd=str(root),
                metadata=_tool_metadata(self.kind, root, explicit=True, collect_artifacts=action == "package", verify_artifacts=action == "package", artifact_required=action == "package"),
            )]
        goals = {
            "restore": ["dependency:go-offline"], "build": ["package", "-DskipTests"],
            "test": ["test"], "wine-test": ["test"], "package": ["package"],
            "clean": ["clean"], "verify": ["verify"],
        }.get(action)
        if goals is None:
            raise SystemExit(f"Unsupported Maven action: {action}")
        spec = CommandSpec(
            action.replace("wine-", ""), [*self._base(config), *goals], env=env, cwd=str(root), destructive=action == "clean",
            metadata=_tool_metadata(self.kind, root, collect_artifacts=action == "package", verify_artifacts=action == "package", artifact_required=action == "package"),
        )
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
        if not skip_build and not skip_test and _bool_option(options.get("coalesce_validation"), True):
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
    capabilities = RunnerCapabilities(restore=True, build=True, test=True, selective_tests=True, validate=True, run=False, package=True, clean=True, inspect=True, doctor=True, collect_artifacts=True, verify_artifacts=True)

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

    def _native_termux_context(self, config: ProjectConfig) -> tuple[object | None, bool]:
        policy = resolve_build_policy(config, self.kind)
        native_tool_detection = policy.environment == detect_current_environment()
        runtime = detect_runtime_identity() if native_tool_detection else None
        return runtime, native_tool_detection

    def _cmake_command(self, config: ProjectConfig, tool: str = "cmake") -> str:
        options = self._options(config)
        runtime, _ = self._native_termux_context(config)
        provider = str(options.get("provider") or "auto").strip().lower()
        if provider not in {"auto", "termux", "system"}:
            raise SystemExit("Unsupported CMake provider: " + provider + ". Expected auto, termux, or system")
        if runtime is not None and getattr(runtime, "is_termux", False) and provider in {"auto", "termux"}:
            probe = termux_cmake_probe(runtime=runtime)
            if provider == "termux" and not probe.compatible:
                raise SystemExit(f"Termux CMake provider requested but unavailable: {probe.rejection_reason or probe.reason}")
            if probe.compatible and probe.path:
                if tool == "cmake":
                    return str(probe.path)
                sibling = probe.path.with_name(tool)
                if sibling.is_file() and os.access(sibling, os.X_OK) and (runtime.prefix is None or sibling.resolve(strict=False).is_relative_to(runtime.prefix.resolve(strict=False))):
                    return str(sibling)
                if provider == "termux":
                    raise SystemExit(f"Termux CMake provider requested but executable {tool} is unavailable next to {probe.path}")
        return tool

    def _configure_spec(self, config: ProjectConfig) -> CommandSpec:
        root = self._root(config)
        build_dir = self._build_dir(config)
        options = self._options(config)
        runtime, native_tool_detection = self._native_termux_context(config)
        provider = str(options.get("provider") or "auto").strip().lower()
        cmake_command = self._cmake_command(config)
        ninja_available = bool(shutil.which("ninja"))
        ccache_command = shutil.which("ccache")
        native_compiler_provider = normalize_native_provider(str(options.get("native_provider") or "auto"))
        native_probe = None
        if runtime is not None and runtime.is_termux:
            ninja_probe = termux_ninja_probe(runtime=runtime)
            ccache_probe = termux_ccache_probe(runtime=runtime)
            ninja_available = bool(ninja_probe.compatible and ninja_probe.path)
            ccache_command = str(ccache_probe.path) if ccache_probe.compatible and ccache_probe.path else None
            if native_compiler_provider in {"auto", "termux-sysroot"}:
                native_probe = termux_native_provider(runtime=runtime)
                if native_compiler_provider == "termux-sysroot" and not native_probe.compatible:
                    raise SystemExit(f"Termux native compiler provider requested but unavailable: {native_probe.rejection_reason or native_probe.reason}")
            elif native_compiler_provider == "ndk":
                raise SystemExit("Generic CMake projects cannot select native_provider=ndk without an Android NDK toolchain file; use the Android runner for Android NDK builds")
        elif native_compiler_provider == "termux-sysroot":
            raise SystemExit("Termux native compiler provider requested outside native Termux execution")
        argv = [cmake_command, "-S", str(root), "-B", str(build_dir)]
        generator = str(options.get("generator") or ("Ninja" if native_tool_detection and ninja_available else "")).strip()
        if generator:
            argv.extend(["-G", generator])
        configuration = str(config.variant or options.get("configuration") or "Debug")
        argv.extend([f"-DCMAKE_BUILD_TYPE={configuration}", "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"])
        if native_probe is not None and native_probe.compatible and native_probe.path:
            clang = native_probe.path
            clangxx = clang.with_name("clang++")
            argv.extend([f"-DCMAKE_C_COMPILER={clang}", f"-DCMAKE_CXX_COMPILER={clangxx}"])
        cache_provider = normalize_native_cache_provider(str(options.get("cache") or ("auto" if _bool_option(options.get("ccache"), True) else "off")))
        if cache_provider == "ccache" and not ccache_command:
            raise SystemExit("ccache provider requested but ccache is unavailable in the selected execution environment")
        if cache_provider != "off" and native_tool_detection and ccache_command:
            argv.extend([f"-DCMAKE_C_COMPILER_LAUNCHER={ccache_command}", f"-DCMAKE_CXX_COMPILER_LAUNCHER={ccache_command}"])
        extra = options.get("configure_args", [])
        if isinstance(extra, str):
            argv.extend(_shell_split_option(extra, name="cmake.configure_args"))
        elif isinstance(extra, (list, tuple)):
            argv.extend(str(item) for item in extra)
        return CommandSpec("configure", argv, env=_optimized_env(config, self.kind), cwd=str(root), metadata=_tool_metadata(self.kind, root, build_directory=str(build_dir)))

    def _configured(self, config: ProjectConfig) -> bool:
        root = self._root(config)
        build_dir = self._build_dir(config)
        cache = build_dir / "CMakeCache.txt"
        if not cache.is_file() or not (build_dir / "CMakeFiles").exists():
            return False
        try:
            cache_mtime = cache.stat().st_mtime_ns
        except OSError:
            return False
        for pattern in ("CMakeLists.txt", "**/CMakeLists.txt", "cmake/**/*.cmake"):
            for source in root.glob(pattern):
                if not source.is_file():
                    continue
                try:
                    if source.stat().st_mtime_ns > cache_mtime:
                        return False
                except OSError:
                    return False
        return True

    def build_specs(self, config: ProjectConfig, action: str) -> list[CommandSpec]:
        root = self._root(config)
        build_dir = self._build_dir(config)
        env = _optimized_env(config, self.kind)
        workers = resolve_build_policy(config, self.kind).max_workers
        explicit = {"restore": config.restore_command, "build": config.build_command, "test": config.test_command, "wine-test": config.test_command, "package": config.package_command}.get(action, "")
        if explicit:
            return [CommandSpec(
                action, _shell_split_option(explicit, name="explicit command"), env=env, cwd=str(root),
                metadata=_tool_metadata(self.kind, root, explicit=True, collect_artifacts=action == "package", verify_artifacts=action == "package", artifact_required=action == "package"),
            )]
        configured = self._configured(config)
        if action == "restore":
            return [self._configure_spec(config)]
        if action == "build":
            specs = [] if configured else [self._configure_spec(config)]
            argv = [self._cmake_command(config), "--build", str(build_dir), "--parallel", str(workers)]
            configuration = str(config.variant or self._options(config).get("configuration") or "Debug")
            argv.extend(["--config", configuration])
            specs.append(CommandSpec("build", argv, env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, build_directory=str(build_dir))))
            return specs
        if action in {"test", "wine-test"}:
            argv = [self._cmake_command(config, "ctest"), "--test-dir", str(build_dir), "--parallel", str(workers), "--output-on-failure"]
            spec = CommandSpec("test", argv, env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, build_directory=str(build_dir)))
            from .test_selection import apply_test_selection
            return [apply_test_selection(config, spec)]
        if action == "package":
            package_dir = root / ".devtool" / "package" / "cmake"
            specs = [] if configured else [self._configure_spec(config)]
            specs.append(CommandSpec(
                "package", [self._cmake_command(config, "cpack"), "--config", str(build_dir / "CPackConfig.cmake"), "-B", str(package_dir)],
                env=env, cwd=str(root),
                metadata=_tool_metadata(
                    self.kind, root, build_directory=str(build_dir), artifact_directory=str(package_dir),
                    collect_artifacts=True, verify_artifacts=True, artifact_required=True,
                ),
            ))
            return specs
        if action == "clean":
            return [CommandSpec("clean", [self._cmake_command(config), "--build", str(build_dir), "--target", "clean", "--parallel", str(workers)], env=env, cwd=str(root), destructive=True, metadata=_tool_metadata(self.kind, root, build_directory=str(build_dir)))]
        raise SystemExit(f"Unsupported CMake action: {action}")

    def build_validation_specs(self, config: ProjectConfig, *, wine: bool = False, skip_restore: bool = False, skip_build: bool = False, skip_test: bool = False) -> list[CommandSpec]:
        specs: list[CommandSpec] = []
        configured = self._configured(config)
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
    capabilities = RunnerCapabilities(restore=True, build=True, test=True, selective_tests=True, validate=True, run=False, package=True, clean=True, inspect=True, doctor=True, collect_artifacts=True, verify_artifacts=True)

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
        stamp = build_dir / "meson-private" / "coredata.dat"
        if not ((build_dir / "build.ninja").is_file() and stamp.is_file()):
            return False
        try:
            stamp_mtime = stamp.stat().st_mtime_ns
        except OSError:
            return False
        root = self._root(config)
        for pattern in ("meson.build", "**/meson.build", "meson_options.txt", "**/meson_options.txt"):
            for source in root.glob(pattern):
                if not source.is_file():
                    continue
                try:
                    if source.stat().st_mtime_ns > stamp_mtime:
                        return False
                except OSError:
                    return False
        return True

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
            return [CommandSpec(
                action, _shell_split_option(explicit, name="explicit command"), env=env, cwd=str(root),
                metadata=_tool_metadata(self.kind, root, explicit=True, collect_artifacts=action == "package", verify_artifacts=action == "package", artifact_required=action == "package"),
            )]
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
            specs = [] if self._configured(config) else [self._setup(config)]
            specs.append(CommandSpec(
                "package", ["meson", "dist", "-C", str(build_dir), "--no-tests"], env=env, cwd=str(root),
                metadata=_tool_metadata(
                    self.kind, root, build_directory=str(build_dir), collect_artifacts=True,
                    verify_artifacts=True, artifact_required=True,
                ),
            ))
            return specs
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
            return [CommandSpec(action, _shell_split_option(explicit, name="explicit command"), env=env, cwd=str(root), metadata=_tool_metadata(self.kind, root, explicit=True))]
        targets = self._targets(config)
        target = ""
        if action == "build":
            target = str(self._options(config).get("build_target") or "")
        elif action in {"test", "wine-test"}:
            from .test_selection import resolve_test_selection
            selected = resolve_test_selection(config)
            unsupported_selection = any((
                selected.selectors, selected.ids, selected.tags, selected.exclude_tags,
                selected.paths, selected.classes, selected.methods, selected.native_filter,
            ))
            if unsupported_selection:
                raise SystemExit(
                    "Make test selection supports only Make targets through test_selection.tasks; "
                    "generic selectors cannot be translated safely."
                )
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
            _shell_split_option(text, name="command"),
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
        if isinstance(config_or_kind, ProjectConfig):
            if str(config_or_kind.build_system).lower() == "gradle":
                kind = "android" if str(config_or_kind.project_subtype).lower() == "android" else "gradle"
            else:
                kind = config_or_kind.runner or config_or_kind.kind
        else:
            kind = str(config_or_kind)
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
