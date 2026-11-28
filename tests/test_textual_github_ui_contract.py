"""Consolidated modern integration gates, keeping existing authority boundaries."""
from __future__ import annotations
import os

import inspect
from pathlib import Path


def test_canonical_tui_navigation_and_completion_have_exactly_one_actions_page():
    from devtool.ui_core.shortcuts import TUI_PAGES,TUI_NAV_SHORTCUTS,shortcut_collisions
    from devtool.ui_core.navigation import navigation_shortcut_map
    from devtool.textual_control_center import PAGES,PAGE_ACTIONS,PANEL_AUTHORITIES
    assert TUI_PAGES.count("github")==1
    assert TUI_NAV_SHORTCUTS["a"] == "github"
    assert navigation_shortcut_map("tui") == TUI_NAV_SHORTCUTS
    assert shortcut_collisions() == {}
    assert PAGES == TUI_PAGES
    assert PAGE_ACTIONS["github"] == ()
    assert "read-only observation" in PANEL_AUTHORITIES["github"]
    assert "confirmed dispatch, cancel, rerun" in PANEL_AUTHORITIES["github"]
    root=Path(__file__).resolve().parents[1]
    expected = "dashboard github runs logs diagnostics history targets workers validation artifacts overlays evidence build run actions github-actions log diagnostic worker ssh artifact overlay"
    for relative in ("devtool/completion.py","completions/_devtool"):
        text=(root/relative).read_text(encoding="utf-8")
        assert f"({expected})" in text


def test_single_shell_runtime_uses_isolated_actions_widget_with_no_implicit_post():
    from devtool import textual_control_center_runtime as runtime
    from devtool import textual_github_view, textual_github_workspace
    source=inspect.getsource(runtime.create_textual_control_center_app)
    assert 'yield GithubWorkspace(id="github")' in source
    assert 'create_github_workspace_widget(root)' in source
    assert '"github": "#github-runs"' in source
    assert "self._schedule_provider_refresh" in source
    widget_source=inspect.getsource(textual_github_view)
    projection_source=inspect.getsource(textual_github_workspace)
    assert 'run_mutation(' not in projection_source + widget_source
    assert '.dispatch(' not in projection_source + widget_source
    assert 'run_jobs(client, slug, expected)' in projection_source
    assert 'fetch_selected_run_logs(client, slug, identity)' in projection_source
    assert 'job_logs(client, slug, run, job)' in projection_source
    assert 'release_qualified": False' in projection_source
    assert 'canonical safe downloader' in widget_source


def test_native_launcher_preserves_user_ui_settings_without_clipboard():
    from devtool import launcher_contract_v3 as launcher
    env=launcher.safe_env()
    # C3 does not synthesize deprecated job/presentation selectors.
    assert env.get("DEVTOOL_EXECUTION_PRESENTATION") == os.environ.get("DEVTOOL_EXECUTION_PRESENTATION")
    assert env.get("DEVTOOL_JOB_UI") == os.environ.get("DEVTOOL_JOB_UI")
    assert env["DEVTOOL_TEXTUAL_APPLY_HOST"] == "0"
    assert env["DEVTOOL_TEXTUAL_EXECUTION_HOST"] == "0"
    native_source=inspect.getsource(launcher.native)
    assert "--no-clipboard" in native_source
