from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source() -> dict:
    return json.loads((ROOT / "LIFE_G3_SOURCE_DISPOSITIONS.json").read_text())


def _feature() -> dict:
    return json.loads((ROOT / "LIFE_G3_FEATURE_DISPOSITIONS.json").read_text())


def _promises() -> dict:
    return json.loads((ROOT / "LIFE_G3_PROMISE_DISPOSITIONS.json").read_text())


def test_life_g3_formal_source_dispositions_are_complete_final_and_hash_bound() -> None:
    payload = _source()
    rows = payload["rows"]
    expected = {f"ART-P{i:03d}" for i in range(1, 85)} | {f"CLEAN-P{i:03d}" for i in range(1, 85)}
    assert payload["source_obligations"] == 168
    assert len(rows) == 168
    assert {row["source_id"] for row in rows} == expected
    assert len({row["source_id"] for row in rows}) == 168
    assert all(row["disposition"] in {"SATISFIED", "SUPERSEDED", "EXPORTED", "RETIRED"} for row in rows)
    for row in rows:
        assert re.fullmatch(r"LIFE-P\d{3}", row["mapped_life_promise"])
        assert 1 <= int(row["mapped_life_promise"].split("P")[1]) <= 109
        assert row["source_text"] and len(row["source_audit_sha256"]) == 64 and len(row["source_row_sha256"]) == 64
        assert row["mapping_basis"] and row["closure_reason"] and len(row["evidence"]) >= 2
        assert set(row["evidence_sha256"]) == set(row["evidence"])
        for rel in row["evidence"]:
            assert (ROOT / rel).is_file(), (row["source_id"], rel)
            assert _sha(ROOT / rel) == row["evidence_sha256"][rel]
        if row["disposition"] == "EXPORTED":
            assert row["export_owner"] and row["export_contract"]
    assert len({row["mapped_life_promise"] for row in rows}) >= 35
    assert len({tuple(row["evidence"]) for row in rows}) >= 10
    assert len({(row["source_audit_sha256"], row["source_line"], row["source_row_sha256"]) for row in rows}) == 168


def test_life_g3_explicit_cross_program_ownership_is_not_reclaimed() -> None:
    rows = {row["source_id"]: row for row in _source()["rows"]}
    expected = {
        "ART-P059": "CORE",
        "ART-P070": "EXEC",
        "ART-P071": "RMP",
        "ART-P072": "CORE",
        "ART-P073": "PRES",
        "CLEAN-P029": "CORE",
        "CLEAN-P045": "CORE",
        "CLEAN-P047": "ANDROID",
        "CLEAN-P048": "ANDROID",
        "CLEAN-P052": "ENV",
        "CLEAN-P056": "DIST",
        "CLEAN-P057": "DIST",
        "CLEAN-P060": "DIST",
        "CLEAN-P061": "DIST",
        "CLEAN-P062": "DIST",
        "CLEAN-P068": "PRES",
        "CLEAN-P072": "PRES",
        "CLEAN-P082": "CORE",
    }
    assert {source_id: rows[source_id]["export_owner"] for source_id in expected} == expected
    assert all(rows[source_id]["disposition"] == "EXPORTED" for source_id in expected)


def test_life_g3_legacy_feature_audit_has_all_45_named_dispositions() -> None:
    payload = _feature()
    rows = payload["rows"]
    expected = {f"FEA-X{i:02d}" for i in range(7)} | {f"FEA-R{i:02d}" for i in range(1, 39)}
    assert payload["feature_obligations"] == 45
    assert len(rows) == 45 and {row["obligation_id"] for row in rows} == expected
    assert all(row["disposition"] in {"SATISFIED", "EXPORTED", "SPLIT", "RETIRED", "SUPERSEDED"} for row in rows)
    for row in rows:
        assert row["source_text"] and row["closure_reason"] and row["evidence"]
        assert set(row["evidence_sha256"]) == set(row["evidence"])
        for rel in row["evidence"]:
            assert _sha(ROOT / rel) == row["evidence_sha256"][rel]
        if row["disposition"] == "EXPORTED":
            assert row["export_owner"] and row["export_contract"]
        if row["disposition"] == "SPLIT":
            assert len(row["subdispositions"]) >= 2
            assert {item["disposition"] for item in row["subdispositions"]} >= {"SATISFIED", "EXPORTED"}
    for number in range(25, 39):
        row = next(item for item in rows if item["obligation_id"] == f"FEA-R{number:02d}")
        assert row["disposition"] == "EXPORTED"




