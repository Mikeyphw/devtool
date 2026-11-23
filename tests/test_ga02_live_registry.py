"""GA-02 live registry: negative security, permission, identity, catalog tests."""
import json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest

from devtool import ga02_live_registry as live
from devtool.github_installations import RepositoryObservation
from devtool.github_relationships import RelationshipRegistry, RegistryNode, RegistryEdge

SOURCE='a'*40
REPO='Owner/repo'


def fixture_registry(*, role='base', repoid=41, install=31):
    return RelationshipRegistry('example-ga02',SOURCE,'f'*64,
        (RegistryNode(REPO,repoid,21,role,install if role!='public' else None,'private' if role!='public' else 'public'),),
        (),'owner-reviewed.json')


def observation(*, repoid=41, install=31, role='base', evidence='SCOPED_APP_READ_VERIFIED',
                visibility='private', ownerid=21, appid=17):
    return RepositoryObservation(REPO,repoid,'Owner',ownerid,
        install if role!='public' else None,ownerid if role!='public' else None,
        appid if role!='public' else None,role,'selected' if role!='public' else 'none',
        visibility,False,None,None,None,'main',False,False,evidence)


def doctor(*, repoid=41, install=31, ownerid=21,read=True):
    return {'identity_verified':True,'effective_read_verified':read,
            'repository_id':repoid,'installation_id':install,'account_id':ownerid,
            'permissions':{'contents':'read','pull_requests':'write'}}


def provider(*, repoid=41, install=31, appid=17, operation_capability=None, deny=False):
    class P:
        def issue(self, repository, capability):
            if deny:
                from devtool.github_app import GitHubAppError
                raise GitHubAppError('APP_INSTALLATION_PERMISSION_MISSING')
            return NS(repository=repository,repository_id=repoid,installation_id=install,
                      app_id=appid,capability=operation_capability or capability,
                      expires_at=1893456000,token='secret-must-never-serialize')
    return P()


def qualify(**kwargs):
    return live.qualify_operation(fixture_registry(),REPO,'delivery.pr.create',source_sha=SOURCE,
        environ={'TEST_ONLY':'x'},observer=lambda *a,**k:observation(),
        doctor=lambda *a,**k:doctor(),provider_factory=lambda *a,**k:provider(),**kwargs)


def test_real_scoped_token_receipt_contains_no_secret_or_write_claim():
    result=qualify()
    assert result['status']=='EFFECTIVE_SCOPE_VERIFIED'
    assert result['scoped_token_issued'] is True
    assert result['write_execution_verified'] is False
    assert result['write_authorized'] is False
    assert result['remote_mutation'] is False
    assert 'secret-must-never-serialize' not in json.dumps(result)


@pytest.mark.parametrize('change,reason',[('source','GA02_LIVE_POLICY_SOURCE_DRIFT'),
                                          ('operation','GA02_LIVE_OPERATION_UNSUPPORTED'),
                                          ('public','GA02_LIVE_PUBLIC_REPOSITORY_HAS_NO_APP_GRANT')])
def test_scope_and_policy_fail_closed(change,reason):
    kw={'registry':fixture_registry(),'repository':REPO,'operation':'delivery.pr.create',
        'source_sha':SOURCE,'environ':{},'observer':lambda *a,**k:observation(),
        'doctor':lambda *a,**k:doctor(),'provider_factory':lambda *a,**k:provider()}
    if change=='source':kw['source_sha']='b'*40
    if change=='operation':kw['operation']='delivery.pr.delete-unapproved'
    if change=='public':kw['registry']=fixture_registry(role='public')
    with pytest.raises(live.LiveRegistryRefused,match=reason):
        live.qualify_operation(**kw)


@pytest.mark.parametrize('case',["recreated_repo","wrong_installation","owner_transfer",
                                  "m7_read_missing","m7_repo_mismatch","scoped_permission_missing",
                                  "scoped_app_mismatch"])
