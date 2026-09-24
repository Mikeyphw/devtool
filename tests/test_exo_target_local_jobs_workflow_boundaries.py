from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from devtool.config import load_project_config
from devtool.workflow_runtime import build_workspace_specs


class ExoTargetLocalJobsWorkflowBoundariesTests(unittest.TestCase):
    def _repo(self, config_text: str) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / ".devtool.toml").write_text(textwrap.dedent(config_text), encoding="utf-8")
        return temp, root

    def test_foreign_target_local_job_reference_fails_during_config_load(self) -> None:
        temp, root = self._repo(
            """
            [project]
            runner = "command"
            default_target = "api"

            [targets.api]
            runner = "command"
            root = "api"

            [targets.api.workflows]
            fast = ["job:web.assets"]

            [targets.web]
            runner = "command"
            root = "web"

            [targets.web.jobs.assets]
            runner = "command"
            command = ["python3", "assets.py"]
            """
        )
        with temp:
            with self.assertRaisesRegex(SystemExit, "target-local job 'web.assets' owned by target 'web'"):
                load_project_config(str(root), None)

    def test_same_target_may_reference_local_job_by_short_or_scoped_key(self) -> None:
        temp, root = self._repo(
            """
            [project]
            runner = "command"
            default_target = "web"

            [targets.web]
            runner = "command"
            root = "web"

            [targets.web.jobs.assets]
            runner = "command"
            command = ["python3", "assets.py"]

            [targets.web.workflows]
            short = ["job:assets"]
            scoped = ["job:web.assets"]
            """
        )
        with temp:
            (root / "web").mkdir()
            cfg = load_project_config(str(root), None)
            short_specs = build_workspace_specs(cfg, ["web"], "short")
            scoped_specs = build_workspace_specs(cfg, ["web"], "scoped")

        self.assertEqual(short_specs[0].metadata["workflow_resolution_key"], "web.assets")
        self.assertEqual(scoped_specs[0].metadata["workflow_resolution_key"], "web.assets")
        self.assertEqual(short_specs[0].metadata["workflow_resolution_target"], "web")
        self.assertEqual(scoped_specs[0].metadata["workflow_resolution_target"], "web")

    def test_cross_target_composition_uses_target_reference_and_preserves_child_affinity(self) -> None:
        temp, root = self._repo(
            """
            [project]
            runner = "command"
            default_target = "api"

            [targets.api]
            runner = "command"
            root = "api"

            [targets.api.jobs.contracts]
            runner = "command"
            command = ["python3", "api_contracts.py"]

            [targets.api.workflows]
            fast = [
              { id = "api_contracts", ref = "job:contracts", depends_on = [] },
              { id = "web", ref = "target:web#fast", depends_on = ["api_contracts"] },
            ]

            [targets.web]
            runner = "command"
            root = "web"

            [targets.web.jobs.contracts]
            runner = "command"
            command = ["python3", "web_contracts.py"]

            [targets.web.workflows]
            fast = ["job:contracts"]
            """
        )
        with temp:
            (root / "api").mkdir()
            (root / "web").mkdir()
            cfg = load_project_config(str(root), None)
            specs = build_workspace_specs(cfg, ["api"], "fast")

        by_target = {spec.metadata["workflow_resolution_target"]: spec for spec in specs}
        self.assertEqual(set(by_target), {"api", "web"})
        self.assertEqual(by_target["api"].metadata["workflow_resolution_key"], "api.contracts")
        self.assertEqual(by_target["web"].metadata["workflow_resolution_key"], "web.contracts")
        self.assertEqual(Path(by_target["api"].cwd), root / "api")
        self.assertEqual(Path(by_target["web"].cwd), root / "web")
        self.assertEqual(by_target["web"].metadata["workflow_job_target"], "web")


if __name__ == "__main__":
    unittest.main()
