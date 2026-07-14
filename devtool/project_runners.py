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
import os
import shlex
import shutil
import sys
from typing import Iterable

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
        env = config.env
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
            return _maybe_explicit("restore", config.restore_command, env) or [
                CommandSpec("restore", ["dotnet", "restore", restore_target], env=env)
            ]
        if action == "build":
            return _maybe_explicit("build", config.build_command, env) or [
                CommandSpec("build", ["dotnet", "build", build_target], env=env)
            ]
        if action in {"test", "wine-test"}:
            from .test_selection import apply_test_selection
            name = "wine-test" if action == "wine-test" else "test"
            specs = _maybe_explicit(name, config.test_command, env) or [
                CommandSpec(name, ["dotnet", "test", test_target], env=env)
            ]
            return [apply_test_selection(config, spec) for spec in specs]
        if action == "run":
            return _maybe_explicit("run", config.run_command, env) or [
                CommandSpec("run", ["dotnet", "run", "--project", run_target], env=env)
            ]
        if action == "clean":
            return [CommandSpec("clean", ["dotnet", "clean", build_target], env=env)]
        if action == "package":
            if config.package_command:
                return [CommandSpec("package", shlex.split(config.package_command), env=env)]
            if (config.repo_root / "scripts" / "package-release.sh").exists():
                return [CommandSpec("package", ["bash", "./scripts/package-release.sh"], env=env)]
            raise SystemExit("No package command configured.")
        raise SystemExit(f"Unsupported action for dotnet: {action}")

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
                "--variant", str(android.get("variant") or "debug"),
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
                "instrumentation", argv, env=config.env, cwd=str(root),
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
            specs = _maybe_explicit(action, explicit, config.env)
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
                env=config.env,
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
        specs: list[CommandSpec] = []
        if not skip_restore:
            specs.extend(self.build_specs(config, "restore"))
        if not skip_build:
            specs.extend(self.build_specs(config, "build"))
        if not skip_test:
            specs.extend(self.build_specs(config, "test"))

        # Android Lint is part of the default Android validation gate. Pure JVM
        # Gradle projects only run lint when the target explicitly names a task.
        from .android_integration import configured_tasks
        if configured_tasks(config, "lint"):
            specs.extend(self.build_specs(config, "lint"))

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
            env=config.env,
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
                f"lint:{tool}", argv, env=config.env, cwd=str(self._root(config)),
                metadata={"runner": self.kind, "project_type": self.kind, "lint_tool": tool},
            ))
        return specs

    def build_specs(self, config: ProjectConfig, action: str) -> list[CommandSpec]:
        env = config.env
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
            specs = [CommandSpec("venv", [config.python_executable, "-m", "venv", venv_root], env=env, cwd=str(root))]
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
        return [CommandSpec(action, shlex.split(text), env=config.env, metadata={"explicit": True})]


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
    [DotnetRunner(), AndroidGradleRunner(), JvmGradleRunner(), PythonRunner()],
    fallback=GenericCommandRunner(),
)


def get_project_runner(config_or_kind: ProjectConfig | str) -> ProjectRunner:
    return DEFAULT_RUNNER_REGISTRY.resolve(config_or_kind)
