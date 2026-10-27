"""Adversarial independent H1 N02/C04 consumer-path regression cases."""
from __future__ import annotations

from copy import deepcopy
import pytest

from devtool.models import CommandSpec
from devtool.validation_sessions import (
    ValidationSessionError, deserialize_spec, serialize_spec, stored_specs,
)
from devtool.test_runtime_optimization import ShardOutcome, aggregate_shards, run_attachment_manifest


def _safe_row():
    return serialize_spec(CommandSpec("safe", ["true", "--token", "@env:SESSION_TOKEN"], metadata={"session_token":"@vault:job/1", "label":"test"}))


def test_persisted_safe_reference_remains_readable():
    expected = _safe_row()
    restored = deserialize_spec(expected)
    assert restored.argv == expected["argv"]
    assert restored.metadata["session_token"] == "@vault:job/1"


@pytest.mark.parametrize('tamper', [
    lambda row: row['metadata'].update({'credentials': {'password':'inline-credential'}}),
    lambda row: row['metadata'].update({'authorization':'Bearer exposed'}),
    lambda row: row['env'].update({'SECRET_TOKEN':'plaintext-not-reference'}),
    lambda row: row['argv'].extend(['--api-key=inline-credential']),
    lambda row: row['argv'].extend(['--password','inline-credential']),
    lambda row: row.update({'destructive': 'false'}),
    lambda row: row.update({'argv': ['true', 42]}),
    lambda row: row.update({'metadata': ['not-a-map']}),
])
def test_persisted_plan_tampering_is_rejected_on_read(tamper):
    row = _safe_row()
    tamper(row)
    with pytest.raises(ValidationSessionError):
        deserialize_spec(row)


def test_stored_plan_refuses_malformed_item_instead_of_dropping_it():
    class Journal:
        plan_specs=[_safe_row(), {'argv':['true'],'metadata':{'api_key':'plaintext'}},42]
    with pytest.raises(ValidationSessionError):
        stored_specs(Journal())


def _valid_aggregate():
    return aggregate_shards(('case-a','case-b'),(
        ShardOutcome('shard-a',('case-a',),passed=1),
        ShardOutcome('shard-b',('case-b',),skipped=1),
    ))


def test_manifest_retains_canonical_selection_membership():
    agg=_valid_aggregate()
    assert agg['expected_case_ids']==['case-a','case-b']
    assert run_attachment_manifest('run','selection',agg)['complete']


@pytest.mark.parametrize('tamper', [
    lambda agg: agg['attachments'][0].update({'case_ids':['substituted-case']}),
    lambda agg: agg.pop('expected_case_ids'),
    lambda agg: agg.update({'expected_case_ids':['case-a','substituted-case']}),
    lambda agg: agg.update({'expected_case_ids':['case-a','case-a']}),
    lambda agg: agg.update({'expected_case_ids':['case-a',False]}),
    lambda agg: agg.update({'expected_case_ids':'case-a,case-b'}),
])
def test_manifest_rejects_borrowed_case_membership_even_if_counts_pass(tamper):
    aggregate=deepcopy(_valid_aggregate())
    tamper(aggregate)
    assert not run_attachment_manifest('run','selection',aggregate)['complete']
