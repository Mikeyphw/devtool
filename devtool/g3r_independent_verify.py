"""Independent read-only G3R v3 evidence verifier (historical ledgers retired).

The verified candidate is a source-bound receipt, NOT a release attestation.
No legacy source-disposition or prior-seal JSON is loaded or expected.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from .g3r_obligation_coverage import evaluate as coverage
from .remed_r04b_fixed_point import G3R_REAL_SUBSTRATE_CARRY

RETIRED = (
    "CORE_G3_FINAL_ATTESTATION.json", "ENV_G3_FINAL_ATTESTATION.json",
    "ANDROID_G3_FINAL_ATTESTATION.json", "LIFE_G3_FINAL_ATTESTATION.json",
    "CORE_G3_SOURCE_DISPOSITIONS.json", "ENV_G3_SOURCE_DISPOSITIONS.json",
    "ANDROID_G3_SOURCE_DISPOSITIONS.json", "LIFE_G3_SOURCE_DISPOSITIONS.json",
    "LIFE_G3_FEATURE_DISPOSITIONS.json", "LIFE_G3_PROMISE_DISPOSITIONS.json",
    "REMED_R04B_SOURCE_EVIDENCE_BINDINGS.json", "REMED_R04B_PRESENTATION_SURFACE_MATRIX.json",
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def git(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(["git", "-c", f"safe.directory={repo}", *args],
                          cwd=repo, capture_output=True, timeout=60, check=False)
    check(proc.returncode == 0, f"Git authority failed: {' '.join(args)}: {proc.stderr.decode(errors='replace')}")
    return proc.stdout


def tracked_files(repo: Path) -> list[str]:
    names = git(repo, "ls-files", "-z").decode("utf-8", errors="surrogateescape").split("\0")
    return sorted(name for name in names if name)


def confined(root: Path, name: str) -> Path:
    check(isinstance(name, str) and bool(name) and "\x00" not in name and "\\" not in name
          and not name.startswith("/") and all(s not in ("", ".", "..") for s in name.split("/")),
          "invalid evidence path: " + repr(name))
    result = (root / name).resolve(strict=False)
    check(result.is_relative_to(root) and result.is_file(), "missing or escaped evidence: " + name)
    return result


def expected_lane_log_name(key: str) -> str:
    """Canonical G3R producer filename, never inferred from receipt claims."""
    if key in {"android_g1_gate", "android_g2_gate", "life_g3_fresh_lifecycle_chain"}:
        return f"native-{key}.log"
    if isinstance(key, str) and key.startswith("tests/test_") and key.endswith(".py"):
        stem = key.removeprefix("tests/").removesuffix(".py")
        if stem and all(character.isalnum() or character == "_" for character in stem):
            return f"semantic-{stem}.log"
    raise ValueError("unknown G3R execution lane: " + str(key))


def validate_lane_log(evidence: Path, key: str, run: dict[str, Any], seen: set[str]) -> None:
    """Reject copied, missing, empty or cross-lane log provenance."""
    if "output_file" not in run:
        check(run.get("status") != "PASS", "passed lane missing log")
        return
    filename = run.get("output_file")
    check(type(filename) is str and filename == expected_lane_log_name(key),
          "G3R lane log identity mismatch: " + str(key))
    check(filename not in seen, "G3R duplicate lane log: " + filename)
    seen.add(filename)
    content = confined(evidence / "runs", filename).read_bytes()
    check(run.get("output_sha256") == sha(content), "native/semantic log SHA mismatch: " + key)
    if run.get("status") == "PASS":
        check(bool(content.strip()), "G3R passed lane has empty execution log: " + key)
        check(type(run.get("exit_code")) is int and run["exit_code"] == 0,
              "G3R passed lane has invalid exit code: " + key)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    check(isinstance(value, dict), "expected evidence object: " + str(path))
    return value


def verify(evidence: Path, repo: Path) -> dict[str, Any]:
    evidence = evidence.resolve(strict=True)
    repo = repo.resolve(strict=True)
    check(not git(repo, "status", "--porcelain=v1", "--untracked-files=all").strip(), "source checkout is dirty")
    commit = git(repo, "rev-parse", "HEAD").decode().strip()
    tree = git(repo, "rev-parse", "HEAD^{tree}").decode().strip()
    # No missing historical ledger is permitted to silently re-enable the old gate.
    check(all(not (repo / name).exists() for name in RETIRED), "retired historical artifact unexpectedly present")
    receipt_lines = (evidence / "SHA256SUMS").read_text().splitlines()
    expected: dict[str, str] = {}
    for line in receipt_lines:
        digest, separator, name = line.partition("  ")
        check(separator == "  " and len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest), "bad receipt format")
        check(name != "SHA256SUMS" and name not in expected, "duplicate/self-referential receipt")
        expected[name] = digest
        check(sha(confined(evidence, name).read_bytes()) == digest, "candidate file checksum mismatch: " + name)
    actual = {p.relative_to(evidence).as_posix() for p in evidence.rglob("*") if p.is_file()} - {"SHA256SUMS"}
    check(actual == set(expected), "candidate file inventory mismatch")
    record = load(confined(evidence, "EVIDENCE_RECORD.json"))
    check(record.get("schema_version") == 3 and record.get("format") == "G3R-CURRENT-SOURCE-V3", "obsolete/unsupported candidate")
    check(record.get("source_commit") == commit and record.get("source_tree") == tree, "source SHA/tree mismatch")
    check(record.get("status") == "SOURCE_RECONCILED_CANDIDATE", "source reconciliation blocked")
    check(record.get("current_source_truth_only") is True, "candidate scope claim missing")
    check(record.get("formal_release_qualified") is False and record.get("historical_g3_requalified") is False, "false qualification claim")
    check(record.get("retired_artifacts") == list(RETIRED), "retirement scope mismatch")
    check(record.get("open_real_substrate_obligations") == list(G3R_REAL_SUBSTRATE_CARRY), "real-substrate obligations suppressed")
    source = load(confined(evidence, "SOURCE_MANIFEST.json"))
    check(source.get("schema_version") == 3 and source.get("source_commit") == commit and source.get("source_tree") == tree, "source manifest identity mismatch")
    paths = tracked_files(repo)
    rows = source.get("files")
    check(isinstance(rows, list) and len(rows) == len(paths) and [r.get("path") for r in rows] == paths,
          "source manifest file inventory mismatch")
    parts: list[str] = []
    for row in rows:
        rel = row["path"]
        digest = sha(confined(repo, rel).read_bytes())
        check(row.get("sha256") == digest, "source SHA mismatch: " + rel)
        parts.append(f"{rel}\0{digest}")
    root_digest = sha(("\n".join(parts) + "\n").encode())
    check(source.get("root_sha256") == root_digest, "source digest root mismatch")
    check(load(confined(evidence, "COVERAGE.json")) == coverage(repo), "current coverage contract mismatch")
    runtime = load(confined(evidence, "INDEPENDENT_SEMANTIC_AND_NATIVE_RUNS.json"))
    check(runtime.get("schema_version") == 3 and runtime.get("qualification") == "NOT_QUALIFIED", "native evidence false release claim")
    checks = runtime.get("checks")
    check(isinstance(checks, dict), "missing native lane results")
    seen_lane_logs: set[str] = set()
    for key, run in checks.items():
        check(isinstance(key, str) and isinstance(run, dict), "invalid lane receipt")
        check(run.get("status") in ("PASS", "FAIL", "BLOCKED_TIMEOUT", "BLOCKED_ERROR", "BLOCKED_NOT_NATIVE_TERMUX"), "invalid lane state")
        if run["status"] == "PASS":
            check(type(run.get("exit_code")) is int and run["exit_code"] == 0, "false-success lane receipt")
        validate_lane_log(evidence, key, run, seen_lane_logs)
    return {"verification": "PASS_CURRENT_SOURCE_CANDIDATE_ONLY", "source_commit": commit,
            "source_tree": tree, "source_files": len(paths), "source_root_sha256": root_digest,
            "historical_artifacts_retired": len(RETIRED), "real_substrate_obligations_open": list(G3R_REAL_SUBSTRATE_CARRY),
            "release_qualified": False}
