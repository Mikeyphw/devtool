"""R01-R03 bounded local closure: never infer external/device/release authority."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLOSURE = ROOT / 'docs/actions/DEVTOOL_R01_R03_SCOPED_CLOSURE_20261020.json'
INDEX = ROOT / 'docs/DEVTOOL_IMPROVEMENT_INDEX.json'
TREES = {
    'R01': 'ea93004437c1f5d7a1c8353aef834b1844ddcc38',
    'R02': 'e6165d2226173d163fae11bdc34eb1ba821ab7f3',
    'R03': '8f61a2857fddb3435ce20de5eb8a65b46f236ee6',
}
UNQUALIFIED = (
    'ENV-A06', 'ANDROID-A16', 'DIST-A01', 'DIST-A12', 'DIST-A13', 'TEST-A11'
)
CODE_HASHES = {
    'devtool/actions_android.py': '952c8bd6c4d7994bc99ba81941462cd0f7c0db2524b031bf01872ebb475bdc61',
    'devtool/actions_release.py': 'c1348ef8ad98b0e6a89ed2a9928ec73d57b8c2cd306303d346f4e8b2c1579c6f',
    'devtool/android_package_presence.py': 'f7e8c8ce8894f062f793d1745ba30db6e29f42a6a3daf7897e8718b909cfcd3c',
    'devtool/github_actions.py': '7a7cc594931ac7d050832244df8ac489680fe3c3406ea34ce3fed50a07471d53',
}


def load():
    return json.loads(CLOSURE.read_text()), json.loads(INDEX.read_text())


def validate(cl, ix):
    assert cl['kind'] == 'devtool.source-bound-scoped-milestone-closure'
    assert cl['schema_version'] == 1
    assert [cl['basis'][f'r0{i}_tree'] for i in (1, 2, 3)] == list(TREES.values())
    assert {k: cl['milestones'][k]['accepted_tree'] for k in TREES} == TREES
    assert cl['milestones']['R01']['scoped_qualification'] == 'CLOSED_LOCAL_TERMUX'
    assert cl['milestones']['R02']['scoped_qualification'] == 'CLOSED_LOCAL_TERMUX'
    assert cl['milestones']['R03']['scoped_qualification'] == 'SOURCE_VERIFIED_NATIVE_APPLY_REPORTED'
    assert cl['milestones']['R01']['observed_evidence']['pytest_passed'] == 56
    assert cl['milestones']['R01']['observed_evidence']['source_paths_verified'] == 11
    assert cl['milestones']['R01']['observed_evidence']['prior_interrupted_apply_rollback_verified'] is True
    r02 = cl['milestones']['R02']['observed_evidence']
    assert r02['tests_passed'] == 34 and r02['tests_skipped'] == 0
    assert r02['devtool_commit'] == '6b1823e1e0b282a9aefe9cb1b98578478776ff72'
    assert r02['remote_dispatch'] is False and r02['source_attested'] is False
    r03 = cl['milestones']['R03']['observed_evidence']
    assert r03['focused_regressions_passed'] == 101
    assert r03['native_apply_user_reported'] is True
    assert r03['native_log_independently_reviewed'] is False
    for v in cl['milestones'].values():
        assert not v['hosted_qualified']
        assert not v['device_qualified']
        assert not v['release_qualified']
    for gate in (*UNQUALIFIED, 'R02_SECOND_CONSUMER_HOSTED_GO', 'R03_NATIVE_APPLY_RECEIPT',
                 'R03_REAL_INSTRUMENTATION_APK', 'R03_TRUSTED_ANDROID_BROKER_IDENTITY',
                 'FORMAL_RELEASE'):
        assert cl['independent_gates'][gate] in ('OPEN', 'BLOCKED', 'BLOCKED_EXTERNAL')
    assert ix['formal_release_qualified'] is False
    issues = {r['id']: r for r in ix['issues']}
    assert len(issues) == len(ix['issues']) == 44
    assert all(f'DT-{i:03}' in issues for i in range(1, 24))
    assert all(issues[g]['qualification_status'] == 'BLOCKED_EXTERNAL' for g in UNQUALIFIED)
    assert issues['RMP-H1/H2']['qualification_status'] == 'EXTERNAL_EVIDENCE_PENDING'
    assert issues['ACTIONS-A04-LOCAL-RUN-V3']['qualification_status'] == 'LOCAL_TERMUX_SCOPED_CLOSED_R01'
    assert issues['ACTIONS-A09']['qualification_status'] == 'LOCAL_TERMUX_SCOPED_CLOSED_R02_HOSTED_PENDING'
    for key in ('SKY-ACTIONS-COLLECT-008', 'SKY-ACTIONS-APK-META-012',
                'SKY-ACTIONS-APK-META-013', 'SKY-B1C-PM-014'):
        assert issues[key]['qualification_status'] == 'SOURCE_DISPOSABLE_VERIFIED_R03_NATIVE_RECEIPT_PENDING'
    assert ix['scoped_milestone_closure']['campaign'] == 'OPEN'


def test_bounded_closure_evidence_and_index():
    validate(*load())


def test_source_implementation_does_not_change_android_verifier():
    for rel, expected in CODE_HASHES.items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == expected, rel


def test_historical_ledger_is_append_only_and_releaseless():
    ledger = (ROOT / 'docs/DEVTOOL_IMPROVEMENTS.md').read_text()
    for heading in ('## DEVTOOL-R01', '## DEVTOOL-R02', '## DEVTOOL-R03',
                    '## DEVTOOL-R01–R03'):
        assert heading in ledger
    assert 'FORMAL_RELEASE' in CLOSURE.read_text()
    assert 'native overlay/validator log has not been reviewed' in ledger


@pytest.mark.parametrize('case', [
    'fake_r03_native_pass', 'fake_r03_hosted', 'fake_r02_remote_dispatch',
    'fake_release', 'retire_rmp', 'drop_gate', 'rewrite_r01_test_count',
    'erase_issue', 'pretend_apk_live'])
def test_adversarial_closure_promotion_refused(case):
    cl, ix = copy.deepcopy(load())
    if case == 'fake_r03_native_pass':
        cl['milestones']['R03']['scoped_qualification'] = 'CLOSED_LOCAL_TERMUX'
    elif case == 'fake_r03_hosted':
        cl['milestones']['R03']['hosted_qualified'] = True
    elif case == 'fake_r02_remote_dispatch':
        cl['milestones']['R02']['observed_evidence']['remote_dispatch'] = True
    elif case == 'fake_release':
        ix['formal_release_qualified'] = True
    elif case == 'retire_rmp':
        next(r for r in ix['issues'] if r['id'] == 'RMP-H1/H2')['qualification_status'] = 'CLOSED'
    elif case == 'drop_gate':
        cl['independent_gates']['R03_REAL_INSTRUMENTATION_APK'] = 'CLOSED'
    elif case == 'rewrite_r01_test_count':
        cl['milestones']['R01']['observed_evidence']['pytest_passed'] = 999
    elif case == 'erase_issue':
        ix['issues'].pop()
    elif case == 'pretend_apk_live':
        next(r for r in ix['issues'] if r['id'] == 'SKY-ACTIONS-APK-META-013')['qualification_status'] = 'DEVICE_QUALIFIED'
    with pytest.raises((AssertionError, KeyError)):
        validate(cl, ix)


def test_native_closure_failure_is_truthfully_preserved():
    cl, ix = load()
    failures = cl['validation_attempts']
    assert len(failures) == 1
    attempt = failures[0]
    assert attempt['result'] == 'FAILED_EARLY'
    assert attempt['terminal_transaction_status'] == 'UNKNOWN_FROM_PARTIAL_LOG'
    assert attempt['observed_ms'] > attempt['budget_ms'] == 5000
    assert cl['closure_overlay_validation_status'] == 'NATIVE_REPLACEMENT_PENDING'
    item = next(row for row in ix['issues'] if row['id'] == 'CORE-X04-NATIVE-PERF-001')
    assert item['qualification_status'] == 'SOURCE_TESTED_NATIVE_PENDING'
    assert not cl['milestones']['R03']['hosted_qualified']
    assert not cl['milestones']['R03']['device_qualified']
