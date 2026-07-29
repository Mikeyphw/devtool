from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main"
JAVA = APP / "java" / "io" / "github" / "mikeceead" / "devtoolhud"
LAYOUT = APP / "res" / "layout"
VALUES = APP / "res" / "values"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def java(name: str) -> str:
    return read(JAVA / name)


def test_phase47c2_removes_min_sdk_26_obsolete_sdk_guards() -> None:
    debug_notifications = java("DebugNotificationController.java")
    assert "if (Build.VERSION.SDK_INT >= 26) return new Notification.Builder(context, CHANNEL_ID)" not in debug_notifications
    assert "return new Notification.Builder(context);" not in debug_notifications
    assert "if (Build.VERSION.SDK_INT < 26) return;" not in debug_notifications
    assert "return new Notification.Builder(context, CHANNEL_ID);" in debug_notifications
    assert "new NotificationChannel(CHANNEL_ID, \"Debug notifications\"" in debug_notifications

    repo_notifications = java("RepositoryNotificationController.java")
    assert "manager != null && Build.VERSION.SDK_INT >= 26" not in repo_notifications
    assert "Build.VERSION.SDK_INT >= 26\n                ? new Notification.Builder(context, CHANNEL)" not in repo_notifications
    assert "Notification.Builder builder = new Notification.Builder(context, CHANNEL);" in repo_notifications

    overlay = java("OverlayHudService.java")
    assert "return Build.VERSION.SDK_INT < 23 || Settings.canDrawOverlays(context);" not in overlay
    assert "return Settings.canDrawOverlays(context);" in overlay
    assert "if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(intent);" not in overlay
    assert "context.startForegroundService(intent);" in overlay
    assert "if (Build.VERSION.SDK_INT >= 21)" not in overlay
    assert "overallProgress.setProgressTintList" in overlay
    assert "stepProgress.setProgressTintList" in overlay

    main = java("MainActivity.java")
    assert "Build.VERSION.SDK_INT >= 23 && !TermuxCommandRunner.hasPermission" not in main
    assert "if (!TermuxCommandRunner.hasPermission(this))" in main

    termux = java("TermuxCommandRunner.java")
    assert "Build.VERSION.SDK_INT < 23" not in termux
    assert "return context.checkSelfPermission(RUN_COMMAND_PERMISSION) == PackageManager.PERMISSION_GRANTED;" in termux

    tasker = java("TaskerProtocol.java")
    assert "import android.os.Build;" not in tasker
    assert "foreground && Build.VERSION.SDK_INT >= 26" not in tasker
    assert "if (foreground) context.startForegroundService(completion);" in tasker


def test_phase47c2_deletes_obsolete_mipmap_anydpi_v26_folder() -> None:
    delete_manifest = read(ROOT / ".devtool-delete")
    assert "android/devtool-live-hud/app/src/main/res/mipmap-anydpi-v26" in delete_manifest
    assert (APP / "res" / "mipmap" / "ic_launcher.xml").is_file()
    assert (APP / "res" / "mipmap" / "ic_launcher_round.xml").is_file()


def test_phase47c3_notification_layouts_use_string_resources() -> None:
    strings = read(VALUES / "strings.xml")
    for name in (
        "notification_app_label",
        "notification_state_live",
        "notification_default_task_title",
        "notification_metric_tests",
        "notification_metric_warnings",
        "notification_metric_errors",
        "notification_metric_elapsed",
    ):
        assert f'name="{name}"' in strings

    for layout_name in (
        "notification_devtool_compact.xml",
        "notification_devtool_expanded.xml",
        "notification_devtool_expanded_large_text.xml",
    ):
        layout = read(LAYOUT / layout_name)
        assert 'android:text="Devtool"' not in layout
        assert 'android:text="LIVE"' not in layout
        assert 'android:text="Applying overlay bundle"' not in layout
        assert 'android:text="Tests"' not in layout
        assert 'android:text="Warnings"' not in layout
        assert 'android:text="Errors"' not in layout
        assert 'android:text="Elapsed"' not in layout

    expanded = read(LAYOUT / "notification_devtool_expanded.xml")
    assert 'android:orientation="horizontal" android:baselineAligned="false" android:layout_marginTop="7dp"' in expanded
