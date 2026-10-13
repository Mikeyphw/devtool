"""G4 Android App Bundle trust, source binding, CLI and paid-gate isolation."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from devtool.actions_aab import (
    _bundle_zip, _unsigned_bundle, _archive_manifest, _test_receipt, _lint_receipt,
    _imported_bundle, _owner_only_directory, emit_aab_manifest, aab_dispatch,
    WORKFLOW_PATH, ARTIFACT_NAME, APP, BUNDLE, TESTS, LINT, MANIFEST,
)
from devtool.cli import build_parser
from devtool.github_actions import GitHubActionsError

SHA = 'a'*40
RUN = {'id':1234,'run_attempt':2,'head_sha':SHA,'head_branch':'main'}
ART = {'name':ARTIFACT_NAME}


def aab(path, *, name='base/manifest/AndroidManifest.xml', special=None):
    with zipfile.ZipFile(path,'w') as z:
        z.writestr(name,b'protobuf-encoded-manifest')
        z.writestr('BundleConfig.pb',b'bundle-config')
        z.writestr('base/dex/classes.dex',b'dex')
        if special: z.writestr(*special)
    return path


def source(tmp_path, *, bad=False):
    stage=tmp_path/'stage';stage.mkdir()
    bundle=aab(stage/BUNDLE)
    (stage/TESTS).write_text(json.dumps({'tests':8, 'failures':0,'errors':0,'skipped':1,
                                        'gradle_task':':app:testDebugUnitTest'}))
    (stage/LINT).write_text('<issues format="5"><issue id="Notice" severity="Warning" /></issues>')
    env={'GITHUB_SHA':SHA,'GITHUB_REPOSITORY':'Mikeyphw/devtool',
         'GITHUB_RUN_ID':'1234','GITHUB_RUN_ATTEMPT':'2','GITHUB_REF':'refs/heads/main'}
    emit_aab_manifest(bundle,stage/TESTS,stage/LINT,stage/MANIFEST,env)
    if bad: (stage/LINT).write_text('<issues><issue severity="Error" /></issues>')
    archive=tmp_path/'artifact.zip'
    with zipfile.ZipFile(archive,'w') as z:
        for name in (BUNDLE,TESTS,LINT,MANIFEST):z.write(stage/name,arcname=name)
    return archive


def test_g4_cli_explicit_surface_and_confirmation():
    parser=build_parser()
    for name in ('aab-plan','aab-run','aab-check','aab-import','aab-sign'):
        args=['actions','android',name,'--json']
        if name in ('aab-plan','aab-run'):args+=['--ref','main']
        obj=parser.parse_args(args)
        assert obj.android_command==name
        assert obj.json
        if name in ('aab-run','aab-sign'):assert not obj.yes


def test_g4_aab_zip_structure_and_signature_rejections(tmp_path):
    good=aab(tmp_path/'ok.aab')
    assert _bundle_zip(good)['size_bytes']>0
    for i,special in enumerate((('META-INF/CERT.SF','a'),('../escape','a'),('META-INF/SIG-A','a'))):
        path=aab(tmp_path/f'bad{i}.aab',special=special)
        with pytest.raises(GitHubActionsError):_bundle_zip(path)
    bad=tmp_path/'missing.aab'
    with zipfile.ZipFile(bad,'w') as z:z.writestr('base/dex/classes.dex',b'a')
    with pytest.raises(GitHubActionsError):_bundle_zip(bad)


def test_g4_aab_requires_native_tools_and_bundletool(tmp_path,monkeypatch):
    bundle=aab(tmp_path/'unsigned.aab')
    monkeypatch.delenv('DEVTOOL_BUNDLETOOL_JAR',raising=False)
    monkeypatch.setattr('devtool.actions_aab._tool',lambda s:'/bin/false')
    monkeypatch.setattr('devtool.actions_aab.subprocess.run',lambda *a,**k:SimpleNamespace(
        returncode=0,stdout=b'\njar is unsigned.\n',stderr=b''))
    with pytest.raises(GitHubActionsError,match='BUNDLETOOL_JAR'):_unsigned_bundle(bundle)


def test_g4_ci_evidence_manifest_checks(tmp_path):
    archive=source(tmp_path)
    out=_archive_manifest(archive,RUN,ART,SHA)
    assert out['head_sha']==SHA
    assert out['unit_tests']['tests']==8
    assert out['qualified_release'] is False
    with pytest.raises(GitHubActionsError):_archive_manifest(archive,{**RUN,'run_attempt':1},ART,SHA)
    with pytest.raises(GitHubActionsError):_archive_manifest(archive,RUN,{'name':'foreign'},SHA)
    with pytest.raises(GitHubActionsError):_archive_manifest(archive,RUN,ART,'b'*40)
    with pytest.raises(GitHubActionsError):_archive_manifest(archive,RUN,ART,'mutable-branch')


def test_g4_rejects_tampered_lint_even_with_manifest_sha(tmp_path):
    archive=source(tmp_path,bad=True)
    with pytest.raises(GitHubActionsError,match='digest mismatch'):_archive_manifest(archive,RUN,ART,SHA)


def test_g4_rejects_conflicting_manifest_identity_and_missing_receipts(tmp_path):
    archive=source(tmp_path)
    contents={}
    with zipfile.ZipFile(archive) as z:
        for name in (BUNDLE,TESTS,LINT,MANIFEST):contents[name]=z.read(name)
    manifest=json.loads(contents[MANIFEST]);manifest['run_attempt']=1
    contents[MANIFEST]=json.dumps(manifest).encode()
    broken=tmp_path/'broken.zip'
    with zipfile.ZipFile(broken,'w') as z:
        for name,data in contents.items():z.writestr(name,data)
    with pytest.raises(GitHubActionsError):_archive_manifest(broken,RUN,ART,SHA)
    for name in (LINT,TESTS):
        variant=tmp_path/f'no_{name}.zip'
        with zipfile.ZipFile(variant,'w') as z:
            for n,data in contents.items():
                if n!=name:z.writestr(n,data)
        with pytest.raises(GitHubActionsError):_archive_manifest(variant,RUN,ART,SHA)


@pytest.mark.parametrize('candidate',[
    {'tests':0,'failures':0,'errors':0,'skipped':0,'gradle_task':':app:testDebugUnitTest'},
    {'tests':2,'failures':1,'errors':0,'skipped':0,'gradle_task':':app:testDebugUnitTest'},
    {'tests':2,'failures':0,'errors':1,'skipped':0,'gradle_task':':app:testDebugUnitTest'},
    {'tests':2,'failures':0,'errors':0,'skipped':3,'gradle_task':':app:testDebugUnitTest'},
    {'tests':2,'failures':0,'errors':0,'skipped':0,'gradle_task':':app:testReleaseUnitTest'},
])
def test_g4_tests_reject_bad_counts(candidate):
    with pytest.raises(GitHubActionsError):_test_receipt(candidate)


@pytest.mark.parametrize('document',[b'<issues><issue severity="Error"/></issues>',
    b'<!DOCTYPE foo><issues/>',b'<root/>',b'<issues>'])
def test_g4_lint_rejects_errors_and_invalid_xml(document):
    with pytest.raises(GitHubActionsError):_lint_receipt(document)


def test_g4_github_dispatch_is_read_only_without_execute(monkeypatch,tmp_path):
    fake=SimpleNamespace()
    monkeypatch.setattr('devtool.actions_aab._project_root',lambda root,path:(tmp_path/'android','android/devtool-live-hud'))
    monkeypatch.setattr('devtool.actions_aab._gradle_modules',lambda root:{'app':{}})
    monkeypatch.setattr('devtool.actions_aab._gradle_bootstrap',lambda root:{'mode':'wrapper'})
    monkeypatch.setattr('devtool.actions_aab._git',lambda root,*args:SHA if args[0]=='rev-parse' else '')
    dispatched=[]
    def plan(client,slug,workflow,ref,inputs,execute=False):
        dispatched.append(execute)
        return {'workflow_path':WORKFLOW_PATH,'pre_dispatch_sha':SHA}
    monkeypatch.setattr('devtool.actions_aab.dispatch_workflow',plan)
    result=aab_dispatch(fake,'Mikeyphw/devtool',tmp_path,ref='main')
    assert not result['dispatched'] and dispatched==[False]
    with pytest.raises(GitHubActionsError):aab_dispatch(fake,'other/repo',tmp_path,ref='main')


def test_g4_import_requires_owner_only_paths(tmp_path):
    link=tmp_path/'.devtool';link.symlink_to(tmp_path)
    with pytest.raises(GitHubActionsError):_owner_only_directory(tmp_path,'android-aab-unsigned')


def test_g4_workflow_does_not_modify_existing_signing_lane():
    repo=Path(__file__).resolve().parents[1]
    path=repo/'.github/workflows/devtool-android-aab-candidate.yml'
    raw=path.read_text()
    assert 'bundle-candidate:' in raw and ':app:devtoolBundleUnsignedReleaseCandidate' in raw
    assert 'DEVTOOL_RELEASE_KEYSTORE_B64' not in raw
    assert "'testDebugUnitTest'" in (repo/'android/devtool-live-hud/app/build.gradle').read_text()
    assert 'lintRelease' in (repo/'android/devtool-live-hud/app/build.gradle').read_text()
    assert 'devtool-android-unsigned-release.yml' in (repo/'devtool/actions_unsigned.py').read_text()


def test_g4_bundletool_validates_package_and_version(tmp_path, monkeypatch):
    bundle=aab(tmp_path/'candidate.aab')
    jar=tmp_path/'bundletool.jar';jar.write_bytes(b'jar')
    monkeypatch.setenv('DEVTOOL_BUNDLETOOL_JAR',str(jar))
    monkeypatch.setattr('devtool.actions_aab._tool',lambda s:'/fake/'+s)
    calls=[]
    def run(cmd, purpose, timeout=45):
        calls.append((cmd,purpose))
        if cmd[-1]=='/manifest/@package':return APP
        if cmd[-1]=='/manifest/@android:versionCode':return '31'
        return ''
    monkeypatch.setattr('devtool.actions_aab._run',run)
    monkeypatch.setattr('devtool.actions_aab.subprocess.run',lambda *a,**k:SimpleNamespace(returncode=0, stdout=b'jar is unsigned.\n', stderr=b''))
    result=_unsigned_bundle(bundle)
    assert result['package']==APP and result['version_code']==31
    assert result['signature_verified'] is False
    assert len(calls)==3
    monkeypatch.setattr('devtool.actions_aab._run',lambda cmd,purpose,timeout=45:'unexpected.package' if cmd[-1]=='/manifest/@package' else '31')
    with pytest.raises(GitHubActionsError,match='identity mismatch'):_unsigned_bundle(bundle)


def test_g4_signed_aab_rejected_as_unsigned(tmp_path,monkeypatch):
    bundle=aab(tmp_path/'candidate.aab')
    monkeypatch.setattr('devtool.actions_aab._tool',lambda s:'/fake/'+s)
    monkeypatch.setattr('devtool.actions_aab.subprocess.run',lambda *a,**k:SimpleNamespace(returncode=0, stdout=b'jar verified.\n', stderr=b''))
    with pytest.raises(GitHubActionsError,match='Expected unsigned'):_unsigned_bundle(bundle)


def test_g4_signing_requires_real_registry_identity(tmp_path):
    from devtool.actions_aab import aab_local_sign
    with pytest.raises(GitHubActionsError):aab_local_sign(tmp_path,registry_id='malformed',execute=True)
