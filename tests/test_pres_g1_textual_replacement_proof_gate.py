from __future__ import annotations

from pathlib import Path

import pytest

from devtool.cli import build_parser
from devtool.tui_textual import (
    TextualAvailability,
    assert_textual_replacement_gate_passes,
    build_textual_replacement_proof_gate,
    build_tui_backend_notice,
    required_pres_gate_reports,
    resolve_tui_backend_selection,
    textual_replacement_hardening_contract,
)


def test_pres_g1_required_reports_are_complete_in_repository() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    present = [path.name for path in repo_root.glob("PRES_X*_REPORT.md")]
    gate = build_textual_replacement_proof_gate(present_reports=present)
    assert gate.passed is True
    assert gate.missing_reports == ()
    assert required_pres_gate_reports() == (
        "PRES_X01_PRESENTATION_PROJECTION_FOUNDATION_REPORT.md",
        "PRES_X02_PRESENTATION_LAYOUT_INTERACTIONS_REPORT.md",
        "PRES_X03_CROSS_SURFACE_RENDERING_ADOPTION_REPORT.md",
        "PRES_X04_TEXTUAL_TUI_SUBSTRATE_REPORT.md",
        "PRES_X05_TEXTUAL_FEATURE_PARITY_REPORT.md",
        "PRES_X06_TEXTUAL_DEFAULT_REPLACEMENT_REPORT.md",
        "PRES_X07_TEXTUAL_REPLACEMENT_HARDENING_REPORT.md",
    )


def test_pres_g1_fails_closed_when_a_pres_report_is_missing() -> None:
    present = [name for name in required_pres_gate_reports() if not name.startswith("PRES_X04")]
    with pytest.raises(AssertionError, match="missing report evidence"):
        build_textual_replacement_proof_gate(present_reports=present)


def test_gate_machine_contract_proves_textual_default_and_renderer_only() -> None:
    gate = build_textual_replacement_proof_gate()
    machine = gate.as_dict()
    assert machine["schema_version"] == 1
    assert machine["gate_id"] == "PRES-G1"
    assert machine["passed"] is True
    assert machine["owns_truth"] is False
    assert machine["machine_output_changed"] is False
    assert machine["default_contract"]["default_backend"] == "textual"
    assert machine["hardening_contract"]["primary_backend"] == "textual"
    assert machine["gate_digest"]


def test_gate_checks_all_major_replacement_invariants() -> None:
    gate = build_textual_replacement_proof_gate()
    by_id = {item.id: item.status for item in gate.items}
    assert by_id == {
        "pres-report-window-complete": "passed",
        "textual-is-default": "passed",
        "auto-prefers-textual": "passed",
        "fallback-is-fenced": "passed",
        "feature-parity-complete": "passed",
        "cross-surface-parity": "passed",
        "renderer-only-authority": "passed",
    }


def test_backend_selection_matrix_is_gate_qualified() -> None:
    gate = build_textual_replacement_proof_gate()
    effective = [(selection.requested_backend, selection.effective_backend, selection.require_textual) for selection in gate.backend_selections]
    assert effective == [
        ("auto", "textual", False),
        ("auto", "unavailable", True),
        ("auto", "unavailable", True),
        ("textual", "unavailable", True),
        ("textual", "textual", False),
    ]
    severities = [notice.severity for notice in gate.notices]
    assert severities == ["info", "error", "error", "error", "info"]


def test_gate_embeds_feature_parity_without_missing_features() -> None:
    gate = build_textual_replacement_proof_gate()
    parity = gate.parity_plan.as_dict()
    assert parity["missing_features"] == []
    assert parity["owns_truth"] is False
    feature_ids = {feature["id"] for feature in parity["features"]}
    assert {
        "run-list",
        "active-progress",
        "node-tree",
        "diagnostics",
        "evidence",
        "validation-session",
        "artifact-lifecycle",
        "command-palette",
        "keyboard-shortcuts",
        "copy-share",
        "responsive-layout",
    } <= feature_ids


def test_gate_cross_surface_bundle_uses_schema_v3_and_consistent_parity() -> None:
    gate = build_textual_replacement_proof_gate()
    machine = gate.parity_plan.base_spec.source_bundle.as_machine()
    assert machine["schema_version"] == 3
    assert machine["parity"]["consistent"] is True
    assert [render["consumer"] for render in machine["renders"]] == [
        "machine",
        "terminal",
        "tui",
        "desktop",
        "hud",
        "ipc",
    ]


def test_explain_backend_and_no_fallback_flags_remain_available() -> None:
    args = build_parser().parse_args(["tui", "--explain-backend", "--no-tui-fallback"])
    assert args.explain_backend is True
    assert args.require_textual is True


def test_gate_reuses_x07_notice_semantics_for_missing_textual() -> None:
    selection = resolve_tui_backend_selection(
        requested_backend="auto",
        availability=TextualAvailability(False, reason="textual package is not importable"),
    )
    notice = build_tui_backend_notice(selection)
    assert notice.severity == "error"
    assert "no legacy fallback" in notice.message
    assert any("devtool[textual]" in item for item in notice.remediations)


def test_hardening_contract_is_still_gate_ready() -> None:
    contract = textual_replacement_hardening_contract()
    assert contract["legacy_backend_status"] == "removed"
    assert "devtool tui --explain-backend" == contract["explain_command"]
    assert contract["machine_output_changed"] is False
    assert contract["textual_owns_truth"] is False


def test_gate_assertion_rejects_mutated_failed_item() -> None:
    gate = build_textual_replacement_proof_gate()
    broken = type(gate)(
        schema_version=gate.schema_version,
        gate_id=gate.gate_id,
        items=tuple(type(item)(item.id, "failed" if item.id == "textual-is-default" else item.status, item.evidence, item.requirement, item.owner) for item in gate.items),
        backend_selections=gate.backend_selections,
        notices=gate.notices,
        parity_plan=gate.parity_plan,
        default_contract=gate.default_contract,
        hardening_contract=gate.hardening_contract,
        required_reports=gate.required_reports,
        present_reports=gate.present_reports,
        owns_truth=gate.owns_truth,
        machine_output_changed=gate.machine_output_changed,
    )
    with pytest.raises(AssertionError, match="proof gate failed"):
        assert_textual_replacement_gate_passes(broken)
