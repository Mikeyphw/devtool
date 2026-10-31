"""R34: source-only graph/export + canonical registry adversarial contracts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from devtool.actions_android_lab_export import (
    APK, FIXTURE, JAR_SHA, WORKFLOW, LabError, generate, register, reviewed_graph,
)
from devtool.artifact_registry import load_registry
from tools.r33_hosted_android_evidence import graph_plan

ROOT=Path(__file__).resolve().parents[1]


def fake_fixture(tmp_path: Path) -> Path:
    target=tmp_path/'fixture'
    shutil.copytree(ROOT/FIXTURE,target)
    stub=b'GradleWrapperMain fake source-only fixture JAR - never executable'
    pin=hashlib.sha256(stub).hexdigest()
    f=target/'.devtool.toml'
    f.write_text(f.read_text().replace(JAR_SHA,pin))
    d=target/'gradle/wrapper';d.mkdir(parents=True)
    (d/'gradle-wrapper.jar').write_bytes(stub)
    (target/'gradle-wrapper.properties').rename(d/'gradle-wrapper.properties')
    subprocess.run(['git','init','-q',str(target)],check=True)
    subprocess.run(['git','-C',str(target),'add','-A'],check=True)
    subprocess.run(['git','-C',str(target),'-c','user.name=Fixture',
                    '-c','user.email=fixture@example.invalid','commit','-qm','source'],check=True)
    return target


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()


def test_generated_workflow_is_exactly_graph_derived_and_non_release():
    graph=reviewed_graph(ROOT)
    assert [x['id'] for x in graph['jobs']]==['node-01','node-02']
    assert [x['needs'] for x in graph['jobs']]==[[],['node-01']]
    assert generate(ROOT)==(ROOT/WORKFLOW).read_text()
    s=generate(ROOT)
    assert '  node-02:\n    needs: [node-01]' in s
    assert 'devtool.actions_android_lab_export verify' in s
    assert 'r33_hosted_android_evidence.py execute' in s
    assert 'devtool.actions_android_lab_export register' in s
    assert 'devtool-r34-debug-apk-attempt${{ github.run_attempt }}' in s
    assert 'actions/download-artifact@' in s
    assert 'release_qualified: true' not in s and 'secrets.' not in s
    assert 'assembleRelease' not in s and 'connectedDebugAndroidTest' not in s
    assert 'gradle --offline --no-daemon :wrapper' in s


def test_graph_drift_is_refused_before_workflow_changes(tmp_path):
    root=tmp_path/'src';(root/'tests/fixtures').mkdir(parents=True)
    shutil.copytree(ROOT/FIXTURE,root/FIXTURE)
    p=root/FIXTURE/'.devtool.toml'
    p.write_text(p.read_text().replace(':app:assembleDebug',':app:assembleRelease'))
    with pytest.raises((LabError, __import__('devtool.actions_language_providers',fromlist=['ProviderError']).ProviderError)):
        reviewed_graph(root)


def prepare_receipts(tmp_path,monkeypatch):
    fixture=fake_fixture(tmp_path)
    ctx={'devtool_sha':'a'*40,'github_run_id':'123456','github_run_attempt':2}
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    monkeypatch.setenv('RUNNER_OS','Linux')
    monkeypatch.setenv('GITHUB_SHA',ctx['devtool_sha'])
    monkeypatch.setenv('GITHUB_RUN_ID',ctx['github_run_id'])
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT','2')
    sha=git(fixture,'rev-parse','HEAD')
    graph=graph_plan(fixture,ctx['devtool_sha'])
    data=b'R34 APK bytes stub' * 32
    apk=fixture/APK;apk.parent.mkdir(parents=True,exist_ok=True);apk.write_bytes(data)
    digest=hashlib.sha256(data).hexdigest()
    cert='d'*64
    out=tmp_path/'evidence';out.mkdir()
    receipt=out/'result.json'
    report={'kind':'devtool.r33.live-node.v1','node':'build','status':'PASS','provider':'android-gradle',
            'actual_gradle_execution':True,'native_dag_executed':True,'release_qualified':False,
            'device_qualified':False,'fixture_sha':sha,'graph_sha256':graph['graph_sha256'],**ctx,
            'apk':{'package':'org.devtool.r33.fixture','variant':'debug','path':APK,
                   'sha256':digest,'size':len(data),'debug_signed':True,'signing_release_authorized':False,
                   'signer_certificate_sha256':cert},
            'node_result':{'status':'LOCAL_JOB_SUCCEEDED','job_id':'node-02',
                           'github_source':sha,'graph_sha256':graph['graph_sha256'],
                           'run_id':ctx['github_run_id'],'attempt':2}}
    receipt.write_text(json.dumps(report))
    stage=out/'native-outputs/node-02/receipt.json';stage.parent.mkdir(parents=True)
    stage.write_text(json.dumps({'source_commit':sha,'graph_sha256':graph['graph_sha256'],
          'run_id':ctx['github_run_id'],'attempt':2,
          'files':[{'path':APK,'sha256':digest,'size':len(data)}]}))
    (out/'bootstrap.json').write_text(json.dumps({'source_sha':ctx['devtool_sha'],
           'run_id':ctx['github_run_id'],'run_attempt':2,'job':'build','status':'NOT_QUALIFIED'}))
    sdk=tmp_path/'sdk/build-tools/35.0.0';sdk.mkdir(parents=True)
    aapt=sdk/'aapt';aapt.write_text("#!/bin/sh\necho \"package: name='org.devtool.r33.fixture' versionCode='1'\"\n");aapt.chmod(0o755)
    signer=sdk/'apksigner';signer.write_text('#!/bin/sh\necho "Signer #1 certificate SHA-256 digest: '+cert+'"\n');signer.chmod(0o755)
    monkeypatch.setenv('ANDROID_HOME',str(tmp_path/'sdk'))
    return fixture,receipt,out/'registry.json',tmp_path/'publish/app-debug.apk',graph


def test_real_canonical_registry_api_registers_and_reverifies_local_apk(tmp_path,monkeypatch):
    fixture,receipt,out,apk_out,graph=prepare_receipts(tmp_path,monkeypatch)
    r=register(fixture,receipt,out,apk_out)
    assert r['local_registry_registered'] and r['local_registry_verified']
    assert r['remote_actions_artifact_authenticated'] is False
    assert r['release_qualified'] is False
    assert r['graph_sha256']==graph['graph_sha256']
    assert r['apk_sha256']==hashlib.sha256(apk_out.read_bytes()).hexdigest()
    assert len(load_registry(fixture)['items'])==1
    assert load_registry(fixture)['items'][0]['run_id']=='123456'
    assert (out.parent/'registry-snapshot.json').is_file()


@pytest.mark.parametrize('change',[
    ('result.json',('github_run_attempt',3)),
    ('result.json',('devtool_sha','b'*40)),
    ('result.json',('release_qualified',True)),
    ('result.json',('graph_sha256','1'*64)),
    ('result.json',('actual_gradle_execution',False)),
    ('result.json',('apk','tamper')),
    ('native-outputs/node-02/receipt.json',('attempt',3)),
    ('bootstrap.json',('job','lint')),
])
def test_mismatched_evidence_fails_before_registry_mutation(tmp_path,monkeypatch,change):
    fixture,receipt,out,apk_out,_=prepare_receipts(tmp_path,monkeypatch)
    p=receipt.parent/change[0]
    data=json.loads(p.read_text())
    k,v=change[1]
    if k=='apk':data[k]['sha256']='2'*64
    else:data[k]=v
    p.write_text(json.dumps(data))
    with pytest.raises(LabError):register(fixture,receipt,out,apk_out)
    assert not (fixture/'.devtool/artifacts/registry.json').exists()
    assert not apk_out.exists()


def test_tampered_apk_fails_before_registration(tmp_path,monkeypatch):
    fixture,receipt,out,apk_out,_=prepare_receipts(tmp_path,monkeypatch)
    p=fixture/APK;p.write_bytes(p.read_bytes()+b'tamper')
    with pytest.raises(LabError):register(fixture,receipt,out,apk_out)
    assert not (fixture/'.devtool/artifacts/registry.json').exists()


def test_publication_corruption_fails_before_registry_and_preserves_private_evidence(tmp_path,monkeypatch):
    fixture,receipt,out,apk_out,_=prepare_receipts(tmp_path,monkeypatch)
    from devtool import actions_android_lab_export as lab
    def corrupt_copy(source,destination,length):
        destination.write(b'corrupted-private-publication-for-diagnostics')
    monkeypatch.setattr(lab.shutil,'copyfileobj',corrupt_copy)
    with pytest.raises(LabError,match='standalone publication changed bytes'):
        register(fixture,receipt,out,apk_out)
    assert apk_out.read_bytes()==b'corrupted-private-publication-for-diagnostics'
    assert not (fixture/'.devtool/artifacts/registry.json').exists()
    assert not out.exists()


def test_existing_publication_is_never_overwritten_or_registered(tmp_path,monkeypatch):
    fixture,receipt,out,apk_out,_=prepare_receipts(tmp_path,monkeypatch)
    apk_out.parent.mkdir(parents=True,exist_ok=True)
    apk_out.write_bytes(b'original-user-owned-bytes')
    with pytest.raises(LabError,match='already exists'):
        register(fixture,receipt,out,apk_out)
    assert apk_out.read_bytes()==b'original-user-owned-bytes'
    assert not (fixture/'.devtool/artifacts/registry.json').exists()
