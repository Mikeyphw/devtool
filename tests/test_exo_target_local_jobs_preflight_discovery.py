
from __future__ import annotations

import io
import json
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from devtool.cli import _jobs, _targets
from devtool.config import load_project_config, resolve_config_dict
from devtool.toolchain_requirements import analyze_toolchain_requirements
from devtool.workflow_runtime import build_workspace_specs
from devtool.workflow_scheduler import build_execution_plan_from_specs


class ExoTargetLocalJobsPreflightDiscoveryTests(unittest.TestCase):
    def _repo(self, config_text: str) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / ".devtool.toml").write_text(textwrap.dedent(config_text), encoding="utf-8")
        return temp, root

    def test_target_local_job_default_root_is_target_root_in_preflight(self) -> None:
        temp, root = self._repo(
            """
            [project]
            runner = "command"
            default_target = "dustui_android"

            [targets.dustui_android]
            runner = "command"
            platform = "android"
            root = "android/DustUI"

            [targets.dustui_android.jobs.contracts]
            runner = "command"
            command = ["python3", "scripts/check_contracts.py"]
            inputs = ["scripts/check_contracts.py"]
            outputs = ["build/evidence/contracts.json"]

            [targets.dustui_android.workflows]
            fast = ["job:contracts"]
            """
        )
        with temp:
            target_root = root / "android" / "DustUI"
            (target_root / "scripts").mkdir(parents=True)
            (target_root / "scripts" / "check_contracts.py").write_text("print('ok')\n", encoding="utf-8")
            cfg = load_project_config(str(root), None)
            specs = build_workspace_specs(cfg, ["dustui_android"], "fast")
            report = analyze_toolchain_requirements(cfg, ["dustui_android"], workflow="fast")

        self.assertEqual(Path(specs[0].cwd), target_root)
        root_checks = [item for item in report.checks if item.section == "root" and item.name == "job:dustui_android.contracts"]
        self.assertEqual(len(root_checks), 1)
        self.assertEqual(root_checks[0].status, "ready")
        self.assertEqual(Path(root_checks[0].detail), target_root)
        output_checks = [item for item in report.checks if item.section == "output" and item.name == "job:dustui_android.contracts:build/evidence/contracts.json"]
        self.assertEqual(len(output_checks), 1)
        self.assertEqual(output_checks[0].status, "ready")
        self.assertEqual(output_checks[0].detail, "android/DustUI/build/evidence/contracts.json")

    def test_target_local_jobs_are_reported_in_config_jobs_targets_and_cli_jobs(self) -> None:
        temp, root = self._repo(
            """
            [project]
            runner = "command"
            default_target = "app"

            [targets.app]
            runner = "command"
            root = "app"

            [targets.app.jobs.contracts]
            runner = "command"
            command = ["python3", "contracts.py"]

            [targets.app.workflows]
            fast = ["job:contracts"]
            """
        )
        with temp:
            (root / "app").mkdir()
            cfg = load_project_config(str(root), None)
            resolved = resolve_config_dict(cfg)
            out = io.StringIO()
            with redirect_stdout(out):
                _jobs(cfg, as_json=True)
            jobs_payload = json.loads(out.getvalue())
            out = io.StringIO()
            with redirect_stdout(out):
                _targets(cfg, as_json=True)
            targets_payload = json.loads(out.getvalue())

        self.assertEqual(resolved["jobs"]["app.contracts"]["scope"], "target")
        self.assertEqual(resolved["jobs"]["app.contracts"]["target"], "app")
        self.assertEqual(resolved["jobs"]["app.contracts"]["local_name"], "contracts")
        self.assertEqual(resolved["targets"]["app"]["jobs"]["contracts"]["job_key"], "app.contracts")
        self.assertIn("app.contracts", jobs_payload["jobs"])
        self.assertIn("contracts", jobs_payload["target_jobs"]["app"])
        self.assertEqual(jobs_payload["target_jobs"]["app"]["contracts"]["target"], "app")
        self.assertEqual(targets_payload["targets"][0]["jobs"], ["contracts"])

    def test_target_local_output_scope_feeds_safe_scheduler_reservations(self) -> None:
        temp, root = self._repo(
            """
            [project]
            runner = "command"
            default_target = "app"

            [targets.app]
            runner = "command"
            root = "app"

            [targets.app.jobs.a]
            runner = "command"
            command = ["python3", "-c", "print('a')"]
            inputs = ["src/a.txt"]
            outputs = ["build/a/**"]

            [targets.app.jobs.b]
            runner = "command"
            command = ["python3", "-c", "print('b')"]
            inputs = ["src/b.txt"]
            outputs = ["build/b/**"]

            [targets.app.workflows]
            fast = [
              { id = "a", ref = "job:a", depends_on = [] },
              { id = "b", ref = "job:b", depends_on = [] },
            ]
            """
        )
        with temp:
            (root / "app" / "src").mkdir(parents=True)
            (root / "app" / "src" / "a.txt").write_text("a", encoding="utf-8")
            (root / "app" / "src" / "b.txt").write_text("b", encoding="utf-8")
            cfg = load_project_config(str(root), None)
            specs = build_workspace_specs(cfg, ["app"], "fast")
            plan = build_execution_plan_from_specs(cfg, specs, parallel_requested=True, max_parallel_nodes=2)

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertTrue(plan.reservation_paths_known)
        self.assertEqual(set(plan.reservation_paths), {"app/src/a.txt", "app/build/a", "app/src/b.txt", "app/build/b"})
        self.assertTrue(plan.parallel_effective)
