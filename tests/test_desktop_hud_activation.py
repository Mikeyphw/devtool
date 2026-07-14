from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop" / "devtool-control-center"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_hud_activation_requires_acknowledgement_and_surfaces_failures() -> None:
    hud = read(DESKTOP / "src" / "HudMain.cpp")
    controller = read(DESKTOP / "src" / "AppController.cpp")
    main = read(DESKTOP / "src" / "main.cpp")
    assert 'waitForReadyRead(1000)' in hud
    assert 'socket->write("shown")' in hud
    assert 'activateHudWindow(window, true)' in hud
    assert 'Devtool HUD: window shown at' in hud
    assert 'QStandardPaths::findExecutable(QStringLiteral("devtool-control-center-hud"))' in controller
    assert 'Devtool HUD executable was not found' in controller
    assert 'Devtool HUD failed to start' in controller
    assert 'Devtool HUD executable was not found' in main


def test_hud_uses_reliably_mapped_top_level_window() -> None:
    qml = read(DESKTOP / "qml" / "CompactHud.qml")
    assert 'flags: Qt.Window | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint' in qml
    assert 'flags: Qt.Tool |' not in qml
