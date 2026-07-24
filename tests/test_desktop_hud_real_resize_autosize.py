from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUD_QML = ROOT / "desktop" / "devtool-control-center" / "qml" / "CompactHud.qml"


def read_hud() -> str:
    return HUD_QML.read_text(encoding="utf-8")


def test_hud_uses_manual_resize_dragging_not_only_system_resize() -> None:
    qml = read_hud()
    assert "function beginManualResize(screenX, screenY, rightEdge, bottomEdge)" in qml
    assert "function updateManualResize(screenX, screenY)" in qml
    assert "manualResizeStartWidth + screenX - manualResizeStartScreenX" in qml
    assert "manualResizeStartHeight + screenY - manualResizeStartScreenY" in qml
    assert "function resetManualResizeToAuto()" in qml
    assert "id: resizeGrip" in qml
    assert "id: resizeRightEdge" in qml
    assert "id: resizeBottomEdge" in qml
    assert "root.startSystemResize" not in qml


def test_hud_autosize_uses_real_content_and_scrolls_when_capped() -> None:
    qml = read_hud()
    assert "function preferredBodyHeight()" in qml
    assert "lowerContent.implicitHeight" in qml
    assert "contentHeight: lowerContent.implicitHeight" in qml
    assert "contentWidth: availableWidth" in qml
    assert "ScrollBar.vertical.interactive: true" in qml
    assert "Layout.preferredHeight: root.preferredBodyHeight()" in qml
    assert "target: headerContent" in qml
    assert "target: lowerContent" in qml
    assert "target: workspaceColumn" in qml
    assert "target: parserContent" in qml


def test_hud_manual_size_pauses_autosize_until_double_click_reset() -> None:
    qml = read_hud()
    assert "autoSizeEnabled = false" in qml
    assert "userSizedHud = true" in qml
    assert "if (!autoSizeEnabled || userSizedHud) return" in qml
    assert "autoSizeEnabled = true" in qml
    assert "onDoubleClicked: root.resetManualResizeToAuto()" in qml
