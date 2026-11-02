"""R36 reviewed Android-hosted DAG admissions. No local Gradle execution."""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess

import pytest

from devtool.actions_language_providers import provider_plan, ProviderError
from devtool.actions_graph_export import compile_graph, workflow_yaml

SHA = 'e' * 40


def project(tmp_path: Path, *, nested=True, variant='officialDebug', mode='command',
            tasks=None, outputs=None, metadata='', project_dir_override=None):
    root = tmp_path/'consumer'
    root.mkdir(parents=True)
    directory = root/'android' if nested else root
    (directory/'app').mkdir(parents=True)
    (directory/'gradle/wrapper').mkdir(parents=True)
    (directory/'gradlew').write_text('#!/bin/sh\n# GradleWrapperMain\nexec java -cp gradle/wrapper/gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain "$@"\n')
    (directory/'gradlew').chmod(0o755)
    jar = directory/'gradle/wrapper/gradle-wrapper.jar'
    jar.write_bytes(b'jar-wrapper-placeholder-for-source-review')
    (directory/'gradle/wrapper/gradle-wrapper.properties').write_text(
        'distributionUrl=https\\://services.gradle.org/distributions/gradle-9.1.0-bin.zip\n'
        + 'distributionSha256Sum='+'f'*64+'\n')
    (directory/'settings.gradle.kts').write_text('include(":app")\n')
    (directory/'app/build.gradle.kts').write_text('plugins { id("com.android.application") }\n')
    cap = variant[0].upper() + variant[1:]
    tasks = tasks if tasks is not None else [':app:lint'+cap, ':app:assemble'+cap]
    if outputs is None:
        suffix = 'debug' if variant == 'debug' else variant[:-5]+'/debug'
        output = ('android/' if nested else '') + 'app/build/outputs/apk/'+suffix+'/app-debug.apk'
        outputs = [output] if any('assemble' in t for t in tasks) else []
    projectdir = ('android' if nested else '') if project_dir_override is None else project_dir_override
    md = ('ci_toolchain="android-gradle", ci_android_java_version="17", '
          'ci_android_wrapper_jar_sha256="'+hashlib.sha256(jar.read_bytes()).hexdigest()+'", '
          'ci_android_sdk_packages=["platforms;android-35", "build-tools;35.0.0", "platform-tools"], '
          'ci_android_project_dir="'+projectdir+'", ci_android_debug_variant="'+variant+'"'
          + (', '+metadata if metadata else ''))
    text = ('[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n'
            '[targets.app]\nrunner="'+('android' if mode=='semantic' else 'command')+'"\n'
            + ('root="android"\n' if nested else '')
            + '[targets.app.jobs.verify]\nrunner="'+('android' if mode=='semantic' else 'command')+'"\n'
            + ('gradle_tasks = '+repr(tasks).replace("'",'"')+'\n' if mode=='semantic' else
               'command = '+repr(['./gradlew','--no-daemon',*tasks]).replace("'",'"')+'\n')
            + 'outputs = '+repr(outputs).replace("'",'"')+'\n'
            + 'metadata = {'+md+'}\n[targets.app.workflows]\nci=["job:verify"]\n')
    (root/'.devtool.toml').write_text(text)
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'add','-A'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=R36','-c','user.email=r36@example.invalid','commit','-qm','source'],check=True)
    return root


def plan(root):
    return provider_plan(root,target='app',workflow='ci',devtool_sha=SHA)


def test_nested_flavor_multitask_provider_plan_and_generated_dag(tmp_path):
    root=project(tmp_path)
    p=plan(root)
    declaration=p['nodes'][0]['provider']
    assert declaration['tasks']==[':app:lintOfficialDebug',':app:assembleOfficialDebug']
    assert declaration['variant']=='officialDebug'
    assert declaration['project_dir']=='android'
    assert declaration['lockfiles'][0]=='android/gradlew'
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=SHA)
    assert graph['jobs'][0]['android_review']['project_dir']=='android'
    yml=workflow_yaml(graph)
    assert 'devtool.actions_graph_export run-node' in yml
    assert 'setup-java@' in yml and 'setup-android@' in yml
    assert 'secrets.' not in yml
    assert p['release_qualified'] is False


def test_nested_semantic_android_job_is_reviewed_hosted_only(tmp_path):
    root=project(tmp_path,mode='semantic')
    p=plan(root)
    assert p['nodes'][0]['provider']['semantic_request'] is True
    assert p['nodes'][0]['command']==['./gradlew','--no-daemon',':app:lintOfficialDebug',':app:assembleOfficialDebug']
    assert compile_graph(root,target='app',workflow='ci',devtool_sha=SHA)['jobs'][0]['android_review']['semantic_request']


