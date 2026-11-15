"""S7 P07: read-only, source-bound integration and release-readiness review.

Reuses P06's provider-qualified consumer gate. This is a review/report generator,
not a GitHub writer, release authority, merge command, or replacement CI engine.
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
    from .delivery_cross_consumer import (CONSUMERS, CLEANUP_DECISIONS,
        GhCLI, QualificationError, verify as verify_consumers)
except ImportError:  # bundled .pyz read-only review before installation
    from p06_core import (CONSUMERS, CLEANUP_DECISIONS,
        GhCLI, QualificationError, verify as verify_consumers)

SCHEMA = "devtool.delivery-integration-review.v1"
REPO = "Mikeyphw/devtool"
PR = 17
BRANCH = "feature/devtool-delivery-s0-s1-20261114"
HEX40 = re.compile(r"[0-9a-f]{40}\Z")

ORDER = ((REPO, PR, BRANCH),) + tuple(
    (s.repository, s.pr, s.head_branch) for s in CONSUMERS
)

class ReviewError(RuntimeError):
    """Refused stale, ambiguous or unsafe integration evidence."""


class IntegrationGhCLI(GhCLI):
    """P06 GET client plus the one strictly delimited GitHub compare URL.

    P06 intentionally blocks `..` in generic provider paths. GitHub compare
    syntax requires `main...<sha>`; do NOT relax P06's generic path guard.
    """
    _COMPARE = re.compile(
        r"repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/compare/main\.\.\.[0-9a-f]{40}\Z"
    )

    def get(self, endpoint: str) -> dict[str, Any]:
        if "/compare/" not in endpoint:
            return super().get(endpoint)
        if not self._COMPARE.fullmatch(endpoint):
            raise ReviewError("UNSAFE_OR_AMBIGUOUS_COMPARE_ENDPOINT")
        try:
            result = subprocess.run(
                ["gh", "api", "-X", "GET", "-H", "Accept: application/vnd.github+json", endpoint],
                capture_output=True, text=True, timeout=75, check=False,
                env={**os.environ, "GH_PROMPT_DISABLED": "1"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReviewError("GITHUB_COMPARE_READ_UNAVAILABLE") from exc
        if result.returncode:
            raise ReviewError("GITHUB_COMPARE_READ_REFUSED")
        try:
            value = json.loads(result.stdout)
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
        proc = subprocess.run(["git", "-C", str(root), *args],
            text=True, capture_output=True, timeout=25, check=False,
            env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewError("LOCAL_GIT_UNAVAILABLE") from exc
    if proc.returncode:
        raise ReviewError("LOCAL_GIT_COMMAND_REFUSED: " + args[0])
    return proc.stdout.strip()


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
    if not re.fullmatch(r"(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)Mikeyphw/devtool(?:\.git)?/?", url, re.I):
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


def _pr_binding(p: dict[str, Any], repo: str, number: int, branch: str,
                head: str) -> dict[str, Any]:
    h, b = p.get("head"), p.get("base")
    if not isinstance(h, dict) or not isinstance(b, dict):
        raise ReviewError("PR_HEAD_OR_BASE_MISSING: " + repo)
    if (p.get("number") != number or p.get("state") != "open"
        or p.get("merged_at") is not None or type(p.get("draft")) is not bool
        or h.get("ref") != branch or h.get("sha") != head
        or (h.get("repo") or {}).get("full_name") != repo
        or b.get("ref") != "main" or (b.get("repo") or {}).get("full_name") != repo):
        raise ReviewError("PR_SOURCE_OR_STATE_DRIFT: " + repo)
    base_sha = _sha(b.get("sha"), "base:" + repo)
    mergeable = p.get("mergeable")
    mergeable_state = p.get("mergeable_state")
    if mergeable is not None and type(mergeable) is not bool:
        raise ReviewError("INVALID_MERGEABILITY_TYPE: " + repo)
    if mergeable_state is not None and not isinstance(mergeable_state, str):
        raise ReviewError("INVALID_MERGEABILITY_STATE: " + repo)
    return {"repository": repo, "pr": number, "head_branch": branch,
        "head_sha": head, "base_sha": base_sha,
        "draft": p["draft"], "mergeable": mergeable,
        "mergeable_state": mergeable_state,
        "url": f"https://github.com/{repo}/pull/{number}"}


def _branch_main(reader: Any, repo: str) -> str:
    p = _provider(reader, f"repos/{repo}/branches/main")
    if p.get("name") != "main" or not isinstance(p.get("commit"), dict):
        raise ReviewError("MAIN_BRANCH_IDENTITY_INVALID: " + repo)
    return _sha(p["commit"].get("sha"), "main:" + repo)


def _compare(reader: Any, repo: str, head: str, main_sha: str) -> dict[str, Any]:
    value = _provider(reader, f"repos/{repo}/compare/main...{head}")
    cbase, mb = value.get("base_commit"), value.get("merge_base_commit")
    if not isinstance(cbase, dict) or not isinstance(mb, dict):
        raise ReviewError("COMPARE_IDENTITY_MISSING: " + repo)
    if _sha(cbase.get("sha"), "compare-main") != main_sha or _sha(mb.get("sha"), "merge-base") != main_sha:
        raise ReviewError("STALE_OR_DIVERGED_COMPARE_BASE: " + repo)
    ahead, behind = value.get("ahead_by"), value.get("behind_by")
    if (type(ahead) is not int or type(behind) is not int or ahead < 1
        or behind != 0 or value.get("status") != "ahead"):
        raise ReviewError("NON_FAST_FORWARD_SOURCE_RELATION: " + repo)
    return {"status": "ahead", "ahead_by": ahead, "behind_by": behind,
        "base_sha": main_sha, "merge_base_sha": main_sha}


def _snapshot(reader: Any, repo: str, number: int, branch: str, head: str) -> dict[str, Any]:
    p = _pr_binding(_provider(reader, f"repos/{repo}/pulls/{number}"), repo, number, branch, head)
    main = _branch_main(reader, repo)
    if p["base_sha"] != main:
        raise ReviewError("PR_BASE_NOT_CURRENT_MAIN: " + repo)
    p["main_sha"] = main
    p["comparison"] = _compare(reader, repo, head, main)
    blockers: list[str] = []
    if p["draft"]:
        blockers.append("draft_pr_requires_owner_review")
    if p["mergeable"] is not True or p["mergeable_state"] != "clean":
        blockers.append("mergeability_not_confirmed_clean")
    p["merge_review_blockers"] = blockers
    return p


def _recheck(reader: Any, sources: list[dict[str, Any]]) -> None:
    """Reject racing provider updates before writing a source-valid receipt."""
    for row in sources:
        repo = row["repository"]
        current = _pr_binding(_provider(reader, f"repos/{repo}/pulls/{row['pr']}"),
                 repo, row["pr"], row["head_branch"], row["head_sha"])
        if (current["base_sha"] != row["base_sha"] or current["draft"] != row["draft"]
            or current["mergeable"] != row["mergeable"]
            or current["mergeable_state"] != row["mergeable_state"]):
            raise ReviewError("PR_CHANGED_DURING_AUDIT: " + repo)
        if _branch_main(reader, repo) != row["main_sha"]:
            raise ReviewError("MAIN_CHANGED_DURING_AUDIT: " + repo)


def review(reader: Any, expected_head: str, *, root: Path | None = None) -> dict[str, Any]:
    expected_head = _sha(expected_head, "requested-devtool-head")
    local_tree = _local_source(root, expected_head) if root is not None else None
    # Existing P06 is the single owner of workflow/attempt/job/step qualification.
    p06 = verify_consumers(reader)
    if p06.get("hosted_cross_consumer_acceptance") != "passed" or len(p06.get("consumers", [])) != 3:
        raise ReviewError("P06_CONSUMER_QUALIFICATION_INCOMPLETE")
    commit = _provider(reader, f"repos/{REPO}/git/commits/{expected_head}")
    commit_tree = _sha((commit.get("tree") or {}).get("sha"), "devtool-provider-tree")
    if local_tree is not None and local_tree != commit_tree:
        raise ReviewError("LOCAL_TREE_PROVIDER_MISMATCH")
    pinned = {spec.repository: spec.source_sha for spec in CONSUMERS}
    pinned[REPO] = expected_head
    snapshots = [_snapshot(reader, repo, n, branch, pinned[repo]) for repo, n, branch in ORDER]
    _recheck(reader, snapshots)
    # A source-consistent snapshot is *not* an authorization to merge or ship.
    merge_order = [{"position": i, "repository": r["repository"], "pr": r["pr"],
        "head_sha": r["head_sha"], "url": r["url"],
        "precondition": "review draft, confirm current exact-head CI and branch rules, obtain owner approval; re-audit after each merge"}
        for i, r in enumerate(snapshots, 1)]
    review_blockers = [
        "all_prs_must_receive_manual_review_and_leave_draft",
        "current_devtool_p07_head_ci_must_pass_after_this_report",
        "formal_delivery_required_ci_policy_and_branch_rules_not_verified_here",
        "consumer_workflows_still_pin_reviewed_s7_p02_devtool_sha",
        "release_signing_and_distribution_not_qualified",
        "android_physical_device_qualification_not_performed",
        "no_automated_merge_or_release_authority",
    ]
    if any(row["mergeable"] is not True or row["mergeable_state"] != "clean" for row in snapshots):
        review_blockers.append("one_or_more_prs_not_confirmed_mergeable")
    details = {"devtool_source_sha": expected_head,
        "devtool_tree_sha": commit_tree,
        "p06_evidence_sha256": p06["evidence_sha256"],
        "sources": snapshots, "merge_sequence": merge_order,
        "review_blockers": review_blockers,
        "cleanup_decisions": list(CLEANUP_DECISIONS)}
    digest = hashlib.sha256(json.dumps(details, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"schema": SCHEMA,
        "provenance_sha256": digest,
        "source_snapshot": "exact-head-complete-nonrelease",
        "integration_source_consistency": "passed",
        "consumer_hosted_acceptance": "passed",
        "release_readiness": "blocked_pending_manual_gates",
        "merge_permitted": False, "auto_merge_permitted": False,
        "release_qualified": False, "physical_device_qualified": False,
        "devtool_delivery_session_ci_qualified": False,
        **details}


def markdown(value: dict[str, Any]) -> str:
    out = ["# S7 P07 — final integration and release-readiness review", "",
        "**Source integration:** verified snapshot; **merge/release:** not authorized.", "",
        f"Provider evidence SHA-256: `{value['provenance_sha256']}`", "",
        "| Suggested order | Repository / PR | Source SHA | Main relation | Draft | Mergeability |", "|---:|---|---|---|---|---|"]
    for i, s in enumerate(value["sources"], 1):
        out.append(f"| {i} | `{s['repository']} #{s['pr']}` | `{s['head_sha']}` | "
                   f"ahead {s['comparison']['ahead_by']}, behind {s['comparison']['behind_by']} | "
                   f"{s['draft']} | {s['mergeable_state']} |")
    out += ["", "## Unresolved review and release gates", ""]
    out += ["- " + item.replace("_", " ") for item in value["review_blockers"]]
    out += ["", "## Safe manual merge sequencing", ""]
    for s in value["merge_sequence"]:
        out.append(f"{s['position']}. {s['url']} — revalidate exact HEAD, policy and CI; no automatic merge")
    out += ["", "## Retained orchestration and cleanup decisions", ""]
    for c in value["cleanup_decisions"]:
        out.append(f"- `{c['repository']}/{c['candidate']}`: {c['decision']} — {c['reason']}")
    out += ["", "P06 consumer workflows are provider-qualified for their **frozen** PR HEADs. "
        "The P07 workflow itself can only be confirmed *after* it runs on the new Devtool HEAD. "
        "No branch-rule, release signing, physical Android, or final reviewer approval is implied.", ""]
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
    parser = argparse.ArgumentParser(description="S7 P07 read-only source-bound integration review")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        value = review(IntegrationGhCLI(), args.expected_head, root=args.repo)
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
