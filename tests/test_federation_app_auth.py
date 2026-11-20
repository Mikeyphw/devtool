from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from devtool.federation_app_auth import (
    fetch_from_github_app, github_slug_for_locator, installed_app_environment,
)
from devtool.github_app import GitHubAppError, run_git_authenticated
from devtool.federation_config import FederationConfigError, federation_as_dict, load_federation_config
from devtool.federation_sync import _fetch_origin, _plan_bootstrap, FederationSyncError

KEYS = ('ID', 'INSTALLATION_ID', 'ACCOUNT', 'PRIVATE_KEY_PATH')


def test_default_federation_auth_remains_git_without_schema_drift(tmp_path):
    (tmp_path/'owner').mkdir()
    (tmp_path/'child').mkdir()
    c=load_federation_config({'schema':1,'repositories':{
        'child':{'path':'../child'}}},owner_root=tmp_path/'owner')
    assert c.repositories['child'].auth == 'git'
    assert 'auth' not in federation_as_dict(c)['repositories']['child']


def test_explicit_app_auth_is_repository_scoped_and_serialized(tmp_path):
    (tmp_path/'owner').mkdir()
    (tmp_path/'child').mkdir()
    c=load_federation_config({'schema':1,'repositories':{
        'child':{'path':'../child','auth':'app'}}},owner_root=tmp_path/'owner')
    assert c.repositories['child'].auth == 'app'
    assert federation_as_dict(c)['repositories']['child']['auth'] == 'app'


@pytest.mark.parametrize('value',[True, 0, None.__class__, 'auto','App','token','gh','',[]])
def test_invalid_auth_is_refused(tmp_path,value):
    (tmp_path/'owner').mkdir()
    (tmp_path/'child').mkdir()
    with pytest.raises(FederationConfigError,match="must be 'git' or 'app'"):
        load_federation_config({'schema':1,'repositories':{
            'child':{'path':'../child','auth':value}}},owner_root=tmp_path/'owner')


@pytest.mark.parametrize('locator',[
    'git@github.com:Mikeyphw/devtool.git',
    'ssh://git@github.com/Mikeyphw/devtool.git',
    'http://github.com/Mikeyphw/devtool.git',
    'https://other.github.com/Mikeyphw/devtool.git',
    'https://user:token@github.com/Mikeyphw/devtool.git',
    'https://github.com/Other/repo.git?token=foo',
    'https://github.com/Mikeyphw/devtool.git#frag',
    'https://github.com/Mikeyphw/devtool.git\n',
])
def test_git_locator_fail_closed(locator):
    with pytest.raises(GitHubAppError, match='EXACT_HTTPS_GITHUB'):
        github_slug_for_locator(locator)


def test_https_locator_exact_and_stable():
    assert github_slug_for_locator('https://github.com/Mikeyphw/devtool.git')=='Mikeyphw/devtool'
    assert github_slug_for_locator('https://github.com/Mikeyphw/devtool')=='Mikeyphw/devtool'


def _env_file(tmp_path, text):
    directory=tmp_path/'private'
    directory.mkdir(mode=0o700)
    os.chmod(directory,0o700)
    f=directory/'env.sh'
    f.write_text(text,encoding='utf-8')
    f.chmod(0o600)
    return f


def test_existing_termux_private_env_file_reused_without_shell_execution(tmp_path):
    f=_env_file(tmp_path,'\n'.join([
        'export DEVTOOL_GITHUB_APP_ID=5853103',
        'export DEVTOOL_GITHUB_APP_INSTALLATION_ID=184133864',
        'export DEVTOOL_GITHUB_APP_ACCOUNT=Mikeyphw',
        "export DEVTOOL_GITHUB_APP_PRIVATE_KEY_PATH='"+str(tmp_path/'key with spaces.pem')+"'",'']))
    got=installed_app_environment({},config=f)
    assert got['DEVTOOL_GITHUB_APP_ID']=='5853103'
    assert got['DEVTOOL_GITHUB_APP_ACCOUNT']=='Mikeyphw'
    assert got['DEVTOOL_GITHUB_APP_PRIVATE_KEY_PATH'].endswith('key with spaces.pem')


def test_secure_app_env_refuses_literal_command_substitution(tmp_path):
    marker=tmp_path/'SHOULD_NOT_EXIST'
    f=_env_file(tmp_path,'\n'.join([
        'export DEVTOOL_GITHUB_APP_ID=1',
        'export DEVTOOL_GITHUB_APP_INSTALLATION_ID=2',
        'export DEVTOOL_GITHUB_APP_ACCOUNT=Mikeyphw',
        'export DEVTOOL_GITHUB_APP_PRIVATE_KEY_PATH=$(touch '+str(marker)+')',
    ]))
    with pytest.raises(GitHubAppError,match='SYNTAX|NONLITERAL'):
        installed_app_environment({},config=f)
    assert not marker.exists()


def test_env_file_world_readable_or_symlink_refused(tmp_path):
    f=_env_file(tmp_path,'export DEVTOOL_GITHUB_APP_ID=1\n')
    f.chmod(0o644)
    with pytest.raises(GitHubAppError,match='UNSAFE'):
        installed_app_environment({},config=f)
    f.chmod(0o600)
    symlink=f.parent/'symlink'
    symlink.symlink_to(f)
    with pytest.raises(GitHubAppError,match='SYMLINK'):
        installed_app_environment({},config=symlink)


