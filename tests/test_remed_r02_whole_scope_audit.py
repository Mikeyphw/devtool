from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_r02_whole_scope_status_is_not_final_closure() -> None:
    disposition = _json("REMED_R02_WHOLE_SCOPE_DISPOSITION.json")
    assert disposition["whole_scope_status"] == "IMPLEMENTATION_WINDOW_DELIVERED_WITH_EXPLICIT_EXPORTS"
    assert disposition["current_maxima"] == {
        "CORE": "CORE-P084",
        "EXEC": "EXEC-P087",
        "ENV": "ENV-P131",
        "ANDROID": "ANDROID-P111",
        "LIFE": "LIFE-P127",
    }
    assert disposition["next"]["next_overlay"] == "REMED-R03A Provider & Lifecycle Authority"


def test_r02_disposition_sets_are_disjoint_and_explicit() -> None:
    disposition = _json("REMED_R02_WHOLE_SCOPE_DISPOSITION.json")
    delivered = set(disposition["fully_delivered_findings"])
    partial = set(disposition["partial_findings"])
    deferred = set(disposition["deferred_or_exported_findings"])

    assert delivered == {
        "EXEC-A04", "EXEC-A05", "EXEC-A07",
        "TEST-A02", "TEST-A09", "TEST-A13",
        "ANDROID-A05", "ANDROID-A06",
    }
    assert not delivered & partial
    assert not delivered & deferred
    assert {"EXEC-A01", "EXEC-A02", "EXEC-A06", "EXEC-A13"}.issubset(partial)
    assert "EXEC-A08" in deferred


def test_r02a_r02b_dispositions_match_whole_scope_without_overclaim() -> None:
    whole = _json("REMED_R02_WHOLE_SCOPE_DISPOSITION.json")
    r02a = _json("REMED_R02A_PROMISE_DISPOSITION.json")
    r02b = _json("REMED_R02B_PROMISE_DISPOSITION.json")

    delivered = set(whole["fully_delivered_findings"])
    assert set(r02a["delivered_findings"]).issubset(delivered)
    assert set(r02b["delivered_findings"]).issubset(delivered)
    assert set(r02a["partial_findings"]).issubset(set(whole["partial_findings"]))
    assert set(r02b["partial_findings"]).issubset(set(whole["partial_findings"]))

    for hotfix in r02b.get("hotfixes", []):
        assert not ({"EXEC-A01", "EXEC-A02"} & set(hotfix.get("fixed_findings", [])))
        if hotfix.get("id") == "REMED-R02B-v2-folded-H01":
            assert set(hotfix.get("partial_findings_touched", [])) == {"EXEC-A01", "EXEC-A02"}


def test_r02_reports_do_not_present_partial_findings_as_closed() -> None:
    report = (ROOT / "REMED_R02_WHOLE_SCOPE_FIXED_POINT_AUDIT_REPORT.md").read_text(encoding="utf-8")
    assert "not a final closed whole-scope seal" in report
    assert "Partial or exported by design after R02" in report
    assert "EXEC-A08" in report
    assert "REMED-R03A" in report
