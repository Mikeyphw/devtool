from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_phase34_schema10_debug_event_recorder_tables_and_accessors() -> None:
    database = read("DevtoolDatabase.java")
    assert "private static final int VERSION = 11" in database
    assert "createDebugTables(db)" in database
    assert "CREATE TABLE IF NOT EXISTS debug_events" in database
    for column in (
        "timestamp_ms", "severity", "category", "event_type", "surface",
        "run_id", "repository_id", "operation_id", "validation_session_id",
        "message", "detail_json", "fingerprint", "redaction_state",
    ):
        assert column in database
    for index in (
        "debug_events_time_idx", "debug_events_category_idx", "debug_events_run_idx",
        "debug_events_repository_idx", "debug_events_fingerprint_idx",
    ):
        assert index in database
    assert "addDebugEvent(DebugEventRecord event)" in database
    assert "recentDebugEvents(int requestedLimit)" in database
    assert "debugEventsForRun(String runId" in database
    assert "debugEventsForRepository(String repositoryId" in database
    assert "stats.debugEvents" in database


def test_phase34_debug_recorder_model_redaction_and_fingerprints_are_present() -> None:
    recorder = read("DebugRecorder.java")
    event = read("DebugEventRecord.java")
    classifier = read("DebugIssueClassifier.java")
    assert "ACTION_UPDATED" in recorder
    assert "SupportRedactor.redact" in recorder
    assert "DebugIssueClassifier.fingerprint" in recorder
    assert "DevtoolDatabase.get(context.getApplicationContext()).addDebugEvent(event)" in recorder
    assert "redactionState = \"redacted\"" in recorder or 'redactionState = "redacted"' in event
    for field in ("eventId", "timestampMs", "severity", "category", "eventType", "surface", "fingerprint"):
        assert field in event
    for classifier_key in (
        "repo_runuser_failed", "path_missing", "bridge_token_mismatch",
        "termux_run_command_permission_missing", "stale_bridge_update",
    ):
        assert classifier_key in classifier


def test_phase34_run_and_bridge_failure_surfaces_record_debug_events() -> None:
    termux = read("TermuxCommandRunner.java")
    bridge = read("BridgeReceiver.java")
    result = read("TermuxResultService.java")
    for event_type in (
        "RUN_COMMAND_REJECTED", "TERMUX_MISSING", "TERMUX_RUN_COMMAND_PERMISSION_MISSING",
        "RUN_COMMAND_REQUESTED", "TERMUX_INTENT_SENT", "TERMUX_INTENT_ACCEPTED",
        "TERMUX_INTENT_FAILED", "RUN_PROGRESS_WATCHDOG_WAITING",
    ):
        assert event_type in termux
    for event_type in (
        "BRIDGE_TOKEN_REJECTED", "BRIDGE_ACTION_REJECTED", "BRIDGE_RUN_ID_MISSING",
        "BRIDGE_UPDATE_STALE", "BRIDGE_CANCEL_ACCEPTED",
    ):
        assert event_type in bridge
    for event_type in ("TERMUX_RESULT_MISSING", "TERMUX_RESULT_DELIVERED", "REPOSITORY_UTILITY_FAILED"):
        assert event_type in result


def test_phase34_repo_root_and_validation_surfaces_record_debug_events() -> None:
    workspace = read("RepositoryWorkspaceStore.java")
    broker = read("RootCommandBroker.java")
    root_bridge = read("RootBridgeManager.java")
    validation = read("ValidationController.java")
    for event_type in (
        "REPO_REFRESH_REQUESTED", "REPO_REFRESH_FAILED", "REPO_OPERATION_QUEUED",
        "REPO_OPERATION_STARTED", "REPO_OPERATION_SUCCEEDED", "REPO_OPERATION_FAILED",
        "REPO_OPERATION_ORPHANED", "REPO_OPERATION_INTERRUPTED",
    ):
        assert event_type in workspace
    for event_type in (
        "REPO_EXECUTOR_SELECTED", "CHROOT_EXECUTOR_SELECTED", "TERMUX_EXECUTOR_SELECTED",
        "ROOT_EXECUTION_TIMEOUT", "ROOT_EXECUTION_FAILED",
    ):
        assert event_type in broker
    for event_type in (
        "ROOT_BRIDGE_STATE_CHANGED", "ROOT_BRIDGE_NOT_CONNECTED", "ROOT_COMMAND_REJECTED",
    ):
        assert event_type in root_bridge
    for event_type in (
        "VALIDATION_ACTION_REQUESTED", "VALIDATION_ACTION_PENDING", "VALIDATION_IDENTITY_REJECTED",
        "VALIDATION_STALE_RESPONSE_IGNORED", "VALIDATION_COMMAND_FAILED",
    ):
        assert event_type in validation


def test_phase34_support_bundle_exposes_debug_event_count() -> None:
    bundle = read("SupportBundle.java")
    assert '"debug_events"' in bundle
    assert "stats.debugEvents" in bundle
