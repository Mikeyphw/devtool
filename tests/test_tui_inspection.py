from __future__ import annotations

from io import StringIO
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from devtool.dotnet_progress import DotnetProgressTracker
from devtool.generic_progress import GenericProgressTracker
from devtool.gradle_progress import GradleProgressTracker
from devtool.npm_progress import NpmProgressTracker
from devtool.runners import _run_process_live
from devtool.rich_progress import (
    ThreeLineProgressBody,
    ThreeLineProgressState,
    TuiDisplayMode,
    TuiInspectionController,
)


def _console(*, width: int = 72, height: int = 24) -> tuple[Console, StringIO]:
    output = StringIO()
    return (
        Console(
            file=output,
            force_terminal=True,
            color_system=None,
            width=width,
            height=height,
        ),
        output,
    )


def test_shortcut_state_machine_merges_verbose_modal_and_direct_modes() -> None:
    console, _ = _console()
    controller = TuiInspectionController(console, force_interactive=True)

    assert controller.mode == TuiDisplayMode.COMPACT
    assert controller.handle_key("v") is True
    assert controller.mode == TuiDisplayMode.VERBOSE
    assert controller.handle_key("v") is True
    assert controller.mode == TuiDisplayMode.COMPACT

    controller.handle_key("l")
    assert controller.mode == TuiDisplayMode.LOGS
    controller.handle_key("?")
    assert controller.mode == TuiDisplayMode.HELP
    controller.handle_key("?")
    assert controller.mode == TuiDisplayMode.LOGS

    controller.handle_key("v")
    assert controller.mode == TuiDisplayMode.VERBOSE
    controller.handle_key("\x1b")
    assert controller.mode == TuiDisplayMode.COMPACT
    controller.handle_key("l")
    controller.handle_key("c")
    assert controller.mode == TuiDisplayMode.COMPACT
    assert controller.handle_key("x") is False


def test_stop_requires_two_q_presses_and_escape_cancels() -> None:
    console, _ = _console()
    controller = TuiInspectionController(console, force_interactive=True)
    stopped: list[str] = []
    controller.set_stop_callback(lambda: stopped.append("stop"))

    controller.handle_key("q")
    assert controller.mode == TuiDisplayMode.CONFIRM_STOP
    assert stopped == []
    controller.handle_key("\x1b")
    assert controller.mode == TuiDisplayMode.COMPACT
    assert stopped == []

    controller.handle_key("q")
    controller.handle_key("q")
    assert stopped == ["stop"]
    assert controller.mode == TuiDisplayMode.COMPACT


def test_stop_request_is_not_lost_before_child_callback_is_bound() -> None:
    console, _ = _console()
    controller = TuiInspectionController(console, force_interactive=True)
    controller.handle_key("q")
    controller.handle_key("q")

    stopped: list[str] = []
    controller.set_stop_callback(lambda: stopped.append("stop"))
    assert stopped == ["stop"]


def test_verbose_buffer_is_bounded_and_strips_terminal_control_sequences() -> None:
    console, _ = _console()
    controller = TuiInspectionController(console, max_lines=20, force_interactive=True)
    for index in range(30):
        controller.append_line(f"\x1b[31mline-{index}\x1b[0m\n")

    lines = controller.lines()
    assert len(lines) == 20
    assert lines[0] == "line-10"
    assert lines[-1] == "line-29"
    assert all("\x1b" not in line for line in lines)


def test_modal_rendering_keeps_progress_in_verbose_and_hides_it_in_logs_only() -> None:
    console, output = _console(width=62, height=20)
    controller = TuiInspectionController(console, force_interactive=True)
    controller.append_line("compile line one")
    controller.append_line("warning: sample warning")
    controller.append_line("compile line three")
    body = ThreeLineProgressBody(
        lambda: ThreeLineProgressState(
            completed=2,
            total=10,
            detail=":app:compileDebugKotlin",
            started_at=1.0,
        ),
        inspection=controller,
    )

    controller.handle_key("v")
    console.print(body)
    verbose = output.getvalue()
    assert "Verbose · progress + output" in verbose
    assert "2/10" in verbose
    assert "compile line three" in verbose
    assert "c compact" in verbose

    output.seek(0)
    output.truncate(0)
    controller.handle_key("l")
    console.print(body)
    logs = output.getvalue()
    assert "Logs · scrollable output" in logs
    assert "compile line three" in logs
    assert "2/10" not in logs
    assert "v verbose" in logs
    assert "j/k" in logs


