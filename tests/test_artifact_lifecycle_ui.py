from __future__ import annotations

import io
from pathlib import Path

from devtool.hooks.engine import _run_live
from devtool.models import OperationResult
from devtool.summary import _presentation_results


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_hook_child_output_is_captured_not_echoed_on_interactive_stdout(monkeypatch, tmp_path: Path) -> None:
    stream = _TTY()
    monkeypatch.setattr("sys.stdout", stream)
    rc, output = _run_live(
        ["bash", "-lc", "printf 'hook-json\\n'"],
        cwd=tmp_path,
        env={},
        timeout_seconds=10,
        activity_label="test",
        script_label="Preflight",
        stdin_text=None,
    )
    assert rc == 0
    assert "hook-json" in output
    assert "hook-json" not in stream.getvalue()


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
