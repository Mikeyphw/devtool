from __future__ import annotations

import io
import json
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from devtool.cli import _jobs, _plan, _preflight, build_parser
from devtool.completion_state import build_completion_state
from devtool.config import load_project_config
from devtool.doctor import doctor_payload
from devtool.models import OperationResult
from devtool.summary import build_runner_json
from devtool.target_local_adoption import target_local_adoption_report


class ExoTargetLocalJobsGateSealTests(unittest.TestCase):
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
        return temp, load_project_config(str(root), None)

    def test_gate_seal_public_surfaces_share_target_local_identity(self) -> None:
        temp, cfg = self._config()
        with temp:
            self.assertTrue(cfg.jobs["api.contracts"].metadata["target_local"])
            self.assertEqual(cfg.jobs["api.contracts"].metadata["target"], "api")

            completion = build_completion_state(cfg)
            self.assertIn("api.contracts", completion.target_job_keys)
            self.assertIn("api:job:contracts", completion.target_job_refs)

            doctor = doctor_payload(cfg)
            self.assertEqual(doctor["target_jobs"], {"api": ["contracts"], "web": ["assets"]})

            jobs_out = io.StringIO()
            with redirect_stdout(jobs_out):
                _jobs(cfg, as_json=True, selected_target="api")
            jobs_payload = json.loads(jobs_out.getvalue())
            self.assertEqual(jobs_payload["schema_version"], 4)
            self.assertEqual(jobs_payload["selected_target"], "api")
            self.assertEqual(jobs_payload["summary"]["target_local_jobs"], 1)

            plan_out = io.StringIO()
            with redirect_stdout(plan_out):
                self.assertEqual(_plan(cfg, build_parser().parse_args(["plan", "fast", "--json"])), 0)
            plan_payload = json.loads(plan_out.getvalue())
            self.assertEqual(plan_payload["schema_version"], 2)
            self.assertEqual(plan_payload["summary"]["target_local_job_count"], 2)
            by_key = {item["job"]["key"]: item for item in plan_payload["commands"]}
            self.assertTrue(by_key["api.contracts"]["target_local_job"])
            self.assertEqual(by_key["api.contracts"]["job"]["scope"], "target")
            self.assertFalse(by_key["repo_lint"]["target_local_job"])

            preflight_out = io.StringIO()
            with redirect_stdout(preflight_out):
                self.assertEqual(_preflight(cfg, build_parser().parse_args(["preflight", "--workflow", "fast", "--json"])), 0)
            preflight_payload = json.loads(preflight_out.getvalue())
            self.assertEqual(preflight_payload["schema_version"], 2)
            self.assertEqual([item["key"] for item in preflight_payload["target_local_jobs"]], ["api.contracts", "web.assets"])

            adoption = target_local_adoption_report(cfg, selected_target="api")
            self.assertEqual(adoption["selected_target"], "api")
            self.assertEqual(adoption["summary"]["target_local_job_count"], 1)
            self.assertIn("devtool plan --target api fast --json", adoption["targets"]["api"]["workflows"][0]["commands"]["plan"])

    def test_gate_seal_runner_summary_preserves_job_vocabulary(self) -> None:
        payload = build_runner_json(
            [
                OperationResult(
                    "contracts",
                    ["python3", "api_contracts.py"],
                    0,
                    0.1,
                    "",
                    [],
                    metadata={
                        "workflow_node_key": "api:fast:contracts",
                        "workflow_target": "api",
                        "workflow_name": "fast",
                        "workflow_runner": "command",
                        "workflow_action": "contracts",
                        "workflow_ref": "job:contracts",
                        "workflow_resolution_scope": "target",
                        "workflow_resolution_target": "api",
                        "workflow_resolution_local_name": "contracts",
                        "workflow_resolution_key": "api.contracts",
                        "workflow_job_scope": "target",
                        "workflow_job_target": "api",
                        "workflow_job_local_name": "contracts",
                        "workflow_job_key": "api.contracts",
                        "workflow_job_target_local": True,
                    },
                ),
                OperationResult(
                    "repo_lint",
                    ["python3", "repo_lint.py"],
                    0,
                    0.1,
                    "",
                    [],
                    metadata={
                        "workflow_node_key": "api:fast:repo_lint",
                        "workflow_target": "api",
                        "workflow_name": "fast",
                        "workflow_runner": "command",
                        "workflow_action": "repo_lint",
                        "workflow_ref": "job:repo_lint",
                        "workflow_resolution_scope": "repository",
                        "workflow_resolution_key": "repo_lint",
                        "workflow_job_scope": "repository",
                        "workflow_job_key": "repo_lint",
                    },
                ),
            ],
            repo_root="/repo",
            command="devtool run fast",
            raw_output="raw",
            summary_text="summary",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            finished_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(payload["workflow"]["schema_version"], 2)
        self.assertEqual(payload["workflow"]["summary"]["target_local_job_count"], 1)
        self.assertEqual(payload["workflow"]["nodes"][0]["job"]["key"], "api.contracts")
        self.assertTrue(payload["workflow"]["nodes"][0]["job"]["target_local"])
        self.assertEqual(payload["execution_context"]["target_local_job_count"], 1)
        self.assertEqual(payload["execution_context"]["repository_job_count"], 1)

    def test_gate_seal_documentation_and_report_are_explicit_about_validation(self) -> None:
        doc = Path("docs/EXO_TARGET_LOCAL_JOBS.md").read_text(encoding="utf-8")
        report = Path("EXO_TARGET_LOCAL_JOBS_GATE_SEAL_OV14_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("TLJ-14 gate + seal overlay", doc)
        self.assertIn("Apply this overlay without `--no-validate`", doc)
        self.assertIn("authoritative target-local jobs gate/seal result", doc)
        self.assertIn("validation.required=true", report)
        self.assertIn("Do not pass `--no-validate`", report)


if __name__ == "__main__":
    unittest.main()
