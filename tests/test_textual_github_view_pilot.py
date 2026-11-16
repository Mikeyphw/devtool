"""Real Textual 8 Pilot interactions; no network or GitHub mutations."""
from __future__ import annotations

import asyncio
import importlib.util
import os
from pathlib import Path
import pytest


def _require_textual():
    if importlib.util.find_spec("textual") is None:
        if os.environ.get("DEVTOOL_GITHUB_TUI_REQUIRE_TEXTUAL") == "1":
            pytest.fail("Textual Pilot required but optional Textual dependency unavailable")
        pytest.skip("Textual optional on this host")


def fixture(selection):
    rid=selection.run_id
    return {
        "repository":"Mikeyphw/devtool","read_only":True,"release_qualified":False,
        "authentication":"provided-client",
        "workflows":[{"id":i,"name":f"Workflow {i}","state":"active","path":f".github/{i}.yml"}
                     for i in range(1,33)],
        "runs":[{"id":i,"workflow_id":(i%32)+1,"name":f"Run {i}","status":"completed",
                 "conclusion":"failure" if i==55 else "success","branch":f"feature/r{i}","sha":(f"{i:040x}")}
                for i in range(50,91)],
        "selected_run":({"id":rid,"status":"completed","conclusion":"failure",
                         "run_attempt":2,"sha":f"{rid:040x}","event":"push"} if rid else None),
        "jobs_scope":"exact-run-attempt" if rid else "none",
        "jobs":[{"id":rid*10,"name":"pytests","status":"completed","conclusion":"failure",
                 "steps":[{"name":"syntax","status":"completed","conclusion":"success"}]}] if rid else [],
        "runs_limit_reached":False,"jobs_limit_reached":False,
    }


@pytest.mark.parametrize("size",[(42,16),(64,22),(80,24),(120,35)])
def test_widget_real_pilot_selection_search_logs_and_geometry(tmp_path:Path,size):
    _require_textual()
    from textual.app import App, ComposeResult
    from textual.widgets import Button, DataTable, Input, Tree, RichLog
    from devtool.textual_github_view import create_github_workspace_widget

    calls=[]
    def loader(root,*,selection):
        calls.append(("fetch",selection.run_id))
        return fixture(selection)
    def logs(root,*,run_id,attempt,head_sha):
        calls.append(("logs",run_id,attempt))
        return {"run_id":run_id,"run_attempt":attempt,"sha":head_sha,
                "lines":["job secret-safe line", "test PASS"],"truncated":False}
    Workspace=create_github_workspace_widget(tmp_path,loader=loader,log_reader=logs,refresh_seconds=999)
    class Shell(App):
        def compose(self)->ComposeResult:
            yield Workspace(id="github")
    async def run():
        app=Shell()
        async with app.run_test(size=size) as pilot:
            if size[0]<72:
                app.screen.add_class("compact")
            if size[1]<24:
                app.screen.add_class("short")
            if size[0]<52 or size[1]<17:
                app.screen.add_class("tiny")
            await pilot.pause(0.25)
            assert app.query_one("#github-workflows",DataTable).row_count==15
            assert app.query_one("#github-runs",DataTable).row_count==16
            assert app.query_one("#github-log-lines",RichLog) is not None
            assert app.query_one("#github-jobs",Tree) is not None
            app.query_one("#github-runs",DataTable).move_cursor(row=0,column=0,animate=False)
            await pilot.pause(0.25)
            w=app.query_one("#github",Workspace)
            assert w._selected_run==50
            assert ("fetch",50) in calls
            assert "Exact attempt" in str(app.query_one("#github-jobs",Tree).root.label)
            app.query_one("#github-load-logs",Button).press()
            await pilot.pause(0.25)
            assert w._shown_logs==["job secret-safe line","test PASS"]
            assert ("logs",50,2) in calls
            # Switching runs must clear the old run's log lines before fetching.
            app.query_one("#github-runs",DataTable).move_cursor(row=1,column=0,animate=False)
            await pilot.pause(0.25)
            assert w._selected_run==51 and not w._shown_logs
            app.query_one("#github-search",Input).value="r52"
            await pilot.pause(0.08)
            assert app.query_one("#github-runs",DataTable).row_count==1
            app.query_one("#github-search",Input).value=""
            await pilot.pause(0.08)
            app.query_one("#github-runs-next",Button).press()
            await pilot.pause(0.08)
            assert w._run_page==1
            app.query_one("#github-workflows-next",Button).press()
            await pilot.pause(0.08)
            assert w._workflow_page==1
    asyncio.run(run())


