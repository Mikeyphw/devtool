"""RM15-HF02 behavioral acceptance; no actual Gradle code is executed by default."""
from __future__ import annotations
import json,os,subprocess
from pathlib import Path
import pytest
from devtool.actions_android_model import discover_modules
from devtool.actions_android import android_plan,android_discover
from devtool.actions_android_release import release_build,release_import,release_sign
from devtool.github_actions import GitHubActionsError
from devtool.cli import build_parser
from tests.test_actions_05_android_builds import project
from tests.test_android_unsigned_first_hotfix import _dispatch,_script,_run


def _multidimensional(repo:Path):
    root=project(repo)
    (root/'app/build.gradle.kts').write_text('''plugins { id("com.android.application") }
android {
  defaultConfig { applicationId = "org.example.app" }
  flavorDimensions += listOf("tier", "region")
  productFlavors {
    create("free") { dimension = "tier" }
    create("paid") { dimension = "tier" }
    create("us") { dimension = "region" }
    create("eu") { dimension = "region" }
  }
  buildTypes { create("staging") { } }
}
''')
    subprocess.run(['git','-C',str(repo),'add','-A'],check=True)
    subprocess.run(['git','-C',str(repo),'commit','-qm','review multidimensional matrix'],check=True)
    return root


def test_four_flavors_three_build_types_and_explicit_identity(tmp_path,monkeypatch):
    root=_multidimensional(tmp_path);data=discover_modules(root)['app']['variants']
    assert len(data)==12
    assert data['freeEuStaging']['build_type']=='staging'
    assert data['freeEuStaging']['flavors']==['free','eu']
    assert {'paidUsRelease','freeEuDebug','paidEuStaging'}<=set(data)
    calls=_dispatch(monkeypatch,tmp_path)
    p=android_plan(object(),'owner/repo',tmp_path,module='app',variant='freeEuStaging',fmt='apk',ref='main')
    assert p['build_type']=='staging' and p['signing_state']=='unsigned-candidate'
    assert calls[-1][1]['build_type']=='staging'
    assert calls[-1][1]['signing_mode']=='local'
    with pytest.raises(GitHubActionsError,match='Unknown or unreviewed'):
        android_plan(object(),'owner/repo',tmp_path,module='app',variant='unknownStaging',fmt='apk',ref='main')


def test_invalid_dimension_and_matrix_explosion_refused(tmp_path):
    root=_multidimensional(tmp_path);p=root/'app/build.gradle.kts'
    p.write_text(p.read_text().replace('dimension = "region"','dimension = "missing"',1))
    with pytest.raises(GitHubActionsError,match='missing or unknown'):
        discover_modules(root)
    p.write_text(p.read_text().replace('dimension = "missing"','dimension = "region"'))
    p.write_text(p.read_text().replace('create("paid") { dimension = "tier" }','create("free") { dimension = "tier" }'))
    with pytest.raises(GitHubActionsError,match='Duplicate'):
        discover_modules(root)


def test_explicit_gradle_runtime_accepts_reviewed_convention_plugin(tmp_path,monkeypatch):
    root=project(tmp_path)
    gradle=root/'app/build.gradle.kts';gradle.write_text('plugins { id("unresolvable.dynamic.convention") }')
    wrapper=root/'gradlew';wrapper.write_text('''#!/bin/sh
set -eu
[ -z "${GH_TOKEN:-}" ] || exit 47
printf '%s' '{"app":{"module":"app","application_id":"org.example.app","project_path":"app","variants":{"paidEuStaging":{"build_type":"staging","flavor":"paidEu","flavors":["paid","eu"]}}}}' > "$DEVTOOL_ANDROID_DISCOVERY_OUTPUT"
''');wrapper.chmod(0o755)
    subprocess.run(['git','-C',str(tmp_path),'add','-A'],check=True)
    subprocess.run(['git','-C',str(tmp_path),'commit','-qm','review convention plugin'],check=True)
    assert android_discover(tmp_path)['modules']==[]
    monkeypatch.setenv('GH_TOKEN','should-never-enter-gradle')
    observed=android_discover(tmp_path,discovery_mode='gradle')['modules']
    assert observed[0]['variants']['paidEuStaging']['build_type']=='staging'
    assert observed[0]['source']=='reviewed-gradle-execution'
    calls=_dispatch(monkeypatch,tmp_path)
    p=android_plan(object(),'owner/repo',tmp_path,module='app',variant='paidEuStaging',fmt='apk',ref='main',discovery_mode='gradle')
    assert p['discovery_mode']=='gradle' and calls[-1][1]['build_type']=='staging'
    gradle.write_text(gradle.read_text()+'\n// dirty')
    with pytest.raises(GitHubActionsError,match='clean reviewed source'):
        android_discover(tmp_path,discovery_mode='gradle')


