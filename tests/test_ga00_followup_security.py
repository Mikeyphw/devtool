"""GA-00 P09/P15/P16: hostile-path regression cases. No real GitHub mutations."""
from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from devtool.actions_setup import release_setup, SECRETS
from devtool.ga00_environment import SIGNING_ENVIRONMENT, evaluate_environment
from devtool.ga00_governance_approval import (GovernanceApprovalRefusal, preview,
                                             apply_approved)
from devtool.github_actions import GitHubActionsError
from tests.test_actions_09_release_setup import canonical_signer, Client, SHA


def protected_environment():
    return {'name': SIGNING_ENVIRONMENT, 'can_admins_bypass': False,
            'protection_rules': [{'type':'required_reviewers', 'prevent_self_review':True,
                                 'reviewers':[{'type':'User','reviewer':{'id':10}}]}],
            'deployment_branch_policy':{'protected_branches':True,'custom_branch_policies':False}}


def test_environment_requires_all_independent_protections():
    assert evaluate_environment(protected_environment())['verified']
    for field, value in [('name','wrong'), ('can_admins_bypass',True),
                         ('protection_rules', []),('deployment_branch_policy', None)]:
        obj=protected_environment();obj[field]=value
        assert not evaluate_environment(obj)['verified'],field
    for key,value in [('prevent_self_review', False), ('reviewers', [])]:
        obj=protected_environment();obj['protection_rules'][0][key]=value
        assert not evaluate_environment(obj)['verified'],key
    obj=protected_environment();del obj['can_admins_bypass']
    assert not evaluate_environment(obj)['verified']
    obj=protected_environment();obj['deployment_branch_policy']['custom_branch_policies']=True
    assert not evaluate_environment(obj)['verified']


def test_repository_secret_leakage_refuses_before_signer_or_mutation(canonical_signer,monkeypatch,tmp_path):
    root,_,_=canonical_signer
    c=Client();c.protected=True
    monkeypatch.setattr('devtool.actions_setup._source',lambda _:SHA)
    monkeypatch.setattr('devtool.actions_setup._secrets',lambda _:set(SECRETS))
    monkeypatch.setattr('devtool.actions_setup._environment_secrets',lambda _:set(SECRETS))
    monkeypatch.setattr('devtool.actions_setup._signer_material',lambda *a:(_ for _ in ()).throw(AssertionError('signer read')))
    monkeypatch.setattr('devtool.actions_setup._gh',lambda *a,**k:(_ for _ in ()).throw(AssertionError('mutation')))
    with pytest.raises(GitHubActionsError,match='REPOSITORY_SIGNING_SECRET_LEAKAGE'):
        release_setup(c,'Owner/repo',root,policy_file=str(tmp_path/'policy'),execute=True)


def test_missing_environment_refuses_before_signer_or_mutation(canonical_signer,monkeypatch,tmp_path):
    root,_,_=canonical_signer
    class NoEnv(Client):
        protected=True
        def _get(self,path):
            if '/environments/' in path:return {'name':SIGNING_ENVIRONMENT,'protection_rules':[]}
            return super()._get(path)
    monkeypatch.setattr('devtool.actions_setup._source',lambda _:SHA)
    monkeypatch.setattr('devtool.actions_setup._secrets',lambda _:set())
    monkeypatch.setattr('devtool.actions_setup._environment_secrets',lambda _:set())
    monkeypatch.setattr('devtool.actions_setup._signer_material',lambda *a:(_ for _ in ()).throw(AssertionError('signer read')))
    monkeypatch.setattr('devtool.actions_setup._gh',lambda *a,**k:(_ for _ in ()).throw(AssertionError('mutation')))
    with pytest.raises(GitHubActionsError,match='SIGNING_ENVIRONMENT_NOT_PROTECTED'):
        release_setup(NoEnv(),'Owner/repo',root,policy_file=str(tmp_path/'policy'),execute=True)


def mock_owner_get(path):
    if path == 'user':return {'login':'owner'}
    if path == 'repos/Owner/repo':return {'full_name':'Owner/repo','permissions':{'admin':True}}
    if '/commits/main' in path:return {'sha':SHA}
    if '/branches/main' in path:return {'protected':False}
    if '/rulesets?' in path:return [{'id':2,'updated_at':'2026-11-22T03:00:00Z'}]
    raise AssertionError('unexpected GET: '+path)


