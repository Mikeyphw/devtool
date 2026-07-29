from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main"
JAVA = APP / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_backup_rules_cover_legacy_and_android_12_data_extraction() -> None:
    manifest = read(APP / "AndroidManifest.xml")
    assert 'android:allowBackup="false"' in manifest
    assert 'android:dataExtractionRules="@xml/data_extraction_rules"' in manifest
    assert 'android:fullBackupContent="@xml/backup_rules"' in manifest
    backup = read(APP / "res" / "xml" / "backup_rules.xml")
    extraction = read(APP / "res" / "xml" / "data_extraction_rules.xml")
    for domain in ('domain="file"', 'domain="database"', 'domain="sharedpref"', 'domain="external"'):
        assert domain in backup
        assert domain in extraction


def test_launcher_monochrome_icon_and_unused_drawables_are_cleaned() -> None:
    launcher = read(APP / "res" / "mipmap" / "ic_launcher.xml")
    launcher_round = read(APP / "res" / "mipmap" / "ic_launcher_round.xml")
    assert '<monochrome android:drawable="@drawable/ic_launcher_monochrome"' in launcher
    assert '<monochrome android:drawable="@drawable/ic_launcher_monochrome"' in launcher_round
    monochrome = read(APP / "res" / "drawable" / "ic_launcher_monochrome.xml")
    assert '#FFFFFFFF' in monochrome
    for unused in ('ic_launcher_legacy.xml', 'ic_permissions.xml', 'ic_start.xml'):
        assert not (APP / "res" / "drawable" / unused).exists()


def test_accessibility_drag_header_overrides_perform_click() -> None:
    overlay = read(JAVA / "OverlayHudService.java")
    assert 'new DraggableHudHeader(this)' in overlay
    assert 'final class DraggableHudHeader extends LinearLayout' in overlay
    assert 'public boolean performClick()' in overlay
    assert 'super.performClick();' in overlay


def test_set_text_i18n_warning_call_sites_use_resources_or_dynamic_values() -> None:
    main = read(JAVA / "MainActivity.java")
    picker = read(JAVA / "RepositoryPickerActivity.java")
    ui = read(JAVA / "UiKit.java")
    validation = read(JAVA / "ValidationWorkbenchView.java")
    overlay = read(JAVA / "OverlayHudService.java")
    strings = read(APP / "res" / "values" / "strings.xml")
    for resource in (
        "configuration_issue_format",
        "runner_status_format",
        "repository_picker_reading_format",
        "repository_picker_directory_count",
        "two_line_title_detail",
        "validation_phase_row_format",
        "validation_phase_required_dependency",
        "validation_phase_optional",
        "overlay_hud_ready",
    ):
        assert resource in strings
    assert 'commandPreview.setText("Configuration issue: " +' not in main
    assert 'statusView.setText(\n                "Notifications' not in main
    assert 'state.setText("Reading " +' not in picker
    assert 'state.setText(directories.size() + " directories")' not in picker
    assert 'button.setText(title + "\\n" + detail)' not in ui
    assert 'row.setText(marker + "  " + phase.label + "\\n" + detail)' not in validation
    assert 'check.setText(phase.label + suffix)' not in validation
    assert 'headerCount.setText("Ready")' not in overlay


def test_unused_palette_resources_and_reflection_warning_are_cleaned() -> None:
    colors = read(APP / "res" / "values" / "colors.xml")
    styles = read(APP / "res" / "values" / "styles.xml")
    palette = read(JAVA / "AppPalette.java")
    for name in (
        "devtool_surface",
        "devtool_text",
        "devtool_muted",
        "devtool_cyan",
        "devtool_yellow",
        "devtool_red",
    ):
        assert name not in colors
    for style in (
        "TextAppearance.Devtool.PageTitle",
        "TextAppearance.Devtool.CardTitle",
        "TextAppearance.Devtool.Body\"",
        "TextAppearance.Devtool.BodySmall",
        "TextAppearance.Devtool.Label",
        "TextAppearance.Devtool.Caption",
    ):
        assert style not in styles
    assert '@SuppressLint("DiscouragedApi")' in palette
