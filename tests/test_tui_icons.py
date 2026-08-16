from __future__ import annotations

from pathlib import Path

from devtool.tui_icons import (
    FALLBACK_ACTIVITY_ICON,
    NERD_FONT_ICONS,
    activity_icon,
    infer_project_language,
    nerd_font_enabled,
)


def _enabled_env(**extra: str) -> dict[str, str]:
    env = {"DEVTOOL_NERD_FONT": "1", "TERM": "xterm-256color", "LANG": "C.UTF-8"}
    env.update(extra)
    return env


def test_nerd_font_is_conservatively_disabled_for_plain_terminals() -> None:
    assert not nerd_font_enabled({"TERM": "dumb", "LANG": "C.UTF-8"})
    assert not nerd_font_enabled({"TERM": "xterm-256color", "LANG": "C", "DEVTOOL_ASCII": "1"})
    assert activity_icon(kind="python", env={"TERM": "dumb", "LANG": "C.UTF-8"}) == FALLBACK_ACTIVITY_ICON


def test_nerd_font_can_be_forced_and_disabled() -> None:
    assert nerd_font_enabled(_enabled_env())
    assert not nerd_font_enabled(_enabled_env(DEVTOOL_NO_NERD_FONT="1"))
    assert not nerd_font_enabled(_enabled_env(DEVTOOL_NERD_FONT="0"))


def test_activity_icon_uses_project_language_when_enabled() -> None:
    assert activity_icon(kind="python", env=_enabled_env()) == NERD_FONT_ICONS["python"]
    assert activity_icon(command="npm", env=_enabled_env()) == NERD_FONT_ICONS["node"]
    assert activity_icon(target="android_hud", env=_enabled_env()) == NERD_FONT_ICONS["android"]


def test_infer_project_language_from_files(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    assert infer_project_language(cwd=tmp_path) == "python"

    android = tmp_path / "android"
    android.mkdir()
    (android / "settings.gradle").write_text("pluginManagement {}\n", encoding="utf-8")
    (android / "build.gradle").write_text("plugins { id 'com.android.application' }\n", encoding="utf-8")
    assert infer_project_language(kind="gradle", cwd=android) == "android"

    rust = tmp_path / "rust"
    rust.mkdir()
    (rust / "Cargo.toml").write_text("[package]\nname='demo'\n", encoding="utf-8")
    assert infer_project_language(cwd=rust) == "rust"


def test_activity_icon_keeps_success_failure_icons_to_progress_renderers() -> None:
    # The helper only replaces the running-state diamond.  Success/failure icons
    # remain owned by each progress renderer, so this helper must never return
    # check or cross symbols for language keys.
    for key in ("python", "android", "rust", "node", "go", "java"):
        assert activity_icon(kind=key, env=_enabled_env()) not in {"✓", "✗"}
