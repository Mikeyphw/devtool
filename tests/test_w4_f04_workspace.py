"""F04 native workspace acceptance: real rendering and provider read boundaries."""
from __future__ import annotations
import io
import json
import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from rich.console import Console
from rich.cells import cell_len
from devtool import operator_workspace as workspace
from devtool import workspace_actions as actions
from devtool.workspace_viewport import LogCursor, log_page
from devtool.github_actions import GitHubActionsClient, GitHubActionsError

HEAD='a'*40
REPO='Mikeyphw/devtool'
WF={'id':42,'name':'Source qualification','path':'.github/workflows/source.yml','state':'active'}
RUN={'id':23,'repository':{'full_name':REPO},'workflow_id':42,'head_sha':HEAD,'run_attempt':2,'event':'pull_request','status':'completed','conclusion':'failure'}
JOB={'id':89,'run_id':23,'run_attempt':2,'name':'Build tests','status':'completed','conclusion':'failure','steps':[{'number':1,'name':'Prepare','status':'completed','conclusion':'success'},{'number':2,'name':'Test','status':'completed','conclusion':'failure'}]}

def make_workspace(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,'_source_head',lambda root:HEAD)
    return workspace.Workspace(tmp_path,HEAD,slug=REPO,auth_mode='none')

def render(ws,w,h):
    out=io.StringIO();Console(file=out,width=w,height=h,color_system=None).print(ws.current_view(width=w,height=h))
    return out.getvalue().splitlines()

def finish(ws):
    end=time.monotonic()+2
    while time.monotonic()<end:
        if ws.consume_actions():return
        time.sleep(.002)
    pytest.fail('remote worker timeout')

@pytest.mark.parametrize('w',[28,40,60,80,120])
@pytest.mark.parametrize('h',[8,12,19,24,32])
def test_physical_viewport_and_selected_item_visible(tmp_path,monkeypatch,w,h):
    ws=make_workspace(tmp_path,monkeypatch)
    ws.remote_detail={'kind':'workflows','items':[{'name':f'Workflow {i:02d}','id':i+1,'state':'active'} for i in range(51)]}
    ws.remote_selected=34
    rows=render(ws,w,h)
    assert len(rows)<=h
    assert all(cell_len(row)<=w for row in rows)
    assert 'q detach' in rows[-1]
    assert any('› ACTIVE · Workflow 34' in row for row in rows)
    ws.press('j');assert ws.remote_selected==35
    assert any('Workflow 35' in row for row in render(ws,w,h))


def test_large_log_pages_fold_only_visible_lines_and_keep_anchor():
    class Lines:
        reads=[]
        def __len__(self):return 50000
        def __getitem__(self,index):
            self.reads.append(index)
            return 'abcdefghij'*10000 if index==0 else 'END'
    lines=Lines()
    page,cursor=log_page(lines,LogCursor(),40,8)
    assert len(page)==8 and cursor==LogCursor(0,320)
    assert set(lines.reads)=={0}
    resized,next_cursor=log_page(lines,cursor,60,5)
    assert ''.join(resized)==('abcdefghij'*10000)[320:620]
    assert next_cursor==LogCursor(0,620)


def test_logs_and_detail_page_reach_end_and_back(tmp_path,monkeypatch):
    ws=make_workspace(tmp_path,monkeypatch)
    lines=('a'*120,'é界'*38,'last source line')
    ws.remote_detail={'kind':'job-logs','log_lines':lines,'read_only':True}
    ws.current_view(width=28,height=10)
    seen=[]
    from devtool.workspace_viewport import page_capacity
    for _ in range(40):
        parts,next_cursor=log_page(lines,ws.cursor,28,page_capacity(10,False,False));seen.extend(parts)
        if next_cursor is None:break
        ws.press(']');assert ws.cursor==next_cursor
    assert ''.join(seen)==''.join(lines)
    current=ws.cursor;ws.press('?');ws.press(']');ws.press('b');assert ws.cursor==current
    ws.press('[');assert ws.cursor!=current


