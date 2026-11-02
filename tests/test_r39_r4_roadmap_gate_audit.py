from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import subprocess
import sys

import pytest

from devtool.roadmap_gate_audit import (
    GateAuditError, _OPEN, audit, audit_workflows, verify,
)

ROOT = Path(__file__).resolve().parents[1]


def test_current_repository_source_inventory_is_complete():
    report = audit(ROOT)
    assert report['admission']['status'] == 'PASS'
    assert report['workflows']['workflow_count'] >= 27
    assert not report['workflows']['first_party_violations']
    assert report['qualification']['release_attested'] is False
    assert report['independent_gates_this_source']['full_legacy_engine_retirement'] == 'BLOCKED'


def test_git_source_identity_is_current():
    report = audit(ROOT)
    for key, spec in (('commit', 'HEAD'), ('tree', 'HEAD^{tree}')):
        expected = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', spec], text=True).strip()
        assert report['source'][key] == expected


@pytest.mark.parametrize('field', [
    'legacy_retirement_authorized', 'release_attested', 'signing_attested',
    'device_attested', 'native_parallel_parity_attested',
    'hosted_real_consumer_attested', 'full_current_sha_ci_attested',
])
def test_unproven_qualifications_are_rejected(field):
    report = audit(ROOT)
    report['qualification'][field] = True
    with pytest.raises(GateAuditError, match='UNAUTHORIZED_QUALIFICATION'):
        verify(report)


@pytest.mark.parametrize('name', tuple(_OPEN))
def test_unproven_gates_cannot_be_claimed_pass(name):
    report = audit(ROOT)
    report['independent_gates_this_source'][name] = 'PASS'
    with pytest.raises(GateAuditError, match='UNAUTHORIZED_GATE_CLOSURE'):
        verify(report)


def test_old_action_version_detected(tmp_path):
    workflow_dir = tmp_path / '.github' / 'workflows'
    workflow_dir.mkdir(parents=True)
    for idx in range(20):
        (workflow_dir / f'w{idx}.yml').write_text('jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v6\n')
    assert audit_workflows(tmp_path)['status'] == 'PASS'
    (workflow_dir / 'w0.yml').write_text('jobs:\n  a:\n    steps:\n      - uses: actions/upload-artifact@v5\n')
    report = audit_workflows(tmp_path)
    assert report['status'] == 'FAIL'
    assert 'w0.yml:actions/upload-artifact@v5:expected_v7' in report['first_party_violations']


def test_historical_receipt_cannot_be_masqueraded_as_current_sha():
    report = audit(ROOT)
    mismatch = dict(report['source'])
    mismatch['commit'] = '0' * 40 if report['source']['commit'] != '0' * 40 else '1' * 40
    with pytest.raises(GateAuditError, match='SOURCE_MISMATCH'):
        verify(report, source=mismatch)

    # The artifact validator runs BEFORE auto-commit, so the real HEAD may
    # legitimately equal a historical receipt's commit. Replacing the field
    # with that same SHA is a no-op, not evidence tampering. Simulate the
    # post-commit source with a DISTINCT SHA before testing impersonation.
    fake_new_source = dict(report['source'])
    historical_commit = report['historical_exact_source_evidence'][0]['commit']
    fake_new_source['commit'] = 'f' * 40 if historical_commit != 'f' * 40 else 'e' * 40
    report['source'] = fake_new_source
    verify(report, source=fake_new_source)  # Genuine old receipt remains valid.
    report['historical_exact_source_evidence'][0]['commit'] = fake_new_source['commit']
    with pytest.raises(GateAuditError, match='HISTORICAL_PROVENANCE_CHANGED'):
        verify(report, source=fake_new_source)


def test_historical_receipt_detects_actual_tampering_at_original_baseline():
    report = audit(ROOT)
    recorded = report['historical_exact_source_evidence'][0]['commit']
    # This must be different even when current HEAD is the historical source.
    report['historical_exact_source_evidence'][0]['commit'] = (
        '0' * 40 if recorded != '0' * 40 else '1' * 40
    )
    with pytest.raises(GateAuditError, match='HISTORICAL_PROVENANCE_CHANGED'):
        verify(report, source=report['source'])


def test_missing_source_component_refused(tmp_path):
    # The auditor never treats an arbitrary directory as an accepted source.
    with pytest.raises(GateAuditError):
        audit(tmp_path)


def test_cli_machine_contract():
    proc = subprocess.run([sys.executable, '-m', 'devtool.roadmap_gate_audit', '--json'],
                          cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    verify(report)


def test_worktree_ignores_untracked_workflow_outside_versioned_source(tmp_path):
    workflow_dir = tmp_path / '.github' / 'workflows'
    workflow_dir.mkdir(parents=True)
    subprocess.run(['git', '-C', str(tmp_path), 'init', '-q'], check=True)
    for idx in range(20):
        (workflow_dir / f'w{idx}.yml').write_text('jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v6\n')
    subprocess.run(['git', '-C', str(tmp_path), 'add', '.github/workflows'], check=True)
    rogue = workflow_dir / 'local-debug.yml'
    rogue.write_text('jobs:\n  a:\n    steps:\n      - uses: actions/upload-artifact@v5\n')
    report = audit_workflows(tmp_path)
    assert report['status'] == 'PASS'
    assert report['workflow_count'] == 20
    # The same obsolete Action is rejected as soon as it is tracked.
    subprocess.run(['git', '-C', str(tmp_path), 'add', str(rogue)], check=True)
    report = audit_workflows(tmp_path)
    assert report['status'] == 'FAIL'
    assert 'local-debug.yml:actions/upload-artifact@v5:expected_v7' in ' '.join(report['first_party_violations'])


def test_action_runtime_refusal_includes_offending_action(tmp_path):
    source = {'commit': 'a'*40, 'tree': 'b'*40}
    report = {
        'schema': 'devtool.roadmap-gate-audit.v1', 'source': source,
        'workflows': {
            'status': 'FAIL',
            'first_party_violations': ['legacy.yml:actions/upload-artifact@v5:expected_v7'],
        },
    }
    with pytest.raises(GateAuditError, match='legacy.yml:actions/upload-artifact@v5:expected_v7'):
        verify(report, source=source)


def test_r37_mmrl_pinned_actions_are_node24_source_verified():
    """R37 source is in the exact GitHub baseline, not older QA snapshots."""
    path = ROOT / '.github/workflows/devtool-r37-mmrl-device-source.yml'
    source = path.read_text(encoding='utf-8')
    assert 'actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803' in source
    assert 'actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1' in source
    assert 'actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9' in source
    assert '11bd71901bbe5b1630ceea73d27597364c9af683' not in source
    assert 'a26af69be951a213d495a4c3e4e4022e16d87065' not in source
    assert 'ea165f8d65b6e75b540449e92b4886f43607fa02' not in source
    assert audit_workflows(ROOT)['status'] == 'PASS'
