from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import shutil
import subprocess
import textwrap
import time

import pytest
from rich.console import Console

from devtool.diagnostics import parse_issues
from devtool.hud import HudState, event_is_error, friendly_event_line, operation_steps
from devtool.models import Issue, OperationResult
from devtool.summary import render_summary
from devtool.ui_regression import (
    canonical_label,
    filter_history_records,
    load_contract,
    normalize_terminal_snapshot,
    render_visual_svg,
    visual_scenarios,
    with_history_search_index,
)


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = ROOT / "tests" / "snapshots" / "ui"
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


def render_terminal(results: list[OperationResult]) -> str:
    stream = StringIO()
    render_summary(results, console_obj=Console(file=stream, force_terminal=False, width=88))
    return normalize_terminal_snapshot(stream.getvalue())


def terminal_scenarios() -> dict[str, list[OperationResult]]:
    return {
        "terminal-success.txt": [
            OperationResult("restore", ["dotnet", "restore"], 0, 1.2, "", []),
            OperationResult("build", ["dotnet", "build"], 0, 2.4, "", []),
            OperationResult(
                "test",
                ["dotnet", "test"],
                0,
                1.8,
                "Passed! - Failed: 0, Passed: 12, Skipped: 1, Total: 13",
                [],
            ),
            OperationResult("git-commit", ["git", "commit"], 0, 0.2, "[main abc1234] Apply UI slice", []),
        ],
        "terminal-rollback.txt": [
            OperationResult(
                "build",
                ["dotnet", "build"],
                1,
                1.4,
                "",
                [Issue("error", "build", "src/App.cs", "CS1002", "; expected", line=42)],
            ),
            OperationResult("apply-rollback", ["devtool", "rollback"], 0, 0.4, "Changes rolled back", []),
        ],
    }


def test_terminal_rich_output_matches_golden_snapshots() -> None:
    for filename, results in terminal_scenarios().items():
        expected = read(SNAPSHOTS / filename)
        assert render_terminal(results) == expected, f"stale terminal snapshot: {filename}"


def test_cross_platform_visual_contract_matches_golden_svg_snapshots() -> None:
    scenarios = visual_scenarios()
    assert {scenario.surface for scenario in scenarios} == {"desktop_hud", "desktop_page", "android"}
    assert len(scenarios) >= 6
    for scenario in scenarios:
        expected = read(SNAPSHOTS / f"{scenario.id}.svg")
        actual = render_visual_svg(scenario)
        assert actual == expected, f"stale visual snapshot: {scenario.id}"
        assert "<svg" in actual and scenario.title in actual
        assert len(actual) > 1500


def test_visual_snapshot_states_are_backed_by_real_ui_controls() -> None:
    compact_hud = read(QML / "CompactHud.qml")
    run_page = read(QML / "pages" / "RunPage.qml")
    history_page = read(QML / "pages" / "HistoryPage.qml")
    notifications = read(ANDROID / "NotificationController.java")
    overlay = read(ANDROID / "OverlayHudService.java")

    for label in ("Details", "Open run log", "Copy run summary"):
        assert label in compact_hud
    for label in ("Review", "Copy command", "Run"):
        assert label in run_page
    for label in ("Rerun", "Open log", "Open artifact"):
        assert label in history_page
    for label in ("active Devtool runs", "Open log", "Copy summary"):
        assert label in notifications
    assert "currentRuns" in overlay
    assert "Changes rolled back" in read(ANDROID / "HudUpdate.java")


def test_multi_run_pipeline_keeps_workspace_progress_independent() -> None:
    state = HudState(
        status="running",
        command="apply-overlays",
        phase_name="test[android-app]",
        repo_name="suite",
        raw={
            "pipeline": [
                {"name": "restore[desktop]", "workspace": "desktop"},
                {"name": "build[desktop]", "workspace": "desktop"},
                {"name": "test[desktop]", "workspace": "desktop"},
                {"name": "build[android-app]", "workspace": "android-app"},
                {"name": "test[android-app]", "workspace": "android-app"},
            ],
            "phase_progress": [
                {"name": "restore[desktop]", "workspace": "desktop", "status": "ok", "percent": 100},
                {"name": "build[desktop]", "workspace": "desktop", "status": "ok", "percent": 100},
                {"name": "test[desktop]", "workspace": "desktop", "status": "ok", "percent": 100},
                {"name": "build[android-app]", "workspace": "android-app", "status": "ok", "percent": 100},
                {"name": "test[android-app]", "workspace": "android-app", "status": "running", "percent": 37},
            ],
        },
    )

    steps = operation_steps(state, [])
    keys = [step.key for step in steps]
    assert keys == [
        "extract",
        "restore:desktop",
        "build:desktop",
        "test:desktop",
        "build:android-app",
        "test:android-app",
        "commit",
    ]
    desktop_test = next(step for step in steps if step.key == "test:desktop")
    android_test = next(step for step in steps if step.key == "test:android-app")
    assert (desktop_test.status, desktop_test.progress) == ("ok", 100)
    assert (android_test.status, android_test.progress) == ("running", 37)


