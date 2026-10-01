from __future__ import annotations

import importlib.util
import inspect
import os
from pathlib import Path

import pytest

from devtool.textual_apply_host import create_overlay_apply_source
from devtool.textual_ux import (
    ArtifactActivityTracker,
    TEXTUAL_COMPACT_WIDTH,
    TEXTUAL_SHORT_HEIGHT,
    artifact_display_progress,
    artifact_review_text,
    artifact_stage,
    artifact_stage_line,
    progress_counter,
    progress_percent,
    responsive_class_names,
)


def test_shared_textual_ux_projects_progress_and_artifact_stages_without_owning_truth() -> None:
    state = {
        "status": "running",
        "message": "Artifact validation",
        "phase": {"name": "artifact validation", "detail": "pytest"},
        "progress": {"completed": 30, "total": 60},
    }
    assert progress_percent(state) == 50.0
    assert progress_counter(state) == "30/60"
    assert artifact_stage(state) == "Validate"
    line = artifact_stage_line(state)
    assert "▶ Validate" in line
    assert "Review" in line and "Finish" in line
    assert artifact_stage(state, returncode=0) == "Finish"


def test_artifact_review_text_is_dense_but_readable() -> None:
    text = artifact_review_text({
        "artifact": "ux.zip",
        "target": "devtool",
        "changes": "4 files · modify",
        "validation": "required",
        "lifecycle": "validator · 3 tests",
        "apply": "atomic · commit",
    })
    assert "Artifact   ux.zip" in text
    assert "Validation required" in text
    assert text.count("\n") == 5


def test_control_center_revamp_uses_contextual_actions_and_inline_apply_workspace() -> None:
    import devtool.textual_control_center_runtime as runtime

    source = inspect.getsource(runtime.create_textual_control_center_app)
    assert "PAGE_CONTEXT" in source
    assert "ACTIONS_BY_PAGE" in source
    assert "dashboard-summary" in source
    assert "_apply_contextual_action_visibility" in source
    assert "ApplyWorkspaceScreen" in source
    assert "create_overlay_apply_source" in source
    assert "Artifact apply is still running" in source
    # Applying from the production TUI no longer has to disappear into a
    # detached DEVNULL child; the old dispatcher remains available for injected
    # / non-inline control clients.
    assert "use_inline_overlay_apply" in source
    assert '"diagnostics": ("cancel", "retry", "rerun", "resume")' in source
    assert "self._update_action_state()" in source
    assert ".short #action-bar { height: 3; min-height: 3; max-height: 3; }" in source


def test_execution_and_apply_hosts_share_responsive_workspace_language() -> None:
    import devtool.execution_presentation as execution
    import devtool.textual_apply_host as apply_host

    execution_source = inspect.getsource(execution.build_textual_execution_app)
    apply_source = inspect.getsource(apply_host.build_apply_textual_app)
    for source in (execution_source, apply_source):
        assert "TEXTUAL_SHARED_CSS" in source
        assert "compact" in source
        assert "ProgressBar" in source
        assert "RichLog" in source
    assert "execution-log-toolbar" in execution_source
    assert "apply-stage" in apply_source
    assert "apply-log-toolbar" in apply_source


def test_textual_css_uses_only_supported_v8_workspace_properties() -> None:
    import devtool.textual_apply_host as apply_host
    import devtool.textual_control_center_runtime as control_center
    import devtool.textual_ux as textual_ux

    sources = (
        textual_ux.TEXTUAL_SHARED_CSS,
        inspect.getsource(apply_host.build_apply_textual_app),
        inspect.getsource(control_center.create_textual_control_center_app),
    )
    for source in sources:
        # Textual 8.2.x has border styles but no CSS border-radius property.
        # Keep compact cards square by dropping side borders instead.
        assert "border-radius" not in source


def test_control_center_apply_source_reuses_h02_managed_child_authority(tmp_path: Path) -> None:
    source = create_overlay_apply_source(tmp_path, "demo.zip", target="devtool", env={"HOME": str(tmp_path)})
    assert source.cwd == tmp_path.resolve()
    assert source.review["command"] == "apply-overlay"
    assert source.review["path"] == "demo.zip"
    assert source.raw_argv[-3:] == ("--yes", "apply-overlay", "demo.zip")



