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
    # G00/G01 binds historical bytes from the 2026-10-13 capture, *not*
    # the live workflow files (which are allowed to receive security updates).
    index = json.loads((ROOT/'docs/actions/ACTIONS_G00_G01_EVIDENCE_INDEX.json').read_text())
    captured = index['snapshot_source']
    assert captured['head'] == 'e72335640aab0db58ae96c3e319a6bcec1e2ac19'
    archive = ROOT/'docs/actions/frozen_source/e7233564'
    for relative, expected in captured['workflow_sha256'].items():
        historical = archive/relative
        assert historical.is_file() and not historical.is_symlink()
        assert hashlib.sha256(historical.read_bytes()).hexdigest() == expected
        # The current workflow exists, but its digest is not historical proof.
        assert (ROOT/relative).is_file()


def test_frozen_snapshot_hashes_reject_tampering(tmp_path):
    index = json.loads((ROOT/'docs/actions/ACTIONS_G00_G01_EVIDENCE_INDEX.json').read_text())
    for relative, expected in index['snapshot_source']['workflow_sha256'].items():
        original = (ROOT/'docs/actions/frozen_source/e7233564'/relative).read_bytes()
        assert hashlib.sha256(original).hexdigest() == expected
        tampered = tmp_path/Path(relative).name
        tampered.write_bytes(original + b'\n# simulated history rewrite\n')
        assert hashlib.sha256(tampered.read_bytes()).hexdigest() != expected
