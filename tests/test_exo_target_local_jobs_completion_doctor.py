from __future__ import annotations

import io
import json
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from devtool.cli import _doctor, _jobs, build_parser
from devtool.completion import ZSH_COMPLETION
from devtool.completion_state import build_completion_state, completion_values
from devtool.config import load_project_config
from devtool.doctor import doctor_payload


class ExoTargetLocalJobsCompletionDoctorTests(unittest.TestCase):
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
            default_target = "app"

            [targets.app]
            runner = "command"
            root = "app"

            [targets.app.jobs.contracts]
            runner = "command"
            command = ["python3", "contracts.py"]

            [targets.app.workflows]
            fast = ["job:contracts"]
            """
        )
        root.joinpath("app").mkdir()
        return temp, load_project_config(str(root), None)

    def test_completion_state_exposes_target_local_jobs(self) -> None:
        temp, cfg = self._config()
        with temp:
            state = build_completion_state(cfg)
            self.assertEqual(state.schema_version, 2)
            self.assertIn("app:contracts", state.target_jobs)
            self.assertIn("app.contracts", state.target_job_keys)
            self.assertIn("job:contracts", state.target_job_refs)
            self.assertIn("job:app.contracts", state.target_job_refs)
            self.assertIn("app:job:contracts", state.target_job_refs)
            self.assertIn("app:contracts", completion_values(cfg, "target-jobs"))
            self.assertIn("app.contracts", completion_values(cfg, "target-job-keys"))
            self.assertIn("job:contracts", completion_values(cfg, "target-job-refs"))
            self.assertIn("app.contracts", completion_values(cfg, "all"))

    def test_parser_and_zsh_completion_accept_target_local_job_kinds(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["completion-state", "--kind", "target-jobs"])
        self.assertEqual(args.kind, "target-jobs")
        args = parser.parse_args(["completion-state", "--kind", "target-job-keys"])
        self.assertEqual(args.kind, "target-job-keys")
        args = parser.parse_args(["completion-state", "--kind", "target-job-refs"])
        self.assertEqual(args.kind, "target-job-refs")
        self.assertIn("target-jobs", ZSH_COMPLETION)
        self.assertIn("target-job-keys", ZSH_COMPLETION)
        self.assertIn("target-job-refs", ZSH_COMPLETION)

    def test_jobs_json_has_target_local_completion_payload(self) -> None:
        temp, cfg = self._config()
        with temp:
            out = io.StringIO()
            with redirect_stdout(out):
                _jobs(cfg, as_json=True)
            payload = json.loads(out.getvalue())
        self.assertEqual(payload["schema_version"], 4)
        self.assertIn("contracts", payload["target_jobs"]["app"])
        self.assertIn("app.contracts", payload["completion"]["target_job_keys"])
        self.assertIn("job:contracts", payload["completion"]["target_job_refs"])

    def test_doctor_payload_and_text_report_target_local_jobs(self) -> None:
        temp, cfg = self._config()
        with temp:
            payload = doctor_payload(cfg)
            out = io.StringIO()
            with redirect_stdout(out):
                _doctor(cfg)
            text = out.getvalue()
        self.assertEqual(payload["target_jobs"], {"app": ["contracts"]})
        self.assertEqual(payload["target_job_keys"], ["app.contracts"])
        checks = {(item["section"], item["name"]): item for item in payload["checks"]}
        self.assertEqual(checks[("project", "target-local-jobs")]["status"], "ok")
        self.assertEqual(checks[("project", "target-local-workflows")]["status"], "ok")
        self.assertIn("target-local-jobs", text)
        self.assertIn("target-local-workflows", text)


if __name__ == "__main__":
    unittest.main()