def test_mobile_viewport_classes_and_semantic_validator_progress_do_not_fake_overall_zero() -> None:
    assert set(responsive_class_names(48, 14)) == {"compact", "ultra-compact", "short", "tiny"}
    assert "short" not in responsive_class_names(90, TEXTUAL_SHORT_HEIGHT)

    tracker = ArtifactActivityTracker()
    assert tracker.observe(".................. [ 73%]") is False
    state = {
        "status": "running",
        "phase": {"name": "artifact validation", "detail": "pytest"},
        "progress": {"percent": 0, "detail": "validator"},
    }
    percent, label, scope = artifact_display_progress(state, tracker)
    assert percent == 73.0
    assert label == "pytest 73%"
    assert scope == "validation child"

    # A validator with no known child denominator stays indeterminate instead of
    # presenting a deceptive 0% artifact completion bar.
    empty = ArtifactActivityTracker()
    percent, label, scope = artifact_display_progress(state, empty)
    assert percent is None
    assert label == "working"
    assert scope == "indeterminate"


def test_activity_tracker_filters_heartbeat_and_pytest_dots_but_preserves_raw_semantics() -> None:
    tracker = ArtifactActivityTracker()
    assert tracker.observe("Devtool heartbeat: artifact validator is still running after 60s without child output.") is False
    assert tracker.observe(".............. [ 90%]") is False
    assert tracker.pytest_percent == 90.0
    assert tracker.observe("70 passed, 4 deselected in 31.50s") is True
    assert tracker.test_summary == "70 passed, 4 deselected · 31.50s"
    text = tracker.activity_text(elapsed_seconds=62.0, max_lines=2, width=70)
    assert "70 passed" in text
    assert tracker.raw_line_count == 3

def _require_textual() -> None:
    if importlib.util.find_spec("textual") is not None:
        return
    if os.environ.get("DEVTOOL_PRES_UX01_REQUIRE_TEXTUAL") == "1":
        pytest.fail("PRES-UX01 requires Textual for concrete workspace qualification")
    pytest.skip("Textual unavailable on construction host")


def _payload() -> dict:
    return {
        "runs": [{"run_id": "run-a", "status": "running", "command": "validate", "project": "demo"}],
        "nodes_by_run": {"run-a": []},
        "logs": {"path": "/tmp/run-a.log", "lines": ["one", "two"], "bounded": True},
        "diagnostics": [],
        "history": [],
        "targets": [],
        "workers": [{"profile": "tablet", "status": "ready", "provider": "ssh-native", "target": "devtool"}],
        "validation": {"active": True, "state": "running", "phases": []},
        "artifacts": [],
        "overlays": [{"history_id": "overlay-1", "artifact": "demo.zip", "status": "ready", "operation": "preflight"}],
        "evidence": [],
        "ipc": {"ok": True},
    }


def test_real_textual_revamp_contextual_actions_compact_layout_and_inline_apply(tmp_path: Path) -> None:
    import asyncio

    _require_textual()
    from textual.widgets import Button, ListView, Static

    from devtool.textual_control_center_runtime import InMemoryControlCenterProvider, RecordingControlActionDispatcher, create_textual_control_center_app

    class FakeApplySource:
        max_log_lines = 40
        review = {
            "artifact": "demo.zip", "path": "demo.zip", "target": "devtool",
            "changes": "2 files · modify", "validation": "required",
            "lifecycle": "validator", "apply": "atomic · commit",
        }

        def __init__(self) -> None:
            self.started = False
            self.cancel_requested = False
            self.returncode = None
            self._polls = 0

        @property
        def running(self) -> bool:
            return self.started and self.returncode is None

        def start(self, *, force_yes: bool) -> None:
            assert force_yes is True
            self.started = True

        def drain_lines(self, *, limit: int = 256):
            return ["canonical apply output"] if self._polls == 0 else []

        def read_state(self):
            self._polls += 1
            if self._polls == 1:
                return {
                    "status": "running", "message": "validating",
                    "phase": {"name": "artifact validation"},
                    "progress": {"percent": 50, "detail": "tests"},
                }
            self.returncode = 0
            return {"status": "ok", "message": "done", "progress": {"percent": 100}}

        def request_cancel(self) -> None:
            self.cancel_requested = True

        def terminate(self) -> None:
            return None

    fake_sources: list[FakeApplySource] = []

    def factory(_artifact: str):
        source = FakeApplySource()
        fake_sources.append(source)
        return source

    app = create_textual_control_center_app(
        repo_root=tmp_path,
        page="overlays",
        provider=InMemoryControlCenterProvider(_payload()),
        dispatcher=RecordingControlActionDispatcher(),
        inline_overlay_apply=True,
        apply_source_factory=factory,
        refresh_interval=0.04,
    )

    async def exercise() -> None:
        async with app.run_test(size=(52, 20)) as pilot:
            await pilot.pause(0.1)
            assert app.screen.has_class("compact")
            assert app.query_one("#navigation", ListView).has_class("-narrow")
            context = app.query_one("#page-context", Static)
            assert "Overlays" in str(context.content if hasattr(context, "content") else context.render())
            assert app.query_one("#action-overlay-apply", Button).display is True
            assert app.query_one("#action-cancel", Button).display is False

            table = app.query_one("#overlays-table")
            table.move_cursor(row=0, column=0, animate=False)
            await pilot.pause()
            button = app.query_one("#action-overlay-apply", Button)
            assert button.disabled is False
            button.scroll_visible()
            await pilot.click("#action-overlay-apply")
            await pilot.pause()
            assert app.screen.__class__.__name__ == "ConfirmationModal"
            await pilot.click("#confirm-yes")
            await pilot.pause(0.05)
            assert app.screen.__class__.__name__ == "ApplyWorkspaceScreen"
            await pilot.pause(0.25)
            assert fake_sources and fake_sources[0].started
            assert app.screen.query_one("#inline-apply-close", Button).disabled is False
            await pilot.click("#inline-apply-close")
            await pilot.pause()
            assert app.screen.__class__.__name__ == "Screen"

            await pilot.resize_terminal(110, 34)
            await pilot.pause()
            assert not app.screen.has_class("compact")
            await pilot.press("q")

    asyncio.run(exercise())