def test_live_drift_and_denied_scope_never_upgrade(case):
    kw={'registry':fixture_registry(),'repository':REPO,'operation':'delivery.pr.create',
        'source_sha':SOURCE,'environ':{},'observer':lambda *a,**k:observation(),
        'doctor':lambda *a,**k:doctor(),'provider_factory':lambda *a,**k:provider()}
    if case=='recreated_repo':kw['observer']=lambda *a,**k:observation(repoid=42)
    elif case=='wrong_installation':kw['observer']=lambda *a,**k:observation(install=32)
    elif case=='owner_transfer':kw['observer']=lambda *a,**k:observation(ownerid=22)
    elif case=='m7_read_missing':kw['doctor']=lambda *a,**k:doctor(read=False)
    elif case=='m7_repo_mismatch':kw['doctor']=lambda *a,**k:doctor(repoid=43)
    elif case=='scoped_permission_missing':kw['provider_factory']=lambda *a,**k:provider(deny=True)
    elif case=='scoped_app_mismatch':kw['provider_factory']=lambda *a,**k:provider(appid=29)
    with pytest.raises(live.LiveRegistryRefused):live.qualify_operation(**kw)


def test_head_role_is_distinct_and_wrong_installation_cannot_inherit_base():
    reg=fixture_registry(role='head')
    def obs(*a,**k):return observation(role='head')
    x=live.qualify_operation(reg,REPO,'delivery.pr.read',source_sha=SOURCE,
         environ={'DEVTOOL_GITHUB_APP_HEAD_ACCOUNT':'Owner'},observer=obs,
         doctor=lambda *a,**k:doctor(),provider_factory=lambda *a,**k:provider())
    assert x['role']=='head' and x['write_executed'] is False
    with pytest.raises(live.LiveRegistryRefused):
        live.qualify_operation(reg,REPO,'delivery.pr.read',source_sha=SOURCE,
            environ={},observer=lambda *a,**k:observation(role='base'),
            doctor=lambda *a,**k:doctor(),provider_factory=lambda *a,**k:provider())


def test_account_discovery_stays_distinct_from_app_authorization():
    entry={'id':41,'full_name':REPO,'owner':{'id':21,'login':'Owner'},'private':True,'fork':False}
    class Client:
        def viewer(self):return {'id':13,'login':'someone'}
        def user_repositories(self,*,max_results):return [entry]
    result=live.account_visible_repositories(mode='env',limit=20,
            auth_resolver=lambda mode:('sensitive','environment'),client_factory=lambda token: Client())
    assert result['repositories'][0]['authority']=='USER_VISIBLE_ONLY'
    assert result['repositories'][0]['write_authorized'] is False
    assert 'sensitive' not in json.dumps(result)


@pytest.mark.parametrize('entries',[ [{'id':12,'full_name':'Owner/repo','owner':{'id':21,'login':'Wrong'},'private':True,'fork':False}],
    [{'id':41,'full_name':REPO,'owner':{'id':21,'login':'Owner'},'private':True,'fork':False},
     {'id':42,'full_name':REPO,'owner':{'id':21,'login':'Owner'},'private':True,'fork':False}],
    [{'id':41,'full_name':REPO,'owner':{'id':21,'login':'Owner'},'private':'true','fork':False}]] )
def test_user_repository_identity_privacy_collisions_fail_closed(entries):
    class Client:
        def viewer(self):return {'id':13,'login':'viewer'}
        def user_repositories(self,*,max_results):return entries
    with pytest.raises(live.LiveRegistryRefused):
        live.account_visible_repositories(mode='gh',limit=10,auth_resolver=lambda mode:('token','github-cli'),
            client_factory=lambda token:Client())


def test_catalog_separates_nodes_account_local_and_declared_edges():
    nodes=[{'host':'termux','checkout':'/home/Code/repo','repository':REPO,
        'repository_id':None,'head_sha':SOURCE,'authority':'LOCAL_NAVIGATION_ONLY'}]
    acc={'repositories':[{'repository':REPO,'repository_id':41,'write_authorized':False}]}
    result=live.catalog(fixture_registry(),source_sha=SOURCE,account=acc,checkouts=nodes,
        observer=lambda *a,**k:observation(),environ={})
    assert len(result['app_enrolled'])==len(result['account_visible'])==len(result['local_checkouts'])==1
    assert result['local_checkouts'][0]['linked_live_repository_id']==41
    assert not result['write_authorized']
    assert result['permission_qualification_separate'] is True


def test_catalog_blocks_identity_drift_or_marks_unverified_without_grant():
    with pytest.raises(live.LiveRegistryRefused,match='GA02_CATALOG_LIVE_OBSERVATION_BLOCKED'):
        live.catalog(fixture_registry(),source_sha=SOURCE,observer=lambda *a,**k:observation(repoid=43),environ={})
    result=live.catalog(fixture_registry(),source_sha=SOURCE,
         observer=lambda *a,**k:observation(repoid=43),environ={},strict=False)
    assert result['app_enrolled'][0]['status']=='LIVE_OBSERVATION_BLOCKED'
    assert not result['app_enrolled'][0]['write_authorized']


