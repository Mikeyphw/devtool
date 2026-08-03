from __future__ import annotations

from pathlib import Path

from devtool.desktop.gtk_smoke import (
    GTK_WARNING_PATTERNS,
    control_center_startup_command,
    hud_startup_command,
    warning_lines,
)

ROOT = Path(__file__).resolve().parents[1]
GTK = ROOT / "desktop" / "gtk-control-center"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phase8_package_verifies_staged_imports_with_distro_python_and_stable_entry() -> None:
    package = read(GTK / "package-deb.sh")
    desktop = read(GTK / "packaging" / "io.github.mikeceead.DevtoolControlCenter.desktop")

    for token in (
        "PYTHONPATH=\"$verify_stage/usr/lib/python3/dist-packages\" /usr/bin/python3",
        "import gi",
        "import rich",
        "gi.require_version('Gtk', '4.0')",
        "gi.require_version('Adw', '1')",
        "from devtool.desktop.control_center_cli import main",
        "from devtool.desktop.gtk_hud_cli import main",
        "staged package imports must pass with /usr/bin/python3",
        "desktop entry must use the stable installed /usr/bin/devtool-control-center launcher",
    ):
        assert token in package
    assert "Exec=/usr/bin/devtool-control-center" in desktop
    assert "TryExec=/usr/bin/devtool-control-center" in desktop
    assert "#!/usr/bin/env python3" not in package


def test_phase8_installer_has_no_broken_clipboard_monitor_unit() -> None:
    installer = read(ROOT / "install.sh")
    assert "--with-clipboard-service" in installer
    assert "desktop clipboard monitor service is not implemented" in installer
    assert "unsupported --clipboard-monitor" in installer
    assert 'ExecStart="$clipboard_binary" --clipboard-monitor' not in installer
    assert "devtool-control-center-clipboard.service" in installer


def test_phase9_release_qualification_uses_real_gtk_startup_gates() -> None:
    source = read(ROOT / "devtool" / "release_qualification.py")
    matrix = read(ROOT / "release" / "qualification-matrix.json")
    workflow = read(ROOT / ".github" / "workflows" / "release-qualification.yml")
    readme = read(GTK / "README.md")

    for token in (
        "desktop-gtk-imports",
        "desktop-gtk-model-snapshot",
        "desktop-gtk-control-center-startup",
        "desktop-gtk-hud-startup",
    ):
        assert token in source
        assert token in matrix
    for token in ("run_gtk_startup_test", "control_center_startup_command", "hud_startup_command"):
        assert token in source
    assert "desktop-gtk-smoke" not in matrix
    assert "xvfb-run -a python3 -m devtool control-center --backend gtk4 --startup-test" in workflow
    assert "xvfb-run -a python3 -m devtool.desktop.gtk_hud_cli --startup-test" in workflow
    assert "`--once` is a model snapshot test" in readme
    assert "GTK/Adwaita warnings" in readme


def test_phase9_gtk_warning_filter_catches_reported_overflow() -> None:
    text = "(python3:138770): Adwaita-WARNING **: GtkBox exceeds AdwApplicationWindow height: requested 816 px, 722 px available"
    found = warning_lines(text)
    assert found == [text]
    assert any(pattern.search(text) for pattern in GTK_WARNING_PATTERNS)


def test_phase9_startup_commands_are_real_windows_not_once_snapshots(tmp_path: Path) -> None:
    control = control_center_startup_command(repo=tmp_path, python="/usr/bin/python3")
    hud = hud_startup_command(state_dir=tmp_path / "hud", python="/usr/bin/python3")
    assert "--startup-test" in control
    assert "--startup-test" in hud
    assert "--once" not in control
    assert "--once" not in hud
    assert control[:3] == ["/usr/bin/python3", "-m", "devtool"]
    assert hud[:3] == ["/usr/bin/python3", "-m", "devtool.desktop.gtk_hud_cli"]


def test_final_desktop_docs_cover_logs_recovery_shortcuts_and_package_requirements() -> None:
    readme = read(GTK / "README.md")
    release = read(ROOT / "docs" / "RELEASE_QUALIFICATION.md")
    for token in (
        "~/.local/state/devtool/control-center.log",
        "~/.local/state/devtool/hud.log",
        "Ctrl+R",
        "Ctrl+Q",
        "python3-gi",
        "gir1.2-gtk-4.0",
        "gir1.2-adw-1",
        "xvfb",
    ):
        assert token in readme
    assert "mapped-window GTK launch test" in release
    assert "exacts" not in release.lower()
