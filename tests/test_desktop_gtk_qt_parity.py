from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_gtk_control_center_keeps_rich_desktop_surfaces_without_qt() -> None:
    source = read(ROOT / "devtool" / "desktop" / "gtk_control_center.py")

    for token in (
        '"overview": "Overview"',
        '"runs": "Active runs"',
        '"history": "History"',
        '"artifacts": "Artifacts"',
        '"diagnostics": "Diagnostics"',
        '"repositories": "Repository"',
        '"targets": "Targets"',
        '"run": "Run"',
        '"settings": "Settings"',
        "def _render_artifacts",
        "Gtk.ScrolledWindow",
        "Gtk.SearchEntry",
        "Gtk.ProgressBar",
        "Gtk.Notebook",
        "Stage 1",
        "Next action",
        "Validate repository",
        "Build workflow",
        "Open HUD",
        "<Control>",
        "<Alt>",
        "Copy all diagnostics",
        "Severity: all",
        "Inspect manifest",
        "Run again",
        "Repository manager",
        "Clipboard monitor",
        "def _append_run_card",
        "def _append_step_card",
    ):
        assert token in source
    assert "PyQt" not in source
    assert "PySide" not in source
    assert "QML" not in source


def test_gtk_hud_keeps_compact_pipeline_diagnostics_and_event_surfaces() -> None:
    source = read(ROOT / "devtool" / "desktop" / "gtk_hud.py")

    for token in (
        "Gtk.FlowBox",
        "Gtk.TextView",
        "Gtk.ScrolledWindow",
        "Copy diagnostics",
        "Copy summary",
        "Open log",
        "devtool-progress",
        "devtool-progress-failed",
        "phase_highlights",
        "phase_progress",
        "operation_steps",
        "completion_copy_text",
        "compact_operation_title",
        "manualResize",
        "autoSizeEnabled",
        "workspace lane",
        "<Control>d",
        "def _phase_cards_from_snapshot",
        "def _diagnostics_from_state",
        "def _append_step_card",
        "Events",
        "Diagnostics",
    ):
        assert token in source
    assert "PyQt" not in source
    assert "PySide" not in source
    assert 'gi.require_version("Gtk", "3.0")' not in source


def test_control_center_run_summary_uses_canonical_hud_model() -> None:
    from devtool.desktop.control_center_model import _run_summary_dict

    summary = _run_summary_dict({
        "command": "apply-overlay",
        "status": "running",
        "repo_name": "studyflow",
        "target": "studyflow_android",
        "current": 5,
        "total": 10,
        "phase_progress": [
            {"name": "restore[studyflow_android]", "status": "ok", "percent": 100},
            {"name": "build[studyflow_android]", "status": "running", "percent": 50},
        ],
        "totals": {"errors": 1, "warnings": 2},
    })

    assert summary["percent"] == 50
    assert summary["errors"] == 1
    assert summary["warnings"] == 2
    assert summary["context"] == "studyflow · studyflow_android"
    assert any(step["label"] == "Build · studyflow_android" for step in summary["steps"])
