"""G3R consumer safety, run-truth and historical separation contracts."""
from __future__ import annotations

import json
from pathlib import Path
import stat
from zipfile import ZipFile, ZipInfo

import pytest

from devtool import g3r_evidence_gate as gate


@pytest.fixture
def candidate(tmp_path: Path, monkeypatch):
    root = tmp_path / "evidence"
    root.mkdir()
    record = {
        "schema_version": 2, "source_candidate_status": "SOURCE_RECONCILED_CANDIDATE",
        "current_source_truth_only": True, "immutable_originals_preserved": True,
        "evidence_id": "candidate-test", "source_tree": "1" * 40,
    }
    reconcile = {
        "historical_attribution": "UNCHANGED_FILES_PRESERVED_NOT_RESEALED",
        "ledger_structure_pass": True, "r04b_unresolved_references": 0,
        "all_binding_files_at_head": True, "r04b_paths_at_head": True,
        "r04b_matrix_at_head": True,
    }
    checks = {x: {"status": "PASS", "exit_code": 0,
                  "output_file": x.replace("/", "_") + ".log",
                  "output_sha256": "2" * 64} for x in gate.REQUIRED_LANES}
    runtime = {"host_os": "Android", "native_termux": True,
               "qualification": "NOT_QUALIFIED", "checks": checks}
    payloads = {
        "EVIDENCE_RECORD.json": record,
        "SOURCE_RECONCILIATION.json": reconcile,
        "INDEPENDENT_SEMANTIC_AND_NATIVE_RUNS.json": runtime,
    }
    for name, contents in payloads.items():
        (root / name).write_text(json.dumps(contents))
    def independent_verifier(_evidence, _repo):
        return {"verification": "PASS_CURRENT_SOURCE_CANDIDATE_ONLY",
                "source_commit": "7" * 40, "g3_bindings": 2062, "g3_drift": 352,
                "r04b_bindings": 3893, "r04b_root_sha256": "a" * 64}
    monkeypatch.setattr(gate, "verify", independent_verifier)
    return root


def alter(root: Path, file: str, key: str, value):
    path = root / file
    contents = json.loads(path.read_text())
    contents[key] = value
    path.write_text(json.dumps(contents))


def test_13_lane_native_candidate_retains_release_block(candidate):
    result = gate.evaluate(candidate)
    assert result["status"] == "PASS_CURRENT_SOURCE_AND_NATIVE_LANES"
    assert result["passed_lanes"] == result["required_lanes"] == 13
    assert result["formal_release_qualified"] is False
    assert result["historical_g3_requalified"] is False
    assert result["open_real_substrate_obligations"] == list(gate.G3R_REAL_SUBSTRATE_CARRY)


def test_source_only_does_not_fake_native_lanes(candidate):
    alter(candidate, "INDEPENDENT_SEMANTIC_AND_NATIVE_RUNS.json", "checks", {})
    result = gate.evaluate(candidate, require_native=False)
    assert result["status"] == "PASS_CURRENT_SOURCE_ONLY"
    assert result["passed_lanes"] == 0


def test_missing_required_lane_is_blocked(candidate):
    p = candidate / "INDEPENDENT_SEMANTIC_AND_NATIVE_RUNS.json"
    runtime = json.loads(p.read_text())
    runtime["checks"].pop("life_g3_fresh_lifecycle_chain")
    p.write_text(json.dumps(runtime))
    with pytest.raises(gate.G3REvidenceError, match="incomplete"):
        gate.evaluate(candidate)


def test_additional_unexpected_lane_is_not_complete(candidate):
    p = candidate / "INDEPENDENT_SEMANTIC_AND_NATIVE_RUNS.json"
    runtime = json.loads(p.read_text())
    runtime["checks"]["unexpected-lane"] = dict(next(iter(runtime["checks"].values())))
    p.write_text(json.dumps(runtime))
    with pytest.raises(gate.G3REvidenceError, match="incomplete"):
        gate.evaluate(candidate)


