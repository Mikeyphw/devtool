"""DN03-R3: fail-closed verification of a real RM-09 native node's local receipts.

This is a post-execution, same-run integrity check, not independent GitHub
authentication, producer attestation, a test pass, or release qualification.
The existing RM-06 executor owns the graph, handoffs and native command.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
MAX_RECEIPT_BYTES = 128 * 1024


class R3Refused(ValueError):
    """A required source, native node, host or evidence contract did not hold."""


def _read_json(file: Path, *, max_bytes: int = MAX_RECEIPT_BYTES) -> dict[str, Any]:
    if not file.is_file() or file.is_symlink() or file.stat().st_size not in range(1, max_bytes + 1):
        raise R3Refused(f"missing, symlinked or oversized evidence: {file.name}")
    try:
        value = json.loads(file.read_bytes())
    except (ValueError, UnicodeError) as exc:
        raise R3Refused(f"malformed evidence: {file.name}") from exc
    if not isinstance(value, dict):
        raise R3Refused(f"evidence must be an object: {file.name}")
    return value


def _safe_child(root: Path, *segments: str) -> Path:
    if not root.is_dir() or root.is_symlink():
        raise R3Refused("evidence root must be a real directory")
    result = root
    for segment in segments:
        if not SLUG.fullmatch(segment):
            raise R3Refused("unsafe evidence path component")
        result = result / segment
        if result.is_symlink():
            raise R3Refused("symlinked evidence path refused")
    return result


def _git_head(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], check=False,
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise R3Refused("Git worktree identity unavailable") from exc
    if result.returncode:
        raise R3Refused("Git worktree identity unavailable")
    return result.stdout.strip()


def _context(root: Path, environment: Mapping[str, str]) -> tuple[str, str, int, int]:
    source = environment.get("GITHUB_SHA", "")
    devtool_repo = environment.get("GITHUB_REPOSITORY", "")
    run_str = environment.get("GITHUB_RUN_ID", "")
    attempt_str = environment.get("GITHUB_RUN_ATTEMPT", "")
    if (environment.get("GITHUB_ACTIONS") != "true" or
            environment.get("RUNNER_OS") != "Linux" or
            environment.get("RUNNER_ARCH") != "X64" or
            not SHA40.fullmatch(source) or
            not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", devtool_repo) or
            not run_str.isdecimal() or not attempt_str.isdecimal() or
            not (0 < int(run_str) < 10**20) or not (0 < int(attempt_str) < 10**6)):
        raise R3Refused("real hosted source/run/host context unavailable")
    workspace = environment.get("GITHUB_WORKSPACE", "")
    if not workspace or root.resolve() != Path(workspace).resolve():
        raise R3Refused("consumer Git workspace differs from runner workspace")
    if _git_head(root, "rev-parse", "HEAD") != source:
        raise R3Refused("consumer HEAD differs from GitHub source SHA")
    return source, devtool_repo, int(run_str), int(attempt_str)


def verify_native_node(
    root: Path, *, target: str, workflow: str, devtool_sha: str,
    graph_sha: str, job_id: str, outputs: Path, environment: Mapping[str, str],
    write: bool = True,
) -> dict[str, Any]:
    """Recompile the authoritative RM-06 graph; rehash outputs and local host receipt.

    A successful result never promotes a test NOT_RUN gate or uploaded artifact
    to authentication. `write=False` is a read-only review useful in tests.
    """
    if any(not SLUG.fullmatch(name) for name in (target, workflow, job_id)):
        raise R3Refused("unreviewed target, workflow or node")
    if not SHA40.fullmatch(devtool_sha) or not SHA64.fullmatch(graph_sha):
        raise R3Refused("source or graph hash must be an exact immutable digest")
    root = root.resolve()
    source, repository, run_id, attempt = _context(root, environment)
    temp = environment.get("RUNNER_TEMP", "")
    if not temp or not Path(temp).is_dir() or Path(temp).is_symlink():
        raise R3Refused("runner temporary root unavailable or unsafe")
    temp_path = Path(temp).resolve()
    if outputs.is_symlink() or outputs.resolve() != temp_path / "rm06-output":
        raise R3Refused("native output staging differs from RM-06's runner-local path")

    from .actions_graph_export import compile_graph
    from .actions_node_evidence import verify_node_evidence
    try:
        graph = compile_graph(root, target=target, workflow=workflow,
                              devtool_sha=devtool_sha, host_profiles="ubuntu-24.04-x64",
                              cache_locks=False)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise R3Refused("native RM-06 graph recomputation refused: " + str(exc)[:200]) from exc
    if graph.get("graph_sha256") != graph_sha or graph.get("runtime_sha") != devtool_sha:
        raise R3Refused("native graph/source differs from frozen DN03 export")
    jobs = graph.get("jobs")
    if not isinstance(jobs, list) or sum(j.get("id") == job_id for j in jobs if isinstance(j, dict)) != 1:
        raise R3Refused("native node missing or ambiguous in frozen graph")
    job = next(j for j in jobs if j["id"] == job_id)
    if job.get("provider") != "dotnet" or job.get("profile_id") != "ubuntu-24.04-x64":
        raise R3Refused("native node is not the reviewed Linux .NET provider")
    if graph.get("consumer_commit") != source:
        raise R3Refused("compiled native graph binds a different consumer source")
    source_tree = _git_head(root, "rev-parse", "HEAD^{tree}")
    evidence_root = _safe_child(temp_path, "rm06-evidence")
    evidence_dir = _safe_child(evidence_root, job_id)
    native_file = evidence_dir / "node-evidence.json"
    if native_file.is_symlink():
        raise R3Refused("native node evidence cannot be a symlink")
    native = _read_json(native_file)
    try:
        verified = verify_node_evidence(
            native, graph, job, source=source, source_tree=source_tree,
            run_id=run_id, attempt=attempt, staging=outputs,
        )
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise R3Refused("typed native node/source/output verification failed: " + str(exc)[:200]) from exc
    if (verified.get("identity_matched") is not True or
            verified.get("structure_valid") is not True or
            verified.get("remote_authenticated") is not False or
            verified.get("release_qualified") is not False):
        raise R3Refused("native typed verifier yielded unexpected trust claim")

    test_node = job.get('dotnet_command') == 'test'
    trx_verified = verified.get('verified_trx')
    if test_node != isinstance(trx_verified, dict):
        raise R3Refused('native .NET test node is missing independently verified TRX evidence')
    if test_node and (trx_verified.get('remote_authenticated') is not False or
            trx_verified.get('release_qualified') is not False or
            trx_verified.get('tests', {}).get('format') != 'trx' or
            trx_verified.get('tests', {}).get('total', 0) <= 0 or
            trx_verified.get('tests', {}).get('report_sha256') != trx_verified.get('sha256')):
        raise R3Refused('native .NET TRX evidence identity or trust boundary invalid')

    host_dir = _safe_child(_safe_child(temp_path, "dn03-host"), job_id)
    host_file = host_dir / "host.json"
    host = _read_json(host_file, max_bytes=32768)
    required = {
        "kind": "devtool.dn03-host-review",
        "source_sha": source, "devtool_sha": devtool_sha,
        "graph_sha256": graph_sha, "node": job_id,
        "sdk": job["toolchain_version"],
        "legacy_runtime": job.get("dotnet_legacy_runtime", ""),
        "run_id": run_id, "run_attempt": attempt,
        "hosted_authenticated": False, "release_qualified": False,
        "runtime_inventory_checked": True,
    }
    if any(type(host.get(k)) is not type(value) or host.get(k) != value
           for k, value in required.items()):
        raise R3Refused("DN03 host review differs from node/host/run pin")
    if job.get("dotnet_legacy_runtime"):
        token = host.get("runtime_receipt_sha256")
        if not isinstance(token, str) or not SHA64.fullmatch(token):
            raise R3Refused("missing hash-pinned legacy runtime installation receipt")

    payload = {
        "schema_version": 1, "kind": "devtool.dn03-r3-local-node-verified",
        "source_sha": source, "source_tree": source_tree,
        "repository": repository,
        "devtool_sha": devtool_sha, "graph_sha256": graph_sha,
        "job_id": job_id, "native_node": job["node"],
        "run_id": run_id, "run_attempt": attempt,
        "node_evidence_sha256": sha256(native_file.read_bytes()).hexdigest(),
        "host_receipt_sha256": sha256(host_file.read_bytes()).hexdigest(),
        "native_output_bytes_rechecked": True,
        "native_job_qualified": True,
        "test_reports_independently_verified": test_node,
        **({"test_report": trx_verified} if test_node else {}),
        "remote_authenticated": False,
        "release_qualified": False,
    }
    if write:
        target_file = evidence_dir / "dn03-r3-node-verification.json"
        with target_file.open("x", encoding="utf-8") as file:
            json.dump(payload, file, sort_keys=True, indent=2)
            file.write("\n")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify-node",))
    for key in ("repo", "target", "workflow", "devtool-sha", "graph-sha", "job-id", "outputs"):
        parser.add_argument("--" + key, required=True)
    ns = parser.parse_args(argv)
    try:
        result = verify_native_node(
            Path(ns.repo), target=ns.target, workflow=ns.workflow,
            devtool_sha=ns.devtool_sha, graph_sha=ns.graph_sha,
            job_id=ns.job_id, outputs=Path(ns.outputs),
            environment=os.environ,
        )
    except (R3Refused, ValueError, OSError, KeyError, TypeError) as exc:
        print("DN03_R3_REFUSED: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"status": "LOCAL_NODE_VERIFIED", "job_id": result["job_id"],
                      "native_output_bytes_rechecked": True,
                      "remote_authenticated": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
