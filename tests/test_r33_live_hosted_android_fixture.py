"""R33 source-only contracts for a future *real* hosted Gradle execution.

These tests never execute Gradle and cannot substitute for GitHub evidence.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import shutil
import subprocess

import pytest

from tools.r33_hosted_android_evidence import (
    DISTRIBUTION_SHA256, FIXTURE_SUBDIR, JAR_SHA256, PACKAGE,
    ROOT_ALLOWED, execute, graph_plan, prepare,
)

ROOT=Path(__file__).resolve().parents[1]
TEMPLATE=ROOT/FIXTURE_SUBDIR


def fake_fixture(tmp_path: Path) -> Path:
    target=tmp_path/'fixture'
    shutil.copytree(TEMPLATE,target)
    jar=b'fixture-only-GradleWrapperMain-fake-not-official'
    checksum=hashlib.sha256(jar).hexdigest()
    config=target/'.devtool.toml'
    config.write_text(config.read_text().replace(JAR_SHA256,checksum))
    (target/'gradle/wrapper').mkdir(parents=True)
    (target/'gradle/wrapper/gradle-wrapper.jar').write_bytes(jar)
    (target/'gradle-wrapper.properties').rename(target/'gradle/wrapper/gradle-wrapper.properties')
    subprocess.run(['git','init','-q',str(target)],check=True)
    subprocess.run(['git','-C',str(target),'add','-A'],check=True)
    subprocess.run(['git','-C',str(target),'-c','user.name=R33','-c','user.email=r33@example.invalid',
                    'commit','-qm','fixture-only'],check=True)
    return target


def test_android_native_dag_plan_reviews_lint_and_build_without_gradle(tmp_path):
    fixture=fake_fixture(tmp_path)
    graph=graph_plan(fixture,'a'*40)
    assert graph['jobs'][0]['id']=='node-01'
    assert graph['jobs'][1]['needs']==['node-01']
    assert graph['jobs'][1]['outputs']==['app/build/outputs/apk/debug/app-debug.apk']
    assert not graph['release_qualified']
    assert not (fixture/'app/build').exists()


def test_fixture_source_is_bounded_and_shares_pinned_toolchains():
    assert {str(p.relative_to(TEMPLATE)) for p in TEMPLATE.rglob('*') if p.is_file()} == ROOT_ALLOWED
    assert JAR_SHA256 in (TEMPLATE/'.devtool.toml').read_text()
    assert DISTRIBUTION_SHA256 in (TEMPLATE/'gradle-wrapper.properties').read_text()
    assert "applicationId '%s'" % PACKAGE in (TEMPLATE/'app/build.gradle').read_text()
    assert 'assembleRelease' not in (TEMPLATE/'.devtool.toml').read_text()
    assert 'connectedDebugAndroidTest' not in (TEMPLATE/'.devtool.toml').read_text()


def test_real_fixture_preparation_rejects_forged_wrapper_jar(tmp_path):
    wrapper=tmp_path/'spoof.jar'
    wrapper.write_bytes(b'GradleWrapperMain-junk')
    with pytest.raises(SystemExit,match='SHA-256 mismatch'):
        prepare(ROOT,tmp_path/'out',wrapper)
    assert not (tmp_path/'out').exists()


def test_real_node_refuses_local_fake_github_evidence(tmp_path,monkeypatch):
    fixture=fake_fixture(tmp_path)
    monkeypatch.delenv('GITHUB_ACTIONS',raising=False)
    with pytest.raises(SystemExit,match='GitHub-hosted'):
        execute(fixture,'lint',tmp_path/'receipt.json',prior=None)


def test_live_workflow_uses_two_hosted_jobs_and_non_release_immutability():
    file=ROOT/'.github/workflows/devtool-android-r33-live-hosted-dag.yml'
    content=file.read_text()
    assert 'workflow_dispatch:' in content
    assert '    needs: [lint]' in content
    assert 'gradle-8.11.1-wrapper.jar' in content
    assert 'gradle/actions/setup-gradle@ed408507eac070d1f99cc633dbcf757c94c7933a' in content
    assert 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02' in content
    assert 'actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093' in content
    assert 'secrets.' not in content
    assert 'assembleRelease' not in content


def test_live_workflow_generates_official_wrapper_in_hosted_isolated_project():
    text=(ROOT/'.github/workflows/devtool-android-r33-live-hosted-dag.yml').read_text()
    assert text.count("gradle-version: '8.11.1'")==2
    assert text.count('gradle --offline --no-daemon :wrapper')==2
    assert text.count('--gradle-distribution-sha256-sum '+DISTRIBUTION_SHA256)==2
    assert text.count("'"+JAR_SHA256+"'")==2
    assert 'curl --fail' not in text
    assert 'https://services.gradle.org/distributions/gradle-8.11.1-wrapper.jar' not in text
    assert text.count('hosted-bootstrap.json')==2
    assert text.count('hosted-failure.json')==2
    assert text.count('R33_WRAPPER_OUTCOME: ${{ steps.generate_wrapper.outcome }}')==2
    assert text.count('if: always()')==2
    assert "'release_qualified':False" in text
    assert 'needs: [lint]' in text
