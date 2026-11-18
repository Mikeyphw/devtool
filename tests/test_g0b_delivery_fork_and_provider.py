"""Current B1/B2 authorization, fork source, and provider negative contracts."""
from __future__ import annotations

from pathlib import Path
import subprocess
import pytest

from devtool.delivery_session import (start, status, DeliverySessionError,
                                      record_pr_observation)
from devtool import delivery_github as delivery
from devtool.delivery_provider import preflight, request_json, classify_provider_error


def git(root: Path, *args):
    return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()


@pytest.fixture
def fork(tmp_path):
    repo=tmp_path/'fork'
    repo.mkdir()
    git(repo,'init','-b','main')
    git(repo,'config','user.name','QA')
    git(repo,'config','user.email','qa@example.invalid')
    (repo/'one').write_text('one')
    git(repo,'add','one')
    git(repo,'commit','-m','initial')
    git(repo,'switch','-c','feature/new')
    git(repo,'remote','add','origin','git@github.com:forkOwner/devtool.git')
    root=tmp_path/'sessions'
    record=start(repo,base_repository='upstream/devtool',head_repository='forkOwner/devtool',
                 base_sha='b'*40,state_root=root)
    return record,root


def pr_row(record, *, number=3, head_repo=None, base_repo=None, label=None, sha=None, base_sha=None):
    head_repo=head_repo or record['head_repository']
    base_repo=base_repo or record['base_repository']
    return {'number':number,'state':'open','head':{'sha':sha or record['head_sha'],
            'ref':record['head'],'label':label if label is not None else
            f"{head_repo.split('/')[0]}:{record['head']}",
            'repo':{'full_name':head_repo}},'base':{'ref':record['base'],
            'sha':base_sha or 'b'*40,'repo':{'full_name':base_repo}}}


class Client:
    def __init__(self,record,items=()):
        self.record=record
        self.items=list(items)
        self._token='test-token'
        self.posts=0
        self.head_sha=record['head_sha']
        self.base_sha='b'*40
    def _get(self,path):
        if 'branches/' in path:
            return {'commit':{'sha':self.head_sha if 'forkOwner' in path else self.base_sha}}
        if 'pulls/' in path:
            return pr_row(self.record)
        return {}


def test_exact_fork_plan_and_pinned_base(fork):
    record,root=fork
    assert record['head_repository']=='forkOwner/devtool'
    assert record['base_repository']=='upstream/devtool'
    assert status(record['id'],state_root=root)['source_binding']=='matching'
    assert record['head_sha']==record['head_sha'].lower()


def test_head_repo_must_match_origin(tmp_path, fork):
    record,_=fork
    repo=Path(record['repo_root'])
    with pytest.raises(DeliverySessionError,match='Head repository must match'):
        start(repo,base_repository='upstream/devtool',head_repository='attacker/devtool',state_root=tmp_path/'other')


def test_fork_pr_read_only_verification(fork,monkeypatch):
    record,root=fork
    client=Client(record,[pr_row(record)])
    monkeypatch.setattr(delivery,'pull_requests',lambda c,slug,head,base,**kw:client.items)
    found=delivery.pr(record['id'],state_root=root,client=client)
    assert found['state']=='PR_VERIFIED'
    assert found['head_repository']=='forkOwner/devtool'
    assert found['base_repository']=='upstream/devtool'
    assert status(record['id'],state_root=root)['pr']['head_repository']=='forkOwner/devtool'


@pytest.mark.parametrize('spoof',[
    {'head_repo':'attacker/devtool'},
    {'base_repo':'attacker/devtool'},
    {'label':'attacker:feature/new'},
])
def test_fork_identity_spoof_never_accepted(fork,monkeypatch,spoof):
    record,root=fork
    client=Client(record,[pr_row(record,**spoof)])
    monkeypatch.setattr(delivery,'pull_requests',lambda c,slug,head,base,**kw:client.items)
    assert delivery.pr(record['id'],state_root=root,client=client)['state']=='PR_REQUIRES_APPROVAL'
    assert client.posts==0


