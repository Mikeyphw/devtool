from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QML = ROOT / "desktop/devtool-control-center/qml"
ANDROID = ROOT / "android/devtool-live-hud/app/src/main/java/io/github/mikeceead/devtoolhud"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_desktop_typography_tokens_are_integer_and_named() -> None:
    theme = read(QML / "components/Theme.qml")
    assert "pageTitlePointSize: 24.0 * typeScale" in theme
    assert "sectionTitlePointSize: 15.0 * typeScale" in theme
    assert "cardTitlePointSize: 14.0 * typeScale" in theme
    assert "metricValuePointSize: 20.0 * typeScale" in theme
    assert "labelPointSize: 10.0 * typeScale" in theme
    assert "captionPointSize: 9.0 * typeScale" in theme
    assert "13.5 * typeScale" not in theme
    assert "9.5 * typeScale" not in theme


def test_desktop_header_and_sidebar_are_navigation_first() -> None:
    main = read(QML / "Main.qml")
    sidebar = read(QML / "components/SidebarButton.qml")
    section = read(QML / "components/SectionHeading.qml")
    overview = read(QML / "pages/OverviewPage.qml")
    assert "implicitHeight: root.veryNarrowMode ? 48 : 52" in main
    assert "1.0 · Multi-runner" not in main
    assert 'text: "Logs"; selected: root.pageIndex === 3' in main
    assert "id: selectedRail" in sidebar
    assert "visible: root.selected" in sidebar
    assert "theme.labelPointSize" in section
    assert 'label: "Current runs"' in overview
    assert 'label: "ACTIVE RUNS"' not in overview


def test_android_headers_are_compact_and_copy_is_human() -> None:
    typography = read(ANDROID / "UiTypography.java")
    main = read(ANDROID / "MainActivity.java")
    ui = read(ANDROID / "UiKit.java")
    assert 'PAGE_TITLE(24f, "sans-serif-medium", 1.20f)' in typography
    assert 'if (sp >= 24) return Role.PAGE_TITLE;' in typography
    assert 'header.setPadding(ui.contentHorizontalPadding(), ui.dp(ui.wideLayout() ? 18 : 12)' in main
    assert 'if (ui.wideLayout()) {' in main
    assert 'header.addView(ui.text("DEVTOOL", UiTypography.Role.LABEL, ui.palette.cyan));' in main
    assert '"Overview", "Run", "Logs", "Repos", "Settings"' in main
    assert '"Ready to run"' in main
    assert '"Structured logs"' not in main
    assert '"Search run output, problems and metadata."' in main
    assert 'withAlpha(palette.green, 36)' in ui
