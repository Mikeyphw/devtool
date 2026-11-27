"""UX-RICH03 native read-only workspace, source/TTY, and adversarial tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import types
import time
from unittest import mock

import pytest

from devtool import operator_workspace as mod

HEAD='a'*40
NEW='b'*40


def ws(tmp_path, *, tab='actions'):
    repo=tmp_path/'repo';repo.mkdir(exist_ok=True)
    return mod.Workspace(repo, HEAD, slug='Mikeyphw/devtool',tab=tab)


def test_five_tabs_are_native_and_stable():
    assert mod.TABS==('actions','runs','validation','ssh','events')


def test_preferences_only_saved_outside_repo(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    obj=ws(tmp_path)
    assert mod.save_ui_state(obj.root,HEAD,'validation')
    path=mod._ui_state_path(obj.root)
    assert path.is_file() and obj.root not in path.parents
    assert path.stat().st_mode&0o077==0
    assert path.parent.stat().st_mode&0o077==0
    assert mod.load_ui_state(obj.root,HEAD)=='validation'
    assert mod.load_ui_state(obj.root,NEW)=='actions'
    assert json.loads(path.read_text())=={'tab':'validation','source_sha':HEAD,'version':1}


def test_preferences_refuse_symlink_instead_of_clobber(tmp_path,monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    obj=ws(tmp_path)
    path=mod._ui_state_path(obj.root)
    path.parent.mkdir(parents=True,mode=0o700)
    target=tmp_path/'private';target.write_text('unchanged')
    path.symlink_to(target)
    assert not mod.save_ui_state(obj.root,HEAD,'ssh')
    assert mod.load_ui_state(obj.root,HEAD)=='actions'
    assert target.read_text()=='unchanged'


def test_preferences_refuse_untrusted_permissions(tmp_path,monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    obj=ws(tmp_path);path=mod._ui_state_path(obj.root)
    path.parent.mkdir(parents=True,mode=0o700)
    path.parent.chmod(0o755)
    assert not mod.save_ui_state(obj.root,HEAD,'ssh')
    assert not path.exists()


def test_bad_preference_is_not_restored(tmp_path,monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    obj=ws(tmp_path);path=mod._ui_state_path(obj.root)
    path.parent.mkdir(parents=True,mode=0o700)
    path.write_text('{bad')
    assert mod.load_ui_state(obj.root,HEAD)=='actions'
    path.write_text('0'*5000)
    assert mod.load_ui_state(obj.root,HEAD)=='actions'
    assert not mod.save_ui_state(obj.root,HEAD,'unknown-tab')


def test_no_ssh_network_on_automatic_default(tmp_path,monkeypatch):
    obj=ws(tmp_path)
    monkeypatch.setattr(mod,'_source_head',lambda root:HEAD)
    calls=[]
    readers={t:lambda t=t:(calls.append(t) or {'state':'observed','items':[]}) for t in mod.TABS}
    assert obj.maybe_refresh(now=3,readers=readers)
    assert calls==['actions','runs','validation','events']
    assert 'ssh' not in obj.snapshots


def test_live_local_refresh_is_bounded(tmp_path,monkeypatch):
    obj=ws(tmp_path);monkeypatch.setattr(mod,'_source_head',lambda root:HEAD)
    calls=[]
    readers={t:lambda t=t:(calls.append(t) or {'items':[]}) for t in mod.TABS}
    obj.maybe_refresh(now=10,readers=readers)
    assert not obj.maybe_refresh(now=11.99,readers=readers)
    assert len(calls)==4
    obj.maybe_refresh(now=12.01,readers=readers)
    assert len(calls)==8


def test_explicit_ssh_get_only_and_optional_bounded_auto(tmp_path,monkeypatch):
    obj=ws(tmp_path,tab='ssh');monkeypatch.setattr(mod,'_source_head',lambda root:HEAD)
    calls=[]
    readers={t:lambda t=t:(calls.append(t) or {'sessions':[]}) for t in mod.TABS}
    obj.press('r',readers=readers)
    assert calls==[]
    obj.press('g',readers=readers)
    assert calls==['ssh']
    obj.press('a',readers=readers)
    assert obj.ssh_auto_consent
    obj.refreshed_at['ssh']=8.0
    obj.maybe_refresh(now=20,readers=readers)
    assert calls.count('ssh')==2
    obj.maybe_refresh(now=30,readers=readers)
    assert calls.count('ssh')==2
    obj.maybe_refresh(now=32.01,readers=readers)
    assert calls.count('ssh')==3
    obj.press('a',readers=readers)
    assert not obj.ssh_auto_consent
    obj.maybe_refresh(now=60,readers=readers)
    assert calls.count('ssh')==3


def test_source_head_drift_refuses_and_retains_old_snapshot(tmp_path,monkeypatch):
    obj=ws(tmp_path)
    obj.snapshots['runs']={'runs':[{'run':{'run_id':'prev'}}]}
    monkeypatch.setattr(mod,'_source_head',lambda root:NEW)
    obj.refresh('runs',readers={'runs':lambda:pytest.fail('stale callback used')})
    assert obj.source_drift and not obj.automatic
    assert obj.snapshots['runs']['runs'][0]['run']['run_id']=='prev'
    assert 'HEAD changed' in obj.errors['runs']


def test_source_unavailable_is_not_success(tmp_path,monkeypatch):
    obj=ws(tmp_path)
    monkeypatch.setattr(mod,'_source_head',lambda root:(_ for _ in ()).throw(RuntimeError('error')))
    obj.refresh('runs',readers={'runs':lambda:pytest.fail('should not run')})
    assert 'verification unavailable' in obj.errors['runs']


def test_exception_keeps_previous_and_warns(tmp_path,monkeypatch):
    obj=ws(tmp_path);monkeypatch.setattr(mod,'_source_head',lambda root:HEAD)
    old={'runs':[{'run':{'run_id':'old'}}]};obj.snapshots['runs']=old
    obj.refresh('runs',readers={'runs':lambda:(_ for _ in ()).throw(OSError('TOP SECRET'))})
    assert obj.snapshots['runs'] is old
    assert 'TOP SECRET' not in obj.errors['runs']
    assert 'OSError' in obj.errors['runs']


def test_read_only_contract_refused(tmp_path,monkeypatch):
    obj=ws(tmp_path);monkeypatch.setattr(mod,'_source_head',lambda root:HEAD)
    obj.refresh('actions',readers={'actions':lambda:{'read_only':False,'items':[]}})
    assert 'ValueError' in obj.errors['actions']
    assert 'actions' not in obj.snapshots


def test_row_selection_bound_to_native_identity(tmp_path,monkeypatch):
    obj=ws(tmp_path);monkeypatch.setattr(mod,'_source_head',lambda root:HEAD)
    obj.snapshots['runs']={'runs':[{'run':{'run_id':'first'}},{'run':{'run_id':'second'}}]}
    obj.selections['runs']=1
    obj.refresh('runs',readers={'runs':lambda:{'runs':[{'run':{'run_id':'second'}},
                                               {'run':{'run_id':'first'}}]}})
    assert obj.selections['runs']==0
    obj.selections['runs']=1
    obj.refresh('runs',readers={'runs':lambda:{'runs':[{'run':{'run_id':'new'}}]}})
    assert obj.selections['runs']==0


def test_no_auto_remote_actions_get_without_keypress(tmp_path,monkeypatch):
    obj=ws(tmp_path);monkeypatch.setattr(mod,'_source_head',lambda root:HEAD)
    monkeypatch.setattr(mod,'native_actions',lambda *a: {'items':[]})
    obj.maybe_refresh(now=20)
    assert obj.remote_detail is None


def finish_actions(obj):
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        if obj.consume_actions():return
        time.sleep(.005)
    pytest.fail("Actions worker did not finish")


def test_actions_operator_explicit_get_preserved(tmp_path,monkeypatch):
    obj=ws(tmp_path)
    monkeypatch.setattr(mod,'_source_head',lambda root:HEAD)
    obj.snapshots['actions']={'items':[{'kind':'dispatch','id':'abc'}]}
    called=[]
    fake=types.SimpleNamespace(
        operator_workflows=lambda slug, **kw:(called.append(('w',slug)) or {'read_only':True,'workflows':[]}),
        operator_review=lambda row,slug, **kw:(called.append(('g',row['id'])) or {'read_only':True,'jobs':[]}),
        operator_lineage=lambda row,root,slug:(called.append(('l',row['id'])) or {'read_only':True,'nodes':[]}))
    monkeypatch.setitem(sys.modules,'devtool.actions_operator',fake)
    obj.auth_mode='none'
    for key in ('w','g','l'):
        obj.press(key)
        finish_actions(obj)
        assert obj.remote_detail is not None
        obj.press('b')
    assert called==[('w','Mikeyphw/devtool'),('g','abc'),('l','abc')]
    obj.press('b')
    assert obj.remote_detail is None


def test_actions_remote_failure_no_leaked_exception_message(tmp_path,monkeypatch):
    obj=ws(tmp_path)
    monkeypatch.setattr(mod,'_source_head',lambda root:HEAD)
    fake=types.SimpleNamespace(operator_workflows=lambda slug, **kw:(_ for _ in ()).throw(
        RuntimeError('SECRET_TOKEN')))
    monkeypatch.setitem(sys.modules,'devtool.actions_operator',fake)
    obj.auth_mode='none'
    obj.press('w')
    finish_actions(obj)
    assert 'SECRET_TOKEN' not in obj.errors['actions']
    assert obj.remote_detail is None


def test_tab_select_and_detach_without_mutation(tmp_path,monkeypatch):
    obj=ws(tmp_path)
    monkeypatch.setattr(mod,'save_ui_state',lambda *a: True)
    assert obj.press('3') and obj.tab=='validation'
    assert obj.press('4') and obj.tab=='ssh'
    assert not obj.press('q')


def test_view_is_sanitized_and_false_success_not_assumed(tmp_path):
    obj=ws(tmp_path)
    obj.snapshots['ssh']={'sessions':[{'name':'ssh-worker','status':'failure',
                                      'token':'secret-abc'}], 'state':'blocked'}
    obj.tab='ssh'
    from rich.console import Console
    import io
    out=io.StringIO();console=Console(file=out,width=90,force_terminal=False)
    console.print(obj.current_view(width=90))
    rendered=out.getvalue()
    assert 'DEVTOOL' in rendered and 'blocked' in rendered.lower()
    assert 'secret-abc' not in rendered


def test_native_runs_open_read_only_and_whitelist(tmp_path,monkeypatch):
    repo=tmp_path/'repo';repo.mkdir()
    from devtool import core_runtime, run_store
    called=[]
    class Catalog:
        @staticmethod
        def open(root,*,create=True):
            called.append(create);return Catalog()
        def query(self,q):
            return [{'run':{'run_id':'r1','status':'running','command_id':'build',
                            'argv':['--secret','abc'], 'environment':{'TOKEN':'private'}}}]
    monkeypatch.setattr(run_store,'RunCatalog',Catalog)
    monkeypatch.setattr(core_runtime,'private_run_root',lambda x:repo/'state')
    data=mod.native_runs(repo)
    assert called==[False]
    assert data['runs'][0]['run']['status']=='running'
    assert 'argv' not in data['runs'][0]['run']
    assert 'environment' not in data['runs'][0]['run']


def test_native_validation_no_active_session(tmp_path,monkeypatch):
    from devtool import validation_sessions
    monkeypatch.setattr(validation_sessions,'load_active_session',lambda *a,**k:None)
    data=mod.native_validation(tmp_path)
    assert data['state']=='no-active-session' and data['phases']==[]


def test_native_validation_phase_filter(tmp_path,monkeypatch):
    from devtool import validation_sessions
    monkeypatch.setattr(validation_sessions,'load_active_session',lambda *a,**k:object())
    monkeypatch.setattr(validation_sessions,'status_payload',lambda _: {
        'session_id':'vv','status':'paused','revision':4,
        'password':'NEVER',
        'phases':[{'name':'compile','status':'failed','secret':'NEVER'}]})
    data=mod.native_validation(tmp_path)
    assert data['phases'][0]=={'name':'compile','status':'failed'}
    assert 'password' not in data and 'secret' not in data['phases'][0]


def test_ssh_not_fetched_by_default_and_fields_whitelisted(tmp_path,monkeypatch):
    from devtool import ssh_worker, ssh_history
    monkeypatch.setattr(ssh_worker,'resolve_worker_profile',lambda *a,**kw:'worker')
    monkeypatch.setattr(ssh_history,'sessions_payload',lambda *a,**kw:{
        'profile':'remote1','active_count':1,'sessions':[{
        'name':'s1','state':'running','stdout':'SECRET',
        'stderr':'SECRET','password':'SECRET','run_id':'a1'}]})
    data=mod.native_ssh(tmp_path)
    assert data['sessions']==[{'name':'s1','run_id':'a1','state':'running'}]
    assert 'stdout' not in str(data) and 'SECRET' not in str(data)


def test_plain_pipe_disables_workspace(tmp_path,monkeypatch):
    obj=ws(tmp_path)
    monkeypatch.setattr(mod,'operator_enabled',lambda **kw:False)
    assert mod.run_operator_workspace(obj.root,obj.slug) is False


def test_head_validation_requires_git_identity(tmp_path,monkeypatch):
    obj=ws(tmp_path)
    with mock.patch.object(mod.subprocess,'run',return_value=types.SimpleNamespace(
            returncode=0,stdout='not-a-sha\n')):
        with pytest.raises(RuntimeError,match='HEAD'):
            mod._source_head(obj.root)


def test_no_extra_engine_imports_or_clipboard():
    text=Path(mod.__file__).read_text()
    for forbidden in ('termux-clipboard-set','pyperclip','clipboard.copy(',
                      'screen=True','subprocess.Popen(', 'os.system('):
        assert forbidden not in text


def test_ssh_auto_consent_does_not_enable_native_mutations(tmp_path,monkeypatch):
    obj=ws(tmp_path,tab='ssh');monkeypatch.setattr(mod,'_source_head',lambda root:HEAD)
    readers={t:lambda:{'read_only':True,'sessions':[]} for t in mod.TABS}
    obj.press('a',readers=readers)
    obj.maybe_refresh(now=3,readers=readers)
    assert obj.ssh_auto_consent and not obj.errors


def test_actions_ui_bridge_routes_native_read_only(tmp_path,monkeypatch):
    from devtool import actions_cli as bridge, operator_rich
    obj=ws(tmp_path)
    monkeypatch.setattr(operator_rich,'operator_enabled',lambda **kw:True)
    called=[]
    monkeypatch.setattr(mod,'run_operator_workspace',lambda root,slug,**kw: (
        called.append((root,slug,kw.get('initial'))) or True))
    payload={'repository':'Mikeyphw/devtool','read_only':True,'items':[]}
    assert bridge._uxrich02_operator_view(types.SimpleNamespace(), 'ui',obj.root,payload)
    assert called==[(obj.root,'Mikeyphw/devtool',payload)]


def test_other_actions_views_are_not_mutated_by_bridge(tmp_path,monkeypatch):
    from devtool import actions_cli as bridge, operator_rich
    obj=ws(tmp_path)
    monkeypatch.setattr(operator_rich,'operator_enabled',lambda **kw:True)
    called=[]
    monkeypatch.setattr(operator_rich,'render_operator',lambda kind,payload,**kw:(
        called.append(kind) or True))
    payload={'repository':'Mikeyphw/devtool','read_only':True,'nodes':[]}
    assert bridge._uxrich02_operator_view(types.SimpleNamespace(), 'pipeline',obj.root,payload)
    assert called==['pipeline']


def test_actions_remote_get_refuses_new_head_before_provider(tmp_path,monkeypatch):
    obj=ws(tmp_path)
    monkeypatch.setattr(mod,'_source_head',lambda root:NEW)
    fake=types.SimpleNamespace(operator_workflows=lambda slug, **kw:pytest.fail('remote GET must not run'))
    monkeypatch.setitem(sys.modules,'devtool.actions_operator',fake)
    obj.press('w')
    assert obj.source_drift
    assert obj.remote_detail is None
    assert 'HEAD changed' in obj.errors['actions']


def test_empty_and_partial_terminal_keys_are_inert(tmp_path, monkeypatch):
    obj=ws(tmp_path)
    monkeypatch.setattr(mod,'save_ui_state',lambda *_: pytest.fail('unexpected save'))
    for key in ('', '\x1b', '[A', '12', ' '):
        assert obj.press(key) is True
    assert obj.tab=='actions'


def test_manual_refresh_still_checks_head_and_disables_stale_tabs(tmp_path, monkeypatch):
    obj=ws(tmp_path)
    obj.automatic=False
    old={'runs':[{'run':{'run_id':'prior','status':'success'}}]}
    obj.snapshots['runs']=old
    monkeypatch.setattr(mod,'_source_head',lambda _root: NEW)
    assert not obj.maybe_refresh(now=20,readers={'runs':lambda:pytest.fail('stale read')})
    assert obj.source_drift
    assert obj.snapshots['runs'] is old
    assert obj.source_issue
    obj.tab='runs'
    from rich.console import Console
    import io
    buffer=io.StringIO()
    Console(file=buffer,force_terminal=False).print(obj.current_view())
    assert 'HEAD changed' in buffer.getvalue()


def test_duplicate_name_never_retargets_selection(tmp_path, monkeypatch):
    obj=ws(tmp_path)
    monkeypatch.setattr(mod,'_source_head',lambda _root: HEAD)
    old=[{'name':'worker'}, {'name':'worker'}]
    obj.snapshots['ssh']={'sessions':old}
    obj.selections['ssh']=1
    obj.refresh('ssh',explicit_ssh=True,readers={'ssh':lambda:{'sessions':old[::-1]}})
    assert obj.selections['ssh']==0


def test_colliding_native_ids_are_not_rebound_after_refresh(tmp_path, monkeypatch):
    obj=ws(tmp_path)
    monkeypatch.setattr(mod,'_source_head',lambda _root: HEAD)
    obj.snapshots['actions']={'items':[{'id':1,'kind':'dispatch'}, {'id':1,'kind':'dispatch'}]}
    obj.selections['actions']=1
    obj.refresh('actions',readers={'actions':lambda:{'items':[
        {'id':1,'kind':'dispatch'}, {'id':2,'kind':'pipeline'}]}})
    assert obj.selections['actions']==0


def test_source_changes_during_snapshot_discard_result(tmp_path, monkeypatch):
    obj=ws(tmp_path)
    status=iter((HEAD, NEW))
    monkeypatch.setattr(mod,'_source_head',lambda _root:next(status))
    obj.refresh('runs',readers={'runs':lambda:{'runs':[{'run':{'run_id':'new'}}]}})
    assert obj.source_drift
    assert 'runs' not in obj.snapshots


def test_ssh_status_thread_does_not_block_terminal_or_duplicate(tmp_path, monkeypatch):
    from threading import Event
    started, proceed=Event(),Event()
    calls=[]
    def blocking():
        calls.append('read')
        started.set()
        assert proceed.wait(timeout=3)
        return {'sessions':[{'run_id':'s1','state':'running'}],'read_only':True}
    observer=mod.SSHReadObserver()
    obj=ws(tmp_path,tab='ssh')
    monkeypatch.setattr(mod,'_source_head',lambda _root:HEAD)
    assert observer.request(blocking)
    assert started.wait(timeout=1)
    assert observer.request(blocking) is False
    assert not observer.consume(obj)
    proceed.set()
    import time
    for _ in range(100):
        if observer.consume(obj):break
        time.sleep(0.005)
    else:pytest.fail('SSH worker did not return')
    assert calls==['read']
    assert obj.snapshots['ssh']['sessions'][0]['run_id']=='s1'


def test_late_ssh_response_rejected_after_source_drift(tmp_path,monkeypatch):
    from threading import Event
    import time
    started, release=Event(),Event()
    obj=ws(tmp_path,tab='ssh')
    observer=mod.SSHReadObserver()
    current=[HEAD]
    monkeypatch.setattr(mod,'_source_head',lambda _root:current[0])
    obj.snapshots['ssh']={'sessions':[{'run_id':'previous'}]}
    def blocking():
        started.set()
        release.wait(timeout=3)
        return {'sessions':[{'run_id':'wrong'}]}
    assert observer.request(blocking)
    assert started.wait(timeout=1)
    current[0]=NEW
    release.set()
    for _ in range(100):
        if observer.consume(obj):break
        time.sleep(0.005)
    else:pytest.fail('SSH worker did not finish')
    assert obj.source_drift
    assert obj.snapshots['ssh']['sessions'][0]['run_id']=='previous'


def test_ssh_result_failure_does_not_disclose_private_error(tmp_path,monkeypatch):
    from threading import Event
    import time
    started=Event()
    observer=mod.SSHReadObserver()
    obj=ws(tmp_path,tab='ssh')
    monkeypatch.setattr(mod,'_source_head',lambda _root:HEAD)
    def failing():
        started.set()
        raise RuntimeError('access_token=SECRET123')
    assert observer.request(failing)
    assert started.wait(timeout=1)
    for _ in range(100):
        if observer.consume(obj):break
        time.sleep(0.005)
    else:pytest.fail('SSH worker did not finish')
    assert 'SECRET123' not in obj.errors['ssh']
    assert 'RuntimeError' in obj.errors['ssh']


def test_ui_state_refuses_symlinked_state_root(tmp_path,monkeypatch):
    workspace=ws(tmp_path)
    real=tmp_path/'private';real.mkdir(mode=0o700)
    fake=tmp_path/'link';fake.symlink_to(real,target_is_directory=True)
    monkeypatch.setenv('XDG_STATE_HOME',str(fake))
    assert not mod.save_ui_state(workspace.root,HEAD,'runs')
    assert mod.load_ui_state(workspace.root,HEAD)=='actions'


def test_tui_discovers_local_github_origin_using_native_resolver(tmp_path, monkeypatch):
    import io
    repo=tmp_path/'repo';repo.mkdir()
    monkeypatch.setattr(mod,'_source_head',lambda _root:HEAD)
    monkeypatch.setattr(mod,'operator_enabled',lambda **_:True)
    class FakeTTY(io.StringIO):
        def isatty(self):return True
        def fileno(self):return 19
    monkeypatch.setattr(mod.sys,'stdin',FakeTTY())
    monkeypatch.setattr(mod.sys,'stdout',FakeTTY())
    from devtool import github_actions
    # Some offline test environments do not have github_actions; verify the
    # resolver is native and only used when an explicit slug is missing.
    called=[]
    monkeypatch.setattr(github_actions,'resolve_repository',lambda x,y:
                         called.append((x,y)) or 'Mikeyphw/devtool')
    monkeypatch.setattr(mod,'load_ui_state',lambda *_:'runs')
    monkeypatch.setattr(mod.Workspace,'maybe_refresh',lambda *a,**k:False)
    import termios, tty
    restored=[]
    old=[0,0,0,termios.ISIG,0,0,[b'\x00']*32]
    import copy
    monkeypatch.setattr(termios,'tcgetattr',lambda fd:copy.deepcopy(old))
    monkeypatch.setattr(tty,'setcbreak',lambda fd:None)
    monkeypatch.setattr(termios,'tcsetattr',lambda fd,when,attrs:restored.append(attrs))
    from devtool import posix_input
    monkeypatch.setattr(posix_input.select,'select',lambda *a:([19],[],[]))
    monkeypatch.setattr(mod.os,'read',lambda fd,n:b'q')
    console=mod.Console(file=io.StringIO(),force_terminal=False)
    assert mod.run_operator_workspace(repo,None,console=console)
    assert called==[('',repo.resolve())]
    assert restored[-1]==old


def test_ssh_worker_refuses_false_readonly_evidence(tmp_path,monkeypatch):
    import time
    observer=mod.SSHReadObserver()
    obj=ws(tmp_path,tab='ssh')
    monkeypatch.setattr(mod,'_source_head',lambda _root:HEAD)
    assert observer.request(lambda:{'read_only':False,'sessions':[{'state':'success'}]})
    for _ in range(100):
        if observer.consume(obj):break
        time.sleep(0.005)
    else:pytest.fail('SSH request did not complete')
    assert 'ssh' not in obj.snapshots
    assert 'refused' in obj.errors['ssh']
