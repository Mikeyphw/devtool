"""G8 source-only G3 amendment must authenticate the complete narrow UI repair."""
from pathlib import Path
import shutil
import pytest
from devtool.actions_qualification import qualification_report
from devtool.github_actions import GitHubActionsError

ROOT = Path(__file__).resolve().parents[1]
PLAN = 'docs/actions/ACTIONS_G08_QUALIFICATION_MATRIX.json'
EXTRA = (
    'docs/actions/ACTIONS_G03_NARROW_TEXTUAL_FIX.md',
    'tests/test_actions_g03_compact_narrow.py',
    'docs/actions/ACTIONS_G03_G8_SOURCE_AMENDMENT_20261014.md',
    'tests/test_actions_g08_g03_amendment.py',
    'docs/actions/ACTIONS_G04_JARSIGNER_UNSIGNED_VERDICT_HOTFIX.md',
    'tests/test_actions_g04_unsigned_jarsigner_truth.py',
)

def fixture(tmp_path):
    import json
    plan = json.loads((ROOT / PLAN).read_text())
    files = {PLAN, plan['original_ledger_path'], *EXTRA}
    files.update(row['path'] for wave in plan['waves'] for row in wave['source_contracts'])
    repo = tmp_path / 'devtool'
    for filename in files:
        path = repo / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / filename, path)
    return repo


def test_verified_g3_transition_is_explicit_but_cannot_qualify_release(tmp_path):
    got = qualification_report(fixture(tmp_path), strict_source=True)
    assert got['source_contract_consistent'] is True
    assert got['qualified_release'] is False
    assert got['campaign_closed'] is False
    assert got['source_attested'] is False
    assert got['original_obligations']['count'] == 88
    assert got['pending_required_gate_count'] == 8
    records = got['verified_source_amendments']
    assert len(records) == 3  # one G3 Textual + two preexisting G4 sources
    g3 = [r for r in records if r['wave'] == 'G3']
    assert len(g3) == 1
    assert g3[0]['path'] == 'devtool/actions_pipeline_textual.py'
    assert g3[0]['original_sha256'] != g3[0]['amended_sha256']
    assert any(r.get('wave') == 'G3' and r.get('status') == 'SOURCE_PINS_AMENDED_VERIFIED'
               for r in got['source_contracts'])
    assert any(r.get('wave') == 'G4' and r.get('status') == 'SOURCE_PINS_AMENDED_VERIFIED'
               for r in got['source_contracts'])


@pytest.mark.parametrize('name', (
    'devtool/actions_pipeline_textual.py',
    'tests/test_actions_g03_compact_narrow.py',
    'docs/actions/ACTIONS_G03_NARROW_TEXTUAL_FIX.md',
    'docs/actions/ACTIONS_G03_G8_SOURCE_AMENDMENT_20261014.md',
    'tests/test_actions_g08_g03_amendment.py',
))
def test_any_g3_source_or_evidence_tamper_fails_closed(tmp_path, name):
    repo = fixture(tmp_path)
    target = repo / name
    target.write_bytes(target.read_bytes() + b'\nUNTRUSTED-CHANGE\n')
    report = qualification_report(repo)
    assert report['source_contract_consistent'] is False
    assert report['qualified_release'] is False
    with pytest.raises(GitHubActionsError, match='source contract'):
        qualification_report(repo, strict_source=True)


def test_unrelated_pinned_source_change_still_fails_closed(tmp_path):
    repo = fixture(tmp_path)
    target = repo / 'devtool/actions_pipeline.py'
    target.write_bytes(target.read_bytes() + b'\ntampered\n')
    with pytest.raises(GitHubActionsError, match='source contract'):
        qualification_report(repo, strict_source=True)


def test_missing_g3_evidence_is_not_accepted(tmp_path):
    repo = fixture(tmp_path)
    (repo / 'docs/actions/ACTIONS_G03_G8_SOURCE_AMENDMENT_20261014.md').unlink()
    assert qualification_report(repo)['source_contract_consistent'] is False
