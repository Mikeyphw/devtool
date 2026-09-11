from __future__ import annotations

from pathlib import Path
import json
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main"
JAVA = APP / "java" / "io" / "github" / "mikeceead" / "devtoolhud"
RES = APP / "res"
ANDROID = "{http://schemas.android.com/apk/res/android}"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def ids(path: Path) -> set[str]:
    root = ET.parse(path).getroot()
    result: set[str] = set()
    for node in root.iter():
        value = node.attrib.get(ANDROID + "id", "")
        if value.startswith("@+id/"):
            result.add(value.removeprefix("@+id/"))
    return result


def test_notification_uses_custom_collapsed_and_expanded_mock_layouts() -> None:
    controller = read(JAVA / "NotificationController.java")
    assert 'CHANNEL_ID = "devtool_live_runs_v3"' in controller
    assert "R.layout.notification_devtool_compact" in controller
    assert "R.layout.notification_devtool_expanded" in controller
    assert ".setCustomContentView(compact)" in controller
    assert ".setCustomBigContentView(expanded)" in controller
    assert ".setCustomHeadsUpContentView(compact)" in controller
    assert 'putString("devtool.notification_layout", "semantic-v2")' in controller


def test_expanded_layout_contains_mock_hierarchy() -> None:
    compact = ids(RES / "layout" / "notification_devtool_compact.xml")
    expanded = ids(RES / "layout" / "notification_devtool_expanded.xml")
    assert {
        "notification_app_icon", "notification_state_dot", "notification_state_label",
        "notification_task_title", "notification_compact_status",
        "notification_progress_label", "notification_progress",
        "notification_status_lead", "notification_status_detail",
    } <= compact
    assert {
        "notification_artifact", "notification_workspace_name", "notification_workspace_index",
        "notification_phase_row_1", "notification_phase_row_2", "notification_phase_row_3",
        "notification_workspace_current", "notification_workspace_meta",
        "notification_status_lead", "notification_status_detail",
        "notification_workspace_remaining", "notification_metric_tests",
        "notification_metric_warnings", "notification_metric_errors",
        "notification_metric_elapsed", "notification_action_1",
        "notification_action_2", "notification_action_3",
    } <= expanded


def test_notification_state_modes_and_actions_match_approved_mock() -> None:
    controller = read(JAVA / "NotificationController.java")
    update = read(JAVA / "HudUpdate.java")
    for label in ("LIVE", "COMPLETE", "FAILED", "ROLLBACK", "RECOVERED", "CRITICAL"):
        assert f'return "{label}"' in update
    for title in ("Overlay applied successfully", "Build failed", "Repository restored", "Rollback failed"):
        assert f'return "{title}"' in update
    assert '(mode.equals("warning") && !update.isTerminal())' in controller
    assert 'mode.equals("success") || mode.equals("warning")' in controller
    for action in (
        "Open log", "Copy summary", "Cancel", "Open app", "Dismiss",
        "Copy error", "Retry", "Copy status", "Keep visible", "Copy recovery",
    ):
        assert f'new ActionSpec("{action}"' in controller


def test_workspace_payload_is_end_to_end_and_part_of_refresh_identity() -> None:
    hud = read(ROOT / "devtool" / "hud.py")
    update = read(JAVA / "HudUpdate.java")
    assert '"workspaces_json": json.dumps(self._workspace_rows(state)' in hud
    signature = hud[hud.index("signature = (", hud.index("class AndroidAppHud")):]
    assert 'payload.get("workspaces_json")' in signature
    assert 'intent.getStringExtra("workspaces_json")' in update
    assert "parseWorkspaces(this.workspacesJson)" in update
    assert "remainingWorkspacesText()" in update

def test_android_notification_has_live_elapsed_and_heartbeat_refresh() -> None:
    controller = read(JAVA / "NotificationController.java")
    gate = read(JAVA / "BridgeUpdateGate.java")
    hud = read(ROOT / "devtool" / "hud.py")
    expanded = read(RES / "layout" / "notification_devtool_expanded.xml")
    large = read(RES / "layout" / "notification_devtool_expanded_large_text.xml")
    assert "views.setChronometer(R.id.notification_metric_elapsed" in controller
    assert "SystemClock.elapsedRealtime()" in controller
    assert '<Chronometer android:id="@+id/notification_metric_elapsed"' in expanded
    assert '<Chronometer android:id="@+id/notification_metric_elapsed"' in large
    assert "HEARTBEAT_INTERVAL_SECONDS = 5" in hud
    assert '"heartbeat": heartbeat_bucket' in hud
    signature = hud[hud.index("signature = (", hud.index("class AndroidAppHud")):]
    assert 'payload.get("heartbeat")' in signature
    assert "sequence == lastSequence && updatedAt <= lastUpdated" in gate


def test_notification_resources_and_contract_are_parseable() -> None:
    for path in [
        RES / "layout" / "notification_devtool_compact.xml",
        RES / "layout" / "notification_devtool_expanded.xml",
        RES / "values" / "colors.xml",
        *sorted((RES / "drawable").glob("notification_*.xml")),
    ]:
        ET.parse(path)
    contract = json.loads(read(ROOT / "android" / "devtool-live-hud" / "docs" / "notification-ui-contract.json"))
    assert contract["schema_version"] == 1
    assert contract["layout"] == "mock-v1"
    assert contract["actions"]["running"] == ["Open log", "Copy summary", "Cancel"]
    assert contract["actions"]["failed"] == ["Open log", "Copy error", "Retry"]
    assert contract["parity"]["status_lines_below_progress"] is True


def test_notification_status_lines_match_desktop_hud_semantics() -> None:
    controller = read(JAVA / "NotificationController.java")
    update = read(JAVA / "HudUpdate.java")
    compact_xml = read(RES / "layout" / "notification_devtool_compact.xml")
    expanded_xml = read(RES / "layout" / "notification_devtool_expanded.xml")
    large_xml = read(RES / "layout" / "notification_devtool_expanded_large_text.xml")
    assert "String notificationStatusLead()" in update
    assert "String notificationStatusDetail()" in update
    assert "String notificationStatusLine()" in update
    assert "views.setTextViewText(R.id.notification_status_lead, update.notificationStatusLead())" in controller
    assert "views.setTextViewText(R.id.notification_status_detail, update.notificationStatusDetail())" in controller
    assert 'putString("devtool.status_lead", update.notificationStatusLead())' in controller
    assert 'putString("devtool.status_detail", update.notificationStatusDetail())' in controller
    for xml in (compact_xml, expanded_xml, large_xml):
        assert '@+id/notification_status_lead' in xml
        assert '@+id/notification_status_detail' in xml
        assert xml.index('@+id/notification_progress') < xml.index('@+id/notification_status_lead')