def test_life_g3_every_defined_life_promise_is_individually_evidenced() -> None:
    payload = _promises()
    rows = payload["rows"]
    expected = {f"LIFE-P{i:03d}" for i in range(1, 118)} | {f"LIFE-P{i:03d}" for i in range(123, 128)}
    reserved = {f"LIFE-P{i:03d}" for i in range(118, 123)}
    assert payload["defined_life_promises"] == 122
    assert payload["reserved_ids"] == sorted(reserved)
    assert len(rows) == 122 and {row["promise_id"] for row in rows} == expected
    assert not (reserved & {row["promise_id"] for row in rows})
    for row in rows:
        assert row["promise_text"] and row["disposition"] in {"SATISFIED", "EXPORTED", "SUPERSEDED", "RETIRED"}
        assert row["closure_reason"] and row["evidence"]
        assert set(row["evidence_sha256"]) == set(row["evidence"])
        for rel in row["evidence"]:
            assert (ROOT / rel).is_file(), (row["promise_id"], rel)
            assert _sha(ROOT / rel) == row["evidence_sha256"][rel]

def test_life_g3_external_evidence_dependencies_are_transaction_portable() -> None:
    # Isolated apply transactions are detached Git worktrees.  Historical evidence
    # that exists only as an untracked file in the primary checkout is therefore
    # not a valid LIFE-G3 prerequisite.  Overlay-owned evidence is allowed because
    # it is materialized by this artifact before validation.
    overlay_owned = {
        "LIFE_G3_SOURCE_DISPOSITIONS.json",
        "LIFE_G3_FEATURE_DISPOSITIONS.json",
        "LIFE_G3_PROMISE_DISPOSITIONS.json",
        "devtool/life_g3_gate.py",
        "tests/test_life_g3_final_seal.py",
    }
    refs: set[str] = set()
    for payload in (_source(), _feature(), _promises()):
        for row in payload["rows"]:
            refs.update(str(item) for item in row.get("evidence", []))
    att = json.loads((ROOT / "LIFE_G3_FINAL_ATTESTATION.json").read_text())
    refs.update(str(item) for item in att.get("prior_seals", {}))

    for rel in sorted(refs - overlay_owned):
        assert (ROOT / rel).is_file(), rel
        cp = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", rel],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert cp.returncode == 0, f"external LIFE-G3 evidence is not Git-tracked: {rel}"


def test_life_g3_attestation_binds_all_disposition_manifests_evidence_roots_and_prior_seals() -> None:
    att = json.loads((ROOT / "LIFE_G3_FINAL_ATTESTATION.json").read_text())
    assert att["status"] == "candidate-awaiting-native-termux-qualification"
    assert att["source_obligations"] == 168 and att["feature_obligations"] == 45
    assert att["defined_life_promises"] == 122 and att["workflow_nodes"] == 8
    assert att["next_gate"] == "DIST-G3 61/70"
    assert att["source_dispositions_sha256"] == _sha(ROOT / "LIFE_G3_SOURCE_DISPOSITIONS.json")
    assert att["feature_dispositions_sha256"] == _sha(ROOT / "LIFE_G3_FEATURE_DISPOSITIONS.json")
    assert att["promise_dispositions_sha256"] == _sha(ROOT / "LIFE_G3_PROMISE_DISPOSITIONS.json")

    source_items = []
    for row in _source()["rows"]:
        for rel, digest in row["evidence_sha256"].items():
            source_items.append(f"{row['source_id']}\0{rel}\0{digest}")
    feature_items = []
    for row in _feature()["rows"]:
        for rel, digest in row["evidence_sha256"].items():
            feature_items.append(f"{row['obligation_id']}\0{rel}\0{digest}")
    source_root = hashlib.sha256(("\n".join(sorted(source_items)) + "\n").encode()).hexdigest()
    feature_root = hashlib.sha256(("\n".join(sorted(feature_items)) + "\n").encode()).hexdigest()
    promise_items = []
    for row in _promises()["rows"]:
        for rel, digest in row["evidence_sha256"].items():
            promise_items.append(f"{row['promise_id']}\0{rel}\0{digest}")
    promise_root = hashlib.sha256(("\n".join(sorted(promise_items)) + "\n").encode()).hexdigest()
    assert att["source_evidence_root_sha256"] == source_root
    assert att["feature_evidence_root_sha256"] == feature_root
    assert att["promise_evidence_root_sha256"] == promise_root
    assert att["source_evidence_bindings"] == len(source_items)
    assert att["feature_evidence_bindings"] == len(feature_items)
    assert att["promise_evidence_bindings"] == len(promise_items)
    for rel, digest in att["prior_seals"].items():
        assert _sha(ROOT / rel) == digest


