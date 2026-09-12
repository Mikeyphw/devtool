from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from devtool.cli import main as devtool_main
from devtool.config import load_project_config, minimal_global_config_template, resolve_config_dict
from devtool.desktop.preferences import DesktopPreferences
from devtool.hud import event_icon
from devtool.tui_icons import nerd_font_enabled
from devtool.ui_core import (
    COPY_SCHEMA_VERSION,
    ICON_SCHEMA_VERSION,
    TOKEN_SCHEMA_VERSION,
    UI_SCHEMA_VERSION,
    action_spec,
    copy_text,
    icon_catalog,
    navigation_icon_map,
    profile_catalog,
    resolve_icon,
    resolve_ui_preferences,
    runtime_state_to_ui_snapshot,
    token_catalog,
)
from devtool.ui_core.preferences import UIPreferencesError, validate_ui_config


def _empty_global(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "missing-global.toml"
    monkeypatch.setenv("DEVTOOL_GLOBAL_CONFIG", str(path))
    for key in list(os.environ):
        if key.startswith("DEVTOOL_UI_"):
            monkeypatch.delenv(key, raising=False)
    return path


def test_builtin_profiles_and_surface_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _empty_global(monkeypatch, tmp_path)
    standard_tui = resolve_ui_preferences({}, surface="tui", env={})
    assert standard_tui.profile == "standard"
    assert standard_tui.density == "compact"
    assert standard_tui.sidebar == "auto"

    debug = resolve_ui_preferences({"profile": "debug"}, surface="desktop", env={})
    assert debug.profile == "debug"
    assert debug.verbosity == "debug"
    assert debug.timestamps == "absolute"
    assert debug.show_commands == "always"
    assert debug.show_environment == "always"

    minimal_hud = resolve_ui_preferences({"profile": "minimal"}, surface="hud", env={})
    assert minimal_hud.verbosity == "compact"
    assert minimal_hud.density == "dense"
    assert minimal_hud.max_steps == 6
    assert minimal_hud.show_completed_steps is False


def test_custom_profile_inheritance_surface_env_and_cli_precedence() -> None:
    ui = {
        "profile": "focused",
        "profiles": {
            "focused": {
                "extends": "developer",
                "show_completed_steps": False,
                "density": "dense",
            }
        },
        "density": "comfortable",
        "tui": {"density": "compact", "max_steps": 7},
    }
    prefs = resolve_ui_preferences(
        ui,
        surface="tui",
        env={"DEVTOOL_UI_ICONS": "ascii", "DEVTOOL_UI_VERBOSITY": "compact"},
        overrides={"density": "detailed"},
    )
    assert prefs.profile == "focused"
    assert prefs.show_environment == "always"  # inherited from developer
    assert prefs.show_completed_steps is False
    assert prefs.max_steps == 7
    assert prefs.icons == "ascii"
    assert prefs.verbosity == "compact"
    assert prefs.density == "detailed"
    assert prefs.provenance["density"] == "cli"
    assert prefs.provenance["icons"] == "environment"


def test_custom_profile_cycles_and_invalid_values_are_rejected() -> None:
    with pytest.raises(UIPreferencesError, match="inheritance cycle"):
        resolve_ui_preferences({
            "profile": "a",
            "profiles": {
                "a": {"extends": "b"},
                "b": {"extends": "a"},
            },
        })
    with pytest.raises(UIPreferencesError, match="Invalid UI density"):
        validate_ui_config({"density": "microscopic"})
    with pytest.raises(UIPreferencesError, match="Unknown UI preference"):
        validate_ui_config({"made_up": True})


def test_profile_catalog_includes_custom_profiles() -> None:
    catalog = profile_catalog({"profiles": {"focused": {"extends": "minimal", "max_steps": 2}}})
    names = {item["name"] for item in catalog}
    assert {"minimal", "standard", "developer", "debug", "focused"}.issubset(names)
    focused = next(item for item in catalog if item["name"] == "focused")
    assert focused["built_in"] is False
    assert focused["values"]["max_steps"] == 2
    assert focused["values"]["show_completed_steps"] is False


def test_semantic_token_icon_and_copy_registries_are_central() -> None:
    token_ids = {item["id"] for item in token_catalog()}
    assert {"status.success", "status.failed", "severity.warning", "action.destructive", "text.muted"}.issubset(token_ids)
    icon_ids = {item["id"] for item in icon_catalog()}
    assert {"status.success", "severity.error", "action.stop", "nav.dashboard", "nav.settings"}.issubset(icon_ids)
    assert TOKEN_SCHEMA_VERSION == 1
    assert ICON_SCHEMA_VERSION == 1
    assert COPY_SCHEMA_VERSION == 1

    assert action_spec("operation.cancel").label == copy_text("operation.cancel")
    assert action_spec("operation.cancel").notification_label == copy_text("operation.cancel", "notification")
    assert copy_text("nav.dashboard", surface="tui") == "Dashboard"
    assert copy_text("nav.dashboard", surface="desktop") == "Overview"


def test_icon_fallbacks_and_navigation_rendering_modes() -> None:
    assert resolve_icon("status.success", mode="unicode") == "✓"
    assert resolve_icon("status.success", mode="ascii") == "[OK]"
    assert resolve_icon("nav.settings", mode="native") == "emblem-system-symbolic"
    desktop = navigation_icon_map("desktop", mode="unicode")
    assert desktop["overview"] == "⌂"
    assert desktop["settings"] == "⚙"
    ascii_icons = navigation_icon_map("desktop", mode="ascii")
    assert ascii_icons["overview"] == "[HOME]"
    assert ascii_icons["settings"] == "[SET]"


def test_snapshot_carries_presentation_and_semantic_tokens(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _empty_global(monkeypatch, tmp_path)
    snapshot = runtime_state_to_ui_snapshot(
        {
            "status": "running",
            "command": "validate",
            "phase_progress": [
                {"name": "prepare", "status": "ok"},
                {"name": "tests", "status": "running", "current": 2, "total": 5},
            ],
            "diagnostics": [{"severity": "warning", "message": "watch this"}],
        },
        surface="hud",
        preferences={"profile": "debug", "hud": {"max_steps": 3}},
    ).as_dict()
    assert snapshot["schema_version"] == UI_SCHEMA_VERSION == 1  # UI01 contract stays compatible
    assert snapshot["operation"]["token"] == "status.running"
    assert snapshot["progress"][0]["token"] == "status.success"
    assert snapshot["progress"][1]["token"] == "status.running"
    assert snapshot["diagnostics"][0]["token"] == "severity.warning"
    assert snapshot["actions"][0]["token"] == "action.destructive"
    assert snapshot["presentation"]["profile"] == "debug"
    assert snapshot["presentation"]["density"] == "dense"
    assert snapshot["presentation"]["max_steps"] == 3
    assert snapshot["presentation"]["show_commands"] == "always"


def test_global_and_repository_ui_configuration_merge(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    global_path = tmp_path / "global.toml"
    global_path.write_text(
        """[ui]\nprofile = \"minimal\"\nicons = \"ascii\"\n[ui.desktop]\ntheme = \"dark\"\n""",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEVTOOL_GLOBAL_CONFIG", str(global_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = repo / ".devtool.toml"
    config_path.write_text(
        """[project]\nrunner = \"command\"\nplatform = \"custom\"\nbuild_command = \"printf build\"\n\n[ui]\nprofile = \"developer\"\n[ui.desktop]\ndensity = \"detailed\"\n""",
        encoding="utf-8",
    )
    config = load_project_config(str(repo), str(config_path))
    assert config.ui_config["profile"] == "developer"
    assert config.ui_config["icons"] == "ascii"
    assert config.ui_config["desktop"]["theme"] == "dark"
    assert config.ui_config["desktop"]["density"] == "detailed"
    resolved = resolve_config_dict(config)["ui"]
    assert resolved["resolved"]["desktop"]["profile"] == "developer"
    assert resolved["resolved"]["desktop"]["theme"] == "dark"
    assert resolved["resolved"]["desktop"]["density"] == "detailed"
    assert resolved["resolved"]["desktop"]["icons"] == "ascii"


def test_invalid_repository_ui_config_fails_fast(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _empty_global(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = repo / ".devtool.toml"
    config_path.write_text(
        """[project]\nrunner = \"command\"\n[ui]\ndensity = \"impossible\"\n""",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="Invalid UI configuration"):
        load_project_config(str(repo), str(config_path))


def test_global_config_template_documents_ui03_profiles_and_surface_overrides() -> None:
    text = minimal_global_config_template()
    assert "[ui]" in text
    assert 'profile = "auto"' in text
    assert "[ui.tui]" in text
    assert "[ui.hud]" in text
    assert "[ui.desktop]" in text
    assert "[ui.android]" in text
    assert "[ui.profiles.focused]" in text


def test_ui_contract_cli_reports_preferences_profiles_tokens_icons_and_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    _empty_global(monkeypatch, tmp_path)
    rc = devtool_main([
        "--ui-profile", "debug",
        "--ui-icons", "ascii",
        "ui-contract", "--surface", "desktop", "--json",
    ])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    envelope = json.loads(captured.out)
    data = envelope["data"]
    assert data["schema_version"] == 1
    assert data["preferences"]["profile"] == "debug"
    assert data["preferences"]["icons"] == "ascii"
    assert any(item["name"] == "developer" for item in data["profiles"])
    assert any(item["id"] == "status.success" for item in data["tokens"])
    assert any(item["id"] == "nav.dashboard" for item in data["icons"])
    assert any(item["id"] == "operation.cancel" for item in data["copy"])
    assert any(item["id"] == "operation.cancel" for item in data["actions"])
    assert any(item["id"] == "dashboard" for item in data["navigation"])


def test_desktop_legacy_preferences_map_to_shared_ui_preferences(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    global_path = tmp_path / "global.toml"
    global_path.write_text('[ui]\nprofile = "developer"\nicons = "ascii"\n', encoding="utf-8")
    monkeypatch.setenv("DEVTOOL_GLOBAL_CONFIG", str(global_path))
    prefs = DesktopPreferences(appearance="dark", compact_density=True, reduced_motion=True)
    resolved = prefs.resolved_ui_preferences()
    assert resolved.profile == "developer"
    assert resolved.icons == "ascii"
    assert resolved.theme == "dark"
    assert resolved.density == "compact"
    assert resolved.motion == "reduced"


def test_hud_and_tui_consume_shared_icon_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    global_path = tmp_path / "global.toml"
    global_path.write_text('[ui]\nicons = "ascii"\n', encoding="utf-8")
    monkeypatch.setenv("DEVTOOL_GLOBAL_CONFIG", str(global_path))
    monkeypatch.delenv("DEVTOOL_UI_ICONS", raising=False)
    assert event_icon({"event_type": "phase-finished", "status": "failed", "message": "failed"}) == "[ERR]"

    global_path.write_text('[ui]\nicons = "nerd"\n', encoding="utf-8")
    assert nerd_font_enabled() is True


def test_live_state_transports_resolved_repo_presentations_to_hud_and_android(tmp_path: Path) -> None:
    from devtool.hud import snapshot_from_directory
    from devtool.tasker import build_tasker_sink

    ui = {
        "profile": "developer",
        "icons": "ascii",
        "hud": {"max_steps": 9},
        "android": {"notification_detail": "detailed"},
    }
    presentations = {
        surface: resolve_ui_preferences(ui, surface=surface, env={}).as_dict()
        for surface in ("machine", "desktop", "tui", "hud", "android")
    }
    sink = build_tasker_sink(
        enabled=True,
        directory=str(tmp_path / "hud"),
        command="validate",
        repo_root=tmp_path,
        target="api",
        project_type="python",
        execution_environment="native-termux",
        ui_presentations=presentations,
    )
    assert sink is not None
    sink.started()
    raw = json.loads((tmp_path / "hud" / "state.json").read_text(encoding="utf-8"))
    assert raw["ui_presentations"]["hud"]["max_steps"] == 9
    assert raw["ui_presentations"]["android"]["notification_detail"] == "detailed"
    assert raw["ui"]["presentation"]["profile"] == "developer"

    snapshot = snapshot_from_directory(tmp_path / "hud", recent_events=1)
    assert snapshot["ui"]["presentation"]["profile"] == "developer"
    assert snapshot["ui"]["presentation"]["icons"] == "ascii"
    assert snapshot["ui"]["presentation"]["max_steps"] == 9


def test_zsh_completion_discovers_custom_ui_profiles() -> None:
    from devtool.completion import ZSH_COMPLETION

    assert "_devtool_ui_profiles()" in ZSH_COMPLETION
    assert "[ui\\.profiles\\." in ZSH_COMPLETION
    assert "--ui-profile[shared UI profile]:profile:_devtool_ui_profiles" in ZSH_COMPLETION
