"""S7 integration review: current Devtool PR + immutable historic consumer evidence.

Read only. GitHub source and CI provenance may be checked here, but neither
merge authorization nor release qualification can be issued by this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any

try:
    from .delivery_cross_consumer import (
        CONSUMERS, CLEANUP_DECISIONS, GhCLI, QualificationError,
        _pr as verify_consumer_pr, verify as verify_consumers,
    )
except ImportError:  # historical standalone read-only zipapp compatibility
    from p06_core import (
        CONSUMERS, CLEANUP_DECISIONS, GhCLI, QualificationError,
        _pr as verify_consumer_pr, verify as verify_consumers,
    )

SCHEMA = "devtool.delivery-integration-review.v2"
REPO = "Mikeyphw/devtool"
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,199}\Z")


class ReviewError(RuntimeError):
    """A source / provider boundary cannot be proven, so qualification stops."""


class IntegrationGhCLI(GhCLI):
    """Extend P06's read-only GET client for one exact SHA comparison shape."""
    _COMPARE = re.compile(
        r"repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/compare/main\.\.\.[0-9a-f]{40}\Z"
    )

    def get(self, endpoint: str) -> dict[str, Any]:
        if "/compare/" not in endpoint:
            return super().get(endpoint)
        if not self._COMPARE.fullmatch(endpoint):
            raise ReviewError("UNSAFE_OR_AMBIGUOUS_COMPARE_ENDPOINT")
        try:
            proc = subprocess.run(
                ["gh", "api", "-X", "GET", "-H", "Accept: application/vnd.github+json", endpoint],
                capture_output=True, text=True, timeout=75, check=False,
                env={**os.environ, "GH_PROMPT_DISABLED": "1"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReviewError("GITHUB_COMPARE_READ_UNAVAILABLE") from exc
        if proc.returncode:
            raise ReviewError("GITHUB_COMPARE_READ_REFUSED")
        try:
            value = json.loads(proc.stdout)
        except (TypeError, ValueError) as exc:
            raise ReviewError("GITHUB_COMPARE_NON_JSON") from exc
        if not isinstance(value, dict):
            raise ReviewError("GITHUB_COMPARE_SHAPE_INVALID")
        return value


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HEX40.fullmatch(value):
        raise ReviewError("INVALID_SOURCE_SHA: " + label)
    return value


def _git(root: Path, *args: str) -> str:
    try:
        p = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True,
            timeout=25, check=False,
            env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewError("LOCAL_GIT_UNAVAILABLE") from exc
    if p.returncode:
        raise ReviewError("LOCAL_GIT_COMMAND_REFUSED: " + args[0])
    return p.stdout.strip()


def _local_source(root: Path, expected_head: str) -> str:
    if root.is_symlink() or not root.is_dir():
        raise ReviewError("UNSAFE_OR_MISSING_REPOSITORY_ROOT")
    root = root.resolve()
    if Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise ReviewError("NOT_EXACT_DEVTOOL_GIT_WORKTREE_ROOT")
    if _git(root, "rev-parse", "HEAD") != expected_head:
        raise ReviewError("LOCAL_HEAD_DRIFT")
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ReviewError("DIRTY_WORKTREE_REFUSED")
    url = _git(root, "remote", "get-url", "origin")
    if not re.fullmatch(
        r"(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)Mikeyphw/devtool(?:\.git)?/?",
        url, re.I,
    ):
        raise ReviewError("DEVTOOL_ORIGIN_MISMATCH")
    return _sha(_git(root, "rev-parse", "HEAD^{tree}"), "local-tree")


def _provider(reader: Any, endpoint: str) -> dict[str, Any]:
    try:
        value = reader.get(endpoint)
    except (KeyError, OSError, ValueError, TypeError, QualificationError) as exc:
        raise ReviewError("PROVIDER_READ_FAILED: " + endpoint.split("?")[0]) from exc
    if not isinstance(value, dict):
        raise ReviewError("PROVIDER_RESPONSE_SHAPE_INVALID")
    return value


def _branch_main(reader: Any, repo: str) -> str:
    p = _provider(reader, f"repos/{repo}/branches/main")
    if p.get("name") != "main" or not isinstance(p.get("commit"), dict):
        raise ReviewError("MAIN_BRANCH_IDENTITY_INVALID: " + repo)
    return _sha(p["commit"].get("sha"), "main:" + repo)


def _pr_binding(p: dict[str, Any], number: int, branch: str, expected_head: str) -> dict[str, Any]:
    h, b = p.get("head"), p.get("base")
    if not isinstance(h, dict) or not isinstance(b, dict):
        raise ReviewError("PR_HEAD_OR_BASE_MISSING")
    if (p.get("number") != number or p.get("state") != "open"
        or p.get("merged_at") is not None or type(p.get("draft")) is not bool
        or h.get("ref") != branch or h.get("sha") != expected_head
        or (h.get("repo") or {}).get("full_name") != REPO
        or b.get("ref") != "main" or (b.get("repo") or {}).get("full_name") != REPO):
        raise ReviewError("CURRENT_PR_SOURCE_OR_STATE_DRIFT")
    mergeable = p.get("mergeable")
    mergeable_state = p.get("mergeable_state")
    if mergeable is not None and type(mergeable) is not bool:
        raise ReviewError("INVALID_MERGEABILITY_TYPE")
    if mergeable_state is not None and not isinstance(mergeable_state, str):
        raise ReviewError("INVALID_MERGEABILITY_STATE")
    return {
        "repository": REPO, "pr": number, "head_branch": branch,
        "head_sha": expected_head, "base_sha": _sha(b.get("sha"), "current-pr-base"),
        "draft": p["draft"], "mergeable": mergeable,
        "mergeable_state": mergeable_state, "state": "open",
        "url": f"https://github.com/{REPO}/pull/{number}",
    }


def _compare(reader: Any, head: str, main_sha: str) -> dict[str, Any]:
    value = _provider(reader, f"repos/{REPO}/compare/main...{head}")
    cbase, mb = value.get("base_commit"), value.get("merge_base_commit")
    if not isinstance(cbase, dict) or not isinstance(mb, dict):
        raise ReviewError("COMPARE_IDENTITY_MISSING")
    if (_sha(cbase.get("sha"), "compare-main") != main_sha
        or _sha(mb.get("sha"), "merge-base") != main_sha):
        raise ReviewError("STALE_OR_DIVERGED_COMPARE_BASE")
    ahead, behind = value.get("ahead_by"), value.get("behind_by")
    if (type(ahead) is not int or type(behind) is not int or ahead < 1
        or behind != 0 or value.get("status") != "ahead"):
        raise ReviewError("NON_FAST_FORWARD_SOURCE_RELATION")
    return {"status": "ahead", "ahead_by": ahead, "behind_by": behind,
            "base_sha": main_sha, "merge_base_sha": main_sha}


def _current_snapshot(reader: Any, number: int, branch: str, head: str, main: str) -> dict[str, Any]:
    if (type(number) is not int or number <= 0 or not isinstance(branch, str)
        or not BRANCH_RE.fullmatch(branch) or branch.startswith("refs/")
        or any(p in (".", "..") for p in branch.split("/"))):
        raise ReviewError("INVALID_CURRENT_PR_SELECTOR")
    p = _pr_binding(_provider(reader, f"repos/{REPO}/pulls/{number}"), number, branch, head)
    if p["base_sha"] != main:
        raise ReviewError("CURRENT_PR_BASE_NOT_MAIN")
    p["main_sha"] = main
    p["comparison"] = _compare(reader, head, main)
    blockers = []
    if p["draft"]:
        blockers.append("current_pr_draft_requires_owner_review")
    if p["mergeable"] is not True or p["mergeable_state"] != "clean":
        blockers.append("current_pr_mergeability_not_confirmed_clean")
    p["merge_review_blockers"] = blockers
    return p


def review(reader: Any, expected_head: str, *, root: Path | None = None,
           pr_number: int = 0, head_branch: str = "") -> dict[str, Any]:
    """Read-only historic consumer provenance and current Devtool source review.

    On pull_request, both PR selector arguments are mandatory. On manual main
    dispatch, selector 0 is accepted only when expected_head is live main.
    """
    head = _sha(expected_head, "requested-devtool-head")
    local_tree = _local_source(root, head) if root is not None else None
    p06 = verify_consumers(reader)
    if (p06.get("hosted_cross_consumer_acceptance") != "passed"
        or len(p06.get("consumers", [])) != len(CONSUMERS)):
        raise ReviewError("CONSUMER_HISTORICAL_QUALIFICATION_INCOMPLETE")
    commit = _provider(reader, f"repos/{REPO}/git/commits/{head}")
    commit_tree = _sha((commit.get("tree") or {}).get("sha"), "devtool-provider-tree")
    if local_tree is not None and local_tree != commit_tree:
        raise ReviewError("LOCAL_TREE_PROVIDER_MISMATCH")
    main = _branch_main(reader, REPO)
    if type(pr_number) is not int or pr_number < 0:
        raise ReviewError("INVALID_CURRENT_PR_SELECTOR")
    if pr_number:
        current = _current_snapshot(reader, pr_number, head_branch, head, main)
        mode = "current-pr"
    else:
        if head_branch or head != main:
            raise ReviewError("MANUAL_MAIN_DISPATCH_SOURCE_NOT_MAIN")
        current = {
            "repository": REPO, "pr": None, "head_branch": "main", "head_sha": head,
            "main_sha": main, "state": "main", "draft": False, "mergeable": None,
            "mergeable_state": "not-applicable", "comparison": {"status": "identical"},
            "url": f"https://github.com/{REPO}/commit/{head}",
            "merge_review_blockers": ["main_mode_has_no_current_pr_approval"],
        }
        mode = "main-manual"
    historical: list[dict[str, Any]] = []
    for spec, evidence in zip(CONSUMERS, p06["consumers"]):
        if evidence.get("repository") != spec.repository or evidence.get("qualified_source_sha") != spec.source_sha:
            raise ReviewError("CONSUMER_EVIDENCE_ORDER_OR_SHA_DRIFT")
        prv = evidence.get("pr") or {}
        if prv.get("head_sha") != spec.source_sha or prv.get("number") != spec.pr:
            raise ReviewError("CONSUMER_PR_BINDING_DRIFT")
        historical.append({
            "repository": spec.repository, "pr": spec.pr, "head_branch": spec.head_branch,
            "head_sha": spec.source_sha, "state": prv["state"],
            "merge_commit_sha": prv.get("merge_commit_sha"),
            "url": prv["url"], "historical_evidence_only": True,
            "hosted_acceptance": evidence["hosted_acceptance"],
        })
    # Provider recheck before output: current live PR/main plus immutable history.
    if pr_number:
        reread = _pr_binding(_provider(reader, f"repos/{REPO}/pulls/{pr_number}"),
                             pr_number, head_branch, head)
        for key in ("base_sha", "draft", "mergeable", "mergeable_state"):
            if reread[key] != current[key]:
                raise ReviewError("CURRENT_PR_CHANGED_DURING_AUDIT")
    if _branch_main(reader, REPO) != main:
        raise ReviewError("MAIN_CHANGED_DURING_AUDIT")
    for spec, original in zip(CONSUMERS, historical):
        fresh = verify_consumer_pr(reader, spec)
        if (fresh["state"] != original["state"]
            or fresh.get("merge_commit_sha") != original.get("merge_commit_sha")):
            raise ReviewError("CONSUMER_PROVENANCE_CHANGED_DURING_AUDIT")
    blockers = [
        "current_devtool_head_ci_and_branch_rules_not_qualified_here",
        "manual_owner_review_required_before_merge",
        "formal_delivery_required_ci_policy_not_verified_here",
        "release_signing_and_distribution_not_qualified",
        "physical_device_qualification_not_performed",
        "no_automated_merge_or_release_authority",
    ]
    if current["merge_review_blockers"]:
        blockers.append("current_source_review_blocked_or_incomplete")
    details = {
        "devtool_source_sha": head, "devtool_tree_sha": commit_tree,
        "devtool_main_sha": main, "current_source_mode": mode,
        "current_devtool_source": current, "historical_consumers": historical,
        "p06_evidence_sha256": p06["evidence_sha256"],
        "review_blockers": blockers, "cleanup_decisions": list(CLEANUP_DECISIONS),
    }
    digest = hashlib.sha256(json.dumps(details, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema": SCHEMA, "provenance_sha256": digest,
        "source_snapshot": "current-head-and-historical-consumer-evidence-nonrelease",
        "integration_source_consistency": "passed",
        "consumer_hosted_acceptance": "passed",
        "release_readiness": "blocked_pending_manual_gates",
        "merge_permitted": False, "auto_merge_permitted": False,
        "release_qualified": False, "physical_device_qualified": False,
        "devtool_delivery_session_ci_qualified": False, **details,
    }


def markdown(value: dict[str, Any]) -> str:
    out = [
        "# S7 — current Devtool integration and historical consumer provenance", "",
        "**Source integration:** verified read-only; **merge and release:** not authorized.", "",
        f"Devtool HEAD: `{value['devtool_source_sha']}`",
        f"Current source mode: `{value['current_source_mode']}`",
        f"Evidence SHA-256: `{value['provenance_sha256']}`", "",
        "| Consumer | Pinned source SHA | Historical PR status |", "|---|---|---|",
    ]
    for row in value["historical_consumers"]:
        out.append(f"| `{row['repository']} #{row['pr']}` | `{row['head_sha']}` | {row['state']} |")
    out += ["", "## Current Devtool PR review", "",
            f"Reference: {value['current_devtool_source']['url']}",
            f"State: {value['current_devtool_source']['state']}",
            "", "## Unresolved checks", ""]
    out += ["- " + item.replace("_", " ") for item in value["review_blockers"]]
    out += ["", "The merged consumer PRs supply historical source-bound CI evidence; "
            "they are not pending merge candidates. Re-run current Devtool CI and review "
            "branch rules independently. Neither release nor merge approval is implied.", ""]
    return "\n".join(out)


def write_report(value: dict[str, Any], directory: Path) -> tuple[Path, Path]:
    if directory.is_symlink():
        raise ReviewError("SYMLINK_EVIDENCE_DIRECTORY_REFUSED")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not directory.is_dir() or (os.name == "posix" and directory.stat().st_mode & 0o077):
        raise ReviewError("EVIDENCE_DIRECTORY_MUST_BE_PRIVATE_0700")
    result = []
    for name, body in (("s7-p07-evidence.json", json.dumps(value, indent=2, sort_keys=True) + "\n"),
                       ("s7-p07-readiness.md", markdown(value))):
        dest = directory / name
        if dest.is_symlink():
            raise ReviewError("SYMLINK_EVIDENCE_FILE_REFUSED")
        fd, temporary = tempfile.mkstemp(prefix=".s7-p07-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(body)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, dest)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        result.append(dest)
    return result[0], result[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only current Devtool + historical consumer S7 qualification")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--pr-number", type=int, default=0, help="GitHub event's current PR number; 0 for main dispatch")
    parser.add_argument("--head-branch", default="", help="GitHub event's current PR head ref")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        value = review(IntegrationGhCLI(), args.expected_head, root=args.repo,
                       pr_number=args.pr_number, head_branch=args.head_branch)
        if args.output:
            files = write_report(value, args.output)
            print("S7_P07_DURABLE_REPORT " + " ".join(str(f) for f in files), file=sys.stderr)
        print(json.dumps(value, indent=2, sort_keys=True) if args.json else markdown(value))
        return 0
    except (ReviewError, QualificationError) as exc:
        print(f"S7_P07_REFUSED: {exc}; MERGE_RELEASE_NOT_QUALIFIED", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