def test_life_g3_wrapper_workflow_is_eight_ordered_stages() -> None:
    cfg = tomllib.loads((ROOT / ".devtool.toml").read_text())
    command = cfg["wrapper"]["commands"]["life-g3"]
    assert command["workflow"] == "roadmap-life-g3" and command["target"] == "devtool"
    workflow = cfg["targets"]["devtool"]["workflows"]["roadmap-life-g3"]
    assert [node["id"] for node in workflow] == [
        "syntax",
        "source-dispositions",
        "feature-dispositions",
        "mutation-recovery-authority",
        "ownership-retention-authority",
        "pack-signing-adoption",
        "cross-program-fixed-point",
        "final-fresh-process",
    ]


def test_life_g3_cross_program_consumers_and_prior_g3_seals_exist() -> None:
    required = [
        "CORE_G3_FINAL_ATTESTATION.json",
        "ENV_G3_FINAL_ATTESTATION.json",
        "ANDROID_G3_FINAL_ATTESTATION.json",
        "DIST_X04_OPERATIONAL_ADOPTION_REPORT.md",
        "tests/test_dist_x04_operational_adoption.py",
        "TEST_X04_HISTORICAL_REUSE_DISTRIBUTED_UX_CONVERGENCE_REPORT.md",
        "tests/test_test_x04_convergence.py",
        "EXEC_X04_POST_CONSUMER_RESEAL_REPORT.md",
        "tests/test_exec_x04_post_consumer_convergence.py",
        "PRES_G2_INTERACTION_RELIABILITY_MILESTONE_GATE_REPORT.md",
        "tests/test_pres_g2_interaction_reliability_gate.py",
    ]
    assert all((ROOT / path).is_file() for path in required)


def test_life_g3_core_generated_surfaces_remain_current() -> None:
    from devtool.cli import build_parser
    from devtool.core_surfaces import build_generated_surfaces, check_generated_surfaces

    assert check_generated_surfaces(build_generated_surfaces(build_parser()), ROOT / "share/core-generated")["status"] == "pass"


def test_life_g3_gate_tooling_respects_host_semantics_and_is_observational() -> None:
    before = subprocess.run(["git", "status", "--porcelain=v1"], cwd=ROOT, text=True, capture_output=True, check=True).stdout
    native_termux = str(os.environ.get("PREFIX") or "").startswith("/data/data/com.termux/") or "/com.termux/" in sys.executable

    gate_env = dict(os.environ)
    if native_termux:
        bypass_env = dict(gate_env)
        bypass_env["DEVTOOL_LIFE_G3_ALLOW_TEST_HOST"] = "1"
        bypass = subprocess.run(
            ["python", "-m", "devtool.life_g3_gate"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            env=bypass_env,
            timeout=180,
        )
        assert bypass.returncode != 0
        assert "construction-host bypass must not be enabled on native Termux" in bypass.stderr
        gate_env.pop("DEVTOOL_LIFE_G3_ALLOW_TEST_HOST", None)
    else:
        gate_env["DEVTOOL_LIFE_G3_ALLOW_TEST_HOST"] = "1"

    cp = subprocess.run(
        ["python", "-m", "devtool.life_g3_gate"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=gate_env,
        timeout=180,
    )
    assert cp.returncode == 0, cp.stderr
    payload = json.loads([line for line in cp.stdout.splitlines() if line.lstrip().startswith("{")][-1])
    assert payload["status"] == "passed"
    assert payload["native_termux"] is native_termux
    assert payload["source_obligations"] == 168 and payload["feature_obligations"] == 45
    assert payload["defined_life_promises"] == 122
    assert payload["cleanup_status"] == "applied" and payload["delivery_status"] == "qualified"
    assert payload["pack_sha256"] and payload["remote_envelope_id"].startswith("life:")
    after = subprocess.run(["git", "status", "--porcelain=v1"], cwd=ROOT, text=True, capture_output=True, check=True).stdout
    assert after == before
