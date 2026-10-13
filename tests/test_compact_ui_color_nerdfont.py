"""Actual terminal color, responsive Textual, and icon fallback regressions."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from queue import Queue
from threading import Event

import pytest

from devtool.job_ui import (
    CompactLogBuffer, job_icons, readable_job_line, safe_ansi_line,
    styled_job_line, build_compact_execution_app,
)


def test_python_rich_subprocess_preserves_color_and_bold():
    """FORCE_COLOR is required because Devtool captures child stdout via a pipe."""
    pytest.importorskip("rich")
    proc = subprocess.run(
        [sys.executable, "-c", 'from rich import print; print("[bold red]FAILED[/bold red] [green]PASSED[/green]")'],
        env={key: val for key, val in {**os.environ, "FORCE_COLOR": "1"}.items() if key != "NO_COLOR"},
        text=True, capture_output=True, check=True,
    )
    assert "\x1b[" in proc.stdout
    buf = CompactLogBuffer(20)
    line, active = buf.append(proc.stdout)
    assert active
    assert "\x1b[" in line and "\x1b[" in buf.activity[0]
    rendered = styled_job_line(line, no_color=False)
    assert rendered.plain == "FAILED PASSED"
    assert any("bold" in str(span.style) for span in rendered.spans)
    assert len(rendered.spans) >= 2


def test_no_color_preference_works_even_if_child_uses_ansi():
    text = styled_job_line("\x1b[1;31mFAILED\x1b[0m", no_color=True)
    assert text.plain == "FAILED"
    assert not text.spans


def test_untrusted_output_does_not_expose_terminal_control_sequences():
    sample = (
        "\x1b]0;title-hijack\x07\x1b[2J\x1b[H\x1b[31mFAILED\x1b[0m"
        "\x1b]8;;https://untrusted.invalid\x1b\\link\x1b]8;;\x1b\\"
    )
    safe = safe_ansi_line(sample)
    assert "\x1b[31m" in safe
    assert "\x1b[2J" not in safe
    assert "\x1b]" not in safe
    assert "title-hijack" not in safe
    assert readable_job_line(safe) == "FAILED"
    assert styled_job_line(safe).plain == "FAILED"


def test_icons_are_opt_in_for_unpatched_termux_fonts():
    default = job_icons({})
    rich = job_icons({"DEVTOOL_NERD_FONT": "1"})
    assert default["brand"] == "◆"
    assert rich["brand"] != default["brand"]
    assert len(rich["done"]) == 1
    assert job_icons({"DEVTOOL_NERD_FONT": "off"}) == default


class Stub:
    max_log_lines = 60
    def __init__(self):
        self._queue = Queue()
        self._ready = Event()
        self.cancels = 0
    def _request_cancel(self):
        self.cancels += 1


@pytest.mark.parametrize("size", [(32, 10), (40, 12), (74, 20), (90, 28)])
def test_fullscreen_layout_fills_available_height_and_color_survives(size):
    pytest.importorskip("textual")
    from textual.widgets import RichLog
    source = Stub()
    app = build_compact_execution_app(source)

    async def scenario():
        async with app.run_test(size=size) as pilot:
            log = app.query_one("#job-logs", RichLog)
            assert log.size.height >= 2
            assert log.size.width >= min(size[0] - 8, 20)
            source._queue.put(("line", "\x1b[1;31mERROR: broken\x1b[0m"))
            await pilot.pause(0.15)
            assert "\x1b[" in app._buffer.raw[0]
            assert "ERROR: broken" in readable_job_line(app._buffer.raw[0])
            await pilot.press("r")
            assert app._raw_mode
            await pilot.press("r")
            assert not app._raw_mode
            source._queue.put(("snapshot", {"command": "test", "completion_status": "failed"}))
            await pilot.pause(0.15)
            assert app._outcome == "failed"
            await pilot.press("q")

    asyncio.run(scenario())
