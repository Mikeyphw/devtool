from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_phase46_debug_notification_controller_posts_safe_problem_cards() -> None:
    controller = read("DebugNotificationController.java")
    for symbol in (
        "DebugNotificationController",
        "CHANNEL_ID = \"devtool_debug_notifications\"",
        "GROUP_KEY = \"io.github.mikeceead.devtoolhud.DEBUG_NOTIFICATIONS\"",
        "maybePost(Context context, DebugEventRecord event)",
        "postSupportBundleReady(Context context, String mode, String bundle)",
        "Devtool launch stalled",
        "Bridge roundtrip failed",
        "Repo manager needs attention",
        "Repo operation orphaned",
        "Validation stopped",
        "Notification blocked",
        "Debug bundle ready",
        "safe_actions_only=true",
    ):
        assert symbol in controller
    for symbol in (
        "Open Debug",
        "Open Doctor",
        "Open Validation",
        "Copy report",
        "Copy bundle",
        "Copy health",
        "Run probe",
        "Export Bundle",
    ):
        assert symbol in controller
    for forbidden in ("git reset", "git clean", "rm -rf", "deleteRepository", "Hard reset", "Discard all"):
        assert forbidden not in controller


def test_phase46_debug_recorder_auto_posts_only_problem_events_after_persisting_ids() -> None:
    recorder = read("DebugRecorder.java")
    assert "event.eventId = DevtoolDatabase.get(context.getApplicationContext()).addDebugEvent(event)" in recorder
    assert "DebugNotificationController.maybePost(context.getApplicationContext(), event)" in recorder
    controller = read("DebugNotificationController.java")
    assert "if (context == null || event == null || !event.problem()) return;" in controller
    assert "notificationsAllowed(app)" in controller
    assert "ensureChannel(app, manager)" in controller


def test_phase46_notification_actions_are_broadcast_open_copy_probe_export_only() -> None:
    receiver = read("ActionReceiver.java")
    for symbol in (
        "ACTION_RUN_BRIDGE_PROBE",
        "ACTION_COPY_DEBUG_REPORT",
        "ACTION_COPY_DEBUG_HEALTH",
        "ACTION_COPY_SUPPORT_BUNDLE",
        "BridgeRoundtripProbe.start(context)",
        "DebugWorkbenchHealth.plainText(context)",
        "SupportBundle.build(context)",
        "copyToClipboard(Context context, String label, String value, String toast)",
    ):
        assert symbol in receiver
    destructive_actions = (
        "ACTION_GIT_RESET",
        "ACTION_GIT_CLEAN",
        "ACTION_DELETE_REPOSITORY",
        "deleteRepository",
        "hardReset",
        "cleanTracked",
    )
    for symbol in destructive_actions:
        assert symbol not in receiver


def test_phase46_debug_workbench_bundle_export_posts_ready_notification() -> None:
    activity = read("DebugWorkbenchActivity.java")
    for symbol in (
        "copySupportBundle()",
        "shareSupportBundle()",
        "DebugNotificationController.postSupportBundleReady(this, \"copied\", bundle)",
        "DebugNotificationController.postSupportBundleReady(this, \"shared\", bundle)",
        "Copy support bundle",
        "Share support bundle",
    ):
        assert symbol in activity


def test_phase46_support_bundle_reports_debug_notification_channel_and_safe_actions() -> None:
    support = read("SupportBundle.java")
    for symbol in (
        "DebugNotificationController.CHANNEL_ID",
        "DebugNotificationController.GROUP_KEY",
        "debug_notification_safe_actions",
        "Open Debug, Open Doctor, Open Validation, Copy Report, Copy Trace, Export Bundle, Run Probe",
        'value.put("safe_actions_only", true)',
    ):
        assert symbol in support
