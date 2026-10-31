"""Android R31: source/run/attempt/cert/bytes/registry fail-closed contract."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import pytest

from devtool.actions_android import android_plan
from devtool.actions_android_handoff import handoff
from devtool.artifact_registry import register_paths,registry_path
from devtool.github_actions import GitHubActionsError

CERT='a'*64
REPO='Mikeyphw/relatorio'
WORKFLOW='devtool-android-variants.yml'
PACKAGE='com.example.relatorio'


def _git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()


def _fixture(tmp_path):
    root=tmp_path/'consumer';root.mkdir()
    subprocess.run(['git','init','-q',str(root)],check=True)
    (root/'README.md').write_text('consumer source\n')
    (root/'.gitignore').write_text('.devtool/\n')
    _git(root,'add','README.md','.gitignore')
    subprocess.run(['git','-C',str(root),'-c','user.email=test@invalid.test','-c','user.name=Test','commit','-qm','source'],check=True)
    sha=_git(root,'rev-parse','HEAD')
    source=b'pretend verified APK output for offline registry fixture'
    digest=hashlib.sha256(source).hexdigest()
    storage=root/'.devtool'/'artifacts'/'android-verified'
    storage.mkdir(parents=True)
    file=storage/f'github-run300-artifact41-{digest[:16]}.apk'
    file.write_bytes(source)
    meta={'producer':'github-actions','state':'android-inspected-unqualified',
          'remote_repository':REPO,'remote_run_id':300,'remote_run_attempt':2,
          'remote_workflow_id':48,'remote_head_sha':sha,'remote_artifact_id':41,
          'workflow':WORKFLOW,'apk_role':'application','instrumentation_target_package':'',
          'package':PACKAGE,'format':'apk','variant':'debug','signer_sha256':CERT,
          'package_sha256':digest,'signature_verified':True,'qualified_release':False,
          'release_gate':'not-implemented'}
    record=register_paths(root,[file],metadata=meta)[0]
    collected={'phase':'ACTIONS-05','repository':REPO,'run_id':300,'artifact_id':41,
               'remote_head_sha':sha,'remote_attempt':2,'remote_workflow_id':48,
               'workflow':WORKFLOW,'archive_verified':True,
               'android':{'package':PACKAGE,'signer_sha256':CERT,'format':'apk',
                          'variant':'debug','signature_verified':True,'manifest_verified':True,
                          'qualified_release':False,'sha256':digest,'size':len(source)},
               'package_file':str(file),'registry_id':record['id'],'qualified_release':False,
               'release_gate':'not-implemented'}
    params=dict(consumer='relatorio',repository=REPO,source_sha=sha,run_id=300,
                run_attempt=2,package=PACKAGE,signer_sha256=CERT,workflow=WORKFLOW,
                variant='debug',fmt='apk',role='application')
    return root,file,record,collected,params


def test_valid_read_only_handoff_is_not_release_or_installation(tmp_path):
    root,file,record,collected,params=_fixture(tmp_path)
    path=registry_path(root)
    before=(file.read_bytes(),path.read_bytes())
    proof=handoff(root,collected,**params)
    assert proof['status']=='PASS_UNQUALIFIED' and proof['signed_package_file_rehashed_here']
    assert proof['archive_digest_reverified_here'] is False
    assert proof['upstream_android_signature_and_manifest_verified'] is True
    assert proof['consumer_build_integration_qualified'] is False
    assert proof['consumer_installation_qualified'] is False
    assert proof['release_qualified'] is False and proof['fc09_qualified'] is False
    assert (file.read_bytes(),path.read_bytes())==before


@pytest.mark.parametrize('field,mutated',[
    ('repository','Mikeyphw/other'),('run_id',301),('remote_attempt',3),
    ('remote_head_sha','b'*40),('remote_workflow_id',49),('workflow','other.yml'),
    ('archive_verified',False),('qualified_release',True),('release_gate','qualified'),
])
def test_collection_identity_and_release_claim_denied(tmp_path,field,mutated):
    root,file,record,collected,params=_fixture(tmp_path)
    collected[field]=mutated
    with pytest.raises(GitHubActionsError,match='ANDROID_R31_'):
        handoff(root,collected,**params)
    assert file.exists()


@pytest.mark.parametrize('field,mutated',[
    ('package','com.example.other'),('signer_sha256','b'*64),('signature_verified',False),
    ('manifest_verified',False),('variant','release'),('sha256','c'*64),
    ('qualified_release',True),('size',999),
])
def test_package_identity_or_bytes_denied(tmp_path,field,mutated):
    root,file,record,collected,params=_fixture(tmp_path)
    collected['android'][field]=mutated
    with pytest.raises(GitHubActionsError):handoff(root,collected,**params)


def test_real_tampering_detected_without_overwriting_or_delete(tmp_path):
    root,file,record,collected,params=_fixture(tmp_path)
    file.write_bytes(b'tampered')
    with pytest.raises(GitHubActionsError,match='PACKAGE_BYTES_CHANGED'):
        handoff(root,collected,**params)
    assert file.read_bytes()==b'tampered'


def test_symlink_and_forged_registry_metadata_rejected(tmp_path):
    root,file,record,collected,params=_fixture(tmp_path)
    fake=file.with_suffix('.orig');file.rename(fake);file.symlink_to(fake)
    with pytest.raises(GitHubActionsError):handoff(root,collected,**params)
    file.unlink();fake.rename(file)
    reg=registry_path(root);doc=json.loads(reg.read_text());doc['items'][0]['metadata']['signer_sha256']='b'*64
    reg.write_text(json.dumps(doc))
    with pytest.raises(GitHubActionsError,match='REGISTRY_EVIDENCE_MISMATCH'):
        handoff(root,collected,**params)


def test_missing_or_corrupt_registry_refuses_without_mutating_data(tmp_path):
    root,file,record,collected,params=_fixture(tmp_path)
    reg=registry_path(root)
    reg.write_text('{bad')
    with pytest.raises(GitHubActionsError,match='REGISTRY_INVALID'):
        handoff(root,collected,**params)
    assert reg.read_text()=='{bad'
    assert not list(reg.parent.glob('registry.corrupt-*.json'))


def test_uncommitted_consumer_source_rejected_before_reading_package(tmp_path):
    root,file,record,collected,params=_fixture(tmp_path)
    (root/'untracked.txt').write_text('dirty')
    with pytest.raises(GitHubActionsError,match='SOURCE_NOT_CLEAN_OR_BOUND'):
        handoff(root,collected,**params)


def test_unreviewed_consumer_and_instrumentation_are_blocked(tmp_path):
    root,file,record,collected,params=_fixture(tmp_path)
    with pytest.raises(GitHubActionsError,match='CONSUMER_UNREVIEWED'):
        handoff(root,collected,**{**params,'consumer':'unknown'})
    with pytest.raises(GitHubActionsError,match='ROLE_NOT_SUPPORTED_FOR_CONSUMER'):
        handoff(root,collected,**{**params,'role':'instrumentation'})


def test_android_dispatch_resume_is_not_counted_as_new_post(tmp_path,monkeypatch):
    # Minimal reviewed Android source: no real GitHub request is sent.
    project=tmp_path/'android'; (project/'app').mkdir(parents=True)
    (project/'settings.gradle.kts').write_text('include(":app")')
    (project/'app/build.gradle.kts').write_text('plugins { id("com.android.application") }\nandroid { defaultConfig { applicationId = "org.example.app" } }')
    (project/'gradlew').write_text('gradlew')
    w=project/'gradle/wrapper';w.mkdir(parents=True);(w/'gradle-wrapper.properties').write_text('distributionUrl=x')
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    _git(tmp_path,'add','-A')
    subprocess.run(['git','-C',str(tmp_path),'-c','user.name=Test','-c','user.email=test@invalid.test','commit','-qm','source'],check=True)
    sha=_git(tmp_path,'rev-parse','HEAD')
    called=[]
    def fake_dispatch(*args,execute,**kwargs):
        called.append(execute)
        return {'pre_dispatch_sha':sha,'dispatched':False,'resume_existing':execute,
                'request_id':'reviewed-existing'}
    monkeypatch.setattr('devtool.actions_android.dispatch_workflow',fake_dispatch)
    proof=android_plan(object(),'Mikeyphw/relatorio',tmp_path,module='app',variant='debug',fmt='apk',ref='main',execute=True)
    assert not proof['dispatched'] and proof['resumed_existing']
    assert called==[False,True]