def test_local_checkout_readonly_git_identity_and_symlink_rejection(tmp_path):
    repo=tmp_path/'repo';repo.mkdir()
    calls=[]
    def runner(cmd,**kw):
        calls.append(cmd)
        if cmd[-2:]==['rev-parse','--show-toplevel']: return NS(returncode=0,stdout=str(repo)+'\n')
        if cmd[-3:]==['remote','get-url','origin']:return NS(returncode=0,stdout='https://github.com/Owner/repo.git\n')
        return NS(returncode=0,stdout=SOURCE+'\n')
    x=live.local_checkouts([repo],host='termux',runner=runner)
    assert x[0]['repository']==REPO and x[0]['repository_id'] is None
    assert all(c[0]=='git' for c in calls)
    link=tmp_path/'link';link.symlink_to(repo,target_is_directory=True)
    with pytest.raises(live.LiveRegistryRefused,match='GA02_LOCAL_PATH_UNSAFE_OR_DUPLICATE'):
        live.local_checkouts([link],host='termux',runner=runner)


def test_alias_casefold_collision_and_navigation_never_authorize():
    r=live.catalog(fixture_registry(),source_sha=SOURCE,environ={},
                   observer=lambda *a,**kw:observation(),
                   aliases=['mine=Owner/repo','other=Owner/upstream'])
    assert [x['alias'] for x in r['aliases']]==['mine','other']
    assert all(not x['write_authorized'] for x in r['aliases'])
    with pytest.raises(live.LiveRegistryRefused,match='GA02_ALIAS_COLLISION_OR_UNSAFE'):
        live.catalog(fixture_registry(),source_sha=SOURCE,environ={},
                     observer=lambda *a,**kw:observation(),
                     aliases=['mine=Owner/repo','MINE=Other/irrelevant'])


def test_provider_exception_never_leaks_token_or_promotes_permission():
    def boom(*a,**kw):raise RuntimeError('secret-must-not-escape')
    with pytest.raises(live.LiveRegistryRefused, match='GA02_LIVE_PROBE_UNAVAILABLE') as exc:
        live.qualify_operation(fixture_registry(),REPO,'delivery.pr.create',source_sha=SOURCE,
             environ={},observer=lambda *a,**k:boom(),
             doctor=lambda *a,**k:doctor(),provider_factory=lambda *a,**k:provider())
    assert 'secret-must-not-escape' not in str(exc.value)


def test_public_observation_never_becomes_an_app_scope():
    reg=fixture_registry(role='public')
    view=live.catalog(reg,source_sha=SOURCE,
         public_observer=lambda *a,**k:observation(role='public',visibility='public',evidence='PUBLIC_ANONYMOUS_METADATA_ONLY'))
    assert view['app_enrolled'][0]['installation_id'] is None
    assert not view['app_enrolled'][0]['write_authorized']
    with pytest.raises(live.LiveRegistryRefused):
        live.qualify_operation(reg,REPO,'delivery.pr.create',source_sha=SOURCE)


def test_local_checkout_rejects_ancestor_symlink(tmp_path):
    actual=tmp_path/'actual';actual.mkdir()
    symlink=tmp_path/'link';symlink.symlink_to(actual,target_is_directory=True)
    nested=symlink/'repo';nested.mkdir()
    with pytest.raises(live.LiveRegistryRefused,match='GA02_LOCAL_PATH_UNSAFE_OR_DUPLICATE'):
        live.local_checkouts([nested],host='termux')


def test_local_only_checkout_is_navigable_but_not_authorized(tmp_path):
    repo=tmp_path/'no-origin';repo.mkdir()
    def runner(cmd,**kw):
        if cmd[-2:]==['rev-parse','--show-toplevel']:return NS(returncode=0,stdout=str(repo)+'\n')
        if cmd[-2:]==['rev-parse','HEAD']:return NS(returncode=0,stdout=SOURCE+'\n')
        if cmd[-3:]==['remote','get-url','origin']:return NS(returncode=2,stdout='')
        raise AssertionError('Unexpected git command')
    result=live.local_checkouts([repo],host='termux',runner=runner)
    assert result[0]['repository'] is None
    assert result[0]['identity']=='GITHUB_ORIGIN_UNVERIFIED_LOCAL_ONLY'
    assert not result[0]['write_authorized']
