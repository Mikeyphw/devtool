"""B3 fail-closed 24/24+summary source/dispatch-scope qualification."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import pytest

from devtool.delivery_session import start,record_pr_observation
from devtool import delivery_qualification as q
from devtool.cli import build_parser


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()


@pytest.fixture
def setup(tmp_path):
    repo=tmp_path/'repo';repo.mkdir()
    git(repo,'init','-b','main');git(repo,'config','user.name','QA');git(repo,'config','user.email','qa@invalid.test')
    (repo/'x').write_text('x');git(repo,'add','x');git(repo,'commit','-m','x')
    git(repo,'switch','-c','feature/b')
    git(repo,'remote','add','origin','git@github.com:owner/repo.git')
    state=tmp_path/'state'
    session=start(repo,state_root=state)
    record_pr_observation(session['id'],{'number':4,'head':session['head'],
        'head_sha':session['head_sha'],'base':session['base'],
        'url':'https://github.com/owner/repo/pull/4'},state_root=state)
    request_id='a'*32
    runs=tmp_path/'runs';runs.mkdir()
    rid=1223
    (runs/(request_id+'.json')).write_text(json.dumps({'schema':1,'request_id':request_id,
        'repository':'owner/repo','ref':'feature/b',
        'pre_dispatch_sha':session['head_sha'],'workflow_id':700,'run_id':rid,
        'state':'accepted-correlated'}))
    return session,state,runs,request_id,rid


class Client:
    def __init__(self,session,rid):
        self.session=session;self.rid=rid
        self.att=1;self.sha=session['head_sha']
        self.conclusion='success'
        self.jobs=[{'id':i+300,'name':f'Python 3.13 / shard {i}/24',
                    'conclusion':'success','status':'completed','run_attempt':1,'run_id':rid}
                   for i in range(1,25)]
        self.jobs.append({'id':400,'name':'Summarize all 24 shards (fail closed)',
                          'status':'completed','conclusion':'success','run_attempt':1,'run_id':rid})
        self.workflow_id=700
        self.artifacts=[{'id':500+i,'name':f'devtool-full-shard-{i}',
                         'expired':False,'size_in_bytes':128,
                         'workflow_run':{'id':rid}} for i in range(1,25)]
        self.artifacts.append({'id':600,'name':'devtool-full-pytest-summary',
                               'expired':False,'size_in_bytes':128,
                               'workflow_run':{'id':rid}})
    def workflow(self,repo,filename):
        assert filename=='devtool-full-repository-pytest.yml'
        return {'id':self.workflow_id,'state':'active',
                'name':'Devtool Full Python Regression (24 Shards)',
                'path':'.github/workflows/'+filename}
    def run_artifacts(self,repo,rid):
        return self.artifacts
    def _get(self,path):
        if 'branches/' in path:return {'commit':{'sha':self.sha}}
        if 'pulls/' in path:return {'state':'open','number':4,
            'head':{'repo':{'full_name':'owner/repo'},'ref':self.session['head'],
                    'sha':self.session['head_sha'],'label':'owner:feature/b'},
            'base':{'repo':{'full_name':'owner/repo'},'ref':'main','sha':'b'*40}}
        if '/merge' in path: return {'object':{'sha':'c'*40}}
        raise AssertionError(path)
    def get_run(self,repo,rid):
        return {'id':self.rid,'workflow_id':self.workflow_id,
                'name':'Devtool Full Python Regression (24 Shards)',
                'path':'.github/workflows/devtool-full-repository-pytest.yml',
                'event':'workflow_dispatch',
                'head_sha':self.sha,'head_branch':self.session['head'],
                'status':'completed','conclusion':self.conclusion,'run_attempt':self.att,
                'repository':{'full_name':'owner/repo'}}
    def run_attempt_jobs(self,repo,rid,attempt):return self.jobs


def verify(setup,api):
    session,state,dispatch,req,_=setup
    return q.qualify(session['id'],request_id=req,client=api,
                     session_state_root=state,dispatch_state_root=dispatch)


def test_unrelated_success_does_not_qualify_required_ci(setup):
    session,_,_,_,rid=setup
    doc=verify(setup,Client(session,rid))
    assert doc['repository_regression']['state']=='PASSED'
    assert doc['repository_regression']['shards_passed']==24
    assert doc['required_delivery_ci']['state']=='NOT_OBSERVED'
    assert doc['pre_merge_qualification']!='PASSED'
    assert doc['overall_acceptance']=='BLOCKED_OR_PENDING'
    assert doc['release_qualification']=='NOT_PERFORMED'


@pytest.mark.parametrize('flaw',[
    'missing_shard','skipped_shard','duplicated_shard','missing_summary',
    'changed_attempt','changed_head','failed_run','wrong_job_attempt','wrong_job_run',
    'foreign_repo'
])
def test_regression_refuses_incomplete_or_inconsistent_evidence(setup,flaw):
    session,state,dispatch,req,rid=setup
    api=Client(session,rid)
    if flaw=='missing_shard':api.jobs.pop(0)
    if flaw=='skipped_shard':api.jobs[2]['conclusion']='skipped'
    if flaw=='duplicated_shard':api.jobs[2]['name']='Python 3.13 / shard 1/24'
    if flaw=='missing_summary':api.jobs[-1]['name']='Some unrelated task'
    if flaw=='changed_attempt':api.att=2
    if flaw=='changed_head':api.sha='b'*40
    if flaw=='failed_run':api.conclusion='failure'
    if flaw=='wrong_job_attempt':api.jobs[1]['run_attempt']=2
    if flaw=='wrong_job_run':api.jobs[0]['run_id']=rid+5
    if flaw=='foreign_repo':
        api.get_run=lambda repo,run:{'id':rid,'workflow_id':700,
                  'name':'Devtool Full Python Regression (24 Shards)',
                  'path':'.github/workflows/devtool-full-repository-pytest.yml',
                  'event':'workflow_dispatch',
                  'head_sha':session['head_sha'],'head_branch':session['head'],
                  'status':'completed','conclusion':'success','run_attempt':1,
                  'repository':{'full_name':'attacker/repo'}}
    doc=verify(setup,api)
    assert doc['repository_regression']['passed'] is False,flaw
    assert doc['pre_merge_qualification']!='PASSED'


def test_uncorrelated_dispatch_cannot_qualify_even_if_run_green(setup):
    session,state,dispatch,req,rid=setup
    p=dispatch/(req+'.json')
    j=json.loads(p.read_text());j['state']='accepted-uncorrelated';j['run_id']=None;p.write_text(json.dumps(j))
    doc=verify(setup,Client(session,rid))
    assert doc['repository_regression']['state']=='DISPATCH_UNCORRELATED'


def test_changed_fork_or_source_blocks_qualification(setup):
    session,state,dispatch,req,rid=setup
    api=Client(session,rid)
    api.sha='d'*40
    doc=verify(setup,api)
    assert 'remote PR or head' in doc['blocking_reason']
    assert not doc['repository_regression']['passed']


def test_native_parser_exposes_current_scope_commands():
    p=build_parser()
    for op in ('plan','start'):
        x=p.parse_args(['delivery','session',op,'--base-repo','upstream/devtool',
                        '--head-repo','fork/devtool','--base-sha','a'*40])
        assert x.base_repo=='upstream/devtool' and x.head_repo=='fork/devtool'
    x=p.parse_args(['delivery','session','report','--id','b'*32,
                    '--regression-request-id','c'*32])
    assert x.regression_request_id=='c'*32


def test_premerge_pass_only_with_both_independent_verified_scopes(setup,monkeypatch):
    session,state,dispatch,req,rid=setup
    api=Client(session,rid)
    # Existing exact-run policy and provider attempt verifier are the authority;
    # this test verifies the B3 composition, not their separate algorithms.
    class Policy:
        repository='owner/repo'
        sha256='e'*64
        workflow_ids={'Required CI':100}
    monkeypatch.setattr(q,'load_policy',lambda *a,**kw:Policy())
    monkeypatch.setattr(q,'assert_observation',lambda *a,**kw:None)
    monkeypatch.setattr(q,'verify_exact_attempts',lambda *a,**kw:{'verified':True,'runs':[{'run_id':55,'attempt':1}]})
    from devtool.delivery_session import record_ci_observation
    record_ci_observation(session['id'],{'state':'REQUIRED_CHECKS_PASSED','checks_qualified':True,
          'source_sha':session['head_sha'],'runs':[], 'required':['Required CI']},state_root=state)
    doc=verify(setup,api)
    assert doc['required_delivery_ci']['state']=='PASSED'
    assert doc['repository_regression']['state']=='PASSED'
    assert doc['pre_merge_qualification']=='PASSED'
    assert doc['overall_acceptance']=='PENDING_POST_MERGE_MAIN_HEAD'
    assert doc['release_qualification']=='NOT_PERFORMED'


def test_remote_policy_github_blob_and_source_verification():
    from devtool.delivery_ci_policy import load_upstream_policy, DeliveryCIPolicyError, POLICY_PATH
    import hashlib,base64
    manifest={'schema_version':1,'repository':'upstream/devtool','base_branch':'main',
              'event':'pull_request','release_qualification':'separate',
              'required_workflows':[{'name':'Required CI','workflow_id':123}]}
    raw=json.dumps(manifest).encode()
    git_blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    class API:
        tamper=False
        def _get(self,path):
            if '/branches/' in path:return {'commit':{'sha':'b'*40}}
            assert path==f'/repos/upstream/devtool/contents/{POLICY_PATH}?ref='+('b'*40)
            return {'type':'file','path':POLICY_PATH,'encoding':'base64',
                    'content':base64.b64encode(raw).decode(),
                    'sha':'0'*40 if self.tamper else git_blob}
    api=API()
    policy,pinned=load_upstream_policy(api,expected_repository='upstream/devtool',
                                      expected_base='main',expected_base_sha='b'*40)
    assert policy.repository=='upstream/devtool' and policy.workflow_ids=={'Required CI':123}
    assert pinned=='b'*40
    api.tamper=True
    with pytest.raises(DeliveryCIPolicyError,match='Git blob identity'):
        load_upstream_policy(api,expected_repository='upstream/devtool',expected_base='main')
    with pytest.raises(DeliveryCIPolicyError,match='drifted'):
        load_upstream_policy(api,expected_repository='upstream/devtool',expected_base='main',
                             expected_base_sha='c'*40)


@pytest.mark.parametrize('flaw',[
    'wrong_workflow_id','wrong_workflow_name','wrong_workflow_path',
    'disabled_workflow','wrong_run_path','missing_artifact','expired_artifact',
    'wrong_artifact_run','duplicate_artifact','wrong_artifact_size',
    'unavailable_artifacts',
])
def test_regression_requires_exact_workflow_and_artifact_receipts(setup,flaw):
    session,state,dispatch,req,rid=setup
    api=Client(session,rid)
    if flaw=='wrong_workflow_id':api.workflow_id+=1
    if flaw=='wrong_workflow_name':
        api.workflow=lambda repo,name:{'id':700,'name':'Unrelated workflow',
            'path':'.github/workflows/devtool-full-repository-pytest.yml','state':'active'}
    if flaw=='wrong_workflow_path':
        api.workflow=lambda repo,name:{'id':700,'name':'Devtool Full Python Regression (24 Shards)',
            'path':'.github/workflows/unrelated.yml','state':'active'}
    if flaw=='disabled_workflow':
        api.workflow=lambda repo,name:{'id':700,'name':'Devtool Full Python Regression (24 Shards)',
            'path':'.github/workflows/devtool-full-repository-pytest.yml','state':'disabled_manually'}
    if flaw=='wrong_run_path':
        old=api.get_run
        def fake(repo,run):
            payload=old(repo,run)
            payload['path']='.github/workflows/unrelated.yml'
            return payload
        api.get_run=fake
    if flaw=='missing_artifact':api.artifacts.pop()
    if flaw=='expired_artifact':api.artifacts[0]['expired']=True
    if flaw=='wrong_artifact_run':api.artifacts[0]['workflow_run']['id']+=1
    if flaw=='duplicate_artifact':api.artifacts[0]['id']=api.artifacts[1]['id']
    if flaw=='wrong_artifact_size':api.artifacts[0]['size_in_bytes']=0
    if flaw=='unavailable_artifacts':api.run_artifacts=lambda *a: (_ for _ in ()).throw(OSError('denied'))
    doc=verify(setup,api)
    assert doc['repository_regression']['passed'] is False,flaw
    assert doc['pre_merge_qualification']!='PASSED'


def test_fork_premerge_acceptance_requires_base_pin(setup,monkeypatch):
    session,state,dispatch,req,rid=setup
    # Even a run matching the fork HEAD cannot qualify an unpinned upstream.
    from devtool.delivery_session import _path,_write,_read
    path=_path(state,session['id'])
    raw=_read(path)
    raw['head_repository']='owner/repo'
    raw['base_repository']='upstream/repo'
    raw['base_sha']=None
    _write(path,raw)
    doc=verify(setup,Client(session,rid))
    assert doc['pre_merge_qualification']!='PASSED'
    assert 'remote PR or head' in doc['blocking_reason']
