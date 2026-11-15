"""Current delivery behavior: source binding, mutation safety, CI registration."""
from __future__ import annotations

from pathlib import Path
import json
import subprocess
import pytest

from devtool.delivery_session import DeliverySessionError, start, status, resume
from devtool import delivery_github as target


def git(root, *args):
    p = subprocess.run(['git','-C',str(root),*args],text=True,capture_output=True,check=True)
    return p.stdout.strip()


@pytest.fixture
def session(tmp_path):
    repo = tmp_path/'repo'
    repo.mkdir()
    git(repo,'init','-b','main')
    git(repo,'config','user.name','Testing')
    git(repo,'config','user.email','test@example.invalid')
    (repo/'file').write_text('one\n')
    git(repo,'add','file')
    git(repo,'commit','-m','initial')
    git(repo,'switch','-c','feature/test')
    git(repo,'remote','add','origin','git@github.com:Mikeyphw/devtool.git')
    state=tmp_path/'sessions'
    data=start(repo,state_root=state)
    return data,state


def fake_pr(data,n=18,sha=None):
    return {'number':n,'state':'open',
            'head':{'sha':sha or data['head_sha'],'ref':data['head'],
                    'repo':{'full_name':data['repository']}},
            'base':{'ref':data['base'],'repo':{'full_name':data['repository']}},
            'merge_commit_sha':'b'*40}


class Client:
    def __init__(self,data,items=(),runs=(),merge=None):
        self.data=data
        self.items=list(items)
        self.raw_runs=list(runs)
        self.merge=merge
        self._token='fake'
        self.posts=0
    def _get(self,path):
        if path.endswith('/pulls/18'):
            return fake_pr(self.data)
        if path.endswith('/merge'):
            if not self.merge:
                raise RuntimeError('ref unavailable')
            return {'object':{'sha':self.merge}}
        return {'commit':{'sha':self.data['head_sha']}}
    def runs(self,slug,*,limit):
        assert slug==self.data['repository'] and limit==100
        return list(self.raw_runs)
    def run_attempt_jobs(self,slug,identity,attempt):
        return [{'id':40,'name':'unit-tests','conclusion':'failure',
                 'steps':[{'name':'pytest','conclusion':'failure'}]}]


def test_push_does_not_retry_after_uncertain_outcome(session):
    data,root=session
    absent=lambda *a:None
    calls=[]
    def uncertain(*a):
        calls.append('push')
        raise OSError('connection lost')
    with pytest.raises(DeliverySessionError,match='uncertain'):
        target.push(data['id'],yes=True,state_root=root,remote_probe=absent,git_push=uncertain)
    assert calls==['push']
    assert not status(data['id'],state_root=root)['resumable']
    with pytest.raises(DeliverySessionError,match='cannot be replayed'):
        target.push(data['id'],yes=True,state_root=root,remote_probe=absent,git_push=uncertain)
    assert calls==['push']


def test_push_read_only_reconciliation_and_remote_drift(session):
    data,root=session
    with pytest.raises(DeliverySessionError,match='differs'):
        target.push(data['id'],yes=True,state_root=root,remote_probe=lambda *a:'b'*40)
    res=target.push(data['id'],state_root=root,remote_probe=lambda *a:data['head_sha'])
    assert res['state']=='PUSH_VERIFIED' and not res['pushed_now']


def test_push_approval_and_verified_remote_confirm(session):
    data,root=session
    probe=[None,data['head_sha']]
    def remote(*a):return probe.pop(0)
    # Missing remote branch does not itself authorize git push.
    assert target.push(data['id'],state_root=root,remote_probe=lambda *a:None)['state']=='PUSH_REQUIRES_APPROVAL'
    pushed=[]
    result=target.push(data['id'],yes=True,state_root=root,remote_probe=remote,
                       git_push=lambda *a:pushed.append(True) or 0)
    assert result['state']=='PUSH_VERIFIED' and pushed == [True]
    assert status(data['id'],state_root=root)['resumable']


def test_pr_existing_does_not_post_and_binds_exact_source(session,monkeypatch):
    data,root=session
    cli=Client(data,items=[fake_pr(data)])
    monkeypatch.setattr(target,'pull_requests',lambda client,slug,head,base:cli.items)
    result=target.pr(data['id'],state_root=root,client=cli)
    assert result['state']=='PR_VERIFIED' and not result['created_now']
    assert status(data['id'],state_root=root)['pr']['number']==18
    cli.items=[fake_pr(data,sha='b'*40)]
    with pytest.raises(DeliverySessionError,match='Existing PR head differs'):
        target.pr(data['id'],state_root=root,client=cli)


def test_pr_post_once_and_verify_with_read_after_write(session,monkeypatch):
    data,root=session
    cli=Client(data)
    def listing(*a):return cli.items
    def create(client,method,path,payload=None):
        assert method=='POST'
        cli.posts+=1
        cli.items=[fake_pr(data)]
        return fake_pr(data)
    monkeypatch.setattr(target,'pull_requests',listing)
    monkeypatch.setattr(target,'_github_request',create)
    assert target.pr(data['id'],client=cli,state_root=root)['state']=='PR_REQUIRES_APPROVAL'
    result=target.pr(data['id'],yes=True,client=cli,state_root=root,title='Delivery test')
    assert result['created_now'] and cli.posts==1
    assert status(data['id'],state_root=root)['resumable']
    again=target.pr(data['id'],yes=True,client=cli,state_root=root)
    assert not again['created_now'] and cli.posts==1