def test_fork_pinned_base_refuses_drift_before_post(fork,monkeypatch):
    old,root=fork
    record=start(Path(old['repo_root']),base_repository='upstream/devtool',
                 base_sha='c'*40,state_root=root)
    client=Client(record)
    monkeypatch.setattr(delivery,'pull_requests',lambda *args,**kw:[])
    with pytest.raises(DeliverySessionError,match='Base branch drift'):
        delivery.pr(record['id'],state_root=root,client=client,yes=True)
    assert status(record['id'],state_root=root)['intents']==[]


def test_fork_pr_post_uses_upstream_and_explicit_owner(fork,monkeypatch):
    record,root=fork
    client=Client(record)
    def listings(*args,**kwargs):return client.items
    def posting(client_arg,method,path,payload=None):
        assert method=='POST' and path=='/repos/upstream/devtool/pulls'
        assert payload['head']=='forkOwner:feature/new'
        assert payload['base']=='main'
        client.posts+=1
        client.items=[pr_row(record)]
        return client.items[0]
    monkeypatch.setattr(delivery,'pull_requests',listings)
    monkeypatch.setattr(delivery,'_github_request',posting)
    result=delivery.pr(record['id'],state_root=root,client=client,yes=True)
    assert result['state']=='PR_VERIFIED' and client.posts==1
    result=delivery.pr(record['id'],state_root=root,client=client,yes=True)
    assert result['created_now'] is False and client.posts==1


def test_fork_pr_unresolved_post_cannot_replay(fork,monkeypatch):
    record,root=fork
    client=Client(record)
    monkeypatch.setattr(delivery,'pull_requests',lambda *args,**kw:[])
    def failing(*a,**kw):
        client.posts+=1
        raise DeliverySessionError('PR POST outcome uncertain')
    monkeypatch.setattr(delivery,'_github_request',failing)
    with pytest.raises(DeliverySessionError,match='uncertain'):
        delivery.pr(record['id'],state_root=root,client=client,yes=True)
    with pytest.raises(DeliverySessionError,match='do not retry'):
        delivery.pr(record['id'],state_root=root,client=client,yes=True)
    assert client.posts==1


def test_provider_rejects_cross_origin_and_unauthorized_mutation():
    client=Client({'head_sha':'a'*40})
    with pytest.raises(DeliverySessionError,match='Unsafe'):
        request_json(client,'GET','https://evil.example/repos/x/y/pulls')
    with pytest.raises(DeliverySessionError,match='Unsafe'):
        request_json(client,'POST','/repos/a/b/actions/workflows/1/dispatches',{})
    client._token=None
    with pytest.raises(DeliverySessionError,match='authenticated'):
        request_json(client,'POST','/repos/a/b/pulls',{})


def test_capability_is_not_silent_permission_success():
    class API:
        _token=None
        def repository(self,slug): return {'full_name':slug,'permissions':{}}
    api=API()
    assert preflight(api,'one/two','pr_create')['state']=='permission_unavailable'
    assert preflight(api,'one/two','branch_write')['state']=='unknown'
    assert preflight(api,'one/two','checks_read')['state']=='unknown'
    api._token='secret'
    api.workflow=lambda repo,wid: {'state':'disabled_manually'}
    assert preflight(api,'one/two','actions_dispatch',workflow='w.yml')['state']=='disabled'


def test_open_pr_with_wrong_base_is_detected_before_creation(fork,monkeypatch):
    record,root=fork
    client=Client(record)
    wrong=pr_row(record)
    wrong['base']['ref']='other-branch'
    client.items=[wrong]
    monkeypatch.setattr(delivery,'pull_requests',lambda *args,**kw:client.items)
    with pytest.raises(DeliverySessionError,match='another base'):
        delivery.pr(record['id'],state_root=root,client=client,yes=True)
    assert client.posts==0
    assert status(record['id'],state_root=root)['intents']==[]


