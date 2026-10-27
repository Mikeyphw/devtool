"""Phase B: historical Markdown must not masquerade as runnable release proof."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from devtool.final_seal import final_seal_report
from devtool.target_seal import target_seal_report
from devtool.validation_profiles import BUILTIN_PROFILES

ROOT = Path(__file__).resolve().parents[1]


def _checks(report):
    return {item["id"]: item for item in report["checks"]}


def test_historical_report_files_not_required_by_active_contracts(monkeypatch):
    original_is_file = Path.is_file
    # Treat every historic *REPORT.md as removed; release documentation and
    # required sources/tests remain untouched. This covers both root & nested.
    def without_history(path):
        if path.name.endswith("REPORT.md"):
            return False
        return original_is_file(path)
    monkeypatch.setattr(Path, "is_file", without_history)
    root = final_seal_report(ROOT)
    assert root["status"] == "pass", root
    assert "roadmap.reports" not in _checks(root)
    assert root["formal_release_qualified"] is False
    for target in ("devtool", "android_hud"):
        result = target_seal_report(ROOT, target)
        assert result["status"] == "pass", result
        assert result["formal_release_qualified"] is False
        assert "target.report" not in _checks(result)


def test_missing_artifact_or_external_verification_policy_still_fails(monkeypatch):
    profile = BUILTIN_PROFILES["final-seal"]
    monkeypatch.setitem(BUILTIN_PROFILES, "final-seal", {
        **profile,
        "artifacts": {"required": False, "external_verification": "optional"},
    })
    report = final_seal_report(ROOT)
    check = _checks(report)["validation.final-seal-profile"]
    assert check["status"] == "fail"
    assert set(check["details"]) == {"artifacts.required", "artifacts.external_verification"}
    assert report["status"] == "fail"


def test_target_cli_contract_rejects_missing_strict_dispatch(monkeypatch):
    import devtool.cli as cli
    original = cli.build_parser
    class BadParser:
        def parse_args(self, argv):
            args = original().parse_args(argv)
            args.strict = False
            return args
    monkeypatch.setattr(cli, "build_parser", lambda: BadParser())
    report = target_seal_report(ROOT, "devtool")
    assert _checks(report)["target.validation-seal-command"]["status"] == "fail"
    assert report["status"] == "fail"


def test_missing_actual_target_source_is_still_failure(monkeypatch):
    original_is_file = Path.is_file
    def without_required(path):
        if path == ROOT / "devtool" / "cli.py":
            return False
        return original_is_file(path)
    monkeypatch.setattr(Path, "is_file", without_required)
    report = target_seal_report(ROOT, "devtool")
    assert _checks(report)["target.required-paths"]["status"] == "fail"
    assert report["status"] == "fail"