@pytest.mark.parametrize("status,code", [("FAIL", 1), ("PASS", 1), ("BLOCKED_TIMEOUT", None)])
def test_failing_or_falsely_passing_lane_is_blocked(candidate, status, code):
    p = candidate / "INDEPENDENT_SEMANTIC_AND_NATIVE_RUNS.json"
    runtime = json.loads(p.read_text())
    runtime["checks"]["android_g1_gate"].update(status=status, exit_code=code)
    p.write_text(json.dumps(runtime))
    with pytest.raises(gate.G3REvidenceError, match="incomplete"):
        gate.evaluate(candidate)


@pytest.mark.parametrize("file,key,value,reason", [
    ("EVIDENCE_RECORD.json", "schema_version", 3, "generation"),
    ("EVIDENCE_RECORD.json", "source_candidate_status", "BLOCKED", "reconciliation"),
    ("EVIDENCE_RECORD.json", "immutable_originals_preserved", False, "originals"),
    ("EVIDENCE_RECORD.json", "current_source_truth_only", False, "scope"),
    ("SOURCE_RECONCILIATION.json", "historical_attribution", "REWRITTEN", "attribution"),
    ("SOURCE_RECONCILIATION.json", "ledger_structure_pass", False, "reconciled"),
    ("SOURCE_RECONCILIATION.json", "r04b_unresolved_references", 1, "reconciled"),
    ("SOURCE_RECONCILIATION.json", "r04b_matrix_at_head", False, "reconciled"),
    ("INDEPENDENT_SEMANTIC_AND_NATIVE_RUNS.json", "native_termux", False, "native Termux"),
    ("INDEPENDENT_SEMANTIC_AND_NATIVE_RUNS.json", "host_os", "Linux", "native Termux"),
    ("INDEPENDENT_SEMANTIC_AND_NATIVE_RUNS.json", "qualification", "QUALIFIED", "release"),
])
def test_false_evidence_claims_fail_closed(candidate, file, key, value, reason):
    alter(candidate, file, key, value)
    with pytest.raises(gate.G3REvidenceError, match=reason):
        gate.evaluate(candidate)


def test_independent_source_verifier_failure_propagates(candidate, monkeypatch):
    def failure(*_args):
        raise ValueError("source commit mismatch")
    monkeypatch.setattr(gate, "verify", failure)
    with pytest.raises(ValueError, match="source commit mismatch"):
        gate.evaluate(candidate)


def test_zip_candidate_accepts_regular_files(candidate, tmp_path):
    zip_path = tmp_path / "source.zip"
    with ZipFile(zip_path, "w") as z:
        for file in candidate.iterdir():
            z.write(file, arcname=file.name)
    assert gate.evaluate(zip_path)["passed_lanes"] == 13


@pytest.mark.parametrize("name", ["../outside", "/absolute", "runs/../../escape", "dir\\evil", "runs/./x"])
def test_zip_unsafe_names_are_rejected(tmp_path, name):
    zpath = tmp_path / "malicious.zip"
    with ZipFile(zpath, "w") as z:
        z.writestr(name, "bad")
    with pytest.raises(gate.G3REvidenceError, match="Unsafe"):
        with gate._candidate_dir(zpath):
            pass


def test_zip_symlink_is_rejected(tmp_path):
    zpath = tmp_path / "malicious.zip"
    info = ZipInfo("linked")
    info.create_system = 3
    info.external_attr = ((stat.S_IFLNK | 0o777) << 16)
    with ZipFile(zpath, "w") as z:
        z.writestr(info, "../outside")
    with pytest.raises(gate.G3REvidenceError, match="Symlink"):
        with gate._candidate_dir(zpath):
            pass


def test_zip_duplicate_member_is_rejected(tmp_path):
    zpath = tmp_path / "duplicate.zip"
    with ZipFile(zpath, "w") as z:
        z.writestr("a/../b", "1")
        z.writestr("b", "2")
    with pytest.raises(gate.G3REvidenceError):
        with gate._candidate_dir(zpath):
            pass


def test_zip_empty_is_rejected(tmp_path):
    zpath = tmp_path / "empty.zip"
    with ZipFile(zpath, "w"):
        pass
    with pytest.raises(gate.G3REvidenceError, match="member count"):
        with gate._candidate_dir(zpath):
            pass
