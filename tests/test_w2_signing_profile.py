"""Single-owner profile is explicit and keeps all independent controls."""
import copy
import pytest
from devtool.ga00_environment import evaluate_environment, inspect_environment
from devtool.actions_setup import release_setup, release_readiness, SECRETS
from devtool.actions_release import release_policy
from devtool.android.signing import AndroidSigningManager
from types import SimpleNamespace
from tests.test_actions_09_release_setup import canonical_signer, Client, SHA, SIGNER
from tests.test_ga00_followup_security import protected_environment

class OwnerClient(Client):
    protected=True
    owner_type='User'
    owner_id=12
    def repository(self,slug):return {**super().repository(slug),'owner':{'type':self.owner_type,'login':slug.split('/')[0],'id':self.owner_id}}
    def _get(self,path):
        val=super()._get(path)
        if '/environments/' in path:val['protection_rules'][0]['prevent_self_review']=False
        return val

def test_explicit_owner_only():
    obj=protected_environment();obj['protection_rules'][0]['prevent_self_review']=False
    assert evaluate_environment(obj,approval_profile='single-owner',owner_id=10)['verified']
    assert not evaluate_environment(obj)['verified']
    assert not evaluate_environment(obj,approval_profile='single-owner')['verified']
    assert not evaluate_environment(obj,approval_profile='single-owner',owner_id=11)['verified']

@pytest.mark.parametrize('fault',['bypass','branches','team','other_reviewer','self_review','missing_flag','duplicate'])
def test_independent_controls(fault):
    obj=protected_environment();rule=obj['protection_rules'][0];rule['prevent_self_review']=False
    if fault=='bypass':obj['can_admins_bypass']=True
    elif fault=='branches':obj['deployment_branch_policy']['protected_branches']=False
    elif fault=='team':rule['reviewers'][0]['type']='Team'
    elif fault=='other_reviewer':rule['reviewers'].append({'type':'User','reviewer':{'id':11}})
    elif fault=='self_review':rule['prevent_self_review']=True
    elif fault=='missing_flag':del rule['prevent_self_review']
    elif fault=='duplicate':obj['protection_rules'].append(copy.deepcopy(rule))
    assert not evaluate_environment(obj,approval_profile='single-owner',owner_id=10)['verified']

def test_owner_live_metadata():
    c=OwnerClient();assert inspect_environment(c,'Owner/repo',approval_profile='single-owner')['verified']
    assert not inspect_environment(c,'Owner/repo',approval_profile='single-owner',expected_owner_id=13)['verified']
    c.owner_type='Organization';assert not inspect_environment(c,'Owner/repo',approval_profile='single-owner')['verified']

def test_setup_readiness_roundtrip(canonical_signer,tmp_path,monkeypatch):
    root,_,_=canonical_signer;c=OwnerClient();names=set();calls=[];policy=tmp_path/'owner.json'
    monkeypatch.setattr('devtool.actions_setup._source',lambda _:SHA)
    monkeypatch.setattr('devtool.actions_setup._application',lambda *a:'org.example.release')
    monkeypatch.setattr('devtool.actions_setup._secrets',lambda _:set())
    monkeypatch.setattr('devtool.actions_setup._environment_secrets',lambda _:names.copy())
    monkeypatch.setattr(AndroidSigningManager,'inspect_profile',lambda *a,**k:SimpleNamespace(sha256=SIGNER,expired=False))
    def gh(argv,*,input_text=None,**kw):
        assert argv[:2]==['secret','set'] and argv[-2:]==['--env','devtool-android-signing'] and input_text
        names.add(argv[2]);calls.append(argv[2]);return ''
    monkeypatch.setattr('devtool.actions_setup._gh',gh)
    assert release_setup(c,'Owner/repo',root,policy_file=str(policy),approval_profile='single-owner',execute=True)['ready']
    doc=release_policy(root,str(policy));assert doc['schema']==2 and doc['owner_reviewer_id']==12 and doc['approval_profile']=='single-owner'
    assert set(calls)==set(SECRETS) and len(calls)==4
    assert release_readiness(c,'Owner/repo',root,ref='main',policy_file=str(policy))['ready']
    assert not release_readiness(c,'Owner/repo',root,ref='main',policy_file=str(policy),approval_profile='multi-reviewer')['ready']
    c.owner_id=13;assert not release_readiness(c,'Owner/repo',root,ref='main',policy_file=str(policy))['ready']
