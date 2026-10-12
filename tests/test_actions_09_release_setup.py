"""ACTIONS-09 governed provisioning backed ONLY by existing Devtool signing."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from devtool.cli import build_parser
from devtool.actions_setup import (release_setup, release_readiness, _keystore, _write_policy,
                                   _gh, _branch, _signing_profile, _signer_material, SECRETS)
from devtool.github_actions import GitHubActionsError
from devtool.actions_release import release_policy
from devtool.artifact_trust import SigningIdentityRegistry, SecretProviderRef
from devtool.models import ProjectConfig
from devtool.android.signing import AndroidSigningManager, AndroidSigningError

SHA='a'*40
SIGNER='f'*64

class Client:
    protected=False
    def repository(self, slug):
        return {'full_name':slug,'default_branch':'main'}
    def workflow(self, slug, identifier):
        # Only a GitHub-supported identifier is allowed for API lookup.
        assert identifier == 'devtool-android-build.yml'
        return {'path':'.github/workflows/devtool-android-build.yml','state':'active'}
    def _get(self,path):
        if '/commits/' in path:return {'sha':SHA}
        if '/branches/' in path:return {'protected':self.protected}
        raise AssertionError(path)

@pytest.fixture
def canonical_signer(monkeypatch,tmp_path):
    root=tmp_path/'repo';root.mkdir()
    store=tmp_path/'release.p12';store.write_bytes(b'mock keystore private material');store.chmod(0o600)
    registry=SigningIdentityRegistry(tmp_path/'identities.json')
    registry.create_reference(identity_id='life-release', provider='android-keystore-pkcs12',
        key_reference=str(store),alias='release',certificate_sha256=SIGNER,
        purposes=('android-signing',),variants=('release',),
        secret_provider=SecretProviderRef('local','MY_EXISTING_DEVTOOL_STOREPASS'))
    monkeypatch.setenv('DEVTOOL_SIGNING_IDENTITY_REGISTRY',str(registry.path))
    cfg=ProjectConfig(repo_root=root,kind='android',runner_options={'android':{'signing':{'release':{'mode':'devtool','policy':'release-required','identity_id':'life-release'}}}})
    monkeypatch.setattr('devtool.config.load_project_config',lambda *a,**k:cfg)
    monkeypatch.setenv('MY_EXISTING_DEVTOOL_STOREPASS','existing-password')
    return root,store,registry


def test_parser_and_canonical_registry():
    p=build_parser()
    args=p.parse_args('actions release setup --signing-identity life-release --json'.split())
    assert args.actions_command=='release' and args.release_command=='setup' and not args.yes
    assert args.signing_identity=='life-release'
    assert p.parse_args('actions release readiness --ref main'.split()).release_command=='readiness'
    with pytest.raises(SystemExit):p.parse_args('actions release setup --keystore /tmp/new.p12'.split())
    from devtool.core_registry import build_core_registry
    reg=build_core_registry()
    assert reg.resolve_command(['actions','release','setup'])['command'].stable_id.endswith('.setup')


def test_plan_never_reads_material_or_mutates(canonical_signer,monkeypatch,tmp_path):
    root,_,_=canonical_signer
    monkeypatch.setattr('devtool.actions_setup._source',lambda root:SHA)
    monkeypatch.setattr('devtool.actions_setup._secrets',lambda s:set())
    monkeypatch.setattr('devtool.actions_setup._gh',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('called')))
    monkeypatch.setattr('devtool.actions_setup._signer_material',lambda *a,**k:(_ for _ in ()).throw(AssertionError('secret touched')))
    p=tmp_path/'policy.json'
    plan=release_setup(Client(),'Owner/repo',root,policy_file=str(p))
    assert plan['mode']=='plan' and not plan['ready'] and not plan['qualified_release']
    assert plan['signing_identity_id']=='life-release'
    assert plan['gates']['signing_identity_configured']
    assert plan['secret_names_missing']==list(SECRETS)
    assert not p.exists()


def test_readiness_only_when_all_gates_pass(canonical_signer,tmp_path,monkeypatch):
    root,_,_=canonical_signer;p=tmp_path/'trust.json'
    monkeypatch.setattr('devtool.actions_setup._secrets',lambda s:set(SECRETS))
    doc={'schema':1,'repository':'Owner/repo','workflow':'.github/workflows/devtool-android-build.yml',
         'ref':'refs/heads/main','source_sha':SHA,'application_id':'org.example.release',
         'signer_sha256':SIGNER,'format':'apk','minimum_version_code':1}
    p.write_text(json.dumps(doc));p.chmod(0o600)
    c=Client();c.protected=True
    report=release_readiness(c,'Owner/repo',root,ref='main',policy_file=str(p),source_sha=SHA)
    assert report['ready'] and not report['qualified_release']
    assert report['gates']['signing_identity_configured']
    doc['signer_sha256']='b'*64;p.write_text(json.dumps(doc))
    assert not release_readiness(c,'Owner/repo',root,ref='main',policy_file=str(p))['ready']
    doc['signer_sha256']=SIGNER;doc['source_sha']='b'*40;p.write_text(json.dumps(doc))
    assert not release_readiness(c,'Owner/repo',root,ref='main',policy_file=str(p))['ready']


def test_existing_signing_manager_is_authority(canonical_signer,monkeypatch):
    root,store,_=canonical_signer
    profile=_signing_profile(root)
    assert profile.identity_id=='life-release' and profile.keystore==str(store)
    assert profile.expected_cert_sha256==SIGNER
    inspected=[]
    def inspect(self,profile,*,store_password=None):
        inspected.append((profile.identity_id,store_password))
        return SimpleNamespace(sha256=SIGNER,expired=False)
    monkeypatch.setattr(AndroidSigningManager,'inspect_profile',inspect)
    item,secrets,cert=_signer_material(root,profile)
    assert item==store and cert==SIGNER
    assert secrets['DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD']=='existing-password'
    assert secrets['DEVTOOL_ANDROID_SIGNING_KEY_PASSWORD']=='existing-password'
    assert inspected==[('life-release','existing-password')]


def test_setup_sends_existing_secret_provider_via_stdin_and_creates_policy(canonical_signer,monkeypatch,tmp_path):
    root,store,_=canonical_signer
    policy=tmp_path/'policy.json';events=[];names=set();client=Client()
    monkeypatch.setattr('devtool.actions_setup._source',lambda root:SHA)
    monkeypatch.setattr('devtool.actions_setup._application',lambda *a:'org.example.release')
    monkeypatch.setattr('devtool.actions_setup._secrets',lambda s:set(names))
    monkeypatch.setattr(AndroidSigningManager,'inspect_profile',lambda self,profile,*,store_password=None:SimpleNamespace(sha256=SIGNER,expired=False))
    def gh(argv,*,input_text=None,**kwargs):
        assert 'existing-password' not in repr(argv)
        events.append((tuple(argv),input_text))
        if argv[:2]==['api','--method']:
            client.protected=True
            assert 'non_fast_forward' in input_text and 'deletion' in input_text
        elif argv[:2]==['secret','set']:
            names.add(argv[2]);assert input_text
        return ''
    monkeypatch.setattr('devtool.actions_setup._gh',gh)
    result=release_setup(client,'Owner/repo',root,policy_file=str(policy),execute=True)
    assert result['ready'] and result['policy_created'] and not result['qualified_release']
    assert result['signing_identity_id']=='life-release'
    assert len(events)==5 and names==set(SECRETS)
    assert policy.stat().st_mode & 0o777==0o600
    assert release_policy(root,str(policy))['signer_sha256']==SIGNER
    again=release_setup(client,'Owner/repo',root,policy_file=str(policy),execute=True)
    assert not again['policy_created']
    assert len([x for x in events if x[0][:2]==('api','--method')])==1
    assert len([x for x in events if x[0][:2]==('secret','set')])==4  # existing values preserved


def test_no_unregistered_or_conflicting_signer_can_be_exported(canonical_signer,monkeypatch,tmp_path):
    root,store,_=canonical_signer
    monkeypatch.setattr('devtool.actions_setup._source',lambda root:SHA)
    monkeypatch.setattr('devtool.actions_setup._secrets',lambda s:set())
    monkeypatch.setattr('devtool.actions_setup._gh',lambda *a,**k:(_ for _ in ()).throw(AssertionError('remote mutation')))
    with pytest.raises(GitHubActionsError,match='conflicts'):
        release_setup(Client(),'Owner/repo',root,signing_identity='another',execute=True)
    store.chmod(0o644)
    with pytest.raises(GitHubActionsError,match='private'):
        release_setup(Client(),'Owner/repo',root,execute=True)
    store.chmod(0o600)
    with pytest.raises(GitHubActionsError,match='outside'):
        _keystore(str(root/'store.p12'),root)


def test_existing_policy_conflict_precedes_remote_mutation(canonical_signer,tmp_path,monkeypatch):
    root,_,_=canonical_signer; policy=tmp_path/'policy.json';policy.write_text('{}');policy.chmod(0o600)
    monkeypatch.setattr('devtool.actions_setup._source',lambda root:SHA)
    monkeypatch.setattr('devtool.actions_setup._secrets',lambda s:set())
    monkeypatch.setattr('devtool.actions_setup._gh',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('mutation')))
    monkeypatch.setattr('devtool.actions_setup._signer_material',lambda *a,**k:(_ for _ in ()).throw(AssertionError('signing material accessed')))
    # The policy must be parsed and rejected ahead of reading the signer.
    # Read-only readiness flags policy mismatch, apply then fails closed.
    with pytest.raises(GitHubActionsError):
        release_setup(Client(),'Owner/repo',root,policy_file=str(policy),execute=True)
    assert policy.read_text()=='{}'


def test_ambiguous_identity_never_selected_implicitly(canonical_signer,monkeypatch):
    root,_,registry=canonical_signer
    registry.create_reference(identity_id='other-sign', provider='android-keystore-pkcs12',
        key_reference='/tmp/missing',alias='x',certificate_sha256='b'*64,
        purposes=('android',), variants=('release',), secret_provider=SecretProviderRef('local','OTHER_PW'))
    # Force unconfigured profile -> fallback LIFE unique selection requirement.
    monkeypatch.setattr('devtool.config.load_project_config',lambda *a,**k:ProjectConfig(repo_root=root,kind='android'))
    with pytest.raises(GitHubActionsError,match='Multiple'):
        _signing_profile(root)
    assert _signing_profile(root,identity_id='life-release').identity_id=='life-release'


def test_ref_safety_and_gh_redacts_failed_response(monkeypatch):
    for ref in ['refs/heads/main','../main','bad//name','feature@{1}','a.lock','']:
        with pytest.raises(GitHubActionsError):_branch(ref)
    monkeypatch.setattr('devtool.actions_setup.shutil.which',lambda x:'/usr/bin/gh')
    monkeypatch.setattr('devtool.actions_setup.subprocess.run',lambda *args,**kwargs:subprocess.CompletedProcess(args,1,'secret-output','secret-stderr'))
    with pytest.raises(GitHubActionsError) as info:
        _gh(['secret','set','NAME'],input_text='my-secret')
    assert not any(x in str(info.value) for x in ('secret-output','secret-stderr','my-secret'))


def test_private_policy_creation_rejects_in_repo_symlinks(tmp_path):
    from devtool.actions_setup import _policy_target
    root=tmp_path/'repo';root.mkdir();out=tmp_path/'private.json'
    with pytest.raises(GitHubActionsError,match='outside'):_policy_target(root,str(root/'policy.json'))
    out.symlink_to(root/'missing')
    with pytest.raises(GitHubActionsError,match='symlink'):_policy_target(root,str(out))


def test_release_build_uses_existing_gradle_signing_contract_before_packaging(tmp_path):
    """ACTIONS-09 must supply the existing Gradle release inputs *at build time*."""
    import base64
    import os
    import yaml
    live=Path('.github/workflows/devtool-android-build.yml').read_text()
    assert live==Path('docs/templates/devtool-android-build.yml').read_text()
    steps=yaml.safe_load(live)['jobs']['build']['steps']
    debug=next(x for x in steps if x.get('name')=='Build Android debug output')
    release=next(x for x in steps if x.get('name')=='Build Android signed release output')
    assert "inputs.variant == 'debug'" in debug['if']
    assert "inputs.variant == 'release'" in release['if']
    assert not debug.get('env')
    assert len(release['env'])==4
    for name in ('DEVTOOL_RELEASE_STORE_FILE','DEVTOOL_RELEASE_STORE_PASSWORD',
                 'DEVTOOL_RELEASE_KEY_ALIAS','DEVTOOL_RELEASE_KEY_PASSWORD'):
        assert f'export {name}=' in release['run']
    assert 'trap \'rm -f "$store"\' EXIT' in release['run']
    assert release['run'].index('export DEVTOOL_RELEASE_STORE_FILE') < release['run'].index('./gradlew')
    # Exercise shell contract using a fake Gradle launcher, not real credentials.
    root=tmp_path/'gradleproj';root.mkdir();
    gradle=root/'gradlew';gradle.write_text('''#!/bin/bash
set -eu
test -s "$DEVTOOL_RELEASE_STORE_FILE"
test "$DEVTOOL_RELEASE_KEY_ALIAS" = release
test "$DEVTOOL_RELEASE_STORE_PASSWORD" = pw-store
test "$DEVTOOL_RELEASE_KEY_PASSWORD" = pw-key
test "$1" = --no-daemon
test "$2" = :app:assembleRelease
''');gradle.chmod(0o700)
    env=dict(os.environ,RUNNER_TEMP=str(tmp_path),GITHUB_REF_PROTECTED='true',
             TASK_PROJECT=str(root),TASK_MODULE='app',TASK_NAME='assembleRelease',
             DT_KEYSTORE_B64=base64.b64encode(b'private dummy material').decode(),
             DT_STORE_PASSWORD='pw-store',DT_KEY_ALIAS='release',DT_KEY_PASSWORD='pw-key')
    ok=subprocess.run(['bash','-c',release['run']],env=env,capture_output=True,text=True)
    assert ok.returncode==0,ok.stderr
    assert not (tmp_path/'devtool-gradle-release-keystore.p12').exists()
    env['DT_STORE_PASSWORD']=''
    bad=subprocess.run(['bash','-c',release['run']],env=env,capture_output=True,text=True)
    assert bad.returncode!=0
    assert not (tmp_path/'devtool-gradle-release-keystore.p12').exists()


def test_partial_github_secrets_fail_closed_without_explicit_rotation(canonical_signer,monkeypatch,tmp_path):
    root,_,_=canonical_signer
    monkeypatch.setattr('devtool.actions_setup._source',lambda root:SHA)
    monkeypatch.setattr('devtool.actions_setup._secrets',lambda s:{SECRETS[0]})
    monkeypatch.setattr('devtool.actions_setup._gh',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('remote mutation')))
    monkeypatch.setattr(AndroidSigningManager,'inspect_profile',lambda self,profile,*,store_password=None:SimpleNamespace(sha256=SIGNER,expired=False))
    monkeypatch.setattr('devtool.actions_setup._application',lambda *a:'org.example.release')
    with pytest.raises(GitHubActionsError,match='Partial GitHub release secrets'):
        release_setup(Client(),'Owner/repo',root,policy_file=str(tmp_path/'policy.json'),execute=True)
    assert not (tmp_path/'policy.json').exists()


def test_invalid_signing_certificate_blocks_remote_mutation(canonical_signer,monkeypatch,tmp_path):
    root,_,_=canonical_signer
    monkeypatch.setattr('devtool.actions_setup._source',lambda root:SHA)
    monkeypatch.setattr('devtool.actions_setup._secrets',lambda s:set())
    monkeypatch.setattr('devtool.actions_setup._gh',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('remote mutation')))
    monkeypatch.setattr(AndroidSigningManager,'inspect_profile',lambda self,profile,*,store_password=None:SimpleNamespace(sha256='0'*64,expired=False))
    with pytest.raises(GitHubActionsError,match='does not match'):
        release_setup(Client(),'Owner/repo',root,policy_file=str(tmp_path/'policy.json'),execute=True)


def test_release_readiness_uses_real_github_workflow_identifier(canonical_signer, monkeypatch, tmp_path):
    """Exercise GitHubActionsClient's real input validator and returned-path pinning."""
    from devtool.github_actions import GitHubActionsClient
    root, _, _ = canonical_signer
    monkeypatch.setattr('devtool.actions_setup._secrets', lambda s: set())

    class RealClient(GitHubActionsClient):
        def __init__(self):
            super().__init__(token='')
            self.requested = []
            self.reported_path = '.github/workflows/devtool-android-build.yml'

        def repository(self, slug):
            return {'full_name': slug, 'default_branch': 'main'}

        def _get(self, path):
            self.requested.append(path)
            if path.endswith('/commits/main'):
                return {'sha': SHA}
            if path.endswith('/branches/main'):
                return {'protected': True}
            if path.endswith('/actions/workflows/devtool-android-build.yml'):
                return {'id': 381518746, 'path': self.reported_path, 'state': 'active',
                        'name': 'Devtool Android Build'}
            raise AssertionError('Unexpected request: ' + path)

    client = RealClient()
    result = release_readiness(client, 'Owner/repo', root, ref='main',
                               policy_file=str(tmp_path / 'nonexistent.json'))
    assert result['gates']['workflow_active']
    assert not result['ready'] and not result['qualified_release']
    assert any(path.endswith('/actions/workflows/devtool-android-build.yml')
               for path in client.requested)
    assert not any('/actions/workflows/.github/' in path for path in client.requested)

    client.reported_path = '.github/workflows/unexpected.yml'
    rejected = release_readiness(client, 'Owner/repo', root, ref='main',
                                 policy_file=str(tmp_path / 'nonexistent.json'))
    assert not rejected['gates']['workflow_active']
    assert not rejected['ready']