def test_failure_preserves_last_known_and_never_reports_qualification(tmp_path:Path):
    _require_textual()
    from textual.app import App,ComposeResult
    from textual.widgets import Static
    from devtool.textual_github_view import create_github_workspace_widget
    failing=[False]
    def loader(root,*,selection):
        if failing[0]:raise RuntimeError("simulated network failure; hidden token")
        return fixture(selection)
    Workspace=create_github_workspace_widget(tmp_path,loader=loader,refresh_seconds=999)
    class Shell(App):
        def compose(self)->ComposeResult:yield Workspace(id="github")
    async def run():
        app=Shell()
        async with app.run_test(size=(80,24)) as pilot:
            await pilot.pause(0.3)
            w=app.query_one("#github",Workspace)
            assert w._snapshot["runs"]
            failing[0]=True
            w.request_refresh()
            await pilot.pause(0.25)
            assert w._snapshot["runs"]
            assert w._stale is True
            status=str(app.query_one("#github-status",Static).render())
            assert "STALE" in status and "NOT CI evidence" in status
            assert "hidden token" not in status
    asyncio.run(run())


def test_control_center_integration_palette_and_nav(tmp_path:Path,monkeypatch):
    _require_textual()
    from textual.widgets import ContentSwitcher,Input,ListView
    from devtool import textual_github_view as view
    from devtool.textual_control_center_runtime import (
        InMemoryControlCenterProvider, RecordingControlActionDispatcher,
        create_textual_control_center_app,
    )
    monkeypatch.setattr(view,"load_github_actions",lambda _root,*,selection:fixture(selection))
    app=create_textual_control_center_app(repo_root=tmp_path,page="dashboard",
        provider=InMemoryControlCenterProvider({"runs":[],"history":[],"diagnostics":[]}),
        dispatcher=RecordingControlActionDispatcher(),refresh_interval=0.3)
    async def run():
        async with app.run_test(size=(64,22)) as pilot:
            await pilot.press("a")
            await pilot.pause(0.25)
            assert app.query_one("#page-switcher",ContentSwitcher).current=="github"
            await pilot.press("ctrl+p")
            await pilot.pause(0.08)
            query=app.screen.query_one("#palette-search",Input)
            query.value="Github"
            await pilot.pause(0.08)
            assert len(app.screen.query_one("#palette-list",ListView).children)>0
            await pilot.press("escape")
            await pilot.pause()
    asyncio.run(run())


def test_remote_controls_require_confirmation_and_do_not_claim_success(tmp_path:Path):
    _require_textual()
    from textual.app import App,ComposeResult
    from textual.widgets import Button,DataTable,Input,Static
    from devtool.textual_github_view import create_github_workspace_widget
    calls=[]
    def run_control(root,*,run_id,attempt,head_sha,action):
        calls.append(("run",run_id,attempt,head_sha,action))
        return {"control_id":"a"*32,"state":"request-accepted"}
    def dispatch(root,*,workflow_id,ref,inputs):
        calls.append(("dispatch",workflow_id,ref,inputs))
        return {"request_id":"b"*32,"dispatched":True}
    Workspace=create_github_workspace_widget(tmp_path,loader=lambda root,*,selection:fixture(selection),
        run_controller=run_control,workflow_dispatcher=dispatch,refresh_seconds=999)
    class Shell(App):
        def compose(self)->ComposeResult:yield Workspace(id="github")
    async def run():
        app=Shell()
        async with app.run_test(size=(120,35)) as pilot:
            await pilot.pause(0.2)
            run_table=app.query_one("#github-runs",DataTable)
            run_table.move_cursor(row=0,column=0,animate=False)
            await pilot.pause(0.2)
            app.query_one("#github-cancel",Button).press()
            await pilot.pause(0.08)
            assert app.screen.__class__.__name__=="RunControlModal"
            assert calls==[]
            await pilot.click("#github-confirm-no")
            await pilot.pause(0.05)
            assert calls==[]
            app.query_one("#github-rerun",Button).press()
            await pilot.pause(0.06)
            await pilot.click("#github-confirm-yes")
            await pilot.pause(0.25)
            assert calls == [("run",50,2,f"{50:040x}","rerun")]
            w=app.query_one("#github",Workspace)
            assert "pending GitHub confirmation" in w._remote_notice
            workflow_table=app.query_one("#github-workflows",DataTable)
            workflow_table.move_cursor(row=1,column=0,animate=False)
            await pilot.pause(0.1)
            assert w._workflow==1
            app.query_one("#github-dispatch",Button).press()
            await pilot.pause(0.08)
            assert app.screen.__class__.__name__=="WorkflowDispatchModal"
            assert len(calls)==1
            app.screen.query_one("#github-dispatch-ref",Input).value="feature/source"
            app.screen.query_one("#github-dispatch-inputs",Input).value='{"debug":true}'
            await pilot.click("#github-dispatch-yes")
            await pilot.pause(0.25)
            assert calls[-1]==("dispatch",1,"feature/source",{"debug":True})
    asyncio.run(run())