def test_failed_apply_and_successful_rollback_have_distinct_ui_states() -> None:
    build_failure = {
        "event_type": "phase_finished",
        "phase": {"name": "build"},
        "result": {"status": "failed", "exit_code": 1},
        "message": "Build failed",
    }
    rollback_success = {
        "event_type": "phase_finished",
        "phase": {"name": "apply-rollback"},
        "result": {"status": "ok", "exit_code": 0},
        "message": "Apply Rollback completed",
    }
    state = HudState(status="failed", command="apply-overlay", phase_name="apply-rollback")
    steps = operation_steps(state, [build_failure, rollback_success])

    assert event_is_error(build_failure)
    assert not event_is_error(rollback_success)
    assert friendly_event_line(rollback_success) == "Changes rolled back"
    assert next(step for step in steps if step.key == "build").status == "failed"
    assert next(step for step in steps if step.key == "rollback").status == "ok"


def test_shared_terminology_contract_matches_python_qml_and_java() -> None:
    contract = load_contract()
    terminology = contract["terminology"]
    assert canonical_label("runners", "dotnet") == ".NET"
    assert canonical_label("statuses", "rolled_back") == "Changes rolled back"

    qml_sources = "\n".join(read(path) for path in QML.rglob("*.qml"))
    java_sources = "\n".join(read(path) for path in ANDROID.glob("*.java"))
    python_sources = "\n".join(read(path) for path in (ROOT / "devtool").glob("*.py"))
    production = "\n".join((qml_sources, java_sources, python_sources))

    for forbidden in terminology["forbidden_ui_phrases"]:
        assert forbidden not in production

    compact_hud = read(QML / "CompactHud.qml")
    hud_update = read(ANDROID / "HudUpdate.java")
    for label in terminology["runners"].values():
        assert label in compact_hud, label
        assert label in hud_update, label

    assert "Changes rolled back" in python_sources
    assert "Changes rolled back" in hud_update


