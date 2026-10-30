"""R1R25 contracts: no network, no live dispatch in tests."""
from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import yaml
# `tools` is not a project-owned package name.  On Termux, Hermes Agent
# installs its own `tools` package and pytest can import that one first.
# Resolve this reviewed source file by path so import ordering cannot redirect
# test execution to a foreign package, including in isolated git worktrees.
import importlib.util

_CYCLE_SOURCE = Path(__file__).resolve().parents[1] / "tools" / "fc08_dispatch_cycle.py"
_CYCLE_SPEC = importlib.util.spec_from_file_location("devtool_fc08_dispatch_cycle_reviewed", _CYCLE_SOURCE)
if _CYCLE_SPEC is None or _CYCLE_SPEC.loader is None:
    raise ImportError("FC08_DISPATCH_REVIEWED_SOURCE_IMPORT_UNAVAILABLE")
cycle = importlib.util.module_from_spec(_CYCLE_SPEC)
_CYCLE_SPEC.loader.exec_module(cycle)

def test_reviewed_dispatch_source_is_bound_to_repo_not_external_tools():
    """Hermes/third-party tools imports cannot substitute the reviewed runner."""
    assert Path(cycle.__file__).resolve() == _CYCLE_SOURCE.resolve()
    assert cycle.FAMILY == 'devtool.fc08.real-dispatch-cycle.v1'

SHA = '1'*40
IDENT = {'repository':cycle.REPO, 'sha':SHA, 'run_id':123456, 'run_attempt':1,
         'ref':'main','correlation':'fc08-123456-1'}

class FakeClient:
    def __init__(self):
        self.dispatch_calls=0
        self.calls=[]
        self.remote_calls=[]
    def get_run(self,repo,run_id):
        self.remote_calls.append((repo,run_id))
        return {'id':run_id,'repository':{'full_name':repo},'workflow_id':91,
                'event':'workflow_dispatch','head_branch':'main','head_sha':SHA,
                'run_attempt':1,'status':'completed',
                'conclusion':'success' if run_id==11 else 'failure'}


def fake_dispatch(client,slug,workflow,ref,inputs,*,execute,state_root,expected_source_sha,resume_if_existing):
    assert slug==cycle.REPO and workflow==cycle.WORKFLOW
    assert ref=='main' and expected_source_sha==SHA and execute and resume_if_existing
    name=inputs['scenario']
    assert inputs['correlation']==IDENT['correlation']
    journal=state_root / (name+'.json')
    client.calls.append(name)
    if journal.exists():
        return {'dispatched':False,'resume_existing':True,'request_id':name,'run_id':11 if name=='success' else 22,
                'state':'accepted-correlated','workflow_id':91,'pre_dispatch_sha':SHA}
    client.dispatch_calls+=1
    journal.write_text(name)
    return {'dispatched':True,'resume_existing':False,'request_id':name,'run_id':11 if name=='success' else 22,
            'state':'accepted-correlated','workflow_id':91,'pre_dispatch_sha':SHA}


def test_real_cycle_exactly_two_posts_and_two_safe_journal_resumes(tmp_path,monkeypatch):
    monkeypatch.setattr(cycle,'dispatch_workflow',fake_dispatch)
    c=FakeClient()
    result=cycle.execute_cycle(c,IDENT,tmp_path/'journal',sleep=lambda s:None,polls=2)
    assert c.dispatch_calls==2 and c.calls==['success','success','failure','failure']
    assert result['total_post_calls']==2
    assert result['scenarios']['success']['remote']['conclusion']=='success'
    assert result['scenarios']['failure']['remote']['conclusion']=='failure'
    assert all(not result[k] for k in ('release_qualified','fc08_overall_qualified','device_qualified','consumer_qualified','api_failure_retry_proven'))

@pytest.mark.parametrize('mutated,reason',[({'head_sha':'2'*40},'IDENTITY'),({'run_attempt':2},'IDENTITY'),
    ({'event':'push'},'IDENTITY'),({'head_branch':'other'},'IDENTITY'),({'workflow_id':9},'IDENTITY'),
    ({'conclusion':'success'},'WRONG_REMOTE_CONCLUSION')])
def test_remote_identity_or_unexpected_conclusion_fails_closed(mutated,reason):
    c=FakeClient();orig=c.get_run
    def altered(repo,run_id):
        obj=orig(repo,run_id);obj.update(mutated);return obj
    c.get_run=altered
    with pytest.raises(RuntimeError,match=reason):
        cycle.remote_proof(c,IDENT,{'run_id':22,'workflow_id':91},'failure',sleep=lambda x:None,polls=1)

@pytest.mark.parametrize('bad',[{}, {'run_id':None,'workflow_id':91}, {'run_id':'11','workflow_id':91},
 {'run_id':11,'workflow_id':None}])
def test_uncorrelated_or_missing_remote_identity_rejected(bad):
    with pytest.raises(RuntimeError,match='UNCORRELATED'):
        cycle.remote_proof(FakeClient(),IDENT,bad,'success',sleep=lambda x:None,polls=1)

def test_remote_timeout_bounded():
    c=FakeClient()
    def pending(repo,run_id):
        return {'id':run_id,'repository':{'full_name':repo},'workflow_id':91,'event':'workflow_dispatch',
                'head_branch':'main','head_sha':SHA,'run_attempt':1,'status':'in_progress','conclusion':None}
    c.get_run=pending
    with pytest.raises(RuntimeError,match='COMPLETION_UNPROVEN'):
        cycle.remote_proof(c,IDENT,{'run_id':11,'workflow_id':91},'success',sleep=lambda _:None,polls=2)

