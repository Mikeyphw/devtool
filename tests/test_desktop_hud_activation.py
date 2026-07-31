from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GTK_HUD = ROOT / "devtool" / "desktop" / "gtk_hud.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_gtk4_hud_activation_uses_single_runtime_state_channel() -> None:
    source = read(GTK_HUD)
    assert "Gtk4CompactHud" in source
    assert "load_hud_snapshot" in source
    assert "follow_active" in source
    assert "renderer" in source
    assert "gtk4-window" in source


def test_legacy_qt_hud_activation_source_is_removed() -> None:
    assert not (ROOT / "desktop" / "devtool-control-center").exists()
    hud_source = read(ROOT / "devtool" / "hud.py")
    assert "devtool-control-center-hud" not in hud_source
    assert "QtCompactHud" not in hud_source
