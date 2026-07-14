from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QML = ROOT / "desktop" / "devtool-control-center" / "qml"
ANDROID = (
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


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_shared_qml_controls_expose_keyboard_and_accessibility_contracts() -> None:
    controls = [
        "AppButton.qml",
        "AppCheckBox.qml",
        "AppTextField.qml",
        "AppTextArea.qml",
        "AppComboBox.qml",
        "AppTabButton.qml",
        "SidebarButton.qml",
    ]
    for name in controls:
        source = read(QML / "components" / name)
        assert "Accessible.name" in source, name
        assert "activeFocusOnTab: true" in source, name

    button = read(QML / "components" / "AppButton.qml")
    combo = read(QML / "components" / "AppComboBox.qml")
    assert "ToolTip.visible" in button
    assert "ToolTip.visible" in combo
    assert "Accessible.role: Accessible.Button" in button
    assert "Accessible.role: Accessible.ComboBox" in combo


def test_desktop_shell_has_narrow_mode_and_complete_keyboard_navigation() -> None:
    source = read(QML / "Main.qml")
    assert "minimumWidth: 640" in source
    assert "readonly property bool narrowMode: width < 900" in source
    assert "Layout.preferredWidth: root.narrowMode ? 72 : 218" in source
    assert 'Shortcut { sequence: "Alt+7"' in source
    assert 'Shortcut { sequence: "F9"' in source
    # ApplicationWindow is a QWindow, not a QQuickItem. Accessibility belongs
    # on the inner Item hierarchy so Qt does not reject the attachment at runtime.
    assert "Accessible.role: Accessible.Window" not in source
    assert 'Accessible.name: "Devtool Control Center"' in source
    assert "Accessible.name: \"Primary navigation\"" in source


def test_desktop_workflow_and_history_reflow_at_narrow_widths() -> None:
    run_page = read(QML / "pages" / "RunPage.qml")
    history = read(QML / "pages" / "HistoryPage.qml")
    artifacts = read(QML / "pages" / "ArtifactsPage.qml")
    diagnostics = read(QML / "pages" / "DiagnosticsPage.qml")
    repositories = read(QML / "pages" / "RepositoriesPage.qml")

    assert "readonly property bool narrowMode: width < 760" in run_page
    assert "columns:root.narrowMode?1:3" in run_page.replace(" ", "")
    assert 'Shortcut { sequence: "Ctrl+Return"' in run_page
    assert 'Shortcut { sequence: "Ctrl+F"' in run_page

    assert "columns: root.narrowMode ? 1 : 3" in history
    assert "activeFocusOnTab: true" in history
    assert "Accessible.role: Accessible.Dialog" in history

    assert "readonly property bool narrowMode: width < 780" in artifacts
    assert "columns: root.narrowMode ? 2 : 5" in artifacts
    assert "readonly property bool narrowMode: width < 760" in diagnostics
    assert "columns: root.narrowMode ? 2 : 3" in diagnostics
    assert "readonly property bool narrowMode: width < 900" in repositories
    assert "columns: root.narrowMode ? 1 : 2" in repositories


def test_compact_hud_uses_screen_bounds_safer_opacity_and_keyboard_actions() -> None:
    source = read(QML / "CompactHud.qml")
    assert "Screen.availableWidth - 32" in source
    assert "Screen.availableHeight - 32" in source
    assert "property real backgroundOpacity: theme.dark ? 0.94 : 0.97" in source
    assert "property real panelOpacity: theme.dark ? 0.88 : 0.93" in source
    assert "readonly property bool narrowHud: width < 560" in source
    assert 'Shortcut { sequence: "Ctrl+D"' in source
    assert 'Shortcut { sequence: "Escape"' in source
    assert "Accessible.name: \"Devtool live progress\"" in source


def test_android_ui_kit_enforces_touch_targets_labels_and_large_text_mode() -> None:
    source = read(ANDROID / "UiKit.java")
    assert "fontScale >= 1.3f" in source
    assert "setMinimumHeight(dp(48))" in source
    assert "setMinHeight(dp(56))" in source
    assert "View.generateViewId()" in source
    assert "label.setLabelFor(input.getId())" in source
    assert "setAccessibilityHeading(true)" in source
    assert "setContentDescription" in source


def test_android_run_history_and_talkback_behaviors_are_present() -> None:
    source = read(ANDROID / "MainActivity.java")
    assert "announceForAccessibility" in source
    assert "ui.largeText()?LinearLayout.VERTICAL:LinearLayout.HORIZONTAL" in source
    assert "check.setMinimumHeight(ui.dp(48))" in source
    assert "public boolean onKeyUp(int keyCode, KeyEvent event)" in source
    assert "event.isCtrlPressed()" in source
    assert "event.isAltPressed()" in source
    assert 'setContentDescription("Search run history by command, repository, target, or output")' in source
    assert 'setContentDescription("Generated devtool command preview")' in source


def test_android_overlay_scales_width_and_keeps_48dp_actions() -> None:
    source = read(ANDROID / "OverlayHudService.java")
    assert "int preferredWidth = largeText() ? dp(520) : dp(380)" in source
    assert "widthPixels - dp(24)" in source
    assert "button.setMinimumWidth(dp(48))" in source
    assert "button.setMinimumHeight(dp(48))" in source
    assert "int maxSegmentsPerRow = largeText() ? 2 : 4" in source
    assert 'overlayRoot.setContentDescription("Devtool floating progress HUD")' in source
