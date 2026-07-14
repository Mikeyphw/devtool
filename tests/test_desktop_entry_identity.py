from pathlib import Path


def test_system_desktop_entry_uses_installed_absolute_binary_and_stable_identity():
    root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
    desktop = (root / "packaging/io.github.mikeceead.DevtoolControlCenter.desktop").read_text(encoding="utf-8")
    assert "TryExec=/usr/bin/devtool-control-center" in desktop
    assert "Exec=/usr/bin/devtool-control-center" in desktop
    assert "StartupWMClass=devtool-control-center" in desktop
    assert "Icon=io.github.mikeceead.DevtoolControlCenter" in desktop


def test_generated_hud_entry_never_depends_on_literal_devtool_path():
    source = (Path(__file__).resolve().parents[1] / "devtool/hud.py").read_text(encoding="utf-8")
    assert "def _desktop_hud_exec_argv()" in source
    assert "shutil.which(\"devtool\")" in source
    assert "return [sys.executable, \"-m\", \"devtool\"" in source
    assert "Exec=devtool hud --mode desktop-window --follow-active" not in source
    assert "Icon={icon_path}" in source


def test_debian_package_refreshes_desktop_and_icon_caches():
    root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
    postinst = (root / "packaging/debian/postinst").read_text(encoding="utf-8")
    postrm = (root / "packaging/debian/postrm").read_text(encoding="utf-8")
    for source in (postinst, postrm):
        assert "update-desktop-database" in source
        assert "gtk-update-icon-cache" in source
