from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from devtool.config import load_project_config, resolve_config_dict
from devtool.toolchain_requirements import analyze_toolchain_requirements
from devtool.workflow_runtime import build_workspace_specs
from devtool.workflow_scheduler import build_execution_plan_from_specs


class ExoTargetLocalJobsExecutionAffinityTests(unittest.TestCase):
    def _repo(self, config_text: str) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / ".devtool.toml").write_text(textwrap.dedent(config_text), encoding="utf-8")
        return temp, root

    def test_explicit_target_local_job_root_is_relative_to_target_root(self) -> None:
        temp, root = self._repo(
            """
            [project]
            runner = "command"
            default_target = "dustui_android"

            [targets.dustui_android]
            runner = "command"
            root = "android/DustUI"

            [targets.dustui_android.jobs.contracts]
            runner = "command"
            root = "tools/contracts"
            command = ["python3", "check.py"]
            inputs = ["check.py"]
            outputs = ["out/contracts.json"]

            [targets.dustui_android.workflows]
            fast = ["job:contracts"]
            """
        )
        with temp:
            job_root = root / "android" / "DustUI" / "tools" / "contracts"
            job_root.mkdir(parents=True)
            (job_root / "check.py").write_text("print('ok')\n", encoding="utf-8")
            cfg = load_project_config(str(root), None)
            resolved = resolve_config_dict(cfg)
            specs = build_workspace_specs(cfg, ["dustui_android"], "fast")
            report = analyze_toolchain_requirements(cfg, ["dustui_android"], workflow="fast")

        self.assertEqual(Path(resolved["jobs"]["dustui_android.contracts"]["root"]), job_root)
        self.assertEqual(Path(specs[0].cwd), job_root)
        self.assertEqual(specs[0].metadata["workflow_effective_cwd"], str(job_root))
        self.assertEqual(specs[0].metadata["workflow_job_scope"], "target")
        self.assertEqual(specs[0].metadata["workflow_job_target"], "dustui_android")
        self.assertEqual(specs[0].metadata["workflow_job_local_name"], "contracts")
        self.assertTrue(specs[0].metadata["workflow_job_target_local"])

        root_checks = [item for item in report.checks if item.section == "root" and item.name == "job:dustui_android.contracts"]
        self.assertEqual(len(root_checks), 1)
        self.assertEqual(root_checks[0].detail, str(job_root))
        output_checks = [
            item for item in report.checks
            if item.section == "output" and item.name == "job:dustui_android.contracts:out/contracts.json"
        ]
        self.assertEqual(len(output_checks), 1)
        self.assertEqual(output_checks[0].detail, "android/DustUI/tools/contracts/out/contracts.json")

    def test_target_local_job_env_inherits_or_explicitly_clears_target_env(self) -> None:
        temp, root = self._repo(
            """
            [project]
            runner = "command"
            default_target = "app"

            [targets.app]
            runner = "command"
            root = "app"

            [targets.app.env]
            TARGET_ONLY = "yes"
            COMMON = "target"

            [targets.app.jobs.inherit]
            runner = "command"
            command = ["python3", "-c", "print('inherit')"]

            [targets.app.jobs.clear]
            runner = "command"
            command = ["python3", "-c", "print('clear')"]

            [targets.app.jobs.clear.env]

            [targets.app.workflows]
            fast = ["job:inherit", "job:clear"]
            """
        )
        with temp:
            (root / "app").mkdir()
            cfg = load_project_config(str(root), None)
            specs = build_workspace_specs(cfg, ["app"], "fast")

        by_job = {spec.metadata["workflow_resolution_local_name"]: spec for spec in specs}
        self.assertEqual(by_job["inherit"].env["TARGET_ONLY"], "yes")
        self.assertEqual(by_job["inherit"].env["COMMON"], "target")
        self.assertNotIn("TARGET_ONLY", by_job["clear"].env)
        self.assertNotIn("COMMON", by_job["clear"].env)

    def test_gradle_task_shortcut_uses_target_root_and_scheduler_identity(self) -> None:
        temp, root = self._repo(
            """
            [project]
            runner = "command"
            default_target = "android_app"

            [targets.android_app]
            runner = "android"
            root = "android/App"

            [targets.android_app.jobs.unit_tests]
            gradle_tasks = [":app:testDebugUnitTest"]
            outputs = ["app/build/test-results/**"]

            [targets.android_app.workflows]
            validate = ["job:unit_tests"]
            """
        )
        with temp:
            target_root = root / "android" / "App"
            target_root.mkdir(parents=True)
            cfg = load_project_config(str(root), None)
            specs = build_workspace_specs(cfg, ["android_app"], "validate")
            plan = build_execution_plan_from_specs(cfg, specs, parallel_requested=True)

        self.assertEqual(Path(specs[0].cwd), target_root)
        self.assertEqual(specs[0].argv, ["./gradlew", ":app:testDebugUnitTest"])
        self.assertEqual(specs[0].metadata["workflow_job_scope"], "target")
        self.assertEqual(specs[0].metadata["gradle_tasks"], [":app:testDebugUnitTest"])
        self.assertIsNotNone(plan)
        assert plan is not None
        node = plan.as_dict()["nodes"][0]
        self.assertEqual(node["job_scope"], "target")
        self.assertEqual(node["job_target"], "android_app")
        self.assertEqual(node["job_local_name"], "unit_tests")
        self.assertEqual(node["job_key"], "android_app.unit_tests")
        self.assertTrue(node["target_local_job"])


if __name__ == "__main__":
    unittest.main()
