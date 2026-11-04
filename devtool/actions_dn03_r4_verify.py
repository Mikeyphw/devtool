"""DN03-R4: fail-closed same-run, cross-node .NET evidence verification.

The verifier consumes artifacts downloaded from the current GitHub Actions
attempt and rechecks RM-06/R3 graph and byte identities. Evidence uploaded by
an Actions job is not independent authenticated remote provenance; no release
or signing authority is inferred from this receipt.
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

SHA40 = re.compile(r"[a-f0-9]{40}\Z")
SHA64 = re.compile(r"[a-f0-9]{64}\Z")
SLUG = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}\Z")
MAX_JSON = 128 * 1024


class R4Refused(ValueError):
    """Cannot independently correlate the downloaded native receipts."""


def _git(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True, timeout=18)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise R4Refused("consumer Git identity unavailable") from exc
    if proc.returncode:
        raise R4Refused("consumer Git identity unavailable")
    return proc.stdout.strip()


def _dir(root: Path, *parts: str) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise R4Refused("missing or symlinked evidence directory")
    path = root
    for part in parts:
        if not SLUG.fullmatch(part):
            raise R4Refused("unsafe evidence directory segment")
        path = path / part
        if path.is_symlink() or not path.is_dir():
            raise R4Refused("missing or symlinked evidence directory")
    return path


def _json(root: Path, name: str) -> tuple[dict[str, Any], bytes]:
    if not SLUG.fullmatch(name.replace(".", "-")) or "/" in name or "\\" in name:
        raise R4Refused("unsafe evidence filename")
    file = root / name
    if file.is_symlink() or not file.is_file() or not 0 < file.stat().st_size <= MAX_JSON:
        raise R4Refused("missing, unsafe or oversized evidence file: " + name)
    payload = file.read_bytes()
    try:
        obj = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise R4Refused("malformed evidence file: " + name) from exc
    if not isinstance(obj, dict):
        raise R4Refused("evidence root value is not an object")
    return obj, payload


def _same(obj: Mapping[str, Any], expected: Mapping[str, Any], context: str) -> None:
    if any(type(obj.get(k)) is not type(v) or obj.get(k) != v
           for k, v in expected.items()):
        raise R4Refused("source/run/graph mismatch in " + context)


def _context(root: Path, jobs: list[dict[str, Any]], env: Mapping[str, str]) -> tuple[str, str, int, int]:
    source = env.get("GITHUB_SHA", "")
    repository = env.get("GITHUB_REPOSITORY", "")
    run = env.get("GITHUB_RUN_ID", "")
    attempt = env.get("GITHUB_RUN_ATTEMPT", "")
    if (env.get("GITHUB_ACTIONS") != "true" or
            env.get("RUNNER_OS") != "Linux" or env.get("RUNNER_ARCH") != "X64" or
            not SHA40.fullmatch(source) or
            not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or
            not run.isdecimal() or not attempt.isdecimal() or
            not 0 < int(run) < 10**20 or not 0 < int(attempt) < 10**6 or
            root.resolve() != Path(env.get("GITHUB_WORKSPACE", "")).resolve() or
            _git(root, "rev-parse", "HEAD") != source):
        raise R4Refused("GHA source/run/host or workspace identity unavailable")
    try:
        needs = json.loads(env.get("DN03_R4_NEEDS_JSON", ""))
    except (ValueError, UnicodeError) as exc:
        raise R4Refused("missing/invalid GHA needs results") from exc
    expected = {j["id"] for j in jobs}
    if (not isinstance(needs, dict) or set(needs) != expected or
            any(not isinstance(needs[j], dict) or needs[j].get("result") != "success"
                for j in expected)):
        raise R4Refused("a native DAG job is absent, failed, cancelled, or skipped")
    return source, repository, int(run), int(attempt)


def verify_workflow(root: Path, *, target: str, workflow: str,
                    devtool_sha: str, graph_sha: str,
                    evidence: Path, environment: Mapping[str, str],
                    write: bool = True) -> dict[str, Any]:
    """Recompile the original graph, validate every uploaded local R3 receipt.

    Zero output jobs are still checked, but cannot claim byte verification.
    Test gates remain NOT_RUN until independently parsed test reports exist.
    """
    if any(not SLUG.fullmatch(x) for x in (target, workflow)):
        raise R4Refused("invalid native target/workflow identifier")
    if not SHA40.fullmatch(devtool_sha) or not SHA64.fullmatch(graph_sha):
        raise R4Refused("immutable Devtool/graph pin is missing")
    if root.is_symlink() or not root.is_dir():
        raise R4Refused("consumer checkout must be an actual directory")
    root = root.resolve()
    if evidence.is_symlink() or not evidence.is_dir():
        raise R4Refused("missing or symlinked R4 evidence root")
    evidence = evidence.resolve()
    runner = environment.get("RUNNER_TEMP", "")
    if not runner or Path(runner).is_symlink() or evidence != Path(runner).resolve() / "dn03-r4":
        raise R4Refused("R4 evidence root must be runner-local and isolated")
    from .actions_graph_export import compile_graph
    from .actions_node_evidence import verify_node_evidence
    try:
        graph = compile_graph(root, target=target, workflow=workflow,
                              devtool_sha=devtool_sha,
                              host_profiles="ubuntu-24.04-x64", cache_locks=False)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise R4Refused("native graph recompilation refused: " + str(exc)[:180]) from exc
    jobs = graph.get("jobs")
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 24:
        raise R4Refused("R4 graph node count invalid")
    if (graph.get("graph_sha256") != graph_sha or graph.get("runtime_sha") != devtool_sha or
            any(j.get("provider") != "dotnet" or j.get("profile_id") != "ubuntu-24.04-x64"
                for j in jobs)):
        raise R4Refused("native graph, runner or provider differs from pinned review")
    source, repository, run_id, attempt = _context(root, jobs, environment)
    if graph.get("consumer_commit") != source:
        raise R4Refused("native graph consumer commit differs from GITHUB_SHA")
    source_tree = _git(root, "rev-parse", "HEAD^{tree}")
    # Recompile the complete deployed YAML, not only the native DAG digest.
    # The generated workflow is committed in the consumer and must still match
    # the exact bytes produced from its reviewed config/lockfile state.
    from . import actions_dn03_r4 as r4
    try:
        workflow_data, export_receipt = r4.compile_r4(
            root, target=target, workflow=workflow, devtool_sha=devtool_sha)
        yaml_path, receipt_path = r4._paths(root, target, workflow)
        expected_receipt = (json.dumps(export_receipt, sort_keys=True, indent=2) + "\n").encode()
        if (yaml_path.is_symlink() or not yaml_path.is_file() or
                yaml_path.read_bytes() != workflow_data.encode() or
                receipt_path.is_symlink() or not receipt_path.is_file() or
                receipt_path.read_bytes() != expected_receipt):
            raise R4Refused("deployed consumer workflow/receipt differs from frozen native graph")
        for path in (yaml_path, receipt_path):
            relative = path.relative_to(root).as_posix()
            try:
                tracked = subprocess.run(
                    ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative],
                    capture_output=True, timeout=15,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise R4Refused("cannot verify tracked generated workflow") from exc
            if tracked.returncode:
                raise R4Refused("generated workflow/receipt is not tracked in Git source")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise R4Refused("deployed generated RM-09 workflow verification failed: " + str(exc)[:180]) from exc
    receipt_dirs = (_dir(evidence, "evidence"), _dir(evidence, "host"), _dir(evidence, "bytes"))
    evidence_root, host_root, bytes_root = receipt_dirs
    checked = []
    for job in jobs:
        jid = job["id"]
        if not SLUG.fullmatch(jid):
            raise R4Refused("native DAG has unsafe job identifier")
        e_dir = _dir(evidence_root, jid)
        h_dir = _dir(host_root, jid)
        native, native_bytes = _json(e_dir, "node-evidence.json")
        local, local_bytes = _json(e_dir, "dn03-r3-node-verification.json")
        host, host_bytes = _json(h_dir, "host.json")
        owns_outputs = bool(job.get("outputs"))
        staging = bytes_root if owns_outputs else None
        if owns_outputs:
            _dir(bytes_root, jid)
        try:
            result = verify_node_evidence(
                native, graph, job, source=source, source_tree=source_tree,
                run_id=run_id, attempt=attempt, staging=staging)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise R4Refused("native typed evidence or output bytes failed: " + jid + ": " + str(exc)[:180]) from exc
        _same(result, {"identity_matched": True, "structure_valid": True,
                       "remote_authenticated": False, "release_qualified": False},
              "native verification " + jid)
        test_node = job.get('dotnet_command') == 'test'
        trx_verified = result.get('verified_trx')
        if test_node != isinstance(trx_verified, dict):
            raise R4Refused('native .NET test report missing from independently reparsed R4 bytes')
        if test_node and (trx_verified.get('remote_authenticated') is not False or
                trx_verified.get('release_qualified') is not False or
                trx_verified.get('tests', {}).get('format') != 'trx' or
                trx_verified.get('tests', {}).get('total', 0) <= 0 or
                trx_verified.get('tests', {}).get('report_sha256') != trx_verified.get('sha256')):
            raise R4Refused('R4 reparsed TRX digest/count/trust claim invalid')
        source_identity = (native.get("typed_evidence") or {}).get("source", {})
        if (not isinstance(source_identity, dict) or
                source_identity.get("repository") != repository):
            raise R4Refused("native producer repository differs from hosted consumer")
        _same(host, {
            "kind": "devtool.dn03-host-review", "source_sha": source,
            "devtool_sha": devtool_sha, "graph_sha256": graph_sha,
            "node": jid, "sdk": job["toolchain_version"],
            "legacy_runtime": job.get("dotnet_legacy_runtime", ""),
            "run_id": run_id, "run_attempt": attempt,
            "runtime_inventory_checked": True, "hosted_authenticated": False,
            "release_qualified": False,
        }, "host receipt " + jid)
        if job.get("dotnet_legacy_runtime") and not SHA64.fullmatch(str(host.get("runtime_receipt_sha256", ""))):
            raise R4Refused("legacy runtime digest missing in host receipt")
        _same(local, {
            "kind": "devtool.dn03-r3-local-node-verified",
            "source_sha": source, "source_tree": source_tree,
            "repository": repository, "devtool_sha": devtool_sha,
            "graph_sha256": graph_sha, "job_id": jid,
            "native_node": job["node"], "run_id": run_id,
            "run_attempt": attempt,
            "node_evidence_sha256": sha256(native_bytes).hexdigest(),
            "host_receipt_sha256": sha256(host_bytes).hexdigest(),
            "native_output_bytes_rechecked": True,
            "native_job_qualified": True,
            "test_reports_independently_verified": test_node,
            **({"test_report": trx_verified} if test_node else {}),
            "remote_authenticated": False, "release_qualified": False,
        }, "R3 verification " + jid)
        checked.append({"job_id": jid, "native_node": job["node"],
                        "native_evidence_sha256": sha256(native_bytes).hexdigest(),
                        "r3_verification_sha256": sha256(local_bytes).hexdigest(),
                        "host_sha256": sha256(host_bytes).hexdigest(),
                        "declared_outputs_verified": owns_outputs,
                        "verified_trx": trx_verified if test_node else None})

    payload = {
        "schema_version": 1, "kind": "devtool.dn03-r4-cross-node-review",
        "consumer_repository": repository, "consumer_source_sha": source,
        "consumer_tree": source_tree, "devtool_sha": devtool_sha,
        "graph_sha256": graph_sha, "run_id": run_id,
        "run_attempt": attempt, "node_count": len(jobs),
        "nodes": checked, "native_dag_passed": True,
        "declared_output_nodes_verified": sum(1 for item in checked if item["declared_outputs_verified"]),
        "all_declared_outputs_verified": True,
        "test_reports_independently_verified": (
            bool([j for j in jobs if j.get('dotnet_command') == 'test']) and
            all(x['verified_trx'] is not None for x in checked
                if next(j for j in jobs if j['id'] == x['job_id']).get('dotnet_command') == 'test')),
        "verified_test_node_count": sum(x['verified_trx'] is not None for x in checked),
        "verified_test_case_count": sum(x['verified_trx']['passed']
                                        for x in checked if x['verified_trx'] is not None),
        "remote_authenticated": False, "release_qualified": False,
    }
    if write:
        with (evidence / "acceptance.json").open("x", encoding="utf-8") as out:
            json.dump(payload, out, sort_keys=True, indent=2)
            out.write("\n")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify-workflow",))
    for field in ("repo", "target", "workflow", "devtool-sha", "graph-sha", "evidence"):
        parser.add_argument("--" + field, required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_workflow(
            Path(args.repo), target=args.target, workflow=args.workflow,
            devtool_sha=args.devtool_sha, graph_sha=args.graph_sha,
            evidence=Path(args.evidence), environment=os.environ)
    except (R4Refused, ValueError, OSError, TypeError, KeyError) as exc:
        print("DN03_R4_REFUSED: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"status": "LOCAL_CROSS_NODE_VERIFIED",
                      "node_count": result["node_count"],
                      "remote_authenticated": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
