"""DT-10: opted-in federation must attest local source + both App identities.

Mocked GitHub provider only. This is not evidence of live App permissions.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import types
from types import SimpleNamespace

import pytest


SHA='a'*40
ORIGIN='https://github.com/Bob/fork.git'


@pytest.fixture
def harness(tmp_path, monkeypatch):
    # Import real production module from branch checkout. In the isolated local
    # preflight environment use the explicitly provided source file via env.
    from devtool import github_app, federation_lock
    from devtool import federation_app_auth as installed
    source=Path(__file__).resolve().parent.parent/'devtool'/'federation_app_auth.py'
    candidate=Path(__import__('os').environ.get('GA02_HOTFIX_PRODUCTION_SOURCE',source))
    spec=importlib.util.spec_from_file_location('devtool.federation_app_auth',candidate)
    f=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(f)
    calls=[]
    refusals=[]
    owner=SimpleNamespace(repository='Alice/origin',repository_id=10,
                          owner_id=1,role='base',installation_id=200,
                          visibility='private',archived=False,suspended=False,
                          evidence='SCOPED_APP_READ_VERIFIED')
    target=SimpleNamespace(repository='Bob/fork',repository_id=20,
                          owner_id=2,role='head',installation_id=201,
                          visibility='private',archived=False,suspended=False,
                          evidence='SCOPED_APP_READ_VERIFIED')
    class Refused(RuntimeError): pass
    class Registry:
        source_sha=SHA
        def node(self,slug):
            return {'Alice/origin':SimpleNamespace(role='base',repository_id=10),
                    'Bob/fork':SimpleNamespace(role='head',repository_id=20)}[slug]
    registry=Registry()
    relationships=types.ModuleType('devtool.github_relationships')
    relationships.RelationshipRefused=Refused
    def opted_in_registry(*a,**kw):
        calls.append(('policy',kw.get('source_sha')))
        if kw.get('source_sha') != SHA:
            raise Refused('GA02_POLICY_SOURCE_SHA_DRIFT')
        return registry
    relationships.opted_in_registry=opted_in_registry
    def assert_observation(reg, observed):
        calls.append(('attest',observed.repository))
        exp=reg.node(observed.repository)
        if exp.repository_id != observed.repository_id:
            raise Refused('GA02_REPOSITORY_RECREATED_OR_RENAMED')
    relationships.assert_observation=assert_observation
    def authorize(reg,owner_repository,target):
        assert_observation(reg,target)
        calls.append(('authorize',owner_repository,target.repository))
    relationships.authorize_federation_source=authorize
    monkeypatch.setitem(sys.modules,'devtool.github_relationships',relationships)
    installations=types.ModuleType('devtool.github_installations')
    def observe(slug,**kwargs):
        calls.append(('observe',slug))
        return owner if slug=='Alice/origin' else target
    installations.observe_installed=observe
    monkeypatch.setitem(sys.modules,'devtool.github_installations',installations)
    monkeypatch.setattr(f,'git_remote_locator',lambda root:ORIGIN)
    monkeypatch.setattr(f,'installed_app_environment',lambda *a,**kw:{'x':'y'})
    monkeypatch.setattr(f,'run_git_authenticated',lambda *a,**kw:calls.append(('FETCH',)) or 'FETCHED')
    real_commit=f._local_commit
    monkeypatch.setattr(f,'_local_commit',lambda root:SHA)
    monkeypatch.setenv('DEVTOOL_GA02_RELATIONSHIPS_FILE','/nonexistent/pinned.json')
    monkeypatch.setenv('DEVTOOL_GA02_RELATIONSHIPS_SHA256','f'*64)
    monkeypatch.setenv('DEVTOOL_GA02_FEDERATION_OWNER','Alice/origin')
    class Provider:
        @staticmethod
        def from_environment(*a,**kwargs):
            return SimpleNamespace(issue=lambda *a:SimpleNamespace(repository_id=20))
    monkeypatch.setattr(f,'AppInstallationProvider',Provider)
    return SimpleNamespace(f=f,calls=calls,refused=Refused,owner=owner,target=target,
                           policy=relationships,installations=installations,registry=registry,real_commit=real_commit)


def test_authorized_fetch_attests_both_installations_then_fetches(harness,tmp_path):
    h=harness
    assert h.f.fetch_from_github_app(tmp_path,ORIGIN)=='FETCHED'
    assert h.calls[:5]==[('policy',SHA),('observe','Alice/origin'),
                          ('attest','Alice/origin'),('observe','Bob/fork'),
                          ('attest','Bob/fork')]
    assert ('authorize','Alice/origin','Bob/fork') in h.calls
    assert h.calls[-1]==('FETCH',)


def test_pinned_policy_rejects_different_local_commit(harness,tmp_path,monkeypatch):
    h=harness
    monkeypatch.setattr(h.f,'_local_commit',lambda root:'b'*40)
    with pytest.raises(h.refused,match='GA02_POLICY_SOURCE_SHA_DRIFT'):
        h.f.fetch_from_github_app(tmp_path,ORIGIN)
    assert ('FETCH',) not in h.calls


def test_public_owner_cannot_authorize(harness,tmp_path):
    h=harness
    original=h.registry.node
    h.registry.node=lambda slug: SimpleNamespace(role='public',repository_id=10) if slug=='Alice/origin' else original(slug)
    with pytest.raises(h.refused,match='PUBLIC_OWNER'):
        h.f.fetch_from_github_app(tmp_path,ORIGIN)
    assert ('FETCH',) not in h.calls


def test_owner_identity_drift_is_refused_before_target(harness,tmp_path):
    h=harness
    h.owner.repository_id=999
    with pytest.raises(h.refused,match='REPOSITORY_RECREATED'):
        h.f.fetch_from_github_app(tmp_path,ORIGIN)
    assert ('observe','Bob/fork') not in h.calls and ('FETCH',) not in h.calls


def test_target_identity_drift_is_refused_before_credential(harness,tmp_path):
    h=harness
    h.target.repository_id=999
    with pytest.raises(h.refused,match='REPOSITORY_RECREATED'):
        h.f.fetch_from_github_app(tmp_path,ORIGIN)
    assert ('FETCH',) not in h.calls


def test_midflight_commit_drift_before_git_fetch(harness,tmp_path,monkeypatch):
    h=harness
    seq=iter((SHA,'b'*40))
    monkeypatch.setattr(h.f,'_local_commit',lambda root:next(seq))
    with pytest.raises(h.refused,match='SOURCE_SHA_DRIFT'):
        h.f.fetch_from_github_app(tmp_path,ORIGIN)
    assert ('FETCH',) not in h.calls


def test_midflight_origin_drift_before_git_fetch(harness,tmp_path,monkeypatch):
    h=harness
    seq=iter((ORIGIN,'https://github.com/Mallory/fork.git'))
    monkeypatch.setattr(h.f,'git_remote_locator',lambda root:next(seq))
    with pytest.raises(h.refused,match='REMOTE_CHANGED'):
        h.f.fetch_from_github_app(tmp_path,ORIGIN)
    assert ('FETCH',) not in h.calls


def test_issued_credential_repo_id_drift_rejected(harness,tmp_path,monkeypatch):
    h=harness
    class Provider:
        @staticmethod
        def from_environment(*a,**kw):
            return SimpleNamespace(issue=lambda *a: SimpleNamespace(repository_id=31))
    monkeypatch.setattr(h.f,'AppInstallationProvider',Provider)
    with pytest.raises(h.refused,match='ACCESS_REPOSITORY_ID_DRIFT'):
        h.f.fetch_from_github_app(tmp_path,ORIGIN)
    assert ('FETCH',) not in h.calls


def test_legacy_no_policy_preserves_m4_fetch(harness,tmp_path,monkeypatch):
    h=harness
    monkeypatch.delenv('DEVTOOL_GA02_RELATIONSHIPS_FILE')
    monkeypatch.delenv('DEVTOOL_GA02_RELATIONSHIPS_SHA256')
    h.policy.opted_in_registry=lambda *a,**kw:None
    monkeypatch.setattr(h.f,'_local_commit',lambda root:pytest.fail('M4 no-policy must not require Git HEAD probe'))
    assert h.f.fetch_from_github_app(tmp_path,ORIGIN)=='FETCHED'
    assert ('FETCH',) in h.calls


def test_real_git_head_probe_is_commit_bound(tmp_path,harness,monkeypatch):
    root=tmp_path/'repo';root.mkdir()
    subprocess.run(['git','-C',str(root),'init','-q'],check=True)
    subprocess.run(['git','-C',str(root),'config','user.name','Test'],check=True)
    subprocess.run(['git','-C',str(root),'config','user.email','test@example.invalid'],check=True)
    (root/'readme.txt').write_text('test')
    subprocess.run(['git','-C',str(root),'add','readme.txt'],check=True)
    subprocess.run(['git','-C',str(root),'commit','-qm','initial'],check=True)
    assert harness.real_commit(root)==subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    with pytest.raises(harness.refused,match='LOCAL_HEAD_UNVERIFIED'):
        harness.real_commit(tmp_path/'not-a-repo')
