"""R1R21 bounded real GitHub permission refusal and Cargo frozen lock contracts."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError
import io
import pytest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('fc08_r1r21_production',ROOT/'tools/fc08_host_workloads.py')
assert SPEC and SPEC.loader
h=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)
IDENT={'repository':'Mikeyphw/devtool','sha':'b'*40,'tree':'c'*40,
       'ref':'refs/heads/main','run_id':1234,'attempt':1}

class Response:
    def __init__(self,status:int, body:bytes=b''):
        self.status=status;self.body=body
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,n=-1):return self.body[:n]


def test_github_live_real_api_denial_with_secret_redacted(monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    secret='a-beacon-that-must-never-be-in-receipts'
    calls=[]
    def fake(req,timeout):
        calls.append(req)
        if req.get_method()=='GET':
            return Response(200,json.dumps({'type':'file','path':'.github/workflows/devtool-fc08-host-evidence.yml'}).encode())
        assert req.get_method()=='POST'
        assert json.loads(req.data)=={'ref':'main'}
        raise HTTPError(req.full_url,403,'denied',{},io.BytesIO(b'RESTRICTED'))
    monkeypatch.setattr(h,'urlopen',fake)
    receipt=h.github_live_restricted_dispatch_refusal(IDENT,token=secret)
    assert receipt['dispatch_http_status']==403
    assert receipt['source_workflow_read_http_status']==200
    assert receipt['live_success_retry_or_dedup_qualified'] is False
    assert 'beacon' not in json.dumps(receipt)
    assert len(calls)==2
    assert calls[0].get_method()=='GET' and calls[1].get_method()=='POST'
    assert calls[0].full_url.endswith('?ref='+IDENT['sha'])
    assert '/devtool-fc08-host-evidence.yml/dispatches' in calls[1].full_url
    assert calls[0].get_header('Authorization')=='Bearer '+secret


@pytest.mark.parametrize('code',[200,204,401,404,429,500])
def test_restricted_dispatch_rejects_anything_except_actual_403(monkeypatch,code):
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    def fake(req,timeout):
        if req.get_method()=='GET':
            return Response(200,b'{"type":"file","path":".github/workflows/devtool-fc08-host-evidence.yml"}')
        if code in (200,204):return Response(code)
        raise HTTPError(req.full_url,code,'not qualified',{},io.BytesIO())
    monkeypatch.setattr(h,'urlopen',fake)
    with pytest.raises(RuntimeError,match='NOT_PROVEN_DENIED'):
        h.github_live_restricted_dispatch_refusal(IDENT,token='restricted')


def test_restricted_dispatch_requires_real_action_and_credentials(monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS',raising=False)
    with pytest.raises(RuntimeError,match='ONLY_REAL_GITHUB_RUN'):
        h.github_live_restricted_dispatch_refusal(IDENT,token='restricted')
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    with pytest.raises(RuntimeError,match='INVALID_SOURCE_REF'):
        h.github_live_restricted_dispatch_refusal({**IDENT,'ref':'refs/heads/feature'},token='restricted')
    monkeypatch.delenv('GITHUB_TOKEN',raising=False)
    with pytest.raises(RuntimeError,match='TOKEN_MISSING'):
        h.github_live_restricted_dispatch_refusal(IDENT)


def test_restricted_dispatch_refuses_untrusted_workflow_or_read_failed(monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    monkeypatch.setattr(h,'urlopen',lambda *a,**k:Response(200,b'{"type":"dir","path":"unexpected"}'))
    with pytest.raises(RuntimeError,match='WRONG_WORKFLOW_IDENTITY'):
        h.github_live_restricted_dispatch_refusal(IDENT,token='read-only')
    def failure(req,timeout):raise URLError('offline')
    monkeypatch.setattr(h,'urlopen',failure)
    with pytest.raises(RuntimeError,match='SOURCE_READ_FAILED'):
        h.github_live_restricted_dispatch_refusal(IDENT,token='read-only')


def test_workflow_explicit_readonly_authority_no_token_in_artifact():
    y=(ROOT/'.github/workflows/devtool-fc08-host-workloads.yml').read_text()
    assert 'permissions:\n  contents: read\n  actions: none' in y
    assert 'actions: write' not in y
    assert 'GITHUB_TOKEN: ${{ github.token }}' in y
    assert y.count('GITHUB_TOKEN: ${{ github.token }}')==1
    assert 'persist-credentials: false' in y
    assert 'github-live-restricted-dispatch-refusal' in h.EXPECTED_CHECKS['github-actions-dag']
    assert 'linux-cargo-frozen-lock-refusal' in h.EXPECTED_CHECKS['linux-polyglot']
    assert len(h.EXPECTED_CHECKS['windows-dotnet-msvc'])==8
    assert len(h.EXPECTED_CHECKS['macos-native'])==3


def create_cargo(tmp_path):
    folder=tmp_path/'rust';folder.mkdir()
    (folder/'Cargo.toml').write_text('[package]\nname="smoke"\nversion="0.1.0"\n')
    (folder/'Cargo.lock').write_text('original lock file\n')
    return folder


def test_cargo_lock_drift_real_subprocess_protocol_success_mock(tmp_path,monkeypatch):
    folder=create_cargo(tmp_path)
    original=(folder/'Cargo.toml').read_bytes()
    original_lock=h.sha_file(folder/'Cargo.lock')
    run_calls=[]
    def must(argv,cwd,**kwargs):
        assert argv==['cargo','metadata','--no-deps','--format-version','1','--frozen']
        assert cwd==folder
        return {'stdout_sha256':'a'*64}
    def run(argv,cwd,**kwargs):
        run_calls.append((argv,cwd))
        assert b'version="0.2.0"' in (folder/'Cargo.toml').read_bytes()
        return {'code':101,'stderr_sha256':'f'*64}
    monkeypatch.setattr(h,'must',must)
    monkeypatch.setattr(h,'run',run)
    receipt=h.linux_cargo_frozen_lock_refusal(folder)
    assert receipt['refusal_exit']==101
    assert receipt['original_lock_sha256']==original_lock
    assert receipt['baseline_sha256']==receipt['restored_sha256']=='a'*64
    assert (folder/'Cargo.toml').read_bytes()==original
    assert h.sha_file(folder/'Cargo.lock')==original_lock
    assert len(run_calls)==1


def test_cargo_refuses_exit_zero_and_restores_manifest(tmp_path,monkeypatch):
    folder=create_cargo(tmp_path);original=(folder/'Cargo.toml').read_bytes()
    monkeypatch.setattr(h,'must',lambda *a,**k:{'stdout_sha256':'a'*64})
    monkeypatch.setattr(h,'run',lambda *a,**k:{'code':0,'stderr_sha256':'b'*64})
    with pytest.raises(RuntimeError,match='STALE_FROZEN_LOCK_ACCEPTED'):
        h.linux_cargo_frozen_lock_refusal(folder)
    assert (folder/'Cargo.toml').read_bytes()==original


def test_cargo_refuses_mutated_lock_and_restores_manifest(tmp_path,monkeypatch):
    folder=create_cargo(tmp_path);original=(folder/'Cargo.toml').read_bytes()
    monkeypatch.setattr(h,'must',lambda *a,**k:{'stdout_sha256':'a'*64})
    def tamper(*a,**k):
        (folder/'Cargo.lock').write_text('changed')
        return {'code':101,'stderr_sha256':'b'*64}
    monkeypatch.setattr(h,'run',tamper)
    with pytest.raises(RuntimeError,match='REFUSAL_CHANGED_LOCK'):
        h.linux_cargo_frozen_lock_refusal(folder)
    assert (folder/'Cargo.toml').read_bytes()==original


def test_cargo_refuses_missing_unexpected_baseline(tmp_path):
    folder=create_cargo(tmp_path)
    (folder/'Cargo.toml').write_text('version="9.9.9"')
    with pytest.raises(RuntimeError,match='BASE_UNEXPECTED'):
        h.linux_cargo_frozen_lock_refusal(folder)
