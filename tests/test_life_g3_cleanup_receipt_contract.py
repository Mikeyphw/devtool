from __future__ import annotations

"""Fresh LIFE-G3 chain truth checks; historical seals are intentionally unchanged."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from devtool.life_g3_gate import (
    LifeG3Error,
    _is_termux,
    _require_cleanup_chain_truth,
    _run_fresh_lifecycle_chain,
)


def _fixture(tmp_path: Path, *, protected: bool = True):
    generated = tmp_path / "generated"
    protected_evidence = tmp_path / "evidence.json"
    protected_evidence.write_text("retained\n", encoding="utf-8")

    selected = SimpleNamespace(candidate_id="selected", resource_id="disposable", path=str(generated))
    protected_item = SimpleNamespace(
        candidate_id="protected", resource_id="run-evidence", path=str(protected_evidence),
        source_snapshot={"exists": True},
    )
    candidates = (selected, protected_item) if protected else (selected,)
    plan = SimpleNamespace(
        plan_id="plan", repository_id="repo", selected=(selected,),
        protected=(protected_item,) if protected else (), candidates=candidates, opaque_actions=(),
    )
    applied = SimpleNamespace(action_id="selected", resource_id="disposable", status="applied", postcondition="absent")
    kept = SimpleNamespace(action_id="protected", resource_id="run-evidence", status="protected", postcondition="")
    receipt = SimpleNamespace(
        plan_id="plan", repository_id="repo", status="partial" if protected else "applied",
        results=(applied, kept) if protected else (applied,),
    )
    return generated, protected_evidence, plan, receipt


def test_valid_partial_receipt_preserves_run_evidence(tmp_path: Path) -> None:
    output, protected, plan, receipt = _fixture(tmp_path)
    _require_cleanup_chain_truth(plan, receipt, output, "disposable")
    assert protected.is_file()


def test_valid_applied_receipt_without_protected_resources(tmp_path: Path) -> None:
    output, _, plan, receipt = _fixture(tmp_path, protected=False)
    _require_cleanup_chain_truth(plan, receipt, output, "disposable")


def test_wrong_aggregate_state_is_not_accepted(tmp_path: Path) -> None:
    output, _, plan, receipt = _fixture(tmp_path)
    receipt.status = "applied"
    with pytest.raises(LifeG3Error, match="aggregate"):
        _require_cleanup_chain_truth(plan, receipt, output, "disposable")


def test_missing_protected_action_fails(tmp_path: Path) -> None:
    output, _, plan, receipt = _fixture(tmp_path)
    receipt.results = (receipt.results[0],)
    with pytest.raises(LifeG3Error, match="inventory"):
        _require_cleanup_chain_truth(plan, receipt, output, "disposable")


def test_protected_action_cannot_be_marked_deleted(tmp_path: Path) -> None:
    output, _, plan, receipt = _fixture(tmp_path)
    receipt.results[1].status = "applied"
    with pytest.raises(LifeG3Error, match="preserve"):
        _require_cleanup_chain_truth(plan, receipt, output, "disposable")


def test_deleted_protected_evidence_is_rejected(tmp_path: Path) -> None:
    output, protected, plan, receipt = _fixture(tmp_path)
    protected.unlink()
    with pytest.raises(LifeG3Error, match="preserve"):
        _require_cleanup_chain_truth(plan, receipt, output, "disposable")


def test_failed_generated_action_is_not_accepted(tmp_path: Path) -> None:
    output, _, plan, receipt = _fixture(tmp_path)
    receipt.results[0].status = "failed"
    with pytest.raises(LifeG3Error, match="managed-output"):
        _require_cleanup_chain_truth(plan, receipt, output, "disposable")


def test_missing_absence_postcondition_is_rejected(tmp_path: Path) -> None:
    output, _, plan, receipt = _fixture(tmp_path)
    receipt.results[0].postcondition = ""
    with pytest.raises(LifeG3Error, match="managed-output"):
        _require_cleanup_chain_truth(plan, receipt, output, "disposable")


def test_live_generated_resource_is_not_accepted(tmp_path: Path) -> None:
    output, _, plan, receipt = _fixture(tmp_path)
    output.mkdir()
    with pytest.raises(LifeG3Error, match="managed-output"):
        _require_cleanup_chain_truth(plan, receipt, output, "disposable")


def test_forged_resource_identity_is_not_accepted(tmp_path: Path) -> None:
    output, _, plan, receipt = _fixture(tmp_path)
    receipt.results[0].resource_id = "other"
    with pytest.raises(LifeG3Error, match="managed-output"):
        _require_cleanup_chain_truth(plan, receipt, output, "disposable")


def test_full_fresh_lifecycle_chain_uses_real_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # This is an implementation regression check. Only a native Termux run
    # without host override can contribute authoritative device proof.
    monkeypatch.setenv("DEVTOOL_LIFE_G3_ALLOW_TEST_HOST", "0" if _is_termux() else "1")
    result = _run_fresh_lifecycle_chain()
    assert result["cleanup_status"] == "partial"  # Referenced RUN evidence remains protected.
    assert result["fresh_process_dogfood_rows"] > 0
