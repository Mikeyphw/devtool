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
        "Stage 1",
        "Copy all diagnostics",
        "Inspect manifest",
        "Run again",
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
        "devtool-progress",
        "devtool-progress-failed",
        "phase_highlights",
        "phase_progress",
        "def _phase_cards_from_snapshot",
        "def _diagnostics_from_state",
        "Events",
        "Diagnostics",
    ):
        assert token in source
    assert "PyQt" not in source
    assert "PySide" not in source
    assert 'gi.require_version("Gtk", "3.0")' not in source