def test_help_and_compact_views_advertise_discoverable_shortcuts() -> None:
    console, output = _console(width=72)
    controller = TuiInspectionController(console, force_interactive=True)
    body = ThreeLineProgressBody(
        lambda: ThreeLineProgressState(total=None, detail="Working", started_at=1.0),
        inspection=controller,
    )

    console.print(body)
    compact = output.getvalue()
    assert "[v] verbose" in compact
    assert "[q] stop" in compact

    output.seek(0)
    output.truncate(0)
    controller.handle_key("?")
    console.print(body)
    help_text = output.getvalue()
    assert "TUI shortcuts" in help_text
    assert "live verbose view" in help_text
    assert "two presses" in help_text



def test_logs_view_scrolls_and_stays_anchored_when_new_output_arrives() -> None:
    console, output = _console(width=62, height=14)
    controller = TuiInspectionController(console, max_lines=50, force_interactive=True)
    for index in range(30):
        controller.append_line(f"line-{index:02d}")
    body = ThreeLineProgressBody(
        lambda: ThreeLineProgressState(total=None, detail="Working", started_at=1.0),
        inspection=controller,
    )

    controller.handle_key("l")
    console.print(body)
    latest = output.getvalue()
    assert "line-29" in latest
    assert "follow" in latest

    output.seek(0)
    output.truncate(0)
    for _ in range(4):
        controller.handle_key("k")
    assert controller.scroll_offset == 4
    console.print(body)
    older = output.getvalue()
    assert "line-29" not in older
    assert "↑4" in older

    controller.append_line("line-30")
    assert controller.scroll_offset == 5
    output.seek(0)
    output.truncate(0)
    console.print(body)
    anchored = output.getvalue()
    assert "line-30" not in anchored

    controller.handle_key("G")
    assert controller.following is True
    output.seek(0)
    output.truncate(0)
    console.print(body)
    resumed = output.getvalue()
    assert "line-30" in resumed
    assert "follow" in resumed


def test_log_navigation_supports_arrows_pages_and_ends() -> None:
    console, _ = _console(width=62, height=14)
    controller = TuiInspectionController(console, max_lines=50, force_interactive=True)
    for index in range(30):
        controller.append_line(f"line-{index:02d}")
    controller.handle_key("l")
    controller.set_page_size(6)

    controller.handle_key("UP")
    assert controller.scroll_offset == 1
    controller.handle_key("PAGEUP")
    assert controller.scroll_offset == 6
    controller.handle_key("DOWN")
    assert controller.scroll_offset == 5
    controller.handle_key("PAGEDOWN")
    assert controller.scroll_offset == 0
    controller.handle_key("HOME")
    assert controller.scroll_offset == 24
    controller.handle_key("END")
    assert controller.scroll_offset == 0


def test_narrow_log_view_wraps_text_and_uses_compact_chrome() -> None:
    console, output = _console(width=40, height=15)
    controller = TuiInspectionController(console, force_interactive=True)
    controller.append_line("prefix-" + "x" * 58 + "-tail")
    body = ThreeLineProgressBody(
        lambda: ThreeLineProgressState(total=None, detail="Working", started_at=1.0),
        inspection=controller,
    )

    controller.handle_key("l")
    console.print(body)
    rendered = output.getvalue()
    assert "Logs" in rendered
    assert "-tail" in rendered
    assert "j/k" in rendered
    assert "Logs · scrollable output" not in rendered
    assert all(len(line) <= 40 for line in rendered.splitlines())


