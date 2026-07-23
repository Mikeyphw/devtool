from __future__ import annotations

from pathlib import Path
import json
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main"
JAVA = APP / "java" / "io" / "github" / "mikeceead" / "devtoolhud"
RES = APP / "res"
ANDROID = "{http://schemas.android.com/apk/res/android}"

LAYOUTS = (
    RES / "layout" / "notification_devtool_compact.xml",
    RES / "layout" / "notification_devtool_expanded.xml",
    RES / "layout" / "notification_devtool_expanded_large_text.xml",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def nodes_by_id(path: Path) -> dict[str, ET.Element]:
    root = ET.parse(path).getroot()
    result: dict[str, ET.Element] = {}
    for node in root.iter():
        value = node.attrib.get(ANDROID + "id", "")
        if value.startswith("@+id/"):
            result[value.removeprefix("@+id/")] = node
    return result


def test_notification_progress_is_more_prominent_on_all_surfaces() -> None:
    for layout in LAYOUTS:
        nodes = nodes_by_id(layout)
        progress = nodes["notification_progress"]
        assert progress.attrib[ANDROID + "layout_height"] == "7dp"
        assert progress.attrib[ANDROID + "layout_width"] == "match_parent"


def test_notification_state_label_is_the_dominant_non_color_status_marker() -> None:
    styles = read(RES / "values" / "styles.xml")
    assert 'name="TextAppearance.Devtool.Notification.State"' in styles
    assert '<item name="android:textSize">11sp</item>' in styles
    for layout in LAYOUTS:
        state = nodes_by_id(layout)["notification_state_label"]
        assert state.attrib["style"] == "@style/TextAppearance.Devtool.Notification.State"
        assert state.attrib[ANDROID + "minHeight"] == "24dp"
        assert state.attrib[ANDROID + "background"] == "@drawable/notification_chip"
        assert state.attrib[ANDROID + "letterSpacing"] == "0.06"


def test_expanded_notifications_keep_48dp_touch_targets_and_workspace_card() -> None:
    for layout in (RES / "layout" / "notification_devtool_expanded.xml", RES / "layout" / "notification_devtool_expanded_large_text.xml"):
        nodes = nodes_by_id(layout)
        assert nodes["notification_workspace_card"].attrib[ANDROID + "minHeight"] == "48dp"
        for action in ("notification_action_1", "notification_action_2", "notification_action_3"):
            assert nodes[action].attrib[ANDROID + "minHeight"] == "48dp"
            assert nodes[action].attrib[ANDROID + "gravity"] == "center"


def test_notification_remoteviews_have_accessibility_descriptions() -> None:
    controller = read(JAVA / "NotificationController.java")
    assert 'stateLabel + ". " + taskTitle + ". Progress " + progressLabel' in controller
    assert 'views.setContentDescription(R.id.notification_state_label, "State " + stateLabel)' in controller
    assert 'views.setContentDescription(R.id.notification_progress, "Progress " + progressLabel)' in controller
    assert 'views.setContentDescription(R.id.notification_status_lead, statusLead)' in controller
    assert 'views.setContentDescription(R.id.notification_status_detail, statusDetail)' in controller
    assert 'views.setContentDescription(PHASE_ROWS[i], label[0] + ". " + detail)' in controller
    assert 'views.setContentDescription(R.id.notification_workspace_card,' in controller
    assert 'views.setContentDescription(R.id.notification_metric_elapsed, "Elapsed time")' in controller
    assert 'views.setContentDescription(ACTION_VIEWS[i], action.label + " for " + update.notificationTaskTitle())' in controller


def test_notification_contract_documents_accessibility_polish() -> None:
    contract = json.loads(read(ROOT / "android" / "devtool-live-hud" / "docs" / "notification-ui-contract.json"))
    assert contract["visual_prominence"]["progress_height_dp"] == 7
    assert contract["visual_prominence"]["dominant_state_label"] is True
    assert contract["visual_prominence"]["state_label_min_height_dp"] == 24
    assert contract["accessibility"]["root_summary_includes"] == ["state", "task", "progress", "status"]
    assert contract["accessibility"]["phase_row_content_descriptions"] is True
    assert contract["accessibility"]["workspace_card_content_description"] is True
    assert contract["accessibility"]["action_min_height_dp"] == 48
