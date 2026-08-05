from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROL_CENTER = ROOT / "devtool" / "desktop" / "gtk_control_center.py"
HUD = ROOT / "devtool" / "desktop" / "gtk_hud.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_control_center_uses_flat_shared_surfaces_and_navigation() -> None:
    source = read(CONTROL_CENTER)

    for token in (
        "devtool-app-header",
        "navigation-sidebar row:selected",
        "devtool-surface",
        "devtool-accent-surface",
        "devtool-metric-strip",
        "devtool-data-group",
        "devtool-flat-button",
        "devtool-primary-button",
        "devtool-nav-icon",
        "_PAGE_ICONS",
    ):
        assert token in source

    assert 'card.add_css_class("card")' not in source
    assert "set_size_request(150, 72)" not in source
    assert ".devtool-surface {\n      padding: 16px;\n      border: none;" in source


def test_run_page_is_a_split_setup_review_workspace() -> None:
    source = read(CONTROL_CENTER)

    for token in (
        "Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)",
        "Command setup",
        "Advanced project, test, environment, and HUD options",
        "Execution options",
        "Review & launch",
        "Live run output",
        "devtool-command-preview",
        "devtool-run-output",
    ):
        assert token in source


def test_hud_groups_metrics_and_pipeline_without_nested_card_borders() -> None:
    source = read(HUD)

    for token in (
        "devtool-hud-metrics",
        "devtool-hud-button",
        "devtool-hud-primary-button",
        ".devtool-summary-card {",
        ".devtool-stepper-panel {",
        "box-shadow: inset 3px 0 rgb(53, 132, 228)",
    ):
        assert token in source

    assert ".devtool-summary-card {\n      padding: 13px 14px;\n      border: none;" in source
    assert ".devtool-stepper-panel {\n      padding: 12px 13px;\n      border: none;" in source
    assert "border: 1px solid alpha(@window_fg_color, 0.08);" not in source


def test_promise_audit_closes_native_control_center_gaps() -> None:
    source = read(CONTROL_CENTER)

    for token in (
        'Gtk.ToggleButton(label="Compact nav")',
        "apply_compact_navigation",
        '"Appearance", "System-aware theme, contrast, density, and motion preferences"',
        'appearance_combo.append(value, label)',
        '"Compact density"',
        '"Diagnostics & evidence"',
        'evidence_tabs.append_page(diagnostics_scroll, Gtk.Label(label="Diagnostics"))',
        'evidence_tabs.append_page(evidence_scroll, Gtk.Label(label="Evidence"))',
    ):
        assert token in source


def test_promise_audit_closes_native_hud_gaps() -> None:
    source = read(HUD)

    for token in (
        '("compact", "Compact"), ("auto", "Auto"), ("expanded", "Expanded")',
        "apply_hud_size_mode",
        'Gtk.Label(label="Workspace lanes"',
        'Gtk.Button(label="Cancel")',
        'Gtk.Button(label="Force cancel")',
        "_request_ipc_cancel",
        "_force_cancel_state",
        "_can_force_cancel_state",
    ):
        assert token in source


def test_desktop_preferences_persist_flat_ui_modes(tmp_path: Path, monkeypatch) -> None:
    from devtool.desktop.preferences import DesktopPreferences

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    prefs = DesktopPreferences(
        compact_density=True,
        compact_navigation=True,
        hud_size_mode="expanded",
        appearance="light",
    )
    prefs.save("control-center")
    loaded = DesktopPreferences.load("control-center")

    assert loaded.compact_density is True
    assert loaded.compact_navigation is True
    assert loaded.hud_size_mode == "expanded"
    assert loaded.appearance == "light"
    assert DesktopPreferences.from_mapping({"hud_size_mode": "invalid"}).hud_size_mode == "auto"


def test_force_cancel_requires_verified_process_identity(monkeypatch) -> None:
    import devtool.desktop.gtk_hud as hud_module

    state = {
        "status": "running",
        "pid": 4242,
        "boot_id": "boot-a",
        "process_start": "start-a",
    }
    monkeypatch.setattr(hud_module, "_same_process_owner", lambda pid: pid == 4242)
    monkeypatch.setattr(hud_module, "_current_boot_id", lambda: "boot-a")
    monkeypatch.setattr(hud_module, "_process_start_token", lambda pid: "start-a")
    monkeypatch.setattr(hud_module.os, "kill", lambda pid, sig: None)

    assert hud_module._can_force_cancel_state(state) is True
    assert hud_module._can_force_cancel_state({**state, "process_start": "wrong"}) is False
    assert hud_module._can_force_cancel_state({**state, "boot_id": "other"}) is False
    assert hud_module._can_force_cancel_state({**state, "status": "completed"}) is False


def test_hud_graceful_cancel_uses_ipc_ack(tmp_path: Path) -> None:
    import json
    import os
    import socket
    import threading

    from devtool.desktop.gtk_hud import _request_ipc_cancel

    # AF_UNIX paths are commonly capped around 108 bytes. Devtool validates
    # overlays from deep transaction directories, so keep this fixture short.
    socket_path = Path("/tmp") / f"dt-hud-cancel-{os.getpid()}.sock"
    socket_path.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.listen(1)
    received: dict[str, object] = {}

    def serve() -> None:
        connection, _ = server.accept()
        with connection:
            line = connection.makefile("rb").readline()
            received.update(json.loads(line.decode("utf-8")))
            connection.sendall(b'{"type":"cancel_ack","run_id":"run-7","accepted":true}\n')
        server.close()
        socket_path.unlink(missing_ok=True)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    accepted, message = _request_ipc_cancel("run-7", socket_path=socket_path)
    thread.join(timeout=2)

    assert accepted is True
    assert received == {"type": "cancel", "run_id": "run-7", "reason": "Desktop HUD request"}
    assert "run-7" in message
