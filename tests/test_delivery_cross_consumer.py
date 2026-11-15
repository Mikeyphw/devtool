"""S7 P06 offline provider-shape and adversarial qualification tests.

Synthetic fixtures never establish real hosted qualification.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

try:
    from devtool.delivery_cross_consumer import (CONSUMERS, SCHEMA, QualificationError,
        markdown, verify, write_report)
except ModuleNotFoundError:  # standalone ZIPAPP self-test, not installed Devtool
    from p06_core import (CONSUMERS, SCHEMA, QualificationError,
        markdown, verify, write_report)


class FakeGithub:
    def __init__(self):
        self.data = {}
        for consumer in CONSUMERS:
            repo = consumer.repository
            self.data[f"repos/{repo}/pulls/{consumer.pr}"] = {
                "number": consumer.pr, "state": "open", "draft": True, "merged_at": None,
                "head": {"sha": consumer.source_sha, "ref": consumer.head_branch,
                         "repo": {"full_name": repo}},
                "base": {"ref": "main", "repo": {"full_name": repo}},
            }
            runs = []
            for index, wf in enumerate(consumer.workflows, 1):
                run_id = consumer.pr * 10000 + index
                runs.append({"id": run_id, "name": wf.name,
                             "workflow_id": wf.workflow_id, "head_sha": consumer.source_sha,
                             "head_branch": consumer.head_branch, "event": "pull_request",
                             "status": "completed", "conclusion": "success",
                             "run_attempt": 1, "created_at": "2026-11-15T05:00:00Z"})
                jobs = []
                for name in (*wf.jobs, *wf.optional_jobs):
                    conclusion = "success" if name in wf.jobs else "skipped"
                    steps = [{"name": s, "status": "completed", "conclusion": "success"}
                             for s in dict(wf.steps).get(name, ())]
                    jobs.append({"run_id": run_id, "name": name, "status": "completed",
                                 "conclusion": conclusion, "steps": steps})
                self.data[f"repos/{repo}/actions/runs/{run_id}/attempts/1/jobs?per_page=100"] = {
                    "total_count": len(jobs), "jobs": jobs}
            self.data[f"repos/{repo}/actions/runs?event=pull_request&head_sha={consumer.source_sha}&per_page=100"] = {
                "total_count": len(runs), "workflow_runs": runs}

    def get(self, endpoint):
        return self.data[endpoint]

    def runs(self, consumer):
        return self.data[f"repos/{consumer.repository}/actions/runs?event=pull_request&head_sha={consumer.source_sha}&per_page=100"]["workflow_runs"]

    def jobs(self, consumer, workflow_index=0):
        wf = consumer.workflows[workflow_index]
        run_id = consumer.pr*10000 + workflow_index+1
        return self.data[f"repos/{consumer.repository}/actions/runs/{run_id}/attempts/1/jobs?per_page=100"]["jobs"]


class CrossConsumerQualificationTests(unittest.TestCase):
    def setUp(self):
        self.provider = FakeGithub()

    def fails(self, label=""):
        with self.assertRaises(QualificationError, msg=label):
            verify(self.provider)

    def test_all_three_synthetic_consumers_pass_schema_non_release(self):
        obj = verify(self.provider)
        self.assertEqual(obj["schema"], SCHEMA)
        self.assertEqual(len(obj["consumers"]), 3)
        self.assertEqual(sum(len(c["workflows"]) for c in obj["consumers"]), 7)
        self.assertEqual(obj["hosted_cross_consumer_acceptance"], "passed")
        self.assertFalse(obj["release_qualified"])
        self.assertFalse(obj["devtool_delivery_session_ci_qualified"])
        self.assertFalse(obj["physical_device_qualified"])
        self.assertEqual(len(obj["evidence_sha256"]), 64)

    def test_source_drift_refused(self):
        spec = CONSUMERS[2]
        self.provider.data[f"repos/{spec.repository}/pulls/{spec.pr}"]["head"]["sha"] = "f" * 40
        self.fails("source drift")

    def test_non_draft_or_merged_refused(self):
        spec = CONSUMERS[0]
        p = self.provider.data[f"repos/{spec.repository}/pulls/{spec.pr}"]
        p["draft"] = False
        self.fails("not draft")
        p["draft"] = True
        p["merged_at"] = "2026-11-15T00:00:00Z"
        self.fails("merged")

    def test_wrong_repo_or_branch_refused(self):
        spec = CONSUMERS[1]
        p = self.provider.data[f"repos/{spec.repository}/pulls/{spec.pr}"]
        p["head"]["repo"]["full_name"] = "someone/fork"
        self.fails("fork")
        p["head"]["repo"]["full_name"] = spec.repository
        p["head"]["ref"] = "main"
        self.fails("branch")

    def test_missing_workflow_refused(self):
        self.provider.runs(CONSUMERS[0]).pop()
        self.fails("missing workflow")

    def test_failed_or_pending_run_refused(self):
        run = self.provider.runs(CONSUMERS[2])[-1]
        run["conclusion"] = "failure"
        self.fails("failed")
        run["conclusion"] = None
        run["status"] = "in_progress"
        self.fails("pending")

    def test_wrong_workflow_name_or_sha_refused(self):
        run = self.provider.runs(CONSUMERS[1])[0]
        run["name"] = "Different Job"
        self.fails("name mismatch")
        run["name"] = CONSUMERS[1].workflows[0].name
        run["head_sha"] = "0" * 40
        self.fails("run sha mismatch")

    def test_missing_job_and_unexpected_job_refused(self):
        jobs = self.provider.jobs(CONSUMERS[2], 1)
        jobs.pop()
        self.fails("missing android build")
        jobs.append({"name": "unauthorized", "conclusion": "success", "status": "completed"})
        self.fails("unexpected job")

    def test_missing_required_apk_step_refused(self):
        jobs = self.provider.jobs(CONSUMERS[2], 1)
        jobs[1]["steps"] = [s for s in jobs[1]["steps"] if s["name"] != "Verify APK and record source-bound digest"]
        self.fails("APK evidence missing")

    def test_failed_required_step_refused(self):
        jobs = self.provider.jobs(CONSUMERS[2], 1)
        jobs[1]["steps"][-1]["conclusion"] = "skipped"
        self.fails("skipped evidence")

    def test_optional_android_job_may_be_skipped_only(self):
        jobs = self.provider.jobs(CONSUMERS[2], 0)
        self.assertEqual(jobs[-1]["conclusion"], "skipped")
        verify(self.provider)
        jobs[-1]["conclusion"] = "failure"
        self.fails("optional failed")

    def test_duplicate_job_refused(self):
        jobs = self.provider.jobs(CONSUMERS[0])
        jobs.append(jobs[0].copy())
        self.fails("duplicate job")

    def test_incomplete_pagination_refused(self):
        key = next(x for x in self.provider.data if x.endswith("&per_page=100"))
        self.provider.data[key]["total_count"] = 101
        self.fails("incomplete page")

    def test_wrong_run_attempt_refused(self):
        run = self.provider.runs(CONSUMERS[0])[0]
        run["run_attempt"] = 0
        self.fails("attempt")

    def test_report_private_and_atomic(self):
        r = verify(self.provider)
        with tempfile.TemporaryDirectory() as d:
            target = Path(d)/"evidence"
            a, b = write_report(r, target)
            self.assertEqual(json.loads(a.read_text())["evidence_sha256"], r["evidence_sha256"])
            self.assertIn("release", b.read_text())
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            (Path(d)/"linked").symlink_to(target, target_is_directory=True)
            with self.assertRaises(QualificationError):
                write_report(r, Path(d)/"linked")

    def test_cleanup_review_does_not_delete_specific_contracts(self):
        value = verify(self.provider)
        decisions = value["cleanup_decisions"]
        self.assertTrue(all(x["decision"] != "delete" for x in decisions))
        self.assertIn("GO17", markdown(value))
        self.assertIn("scripts/devtool_actions_contract.py", markdown(value))


if __name__ == "__main__":
    unittest.main()
