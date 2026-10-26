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
                  # Superseded runnable historical G8 amendment removed.
                  })
    for name in paths:
        dest = root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / name, dest)
    return root


def test_current_tree_does_not_qualify_legacy_g8_pins(tmp_path):
    root = fixture(tmp_path)
    got = qualification_report(root)
    assert got['source_contract_consistent'] is False
    assert got['qualified_release'] is False
    assert got['campaign_closed'] is False
    with pytest.raises(GitHubActionsError, match='source contract'):
        qualification_report(root, strict_source=True)


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
        if sub == 'check':
            with pytest.raises(GitHubActionsError, match='source contract'):
                actions_cli(args, root)
        else:
            got = actions_cli(args, root)
            assert got['campaign_closed'] is False
            assert got['qualified_release'] is False
            assert got['mode'] == 'read-only'
    args = build_parser().parse_args('actions qualification check --require-complete --json'.split())
    assert args.require_complete is True


def test_current_historical_qualification_report_remains_read_only(tmp_path):
    root = fixture(tmp_path)
    before = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in root.rglob('*') if p.is_file()}
    got = qualification_report(root)
    assert got['qualified_release'] is False
    after = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
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
