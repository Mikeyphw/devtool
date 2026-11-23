"""QG01-G1-F02 hosted G1 machine-output and fail-closed evidence regression.

No Gradle, Android device or GitHub network work is executed by these tests.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from devtool import android_g1_gate, g3r_hosted_android


def test_g1_main_owns_stdout_and_forwards_progress_to_stderr(monkeypatch, capsys):
    report = {'gate':'ANDROID-G1', 'status':'passed', 'mock':True}
    def fake_run_gate():
        print('RICH_NATIVE_PROGRESS_NOT_MACHINE_DATA')
        return report
    monkeypatch.setattr(android_g1_gate, 'run_gate', fake_run_gate)
    assert android_g1_gate.main() == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == report
    assert captured.out.count('"gate"') == 1
    assert 'RICH_NATIVE_PROGRESS_NOT_MACHINE_DATA' not in captured.out
    assert 'RICH_NATIVE_PROGRESS_NOT_MACHINE_DATA' in captured.err


def _prepare_host(monkeypatch, tmp_path, child):
    workspace = tmp_path / 'source'
    workspace.mkdir()
    source = 'b'*40
    tree = 'c'*40
    for k, v in {
        'GITHUB_ACTIONS':'true', 'RUNNER_OS':'Linux',
        'GITHUB_EVENT_NAME':'workflow_dispatch', 'GITHUB_JOB':'android-g1-hosted',
        'GITHUB_WORKSPACE':str(workspace), 'GITHUB_SHA':source,
        'GITHUB_RUN_ID':'45190682963', 'GITHUB_RUN_ATTEMPT':'1',
    }.items():
        monkeypatch.setenv(k,v)
    for key in ('PREFIX','DEVTOOL_ANDROID_G1_ALLOW_TEST_HOST',
                'DEVTOOL_ANDROID_G1_ADB', 'DEVTOOL_ANDROID_G1_GRADLE',
                'DEVTOOL_ANDROID_G1_JAVA', 'DEVTOOL_ANDROID_G2_ALLOW_TEST_HOST'):
        monkeypatch.delenv(key, raising=False)
    def fake_run(argv, *args, **kwargs):
        if 'git' == argv[0] and 'rev-parse' in argv:
            return SimpleNamespace(stdout=(source if argv[-1]=='HEAD' else tree)+'\n', returncode=0)
        if 'git' == argv[0] and 'status' in argv:
            return SimpleNamespace(stdout='', returncode=0)
        assert 'devtool.android_g1_gate' in argv, argv
        return child
    monkeypatch.setattr(g3r_hosted_android.subprocess,'run',fake_run)
    return tmp_path/'g1-proof',source


def test_non_json_successful_child_fails_closed_with_sanitized_artifact(monkeypatch,tmp_path):
    child=SimpleNamespace(stdout=b'\nRICH PROGRESS\n{ "gate": "ANDROID-G1" }\n',
                          stderr=b'\nSECRET_TOKEN=do-not-leak',returncode=0)
    path, source = _prepare_host(monkeypatch,tmp_path,child)
    with pytest.raises(RuntimeError,match='HOSTED_ANDROID_G1_MACHINE_OUTPUT_INVALID'):
        g3r_hosted_android.produce(path)
    receipt=json.loads((path/'android_g1_failure.json').read_text())
    assert receipt['source_commit']==source
    assert receipt['run_id']==45190682963
    assert receipt['run_attempt']==1
    assert receipt['reason_code']=='MACHINE_STDOUT_NOT_JSON'
    assert receipt['child_returncode']==0
    assert receipt['status']=='FAIL'
    assert receipt['formal_release_qualified'] is False
    assert not (path/'android_g1_receipt.json').exists()
    assert b'SECRET_TOKEN' not in (path/'android_g1_failure.json').read_bytes()


def test_structural_output_rejection_creates_diagnostic_not_pass(monkeypatch,tmp_path):
    child=SimpleNamespace(stdout=b'{"status":"passed","gate":"WRONG"}',
                          stderr=b'',returncode=0)
    path,_=_prepare_host(monkeypatch,tmp_path,child)
    with pytest.raises(RuntimeError,match='HOSTED_ANDROID_G1_UNQUALIFIED_REPORT'):
        g3r_hosted_android.produce(path)
    receipt=json.loads((path/'android_g1_failure.json').read_text())
    assert receipt['reason_code']=='MACHINE_REPORT_UNQUALIFIED'
    assert not (path/'android_g1_receipt.json').exists()


def test_one_clean_report_remains_eligible_for_full_source_receipt(monkeypatch,tmp_path):
    report={'gate':'ANDROID-G1','status':'passed', 'host':{'termux':False},
            'gradle_receipt_id':'g1-real-process','artifact_sha256':'a'*64,
            'standalone_runtime_sealed':True}
    child=SimpleNamespace(stdout=json.dumps(report).encode(),stderr=b'',returncode=0)
    path,source=_prepare_host(monkeypatch,tmp_path,child)
    success=g3r_hosted_android.produce(path)
    assert success['source_commit']==source
    assert success['status']=='PASS'
    assert success['run_attempt']==1
    assert success['output_sha256']==hashlib.sha256((path/'android_g1.log').read_bytes()).hexdigest()
    assert not (path/'android_g1_failure.json').exists()
