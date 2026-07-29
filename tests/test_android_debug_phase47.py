from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"
MANIFEST = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "AndroidManifest.xml"
SEAL = ROOT / "docs" / "android-debug-regression-seal.json"


def read_java(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phase47_contract_inventory_covers_phases_34_through_46() -> None:
    seal = json.loads(SEAL.read_text(encoding="utf-8"))
    assert seal["seal"] == "android-debug-system-phase-47"
    contracts = {item["id"]: item for item in seal["contracts"]}
    expected = {
        "debug-recorder-schema": 34,
        "run-command-trace": 35,
        "termux-wrapper-sentinels": 36,
        "bridge-roundtrip-probe": 37,
        "repository-doctor": 38,
        "root-chroot-executor-planner": 39,
        "repository-path-resolver-repair": 40,
        "git-operation-trace": 41,
        "repo-queue-orphan-recovery": 42,
        "debug-workbench-ui": 43,
        "guided-fixes": 44,
        "support-bundle-v2-redaction": 45,
        "debug-notification-safe-actions": 46,
    }
    assert {key: value["phase"] for key, value in contracts.items()} == expected
    for contract in contracts.values():
        for source in contract["source_files"]:
            assert (ROOT / source).is_file(), source
    assert "summary" in seal["support_bundle_required_sections"]
    assert "redaction_self_test" in seal["support_bundle_required_sections"]


def test_phase47_debug_recorder_schema_and_problem_notifications_are_sealed() -> None:
    database = read_java("DevtoolDatabase.java")
    recorder = read_java("DebugRecorder.java")
    record = read_java("DebugEventRecord.java")
    for symbol in (
        "private static final int VERSION = 10",
        "CREATE TABLE IF NOT EXISTS debug_events",
        "debug_events_time_idx",
        "debug_events_run_idx",
        "debug_events_repository_idx",
        "debug_events_fingerprint_idx",
        "synchronized long addDebugEvent(DebugEventRecord event)",
        "synchronized List<DebugEventRecord> recentDebugEvents",
        "synchronized List<DebugEventRecord> debugEventsForRun",
        "synchronized List<DebugEventRecord> debugEventsForRepository",
    ):
        assert symbol in database
    assert "event.eventId = DevtoolDatabase.get(context.getApplicationContext()).addDebugEvent(event)" in recorder
    assert "DebugNotificationController.maybePost(context.getApplicationContext(), event)" in recorder
    assert "boolean problem()" in record
    assert "severity.equals(\"warning\") || severity.equals(\"error\") || severity.equals(\"fatal\")" in record


def test_phase47_run_trace_wrapper_and_bridge_roundtrip_contracts_are_sealed() -> None:
    trace = read_java("RunCommandTrace.java")
    wrapper = read_java("TermuxWrapperSentinel.java")
    bridge = read_java("BridgeRoundtripProbe.java")
    receiver = read_java("BridgeReceiver.java")
    result_service = read_java("TermuxResultService.java")
    for checkpoint in (
        "UI_RUN_REQUESTED",
        "TERMUX_INTENT_SENT",
        "TERMUX_RUNCOMMAND_ACCEPTED",
        "TERMUX_WRAPPER_STARTED",
        "TOKEN_MARKERS_OK",
        "WORKDIR_CHECKED",
        "ENV_READY",
        "EXEC_STARTING",
        "FIRST_PROGRESS_EVENT_RECEIVED",
        "TERMUX_RESULT_RECEIVED",
        "RUN_COMPLETED",
        "RUN_FAILED",
        "BRIDGE_ROUNDTRIP_CALLBACK_RECEIVED",
    ):
        assert checkpoint in trace
    assert "case TERMUX_INTENT_SENT: return 50;" in trace
    assert "case TERMUX_WRAPPER_STARTED: return 82;" in trace
    assert "case EXEC_STARTING: return 96;" in trace
    assert "case FIRST_PROGRESS_EVENT_RECEIVED: return 100;" in trace
    assert "__devtool_wrapper_emit" in wrapper
    assert "am broadcast" in wrapper
    assert "exitReporter()" in wrapper
    assert "BRIDGE_ROUNDTRIP_CALLBACK_RECEIVED" in bridge
    assert "boolean callbackReceived = has(events, RunCommandTrace.BRIDGE_ROUNDTRIP_CALLBACK_RECEIVED)" in bridge
    assert "tokenMarkersOk" in bridge and "wrapperStarted" in bridge and "execStarting" in bridge
    assert "RunCommandTrace.fromBridgeIntent(intent)" in receiver
    assert "BridgeRoundtripProbe.OPERATION" in result_service
    assert "System.out.println" not in wrapper


def test_phase47_repo_doctor_executor_and_path_repair_regressions_are_sealed() -> None:
    doctor_result = read_java("RepositoryDoctorResult.java")
    doctor = read_java("RepositoryDoctor.java")
    planner = read_java("RootExecutionPlan.java")
    broker = read_java("RootCommandBroker.java")
    resolver = read_java("RepositoryPathResolver.java")
    store = read_java("RepositoryStore.java")
    for symbol in (
        "repo_path_wrong_namespace",
        "repo_doctor_path_missing",
        "repo_git_root_missing",
        "repo_doctor_git_missing",
        "repo_doctor_shell_missing",
        "repo_chroot_user_missing",
        "repo_doctor_runuser_failed",
        "repo_git_command_failed",
        "selectedShell",
        "userSwitchMethod",
        "rejectedShellCandidates",
        "rejectedUserSwitchCandidates",
    ):
        assert symbol in doctor_result
    assert "static JSONObject supportSummary" in doctor
    for symbol in (
        "zshAvailable",
        "bashAvailable",
        "shAvailable",
        "runuserAvailable",
        "runuserWorks",
        "suAvailable",
        "suWorks",
        "METHOD_DIRECT_ROOT",
        "root_execution_plan_direct_root_fallback",
        "root_execution_plan_su_fallback",
        "rejected_shell_candidates",
        "rejected_user_switch_candidates",
    ):
        assert symbol in planner
    assert "RootExecutionPlan.fromProbe" in broker
    assert "CommandResult execute(RepositoryLocation location" in broker
    for symbol in (
        "repo_termux_path_saved_as_chroot",
        "repo_chroot_path_saved_as_termux_or_root",
        "repo_root_path_saved_as_termux",
        "repo_termux_path_saved_as_root",
        "repo_discovered_git_root_available",
        "repo_invalid_home_expansion",
        "repo_missing_path_repair_available",
    ):
        assert symbol in resolver
    assert "applyPathRepair" in store
    assert "REPO_PATH_REPAIR_APPLIED" in store
    for forbidden in ("git reset", "git clean", "rm -rf"):
        assert forbidden not in resolver


def test_phase47_git_trace_queue_recovery_guided_fixes_and_workbench_are_sealed() -> None:
    git_trace = read_java("GitOperationTrace.java")
    queue = read_java("RepoQueueRecovery.java")
    workspace = read_java("RepositoryWorkspaceStore.java")
    fixes = read_java("DebugGuidedFix.java")
    workbench = read_java("DebugWorkbenchActivity.java")
    health = read_java("DebugWorkbenchHealth.java")
    for symbol in (
        "GIT_OPERATION_QUEUED",
        "GIT_OPERATION_STARTED",
        "REPO_EXECUTION_PLAN_BUILT",
        "REPO_PATH_CONFIRMED",
        "GIT_BINARY_CONFIRMED",
        "GIT_COMMAND_STARTED",
        "GIT_OUTPUT_RECEIVED",
        "GIT_OPERATION_FINISHED",
        "GIT_OPERATION_FAILED",
        "REPO_PATH_CONFIRMATION_FAILED",
        "GIT_BINARY_CONFIRMATION_FAILED",
        "REPO_GIT_ROOT_CONFIRMATION_FAILED",
        "before_git_command",
    ):
        assert symbol in git_trace
    assert "database.addLogEntry" in git_trace
    assert "DebugRecorder.record" in git_trace
    assert "System.out.println" not in git_trace
    for symbol in (
        "canTerminateOrphan",
        "canMarkInterrupted",
        "canRetry",
        "REPO_QUEUE_HEALTH_FAILED",
        "can_terminate_orphan",
        "can_mark_interrupted",
    ):
        assert symbol in queue
    for symbol in ("terminateOrphan", "markInterrupted", "static boolean retry", "REPO_OPERATION_RETRY_UNAVAILABLE"):
        assert symbol in workspace
    for symbol in (
        "whatHappened",
        "whatItMeans",
        "nextAction",
        "copyable evidence",
        "termux_wrapper_never_started",
        "repo_path_wrong_namespace",
        "repo_runuser_failed",
        "repo_operation_orphaned",
        "validation_stale_revision",
        "notification_blocked",
    ):
        assert symbol in fixes
    assert "DebugWorkbenchActivity extends Activity" in workbench
    assert "Guided Fixes" in workbench
    assert "DebugWorkbenchHealth.assess" in workbench
    assert "DebugGuidedFix.from(this, snapshot)" in workbench
    assert "supportSummary" in health
    assert 'android:name=".DebugWorkbenchActivity"' in read(MANIFEST)


def test_phase47_support_bundle_v2_redaction_and_safe_notifications_are_sealed() -> None:
    support = read_java("SupportBundle.java")
    redactor = read_java("SupportRedactor.java")
    notification = read_java("DebugNotificationController.java")
    receiver = read_java("ActionReceiver.java")
    classifier = read_java("DebugIssueClassifier.java")
    for section in (
        'root.put("schema", 2)',
        'root.put("bundle_version", "support_bundle_v2")',
        'root.put("summary", summary(app))',
        'root.put("latest_probe_results", latestProbeResults(app))',
        'root.put("debug_events", debugEvents(app, token))',
        'root.put("run_command_trace", runCommandTrace(app, token))',
        'root.put("bridge_roundtrip", bridgeRoundtrip(app))',
        'root.put("repo_doctor_results", repoDoctorResults(app))',
        'root.put("repo_execution_plan", repoExecutionPlan(app))',
        'root.put("repo_path_resolution", repoPathResolution(app))',
        'root.put("repo_queue_health", repoQueueHealth(app))',
        'root.put("recent_git_operations", recentGitOperations(app, token))',
        'root.put("guided_fixes", DebugGuidedFix.supportSummary(app))',
        'root.put("validation_health", validationHealth(app))',
        'root.put("notification_health", notificationHealth(app))',
        'root.put("redaction_self_test", redactionSelfTest(token))',
        'root.put("debug_workbench", DebugWorkbenchHealth.supportSummary(app))',
    ):
        assert section in support
    for symbol in (
        "MAX_DEBUG_EVENTS = 160",
        "MAX_TRACE_EVENTS = 96",
        'value.put("raw_log_output_included", false)',
        'value.put("safe_actions_only", true)',
        "SupportRedactor.redact(sample, token)",
        'value.put("private_key_redacted"',
    ):
        assert symbol in support
    for symbol in ("BEARER", "HTTPS_CREDENTIALS", "KEY_VALUE_SECRET", "PRIVATE_KEY_BLOCK"):
        assert symbol in redactor
    for symbol in (
        "CHANNEL_ID = \"devtool_debug_notifications\"",
        "safe_actions_only=true",
        "Open Debug",
        "Open Doctor",
        "Open Validation",
        "Copy report",
        "Copy health",
        "Copy bundle",
        "Run probe",
        "Export Bundle",
    ):
        assert symbol in notification
    for symbol in ("ACTION_COPY_DEBUG_REPORT", "ACTION_COPY_DEBUG_HEALTH", "ACTION_COPY_SUPPORT_BUNDLE", "ACTION_RUN_BRIDGE_PROBE"):
        assert symbol in receiver
    for symbol in ("bridge_token_mismatch", "termux_wrapper_never_started", "repo_path_wrong_namespace", "repo_runuser_failed"):
        assert symbol in classifier
    for source in (support, notification, receiver):
        for forbidden in ("ACTION_GIT_RESET", "ACTION_GIT_CLEAN", "ACTION_DELETE_REPOSITORY", "git reset", "git clean", "rm -rf", "deleteRepository"):
            assert forbidden not in source
