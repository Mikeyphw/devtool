from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QML = ROOT / "desktop" / "devtool-control-center" / "qml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_window_roots_do_not_use_item_only_accessible_attachment() -> None:
    main = read(QML / "Main.qml")
    hud = read(QML / "CompactHud.qml")
    assert "Accessible.role: Accessible.Window" not in main
    assert "Accessible.role: Accessible.Window" not in hud
    assert 'Accessible.name: "Devtool Control Center"' in main
    assert 'Accessible.name: "Devtool live progress"' in hud


def test_hud_copy_shortcut_binds_all_standard_sequences() -> None:
    hud = read(QML / "CompactHud.qml")
    assert "sequences: [StandardKey.Copy]" in hud
    assert "sequence: StandardKey.Copy" not in hud


def test_repository_declares_explicit_root_project_kind() -> None:
    config = read(ROOT / ".devtool.toml")
    assert '[project]' in config
    assert 'kind = "python"' in config
    assert '[targets.android_hud]' in config


def test_component_icon_urls_resolve_from_qml_icons_directory() -> None:
    for name in ("SidebarButton.qml", "AppButton.qml"):
        source = read(QML / "components" / name)
        assert 'Qt.resolvedUrl("../icons/" + basename)' in source
