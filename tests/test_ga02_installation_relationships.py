"""GA-02 adversarial and integration cases; no production GitHub writes."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from devtool.github_app import GitHubAppError
from devtool.github_installations import (
    InstallationIdentityRefused, RepositoryObservation, observe_installed,
    observe_public_upstream, prewrite_snapshot,
)
from devtool.github_relationships import (
    RelationshipRefused, load_registry, assert_observation, assert_relation,
    authorize_federation_source, opted_in_registry,
)

SHA='a'*40
ENV={'DEVTOOL_GITHUB_APP_ID':'101','DEVTOOL_GITHUB_APP_INSTALLATION_ID':'200',
     'DEVTOOL_GITHUB_APP_ACCOUNT':'Alice','DEVTOOL_GITHUB_APP_PRIVATE_KEY_PATH':'/private/key',
     'DEVTOOL_GITHUB_APP_HEAD_ID':'102','DEVTOOL_GITHUB_APP_HEAD_INSTALLATION_ID':'201',
     'DEVTOOL_GITHUB_APP_HEAD_ACCOUNT':'Bob','DEVTOOL_GITHUB_APP_HEAD_PRIVATE_KEY_PATH':'/private/head'}


def remote(slug='Alice/origin', *, repo_id=10, owner_id=1, fork=False, parent=None,
           visibility='public', archived=False):
    obj={'full_name':slug,'id':repo_id,'owner':{'login':slug.split('/')[0],'id':owner_id},
         'visibility':visibility,'private':visibility=='private','fork':fork,
         'archived':archived,'default_branch':'main'}
    if parent is not None:obj['parent']=parent
    return obj


class Http:
    def __init__(self, slug='Alice/origin', repo=None, installation=None):
        self.slug=slug
        self.repo=repo or remote(slug)
        self.installation=installation or {'id':200,'app_id':101,
               'account':{'login':slug.split('/')[0], 'id':1},
               'repository_selection':'selected','suspended_at':None}
        self.calls=[]
    def request(self, method,path,credential, payload=None):
        self.calls.append((method,path,credential))
        if path=='/repos/'+self.slug:return self.repo
        if path=='/app/installations/'+str(self.installation['id']):return self.installation
        if '/commits/' in path:return {'sha':SHA}
        if '/branches/' in path:return {'protected':True}
        raise GitHubAppError('APP_INSTALLATION_OR_REPOSITORY_UNAVAILABLE')


@pytest.fixture
def installed(monkeypatch):
    class FakeProvider:
        def __init__(self, config, *, http,clock):
            self.config=config
            self.http=http
        def issue(self,slug,capability):
            if slug!=http.slug:raise GitHubAppError('APP_INSTALLATION_OR_REPOSITORY_UNAVAILABLE')
            return SimpleNamespace(repository=slug,repository_id=http.repo['id'],
                     installation_id=self.config.installation_id,
                     capability=capability,token='private-test-token')
    http=Http()
    monkeypatch.setattr('devtool.github_installations._jwt',lambda *a:'test-jwt')
    monkeypatch.setattr('devtool.github_installations.AppInstallationProvider',FakeProvider)
    return http


def test_installed_observes_real_ids_scope_without_serializing_tokens(installed):
    out=observe_installed('Alice/origin',environ=ENV,http=installed)
    r=out.receipt()
    assert out.repository_id==10 and out.owner_id==1 and out.installation_id==200
    assert out.selection=='selected' and r['write_authorized'] is False
    assert 'private-test-token' not in json.dumps(r)
    assert [v[1] for v in installed.calls]==['/repos/Alice/origin','/app/installations/200']


@pytest.mark.parametrize('changed,code',[
    ({'id':11},'GA02_REPOSITORY_NUMERIC_ID_DRIFT'),
    ({'full_name':'Mallory/origin'},'GA02_REPOSITORY_SLUG_DRIFT'),
    ({'owner':{'login':'Alice','id':2}},'GA02_OWNER_NUMERIC_ID_DRIFT'),
    ({'archived':None},'GA02_FORK_OR_ARCHIVE_UNVERIFIED'),
    ({'visibility':'private'},'GA02_VISIBILITY_CONTRADICTION'),
])
def test_installed_repository_identity_and_metadata_fail_closed(installed,changed,code):
    installed.repo.update(changed)
    # M4-issued token remains pinned to the original installation repository ID.
    if 'id' in changed:
        from devtool.github_installations import AppInstallationProvider
        # Replace FakeProvider's dynamic ID with the previously issued 10.
        orig=AppInstallationProvider.issue
        def pinned(self,slug,cap):
            result=orig(self,slug,cap)
            result.repository_id=10
            return result
        AppInstallationProvider.issue=pinned
    with pytest.raises(InstallationIdentityRefused,match=code):
        observe_installed('Alice/origin',environ=ENV,http=installed)


def test_installation_suspension_selection_account_drift(installed):
    for field,value,code in [
      ('suspended_at','now','GA02_INSTALLATION_CHANGED_OR_SUSPENDED'),
      ('repository_selection','unexpected','GA02_INSTALLATION_SELECTION_INVALID'),
      ('account',{'login':'Mallory','id':1},'GA02_INSTALLATION_ACCOUNT_DRIFT'),
      ('account',{'login':'Alice','id':777},'GA02_OWNER_NUMERIC_ID_DRIFT')]:
        saved=installed.installation[field]
        installed.installation[field]=value
        with pytest.raises(InstallationIdentityRefused,match=code):
            observe_installed('Alice/origin',environ=ENV,http=installed)
        installed.installation[field]=saved


def test_head_installation_is_separate_and_cannot_inherit_base(installed):
    installed.slug='Bob/fork'
    installed.repo=remote('Bob/fork',repo_id=20,owner_id=2,
        fork=True,parent=remote('Alice/origin'))
    installed.installation.update(id=201,app_id=102,account={'login':'Bob','id':2})
    head=observe_installed('Bob/fork',role='head',environ=ENV,http=installed)
    assert head.installation_id==201 and head.role=='head'
    with pytest.raises(InstallationIdentityRefused):
        observe_installed('Bob/fork',role='base',environ=ENV,http=installed)
    with pytest.raises(InstallationIdentityRefused):
        observe_installed('Bob/fork',role='head',environ={k:v for k,v in ENV.items() if '_HEAD_' not in k},http=installed)


def test_upstream_read_only_never_grants_write():
    class Anonymous:
        is_anonymous=True
        def __init__(self,obj):self.obj=obj;self.calls=[]
        def request(self,method,path):self.calls.append((method,path));return self.obj
    h=Anonymous(remote('Alice/origin'))
    observed=observe_public_upstream('Alice/origin',http=h)
    assert observed.installation_id is None and observed.role=='public'
    assert observed.receipt()['write_authorized'] is False
    with pytest.raises(InstallationIdentityRefused):
        prewrite_snapshot(observed,http=None,token='not-a-token',branch='main',expected_sha=SHA)
    with pytest.raises(InstallationIdentityRefused,match='GA02_PUBLIC_UPSTREAM_PRIVATE_OR_INACCESSIBLE'):
        observe_public_upstream('Alice/origin',http=Anonymous(remote('Alice/origin',visibility='private')))
    class Authenticated(Anonymous):is_anonymous=False
    with pytest.raises(InstallationIdentityRefused,match='GA02_PUBLIC_OBSERVER_MUST_BE_ANONYMOUS'):
        observe_public_upstream('Alice/origin',http=Authenticated(remote()))


def test_prewrite_remote_sha_and_branch_policy_are_current(installed):
    observed=observe_installed('Alice/origin',environ=ENV,http=installed)
    snapshot=prewrite_snapshot(observed,http=installed,token='test',branch='main',expected_sha=SHA,require_protected=True)
    assert snapshot['protected'] and snapshot['write_authorized'] is False
    with pytest.raises(InstallationIdentityRefused,match='GA02_REMOTE_BASE_SHA_DRIFT'):
        prewrite_snapshot(observed,http=installed,token='test',branch='main',expected_sha='b'*40)
    with pytest.raises(InstallationIdentityRefused,match='GA02_BRANCH_UNSAFE'):
        prewrite_snapshot(observed,http=installed,token='test',branch='../bad',expected_sha=SHA)
    class Unprotected(Http):
        def request(self,method,path,credential,payload=None):
            if '/branches/' in path:return {'protected':False}
            return super().request(method,path,credential,payload)
    with pytest.raises(InstallationIdentityRefused,match='GA02_BRANCH_PROTECTION_NOT_ENFORCED'):
        prewrite_snapshot(observed,http=Unprotected(),token='test',branch='main',expected_sha=SHA,require_protected=True)


def policy_data():
    return {'schema':'devtool.ga02.relationships.v1','policy_id':'ga02-test-owner-approved',
            'source_sha':SHA,
            'repositories':[
                {'repository':'Alice/origin','repository_id':10,'owner_id':1,'role':'base',
                 'installation_id':200,'visibility':'public'},
                {'repository':'Bob/fork','repository_id':20,'owner_id':2,'role':'head',
                 'installation_id':201,'visibility':'private'},
                {'repository':'Public/lib','repository_id':30,'owner_id':3,'role':'public',
                 'installation_id':None,'visibility':'public'}],
            'relations':[
                {'kind':'fork_of','source':'Bob/fork','target':'Alice/origin',
                 'policy_ref':'docs/fork-approval.md'},
                {'kind':'upstream','source':'Bob/fork','target':'Alice/origin',
                 'policy_ref':'docs/fork-approval.md'},
                {'kind':'submodule','source':'Alice/origin','target':'Bob/fork',
                 'policy_ref':'.gitmodules'},
                {'kind':'library_provider','source':'Alice/origin','target':'Public/lib',
                 'policy_ref':'docs/library.md'},
                {'kind':'consumer','source':'Bob/fork','target':'Public/lib',
                 'policy_ref':'docs/consumer.md'},
                {'kind':'package_dependency','source':'Alice/origin','target':'Public/lib',
                 'policy_ref':'docs/deps.md'}]}


def write_policy(tmp_path, data=None):
    p=tmp_path/'relationships.json'
    p.write_text(json.dumps(policy_data() if data is None else data,sort_keys=True))
    return p,hashlib.sha256(p.read_bytes()).hexdigest()


def observation(slug,repo_id,owner_id,role,installation_id,*,visibility='public',fork=False,parent=None):
    return RepositoryObservation(slug,repo_id,slug.split('/')[0],owner_id,installation_id,
       owner_id if role!='public' else None,101 if role!='public' else None,
       role,'selected' if role!='public' else 'none',visibility,fork,
       parent[0] if parent else None,parent[1] if parent else None,parent[2] if parent else None,
       'main',False,False,'SCOPED_APP_READ_VERIFIED' if role!='public' else 'PUBLIC_ANONYMOUS_METADATA_ONLY')


def test_policy_load_explicit_edges_and_numeric_fork_identity(tmp_path):
    p,h=write_policy(tmp_path)
    reg=load_registry(p,expected_sha256=h,expected_source_sha=SHA)
    assert reg.receipt()['write_authorized'] is False
    fork=observation('Bob/fork',20,2,'head',201,visibility='private',fork=True,
            parent=('Alice/origin',10,1))
    base=observation('Alice/origin',10,1,'base',200)
    assert assert_relation(reg,'fork_of',fork,base)['remote_fork_identity_verified']
    assert_relation(reg,'upstream',fork,base)
    assert authorize_federation_source(reg,owner_repository='Alice/origin',target=fork)['fetch_admitted']
    with pytest.raises(RelationshipRefused,match='GA02_RELATION_NOT_EXPLICITLY_ENROLLED'):
        assert_relation(reg,'submodule',fork,base)
    with pytest.raises(RelationshipRefused,match='GA02_FORK_PARENT_REMOTE_IDENTITY_UNVERIFIED'):
        assert_relation(reg,'fork_of',replace(fork,parent_repository_id=99),base)
    with pytest.raises(RelationshipRefused,match='GA02_REPOSITORY_RECREATED_OR_RENAMED'):
        assert_observation(reg,replace(fork,repository_id=99))
    with pytest.raises(RelationshipRefused,match='GA02_OWNER_TRANSFER_OR_ID_DRIFT'):
        assert_observation(reg,replace(fork,owner_id=10))
    with pytest.raises(RelationshipRefused,match='GA02_INSTALLATION_SELECTION_VISIBILITY_OR_ARCHIVE_DRIFT'):
        assert_observation(reg,replace(fork,installation_id=123))


@pytest.mark.parametrize('mutation,reason',[
    (lambda p:p.update(policy_id='**bad**'),'GA02_POLICY_ID_INVALID'),
    (lambda p:p['repositories'].append(p['repositories'][0]),'GA02_POLICY_DUPLICATE_REPOSITORY'),
    (lambda p:p['relations'][0].update(target='External/missing'),'GA02_POLICY_EDGE_NODE_UNENROLLED'),
    (lambda p:p['repositories'][2].update(visibility='private'),'GA02_PUBLIC_NODE_MUST_BE_ANONYMOUS'),
    (lambda p:p['relations'][0].update(policy_ref='../escape'),'GA02_POLICY_PROVENANCE_PATH_UNSAFE'),
])
def test_relationship_manifest_rejects_invalid_policy(tmp_path,mutation,reason):
    obj=policy_data();mutation(obj)
    p,h=write_policy(tmp_path,obj)
    with pytest.raises(RelationshipRefused,match=reason):
        load_registry(p,expected_sha256=h)


def test_policy_digest_drift_partial_env_and_source_sha(tmp_path):
    p,h=write_policy(tmp_path)
    with pytest.raises(RelationshipRefused,match='GA02_POLICY_DIGEST_DRIFT'):
        load_registry(p,expected_sha256='0'*64)
    with pytest.raises(RelationshipRefused,match='GA02_POLICY_SOURCE_SHA_DRIFT'):
        load_registry(p,expected_sha256=h,expected_source_sha='b'*40)
    assert opted_in_registry({}) is None
    with pytest.raises(RelationshipRefused,match='GA02_RELATIONSHIP_POLICY_PARTIAL_ENV'):
        opted_in_registry({'DEVTOOL_GA02_RELATIONSHIPS_FILE':str(p)})
    assert opted_in_registry({'DEVTOOL_GA02_RELATIONSHIPS_FILE':str(p),'DEVTOOL_GA02_RELATIONSHIPS_SHA256':h}).sha256==h
    p.write_text('{"schema":"x","schema":"y"}')
    bad=hashlib.sha256(p.read_bytes()).hexdigest()
    with pytest.raises(RelationshipRefused,match='GA02_POLICY_DUPLICATE_KEY'):
        load_registry(p,expected_sha256=bad)


def test_federation_git_fetch_requires_enrolled_identity_before_git_write(monkeypatch,tmp_path):
    from devtool import federation_app_auth as f
    p,h=write_policy(tmp_path)
    url='https://github.com/Bob/fork.git'
    monkeypatch.setattr(f,'git_remote_locator',lambda _:url)
    # The test must pass the independent source and owner guards before it can
    # test the target numeric-ID drift that previously failed too early.
    monkeypatch.setattr(f,'_local_commit',lambda _:SHA)
    calls=[]
    observed=[]
    monkeypatch.setattr(f,'run_git_authenticated',lambda *a,**k:calls.append('FETCH'))
    monkeypatch.setattr(f,'github_slug_for_locator',lambda v:'Bob/fork')
    env=dict(ENV,DEVTOOL_GA02_RELATIONSHIPS_FILE=str(p),DEVTOOL_GA02_RELATIONSHIPS_SHA256=h,
             DEVTOOL_GA02_FEDERATION_OWNER='Alice/origin')
    monkeypatch.setattr(f.os,'environ',env)
    def observe(repository,**kwargs):
        observed.append((repository,kwargs['role']))
        return (observation('Alice/origin',10,1,'base',200)
                if repository=='Alice/origin' else
                observation('Bob/fork',99,2,'head',201,visibility='private'))
    monkeypatch.setattr('devtool.github_installations.observe_installed',observe)
    with pytest.raises(RelationshipRefused,match='GA02_REPOSITORY_RECREATED_OR_RENAMED'):
        f.fetch_from_github_app(tmp_path,url)
    assert observed==[('Alice/origin','base'),('Bob/fork','head')]
    assert not calls


def test_public_only_dependency_never_grants_federated_fetch(tmp_path):
    p,h=write_policy(tmp_path)
    reg=load_registry(p,expected_sha256=h)
    pub=observation('Public/lib',30,3,'public',None)
    with pytest.raises(RelationshipRefused,match='GA02_FEDERATION_REQUIRES_APP_ENROLLED_TARGET'):
        authorize_federation_source(reg,owner_repository='Alice/origin',target=pub,kind='package_dependency')


def test_delivery_plan_opt_in_pins_cross_owner_fork_and_branch_without_writes(tmp_path, monkeypatch):
    from devtool.github_relationships import guard_delivery_plan
    p,h=write_policy(tmp_path)
    env=dict(ENV,DEVTOOL_GA02_RELATIONSHIPS_FILE=str(p),DEVTOOL_GA02_RELATIONSHIPS_SHA256=h)
    fork=observation('Bob/fork',20,2,'head',201,visibility='private',fork=True,
        parent=('Alice/origin',10,1))
    base=observation('Alice/origin',10,1,'base',200)
    observed=[]
    monkeypatch.setattr('devtool.github_relationships.observe_installed',
       lambda repo,**kw:observed.append((repo,kw['role'])) or (fork if repo=='Bob/fork' else base))
    class App:
        def issue(self,repo,scope):
            return SimpleNamespace(repository_id=20 if repo=='Bob/fork' else 10,token='not-serialized')
    class Provider:
        @classmethod
        def from_environment(cls,*args,**kw):return App()
    monkeypatch.setattr('devtool.github_app.AppInstallationProvider',Provider)
    monkeypatch.setattr('devtool.github_relationships.prewrite_snapshot',
       lambda obs,**kw:{'protected':True,'write_authorized':False}
       if kw['expected_sha']==SHA else (_ for _ in ()).throw(AssertionError('unpinned source')))
    # Actual source SHA/policy pin is mandatory even for a successful fork.
    result=guard_delivery_plan(head_repository='Bob/fork',base_repository='Alice/origin',
      head_branch='main',base_branch='main',head_sha=SHA,base_sha=SHA,environ=env)
    assert result['head_repository_id']==20 and result['base_repository_id']==10
    assert result['write_authorized'] is False and result['remote_mutation'] is False
    assert ('Bob/fork','head') in observed and ('Alice/origin','base') in observed
    assert 'not-serialized' not in json.dumps(result)
    with pytest.raises(RelationshipRefused,match='GA02_DELIVERY_REQUIRES_EXACT_BASE_SHA'):
        guard_delivery_plan(head_repository='Bob/fork',base_repository='Alice/origin',
          head_branch='main',base_branch='main',head_sha=SHA,base_sha=None,environ=env)
    with pytest.raises(RelationshipRefused,match='GA02_POLICY_SOURCE_SHA_DRIFT'):
        guard_delivery_plan(head_repository='Bob/fork',base_repository='Alice/origin',
          head_branch='main',base_branch='main',head_sha='c'*40,base_sha=SHA,environ=env)


def test_anonymous_public_http_never_sends_credentials_or_follows_redirects():
    from devtool.github_installations import AnonymousGitHubMetadata
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,n):return json.dumps(remote('Alice/origin')).encode()
    class Opener:
        def __init__(self):self.calls=[]
        def open(self,req,timeout):
            self.calls.append(req)
            assert req.full_url=='https://api.github.com/repos/Alice/origin'
            assert 'Authorization' not in req.headers
            return Response()
    obj=Opener();api=AnonymousGitHubMetadata(opener=obj)
    assert api.request('GET','/repos/Alice/origin')['id']==10
    with pytest.raises(InstallationIdentityRefused,match='GA02_PUBLIC_HTTP_ENDPOINT_REFUSED'):
        api.request('POST','/repos/Alice/origin')
    with pytest.raises(InstallationIdentityRefused):
        api.request('GET','/repos/Alice/origin/contents/secrets')
    assert len(obj.calls)==1


def test_delivery_session_plan_remains_offline_until_explicitly_opted_in(monkeypatch,tmp_path):
    from devtool import delivery_session
    monkeypatch.delenv('DEVTOOL_GA02_RELATIONSHIPS_FILE',raising=False)
    monkeypatch.delenv('DEVTOOL_GA02_RELATIONSHIPS_SHA256',raising=False)
    calls=[]
    monkeypatch.setattr('devtool.github_relationships.observe_installed',
        lambda *a,**kw:calls.append('network'))
    assert delivery_session._identity is not None
    from devtool.github_relationships import guard_delivery_plan
    assert guard_delivery_plan(head_repository='Alice/origin',base_repository='Alice/origin',
         head_branch='main',base_branch='main',head_sha=SHA,base_sha=None) is None
    assert calls==[]


def test_same_repository_requires_independent_base_sha_and_protection(tmp_path,monkeypatch):
    from devtool.github_relationships import guard_delivery_plan
    obj=policy_data()
    obj['repositories']=obj['repositories'][:1]
    obj['relations']=[]
    p,h=write_policy(tmp_path,obj)
    env=dict(ENV,DEVTOOL_GA02_RELATIONSHIPS_FILE=str(p),DEVTOOL_GA02_RELATIONSHIPS_SHA256=h)
    base=observation('Alice/origin',10,1,'base',200)
    monkeypatch.setattr('devtool.github_relationships.observe_installed',lambda *a,**kw:base)
    class App:
        def issue(self,*a):return SimpleNamespace(repository_id=10,token='private')
    class Provider:
        @classmethod
        def from_environment(cls,*a,**kw):return App()
    monkeypatch.setattr('devtool.github_app.AppInstallationProvider',Provider)
    calls=[]
    def snapshot(obs,*,branch,expected_sha,**kw):
        calls.append((branch,expected_sha))
        if branch=='main' and expected_sha!='b'*40:
            raise InstallationIdentityRefused('GA02_REMOTE_BASE_SHA_DRIFT')
        return {'protected':branch=='main'}
    monkeypatch.setattr('devtool.github_relationships.prewrite_snapshot',snapshot)
    with pytest.raises(RelationshipRefused,match='GA02_REMOTE_BASE_SHA_DRIFT'):
        guard_delivery_plan(head_repository='Alice/origin',base_repository='Alice/origin',
            head_branch='feature/ga02',base_branch='main',head_sha=SHA,
            base_sha='c'*40,environ=env)
    result=guard_delivery_plan(head_repository='Alice/origin',base_repository='Alice/origin',
            head_branch='feature/ga02',base_branch='main',head_sha=SHA,
            base_sha='b'*40,environ=env)
    assert result['base_branch_protected'] is True
    assert result['head_branch_protected'] is False
    assert calls[-2:]==[('feature/ga02',SHA),('main','b'*40)]
    assert result['write_authorized'] is False


def test_public_anonymous_endpoint_rejects_query_and_fragment():
    from devtool.github_installations import AnonymousGitHubMetadata
    reader=AnonymousGitHubMetadata()
    for path in ['/repos/Alice/origin/commits/main?state=all',
                 '/repos/Alice/origin/branches/main#x',
                 '/repos/Alice/origin/branches/main%0aevil']:
        with pytest.raises(InstallationIdentityRefused,match='GA02_PUBLIC_HTTP_ENDPOINT_REFUSED'):
            reader.request('GET',path)
