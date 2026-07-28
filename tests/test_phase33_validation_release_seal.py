from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_android_schema9_report_parser_and_distinct_history_cards() -> None:
    database = read(JAVA / "DevtoolDatabase.java")
    parser = read(JAVA / "ValidationBridgeParser.java")
    store = read(JAVA / "ValidationStore.java")
    main = read(JAVA / "MainActivity.java")
    evidence = read(JAVA / "ValidationEvidenceActivity.java")
    assert "private static final int VERSION = 9" in database
    for column in ("report_path", "final_verdict", "commit_status", "initial_git_head", "report_warnings", "report_errors"):
        assert column in database
        assert column in store
    assert "parseReportSummary" in parser
    assert "validationHistoryCard" in main
    assert '"VALIDATION · "' in main
    assert '"Open report"' in main
    assert '"Final verdict"' in evidence
    assert '"Copy validation report"' in evidence


def test_android_phase32_release_version_is_consistent() -> None:
    gradle = read(ROOT / "android" / "devtool-live-hud" / "app" / "build.gradle")
    versions = json.loads(read(ROOT / "release" / "versions.json"))
    assert "versionCode 30" in gradle
    assert "versionName '1.16.0'" in gradle
    assert versions["android"] == {
        "version_name": "1.16.0",
        "version_code": 30,
        "application_id": "io.github.mikeceead.devtoolhud",
    }


def test_release_scenario_matrix_covers_every_promised_failure_case() -> None:
    matrix = json.loads(read(ROOT / "docs" / "validation-release-scenarios.json"))
    required = {
        "user-stop", "pause-after-phase", "android-app-death", "termux-death",
        "resource-exit-137", "genuine-test-failure", "source-change-during-phase",
        "plan-change", "missing-validator", "commit-failure", "rollback-interruption",
        "stale-notification", "dry-run-attached", "status-live-owner",
        "keep-changes-abort", "minimum-phase-missing",
    }
    entries = {item["id"]: item for item in matrix["scenarios"]}
    assert required == set(entries)
    all_nodeids = []
    for scenario in entries.values():
        assert scenario["automated_tests"]
        assert scenario["expected_state"]
        for test in scenario["automated_tests"]:
            assert (ROOT / test.split("::", 1)[0]).is_file()
            all_nodeids.append(test)
    assert len(all_nodeids) == len(set(all_nodeids)) == 16


def test_release_qualification_has_dedicated_validation_and_android_source_gates() -> None:
    qualification = read(ROOT / "devtool" / "release_qualification.py")
    checklist = read(ROOT / "docs" / "RELEASE_CHECKLIST.md")
    tool = read(ROOT / "tools" / "validate-validation-release.py")
    split_runner = read(ROOT / "tools" / "run-pytest-split.py")
    assert '"validation-release"' in qualification
    assert '"android-validation-source"' in qualification
    assert "validation-release-scenarios.json" in tool
    assert "run-pytest-split.py" in qualification
    assert "python-tests-{group}" in qualification
    assert "--group" in split_runner
    assert "deterministic balanced groups" in split_runner
    assert "Phase 32–33 validation seal" in checklist
    assert "Android Gradle build" in checklist


def test_phase32_33_public_contract_is_documented() -> None:
    report = read(ROOT / "PHASE_32_33_VALIDATION_REPORTING_RELEASE_SEAL.md")
    readme = read(ROOT / "README.md")
    changelog = read(ROOT / "CHANGELOG.md")
    for promise in (
        "immutable validation report", "original and final Git heads", "distinct validation history cards",
        "finalization integrity", "16 mandatory failure scenarios", "Android toolchain gate",
    ):
        assert promise in report
    assert "validation report" in readme.lower()
    assert "Phase 32" in changelog and "Phase 33" in changelog
