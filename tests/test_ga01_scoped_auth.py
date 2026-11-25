"""GA-01 scoped App authority — production-bound, adversarial source tests."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from devtool.ga01_scoped_auth import (AppClient, AppOperationRefused,
    actions_operation, issue_for_operation, _OPERATION_CAPABILITIES)
from devtool.github_actions import GitHubActionsError, _actions_token
from devtool import github_app


def _a(action, sub=None):
    kw={'actions_command':action,'auth':'app'}
    if sub is not None:
        for f in ('ci_command','pipeline_command','extensions_command',
                  'release_command','android_command'):
            kw[f]=sub
    return SimpleNamespace(**kw)


@pytest.mark.parametrize('action,sub,expected', [
    ('workflow',None,'actions_read'),('workflows',None,'actions_read'),
    ('plan',None,'actions_read'),('lineage',None,'actions_read'),
    ('runs',None,'actions_read'),('view',None,'actions_read'),
    ('jobs',None,'actions_read'),('watch',None,'actions_read'),
    ('review',None,'actions_read'),('collect',None,'actions_read'),
    ('download',None,'actions_read'),('verify',None,'actions_read'),
    ('run',None,'actions_dispatch'),('cancel',None,'actions_control'),
    ('rerun',None,'actions_control'),('ci','plan','actions_read'),
    ('ci','run','actions_dispatch'),('ci','collect','actions_read'),
    ('pipeline','plan','actions_read'),('pipeline','run','actions_dispatch'),
    ('pipeline','resume','actions_dispatch'),
    ('extensions','runners','runners_read'),
    ('extensions','metrics','actions_read'),
    ('extensions','publish-plan','release_read'),
    ('extensions','publish','release_write'),
    ('release','readiness','release_read'),
    ('release','verify','release_read'),
    ('android','aab-plan','actions_read'),
    ('android','aab-run','actions_dispatch'),
    ('android','release-batch-run','actions_dispatch'),
    ('android','aab-import','actions_read'),
])
def test_strict_operation_maps_to_least_capability(action,sub,expected):
    operation=actions_operation(_a(action,sub))
    assert _OPERATION_CAPABILITIES[operation] == expected


@pytest.mark.parametrize('action,sub', [
    ('release','setup'), ('extensions','gitlab-pipelines'),
    ('pipeline','delete'), ('ci','ship'), ('android','release-sign'),
    ('unregistered',None),('extensions','bad'),('release','something'),
])
def test_unsupported_route_cannot_mint_app_token(action,sub,monkeypatch):
    monkeypatch.setattr('devtool.ga01_scoped_auth.issue_for_operation',
                       lambda *a, **k:pytest.fail('issued unsupported token'))
    with pytest.raises((GitHubActionsError,AppOperationRefused),match='NOT_SUPPORTED'):
        _actions_token(_a(action,sub),'Mikeyphw/devtool')


class StubProvider:
    def __init__(self,access):
        self.access=access
        self.calls=[]
    def issue(self,repository,capability):
        self.calls.append((repository,capability))
        return self.access


def access(**changes):
    fields={'repository':'Mikeyphw/devtool','repository_id':1276037219,
            'app_id':5853103,'installation_id':184133864,
            'capability':'actions_read', 'expires_at':1795400000,
            'token':'ghs_SECRET_CANNOT_APPEAR_IN_PUBLIC_RECEIPT'}
    fields.update(changes)
    return SimpleNamespace(**fields)


def test_exact_repo_and_numeric_id_and_secret_free_receipt():
    provider=StubProvider(access())
    scope=issue_for_operation('Mikeyphw/devtool','actions.workflows.read',
         expected_repository_id=1276037219,provider=provider)
    assert scope.token.startswith('ghs_')
    assert isinstance(scope,AppClient) and scope.repository_id==1276037219
    assert provider.calls==[('Mikeyphw/devtool','actions_read')]
    assert scope.public_receipt()['token_persisted'] is False
    assert scope.public_receipt()['write_execution_verified'] is False
    assert 'SECRET' not in repr(scope)
    assert 'SECRET' not in json.dumps(scope.public_receipt())


@pytest.mark.parametrize('bad',[
    {'repository':'Mikeyphw/other'}, {'repository_id':0},
    {'repository_id':'1276037219'}, {'installation_id':0},
    {'capability':'actions_dispatch'}, {'token':''},
])
def test_post_issuance_identity_drift_fails_closed(bad):
    with pytest.raises(AppOperationRefused, match='IDENTITY_MISMATCH'):
        issue_for_operation('Mikeyphw/devtool','actions.workflows.read',
                            provider=StubProvider(access(**bad)))


def test_expected_numeric_identity_cannot_be_rebound():
    with pytest.raises(AppOperationRefused,match='NUMERIC_ID_DRIFT'):
        issue_for_operation('Mikeyphw/devtool','actions.workflows.read',
            expected_repository_id=99,provider=StubProvider(access()))
    with pytest.raises(AppOperationRefused,match='NUMERIC_ID_DRIFT'):
        issue_for_operation('Mikeyphw/devtool','actions.workflows.read',
            expected_repository_id=True,provider=StubProvider(access()))


def test_unknown_operation_is_rejected_before_any_provider_access():
    with pytest.raises(AppOperationRefused,match='NOT_ADMITTED'):
        issue_for_operation('Mikeyphw/devtool','repository.delete',
                            provider=StubProvider(access()))


def test_actions_native_path_uses_scoped_policy(monkeypatch):
    issued=[]
    def fake_issue(repo,operation):
        issued.append((repo,operation))
        return access(token='ONE_TIME_SCOPE')
    monkeypatch.setattr('devtool.ga01_scoped_auth.issue_for_operation',fake_issue)
    token,kind=_actions_token(_a('run'),'Mikeyphw/devtool')
    assert (token,kind)==('ONE_TIME_SCOPE','github-app')
    assert issued==[('Mikeyphw/devtool','actions.dispatch')]


def test_actions_explicit_app_never_falls_back_to_pat(monkeypatch):
    monkeypatch.setattr('devtool.github_actions.resolve_token',
                        lambda mode:pytest.fail('PAT fallback'))
    def fail(*args):
        raise AppOperationRefused('APP_PROVIDER_UNAVAILABLE')
    monkeypatch.setattr('devtool.ga01_scoped_auth.issue_for_operation',fail)
    with pytest.raises(GitHubActionsError,match='APP_PROVIDER_UNAVAILABLE'):
        _actions_token(_a('workflow'),'Mikeyphw/devtool')


def test_auto_auth_semantics_preserved(monkeypatch):
    calls=[]
    def legacy(mode):
        calls.append(mode)
        return ('PAT_OR_GH_EXPLICIT','environment')
    monkeypatch.setattr('devtool.github_actions.resolve_token',legacy)
    assert _actions_token(SimpleNamespace(actions_command='run',auth='auto'),
                          'Mikeyphw/devtool')==('PAT_OR_GH_EXPLICIT','environment')
    assert calls==['auto']


def test_app_permissions_are_narrow_not_union():
    p=github_app._PERMISSIONS
    assert p['actions_read']=={'contents':'read','actions':'read'}
    assert p['actions_control']=={'contents':'read','actions':'write'}
    assert p['runners_read']=={'contents':'read','administration':'read'}
    assert p['release_read']=={'contents':'read'}
    assert p['release_write']=={'contents':'write'}
    assert 'pull_requests' not in p['actions_dispatch']


def test_installed_m7_config_reused_for_scoped_actions(monkeypatch):
    captured=[]
    fake=StubProvider(access())
    monkeypatch.setattr('devtool.federation_app_auth.installed_app_environment',
                        lambda:{'DEVTOOL_GITHUB_APP_ID':'fake-m7'})
    monkeypatch.setattr(github_app.AppInstallationProvider,'from_environment',
                        lambda env:captured.append(env) or fake)
    scope=issue_for_operation('Mikeyphw/devtool','actions.workflows.read')
    assert scope.repository_id==1276037219
    assert captured==[{'DEVTOOL_GITHUB_APP_ID':'fake-m7'}]


def test_provider_errors_redacted(monkeypatch):
    provider=StubProvider(access())
    def fail(*_):
        raise github_app.GitHubAppError('APP_INSTALLATION_PERMISSION_MISSING')
    provider.issue=fail
    with pytest.raises(AppOperationRefused,match='APP_INSTALLATION_PERMISSION_MISSING'):
        issue_for_operation('Mikeyphw/devtool','actions.dispatch',provider=provider)


def test_app_client_pin_rejects_cross_repo_before_http(monkeypatch):
    monkeypatch.setattr('devtool.federation_app_auth.installed_app_environment',
                        lambda:{'DEVTOOL_GITHUB_APP_ID':'M7'})
    issued=[]
    class P:
        def issue(self,repository,capability):
            issued.append((repository,capability))
            return access(capability=capability,token='SCOPED_FOR_ONE_REPO')
    monkeypatch.setattr(github_app.AppInstallationProvider,'from_environment',
                        lambda *a,**k:P())
    client=github_app.make_app_client('Mikeyphw/devtool','actions_read')
    with pytest.raises(github_app.GitHubAppError,match='CROSS_REPOSITORY'):
        client._get('/repos/Other/project/actions/runs/3')
    with pytest.raises(github_app.GitHubAppError,match='CROSS_REPOSITORY'):
        client.dispatch('Other/project','ci.yml','main',{})
    with pytest.raises(github_app.GitHubAppError,match='CROSS_REPOSITORY'):
        client.run_mutation('Other/project',123,'cancel')
    with pytest.raises(github_app.GitHubAppError,match='CROSS_REPOSITORY'):
        client.artifact_download_location('Other/project',123)
    with pytest.raises(github_app.GitHubAppError,match='CROSS_REPOSITORY'):
        client.run_log_location('Other/project',123)
    with pytest.raises(github_app.GitHubAppError,match='CROSS_REPOSITORY'):
        client._request('https://api.github.com/repos/Mikeyphw/devtool-attacker/actions/runs')
    with pytest.raises(github_app.GitHubAppError,match='CAPABILITY_REFUSED'):
        client.dispatch('Mikeyphw/devtool','ci.yml','main',{})
    with pytest.raises(github_app.GitHubAppError,match='CAPABILITY_REFUSED'):
        client.run_mutation('Mikeyphw/devtool',123,'cancel')
    assert issued==[]


def test_app_client_reissues_safely_on_read_again(monkeypatch):
    monkeypatch.setattr('devtool.federation_app_auth.installed_app_environment',
                        lambda:{'DEVTOOL_GITHUB_APP_ID':'M7'})
    seen=[]
    class P:
        def issue(self,repository,capability):
            seen.append((repository,capability))
            return access(capability=capability,token='SCOPED')
    monkeypatch.setattr(github_app.AppInstallationProvider,'from_environment',
                        lambda *a,**k:P())
    client=github_app.make_app_client('Mikeyphw/devtool','actions_read')
    assert client._token=='SCOPED'
    assert client._token=='SCOPED'
    assert seen==[('Mikeyphw/devtool','actions_read')]*2


def test_actions_full_cli_route_uses_refreshing_app_client(monkeypatch,tmp_path):
    from devtool.github_actions import actions_cli
    got=[]
    class Fake:
        def workflows(self,slug):
            got.append(('workflows',slug))
            return [{'id':1,'name':'ci','path':'.github/workflows/ci.yml'}]
    def factory(args,repository):
        got.append(('scope',repository,args.actions_command))
        return Fake(),'github-app'
    monkeypatch.setattr('devtool.github_actions._actions_client',factory)
    result=actions_cli(_a('workflows'),tmp_path) if False else actions_cli(
        SimpleNamespace(actions_command='workflows',github_repo='Mikeyphw/devtool',auth='app'),
        tmp_path)
    assert got[0]==('scope','Mikeyphw/devtool','workflows')
    assert got[1]==('workflows','Mikeyphw/devtool')
    assert 'github-app' in json.dumps(result) or result.get('workflows') is not None


def test_protected_gh_setup_is_not_app_scoped(monkeypatch):
    monkeypatch.setattr('devtool.ga01_scoped_auth.issue_for_operation',
                        lambda *a,**k:pytest.fail('secret-write token minted'))
    with pytest.raises(GitHubActionsError,match='NOT_SUPPORTED'):
        _actions_token(_a('release','setup'),'Mikeyphw/devtool')