def test_signer_failures_report_nonsecret_categories(canonical_signer, monkeypatch):
    from devtool.actions_setup import _signing_inspection_failure
    from devtool.github_actions import GitHubActionsError
    root, _, _ = canonical_signer
    profile = _signing_profile(root)
    sentinel = 'SENSITIVE_PASSWORD_DO_NOT_PRINT'
    cases = [
        ('Could not inspect keystore (exit 1): java.io.IOException: keystore password was incorrect ' + sentinel,
         'keystore-password-rejected'),
        ("Signing alias 'wrong' was not found in /tmp/key.p12 " + sentinel, 'signing-alias-not-found'),
        ('Configured signing certificate SHA-256 does not match the keystore identity ' + sentinel,
         'certificate-fingerprint-mismatch'),
        ('Signing tool execution failed: [Errno 2] No such file or directory ' + sentinel,
         'keytool-unavailable-or-unexecutable'),
        ('Could not inspect keystore: unknown provider exception ' + sentinel,
         'keystore-inspection-failed'),
    ]
    assert _signing_inspection_failure(AndroidSigningError(cases[0][0])) == 'keystore-password-rejected'
    for reason, category in cases:
        def fail(self, profile, *, store_password=None):
            raise AndroidSigningError(reason)
        monkeypatch.setattr(AndroidSigningManager, 'inspect_profile', fail)
        with pytest.raises(GitHubActionsError, match=category) as result:
            _signer_material(root, profile)
        assert sentinel not in str(result.value)
        assert result.value.__cause__ is None
    # Never downgrade wrong-password or missing-provider failures to a release.
    monkeypatch.delenv('MY_EXISTING_DEVTOOL_STOREPASS')
    with pytest.raises(GitHubActionsError, match='secret-resolution-failed') as result:
        _signer_material(root, profile)
    assert result.value.__cause__ is None


