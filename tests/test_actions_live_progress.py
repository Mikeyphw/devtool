"""Live ACTIONS progress: authenticated observations, remote truth, Termux UI."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from devtool.actions_live_progress import (EVENT_PREFIX, decode_observation,
                                           encode_observation, project_observation)
from devtool.actions_runs import watch
from devtool.actions_cli import run_actions_command
from devtool.textual_execution_host import select_execution_textual_host


class TTY:
    def isatty(self):
        return True


def sample(conclusion=None, *, binding='matching'):
    return {
        'remote': {'id': 38319546064, 'repository': 'owner/repo',
                   'status': 'completed' if conclusion else 'in_progress',
                   'conclusion': conclusion, 'completed': bool(conclusion),
                   'html_url': 'https://github.com/owner/repo/actions/runs/38319546064'},
        'source_binding': binding,
        'jobs': [{'name': 'Android CI', 'status': 'completed' if conclusion else 'in_progress',
                  'conclusion': conclusion, 'steps': [
                      {'name': 'Run Gradle build', 'status': 'completed' if conclusion else 'in_progress',
                       'conclusion': conclusion}]}],
    }


def test_event_protocol_and_unknown_gradle_progress():
    item = sample()
    line = encode_observation(item)
    assert line.startswith(EVENT_PREFIX) and '\n' not in line
    restored = decode_observation(line)
    view, lines = project_observation(restored)
    assert view['percent'] is None and 'Gradle' in view['detail']
    assert view['completion_status'] == 'running'
    assert 'Android CI' in '\n'.join(lines)
    assert decode_observation(EVENT_PREFIX + '{oops') is None
    assert decode_observation(EVENT_PREFIX + '{}') is None
    assert decode_observation(EVENT_PREFIX + 'X'*150000) is None
    assert decode_observation('not an event') is None


def test_remote_failure_and_sha_mismatch_are_observations_not_qualification():
    view, lines = project_observation(decode_observation(encode_observation(sample('failure', binding='not-confirmed'))))
    assert view['completion_status'] == 'running'
    assert 'mismatch' in view['detail']
    assert any('failure' in line for line in lines)


def test_live_host_only_selects_watch_or_unsigned_dispatch():
    kw = dict(env={'DEVTOOL_JOB_UI': 'compact'}, stdin=TTY(), stdout=TTY(), textual_probe=lambda: True)
    def args(action, sub=''):
        return SimpleNamespace(command='actions', actions_command=action,
             android_command=sub, quiet=False, verbose=False, trace=False, json=False,
             stdout_payload='auto')
    assert select_execution_textual_host(args('watch'), **kw).enabled
    assert select_execution_textual_host(args('android', 'unsigned-run'), **kw).enabled
    assert not select_execution_textual_host(args('android', 'unsigned-plan'), **kw).enabled
    assert not select_execution_textual_host(args('logs'), **kw).enabled
    assert not select_execution_textual_host(args('watch'), universal_json=True, **kw).enabled
    assert not select_execution_textual_host(args('watch'), env={'DEVTOOL_JOB_UI': 'plain'}, stdin=TTY(), stdout=TTY(), textual_probe=lambda: True).enabled


@pytest.mark.parametrize('conclusion,source_binding,expected_rc', [
    ('success', 'matching', 0), ('failure', 'matching', 1),
    ('cancelled', 'matching', 1), ('success', 'not-confirmed', 1),
])
def test_watch_cli_exit_is_remote_truth(monkeypatch, capsys, conclusion, source_binding, expected_rc):
    monkeypatch.setenv('DEVTOOL_ACTIONS_STREAM', '1')
    observation = sample(conclusion, binding=source_binding)
    def fake(*args, observation_callback=None):
        observation_callback(observation)
        return {'complete': True, 'last': observation, 'checks': 1}
    monkeypatch.setattr('devtool.actions_cli.actions_cli', fake)
    args = SimpleNamespace(actions_command='watch', repo='/tmp', json=False, _devtool_json=False)
    assert run_actions_command(args) == expected_rc
    out = capsys.readouterr().out
    assert EVENT_PREFIX in out
    assert f'GitHub run 38319546064: {conclusion}' in out
    assert 'GitHub workflow qualified' not in out


def test_watch_polling_timeout_is_not_success(monkeypatch):
    monkeypatch.setenv('DEVTOOL_ACTIONS_STREAM', '1')
    def fake(*args, observation_callback=None):
        observation_callback(sample())
        return {'complete': False, 'last': sample(), 'checks': 2}
    monkeypatch.setattr('devtool.actions_cli.actions_cli', fake)
    assert run_actions_command(SimpleNamespace(actions_command='watch', repo='/tmp', json=False)) == 2


def test_streaming_without_live_ui_preserves_json_output(monkeypatch, capsys):
    monkeypatch.delenv('DEVTOOL_ACTIONS_STREAM', raising=False)
    monkeypatch.setattr('devtool.actions_cli.actions_cli', lambda *a, **k: {'phase':'ACTIONS-10', 'dispatched':True})
    assert run_actions_command(SimpleNamespace(actions_command='android', android_command='unsigned-run',
                                                json=True, repo='/tmp')) == 0
    assert json.loads(capsys.readouterr().out)['dispatched'] is True


def test_watch_fetches_jobs_only_for_live_observer():
    from devtool.actions_runs import snapshot
    class API:
        def get_run(self, slug, identity):
            return {'id': identity, 'repository':{'full_name':slug}, 'status':'completed',
                    'conclusion':'failure', 'run_attempt':1, 'head_sha':'a'*40}
        def run_jobs(self, slug, identity):
            return [{'id':99, 'name':'Gradle build', 'status':'completed','conclusion':'failure',
                     'steps':[{'name':'assemble','status':'completed','conclusion':'failure'}]}]
    updates=[]
    result=watch(API(),'owner/repo',99,None,None,max_checks=1,interval=1,
                 on_observation=updates.append)
    assert result['complete'] and result['last']['remote']['conclusion']=='failure'
    assert updates[0]['jobs'][0]['steps'][0]['conclusion']=='failure'
    assert 'jobs' not in watch(API(),'owner/repo',99,None,None,max_checks=1,interval=1)['last']


def test_unsigned_dispatch_live_monitor_uses_correlated_run(monkeypatch):
    import devtool.github_actions as gh
    called=[]
    args=SimpleNamespace(actions_command='android', android_command='unsigned-run', yes=True,
                         github_repo='owner/repo', auth='none', ref='main')
    monkeypatch.setattr(gh, 'resolve_repository', lambda e,r: 'owner/repo')
    monkeypatch.setattr(gh, 'resolve_token', lambda *a,**kw: (None,'anonymous'))
    monkeypatch.setattr('devtool.actions_unsigned.unsigned_dispatch',lambda *a,**kw:{
        'dispatch':{'request_id':'f'*32, 'run_id':99}, 'qualified_release':False})
    monkeypatch.setattr('devtool.actions_runs.select_run',lambda slug,identity,request_id,fallback:
                        (called.append(request_id) or 'owner/repo',99,None,None))
    monkeypatch.setattr('devtool.actions_runs.watch',lambda *a,**kw:
                       {'complete':True,'last':sample('failure'), 'checks':1})
    statuses=[]
    out=gh.actions_cli(args,Path.cwd(),observation_callback=lambda obj: statuses.append(obj['remote']['status']))
    assert statuses==['requested']
    assert called==['f'*32]
    assert out['monitor']['last']['remote']['conclusion']=='failure'
    assert out['qualified_release'] is False


def test_supplied_textual_renders_remote_at_phone_width():
    pytest.importorskip('textual')
    import asyncio
    from devtool.execution_presentation import create_textual_execution_source
    from devtool.job_ui import build_compact_execution_app
    source=create_textual_execution_source()
    app=build_compact_execution_app(source,remote=True)
    async def run():
        async with app.run_test(size=(34,11)) as pilot:
            source.update(project_observation(sample())[0])
            source.output_line('Job Android CI: in_progress')
            await pilot.pause(0.3)
            from textual.widgets import Static
            assert 'detach' in str(app.query_one('#job-help',Static).render())
            source.update({'command':'GitHub Actions','completion_status':'failed', 'detail':'Remote failed'})
            await pilot.pause(0.2)
            await pilot.press('q')
    asyncio.run(run())


def test_host_bridges_canonical_events_with_deduplicated_job_transitions(tmp_path):
    from devtool.textual_execution_host import ExecutionTextualHostSource
    from devtool.job_ui import activity_job_line
    args=SimpleNamespace(command='actions',actions_command='watch')
    source=ExecutionTextualHostSource([],args,cwd=tmp_path)
    event=encode_observation(sample())
    source._present_line(event)
    snapshot=source.presenter._queue.get_nowait()
    assert snapshot[0]=='snapshot' and 'GitHub Actions' == snapshot[1]['command']
    lines=[]
    while not source.presenter._queue.empty():
        lines.append(source.presenter._queue.get_nowait())
    assert any(kind=='line' and 'Gradle' in msg for kind,msg in lines)
    assert source.remote_run_id==38319546064
    assert activity_job_line('Job Android CI: in_progress')
    source._present_line(event)
    assert source.presenter._queue.get_nowait()[0]=='snapshot'
    assert source.presenter._queue.empty()  # unchanged steps never spam logs


def test_event_overflow_degrades_to_run_metadata_without_breaking_monitoring():
    obj=sample()
    obj['jobs']=[{'name':'long-'+'N'*200,'status':'in_progress',
        'steps':[{'name':'long-'+'S'*200,'status':'completed'} for _ in range(100)]}
        for _ in range(200)]
    data=decode_observation(encode_observation(obj))
    assert data['remote']['id']==38319546064
    assert len(data['jobs'])<=20


def test_duplicate_step_names_across_jobs_do_not_suppress_updates(tmp_path):
    from devtool.textual_execution_host import ExecutionTextualHostSource
    src = ExecutionTextualHostSource([], SimpleNamespace(command='actions'), cwd=tmp_path)
    event = sample()
    event['jobs'] = [
        {'name': 'Linux', 'status': 'in_progress', 'steps': [
            {'name': 'Build', 'status': 'completed', 'conclusion': 'success'}]},
        {'name': 'Android', 'status': 'in_progress', 'steps': [
            {'name': 'Build', 'status': 'in_progress', 'conclusion': None}]},
    ]
    src._present_line(encode_observation(event))
    output=[]
    while not src.presenter._queue.empty():
        output.append(src.presenter._queue.get_nowait())
    lines=[value for kind,value in output if kind=='line']
    assert '  Build: success' in lines
    assert '  Build: in_progress' in lines
    src._present_line(encode_observation(event))
    assert src.presenter._queue.get_nowait()[0] == 'snapshot'
    assert src.presenter._queue.empty()
    event['jobs'][1]['steps'][0]['status']='completed'
    event['jobs'][1]['steps'][0]['conclusion']='failure'
    src._present_line(encode_observation(event))
    output=[]
    while not src.presenter._queue.empty():
        output.append(src.presenter._queue.get_nowait())
    assert ('line', '  Build: failure') in output


def test_stream_projection_sanitizes_untrusted_status_and_step_text():
    event = sample()
    event['jobs'][0]['name'] = 'Build\x1b[31m\r\nspoof'
    event['jobs'][0]['steps'][0]['status'] = 'in_progress\nFAKE JOB'
    event['jobs'][0]['steps'][0]['name'] = 'Step\x00\x1b[2J'
    lines=project_observation(decode_observation(encode_observation(event)))[1]
    assert '\x1b' not in '\n'.join(lines)
    assert 'in_progress FAKE JOB' in '\n'.join(lines)
    assert '\nFAKE JOB' not in '\n'.join(lines)


def test_actual_managed_child_exit_preserves_final_remote_update_and_failure(tmp_path):
    import subprocess, sys, time
    from devtool.textual_execution_host import ExecutionTextualHostSource
    event = sample('failure')
    payload=encode_observation(event)
    script = 'import sys;print(sys.argv[1],flush=True);sys.exit(1)'
    def start_child(argv, **opts):
        return subprocess.Popen([sys.executable, '-c', script, payload], **opts)
    src = ExecutionTextualHostSource([], SimpleNamespace(command='actions'),
                                     cwd=tmp_path,popen_factory=start_child)
    src.start()
    assert src.wait(timeout=5) == 1
    src._bridge.join(timeout=5)
    assert not src._bridge.is_alive()
    assert src.remote_run_id == 38319546064
    assert src.remote_run_url.endswith('/38319546064')
    updates=[]
    while not src.presenter._queue.empty():
        updates.append(src.presenter._queue.get_nowait())
    assert any(kind == 'line' and 'Run Gradle build: failure' in value
               for kind, value in updates)
    assert updates[-1][0] == 'snapshot'
    assert updates[-1][1]['completion_status'] == 'failed'


def test_remote_detach_uses_local_signal_and_preserves_remote_status(tmp_path):
    import subprocess, sys
    from devtool.textual_execution_host import ExecutionTextualHostSource
    script = ('import signal,time,sys;'
              'signal.signal(signal.SIGINT,lambda *_:sys.exit(1));'
              'print("ready",flush=True);'
              'time.sleep(30)')
    def start_child(argv, **opts):
        return subprocess.Popen([sys.executable,'-c',script], **opts)
    src = ExecutionTextualHostSource([], SimpleNamespace(command='actions'),
                                     cwd=tmp_path,popen_factory=start_child)
    src.start()
    import time
    for _ in range(100):
        if not src._lines.empty() or src.returncode is not None:
            break
        time.sleep(0.02)
    assert src.running
    src.request_cancel()
    assert src.cancel_requested
    assert src.wait(timeout=5) == 1  # Python handler maps Ctrl+C to exit 1.
    src._bridge.join(timeout=5)
    updates=[]
    while not src.presenter._queue.empty():
        updates.append(src.presenter._queue.get_nowait())
    assert updates[-1][1]['phase_name'] == 'Detached'
    assert updates[-1][1]['completion_status'] == 'cancelled'
    assert 'workflow continues' in updates[-1][1]['detail']


def test_remote_host_reports_requested_detach_even_when_child_returns_one(monkeypatch, capsys):
    import devtool.textual_execution_host as host
    class FakeSource:
        remote = True
        cancel_requested = True
        running = False
        returncode = 1
        remote_run_url = 'https://github.com/owner/repo/actions/runs/99'
        run_id = 'local-test'
        env = {'DEVTOOL_JOB_UI': 'compact'}
        presenter = object()
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
        def log_path(self): return ''
    monkeypatch.setattr(host, 'ExecutionTextualHostSource', FakeSource)
    monkeypatch.setattr(host, 'build_compact_execution_app', lambda *a,**kw: object())
    monkeypatch.setattr(host, 'run_job_app', lambda *a,**kw: None)
    rc = host.run_execution_textual_host(['actions','watch'], SimpleNamespace(command='actions'))
    assert rc == 130
    stdout = capsys.readouterr().out
    assert 'Detached from remote GitHub monitoring' in stdout
    assert 'github.com/owner/repo/actions/runs/99' in stdout


def test_termux_compact_ctrl_c_twice_requests_local_detach():
    import asyncio
    pytest.importorskip('textual')
    from devtool.execution_presentation import create_textual_execution_source
    from devtool.job_ui import build_compact_execution_app
    source = create_textual_execution_source()
    callbacks=[]
    source.set_stop_callback(lambda: callbacks.append('local-detach'))
    app = build_compact_execution_app(source, remote=True)
    async def scenario():
        async with app.run_test(size=(32, 10)) as pilot:
            await pilot.press('ctrl+c')
            assert callbacks == []
            await pilot.press('ctrl+c')
            assert callbacks == ['local-detach']
            source.update({'command':'GitHub Actions','completion_status':'cancelled'})
            await pilot.pause(0.15)
            await pilot.press('q')
    asyncio.run(scenario())
