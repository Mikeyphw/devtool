"""S7 P06: read-only exact-HEAD GitHub qualification for three real Devtool consumers.

This is **not** a delivery-session engine, release policy or GitHub mutation path.
Source pins and required workflow IDs are explicit; provider data must match.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Protocol

SCHEMA = "devtool.delivery-cross-consumer-qualification.v1"
DEVTOOL_SOURCE = "416d24140c7f7a4b9833d7cb19df50ca7e7363b8"


class QualificationError(RuntimeError):
    """Provider data is absent, ambiguous, stale, or inconsistent."""


@dataclass(frozen=True)
class Workflow:
    name: str
    workflow_id: int
    jobs: tuple[str, ...]
    steps: tuple[tuple[str, tuple[str, ...]], ...] = ()
    optional_jobs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Consumer:
    repository: str
    pr: int
    head_branch: str
    source_sha: str
    workflows: tuple[Workflow, ...]


CONSUMERS = (
    Consumer("Mikeyphw/scriptexts", 3, "feature/s7-p03-scriptexts-devtool-adoption-20261115",
        "b77453ba15db824fc3f6d711c25df397d5845207", (
        Workflow("Validate", 317669398, ("validate",)),
        Workflow("Devtool S7 P03 scriptexts Consumer Adoption", 418864667,
            ("Devtool consumer audit and exact-tree scaffold (non-release)",),
            (("Devtool consumer audit and exact-tree scaffold (non-release)",
              ("Preserve consumer-specific static workflow contract",
               "Canonical Devtool adoption pilot and source-drift negative test")),)),
    )),
    Consumer("Mikeyphw/chroot-ctl", 1, "feature/s7-p04-go-devtool-adoption-20261115",
        "dc94ebfb4d0522cad8ab8930f0004ac27c4b58cf", (
        Workflow("chrootctl source-contract checks", 324594821, ("source-contracts",)),
        Workflow("Devtool S7 P04 Go Consumer Adoption", 418878280,
            ("Native Devtool Go audit and exact-tree scaffold (non-release)",),
            (("Native Devtool Go audit and exact-tree scaffold (non-release)",
              ("Verify native Go adoption contract and preserve GO17 purity",
               "Canonical source-bound audit and clean-tree proof",
               "Existing Go source-contract gates (non-release)")),)),
        Workflow("chrootctl dual-edition Devtool release candidates", 391110853, ("dual-release",)),
    )),
    Consumer("Mikeyphw/xbsctl", 29, "feature/s7-p05-xbsctl-android-adoption-20261115",
        "5c3f0da68bcec5606750ab12c13689fa83841a98", (
        Workflow("Devtool XBS Go and Android candidates", 386774409, ("go-contract",),
                 optional_jobs=("android-companion-debug",)),
        Workflow("Devtool S7 P05 XBS Android Consumer Adoption", 418896455,
            ("Canonical Devtool Android audit and exact-tree scaffold (non-release)",
             "XBS Android API36 debug APK and unit tests (non-release)"),
            (("Canonical Devtool Android audit and exact-tree scaffold (non-release)",
              ("Canonical Devtool read-only adoption, exact-tree and drift tests",)),
             ("XBS Android API36 debug APK and unit tests (non-release)",
              ("Configure hosted Android SDK (tools-free v4.0.4)",
               "Provision API 36 and Build Tools 36",
               "Configure stable Gradle 9.7.1",
               "Existing native Android debug and unit-test tasks",
               "Verify APK and record source-bound digest",
               "Retain non-release debug evidence")))),
    )),
)

# Documented consumer-specific boundaries, not a permission to delete code.
CLEANUP_DECISIONS = (
    {"repository": "Mikeyphw/scriptexts", "candidate": "scripts/devtool_actions_contract.py",
     "decision": "retain", "reason": "read-only repository-specific static contract, not delivery/GitHub orchestration"},
    {"repository": "Mikeyphw/chroot-ctl", "candidate": "tests/test_devtool_go_adoption.py",
     "decision": "already_removed", "reason": "GO17 forbids committed Python source; Go-native tests own this contract"},
    {"repository": "Mikeyphw/chroot-ctl", "candidate": ".github/workflows/devtool-chrootctl-build.yml",
     "decision": "retain", "reason": "independent dual-edition release-candidate DAG, not redundant S7 PR adoption"},
    {"repository": "Mikeyphw/xbsctl", "candidate": ".github/workflows/devtool-xbs-ci.yml",
     "decision": "retain", "reason": "existing Go/module workflow and optional manual Android lane, separate from P05 hosted PR acceptance"},
)


class GithubReader(Protocol):
    def get(self, endpoint: str) -> dict[str, Any]: ...


class GhCLI:
    """Use only GitHub REST GET through gh api; no workflow dispatch/retry/write."""

    def get(self, endpoint: str) -> dict[str, Any]:
        if not re.fullmatch(r"repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/[^\s]+", endpoint) or ".." in endpoint:
            raise QualificationError("Unsafe GitHub REST endpoint")
        try:
            result = subprocess.run(["gh", "api", "-X", "GET", "-H",
                                     "Accept: application/vnd.github+json", endpoint],
                                    capture_output=True, text=True, check=False,
                                    timeout=75, env={**os.environ, "GH_PROMPT_DISABLED": "1"})
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise QualificationError("GitHub read unavailable or timed out") from exc
        if result.returncode != 0:
            # Do not print provider stderr; it may include credential-bearing URLs.
            raise QualificationError(f"GitHub read refused ({result.returncode}): {endpoint.split('?')[0]}")
        try:
            data = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            raise QualificationError("GitHub returned non-JSON data") from exc
        if not isinstance(data, dict):
            raise QualificationError("Unexpected GitHub response shape")
        return data


def _integer(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise QualificationError("Provider ID or attempt is not a positive integer")
    return value


def _listed(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 100 or any(not isinstance(x, dict) for x in value):
        raise QualificationError(f"Invalid/bounded {label} collection")
    return value


def _success(entry: dict[str, Any], context: str) -> None:
    if entry.get("status") != "completed" or entry.get("conclusion") != "success":
        raise QualificationError(f"Not successful: {context} ({entry.get('status')}/{entry.get('conclusion')})")


def _pr(reader: GithubReader, spec: Consumer) -> dict[str, Any]:
    """Trust exact source and GitHub merger proof, not an obsolete draft gate."""
    p = reader.get(f"repos/{spec.repository}/pulls/{spec.pr}")
    h, b = p.get("head") or {}, p.get("base") or {}
    identity_ok = (
        p.get("number") == spec.pr
        and h.get("sha") == spec.source_sha
        and h.get("ref") == spec.head_branch
        and (h.get("repo") or {}).get("full_name") == spec.repository
        and b.get("ref") == "main"
        and (b.get("repo") or {}).get("full_name") == spec.repository
    )
    merged_at = p.get("merged_at")
    merge_commit = p.get("merge_commit_sha")
    merged = (
        p.get("state") == "closed"
        and p.get("merged") is True
        and p.get("draft") is False
        and isinstance(merged_at, str)
        and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", merged_at) is not None
        and isinstance(merge_commit, str)
        and re.fullmatch(r"[0-9a-f]{40}", merge_commit) is not None
    )
    open_draft = (
        p.get("state") == "open"
        and p.get("draft") is True
        and merged_at is None
        and p.get("merged") in (None, False)
    )
    if not identity_ok or not (merged or open_draft):
        raise QualificationError(f"PR identity or merge proof drift: {spec.repository} #{spec.pr}")
    return {
        "number": spec.pr, "head_branch": spec.head_branch,
        "head_sha": spec.source_sha,
        "url": f"https://github.com/{spec.repository}/pull/{spec.pr}",
        "state": "merged" if merged else "open_draft",
        "merge_commit_sha": merge_commit if merged else None,
    }

def _runs(reader: GithubReader, spec: Consumer) -> dict[int, dict[str, Any]]:
    endpoint = (f"repos/{spec.repository}/actions/runs?event=pull_request&"
                f"head_sha={spec.source_sha}&per_page=100")
    response = reader.get(endpoint)
    runs = _listed(response.get("workflow_runs"), "workflow runs")
    if (type(response.get("total_count")) is not int or response["total_count"] > len(runs)
        or len(runs) == 100):
        raise QualificationError(f"Incomplete workflow run page for {spec.repository}")
    selected: dict[int, dict[str, Any]] = {}
    for wf in spec.workflows:
        matches = [x for x in runs if x.get("workflow_id") == wf.workflow_id]
        if not matches:
            raise QualificationError(f"Missing required workflow {spec.repository}: {wf.name}")
        for x in matches:
            if (x.get("name") != wf.name or x.get("head_sha") != spec.source_sha
                or x.get("event") != "pull_request" or x.get("head_branch") != spec.head_branch):
                raise QualificationError(f"Workflow identity mismatch: {spec.repository}: {wf.name}")
            _integer(x.get("id"))
            _integer(x.get("run_attempt"))
        matches.sort(key=lambda x: (str(x.get("created_at") or ""), x["id"], x["run_attempt"]))
        item = matches[-1]
        _success(item, f"{spec.repository}: {wf.name}")
        selected[wf.workflow_id] = item
    return selected


def _jobs(reader: GithubReader, spec: Consumer, wf: Workflow, run: dict[str, Any]) -> dict[str, Any]:
    run_id = _integer(run["id"])
    attempt = _integer(run["run_attempt"])
    endpoint = (f"repos/{spec.repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
    data = reader.get(endpoint)
    jobs = _listed(data.get("jobs"), "job list")
    if (type(data.get("total_count")) is not int or data["total_count"] > len(jobs)
        or len(jobs) == 100):
        raise QualificationError(f"Incomplete workflow jobs: {spec.repository} run {run_id}")
    names = [x.get("name") for x in jobs]
    if len(names) != len(set(names)):
        raise QualificationError(f"Ambiguous duplicate job names: run {run_id}")
    expected_names = set(wf.jobs) | set(wf.optional_jobs)
    if set(names) != expected_names:
        raise QualificationError(f"Job inventory drift: {spec.repository} workflow {wf.name}")
    rows = []
    for job in jobs:
        name = job.get("name")
        if job.get("run_id") not in (None, run_id):
            raise QualificationError(f"Wrong run identity for {name}")
        if name in wf.jobs:
            _success(job, f"{spec.repository}: job {name}")
        elif job.get("status") != "completed" or job.get("conclusion") not in ("success", "skipped"):
            raise QualificationError(f"Unexpected optional job status: {name}")
        steps = _listed(job.get("steps") or [], "job steps")
        wanted = dict(wf.steps).get(name, ())
        for required in wanted:
            matched = [s for s in steps if s.get("name") == required]
            if len(matched) != 1:
                raise QualificationError(f"Required step missing/ambiguous: {spec.repository}: {required}")
            _success(matched[0], f"{spec.repository}: step {required}")
        rows.append({"name": name, "conclusion": job.get("conclusion"),
                     "required_steps_verified": list(wanted)})
    return {"workflow_id": wf.workflow_id, "workflow": wf.name, "run_id": run_id,
            "attempt": attempt, "conclusion": run.get("conclusion"),
            "url": f"https://github.com/{spec.repository}/actions/runs/{run_id}/attempts/{attempt}",
            "jobs": rows}


def verify(reader: GithubReader, consumers: tuple[Consumer, ...] = CONSUMERS) -> dict[str, Any]:
    """Fail closed before creating a passing receipt. Purely read-only."""
    audited = []
    for spec in consumers:
        pr = _pr(reader, spec)
        runs = _runs(reader, spec)
        qualified = [_jobs(reader, spec, wf, runs[wf.workflow_id]) for wf in spec.workflows]
        audited.append({"repository": spec.repository, "pr": pr,
                        "qualified_source_sha": spec.source_sha, "workflows": qualified,
                        "hosted_acceptance": "passed"})
    canonical = json.dumps(audited, sort_keys=True, separators=(",", ":"))
    return {"schema": SCHEMA,
            "scope": "source-bound-GitHub-PR-workflow-jobs-and-required-steps",
            "consumers": audited,
            "evidence_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "hosted_cross_consumer_acceptance": "passed",
            "devtool_delivery_session_ci_qualified": False,
            "release_qualified": False,
            "physical_device_qualified": False,
            "automatic_merge_permitted": False,
            "cleanup_decisions": list(CLEANUP_DECISIONS)}


def markdown(receipt: dict[str, Any]) -> str:
    lines = ["# S7 P06 — source-pinned cross-consumer acceptance", "",
             f"Status: **{receipt['hosted_cross_consumer_acceptance']}** (hosted non-release only)",
             "", "| Consumer | PR HEAD | Verified workflows | Acceptance |", "|---|---|---:|---|"]
    for c in receipt["consumers"]:
        lines.append(f"| {c['repository']} | `{c['qualified_source_sha']}` | {len(c['workflows'])} | passed |")
    lines += ["", f"Evidence SHA-256: `{receipt['evidence_sha256']}`", "",
              "**Not qualified:** formal Devtool delivery-session required-CI registration,"
              " release signing, physical-device acceptance, merge.", "",
              "## Orchestration cleanup review", ""]
    for row in receipt["cleanup_decisions"]:
        lines.append(f"- `{row['repository']}/{row['candidate']}`: **{row['decision']}** — {row['reason']}")
    lines.append("")
    return "\n".join(lines)


def write_report(receipt: dict[str, Any], directory: Path) -> tuple[Path, Path]:
    """Private, atomic, durable reports; never write into a symlink directory."""
    if directory.is_symlink():
        raise QualificationError("Symlink report destination refused")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not directory.is_dir():
        raise QualificationError("Invalid report destination")
    if os.name == "posix" and directory.stat().st_mode & 0o077:
        raise QualificationError("Report destination must be private (0700)")
    outputs = [("s7-p06-evidence.json", json.dumps(receipt, indent=2, sort_keys=True)+"\n"),
               ("s7-p06-report.md", markdown(receipt))]
    paths = []
    for name, body in outputs:
        path = directory / name
        if path.is_symlink():
            raise QualificationError("Existing report symlink refused")
        fd, temp = tempfile.mkstemp(prefix=".s7-p06-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        paths.append(path)
    return paths[0], paths[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Devtool S7 P06 cross-consumer hosted acceptance, read-only")
    parser.add_argument("--output", type=Path, help="Private (0700) report directory; omitted = stdout only")
    parser.add_argument("--json", action="store_true", help="Print machine-readable receipt")
    args = parser.parse_args(argv)
    try:
        result = verify(GhCLI())
        if args.output:
            files = write_report(result, args.output)
            print("S7_P06_DURABLE_REPORT " + " ".join(str(x) for x in files), file=sys.stderr)
        print(json.dumps(result, indent=2, sort_keys=True) if args.json else markdown(result))
        return 0
    except QualificationError as exc:
        print(f"S7_P06_REFUSED: {exc}; NOT_QUALIFIED", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