@pytest.mark.parametrize("width", [32, 40, 60, 80, 120])
def test_log_reader_width_matrix_never_exceeds_terminal(width: int) -> None:
    console, output = _console(width=width, height=16)
    controller = TuiInspectionController(console, force_interactive=True)
    for index in range(8):
        controller.append_line(f"line-{index}: /very/long/path/" + "segment/" * 8 + "file.kt")
    body = ThreeLineProgressBody(
        lambda: ThreeLineProgressState(total=None, detail="Working", started_at=1.0),
        inspection=controller,
    )
    controller.handle_key("l")
    console.print(body)
    rendered = output.getvalue()
    assert ".kt" in rendered
    assert all(len(line) <= width for line in rendered.splitlines())


def test_compact_shortcut_footer_adapts_to_termux_width() -> None:
    console, output = _console(width=40, height=15)
    controller = TuiInspectionController(console, force_interactive=True)
    body = ThreeLineProgressBody(
        lambda: ThreeLineProgressState(total=None, detail="Working", started_at=1.0),
        inspection=controller,
    )
    console.print(body)
    rendered = output.getvalue()
    assert "v/l view" in rendered
    assert "[v] verbose" not in rendered

def test_shortcuts_are_disabled_for_ci_and_non_tty_stdin() -> None:
    console, _ = _console()
    fake_stdin = StringIO()
    with patch("devtool.rich_progress.sys.stdin", fake_stdin), patch.dict("devtool.rich_progress.os.environ", {}, clear=True):
        controller = TuiInspectionController(console)
    assert controller.enabled is False

    with patch.dict("devtool.rich_progress.os.environ", {"CI": "true"}, clear=True):
        controller = TuiInspectionController(console)
    assert controller.enabled is False


def test_shortcuts_can_be_explicitly_disabled_even_on_interactive_console() -> None:
    console, _ = _console()

    class TtyInput(StringIO):
        def isatty(self) -> bool:
            return True

    with patch("devtool.rich_progress.sys.stdin", TtyInput()), patch.dict(
        "devtool.rich_progress.os.environ", {"DEVTOOL_TUI_SHORTCUTS": "0"}, clear=True
    ):
        controller = TuiInspectionController(console)
    assert controller.enabled is False


@pytest.mark.parametrize("kind", ["generic", "gradle", "npm", "dotnet"])
def test_all_live_progress_trackers_feed_the_shared_verbose_buffer(tmp_path: Path, kind: str) -> None:
    console, _ = _console()
    if kind == "generic":
        tracker = GenericProgressTracker(["python", "-m", "pytest"], str(tmp_path), "test", console)
    elif kind == "gradle":
        tracker = GradleProgressTracker(["./gradlew", "test"], str(tmp_path), "test", console)
    elif kind == "npm":
        tracker = NpmProgressTracker(["npm", "run", "test"], str(tmp_path), "test", console)
    else:
        tracker = DotnetProgressTracker(["dotnet", "build"], str(tmp_path), "build", console)

    tracker.inspection = TuiInspectionController(console, force_interactive=True)
    tracker.update_from_line("shared verbose output line\n")
    assert tracker.inspection.lines()[-1] == "shared verbose output line"



def test_runner_binds_existing_interrupt_path_to_tui_tracker(tmp_path: Path) -> None:
    console, _ = _console()
    callbacks = []
    original = GenericProgressTracker.set_stop_callback

    def capture(self, callback):  # type: ignore[no-untyped-def]
        callbacks.append(callback)
        return original(self, callback)

    with patch("devtool.runners.console", console), patch.object(GenericProgressTracker, "set_stop_callback", capture):
        code, output = _run_process_live(
            ["python3", "-c", "print('runner-binding-ok')"],
            cwd=str(tmp_path),
            env=os.environ.copy(),
            verbose=False,
            command_name="test",
            progress_parser="generic",
        )

    assert code == 0
    assert "runner-binding-ok" in output
    assert len(callbacks) == 1
    assert callable(callbacks[0])



def test_start_waits_until_keyboard_reader_is_ready() -> None:
    console, _ = _console()

    class DelayedReadyController(TuiInspectionController):
        reader_entered = False

        def _keyboard_loop(self) -> None:
            time.sleep(0.03)
            self.reader_entered = True
            self._reader_ready.set()
            self._stop_event.wait(1.0)

    controller = DelayedReadyController(console, force_interactive=True)
    controller.start()
    try:
        assert controller.reader_entered is True
        assert controller.listening is True
    finally:
        controller.stop()