def fixture_client():
    client=GitHubActionsClient('test-only')
    seen=[]
    def get(path):
        seen.append(path)
        endpoint=path.split('?',1)[0]
        if endpoint.endswith('/workflows/42'):return WF.copy()
        if endpoint.endswith('/workflows/42/runs'):return {'workflow_runs':[RUN.copy()]}
        if endpoint.endswith('/runs/23/attempts/2/jobs'):return {'jobs':[JOB.copy()]}
        if endpoint.endswith('/runs/23'):return RUN.copy()
        raise AssertionError(path)
    client._get=get
    return client,seen


def test_real_native_read_adapter_routes_exact_workflow_run_job_logs(tmp_path,monkeypatch):
    client,seen=fixture_client()
    result=actions.workflow_runs(client,REPO,WF)
    run=result['items'][0]
    assert run['id']==23 and run['run_attempt']==2
    jobs=actions.run_jobs(client,REPO,run)
    assert jobs['items'][0]['id']==89
    assert any('/workflows/42/runs?' in p for p in seen)
    assert any('/runs/23/attempts/2/jobs?' in p for p in seen)
    from devtool import actions_runs
    raw=b'first\n' + b'long '*300 + b'\nTOKEN=ghp_abcdefghijklmnopqrstuvwxyz1234567890\nlast\n'
    monkeypatch.setattr(actions_runs,'_state_root',lambda:tmp_path/'state'/'receipts')
    requested=[]
    class Response(io.BytesIO):status=200
    class Opener:
        def open(self,req,timeout):
            requested.append(req);return Response(raw)
    client._opener=Opener()
    client.job_log_location=lambda slug,job_id: ('https://example.blob.core.windows.net/log?sig=private' if (slug,job_id)==(REPO,89) else pytest.fail('wrong job'))
    logs=actions.job_logs(client,REPO,run,jobs['items'][0])
    assert logs['log_lines'][0]=='first' and logs['log_lines'][-1]=='last'
    assert len(logs['log_lines'][1])==1500
    assert 'ghp_abcdefghijklmnopqrstuvwxyz1234567890' not in ''.join(logs['log_lines'])
    assert requested[0].get_method()=='GET' and not requested[0].get_header('Authorization')
    path,=list((tmp_path/'state'/'remote-logs').glob('*.log'))
    assert path.read_bytes()==raw and path.stat().st_mode&0o077==0
    client.job_log_location=lambda *a:'https://wrong.invalid/log'
    with pytest.raises(GitHubActionsError):actions.job_logs(client,REPO,run,jobs['items'][0])
    assert len(requested)==1


@pytest.mark.parametrize('mutation',[{'run_id':24},{'run_attempt':1},{'steps':[{'number':1},{'number':1}]},{'steps':[{'number':True}]},{'id':False}])
def test_job_identity_and_steps_fail_closed(mutation):
    client,_=fixture_client();run=actions.workflow_runs(client,REPO,WF)['items'][0]
    client.run_attempt_jobs=lambda *a:[{**JOB,**mutation}]
    with pytest.raises(GitHubActionsError):actions.run_jobs(client,REPO,run)


def test_workflow_navigation_and_drilldown_no_implicit_get(tmp_path,monkeypatch):
    ws=make_workspace(tmp_path,monkeypatch);client,seen=fixture_client()
    monkeypatch.setattr(actions,'workspace_client',lambda *a:client)
    ws.remote_detail={'kind':'workflows','items':[WF,{**WF,'id':43,'name':'Second'}]}
    ws.press('j');assert ws.remote_selected==1
    ws.press('k');assert ws.remote_selected==0
    ws.press('\n');ws.press('b');assert seen==[]
    ws.press('g');finish(ws);assert ws.remote_detail['kind']=='workflow-runs'
    ws.press('g');finish(ws);assert ws.remote_detail['kind']=='run-jobs'
    count=len(seen);ws.press('\n');assert ws.remote_detail['kind']=='job-steps'
    ws.press('j');assert ws.remote_selected==1
    ws.press('b');assert ws.remote_detail['kind']=='run-jobs'
    ws.press('b');assert ws.remote_detail['kind']=='workflow-runs'
    ws.press('b');assert ws.remote_detail['kind']=='workflows'
    assert len(seen)==count


