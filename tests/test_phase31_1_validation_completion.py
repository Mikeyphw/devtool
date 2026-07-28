from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from devtool.cli import _resolve_prompted_failure_action
from devtool.models import CommandSpec, ProjectConfig
from devtool.validation_sessions import (
    create_session,
    mark_phase_running,
    recover_interrupted_phases,
    status_payload,
    update_phase_progress,
)

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def project(root: Path) -> ProjectConfig:
    (root / "app").mkdir(parents=True)
    (root / "settings.gradle.kts").write_text('rootProject.name="Demo"\n', encoding="utf-8")
    return ProjectConfig(repo_root=root, project_root=str(root), kind="android", runner_options={"validation": {"resumable": True}})


def test_android_store_rejects_stale_revisions_and_never_deletes_attempt_history() -> None:
    store = read("ValidationStore.java")
    assert "session.revision < storedRevision" in store
    assert "session.updatedAtMs < storedUpdatedAt" in store
    assert "return false" in store
    assert 'CONFLICT_IGNORE' in store
    assert 'db.delete("validation_attempts"' not in store
    assert 'db.delete("validation_phases"' not in store
    assert 'session_id=? AND phase_key=? AND attempt_id=?' in store


def test_android_control_enforces_repository_and_executor_identity() -> None:
    controller = read("ValidationController.java")
    receiver = read("ActionReceiver.java")
    session = read("ValidationSessionRecord.java")
    assert '!action.equals("status") && !action.equals("plan")' in controller
    assert "Validation session chroot name is unavailable" in controller
    assert "Validation session requires the connected root bridge" in controller
    assert "expectedRepository" in receiver
    assert "session.identityMatches(expectedRepository)" in receiver
    assert "RepositoryLocation.chroot" in session


def test_workbench_has_recovery_safe_retry_evidence_and_complete_rollback_context() -> None:
    workbench = read("ValidationWorkbenchView.java")
    evidence = read("ValidationEvidenceActivity.java")
    manifest = (ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "AndroidManifest.xml").read_text(encoding="utf-8")
    for label in ("Recover session", "Retry safely", "Open log and evidence", "Copy command"):
        assert label in workbench
    for label in ("Rollback state:", "Backup health:", "Uncommitted changes:", "Repository identity:"):
        assert label in workbench
    for section in ("Classifier evidence", "Supervisor events", "Inputs before", "Inputs after", "Copy evidence report"):
        assert section in evidence
    assert '.ValidationEvidenceActivity' in manifest


def test_notification_actions_enforce_repository_revision_attempt_and_recovery() -> None:
    notification = read("NotificationController.java")
    receiver = read("ActionReceiver.java")
    assert 'new ActionSpec("Recover"' in notification
    assert 'ACTION_VALIDATION_RECOVER' in notification
    assert 'putExtra("expected_revision"' in notification
    assert 'putExtra("expected_attempt_id"' in notification
    assert 'putExtra("repository_id"' in notification
    assert 'ACTION_VALIDATION_RECOVER.equals(action)' in receiver


def test_numeric_phase_progress_and_structured_identity_are_in_status_payload() -> None:
    with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False):
        root = Path(tmp) / "repo"
        root.mkdir()
        session = create_session(project(root), [CommandSpec("compile", ["true"], metadata={"validation_phase_name": "compile", "supports_progress": True})])
        phase = mark_phase_running(session, session.phases[0].key)
        assert update_phase_progress(session, phase.key, {"percent": 68, "detail": "compileDebugKotlin", "completed": 68, "total": 100, "unit_label": "tasks"})
        payload = status_payload(session)
        assert payload["phases"][0]["progress_percent"] == 68
        assert payload["phases"][0]["progress_detail"] == "compileDebugKotlin"
        assert payload["repository_identity"]["repository_id"]
        assert payload["execution_context"]["kind"]
        assert payload["phases"][0]["effective_status"] == "running"


def test_recovery_requires_a_stale_lease_when_requested() -> None:
    with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False):
        root = Path(tmp) / "repo"
        root.mkdir()
        session = create_session(project(root), [CommandSpec("compile", ["true"], metadata={"validation_phase_name": "compile"})])
        phase = mark_phase_running(session, session.phases[0].key)
        phase.owner_pid = 99999999
        phase.owner_process_start = "dead"
        assert recover_interrupted_phases(session, stale_after_seconds=60.0) == []
        phase.heartbeat_at = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
        assert recover_interrupted_phases(session, stale_after_seconds=60.0) == ["compile"]


def test_prompt_failure_policy_fails_safe_for_machine_and_can_choose_actions_interactively() -> None:
    machine = SimpleNamespace(json=True, machine="", failure_action="")
    assert _resolve_prompted_failure_action(machine, "prompt", phase_label="tests") == "pause"
    human = SimpleNamespace(json=False, machine="", failure_action="")
    with patch("sys.stdin.isatty", return_value=True), patch("devtool.cli.Confirm.ask", side_effect=[True]):
        assert _resolve_prompted_failure_action(human, "prompt", phase_label="tests") == "rollback"
    with patch("sys.stdin.isatty", return_value=True), patch("devtool.cli.Confirm.ask", side_effect=[False, True]):
        assert _resolve_prompted_failure_action(human, "prompt", phase_label="tests") == "keep"
