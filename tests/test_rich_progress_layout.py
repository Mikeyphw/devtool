from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from devtool.gradle_progress import GradleProgressTracker
from devtool.generic_progress import GenericProgressTracker
from devtool.rich_progress import (
    ThreeLineProgressBody,
    ThreeLineProgressState,
    compact_detail_text,
    compact_display_target,
    completion_line,
    render_three_line_progress,
)


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
        assert "Working" in status
        assert "0/?" not in status
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
        "Requested: :app:compileDebugKotlin (+1)",
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


def test_percent_mode_uses_human_percent_instead_of_fraction_label() -> None:
    state = ThreeLineProgressState(
        completed=70,
        total=100,
        unit_label="percent",
        unit_short="%",
        detail="tests/test_validation_sessions.py",
        started_at=100.0,
    )
    status, _bar, _detail = render_three_line_progress(state, 44, now=110.0)
    assert "70%" in status
    assert "70/100" not in status
    assert "percent" not in status


def test_live_body_separates_overall_pipeline_progress_from_current_phase_progress() -> None:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, width=52)
    state = ThreeLineProgressState(
        completed=50,
        total=100,
        unit_label="percent",
        detail="tests/test_validation_sessions.py",
        started_at=100.0,
        phase_index=4,
        phase_total=8,
        phase_label="test",
        tests_passed=923,
        tests_total=1327,
    )
    console.print(ThreeLineProgressBody(lambda: state))
    output = buffer.getvalue()
    assert "Step 4 of 8" in output
    assert "44% overall" in output
    assert "50%" in output
    assert "923 passed" in output
    assert "404 remaining" in output


def test_generic_pytest_progress_tracks_real_test_counts() -> None:
    tracker = GenericProgressTracker(
        ["pytest"],
        "/repo",
        "test",
        Console(file=StringIO(), force_terminal=False),
    )
    tracker.update_from_line("collected 1327 items")
    tracker.update_from_line("tests/test_example.py ....sF [  0%]")
    snapshot = tracker.snapshot()
    assert snapshot["tests_total"] == 1327
    assert snapshot["tests_passed"] == 4
    assert snapshot["tests_skipped"] == 1
    assert snapshot["tests_failed"] == 1
    assert snapshot["completed"] == 6
    assert snapshot["unit_label"] == "tests"



def test_live_detail_compacts_absolute_termux_paths_relative_to_repo() -> None:
    detail = compact_detail_text(
        "/data/data/com.termux/files/home/Code/devtool/.devtool/reports/python/environment.json",
        cwd="/data/data/com.termux/files/home/Code/devtool",
    )
    assert "/data/data/com.termux" not in detail
    assert detail == "…/reports/python/environment.json"


def test_live_detail_path_compaction_does_not_mangle_urls() -> None:
    detail = compact_detail_text(
        "Downloading https://example.com/releases/tool.zip into /data/data/com.termux/files/home/Code/devtool/.devtool/cache/tool.zip",
        cwd="/data/data/com.termux/files/home/Code/devtool",
    )
    assert "https://example.com/releases/tool.zip" in detail
    assert "/data/data/com.termux" not in detail
    assert detail.endswith(".devtool/cache/tool.zip")


def test_very_narrow_overall_header_preserves_phase_position_and_percent() -> None:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, width=40)
    state = ThreeLineProgressState(
        completed=0,
        total=None,
        detail="Starting",
        phase_index=3,
        phase_total=8,
        phase_label="android-validation-explicit-tasks",
    )
    console.print(ThreeLineProgressBody(lambda: state))
    first = buffer.getvalue().splitlines()[0]
    assert "3/8" in first
    assert "25% overall" in first
    assert "Android tasks" in first


def test_generic_panel_title_degrades_cleanly_on_40_column_phone_terminal() -> None:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, width=40)
    tracker = GenericProgressTracker(
        ["python"],
        "/data/data/com.termux/files/home/Code/devtool",
        "android-validation-explicit-tasks",
        console,
        phase_index=3,
        phase_total=8,
        display_target="/data/data/com.termux/files/home/Code/devtool",
    )
    console.print(tracker._renderable())
    output = buffer.getvalue()
    assert "Android tasks · devtool" in output.splitlines()[0]
    assert "explicit task─" not in output

def test_mobile_target_compaction_hides_termux_prefix_but_keeps_identity() -> None:
    compact = compact_display_target(
        "/data/data/com.termux/files/home/Code/devtool/.devtool/reports/pytest.xml"
    )
    assert "/data/data/com.termux" not in compact
    assert compact.endswith("reports/pytest.xml")


def test_completion_row_is_compact_and_keeps_test_outcome() -> None:
    text = completion_line(
        command="test",
        returncode=0,
        started_at=100.0,
        now=105.0,
        snapshot={
            "tests_passed": 1327,
            "tests_failed": 0,
            "tests_skipped": 0,
            "tests_total": 1327,
        },
    )
    assert text.plain == "✓ Test · 1327 passed · 00:05"


def test_gradle_requested_task_preamble_keeps_full_list_on_wide_terminal() -> None:
    buffer = StringIO()
    tracker = GradleProgressTracker(
        ["./gradlew", ":app:compileDebugKotlin", ":app:testDebugUnitTest"],
        "/repo",
        "android-validation-explicit-tasks",
        Console(file=buffer, force_terminal=True, color_system=None, width=120),
        requested_tasks=(":app:compileDebugKotlin", ":app:testDebugUnitTest"),
    )
    tracker.console.print(tracker._requested_tasks_renderable())
    assert buffer.getvalue().splitlines() == [
        "Requested tasks:",
        "  :app:compileDebugKotlin",
        "  :app:testDebugUnitTest",
    ]


def test_run_specs_forwards_overall_validation_phase_metadata(tmp_path: Path) -> None:
    from devtool.models import CommandSpec, ProjectConfig
    from devtool.runners import run_specs

    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="python")
    spec = CommandSpec(
        "test",
        ["python", "-m", "pytest"],
        cwd=str(tmp_path),
        metadata={
            "validation_phase_index": 4,
            "validation_phase_total": 8,
            "validation_scope": "repository",
            "project_root": str(tmp_path),
        },
    )
    with patch("devtool.runners._run_process_live", return_value=(0, "")) as run:
        results, _ = run_specs(config, [spec], verbose=False)
    assert results[0].returncode == 0
    assert run.call_args.kwargs["phase_index"] == 4
    assert run.call_args.kwargs["phase_total"] == 8
    assert run.call_args.kwargs["display_target"] == str(tmp_path)


def test_generic_tracker_is_renderer_neutral_and_keeps_semantic_snapshot() -> None:
    buffer = StringIO()
    tracker = GenericProgressTracker(
        ["pytest"],
        "/repo",
        "test",
        Console(file=buffer, force_terminal=True, color_system=None, width=60),
        phase_index=4,
        phase_total=8,
    )
    updates: list[dict] = []
    tracker.update_callback = updates.append
    with tracker:
        tracker.update_from_line("collected 10 items")
        tracker.update_from_line(".......... [100%]")
        tracker.finish(0)
    assert not hasattr(tracker, "live")
    assert buffer.getvalue() == ""
    assert updates
    assert tracker.snapshot()["returncode"] == 0
    assert tracker.snapshot()["tests_total"] == 10