def test_uncertain_pr_post_cannot_be_retried(session,monkeypatch):
    data,root=session
    cli=Client(data)
    monkeypatch.setattr(target,'pull_requests',lambda *a:[])
    def unknown(*a,**kw):
        cli.posts+=1
        raise DeliverySessionError('PR POST outcome uncertain')
    monkeypatch.setattr(target,'_github_request',unknown)
    with pytest.raises(DeliverySessionError,match='uncertain'):
        target.pr(data['id'],yes=True,client=cli,state_root=root)
    with pytest.raises(DeliverySessionError,match='do not retry'):
        target.pr(data['id'],yes=True,client=cli,state_root=root)
    assert cli.posts==1 and not status(data['id'],state_root=root)['resumable']


def run(data,*,name='Unit CI',event='push',sha=None,status='completed',conclusion='success',rid=12,prs=None):
    return {'id':rid,'event':event,'head_branch':data['head'],'head_sha':sha or data['head_sha'],
            'name':name,'workflow_id':200,'status':status,'conclusion':conclusion,
            'repository':{'full_name':data['repository']},
            'run_attempt':1,'pull_requests':prs or []}


def test_missing_registration_is_pending_not_a_failure(session):
    data,root=session
    cli=Client(data)
    res=target.observe_ci(data['id'],client=cli,state_root=root,required=('Unit CI',),
                          checks=2,interval=1,sleep=lambda x:None)
    assert res['state']=='WAITING_REGISTRATION' and res['missing']==['Unit CI']
    assert res['release_qualified'] is False
    assert [x['state'] for x in res['history']]==['WAITING_REGISTRATION']*2
    assert status(data['id'],state_root=root)['ci']['checks_qualified'] is False


def test_ci_appears_later_after_unrelated_runs(session):
    data,root=session
    wrong=run(data,sha='e'*40,rid=11)
    good=run(data,rid=15)
    cli=Client(data)
    sequences=[[wrong],[wrong,good]]
    cli.runs=lambda *a,**k:sequences.pop(0)
    result=target.observe_ci(data['id'],client=cli,state_root=root,
                             required=('Unit CI',),checks=3,interval=1,sleep=lambda x:None)
    assert result['state']=='REQUIRED_CHECKS_PASSED' and result['poll']==2
    assert [x['state'] for x in result['history']]==['WAITING_REGISTRATION','REQUIRED_CHECKS_PASSED']


def test_ci_pr_merge_ref_must_be_current_and_source_attested(session):
    data,root=session
    from devtool.delivery_session import record_pr_observation
    record_pr_observation(data['id'],{'number':18,'head':data['head'],'base':data['base'],
                          'head_sha':data['head_sha'],
                          'url':f"https://github.com/{data['repository']}/pull/18"},state_root=root)
    verified_merge='c'*40
    cli=Client(data,merge=verified_merge,runs=[run(data,event='pull_request',sha=verified_merge,rid=100)])
    result=target.observe_ci(data['id'],client=cli,state_root=root,required=('Unit CI',))
    assert result['state']=='REQUIRED_CHECKS_PASSED'
    cli.merge='d'*40
    result=target.observe_ci(data['id'],client=cli,state_root=root,required=('Unit CI',))
    assert result['state']=='WAITING_REGISTRATION'


def test_ci_completed_failure_extracts_exact_step(session):
    data,root=session
    cli=Client(data,runs=[run(data,conclusion='failure')])
    result=target.observe_ci(data['id'],client=cli,state_root=root,required=('Unit CI',))
    assert result['state']=='CHECKS_FAILED'
    assert result['failures'][0]['jobs'][0]['steps']==['pytest']


def test_no_required_check_cannot_qualify(session):
    data,root=session
    cli=Client(data,runs=[run(data)])
    result=target.observe_ci(data['id'],client=cli,state_root=root)
    assert result['state']=='OBSERVED_ONLY' and result['checks_qualified'] is False
    assert not status(data['id'],state_root=root)['ci']['checks_qualified']


def test_safety_bounds_are_enforced_before_network(session):
    data,root=session
    with pytest.raises(DeliverySessionError,match='CI polling limits'):
        target.observe_ci(data['id'],checks=1000,state_root=root)
    with pytest.raises(DeliverySessionError,match='Required workflow'):
        target.observe_ci(data['id'],required=('',),state_root=root)


def test_unauthed_pr_create_refuses_before_intent(session,monkeypatch):
    data,root=session
    cli=Client(data)
    cli._token=None
    monkeypatch.setattr(target,'pull_requests',lambda *a:[])
    with pytest.raises(DeliverySessionError,match='Authenticated GitHub PR write access'):
        target.pr(data['id'],yes=True,state_root=root,client=cli)
    assert status(data['id'],state_root=root)['resumable']

def test_ci_rejects_moved_open_pr(session):
    data,root=session
    from devtool.delivery_session import record_pr_observation
    record_pr_observation(data['id'],{'number':18,'head':data['head'],'base':data['base'],
                          'head_sha':data['head_sha'],
                          'url':f"https://github.com/{data['repository']}/pull/18"},state_root=root)
    cli=Client(data)
    cli._get=lambda path: ({'commit':{'sha':'e'*40}} if '/branches/' in path else fake_pr(data))
    with pytest.raises(DeliverySessionError,match='Remote branch moved'):
        target.observe_ci(data['id'],client=cli,state_root=root,required=('Unit CI',))

def test_unrelated_dispatch_cannot_satisfy_unlinked_required_ci(session):
    data,root=session
    cli=Client(data,runs=[run(data,event='workflow_dispatch')])
    result=target.observe_ci(data['id'],client=cli,state_root=root,required=('Unit CI',))
    assert result['state']=='WAITING_REGISTRATION'
