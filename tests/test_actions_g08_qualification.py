"""G8 qualification can never silently upgrade historical or untrusted evidence."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from devtool.actions_qualification import qualification_report
from devtool.github_actions import GitHubActionsError, actions_cli
from devtool.cli import build_parser
from devtool.core_registry import build_core_registry

SOURCE = Path(__file__).resolve().parents[1]
PLAN = 'docs/actions/ACTIONS_G08_QUALIFICATION_MATRIX.json'


def fixture(tmp_path):
    root = tmp_path / 'devtool'
    plan = json.loads((SOURCE / PLAN).read_text())
    paths = {PLAN, plan['original_ledger_path']}
    paths.update(c['path'] for wave in plan['waves'] for c in wave['source_contracts'])
    paths.update({'docs/actions/ACTIONS_G03_R01_FIXTURE_TRANSITION_20261020.json',
                  'docs/actions/ACTIONS_A08_PRIVATE_HANDOFF.md',
                  'tests/test_actions_a08_local_handoff.py',
                  'docs/actions/ACTIONS_G02_SOURCE_TRANSITION_20261020.json',
                  'docs/actions/ACTIONS_G04_JARSIGNER_UNSIGNED_VERDICT_HOTFIX.md',
                  'tests/test_actions_g04_unsigned_jarsigner_truth.py',
                  'docs/actions/ACTIONS_G03_NARROW_TEXTUAL_FIX.md',
                  'tests/test_actions_g03_compact_narrow.py',
                  'docs/actions/ACTIONS_G03_G8_SOURCE_AMENDMENT_20261014.md',
                  'tests/test_actions_g08_g03_amendment.py'})
    for name in paths:
        dest = root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / name, dest)
    return root


def test_pinned_plan_preserves_88_and_all_pending_gates(tmp_path):
    root = fixture(tmp_path)
    got = qualification_report(root, strict_source=True)
    assert got['source_contract_consistent'] is True
    assert got['original_obligations']['count'] == 88
    assert got['original_obligations']['historical_status_counts'] == {
        'LIVE_VERIFIED':23, 'SOURCE_TESTED':21, 'PARTIAL':32, 'OPEN':11, 'EXCLUDED':1}
    assert got['pending_required_gate_count'] == 8
    assert len(got['acceptance_gates']) == 9
    assert got['qualified_release'] is False and got['source_attested'] is False
    assert got['campaign_closed'] is False
    assert got['historical_dependencies']['V105_pre_actions']['required_reconciliation'] is True
    assert got['user_excluded_paid_github_gate'] is True


def test_source_drift_is_reported_not_promoted(tmp_path):
    root = fixture(tmp_path)
    (root/'devtool/actions_pipeline.py').write_text('injected test drift')
    got = qualification_report(root)
    assert got['source_contract_consistent'] is False
    assert got['campaign_closed'] is False
    assert any(row.get('status') == 'SOURCE_PINS_MISMATCH' for row in got['source_contracts'])
    with pytest.raises(GitHubActionsError, match='source contract'):
        qualification_report(root, strict_source=True)


def test_missing_anchored_source_or_symlink_is_not_success(tmp_path):
    root = fixture(tmp_path)
    target = root / 'devtool/actions_pipeline.py'
    target.unlink()
    assert qualification_report(root)['source_contract_consistent'] is False
    target.symlink_to(SOURCE/'devtool/actions_pipeline.py')
    assert qualification_report(root)['source_contract_consistent'] is False
    with pytest.raises(GitHubActionsError): qualification_report(root, strict_source=True)


def test_modified_matrix_forged_live_pass_is_refused(tmp_path):
    root = fixture(tmp_path)
    obj = json.loads((root/PLAN).read_text())
    obj['gates'][0]['live_status'] = 'passed'
    (root/PLAN).write_text(json.dumps(obj))
    with pytest.raises(GitHubActionsError, match='matrix SHA-256'):
        qualification_report(root)


def test_mutated_historical_ledger_rejected(tmp_path):
    root = fixture(tmp_path)
    ledger = root/'docs/actions/ACTIONS_BASELINE_OBLIGATIONS.json'
    o = json.loads(ledger.read_text())
    o['obligations'][0]['status'] = 'LIVE_VERIFIED'
    ledger.write_text(json.dumps(o))
    with pytest.raises(GitHubActionsError, match='ledger SHA-256'):
        qualification_report(root)


def test_ledger_symlink_refused_even_when_bytes_match(tmp_path):
    root = fixture(tmp_path)
    ledger = root/'docs/actions/ACTIONS_BASELINE_OBLIGATIONS.json'
    ledger.unlink()
    ledger.symlink_to(SOURCE/'docs/actions/ACTIONS_BASELINE_OBLIGATIONS.json')
    with pytest.raises(GitHubActionsError, match='symlink'):
        qualification_report(root)


def test_plan_duplicate_json_key_fails_closed(tmp_path):
    root = fixture(tmp_path)
    path = root / PLAN
    path.write_bytes(path.read_bytes().replace(b'"schema_version": 1,', b'"schema_version": 1, "schema_version": 1,', 1))
    with pytest.raises(GitHubActionsError): qualification_report(root)


def test_cli_schema_commands_are_canonical_and_offline(tmp_path, monkeypatch):
    root = fixture(tmp_path)
    for sub in ('report', 'check'):
        command = f'actions qualification {sub} --json'
        args = build_parser().parse_args(command.split())
        assert args.actions_command == 'qualification' and args.qualification_command == sub
        registry = build_core_registry().resolve_command(command.split()[:3])['command']
        assert registry.stable_id == 'cli.command.actions.qualification.' + sub
        monkeypatch.setattr('devtool.github_actions.resolve_token',lambda *a,**kw:
                  (_ for _ in ()).throw(AssertionError('G8 must not request GitHub credentials')))
        got=actions_cli(args,root)
        assert got['mode']=='read-only' and got['campaign_closed'] is False
    args = build_parser().parse_args('actions qualification check --require-complete --json'.split())
    assert args.require_complete is True


def test_g8_does_not_mutate_original_ledger_or_issue_side_effects(tmp_path):
    root = fixture(tmp_path)
    before = {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
              for p in root.rglob('*') if p.is_file()}
    qualification_report(root, strict_source=True)
    after = {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
             for p in root.rglob('*') if p.is_file()}
    assert before == after


def test_exact_historical_gaps_not_promoted(tmp_path):
    got = qualification_report(fixture(tmp_path))
    hist = got['historical_dependencies']
    assert hist['V105_pre_actions']['V103_report']['failed'] == 130
    assert hist['V105_pre_actions']['V104_Termux']['interrupted'] is True
    assert hist['V105_pre_actions']['G3_historical_pin_mismatches']['bad_pins'] == 352
    assert hist['ACTIONS10']['qualified_release'] is False
    assert hist['G6']['status'] == 'user_reported_local_evidence_ok'


def test_g4_live_unsigned_amendment_is_traced_and_original_matrix_unchanged(tmp_path):
    root = fixture(tmp_path)
    got = qualification_report(root, strict_source=True)
    assert got['source_contract_consistent'] is True
    assert got['qualified_release'] is False
    amendments = [row for row in got['verified_source_amendments'] if row['wave'] == 'G4']
    assert len(amendments) == 2
    assert {row['path'] for row in amendments} == {
        'devtool/actions_aab.py', 'tests/test_actions_g04_android_aab_candidate.py'}
    assert all(row['original_sha256'] != row['amended_sha256'] for row in amendments)
    assert any(row.get('status') == 'SOURCE_PINS_AMENDED_VERIFIED' for row in got['source_contracts'])
    for path in ('docs/actions/ACTIONS_G04_JARSIGNER_UNSIGNED_VERDICT_HOTFIX.md',
                 'tests/test_actions_g04_unsigned_jarsigner_truth.py'):
        temporary = fixture(tmp_path / path.rsplit('/', 1)[-1])
        target = temporary / path
        target.write_bytes(target.read_bytes() + b'\ntamper')
        with pytest.raises(GitHubActionsError, match='source contract'):
            qualification_report(temporary, strict_source=True)
