from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HUD_QML = ROOT / "desktop" / "devtool-control-center" / "qml" / "CompactHud.qml"
HUD_CPP = ROOT / "desktop" / "devtool-control-center" / "src" / "HudMain.cpp"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_desktop_hud_content_aware_autosize_is_present_and_preserves_detail() -> None:
    qml = read(HUD_QML)
    assert "property bool autoSizeEnabled: true" in qml
    assert "function desiredHudWidth()" in qml
    assert "function desiredHudHeight()" in qml
    assert "function applyContentSize()" in qml
    assert "function scheduleContentSize()" in qml
    assert "function clampToScreenBounds()" in qml
    assert "onRunChanged: scheduleContentSize()" in qml
    assert "onParserExpandedChanged: scheduleContentSize()" in qml
    assert "target: workspaceColumn" in qml
    assert "target: parserContent" in qml
    # The defect fix must not hide the rich HUD payload.
    for token in (
        "workspaceColumn", "scopedLanes()", "sharedVisibleSteps()",
        "aggregateParserMetrics()", "parserExpanded", "laneMetrics(modelData)",
        "runnerLabel()", "executionLabel()", "statusDetail()",
    ):
        assert token in qml


def test_desktop_hud_cpp_no_longer_forces_fixed_720_width() -> None:
    cpp = read(HUD_CPP)
    assert "qMax(window->width(), 720)" not in cpp
    assert "qMax(window->width(), 320)" in cpp
    assert "&QWindow::widthChanged" in cpp
    assert "&QWindow::heightChanged" in cpp
    assert "ensureHudIsVisible(window)" in cpp


def test_desktop_hud_width_autosize_does_not_feed_on_layout_width() -> None:
    qml = read(HUD_QML)
    width_function = qml.split("function desiredHudWidth()", 1)[1].split("function maxBodyHeight()", 1)[0]
    assert "automaticMaximumWidth" in qml
    assert "headerContent.implicitWidth" not in width_function
    assert "lowerContent.implicitWidth" not in width_function
    assert "function onImplicitWidthChanged() { root.scheduleContentSize() }" not in qml
