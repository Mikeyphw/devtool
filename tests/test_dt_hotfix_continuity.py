"""DT21/23: generic two-attempt native push evidence and safe controller routing."""
from __future__ import annotations
import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest
from devtool import delivery_session as s, delivery_github as d
from devtool.delivery_receipt import build_receipt


def git(root, *args):
    p=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,timeout=10,check=True)
    return p.stdout.strip()

@pytest.fixture
def existing(tmp_path):
    repo=tmp_path/'repo';repo.mkdir();git(repo,'init','-b','main')
    git(repo,'config','user.name','test');git(repo,'config','user.email','test@example.invalid')
    (repo/'README.md').write_text('base\n');git(repo,'add','.');git(repo,'commit','-m','base')
    old=git(repo,'rev-parse','HEAD');git(repo,'switch','-c','feature/hf')
    (repo/'.github/workflows').mkdir(parents=True)
    (repo/'.github/workflows/ci.yml').write_text('name: CI\n')
    git(repo,'add','.');git(repo,'commit','-m','source')
    head=git(repo,'rev-parse','HEAD')
    git(repo,'remote','add','origin','https://github.com/Mikeyphw/devtool.git')
    state=tmp_path/'state';rec=s.start(repo,state_root=state)
    key=f"{rec['repository']}:refs/heads/{rec['head']}:{rec['head_sha']}"
    original=s.record_intent(rec['id'],'push',key,approved=True,state_root=state)
    return repo,state,rec,key,old,head,original['intent_id']


def test_completed_reconciliation_allows_followup_pr_intent(existing):
    root,state,record,key,old,head,prior=existing
    seq=iter([old,old,head]);writes=[]
    out=d.push(record['id'],yes=True,state_root=state,remote_probe=lambda *_:next(seq),
               git_push=lambda *_:writes.append('push') or 0,recovery_intent_id=prior,
               prior_process_finished=True)
    assert out['state']=='PUSH_VERIFIED' and writes==['push']
    status=s.status(record['id'],state_root=state)
    assert status['resumable'] is True
    assert [x['state'] for x in status['intents']]==['reconciled_no_remote_change','confirmed']
    nxt=s.record_intent(record['id'],'pr_create','PR:1',approved=True,state_root=state)
    assert nxt['new'] is True
    assert s.status(record['id'],state_root=state)['resumable'] is False
    receipt=s.report(record['id'],state_root=state)['receipt']
    assert 'pr_create' in receipt['unresolved_intents']


def test_unknown_attempt_does_not_replay_or_claim_terminal(existing):
    root,state,rec,key,old,head,prior=existing
    seq=iter([old,old,old]);writes=[]
    with pytest.raises(s.DeliverySessionError,match='Push outcome uncertain'):
        d.push(rec['id'],yes=True,state_root=state,remote_probe=lambda *_:next(seq),
               git_push=lambda *_:writes.append('one') or SimpleNamespace(returncode=1,stderr='non-fast-forward',stdout=''),
               recovery_intent_id=prior,prior_process_finished=True)
    assert writes==['one']
    status=s.status(rec['id'],state_root=state)
    assert status['resumable'] is False
    assert [x['state'] for x in status['intents']]==['reconciled_no_remote_change','unresolved']
    with pytest.raises(s.DeliverySessionError,match='Original unresolved'):
        d.push(rec['id'],yes=True,state_root=state,remote_probe=lambda *_:old,
               git_push=lambda *_:writes.append('two') or 0,
               recovery_intent_id=prior,prior_process_finished=True)
    assert writes==['one']
    with pytest.raises(s.DeliverySessionError,match='Unresolved mutation'):
        s.record_intent(rec['id'],'pr_create','PR:2',approved=True,state_root=state)
    report=s.report(rec['id'],state_root=state)['receipt']
    assert report['resumable_without_replay'] is False
    assert report['next_safe_action'].startswith('Reconcile')


def test_unknown_remote_change_refuses_before_another_intent(existing):
    root,state,rec,key,old,head,prior=existing
    observations=iter([old, 'f'*40]);writes=[]
    with pytest.raises(s.DeliverySessionError,match='Remote changed'):
        d.push(rec['id'],yes=True,state_root=state,remote_probe=lambda *_:next(observations),
               git_push=lambda *_:writes.append('bad') or 0,
               recovery_intent_id=prior,prior_process_finished=True)
    assert writes==[]
    assert len(s.status(rec['id'],state_root=state)['intents'])==1


