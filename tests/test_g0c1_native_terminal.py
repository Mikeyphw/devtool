"""C1 terminal-first UX, opt-in Textual, and clipboard safety contract."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import io
import os
import subprocess

import pytest

from devtool.native_terminal_policy import explicit_textual_host_requested
from devtool.textual_apply_host import select_apply_textual_host
from devtool.textual_execution_host import select_execution_textual_host
from devtool.job_ui import job_ui_mode
from devtool.delivery_rich import terminal_rich_enabled
from devtool.cli import _apply_global_cli_defaults, _maybe_copy
from devtool.textual_control_center_runtime import CanonicalControlActionDispatcher


class TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def arg(command='build', **kw):
    data = dict(command=command, quiet=False, verbose=False, trace=False,
                stdout_payload='auto', json=False, _devtool_json=False, job_ui=None)
    data.update(kw)
    return SimpleNamespace(**data)


@pytest.mark.parametrize('command', ['build', 'validate', 'test', 'run'])
def test_normal_execution_defaults_to_native_with_textual_installed(command):
    decision = select_execution_textual_host(arg(command), env={},
        stdin=TTY(), stdout=TTY(), textual_probe=lambda: True)
    assert not decision.enabled
    assert 'native terminal' in decision.reason


def test_apply_defaults_to_native_but_explicit_compact_is_available():
    assert not select_apply_textual_host(arg('apply-overlay'), env={},
        stdin=TTY(), stdout=TTY(), textual_probe=lambda: True).enabled
    assert select_apply_textual_host(arg('apply-overlay', job_ui='compact'), env={},
        stdin=TTY(), stdout=TTY(), textual_probe=lambda: True).enabled
    assert select_execution_textual_host(arg('build', job_ui='full'), env={},
        stdin=TTY(), stdout=TTY(), textual_probe=lambda: True).enabled


def test_machine_and_pipe_cannot_start_textual_even_if_explicit():
    assert not select_apply_textual_host(arg('apply-overlay', job_ui='full'),
        env={}, stdin=TTY(), stdout=io.StringIO(), textual_probe=lambda: True).enabled
    assert not select_execution_textual_host(arg('build', job_ui='full'),
        universal_json=True, env={}, stdin=TTY(), stdout=TTY(), textual_probe=lambda: True).enabled


def test_plain_overrides_explicit_ui_and_no_color_only_affects_inline_rich():
    assert job_ui_mode({}) == 'plain'
    assert not explicit_textual_host_requested(arg('build', job_ui='full'),
        {'DEVTOOL_TEXTUAL_EXECUTION_HOST': '0'}, host_key='DEVTOOL_TEXTUAL_EXECUTION_HOST')
    assert not terminal_rich_enabled(env={'NO_COLOR':'1'}, stdin=TTY(), stdout=TTY())
    assert not terminal_rich_enabled(env={'CLICOLOR':'0'}, stdin=TTY(), stdout=TTY())
    assert not terminal_rich_enabled(env={'TERM':'dumb'}, stdin=TTY(), stdout=TTY())
    assert not terminal_rich_enabled(env={}, stdin=TTY(), stdout=io.StringIO())
    assert terminal_rich_enabled(env={}, stdin=TTY(), stdout=TTY())


def test_clipboard_is_never_prompted_or_copied_without_explicit_copy():
    args = SimpleNamespace(copy=False, no_clipboard=False, command='validate',
                           copy_via='auto')
    with patch('devtool.cli.Confirm.ask') as prompt, patch('devtool.cli.copy_text') as copy:
        with patch('devtool.cli.console.is_interactive', True), patch('sys.stdin.isatty', True):
            assert _maybe_copy('completed', 'success', '{"ok":true}', args) is None
    prompt.assert_not_called()
    copy.assert_not_called()


def test_persistent_legacy_copy_config_does_not_auto_copy(monkeypatch):
    from devtool import cli
    monkeypatch.setattr(cli, '_devtool_global_cli_config', lambda: {'output':{'copy':True}})
    args = SimpleNamespace(copy=False, no_clipboard=False, stdout_payload='auto',
                           machine_file='', copy_via='auto', theme='auto')
    _apply_global_cli_defaults(args, ['validate'])
    assert args.copy is False


def test_explicit_control_center_apply_needs_real_approval(tmp_path, monkeypatch):
    state = tmp_path.parent / (tmp_path.name + '-private-state')
    monkeypatch.setenv('XDG_STATE_HOME', str(state))
    calls=[]
    class Proc:
        pid=12345
        def wait(self): return 0
    def start(*argv, **kwargs):
        calls.append(kwargs)
        assert kwargs['stderr'] == subprocess.STDOUT
        assert kwargs['stdout'] != subprocess.DEVNULL
        return Proc()
    dispatcher = CanonicalControlActionDispatcher(tmp_path, popen_factory=start)
    ok, message = dispatcher.dispatch('overlay.apply', payload={'artifact':'demo.zip'})
    assert not ok and 'confirmation' in message.lower()
    assert not calls
    ok, message = dispatcher.dispatch('overlay.apply', payload={'artifact':'demo.zip', 'approved':True})
    assert ok, message
    assert len(calls)==1 and 'native output:' in message
    logs=list((state/'devtool'/'control-center'/'requests').glob('*.log'))
    assert len(logs)==1 and logs[0].stat().st_mode & 0o077 == 0


def test_control_center_symlinked_log_root_fails_closed(tmp_path, monkeypatch):
    shared = tmp_path/'public';shared.mkdir()
    link = tmp_path/'link';link.symlink_to(shared, target_is_directory=True)
    monkeypatch.setenv('XDG_STATE_HOME', str(link))
    dispatcher=CanonicalControlActionDispatcher(tmp_path, popen_factory=lambda *a,**k: pytest.fail('must not launch'))
    ok,_ = dispatcher.dispatch('overlay.apply', payload={'artifact':'demo.zip','approved':True})
    assert not ok


def test_pipeline_status_is_terminal_plain_unless_ui_is_explicit(tmp_path, monkeypatch, capsys):
    import devtool.actions_cli as actions
    monkeypatch.setattr(actions, 'actions_cli', lambda *a, **k: {'nodes': [], 'repository': 'Mikeyphw/devtool'})
    monkeypatch.setattr(actions, '_uxrich02_operator_view', lambda *a, **k: False)
    monkeypatch.setattr(actions.sys.stdout, 'isatty', lambda: True)
    monkeypatch.setattr(actions.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr('devtool.actions_presentation.pipeline_text', lambda *a, **k: 'NATIVE PIPELINE')
    monkeypatch.setattr('devtool.actions_pipeline_textual.run_pipeline_app',
                        lambda *a, **k: pytest.fail('status must not launch Textual'))
    args = SimpleNamespace(actions_command='pipeline', pipeline_command='status',
                           repo=str(tmp_path), json=False, job_ui=None)
    assert actions.run_actions_command(args) == 0
    assert 'NATIVE PIPELINE' in capsys.readouterr().out


def test_terminal_launch_cli_flag_is_explicit_and_native_default():
    from devtool.cli import build_parser
    parser=build_parser()
    native=parser.parse_args(['apply-overlay','demo.zip'])
    assert native.job_ui is None
    explicit=parser.parse_args(['--ui','plain','apply-overlay','demo.zip'])
    assert explicit.job_ui == 'plain'
    assert parser.parse_args(['--ui','compact','build']).job_ui == 'compact'


def test_legacy_explicit_copy_remains_available():
    args=SimpleNamespace(copy=True,no_clipboard=False,command='validate',copy_via='auto')
    from devtool.clipboard import ClipboardResult
    with patch('devtool.cli.copy_text',return_value=ClipboardResult(True,backend='test')) as copy:
        _maybe_copy('completed','success','{"ok":true}',args)
    copy.assert_called_once()
