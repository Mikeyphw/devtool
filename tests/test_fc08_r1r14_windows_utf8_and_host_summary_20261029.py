"""FC08 R1R14 native/host-independent evidence for Windows CLI encoding and host receipts."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    path = _ROOT / 'tools' / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fc08 = _load('_fc08_r1r14_workload', 'fc08_host_workloads.py')
summary = _load('_fc08_r1r14_summary', 'fc08_host_summary.py')


def _needs(**updates):
    out = {name: {'result': 'success'} for name in ('dag', 'linux', 'windows', 'macos')}
    out.update({name: {'result': result} for name, result in updates.items()})
    return {'FC08_NEEDS_JSON': json.dumps(out)}


def _receipts(tmp_path):
    identity = {'repository': 'Mikeyphw/devtool', 'sha': 'a' * 40, 'tree': 'b' * 40,
                'ref': 'refs/heads/main', 'run_id': 222, 'attempt': 1}
    artifact_root = tmp_path / "fc08-raw"
    artifact_root.mkdir()
    for lane in fc08.LANES:
        folder = artifact_root / f'fc08-real-{lane}-attempt1'
        folder.mkdir()
        checks = {k: {'validated': True} for k in fc08.EXPECTED_CHECKS[lane]}
        record = {'schema': 'devtool.fc08.host-real-workload.v1', 'lane': lane,
                  'identity': identity, 'checks': checks,
                  'checks_sha256': fc08.sha_bytes(json.dumps(checks, sort_keys=True, separators=(',', ':')).encode()),
                  'status': 'WORKLOAD_COMPLETED_NEEDS_INDEPENDENT_REVIEW',
                  'release_qualified': False, 'device_qualified': False}
        (folder / (lane + '.json')).write_text(json.dumps(record))
    return identity


def test_windows_host_sets_utf8_only_for_nested_devtool(monkeypatch):
    monkeypatch.setattr(fc08.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(fc08, '_fixture_termux_environment', lambda: {})
    assert fc08._devtool_child_environment() == {'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}


def test_nonwindows_host_retains_existing_prefix_with_no_encoding_override(monkeypatch):
    monkeypatch.setattr(fc08.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(fc08, '_fixture_termux_environment', lambda: {'PREFIX': '/data/data/com.termux/files/usr'})
    assert fc08._devtool_child_environment() == {'PREFIX': '/data/data/com.termux/files/usr'}


def test_windows_real_devtool_cli_receives_utf8_env(monkeypatch, tmp_path):
    monkeypatch.setattr(fc08.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(fc08, '_fixture_termux_environment', lambda: {})
    calls = []
    def fake_must(argv, cwd, **kwargs):
        calls.append((argv, kwargs))
        return {'code': 0}
    monkeypatch.setattr(fc08, 'must', fake_must)
    assert fc08.devtool(tmp_path, 'build') == {'code': 0}
    assert calls[0][1]['env']['PYTHONIOENCODING'] == 'utf-8'
    assert calls[0][1]['env']['PYTHONUTF8'] == '1'
    assert calls[0][0][-1] == 'build'


def test_windows_negative_exit_uses_same_environment_without_faking_success(monkeypatch, tmp_path):
    monkeypatch.setattr(fc08.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(fc08, '_fixture_termux_environment', lambda: {})
    monkeypatch.setattr(fc08, 'fixture', lambda *a, **kw: tmp_path)
    seen = []
    def fake_run(argv, cwd, **kwargs):
        seen.append(kwargs)
        return {'code': 37, 'stdout_tail': '987654321 exit 37'}
    monkeypatch.setattr(fc08, 'run', fake_run)
    assert fc08.fail_negative(tmp_path)['rejection_exit'] == 37
    assert seen[0]['extra_env'] == {'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}


def test_real_cp1252_pipe_fails_on_checkmark_but_utf8_child_succeeds():
    code = 'print("\\u2713")'
    env = {**os.environ, 'PYTHONIOENCODING': 'cp1252', 'PYTHONUTF8': '0'}
    bad = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, timeout=15)
    assert bad.returncode != 0
    assert b'UnicodeEncodeError' in bad.stderr
    env.update({'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'})
    good = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, timeout=15)
    assert good.returncode == 0 and good.stdout == '\u2713\n'.encode('utf-8')


def test_summary_reports_exact_windows_failure_not_dag(tmp_path, monkeypatch):
    monkeypatch.setattr(summary, 'identity', lambda env, **kw: {'attempt': 1})
    with pytest.raises(ValueError, match='FAILED_OR_MISSING_GITHUB_HOST_JOB:windows'):
        summary.summarize(tmp_path, _needs(windows='failure'))


def test_summary_reports_multiple_failed_lanes_in_stable_order(tmp_path, monkeypatch):
    monkeypatch.setattr(summary, 'identity', lambda env, **kw: {'attempt': 1})
    with pytest.raises(ValueError, match='FAILED_OR_MISSING_GITHUB_HOST_JOB:linux,windows'):
        summary.summarize(tmp_path, _needs(linux='failure', windows='failure'))


def test_summary_reports_dag_failure_when_it_really_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(summary, 'identity', lambda env, **kw: {'attempt': 1})
    with pytest.raises(ValueError, match='FAILED_OR_MISSING_GITHUB_HOST_JOB:dag'):
        summary.summarize(tmp_path, _needs(dag='failure'))


def test_summary_rejects_missing_and_foreign_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(summary, 'identity', lambda env, **kw: {'attempt': 1})
    for needs in ({'dag': {'result': 'success'}}, {**json.loads(_needs()['FC08_NEEDS_JSON']), 'foreign': {'result': 'success'}}):
        with pytest.raises(ValueError, match='MISSING_OR_UNEXPECTED_GITHUB_HOST_JOBS'):
            summary.summarize(tmp_path, {'FC08_NEEDS_JSON': json.dumps(needs)})


def test_summary_all_four_real_receipts_pending_review_not_release(tmp_path, monkeypatch):
    ident = _receipts(tmp_path)
    monkeypatch.setattr(summary, 'identity', lambda env, **kw: ident)
    result = summary.summarize(tmp_path / 'fc08-raw', _needs())
    assert result['status'] == 'HOSTED_REAL_WORKLOAD_EVIDENCE_CAPTURED_PENDING_INDEPENDENT_REVIEW'
    assert result['release_qualified'] is False and result['fc08_overall_qualified'] is False
    assert len(result['lanes']) == 4


def test_summary_rejects_malformed_receipt_not_silently_promoted(tmp_path, monkeypatch):
    ident = _receipts(tmp_path)
    p = tmp_path / 'fc08-raw' / 'fc08-real-windows-dotnet-msvc-attempt1' / 'windows-dotnet-msvc.json'
    data = json.loads(p.read_text())
    data['checks_sha256'] = '0' * 64
    p.write_text(json.dumps(data))
    monkeypatch.setattr(summary, 'identity', lambda env, **kw: ident)
    with pytest.raises(ValueError, match='CHECK_DIGEST_MISMATCH'):
        summary.summarize(tmp_path / 'fc08-raw', _needs())


def test_summary_import_ignores_existing_hermes_tools_package():
    code = r'''
import importlib.util, sys, types
from pathlib import Path
sys.modules['tools'] = types.ModuleType('tools')
spec = importlib.util.spec_from_file_location('fc08_test_summary', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert 'windows-dotnet-msvc' in module.LANES
print('PASS_REVIEWED_SIBLING_NOT_HERMES')
'''
    child = subprocess.run([sys.executable, '-c', code, str(_ROOT / 'tools/fc08_host_summary.py')], capture_output=True, text=True, timeout=20)
    assert child.returncode == 0, child.stderr
    assert 'PASS_REVIEWED_SIBLING_NOT_HERMES' in child.stdout