def test_partial_explicit_env_does_not_fallback_to_file(tmp_path):
    f=_env_file(tmp_path,'export DEVTOOL_GITHUB_APP_ID=1\n')
    with pytest.raises(GitHubAppError,match='PARTIAL'):
        installed_app_environment({'DEVTOOL_GITHUB_APP_ID':'1'},config=f)


def test_fetch_only_approved_git_argv_and_repository_read_scope(monkeypatch,tmp_path):
    import devtool.github_app as ga
    monkeypatch.setattr(ga,'_https_origin',lambda *args: None)
    access=SimpleNamespace(repository='Mikeyphw/devtool',capability='repository_read',
                           token='never-display-this-token',expires_at=int(time.time())+3600)
    seen=[]
    def fake_run(argv,**kwargs):
        seen.append((argv,kwargs))
        assert access.token not in ' '.join(argv)
        assert access.token not in str(kwargs['env'])
        assert os.read(int(kwargs['env']['DEVTOOL_APP_AUTH_FD']),4096).decode()==access.token
        return subprocess.CompletedProcess(argv,0,stdout='ok',stderr='')
    monkeypatch.setattr(ga.subprocess,'run',fake_run)
    assert run_git_authenticated(tmp_path,['fetch','--prune','origin'],access).returncode==0
    assert len(seen)==1
    for args in (['fetch','origin'],['fetch','--prune','https://evil/'],
                 ['fetch','--upload-pack=/tmp/x','origin'],['fetch','--prune','origin','other']):
        with pytest.raises(GitHubAppError,match='FETCH_SCOPE_OR_ARGUMENTS'):
            run_git_authenticated(tmp_path,args,access)
    assert len(seen)==1
    access.capability='branch_write'
    with pytest.raises(GitHubAppError,match='FETCH_SCOPE_OR_ARGUMENTS'):
        run_git_authenticated(tmp_path,['fetch','--prune','origin'],access)


def test_app_fetch_uses_installed_config_and_scoped_token(monkeypatch,tmp_path):
    import devtool.federation_app_auth as auth
    monkeypatch.setattr(auth,'git_remote_locator',lambda root:'https://github.com/Mikeyphw/devtool.git')
    monkeypatch.setattr(auth,'installed_app_environment',lambda: {'DEVTOOL_GITHUB_APP_ID':'1'})
    calls=[]
    class FakeProvider:
        @classmethod
        def from_environment(cls,env):
            calls.append(('env',env))
            return cls()
        def issue(self,repo,scope):
            calls.append(('issue',repo,scope))
            return SimpleNamespace(repository=repo,capability=scope)
    monkeypatch.setattr(auth,'AppInstallationProvider',FakeProvider)
    def fake_git(root,argv,credential,timeout):
        calls.append(('git',list(argv),credential.capability,timeout))
        return subprocess.CompletedProcess(argv,0,stdout='',stderr='')
    monkeypatch.setattr(auth,'run_git_authenticated',fake_git)
    result=fetch_from_github_app(tmp_path,'https://github.com/Mikeyphw/devtool.git')
    assert result.returncode==0
    assert ('issue','Mikeyphw/devtool','repository_read') in calls
    assert ('git',['fetch','--prune','origin'],'repository_read',120) in calls


def test_frozen_remote_mismatch_prevents_token_issue(monkeypatch,tmp_path):
    import devtool.federation_app_auth as auth
    monkeypatch.setattr(auth,'git_remote_locator',lambda root:'https://github.com/Mikeyphw/other.git')
    monkeypatch.setattr(auth,'installed_app_environment',lambda: pytest.fail('must not load credentials'))
    with pytest.raises(GitHubAppError,match='REMOTE_MISMATCH'):
        fetch_from_github_app(tmp_path,'https://github.com/Mikeyphw/devtool.git')


def test_explicit_app_fetch_never_falls_back_to_pat(monkeypatch,tmp_path):
    import devtool.federation_app_auth as auth
    def denied(root,locator):
        raise GitHubAppError('APP_INSTALLATION_PERMISSION_MISSING')
    monkeypatch.setattr(auth,'fetch_from_github_app',denied)
    with patch('devtool.federation_sync._git') as legacy:
        with pytest.raises(FederationSyncError,match='APP_INSTALLATION_PERMISSION_MISSING'):
            _fetch_origin(tmp_path,auth='app',locked_locator='https://github.com/Mikeyphw/devtool')
        legacy.assert_not_called()


def test_default_fetch_reuses_legacy_ssh(monkeypatch,tmp_path):
    with patch('devtool.federation_sync._git',return_value='') as legacy:
        _fetch_origin(tmp_path,auth='git',locked_locator='git@github.com:Mikeyphw/devtool.git')
        legacy.assert_called_once_with(tmp_path,'fetch','--prune','origin',timeout=120)


def test_app_bootstrap_dry_run_refuses_ssh_before_network_or_mutation(tmp_path):
    parent=SimpleNamespace(repo_root=tmp_path/'owner',federation=SimpleNamespace(repositories={
        'child':SimpleNamespace(root=str(tmp_path/'child'),auth='app')}))
    parent.repo_root.mkdir()
    locator='git@github.com:Mikeyphw/child.git'
    locked=SimpleNamespace(repository='child',source_kind='git',dirty=False,start_commit='a'*40,
                           remote_locator=locator,remote_locator_digest=hashlib.sha256(locator.encode()).hexdigest())
    result=_plan_bootstrap(parent,locked,apply=False,operation_id='x')
    assert result.state == 'blocked-app-origin'
    assert not (tmp_path/'child').exists()
