from __future__ import annotations

from pathlib import Path
import json
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DESKTOP_QML = ROOT / "desktop" / "devtool-control-center" / "qml"
ANDROID_APP = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main"
ANDROID_JAVA = ANDROID_APP / "java" / "io" / "github" / "mikeceead" / "devtoolhud"
ANDROID_RES = ANDROID_APP / "res"
ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def nodes_by_id(path: Path) -> dict[str, ET.Element]:
    root = ET.parse(path).getroot()
    result: dict[str, ET.Element] = {}
    for node in root.iter():
        value = node.attrib.get(ANDROID_NS + "id", "")
        if value.startswith("@+id/"):
            result[value.removeprefix("@+id/")] = node
    return result


def test_final_docs_capture_all_accepted_ui_surfaces() -> None:
    contract = read(ROOT / "docs" / "UI_UX_CONTRACT.md")
    phase = read(ROOT / "PHASE_13_UI_CONTRACT_FINALIZATION.md")
    for token in (
        "Desktop Control Center",
        "Desktop HUD",
        "Android app",
        "Android floating HUD",
        "Android notification",
        "Content-aware auto-size",
        "status lead/detail below the progress rail",
        "Run progressive disclosure",
        "Overview action-first layout",
    ):
        assert token in contract or token.lower() in contract.lower()
    for phase_name in (
        "Phase 8",
        "Phase 9",
        "Phase 10",
        "Phase 11",
        "Phase 12",
    ):
        assert phase_name in phase


def test_desktop_hud_final_contract_keeps_detail_and_autosize() -> None:
    qml = read(DESKTOP_QML / "CompactHud.qml")
    for token in (
        "property bool autoSizeEnabled: true",
        "function desiredHudWidth()",
        "function desiredHudHeight()",
        "function applyContentSize()",
        "function scheduleContentSize()",
        "function clampToScreenBounds()",
        "onParserExpandedChanged: scheduleContentSize()",
        "target: workspaceColumn",
        "target: parserContent",
        'text: "Current run"',
        'text: "Workspace lanes"',
        'text: "Diagnostics parser"',
        "sharedVisibleSteps()",
        "laneMetrics(modelData)",
        "aggregateParserMetrics()",
        "runnerLabel()",
        "executionLabel()",
        "statusDetail()",
    ):
        assert token in qml
    assert "qMax(window->width(), 720)" not in read(ROOT / "desktop" / "devtool-control-center" / "src" / "HudMain.cpp")


def test_android_hud_final_contract_persists_position_without_losing_detail() -> None:
    source = read(ANDROID_JAVA / "OverlayHudService.java")
    for token in (
        'private static final String KEY_X = "x"',
        'private static final String KEY_Y = "y"',
        "saveHudPosition()",
        "clampAndUpdateOverlayPosition()",
        "onConfigurationChanged(Configuration newConfig)",
        "maxOverlayX(int width)",
        "maxOverlayY(int height)",
        'primaryRun ? "Current run" : "Recent run"',
        "update.notificationStatusLead()",
        "update.notificationStatusDetail()",
        'label("Pipeline"',
        'label("Metrics"',
        "update.runnerLabel() + \" · \" + update.environmentLabel()",
        "for (HudUpdate.Step step : update.steps)",
        "Commit ",
    ):
        assert token in source
    assert "windowParams.x = dp(12)" not in source
    assert "windowParams.y = dp(72)" not in source


