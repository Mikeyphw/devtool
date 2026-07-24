from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QML = ROOT / "desktop" / "devtool-control-center" / "qml"


def read(rel: str) -> str:
    return (QML / rel).read_text(encoding="utf-8")


def test_scroll_pages_bind_content_height_to_real_layouts() -> None:
    assert "ScrollBar.vertical.interactive: true" in read("components/AppScrollView.qml")
    assert "contentHeight: overviewColumn.implicitHeight" in read("pages/OverviewPage.qml")
    assert "contentHeight: historyColumn.implicitHeight" in read("pages/HistoryPage.qml")
    assert "contentHeight: settingsPageColumn.implicitHeight" in read("pages/SettingsPage.qml")
    assert "contentHeight: artifactsColumn.implicitHeight + 20" in read("pages/ArtifactsPage.qml")
    assert "contentHeight: workflowColumn.implicitHeight + 20" in read("pages/RunPage.qml")


def test_desktop_composition_labels_are_consistent_and_contextual() -> None:
    main = read("Main.qml")
    logs = read("pages/DiagnosticsPage.qml")
    overview = read("pages/OverviewPage.qml")
    settings = read("pages/SettingsPage.qml")
    assert 'text: "Devtool"' in main
    assert '"Selected: " + appController.repository.split("/").pop()' in main
    assert 'PageHeading { text: "Logs" }' in logs
    assert "Latest run from another repository" in overview
    assert "Kitty is preferred" not in settings
    assert "same SDKs as your terminal" in settings


def test_pipeline_wraps_instead_of_horizontal_clipping() -> None:
    pipeline = read("components/PipelineView.qml")
    assert "Flow {" in pipeline
    assert "function columnCount()" in pipeline
    assert "ListView.Horizontal" not in pipeline
    assert "ScrollBar.horizontal" not in pipeline


def test_run_clipboard_command_is_review_first_and_mismatch_aware() -> None:
    run = read("pages/RunPage.qml")
    assert "clipboardCandidateRepoMismatch" in run
    assert "Clipboard command targets" in run
    assert 'text: "Review command…"' in run
    assert 'text: "Run command…"' not in run
    assert 'text: "Use command values"' in run
    assert 'text: "Run reviewed command"' in run


def test_empty_states_are_action_first() -> None:
    artifacts = read("pages/ArtifactsPage.qml")
    repos = read("pages/RepositoriesPage.qml")
    logs = read("pages/DiagnosticsPage.qml")
    assert "Inspect current path" in artifacts
    assert "No saved repositories" in repos
    assert "Use current repo" in repos
    assert "No diagnostics were reported for this run" in logs
    assert "Copy summary" in logs
