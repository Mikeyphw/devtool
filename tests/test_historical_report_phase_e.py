"""Phase E: retained consumers and historical bytes remain source-bound."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'docs/HISTORICAL_REPORT_PHASE_E_PROVENANCE.json'
AUDIT = ROOT / 'docs/HISTORICAL_REPORT_PHASE_E_AUDIT.json'


def test_phase_e_retirement_is_exact_ancestor_and_retrievable():
    doc = json.loads(DATA.read_text())
    assert doc['schema_version'] == 1
    assert doc['source_commit'] == '1bb1053f29ba33bf5283244b2badf3f3dccf4581'
    assert doc['source_tree'] == 'da6a7cb1a0a760826ad87fdc6f0095d2a4e1e200'
    assert doc['formal_release_qualified'] is False
    assert doc['external_consumers'] == 'not independently verified'
    assert len(doc['retired']) == 9
    for entry in doc['retired']:
        rel = entry['path']
        assert Path(rel).name == rel and rel.endswith('_REPORT.md')
        assert not (ROOT / rel).exists()
        assert entry['source_commit'] == doc['source_commit']
        assert entry['size'] > 50
        proc = subprocess.run(['git', 'show', doc['source_commit']+':'+rel], cwd=ROOT, capture_output=True, check=False, timeout=15)
        assert proc.returncode == 0, f'Phase E ancestor missing; CI must use fetch-depth: 0: {rel} {proc.stderr!r}'
        assert len(proc.stdout) == entry['size']
        assert hashlib.sha256(proc.stdout).hexdigest() == entry['sha256']


def test_phase_e_audit_accounts_for_every_original_report():
    audit = json.loads(AUDIT.read_text())
    provenance = json.loads(DATA.read_text())
    assert audit['source_commit'] == provenance['source_commit']
    assert (audit['scanned_reports'], audit['retired_count'], audit['retained_count']) == (53, 9, 44)
    assert len(audit['items']) == 53
    assert len({item['path'] for item in audit['items']}) == 53
    retired = {item['path'] for item in provenance['retired']}
    assert retired == {item['path'] for item in audit['items'] if item['disposition']=='PHASE_E_RETIRED'}
    assert len([item for item in audit['items'] if (ROOT/item['path']).is_file()]) == 44
    for item in audit['items']:
        # A guide generator still names WARNING_SCOPE_ACCOUNTING_REPORT; preserve it.
        if item['executable']:
            assert item['path'] == 'WARNING_SCOPE_ACCOUNTING_REPORT.md'
        if item['path'] in retired:
            assert item['ledgers_or_data'] == 0 and item['docs'] == 0
        elif item['reference_count']:
            assert (ROOT/item['path']).is_file()


def test_phase_e_pres_g1_g2_proofs_remain_current_source_executable():
    from devtool.pres_g2_gate import build_pres_g2_gate, assert_pres_g2_gate_passes
    report = build_pres_g2_gate(repo_root=ROOT, present_reports=[])
    assert_pres_g2_gate_passes(report)
    assert report.missing_reports == ()
    assert len(report.source_proofs) == 10
    assert all(proof.status == 'passed' for proof in report.source_proofs)


def test_phase_e_compatibility_and_release_obligations_unchanged():
    from devtool.compatibility import COMPATIBILITY_ENTRIES
    from devtool.remed_r04b_fixed_point import G3R_REAL_SUBSTRATE_CARRY
    assert len(COMPATIBILITY_ENTRIES) == 24
    assert set(G3R_REAL_SUBSTRATE_CARRY) == {'ENV-A06','ANDROID-A16','DIST-A01','DIST-A12','DIST-A13','TEST-A11'}
