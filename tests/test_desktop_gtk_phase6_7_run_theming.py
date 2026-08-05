from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

from devtool.desktop.preferences import DesktopPreferences, preferences_path
from devtool.desktop.run_process import DesktopRunLauncher

ROOT = Path(__file__).resolve().parents[1]
GTK = ROOT / "devtool" / "desktop" / "gtk_control_center.py"
HUD = ROOT / "devtool" / "desktop" / "gtk_hud.py"
RUN_PROCESS = ROOT / "devtool" / "desktop" / "run_process.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phase6_run_launcher_executes_direct_argv_and_streams_redacted_output(tmp_path: Path) -> None:
    launcher = DesktopRunLauncher(state_dir=tmp_path / "runs")
    seen: list[str] = []
    handle = launcher.start(
        [sys.executable, "-c", "import os; print(os.environ['DEVTOOL_DESKTOP_RUN_ID']); print('token=SECRET123')"],
        cwd=tmp_path,
        environment={"EXTRA_VALUE": "ok"},
        on_output=seen.append,
    )
    deadline = time.monotonic() + 5
    while handle.returncode is None and time.monotonic() < deadline:
        time.sleep(0.05)

    assert handle.returncode == 0
    assert handle.run_id in "\n".join(seen)
    assert "token=<redacted>" in handle.log_path.read_text(encoding="utf-8")
    assert handle.argv[0] == sys.executable
    assert handle.environment == {"EXTRA_VALUE": "ok"}


def test_phase6_cancel_uses_process_group_and_force_cancel_path(tmp_path: Path) -> None:
    launcher = DesktopRunLauncher(state_dir=tmp_path / "runs")
    handle = launcher.start([sys.executable, "-c", "import time; time.sleep(20)"], cwd=tmp_path)
    try:
        assert launcher.cancel(handle, force=True) is True
        deadline = time.monotonic() + 5
        while handle.process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert handle.process.poll() is not None
        assert "requested_force_cancel=1" in handle.log_path.read_text(encoding="utf-8")
    finally:
        if handle.process.poll() is None:
            handle.process.kill()


def test_phase6_control_center_no_longer_launches_run_page_through_shell() -> None:
    source = read(GTK)
    assert "subprocess.Popen([\"sh\", \"-lc\", review.command]" not in source
    assert "launcher.start(" in source
    assert "review.argv" in source
    assert "Live run output" in source
    assert "DEVTOOL_DESKTOP_RUN_ID" in read(RUN_PROCESS)
    assert "start_new_session=True" in read(RUN_PROCESS)
    assert "Force cancel" in source


def test_phase7_preferences_persist_window_page_and_validate_values(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    prefs = DesktopPreferences(window_width=1300, window_height=800, selected_page="run", appearance="dark", reduced_motion=True)
    saved = prefs.save("control-center")
    loaded = DesktopPreferences.load("control-center")

    assert saved == preferences_path("control-center")
    assert loaded.window_width == 1300
    assert loaded.window_height == 800
    assert loaded.selected_page == "run"
    assert loaded.appearance == "dark"
    assert loaded.reduced_motion is True

    saved.write_text('{"window_width": 1, "window_height": 99999, "appearance": "bogus"}', encoding="utf-8")
    clamped = DesktopPreferences.load("control-center")
    assert clamped.window_width == 720
    assert clamped.window_height == 1800
    assert clamped.appearance == "system"


def test_phase7_gtk_sources_have_accessibility_shortcuts_reduced_motion_and_semantic_theme() -> None:
    cc = read(GTK)
    hud = read(HUD)
    for token in (
        "_set_accessible_label",
        "Gtk.AccessibleProperty.LABEL",
        "_install_app_shortcuts",
        "<Control>r",
        "<Control>q",
        "_prefers_reduced_motion",
        "DEVTOOL_REDUCED_MOTION",
        "DesktopPreferences.load(\"control-center\")",
        "preferences.save(\"control-center\")",
        "Adw.ColorScheme.FORCE_DARK",
        "rgb(53, 132, 228)",
    ):
        assert token in cc
    for token in (
        "DesktopPreferences.load(\"hud\")",
        "preferences.save(\"hud\")",
        "_set_accessible_label",
        "Adw.ColorScheme.FORCE_LIGHT",
        "Raw Devtool HUD event details",
    ):
        assert token in hud
    assert "#1c71d8" not in cc
