from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from devtool.cli import build_parser, main
from devtool.models import CommandSpec, OperationResult, ProjectConfig
from devtool.validation_reports import (
    finalization_issues,
    load_validation_report,
    write_validation_report,
)
from devtool.validation_sessions import (
    SESSION_SCHEMA_VERSION,
    create_session,
    input_fingerprints,
    mark_completed,
    mark_phase_running,
    record_phase_result,
    status_payload,
)


def project(root: Path) -> ProjectConfig:
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    os.system(f"git -C {root} init -q")
    os.system(f"git -C {root} config user.email test@example.com")
    os.system(f"git -C {root} config user.name Test")
    os.system(f"git -C {root} add . && git -C {root} commit -qm initial")
    return ProjectConfig(repo_root=root, project_root=str(root), kind="python")


def specs() -> list[CommandSpec]:
    return [
        CommandSpec("compile", ["python", "-m", "compileall", "src"], metadata={"validation_phase_name": "compile"}),
        CommandSpec("unit-test", ["python", "-m", "pytest"], metadata={"validation_phase_name": "unit-test"}),
    ]


def pass_phase(session, phase):
    mark_phase_running(session, phase.key)
    record_phase_result(session, phase.key, OperationResult(phase.name, ["true"], 0, 0.1, "passed", []))


def test_report_persists_complete_phase_attempt_git_commit_and_source_context(tmp_path: Path) -> None:
    config = project(tmp_path)
    session = create_session(
        config,
        specs(),
        apply_context={
            "command": "apply-overlay",
            "validation_mode": "deferred",
            "artifact": "/tmp/fix.zip",
            "artifact_type": "overlay",
            "artifact_sha": "abc123",
            "commit": "Apply fix",
        },
    )
    for phase in session.phases:
        pass_phase(session, phase)
    mark_completed(session, commit_status="committed", commit_hash="deadbeef")
    report = load_validation_report(tmp_path, session.session_id)
    assert report["schema_version"] == 1
    assert report["final_verdict"] == "validated"
    assert report["source"]["artifact"] == "/tmp/fix.zip"
    assert report["repository"]["initial_git_head"]
    assert report["repository"]["final_git_head"]
    assert report["plan"]["completed_phases"] == 2
    assert report["attempts"]["count"] == 2
    assert report["commit"] == {"status": "committed", "hash": "deadbeef", "message": "Apply fix"}
    assert session.report_path.endswith(f"{session.session_id}.json")


def test_status_payload_exposes_compact_report_summary(tmp_path: Path) -> None:
    config = project(tmp_path)
    session = create_session(config, specs())
    for phase in session.phases:
        pass_phase(session, phase)
    mark_completed(session)
    payload = status_payload(session)
    assert payload["report_path"] == session.report_path
    assert payload["final_verdict"] == "validated"
    assert payload["report_summary"]["diagnostics"] == {"warnings": 0, "errors": 0}
    assert payload["commit_status"] == "not-requested"
    assert payload["initial_git_head"]
    assert payload["final_git_head"]


def test_finalization_integrity_rejects_active_stale_failed_and_changed_inputs(tmp_path: Path) -> None:
    config = project(tmp_path)
    session = create_session(config, specs(), persist=False)
    assert finalization_issues(session, input_fingerprints(tmp_path))
    for phase in session.phases:
        phase.status = "passed"
    assert finalization_issues(session, input_fingerprints(tmp_path)) == []
    (tmp_path / "src" / "main.py").write_text("print('changed')\n", encoding="utf-8")
    assert "inputs changed" in " ".join(finalization_issues(session, input_fingerprints(tmp_path)))
    session.phases[0].status = "stale"
    assert "active, stale" in " ".join(finalization_issues(session, input_fingerprints(tmp_path)))


def test_validation_report_cli_returns_machine_envelope(tmp_path: Path, capsys) -> None:
    config = project(tmp_path)
    session = create_session(config, specs())
    path = write_validation_report(session)
    session.report_path = str(path)
    from devtool.validation_sessions import save_session
    save_session(session, active=True)
    code = main(["-r", str(tmp_path), "validation-report", "--session", session.session_id, "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["api_version"] == 1
    assert payload["accepted"] is True
    assert payload["action"] == "report"
    assert payload["data"]["report"]["session_id"] == session.session_id



def test_sealed_final_report_is_content_hashed_and_never_overwritten(tmp_path: Path) -> None:
    config = project(tmp_path)
    session = create_session(config, specs())
    for phase in session.phases:
        pass_phase(session, phase)
    mark_completed(session, commit_status="committed", commit_hash="abc")
    first = write_validation_report(session, commit_status="committed", commit_hash="abc", sealed=True)
    original = first.read_bytes()
    payload = json.loads(original)
    assert first.name.endswith(".final.json")
    assert payload["sealed"] is True
    assert len(payload["report_sha256"]) == 64
    second = write_validation_report(
        session, commit_status="failed", commit_hash="different",
        administrative_warnings=["must not replace sealed report"], sealed=True,
    )
    assert second == first
    assert second.read_bytes() == original
    assert load_validation_report(tmp_path, session.session_id)["report_sha256"] == payload["report_sha256"]

def test_schema_v4_session_migrates_to_report_capable_schema(tmp_path: Path) -> None:
    config = project(tmp_path)
    session = create_session(config, specs())
    state = Path(tmp_path) / ".devtool" / "state" / "android-validation" / session.session_id / "state.json"
    payload = json.loads(state.read_text(encoding="utf-8"))
    payload["schema_version"] = 4
    for key in ("initial_git_head", "final_git_head", "report_path", "commit_status", "commit_hash"):
        payload.pop(key, None)
    state.write_text(json.dumps(payload), encoding="utf-8")
    from devtool.validation_sessions import load_session
    migrated = load_session(tmp_path, session.session_id)
    assert SESSION_SCHEMA_VERSION == 6
    assert migrated.initial_git_head == migrated.git_head
    assert migrated.commit_status == "not-requested"
