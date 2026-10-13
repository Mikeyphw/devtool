"""No optimistic promotion of historical ACTIONS evidence."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_frozen_obligations_are_complete_and_no_unverified_promotion():
    doc = ROOT/'docs/actions'
    ledger = json.loads((doc/'ACTIONS_BASELINE_OBLIGATIONS.json').read_text())
    index = json.loads((doc/'ACTIONS_G00_G01_EVIDENCE_INDEX.json').read_text())
    rows = ledger['obligations']
    assert len(rows) == index['roadmap']['obligations'] == 88
    assert len({row['id'] for row in rows}) == 88
    assert sum(ledger['counts'].values()) == len(rows)
    for row in rows:
        assert row['status'] in ('LIVE_VERIFIED','SOURCE_TESTED','PARTIAL','OPEN','EXCLUDED')
        assert row['closure_wave'] in ('G0','G1','G2','G3','G4','G5','G6','G7','G8') or (row['status'] == 'EXCLUDED' and row['closure_wave'] == '')
    assert index['actual_prior_evidence']['source_attested'] is False
    assert index['actual_prior_evidence']['qualified_release'] is False
    assert index['baseline_tests']['previous_actions']['skipped'] == 6
    assert index['g1_status'].endswith('pending')


def test_frozen_workflows_match_unmodified_source_snapshot():
    index = json.loads((ROOT/'docs/actions/ACTIONS_G00_G01_EVIDENCE_INDEX.json').read_text())
    for relative, expected in index['snapshot_source']['workflow_sha256'].items():
        assert hashlib.sha256((ROOT/relative).read_bytes()).hexdigest() == expected
