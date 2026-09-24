from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from devtool.config import load_project_config, resolve_config_dict
from devtool.workflow_runtime import build_workspace_specs
from devtool.workflow_scheduler import build_execution_plan_from_specs


class ExoTargetLocalJobsRuntimeProvenanceTests(unittest.TestCase):
    def _repo(self, config_text: str) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / ".devtool.toml").write_text(textwrap.dedent(config_text), encoding="utf-8")
        return temp, root

    def test_target_local_job_resolution_is_visible_in_config_graph_and_runtime_metadata(self) -> None:
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
            command = ["python3", "scripts/contracts.py"]
            inputs = ["scripts/contracts.py"]
            outputs = ["build/evidence/contracts.json"]

            [targets.dustui_android.workflows]
            fast = ["job:contracts"]
            """
        )
        with temp:
            (root / "android" / "DustUI" / "scripts").mkdir(parents=True)
            (root / "android" / "DustUI" / "scripts" / "contracts.py").write_text("print('ok')\n", encoding="utf-8")
            cfg = load_project_config(str(root), None)
            resolved = resolve_config_dict(cfg)
            specs = build_workspace_specs(cfg, ["dustui_android"], "fast")

        workflow = resolved["targets"]["dustui_android"]["workflow_graphs"]["fast"]
        step = workflow["steps"][0]
        node = workflow["graph"]["nodes"][0]
        self.assertEqual(step["ref"], "job:contracts")
        self.assertEqual(step["name"], "dustui_android.contracts")
        self.assertEqual(step["resolution_scope"], "target")
        self.assertEqual(step["resolution_target"], "dustui_android")
        self.assertEqual(step["resolution_local_name"], "contracts")
        self.assertEqual(step["resolution_key"], "dustui_android.contracts")
        self.assertTrue(step["target_local"])
        self.assertEqual(
            node["resolution"],
            {
                "scope": "target",
                "target": "dustui_android",
                "local_name": "contracts",
                "key": "dustui_android.contracts",
                "target_local": True,
            },
        )

        metadata = specs[0].metadata
        self.assertEqual(metadata["workflow_ref"], "job:contracts")
        self.assertEqual(metadata["workflow_resolution_scope"], "target")
        self.assertEqual(metadata["workflow_resolution_target"], "dustui_android")
        self.assertEqual(metadata["workflow_resolution_local_name"], "contracts")
        self.assertEqual(metadata["workflow_resolution_key"], "dustui_android.contracts")

    def test_scheduler_plan_preserves_target_local_job_identity(self) -> None:
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
            inputs = ["contracts.py"]
            outputs = ["build/contracts.json"]

            [targets.app.workflows]
            fast = ["job:contracts"]
            """
        )
        with temp:
            (root / "app").mkdir()
            (root / "app" / "contracts.py").write_text("print('ok')\n", encoding="utf-8")
            cfg = load_project_config(str(root), None)
            specs = build_workspace_specs(cfg, ["app"], "fast")
            plan = build_execution_plan_from_specs(cfg, specs, parallel_requested=True)

        self.assertIsNotNone(plan)
        assert plan is not None
        payload = plan.as_dict()
        node = payload["nodes"][0]
        self.assertEqual(node["job_scope"], "target")
        self.assertEqual(node["job_target"], "app")
        self.assertEqual(node["job_local_name"], "contracts")
        self.assertEqual(node["job_key"], "app.contracts")
        self.assertTrue(node["target_local_job"])


if __name__ == "__main__":
    unittest.main()
