"""R1R23: real Cargo versioned diagnostics, strict cause, and safe errors."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import re
import pytest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('fc08_r1r23_production',ROOT/'tools/fc08_host_workloads.py')
assert SPEC and SPEC.loader
h=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)

OLD='error: the lock file /tmp/example/Cargo.lock needs to be updated but --locked was passed to prevent this'
NEW='error: cannot update the lock file /home/runner/work/fixture/Cargo.lock because --frozen was passed to prevent this'

@pytest.mark.parametrize('diagnostic,expected',[
    (OLD,'CARGO_LOCKFILE_UPDATE_BLOCKED_LEGACY'),
    (NEW,'CARGO_LOCKFILE_UPDATE_BLOCKED_MODERN'),
    (NEW.replace('--frozen','--locked'),'CARGO_LOCKFILE_UPDATE_BLOCKED_MODERN'),
    (OLD.replace('--locked','--frozen'),'CARGO_LOCKFILE_UPDATE_BLOCKED_LEGACY'),
    ('\x1b[31m'+NEW+'\x1b[0m','CARGO_LOCKFILE_UPDATE_BLOCKED_MODERN'),
])
def test_both_authentic_cargo_wordings(diagnostic,expected):
    result=h._cargo_frozen_refusal_diagnostic({'stderr_tail':diagnostic,'stdout_tail':''})
    assert result['category']==expected
    assert len(result['diagnostic_sha256'])==64
    assert '/home/runner' not in result['error_excerpt']
    assert '/tmp/example' not in result['error_excerpt']
    assert '[PATH]' in result['error_excerpt']

@pytest.mark.parametrize('diagnostic',[
    'error: permission denied writing /tmp/foo/Cargo.lock',
    'error: failed to compile fc08_lock_probe',
    'error: failed to parse manifest at /tmp/secret/Cargo.toml',
    'error: could not resolve registry index in offline mode',
    'error: cannot update the lock file /tmp/foo because --frozen was NOT passed to prevent this',
    'warning: '+NEW,
    'error: unrelated message '+NEW,
    OLD+'\nerror: second failure',
    '',
])
def test_other_errors_never_qualify(diagnostic):
    result=h._cargo_frozen_refusal_diagnostic({'stderr_tail':diagnostic,'stdout_tail':''})
    assert not result['category'].startswith('CARGO_LOCKFILE_UPDATE_BLOCKED_')


def test_primary_diagnostic_also_works_on_stdout():
    result=h._cargo_frozen_refusal_diagnostic({'stderr_tail':'','stdout_tail':NEW})
    assert result['category']=='CARGO_LOCKFILE_UPDATE_BLOCKED_MODERN'


def test_diagnostic_redacts_token_and_path():
    error='error: permission denied at /tmp/secret; authorization=Bearer abcdef; token=highlysecret'
    result=h._cargo_frozen_refusal_diagnostic({'stderr_tail':error,'stdout_tail':''})
    assert result['category']=='NON_LOCKFILE_CARGO_ERROR'
    assert '/tmp/secret' not in result['error_excerpt']
    assert 'abcdef' not in result['error_excerpt']
    assert 'highlysecret' not in result['error_excerpt']
    assert '[REDACTED]' in result['error_excerpt']


def _fixture(folder):
    folder.mkdir()
    (folder/'Cargo.toml').write_text('[package]\nname="smoke"\nversion="0.1.0"\nedition="2021"\n')
    (folder/'Cargo.lock').write_text('original cargo lock')
    (folder/'src').mkdir()
    (folder/'src/lib.rs').write_text('pub fn test() -> u8 { 8 }\n')
    return folder


@pytest.mark.parametrize('diagnostic,passes',[(OLD,True),(NEW,True),('error: compiler failed',False)])
def test_full_real_gate_contract_with_versioned_diag(tmp_path,monkeypatch,diagnostic,passes):
    folder=_fixture(tmp_path/'fixture')
    manifest=(folder/'Cargo.toml').read_bytes()
    lock=h.sha_file(folder/'Cargo.lock')
    called=[]
    def fake_must(argv,cwd,**kwargs):
        assert argv==['cargo','build','--frozen'] and cwd==folder
        assert (folder/'Cargo.toml').read_bytes()==manifest
        assert not (folder/'fc08_lock_probe').exists()
        called.append('must')
        return {'stdout_sha256':'a'*64}
    def fake_run(argv,cwd,**kwargs):
        assert argv==['cargo','build','--frozen'] and cwd==folder
        assert b'fc08_lock_probe' in (folder/'Cargo.toml').read_bytes()
        assert (folder/'fc08_lock_probe/Cargo.toml').is_file()
        called.append('run')
        return {'code':101,'stdout_tail':'','stderr_tail':diagnostic,'stderr_sha256':'b'*64}
    monkeypatch.setattr(h,'must',fake_must)
    monkeypatch.setattr(h,'run',fake_run)
    if passes:
        receipt=h.linux_cargo_frozen_lock_refusal(folder)
        assert receipt['refusal_diagnostic_category'].startswith('CARGO_LOCKFILE_UPDATE_BLOCKED_')
        assert len(receipt['refusal_diagnostic_sha256'])==64
        assert 'error_excerpt' not in receipt
        assert called==['must','run','must']
    else:
        with pytest.raises(RuntimeError,match='FC08_CARGO_REFUSAL_NOT_LOCKFILE_DRIFT.*category=NON_LOCKFILE_CARGO_ERROR.*excerpt=error: compiler failed'):
            h.linux_cargo_frozen_lock_refusal(folder)
        assert called==['must','run']
    assert (folder/'Cargo.toml').read_bytes()==manifest
    assert h.sha_file(folder/'Cargo.lock')==lock
    assert not (folder/'fc08_lock_probe').exists()
