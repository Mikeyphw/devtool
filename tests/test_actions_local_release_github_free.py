"""Offline GitHub Free/private Android release regression gates."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from devtool.cli import build_parser
from devtool.github_actions import GitHubActionsError, actions_cli
from devtool.actions_local_release import local_verify, local_build

CERT = 'a' * 64


def profile(store: Path):
    return SimpleNamespace(mode='devtool', expected_cert_sha256=CERT,
                           identity_id='release-life', alias='release', keystore=str(store),
                           store_password_env='DT_RELEASE_PRIVATE_PW')


def test_parser_and_canonical_surface_registry():
    args = build_parser().parse_args('actions release local-build --signing-identity release-life --json'.split())
    assert args.release_command == 'local-build' and not args.yes
    assert build_parser().parse_args('actions release local-verify --artifact /tmp/release.apk'.split()).artifact == '/tmp/release.apk'
    with pytest.raises(SystemExit):
        build_parser().parse_args('actions release local-verify'.split())
    from devtool.core_registry import build_core_registry
    assert build_core_registry().resolve_command(['actions', 'release', 'local-verify'])['command'].stable_id.endswith('.local_verify')


def test_local_cli_never_resolves_github_auth(monkeypatch, tmp_path):
    args = build_parser().parse_args('actions release local-build --json'.split())
    monkeypatch.setattr('devtool.github_actions.resolve_repository', lambda *a: (_ for _ in ()).throw(AssertionError('GitHub used')))
    monkeypatch.setattr('devtool.github_actions.resolve_token', lambda *a: (_ for _ in ()).throw(AssertionError('GitHub used')))
    monkeypatch.setattr('devtool.actions_local_release.local_build', lambda root, **kwargs: {'qualified_release': False, 'offline': True})
    assert actions_cli(args, tmp_path)['offline']


def test_local_verify_rejects_wrong_cert_debug_and_metadata(monkeypatch, tmp_path):
    root = tmp_path / 'repo'; root.mkdir()
    key = tmp_path / 'release.p12'; key.write_bytes(b'private-dummy'); key.chmod(0o600)
    apk = tmp_path / 'app-release.apk'; apk.write_bytes(b'APK signed test fixture bytes')
    monkeypatch.setattr('devtool.actions_local_release._signing_profile', lambda *a, **k: profile(key))
    monkeypatch.setattr('devtool.actions_local_release._application', lambda *a: 'org.example.release')
    good = {'package':'org.example.release', 'signer_sha256':CERT,
            'version_code':'31', 'debug_signing':False, 'signature_verified':True, 'manifest_verified':True}
    monkeypatch.setattr('devtool.actions_android._inspect_apk', lambda *a:dict(good))
    result = local_verify(root, artifact=str(apk), minimum_version_code=31)
    assert result['local_signature_verified'] and result['artifact_sha256']
    assert not result['qualified_release'] and not result['github_provenance_verified'] and not result['source_attested']
    cases = [('signer_sha256', 'b'*64, 'certificate'), ('debug_signing', True, 'Debug'),
             ('package','other.pkg','application ID'), ('version_code','1','versionCode')]
    for field, value, msg in cases:
        monkeypatch.setattr('devtool.actions_android._inspect_apk', lambda *a, f=field, v=value: {**good, f:v})
        with pytest.raises(GitHubActionsError, match=msg):
            local_verify(root, artifact=str(apk), minimum_version_code=31)
    monkeypatch.setattr('devtool.actions_android._inspect_apk', lambda *a: dict(good))
    apk.unlink(); apk.symlink_to(key)
    with pytest.raises(GitHubActionsError, match='non-symlink'):
        local_verify(root, artifact=str(apk))


def test_plan_does_not_access_password_or_start_gradle(monkeypatch, tmp_path):
    root = tmp_path / 'repo'; root.mkdir();project=root/'android/devtool-live-hud';project.mkdir(parents=True)
    launch=project/'gradlew';launch.write_text('#!/bin/sh\n')
    store=tmp_path/'key.p12';store.write_bytes(b'private');store.chmod(0o600)
    monkeypatch.setattr('devtool.actions_local_release._tracked_state', lambda r:'a'*40)
    monkeypatch.setattr('devtool.actions_android._gradle_bootstrap', lambda r: {'mode':'fixture'})
    monkeypatch.setattr('devtool.actions_android.android_discover', lambda r, d:{'modules':[{'module':'app'}]})
    monkeypatch.setattr('devtool.actions_local_release._application', lambda *a:'org.example.release')
    monkeypatch.setattr('devtool.actions_local_release._signing_profile', lambda *a, **k: profile(store))
    monkeypatch.setattr('devtool.actions_local_release._signer_material', lambda *a: (_ for _ in ()).throw(AssertionError('password accessed during plan')))
    plan=local_build(root)
    assert plan['mode']=='plan' and not plan['qualified_release']
    assert plan['gradle_task']==':app:assembleRelease'


def test_existing_github_release_gates_unmodified():
    source=Path('devtool/actions_release.py').read_text()
    workflow=Path('.github/workflows/devtool-android-build.yml').read_text()
    assert 'gh attestation verify' in source
    assert 'GITHUB_REF_PROTECTED' in workflow
    assert 'if [[ "${GITHUB_REF_PROTECTED:-false}" != "true" ]]' in workflow
    assert 'qualified_release' in source


def test_local_build_child_only_secrets_and_fail_closed(monkeypatch, tmp_path):
    from devtool.models import ProjectConfig
    from devtool.actions_local_release import local_build
    root=tmp_path/'repo';root.mkdir();project=root/'android/devtool-live-hud';project.mkdir(parents=True)
    launch=project/'gradlew';launch.write_text('#!/bin/sh\n')
    store=tmp_path/'key.p12';store.write_bytes(b'private-key');store.chmod(0o600)
    init=tmp_path/'local.init.gradle';init.write_text('// no secrets')
    monkeypatch.setenv('DT_RELEASE_PRIVATE_PW','hidden-password-not-for-logs')
    monkeypatch.setattr('devtool.actions_local_release._tracked_state', lambda r:'a'*40)
    monkeypatch.setattr('devtool.actions_android._gradle_bootstrap', lambda r:{'mode':'fixture'})
    monkeypatch.setattr('devtool.actions_android.android_discover', lambda r,d:{'modules':[{'module':'app'}]})
    monkeypatch.setattr('devtool.actions_local_release._application', lambda *a:'org.example.release')
    monkeypatch.setattr('devtool.actions_local_release._signing_profile', lambda *a,**k:profile(store))
    monkeypatch.setattr('devtool.actions_local_release._signer_material', lambda *a:(store,{
        'DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD':'hidden-password-not-for-logs',
        'DEVTOOL_ANDROID_SIGNING_KEY_PASSWORD':'hidden-password-not-for-logs'},CERT))
    monkeypatch.setattr('devtool.android.signing.stage_gradle_signing_init_script',lambda *a,**k:init)
    monkeypatch.setattr('devtool.config.load_project_config',lambda *a,**k:ProjectConfig(repo_root=root,kind='android'))
    monkeypatch.setattr('devtool.android.variants.activate_android_variant',lambda cfg,v:cfg)
    monkeypatch.setattr('devtool.actions_local_release.local_verify',lambda *a,**k:{'qualified_release':False,'local_signature_verified':True})
    calls=[]
    def fake_gradle(argv,**kwargs):
        calls.append((argv,dict(kwargs['env'])))
        assert argv[-1]==':app:assembleRelease'
        assert 'hidden-password-not-for-logs' not in ' '.join(argv)
        assert kwargs['env']['DEVTOOL_RELEASE_STORE_PASSWORD']=='hidden-password-not-for-logs'
        assert kwargs['env']['DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD']=='hidden-password-not-for-logs'
        output=project/'app/build/outputs/apk/release';output.mkdir(parents=True)
        (output/'app-release.apk').write_bytes(b'fixture-apk')
        return SimpleNamespace(returncode=0,stdout=b'secret leak from build output hidden-password-not-for-logs',stderr=b'')
    monkeypatch.setattr('devtool.actions_local_release.subprocess.run',fake_gradle)
    report=local_build(root,execute=True)
    assert report['verification']['local_signature_verified']
    assert report['qualified_release'] is False and report['source_attested'] is False
    assert 'hidden-password-not-for-logs' not in repr(report)
    assert calls
    def failed(argv,**kwargs):
        return SimpleNamespace(returncode=1,stdout=b'hidden-password-not-for-logs',stderr=b'hidden-password-not-for-logs')
    monkeypatch.setattr('devtool.actions_local_release.subprocess.run',failed)
    with pytest.raises(GitHubActionsError) as info:
        local_build(root,execute=True)
    assert 'hidden-password-not-for-logs' not in str(info.value)


def test_interactive_signing_secret_does_not_survive_exception(monkeypatch):
    from devtool.actions_local_release import _local_signing_password
    monkeypatch.delenv('DT_RELEASE_PRIVATE_PW',raising=False)
    monkeypatch.setattr('devtool.actions_local_release.sys.stdin',SimpleNamespace(isatty=lambda:True))
    monkeypatch.setattr('devtool.actions_local_release.getpass.getpass',lambda *_:'hidden')
    with pytest.raises(RuntimeError):
        with _local_signing_password(SimpleNamespace(store_password_env='DT_RELEASE_PRIVATE_PW')):
            assert os.environ['DT_RELEASE_PRIVATE_PW']=='hidden'
            raise RuntimeError('sentinel')
    assert 'DT_RELEASE_PRIVATE_PW' not in os.environ


def test_real_devtool_config_release_activation_accepts_immutable_test_inventory():
    """Reproduces the real Termux mappingproxy failure, not a mocked launcher."""
    from devtool.android.variants import activate_android_variant
    from devtool.config import load_project_config

    repo = Path(__file__).resolve().parents[1]
    config = load_project_config(str(repo), None, target_name='android_hud')
    assert config.tests and len(config.tests) > 10
    release = activate_android_variant(config, 'release')
    assert release.variant == 'release'
    assert config.variant != 'release'
    assert release.tests is not config.tests
    assert release.tests.keys() == config.tests.keys()
    assert all(release.tests[key] is config.tests[key] for key in config.tests)
    release.runner_options['android'] = {'unrelated': {'mutable': 'only-clone'}}
    assert release.runner_options != config.runner_options


def test_variant_activation_deep_copies_mutable_options_and_legacy_test_entries():
    from devtool.android.variants import activate_android_variant
    from devtool.models import ProjectConfig, TargetConfig
    from devtool.test_model import TestSpec

    fixture = ProjectConfig(repo_root=Path('/tmp/repo'), kind='android', selected_target='app',
        env={'BASE':'1'}, runner_options={'android':{'nested':{'before':['original']}}},
        tests={'immutable':TestSpec(id='immutable', name='fixture', runner='pytest',
                                     metadata={'declared': True}),
               'legacy':{'nested':['original']}},
        targets={'app': TargetConfig(name='app',variants={'release':{'env':{'RELEASE':'true'},
                  'android':{'nested':{'after':['release']}}}})})
    activated = activate_android_variant(fixture,'release')
    assert activated.env == {'BASE':'1','RELEASE':'true'}
    assert fixture.env == {'BASE':'1'}
    assert activated.tests['immutable'] is fixture.tests['immutable']
    activated.tests['legacy']['nested'].append('only-clone')
    assert fixture.tests['legacy']['nested'] == ['original']
    activated.runner_options['android']['nested']['before'].append('only-clone')
    assert fixture.runner_options['android']['nested']['before'] == ['original']
