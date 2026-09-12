from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop" / "gtk-control-center"
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
    workflow = read(ROOT / "devtool" / "desktop" / "run_workflow.py")
    gtk = read(ROOT / "devtool" / "desktop" / "gtk_control_center.py")

    for operation in (
        "apply-overlay",
        "apply-overlays",
        "validate",
        "restore",
        "build",
        "test",
        "lint",
        "run",
        "package",
        "clean",
        "status",
        "doctor",
    ):
        assert operation in workflow
    assert "OPERATIONS" in workflow
    assert "review_payload" in workflow
    assert "def _render_run_workflow" in gtk
    assert "Launch" in gtk


def test_desktop_test_picker_and_advanced_apply_override_use_real_commands() -> None:
    workflow = read(ROOT / "devtool" / "desktop" / "run_workflow.py")
    command_model = read(ROOT / "devtool" / "desktop" / "command_model.py")

    for token in (
        "test_profile",
        "selectors",
        "test_ids",
        "tags",
        "exclude_tags",
        "method",
        "test_tasks",
        "changed_from",
        "failed",
        "changed",
        "instrumented",
        "no_manifest_check",
    ):
        assert token in workflow or token in command_model
    assert "--no-manifest-check" in command_model
    assert "DevtoolCommandDraft" in workflow


def test_desktop_history_is_searchable_and_actionable() -> None:
    history = read(ROOT / "devtool" / "desktop" / "history_store.py")
    model = read(ROOT / "devtool" / "desktop" / "control_center_model.py")
    gtk = read(ROOT / "devtool" / "desktop" / "gtk_control_center.py")

    assert "load_history" in history
    assert "history" in model
    assert "load_history" in history
    assert "history_limit" in model
    assert "Copy command" in gtk


def test_android_run_and_history_workflows_cover_slice_two() -> None:
    activity = read(ANDROID / "MainActivity.java")
    builder = read(ANDROID / "DevtoolCommandBuilder.java")
    store = read(ANDROID / "RunStore.java")
    catalog = read(ANDROID / "TestCatalog.java")

    for token in (
        "runStage",
        "buildRunActionBar",
        "showRunStage",
        "visibleRunStepCount()",
        "visibleRunStepIndex()",
        '"Step "+(visibleRunStepIndex()+1)+" of "+visibleRunStepCount()',
    ):
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
    assert "Delete this indexed run?" in activity
    assert "Clear all indexed runs?" in activity
    assert "requestRunBuiltCommand" in activity
    assert "dangerousOperation" in activity
    assert "static synchronized boolean remove" in store
    assert "JSON_ID" in catalog and "TESTISH" in catalog


@pytest.mark.requires_tool("javac")
@pytest.mark.requires_tool("java")
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
