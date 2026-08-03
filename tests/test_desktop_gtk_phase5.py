from __future__ import annotations

import json
from pathlib import Path

from devtool.release_qualification import desktop_available, gtk4_available, qt_available, shell_entry_points, validate_version_consistency

ROOT = Path(__file__).resolve().parents[1]
GTK = ROOT / "desktop" / "gtk-control-center"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phase5_versions_and_release_matrix_are_gtk4_desktop_first() -> None:
    versions = json.loads(read(ROOT / "release" / "versions.json"))
    matrix = json.loads(read(ROOT / "release" / "qualification-matrix.json"))
    gates = {item["id"]: item for item in matrix["gates"]}

    assert versions["desktop"] == "0.5.0"
    assert versions["minimums"]["gtk"] == "4.0"
    assert versions["minimums"]["libadwaita"] == "1"
    assert "qt" not in versions["minimums"]
    assert validate_version_consistency(ROOT) == []
    assert "desktop-gtk-smoke" not in gates
    assert "desktop-gtk-imports" in gates
    assert "desktop-gtk-model-snapshot" in gates
    assert "desktop-gtk-control-center-startup" in gates
    assert "desktop-gtk-hud-startup" in gates
    assert "desktop-gtk-package" in gates
    assert "qt-configure" not in gates
    assert "qt-build" not in gates
    assert "qt-smoke" not in gates


def test_gtk4_package_script_stages_python_modules_launchers_and_no_qt_deps() -> None:
    package = read(GTK / "package-deb.sh")
    control = read(GTK / "packaging" / "debian" / "control")
    desktop = read(GTK / "packaging" / "io.github.mikeceead.DevtoolControlCenter.desktop")

    for token in (
        "usr/lib/python3/dist-packages/devtool",
        "devtool.desktop.control_center_cli",
        "devtool.desktop.gtk_hud_cli",
        "devtool-control-center-hud",
        "dpkg-deb --contents",
        "GTK package verification passed",
    ):
        assert token in package
    for token in ("python3-gi", "python3-rich", "gir1.2-gtk-4.0", "gir1.2-adw-1", "libgtk-4-1", "libadwaita-1-0"):
        assert token in control
    assert "qml-module" not in control
    assert "libqt5" not in control.lower()
    assert "Exec=/usr/bin/devtool-control-center" in desktop
    assert "StartupWMClass=io.github.mikeceead.DevtoolControlCenter" in desktop


def test_installer_uses_gtk4_desktop_fingerprint_and_package_script() -> None:
    installer = read(ROOT / "install.sh")
    assert "$ROOT/devtool/desktop" in installer
    assert "$ROOT/desktop/gtk-control-center" in installer
    assert "$ROOT/desktop/gtk-control-center/package-deb.sh" in installer
    assert "GTK4 desktop source fingerprint" in installer
    assert "desktop/devtool-control-center/CMakeLists.txt" not in installer


def test_release_qualification_uses_gtk4_desktop_gates_with_compatibility_aliases() -> None:
    source = read(ROOT / "devtool" / "release_qualification.py")
    assert "def gtk4_available" in source
    assert "desktop-gtk-smoke" not in source
    assert "desktop-gtk-model-snapshot" in source
    assert "desktop-gtk-control-center-startup" in source
    assert "desktop-gtk-hud-startup" in source
    assert "desktop-gtk-package" in source
    assert "gtk-control-center" in source and "package-deb.sh" in source
    assert "def desktop_available" in source
    assert "qt_available" in source  # compatibility API alias for old callers
    assert isinstance(gtk4_available(), bool)
    assert isinstance(desktop_available(), bool)
    assert isinstance(qt_available(), bool)
    assert all(path.is_file() for path in shell_entry_points(ROOT))


def test_phase_markdown_contract_tests_were_removed() -> None:
    stale_targets = [
        ROOT / "tests" / "test_ui_contract_finalization.py",
        ROOT / "tests" / "test_phase33_validation_release_seal.py",
    ]
    assert all(not path.exists() for path in stale_targets)
