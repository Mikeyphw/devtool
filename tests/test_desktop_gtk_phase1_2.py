from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path
from unittest.mock import patch

from devtool.desktop.command_model import DevtoolCommandDraft, parse_environment_assignments, split_extra_arguments
from devtool.desktop.hud_store import load_hud_snapshot
from devtool.hud import choose_hud_mode, run_hud


ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_desktop_command_model_preserves_advanced_review_fields() -> None:
    draft = DevtoolCommandDraft(
        repository="/repo/demo",
        operation="test",
        devtool="/usr/bin/devtool",
        target="desktop",
        project_type="python",
        execution_environment="desktop-linux",
        test_profile="fast",
        selectors=["tests/test_a.py::test_ok"],
        tags=["ui"],
        exclude_tags=["slow"],
        copy_mode="json",
        auto_hud=True,
        hud_mode="desktop-window",
        yes=True,
        environment=parse_environment_assignments("DEVTOOL_TRACE=1 FOO='bar baz'"),
        extra_global_args=split_extra_arguments("--performance --machine-file out.json"),
        extra_operation_args=split_extra_arguments("--maxfail 1"),
    )

    argv = draft.argv()
    assert argv[:2] == ["/usr/bin/devtool", "--project-type"]
    assert "--execution-environment" in argv
    assert "--copy-json" in argv
    assert "--auto-hud" in argv
    assert "--yes" in argv
    assert argv[argv.index("--target") + 1] == "desktop"
    assert "--performance" in argv
    assert argv[argv.index("--machine-file") + 1] == "out.json"
    assert "test" in argv
    assert argv[argv.index("--profile") + 1] == "fast"
    assert argv[-2:] == ["--maxfail", "1"]
    command = draft.shell_command()
    assert "export DEVTOOL_TRACE='1'" in command or "export DEVTOOL_TRACE=1" in command
    assert "export FOO='bar baz'" in command
    assert "export DEVTOOL_CONTROL_CENTER_ACTIVE=1" in command


def test_gtk4_hud_is_desktop_window_default_and_legacy_qt_is_removed() -> None:
    with patch("devtool.desktop.gtk_hud.Gtk4CompactHud.available", return_value=True), \
         patch("devtool.hud.DesktopNotificationHud.available", return_value=True):
        assert choose_hud_mode("desktop") == "desktop-window"
        assert choose_hud_mode("gtk4-window") == "desktop-window"
        assert choose_hud_mode("gtk") == "desktop-window"
        assert choose_hud_mode("gtk-window") == "desktop-window"
        assert choose_hud_mode("qt-window") == "legacy-qt-removed"


def test_desktop_window_once_uses_gtk4_snapshot_renderer(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("DEVTOOL_NO_HUD", raising=False)
    (tmp_path / "state.json").write_text(json.dumps({"title": "demo", "text": "phase", "status": "running"}), encoding="utf-8")
    args = argparse.Namespace(
        tasker_dir=str(tmp_path),
        mode="desktop-window",
        host="127.0.0.1",
        port=8765,
        poll_interval=0.1,
        recent_events=2,
        once=True,
        follow_active=False,
        overlay=False,
        no_overlay=False,
        notify_id="devtool",
    )
    assert run_hud(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["renderer"] == "gtk4-window"
    assert payload["state"]["title"] == "demo"


def test_hud_store_wraps_canonical_snapshot_reader(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text(json.dumps({"title": "demo", "status": "ok"}), encoding="utf-8")
    snapshot = load_hud_snapshot(tmp_path, recent_events=1)
    assert snapshot.directory == tmp_path
    assert snapshot.state["title"] == "demo"
    assert snapshot.events == []


def test_gtk4_hud_module_requires_gtk4_not_gtk3() -> None:
    from devtool.desktop import gtk_hud

    source = inspect.getsource(gtk_hud.Gtk4CompactHud._require_gtk4)
    assert 'gi.require_version("Gtk", "4.0")' in source
    assert 'gi.require_version("Adw", "1")' in source
    assert 'gi.require_version("Gtk", "3.0")' not in source


def test_cli_hud_preserves_explicit_state_dir(tmp_path: Path, capsys, monkeypatch) -> None:
    from devtool.cli import main

    monkeypatch.delenv("DEVTOOL_NO_HUD", raising=False)
    (tmp_path / "state.json").write_text(json.dumps({"title": "explicit", "text": "dir", "status": "ok"}), encoding="utf-8")
    assert main(["hud", "--state-dir", str(tmp_path), "--mode", "desktop-window", "--once"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"]["title"] == "explicit"


def test_gtk_hud_console_script_registered() -> None:
    pyproject = read(ROOT / "pyproject.toml")
    assert 'devtool-gtk-hud = "devtool.desktop.gtk_hud_cli:main"' in pyproject
