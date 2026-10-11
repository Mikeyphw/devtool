from types import SimpleNamespace as S
import pytest
from devtool.federation_recovery import cancellation_recovery_evidence

def report(status='cancelled',children=()):
    return S(status=status,parent_run_id='parent-a',children=children)
def child(exit_code=0,status='succeeded',run_id='child-a',step_id='build'):
    return S(exit_code=exit_code,status=status,run_id=run_id,step_id=step_id,
             repository='child',git_lineage={},operational_ownership={})

def test_valid_success_is_only_reusable_child():
    result=cancellation_recovery_evidence(report(children=[child()]))
    assert result['reusable_steps']==['build'] and not result['invalidated_steps']

@pytest.mark.parametrize('exit_code',[None,True,False,'0','nonsense',{},0.0,5])
def test_invalid_or_failed_exit_is_never_reused(exit_code):
    result=cancellation_recovery_evidence(report(children=[child(exit_code)]))
    assert result['reusable_steps']==[]
    assert len(result['invalidated_steps'])==1
    assert result['invalidated_steps'][0]['reason'] in ('invalid-or-missing-exit-code','nonzero-success-exit')

@pytest.mark.parametrize('run,step',[('', 'build'),('child-a',''),('','')])
def test_missing_identity_prevents_reuse(run,step):
    result=cancellation_recovery_evidence(report(children=[child(run_id=run,step_id=step)]))
    assert result['reusable_steps']==[]
    assert result['invalidated_steps'][0]['reason']=='missing-child-identity'

def test_cancellation_keeps_cancelled_run_id():
    result=cancellation_recovery_evidence(report(children=[child(130,'cancelled')]))
    assert result['cancelled_child_run_ids']==['child-a']
    assert result['reusable_steps']==[]
