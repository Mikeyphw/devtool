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


class ExoTargetLocalJobsFinalSealTests(unittest.TestCase):
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
            outputs = ["out/repo-lint.json"]

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
            outputs = ["dist/assets.json"]

            [targets.web.workflows]
            fast = ["job:assets"]
            """
        )
        root.joinpath("api").mkdir()
        root.joinpath("web").mkdir()
        return temp, load_project_config(str(root), None)

    def test_final_seal_identity_contract_is_consistent_across_public_surfaces(self) -> None:
        temp, cfg = self._config()
        with temp:
            self.assertIn("api.contracts", cfg.jobs)
            self.assertIn("web.assets", cfg.jobs)
            self.assertTrue(cfg.jobs["api.contracts"].metadata["target_local"])
            self.assertEqual(cfg.jobs["api.contracts"].metadata["target"], "api")
            self.assertEqual(cfg.jobs["api.contracts"].metadata["local_name"], "contracts")

            completion = build_completion_state(cfg)
            self.assertEqual(completion.schema_version, 2)
            self.assertIn("api.contracts", completion.target_job_keys)
            self.assertIn("web.assets", completion.target_job_keys)
            self.assertIn("job:contracts", completion.target_job_refs)
            self.assertIn("api:job:contracts", completion.target_job_refs)

            doctor = doctor_payload(cfg)
            self.assertEqual(doctor["target_job_keys"], ["api.contracts", "web.assets"])
            self.assertEqual(doctor["target_jobs"], {"api": ["contracts"], "web": ["assets"]})

            jobs_out = io.StringIO()
            with redirect_stdout(jobs_out):
                _jobs(cfg, as_json=True, selected_target="api")
            jobs_payload = json.loads(jobs_out.getvalue())
            self.assertEqual(jobs_payload["schema_version"], 4)
            self.assertIn("api.contracts", jobs_payload["completion"]["target_job_keys"])
            self.assertNotIn("web.assets", jobs_payload["completion"]["target_job_keys"])
            self.assertIn("repo_lint", jobs_payload["repository_jobs"])

            plan_out = io.StringIO()
            with redirect_stdout(plan_out):
                self.assertEqual(_plan(cfg, build_parser().parse_args(["plan", "fast", "--json"])), 0)
            plan_payload = json.loads(plan_out.getvalue())
            self.assertEqual(plan_payload["schema_version"], 2)
            self.assertEqual(plan_payload["summary"]["target_local_job_count"], 2)
            self.assertEqual(plan_payload["summary"]["repository_job_count"], 1)
            by_key = {item["job"]["key"]: item for item in plan_payload["commands"]}
            self.assertEqual(by_key["api.contracts"]["resolution"]["scope"], "target")
            self.assertEqual(by_key["api.contracts"]["job"]["local_name"], "contracts")
            self.assertTrue(by_key["api.contracts"]["target_local_job"])
            self.assertFalse(by_key["repo_lint"]["target_local_job"])

            preflight_out = io.StringIO()
            with redirect_stdout(preflight_out):
                self.assertEqual(_preflight(cfg, build_parser().parse_args(["preflight", "--workflow", "fast", "--json"])), 0)
            preflight_payload = json.loads(preflight_out.getvalue())
            self.assertEqual(preflight_payload["schema_version"], 2)
            self.assertEqual([item["key"] for item in preflight_payload["target_local_jobs"]], ["api.contracts", "web.assets"])
            self.assertEqual([item["key"] for item in preflight_payload["repository_jobs"]], ["repo_lint"])

    def test_final_seal_runner_summary_keeps_same_target_local_job_vocabulary(self) -> None:
        results = [
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
        ]

        payload = build_runner_json(
            results,
            repo_root="/repo",
            command="devtool plan fast",
            raw_output="raw",
            summary_text="summary",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            finished_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(payload["workflow"]["schema_version"], 2)
        self.assertEqual(payload["workflow"]["summary"]["target_local_job_count"], 1)
        self.assertEqual(payload["workflow"]["nodes"][0]["job"]["key"], "api.contracts")
        self.assertTrue(payload["workflow"]["nodes"][0]["job"]["target_local"])
        self.assertEqual(payload["execution_context"]["target_local_jobs"][0]["key"], "api.contracts")
        self.assertEqual(payload["execution_context"]["repository_jobs"], ["repo_lint"])

    def test_final_seal_documentation_records_adoption_contract(self) -> None:
        doc = Path("docs/EXO_TARGET_LOCAL_JOBS.md").read_text(encoding="utf-8")
        self.assertIn("TLJ-12 final seal", doc)
        self.assertIn("workflow_resolution_scope", doc)
        self.assertIn("target_local_jobs[]", doc)
        self.assertIn("[targets.dustui_go.jobs.contracts]", doc)


if __name__ == "__main__":
    unittest.main()