def test_pending_remote_result_revoked(tmp_path,monkeypatch):
    ws=make_workspace(tmp_path,monkeypatch)
    from devtool import actions_operator
    begin=threading.Event();end=threading.Event()
    def fetch(*a,**k):
        begin.set();assert end.wait(2);return {'kind':'workflows','items':[WF],'read_only':True}
    monkeypatch.setattr(actions_operator,'operator_workflows',fetch)
    ws.press('w');assert begin.wait(1)
    ws.press('2');end.set();finish(ws)
    assert ws.remote_detail is None and ws.tab=='runs'


def test_scoped_app_mode_no_pat_fallback(monkeypatch):
    from devtool import github_app
    get=Mock(side_effect=GitHubActionsError('private diagnostics'))
    monkeypatch.setattr(github_app,'make_app_client',get)
    fallback=Mock(side_effect=AssertionError('unexpected fallback'))
    monkeypatch.setattr(actions,'resolve_token',fallback)
    with pytest.raises(GitHubActionsError):actions.workspace_client(REPO,'app')
    get.assert_called_once_with(REPO,'actions_read');fallback.assert_not_called()


def test_app_client_forwards_attempt_and_refuses_cross_repository(monkeypatch):
    from devtool import github_app
    monkeypatch.setattr(github_app.AppInstallationProvider,'from_environment',lambda **kw:object())
    client=github_app.make_app_client(REPO,'actions_read')
    method=Mock(return_value='test');monkeypatch.setattr(GitHubActionsClient,'run_log_location',method)
    client.run_log_location(REPO,23,attempt=2);method.assert_called_once_with(REPO,23,attempt=2)
    with pytest.raises(github_app.GitHubAppError):client.job_log_location('wrong/repo',89)


def test_mixed_timestamp_catalog_keeps_original_bytes_and_filters(tmp_path):
    from devtool.run_store import RunCatalog,RunQuery
    path=tmp_path/'catalog';path.mkdir(mode=0o700)
    index=path/'catalog.json'
    index.write_text(json.dumps({'runs':{'old':{'created_at':1},'recent':{'created_at':'2026-11-26T20:00:00Z'},'middle':{'created_at':'123'},'unknown':{'created_at':'bad'}}}))
    before=index.read_bytes();c=RunCatalog.open(path,create=False)
    assert [r['run']['created_at'] for r in c.query(RunQuery())]==['2026-11-26T20:00:00Z','123',1,'bad']
    assert [r['run']['created_at'] for r in c.query(RunQuery(since=2,until=999))]==['123']
    assert index.read_bytes()==before


@pytest.mark.skipif(os.name!='posix',reason='POSIX PTY required')
def test_actual_workspace_pty_resize_and_restore(tmp_path):
    import pexpect
    import subprocess
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    subprocess.run(['git','-C',str(tmp_path),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm','base'],check=True)
    code='''import os,sys,termios
from pathlib import Path
from devtool.operator_workspace import run_operator_workspace,TABS
root=Path(sys.argv[1]);before=termios.tcgetattr(0)
readers={t:lambda:{'items':[{'name':'Record %02d'%i,'id':i+1,'state':'active'} for i in range(30)]} for t in TABS}
assert run_operator_workspace(root,'Mikeyphw/devtool',readers=readers,initial_tab='actions')
assert termios.tcgetattr(0)==before
print('F04_RESTORED',flush=True)'''
    env={**os.environ,'TERM':'xterm-256color','PYTHONPATH':str(Path(__file__).resolve().parents[1]),'XDG_STATE_HOME':str(tmp_path/'state'),'PYTHONDONTWRITEBYTECODE':'1'}
    for key in ('CI','NO_COLOR','COLUMNS','LINES','CLICOLOR'):env.pop(key,None)
    p=pexpect.spawn(sys.executable,['-B','-c',code,str(tmp_path)],env=env,encoding='utf-8',timeout=10,dimensions=(24,80))
    try:
        p.expect('q detach');p.setwinsize(10,40);p.send('j');p.expect('Record 01');p.send('q');p.expect('F04_RESTORED');p.expect(pexpect.EOF);p.close();assert p.exitstatus==0
    finally:
        if p.isalive():p.close(force=True)
