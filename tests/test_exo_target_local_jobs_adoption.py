from __future__ import annotations

import io
import json
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from devtool.cli import build_parser, main
from devtool.config import load_project_config
from devtool.target_local_adoption import render_target_local_adoption_text, target_local_adoption_report


class ExoTargetLocalJobsAdoptionTests(unittest.TestCase):
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
            outputs = ["out/contracts.json"]

            [targets.api.workflows]
            fast = ["job:contracts", "job:repo_lint", "target:web#fast"]

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
        return temp, load_project_config(str(root), None), root

    def test_target_local_adoption_report_summarizes_jobs_workflows_and_commands(self) -> None:
        temp, cfg, _root = self._config()
        with temp:
            payload = target_local_adoption_report(cfg)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["summary"]["target_local_job_count"], 2)
        self.assertEqual(payload["summary"]["repository_job_count"], 1)
        self.assertEqual(payload["summary"]["targets_with_target_local_jobs"], ["api", "web"])
        self.assertEqual(payload["summary"]["workflows_using_target_local_jobs"], {"api": ["fast"], "web": ["fast"]})
        api = payload["targets"]["api"]
        self.assertEqual(api["status"], "ready")
        self.assertEqual(api["jobs"][0]["refs"]["owner_local"], "job:contracts")
        self.assertEqual(api["workflows"][0]["target_local_jobs"][0]["key"], "api.contracts")
        self.assertIn("devtool plan --target api fast --json", api["workflows"][0]["commands"]["plan"])
        self.assertIn("Use target:<target>#<workflow>", "\n".join(payload["checklist"]))

    def test_target_local_adoption_report_can_filter_one_target(self) -> None:
        temp, cfg, _root = self._config()
        with temp:
            payload = target_local_adoption_report(cfg, selected_target="api")
        self.assertEqual(payload["selected_target"], "api")
        self.assertEqual(sorted(payload["targets"]), ["api"])
        self.assertEqual([item["key"] for item in payload["target_local_jobs"]], ["api.contracts"])
        self.assertIn("repo_lint", payload["repository_jobs"])
        with self.assertRaisesRegex(SystemExit, "Unknown target for target-local adoption report: missing"):
            target_local_adoption_report(cfg, selected_target="missing")

    def test_target_local_adoption_human_renderer_is_actionable(self) -> None:
        temp, cfg, _root = self._config()
        with temp:
            text = render_target_local_adoption_text(target_local_adoption_report(cfg, selected_target="api"))
        self.assertIn("Target-local jobs adoption · api", text)
        self.assertIn("api.contracts ref=job:contracts", text)
        self.assertIn("devtool preflight --target api", text)
        self.assertIn("Checklist:", text)

    def test_cli_parser_and_main_expose_target_local_jobs_command(self) -> None:
        args = build_parser().parse_args(["target-local-jobs", "--target", "api", "--json"])
        self.assertEqual(args.command, "target-local-jobs")
        self.assertEqual(args.target, "api")
        self.assertTrue(args.json)

        temp, _cfg, root = self._config()
        with temp:
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(["-r", str(root), "target-local-jobs", "--target", "api", "--json"])
            payload = json.loads(out.getvalue())
        self.assertEqual(rc, 0)
        data = payload.get("data", payload)
        self.assertEqual(data["selected_target"], "api")
        self.assertEqual(data["target_local_jobs"][0]["key"], "api.contracts")

    def test_zsh_completion_mentions_target_local_adoption_command_and_target_filter(self) -> None:
        from devtool.completion import ZSH_COMPLETION

        self.assertIn("target-local-jobs:report target-local job adoption", ZSH_COMPLETION)
        self.assertIn("tlj:alias for target-local-jobs", ZSH_COMPLETION)
        self.assertIn("jobs|jb|target-local-jobs|tlj", ZSH_COMPLETION)
        self.assertIn("--target[limit to one target]", ZSH_COMPLETION)


if __name__ == "__main__":
    unittest.main()
