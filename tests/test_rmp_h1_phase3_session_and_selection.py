"""H1 Phase 3: durable-load and independently selected inventory boundary."""
from copy import deepcopy
import pytest
from devtool.models import CommandSpec
from devtool.validation_sessions import ValidationSessionError, _session_from_dict, serialize_spec
from devtool.test_runtime_optimization import aggregate_shards, ShardOutcome, run_attachment_manifest


def _plan():
    return serialize_spec(CommandSpec('safe', ['true', '--token', '@env:TOKEN'], metadata={'token':'@vault:opaque'}))


def test_valid_and_absent_persisted_plans_remain_loadable():
    assert len(_session_from_dict({'plan_specs':[_plan()]}).plan_specs)==1
    assert _session_from_dict({}).plan_specs == []


@pytest.mark.parametrize('bad',[None, {}, 'oops', 1, [42], [{'argv':['true','--password=plain']}],
   [{'argv':['true'],'metadata':{'password':'plain'}}], [{'argv':['true'],'destructive':'false'}]])
def test_malformed_saved_plan_is_rejected_at_session_load(bad):
    with pytest.raises(ValidationSessionError):
        _session_from_dict({'plan_specs':bad})


def _aggregate():
    return aggregate_shards(['original-a','original-b'],[
      ShardOutcome('one',('original-a',),passed=1),
      ShardOutcome('two',('original-b',),skipped=1),
    ])


def test_valid_authorized_case_selection_passes():
    agg=_aggregate()
    assert run_attachment_manifest('run','selection',agg,authorized_case_ids=['original-b','original-a'])['complete']


def test_mutable_aggregate_cannot_substitute_authorized_case_set():
    agg=_aggregate()
    agg['expected_case_ids']=['stolen-a','stolen-b']
    agg['attachments'][0]['case_ids']=['stolen-a']
    agg['attachments'][1]['case_ids']=['stolen-b']
    assert run_attachment_manifest('run','selection',agg)['complete']  # demonstrates the previously unbound projection
    assert not run_attachment_manifest('run','selection',agg,authorized_case_ids=['original-a','original-b'])['complete']


@pytest.mark.parametrize('bad',[['original-a','original-a'],['original-a',False],['original-a'], 'original-a,original-b'])
def test_bad_external_selections_refuse_completion(bad):
    assert not run_attachment_manifest('run','selection',_aggregate(),authorized_case_ids=bad)['complete']
