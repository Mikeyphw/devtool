#!/usr/bin/env python3
"""FC-08: source-bound real-host evidence *collection*, not self-certification.

Offline observations never become hosted/device/consumer qualification.  The
GitHub verifier authenticates run/job/artifact metadata using the GitHub API,
but deliberately does not claim tests or artifact contents have been audited.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SCHEMA = "devtool-fc08-evidence-v1"
LANES = {
    "github-actions-dag": "GitHub Actions DAG, fan-out, cache and dispatch evidence",
    "windows-dotnet-msvc": "Real Windows .NET restore, TRX/coverage, MSVC and ABI evidence",
    "linux-polyglot": "Linux Go/Python/Node/JVM/Rust/C/C++/shell native evidence",
    "macos-native": "Native macOS compiler/runtime and admission evidence",
    "termux-arm64": "Android Termux ABI, native CTest, shell and recovery evidence",
    "android-signing": "Android installed build and signing provenance",
    "consumer-adoption": "Real Telegram/Studyflow/BKX adoption and guarded recovery",
}
HOSTED_LANES = {"github-actions-dag", "windows-dotnet-msvc", "linux-polyglot", "macos-native"}
OS_REQUIREMENT = {"windows-dotnet-msvc":"Windows", "linux-polyglot":"Linux", "macos-native":"macOS"}


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, timeout=20).strip()


def source(root: Path) -> dict[str, str]:
    return {"commit": _git(root, "rev-parse", "HEAD"), "tree": _git(root, "rev-parse", "HEAD^{tree}")}


def plan(root: Path) -> dict:
    return {"schema": SCHEMA, "kind": "fc08-qualification-plan", "source": source(root),
            "status": "PENDING_REAL_EVIDENCE", "release_qualified": False,
            "lanes": [{"id": k, "purpose": v, "status": "NOT_RUN", "qualified": False}
                      for k, v in LANES.items()],
            "g1_full_regression": "PENDING_COMPLETE_EXECUTION",
            "note": "Local/model/simulated passes never substitute for authenticated host, device, signing or consumer receipts."}


def _run(argv: list[str]) -> dict:
    try:
        p = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=20, check=False)
        out = p.stdout[:16000]
        return {"command": argv, "status": "PASS" if p.returncode == 0 else "FAILED",
                "exit_code": p.returncode, "output_sha256": hashlib.sha256(p.stdout.encode()).hexdigest(),
                "output_excerpt": out[:3000]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": argv, "status": "UNAVAILABLE", "error_type": type(exc).__name__}


def host_probe(root: Path, lane: str, runner_os: str, command_runner: Callable = _run) -> dict:
    if lane not in HOSTED_LANES:
        raise ValueError("host-probe is not a device, signing or consumer acceptance tool")
    expected_os = OS_REQUIREMENT.get(lane)
    observed_os = platform.system()
    expected_system = {"Windows":"Windows", "macOS":"Darwin", "Linux":"Linux"}
    if expected_os and observed_os != expected_system[expected_os]:
        raise ValueError("wrong physical OS for selected FC-08 host lane")
    if runner_os and runner_os != ({"Darwin":"macOS"}.get(observed_os, observed_os)):
        raise ValueError("runner OS claim contradicts actual platform")
    probes = {
        "windows-dotnet-msvc": [["dotnet", "--info"], ["cmake", "--version"], ["where", "cl"]],
        "linux-polyglot": [["go", "version"], ["python3", "--version"], ["rustc", "--version"],
                           ["node", "--version"], ["java", "-version"], ["cmake", "--version"],
                           ["meson", "--version"], ["bash", "--version"]],
        "macos-native": [["clang++", "--version"], ["cmake", "--version"], ["python3", "--version"]],
        "github-actions-dag": [["git", "--version"], ["python3", "--version"]],
    }
    observed = {"schema": SCHEMA, "kind": "host-tool-observation", "lane": lane,
                "source": source(root), "observed_at": datetime.now(timezone.utc).isoformat(),
                "observed_os": observed_os, "machine": platform.machine(),
                "workflow_ref": os.environ.get("GITHUB_WORKFLOW_REF", ""),
                "github_repository": os.environ.get("GITHUB_REPOSITORY", ""),
                "github_run_id": os.environ.get("GITHUB_RUN_ID", ""),
                "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
                "github_sha": os.environ.get("GITHUB_SHA", ""),
                "claims_checked_against_provider": False, "execution_semantics_qualified": False,
                "release_qualified": False,
                "probes": [command_runner(x) for x in probes[lane]]}
    observed["status"] = "OBSERVED_NOT_EXTERNALLY_VERIFIED"
    return observed



def termux_probe(root: Path, command_runner: Callable = _run) -> dict:
    # The shell, SDK and prefix must be observed on the *real* Android device;
    # neither environment variable TERMUX_VERSION nor a fake compiler qualifies.
    actual_prefix = str(sys.prefix)
    if "/com.termux/" not in actual_prefix and not actual_prefix.startswith("/data/data/com.termux/"):
        raise ValueError("not executing inside the Termux Python installation")
    if platform.system() != "Linux" or platform.machine().lower() not in {"aarch64", "arm64"}:
        raise ValueError("not a native ARM64 Termux host")
    return {"schema": SCHEMA, "kind": "termux-device-observation", "lane": "termux-arm64",
            "source": source(root), "observed_at": datetime.now(timezone.utc).isoformat(),
            "runtime_python_prefix": actual_prefix, "platform": platform.platform(),
            "status": "DEVICE_OBSERVED_ACCEPTANCE_PENDING", "release_qualified": False,
            "native_device_gates_qualified": False,
            "probes": [command_runner(argv) for argv in (["getprop", "ro.build.version.sdk"],
                        ["clang++", "--version"], ["cmake", "--version"],
                        ["meson", "--version"], ["python3", "--version"])],
            "unrun_checks": ["FC01 crash safety native suite", "RM13 ARM64 CTest/Meson binary checks",
                             "RM14 shell and offline refusal", "RM16 narrow UI", "Android signing and installation"]}


def verify_github_metadata(fetch: Callable[[str], dict], *, repo: str, run_id: int, job_id: int,
                           commit: str, tree: str, attempt: int, workflow_path: str,
                           lane: str, artifact_name: str) -> dict:
    """Verify provider API metadata only; do not certify receipt body/semantics."""
    def refuse(why: str) -> dict:
        return {"schema": SCHEMA, "kind": "github-provider-verification", "lane": lane,
                "status": "REFUSED", "reason": why, "qualified": False,
                "execution_semantics_qualified": False, "release_qualified": False}
    if lane not in HOSTED_LANES:
        return refuse("hosted GitHub cannot qualify device, signing or local consumer gates")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        return refuse("invalid repository name")
    if not re.fullmatch("[a-fA-F0-9]{40}", commit) or not re.fullmatch("[a-fA-F0-9]{40}", tree):
        return refuse("source SHA is missing or malformed")
    prefix = f"/repos/{repo}"
    run = fetch(f"{prefix}/actions/runs/{run_id}")
    if run.get("head_sha") != commit or run.get("run_attempt") != attempt:
        return refuse("run source or attempt mismatch")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        return refuse("run not completed successfully")
    if run.get("repository", {}).get("full_name", "").lower() != repo.lower():
        return refuse("repository identity mismatch")
    if str(run.get("path") or "").split("@", 1)[0] != workflow_path:
        return refuse("workflow path mismatch")
    commit_record = fetch(f"{prefix}/commits/{commit}")
    if commit_record.get("commit", {}).get("tree", {}).get("sha") != tree:
        return refuse("Git tree mismatch")
    jobs = fetch(f"{prefix}/actions/runs/{run_id}/jobs?per_page=100&filter=all")
    if int(jobs.get("total_count", 0)) > len(jobs.get("jobs") or []):
        return refuse("job API pagination incomplete")
    job = next((x for x in jobs.get("jobs", []) if x.get("id") == job_id), None)
    if not job or job.get("run_id") != run_id or job.get("run_attempt") != attempt:
        return refuse("job identity or attempt mismatch")
    if job.get("status") != "completed" or job.get("conclusion") != "success":
        return refuse("job not completed successfully")
    os_expected = OS_REQUIREMENT.get(lane)
    if os_expected and job.get("runner_name") is None and job.get("labels") is None:
        return refuse("runner host authority not recorded")
    if os_expected:
        # GitHub job labels or runner_name can describe custom runners, but are
        # insufficient to prove an actual physical OS. Require a hosted label.
        labels = set(str(x).lower() for x in job.get("labels") or [])
        expected = {"Windows":"windows", "Linux":"ubuntu", "macOS":"macos"}[os_expected]
        if not any(expected in x for x in labels):
            return refuse("runner OS not supported by authenticated labels")
    artifacts = fetch(f"{prefix}/actions/runs/{run_id}/artifacts?per_page=100")
    if int(artifacts.get("total_count", 0)) > len(artifacts.get("artifacts") or []):
        return refuse("artifact API pagination incomplete")
    item = next((a for a in artifacts.get("artifacts") or [] if a.get("name") == artifact_name), None)
    if not item or item.get("expired") or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", str(item.get("digest") or "")):
        return refuse("matching live SHA-256-artifact evidence missing or malformed")
    if (item.get("workflow_run") or {}).get("head_sha") != commit:
        return refuse("artifact source does not match")
    return {"schema": SCHEMA, "kind": "github-provider-verification", "lane": lane,
            "status": "HOSTED_METADATA_VERIFIED_ONLY", "source": {"commit": commit, "tree": tree},
            "github_repository": repo, "github_run_id": run_id, "github_job_id": job_id,
            "run_attempt": attempt, "artifact_id": item.get("id"), "artifact_digest": item["digest"],
            "workflow_path": workflow_path, "provider_authenticated": True,
            "artifact_contents_verified": False, "execution_semantics_qualified": False,
            "release_qualified": False}


def github_fetch_factory(token: str) -> Callable[[str], dict]:
    if not token:
        raise ValueError("a GitHub token is required for provider-authenticated verification")
    def fetch(path: str) -> dict:
        request = Request("https://api.github.com" + path,
                          headers={"Authorization": "Bearer " + token,
                                   "Accept": "application/vnd.github+json",
                                   "X-GitHub-Api-Version": "2022-11-28"})
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    return fetch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("plan"); sub.add_argument("--root", type=Path, default=Path.cwd())
    sub = commands.add_parser("host-probe")
    sub.add_argument("--root", type=Path, default=Path.cwd())
    sub.add_argument("--lane", choices=sorted(HOSTED_LANES), required=True)
    sub.add_argument("--runner-os", default="")
    sub = commands.add_parser("device-probe")
    sub.add_argument("--root", type=Path, default=Path.cwd())
    sub = commands.add_parser("github-verify")
    sub.add_argument("--repo", required=True); sub.add_argument("--run-id", type=int, required=True)
    sub.add_argument("--job-id", type=int, required=True); sub.add_argument("--commit", required=True)
    sub.add_argument("--tree", required=True); sub.add_argument("--attempt", type=int, required=True)
    sub.add_argument("--workflow-path", required=True)
    sub.add_argument("--lane", choices=sorted(LANES), required=True)
    sub.add_argument("--artifact-name", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "plan":
            result = plan(args.root.resolve())
        elif args.command == "host-probe":
            result = host_probe(args.root.resolve(), args.lane, args.runner_os)
        elif args.command == "device-probe":
            result = termux_probe(args.root.resolve())
        else:
            result = verify_github_metadata(github_fetch_factory(os.environ.get("GITHUB_TOKEN", "")),
                      repo=args.repo, run_id=args.run_id, job_id=args.job_id,
                      commit=args.commit, tree=args.tree, attempt=args.attempt,
                      workflow_path=args.workflow_path, lane=args.lane,
                      artifact_name=args.artifact_name)
    except (ValueError, subprocess.CalledProcessError, HTTPError, URLError, TimeoutError, OSError) as exc:
        result = {"schema": SCHEMA, "status": "UNAVAILABLE_OR_REFUSED", "reason": type(exc).__name__,
                  "release_qualified": False}
    text = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end="")
    return 0 if result.get("status") not in {"REFUSED", "UNAVAILABLE_OR_REFUSED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
