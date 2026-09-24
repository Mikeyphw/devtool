from __future__ import annotations

import io
import json
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from devtool.cli import _plan, build_parser
from devtool.config import load_project_config


class ExoTargetLocalJobsPlanExportTests(unittest.TestCase):
    def _repo(self, config_text: str) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / ".devtool.toml").write_text(textwrap.dedent(config_text), encoding="utf-8")
        return temp, root

    def _config(self):
        temp, root = self._repo(
            """
            [project]
            runner = "command"
            default_target = "api"

            [jobs.repo_lint]
            runner = "command"
            command = ["python3", "repo_lint.py"]

            [targets.api]
            runner = "command"
            root = "api"

            [targets.api.jobs.contracts]
            runner = "command"
            command = ["python3", "api_contracts.py"]

            [targets.api.workflows]
            fast = [
              "job:contracts",
              "job:repo_lint",
              "target:web#fast",
            ]

            [targets.web]
            runner = "command"
            root = "web"

            [targets.web.jobs.assets]
            runner = "command"
            command = ["python3", "web_assets.py"]

            [targets.web.workflows]
            fast = ["job:assets"]
            """
        )
        root.joinpath("api").mkdir()
        root.joinpath("web").mkdir()
        return temp, load_project_config(str(root), None)

    def test_plan_json_exports_target_local_job_identity_for_commands_and_summary(self) -> None:
        temp, cfg = self._config()
        args = build_parser().parse_args(["plan", "fast", "--json"])
        with temp:
            out = io.StringIO()
            with redirect_stdout(out):
                rc = _plan(cfg, args)
            payload = json.loads(out.getvalue())

        self.assertEqual(rc, 0)
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["summary"]["target_local_job_count"], 2)
        self.assertEqual(payload["summary"]["targets_with_target_local_jobs"], ["api", "web"])
        summary_keys = [item["key"] for item in payload["summary"]["target_local_jobs"]]
        self.assertEqual(summary_keys, ["api.contracts", "web.assets"])

        by_key = {command["job"]["key"]: command for command in payload["commands"]}
        self.assertTrue(by_key["api.contracts"]["target_local_job"])
        self.assertEqual(by_key["api.contracts"]["job"]["scope"], "target")
        self.assertEqual(by_key["api.contracts"]["job"]["target"], "api")
        self.assertEqual(by_key["api.contracts"]["job"]["local_name"], "contracts")
        self.assertEqual(by_key["api.contracts"]["resolution"]["key"], "api.contracts")
        self.assertEqual(Path(by_key["api.contracts"]["cwd"]), Path(cfg.repo_root) / "api")
        self.assertFalse(by_key["repo_lint"]["target_local_job"])
        self.assertEqual(by_key["repo_lint"]["job"]["scope"], "repository")
        self.assertTrue(by_key["web.assets"]["target_local_job"])
        self.assertEqual(Path(by_key["web.assets"]["cwd"]), Path(cfg.repo_root) / "web")

    def test_plan_human_explain_reports_target_local_job_keys(self) -> None:
        temp, cfg = self._config()
        args = build_parser().parse_args(["plan", "fast", "--explain"])
        with temp:
            out = io.StringIO()
            with redirect_stdout(out):
                _plan(cfg, args)
            text = out.getvalue()

        self.assertIn("target-local jobs", text)
        self.assertIn("api.contracts", text)
        self.assertIn("web.assets", text)
        self.assertIn("job=target:api.contracts", text)

    def test_plan_dot_labels_target_local_resolution_keys(self) -> None:
        temp, cfg = self._config()
        args = build_parser().parse_args(["plan", "fast", "--dot"])
        with temp:
            out = io.StringIO()
            with redirect_stdout(out):
                _plan(cfg, args)
            dot = out.getvalue()

        self.assertIn("job:api.contracts", dot)
        self.assertIn("job:web.assets", dot)


if __name__ == "__main__":
    unittest.main()
