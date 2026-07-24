from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

from devtool.cli import _config_for_validation_planning
from devtool.models import ProjectConfig
from devtool.project_runners import PythonRunner
from devtool.python_support import build_sync_command, inspect_python_project, resolve_environment


class PythonChrootEnvironmentConsistencyTests(unittest.TestCase):
    def test_validation_planning_marks_chroot_before_specs_are_built(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(repo_root=Path(tmp), kind="python", execution_environment="auto", chroot_name="androidos")
            args = argparse.Namespace(
                chroot="androidos",
                host=False,
                execution_environment="auto",
                instrumented=False,
                no_auto_chroot=False,
                _devtool_chroot_name="",
                _devtool_chroot_path_map=None,
            )
            planned = _config_for_validation_planning(config, args)
        self.assertEqual(planned.execution_environment, "chroot")
        self.assertEqual(planned.chroot_name, "androidos")

    def test_chroot_plan_uses_guest_bootstrap_python_not_termux_host_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[build-system]\nrequires=['setuptools>=68','wheel']\n"
                "[project]\nname='demo'\nversion='0.1.0'\n",
                encoding="utf-8",
            )
            info = inspect_python_project(root, "venv", "pytest")
            plan = resolve_environment(
                info,
                configured_python="/data/data/com.termux/files/usr/bin/python3.14",
                environment=str(root / ".devtool" / "venv"),
                execution_environment="chroot",
            )
        self.assertEqual(plan.bootstrap_python, "python3.14")

    def test_venv_restore_installs_build_system_then_project_without_build_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[build-system]\nrequires=['setuptools>=68','wheel']\n"
                "[project]\nname='demo'\nversion='0.1.0'\n",
                encoding="utf-8",
            )
            info = inspect_python_project(root, "venv", "pytest")
            plan = resolve_environment(info, environment=str(root / ".devtool" / "venv"), execution_environment="chroot")
            commands = build_sync_command(info, plan, install_project=True)
        joined = [" ".join(command) for command in commands]
        self.assertTrue(any("-m venv" in item for item in joined))
        self.assertTrue(any("setuptools>=68" in item and "wheel" in item for item in joined))
        self.assertTrue(any("--no-build-isolation" in item and " -e ." in item for item in joined))

    def test_restore_phase_names_distinguish_build_system_and_project(self) -> None:
        runner = PythonRunner()
        self.assertEqual(runner._restore_phase_name(["python3", "-m", "venv", "/tmp/env"]), "venv")
        self.assertEqual(runner._restore_phase_name(["/tmp/env/bin/python", "-m", "pip", "install", "pytest"]), "restore:test-tools")
        self.assertEqual(runner._restore_phase_name(["/tmp/env/bin/python", "-m", "pip", "install", "setuptools>=68", "wheel"]), "restore:build-system")
        self.assertEqual(runner._restore_phase_name(["/tmp/env/bin/python", "-m", "pip", "install", "--no-build-isolation", "-e", "."]), "restore:project")


if __name__ == "__main__":
    unittest.main()

class PythonChrootVenvBootstrapRegressionTests(unittest.TestCase):
    def test_chroot_restore_always_emits_venv_bootstrap_even_if_host_path_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[build-system]\nrequires=['setuptools>=68','wheel']\n"
                "[project]\nname='demo'\nversion='0.1.0'\n",
                encoding="utf-8",
            )
            env_root = root / ".devtool" / "venv"
            (env_root / "bin").mkdir(parents=True)
            (env_root / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
            info = inspect_python_project(root, "venv", "pytest")
            plan = resolve_environment(info, environment=str(env_root), execution_environment="chroot")
            commands = build_sync_command(info, plan, install_project=True)
        self.assertEqual(commands[0][1:3], ["-m", "venv"])
        self.assertEqual(commands[0][-1], str(env_root))
        self.assertTrue(any(command[-1] == "pytest" for command in commands))

    def test_chroot_bootstrap_preserves_configured_python_version_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[project]\nname='demo'\nversion='0.1.0'\n",
                encoding="utf-8",
            )
            info = inspect_python_project(root, "venv", "pytest")
            plan = resolve_environment(
                info,
                configured_python="/data/data/com.termux/files/usr/bin/python3.14",
                environment=str(root / ".devtool" / "venv"),
                execution_environment="chroot",
            )
        self.assertEqual(plan.bootstrap_python, "python3.14")
        commands = build_sync_command(info, plan, install_project=True)
        self.assertEqual(commands[0][0], "python3.14")
