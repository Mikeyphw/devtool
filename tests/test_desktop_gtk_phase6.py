from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from devtool.cli import build_parser, main
from devtool.desktop import control_center_cli, gtk_hud_cli
from devtool.hud import choose_hud_mode, run_hud

ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_legacy_qt_qml_source_tree_is_deleted() -> None:
    assert not (ROOT / "desktop" / "devtool-control-center").exists()
    assert (ROOT / "desktop" / "gtk-control-center" / "package-deb.sh").is_file()


def test_hud_module_has_no_qt_or_gtk3_backend_classes() -> None:
    import devtool.hud as hud

    source = read(ROOT / "devtool" / "hud.py")
    assert not hasattr(hud, "QtCompactHud")
    assert not hasattr(hud, "GtkDesktopHud")
    assert 'gi.require_version("Gtk", "3.0")' not in source
    assert "devtool-control-center-hud" not in source
    assert choose_hud_mode("gtk-window") == "desktop-window"
    assert choose_hud_mode("qt-window") == "legacy-qt-removed"


def test_qt_hud_mode_returns_hard_error_without_legacy_launch(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("DEVTOOL_NO_HUD", raising=False)
    args = argparse.Namespace(
        tasker_dir=str(tmp_path),
        mode="qt-window",
        host="127.0.0.1",
        port=8765,
        poll_interval=0.1,
        recent_events=1,
        once=True,
        follow_active=False,
        overlay=False,
        no_overlay=False,
        notify_id="devtool",
    )
    assert run_hud(args) == 2
    assert "Legacy Qt desktop mode" in capsys.readouterr().err


def test_control_center_is_gtk4_only_and_no_binary_override() -> None:
    parser = build_parser()
    args = parser.parse_args(["control-center", "--backend", "gtk4", "--once"])
    assert args.backend == "gtk4"
    with __import__("pytest").raises(SystemExit):
        parser.parse_args(["control-center", "--backend", "qt"])
    with __import__("pytest").raises(SystemExit):
        parser.parse_args(["control-center", "--binary", "/opt/devtool-control-center"])


def test_control_center_once_still_outputs_gtk4_snapshot(tmp_path: Path, capsys, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".devtool.toml").write_text("[project]\nkind = \"python\"\n", encoding="utf-8")
    monkeypatch.delenv("DEVTOOL_NO_HUD", raising=False)
    assert main(["-r", str(repo), "control-center", "--backend", "gtk4", "--once"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["renderer"] == "gtk4-control-center"


def test_direct_gtk_scripts_report_runtime_errors_without_traceback(capsys) -> None:
    with patch("devtool.desktop.control_center_cli.Gtk4ControlCenter.available", return_value=True), \
         patch("devtool.desktop.control_center_cli.Gtk4ControlCenter.run", side_effect=RuntimeError("display denied")):
        assert control_center_cli.main([]) == 2
    assert "devtool-control-center: display denied" in capsys.readouterr().err

    with patch("devtool.desktop.gtk_hud_cli.Gtk4CompactHud.run", side_effect=RuntimeError("display denied")):
        assert gtk_hud_cli.main([]) == 2
    assert "devtool-gtk-hud: display denied" in capsys.readouterr().err


def test_shell_release_and_completion_references_are_gtk4_only() -> None:
    release = read(ROOT / "devtool" / "release_qualification.py")
    completion = read(ROOT / "devtool" / "completion.py")
    generated_completion = read(ROOT / "completions" / "_devtool")
    make_release = read(ROOT / "tools" / "make-release-candidate.sh")
    assert "desktop/devtool-control-center" not in release
    assert "desktop/devtool-control-center" not in make_release
    assert "--binary[override the legacy Qt" not in completion
    assert "qt-window" not in generated_completion
    assert "--backend[Control Center backend]:backend:(auto gtk4)" in completion
