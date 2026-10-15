"""Retired-ledger G3R v3 evidence tests: source integrity + no false release."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import zipfile
import pytest
from devtool import g3r_evidence_gate as gate
from devtool.g3r_evidence_generate import generate
from devtool.g3r_independent_verify import RETIRED, verify, sha

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module')
def candidate(tmp_path_factory):
    # Hosted shard runners write their JUnit/coverage output in the root repo.
    # Never weaken G3R's clean-checkout requirement to accommodate CI output;
    # qualify from a disposable detached worktree at the same exact Git HEAD.
    tmp = tmp_path_factory.mktemp('g3r-v3')
    isolated = tmp / 'checkout'
    git = ['git', '-c', f'safe.directory={ROOT}']
    subprocess.run(git + ['worktree', 'add', '--detach', str(isolated), 'HEAD'],
                   cwd=ROOT, check=True, capture_output=True, text=True)
    try:
        z = generate(isolated, tmp)
        extracted = tmp / 'extract'
        with zipfile.ZipFile(z) as archive:
            archive.extractall(extracted)
        yield extracted, isolated
    finally:
        subprocess.run(git + ['worktree', 'remove', '--force', str(isolated)],
                       cwd=ROOT, check=True, capture_output=True, text=True)

def _rewrite_receipts(root: Path):
    files=sorted(p for p in root.rglob('*') if p.is_file() and p.name!='SHA256SUMS')
    (root/'SHA256SUMS').write_text(''.join(f'{sha(p.read_bytes())}  {p.relative_to(root).as_posix()}\n' for p in files))

def test_historical_attestations_and_disposition_ledgers_retired():
    assert len(RETIRED)==12
    assert all(not (ROOT/path).exists() for path in RETIRED)

def test_current_source_candidate_is_qualified_only_for_source(candidate):
    evidence, checkout = candidate
    proof=verify(evidence, checkout)
    assert proof['verification']=='PASS_CURRENT_SOURCE_CANDIDATE_ONLY'
    assert proof['source_files']>500
    assert proof['historical_artifacts_retired']==12
    result=gate.evaluate(evidence,checkout,require_native=False)
    assert result['status']=='PASS_CURRENT_SOURCE_ONLY'
    assert result['formal_release_qualified'] is False
    assert result['passed_lanes']==0
    assert result['open_real_substrate_obligations']==list(gate.G3R_REAL_SUBSTRATE_CARRY)

def test_missing_native_lanes_fail_closed(candidate):
    evidence, checkout = candidate
    with pytest.raises(gate.G3REvidenceError,match='native Termux'):
        gate.evaluate(evidence,checkout,require_native=True)

def test_source_manifest_tampering_rejected_even_with_rechecksummed_outer_inventory(candidate,tmp_path):
    evidence, checkout = candidate
    import shutil
    c=tmp_path/'candidate';shutil.copytree(evidence,c)
    p=c/'SOURCE_MANIFEST.json';m=json.loads(p.read_text());m['files'][0]['sha256']='0'*64
    p.write_text(json.dumps(m));_rewrite_receipts(c)
    with pytest.raises(ValueError,match='source SHA mismatch'):
        verify(c,checkout)

def test_fake_release_claim_rejected_even_with_rechecksummed_outer_inventory(candidate,tmp_path):
    evidence, checkout = candidate
    import shutil
    c=tmp_path/'candidate';shutil.copytree(evidence,c)
    p=c/'EVIDENCE_RECORD.json';m=json.loads(p.read_text());m['formal_release_qualified']=True
    p.write_text(json.dumps(m));_rewrite_receipts(c)
    with pytest.raises(ValueError,match='false qualification'):
        verify(c,checkout)

def test_old_format_and_artifact_inventory_rejected(candidate,tmp_path):
    evidence, checkout = candidate
    import shutil
    c=tmp_path/'candidate';shutil.copytree(evidence,c)
    p=c/'EVIDENCE_RECORD.json';m=json.loads(p.read_text());m['schema_version']=2
    p.write_text(json.dumps(m));_rewrite_receipts(c)
    with pytest.raises(ValueError,match='unsupported candidate'):
        verify(c,checkout)

@pytest.mark.parametrize('member',['../evil','/absolute','runs/../../escape','runs/./x','runs\\x'])
def test_archive_traversal_rejected(tmp_path,member):
    z=tmp_path/'malicious.zip'
    with zipfile.ZipFile(z,'w') as a:a.writestr(member,'x')
    with pytest.raises(gate.G3REvidenceError,match='Unsafe'):
        with gate._candidate_dir(z):pass

def test_symlink_archive_rejected(tmp_path):
    z=tmp_path/'malicious.zip';i=zipfile.ZipInfo('link');i.create_system=3;i.external_attr=(stat.S_IFLNK|0o777)<<16
    with zipfile.ZipFile(z,'w') as a:a.writestr(i,'../outside')
    with pytest.raises(gate.G3REvidenceError,match='Symlink'):
        with gate._candidate_dir(z):pass

def test_r04b_historical_gate_never_passes():
    from devtool.remed_r04b_fixed_point import audit_r04b_fixed_point
    result=audit_r04b_fixed_point(ROOT)
    assert result['status']=='not_qualified'
    assert len(result['g3r_real_substrate_carry'])==6