def test_fork_pr_provider_base_sha_mismatch_is_refused(fork,monkeypatch):
    record,root=fork
    fixed=start(Path(record['repo_root']),base_repository='upstream/devtool',
                base_sha='b'*40,state_root=root)
    client=Client(fixed,[pr_row(fixed,base_sha='a'*40)])
    monkeypatch.setattr(delivery,'pull_requests',lambda *a,**kw:client.items)
    with pytest.raises(DeliverySessionError,match='PR base branch moved'):
        delivery.pr(fixed['id'],state_root=root,client=client)


def test_fork_required_ci_uses_upstream_repository(fork,monkeypatch):
    record,root=fork
    client=Client(record,[pr_row(record)])
    monkeypatch.setattr(delivery,'pull_requests',lambda *a,**kw:client.items)
    assert delivery.pr(record['id'],state_root=root,client=client)['state']=='PR_VERIFIED'
    captured=[]
    client.runs=lambda slug,*,limit: captured.append(slug) or []
    # No workflow was registered, which must remain pending rather than passed.
    result=delivery.observe_ci(record['id'],state_root=root,client=client,
                               required=('Required CI',),required_event='pull_request')
    assert result['checks_qualified'] is False
    assert result['state']=='WAITING_REGISTRATION'
    assert captured==['upstream/devtool']


def test_provider_http_403_and_429_are_fail_closed():
    from urllib.error import HTTPError
    from email.message import Message
    headers=Message();headers['X-RateLimit-Remaining']='0'
    rate=HTTPError('https://api.github.com/repos/a/b',403,'forbidden',headers,None)
    assert classify_provider_error(rate)=='provider_unavailable'
    forbidden=HTTPError('https://api.github.com/repos/a/b',403,'forbidden',Message(),None)
    assert classify_provider_error(forbidden)=='permission_unavailable'
    missing=HTTPError('https://api.github.com/repos/a/b',404,'not found',Message(),None)
    assert classify_provider_error(missing)=='resource_unverified'


def test_native_committed_adoption_can_pin_fork_without_creating_second_session(tmp_path,monkeypatch):
    from tests.test_g0a_native_delivery_coordinator import repo_fixture
    from devtool.delivery_session import adopt_applied
    repo,overlay,state,_=repo_fixture(tmp_path,monkeypatch)
    first=adopt_applied(repo,overlay,state_root=state,
                        head_repository='Mikeyphw/devtool',base_repository='upstream/devtool',
                        base_sha='a'*40)
    assert first['base_repository']=='upstream/devtool'
    again=adopt_applied(repo,overlay,state_root=state,
                        head_repository='Mikeyphw/devtool',base_repository='upstream/devtool',
                        base_sha='a'*40)
    assert again['id']==first['id'] and again['reused']
    assert len(list(state.glob('*.json')))==1
    with pytest.raises(DeliverySessionError,match='conflicting fork/base'):
        adopt_applied(repo,overlay,state_root=state)


def test_unpinned_fork_create_is_not_permitted(fork,monkeypatch):
    record,state=fork
    from devtool.delivery_session import start
    unpinned=start(Path(record['repo_root']),base_repository='upstream/devtool',
                   head_repository='forkOwner/devtool',state_root=state)
    client=Client(unpinned)
    monkeypatch.setattr(delivery,'pull_requests',lambda *a,**kw:[])
    with pytest.raises(DeliverySessionError,match='requires pinned upstream base SHA'):
        delivery.pr(unpinned['id'],state_root=state,client=client,yes=True)
    assert not status(unpinned['id'],state_root=state)['intents']


def test_pr_listing_exhausts_bounded_pages_and_rejects_duplicates(monkeypatch):
    from devtool.delivery_github import pull_requests
    calls=[]
    def fake(client,method,path,payload=None):
        calls.append(path)
        assert method=='GET'
        page=int(path.split('page=')[-1])
        if page==1:return [{'number':i} for i in range(1,101)]
        if page==2:return [{'number':101}]
        raise AssertionError('unexpected PR pagination request')
    monkeypatch.setattr(delivery,'_github_request',fake)
    assert len(pull_requests(None,'owner/repo','feature/new','main'))==101
    assert [p.split('page=')[-1] for p in calls]==['1','2']
    def duplicate(client,method,path,payload=None):
        page=int(path.split('page=')[-1])
        return [{'number':i} for i in range(1,101)] if page==1 else [{'number':55}]
    monkeypatch.setattr(delivery,'_github_request',duplicate)
    with pytest.raises(DeliverySessionError,match='Duplicate GitHub PR'):
        pull_requests(None,'owner/repo','feature/new','main')
    monkeypatch.setattr(delivery,'_github_request',lambda _client,_method,path,**kw:
        [{'number':i} for i in range((int(path.split('page=')[-1])-1)*100+1,
                                     int(path.split('page=')[-1])*100+1)])
    with pytest.raises(DeliverySessionError,match='five-page capacity'):
        pull_requests(None,'owner/repo','feature/new','main')


