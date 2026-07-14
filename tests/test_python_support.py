from __future__ import annotations

import json
import tempfile
import sys
import unittest
from pathlib import Path
import zipfile

from devtool.models import ProjectConfig
from devtool.python_support import inspect_python_project, verify_packages
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
                archive.writestr("demo-1.dist-info/RECORD", "")
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
        self.assertEqual(names, ["restore", "test", "lint:ruff", "build", "package-check"])
        self.assertEqual(specs[1].metadata["report_parser"], "pytest-junit")
        self.assertEqual(specs[-1].metadata["report_parser"], "python-package")

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
