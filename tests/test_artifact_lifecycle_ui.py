from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from devtool.hooks.engine import _run_live
from devtool.cli import _artifact_hook_presentation
from devtool.models import OperationResult
from devtool.summary import _presentation_results


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_hook_child_output_is_captured_not_echoed_by_shared_progress_ui(monkeypatch, tmp_path: Path) -> None:
    terminal = io.StringIO()
    monkeypatch.setattr(
        "devtool.hooks.engine.console",
        Console(file=terminal, force_terminal=True, width=100),
    )
    rc, output, tracker = _run_live(
        ["bash", "-lc", "printf '{\"ok\": true, \"message\": \"hook-json\"}\\n'"],
        cwd=tmp_path,
        env={},
        timeout_seconds=10,
        activity_label="Artifact preflight",
        script_label="Preflight",
        stdin_text=None,
        render_progress=True,
        progress_target="before extract",
    )
    assert rc == 0
    assert tracker is not None
    tracker.finish(rc)
    tracker.__exit__(None, None, None)
    rendered = terminal.getvalue()
    assert "hook-json" in output
    assert "Artifact preflight" in rendered
    assert "before extract" in rendered
    assert "hook-json" not in rendered
    assert "█" in rendered or "░" in rendered


def test_noninteractive_shared_progress_falls_back_to_raw_stream(monkeypatch, tmp_path: Path) -> None:
    terminal = _TTY()
    monkeypatch.setattr("sys.stdout", terminal)
    monkeypatch.setattr(
        "devtool.hooks.engine.console",
        Console(file=io.StringIO(), force_terminal=False, width=100),
    )
    rc, output, tracker = _run_live(
        ["bash", "-lc", "printf 'hook-ci-output\\n'"],
        cwd=tmp_path,
        env={},
        timeout_seconds=10,
        activity_label="artifact preflight",
        script_label="Preflight",
        progress_label="Artifact preflight",
        stdin_text=None,
        render_progress=True,
        progress_target="before extract",
    )
    assert rc == 0
    assert tracker is not None
    assert tracker.enabled is False
    tracker.__exit__(None, None, None)
    assert "hook-ci-output" in output
    assert "hook-ci-output" in terminal.getvalue()


def test_hook_verbose_stream_is_explicit_and_does_not_start_progress(monkeypatch, tmp_path: Path) -> None:
    stream = _TTY()
    monkeypatch.setattr("sys.stdout", stream)
    rc, output, tracker = _run_live(
        ["bash", "-lc", "printf 'hook-verbose\\n'"],
        cwd=tmp_path,
        env={},
        timeout_seconds=10,
        activity_label="Artifact preflight",
        script_label="Preflight",
        stdin_text=None,
        render_progress=False,
        stream_output=True,
    )
    assert rc == 0
    assert tracker is None
    assert "hook-verbose" in output
    assert "hook-verbose" in stream.getvalue()



def test_artifact_hook_presentation_matches_normal_devtool_modes() -> None:
    class Args:
        _devtool_json = False
        quiet = False
        verbose = False

    args = Args()
    assert _artifact_hook_presentation(args) == (True, False)
    args.verbose = True
    assert _artifact_hook_presentation(args) == (False, True)
    args.verbose = False
    args.quiet = True
    assert _artifact_hook_presentation(args) == (False, False)
    args.quiet = False
    args._devtool_json = True
    assert _artifact_hook_presentation(args) == (False, False)

def test_lifecycle_steps_render_as_normal_actions_without_double_count_parent() -> None:
    parent = OperationResult(
        "artifact-validate-script", [], 0, 1.0, "", [],
        metadata={
            "lifecycle_steps_replace_parent": True,
            "lifecycle_steps": [
                {"name": "artifact-finalize-script", "label": "Artifact finalize: before validation", "returncode": 0, "duration_seconds": 0.2, "hook_kind": "finalize", "hook_phase": "before_validation"},
                {"name": "artifact-validate-script", "label": "Artifact validation", "returncode": 0, "duration_seconds": 0.6, "hook_kind": "validation", "hook_phase": "validate"},
                {"name": "artifact-finalize-script", "label": "Artifact finalize: after validation", "returncode": 0, "duration_seconds": 0.2, "hook_kind": "finalize", "hook_phase": "after_validation"},
            ],
        },
    )
    shown = _presentation_results([parent])
    assert [item.metadata["validation_phase_name"] for item in shown] == [
        "Artifact finalize: before validation",
        "Artifact validation",
        "Artifact finalize: after validation",
    ]
    assert all(item.metadata.get("lifecycle_presentation_only") for item in shown)


def test_preflight_steps_are_shown_before_apply_parent() -> None:
    apply = OperationResult(
        "apply-overlay", [], 0, 2.0, "", [],
        metadata={
            "lifecycle_steps_position": "before",
            "lifecycle_steps": [
                {"name": "artifact-preflight-script", "label": "Artifact preflight: before extract", "returncode": 0, "duration_seconds": 0.1, "hook_kind": "preflight", "hook_phase": "before_extract"},
            ],
        },
    )
    shown = _presentation_results([apply])
    assert shown[0].metadata["validation_phase_name"] == "Artifact preflight: before extract"
    assert shown[1] is apply