def test_signer_expiry_still_denied_with_safe_diagnostics(canonical_signer, monkeypatch):
    from devtool.github_actions import GitHubActionsError
    root, _, _ = canonical_signer
    profile = _signing_profile(root)
    monkeypatch.setattr(AndroidSigningManager, 'inspect_profile',
                        lambda self, profile, *, store_password=None:
                        SimpleNamespace(sha256=SIGNER, expired=True))
    with pytest.raises(GitHubActionsError, match='expired'):
        _signer_material(root, profile)


def test_keytool_inspection_error_is_classified_from_all_lines_without_leak(monkeypatch, tmp_path):
    manager = AndroidSigningManager()
    monkeypatch.setattr(manager, '_keytool', lambda: 'keytool')
    profile = __import__('devtool.android.signing', fromlist=['SigningProfile']).SigningProfile(
        keystore=str(tmp_path / 'key.p12'), alias='release', store_type='PKCS12')
    secret = 'DONT_PRINT_KEYTOOL_SECRET'
    captured = [
        (1, 'keytool error: java.io.IOException: keystore password was incorrect ' + secret + '\n'
            '  at java.base/something\n\t... 6 more', 'password was incorrect'),
        (1, 'keytool error: java.io.IOException: Invalid keystore format ' + secret + '\n\t... 3 more',
         'Invalid keystore format'),
        (1, 'java.io.IOException: unknown failure ' + secret + '\n\t... 4 more',
         'diagnostic withheld'),
    ]
    for rc, stderr, marker in captured:
        monkeypatch.setattr(manager, '_run', lambda *a, **k: (rc, stderr))
        with pytest.raises(AndroidSigningError, match=marker) as exc:
            manager.list_aliases(profile, store_password='not-logged')
        assert secret not in str(exc.value)