def test_gradle_semantic_job_is_reviewed(tmp_path):
    root=project(tmp_path,mode='semantic',nested=False,variant='debug')
    s=(root/'.devtool.toml').read_text().replace('runner="android"','runner="gradle"')
    (root/'.devtool.toml').write_text(s)
    subprocess.run(['git','-C',str(root),'add','-A'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=R36','-c','user.email=r36@example.invalid','commit','-qm','gradle'],check=True)
    assert plan(root)['nodes'][0]['provider']['semantic_request']


def test_versioned_cinderbox_apk_is_owned_by_exact_root_project(tmp_path):
    name = 'CinderboxCompanion-v0.1.1.apk'
    output = 'app/build/outputs/apk/debug/' + name
    root = project(tmp_path, nested=False, variant='debug',
                   tasks=[':app:assembleDebug'], outputs=[output])
    reviewed = plan(root)['nodes'][0]['provider']
    assert reviewed['project_dir'] == ''
    assert reviewed['operation'] == 'assemble'
    assert reviewed['variant'] == 'debug'
    assert compile_graph(root, target='app', workflow='ci', devtool_sha=SHA)['jobs'][0]['outputs'] == [output]


@pytest.mark.parametrize('bad_name', [
    '.hidden.apk', 'Cinderbox..v0.apk', 'Cinderbox-v0..1.apk',
    '../escape.apk', 'app.debug/.apk', 'app.debug.apk.bak',
])
def test_versioned_apk_basename_stays_fail_closed(tmp_path, bad_name):
    root = project(tmp_path, nested=False, variant='debug',
                   tasks=[':app:assembleDebug'],
                   outputs=['app/build/outputs/apk/debug/' + bad_name])
    with pytest.raises(ProviderError, match='own exactly'):
        plan(root)


@pytest.mark.parametrize('kwargs,reason',[
    ({'project_dir_override':'../android'},'project_dir'),
    ({'project_dir_override':'outside'},'project_dir'),
    ({'variant':'officialRelease'},'debug variant'),
    ({'tasks':[':app:assembleOfficialRelease']},'debug tasks'),
    ({'tasks':[':app:connectedOfficialDebugAndroidTest']},'debug tasks'),
    ({'tasks':[':app:assembleOfficialDebug',':app:bundleOfficialDebug']},'build producer'),
    ({'tasks':[':app:assembleOfficialDebug',':another:lintOfficialDebug']},'one module'),
    ({'tasks':[':app:assembleOfficialDebug','--init-script','hack.gradle']},'debug tasks'),
    ({'tasks':[':app:lintOfficialDebug',':app:lintOfficialDebug']},'duplicate'),
    ({'tasks':[':app:lintOfficialDebug']*4},'extra'),
    ({'outputs':['android/app/build/outputs/apk/debug/app-debug.apk']},'own exactly'),
    ({'metadata':'ci_android_expected_output="android/another.apk"'},'expected output pin'),
    ({'metadata':'device_required=true'},'admission blocked|Device, root'),
    ({'metadata':'ci_android_output_subdir="../debug"'},'output subdir'),
])
def test_bad_nested_android_job_fails_closed(tmp_path,kwargs,reason):
    root=project(tmp_path,**kwargs)
    with pytest.raises(ProviderError,match=reason):plan(root)


def test_nested_symlinked_wrapper_fails_closed(tmp_path):
    root=project(tmp_path)
    wrapper=root/'android/gradle/wrapper/gradle-wrapper.jar'
    wrapper.unlink();wrapper.symlink_to(root/'android/settings.gradle.kts')
    with pytest.raises(ProviderError, match='symlink|source review'):
        plan(root)


def test_nested_project_cwd_must_not_be_reinterpreted(tmp_path):
    root=project(tmp_path)
    s=(root/'.devtool.toml').read_text().replace('root="android"','root="."')
    (root/'.devtool.toml').write_text(s)
    subprocess.run(['git','-C',str(root),'add','-A'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=R36','-c','user.email=r36@example.invalid','commit','-qm','wrongcwd'],check=True)
    with pytest.raises(ProviderError,match='root'):
        plan(root)


def test_hosted_semantic_boundary_is_host_only_and_exact_tasks(tmp_path,monkeypatch):
    from devtool.actions_android_provider import hosted_semantic_spec, AndroidProviderError
    from devtool.config import load_wrapper_project_config
    root=project(tmp_path,mode='semantic')
    review=plan(root)['nodes'][0]['provider']
    native=load_wrapper_project_config(str(root),None).targets['app'].jobs['verify']
    # This negative must remain valid when executed *inside* GitHub Actions.
    # The runner's inherited CI identity is never evidence of the local path.
    for key in ('GITHUB_ACTIONS', 'RUNNER_OS', 'RUNNER_ARCH'):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(AndroidProviderError,match='GitHub Actions runner'):
        hosted_semantic_spec(root,native,review)
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    monkeypatch.setenv('RUNNER_OS','Linux')
    monkeypatch.setenv('RUNNER_ARCH','X64')
    s=hosted_semantic_spec(root,native,review)
    assert s.argv==['./gradlew','--no-daemon',':app:lintOfficialDebug',':app:assembleOfficialDebug']
    assert Path(s.cwd)==root/'android'
    assert s.metadata['r36_reviewed_hosted_semantic'] is True
    tampered={**review,'tasks':[':app:assembleRelease']}
    with pytest.raises(AndroidProviderError,match='frozen reviewed'):
        hosted_semantic_spec(root,native,tampered)
    monkeypatch.setenv('RUNNER_ARCH','ARM64')
    with pytest.raises(AndroidProviderError,match='GitHub Actions runner'):
        hosted_semantic_spec(root,native,review)
