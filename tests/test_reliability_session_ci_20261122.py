"""DT-12/13/14/15: real production paths, adversarial and lifecycle fixtures."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from devtool import actions_dispatch, actions_runs, delivery_session
from devtool.delivery_ci_registrar import collect_runs, DeliverySessionError
from devtool.github_actions import GitHubActionsClient, GitHubActionsError
from devtool.native_delivery_continuity import classify_native_commit_phase
from devtool.native_delivery_m5 import _child_env, _run
from devtool.xdg import state_dir, state_roots

T = datetime(2026, 11, 22, 15, 0, tzinfo=timezone.utc)


def observation(*, status='queued', sha='a'*40, updated=0, workflow=91, attempt=1):
    return {'id':81234567890, 'workflow_id':workflow, 'event':'pull_request',
            'created_at':T.isoformat().replace('+00:00','Z'),
            'head_sha':sha, 'head_branch':'feature/reliability', 'run_number':18,
            'path':'.github/workflows/devtool-launcher-v3.yml',
            'repository':{'id':1276037219,'full_name':'Mikeyphw/devtool'},
            'status':status, 'run_attempt':attempt,
            'updated_at':(T+timedelta(seconds=updated)).isoformat().replace('+00:00','Z'),
            'conclusion':'success' if status=='completed' else None}


def scoped_client(rows):
    client=GitHubActionsClient()
    iterator=iter(rows)
    client._get=lambda _: {'total_count':1,'workflow_runs':[next(iterator)]}
    return client


def spans():
    return {'now':T+timedelta(minutes=15), 'ranges':(
        {'began':T,'deadline':T+timedelta(minutes=20)},
        {'began':T+timedelta(minutes=1),'deadline':T+timedelta(minutes=20)})}


def test_ci_run_queued_to_completed_is_one_identity_and_latest_observation():
    rows=collect_runs(scoped_client([observation(),observation(status='completed',updated=9)]),
                      'Mikeyphw/devtool',window=spans(),event='pull_request')
    assert len(rows)==1 and rows[0]['status']=='completed'


def test_ci_run_newer_attempt_wins_over_stale_status():
    rows=collect_runs(scoped_client([observation(status='completed',updated=8),
                                     observation(status='in_progress',updated=9,attempt=2)]),
                      'Mikeyphw/devtool',window=spans(),event='pull_request')
    assert len(rows)==1 and rows[0]['run_attempt']==2 and rows[0]['conclusion'] is None


@pytest.mark.parametrize('key,value',[
    ('head_sha','f'*40),('workflow_id',99),('event','push'),
    ('created_at','2026-11-22T15:02:00Z'),('head_branch','other'),
    ('run_number',19),('repository',{'id':333}),
])
def test_ci_immutable_drift_fails_closed(key,value):
    later=observation(status='completed',updated=9)
    later[key]=value
    if key=='event':
        # Provider filtering fails first, also a safe refusal.
        expected='event filter'
    else:
        expected='immutable identity'
    with pytest.raises(DeliverySessionError,match=expected):
        collect_runs(scoped_client([observation(),later]), 'Mikeyphw/devtool',
                     window=spans(),event='pull_request')


def git(repo,*args):
    result=subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,check=True)
    return result.stdout.strip()


def repo_fixture(tmp_path):
    repo=tmp_path/'repo';repo.mkdir()
    git(repo,'init','-qb','main')
    git(repo,'config','user.email','test@example.invalid')
    git(repo,'config','user.name','Test')
    (repo/'x.txt').write_text('hello\n')
    git(repo,'add','x.txt');git(repo,'commit','-qm','initial')
    git(repo,'checkout','-qb','feature/reliability')
    git(repo,'remote','add','origin','git@github.com:Mikeyphw/devtool.git')
    return repo


def test_delivery_new_root_and_old_journal_discovery(tmp_path,monkeypatch):
    old_state=tmp_path/'old-state'
    custom=tmp_path/'custom-devtool'
    monkeypatch.setenv('XDG_STATE_HOME',str(old_state))
    monkeypatch.delenv('DEVTOOL_XDG_DIR',raising=False)
    repo=repo_fixture(tmp_path)
    legacy=old_state/'devtool'/'delivery'/'sessions'
    old=delivery_session.start(repo,state_root=legacy)
    monkeypatch.setenv('DEVTOOL_XDG_DIR',str(custom))
    assert state_dir()==custom
    assert delivery_session._state_root()==custom/'delivery'/'sessions'
    assert delivery_session.status(old['id'])['id']==old['id']
    # Keep the original journal in its original root; never move it silently.
    delivery_session.record_intent(old['id'],'push','source@exact',approved=True)
    assert (legacy/(old['id']+'.json')).exists()
    assert not (custom/'delivery'/'sessions'/(old['id']+'.json')).exists()
    current=delivery_session.start(repo)
    assert (custom/'delivery'/'sessions'/(current['id']+'.json')).is_file()
    assert delivery_session.status(current['id'])['id']==current['id']


def test_delivery_same_id_both_roots_refused(tmp_path,monkeypatch):
    old_state=tmp_path/'old-state';custom=tmp_path/'custom-devtool'
    monkeypatch.setenv('XDG_STATE_HOME',str(old_state))
    monkeypatch.delenv('DEVTOOL_XDG_DIR',raising=False)
    repo=repo_fixture(tmp_path)
    legacy=old_state/'devtool'/'delivery'/'sessions'
    old=delivery_session.start(repo,state_root=legacy)
    monkeypatch.setenv('DEVTOOL_XDG_DIR',str(custom))
    both=custom/'delivery'/'sessions';both.mkdir(parents=True,mode=0o700)
    (both/(old['id']+'.json')).write_bytes((legacy/(old['id']+'.json')).read_bytes())
    with pytest.raises(DeliverySessionError,match='Conflicting canonical and legacy'):
        delivery_session.status(old['id'])


def test_relative_xdg_home_is_ignored_not_cwd(tmp_path,monkeypatch):
    monkeypatch.setenv('HOME',str(tmp_path/'home'))
    monkeypatch.setenv('XDG_STATE_HOME','relative-state')
    monkeypatch.delenv('DEVTOOL_XDG_DIR',raising=False)
    expected=tmp_path/'home'/'.local'/'state'/'devtool'
    assert state_dir()==expected
    assert delivery_session._state_root()==expected/'delivery'/'sessions'
    assert actions_dispatch._state_root()==expected/'actions'/'dispatches'


def test_dispatch_legacy_exact_request_lookup_and_conflict(tmp_path,monkeypatch):
    old_state=tmp_path/'old';custom=tmp_path/'custom'
    monkeypatch.setenv('XDG_STATE_HOME',str(old_state))
    monkeypatch.setenv('DEVTOOL_XDG_DIR',str(custom))
    request='b'*32
    folder=old_state/'devtool'/'actions'/'dispatches';folder.mkdir(parents=True,mode=0o700)
    receipt={'schema':1,'request_id':request,'repository':'Mikeyphw/devtool',
             'workflow_id':123,'ref':'feature/one','pre_dispatch_sha':'a'*40,
             'state':'accepted-correlated','run_id':420}
    legacy=folder/(request+'.json');legacy.write_text(json.dumps(receipt))
    legacy.chmod(0o600)
    assert actions_dispatch.dispatch_resume_decision(request)['decision']=='EXISTING_CORRELATED'
    p,read=actions_runs._read_dispatch_receipt(request)
    assert p==legacy and read['run_id']==420
    canonical=custom/'actions'/'dispatches';canonical.mkdir(parents=True,mode=0o700)
    (canonical/(request+'.json')).write_text(json.dumps(receipt))
    with pytest.raises(GitHubActionsError,match='Conflicting dispatch receipts'):
        actions_dispatch.dispatch_resume_decision(request)


def test_dispatch_scans_old_and_new_journals_before_post(tmp_path,monkeypatch):
    from tests.test_actions_02_workflow_dispatch import prepared
    from devtool.github_actions import GitHubActionsClient
    old_state=tmp_path/'old';custom=tmp_path/'custom'
    monkeypatch.setenv('XDG_STATE_HOME',str(old_state))
    monkeypatch.setenv('DEVTOOL_XDG_DIR',str(custom))
    legacy=old_state/'devtool'/'actions'/'dispatches'
    legacy.mkdir(parents=True,mode=0o700)
    path=legacy/('c'*32+'.json')
    request={'schema':1,'request_id':'c'*32,'repository':'owner/repo',
             'workflow_id':11,'ref':'main','pre_dispatch_sha':'a'*40,
             'inputs_sha256':actions_dispatch._inputs_digest({'mode':'debug'}),
             'state':'accepted-uncorrelated','run_id':None}
    path.write_text(json.dumps(request));path.chmod(0o600)
    opener=prepared()
    result=actions_dispatch.dispatch_workflow(GitHubActionsClient('secret-token',opener=opener),
          'owner/repo','ci.yml','main',{'mode':'debug'},execute=True,resume_if_existing=True)
    assert result['resume_existing'] is True and result['run_id'] is None
    assert all(req.get_method()=='GET' for req in opener.requests)


@pytest.mark.parametrize('results,classification',[
    ([SimpleNamespace(name='native-test',returncode=1,skipped=False)],'PRECOMMIT_VALIDATION_FAILED'),
    ([SimpleNamespace(name='git-commit',returncode=1,skipped=False)],'COMMIT_UNCERTAIN_REQUIRES_HISTORY'),
    ([SimpleNamespace(name='git-commit',returncode=0,skipped=False)],'COMMITTED_ADOPTION_REQUIRED'),
    ([], 'NO_QUALIFIED_COMMIT_RESULT'),
])
def test_native_commit_phase_classification(results,classification):
    assert classify_native_commit_phase(results)==classification


def test_attached_tty_environment_keeps_native_colors(monkeypatch):
    monkeypatch.setenv('DEVTOOL_EXECUTION_PRESENTATION','rich')
    monkeypatch.setenv('DEVTOOL_RICH_LIVE','1')
    env=_child_env(attached=True)
    assert env['DEVTOOL_EXECUTION_PRESENTATION']=='rich'
    assert env['DEVTOOL_RICH_LIVE']=='1'
    plain=_child_env(attached=False)
    assert plain['DEVTOOL_RICH_LIVE']=='0' and plain['DEVTOOL_EXECUTION_PRESENTATION']=='plain'


def test_m5_nested_controller_cwd_is_attested_runtime_not_target(tmp_path,monkeypatch,capsys):
    """DT-17: a fake target package must not shadow native controller."""
    from devtool.native_delivery_m5 import _native_child_cwd, NativeM5Refused
    target=tmp_path/'target';target.mkdir()
    fake=target/'devtool';fake.mkdir()
    (fake/'__init__.py').write_text('')
    (fake/'__main__.py').write_text('raise SystemExit(71)\n')
    command=[sys.executable,'-B','-m','devtool','--help']
    runtime=Path(__file__).resolve().parents[1]
    assert _native_child_cwd(command,target)==runtime
    assert _native_child_cwd([sys.executable,'-B','-c','pass'],target)==target
    with pytest.raises(NativeM5Refused,match='M5_SEPARATE_RUNTIME_REQUIRED'):
        _native_child_cwd(command,runtime)
    monkeypatch.setattr('devtool.native_delivery_m5._attached_terminal',lambda:False)
    rc,_=_run(command,repo=target,timeout=20)
    displayed=capsys.readouterr()
    assert rc==0,displayed.out+'\n'+displayed.err
    assert 'usage: devtool' in displayed.out


def test_non_tty_child_preserves_return_code_and_both_streams(tmp_path,capsys,monkeypatch):
    monkeypatch.setattr('devtool.native_delivery_m5._attached_terminal',lambda:False)
    rc,lines=_run([sys.executable,'-u','-c',
                  'import sys;print("stdout-real",flush=True);print("stderr-real",file=sys.stderr,flush=True);sys.exit(17)'],
                   repo=tmp_path,timeout=5)
    display=capsys.readouterr()
    assert rc==17 and 'stdout-real' in display.out and 'stderr-real' in display.err
    assert lines==''

@pytest.mark.skipif(os.name!='posix',reason='POSIX PTY required')
def test_real_pty_attached_native_child_keeps_color_prompt_and_exit(tmp_path):
    """End-to-end child detects a real TTY and can read a prompt."""
    import pty
    import select
    import time
    master, slave=pty.openpty()
    parent = ('import sys;from pathlib import Path;'
              'from devtool.native_delivery_m5 import _run;'
              'cmd=[sys.executable,"-u","-c",'
              '"import sys;print(\\"COLOR\\",sys.stdout.isatty(),flush=True);'
              'print(\\"\\\\x1b[31mRED\\\\x1b[0m\\",flush=True);'
              'print(\\"PROMPT\\",flush=True);'
              'value=sys.stdin.readline().strip();'
              'print(\\"RECEIVED\\",value,flush=True);sys.exit(19)" ];'
              'rc,_=_run(cmd,repo=Path.cwd(),timeout=8);'
              'print("NATIVE_RC",rc,flush=True);sys.exit(0)')
    env=os.environ.copy();env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
    proc=subprocess.Popen([sys.executable,'-u','-B','-c',parent],
                          stdin=slave,stdout=slave,stderr=slave,
                          cwd=str(tmp_path),env=env)
    os.close(slave)
    data=bytearray()
    try:
        limit=time.monotonic()+10
        sent=False
        while time.monotonic()<limit:
            ready,_,_=select.select([master],[],[],0.1)
            if ready:
                try:chunk=os.read(master,65536)
                except OSError:break
                if not chunk:break
                data+=chunk
                if b'PROMPT' in data and not sent:
                    os.write(master,b'hello-pty\n');sent=True
            if proc.poll() is not None and b'NATIVE_RC' in data:
                break
        proc.wait(timeout=2)
        assert proc.returncode==0, bytes(data).decode(errors='replace')
        assert b'COLOR True' in data, bytes(data).decode(errors='replace')
        assert b'RECEIVED hello-pty' in data
        assert b'NATIVE_RC 19' in data
        assert b'\x1b[31mRED\x1b[0m' in data
        assert b'M5_NATIVE_FOREGROUND' in data
    finally:
        if proc.poll() is None:
            proc.terminate();proc.wait(timeout=4)
        os.close(master)


def test_pyz_postcommit_session_reconciles_actual_history_without_log(tmp_path,monkeypatch):
    """A verified pyz path does not need Rich output scraping for the SID."""
    from devtool.native_delivery_m5 import _committed_pyz_session
    repo=tmp_path/'source';repo.mkdir()
    before='a'*40;current='b'*40;tree='c'*40
    (repo/'feature.txt').write_text('applied\n')
    expected=hashlib.sha256(b'applied\n').hexdigest()
    package=tmp_path/'package.pyz';package.write_bytes(b'synthetic verified pyz')
    manifest={'source':{'source_sha':before,'source_tree':tree},
              'inventory':[{'action':'modify','path':'feature.txt','payload_sha256':expected}]}
    monkeypatch.setattr('devtool.launcher_contract_v3.read_embedded_package',lambda _: (manifest,{}))
    values={'HEAD':current,'HEAD^':before,'HEAD^{tree}':'d'*40, before+'^{tree}':tree,
            '--show-toplevel':str(repo),'--porcelain':''}
    def mock_git(root,*args):
        joined=' '.join(args)
        if joined.startswith('status --porcelain'):return ''
        return values.get(args[-1],values.get(joined,''))
    monkeypatch.setattr('devtool.native_delivery_m5._native_git',mock_git)
    history={'commit_hash':current,'status':'ok','validation_status':'ok',
             'sha256':'d'*64,'manifest':{'schema_version':3},
             'history_id':'hist-123','artifact':'/private/source-bound-native.zip',
             'transaction':{'id':'txn-123','status':'completed'}}
    monkeypatch.setattr('devtool.artifacts.load_history',lambda _: [history])
    root=tmp_path/'durable';folder=root/'delivery'/'sessions';folder.mkdir(mode=0o700,parents=True)
    sid='e'*32;journal=folder/(sid+'.json');journal.write_text(json.dumps({'head_sha':current,'repo_root':str(repo)}))
    journal.chmod(0o600)
    monkeypatch.setattr('devtool.xdg.state_roots',lambda: (root,))
    data={'id':sid,'repo_root':str(repo),'head_sha':current,'source_binding':'matching',
          'resumable':True,'stage':'PLANNED','intents':[],'pr':None,'ci':None,
          'native_apply':{'state':'COMMITTED','source_commit':before,'commit':current,
                          'transaction_status':'completed','transaction_id':'txn-123',
                          'history_id':'hist-123','artifact_sha256':'d'*64},
          'overlay':{'sha256':'d'*64,'path':history['artifact']}}
    monkeypatch.setattr('devtool.delivery_session.status',lambda _:data)
    assert _committed_pyz_session(repo,package,before)==sid
    data['native_apply']['transaction_id']='wrong'
    from devtool.native_delivery_m5 import NativeM5Refused
    with pytest.raises(NativeM5Refused,match='SESSION_EVIDENCE_DRIFT'):
        _committed_pyz_session(repo,package,before)


def test_native_postcommit_adoption_finds_legacy_and_does_not_duplicate(tmp_path,monkeypatch):
    from tests.test_native_delivery_continuity_m3 import fixture
    from devtool.native_delivery_continuity import adopt_after_apply
    repo, archive, unused, evidence, history, results=fixture(tmp_path,monkeypatch)
    old_state=tmp_path/'old-state'
    old_root=old_state/'devtool'/'delivery'/'sessions'
    old=adopt_after_apply(repo,archive,evidence['manifest'],results,state_root=old_root)
    new_root=tmp_path/'custom-root'
    monkeypatch.setenv('XDG_STATE_HOME',str(old_state))
    monkeypatch.setenv('DEVTOOL_XDG_DIR',str(new_root))
    observed=adopt_after_apply(repo,archive,evidence['manifest'],results)
    assert observed['reused'] is True
    assert observed['id']==old['id']
    assert len(list(old_root.glob('*.json')))==1
    assert not list((new_root/'delivery'/'sessions').glob('*.json'))
