"""R1R20: real offline lock/ELF refusals and bounded hosted DAG verifier cases."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest import mock

import pytest

ROOT=Path(__file__).resolve().parents[1]
MODULE=ROOT/'tools/fc08_host_workloads.py'
SPEC=importlib.util.spec_from_file_location('_fc08_r1r20_reviewed_runner',MODULE)
assert SPEC and SPEC.loader
h=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)


def test_expected_checks_are_additive_and_bounded():
    assert set(('dag-verifier-adversarial',)).issubset(h.EXPECTED_CHECKS['github-actions-dag'])
    assert set(('linux-lock-drift-refusal','linux-native-abi-refusal')).issubset(h.EXPECTED_CHECKS['linux-polyglot'])
    assert len(h.EXPECTED_CHECKS['windows-dotnet-msvc'])==8
    assert len(h.EXPECTED_CHECKS['macos-native'])==3


def test_lock_drift_real_uv_refusal_and_restoration_when_uv_installed(tmp_path):
    # Run only with exact uv installation, but do not introduce G1 skips on
    # shard runners without uv: the deterministic negative contract is below.
    if shutil.which('uv') is None:
        return
    root=tmp_path/'manager-uv';root.mkdir()
    (root/'pyproject.toml').write_text('[project]\nname="fc08-smoke"\nversion="0.1.0"\nrequires-python=">=3.11"\ndependencies=[]\n')
    subprocess.run(['uv','lock','--offline'],cwd=root,check=True,timeout=90,capture_output=True)
    before=(root/'pyproject.toml').read_bytes()
    lock=h.sha_file(root/'uv.lock')
    receipt=h.linux_lock_drift_refusal(root)
    assert receipt['lock_sha256']==lock
    assert receipt['refusal_exit']!=0
    assert (root/'pyproject.toml').read_bytes()==before
    assert h.sha_file(root/'uv.lock')==lock


def test_lock_drift_zero_exit_is_refused_without_mutating_owned_lock(tmp_path,monkeypatch):
    p=tmp_path/'p';p.mkdir()
    (p/'pyproject.toml').write_text('[project]\nversion="0.1.0"\n')
    (p/'uv.lock').write_text('lock')
    monkeypatch.setattr(h,'run',lambda *a,**kw:{'code':0,'stdout_sha256':'0'*64,'stderr_sha256':'0'*64})
    original=(p/'pyproject.toml').read_bytes()
    with pytest.raises(RuntimeError,match='STALE_LOCK_ACCEPTED'):
        h.linux_lock_drift_refusal(p)
    assert (p/'pyproject.toml').read_bytes()==original


def test_lock_drift_mutated_lock_refused_and_manifest_restored(tmp_path,monkeypatch):
    p=tmp_path/'p';p.mkdir()
    (p/'pyproject.toml').write_text('[project]\nversion="0.1.0"\n')
    (p/'uv.lock').write_text('lock')
    original=(p/'pyproject.toml').read_bytes()
    def mutate(*a,**kw):
        (p/'uv.lock').write_text('invalid-mutation')
        return {'code':1,'stdout_sha256':'0'*64,'stderr_sha256':'0'*64}
    monkeypatch.setattr(h,'run',mutate)
    with pytest.raises(RuntimeError,match='LOCK_MUTATED_BY_REFUSAL'):
        h.linux_lock_drift_refusal(p)
    assert (p/'pyproject.toml').read_bytes()==original


def test_lock_drift_fails_closed_if_expected_original_is_absent(tmp_path):
    p=tmp_path/'p';p.mkdir()
    (p/'pyproject.toml').write_text('version="3.0.0"\n')
    (p/'uv.lock').write_text('lock')
    with pytest.raises(RuntimeError,match='BASE_UNEXPECTED'):
        h.linux_lock_drift_refusal(p)


def test_abi_rejects_wrong_host_before_inspection(tmp_path,monkeypatch):
    monkeypatch.setattr(h.platform,'machine',lambda:'aarch64')
    with pytest.raises(RuntimeError,match='HOST_UNSUPPORTED'):
        h.linux_native_abi_refusal(tmp_path)


def test_abi_rejects_missing_binary(tmp_path,monkeypatch):
    monkeypatch.setattr(h.platform,'machine',lambda:'x86_64')
    with pytest.raises(RuntimeError,match='BINARY_MISSING'):
        h.linux_native_abi_refusal(tmp_path)


def test_abi_uses_real_elf_header_and_rejects_declared_aarch64(tmp_path,monkeypatch):
    # A genuine native executable is created with local C++ toolchain if
    # available, otherwise a minimal ELF header exercises the same production
    # RM13 parser. This is unit coverage, not evidence of a hosted CMake build.
    b=tmp_path/'fc08main'
    data=bytearray(64)
    data[:4]=b'\x7fELF';data[4:6]=b'\x02\x01';data[16:18]=(2).to_bytes(2,'little');data[18:20]=(62).to_bytes(2,'little')
    b.write_bytes(data)
    monkeypatch.setattr(h.platform,'machine',lambda:'x86_64')
    receipt=h.linux_native_abi_refusal(tmp_path)
    assert receipt['binary_sha256']==h.sha_file(b)
    assert receipt['accepted_abi']=='elf-x86_64'
    assert receipt['refused_abi']=='elf-aarch64'


def test_abi_refuses_symlink_and_duplicate_ambiguity(tmp_path,monkeypatch):
    monkeypatch.setattr(h.platform,'machine',lambda:'x86_64')
    b=tmp_path/'fc08main';b.write_bytes(b'ELF')
    (tmp_path/'sub').mkdir();(tmp_path/'sub'/'fc08main').write_bytes(b'ELF')
    with pytest.raises(RuntimeError,match='BINARY_MISSING_OR_AMBIGUOUS'):
        h.linux_native_abi_refusal(tmp_path)
    (tmp_path/'sub'/'fc08main').unlink()
    with pytest.raises(ValueError,match='RM-13'):
        h.linux_native_abi_refusal(tmp_path)


def test_dag_adversarial_runs_real_summary_process_in_disposable_repo(tmp_path,monkeypatch):
    # Real subprocess execution against the reviewed summary and workload
    # sources. No token or live dispatch is provided to the synthetic cases.
    repo=tmp_path/'source';tools=repo/'tools';tools.mkdir(parents=True)
    (repo/'.gitignore').write_text('__pycache__/\n*.pyc\n')
    for name in ('fc08_host_workloads.py','fc08_host_summary.py'):
        shutil.copy2(ROOT/'tools'/name,tools/name)
    for argv in (['git','init','-q'],['git','add','.'],
                 ['git','-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','-qm','source']):
        subprocess.run(argv,cwd=repo,check=True,capture_output=True,timeout=30)
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    env={'GITHUB_SHA':sha,'GITHUB_REPOSITORY':'Mikeyphw/devtool','GITHUB_REF':'refs/heads/main',
         'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'2'}
    for k,v in env.items():monkeypatch.setenv(k,v)
    monkeypatch.setattr(h,'SOURCE',repo)
    identity=h.identity(dict(os.environ))
    # The caller supplies an already existing temporary base, just like main.
    base=tmp_path/'run';base.mkdir()
    result=h.dag_verifier_adversarial(base,identity)
    assert set(result['negative_cases'])=={'failed-dag','failed-windows','missing-needs','replayed-attempt'}
    assert result['live_dispatch_denial_qualified'] is False
    assert result['token_permissions_qualified'] is False
    assert all(row['exit']!=0 for row in result['negative_cases'].values())


def test_dag_adversarial_source_refusal_protects_host_identity(tmp_path,monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    with mock.patch.object(h,'identity',side_effect=RuntimeError('REFUSED_SOURCE_SHA_MISMATCH')):
        with pytest.raises(RuntimeError,match='SOURCE_SHA_MISMATCH'):
            h.dag(tmp_path)


def test_workflow_stays_read_only_with_no_dispatch_or_permissions_escalation():
    y=(ROOT/'.github/workflows/devtool-fc08-host-workloads.yml').read_text()
    assert 'permissions:\n  contents: read' in y
    assert 'contents: write' not in y and 'actions: write' not in y
    assert 'workflow_dispatch:' in y and 'if-no-files-found: error' in y
    assert 'needs: [dag, linux, windows, macos]' in y
