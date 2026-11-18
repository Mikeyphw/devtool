"""Full C roadmap safety seal: UI source labels and explicit journaled authority.

Keep focused, grounded behavior checks in addition to existing detailed
negative, launcher, status, and Textual integration test suites.
"""
from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

import pytest

from devtool.textual_control_center import PANEL_AUTHORITIES, PAGE_ACTIONS
from devtool.textual_control_center_runtime import CanonicalControlActionDispatcher
from devtool.textual_github_view import create_github_workspace_widget


def test_github_workspace_describes_read_only_observation_and_mutation_controls():
    text = PANEL_AUTHORITIES["github"]
    assert "read-only observation" in text
    assert "confirmed dispatch, cancel, rerun" in text
    assert PAGE_ACTIONS["github"] == ()  # Controls belong to the embedded widget.
    source = inspect.getsource(create_github_workspace_widget)
    assert "read-only observation" in source
    assert "dispatch/cancel/rerun require confirmation" in source
    assert 'Button("Dispatch…", id="github-dispatch")' in source
    assert 'Button("Cancel run…", id="github-cancel")' in source
    assert 'Button("Rerun…", id="github-rerun")' in source
    assert 'RunControlModal' in source and 'WorkflowDispatchModal' in source


def test_unapproved_transaction_and_worker_controls_fail_before_mutating(tmp_path, monkeypatch):
    import devtool.textual_control_center_runtime as runtime
    monkeypatch.setattr(runtime, 'rollback_transaction', lambda *a, **k: pytest.fail('unapproved rollback'))
    monkeypatch.setattr(runtime, 'resume_transaction_result_publication',
                        lambda *a, **k: pytest.fail('unapproved recovery'))
    dispatcher=CanonicalControlActionDispatcher(tmp_path,
        popen_factory=lambda *a,**k: pytest.fail('unapproved worker spawned'))
    for action in ('rollback','recover'):
        ok,reason=dispatcher.dispatch(action,payload={'transaction_path':str(tmp_path/'tx')})
        assert not ok and 'confirmation' in reason.lower()
    ok,reason=dispatcher.dispatch('worker.recover',payload={'profile':'tablet'})
    assert not ok and 'confirmation' in reason.lower()


def test_explicit_confirmed_worker_uses_native_cli_and_private_unmodified_logs(tmp_path, monkeypatch):
    state=tmp_path.parent/(tmp_path.name+'-state')
    monkeypatch.setenv('XDG_STATE_HOME',str(state))
    # The child must NOT overwrite the caller's presentation preferences.
    monkeypatch.setenv('DEVTOOL_JOB_UI','full')
    monkeypatch.setenv('DEVTOOL_EXECUTION_PRESENTATION','interactive')
    started=[]
    class Proc:
        pid=32123
        def wait(self):return 0
    def start(command,**kwargs):
        started.append((command,kwargs))
        return Proc()
    dispatcher=CanonicalControlActionDispatcher(tmp_path,popen_factory=start)
    ok,reason=dispatcher.dispatch('worker.recover',payload={'profile':'tablet','approved':True})
    assert ok,reason
    assert len(started)==1
    argv,options=started[0]
    assert argv[:3]==[dispatcher.python_executable,'-m','devtool']
    assert argv[3:6]==['--no-clipboard','--ui','plain']
    assert argv[-3:]==['ssh','recover','tablet']
    assert '--yes' not in argv
    env=options['env']
    assert env['DEVTOOL_EXECUTION_PRESENTATION']=='interactive'
    assert env['DEVTOOL_JOB_UI']=='full'
    assert env['DEVTOOL_TEXTUAL_APPLY_HOST']=='0'
    assert env['DEVTOOL_TEXTUAL_EXECUTION_HOST']=='0'
    assert env['DEVTOOL_TEXTUAL_EXECUTION_CHILD']=='1'
    assert options['stdin'] is subprocess.DEVNULL
    assert options['stderr'] is subprocess.STDOUT
    assert options['stdout'] is not subprocess.DEVNULL
    logs=list((state/'devtool'/'control-center'/'requests').glob('*.log'))
    assert len(logs)==1 and logs[0].stat().st_mode & 0o077 == 0
    assert str(logs[0]) in reason


def test_confirmed_apply_uses_one_operator_approved_yes_without_clipboard(tmp_path,monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path.parent/(tmp_path.name+'-state')))
    launched=[]
    class Proc:
        pid=123
        def wait(self):return 0
    dispatcher=CanonicalControlActionDispatcher(tmp_path,popen_factory=lambda cmd,**kwargs:(launched.append((cmd,kwargs)) or Proc()))
    ok,_=dispatcher.dispatch('overlay.apply',payload={'artifact':'patch.zip'})
    assert not ok and not launched
    ok,_=dispatcher.dispatch('overlay.apply',payload={'artifact':'patch.zip','approved':True})
    assert ok and len(launched)==1
    argv,kw=launched[0]
    assert argv[-3:]==['--yes','apply-overlay','patch.zip']
    assert argv.count('--yes')==1
    assert '--no-clipboard' in argv and '--ui' in argv
    assert kw['env']['DEVTOOL_TEXTUAL_APPLY_CHILD']=='1'
    assert kw['env']['DEVTOOL_TEXTUAL_APPLY_HOST']=='0'


def test_real_textual_ui_hands_explicit_approval_to_worker_and_transaction_dispatcher():
    import devtool.textual_control_center_runtime as runtime
    src=inspect.getsource(runtime.create_textual_control_center_app)
    assert '"approved": True' in src
    assert '"worker.recover"' in src
    assert '"transaction_path": overlay.get("transaction_path", ""), "approved": True' in src


def test_opt_in_textual_subprocesses_use_supported_host_disables_only():
    from devtool import textual_apply_host, textual_execution_host
    for runtime in (textual_apply_host, textual_execution_host):
        src=inspect.getsource(runtime)
        assert 'child_env["DEVTOOL_EXECUTION_PRESENTATION"] = "plain"' not in src
        assert 'child_env["DEVTOOL_TEXTUAL_EXECUTION_HOST"] = "0"' in src
        assert 'child_env["DEVTOOL_TEXTUAL_APPLY_HOST"] = "0"' in src


@pytest.mark.parametrize('environment,expected', [
    ({},True),({'NO_COLOR':''},False),({'CLICOLOR':'0'},False),
    ({'TERM':'dumb'},False),({'TERM':'DUMB'},False),
    ({'FORCE_COLOR':'1'},False),({'CLICOLOR':'1','TERM':'xterm-256color'},True),
])
def test_managed_textual_children_only_force_color_when_capable(environment,expected):
    from devtool.native_terminal_policy import may_enable_child_ansi
    assert may_enable_child_ansi(environment) is expected
    from devtool import textual_apply_host,textual_execution_host
    for runtime in (textual_apply_host,textual_execution_host):
        code=inspect.getsource(runtime)
        assert 'if may_enable_child_ansi(child_env):' in code
        assert 'if "NO_COLOR" not in child_env and "FORCE_COLOR" not in child_env:' not in code
