from __future__ import annotations

import io
import json
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from devtool.cli import _jobs, build_parser
from devtool.config import load_project_config


class ExoTargetLocalJobsInspectionTests(unittest.TestCase):
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

            [targets.web]
            runner = "command"
            root = "web"

            [targets.web.jobs.assets]
            runner = "command"
            command = ["python3", "web_assets.py"]
            """
        )
        root.joinpath("api").mkdir()
        root.joinpath("web").mkdir()
        return temp, load_project_config(str(root), None)

    def test_jobs_human_output_includes_target_local_jobs_when_no_repository_filter_is_used(self) -> None:
        temp, cfg = self._config()
        with temp:
            out = io.StringIO()
            with redirect_stdout(out):
                _jobs(cfg)
            text = out.getvalue()
        self.assertIn("repo_lint", text)
        self.assertIn("api.contracts", text)
        self.assertIn("web.assets", text)
        self.assertIn("scope=target", text)
        self.assertIn("ref=job:contracts", text)

    def test_jobs_json_can_filter_target_local_jobs_by_target_without_hiding_repository_jobs(self) -> None:
        temp, cfg = self._config()
        with temp:
            out = io.StringIO()
            with redirect_stdout(out):
                _jobs(cfg, as_json=True, selected_target="api")
            payload = json.loads(out.getvalue())
        self.assertEqual(payload["schema_version"], 4)
        self.assertEqual(payload["selected_target"], "api")
        self.assertIn("repo_lint", payload["jobs"])
        self.assertIn("api.contracts", payload["jobs"])
        self.assertIn("web.assets", payload["jobs"])
        self.assertEqual(sorted(payload["repository_jobs"]), ["repo_lint"])
        self.assertEqual(sorted(payload["target_jobs"]), ["api"])
        self.assertIn("contracts", payload["target_jobs"]["api"])
        self.assertNotIn("web", payload["target_jobs"])
        self.assertEqual(payload["summary"]["target_local_jobs"], 1)
        self.assertEqual(payload["summary"]["targets_with_jobs"], ["api"])
        self.assertIn("api.contracts", payload["completion"]["target_job_keys"])
        self.assertNotIn("web.assets", payload["completion"]["target_job_keys"])

    def test_jobs_parser_accepts_target_filter_and_unknown_targets_fail_fast(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["jobs", "--target", "api", "--json"])
        self.assertEqual(args.command, "jobs")
        self.assertEqual(args.target, "api")
        self.assertTrue(args.json)

        temp, cfg = self._config()
        with temp:
            with self.assertRaisesRegex(SystemExit, "Unknown target for jobs inspection: missing"):
                _jobs(cfg, as_json=True, selected_target="missing")


if __name__ == "__main__":
    unittest.main()
