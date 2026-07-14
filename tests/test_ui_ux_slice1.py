from __future__ import annotations

import json
import tempfile
from pathlib import Path

from devtool.tasker import TaskerProgressSink


ROOT = Path(__file__).resolve().parents[1]
ANDROID_JAVA = (
    ROOT
    / "android"
    / "devtool-live-hud"
    / "app"
    / "src"
    / "main"
    / "java"
    / "io"
    / "github"
    / "mikeceead"
    / "devtoolhud"
)
DESKTOP_QML = ROOT / "desktop" / "devtool-control-center" / "qml"


def source(name: str) -> str:
    return (ANDROID_JAVA / name).read_text(encoding="utf-8")


def test_android_notifications_are_per_run_and_grouped() -> None:
    notification = source("NotificationController.java")
    assert 'GROUP_KEY = "io.github.mikeceead.devtoolhud.LIVE_RUNS"' in notification
    assert "manager.notify(notificationId(update.runId), builder.build())" in notification
    assert "manager.cancel(notificationId(runId))" in notification
    assert "RunStore.activeAll(context)" in notification
    assert ".setGroupSummary(true)" in notification


def test_android_overlay_keeps_multiple_runs() -> None:
    overlay = source("OverlayHudService.java")
    assert "LinkedHashMap<String, HudUpdate> currentRuns" in overlay
    assert "Map<String, HudUpdate> pendingUpdates" in overlay
    assert "currentRuns.put(update.runId, update)" in overlay
    assert "currentRuns.remove(runId)" in overlay


def test_android_run_draft_and_execution_constraint_ui_are_present() -> None:
    settings = source("AppSettings.java")
    activity = source("MainActivity.java")
    config = source("DevtoolRunConfig.java")
    builder = source("DevtoolCommandBuilder.java")

    assert 'KEY_RUN_DRAFT = "run_draft_json"' in settings
    assert "static DevtoolRunConfig runDraft(Context context)" in settings
    assert "static void setRunDraft(Context context, DevtoolRunConfig config)" in settings
    assert "captureRunDraft()" in activity
    assert "syncExecutionControls(" in activity
    assert "String validationError()" in config
    assert "config.validationError()" in builder
    assert "Direct chroot mode cannot be combined" in config


def test_runner_terminology_is_shared_and_not_dotnet_specific() -> None:
    compact = (DESKTOP_QML / "CompactHud.qml").read_text(encoding="utf-8")
    main = (DESKTOP_QML / "Main.qml").read_text(encoding="utf-8")
    hud_update = source("HudUpdate.java")

    assert "runnerLabel()" in compact
    assert "projectUnitLabel()" in compact
    assert 'text: "Details"' in compact
    assert "Waiting for runner output" in compact
    assert "1.0 · Multi-runner" in main
    assert "1.0 · MULTI-RUNNER" not in main
    assert "String runnerLabel()" in hud_update
    assert "String environmentLabel()" in hud_update

    combined = compact + main + hud_update + source("MainActivity.java")
    for stale in ("LIVE .NET PARSER", "Test DLLs", "Building solution", "desktop GTK palette"):
        assert stale not in combined


def test_tasker_state_carries_runner_and_execution_environment() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        sink = TaskerProgressSink(
            directory=Path(tmp),
            command="validate",
            repo_root="/repo/demo",
            repo_name="demo",
            project_type="android",
            execution_environment="native-termux",
            update_interval_s=0,
        )
        sink.started()
        state = json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))
        assert state["project_type"] == "android"
        assert state["execution_environment"] == "native-termux"
        sink.finished(
            ok=True,
            totals={"warnings": 0, "errors": 0, "failed": 0, "skipped": 0},
            results=[],
        )