def test_nonempty_journal_rejected_before_any_post(tmp_path,monkeypatch):
    root=tmp_path/'journal';root.mkdir();(root/'old.json').write_text('{}')
    c=FakeClient();monkeypatch.setattr(cycle,'dispatch_workflow',fake_dispatch)
    with pytest.raises(RuntimeError,match='STATE_ROOT_NOT_EMPTY'):
        cycle.execute_cycle(c,IDENT,root)
    assert c.dispatch_calls==0

def test_resume_claim_without_journal_refused(tmp_path,monkeypatch):
    def bad(*args,**kw):
        args[0].dispatch_calls+=1
        return {'dispatched':True,'request_id':'wrong','run_id':11,'workflow_id':91,'pre_dispatch_sha':SHA}
    monkeypatch.setattr(cycle,'dispatch_workflow',bad)
    with pytest.raises(RuntimeError,match='DUPLICATE_POST'):
        cycle.execute_cycle(FakeClient(),IDENT,tmp_path/'journal',sleep=lambda _:None,polls=1)

def test_204_uncorrelated_never_associates_or_posts_twice(tmp_path,monkeypatch):
    def uncorrelated(client,*args,**kwargs):
        if client.dispatch_calls:
            return {'dispatched':False,'resume_existing':True,'request_id':'same','run_id':None,
                    'pre_dispatch_sha':SHA}
        client.dispatch_calls+=1
        return {'dispatched':True,'request_id':'same','run_id':None,'workflow_id':91,'pre_dispatch_sha':SHA}
    monkeypatch.setattr(cycle,'dispatch_workflow',uncorrelated)
    c=FakeClient()
    with pytest.raises(RuntimeError,match='UNCORRELATED'):
        cycle.execute_cycle(c,IDENT,tmp_path/'journal',sleep=lambda _:None,polls=1)
    assert c.dispatch_calls==1

def test_hosted_source_requires_explicit_opt_in_before_git(monkeypatch,tmp_path):
    env={'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':cycle.REPO,'GITHUB_REF':'refs/heads/main',
         'GITHUB_SHA':SHA,'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}
    with pytest.raises(RuntimeError,match='OPT_IN'):
        cycle.source_identity(env,tmp_path)
    env['FC08_DISPATCH_AUTHORIZED']='true';env['GITHUB_REPOSITORY']='attacker/other'
    with pytest.raises(RuntimeError,match='OPT_IN'):
        cycle.source_identity(env,tmp_path)

def test_source_exact_sha_and_clean(monkeypatch,tmp_path):
    env={'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':cycle.REPO,'GITHUB_REF':'refs/heads/main',
         'GITHUB_SHA':SHA,'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1','FC08_DISPATCH_AUTHORIZED':'true'}
    calls=[]
    def sub(cmd,**kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0,stdout=SHA+'\n' if 'rev-parse' in cmd else '')
    monkeypatch.setattr(cycle.subprocess,'run',sub)
    assert cycle.source_identity(env,tmp_path)['correlation']=='fc08-123-1'
    assert len(calls)==2

def test_workflows_safely_scoped_to_optin_and_no_recursive_fanout():
    repo=Path(__file__).resolve().parents[1]
    parent=(repo/'.github/workflows/devtool-fc08-dispatch-cycle.yml').read_text()
    probe=(repo/'.github/workflows/devtool-fc08-dispatch-probe.yml').read_text()
    main=(repo/'.github/workflows/devtool-fc08-host-workloads.yml').read_text()
    def parse(s):return yaml.safe_load(s)
    for s in (parent,probe):assert 'workflow_dispatch' in parse(s)[True]
    assert 'actions: write' in parent and 'if: inputs.authorize_real_dispatch == true' in parent
    assert 'permissions:\n  contents: read\n  actions: none' in main
    assert 'actions: write' not in probe and 'workflow_dispatch:' in probe
    assert 'exit 37' in probe and 'refs/heads/main' in probe
    assert 'if-no-files-found: error' in parent
    assert 'FC08_DISPATCH_AUTHORIZED:' in parent
    assert parent.count('actions: write')==1

def test_real_dispatcher_durable_journal_and_no_duplicate_post(tmp_path):
    """Exercise actual actions_dispatch.dispatch_workflow, never contact API."""
    import base64
    from devtool.actions_dispatch import dispatch_workflow as actual
    class OfflineClient:
        _token='offline-test-only'
        dispatch_calls=0
        def repository(self,slug):return {'full_name':slug,'id':42}
        def workflow(self,slug,workflow):
            assert workflow==cycle.WORKFLOW
            return {'id':91,'state':'active','path':'.github/workflows/'+cycle.WORKFLOW}
        def _get(self,uri):
            if '/commits/' in uri:return {'sha':SHA}
            doc=(Path(__file__).parents[1]/'.github/workflows'/cycle.WORKFLOW).read_bytes()
            return {'type':'file','path':'.github/workflows/'+cycle.WORKFLOW,
                    'encoding':'base64','content':base64.b64encode(doc).decode()}
        def dispatch(self,slug,workflow,ref,inputs):
            self.dispatch_calls+=1
            assert ref=='main' and inputs['correlation']==IDENT['correlation']
            return {'state':'accepted-correlated','run_id':11 if inputs['scenario']=='success' else 22}
        def get_run(self,repo,run_id):return FakeClient().get_run(repo,run_id)
    c=OfflineClient()
    result=cycle.execute_cycle(c,IDENT,tmp_path/'journal',sleep=lambda x:None,polls=2)
    assert result['total_post_calls']==2 and c.dispatch_calls==2
    journal=list((tmp_path/'journal').glob('*.json'))
    assert len(journal)==2
    assert all(json.loads(p.read_text())['run_id'] in (11,22) for p in journal)
    assert all(json.loads(p.read_text())['inputs_sha256'] for p in journal)
