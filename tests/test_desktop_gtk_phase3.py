from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from devtool.cli import main
from devtool.desktop.control_center_cli import build_parser
from devtool.desktop.control_center_model import DesktopControlCenterModel
from devtool.desktop.gtk_control_center import Gtk4ControlCenter


ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def make_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".devtool.toml").write_text(
        """
[project]
kind = "python"

[targets.desktop]
kind = "python"
root = "."
build_command = "python -m compileall -q devtool"
test_command = "pytest tests/test_desktop_gtk_phase3.py"
execution_environment = "desktop-linux"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / "hud"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "title": "validate",
                "status": "running",
                "percent": 42,
                "target": "desktop",
                "phase": {"name": "unit-test"},
                "updated_at": "2026-07-31T13:00:00Z",
                "results": [
                    {
                        "name": "lint",
                        "issues": [
                            {"severity": "warning", "code": "W1", "message": "sample warning", "file": "demo.py"}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (state_dir / "events.jsonl").write_text(
        json.dumps({"type": "warning", "message": "event warning", "phase": "lint"}) + "\n",
        encoding="utf-8",
    )
    history_dir = repo / ".devtool" / "artifacts.d"
    history_dir.mkdir(parents=True)
    (history_dir / "entry.json").write_text(
        json.dumps({"history_id": "h1", "artifact": "overlay.zip", "applied_at": "2026-07-31T13:10:00Z"}),
        encoding="utf-8",
    )
    return repo, state_dir


def test_control_center_model_reads_desktop_only_surfaces(tmp_path: Path) -> None:
    repo, state_dir = make_repo(tmp_path)
    payload = DesktopControlCenterModel(
        repository=repo,
        target="desktop",
        state_dir=state_dir,
        ipc_socket=tmp_path / "missing.sock",
    ).snapshot().as_dict()

    assert payload["repository"]["path"] == str(repo.resolve())
    assert payload["project"]["kind"] == "python"
    assert payload["project"]["selected_target"] == "desktop"
    assert payload["targets"][0]["name"] == "desktop"
    assert payload["targets"][0]["environment"] == "desktop-linux"
    assert payload["active_runs"][0]["title"] == "validate"
    assert payload["active_runs"][0]["phase"] == "unit-test"
    assert payload["history"][0]["artifact"] == "overlay.zip"
    assert {item["message"] for item in payload["diagnostics"]} >= {"sample warning", "event warning"}


def test_gtk_control_center_once_prints_read_only_snapshot(tmp_path: Path, capsys: object) -> None:
    repo, state_dir = make_repo(tmp_path)
    exit_code = Gtk4ControlCenter(history_limit=5, recent_events=3).run(
        repository=repo,
        target="desktop",
        state_dir=state_dir,
        once=True,
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["renderer"] == "gtk4-control-center"
    assert payload["project"]["kind"] == "python"
    assert payload["active_runs"][0]["status"] == "running"


def test_devtool_control_center_once_routes_to_gtk4_snapshot(tmp_path: Path, capsys: object, monkeypatch) -> None:
    repo, state_dir = make_repo(tmp_path)
    monkeypatch.delenv("DEVTOOL_NO_HUD", raising=False)
    assert main(["-r", str(repo), "--target", "desktop", "control-center", "--once", "--state-dir", str(state_dir)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["renderer"] == "gtk4-control-center"
    assert payload["project"]["selected_target"] == "desktop"
    assert payload["run_workflow"]["selected_target"] == "desktop"


def test_control_center_cli_exposes_gtk4_shell_options() -> None:
    parser = build_parser()
    args = parser.parse_args(["--backend", "gtk4", "--page", "run", "--once", "-r", "/tmp/repo"])
    assert args.backend == "gtk4"
    assert args.page == "run"
    assert args.once is True


def test_control_center_backend_auto_prefers_gtk4_when_available(tmp_path: Path, monkeypatch) -> None:
    from devtool.desktop.control_center_cli import main as control_center_main

    repo, state_dir = make_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    with patch("devtool.desktop.gtk_control_center.Gtk4ControlCenter.available", return_value=True), \
         patch("devtool.desktop.gtk_control_center.Gtk4ControlCenter.run", return_value=0) as run:
        assert control_center_main(["-r", str(repo), "--state-dir", str(state_dir)]) == 0
    assert run.call_args.kwargs["repository"] == repo
    assert run.call_args.kwargs["once"] is False


def test_cli_control_center_options_and_scripts_are_registered() -> None:
    pyproject = read(ROOT / "pyproject.toml")
    cli = read(ROOT / "devtool" / "cli.py")
    completion = read(ROOT / "devtool" / "completion.py")

    assert 'devtool-control-center = "devtool.desktop.control_center_cli:main"' in pyproject
    assert 'devtool-gtk-control-center =' not in pyproject
    from devtool.desktop.control_center_cli import build_parser as build_desktop_parser
    backend = build_desktop_parser().parse_args(["--backend", "gtk4"])
    assert backend.backend == "gtk4"
    assert 'Gtk4ControlCenter.available()' in cli
    assert 'launch_legacy_qt_control_center' not in read(ROOT / "devtool" / "desktop" / "gtk_control_center.py")
    assert '--page[initial GTK4 shell page]' in completion
    assert 'overview runs history diagnostics repositories targets run settings' in completion


def test_gtk_control_center_source_is_gtk4_and_contains_phase4_run_page() -> None:
    source = read(ROOT / "devtool" / "desktop" / "gtk_control_center.py")
    assert 'gi.require_version("Gtk", "4.0")' in source
    assert 'gi.require_version("Adw", "1")' in source
    assert 'gi.require_version("Gtk", "3.0")' not in source
    assert "def _render_run_workflow" in source
    assert "Launch" in source
    assert "review_payload(payload_from_mapping" in source
    assert "subprocess.Popen" in source


def test_phase3_docs_and_desktop_assets_exist() -> None:
    docs = read(ROOT / "docs" / "DESKTOP_GTK_MIGRATION.md")
    readme = read(ROOT / "desktop" / "gtk-control-center" / "README.md")
    desktop_file = read(ROOT / "desktop" / "gtk-control-center" / "packaging" / "io.github.mikeceead.DevtoolControlCenter.desktop")
    metainfo = read(ROOT / "desktop" / "gtk-control-center" / "packaging" / "io.github.mikeceead.DevtoolControlCenter.metainfo.xml")

    assert "Phase 3: GTK4 Control Center read-only shell" in docs
    assert "Phase 4: GTK4 Run workflow" in docs
    assert "devtool control-center --backend gtk4 --page run" in docs
    assert "Run page builds a typed" in readme
    assert "Exec=/usr/bin/devtool-control-center" in desktop_file
    assert "io.github.mikeceead.DevtoolControlCenter" in metainfo

