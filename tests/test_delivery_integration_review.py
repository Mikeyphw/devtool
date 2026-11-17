"""Current-PR and historical-merge S7 contracts; synthetic fixtures are not hosted proof."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from devtool.delivery_integration_review import (
    REPO, SCHEMA, ReviewError, IntegrationGhCLI, _local_source, markdown,
    review, write_report,
)
from devtool.delivery_cross_consumer import CONSUMERS, QualificationError

DEVTOOL_SHA = "a" * 40
CURRENT_PR = 123
CURRENT_BRANCH = "feature/current-fix"
MAIN_SHA = "b" * 40


class Provider:
    def __init__(self):
        self.data = {}
        self.calls = []
        self.data[f"repos/{REPO}/branches/main"] = {
            "name": "main", "commit": {"sha": MAIN_SHA}}
        self.data[f"repos/{REPO}/pulls/{CURRENT_PR}"] = {
            "number": CURRENT_PR, "state": "open", "draft": False,
            "merged_at": None, "head": {"sha": DEVTOOL_SHA, "ref": CURRENT_BRANCH,
            "repo": {"full_name": REPO}},
            "base": {"sha": MAIN_SHA, "ref": "main", "repo": {"full_name": REPO}},
            "mergeable": True, "mergeable_state": "clean",
        }
        self.data[f"repos/{REPO}/compare/main...{DEVTOOL_SHA}"] = {
            "status": "ahead", "ahead_by": 1, "behind_by": 0,
            "base_commit": {"sha": MAIN_SHA}, "merge_base_commit": {"sha": MAIN_SHA},
        }
        self.data[f"repos/{REPO}/git/commits/{DEVTOOL_SHA}"] = {
            "sha": DEVTOOL_SHA, "tree": {"sha": "c" * 40}}
        for consumer in CONSUMERS:
            repo = consumer.repository
            self.data[f"repos/{repo}/pulls/{consumer.pr}"] = {
                "number": consumer.pr, "state": "closed", "draft": False,
                "merged": True, "merged_at": "2026-11-15T19:43:00Z",
                "merge_commit_sha": "d" * 40,
                "head": {"sha": consumer.source_sha, "ref": consumer.head_branch,
                         "repo": {"full_name": repo}},
                "base": {"ref": "main", "repo": {"full_name": repo}},
            }
            runs = []
            for index, wf in enumerate(consumer.workflows, 1):
                rid = consumer.pr * 10000 + index
                runs.append({
                    "id": rid, "name": wf.name, "workflow_id": wf.workflow_id,
                    "head_sha": consumer.source_sha, "head_branch": consumer.head_branch,
                    "event": "pull_request", "status": "completed", "conclusion": "success",
                    "run_attempt": 1, "created_at": "2026-11-15T05:00:00Z"})
                jobs = []
                for name in (*wf.jobs, *wf.optional_jobs):
                    jobs.append({
                        "run_id": rid, "name": name, "status": "completed",
                        "conclusion": "success" if name in wf.jobs else "skipped",
                        "steps": [{"name": name_, "status": "completed", "conclusion": "success"}
                                  for name_ in dict(wf.steps).get(name, ())],
                    })
                self.data[f"repos/{repo}/actions/runs/{rid}/attempts/1/jobs?per_page=100"] = {
                    "total_count": len(jobs), "jobs": jobs}
            self.data[f"repos/{repo}/actions/runs?event=pull_request&head_sha={consumer.source_sha}&per_page=100"] = {
                "total_count": len(runs), "workflow_runs": runs}

    def get(self, endpoint):
        self.calls.append(endpoint)
        return copy.deepcopy(self.data[endpoint])


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.provider = Provider()

    def render(self):
        return review(self.provider, DEVTOOL_SHA,
                      pr_number=CURRENT_PR, head_branch=CURRENT_BRANCH)

    def fails(self):
        with self.assertRaises((ReviewError, QualificationError)):
            self.render()

    def current_pr(self):
        return self.provider.data[f"repos/{REPO}/pulls/{CURRENT_PR}"]

    def test_merged_historical_consumers_and_current_pr_pass_source_only(self):
        result = self.render()
        self.assertEqual(result["schema"], SCHEMA)
        self.assertEqual(result["integration_source_consistency"], "passed")
        self.assertEqual(result["consumer_hosted_acceptance"], "passed")
        self.assertEqual(result["current_source_mode"], "current-pr")
        self.assertEqual(result["current_devtool_source"]["pr"], CURRENT_PR)
        self.assertEqual(len(result["historical_consumers"]), 3)
        self.assertTrue(all(s["state"] == "merged" for s in result["historical_consumers"]))
        self.assertFalse(result["merge_permitted"])
        self.assertFalse(result["release_qualified"])
        self.assertFalse(result["physical_device_qualified"])
        self.assertFalse(result["devtool_delivery_session_ci_qualified"])
        self.assertNotIn("all_prs_must_receive_manual_review_and_leave_draft", result["review_blockers"])

    def test_repeatability_and_current_pr_branch_in_provenance(self):
        first, second = self.render(), self.render()
        self.assertEqual(first["provenance_sha256"], second["provenance_sha256"])
        self.assertEqual(len(first["p06_evidence_sha256"]), 64)
        self.assertIn(CURRENT_BRANCH, str(first["current_devtool_source"]))
        self.assertIn("merged", markdown(first))

    def test_bad_compare_urls_refused(self):
        client = IntegrationGhCLI()
        for endpoint in (
            "repos/Mikeyphw/devtool/compare/main...abcd",
            "repos/Mikeyphw/devtool/compare/../main..." + DEVTOOL_SHA,
            "repos/Mikeyphw/devtool/compare/other..." + DEVTOOL_SHA,
            "repos/Mikeyphw/devtool/compare/main..." + DEVTOOL_SHA + "/extra",
        ):
            with self.assertRaises(ReviewError):
                client.get(endpoint)

    def test_invalid_expected_devtool_head_refused(self):
        with self.assertRaises(ReviewError):
            review(self.provider, "abc", pr_number=CURRENT_PR, head_branch=CURRENT_BRANCH)
        with self.assertRaises(ReviewError):
            review(self.provider, "f" * 40, pr_number=CURRENT_PR, head_branch=CURRENT_BRANCH)

    def test_current_pr_selector_necessary(self):
        self.fails_if(lambda: review(self.provider, DEVTOOL_SHA))
        self.fails_if(lambda: review(self.provider, DEVTOOL_SHA,
                                     pr_number=-1, head_branch=CURRENT_BRANCH))
        self.fails_if(lambda: review(self.provider, DEVTOOL_SHA,
                                     pr_number=CURRENT_PR, head_branch="../escape"))
        self.fails_if(lambda: review(self.provider, DEVTOOL_SHA,
                                     pr_number=CURRENT_PR, head_branch=""))

    def fails_if(self, fn):
        with self.assertRaises((ReviewError, QualificationError)):
            fn()

    def test_wrong_pr_head_refused(self):
        self.current_pr()["head"]["sha"] = "f" * 40
        self.fails()

    def test_wrong_pr_branch_refused(self):
        self.current_pr()["head"]["ref"] = "main"
        self.fails()

    def test_fork_refused(self):
        self.current_pr()["head"]["repo"]["full_name"] = "someone/other"
        self.fails()

    def test_closed_or_merged_current_pr_refused(self):
        p = self.current_pr()
        p["state"] = "closed"; self.fails()
        p["state"] = "open"; p["merged_at"] = "2026-11-15T00:00:00Z"
        self.fails()

    def test_current_pr_base_sha_drift_refused(self):
        self.current_pr()["base"]["sha"] = "e" * 40
        self.fails()

    def test_compare_diverged_or_behind_refused(self):
        c = self.provider.data[f"repos/{REPO}/compare/main...{DEVTOOL_SHA}"]
        c["status"] = "diverged"; self.fails()
        c["status"] = "ahead"; c["behind_by"] = 1; self.fails()

    def test_compare_main_and_merge_base_checked(self):
        c = self.provider.data[f"repos/{REPO}/compare/main...{DEVTOOL_SHA}"]
        c["merge_base_commit"]["sha"] = "d" * 40; self.fails()
        c["merge_base_commit"]["sha"] = MAIN_SHA
        c["base_commit"]["sha"] = "d" * 40; self.fails()

    def test_mergeability_unknown_or_conflicted_never_authorizes_merge(self):
        p = self.current_pr()
        p["mergeable"] = None; p["mergeable_state"] = "unknown"
        r = self.render()
        self.assertIn("current_source_review_blocked_or_incomplete", r["review_blockers"])
        self.assertFalse(r["merge_permitted"])
        p["mergeable"] = False; p["mergeable_state"] = "dirty"
        r = self.render()
        self.assertIn("current_source_review_blocked_or_incomplete", r["review_blockers"])
        self.assertFalse(r["release_qualified"])

    def test_historical_consumer_merger_evidence_must_be_real(self):
        s = CONSUMERS[0]
        p = self.provider.data[f"repos/{s.repository}/pulls/{s.pr}"]
        p["merged"] = False; self.fails()
        p["merged"] = True; p["merge_commit_sha"] = "abc"; self.fails()
        p["merge_commit_sha"] = "d" * 40; p["head"]["sha"] = "e" * 40
        self.fails()

    def test_historical_consumer_failed_job_refused(self):
        s = CONSUMERS[2]
        r = self.provider.data[f"repos/{s.repository}/actions/runs?event=pull_request&head_sha={s.source_sha}&per_page=100"]
        r["workflow_runs"][-1]["conclusion"] = "failure"
        self.fails()

    def test_historical_consumer_failed_apk_step_refused(self):
        s = CONSUMERS[2]
        jobs = self.provider.data[f"repos/{s.repository}/actions/runs/{s.pr*10000+2}/attempts/1/jobs?per_page=100"]["jobs"]
        jobs[1]["steps"][-1]["conclusion"] = "failure"
        self.fails()

    def test_historical_pr_provider_race_refused(self):
        original = self.provider.get
        target = f"repos/{CONSUMERS[0].repository}/pulls/{CONSUMERS[0].pr}"
        count = 0
        def racing(endpoint):
            nonlocal count
            value = original(endpoint)
            if endpoint == target:
                count += 1
                if count >= 2:
                    value["merge_commit_sha"] = "e" * 40
            return value
        self.provider.get = racing
        self.fails()

    def test_current_pr_race_refused(self):
        original = self.provider.get
        target = f"repos/{REPO}/pulls/{CURRENT_PR}"
        count = 0
        def racing(endpoint):
            nonlocal count
            value = original(endpoint)
            if endpoint == target:
                count += 1
                if count == 2:
                    value["head"]["sha"] = "f" * 40
            return value
        self.provider.get = racing
        self.fails()

    def test_mergeability_race_refused(self):
        original = self.provider.get
        target = f"repos/{REPO}/pulls/{CURRENT_PR}"
        count = 0
        def racing(endpoint):
            nonlocal count
            value = original(endpoint)
            if endpoint == target:
                count += 1
                if count == 2:
                    value["mergeable"] = False
            return value
        self.provider.get = racing
        self.fails()

    def test_main_race_refused(self):
        original = self.provider.get
        count = 0
        def racing(endpoint):
            nonlocal count
            value = original(endpoint)
            if endpoint == f"repos/{REPO}/branches/main":
                count += 1
                if count == 2:
                    value["commit"]["sha"] = "e" * 40
            return value
        self.provider.get = racing
        self.fails()

    def test_manual_dispatch_only_accepts_live_main(self):
        self.fails_if(lambda: review(self.provider, DEVTOOL_SHA, pr_number=0))
        # Main's real tree is verified using the same provider SHA, even when
        # no live PR exists. The fixture does not imply current PR acceptance.
        self.provider.data[f"repos/{REPO}/branches/main"]["commit"]["sha"] = DEVTOOL_SHA
        value = review(self.provider, DEVTOOL_SHA, pr_number=0)
        self.assertEqual(value["current_source_mode"], "main-manual")
        self.assertFalse(value["merge_permitted"])

    def test_private_atomic_reports(self):
        receipt = self.render()
        with tempfile.TemporaryDirectory() as d:
            target = Path(d)/"evidence"
            jsonfile, doc = write_report(receipt, target)
            self.assertEqual(json.loads(jsonfile.read_text())["provenance_sha256"], receipt["provenance_sha256"])
            self.assertIn("not authorized", doc.read_text())
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            self.assertEqual(jsonfile.stat().st_mode & 0o777, 0o600)
            linked = Path(d)/"linked"
            linked.symlink_to(target, target_is_directory=True)
            with self.assertRaises(ReviewError):
                write_report(receipt, linked)
            jsonfile.unlink(); jsonfile.symlink_to(doc)
            with self.assertRaises(ReviewError):
                write_report(receipt, target)

    def test_read_only_provider_endpoints_and_cleanup_decisions(self):
        result = self.render()
        self.assertTrue(self.provider.calls)
        self.assertTrue(all(endpoint.startswith("repos/") for endpoint in self.provider.calls))
        self.assertTrue(all("POST" not in ep and "dispatch" not in ep for ep in self.provider.calls))
        self.assertTrue(all(x["decision"] in ("retain", "already_removed")
                            for x in result["cleanup_decisions"]))

    def test_local_git_source_binding_and_dirty_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)/"repo"; root.mkdir()
            def git(*args):
                return subprocess.run(["git", "-C", str(root), *args],
                                      text=True, capture_output=True, check=True).stdout.strip()
            git("init", "-b", "feature/test")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.invalid")
            git("remote", "add", "origin", "https://github.com/Mikeyphw/devtool.git")
            (root/".gitignore").write_text("__pycache__/\n")
            git("add", "."); git("commit", "-m", "fixture")
            sha = git("rev-parse", "HEAD")
            self.assertEqual(_local_source(root, sha), git("rev-parse", "HEAD^{tree}"))
            with self.assertRaises(ReviewError):
                _local_source(root, "f" * 40)
            (root/"untracked.txt").write_text("drift")
            with self.assertRaises(ReviewError):
                _local_source(root, sha)


if __name__ == "__main__":
    unittest.main()
