"""R32 Devtool-only Android export admission and contract regressions.

No Gradle process or network hosted dispatch is run in these tests.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import pytest

from devtool.actions_language_providers import provider_plan, ProviderError
from devtool.actions_graph_export import compile_graph, workflow_yaml

PIN='b'*40


def repo(tmp_path: Path, *, command='["./gradlew", "--no-daemon", ":app:assembleDebug"]',
         outputs='["app/build/outputs/apk/debug/app-debug.apk"]',
         metadata='', wrapper_hash=True) -> Path:
    root=tmp_path/'android';root.mkdir(parents=True)
    (root/'app').mkdir()
    (root/'gradle/wrapper').mkdir(parents=True)
    (root/'gradlew').write_text('#!/bin/sh\n# GradleWrapperMain\nexec java -cp gradle/wrapper/gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain "$@"\n')
    (root/'gradlew').chmod(0o755)
    (root/'gradle/wrapper/gradle-wrapper.jar').write_bytes(b'jar-wrapper-placeholder-for-source-review')
    (root/'gradle/wrapper/gradle-wrapper.properties').write_text('distributionUrl=https\\://services.gradle.org/distributions/gradle-9.1.0-bin.zip\n'+
              ('distributionSha256Sum='+'f'*64+'\n' if wrapper_hash else ''))
    (root/'settings.gradle.kts').write_text('include(":app")\n')
    (root/'app/build.gradle.kts').write_text('plugins { id("com.android.application") }\n')
    jar_pin=hashlib.sha256((root/'gradle/wrapper/gradle-wrapper.jar').read_bytes()).hexdigest()
    (root/'.devtool.toml').write_text('''[project]
runner = "command"
[workspace]
default_target = "app"
[targets.app]
runner = "command"
[targets.app.jobs.android]
runner = "command"
command = '''+command+'''
outputs = '''+outputs+'''
metadata = {ci_toolchain="android-gradle", ci_android_java_version="17", ci_android_wrapper_jar_sha256="'''+jar_pin+'''", ci_android_sdk_packages=["platforms;android-35", "build-tools;35.0.0", "platform-tools"]'''+(', '+metadata if metadata else '')+'''}
[targets.app.workflows]
android = ["job:android"]
''')
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=test@example.org','commit','-qm','source'],check=True)
    return root


def plan(root):
    return provider_plan(root,target='app',workflow='android',devtool_sha=PIN)


def test_native_hosted_gradle_graph_and_immutable_action_pins(tmp_path):
    root=repo(tmp_path)
    p=plan(root)
    assert p['status']=='LOCALLY_ELIGIBLE'
    assert p['providers']==['android-gradle']
    graph=compile_graph(root,target='app',workflow='android',devtool_sha=PIN)
    assert graph['jobs'][0]['provider']=='android-gradle'
    assert graph['jobs'][0]['outputs']==['app/build/outputs/apk/debug/app-debug.apk']
    yaml=workflow_yaml(graph)
    assert 'android-actions/setup-android@be39fa834029ff78f1a44aa3bb0819b8fc2bd8fd' in yaml
    assert 'gradle/actions/setup-gradle@ed408507eac070d1f99cc633dbcf757c94c7933a' in yaml
    assert 'cmdline-tools-version: 15859902' in yaml
    assert 'platforms;android-35 build-tools;35.0.0 platform-tools' in yaml
    assert 'devtool.actions_graph_export run-node' in yaml
    assert 'permissions:\n  contents: read' in yaml
    assert 'secrets.' not in yaml and 'signing' not in yaml.lower()
    assert p['release_qualified'] is False


@pytest.mark.parametrize('argv,expected',[
    ('["./gradlew", ":app:assembleRelease"]','debug'),
    ('["./gradlew", ":app:connectedDebugAndroidTest"]','debug'),
    ('["./gradlew", ":app:assembleDebug", ":app:assembleRelease"]','extra'),
    ('["bash", "./gradlew", ":app:assembleDebug"]','requires'),
    ('["./gradlew", "--init-script", "injected.gradle", ":app:assembleDebug"]','requires'),
])
def test_unreviewed_gradle_task_is_refused(tmp_path,argv,expected):
    r=repo(tmp_path,command=argv)
    with pytest.raises(ProviderError,match=expected):plan(r)


def test_unsigned_build_requires_exact_owned_artifact_path(tmp_path):
    r=repo(tmp_path,outputs='["dist/fake.apk"]')
    with pytest.raises(ProviderError,match='own exactly'):plan(r)


def test_missing_wrapper_distribution_hash_refused(tmp_path):
    r=repo(tmp_path,wrapper_hash=False)
    with pytest.raises(ProviderError,match='distributionSha256Sum'):plan(r)


def test_untracked_or_symlinked_wrapper_jar_refused(tmp_path):
    r=repo(tmp_path)
    jar=r/'gradle/wrapper/gradle-wrapper.jar'
    jar.unlink()
    jar.symlink_to(r/'settings.gradle.kts')
    with pytest.raises(ProviderError,match='symlink|source review'):plan(r)


def test_android_provider_device_and_signer_remain_refused(tmp_path):
    for i,meta in enumerate(('device_required=true','signing_required=true','release_signed=true','termux_native_required=true')):
        r=repo(tmp_path/str(i),metadata=meta)
        with pytest.raises(ProviderError,match='admission blocked|Device, root|signing'):plan(r)


def test_android_source_revision_is_bound_to_frozen_graph(tmp_path):
    r=repo(tmp_path)
    graph=compile_graph(r,target='app',workflow='android',devtool_sha=PIN)
    (r/'gradle/wrapper/gradle-wrapper.properties').write_text('tampered')
    with pytest.raises(ProviderError):compile_graph(r,target='app',workflow='android',devtool_sha=PIN)
    assert graph['provider_plan_sha256']


def test_export_refuses_single_job_without_provisioned_android_sdk(tmp_path):
    from devtool.actions_language_providers import provider_export
    root=repo(tmp_path)
    with pytest.raises(ProviderError,match='multi-job'):
        provider_export(root,target='app',workflow='android',devtool_sha=PIN,mode='single-job')
    result=provider_export(root,target='app',workflow='android',devtool_sha=PIN,mode='multi-job')
    assert result['status']=='REVIEW_ONLY'
    assert result['writes_performed'] is False
    assert result['workflow_path'].endswith('-providers-multi.yml')


def test_provider_doctor_keeps_four_independent_qualifications(tmp_path):
    from devtool.actions_language_providers import provider_doctor
    root=repo(tmp_path)
    result=provider_doctor(root,target='app')
    assert result['summary']['provider_plan_locally_eligible']==1
    assert result['summary']['provider_export_locally_eligible']==1
    row=result['workflows'][0]
    assert row['provider_plan']['state']=='locally_eligible'
    assert row['provider_export']['state']=='locally_eligible'
    assert row['hosted_execution']['state']=='NOT_VERIFIED'
    assert row['release_qualification']['state']=='NOT_QUALIFIED'
