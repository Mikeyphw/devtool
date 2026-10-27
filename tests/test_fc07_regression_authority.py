"""FC-07: verify inventory/status claims are neither stale nor self-certifying."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
import importlib.util
import pytest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('fc07_inventory',ROOT/'tools/fc07_regression_inventory.py')
assert SPEC and SPEC.loader
inventory=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)


def test_all_tracked_tests_in_exactly_one_stable_shard():
    m=inventory.manifest(ROOT)
    all_files=[file for shard in m['shards'] for file in shard['files']]
    assert len(all_files)==len(set(all_files))==m['tracked_test_modules']
    assert m['tracked_test_modules']>=500
    assert 'tests/test_rm00_shared_contracts.py' in all_files
    assert 'tests/test_fc07_regression_authority.py' in all_files
    for name in ('test_fc01_safety_closure.py','test_fc02_transaction_closure.py',
                 'test_fc03_provider_selection.py','test_fc04_artifact_integrity.py',
                 'test_fc05_onboarding_closure.py','test_fc06_dotnet_shared_restore.py'):
        assert 'tests/'+name in all_files
    assert all(row['files'] for row in m['shards'])
    assert m==inventory.manifest(ROOT)


def test_shard_distribution_never_promotes_to_test_pass():
    m=inventory.manifest(ROOT)
    assert m['status']=='INVENTORY_ONLY_NO_TEST_EXECUTION'
    assert sum(len(x['files']) for x in m['shards'])==m['tracked_test_modules']
    assert all(inventory.shard_id(p)==row['shard'] for row in m['shards'] for p in row['files'])


def test_original_168_obligation_ledger_preserved_and_not_claimed_qualified():
    plan=json.loads((ROOT/'docs/RM_ORIGINAL_PROMISE_LEDGER_20261024.json').read_text())
    ids={item['id'] for item in plan['obligations']}
    assert len(ids)==168
    assert len([i for i in ids if i.startswith('RM19-')])==8
    assert all(p['release_qualified'] is False for p in plan['obligations'])
    assert plan['active_obligations']==160


def test_current_status_is_complete_and_never_promotes_local_tests_to_external():
    d=json.loads((ROOT/'docs/RM_CURRENT_STATUS.json').read_text())
    statuses=d['milestones']
    assert len(statuses)==21
    assert {r['milestone'] for r in statuses} == {f'RM{i:02d}' for i in range(21)}
    assert d['historical_reports_immutable'] is True
    for row in statuses:
        assert row['release_qualified'] is False
        assert row['historical_reference']
        if row['milestone']=='RM19':assert row['engineering_status']=='CANCELLED_BY_USER'
        else:assert row['external_gate'].startswith('PENDING_OR_UNVERIFIED')
    assert (ROOT/'docs/RM_CURRENT_STATUS.md').is_file()


def test_junit_parser_counts_skips_errors_and_failures(tmp_path):
    f=tmp_path/'result.junit.xml'
    f.write_text('<testsuite><testcase name="good"/><testcase name="skip"><skipped/></testcase>'
                 '<testcase name="bad"><failure/></testcase><testcase name="error"><error/></testcase></testsuite>')
    assert inventory._junit_counts(f)=={'cases':4,'passed':1,'failed':1,'errors':1,'skipped':1}
    assert inventory._junit_counts(tmp_path/'absent') is None


def test_timeout_fails_closed_and_cleans_process(tmp_path):
    log=tmp_path/'timeout.log'
    status,code,elapsed=inventory._run([sys.executable,'-c','import time;time.sleep(10)'],tmp_path,log,1)
    assert status=='TIMEOUT' and code is None
    assert elapsed<5


def test_out_of_range_shard_and_timeout_rejected(tmp_path):
    with pytest.raises(ValueError,match='run requires'):
        inventory.execute(ROOT,'run',None,2,tmp_path/'receipt.json')
    with pytest.raises(ValueError,match='timeout'):
        inventory.execute(ROOT,'collect',None,0,tmp_path/'receipt.json')
