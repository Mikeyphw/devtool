"""Phase C: old audit Markdown is retired without weakening runnable gates."""
from __future__ import annotations

from pathlib import Path
from devtool.final_seal import final_seal_report
from devtool.target_seal import target_seal_report
from devtool.remed_r04b_fixed_point import G3R_REAL_SUBSTRATE_CARRY

ROOT = Path(__file__).resolve().parents[1]


def test_retired_report_set_is_not_in_active_source():
    # Chosen from files with no executable, data or retained-doc references.
    retired = (ROOT / "docs" / "HISTORICAL_REPORT_RETIREMENT.md").read_text(encoding="utf-8")
    assert "192" in retired
    for name in (
        "ANDROID_G3_FINAL_SEAL_REPORT.md",
        "CORE_G3_FINAL_SEAL_REPORT.md",
        "DEVTOOL_DESKTOP_PHASE8_9_FINAL_SEAL_REPORT.md",
        "ARTIFACT_LIFECYCLE_REGISTRY_REPORT.md",
    ):
        assert not (ROOT / name).exists(), name


def test_remaining_active_gate_docs_and_behavior_are_preserved():
    for name in (
        "README.md", "docs/FINAL_RELEASE_SEAL.md",
        "EXEC_X01_EXECUTION_VALIDATION_FOUNDATION_REPORT.md",
        "PRES_X04_TEXTUAL_TUI_SUBSTRATE_REPORT.md",
        "AOPS04_ANDROID_RELEASE_EVIDENCE_FINAL_SEAL_REPORT.md",
    ):
        assert (ROOT / name).is_file(), name
    result = final_seal_report(ROOT)
    assert result["status"] == "pass", result
    assert result["formal_release_qualified"] is False
    for target in ("devtool", "android_hud"):
        report = target_seal_report(ROOT, target)
        assert report["status"] == "pass", report
        assert report["formal_release_qualified"] is False


def test_unresolved_real_substrate_obligations_remain_exposed():
    expected = {"ENV-A06", "ANDROID-A16", "DIST-A01", "DIST-A12", "DIST-A13", "TEST-A11"}
    assert expected <= set(G3R_REAL_SUBSTRATE_CARRY)
