"""R14 tests at the actual Android release consumer's receipt admission seam."""
import pytest
from devtool.android.release import _gradle_receipt_binding_error
from devtool.gradle_planning import _hash_payload
from devtool.models import CommandSpec, OperationResult

PLAN = {"plan_digest": "plan-012", "context": {"context_digest": "ctx-012"},
        "policy": {"policy_digest": "policy-012"},
        "request": {"selection_identity": "selected-012"}}

def pair(*, plan=True, receipt=True, changes=None, output=None):
    spec = CommandSpec('assembleRelease', ['gradle', 'assembleRelease'], metadata={'gradle_execution_plan': PLAN} if plan else {})
    payload = {"plan_digest": PLAN['plan_digest'], "context_digest": 'ctx-012',
               "policy_digest": 'policy-012', "selection_identity": 'selected-012',
               "success": True, "process_executed": True}
    payload.update(changes or {})
    payload['receipt_id'] = _hash_payload(payload)
    meta = {'gradle_execution_receipt': payload, 'gradle_receipt_id': payload['receipt_id']} if receipt else {}
    result = OperationResult('assembleRelease', ['gradle', 'assembleRelease'], 0, 0.01, 'ok', [], metadata=meta)
    if output is not None: result.name = output
    return spec, result

def test_canonical_receipt_matches_planned_gradle_metadata():
    assert _gradle_receipt_binding_error(*pair()) == ''

def test_non_gradle_command_does_not_require_receipt():
    assert _gradle_receipt_binding_error(*pair(plan=False, receipt=False)) == ''

@pytest.mark.parametrize('change', [
    {'plan_digest':'other'}, {'context_digest':'other'},
    {'policy_digest':'other'}, {'selection_identity':'other'}, {'success':False},
])
def test_foreign_or_failed_receipt_rejected(change):
    assert _gradle_receipt_binding_error(*pair(changes=change))

def test_missing_planned_receipt_rejected():
    assert 'no execution receipt' in _gradle_receipt_binding_error(*pair(receipt=False))

def test_unplanned_receipt_rejected():
    assert 'unplanned' in _gradle_receipt_binding_error(*pair(plan=False))

def test_modified_receipt_without_rehash_rejected():
    spec,result=pair(); result.metadata['gradle_execution_receipt']['observed_tasks']=['forged']
    assert 'content digest' in _gradle_receipt_binding_error(spec,result)

def test_result_metadata_receipt_identity_mismatch_rejected():
    spec,result=pair(); result.metadata['gradle_receipt_id']='f'*64
    assert 'different receipt' in _gradle_receipt_binding_error(spec,result)

def test_result_named_for_wrong_command_rejected():
    assert 'identity' in _gradle_receipt_binding_error(*pair(output='wrong'))


def test_real_canonical_receipt_handles_unrounded_duration():
    from devtool.gradle_planning import GradleExecutionReceipt
    plan = {
        'plan_digest':'canon', 'request': {'selection_identity':'selected-012'},
        'context':{'context_digest':'ctx-012'}, 'policy':{'policy_digest':'policy-012'},
        'requested_roots':[':app:assembleRelease'], 'optimized_roots':[':app:assembleRelease'],
        'arguments':{},
    }
    receipt = GradleExecutionReceipt.from_plan_metadata(
        plan, progress={'observed_tasks':[':app:assembleRelease'],
                        'task_outcomes':{':app:assembleRelease':'SUCCESS'}},
        success=True, process_executed=True, duration_seconds=0.123456789,
    )
    spec = CommandSpec('assembleRelease',['gradle','assembleRelease'],metadata={'gradle_execution_plan':plan})
    result = OperationResult('assembleRelease',['gradle','assembleRelease'],0,0.01,'ok',[],metadata={
        'gradle_execution_receipt':receipt.as_dict(), 'gradle_receipt_id':receipt.receipt_id})
    assert _gradle_receipt_binding_error(spec,result) == ''
    # A mutation of signed-receipt-shaped content fails regardless of duration normalization.
    result.metadata['gradle_execution_receipt']['observed_tasks'].append(':app:forged')
    assert 'content digest' in _gradle_receipt_binding_error(spec,result)


def test_receipt_must_attest_process_execution():
    assert 'successful' in _gradle_receipt_binding_error(*pair(changes={'process_executed':False}))

@pytest.mark.parametrize('bad_duration', ['bad', None, float('nan'), float('inf')])
def test_malformed_duration_is_rejected(bad_duration):
    assert 'duration' in _gradle_receipt_binding_error(*pair(changes={'duration_seconds':bad_duration}))