def test_notification_final_contract_matches_layouts_controller_and_json() -> None:
    controller = read(ANDROID_JAVA / "NotificationController.java")
    update = read(ANDROID_JAVA / "HudUpdate.java")
    contract = json.loads(read(ROOT / "android" / "devtool-live-hud" / "docs" / "notification-ui-contract.json"))
    assert "String notificationStatusLead()" in update
    assert "String notificationStatusDetail()" in update
    assert "String notificationStatusLine()" in update
    assert 'putString("devtool.status_lead", update.notificationStatusLead())' in controller
    assert 'putString("devtool.status_detail", update.notificationStatusDetail())' in controller
    assert 'views.setContentDescription(R.id.notification_progress, "Progress " + progressLabel)' in controller
    assert 'views.setContentDescription(R.id.notification_state_label, "State " + stateLabel)' in controller
    assert 'views.setContentDescription(R.id.notification_status_lead, statusLead)' in controller
    assert 'views.setContentDescription(R.id.notification_status_detail, statusDetail)' in controller
    assert 'views.setContentDescription(ACTION_VIEWS[i], action.label + " for " + update.notificationTaskTitle())' in controller
    layouts = (
        ANDROID_RES / "layout" / "notification_devtool_compact.xml",
        ANDROID_RES / "layout" / "notification_devtool_expanded.xml",
        ANDROID_RES / "layout" / "notification_devtool_expanded_large_text.xml",
    )
    for layout in layouts:
        xml = read(layout)
        nodes = nodes_by_id(layout)
        assert xml.index("@+id/notification_progress") < xml.index("@+id/notification_status_lead")
        assert xml.index("@+id/notification_status_lead") < xml.index("@+id/notification_status_detail")
        assert nodes["notification_progress"].attrib[ANDROID_NS + "layout_height"] == "7dp"
        assert nodes["notification_state_label"].attrib["style"] == "@style/TextAppearance.Devtool.Notification.State"
        assert nodes["notification_state_label"].attrib[ANDROID_NS + "minHeight"] == "24dp"
    for layout in layouts[1:]:
        nodes = nodes_by_id(layout)
        for action in ("notification_action_1", "notification_action_2", "notification_action_3"):
            assert nodes[action].attrib[ANDROID_NS + "minHeight"] == "48dp"
    assert contract["parity"]["status_lines_below_progress"] is True
    assert contract["visual_prominence"]["progress_height_dp"] == 7
    assert contract["visual_prominence"]["dominant_state_label"] is True
    assert contract["accessibility"]["action_min_height_dp"] == 48


def test_typography_navigation_overview_run_flow_final_contract() -> None:
    main_qml = read(DESKTOP_QML / "Main.qml")
    overview_qml = read(DESKTOP_QML / "pages" / "OverviewPage.qml")
    run_qml = read(DESKTOP_QML / "pages" / "RunPage.qml")
    android_main = read(ANDROID_JAVA / "MainActivity.java")
    typography = read(ANDROID_JAVA / "UiTypography.java")
    assert 'text: "Logs"; selected: root.pageIndex === 3' in main_qml
    assert 'PAGE_TITLE(24f, "sans-serif-medium", 1.20f)' in typography
    assert '"Overview", "Run", "Logs", "Repos", "Settings"' in android_main
    assert 'ui.sectionTitle("Next action")' in android_main
    assert 'ui.sectionTitle("Latest run")' in android_main
    assert 'ui.sectionTitle("Readiness")' in android_main
    assert android_main.index('ui.sectionTitle("Next action")') < android_main.index('ui.sectionTitle("Latest run")')
    assert 'private LinearLayout progressiveSection' in android_main
    assert 'visibleRunStepCount()' in android_main
    assert 'text: "Next action"' in overview_qml
    assert 'text: "Snapshot"' in overview_qml
    assert 'Flow {' in overview_qml
    assert 'GridLayout {' in overview_qml
    assert 'readonly property var compactStages: ["Project", "Operation", "Review"]' in run_qml
    assert 'Show advanced execution' in run_qml
    assert 'Danger zone' in run_qml


def test_release_and_docs_register_final_ui_contract_gate() -> None:
    matrix = json.loads(read(ROOT / "release" / "qualification-matrix.json"))
    assert any(g["id"] == "ui-ux-contract" and g["required"] is True for g in matrix["gates"])
    ui_accessibility = read(ROOT / "docs" / "UI_ACCESSIBILITY.md")
    typography = read(ROOT / "docs" / "TYPOGRAPHY_SYSTEM.md")
    regression = read(ROOT / "docs" / "UI_REGRESSION.md")
    changelog = read(ROOT / "CHANGELOG.md")
    assert "Overview, Run, Artifacts, Logs, History" in ui_accessibility
    assert "Desktop HUD auto-size grows and shrinks" in ui_accessibility
    assert "Page title | 24sp | 1.20" in typography
    assert "UI/UX roadmap finalization" in typography
    assert "UI/UX roadmap final contract" in regression
    assert "Finalized the UI/UX contract" in changelog
