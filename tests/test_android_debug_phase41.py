from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_phase41_git_operation_trace_model_defines_clean_timeline_and_preflight() -> None:
    trace = read("GitOperationTrace.java")
    for symbol in (
        "GitOperationTrace",
        "GIT_OPERATION_QUEUED",
        "GIT_OPERATION_STARTED",
        "REPO_EXECUTION_PLAN_BUILT",
        "REPO_PATH_CONFIRMED",
        "GIT_BINARY_CONFIRMED",
        "GIT_COMMAND_STARTED",
        "GIT_OUTPUT_RECEIVED",
        "GIT_OPERATION_FINISHED",
        "GIT_OPERATION_FAILED",
        "EXIT_PATH_MISSING_BEFORE_GIT",
        "EXIT_GIT_MISSING_BEFORE_GIT",
        "EXIT_NOT_GIT_BEFORE_GIT",
        "preflightPayload(String repoPath)",
        "supportSummary(Context context, RepositoryOperationRecord operation",
        "plainText(Context context, RepositoryOperationRecord operation",
    ):
        assert symbol in trace
    assert "status --porcelain=v2 -z" not in trace
    assert "git reset" not in trace
    assert "git clean" not in trace


def test_phase41_workspace_records_operation_timeline_without_git_output_pollution() -> None:
    workspace = read("RepositoryWorkspaceStore.java")
    for symbol in (
        "GitOperationTrace.queued",
        "GitOperationTrace.started",
        "GitOperationTrace.outputReceived",
        "GitOperationTrace.finished",
        "request.commandDescription()",
    ):
        assert symbol in workspace
    assert "GitOperationTrace" in workspace
    assert "System.out" not in workspace


def test_phase41_broker_prefights_path_git_and_records_command_started() -> None:
    broker = read("RootCommandBroker.java")
    for symbol in (
        "GitOperationTrace.executionPlanBuilt",
        "GitOperationTrace.preflightPayload(commandPath(location))",
        "GitOperationTrace.preflightFailed(preflight)",
        "GitOperationTrace.executorPreflightFailed",
        "GitOperationTrace.pathConfirmed",
        "GitOperationTrace.gitBinaryConfirmed",
        "GitOperationTrace.commandStarted",
        "preflightTimeout",
    ):
        assert symbol in broker
    assert "Git trace preflight" not in broker
    assert broker.count("GitOperationTrace.commandStarted") >= 3


def test_phase41_support_bundle_and_repository_ui_surface_trace() -> None:
    support = read("SupportBundle.java")
    detail = read("RepositoryDetailActivity.java")
    assert "git_operation_trace" in support
    assert "GitOperationTrace.supportSummary(context, operation, token)" in support
    assert "Copy Git trace" in detail
    assert "GitOperationTrace.plainText" in detail


def test_phase41_classifier_knows_git_trace_failures_and_milestones() -> None:
    classifier = read("DebugIssueClassifier.java")
    for issue in (
        "git_operation_queued",
        "git_operation_started",
        "repo_execution_plan_built",
        "repo_path_confirmed",
        "git_binary_confirmed",
        "git_command_started",
        "git_output_received",
        "git_operation_finished",
        "git_operation_failed",
        "repo_path_confirmation_failed",
        "git_binary_confirmation_failed",
        "repo_git_root_confirmation_failed",
        "git_operation_preflight_failed",
    ):
        assert issue in classifier
