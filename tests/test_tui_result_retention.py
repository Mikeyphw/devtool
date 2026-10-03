from __future__ import annotations

import inspect

from devtool import execution_presentation, textual_apply_host, textual_control_center_runtime, textual_execution_host
from devtool.textual_ux import normalized_status


def test_execution_host_does_not_auto_dismiss_after_child_completion() -> None:
    bridge_source = inspect.getsource(textual_execution_host.ExecutionTextualHostSource.start)
    app_source = inspect.getsource(execution_presentation.build_textual_execution_app)

    assert 'self.presenter._offer("stop", None)' not in bridge_source
    assert '("q", "close_finished", "Close")' in app_source
    assert '("enter", "close_finished", "Close")' in app_source
    assert "Presenter completion must not dismiss the result workspace" in app_source
    assert "def action_close_finished" in app_source


def test_standalone_apply_retains_terminal_result_until_explicit_close() -> None:
    app_source = inspect.getsource(textual_apply_host.build_apply_textual_app)

    assert 'time.monotonic() - self._finish_seen_at >= 0.45' not in app_source
    assert '("q", "close_finished", "Close")' in app_source
    assert '("enter", "close_finished", "Close")' in app_source
    assert "def action_close_finished" in app_source
    assert "self.exit(self._returncode)" in app_source


def test_terminal_result_colors_and_non_color_cues_are_declared() -> None:
    execution_source = inspect.getsource(execution_presentation.build_textual_execution_app)
    apply_source = inspect.getsource(textual_apply_host.build_apply_textual_app)
    inline_source = inspect.getsource(textual_control_center_runtime.create_textual_control_center_app)

    for source, prefix in (
        (execution_source, "execution"),
        (apply_source, "apply"),
        (inline_source, "inline-apply"),
    ):
        assert "result-completed" in source
        assert "result-failed" in source
        assert "result-cancelled" in source
        assert "$success" in source
        assert "$error" in source
        assert "$warning" in source
        assert f"#{prefix}" in source

    assert '"✓"' in execution_source
    assert '"✕"' in execution_source
    assert '"■"' in execution_source


def test_cancelled_execution_projects_as_cancelled_not_failed() -> None:
    bridge_source = inspect.getsource(textual_execution_host.ExecutionTextualHostSource.start)
    assert '"cancelled" if rc == 130 else "failed"' in bridge_source
    assert normalized_status("cancelled") == "cancelled"
    assert normalized_status("ok") == "completed"