def test_android_run_identity_is_stable_unique_and_java_only(tmp_path: Path) -> None:
    if shutil.which("javac") is None or shutil.which("java") is None:
        pytest.skip("JDK is not installed")

    package = tmp_path / "io" / "github" / "mikeceead" / "devtoolhud"
    package.mkdir(parents=True)
    shutil.copy2(ANDROID / "RunIdentity.java", package / "RunIdentity.java")
    (package / "RunIdentityHarness.java").write_text(
        textwrap.dedent(
            """
            package io.github.mikeceead.devtoolhud;
            public final class RunIdentityHarness {
                public static void main(String[] args) {
                    int a = RunIdentity.notificationId("run-a");
                    int b = RunIdentity.notificationId("run-b");
                    int again = RunIdentity.notificationId("run-a");
                    System.out.println(a == again);
                    System.out.println(a != b);
                    System.out.println(a >= 0x10000000 && a <= 0x1fffffff);
                }
            }
            """
        ),
        encoding="utf-8",
    )
    classes = tmp_path / "classes"
    classes.mkdir()
    subprocess.run(["javac", "-d", str(classes), *map(str, package.glob("*.java"))], check=True, capture_output=True, text=True)
    completed = subprocess.run(
        ["java", "-cp", str(classes), "io.github.mikeceead.devtoolhud.RunIdentityHarness"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.splitlines() == ["true", "true", "true"]


def test_large_history_filter_stays_within_budget_and_uses_desktop_index() -> None:
    budget = int(load_contract()["performance_budgets_ms"]["history_filter_20000"])
    records = with_history_search_index(
        {
            "run_id": f"run-{index}",
            "title": f"Apply overlay {index}",
            "repo_name": "sims3tools" if index % 2 == 0 else "xdm",
            "repo_root": "/repos/sims3tools" if index % 2 == 0 else "/repos/xdm",
            "target": f"target-{index % 17}",
            "summary": "Build failed" if index % 97 == 0 else "Completed successfully",
            "command": "devtool apply-overlay",
            "artifact_name": f"artifact-{index}.zip",
            "git_commit": f"{index:08x}",
            "status": "failed" if index % 97 == 0 else "ok",
        }
        for index in range(20_000)
    )

    started = time.perf_counter()
    result = filter_history_records(records, "build failed", "failed", "/repos/sims3tools")
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert result
    assert all(item["status"] == "failed" and item["repo_root"] == "/repos/sims3tools" for item in result)
    assert elapsed_ms < budget, f"history filter took {elapsed_ms:.1f} ms (budget {budget} ms)"

    source = read(ROOT / "desktop" / "devtool-control-center" / "src" / "HistoryModel.cpp")
    assert "m_searchText.value(index).contains(needle)" in source
    assert "rebuildSearchIndex" in source


def test_large_diagnostics_parse_stays_within_budget(tmp_path: Path) -> None:
    budget = int(load_contract()["performance_budgets_ms"]["diagnostics_parse_25000"])
    lines: list[str] = []
    for index in range(5_000):
        lines.extend(
            [
                f"/repo/src/C{index}.cs(10,2): warning CS8600: nullable warning {index}",
                f"ERROR: /repo/app/src/main/res/layout/view.xml:{index + 1}: invalid resource {index}",
                f"src/native{index}.cpp:{index + 1}:4: error: missing symbol {index}",
                f"src/module{index}.py:{index + 1}: error: incompatible value {index} [assignment]",
                f"FAILED tests/test_{index}.py::test_case - assertion failed {index}",
            ]
        )
    output = "\n".join(lines)

    started = time.perf_counter()
    issues = parse_issues(tmp_path, "test", output)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert len(issues) >= 25_000
    assert elapsed_ms < budget, f"diagnostics parse took {elapsed_ms:.1f} ms (budget {budget} ms)"


def test_android_test_catalog_handles_12000_tests_within_budget(tmp_path: Path) -> None:
    if shutil.which("javac") is None or shutil.which("java") is None:
        pytest.skip("JDK is not installed")

    budget = int(load_contract()["performance_budgets_ms"]["test_catalog_12000"])
    package = tmp_path / "io" / "github" / "mikeceead" / "devtoolhud"
    package.mkdir(parents=True)
    shutil.copy2(ANDROID / "TestCatalog.java", package / "TestCatalog.java")
    (package / "RunRecord.java").write_text(
        "package io.github.mikeceead.devtoolhud; final class RunRecord { String stdout=\"\"; String stderr=\"\"; String summary=\"\"; String detail=\"\"; }\n",
        encoding="utf-8",
    )
    (package / "CatalogPerformanceHarness.java").write_text(
        textwrap.dedent(
            """
            package io.github.mikeceead.devtoolhud;
            import java.util.List;
            public final class CatalogPerformanceHarness {
                public static void main(String[] args) {
                    RunRecord run = new RunRecord();
                    StringBuilder text = new StringBuilder();
                    for (int i = 0; i < 12000; i++) text.append("tests/test_suite.py::test_case_").append(i).append('\\n');
                    run.stdout = text.toString();
                    long started = System.nanoTime();
                    List<TestCatalog.Item> items = TestCatalog.fromRuns(List.of(run));
                    List<TestCatalog.Item> filtered = TestCatalog.filter(items, "case_11999");
                    long elapsedMs = (System.nanoTime() - started) / 1_000_000L;
                    System.out.println(items.size());
                    System.out.println(filtered.size());
                    System.out.println(elapsedMs);
                }
            }
            """
        ),
        encoding="utf-8",
    )
    classes = tmp_path / "classes"
    classes.mkdir()
    subprocess.run(["javac", "-d", str(classes), *map(str, package.glob("*.java"))], check=True, capture_output=True, text=True)
    completed = subprocess.run(
        ["java", "-cp", str(classes), "io.github.mikeceead.devtoolhud.CatalogPerformanceHarness"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    count, filtered, elapsed_ms = map(int, completed.stdout.splitlines())
    assert count == 12_000
    assert filtered == 1
    assert elapsed_ms < budget, f"test catalog took {elapsed_ms} ms (budget {budget} ms)"
    source = read(ANDROID / "TestCatalog.java")
    assert "final String searchKey" in source
    assert "scan(ids, run.stdout)" in source


def test_slice4_contract_and_snapshot_inventory_are_complete() -> None:
    contract = load_contract()
    assert contract["schema_version"] == 1
    assert set(contract["performance_budgets_ms"]) == {
        "history_filter_20000",
        "diagnostics_parse_25000",
        "test_catalog_12000",
    }
    expected = {"terminal-success.txt", "terminal-rollback.txt"}
    expected.update(f"{scenario.id}.svg" for scenario in visual_scenarios(contract))
    actual = {path.name for path in SNAPSHOTS.iterdir() if path.is_file()}
    assert actual == expected
    assert (ROOT / "tools" / "update-ui-snapshots.py").is_file()
    assert (ROOT / "docs" / "UI_REGRESSION.md").is_file()
