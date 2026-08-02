from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from devtool.gradle_progress import GradleProgressTracker
from devtool.rich_progress import ThreeLineProgressState, render_three_line_progress


def test_indeterminate_three_line_layout_preserves_spinner_count_timer_and_bar() -> None:
    state = ThreeLineProgressState(
        completed=0,
        total=None,
        unit_label="tasks",
        detail=":app:compileDebugKotlin",
        started_at=100.0,
    )
    for width in (28, 36, 44, 60):
        status, bar, detail = render_three_line_progress(state, width, now=103.0)
        assert len((status, bar, detail)) == 3
        assert "0/?" in status
        assert "00:03" in status
        assert status[0] in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        assert len(bar) == width
        assert "█" in bar and "░" in bar
        assert detail.endswith("compileDebugKotlin")
        assert all(len(line) <= width for line in (status, bar, detail))


def test_determinate_explicit_task_layout_keeps_dependency_breakdown_on_narrow_terminal() -> None:
    state = ThreeLineProgressState(
        completed=5,
        total=18,
        unit_label="tasks",
        detail=":feature:dashboard:compileDebugKotlin",
        detail_prefix_full="[dependency]",
        detail_prefix_compact="[dep]",
        extra_full="2 requested + 16 deps",
        extra_compact="2 req + 16 deps",
        extra_tiny="2r+16d",
        started_at=100.0,
    )
    status, bar, detail = render_three_line_progress(state, 36, now=127.0)
    assert "5/18" in status
    assert "2r+16d" in status
    assert "00:27" in status
    assert len(bar) == 36
    assert bar.startswith("█") and "░" in bar
    assert detail.startswith("[dep] ")
    assert detail.endswith("compileDebugKotlin")
    assert all(len(line) <= 36 for line in (status, bar, detail))


def test_completed_unknown_total_renders_a_full_real_bar() -> None:
    state = ThreeLineProgressState(
        completed=3,
        total=None,
        unit_label="tasks",
        detail="complete",
        started_at=100.0,
        returncode=0,
    )
    status, bar, _ = render_three_line_progress(state, 24, now=104.0)
    assert status.startswith("✓")
    assert bar == "█" * 24


def test_explicit_gradle_tasks_are_announced_and_graph_counts_dependencies() -> None:
    tracker = GradleProgressTracker(
        ["./gradlew", ":app:compileDebugKotlin", ":app:testDebugUnitTest"],
        "/repo",
        "android-validation-explicit-tasks",
        Console(file=StringIO(), force_terminal=False),
        requested_tasks=(":app:compileDebugKotlin", ":app:testDebugUnitTest"),
    )
    event = {
        "sequence": 1,
        "kind": "task-graph",
        "total": 5,
        "tasks": [
            ":app:kspDebugKotlin",
            ":app:compileDebugKotlin",
            ":app:processDebugResources",
            ":app:testDebugUnitTest",
            ":app:testDebugUnitTestClasses",
        ],
    }
    tracker.update_from_line("DEVTOOL_EVENT " + json.dumps(event))
    snapshot = tracker.snapshot()
    assert snapshot["requested_tasks"] == [":app:compileDebugKotlin", ":app:testDebugUnitTest"]
    assert snapshot["requested_task_count"] == 2
    assert snapshot["dependency_task_count"] == 3

    tracker.update_from_line(
        "DEVTOOL_EVENT "
        + json.dumps(
            {
                "sequence": 2,
                "kind": "task-start",
                "task": ":app:kspDebugKotlin",
                "completed": 0,
                "total": 5,
            }
        )
    )
    state = tracker._progress_state()
    assert state.detail_prefix_full == "[dependency]"
    assert state.detail_prefix_compact == "[dep]"

    tracker.update_from_line(
        "DEVTOOL_EVENT "
        + json.dumps(
            {
                "sequence": 3,
                "kind": "task-start",
                "task": ":app:compileDebugKotlin",
                "completed": 1,
                "total": 5,
            }
        )
    )
    assert tracker._progress_state().detail_prefix_full == "[requested]"


def test_explicit_gradle_requested_task_preamble_is_stable() -> None:
    buffer = StringIO()
    tracker = GradleProgressTracker(
        ["./gradlew", ":app:compileDebugKotlin", ":app:testDebugUnitTest"],
        "/repo",
        "android-validation-explicit-tasks",
        Console(file=buffer, force_terminal=True, color_system=None, width=60),
        requested_tasks=(":app:compileDebugKotlin", ":app:testDebugUnitTest"),
    )
    tracker.console.print(tracker._requested_tasks_renderable())
    assert buffer.getvalue().splitlines() == [
        "Requested tasks:",
        "  :app:compileDebugKotlin",
        "  :app:testDebugUnitTest",
    ]


def test_run_specs_forwards_explicit_task_list_to_gradle_progress_tracker(tmp_path: Path) -> None:
    from devtool.models import CommandSpec, ProjectConfig
    from devtool.runners import run_specs

    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="android")
    spec = CommandSpec(
        "android-validation-explicit-tasks",
        ["./gradlew", ":app:compileDebugKotlin", ":app:testDebugUnitTest"],
        cwd=str(tmp_path),
        metadata={
            "progress_parser": "gradle",
            "explicit_validation_tasks": [":app:compileDebugKotlin", ":app:testDebugUnitTest"],
        },
    )
    with patch("devtool.runners._run_process_live", return_value=(0, "")) as run:
        results, _ = run_specs(config, [spec], verbose=False)
    assert results[0].returncode == 0
    assert run.call_args.kwargs["requested_gradle_tasks"] == [
        ":app:compileDebugKotlin",
        ":app:testDebugUnitTest",
    ]


def test_run_specs_forwards_gradle_tasks_metadata_to_progress_tracker(tmp_path: Path) -> None:
    from devtool.models import CommandSpec, ProjectConfig
    from devtool.runners import run_specs

    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="android")
    spec = CommandSpec(
        "android-validation-explicit-tasks",
        ["python3", "build-apk", str(tmp_path), "--task", ":app:testDebugUnitTest"],
        cwd=str(tmp_path),
        metadata={
            "progress_parser": "gradle",
            "gradle_tasks": [":core:diagnostics:test", ":app:testDebugUnitTest"],
        },
    )
    with patch("devtool.runners._run_process_live", return_value=(0, "")) as run:
        results, _ = run_specs(config, [spec], verbose=False)
    assert results[0].returncode == 0
    assert run.call_args.kwargs["requested_gradle_tasks"] == [
        ":core:diagnostics:test",
        ":app:testDebugUnitTest",
    ]


def test_run_specs_parses_task_args_when_metadata_is_missing(tmp_path: Path) -> None:
    from devtool.models import CommandSpec, ProjectConfig
    from devtool.runners import run_specs

    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="android")
    spec = CommandSpec(
        "android-validation-explicit-tasks",
        ["python3", "build-apk", str(tmp_path), "--task", ":app:testDebugUnitTest", "--task=:app:lintDebug"],
        cwd=str(tmp_path),
        metadata={"progress_parser": "gradle"},
    )
    with patch("devtool.runners._run_process_live", return_value=(0, "")) as run:
        results, _ = run_specs(config, [spec], verbose=False)
    assert results[0].returncode == 0
    assert run.call_args.kwargs["requested_gradle_tasks"] == [":app:testDebugUnitTest", ":app:lintDebug"]
