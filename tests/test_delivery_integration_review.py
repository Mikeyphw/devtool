"""S7 P07 source-bound, read-only final integration review adversarial contracts."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

try:
    from devtool.delivery_integration_review import (ORDER, REPO, PR, BRANCH, SCHEMA,
        CONSUMERS, ReviewError, IntegrationGhCLI, _local_source, markdown, review, write_report)
except ImportError:  # executable zipapp self-test
    from p07_core import (ORDER, REPO, PR, BRANCH, SCHEMA,
        CONSUMERS, ReviewError, IntegrationGhCLI, _local_source, markdown, review, write_report)

DEVTOOL_SHA = "48478c8df64073ae63e4c13d85c9844de151cd1e"


class Provider:
    """Synthetic GitHub protocol fixture; never represents a real remote check."""
    def __init__(self):
        self.data = {}
        self.calls = []
        self.main = {}
        self.refs = {}
        for i, (repo, number, branch) in enumerate(ORDER):
            sha = DEVTOOL_SHA if repo == REPO else next(c.source_sha for c in CONSUMERS if c.repository == repo)
            main = f"{i + 1:040x}"
            self.main[repo] = main
            self.refs[repo] = sha
            self.data[f"repos/{repo}/pulls/{number}"] = {
                "number": number, "state": "open", "draft": True, "merged_at": None,
                "head": {"sha": sha, "ref": branch, "repo": {"full_name": repo}},
                "base": {"sha": main, "ref": "main", "repo": {"full_name": repo}},
                "mergeable": True, "mergeable_state": "clean"}
            self.data[f"repos/{repo}/branches/main"] = {
                "name": "main", "commit": {"sha": main}}
            self.data[f"repos/{repo}/compare/main...{sha}"] = {
                "status": "ahead", "ahead_by": i + 1, "behind_by": 0,
                "base_commit": {"sha": main}, "merge_base_commit": {"sha": main}}
        self.data[f"repos/{REPO}/git/commits/{DEVTOOL_SHA}"] = {
            "sha": DEVTOOL_SHA, "tree": {"sha": "a" * 40}}
        for spec in CONSUMERS:
            rows = []
            for i, workflow in enumerate(spec.workflows, 1):
                rid = spec.pr * 10000 + i
                rows.append({"id": rid, "workflow_id": workflow.workflow_id,
                    "name": workflow.name, "head_sha": spec.source_sha,
                    "head_branch": spec.head_branch, "event": "pull_request",
                    "status": "completed", "conclusion": "success",
                    "run_attempt": 1, "created_at": "2026-11-15T00:00:00Z"})
                jobs = []
                for name in (*workflow.jobs, *workflow.optional_jobs):
                    jobs.append({"name": name, "run_id": rid, "status": "completed",
                                 "conclusion": "success" if name in workflow.jobs else "skipped",
                                 "steps": [{"name": n, "status": "completed", "conclusion": "success"}
                                            for n in dict(workflow.steps).get(name, ())]})
                self.data[f"repos/{spec.repository}/actions/runs/{rid}/attempts/1/jobs?per_page=100"] = {
                    "total_count": len(jobs), "jobs": jobs}
            self.data[f"repos/{spec.repository}/actions/runs?event=pull_request&head_sha={spec.source_sha}&per_page=100"] = {
                "total_count": len(rows), "workflow_runs": rows}

    def get(self, endpoint):
        self.calls.append(endpoint)
        return copy.deepcopy(self.data[endpoint])


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.provider = Provider()

    def render(self):
        return review(self.provider, DEVTOOL_SHA)

    def fails(self):
        with self.assertRaises(ReviewError):
            self.render()

    def key(self, repo=REPO, number=PR):
        return f"repos/{repo}/pulls/{number}"

    def test_valid_source_snapshot_still_blocks_release_and_merge(self):
        result = self.render()
        self.assertEqual(result["schema"], SCHEMA)
        self.assertEqual(result["integration_source_consistency"], "passed")
        self.assertEqual(result["consumer_hosted_acceptance"], "passed")
        self.assertEqual(result["release_readiness"], "blocked_pending_manual_gates")
        for label in ("merge_permitted", "auto_merge_permitted", "release_qualified",
                      "physical_device_qualified", "devtool_delivery_session_ci_qualified"):
            self.assertFalse(result[label])
        self.assertEqual(len(result["sources"]), 4)
        self.assertEqual(len(result["merge_sequence"]), 4)
        self.assertEqual(result["merge_sequence"][0]["repository"], REPO)
        self.assertIn("all_prs_must_receive_manual_review_and_leave_draft", result["review_blockers"])

    def test_repeatable_digest_and_p06_reused(self):
        first, second = self.render(), self.render()
        self.assertEqual(first["provenance_sha256"], second["provenance_sha256"])
        self.assertEqual(len(first["p06_evidence_sha256"]), 64)
        self.assertIn("release", markdown(first).lower())

    def test_compare_adapter_allowlists_exact_main_sha_only(self):
        reader = IntegrationGhCLI()
        bad = (
            "repos/Mikeyphw/devtool/compare/main...abcd",
            "repos/Mikeyphw/devtool/compare/../main..." + DEVTOOL_SHA,
            "repos/Mikeyphw/devtool/compare/other..." + DEVTOOL_SHA,
            "repos/Mikeyphw/devtool/compare/main..." + DEVTOOL_SHA + "/extra",
        )
        for path in bad:
            with self.assertRaises(ReviewError):
                reader.get(path)

    def test_wrong_expected_devtool_head_refused(self):
        with self.assertRaises(ReviewError):
            review(self.provider, "b" * 40)

    def test_invalid_expected_head_refused(self):
        with self.assertRaises(ReviewError):
            review(self.provider, "abc")

    def test_devtool_pr_wrong_branch_refused(self):
        self.provider.data[self.key()]["head"]["ref"] = "main"
        self.fails()

    def test_wrong_devtool_pr_head_refused(self):
        self.provider.data[self.key()]["head"]["sha"] = "f" * 40
        self.fails()

    def test_fork_pr_refused(self):
        self.provider.data[self.key()]["head"]["repo"]["full_name"] = "other/devtool"
        self.fails()

    def test_merged_or_closed_pr_refused(self):
        p = self.provider.data[self.key()]
        p["state"] = "closed"
        self.fails()
        p["state"] = "open"; p["merged_at"] = "now"
        self.fails()

    def test_main_base_sha_drift_refused(self):
        self.provider.data[self.key()]["base"]["sha"] = "f" * 40
        self.fails()

    def test_compare_diverged_refused(self):
        self.provider.data[f"repos/{REPO}/compare/main...{DEVTOOL_SHA}"]["status"] = "diverged"
        self.fails()

    def test_compare_behind_refused(self):
        self.provider.data[f"repos/{REPO}/compare/main...{DEVTOOL_SHA}"]["behind_by"] = 1
        self.fails()

    def test_compare_main_and_merge_base_checked(self):
        comp = self.provider.data[f"repos/{REPO}/compare/main...{DEVTOOL_SHA}"]
        comp["merge_base_commit"]["sha"] = "b" * 40
        self.fails()
        comp["merge_base_commit"]["sha"] = self.provider.main[REPO]
        comp["base_commit"]["sha"] = "c" * 40
        self.fails()

    def test_mergeable_unknown_does_not_become_approval(self):
        p = self.provider.data[self.key()]
        p["mergeable"] = None; p["mergeable_state"] = "unknown"
        v = self.render()
        self.assertIn("one_or_more_prs_not_confirmed_mergeable", v["review_blockers"])
        self.assertIn("mergeability_not_confirmed_clean", v["sources"][0]["merge_review_blockers"])
        self.assertFalse(v["merge_permitted"])

    def test_merge_conflict_not_silently_approved(self):
        p = self.provider.data[self.key()]
        p["mergeable"] = False; p["mergeable_state"] = "dirty"
        r = self.render()
        self.assertFalse(r["release_qualified"])
        self.assertIn("one_or_more_prs_not_confirmed_mergeable", r["review_blockers"])

    def test_incomplete_consumer_job_refused(self):
        spec = CONSUMERS[2]
        key = f"repos/{spec.repository}/actions/runs/{spec.pr * 10000 + 2}/attempts/1/jobs?per_page=100"
        self.provider.data[key]["jobs"].pop()
        with self.assertRaises(Exception):
            self.render()

    def test_failed_consumer_apk_step_refused(self):
        spec = CONSUMERS[2]
        key = f"repos/{spec.repository}/actions/runs/{spec.pr * 10000 + 2}/attempts/1/jobs?per_page=100"
        self.provider.data[key]["jobs"][1]["steps"][-1]["conclusion"] = "failure"
        with self.assertRaises(Exception):
            self.render()

    def test_incomplete_consumer_run_page_refused(self):
        spec = CONSUMERS[0]
        key = f"repos/{spec.repository}/actions/runs?event=pull_request&head_sha={spec.source_sha}&per_page=100"
        self.provider.data[key]["total_count"] = 102
        with self.assertRaises(Exception):
            self.render()

    def test_provider_missing_comparison_refused(self):
        self.provider.data.pop(f"repos/{REPO}/compare/main...{DEVTOOL_SHA}")
        self.fails()

    def test_main_race_refused(self):
        original = self.provider.get
        seen = 0
        def racing(endpoint):
            nonlocal seen
            if endpoint == f"repos/{REPO}/branches/main":
                seen += 1
                if seen == 2:
                    obj = original(endpoint)
                    obj["commit"]["sha"] = "b" * 40
                    return obj
            return original(endpoint)
        self.provider.get = racing
        self.fails()

    def test_pr_race_refused(self):
        original = self.provider.get
        seen = 0
        def racing(endpoint):
            nonlocal seen
            if endpoint == self.key():
                seen += 1
                if seen >= 2:  # integration snapshot, then final recheck
                    obj = original(endpoint)
                    obj["head"]["sha"] = "c" * 40
                    return obj
            return original(endpoint)
        self.provider.get = racing
        self.fails()

    def test_mergeability_race_refused(self):
        original = self.provider.get
        seen = 0
        def racing(endpoint):
            nonlocal seen
            obj = original(endpoint)
            if endpoint == self.key():
                seen += 1
                if seen == 2:
                    obj["mergeable"] = False
                    obj["mergeable_state"] = "dirty"
            return obj
        self.provider.get = racing
        self.fails()

    def test_private_atomic_reports(self):
        receipt = self.render()
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "evidence"
            jsonfile, doc = write_report(receipt, target)
            self.assertEqual(json.loads(jsonfile.read_text())["provenance_sha256"], receipt["provenance_sha256"])
            self.assertIn("not authorized", doc.read_text())
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            self.assertEqual(jsonfile.stat().st_mode & 0o777, 0o600)

    def test_symlink_destination_refused(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "evidence"
            write_report(self.render(), target)
            shortcut = Path(d) / "alias"
            shortcut.symlink_to(target, target_is_directory=True)
            with self.assertRaises(ReviewError):
                write_report(self.render(), shortcut)
            (target / "s7-p07-evidence.json").unlink()
            (target / "s7-p07-evidence.json").symlink_to(target / "s7-p07-readiness.md")
            with self.assertRaises(ReviewError):
                write_report(self.render(), target)

    def test_no_mutation_endpoints_and_retained_cleanup(self):
        result = self.render()
        self.assertTrue(self.provider.calls)
        self.assertTrue(all(x.startswith("repos/") for x in self.provider.calls))
        self.assertTrue(all("POST" not in x and "dispatch" not in x for x in self.provider.calls))
        self.assertTrue(all(x["decision"] in ("retain", "already_removed")
                        for x in result["cleanup_decisions"]))

    def test_local_git_source_bound_and_dirty_refusal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"; root.mkdir()
            def git(*args):
                return subprocess.run(["git", "-C", str(root), *args],
                    capture_output=True, text=True, check=True).stdout.strip()
            git("init", "-b", "feature/test"); git("config", "user.name", "fixture")
            git("config", "user.email", "fixture@example.invalid")
            git("remote", "add", "origin", "https://github.com/Mikeyphw/devtool.git")
            (root / ".gitignore").write_text("__pycache__/\n")
            git("add", ".");git("commit", "-m", "fixture")
            sha = git("rev-parse", "HEAD")
            self.assertEqual(_local_source(root, sha), git("rev-parse", "HEAD^{tree}"))
            with self.assertRaises(ReviewError): _local_source(root, "c" * 40)
            (root / "untracked.txt").write_text("dirty\n")
            with self.assertRaises(ReviewError): _local_source(root, sha)


if __name__ == "__main__":
    unittest.main()
