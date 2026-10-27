"""Source-bound replacements for historical PRES/EXEC runtime Markdown reads."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from devtool.exec_g3_gate import audit_exec_promises
from devtool.pres_g2_gate import assert_pres_g2_gate_passes, build_pres_g2_gate
from devtool.source_bound_proofs import PRES_G1_IDS, PRES_G2_IDS, exec_p082_source_proof
from devtool.tui_textual import build_textual_replacement_proof_gate, assert_textual_replacement_gate_passes

ROOT = Path(__file__).resolve().parents[1]


def _assert_digest_is_bound_to_source(receipt) -> None:
    content = b"".join(
        rel.encode() + b"\0" + (ROOT / rel).read_bytes() + b"\0"
        for rel in receipt.source_paths
    )
    assert receipt.schema_version == 1
    assert receipt.source_digest == sha256(content).hexdigest()
    assert receipt.status == "passed"


def test_gates_produce_full_versioned_source_receipts() -> None:
    g1 = build_textual_replacement_proof_gate()
    g2 = build_pres_g2_gate()
    assert tuple(p.id for p in g1.source_proofs) == PRES_G1_IDS
    assert tuple(p.id for p in g2.source_proofs) == PRES_G2_IDS
    for report in (g1, g2):
        assert report.as_dict()["source_proof_schema_version"] == 1
        assert len(report.as_dict()["source_proof_digest"]) == 64
        assert report.missing_reports == ()
        assert report.passed
        for proof in report.source_proofs:
            _assert_digest_is_bound_to_source(proof)


def test_markdown_presence_and_content_never_grant_or_block_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    # If any runtime gate attempts to read a historical report this test fails.
    original = Path.read_text

    def forbid_historical_markdown(self: Path, *args, **kwargs):
        if self.suffix.lower() == ".md":
            raise AssertionError(f"historical Markdown was consulted: {self}")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", forbid_historical_markdown)
    assert_textual_replacement_gate_passes(build_textual_replacement_proof_gate(present_reports=["imaginary.md"]))
    assert_pres_g2_gate_passes(build_pres_g2_gate(present_reports=["imaginary.md"]))
    assert audit_exec_promises(ROOT)["explicit_promises"] == 82


def test_pres_source_monkeypatch_is_detected_even_with_report_names(monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool import presentation_surfaces as surfaces

    original = surfaces.status_from_exo

    def fake_status_from_exo(*args, **kwargs):
        projection = original(*args, **kwargs)
        return replace(projection, status="passed")  # False success in live code.

    monkeypatch.setattr(surfaces, "status_from_exo", fake_status_from_exo)
    with pytest.raises(RuntimeError, match="PRES-X01"):
        build_textual_replacement_proof_gate(present_reports=["PRES_X01_PRESENTATION_PROJECTION_FOUNDATION_REPORT.md"])


def test_pres_g2_source_binding_rejects_different_repo_root(tmp_path: Path) -> None:
    (tmp_path / "devtool").mkdir()
    (tmp_path / "devtool" / "__init__.py").write_text("")
    (tmp_path / "devtool" / "presentation_surfaces.py").write_text("# substituted")
    with pytest.raises((RuntimeError, FileNotFoundError), match="source-proof|No such file"):
        build_pres_g2_gate(repo_root=tmp_path)


def test_report_objects_cannot_pass_with_missing_or_failed_proof_receipts() -> None:
    g1 = build_textual_replacement_proof_gate()
    with pytest.raises(AssertionError, match="missing or failed"):
        assert_textual_replacement_gate_passes(replace(g1, source_proofs=g1.source_proofs[1:]))
    g2 = build_pres_g2_gate()
    with pytest.raises(AssertionError, match="missing or failed"):
        assert_pres_g2_gate_passes(replace(g2, source_proofs=tuple(
            replace(p, status="failed") if p.id == "PRES-X08" else p for p in g2.source_proofs
        )))


def test_exec_p082_executable_semantics_and_digest() -> None:
    receipt = exec_p082_source_proof(ROOT)
    _assert_digest_is_bound_to_source(receipt)
    audited = audit_exec_promises(ROOT)
    assert audited["explicit_promises"] == 82
    assert audited["exec_p082_source_proof"] == receipt.as_dict()
    assert "Gradle" in receipt.contract


def test_exec_p082_fails_closed_when_canonical_verdict_lies(monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.core import execution_semantics as sem

    original = sem.ResolvedValidationContract.evaluate

    def fake_evaluate(self, outcomes):
        # A broken canonical policy treating generic SKIPPED as successful.
        converted = tuple(
            sem.ValidationOutcome(o.check_id, sem.ValidationOutcomeState.PASSED)
            if o.state == sem.ValidationOutcomeState.SKIPPED else o
            for o in outcomes
        )
        return original(self, converted)

    monkeypatch.setattr(sem.ResolvedValidationContract, "evaluate", fake_evaluate)
    with pytest.raises(RuntimeError, match="EXEC-P082-non-gradle-required-validation"):
        exec_p082_source_proof(ROOT)


def test_exec_p082_disallows_substituted_repo_root(tmp_path: Path) -> None:
    with pytest.raises((RuntimeError, FileNotFoundError), match="source-proof|No such file"):
        exec_p082_source_proof(tmp_path)
