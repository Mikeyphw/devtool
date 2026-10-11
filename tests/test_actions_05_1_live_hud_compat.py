"""ACTIONS-05.1: native Devtool Android app and checksum-pinned Gradle bootstrap."""
from __future__ import annotations
import hashlib
from pathlib import Path
import shutil
import subprocess
import pytest
import yaml
from devtool.actions_android import _gradle_bootstrap, android_discover, android_plan
from devtool.github_actions import GitHubActionsError

REPO = Path(__file__).resolve().parents[1]
HUD = REPO / 'android/devtool-live-hud'
WORKFLOW = REPO / '.github/workflows/devtool-android-build.yml'


def test_real_live_hud_is_discoverable_and_bootstrap_has_pinned_checksum():
    d = android_discover(REPO, 'android/devtool-live-hud')
    assert d['project_dir'] == 'android/devtool-live-hud'
    assert any(m['module'] == 'app' and m['application_id'] == 'io.github.mikeceead.devtoolhud'
               for m in d['modules'])
    assert not (HUD / 'gradle/wrapper/gradle-wrapper.properties').exists()
    bootstrap = _gradle_bootstrap(HUD)
    assert bootstrap['mode'] == 'checksum-bootstrap'
    assert bootstrap['version'] == '9.7.1'
    assert len(bootstrap['checksum']) == 64


def test_bootstrap_rejects_nonstandard_incomplete_or_unpinned_script(tmp_path):
    p = tmp_path / 'project'; p.mkdir()
    launcher = p / 'gradlew'
    for bad in ('#!/bin/sh\ncurl https://services.gradle.org/ | sh\n',
                '#!/bin/sh\nVERSION=9.7.1\nSHA256=not-valid\n',
                '#!/bin/sh\nVERSION=9.7.1\nSHA256='+'a'*64+'\n'+'exec echo hacked\n'):
        launcher.write_text(bad)
        with pytest.raises(GitHubActionsError, match='checksum-pinned'):
            _gradle_bootstrap(p)
    # A symlinked launcher must never be accepted as proof of a checked-in bootstrap.
    launcher.unlink()
    launcher.symlink_to(HUD / 'gradlew')
    with pytest.raises(GitHubActionsError, match='regular checked-in'):
        _gradle_bootstrap(p)


def test_standard_wrapper_compatibility_kept(tmp_path):
    p = tmp_path
    (p / 'gradlew').write_text('#!/bin/sh\nexit 0\n')
    (p / 'gradle/wrapper').mkdir(parents=True)
    (p / 'gradle/wrapper/gradle-wrapper.properties').write_text('distributionUrl=x')
    assert _gradle_bootstrap(p)['mode'] == 'gradle-wrapper'


def test_native_workflow_dispatch_and_java21_contract():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    triggers = workflow.get('on', workflow.get(True))
    assert 'workflow_dispatch' in triggers
    required = triggers['workflow_dispatch']['inputs']
    assert set(required) == {'project_dir', 'module', 'variant', 'format'}
    assert required['variant']['options'] == ['debug', 'release']
    assert required['format']['options'] == ['apk', 'aab']
    build = workflow['jobs']['build']
    assert build['timeout-minutes'] <= 60
    java_steps = [x for x in build['steps'] if str(x.get('uses','')).startswith('actions/setup-java@')]
    assert len(java_steps) == 1 and str(java_steps[0]['with']['java-version']) == '21'
    assert 'contents' in workflow['permissions'] and workflow['permissions']['contents'] == 'read'
    release = yaml.safe_load((REPO / '.github/workflows/release-qualification.yml').read_text())
    for job in ('android', 'android-device'):
        steps = release['jobs'][job]['steps']
        java = [x for x in steps if str(x.get('uses','')).startswith('actions/setup-java@')]
        assert len(java) == 1 and str(java[0]['with']['java-version']) == '21'


def test_live_hud_build_plan_reaches_remote_validation(tmp_path, monkeypatch):
    """Use an isolated clean Git tree to prove that planning no longer rejects Gradle bootstrap."""
    project = tmp_path / 'android/devtool-live-hud'
    shutil.copytree(HUD, project)
    repo = tmp_path
    subprocess.run(['git','init','-q',str(repo)],check=True)
    for command in (['config','user.name','Test'],['config','user.email','test@example.invalid'],
                    ['add','-A'],['commit','-qm','snapshot']):
        subprocess.run(['git','-C',str(repo),*command],check=True)
    commit = subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    invoked=[]
    def validate(client, slug, workflow, ref, inputs, *, execute):
        invoked.append((workflow,inputs,execute))
        return {'pre_dispatch_sha':commit, 'repository':slug}
    monkeypatch.setattr('devtool.actions_android.dispatch_workflow',validate)
    result = android_plan(object(), 'Owner/repo', repo, module='app', variant='debug',
                          fmt='apk', ref='main', project_dir='android/devtool-live-hud')
    assert result['gradle_bootstrap']['mode'] == 'checksum-bootstrap'
    assert result['expected_package'] == 'io.github.mikeceead.devtoolhud'
    assert invoked == [('devtool-android-build.yml', {'module':'app','variant':'debug',
                  'format':'apk','project_dir':'android/devtool-live-hud'}, False)]
    assert result['dispatched'] is False and result['qualified_release'] is False