def test_owner_preview_is_pure_read_and_digest_bound(monkeypatch):
    commands=[]
    def gh(args,**kw):
        assert args[:3]==['api','--method','GET']
        commands.append(args)
        return json.dumps(mock_owner_get(args[3]))
    monkeypatch.setattr('devtool.ga00_governance_approval._gh',gh)
    a=preview('Owner/repo')
    b=preview('Owner/repo')
    assert a==b and a['effect']=='NO_MUTATION_PREVIEW'
    assert a['actor_admin'] and len(a['approval_digest'])==64
    assert len(commands)==10 and all(x[2]=='GET' for x in commands)


def test_owner_approved_write_requires_exact_digest_and_is_not_repeatable(tmp_path, monkeypatch):
    events=[];protected=False
    def gh(args,**kw):
        nonlocal protected
        events.append(args[2])
        if args[2]=='POST':
            assert kw.get('input_text') and 'non_fast_forward' in kw['input_text']
            protected=True
            return json.dumps({'id':33})
        path=args[3]
        if path.endswith('/branches/main') and protected:
            return json.dumps({'protected':True})
        return json.dumps(mock_owner_get(path))
    monkeypatch.setattr('devtool.ga00_governance_approval._gh',gh)
    plan=preview('Owner/repo')
    with pytest.raises(GovernanceApprovalRefusal,match='OWNER_DIGEST'):
        apply_approved('Owner/repo','main',yes=False,owner_login='owner',
                       approval_digest=plan['approval_digest'],state_dir=tmp_path/'state')
    with pytest.raises(GovernanceApprovalRefusal,match='LIVE_PLAN_DRIFT'):
        apply_approved('Owner/repo','main',yes=True,owner_login='owner',
                       approval_digest='a'*64,state_dir=tmp_path/'state')
    result=apply_approved('Owner/repo','main',yes=True,owner_login='owner',
                          approval_digest=plan['approval_digest'],state_dir=tmp_path/'state')
    assert result['state']=='PROTECTION_OBSERVED' and events.count('POST')==1
    record=list((tmp_path/'state').glob('*.jsonl'))[0]
    assert record.stat().st_mode & 0o777==0o600
    assert [json.loads(x)['state'] for x in record.read_text().splitlines()]==[
        'INTENT_BEFORE_POST','ACK_POST','PROTECTION_OBSERVED']
    with pytest.raises(GovernanceApprovalRefusal):
        apply_approved('Owner/repo','main',yes=True,owner_login='owner',
                       approval_digest=plan['approval_digest'],state_dir=tmp_path/'state')
    assert events.count('POST')==1


def test_owner_network_failure_retains_intent_and_never_replays(tmp_path, monkeypatch):
    post=[]
    def gh(args,**kw):
        if args[2]=='POST':
            post.append(1)
            raise OSError('remote outcome ambiguous')
        return json.dumps(mock_owner_get(args[3]))
    monkeypatch.setattr('devtool.ga00_governance_approval._gh',gh)
    plan=preview('Owner/repo');state=tmp_path/'state'
    for i in range(2):
        with pytest.raises(GovernanceApprovalRefusal):
            apply_approved('Owner/repo','main',yes=True,owner_login='owner',
                           approval_digest=plan['approval_digest'],state_dir=state)
    assert len(post)==1
    assert [json.loads(x)['state'] for x in list(state.glob('*.jsonl'))[0].read_text().splitlines()]==['INTENT_BEFORE_POST']


def test_owner_non_admin_denied_without_remote_post(tmp_path, monkeypatch):
    def gh(args,**kw):
        if args[2]=='POST':raise AssertionError('unexpected POST')
        p=mock_owner_get(args[3])
        if args[3]=='repos/Owner/repo':p['permissions']['admin']=False
        return json.dumps(p)
    monkeypatch.setattr('devtool.ga00_governance_approval._gh',gh)
    plan=preview('Owner/repo')
    with pytest.raises(GovernanceApprovalRefusal,match='OWNER_AUTHORITY'):
        apply_approved('Owner/repo','main',yes=True,owner_login='owner',
                       approval_digest=plan['approval_digest'],state_dir=tmp_path/'state')


def test_actions09_never_creates_rulesets():
    from devtool.actions_setup import _ruleset
    with pytest.raises(GitHubActionsError,match='OWNER_APPROVAL_REQUIRED'):
        _ruleset('Owner/repo','main')
