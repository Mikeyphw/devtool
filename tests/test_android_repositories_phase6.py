from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "android/devtool-live-hud/app/src/main/java/io/github/mikeceead/devtoolhud"
ANDROID_TEST = ROOT / "android/devtool-live-hud/app/src/androidTest/java/io/github/mikeceead/devtoolhud"


def read(name: str) -> str:
    return (APP / name).read_text()


def test_phase6_adaptive_layout_policy_and_surfaces():
    adaptive = read("AdaptiveLayout.java")
    main = read("MainActivity.java")
    detail = read("RepositoryDetailActivity.java")
    viewer = read("LogViewerActivity.java")
    assert "screenWidthDp" in adaptive
    assert ">= 720" in adaptive
    assert ">= 840" in adaptive
    assert "fontScale(context) >= 1.30f" in adaptive
    assert "fontScale(context) >= 1.60f" in adaptive
    assert "buildNavigationRail" in main
    assert "ui.twoPaneLayout()" in detail
    assert "ui.twoPaneLayout()" in viewer
    assert "ui.verticalActions()" in main


def test_phase6_retention_preserves_active_and_pinned_runs():
    database = read("DevtoolDatabase.java")
    settings = read("AppSettings.java")
    manager = read("RetentionManager.java")
    assert "private static final int VERSION = 9" in database
    assert "pinned INTEGER NOT NULL DEFAULT 0" in database
    assert "status IN ('ok','failed','cancelled','interrupted','skipped') AND pinned=0" in database
    assert "state NOT IN ('queued','running','orphaned')" in database
    assert "VACUUM" in database
    assert "AUTOMATIC_INTERVAL_MS" in manager
    assert "setRetentionPolicy" in settings
    assert "maximumCompletedRuns" in settings


def test_phase6_queue_reordering_is_repository_local_and_persisted():
    workspace = read("RepositoryWorkspaceStore.java")
    database = read("DevtoolDatabase.java")
    record = read("RepositoryOperationRecord.java")
    assert "moveQueued(Context context, String repositoryId, String operationId, int direction)" in workspace
    assert "ArrayDeque<QueuedOperation> queue = QUEUES.get(id)" in workspace
    assert "Collections.swap" in workspace
    assert "updateRepositoryOperationQueueOrder" in workspace
    assert "queue_order" in database
    assert "queueOrder" in record


def test_phase6_remote_guidance_remains_passive_and_noninteractive():
    broker = read("RootCommandBroker.java")
    diagnostics = read("RemoteDiagnostics.java")
    detail = read("RepositoryDetailActivity.java")
    assert "remediationSteps" in diagnostics
    assert "addRemoteRemediation" in broker
    assert "ssh-keyscan" in broker
    assert "Copy command" in detail
    assert "Host fingerprints must be verified independently" in detail
    forbidden = ["ssh -T", "git ls-remote", "git clean", "push --force", "push -f"]
    for value in forbidden:
        assert value not in broker


def test_phase6_accessibility_instrumentation_covers_profiles_and_targets():
    test = (ANDROID_TEST / "Phase6AdaptiveAccessibilityTest.java").read_text()
    assert "testAdaptivePolicyHandlesPhoneTabletFoldableAndLargeText" in test
    assert "testMainActivityButtonsHaveLabelsAndTouchTargets" in test
    assert "phoneConfig.screenWidthDp = 360" in test
    assert "tabletConfig.screenWidthDp = 900" in test
    assert "48" in test


def test_phase6_pin_controls_and_retention_settings_are_exposed():
    main = read("MainActivity.java")
    viewer = read("LogViewerActivity.java")
    run_store = read("RunStore.java")
    assert "Indexed data retention" in main
    assert "Clean and compact database" in main
    assert 'record.pinned ? "Unpin" : "Pin"' in main
    assert "RunStore.setPinned" in main
    assert "RunStore.setPinned" in viewer
    assert "setPinned(Context context, String runId, boolean pinned)" in run_store


def test_phase6_logs_are_bounded_async_and_debounced():
    main = read("MainActivity.java")
    viewer = read("LogViewerActivity.java")
    database = read("DevtoolDatabase.java")
    assert "HISTORY_PAGE_SIZE = 100" in main
    assert "LOG_FILTER_DEBOUNCE_MS" in main
    assert "historyExecutor.execute" in main
    assert "historyLoadGeneration" in main
    assert '"Load more runs"' in main
    assert "LOG_PAGE_SIZE = 100" in viewer
    assert "UPDATE_DEBOUNCE_MS" in viewer
    assert "logExecutor.execute" in viewer
    assert "loadGeneration" in viewer
    assert '"Load more log entries"' in viewer
    assert "logLines(String runId, boolean problemsOnly, int limit, int offset)" in database
    assert "rawLogLines(String runId, int limit, int offset)" in database


def test_phase6_release_metadata():
    build = (ROOT / "android/devtool-live-hud/app/build.gradle").read_text()
    versions = json.loads((ROOT / "release/versions.json").read_text())
    assert "versionCode 30" in build
    assert "versionName '1.16.0'" in build
    assert versions["android"]["version_name"] == "1.16.0"
    assert versions["android"]["version_code"] == 30
