"""ACTIONS-10: fail-closed remote unsigned build/import and native LIFE signing."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import struct
import subprocess
import zipfile
from types import SimpleNamespace
from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from devtool.actions_unsigned import (
    WORKFLOW, WORKFLOW_PATH, ARTIFACT_NAME, APK_NAME, MANIFEST_NAME,
    _no_apk_signatures, _manifest, _imported, _publish_exclusive, emit_manifest, inspect_unsigned,
    unsigned_import, unsigned_dispatch, unsigned_local_sign,
)
from devtool.github_actions import GitHubActionsError, actions_cli
from devtool.cli import build_parser

SHA = 'd'*40
CHECKSUM = 'c'*64
REPO = 'Mikeyphw/devtool'


def fake_apk(path: Path, *, signing_file: bool = False):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('AndroidManifest.xml', b'<manifest/>')
        z.writestr('classes.dex', b'classes')
        if signing_file:
            z.writestr('META-INF/FILE.RSA', b'fake signature')
    return path


def info(path: Path) -> dict:
    return {'package': 'io.github.mikeceead.devtoolhud', 'version_code': 31,
            'version_name': '1.17.0', 'debuggable': False, 'signing_state': 'unsigned',
            'sha256': __import__('hashlib').sha256(path.read_bytes()).hexdigest(),
            'size_bytes': path.stat().st_size, 'qualified_release': False,
            'signature_verified': False}


def make_manifest(apk: Path, *, run_id: int = 42, attempt: int = 2):
    data= {'schema_version':1,'kind':'devtool.unsigned-android-apk',
           'repository':REPO,'head_sha':SHA,'run_id':run_id,'run_attempt':attempt,
           'ref':'refs/heads/main','workflow_path':WORKFLOW_PATH,
           'artifact_name':ARTIFACT_NAME,'apk_file':APK_NAME,
           'application_id':'io.github.mikeceead.devtoolhud',
           'apk_sha256':info(apk)['sha256'],'apk_size_bytes':apk.stat().st_size,
           'version_code':31,'version_name':'1.17.0','variant':'release',
           'format':'apk','debuggable':False,'signing_state':'unsigned',
           'qualified_release':False,'source_attested':False}
    return data


def archive(tmp_path: Path, apk: Path, data: dict, extras: dict[str, bytes] | None = None) -> Path:
    target=tmp_path/'artifact.zip'
    with zipfile.ZipFile(target,'w') as z:
        z.writestr(MANIFEST_NAME, json.dumps(data).encode())
        z.writestr(APK_NAME, apk.read_bytes())
        for name, content in (extras or {}).items():
            z.writestr(name,content)
    return target


def remote(**kwargs):
    return {'id':42,'repository':REPO,'run_attempt':2,'head_sha':SHA,
            'workflow_id':999,'status':'completed','conclusion':'success',
            'event':'workflow_dispatch',**kwargs}


class Client:
    def get_run(self, slug, identity):
        return {'id':identity,'status':'completed','conclusion':'success',
                'event':'workflow_dispatch','repository':{'full_name':REPO},
                'head_sha':SHA,'run_attempt':2,'workflow_id':999}

    def workflow(self, slug, name):
        return {'id':999,'path':WORKFLOW_PATH}

    def get_artifact(self, slug, id):
        return {'id':id,'name':ARTIFACT_NAME,'expired':False,'size_in_bytes':1000,
                'digest':'sha256:'+CHECKSUM,
                'workflow_run':{'id':42,'head_sha':SHA}}


def test_action10_canonical_parser_and_offline_sign_cli(monkeypatch, tmp_path):
    parser=build_parser()
    for name in ('unsigned-plan','unsigned-run','unsigned-import','unsigned-check','unsigned-sign'):
        args=['actions','android',name]
        if name in ('unsigned-plan','unsigned-run'):
            args+=['--ref','main']
        assert parser.parse_args(args).android_command == name
    args=parser.parse_args('actions android unsigned-sign --registry-id abc --json'.split())
    monkeypatch.setattr('devtool.github_actions.resolve_repository',lambda *a: (_ for _ in ()).throw(AssertionError('network')))
    monkeypatch.setattr('devtool.github_actions.resolve_token',lambda *a: (_ for _ in ()).throw(AssertionError('secret')))
    monkeypatch.setattr('devtool.actions_unsigned.unsigned_local_sign',lambda *a,**k: {'locally_signed':False})
    assert actions_cli(args,tmp_path)['locally_signed'] is False
    from devtool.core_registry import build_core_registry
    assert build_core_registry().resolve_command(['actions','android','unsigned-sign'])


def test_dedicated_workflow_excludes_keys_and_keeps_protected_lane():
    root=Path(__file__).resolve().parents[1]
    unsigned=(root/'.github/workflows/devtool-android-unsigned-release.yml').read_text()
    protected=(root/'.github/workflows/devtool-android-build.yml').read_text()
    gradle=(root/'android/devtool-live-hud/app/build.gradle').read_text()
    assert 'workflow_dispatch:' in unsigned and 'contents: read' in unsigned
    assert 'devtoolAssembleUnsignedReleaseCandidate' in unsigned
    assert 'DEVTOOL_RELEASE_KEYSTORE_B64' not in unsigned
    assert 'actions/upload-artifact@v4' in unsigned
    assert 'GITHUB_REF_PROTECTED' in protected
    assert 'DEVTOOL_RELEASE_KEYSTORE_B64' in protected
    assert 'Release signing is required.' in gradle
    assert "dependsOn tasks.named('verifyReleaseSigning')" in gradle
    assert "if (!unsignedCandidate)" in gradle
    assert "gradle.startParameter.taskNames == [unsignedTask]" in gradle
    assert 'releaseSigningReady' in gradle


def test_apk_signature_evidence_rejected_before_android_tools(tmp_path,monkeypatch):
    unsigned=fake_apk(tmp_path/'unsigned.apk')
    _no_apk_signatures(unsigned)
    signed=fake_apk(tmp_path/'signed.apk',signing_file=True)
    with pytest.raises(GitHubActionsError,match='JAR signing'):_no_apk_signatures(signed)
    corrupt=tmp_path/'corrupt.apk';corrupt.write_bytes(b'not a zip')
    with pytest.raises(GitHubActionsError):_no_apk_signatures(corrupt)
    monkeypatch.setattr('devtool.actions_unsigned._tool',lambda n:n)
    monkeypatch.setattr('devtool.actions_unsigned.subprocess.run',lambda *a,**k:SimpleNamespace(returncode=0))
    with pytest.raises(GitHubActionsError,match='signature verification passed'):inspect_unsigned(unsigned)
    monkeypatch.setattr('devtool.actions_unsigned.subprocess.run',lambda *a,**k:SimpleNamespace(returncode=1))
    monkeypatch.setattr('devtool.actions_unsigned._run',lambda *a,**k:"package: name='io.github.mikeceead.devtoolhud' versionCode='31' versionName='1.17.0'\napplication-debuggable\n")
    with pytest.raises(GitHubActionsError,match='debuggable'):inspect_unsigned(unsigned)
    monkeypatch.setattr('devtool.actions_unsigned._run',lambda *a,**k:"package: name='other.pkg' versionCode='31' versionName='1.17.0'\n")
    with pytest.raises(GitHubActionsError,match='identity'):inspect_unsigned(unsigned)
    monkeypatch.setattr('devtool.actions_unsigned._run',lambda *a,**k:"package: name='io.github.mikeceead.devtoolhud' versionCode='31' versionName='1.17.0'\n")
    assert inspect_unsigned(unsigned)['signing_state']=='unsigned'


def test_apk_signing_block_footer_rejected(tmp_path):
    base=fake_apk(tmp_path/'base.apk').read_bytes()
    # Insert a syntactically valid v2/v3 signing-block marker at CD boundary.
    eocd=base.rfind(b'PK\x05\x06')
    start=struct.unpack_from('<I',base,eocd+16)[0]
    block=b'\x00'*8+b'APK Sig Block 42'
    signed=bytearray(base[:start]+block+base[start:])
    struct.pack_into('<I',signed,eocd+len(block)+16,start+len(block))
    target=tmp_path/'block.apk';target.write_bytes(signed)
    with pytest.raises(GitHubActionsError,match='signing block'):_no_apk_signatures(target)


def test_ci_manifest_emission_never_claims_attestation(tmp_path,monkeypatch):
    apk=fake_apk(tmp_path/'unsigned.apk')
    monkeypatch.setattr('devtool.actions_unsigned.inspect_unsigned',lambda *a:info(apk))
    path=tmp_path/'manifest.json'
    data=emit_manifest(apk,path,{'GITHUB_SHA':SHA,'GITHUB_REPOSITORY':REPO,
                                 'GITHUB_RUN_ID':'42','GITHUB_RUN_ATTEMPT':'2',
                                 'GITHUB_REF':'refs/heads/main'})
    assert json.loads(path.read_text())==data
    assert data['qualified_release'] is False and data['source_attested'] is False
    with pytest.raises(GitHubActionsError,match='source identity'):
        emit_manifest(apk,path,{'GITHUB_SHA':SHA,'GITHUB_REPOSITORY':'Another/repo'})


def test_manifest_exact_payload_source_attempt_and_digest(tmp_path):
    apk=fake_apk(tmp_path/'unsigned.apk')
    data=make_manifest(apk)
    path=archive(tmp_path,apk,data)
    checked,_=_manifest(path,remote(),{'name':ARTIFACT_NAME},expected_sha=SHA)
    assert checked==data
    for key,bad in [('repository','Not/repo'),('head_sha','f'*40),('run_id',50),
                    ('run_attempt',1),('workflow_path','other.yml'),('variant','debug'),
                    ('qualified_release',True),('source_attested',True),
                    ('signing_state','signed'),('application_id','com.malware.test'),
                    ('apk_sha256','a'*64),('apk_size_bytes',999),('debuggable',True)]:
        broken={**data,key:bad}
        with pytest.raises(GitHubActionsError):
            _manifest(archive(tmp_path,apk,broken),remote(),{'name':ARTIFACT_NAME},expected_sha=SHA)
    for unsafe in ('../escape.txt','extra.sh'):
        with pytest.raises(GitHubActionsError):
            _manifest(archive(tmp_path,apk,data,{unsafe:b'test'}),remote(),{'name':ARTIFACT_NAME},expected_sha=SHA)
    with pytest.raises(GitHubActionsError,match='source'):
        _manifest(archive(tmp_path,apk,data),remote(),{'name':ARTIFACT_NAME},expected_sha='f'*40)


def test_import_checks_run_workflow_source_and_registers_immutable(monkeypatch,tmp_path):
    monkeypatch.delattr(os, 'link', raising=False)  # Android/Termux stdlib compatibility
    root=tmp_path/'repo';root.mkdir()
    apk=fake_apk(tmp_path/'unsigned.apk')
    data=make_manifest(apk)
    packed=archive(tmp_path,apk,data)
    monkeypatch.setattr('devtool.actions_unsigned.verify_download',lambda *a,**k:{'file':str(packed)})
    monkeypatch.setattr('devtool.actions_unsigned.inspect_unsigned',lambda *a:info(apk))
    monkeypatch.setattr('devtool.actions_unsigned.register_paths',lambda root,files,**k:[{'id':'receipt'}])
    cli=Client()
    preview=unsigned_import(cli,REPO,root,run_id=42,artifact_id=90,expected_sha=SHA,materialize=False)
    assert preview['unsigned_verified'] and preview['qualified_release'] is False
    assert not (root/'.devtool'/'artifacts'/'android-unsigned').exists()
    signed=unsigned_import(cli,REPO,root,run_id=42,artifact_id=90,expected_sha=SHA)
    assert signed['registry_id']=='receipt' and Path(signed['package_file']).read_bytes()==apk.read_bytes()
    with pytest.raises(GitHubActionsError,match='already imported'):
        unsigned_import(cli,REPO,root,run_id=42,artifact_id=90,expected_sha=SHA)
    class Wrong(Client):
        def workflow(self,*a):return {'id':444,'path':WORKFLOW_PATH}
    with pytest.raises(GitHubActionsError,match='workflow identity'):
        unsigned_import(Wrong(),REPO,root,run_id=42,artifact_id=90,expected_sha=SHA)
    class Failed(Client):
        def get_run(self,*a):return {**super().get_run(*a),'conclusion':'failure'}
    with pytest.raises(GitHubActionsError,match='successful'):
        unsigned_import(Failed(),REPO,root,run_id=42,artifact_id=90,expected_sha=SHA)


def test_unsigned_dispatch_ref_source_bound_no_token_or_secrets(monkeypatch,tmp_path):
    gradle=tmp_path/'android'/'devtool-live-hud';gradle.mkdir(parents=True)
    monkeypatch.setattr('devtool.actions_unsigned._gradle_modules',lambda *a:{'app':{}})
    monkeypatch.setattr('devtool.actions_unsigned._gradle_bootstrap',lambda *a:{'mode':'gradle-wrapper'})
    monkeypatch.setattr('devtool.actions_unsigned._git',lambda root,*args:SHA if args[0]=='rev-parse' else '')
    calls=[]
    def send(client,slug,wf,ref,inputs,*,execute):
        calls.append((wf,ref,inputs,execute))
        return {'workflow_path':WORKFLOW_PATH,'pre_dispatch_sha':SHA,'request_id':'test'}
    monkeypatch.setattr('devtool.actions_unsigned.dispatch_workflow',send)
    result=unsigned_dispatch(object(),REPO,tmp_path,ref='main',execute=True)
    assert result['dispatched'] and result['qualified_release'] is False
    assert calls==[(WORKFLOW,'main',{},False),(WORKFLOW,'main',{},True)]
    with pytest.raises(GitHubActionsError,match='dedicated'):
        unsigned_dispatch(object(),'Other/repo',tmp_path,ref='main')
    monkeypatch.setattr('devtool.actions_unsigned._git',lambda root,*args:SHA if args[0]=='rev-parse' else ' M app/build.gradle')
    with pytest.raises(GitHubActionsError,match='clean'):
        unsigned_dispatch(object(),REPO,tmp_path,ref='main')


def test_sign_requires_known_import_and_checks_bytes(tmp_path,monkeypatch):
    root=tmp_path/'repo';root.mkdir()
    p=root/'.devtool'/'artifacts'/'android-unsigned';p.mkdir(parents=True)
    apk=fake_apk(p/'unsigned.apk')
    metadata={'producer':'github-actions','state':'unsigned-release-unqualified',
              'remote_repository':REPO,'remote_workflow_path':WORKFLOW_PATH,
              'remote_head_sha':SHA,'remote_run_id':42,'remote_run_attempt':2,
              'remote_artifact_id':90,'signing_state':'unsigned',
              'archive_verified':True,'signature_verified':False,'qualified_release':False,
              'package':'io.github.mikeceead.devtoolhud','package_sha256':info(apk)['sha256'],
              'version_code':31}
    item={'id':'receipt','path':str(apk.relative_to(root)),'sha256':info(apk)['sha256'],
          'size':apk.stat().st_size,'metadata':metadata}
    monkeypatch.setattr('devtool.actions_unsigned.load_registry',lambda root:{'items':[item]})
    assert _imported(root,'receipt')[0]==apk
    apk.write_bytes(b'tampered')
    with pytest.raises(GitHubActionsError,match='changed'):_imported(root,'receipt')
    fake_apk(apk)
    apk.unlink();apk.symlink_to(tmp_path/'untracked.apk')
    with pytest.raises(GitHubActionsError,match='trusted import storage'):_imported(root,'receipt')


def test_unsigned_sign_plan_is_readonly_and_no_github(monkeypatch,tmp_path):
    apk=fake_apk(tmp_path/'sample.apk')
    meta={'package_sha256':info(apk)['sha256'],'version_code':31,
          'remote_repository':REPO,'remote_run_id':42,'remote_run_attempt':2,
          'remote_head_sha':SHA}
    monkeypatch.setattr('devtool.actions_unsigned._imported',lambda *a: (apk,meta))
    monkeypatch.setattr('devtool.actions_unsigned.inspect_unsigned',lambda *a:info(apk))
    profile=SimpleNamespace(mode='devtool',identity_id='life',expected_cert_sha256=CHECKSUM,alias='release',keystore='/key')
    monkeypatch.setattr('devtool.actions_setup._signing_profile',lambda *a,**k:profile)
    monkeypatch.setattr('devtool.actions_setup._keystore',lambda *a:tmp_path/'key')
    monkeypatch.setattr('devtool.actions_setup._signer_material',lambda *a:(_ for _ in ()).throw(AssertionError('private material on preview')))
    result=unsigned_local_sign(tmp_path,registry_id='receipt')
    assert result['qualified_release'] is False and result['locally_signed'] is False
    assert result['signing_identity_id']=='life'


def test_local_life_sign_flow_publishes_without_hard_links_or_release_qualification(monkeypatch,tmp_path):
    monkeypatch.delattr(os, 'link', raising=False)  # Termux stdlib compatibility
    root=tmp_path/'repo';root.mkdir()
    apk=fake_apk(tmp_path/'candidate.apk')
    unsigned=info(apk)
    metadata={'package_sha256':unsigned['sha256'],'version_code':31,
              'remote_repository':REPO,'remote_run_id':42,'remote_run_attempt':2,
              'remote_head_sha':SHA}
    profile=SimpleNamespace(mode='devtool',identity_id='life',expected_cert_sha256=CHECKSUM,
                            alias='release',keystore=str(tmp_path/'private.p12'))
    monkeypatch.setattr('devtool.actions_unsigned._imported',lambda *a:(apk,metadata))
    monkeypatch.setattr('devtool.actions_unsigned.inspect_unsigned',lambda *a:dict(unsigned))
    monkeypatch.setattr('devtool.actions_setup._signing_profile',lambda *a,**k:profile)
    monkeypatch.setattr('devtool.actions_setup._keystore',lambda *a:Path(profile.keystore))
    monkeypatch.setattr('devtool.actions_setup._signer_material',lambda *a:(Path(profile.keystore),
              {'DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD':'private-pw',
               'DEVTOOL_ANDROID_SIGNING_KEY_PASSWORD':'private-key-pw'},CHECKSUM))
    monkeypatch.setattr('devtool.actions_local_release._local_signing_password',lambda *a:nullcontext())
    monkeypatch.setattr('devtool.actions_unsigned._tool',lambda name:'/usr/bin/'+name)
    calls=[]
    def execute(cmd, **kwargs):
        calls.append(cmd)
        if 'zipalign' in cmd[0] and '-c' not in cmd:
            Path(cmd[-1]).write_bytes(Path(cmd[-2]).read_bytes())
        if 'apksigner' in cmd[0]:
            Path(cmd[cmd.index('--out')+1]).write_bytes(b'signed bytes for unit test')
            assert 'private-pw' not in repr(cmd) and 'private-key-pw' not in repr(cmd)
            assert kwargs['env']['DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD']=='private-pw'
        return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
    monkeypatch.setattr('devtool.actions_unsigned.subprocess.run',execute)
    def verify(root, **kwargs):
        p=Path(kwargs['artifact'])
        return {'artifact_sha256':__import__('hashlib').sha256(p.read_bytes()).hexdigest(),
                'qualified_release':False,'signature_verified':True}
    monkeypatch.setattr('devtool.actions_local_release.local_verify',verify)
    registered=[]
    def register(root,files,**kwargs):
        registered.append(kwargs['metadata'])
        return [{'id':'signed-id'}]
    monkeypatch.setattr('devtool.actions_unsigned.register_paths',register)
    result=unsigned_local_sign(root,registry_id='receipt',execute=True)
    assert result['locally_signed'] and result['signature_verified']
    assert result['qualified_release'] is False
    assert Path(result['signed_package']).read_bytes()==b'signed bytes for unit test'
    assert registered[0]['unsigned_sha256']==unsigned['sha256']
    assert registered[0]['qualified_release'] is False
    assert registered[0]['remote_head_sha']==SHA
    assert len(calls)==3 and sum('apksigner' in c[0] for c in calls)==1
    with pytest.raises(GitHubActionsError,match='already exists'):
        unsigned_local_sign(root,registry_id='receipt',execute=True)


def test_exclusive_apk_publication_preserves_immutability_without_os_link(tmp_path, monkeypatch):
    monkeypatch.delattr(os, 'link', raising=False)
    payload=tmp_path/'candidate.apk'
    payload.write_bytes(b'APK test source')
    import hashlib
    expected=hashlib.sha256(payload.read_bytes()).hexdigest()
    output=tmp_path/'published.apk'
    _publish_exclusive(payload, output, expected)
    assert output.read_bytes()==payload.read_bytes()
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(GitHubActionsError, match='already exists'):
        _publish_exclusive(payload, output, expected)
    assert output.read_bytes()==b'APK test source'


def test_exclusive_apk_publication_rejects_symlink_and_cleans_failed_copy(tmp_path, monkeypatch):
    monkeypatch.delattr(os, 'link', raising=False)
    source=tmp_path/'source.apk';source.write_bytes(b'verified apk')
    target=tmp_path/'signed.apk'
    existing=tmp_path/'existing.apk'; existing.write_bytes(b'do not overwrite')
    target.symlink_to(existing)
    expected=__import__('hashlib').sha256(source.read_bytes()).hexdigest()
    with pytest.raises(GitHubActionsError, match='already exists'):
        _publish_exclusive(source, target, expected)
    assert target.is_symlink() and existing.read_bytes()==b'do not overwrite'
    target.unlink()
    with pytest.raises(GitHubActionsError, match='checksum'):
        _publish_exclusive(source, target, '0'*64)
    assert not target.exists()
    with pytest.raises(GitHubActionsError, match='exact SHA-256'):
        _publish_exclusive(source, target, 'broken')
    assert not target.exists()


def test_exclusive_apk_publication_missing_source_does_not_leave_destination(tmp_path):
    source=tmp_path/'missing.apk';target=tmp_path/'output.apk'
    with pytest.raises(FileNotFoundError):
        _publish_exclusive(source, target, 'a'*64)
    assert not target.exists()