def test_runtime_refuses_forged_variant_or_escaped_path(tmp_path):
    root=project(tmp_path)
    wrapper=root/'gradlew';wrapper.write_text('''#!/bin/sh
printf '%s' '{"app":{"module":"app","application_id":"org.example.app","project_path":"../escape","variants":{"freeRelease":{"build_type":"release","flavor":"free"}}}}' > "$DEVTOOL_ANDROID_DISCOVERY_OUTPUT"
''');wrapper.chmod(0o755)
    subprocess.run(['git','-C',str(tmp_path),'add','-A'],check=True)
    subprocess.run(['git','-C',str(tmp_path),'commit','-qm','review wrapper'],check=True)
    with pytest.raises(GitHubActionsError,match='unsafe project path'):
        android_discover(tmp_path,discovery_mode='gradle')


def test_custom_build_type_hosted_workflow_syntax_and_identity(tmp_path):
    script=_script('Validate requested Gradle task')
    env=dict(os.environ,DT_PROJECT='.',DT_MODULE='app',DT_VARIANT='freeEuStaging',
             DT_FORMAT='apk',DT_SIGNING_MODE='local',DT_REQUESTED_BUILD_TYPE='staging',
             GITHUB_ENV=str(tmp_path/'out'),GITHUB_REF_PROTECTED='false')
    p=_run(script,cwd=tmp_path,env=env)
    assert p.returncode==0,p.stderr
    assert 'TASK_BUILD_TYPE=staging' in (tmp_path/'out').read_text()
    bad=_run(script,cwd=tmp_path,env={**env,'DT_REQUESTED_BUILD_TYPE':'release'})
    assert bad.returncode!=0 and 'conflicts with variant identity' in bad.stderr
    task=_script('Build Android release output without GitHub signing secrets')
    assert 'DT_KEYSTORE' not in task and 'devtool.unsignedReleaseCandidate=true' in task


def test_unified_release_does_not_promote_generic_build(tmp_path,monkeypatch):
    project(tmp_path);_dispatch(monkeypatch,tmp_path)
    p=release_build(object(),'owner/repo',tmp_path,ref='main')
    assert p['mode']=='generic-unsigned-build' and p['release_import_eligible'] is False
    assert p['qualified_release'] is False
    with pytest.raises(GitHubActionsError,match='explicit registered consumer'):
        release_import(object(),'owner/repo',tmp_path,consumer_id='',run_id=42,artifact_id=9,expected_sha='a'*64)


def test_registered_release_uses_actions10_without_new_signing_engine(tmp_path,monkeypatch):
    contract={'id':'bkx-embedded','variant':'embeddedRelease','application_id':'org.example.bkx',
              'target':'bkx','workflow':'bkx-unsigned.yml','repository':'owner/repo','required_files':[],
              'signer_sha256':'b'*64}
    monkeypatch.setattr('devtool.actions_android_contracts.select_consumer',lambda *a,**kw:contract)
    monkeypatch.setattr('devtool.actions_unsigned.unsigned_dispatch',lambda *a,**kw:{'phase':'ACTIONS-10','operation':'unsigned-build'})
    p=release_build(object(),'owner/repo',tmp_path,consumer_id='bkx-embedded',variant='embeddedRelease',ref='main')
    assert p['mode']=='registered-unsigned-life' and p['build']['phase']=='ACTIONS-10'
    with pytest.raises(GitHubActionsError,match='differs'):
        release_build(object(),'owner/repo',tmp_path,consumer_id='bkx-embedded',variant='liteRelease',ref='main')
    monkeypatch.setattr('devtool.actions_unsigned._imported',lambda *a,**kw:(tmp_path/'artifact.apk',{'consumer_id':'bkx-embedded'}))
    monkeypatch.setattr('devtool.actions_unsigned.unsigned_local_sign',lambda *a,**kw:{'phase':'ACTIONS-10','locally_signed':False})
    with pytest.raises(GitHubActionsError,match='differs'):
        release_sign(tmp_path,registry_id='approved',consumer_id='bkx-lite')
    signed=release_sign(tmp_path,registry_id='approved',consumer_id='bkx-embedded')
    assert signed['signing']['phase']=='ACTIONS-10' and not signed['qualified_release']


