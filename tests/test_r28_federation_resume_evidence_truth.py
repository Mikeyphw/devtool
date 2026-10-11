import copy
import pytest
from devtool.federation_plan import build_federated_plan
from devtool.federation_execution import FederatedRunReport
from devtool.federation_resume import (FederationResumeError, build_resume_plan,checkpoint_report,child_from_checkpoint)
from test_frx_y02_federation_resume_checkpoint import _fixture,_children,_state

@pytest.fixture
def initial(tmp_path,monkeypatch):
    _state(monkeypatch,tmp_path)
    config,_,_=_fixture(tmp_path)
    plan=build_federated_plan(config,'release')
    checkpoint=checkpoint_report(config,FederatedRunReport('release','parent-1','succeeded',0,_children(config,plan),plan))
    return config,checkpoint

def test_canonical_checkpoint_remains_reusable(initial):
    config,cp=initial
    assert [row.action for row in build_resume_plan(config,cp,mode='resume').decisions]==['reused','reused']
    assert child_from_checkpoint(cp['children'][0]).exit_code==0

@pytest.mark.parametrize('field,value',[('exit_code',None),('exit_code',9),('exit_code',True),('exit_code','0'),
    ('result_identity','0'*64),('evidence_digest','0'*64),('artifact_identity','0'*64),
    ('run_id','child-forged'),('status','blocked')])
def test_claimed_success_with_missing_or_tampered_data_not_reused(initial,field,value):
    config,cp=initial
    cp['children'][0][field]=value
    assert build_resume_plan(config,cp,mode='resume').decisions[0].action!='reused'
    with pytest.raises(FederationResumeError):child_from_checkpoint(cp['children'][0])

@pytest.mark.parametrize('field,value',[('git_lineage',{'result_ref':'changed'}),
    ('run_lineage',{'parent_run_id':'other'}),('operational_ownership',{'references':['forged']})])
def test_digest_binds_recorded_evidence(initial,field,value):
    config,cp=initial;cp['children'][0][field]=value
    assert build_resume_plan(config,cp,mode='resume').decisions[0].action!='reused'

def test_duplicate_child_identity_cannot_override_previous_result(initial):
    config,cp=initial;cp['children'].append(copy.deepcopy(cp['children'][0]))
    with pytest.raises(FederationResumeError,match='duplicate'):
        build_resume_plan(config,cp,mode='resume')
