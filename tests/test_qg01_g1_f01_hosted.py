"""QG01-G1-F01 fail-closed hosted G1 admission and diagnostic safety.

These tests mock subprocess outputs. They never invoke Gradle or Android devices.
"""
from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from devtool import android_g1_gate as gate
from devtool import g3r_hosted_android as hosted


def hosted_context(monkeypatch):
    root = Path(gate.__file__).resolve().parents[1]
    names = {
        'GITHUB_ACTIONS': 'true', 'RUNNER_OS': 'Linux',
        'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_JOB': 'android-g1-hosted',
        'GITHUB_REPOSITORY': 'Mikeyphw/devtool',
        'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_RUN_ID': '45176122529',
        'GITHUB_SHA': 'a'*40, 'GITHUB_WORKSPACE': str(root),
    }
    for key, value in names.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv('DEVTOOL_ANDROID_G1_ALLOW_TEST_HOST', raising=False)
    monkeypatch.setattr(gate, '_is_termux', lambda: False)
    return root


def fake_git(monkeypatch, root, *, head='a'*40, status=''):
    def run(argv, **kw):
        assert argv[:3] == ['git', '-C', str(root)]
        if argv[3:] == ['rev-parse', 'HEAD']:
            return CompletedProcess(argv, 0, stdout=head+'\n', stderr='')
        if argv[3:] == ['status', '--porcelain=v1', '--untracked-files=all']:
            return CompletedProcess(argv, 0, stdout=status, stderr='')
        raise AssertionError('Unexpected subprocess in read-only host check')
    monkeypatch.setattr(gate.subprocess, 'run', run)


def test_hosted_positive_exact_execution_context(monkeypatch):
    root=hosted_context(monkeypatch)
    fake_git(monkeypatch,root)
    assert gate._verified_github_hosted_g1() is True


@pytest.mark.parametrize('key,value', [
    ('GITHUB_ACTIONS', 'false'), ('RUNNER_OS', 'Android'),
    ('GITHUB_EVENT_NAME', 'push'), ('GITHUB_JOB', 'candidate-consumer'),
    ('GITHUB_REPOSITORY', 'other/repo'), ('GITHUB_RUN_ATTEMPT','2'),
    ('GITHUB_SHA','not-a-hash'), ('GITHUB_RUN_ID','0'),
])
def test_hosted_context_rejects_wrong_scope(monkeypatch,key,value):
    root=hosted_context(monkeypatch)
    fake_git(monkeypatch,root)
    monkeypatch.setenv(key,value)
    assert gate._verified_github_hosted_g1() is False


def test_hosted_context_rejects_dirty_checkout(monkeypatch):
    root=hosted_context(monkeypatch)
    fake_git(monkeypatch,root,status='?? injected-file.py')
    assert gate._verified_github_hosted_g1() is False


def test_hosted_context_rejects_mismatched_git_head(monkeypatch):
    root=hosted_context(monkeypatch)
    fake_git(monkeypatch,root,head='b'*40)
    assert gate._verified_github_hosted_g1() is False


def test_hosted_context_rejects_untrusted_workspace(monkeypatch, tmp_path):
    root=hosted_context(monkeypatch)
    fake_git(monkeypatch,root)
    monkeypatch.setenv('GITHUB_WORKSPACE',str(tmp_path))
    assert gate._verified_github_hosted_g1() is False


def test_non_hosted_linux_never_launches_gradle(monkeypatch):
    hosted_context(monkeypatch)
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'push')
    with pytest.raises(gate.AndroidG1Error,match='native Termux or source-verified'):
        gate._tool('gradle')


def test_public_failure_diagnostic_no_raw_output(tmp_path):
    log=b'ANDROID-G1 failed: canonical Android-G1 Gradle producer failed; SECRET_TOKEN=should-not-leak'
    hosted._record_public_failure(tmp_path,source='a'*40,run_id=123,attempt=1,
                                  returncode=1,stderr=log)
    target=tmp_path/'android_g1_failure.json'
    content=target.read_text()
    receipt=json.loads(content)
    assert receipt['schema']=='devtool.g3r.hosted-g1-failure.v1'
    assert receipt['reason_code']=='GRADLE_PRODUCER_FAILED'
    assert receipt['formal_release_qualified'] is False
    assert 'SECRET_TOKEN' not in content
    assert 'should-not-leak' not in content


def test_failure_diagnostic_never_acceptance_artifact(tmp_path):
    hosted._record_public_failure(tmp_path,source='a'*40,run_id=123,attempt=1,
                                  returncode=1,stderr=b'unclassified failure')
    assert (tmp_path/'android_g1_failure.json').is_file()
    assert not (tmp_path/'android_g1_receipt.json').exists()
    assert json.loads((tmp_path/'android_g1_failure.json').read_text())['status']=='FAIL'
