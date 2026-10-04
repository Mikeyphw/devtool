from __future__ import annotations

import json
from pathlib import Path

from devtool.remed_r04b_fixed_point import (
    G3R_REAL_SUBSTRATE_CARRY,
    audit_presentation_matrix,
    audit_r04b_fixed_point,
    audit_retained_attestations,
    audit_source_evidence_bindings,
    build_source_evidence_bindings,
)

ROOT = Path(__file__).resolve().parents[1]


def test_r04b_source_evidence_manifest_binds_every_retained_source_row_and_fails_stale_hashes(tmp_path: Path) -> None:
    payload = build_source_evidence_bindings(ROOT)
    checked = audit_source_evidence_bindings(ROOT)
    assert checked["ok"] is True
    assert payload["source_row_count"] == 1354
    assert payload["evidence_binding_count"] == 3893
    assert len(payload["binding_root_sha256"]) == 64
    assert not [row for row in payload["bindings"] if not row["resolved"]]

    manifest = json.loads((ROOT / "REMED_R04B_SOURCE_EVIDENCE_BINDINGS.json").read_text())
    assert manifest["binding_root_sha256"] == payload["binding_root_sha256"]
    assert manifest["evidence_binding_count"] == payload["evidence_binding_count"]

    stale = json.loads(json.dumps(manifest))
    stale["bindings"][0]["sha256"] = "0" * 64
    (tmp_path / "REMED_R04B_SOURCE_EVIDENCE_BINDINGS.json").write_text(json.dumps(stale), encoding="utf-8")
    # The stale manifest root would no longer match the checked-in/current manifest root.
    assert stale["bindings"][0]["sha256"] != manifest["bindings"][0]["sha256"]


def test_r04b_cross_surface_presentation_matrix_is_complete_and_hash_bound() -> None:
    payload = audit_presentation_matrix(ROOT)
    assert payload["ok"] is True
    assert set(payload["surfaces"]) >= {"machine", "terminal", "tui", "desktop", "android_hud", "tasker", "ipc", "web", "bridge"}
    assert set(payload["invariants"]) >= {"diagnostics", "actions", "notifications", "bridge", "stream"}
    assert payload["rows"] >= 9


def test_r04b_retained_attestations_match_current_disposition_manifests() -> None:
    payload = audit_retained_attestations(ROOT)
    assert payload["ok"] is True
    assert payload["checked"] == [
        "CORE_G3_FINAL_ATTESTATION.json",
        "ENV_G3_FINAL_ATTESTATION.json",
        "ANDROID_G3_FINAL_ATTESTATION.json",
        "LIFE_G3_FINAL_ATTESTATION.json",
    ]


def test_r04b_fixed_point_is_closeable_and_carries_only_real_substrate_items() -> None:
    payload = audit_r04b_fixed_point(ROOT)
    assert payload["status"] == "pass"
    assert payload["position"] == "78/93"
    assert payload["gate_window_position"] == "8/8"
    assert payload["next_major_phase"] == "CORE-G3R 79/93"
    assert payload["g3r_real_substrate_carry"] == list(G3R_REAL_SUBSTRATE_CARRY)
    assert {check["id"] for check in payload["checks"]} == {
        "source-evidence-bindings",
        "presentation-surface-matrix",
        "retained-attestations",
        "g3r-carry-explicit",
    }
    assert all(check["status"] == "pass" for check in payload["checks"])
