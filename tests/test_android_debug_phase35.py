from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_phase35_run_command_trace_model_has_ordered_launch_checkpoints() -> None:
    trace = read("RunCommandTrace.java")
    for checkpoint in (
        "UI_RUN_REQUESTED",
        "COMMAND_PLAN_BUILT",
        "COMMAND_ENVELOPE_WRITTEN",
        "TERMUX_INTENT_BUILT",
        "TERMUX_INTENT_SENT",
        "TERMUX_RUNCOMMAND_ACCEPTED",
        "RUNSTORE_CREATED",
        "TERMUX_SHELL_STARTED",
        "FIRST_PROGRESS_EVENT_RECEIVED",
        "RUN_ACTIVE",
        "RUN_PROGRESS_WATCHDOG_WAITING",
        "TERMUX_RESULT_RECEIVED",
        "RUN_COMPLETED",
        "RUN_FAILED",
    ):
        assert checkpoint in trace
    assert "checkpoint_order" in trace
    assert "RunStore.traceCheckpoint" in trace
    assert "DebugRecorder.info(context, \"run_trace\"" in trace
    assert "fromBridgeIntent" in trace


def test_phase35_termux_runner_records_android_side_launch_trace() -> None:
    termux = read("TermuxCommandRunner.java")
    for checkpoint in (
        "RunCommandTrace.COMMAND_PLAN_BUILT",
        "RunCommandTrace.COMMAND_ENVELOPE_WRITTEN",
        "RunCommandTrace.TERMUX_INTENT_BUILT",
        "RunCommandTrace.TERMUX_INTENT_SENT",
        "RunCommandTrace.TERMUX_RUNCOMMAND_ACCEPTED",
        "RunCommandTrace.RUNSTORE_CREATED",
        "RunCommandTrace.RUN_PROGRESS_WATCHDOG_WAITING",
    ):
        assert checkpoint in termux
    assert "String[] arguments = shellArguments" in termux
    assert ".putExtra(EXTRA_ARGUMENTS, arguments)" in termux


def test_phase35_shell_start_acknowledgement_is_explicit_trace_checkpoint() -> None:
    termux = read("TermuxCommandRunner.java")
    bridge = read("BridgeReceiver.java")
    assert "trace_checkpoint" in termux
    assert "RunCommandTrace.TERMUX_SHELL_STARTED" in termux
    assert "trace_message" in termux
    assert "RunCommandTrace.fromBridgeIntent" in bridge
    assert "RunCommandTrace.bridgeMessage" in bridge
    assert "RunCommandTrace.record(context, traceCheckpoint" in bridge


def test_phase35_run_store_appends_trace_to_execution_log_and_debug_events() -> None:
    store = read("RunStore.java")
    assert "traceCheckpoint(Context context, String runId" in store
    assert "appendTraceCheckpoint" in store
    assert '"trace " + cleanCheckpoint' in store
    assert '"run_trace"' in store
    assert "RunCommandTrace.FIRST_PROGRESS_EVENT_RECEIVED" in store
    assert "RunCommandTrace.RUN_ACTIVE" in store
    assert "RunCommandTrace.TERMUX_RESULT_RECEIVED" in store
    assert "RunCommandTrace.RUN_COMPLETED" in store
    assert "RunCommandTrace.RUN_FAILED" in store


def test_phase35_main_and_root_bridge_run_paths_are_traced() -> None:
    main = read("MainActivity.java")
    root = read("RootBridgeManager.java")
    assert "RunCommandTrace.UI_RUN_REQUESTED" in main
    for checkpoint in (
        "RunCommandTrace.COMMAND_PLAN_BUILT",
        "RunCommandTrace.COMMAND_ENVELOPE_WRITTEN",
        "RunCommandTrace.RUNSTORE_CREATED",
        "RunCommandTrace.ROOT_BRIDGE_DISPATCHED",
        "RunCommandTrace.ROOT_BRIDGE_PROCESS_STARTED",
    ):
        assert checkpoint in root


def test_phase35_debug_classifier_knows_launch_trace_failure_shapes() -> None:
    classifier = read("DebugIssueClassifier.java")
    assert "termux_wrapper_never_started" in classifier
    assert "first_progress_received" in classifier
