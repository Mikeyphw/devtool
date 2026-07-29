from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_phase42_repo_queue_recovery_model_defines_health_and_safe_actions() -> None:
    model = read("RepoQueueRecovery.java")
    for symbol in (
        "RepoQueueRecovery",
        "STALE_QUEUED_MS",
        "STALE_RUNNING_MS",
        "QueueHealth",
        "assess(Context context, RepositoryRecord repository)",
        "recordProbe(Context context, RepositoryRecord repository)",
        "canMarkInterrupted",
        "canTerminateOrphan",
        "canRetry",
        "supportSummary(Context context, RepositoryRecord repository)",
        "plainText(Context context, RepositoryRecord repository)",
        "repo_queue_health",
    ):
        assert symbol in model
    for forbidden in ("git reset", "git clean", "rm -rf"):
        assert forbidden not in model


def test_phase42_workspace_exposes_orphan_recovery_mark_interrupted_and_retry() -> None:
    workspace = read("RepositoryWorkspaceStore.java")
    for symbol in (
        "terminateOrphan(Context context, String repositoryId, String operationId)",
        "markInterrupted(Context context, String repositoryId, String operationId)",
        "retry(Context context, String repositoryId, String operationId, boolean afterPathRepair)",
        "queueHealth(Context context, RepositoryRecord repository)",
        "REPO_ORPHAN_TERMINATE_REQUESTED",
        "REPO_OPERATION_MARK_INTERRUPTED",
        "REPO_OPERATION_RETRY_SAME_PLAN_REQUESTED",
        "REPO_OPERATION_RETRY_AFTER_PATH_REPAIR_REQUESTED",
        "REPO_OPERATION_RETRY_UNAVAILABLE",
        "requestFromRecord(RepositoryOperationRecord operation)",
        "activeInMemory(id, wanted)",
    ):
        assert symbol in workspace
    mark_body = workspace[workspace.index("markInterrupted"):workspace.index("static boolean retry")]
    for forbidden in ("git reset", "git clean", "deleteRepository", "rm -rf"):
        assert forbidden not in mark_body


def test_phase42_repository_detail_surfaces_queue_recovery_with_confirmations() -> None:
    detail = read("RepositoryDetailActivity.java")
    for symbol in (
        "queueRecoveryCard(repository)",
        "Repo queue recovery",
        "Check queue health",
        "Copy queue report",
        "Terminate orphan with confirmation",
        "confirmTerminateOrphan",
        "confirmMarkInterrupted",
        "confirmRetryOperation",
        "Retry with same execution plan",
        "Retry after path repair",
        "This only updates Android operation metadata",
        "This sends TERM/KILL only to a detached process tagged with this Git operation id",
    ):
        assert symbol in detail
    assert detail.count("queueRecoveryCard(repository)") >= 2


def test_phase42_support_bundle_exports_queue_health() -> None:
    support = read("SupportBundle.java")
    assert "repo_queue_health" in support
    assert "RepoQueueRecovery.supportSummary(context, repository)" in support
    assert "operations(context, repository.repositoryId, token)" in support


def test_phase42_classifier_knows_queue_recovery_events() -> None:
    classifier = read("DebugIssueClassifier.java")
    for issue in (
        "repo_queue_health_failed",
        "repo_queue_health_warn",
        "repo_operation_mark_interrupted",
        "repo_orphan_terminate_requested",
        "repo_operation_retry_same_plan_requested",
        "repo_operation_retry_after_path_repair_requested",
        "repo_operation_retry_unavailable",
        "repo_operation_orphaned",
    ):
        assert issue in classifier
