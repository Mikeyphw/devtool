from __future__ import annotations

from pathlib import Path

from devtool.desktop.gtk_hud import _hud_status_chip_label, _human_step_status, _step_symbol

ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phase2_control_center_uses_async_selected_page_refresh() -> None:
    source = read(ROOT / "devtool" / "desktop" / "gtk_control_center.py")
    assert "import threading" in source
    assert "threading.Thread(" in source
    assert "devtool-control-center-refresh" in source
    assert "in_flight" in source and "queued" in source and "dirty_pages" in source
    assert "_render_loading_page" in source
    assert "_render_control_center_page" in source
    assert "render_visible_page" in source
    assert "last_payload" in source
    assert "close-request" in source


def test_phase2_control_center_no_longer_rebuilds_every_page_on_each_tick() -> None:
    source = read(ROOT / "devtool" / "desktop" / "gtk_control_center.py")
    old_bulk_calls = [
        '_render_overview(Gtk, pages["overview"], payload)',
        '_render_runs(Gtk, pages["runs"], payload)',
        '_render_history(Gtk, pages["history"], payload)',
        '_render_artifacts(Gtk, pages["artifacts"], payload)',
        '_render_diagnostics(Gtk, pages["diagnostics"], payload)',
        '_render_repositories(Gtk, pages["repositories"], payload)',
        '_render_targets(Gtk, pages["targets"], payload)',
        '_render_run_workflow(Gtk, pages["run"], payload)',
        '_render_settings(Gtk, pages["settings"], payload)',
    ]
    for call in old_bulk_calls:
        assert call not in source
    assert 'window.present()\n            GLib.idle_add(lambda: (request_refresh("startup"), False)[1])' in source


def test_phase3_hud_uses_height_safe_regions_and_sticky_actions() -> None:
    source = read(ROOT / "devtool" / "desktop" / "gtk_hud.py")
    assert "root.append(header)" in source
    assert "root.append(scroller)" in source
    assert "root.append(actions)" in source
    assert "Gtk.Expander" in source
    assert 'raw_expander.set_expanded(False)' in source
    assert "_append_stepper_row" in source
    assert "status_pill.set_valign(Gtk.Align.CENTER)" in source
    assert "status_pill.set_halign(Gtk.Align.END)" in source
    assert "set_ellipsize" in source
    assert "set_tooltip_text(display_title)" in source
    assert "devtool-summary-card" in source
    assert "devtool-stepper-panel" in source


def test_phase3_hud_removes_card_height_traps() -> None:
    source = read(ROOT / "devtool" / "desktop" / "gtk_hud.py")
    assert "set_min_content_height" not in source
    assert "set_size_request(176, 78)" not in source
    assert "set_size_request(178, 92)" not in source
    assert "set_size_request(140, 72)" not in source
    assert "GLib.timeout_add(poll_ms, lambda: (read(), True)[1])" not in source
    assert "GLib.timeout_add(poll_ms, read_safe)" in source


def test_phase3_hud_stepper_labels_are_honest() -> None:
    assert _human_step_status("skipped") == "Not run"
    assert _human_step_status("pending") == "Not run"
    assert _human_step_status("failed") == "Failed"
    assert _hud_status_chip_label("failed") == "● Failed"
    assert _hud_status_chip_label("ok") == "✓ Complete"
    assert _step_symbol("ok", "apply rollback") == "↺"
    assert _step_symbol("running", "build") == "●"
    assert _step_symbol("pending", "test") == "−"
