from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android/devtool-live-hud/app/src/main"
QML = ROOT / "desktop/devtool-control-center/qml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def relative_luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    channels = [int(value[index:index + 2], 16) / 255.0 for index in (0, 2, 4)]
    linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: str, second: str) -> float:
    a, b = relative_luminance(first), relative_luminance(second)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def test_notification_uses_readable_semantic_sizes_and_flexible_rows() -> None:
    styles = read(ANDROID / "res/values/styles.xml")
    sizes = [int(value) for value in re.findall(r"<item name=\"android:textSize\">(\d+)sp</item>", styles)]
    notification_sizes = [
        int(value)
        for value in re.findall(
            r"TextAppearance\.Devtool\.Notification[\s\S]*?<item name=\"android:textSize\">(\d+)sp</item>",
            styles,
        )
    ]
    assert sizes
    assert notification_sizes
    assert min(notification_sizes) >= 10

    for name in (
        "notification_devtool_compact.xml",
        "notification_devtool_expanded.xml",
        "notification_devtool_expanded_large_text.xml",
    ):
        source = read(ANDROID / "res/layout" / name)
        assert not re.search(r'android:textSize="(?:[1-9])sp"', source)
        assert not re.search(r'<TextView[^>]+android:layout_height="(?:19|21|28|34|40)dp"', source)
        ET.parse(ANDROID / "res/layout" / name)

    expanded = read(ANDROID / "res/layout/notification_devtool_expanded.xml")
    assert 'android:minHeight="48dp"' in expanded
    assert 'style="@style/TextAppearance.Devtool.Notification.Action"' in expanded


def test_notification_contrast_and_large_text_layout_are_enforced() -> None:
    colors = read(ANDROID / "res/values/colors.xml")
    parsed = dict(re.findall(r'<color name="([^"]+)">(#[0-9A-Fa-f]{6})</color>', colors))
    assert contrast(parsed["notification_soft"], parsed["notification_card"]) >= 4.5
    assert contrast(parsed["notification_muted"], parsed["notification_card"]) >= 4.5

    controller = read(ANDROID / "java/io/github/mikeceead/devtoolhud/NotificationController.java")
    assert "fontScale >= 1.3f" in controller
    assert "notification_devtool_expanded_large_text" in controller
    assert "bindPhases(views, update, 2)" in controller
    assert "notification_metrics_summary" in controller


def test_android_typography_is_semantic_and_role_specific() -> None:
    typography = read(ANDROID / "java/io/github/mikeceead/devtoolhud/UiTypography.java")
    for role, size in {
        "PAGE_TITLE": 28,
        "LARGE_VALUE": 20,
        "CARD_TITLE": 16,
        "BODY": 14,
        "BODY_SMALL": 13,
        "BUTTON": 14,
        "LABEL": 12,
        "CAPTION": 11,
        "CODE": 13,
    }.items():
        assert re.search(rf"{role}\({size}(?:\.0)?f,", typography)
    assert "BODY(14f, \"sans-serif\", 1.40f)" in typography
    assert "BODY_SMALL(13f, \"sans-serif\", 1.36f)" in typography
    assert "CAPTION(11f, \"sans-serif\", 1.30f)" in typography

    java_root = ANDROID / "java/io/github/mikeceead/devtoolhud"
    offenders = []
    for path in java_root.glob("*.java"):
        if path.name == "UiTypography.java":
            continue
        if "setTextSize(" in read(path):
            offenders.append(path.name)
    assert offenders == []

    ui_kit = read(java_root / "UiKit.java")
    assert "UiTypography.Role" in ui_kit
    assert "setLetterSpacing(0.02f)" in ui_kit
    assert "setAllCaps(true)" not in ui_kit


def test_desktop_scale_follows_system_font_and_has_user_presets() -> None:
    theme = read(QML / "components/Theme.qml")
    assert "Qt.application.font.pointSize" in theme
    assert "platformBodyPointSize" in theme
    assert "typeScale" in theme
    assert "captionPointSize: 9.5 * typeScale" in theme
    assert "bodyLineHeight: 1.42" in theme
    assert "titleLineHeight: 1.24" in theme
    assert "monospaceFamily" in theme and "uiMonospaceFont" in theme
    assert "JetBrainsMono Nerd Font" not in theme

    settings = read(QML / "pages/SettingsPage.qml")
    for preset in ('"100%"', '"125%"', '"150%"', '"200%"'):
        assert preset in settings
    controller_h = read(ROOT / "desktop/devtool-control-center/src/AppController.h")
    controller_cpp = read(ROOT / "desktop/devtool-control-center/src/AppController.cpp")
    assert "Q_PROPERTY(double typographyScale" in controller_h
    assert 'appearance/typographyScale' in controller_cpp
    assert "QFontDatabase::GeneralFont" in controller_cpp


def test_operational_text_never_derives_an_eight_point_size() -> None:
    all_qml = "\n".join(read(path) for path in QML.rglob("*.qml"))
    assert "captionPointSize - 1" not in all_qml
    assert "smallPointSize - 1" not in all_qml
    assert not re.search(r"font\.pointSize\s*:\s*[1-8](?:\D|$)", all_qml)


def test_interface_icons_are_vectors_not_font_glyphs() -> None:
    main = read(QML / "Main.qml")
    sidebar = read(QML / "components/SidebarButton.qml")
    compact = read(QML / "CompactHud.qml")
    pipeline = read(QML / "components/PipelineView.qml")
    android_main = read(ANDROID / "java/io/github/mikeceead/devtoolhud/MainActivity.java")
    notification = read(ANDROID / "java/io/github/mikeceead/devtoolhud/NotificationController.java")

    assert "iconSource:" in main
    assert "Image {" in sidebar
    assert "StatusMark" in compact
    assert "StatusMark" in pipeline
    assert "R.drawable.ic_nav_overview" in android_main
    assert "setImageViewResource" in notification

    forbidden = ("⌂", "▶", "◇", "◷", "⌘", "⚙", "⧉", "⌄", "⌃", "◆")
    for source in (main, sidebar, compact, pipeline, android_main, notification):
        assert not any(glyph in source for glyph in forbidden)


def test_desktop_uses_system_ui_font_and_native_fixed_font_fallback() -> None:
    main_cpp = read(ROOT / "desktop/devtool-control-center/src/main.cpp")
    hud_cpp = read(ROOT / "desktop/devtool-control-center/src/HudMain.cpp")
    assert "QFontDatabase::GeneralFont" in main_cpp
    assert "QFontDatabase::GeneralFont" in hud_cpp
    assert "QFontDatabase::FixedFont" in main_cpp
    assert "QFontDatabase::FixedFont" in hud_cpp
    assert "app.setFont(QFont(preferredNerdFont()))" not in main_cpp
    assert "app.setFont(QFont(preferredNerdFont()))" not in hud_cpp
