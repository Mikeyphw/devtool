"""Source-bound UI regression: one-row truthful stages + live viewport resizing."""
from __future__ import annotations

import asyncio

from devtool.textual_ux import APPLY_STAGES, ApplyStageTracker, horizontal_apply_stage_strip


def test_stage_strip_tracks_observed_events_not_inferred_success() -> None:
    t = ApplyStageTracker()
    t.begin()
    assert t.outcomes["Qualify"] == "active"
    assert "● Qualify" in horizontal_apply_stage_strip(t, width=110).plain
    assert "○ Validate" in horizontal_apply_stage_strip(t, width=110).plain

    t.observe_state({"phase": {"name": "artifact validation"}})
    assert t.visible_current() == "Validate"
    assert t.outcomes["Validate"] == "active"
    # A current phase snapshot is not proof that prior stages succeeded.
    assert t.outcomes["Qualify"] != "done"
    t.observe_state({"phase": {"name": "preflight"}})
    assert t.visible_current() == "Validate"  # ignore stale phase
    t.observe_state({"event_type": "phase_finished", "phase": {"name": "artifact validation"},
                     "result": {"returncode": 1}})
    assert t.outcomes["Validate"] == "failed"
    t.finish(1)
    assert t.outcomes["Validate"] == "failed"
    assert t.outcomes["Finish"] == "failed"
    assert "× Validate" in horizontal_apply_stage_strip(t, width=110).plain
    for w in (22, 34, 48, 60, 72, 95, 120):
        text = horizontal_apply_stage_strip(t, width=w)
        assert "\n" not in text.plain
        assert len(text.plain) <= max(1, w - 2)
    assert all(x in horizontal_apply_stage_strip(t, width=110).plain for x in APPLY_STAGES)


def test_success_never_credits_unobserved_intermediate_stages() -> None:
    t = ApplyStageTracker()
    t.begin()
    t.observe_line("Applied overlay from /tmp/demo.zip")
    assert t.outcomes["Apply"] == "done"
    assert t.outcomes["Validate"] == "active"
    t.finish(0)
    assert t.outcomes["Finish"] == "done"
    assert t.outcomes["Validate"] != "done"
    assert t.outcomes["Commit"] != "done"
    assert "○ Commit" in horizontal_apply_stage_strip(t, width=100).plain


def test_live_textual_terminal_resize_reclaims_log_height() -> None:
    from textual.widgets import RichLog, Static
    from devtool.textual_apply_host import build_apply_textual_app

    class Source:
        max_log_lines = 100
        review = {
            "artifact": "example", "target": "devtool", "changes": "1 file",
            "validation": "required", "lifecycle": "validator", "apply": "atomic",
        }
        running = True
        returncode = None
        def start(self, *, force_yes: bool):
            assert force_yes
        def drain_lines(self):
            return []
        def read_state(self):
            return {"status": "running", "phase": {"name": "artifact validation"},
                    "progress": {"percent": 0}}
        def request_cancel(self):
            pass
        def terminate(self):
            self.running = False

    async def check() -> None:
        app = build_apply_textual_app(Source(), compact=True, confirm_required=False)
        async with app.run_test(size=(64, 19)) as pilot:
            await pilot.pause(0.22)
            stage = app.query_one("#apply-stages", Static)
            log = app.query_one("#apply-log", RichLog)
            assert stage.region.height == 1, stage.region
            assert "Validate" in str(stage.content) or "Test" in str(stage.content)
            short_height = log.region.height
            assert short_height >= 1
            await pilot.resize_terminal(64, 36)  # keyboard hides: bigger pty viewport
            await pilot.pause(0.15)
            assert stage.region.height == 1
            assert log.region.height >= short_height + 8, (short_height, log.region.height)
            assert not app.screen.has_class("short")
            await pilot.resize_terminal(43, 16)  # keyboard opens: reduced pty viewport
            await pilot.pause(0.15)
            assert app.screen.has_class("short")
            assert stage.region.height == 1
            assert log.region.height >= 1
            assert log.region.bottom <= app.screen.region.bottom
            await pilot.resize_terminal(90, 34)
            await pilot.pause(0.1)
            assert "Review" in str(stage.content)
            assert "Validate" in str(stage.content)
            assert log.region.height > short_height

    asyncio.run(check())
