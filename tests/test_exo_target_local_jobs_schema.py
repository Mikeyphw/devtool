from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from devtool.config import load_project_config, resolve_config_dict
from devtool.workflow_runtime import build_workflow_specs, plan_selected_workflow, workflow_runner_kinds


class ExoTargetLocalJobsSchemaTests(unittest.TestCase):
    def _repo(self, config_text: str) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / ".devtool.toml").write_text(textwrap.dedent(config_text), encoding="utf-8")
        return temp, root

    def test_android_target_local_command_and_gradle_jobs_feed_target_workflow(self) -> None:
        temp, root = self._repo(
            """
            [project]
            runner = "android"
            default_target = "dustui_android"

            [targets.dustui_android]
            runner = "android"
            platform = "android"
            root = "android/DustUI"

            [targets.dustui_android.jobs.contracts]
            runner = "command"
            command = ["python3", "scripts/validate_ov01_contract.py", "--audit-loop"]
            inputs = ["scripts/validate_ov01_contract.py"]
            outputs = ["build/evidence/contracts.json"]

            [targets.dustui_android.jobs.android_test]
            gradle_tasks = [":app:testDebugUnitTest"]

            [targets.dustui_android.workflows]
            fast = ["job:contracts", "job:android_test"]
            """
        )
        with temp:
            (root / "android" / "DustUI").mkdir(parents=True)
            config = load_project_config(str(root), None)

        self.assertIn("dustui_android.contracts", config.jobs)
        self.assertIn("dustui_android.android_test", config.jobs)
        self.assertIn("contracts", config.targets["dustui_android"].jobs)
        self.assertEqual(config.jobs["dustui_android.contracts"].metadata["target"], "dustui_android")
        self.assertEqual(config.jobs["dustui_android.contracts"].metadata["local_name"], "contracts")
        self.assertEqual(config.jobs["dustui_android.android_test"].runner, "gradle")
        self.assertEqual(config.jobs["dustui_android.android_test"].command, ())
        self.assertEqual(config.jobs["dustui_android.android_test"].gradle_tasks, (":app:testDebugUnitTest",))

        graph = plan_selected_workflow(config, "fast")
        self.assertEqual([node.ref.raw for node in graph.nodes], ["job:contracts", "job:android_test"])
        self.assertEqual([node.ref.name for node in graph.nodes], ["dustui_android.contracts", "dustui_android.android_test"])
        self.assertEqual(workflow_runner_kinds(config, "fast"), ("command", "gradle"))

        specs = build_workflow_specs(config, "fast")
        self.assertEqual(specs[0].argv, ["python3", "scripts/validate_ov01_contract.py", "--audit-loop"])
        self.assertEqual(Path(specs[0].cwd), root / "android" / "DustUI")
        self.assertEqual(specs[0].metadata["workflow_job"], "dustui_android.contracts")
        self.assertIn(":app:testDebugUnitTest", specs[1].argv)
        self.assertTrue(specs[1].metadata["gradle_semantic_request"])
        self.assertIn("gradle_execution_plan", specs[1].metadata)
        self.assertEqual(specs[1].metadata["gradle_execution_plan"]["requested_roots"], [":app:testDebugUnitTest"])

    def test_target_local_job_wins_for_that_target_without_leaking_to_other_targets(self) -> None:
        temp, root = self._repo(
            """
            [project]
            runner = "python"
            default_target = "app"

            [jobs.contracts]
            runner = "command"
            command = ["python3", "repo_contracts.py"]

            [targets.app]
            runner = "python"
            root = "app"

            [targets.app.jobs.contracts]
            runner = "command"
            command = ["python3", "app_contracts.py"]

            [targets.app.workflows]
            fast = ["job:contracts"]

            [targets.lib]
            runner = "python"
            root = "lib"

            [targets.lib.workflows]
            fast = ["job:contracts"]
            """
        )
        with temp:
            (root / "app").mkdir()
            (root / "lib").mkdir()
            app_config = load_project_config(str(root), None, target_name="app")
            lib_config = load_project_config(str(root), None, target_name="lib")

        self.assertEqual(plan_selected_workflow(app_config, "fast").nodes[0].ref.name, "app.contracts")
        self.assertEqual(build_workflow_specs(app_config, "fast")[0].argv, ["python3", "app_contracts.py"])
        self.assertEqual(plan_selected_workflow(lib_config, "fast").nodes[0].ref.name, "contracts")
        self.assertEqual(build_workflow_specs(lib_config, "fast")[0].argv, ["python3", "repo_contracts.py"])

    def test_bad_target_local_schema_fails_before_execution_with_target_context(self) -> None:
        temp, root = self._repo(
            """
            [project]
            runner = "android"
            default_target = "dustui_android"

            [targets.dustui_android]
            runner = "android"
            root = "."

            [targets.dustui_android.jobs.bad]
            runner = "node"
            gradle_tasks = [":app:testDebugUnitTest"]

            [targets.dustui_android.workflows]
            fast = ["job:bad"]
            """
        )
        with temp, self.assertRaisesRegex(SystemExit, "targets.dustui_android.jobs.*gradle_tasks"):
            load_project_config(str(root), None)

    def test_resolved_config_reports_target_local_jobs_separately_and_globally(self) -> None:
        temp, root = self._repo(
            """
            [project]
            runner = "android"
            default_target = "dustui_android"

            [targets.dustui_android]
            runner = "android"
            root = "."

            [targets.dustui_android.jobs.contracts]
            runner = "command"
            command = ["python3", "scripts/validate.py"]

            [targets.dustui_android.workflows]
            fast = ["job:contracts"]
            """
        )
        with temp:
            config = load_project_config(str(root), None)
            payload = resolve_config_dict(config)

        self.assertIn("dustui_android.contracts", payload["jobs"])
        self.assertIn("contracts", payload["targets"]["dustui_android"]["jobs"])
        self.assertEqual(
            payload["targets"]["dustui_android"]["workflow_graphs"]["fast"]["steps"][0]["name"],
            "dustui_android.contracts",
        )