def test_compact_breakpoint_remains_termux_friendly() -> None:
    assert TEXTUAL_COMPACT_WIDTH == 72


def test_real_textual_apply_workspace_mobile_geometry_reflows_and_surfaces_validation_child_progress() -> None:
    import asyncio

    _require_textual()
    from textual.widgets import ProgressBar, RichLog, Static

    from devtool.textual_apply_host import build_apply_textual_app

    class MobileApplySource:
        max_log_lines = 120
        review = {
            "artifact": "pres_r03_textual_parity_qualification_v6_with_a_very_long_name",
            "path": "demo.zip",
            "target": "devtool",
            "changes": "30 files · add, modify",
            "validation": "required",
            "lifecycle": "validator",
            "apply": "atomic · fail · commit",
        }

        def __init__(self) -> None:
            self.started = False
            self.returncode = None
            self._lines = [
                "Devtool heartbeat: artifact validator is still running after 60s without child output.",
                ".................. [ 73%]",
            ]

        @property
        def running(self) -> bool:
            return self.started and self.returncode is None

        @property
        def elapsed_seconds(self) -> float:
            return 62.0

        def start(self, *, force_yes: bool) -> None:
            assert force_yes is True
            self.started = True

        def drain_lines(self, *, limit: int = 256):
            lines, self._lines = self._lines[:limit], self._lines[limit:]
            return lines

        def read_state(self):
            return {
                "status": "running",
                "message": "artifact validator",
                "phase": {"name": "artifact validation", "detail": "pytest"},
                "progress": {"percent": 0, "detail": "validator"},
            }

        def request_cancel(self) -> None:
            return None

        def terminate(self) -> None:
            self.returncode = 143

    source = MobileApplySource()
    app = build_apply_textual_app(source, confirm_required=False)

    async def exercise() -> None:
        async with app.run_test(size=(48, 14)) as pilot:
            await pilot.pause(0.25)
            assert source.started
            assert app.screen.has_class("compact")
            assert app.screen.has_class("short")
            assert app.screen.has_class("tiny")
            assert app.query_one("#apply-review", Static).display is False
            meta = app.query_one("#apply-progress-meta", Static)
            rendered = str(meta.content if hasattr(meta, "content") else meta.render())
            assert "pytest 73%" in rendered
            assert app.query_one("#apply-progress", ProgressBar).display is True
            log = app.query_one("#apply-log", RichLog)
            assert log.region.height >= 1
            assert log.region.bottom <= app.screen.region.bottom

            await pilot.resize_terminal(52, 20)
            await pilot.pause()
            assert app.screen.has_class("compact")
            assert app.screen.has_class("short")
            assert not app.screen.has_class("tiny")
            assert app.query_one("#apply-log", RichLog).region.height >= 4

            await pilot.resize_terminal(92, 30)
            await pilot.pause()
            assert not app.screen.has_class("compact")
            assert not app.screen.has_class("short")
            await pilot.press("d")
            await pilot.pause()
            assert app.query_one("#apply-review", Static).display is True
            await pilot.press("d")
            await pilot.press("r")
            await pilot.pause()
            assert app._show_raw is True
            assert app.query_one("#apply-log", RichLog).region.height > 0

    asyncio.run(exercise())
