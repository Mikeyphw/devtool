from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "android" / "devtool-live-hud"
JAVA = APP / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_phase29_has_durable_validation_schema_and_api_v1_parser() -> None:
    database = read("DevtoolDatabase.java")
    parser = read("ValidationBridgeParser.java")
    store = read("ValidationStore.java")
    controller = read("ValidationController.java")
    for table in ("validation_sessions", "validation_phases", "validation_attempts"):
        assert f'CREATE TABLE IF NOT EXISTS {table}' in database
    assert "private static final int VERSION = 11" in database
    assert 'envelope.optInt("api_version", -1) != 1' in parser
    assert 'envelope.optLong("revision", 0L)' in parser
    assert 'item.optJSONArray("attempt_history")' in parser
    assert 'ACTION_UPDATED = "io.github.mikeceead.devtoolhud.VALIDATION_UPDATED"' in store
    assert 'operation = "validation-" + action' in controller
    assert "RootBridgeManager.runValidationControl" in controller
    assert "TermuxCommandRunner.runUtilityCommand" in controller
    assert "mergeLocal(incoming, existing)" in controller
    assert 'refresh(context, repo(incoming), incoming.sessionId' in controller


def test_phase29_routes_root_and_termux_results_through_one_store() -> None:
    termux = read("TermuxResultService.java")
    root = read("RootBridgeManager.java")
    assert 'bridgeOperation.startsWith("validation-")' in termux
    assert "ValidationController.handleResult" in termux
    assert "runValidationControl" in root
    assert 'ValidationController.handleResult' in root
    assert "ValidationController.handleResult" in root


def test_phase30_workbench_supports_all_safe_scopes_and_controls() -> None:
    workbench = read("ValidationWorkbenchView.java")
    main = read("MainActivity.java")
    builder = read("ValidationCommandBuilder.java")
    for label in ("Next phase", "Through phase…", "Selected phases…", "All remaining"):
        assert label in workbench
    for label in (
        "Pause after phase", "Stop now", "Keep running", "Review failure",
        "Retry phase", "Restart from phase…", "Reset validation progress",
        "Discard session and keep changes", "Roll back applied changes",
    ):
        assert label in workbench
    assert "required dependency" in workbench
    assert "collectDependencies" in workbench
    assert "ui.wideLayout()" in workbench
    assert "renderPhaseDetailPane" in workbench
    assert 'String[] modes = {"Next phase", "Through phase…", "Selected phases…", "All remaining"};' in workbench
    assert 'getStringExtra("run_section")' in main
    assert 'AppSettings.setRunSection(this, "validation")' in main
    assert 'ui.secondaryButton("Validation"' in main
    assert 'validate run --json' in builder
    assert ' --scope ' in builder
    assert ' --through ' in builder
    assert ' --phase ' in builder
    assert 'validate pause --json' not in builder  # built through the common control helper
    assert ' + " --cancel"' in builder
    assert ' + (rollback ? " --rollback" : " --keep-changes")' in builder


def test_phase30_rollback_is_only_exposed_from_confirmed_workbench_flow() -> None:
    workbench = read("ValidationWorkbenchView.java")
    notification = read("NotificationController.java")
    assert 'new AlertDialog.Builder(activity).setTitle(title).setMessage(message)' in workbench
    assert '"Roll back applied changes"' in workbench
    assert 'ValidationController.abort(activity, session, rollback)' in workbench
    actions = notification[notification.index("private List<ActionSpec> validationActions"):]
    assert 'new ActionSpec("Roll back"' not in actions
    assert 'new ActionSpec("Rollback"' not in actions


def test_phase31_notification_reuses_normal_surface_with_validation_actions() -> None:
    update = read("HudUpdate.java")
    notification = read("NotificationController.java")
    receiver = read("ActionReceiver.java")
    for label in (
        "LIVE VALIDATION", "PAUSING", "STOPPING", "BLOCKED",
        "ACTION REQUIRED", "VALIDATED", "PAUSED",
    ):
        assert f'return "{label}"' in update
    for label in (
        "Pause after phase", "Stop now", "Keep running", "Review",
        "Retry phase", "Retry safely", "Change scope", "Open report",
        "Copy failure", "Copy status", "Copy summary",
    ):
        assert f'new ActionSpec("{label}"' in notification
    assert 'expected_revision' in notification
    assert 'expected_attempt_id' in notification
    assert 'repository_id' in notification
    assert "ACTION_VALIDATION_CANCEL_PAUSE" in receiver
    assert 'active == null || !expectedAttempt.equals(active.currentAttemptId)' in receiver
    assert 'opening current state' in receiver


def test_phase31_contract_documents_validation_notification_modes() -> None:
    contract = json.loads((APP / "docs" / "notification-ui-contract.json").read_text(encoding="utf-8"))
    validation = contract["validation"]
    assert validation["progress"]["system_bar"] == "overall phase completion"
    assert validation["phase_rows"] == ["previous", "current", "next"]
    assert validation["rollback_action"] == "workbench confirmation only"
    assert validation["actions"]["running"] == ["Open", "Pause after phase", "Stop now"]
    assert validation["actions"]["pausing"] == ["Open", "Keep running", "Stop now"]
    assert validation["actions"]["blocked"] == ["Review", "Retry phase", "Copy failure"]


def test_android_version_is_bumped_for_validation_workbench_release() -> None:
    gradle = (APP / "app" / "build.gradle").read_text(encoding="utf-8")
    assert "versionCode 31" in gradle
    assert "versionName '1.17.0'" in gradle
