"""Compact job presentation and legacy Rich backend retirement regression."""
from __future__ import annotations
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
import inspect
import pytest

from devtool.job_ui import job_ui_mode, run_job_app
from devtool.textual_execution_host import select_execution_textual_host
from devtool.textual_apply_host import select_apply_textual_host
from devtool.tui_textual import TextualAvailability, resolve_tui_backend_selection


class TTY:
    def isatty(self): return True


def test_compact_default_and_opt_in_modes():
    assert job_ui_mode({}) == "compact"
    assert job_ui_mode({"DEVTOOL_JOB_UI":"full"}) == "full"
    assert job_ui_mode({"DEVTOOL_JOB_UI":"plain"}) == "plain"


def test_job_run_selects_inline_without_catching_backend_errors():
    class App:
        def run(self, *, inline=False, inline_no_clear=False):
            return (inline, inline_no_clear)
    assert run_job_app(App(), mode="compact") == (True,True)
    assert run_job_app(App(), mode="full") == (False,False)
    class OldApp:
        def run(self): return "ok"
    assert run_job_app(OldApp(), mode="compact") == "ok"
    class BadApp:
        def run(self, *, inline=False): raise TypeError("runtime bug")
    with pytest.raises(TypeError,match="runtime bug"):
        run_job_app(BadApp(),mode="compact")


def test_host_routing_respects_plain_and_machine_modes():
    args=SimpleNamespace(command="build",quiet=False,verbose=False,trace=False,stdout_payload="auto",json=False)
    kw=dict(stdin=TTY(),stdout=TTY(),textual_probe=lambda: True)
    assert select_execution_textual_host(args,env={"DEVTOOL_JOB_UI":"compact"},**kw).enabled
    assert not select_execution_textual_host(args,env={"DEVTOOL_JOB_UI":"plain"},**kw).enabled
    assert not select_execution_textual_host(args,universal_json=True,env={},**kw).enabled
    overlay=SimpleNamespace(command="apply-overlay",quiet=False,verbose=False,trace=False,stdout_payload="auto",json=False)
    assert select_apply_textual_host(overlay,env={"DEVTOOL_JOB_UI":"compact"},**kw).enabled
    assert not select_apply_textual_host(overlay,env={"DEVTOOL_JOB_UI":"plain"},**kw).enabled


def test_rich_dashboard_module_is_absent_and_legacy_not_accepted():
    assert not Path(__file__).resolve().parents[1].joinpath("devtool/tui.py").exists()
    with pytest.raises(ValueError,match="removed"):
        resolve_tui_backend_selection(requested_backend="legacy",availability=TextualAvailability(True))


def test_compact_source_is_renderer_only_and_uses_canonical_queue():
    from devtool.job_ui import build_compact_execution_app
    source=inspect.getsource(build_compact_execution_app)
    assert "source._queue" in source
    assert 'kind == "snapshot"' in source
    assert 'kind == "line"' in source
    assert 'source._request_cancel()' in source
    assert "ProgressBar" in source and "RichLog" in source


def test_global_job_ui_flag_has_expected_choices():
    from devtool.cli import build_parser
    parser=build_parser()
    assert parser.parse_args(["--ui","compact","build"]).job_ui == "compact"
    assert parser.parse_args(["--ui","full","build"]).job_ui == "full"
    assert parser.parse_args(["--ui","plain","build"]).job_ui == "plain"
    with pytest.raises(SystemExit):
        parser.parse_args(["--ui","legacy","build"])


def test_progress_projection_never_invents_a_percentage():
    from devtool.job_ui import compact_job_projection
    unknown=compact_job_projection({"command":"build","phase_name":"Gradle", "completion_status":"running","detail":"Compiling"},width=40)
    assert unknown["percent"] is None
    assert unknown["finished"] is False
    assert "Gradle" in unknown["title"]
    failed=compact_job_projection({"command":"build", "completion_status":"failed","percent":None})
    assert failed["finished"] is True
    assert failed["normalized_status"] == "failed"
    assert failed["percent"] is None


def test_terminal_modes_keep_canonical_child_running_paths_distinct():
    from devtool.textual_apply_host import run_apply_textual_host
    from devtool.textual_execution_host import run_execution_textual_host
    import inspect
    assert "run_job_app" in inspect.getsource(run_apply_textual_host)
    assert "run_job_app" in inspect.getsource(run_execution_textual_host)