def test_recovery_requires_finished_prior_process(existing):
    root,state,rec,key,old,head,prior=existing
    with pytest.raises(s.DeliverySessionError,match='finished previous Git process'):
        d.push(rec['id'],yes=True,state_root=state,remote_probe=lambda *_:old,
               git_push=lambda *_:0,recovery_intent_id=prior)
    assert len(s.status(rec['id'],state_root=state)['intents'])==1


def test_exact_parent_required(existing):
    root,state,rec,key,old,head,prior=existing
    with pytest.raises(s.DeliverySessionError,match='exact parent'):
        s.recover_push_intent(rec['id'],key,prior,remote_old_sha='f'*40,approved=True,state_root=state)
    assert len(s.status(rec['id'],state_root=state)['intents'])==1


def test_duplicate_diagnostic_is_refused(existing):
    root,state,rec,key,old,head,prior=existing
    s.record_push_diagnostic(rec['id'],prior,'GIT_EXIT_NONZERO',1,state_root=state)
    with pytest.raises(s.DeliverySessionError,match='already recorded'):
        s.record_push_diagnostic(rec['id'],prior,'GIT_EXIT_NONZERO',1,state_root=state)
    assert len([x for x in s.status(rec['id'],state_root=state)['event_stream']['events']
                if x['kind']=='push_attempt_result'])==1


def test_diagnostic_never_persists_secret(existing):
    root,state,rec,key,old,head,prior=existing
    seq=iter([old,old,old]);secret='ghp_verysecret'
    with pytest.raises(s.DeliverySessionError,match='GIT_WORKFLOW_PERMISSION_REJECTED'):
        d.push(rec['id'],yes=True,state_root=state,remote_probe=lambda *_:next(seq),
               git_push=lambda *_:SimpleNamespace(returncode=1,stderr='workflow permission '+secret,stdout=''),
               recovery_intent_id=prior,prior_process_finished=True)
    raw=(state/(rec['id']+'.json')).read_text()
    assert secret not in raw
    assert 'GIT_WORKFLOW_PERMISSION_REJECTED' in raw


def test_no_auto_resume_when_journal_pair_incomplete(existing):
    root,state,rec,key,old,head,prior=existing
    s.recover_push_intent(rec['id'],key,prior,remote_old_sha=old,approved=True,state_root=state)
    status=s.status(rec['id'],state_root=state)
    assert status['resumable'] is False
    assert not s.report(rec['id'],state_root=state)['receipt']['resumable_without_replay']


def test_readonly_preview_does_not_create_new_intent(existing):
    root,state,rec,key,old,head,prior=existing
    result=d.push(rec['id'],yes=False,state_root=state,remote_probe=lambda *_:old)
    assert result['state']=='PUSH_REQUIRES_APPROVAL'
    assert len(s.status(rec['id'],state_root=state)['intents'])==1


def test_receipt_contains_sanitized_recovery_event(existing):
    root,state,rec,key,old,head,prior=existing
    s.recover_push_intent(rec['id'],key,prior,remote_old_sha=old,approved=True,state_root=state)
    status=s.status(rec['id'],state_root=state)
    row=[x for x in status['event_stream']['events'] if x['kind']=='push_negative_reconciliation']
    assert len(row)==1 and row[0]['state']=='not-applied-at-observation'
    assert row[0]['historical'] is True


def test_native_m5_stale_runtime_check_is_before_delivery_mutation():
    from devtool import native_delivery_m5
    import inspect
    source=inspect.getsource(native_delivery_m5.main)
    guard=source.index("M5_APP_WORKFLOW_CONTROLLER_STALE_NO_PUSH_INTENT")
    native=source.index("for action in ('push','pr'):")
    assert guard<native
    assert "from .github_app import _PERMISSIONS, _check_git_argv" in source


def test_generic_source_has_no_pinned_old_qg01_exception():
    import inspect
    from devtool import delivery_session, delivery_github
    src=inspect.getsource(delivery_session)
    assert '038f9f9beade4bda9715979c79fdb699226b226b' not in src
    assert 'resume_pretransport_guard_push_intent' not in src
    assert 'pretransport_guard_refusal' not in inspect.getsource(delivery_github)
