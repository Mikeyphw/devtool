from __future__ import annotations

import json
import tempfile
import sys
import unittest
from io import StringIO
from unittest import mock
from pathlib import Path
import zipfile

from devtool.models import ProjectConfig
from devtool.python_support import (
    PythonProjectError,
    audit_source_tree,
    check_notebooks,
    check_service_lifecycle,
    clean_python,
    declared_group_dependencies,
    default_python_cache_root,
    default_python_environment_root,
    discover_python_roots,
    ensure_hatch_environment,
    ensure_venv_environment,
    inspect_python_project,
    python_cache_env,
    run_tool,
    python_validation_scope,
    prepare_dist,
    resolve_environment,
    verify_packages,
    write_environment_state,
)
from devtool.runners import build_specs, build_validation_specs, run_specs
from devtool.tasker import TaskerProgressSink


class PythonSupportTests(unittest.TestCase):
    def test_lock_file_selects_existing_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
            (root / "poetry.lock").write_text("", encoding="utf-8")
            info = inspect_python_project(root)

        self.assertEqual(info.manager, "poetry")
        self.assertEqual(info.lock_file, "poetry.lock")

    def test_wheel_verification_requires_dist_info_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            wheel = dist / "demo-1-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("demo/__init__.py", "")
                archive.writestr("demo-1.dist-info/METADATA", "Metadata-Version: 2.1\nName: demo\nVersion: 1\n")
                archive.writestr("demo-1.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: devtool-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
                archive.writestr(
                    "demo-1.dist-info/RECORD",
                    "demo/__init__.py,,\n"
                    "demo-1.dist-info/METADATA,,\n"
                    "demo-1.dist-info/WHEEL,,\n"
                    "demo-1.dist-info/RECORD,,\n",
                )
            payload, rc = verify_packages(root)

        self.assertEqual(rc, 0)
        self.assertEqual(payload["valid"], 1)

    def test_python_validation_runs_test_lint_build_and_package_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[project]\nname='demo'\nversion='1'\n[tool.ruff]\n",
                encoding="utf-8",
            )
            (root / "tests").mkdir()
            (root / "tests" / "test_demo.py").write_text("def test_demo(): pass\n", encoding="utf-8")
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="python",
                runner_options={"python": {"manager": "uv", "lint": ["ruff"]}},
            )
            specs = build_validation_specs(config)

        names = [spec.name for spec in specs]
        self.assertEqual(
            names,
            [
                "restore", "python-environment", "python-source-audit", "test",
                "lint:ruff", "dependency-check", "prepare-dist", "build", "package-check",
            ],
        )
        test_spec = next(spec for spec in specs if spec.name == "test")
        self.assertEqual(test_spec.metadata["report_parser"], "pytest-junit")
        self.assertEqual(specs[-1].metadata["report_parser"], "python-package")

    def test_ensure_venv_reports_missing_pip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "venv"
            python = env / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text("#!/bin/sh\n", encoding="utf-8")
            python.chmod(0o755)
            failed = mock.Mock(returncode=1, stdout="No module named ensurepip")
            with mock.patch("devtool.python_support._run_capture", return_value=failed):
                with self.assertRaises(PythonProjectError) as raised:
                    ensure_venv_environment(env)
        self.assertIn("does not provide pip", str(raised.exception))

    def test_python_explicit_build_command_publishes_live_progress_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="python",
                build_command=f"{sys.executable} -c \"import time; print('python step 1'); time.sleep(0.15); print('python step 2')\"",
            )
            sink = TaskerProgressSink(
                directory=root / "hud",
                command="build",
                repo_root=str(root),
                repo_name="demo",
                enabled=True,
                update_interval_s=0,
            )
            sink.started()
            specs = build_specs(config, "build")
            results, _ = run_specs(config, specs, verbose=False, tasker_progress=sink)
            events = [json.loads(line) for line in (root / "hud" / "events.jsonl").read_text(encoding="utf-8").splitlines()]

        self.assertEqual(results[0].returncode, 0)
        progress = [event for event in events if event.get("event_type") == "progress"]
        self.assertTrue(any("python step 1" in event.get("message", "") for event in progress))
        self.assertTrue(any("python step 2" in event.get("message", "") for event in progress))


    def test_invalid_pyproject_is_reported_instead_of_swallowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project\nname='broken'\n", encoding="utf-8")
            with self.assertRaises(PythonProjectError) as raised:
                inspect_python_project(root)
        self.assertIn("Invalid TOML", str(raised.exception))

    def test_invalid_manager_and_framework_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
            with self.assertRaises(PythonProjectError):
                inspect_python_project(root, "magic-env")
            with self.assertRaises(PythonProjectError):
                inspect_python_project(root, "venv", "nose")

    def test_configured_unittest_framework_is_honored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_demo.py").write_text("import pytest\n", encoding="utf-8")
            info = inspect_python_project(root, "venv", "unittest")
        self.assertEqual(info.framework, "unittest")

    def test_venv_lint_uses_python_module_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="python",
                runner_options={"python": {"manager": "venv", "lint": ["ruff"]}},
            )
            specs = build_specs(config, "lint")
        argv = specs[0].argv
        self.assertIn("run-tool", argv)
        self.assertEqual(argv[-2:], ["check", "."])
        self.assertNotIn("python ruff", " ".join(argv))

    def test_ruff_findings_are_reported_as_warnings_without_failing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as xdg:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
            (root / "uv.lock").write_text("", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "demo.py").write_text("import importlib\n", encoding="utf-8")
            proc = mock.Mock(
                returncode=1,
                stdout=(
                    "F401 [*] `importlib` imported but unused\n"
                    " --> src/demo.py:1:1\n"
                    "help: Remove unused import: `importlib`\n"
                    "Found 1 error.\n"
                ),
            )
            args = mock.Mock(
                root=root,
                manager="auto",
                framework="auto",
                python="python3",
                environment="",
                timeout=30,
                tool="ruff",
                tool_args=("check", "."),
            )
            with mock.patch.dict("os.environ", {"DEVTOOL_XDG_DIR": xdg}, clear=False), \
                mock.patch("devtool.python_support._run_capture", return_value=proc), \
                mock.patch("sys.stdout", new=StringIO()) as stdout:
                rc = run_tool(args)

        self.assertEqual(rc, 0)
        self.assertIn('"warnings": 1', stdout.getvalue())

    def test_package_command_is_honored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="python", package_command="python custom_package.py")
            spec = build_specs(config, "package")[0]
        self.assertEqual(spec.argv, ["python", "custom_package.py"])

    def test_affected_python_scope_maps_source_to_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
            (root / "src" / "demo").mkdir(parents=True)
            (root / "src" / "demo" / "parser.py").write_text("VALUE=1\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_parser.py").write_text("def test_it(): pass\n", encoding="utf-8")
            info = inspect_python_project(root, "venv")
            scope = python_validation_scope(root, ["src/demo/parser.py"], info)
        self.assertEqual(scope.scope, "python-affected")
        self.assertEqual(scope.lint_targets, ("src/demo/parser.py",))
        self.assertEqual(scope.test_targets, ("tests/test_parser.py",))
        self.assertFalse(scope.full_tests)

    def test_shared_python_configuration_forces_full_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
            info = inspect_python_project(root, "venv")
            scope = python_validation_scope(root, ["pyproject.toml"], info)
        self.assertEqual(scope.scope, "python-full")
        self.assertTrue(scope.full_tests)
        self.assertTrue(scope.run_build)

    def test_workspace_roots_are_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("api", "worker"):
                project = root / name
                project.mkdir()
                (project / "pyproject.toml").write_text(f"[project]\nname='{name}'\nversion='1'\n", encoding="utf-8")
            roots = discover_python_roots(root)
        self.assertEqual([path.name for path in roots], ["api", "worker"])

    def test_source_audit_detects_stdlib_shadowing_and_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='2.0'\n", encoding="utf-8")
            (root / "json.py").write_text("VALUE=1\n", encoding="utf-8")
            (root / "demo.py").write_text("__version__='1.0'\n", encoding="utf-8")
            info = inspect_python_project(root, "venv")
            payload, rc = audit_source_tree(root, info)
        self.assertEqual(rc, 1)
        codes = {item["code"] for item in payload["issues"]}
        self.assertIn("STDLIB_SHADOW", codes)
        self.assertIn("VERSION_MISMATCH", codes)

    def test_safe_cleanup_does_not_enter_nested_project_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
            (root / "build").mkdir()
            nested = root / "fixtures" / "nested"
            nested.mkdir(parents=True)
            (nested / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='1'\n", encoding="utf-8")
            (nested / "build").mkdir()
            clean_python(root)
            self.assertFalse((root / "build").exists())
            self.assertTrue((nested / "build").exists())

    def test_requires_python_is_checked_against_resolved_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\nrequires-python='>=99'\n", encoding="utf-8")
            info = inspect_python_project(root, "venv")
            plan = resolve_environment(info, configured_python=sys.executable, environment=str(root / ".venv"))
        self.assertFalse(plan.compatible)

    def test_implicit_venv_environment_uses_xdg_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as xdg:
            root = Path(tmp) / "demo"
            root.mkdir()
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {"DEVTOOL_XDG_DIR": xdg}, clear=False):
                info = inspect_python_project(root, "venv")
                plan = resolve_environment(info, configured_python=sys.executable)
                expected = default_python_environment_root(root)
        self.assertEqual(Path(plan.environment_root), expected)
        self.assertTrue(str(plan.environment_root).startswith(str(Path(xdg) / "python" / "venvs")))
        self.assertFalse(str(plan.environment_root).startswith(str(root)))

    def test_implicit_uv_environment_uses_xdg_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as xdg:
            root = Path(tmp) / "demo"
            root.mkdir()
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
            (root / "uv.lock").write_text("", encoding="utf-8")
            with mock.patch.dict("os.environ", {"DEVTOOL_XDG_DIR": xdg}, clear=False):
                info = inspect_python_project(root, "auto")
                plan = resolve_environment(info, configured_python=sys.executable)
                expected = default_python_environment_root(root)
        self.assertEqual(info.manager, "uv")
        self.assertEqual(Path(plan.environment_root), expected)
        self.assertTrue(str(plan.environment_root).startswith(str(Path(xdg) / "python" / "venvs")))
        self.assertFalse(str(plan.environment_root).startswith(str(root)))

    def test_explicit_relative_venv_environment_stays_repo_relative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as xdg:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {"DEVTOOL_XDG_DIR": xdg}, clear=False):
                info = inspect_python_project(root, "venv")
                plan = resolve_environment(info, configured_python=sys.executable, environment=".venv")
        self.assertEqual(Path(plan.environment_root), (root / ".venv").resolve())

    def test_python_cache_and_state_use_xdg_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as xdg:
            root = Path(tmp) / "demo"
            root.mkdir()
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {"DEVTOOL_XDG_DIR": xdg}, clear=False):
                info = inspect_python_project(root, "venv")
                plan = resolve_environment(info, configured_python=sys.executable)
                env = python_cache_env(root, "chroot")
                state = write_environment_state(info, plan)
                expected_cache = default_python_cache_root(root, "chroot")
        self.assertEqual(Path(env["PIP_CACHE_DIR"]).parent, expected_cache)
        self.assertTrue(str(state).startswith(str(Path(xdg) / "python" / "environments")))
        self.assertFalse(str(state).startswith(str(root)))

    def test_hatch_environment_create_is_idempotent_when_default_exists(self) -> None:
        completed = mock.Mock(returncode=1, stdout="Environment `default` already exists\n")
        find = mock.Mock(returncode=1, stdout="")
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("devtool.python_support.shutil.which", return_value="/usr/bin/hatch"),
            mock.patch("devtool.python_support.subprocess.run", side_effect=[completed, find]),
        ):
            rc = ensure_hatch_environment(Path(tmp))
        self.assertEqual(rc, 0)

    def test_hatch_environment_installs_hatch_when_command_is_missing(self) -> None:
        install = mock.Mock(returncode=0, stdout="installed hatch\n")
        create = mock.Mock(returncode=0, stdout="created\n")
        find = mock.Mock(returncode=1, stdout="")
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("devtool.python_support.shutil.which", return_value=None),
            mock.patch("devtool.python_support.subprocess.run", side_effect=[install, create, find]) as run,
        ):
            rc = ensure_hatch_environment(Path(tmp))
        self.assertEqual(rc, 0)
        self.assertEqual(run.call_args_list[0].args[0], [sys.executable, "-m", "pip", "install", "hatch"])
        self.assertEqual(run.call_args_list[1].args[0], [sys.executable, "-m", "hatch", "env", "create"])

    def test_hatch_environment_bootstraps_build_backend_and_dev_extra(self) -> None:
        create = mock.Mock(returncode=1, stdout="Environment `default` already exists\n")
        find = mock.Mock(returncode=0, stdout="/tmp/hatch-env\n")
        install_backend = mock.Mock(returncode=0, stdout="installed hatchling\n")
        install_project = mock.Mock(returncode=0, stdout="installed project\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[build-system]\nrequires=['hatchling>=1.27']\n"
                "[project]\nname='demo'\nversion='1'\n"
                "[project.optional-dependencies]\ndev=['pytest>=8']\n",
                encoding="utf-8",
            )
            with (
                mock.patch("devtool.python_support.shutil.which", return_value="/usr/bin/hatch"),
                mock.patch("devtool.python_support.Path.is_file", return_value=True),
                mock.patch("devtool.python_support.subprocess.run", side_effect=[create, find, install_backend, install_project]) as run,
            ):
                rc = ensure_hatch_environment(root, framework="pytest")
        self.assertEqual(rc, 0)
        self.assertEqual(run.call_args_list[2].args[0][-2:], ["hatchling>=1.27", "editables"])
        self.assertEqual(run.call_args_list[3].args[0][-1], ".[dev]")

    def test_hatch_ignores_stale_repo_local_environment_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[tool.hatch]\n", encoding="utf-8")
            info = inspect_python_project(root, "hatch")
            plan = resolve_environment(
                info,
                configured_python=sys.executable,
                environment=str(root / ".devtool" / "venvs" / "python"),
            )
        self.assertEqual(plan.manager, "hatch")
        self.assertEqual(plan.environment_root, "")
        self.assertEqual(plan.run_prefix, ("hatch", "run"))

    def test_wheel_record_digest_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            wheel = dist / "demo-1-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("demo/__init__.py", "x = 1\n")
                archive.writestr("demo-1.dist-info/METADATA", "Metadata-Version: 2.1\nName: demo\nVersion: 1\n")
                archive.writestr("demo-1.dist-info/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
                archive.writestr(
                    "demo-1.dist-info/RECORD",
                    "demo/__init__.py,sha256=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA,6\n"
                    "demo-1.dist-info/METADATA,,\n"
                    "demo-1.dist-info/WHEEL,,\n"
                    "demo-1.dist-info/RECORD,,\n",
                )
            payload, rc = verify_packages(root)
        self.assertEqual(rc, 1)
        self.assertEqual(payload["invalid"], 1)
        self.assertIn("RECORD digest mismatch", payload["items"][0]["checks"][0]["detail"])

    def test_import_graph_maps_nonmatching_test_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
            (root / "src" / "demo").mkdir(parents=True)
            (root / "src" / "demo" / "engine.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_runtime_contract.py").write_text("from demo import engine\n", encoding="utf-8")
            info = inspect_python_project(root)
            scope = python_validation_scope(root, ["src/demo/engine.py"], info)
        self.assertEqual(scope.test_targets, ("tests/test_runtime_contract.py",))
        self.assertFalse(scope.full_tests)

    def test_validator_groups_support_pep735_poetry_and_pdm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[project]\nname='demo'\nversion='1'\n"
                "[dependency-groups]\ntest=['pytest>=8']\n"
                "[tool.poetry.group.test.dependencies]\ncoverage='>=7'\n"
                "[tool.pdm.dev-dependencies]\ntest=['hypothesis>=6']\n",
                encoding="utf-8",
            )
            dependencies = declared_group_dependencies(root, ["test"])
        self.assertEqual(dependencies, ["pytest>=8", "coverage>=7", "hypothesis>=6"])

    def test_prepare_dist_removes_stale_artifacts_only_when_executed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            stale = dist / "stale.whl"
            stale.write_text("old", encoding="utf-8")
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="python")
            specs = build_specs(config, "build")
            self.assertTrue(stale.exists())
            self.assertEqual(specs[0].name, "prepare-dist")
            prepared = prepare_dist(root)
            self.assertEqual(prepared, dist)
            self.assertFalse(stale.exists())

    def test_notebook_execution_uses_disposable_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notebook = root / "analysis.ipynb"
            original = json.dumps({"cells": [{"cell_type": "code", "execution_count": 1, "source": ["1+1"]}]})
            notebook.write_text(original, encoding="utf-8")
            info = inspect_python_project(root)
            plan = resolve_environment(info, configured_python=sys.executable)
            completed = mock.Mock(returncode=0, stdout="")
            with mock.patch("devtool.python_support._run_capture", return_value=completed) as run:
                payload, rc = check_notebooks(root, ["analysis.ipynb"], execute=True, plan=plan)
            self.assertEqual(rc, 0)
            self.assertEqual(payload["executed"], 1)
            self.assertEqual(notebook.read_text(encoding="utf-8"), original)
            command = run.call_args.args[0]
            self.assertNotIn("--inplace", command)

    def test_service_lifecycle_terminates_before_collecting_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = f"{sys.executable} -c \"import time; print('service-ready', flush=True); time.sleep(30)\""
            payload, rc = check_service_lifecycle(root, start_command=command, timeout=5)
        self.assertEqual(rc, 0)
        self.assertTrue(payload["ready"])
        self.assertIn("service-ready", payload["output"])
