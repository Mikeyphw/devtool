from __future__ import annotations

import time
from pathlib import Path

from tests._compat import portable_shell_argv

from devtool.desktop.gtk_smoke import (
    SHORT_WORKAREA_GEOMETRY,
    constrained_layout_environment,
    run_gtk_constrained_layout_test,
)
from devtool.desktop.preferences import DesktopPreferences
from devtool.desktop.run_process import DesktopRunLauncher

ROOT = Path(__file__).resolve().parents[1]
GTK = ROOT / "devtool" / "desktop" / "gtk_control_center.py"
HUD = ROOT / "devtool" / "desktop" / "gtk_hud.py"
RUNTIME = ROOT / "devtool" / "desktop" / "gtk_runtime.py"
SMOKE = ROOT / "devtool" / "desktop" / "gtk_smoke.py"
RELEASE = ROOT / "devtool" / "release_qualification.py"
MATRIX = ROOT / "release" / "qualification-matrix.json"
WORKFLOW = ROOT / ".github" / "workflows" / "release-qualification.yml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_gap_run_launcher_keeps_stdout_and_stderr_distinct(tmp_path: Path) -> None:
    launcher = DesktopRunLauncher(state_dir=tmp_path / "runs")
    seen: list[tuple[str, str]] = []
    handle = launcher.start(
        portable_shell_argv("printf 'phase: compile\n'; printf 'boom error\n' >&2"),
        cwd=tmp_path,
        on_output=lambda channel, line: seen.append((channel, line)),
    )
    deadline = time.monotonic() + 5
    while handle.returncode is None and time.monotonic() < deadline:
        time.sleep(0.05)

    assert handle.returncode == 0
    assert handle.stdout_lines == ["phase: compile"]
    assert handle.stderr_lines == ["boom error"]
    assert ("stdout", "phase: compile") in seen
    assert ("stderr", "boom error") in seen
    assert handle.current_phase == "compile"
    assert handle.diagnostics[-1]["source"] == "stderr"
    assert "[stdout] phase: compile" in handle.log_path.read_text(encoding="utf-8")
    assert "[stderr] boom error" in handle.log_path.read_text(encoding="utf-8")


def test_gap_control_center_blocks_duplicate_launches_and_publishes_failures_to_hud() -> None:
    source = read(GTK)
    assert "duplicate launch blocked" in source
    assert "launch_button.set_sensitive(False)" in source
    assert "launch_button.set_sensitive(True)" in source
    assert "_write_desktop_run_hud_failure" in source
    assert "Desktop-launched Devtool run failed" in source
    assert "Live stdout/stderr are streaming" in source


def test_gap_preferences_cover_maximized_hud_details_filters_and_high_contrast(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    prefs = DesktopPreferences(
        window_width=1440,
        window_height=900,
        selected_page="diagnostics",
        window_maximized=True,
        hud_details_expanded=True,
        high_contrast=True,
    )
    prefs.set_filter_value("diagnostics", "severity", "error")
    prefs.set_filter_value("history", "query", "phase45")
    prefs.save("control-center")

    loaded = DesktopPreferences.load("control-center")
    assert loaded.window_maximized is True
    assert loaded.hud_details_expanded is True
    assert loaded.high_contrast is True
    assert loaded.filter_value("diagnostics", "severity") == "error"
    assert loaded.filter_value("history", "query") == "phase45"

    cc = read(GTK)
    hud = read(HUD)
    for token in (
        "window.is_maximized()",
        "preferences.window_maximized",
        "prefs.set_filter_value(\"diagnostics\", \"severity\"",
        "prefs.set_filter_value(\"history\", \"query\"",
        "devtool-high-contrast",
    ):
        assert token in cc
    for token in (
        "preferences.hud_details_expanded",
        "notify::expanded",
        "preferences.window_maximized",
        "devtool-high-contrast",
    ):
        assert token in hud


def test_gap_gtk_release_gates_have_constrained_layout_and_thread_guard() -> None:
    runtime = read(RUNTIME)
    smoke = read(SMOKE)
    release = read(RELEASE)
    matrix = read(MATRIX)
    workflow = read(WORKFLOW)

    assert "install_gtk_thread_guard" in runtime
    assert "GTK mutation from non-main thread" in runtime
    assert "DEVTOOL_GTK_THREAD_GUARD" in runtime
    assert SHORT_WORKAREA_GEOMETRY == "1024x722x24"
    assert constrained_layout_environment()["DEVTOOL_DESKTOP_ASSERT_CONSTRAINED_LAYOUT"] == "1"
    assert constrained_layout_environment()["DEVTOOL_GTK_THREAD_GUARD"] == "1"
    assert "run_gtk_constrained_layout_test" in smoke
    assert "desktop-gtk-control-center-constrained-layout" in release
    assert "desktop-gtk-hud-constrained-layout" in release
    assert "desktop-gtk-control-center-constrained-layout" in matrix
    assert "desktop-gtk-hud-constrained-layout" in matrix
    assert "1024x722x24" in workflow


def test_gap_constrained_layout_function_delegates_short_workarea(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_run(name, command, **kwargs):
        captured.update({"name": name, "command": command, **kwargs})
        class Probe:
            status = "passed"
            required = True
            passed = True
        return Probe()

    import devtool.desktop.gtk_smoke as gtk_smoke

    monkeypatch.setattr(gtk_smoke, "run_gtk_startup_test", fake_run)
    probe = run_gtk_constrained_layout_test(
        "demo",
        ["python3", "-m", "demo"],
        root=tmp_path,
        logs_dir=tmp_path / "logs",
        required=True,
    )
    assert probe.passed is True
    assert captured["geometry"] == "1024x722x24"
    assert captured["environment"]["DEVTOOL_GTK_THREAD_GUARD"] == "1"