def test_unified_cli_is_canonical():
    p=build_parser()
    a=p.parse_args(['actions','android','release-plan','--ref','main','--discovery-mode','gradle','--consumer','bkx-embedded'])
    assert a.android_command=='release-plan' and a.discovery_mode=='gradle'
    b=p.parse_args(['actions','android','release-sign','--registry-id','xyz','--consumer','bkx-embedded'])
    assert b.android_command=='release-sign' and b.consumer=='bkx-embedded'


def test_custom_build_type_unsigned_output_selection_and_provenance(tmp_path):
    import hashlib
    import zipfile
    from tests.test_android_unsigned_first_hotfix import _candidate_harness
    repo,env,temp=_candidate_harness(tmp_path)
    selected=repo/'app/build/outputs/apk/freeEu/staging'
    selected.mkdir(parents=True)
    with zipfile.ZipFile(selected/'app-free-eu-staging-unsigned.apk','w') as z:
        z.writestr('AndroidManifest.xml',b'fixture')
    env.update(TASK_VARIANT='freeEuStaging',TASK_BUILD_TYPE='staging')
    out=_run(_script('Select output and sign release'),cwd=repo,env=env)
    assert out.returncode==0,(out.stdout,out.stderr)
    emitted=temp/'devtool-android-output/unsigned.apk'
    manifest=json.loads((temp/'devtool-android-output/android-variant-manifest.json').read_text())
    assert emitted.is_file() and manifest['build_type']=='staging'
    assert manifest['signing_state']=='unsigned' and manifest['qualified_release'] is False
    assert manifest['sha256']==hashlib.sha256(emitted.read_bytes()).hexdigest()


def test_nonrelease_custom_signed_archive_requires_certificate_and_exact_identity(tmp_path,monkeypatch):
    import hashlib,zipfile
    from devtool.actions_android import inspect_android_zip
    blob=b'APK-FIXTURE';sha='a'*40;cert='b'*64
    manifest=dict(schema_version=1,variant='freeEuStaging',format='apk',
                  apk_file='app-free-eu-staging.apk',head_sha=sha,run_attempt=3,
                  build_type='staging',sha256=hashlib.sha256(blob).hexdigest())
    archive=tmp_path/'artifact.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr(manifest['apk_file'],blob)
        z.writestr('android-variant-manifest.json',json.dumps(manifest))
    monkeypatch.setattr('devtool.actions_android._inspect_apk',lambda *a,**kw:{
        'package':'org.example.app','signer_sha256':cert,'signature_verified':True,
        'debug_signing':False})
    args=dict(fmt='apk',variant='freeEuStaging',expected_package='org.example.app',
              expected_cert=cert,expected_source_sha=sha,expected_run_attempt=3)
    assert inspect_android_zip(archive,**args)['signing_state']=='signed-verified'
    with pytest.raises(GitHubActionsError,match='stable expected signing certificate'):
        inspect_android_zip(archive,**{**args,'expected_cert':''})
    manifest['build_type']='debug'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('app-free-eu-staging.apk',blob)
        z.writestr('android-variant-manifest.json',json.dumps(manifest))
    with pytest.raises(GitHubActionsError,match='build-type manifest'):
        inspect_android_zip(archive,**args)


def test_registered_non_debug_custom_build_type_and_debug_refusal(tmp_path):
    from devtool.actions_android_contracts import load_consumers
    from tests.test_rm15_android_consumers import registry
    registry(tmp_path)
    p=tmp_path/'config/android-consumers.json'
    src=json.loads(p.read_text());src['consumers'][0]['build_type']='staging'
    src['consumers'][0]['variant']='embeddedStaging'
    p.write_text(json.dumps(src))
    assert any(c['build_type']=='staging' for c in load_consumers(tmp_path))
    src['consumers'][0]['build_type']='debug';p.write_text(json.dumps(src))
    with pytest.raises(GitHubActionsError,match='cannot target debug'):
        load_consumers(tmp_path)
