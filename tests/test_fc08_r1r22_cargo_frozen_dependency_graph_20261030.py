"""R1R22: a real dependency graph drift, not no-deps metadata, is mandatory."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('fc08_r1r22_production',ROOT/'tools/fc08_host_workloads.py')
assert SPEC and SPEC.loader
h=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)
CMD=['cargo','build','--frozen']
LOCK_DIAGNOSTIC='error: the lock file needs to be updated but --locked was passed to prevent this'


def fixture(tmp_path):
    folder=tmp_path/'rust';folder.mkdir()
    (folder/'Cargo.toml').write_text('[package]\nname="smoke"\nversion="0.1.0"\nedition="2021"\n')
    (folder/'Cargo.lock').write_text('original lock file\n')
    (folder/'src').mkdir();(folder/'src/lib.rs').write_text('pub fn value() -> i32 { 8 }\n')
    return folder


def bind_must(monkeypatch,folder):
    def must(argv,cwd,**kw):
        assert argv==CMD and cwd==folder
        assert (folder/'Cargo.toml').read_bytes().find(b'fc08_lock_probe')==-1
        assert not (folder/'fc08_lock_probe').exists()
        return {'stdout_sha256':'a'*64}
    monkeypatch.setattr(h,'must',must)


def bind_run(monkeypatch,folder,*,code=101,tail=LOCK_DIAGNOSTIC,mutate_lock=False,throw=False):
    calls=[]
    def run(argv,cwd,**kw):
        calls.append(argv)
        assert argv==CMD and cwd==folder
        assert b'fc08_lock_probe = { path = "fc08_lock_probe" }' in (folder/'Cargo.toml').read_bytes()
        assert (folder/'fc08_lock_probe/Cargo.toml').is_file()
        assert (folder/'fc08_lock_probe/src/lib.rs').is_file()
        if mutate_lock:(folder/'Cargo.lock').write_text('MUTATED')
        if throw:raise RuntimeError('SIMULATED_CARGO_EXECUTION_FAILURE')
        return {'code':code,'stderr_sha256':'b'*64,'stderr_tail':tail,'stdout_tail':''}
    monkeypatch.setattr(h,'run',run)
    return calls


def test_real_frozen_build_requires_missing_local_dependency_and_restores_owned_fixture(tmp_path,monkeypatch):
    folder=fixture(tmp_path);original=(folder/'Cargo.toml').read_bytes();lock=h.sha_file(folder/'Cargo.lock')
    bind_must(monkeypatch,folder);calls=bind_run(monkeypatch,folder)
    receipt=h.linux_cargo_frozen_lock_refusal(folder)
    assert calls==[CMD]
    assert receipt['refusal_exit']==101
    assert receipt['original_lock_sha256']==lock
    assert receipt['baseline_sha256']==receipt['restored_sha256']=='a'*64
    assert 'local path dependency' in receipt['native']
    assert (folder/'Cargo.toml').read_bytes()==original and h.sha_file(folder/'Cargo.lock')==lock
    assert not (folder/'fc08_lock_probe').exists()


def test_frozen_build_zero_exit_is_not_false_pass(tmp_path,monkeypatch):
    folder=fixture(tmp_path);bind_must(monkeypatch,folder);bind_run(monkeypatch,folder,code=0)
    with pytest.raises(RuntimeError,match='FC08_CARGO_STALE_FROZEN_LOCK_ACCEPTED'):
        h.linux_cargo_frozen_lock_refusal(folder)
    assert not (folder/'fc08_lock_probe').exists()


@pytest.mark.parametrize('diagnostic',['', 'error: failed to compile crate', 'error: permission denied', 'error: failed to download registry crate'])
def test_nonzero_without_lockfile_cause_refused(tmp_path,monkeypatch,diagnostic):
    folder=fixture(tmp_path);bind_must(monkeypatch,folder);bind_run(monkeypatch,folder,tail=diagnostic)
    with pytest.raises(RuntimeError,match='FC08_CARGO_REFUSAL_NOT_LOCKFILE_DRIFT'):
        h.linux_cargo_frozen_lock_refusal(folder)
    assert not (folder/'fc08_lock_probe').exists()


def test_corrupted_lock_detected_and_manifest_restored(tmp_path,monkeypatch):
    folder=fixture(tmp_path);original=(folder/'Cargo.toml').read_bytes()
    bind_must(monkeypatch,folder);bind_run(monkeypatch,folder,mutate_lock=True)
    with pytest.raises(RuntimeError,match='FC08_CARGO_FROZEN_REFUSAL_CHANGED_LOCK'):
        h.linux_cargo_frozen_lock_refusal(folder)
    assert (folder/'Cargo.toml').read_bytes()==original and not (folder/'fc08_lock_probe').exists()


def test_cargo_process_exception_restores_fixture(tmp_path,monkeypatch):
    folder=fixture(tmp_path);original=(folder/'Cargo.toml').read_bytes()
    bind_must(monkeypatch,folder);bind_run(monkeypatch,folder,throw=True)
    with pytest.raises(RuntimeError,match='SIMULATED_CARGO_EXECUTION_FAILURE'):
        h.linux_cargo_frozen_lock_refusal(folder)
    assert (folder/'Cargo.toml').read_bytes()==original and not (folder/'fc08_lock_probe').exists()


def test_preexisting_path_dependency_is_not_overwritten(tmp_path,monkeypatch):
    folder=fixture(tmp_path);probe=folder/'fc08_lock_probe';probe.mkdir();(probe/'marker').write_text('owner')
    with pytest.raises(RuntimeError,match='FC08_CARGO_MANIFEST_OR_LOCK_BASE_UNEXPECTED'):
        h.linux_cargo_frozen_lock_refusal(folder)
    assert (probe/'marker').read_text()=='owner'


def test_source_does_not_use_metadata_no_deps_as_lock_gate():
    source=(ROOT/'tools/fc08_host_workloads.py').read_text()
    portion=source.split('def linux_cargo_frozen_lock_refusal(',1)[1].split('def github_live_restricted_dispatch_refusal(',1)[0]
    assert "command=['cargo','build','--frozen']" in portion
    assert "['cargo','metadata','--no-deps'" not in portion
    assert 'FC08_CARGO_REFUSAL_NOT_LOCKFILE_DRIFT' in portion
    assert h.EXPECTED_CHECKS['linux-polyglot'][-1]=='linux-cargo-frozen-lock-refusal'
    assert len(h.EXPECTED_CHECKS['linux-polyglot'])==14
    assert len(h.EXPECTED_CHECKS['github-actions-dag'])==5