def test_provider_get_retries_once_and_post_never_replays(monkeypatch):
    from devtool.delivery_provider import request_json
    from urllib.error import HTTPError
    from email.message import Message
    from io import BytesIO
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,n):return b'[]'
    class Opener:
        calls=0
        def open(self,req,timeout):
            self.calls+=1
            if self.calls==1:raise HTTPError(req.full_url,429,'too many',Message(),None)
            return Response()
    class API:
        _token='secret'
        timeout=1
        _opener=Opener()
    monkeypatch.setattr('devtool.delivery_provider.time.sleep',lambda t:None)
    api=API()
    assert request_json(api,'GET','/repos/owner/repo/pulls')==[]
    assert api._opener.calls==2
    api._opener=Opener()
    with pytest.raises(DeliverySessionError,match='do not retry'):
        request_json(api,'POST','/repos/owner/repo/pulls',{'head':'x','base':'main'})
    assert api._opener.calls==1


def test_active_workflow_does_not_prove_actions_dispatch_scope():
    class API:
        _token='secret'
        def repository(self,repo):return {'full_name':repo}
        def workflow(self,repo,name):return {'id':123,'state':'active'}
    assert preflight(API(),'owner/repo','actions_dispatch',workflow='full.yml')['state']=='unknown'


def test_checks_rulesets_capability_requires_independent_read_access(monkeypatch):
    from devtool import delivery_provider as provider
    class API:
        _token=None
        def repository(self,repo):return {'full_name':repo,'default_branch':'main'}
        def _get(self,path):
            assert path=='/repos/owner/repo/branches/main'
            return {'commit':{'sha':'a'*40}}
    paths=[]
    def read(client,method,path,payload=None):
        paths.append(path)
        if '/check-runs' in path:return {'total_count':0,'check_runs':[]}
        if '/rulesets' in path:return []
        raise AssertionError(path)
    monkeypatch.setattr(provider,'request_json',read)
    result=preflight(API(),'owner/repo','checks_read')
    assert result['state']=='available'
    assert len(paths)==2 and any('/rulesets' in p for p in paths)
    paths.clear()
    def bad(client,method,path,payload=None):
        if '/rulesets' in path:raise DeliverySessionError('GitHub GET permission_unavailable')
        return {'check_runs':[]}
    monkeypatch.setattr(provider,'request_json',bad)
    result=preflight(API(),'owner/repo','checks_read')
    assert result['state']=='permission_unavailable'  # normalized, sanitized provider error


def test_provider_restricts_extended_ruleset_and_check_paths():
    from devtool.delivery_provider import request_json
    class API:
        _token=None
    api=API()
    for url in ('/repos/owner/repo/rulesets/../secrets',
                '/repos/owner/repo/commits/notsha/check-runs',
                '/repos/owner/repo/contents/private-file'):
        with pytest.raises(DeliverySessionError,match='Unsafe'):
            request_json(api,'GET',url)


def test_same_repo_provider_label_is_required_for_exact_match():
    record={'repository':'owner/repo','head_repository':'owner/repo',
            'base_repository':'owner/repo','head':'feature/new','base':'main',
            'head_sha':'a'*40}
    from devtool.delivery_github import _pr_match
    row=pr_row(record)
    assert _pr_match(row,'owner/repo','feature/new','main','a'*40)
    del row['head']['label']
    assert _pr_match(row,'owner/repo','feature/new','main','a'*40) is None
