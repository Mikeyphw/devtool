"""Pin QW02 G1 dispatcher source with an explicit, falsifiable amendment."""
from pathlib import Path
import json
import shutil
import pytest
from devtool.actions_qualification import qualification_report
from devtool.github_actions import GitHubActionsError

ROOT=Path(__file__).resolve().parents[1]
PLAN='docs/actions/ACTIONS_G08_QUALIFICATION_MATRIX.json'
EXTRA=(
    'docs/actions/ACTIONS_G04_JARSIGNER_UNSIGNED_VERDICT_HOTFIX.md',
    'tests/test_actions_g04_unsigned_jarsigner_truth.py',
    'docs/actions/ACTIONS_G03_NARROW_TEXTUAL_FIX.md',
    'tests/test_actions_g03_compact_narrow.py',
    'docs/actions/ACTIONS_G03_G8_SOURCE_AMENDMENT_20261014.md',
    'tests/test_actions_g08_g03_amendment.py',
    'docs/actions/ACTIONS_G02_SOURCE_TRANSITION_20261020.json',
    'docs/actions/ACTIONS_A08_PRIVATE_HANDOFF.md',
    'tests/test_actions_a08_local_handoff.py',
    'docs/actions/ACTIONS_G03_R01_FIXTURE_TRANSITION_20261020.json',

)

def fixture(tmp_path):
    plan=json.loads((ROOT/PLAN).read_text())
    files={PLAN,plan['original_ledger_path'],*EXTRA}
    files.update(row['path'] for wave in plan['waves'] for row in wave['source_contracts'])
    root=tmp_path/'devtool'
    for rel in files:
        target=root/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/rel,target)
    return root

def test_exact_g1_dispatch_amendment_preserves_pending_live_truth(tmp_path):
    result=qualification_report(fixture(tmp_path),strict_source=True)
    assert result['source_contract_consistent'] is True
    assert result['qualified_release'] is False
    assert result['source_attested'] is False
    assert result['campaign_closed'] is False
    assert result['pending_required_gate_count']==8
    g1=[row for row in result['verified_g1_dispatch_amendments'] if row['wave']=='G1']
    assert len(g1)==1
    assert g1[0]['path']=='devtool/actions_dispatch.py'
    assert g1[0]['original_sha256']!=g1[0]['amended_sha256']
    assert any(row.get('wave')=='G1' and row.get('status')=='SOURCE_PINS_AMENDED_VERIFIED'
               for row in result['source_contracts'])

@pytest.mark.parametrize('rel',(
    'devtool/actions_dispatch.py',
    'tests/test_actions_00_01_github_provider.py',
))
def test_g1_amendment_fails_closed_when_source_or_evidence_changes(tmp_path,rel):
    root=fixture(tmp_path)
    p=root/rel;p.write_bytes(p.read_bytes()+b'\ntamper\n')
    assert qualification_report(root)['source_contract_consistent'] is False
    with pytest.raises(GitHubActionsError,match='source contract'):
        qualification_report(root,strict_source=True)

def test_symlink_or_missing_g1_evidence_rejected(tmp_path):
    root=fixture(tmp_path)
    p=root/'tests/test_actions_00_01_github_provider.py'
    p.unlink();p.symlink_to(ROOT/'tests/test_actions_00_01_github_provider.py')
    with pytest.raises(GitHubActionsError,match='source contract'):
        qualification_report(root,strict_source=True)

def test_unrelated_original_g1_contract_still_fails_closed(tmp_path):
    root=fixture(tmp_path)
    path=root/'devtool/actions_runs.py'
    path.write_bytes(path.read_bytes()+b'\nmodified unrelated source\n')
    with pytest.raises(GitHubActionsError,match='source contract'):
        qualification_report(root,strict_source=True)


def test_amendment_document_and_source_preflight_regressions_are_unmodified():
    import hashlib
    docs=ROOT/'docs/actions/ACTIONS_G01_QW02_SOURCE_AMENDMENT.md'
    test=ROOT/'tests/test_actions_02_workflow_dispatch.py'
    assert docs.is_file() and test.is_file()
    assert hashlib.sha256(docs.read_bytes()).hexdigest() == '7b4d40eb45c45a7002925e4e3a5b81e46ecdedf5fa9d4e03996a07ed1f677ff7'
    assert hashlib.sha256(test.read_bytes()).hexdigest() == 'fbe93a995071c5eb78c6e09adc4c6ecf18e9f6c2fcb5a594518359272ad7040d'
