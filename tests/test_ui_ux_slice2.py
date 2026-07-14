from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop" / "devtool-control-center"
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


def test_desktop_run_workflow_is_staged_sticky_and_complete() -> None:
    qml = read(DESKTOP / "qml" / "pages" / "RunPage.qml")
    controller = read(DESKTOP / "src" / "AppController.cpp")

    assert "property int currentStage" in qml
    assert 'readonly property var stages: ["Project", "Operation", "Tests", "Review"]' in qml
    assert "id:stickyBar" in qml
    assert "anchors.bottom:stickyBar.top" in qml
    for operation in (
        "Apply overlay",
        "Apply bundle",
        "Validate",
        "Restore",
        "Build",
        "Test",
        "Lint",
        "Run project",
        "Package",
        "Clean",
        "Inspect",
        "Commit",
        "Status",
        "Doctor",
    ):
        assert f'"{operation}"' in qml
    for command in ("apply-overlays", "lint", "run", "package", "clean", "inspect", "commit"):
        assert f'QStringLiteral("{command}")' in controller


def test_desktop_test_picker_and_advanced_apply_override_use_real_commands() -> None:
    qml = read(DESKTOP / "qml" / "pages" / "RunPage.qml")
    header = read(DESKTOP / "src" / "AppController.h")
    source = read(DESKTOP / "src" / "AppController.cpp")

    assert "discoverTests(" in header
    assert "AppController::discoverTests" in source
    assert 'QStringLiteral("test") << QStringLiteral("--list-tests")' in source
    assert "collectTestNames" in source
    assert "selectedTestIds" not in qml  # desktop selection is kept in the QML map
    assert "selectedTests" in qml
    assert "Discover tests" in qml
    assert "filteredTests()" in qml
    assert "Advanced: apply without manifest check" in qml
    assert "--no-manifest-check" in qml
    assert "if (noManifestCheck)" in source
    assert "dangerConfirmation" in qml


def test_desktop_history_is_searchable_and_actionable() -> None:
    qml = read(DESKTOP / "qml" / "pages" / "HistoryPage.qml")
    header = read(DESKTOP / "src" / "HistoryModel.h")
    source = read(DESKTOP / "src" / "HistoryModel.cpp")
    controller = read(DESKTOP / "src" / "AppController.cpp")

    for method in ("search", "repositories", "getByRunId", "remove"):
        assert method in header
        assert f"HistoryModel::{method}" in source
    for action in ("Copy command", "Rerun", "Open log", "Open artifact"):
        assert action in qml
    assert "runHistoricalCommand" in controller
    assert "Clear all run history?" in qml
    assert "clearConfirmation.open()" in qml


def test_android_run_and_history_workflows_cover_slice_two() -> None:
    activity = read(ANDROID / "MainActivity.java")
    builder = read(ANDROID / "DevtoolCommandBuilder.java")
    store = read(ANDROID / "RunStore.java")
    catalog = read(ANDROID / "TestCatalog.java")

    for token in ("runStage", "buildRunActionBar", "showRunStage", '"Step "+(runStage+1)+" of 4'):
        assert token in activity
    for operation in ('"run"', '"package"', '"clean"', '"inspect"'):
        assert operation in builder
    assert 'parts.add(shellLiteral(message));' in builder
    assert 'parts.add("-m")' not in builder
    assert "discoverTestsOnAndroid" in activity
    assert "TestCatalog.fromRuns" in activity
    assert "populateTestCatalog" in activity
    assert "Search and filters" in activity
    assert "populateHistoryList" in activity
    assert "Run again" in activity
    assert "Delete this run?" in activity
    assert "Clear all run history?" in activity
    assert "requestRunBuiltCommand" in activity
    assert "dangerousOperation" in activity
    assert "static synchronized boolean remove" in store
    assert "JSON_ID" in catalog and "TESTISH" in catalog


def test_android_test_catalog_parses_and_filters_framework_ids(tmp_path: Path) -> None:
    if shutil.which("javac") is None or shutil.which("java") is None:
        pytest.skip("JDK is not installed")

    package = tmp_path / "io" / "github" / "mikeceead" / "devtoolhud"
    package.mkdir(parents=True)
    shutil.copy2(ANDROID / "TestCatalog.java", package / "TestCatalog.java")
    (package / "RunRecord.java").write_text(
        textwrap.dedent(
            """
            package io.github.mikeceead.devtoolhud;
            final class RunRecord {
                String stdout = "";
                String stderr = "";
                String summary = "";
                String detail = "";
            }
            """
        ),
        encoding="utf-8",
    )
    (package / "CatalogHarness.java").write_text(
        textwrap.dedent(
            """
            package io.github.mikeceead.devtoolhud;
            import java.util.List;
            public final class CatalogHarness {
                public static void main(String[] args) {
                    RunRecord run = new RunRecord();
                    run.stdout = "tests/test_cli.py::test_apply\\n"
                            + "{\\\"nodeid\\\":\\\"tests/test_ui.py::test_history\\\"}";
                    List<TestCatalog.Item> items = TestCatalog.fromRuns(List.of(run));
                    System.out.println(items.size());
                    System.out.println(TestCatalog.filter(items, "history").get(0).id);
                }
            }
            """
        ),
        encoding="utf-8",
    )
    classes = tmp_path / "classes"
    classes.mkdir()
    subprocess.run(
        ["javac", "-d", str(classes), *map(str, package.glob("*.java"))],
        check=True,
        text=True,
        capture_output=True,
    )
    completed = subprocess.run(
        ["java", "-cp", str(classes), "io.github.mikeceead.devtoolhud.CatalogHarness"],
        check=True,
        text=True,
        capture_output=True,
    )
    lines = completed.stdout.splitlines()
    assert lines == ["2", "tests/test_ui.py::test_history"]
