"""Textual 8.x headless integration; never starts real Devtool jobs."""
from __future__ import annotations

import asyncio
from queue import Queue
from threading import Event

import pytest

from devtool.job_ui import (
    CompactLogBuffer, activity_job_line, build_compact_execution_app,
    compact_job_projection, readable_job_line,
)


class Source:
    def __init__(self):
        self.max_log_lines = 80
        self._ready = Event()
        self._queue = Queue()
        self.cancels = 0

    def _request_cancel(self):
        self.cancels += 1


def test_bounded_sanitized_logs_and_activity_never_hide_failures():
    buffer = CompactLogBuffer(max_lines=20)
    assert not activity_job_line("................")
    assert activity_job_line("ERROR: signing failed")
    assert readable_job_line("\x1b[31mFAILED\x1b[0m\r\x00") == "FAILED  "
    for _ in range(70):
        buffer.append("................")
    buffer.append("FAILED: validation")
    assert len(buffer.raw) == 20
    assert buffer.activity[-1] == "FAILED: validation"
    assert len(buffer.activity) == 1
    assert len(buffer.append("a" * 10000)[0]) <= 2048


def test_narrow_projection_truncates_and_preserves_unknown_percent():
    view = compact_job_projection({"command": "build", "phase_name": "gradle-super-long-process", "detail": "x"*900}, width=28)
    assert len(view["title"]) <= 26
    assert len(view["detail"]) <= 26
    assert view["percent"] is None


def test_real_textual_widget_runs_in_phone_sized_terminal():
    pytest.importorskip("textual")
    from textual.widgets import ProgressBar, RichLog, Static
    source = Source()
    app = build_compact_execution_app(source, run_id="de6e8149")

    async def scenario():
        async with app.run_test(size=(40, 12)) as pilot:
            assert source._ready.is_set()
            source._queue.put(("line", ".............."))
            source._queue.put(("line", "ERROR: Gradle build failed"))
            source._queue.put(("snapshot", {
                "command": "build", "phase_name": "Compiling", "detail": "Gradle", "completion_status": "running",
            }))
            await pilot.pause(0.15)
            progress = app.query_one("#job-progress", ProgressBar)
            assert progress.display is True
            assert progress.total is None  # unknown progress must stay indeterminate
            assert len(app._buffer.raw) == 2
            assert len(app._buffer.activity) == 1
            assert not app._finished
            await pilot.press("r")
            assert app._raw_mode
            await pilot.press("r")
            assert not app._raw_mode
            await pilot.press("q")
            assert not app._finished
            app.action_request_cancel()
            app.action_request_cancel()
            assert source.cancels == 1
            source._queue.put(("snapshot", {
                "command": "build", "phase_name": "Finish", "completion_status": "failed", "detail": "Exit 1",
            }))
            await pilot.pause(0.15)
            assert app._finished and app._outcome == "failed"
            assert app._last_status == "FAILED"
            source._queue.put(("snapshot", {"command": "build", "completion_status": "ok"}))
            await pilot.pause(0.15)
            assert app._outcome == "failed"  # stale progress does not overturn real failure
            await pilot.press("q")

    asyncio.run(scenario())


def test_even_smaller_termux_viewport_does_not_crash():
    pytest.importorskip("textual")
    source = Source()
    app = build_compact_execution_app(source)

    async def scenario():
        async with app.run_test(size=(32, 10)) as pilot:
            source._queue.put(("snapshot", {"command": "test", "completion_status": "ok", "percent": 100}))
            await pilot.pause(0.15)
            assert app._outcome == "completed"
            await pilot.press("l")
            await pilot.press("l")
            await pilot.press("q")

    asyncio.run(scenario())


def test_real_textual_apply_app_in_tiny_termux_screen():
    pytest.importorskip("textual")
    from devtool.textual_apply_host import build_apply_textual_app
    from textual.widgets import ProgressBar

    class FakeApplySource:
        max_log_lines = 40
        review = {"artifact": "a.patch.zip", "target": "devtool", "changes": "2 files", "validation": "required"}
        returncode = None
        started = False
        running = False

        def start(self, *, force_yes):
            assert force_yes
            self.started = True

        def drain_lines(self):
            return []

        def read_state(self):
            return {"status": "running", "phase": "validate", "message": "real child running"}

        def wait(self, timeout=None):
            return self.returncode

        def terminate(self):
            pass

    fake = FakeApplySource()
    app = build_apply_textual_app(fake, confirm_required=False, compact=True)

    async def scenario():
        async with app.run_test(size=(38, 12)) as pilot:
            assert fake.started
            await pilot.pause(0.15)
            assert app.screen.has_class("compact-job")
            progress = app.query_one("#apply-progress", ProgressBar)
            assert progress.display is True
            assert progress.total is None  # canonical child did not report a percentage
            fake.returncode = 1
            await pilot.pause(0.20)
            assert app._returncode == 1
            assert app.screen.has_class("result-failed")
            await pilot.press("q")

    asyncio.run(scenario())


def test_phone_modal_confirms_before_child_starts():
    pytest.importorskip("textual")
    from devtool.textual_apply_host import build_apply_textual_app
    from textual.widgets import Button

    class FakeSource:
        max_log_lines = 40
        review = {"artifact": "very-long-name-for-recovery-overlay.zip", "target": "devtool", "changes": "128 files", "validation": "required"}
        returncode = None
        started = False
        running = False

        def start(self, *, force_yes):
            assert force_yes
            self.started = True

        def drain_lines(self):
            return []

        def read_state(self):
            return {}

        def wait(self, timeout=None):
            return self.returncode

        def terminate(self):
            pass

    src = FakeSource()
    app = build_apply_textual_app(src, confirm_required=True, compact=True)

    async def scenario():
        async with app.run_test(size=(36, 11)) as pilot:
            assert not src.started
            yes = app.screen.query_one("#apply-confirm-yes", Button)
            assert yes.display
            await pilot.click("#apply-confirm-yes")
            await pilot.pause(0.10)
            assert src.started
            src.returncode = 0
            await pilot.pause(0.15)
            await pilot.press("q")

    asyncio.run(scenario())
