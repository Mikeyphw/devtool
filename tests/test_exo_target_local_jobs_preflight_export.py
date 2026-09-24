from __future__ import annotations

import io
import json
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from devtool.cli import _preflight, build_parser
from devtool.config import load_project_config


class ExoTargetLocalJobsPreflightExportTests(unittest.TestCase):
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
            outputs = ["out/repo"]

            [targets.api]
            runner = "command"
            root = "api"
            outputs = ["build/api"]

            [targets.api.jobs.contracts]
            runner = "command"
            command = ["python3", "api_contracts.py"]
            outputs = ["out/contracts"]

            [targets.api.workflows]
            fast = ["job:contracts", "job:repo_lint", "target:web#fast"]

            [targets.web]
            runner = "command"
            root = "web"
            outputs = ["build/web"]

            [targets.web.jobs.assets]
            runner = "command"
            command = ["python3", "web_assets.py"]
            outputs = ["dist"]

            [targets.web.workflows]
            fast = ["job:assets"]
            """
        )
        root.joinpath("api").mkdir()
        root.joinpath("web").mkdir()
        return temp, load_project_config(str(root), None)

    def test_preflight_json_exports_target_local_job_summary(self) -> None:
        temp, cfg = self._config()
        args = build_parser().parse_args(["preflight", "--workflow", "fast", "--json"])
        with temp:
            out = io.StringIO()
            with redirect_stdout(out):
                rc = _preflight(cfg, args)
            payload = json.loads(out.getvalue())

        self.assertEqual(rc, 0)
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["summary"]["target_local_job_count"], 2)
        self.assertEqual(payload["summary"]["repository_job_count"], 1)
        self.assertEqual(payload["summary"]["targets_with_target_local_jobs"], ["api", "web"])
        self.assertEqual([item["key"] for item in payload["target_local_jobs"]], ["api.contracts", "web.assets"])
        self.assertEqual(payload["target_local_jobs"][0]["target"], "api")
        self.assertEqual(payload["target_local_jobs"][0]["local_name"], "contracts")
        self.assertEqual([item["key"] for item in payload["repository_jobs"]], ["repo_lint"])
        checks = {(item["section"], item["name"]): item for item in payload["checks"]}
        self.assertEqual(checks[("job", "api.contracts")]["status"], "ready")
        self.assertEqual(checks[("job", "web.assets")]["status"], "ready")

    def test_preflight_human_output_lists_target_local_and_repository_jobs(self) -> None:
        temp, cfg = self._config()
        args = build_parser().parse_args(["preflight", "--workflow", "fast"])
        with temp:
            out = io.StringIO()
            with redirect_stdout(out):
                _preflight(cfg, args)
            text = out.getvalue()

        self.assertIn("target-local jobs", text)
        self.assertIn("api.contracts", text)
        self.assertIn("web.assets", text)
        self.assertIn("repository jobs", text)
        self.assertIn("repo_lint", text)


if __name__ == "__main__":
    unittest.main()
