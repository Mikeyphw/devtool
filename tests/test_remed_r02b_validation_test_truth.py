from __future__ import annotations

import json
from pathlib import Path

from devtool.android.instrumentation import AndroidInstrumentationOrchestrator, InstrumentationRequest, _execution_classification
from devtool.models import CommandSpec, OperationResult, ProjectConfig
from devtool.test_model import TestCaseResult, TestResult, TestStatus, TestSuiteResult
from devtool.test_runstore import latest_failed_test_ids, persist_test_run
from devtool.test_selection import apply_test_selection, load_failed_test_ids, save_failed_test_ids
from devtool.validation_sessions import (
    PhaseRecord,
    ValidationSession,
    append_event,
    mark_phase_running,
    record_phase_result,
    save_session,
    session_path,
)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "tests").mkdir()
    (root / "tests" / "test_sample.py").write_text(
        "def test_good():\n    assert True\n\ndef test_bad():\n    assert False\n",
        encoding="utf-8",
    )
    return root


def test_failed_history_prefers_canonical_run_receipt_over_repo_projection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    root = _repo(tmp_path)
    stale_projection = save_failed_test_ids(root, ["stale::case"])
    assert json.loads(stale_projection.read_text())["authority"] == "projection"

    result = TestResult(
        "pytest",
        suites=(TestSuiteResult(
            "suite",
            "suite",
            cases=(
                TestCaseResult("tests/test_sample.py::test_good", "test_good", TestStatus.PASSED),
                TestCaseResult("tests/test_sample.py::test_bad", "test_bad", TestStatus.FAILED),
            ),
        ),),
    )
    op = OperationResult("pytest", ["python", "-m", "pytest"], 1, 0.1, "", [], metadata={"test_result": result.to_dict()})
    projection_path = persist_test_run(root, [op], run_id="run-r02b")
    projection = json.loads(projection_path.read_text())
    assert projection["authority"] == "projection"
    assert projection["canonical_authority"] == "RUN-V3 TestRunReceipt"
    receipt_path = Path(projection["canonical_receipt_path"])
    receipt = json.loads(receipt_path.read_text())
    assert receipt["authority"] == "RUN-V3 TestRunReceipt"
    assert latest_failed_test_ids(root) == ["tests/test_sample.py::test_bad"]
    assert load_failed_test_ids(root) == ["tests/test_sample.py::test_bad"]




def test_clean_canonical_receipt_with_zero_failures_suppresses_stale_projection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    root = _repo(tmp_path)
    save_failed_test_ids(root, ["stale::case"])

    result = TestResult(
        "pytest",
        suites=(TestSuiteResult(
            "suite",
            "suite",
            cases=(
                TestCaseResult("tests/test_sample.py::test_good", "test_good", TestStatus.PASSED),
            ),
        ),),
    )
    op = OperationResult("pytest", ["python", "-m", "pytest"], 0, 0.1, "", [], metadata={"test_result": result.to_dict()})
    persist_test_run(root, [op], run_id="run-clean")

    assert load_failed_test_ids(root) == []


def test_tampered_canonical_receipt_is_not_failed_selection_authority(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    root = _repo(tmp_path)
    save_failed_test_ids(root, ["projection::fallback"])

    result = TestResult(
        "pytest",
        suites=(TestSuiteResult(
            "suite",
            "suite",
            cases=(TestCaseResult("tests/test_sample.py::test_bad", "test_bad", TestStatus.FAILED),),
        ),),
    )
    op = OperationResult("pytest", ["python", "-m", "pytest"], 1, 0.1, "", [], metadata={"test_result": result.to_dict()})
    projection_path = persist_test_run(root, [op], run_id="run-tampered")
    projection = json.loads(projection_path.read_text(encoding="utf-8"))
    receipt_path = Path(projection["canonical_receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["operations"][0]["test_result"]["suites"][0]["cases"][0]["id"] = "forged::case"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    assert load_failed_test_ids(root) == ["projection::fallback"]


def test_direct_run_specs_persists_test_receipt_with_event_run_id(tmp_path: Path, monkeypatch) -> None:
    from devtool import runners

    root = _repo(tmp_path)
    captured: dict[str, object] = {}

    def fake_persist(repo_root: Path, results, *, run_id: str = ""):
        captured["repo_root"] = repo_root
        captured["run_id"] = run_id
        captured["result_count"] = len(list(results))
        return None

    monkeypatch.setattr("devtool.test_runstore.persist_test_run", fake_persist)
    spec = CommandSpec("unit", ["python", "-c", "print('ok')"], cwd=str(root), metadata={"contains_tests": True})
    results, _ = runners.run_specs(ProjectConfig(repo_root=root, kind="generic"), [spec], verbose=False, event_run_id="direct-r02b")

    assert results[0].returncode == 0
    assert captured["run_id"] == "direct-r02b"

def test_legacy_selection_is_lowered_with_canonical_selection_identity(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    config = ProjectConfig(
        repo_root=root,
        project_root=str(root),
        kind="python",
        runner_options={"test_selection": {"ids": ["tests/test_sample.py::test_bad"]}},
    )
    spec = CommandSpec("test", ["python", "-m", "pytest"], metadata={"contains_tests": True, "test_framework": "pytest"})
    lowered = apply_test_selection(config, spec)
    canonical = lowered.metadata["canonical_test_selection"]
    assert canonical["authority"] == "TEST-X02 immutable selection"
    assert canonical["selection_id"].startswith("tsel-")
    assert canonical["resolved_check_ids"] == ["tests/test_sample.py::test_bad"]
    assert "tests/test_sample.py::test_bad" in lowered.argv


def test_validation_session_state_and_events_are_projection_not_execution_authority(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    root = _repo(tmp_path)
    phase = PhaseRecord("unit", "unit", "Unit", "unit", 1, 1, validation_check_id="unit")
    session = ValidationSession(
        session_id="session-r02b",
        repo_root=str(root),
        project_root=str(root),
        project_type="python",
        target="devtool",
        variant="",
        status="ready",
        created_at="2026-10-03T00:00:00Z",
        updated_at="2026-10-03T00:00:00Z",
        phases=[phase],
        fingerprints={},
    )
    save_session(session, active=True)
    state = json.loads(session_path(root, session.session_id).read_text())
    assert state["authority"] == "projection"
    assert state["canonical_authority"] == "EXEC/RUN validation-reference-state"
    event = append_event(session, "probe", data={"ok": True})
    assert event["authority"] == "projection"

    mark_phase_running(session, "unit")
    record_phase_result(session, "unit", OperationResult("unit", ["python", "-m", "pytest", "secret-node"], 0, 0.1, "ok", []))
    assert session.phase_by_key("unit").attempt_history[-1].argv == []


def test_android_zero_and_setup_failures_do_not_fabricate_testcase_failures(tmp_path: Path) -> None:
    counts, failed_ids, failure_kind, reason = _execution_classification("", 0)
    assert counts["executed"] == 0
    assert counts["failed"] == 0
    assert failed_ids == []
    assert failure_kind == ""

    request = InstrumentationRequest(project=tmp_path, policy="required")
    orchestrator = AndroidInstrumentationOrchestrator.__new__(AndroidInstrumentationOrchestrator)
    result = orchestrator._skip_or_fail(request, "no device", 0.0, intent_id="intent")
    assert result.status == "failed"
    assert result.failure_kind == "device-readiness"
    assert result.executed == 0
    assert result.failed == 0
    assert result.returncode == 1
