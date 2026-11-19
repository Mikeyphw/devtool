"""Milestone A: native applied-commit adoption never replays apply or remote mutation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from zipfile import ZipFile

import pytest

from devtool import artifacts
from devtool.delivery_session import adopt_applied, start, status, resume, record_intent, DeliverySessionError
from devtool.native_validation_plan import normalize


def git(repo, *args):
    proc = subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,check=True)
    return proc.stdout.strip()


def repo_fixture(tmp_path, monkeypatch):
    repo = tmp_path/'repo';repo.mkdir()
    git(repo,'init','-q');git(repo,'config','user.email','qa@example.test');git(repo,'config','user.name','QA')
    git(repo,'remote','add','origin','git@github.com:Mikeyphw/devtool.git')
    git(repo,'checkout','-qb','feature/test')
    (repo/'sample.txt').write_text('old\n');git(repo,'add','sample.txt');git(repo,'commit','-qm','base')
    before=git(repo,'rev-parse','HEAD')
    tree=git(repo,'rev-parse','HEAD^{tree}')
    (repo/'sample.txt').write_text('new\n');git(repo,'add','sample.txt');git(repo,'commit','-qm','native applied')
    after=git(repo,'rev-parse','HEAD')
    payload=b'new\n'; sha=hashlib.sha256(payload).hexdigest()
    artifact=tmp_path/'overlay.zip'
    manifest={'type':'overlay','schema_version':2,'base_commit':before,'base_tree':tree,
              'expected_post_sha256':{'sample.txt':sha},
              'inventory':{'format':1,'entries':[{'path':'sample.txt','action':'modify',
                             'source_sha256':hashlib.sha256(b'old\n').hexdigest(),
                             'sha256':sha,'size':len(payload)}]}}
    with ZipFile(artifact,'w') as z:
        z.writestr('.devtool-artifact.json',json.dumps(manifest));z.writestr('sample.txt',payload)
    digest=hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest['validation']={'required':True,'test':True}
    evidence={'sha256':digest,'commit_hash':after,'artifact':str(artifact),'manifest':manifest,
              'status':'ok','validation_status':'ok','content_identity':{'size':artifact.stat().st_size},
              'transaction':{'id':'native-transaction-qa','status':'completed'},
              'history_id':'native-history-qa','operation_id':'native-operation-qa',
              'validation_plan':{'validation_step_outcomes':[
                  {'name':'artifact-validate-script','outcome':'passed','returncode':0,'skipped':False}]}}
    monkeypatch.setattr(artifacts,'load_history',lambda root:[evidence])
    state=tmp_path/'state';state.mkdir(mode=0o700)
    return repo,artifact,state,evidence


def test_adopt_existing_commit_reuses_durable_session_and_never_reapplies(tmp_path,monkeypatch):
    repo,overlay,state,evidence=repo_fixture(tmp_path,monkeypatch)
    first=adopt_applied(repo,overlay,state_root=state)
    assert first['reused'] is False and first['native_apply_replayed'] is False
    second=adopt_applied(repo,overlay,state_root=state)
    assert second['id']==first['id'] and second['reused'] is True
    current=status(first['id'],state_root=state)
    assert current['source_binding']=='matching'
    assert resume(first['id'],state_root=state)['operation_replayed'] is False
    assert len(list(state.glob('*.json')))==1
    assert git(repo,'rev-parse','HEAD')==evidence['commit_hash']


def test_refuse_without_native_passed_receipt(tmp_path,monkeypatch):
    repo,overlay,state,evidence=repo_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(artifacts,'load_history',lambda _root:[])
    with pytest.raises(DeliverySessionError,match='history receipt'):
        adopt_applied(repo,overlay,state_root=state)
    assert not list(state.glob('*.json'))


def test_refuse_changed_payload_and_dirty_checkout(tmp_path,monkeypatch):
    repo,overlay,state,evidence=repo_fixture(tmp_path,monkeypatch)
    (repo/'sample.txt').write_text('uncommitted')
    with pytest.raises(DeliverySessionError,match='clean'):
        adopt_applied(repo,overlay,state_root=state)
    (repo/'sample.txt').write_text('new\n')
    git(repo,'commit','--allow-empty','-qm','unexpected commit')
    evidence['commit_hash']=git(repo,'rev-parse','HEAD')
    with pytest.raises(DeliverySessionError,match='parent'):
        adopt_applied(repo,overlay,state_root=state)


def test_corrupt_existing_journal_blocks_new_adoption(tmp_path,monkeypatch):
    repo,overlay,state,evidence=repo_fixture(tmp_path,monkeypatch)
    bad=state/('a'*32+'.json');bad.write_text('{invalid')
    bad.chmod(0o600)
    with pytest.raises(DeliverySessionError,match='corrupted'):
        adopt_applied(repo,overlay,state_root=state)


def test_validation_projection_required_vs_static_and_legacy():
    assert normalize({}).mode=='legacy'
    full={'executable_overlay':{},'validation':{'required':True,'test':True}}
    assert normalize(full).mode=='required'
    with pytest.raises(ValueError,match='requires'):
        normalize({'executable_overlay':{},'validation':{'required':False}})
    static={'executable_overlay':{},'validation':{'static_only':True,'restore':False,'build':False,'test':False,
            'static_justification':'Exact source-only verification; hosted tests pending.'}}
    p=normalize(static)
    assert p.mode=='static-only' and not p.required
    assert p.evidence=='static-only-not-runtime-qualified'


def test_refuse_commit_with_uninventoried_extra_path(tmp_path,monkeypatch):
    repo,overlay,state,evidence=repo_fixture(tmp_path,monkeypatch)
    (repo/'unauthorized.txt').write_text('should not be adopted\n')
    git(repo,'add','unauthorized.txt')
    # Amend only in the local test fixture; the overlay delivery process never
    # amends, resets, rebases, or force-pushes a user repository.
    git(repo,'commit','--amend','--no-edit','-q')
    # The native history evidence follows that commit, testing the inventory
    # boundary independently of a matching history receipt.
    evidence['commit_hash']=git(repo,'rev-parse','HEAD')
    with pytest.raises(DeliverySessionError,match='unverified'):
        adopt_applied(repo,overlay,state_root=state)


def test_adopt_native_cleaned_archive_via_receipt(tmp_path, monkeypatch):
    repo, overlay, state, evidence = repo_fixture(tmp_path, monkeypatch)
    overlay.unlink()
    first = adopt_applied(repo, overlay, state_root=state)
    assert first['overlay']['sha256']==evidence['sha256']
    second = adopt_applied(repo, overlay, state_root=state)
    assert second['id']==first['id'] and second['reused']


def test_adopt_reuses_existing_native_planned_session_without_journal_duplication(tmp_path, monkeypatch):
    repo, overlay, state, evidence = repo_fixture(tmp_path, monkeypatch)
    started = start(repo, state_root=state)
    adopted = adopt_applied(repo, overlay, state_root=state)
    assert adopted['id'] == started['id'] and adopted['reused']
    assert adopted['overlay']['sha256'] == evidence['sha256']
    assert status(started['id'], state_root=state)['source_binding'] == 'matching'
    assert len(list(state.glob('*.json'))) == 1


def test_reconcile_prebound_planned_overlay_preserves_identity_and_history(tmp_path, monkeypatch):
    # Regression: the original launcher binds the artifact before it records
    # native_apply. Adoption must augment that SAME session after receipt proof.
    repo, overlay, state, receipt = repo_fixture(tmp_path, monkeypatch)
    prebound = start(repo, overlay=overlay, state_root=state)
    journal = state / (prebound['id'] + '.json')
    original_commit = git(repo, 'rev-parse', 'HEAD')
    before = status(prebound['id'], state_root=state)
    assert before['overlay']['sha256'] == receipt['sha256']
    assert 'native_apply' not in before
    applied = adopt_applied(repo, overlay, state_root=state)
    assert applied['id'] == prebound['id']
    assert applied['reused'] and applied['native_apply_replayed'] is False
    observed = status(prebound['id'], state_root=state)
    assert observed['stage'] == 'PLANNED' and observed['native_apply']['state'] == 'COMMITTED'
    assert observed['native_apply']['commit'] == original_commit
    assert observed['native_apply']['transaction_status'] == 'completed'
    assert observed['native_apply']['validation_status'] == 'ok'
    assert len(list(state.glob('*.json'))) == 1
    assert git(repo, 'rev-parse', 'HEAD') == original_commit
    persisted = journal.read_bytes()
    twice = adopt_applied(repo, overlay, state_root=state)
    assert twice['id'] == prebound['id'] and twice['reused']
    assert journal.read_bytes() == persisted  # idempotent, no extra event
    assert resume(prebound['id'], state_root=state)['operation_replayed'] is False


@pytest.mark.parametrize('fault', [
    'missing_history', 'duplicate_history', 'wrong_commit', 'bad_parent',
    'incomplete_transaction', 'skipped_validation', 'failed_validation',
    'changed_digest',
])
def test_prebound_recovery_refuses_unverified_native_evidence(tmp_path, monkeypatch, fault):
    repo, overlay, state, receipt = repo_fixture(tmp_path, monkeypatch)
    prebound = start(repo, overlay=overlay, state_root=state)
    journal = state / (prebound['id'] + '.json')
    original_bytes = journal.read_bytes()
    original_commit = git(repo, 'rev-parse', 'HEAD')
    if fault == 'missing_history':
        monkeypatch.setattr(artifacts, 'load_history', lambda root: [])
    elif fault == 'duplicate_history':
        monkeypatch.setattr(artifacts, 'load_history', lambda root: [receipt, receipt])
    elif fault == 'wrong_commit':
        receipt['commit_hash'] = '0' * 40
    elif fault == 'bad_parent':
        receipt['manifest']['base_commit'] = '0' * 40
    elif fault == 'incomplete_transaction':
        receipt['transaction']['status'] = 'pending'
    elif fault == 'skipped_validation':
        receipt['validation_plan']['validation_step_outcomes'][0]['skipped'] = True
    elif fault == 'failed_validation':
        receipt['validation_plan']['validation_step_outcomes'][0]['outcome'] = 'failed'
    elif fault == 'changed_digest':
        receipt['sha256'] = '0' * 64
    with pytest.raises(DeliverySessionError):
        adopt_applied(repo, overlay, state_root=state)
    assert journal.read_bytes() == original_bytes
    assert git(repo, 'rev-parse', 'HEAD') == original_commit
    assert len(list(state.glob('*.json'))) == 1


def test_prebound_recovery_refuses_remote_intent_without_mutating_journal(tmp_path, monkeypatch):
    repo, overlay, state, _ = repo_fixture(tmp_path, monkeypatch)
    prebound = start(repo, overlay=overlay, state_root=state)
    record_intent(prebound['id'], 'push', 'origin:feature/test', approved=True, state_root=state)
    journal = state / (prebound['id'] + '.json')
    before = journal.read_bytes()
    with pytest.raises(DeliverySessionError, match='remote-mutated or progressed'):
        adopt_applied(repo, overlay, state_root=state)
    assert journal.read_bytes() == before
    assert len(list(state.glob('*.json'))) == 1


def test_prebound_recovery_refuses_conflicting_transaction_evidence(tmp_path, monkeypatch):
    repo, overlay, state, _ = repo_fixture(tmp_path, monkeypatch)
    from devtool.delivery_session import _read, _write
    prebound = start(repo, overlay=overlay, state_root=state)
    journal = state / (prebound['id'] + '.json')
    record = _read(journal)
    record['native_apply'] = {'state':'COMMITTED', 'commit':'0' * 40}
    _write(journal, record)  # a valid *fixture* journal with conflicting native evidence
    before = journal.read_bytes()
    with pytest.raises(DeliverySessionError, match='transaction proof differs'):
        adopt_applied(repo, overlay, state_root=state)
    assert journal.read_bytes() == before


def test_adopt_refuses_ambiguous_native_delivery_sessions(tmp_path, monkeypatch):
    repo, overlay, state, _ = repo_fixture(tmp_path, monkeypatch)
    start(repo, state_root=state)
    start(repo, state_root=state)
    with pytest.raises(DeliverySessionError, match='Ambiguous native sessions'):
        adopt_applied(repo, overlay, state_root=state)
    assert len(list(state.glob('*.json'))) == 2


def test_adopt_refuses_rebinding_session_with_remote_intent(tmp_path, monkeypatch):
    repo, overlay, state, _ = repo_fixture(tmp_path, monkeypatch)
    started = start(repo, state_root=state)
    record_intent(started['id'], 'push', 'origin:feature/test', approved=True, state_root=state)
    with pytest.raises(DeliverySessionError, match='remote-mutated or progressed'):
        adopt_applied(repo, overlay, state_root=state)


def test_adopt_refuses_existing_other_overlay_identity(tmp_path, monkeypatch):
    repo, overlay, state, _ = repo_fixture(tmp_path, monkeypatch)
    other = tmp_path/'other.zip'; other.write_bytes(b'another artifact')
    start(repo, overlay=other, state_root=state)
    with pytest.raises(DeliverySessionError, match='another overlay'):
        adopt_applied(repo, overlay, state_root=state)


def test_exclusive_tasks_cannot_promise_skipped_checks():
    for extra in ({'script':{'path':'.devtool-artifact/validate.sh'}},
                  {'test':True}, {'refs':['test:foo']}, {'tests':['test:foo']}):
        plan = {'executable_overlay':{},'validation':{
            'required':True,'tasks':['unit'],'restore':False,'build':False,'test':False,
            **extra}}
        with pytest.raises(ValueError, match='Exclusive'):
            normalize(plan)


def test_static_only_requires_explicitly_disabled_execution():
    raw={'executable_overlay':{},'validation':{'static_only':True,
        'static_justification':'Static-only source audit; hosted results pending.'}}
    with pytest.raises(ValueError,match='explicitly disable'):
        normalize(raw)
    raw['validation'].update(restore=False,build=False,test=False)
    assert normalize(raw).mode=='static-only'


def test_exclusive_tasks_plan_never_reports_implicit_builtin_phases():
    p=normalize({'executable_overlay':{},'validation':{'required':True,'tasks':['check']}})
    assert p.phases==('tasks',)
    assert p.task_count==1 and p.evidence=='pending-native-execution'


def test_adoption_projects_real_native_transaction_identity(tmp_path, monkeypatch):
    repo, overlay, state, evidence = repo_fixture(tmp_path, monkeypatch)
    record=adopt_applied(repo,overlay,state_root=state)
    assert record['native_apply']['state']=='COMMITTED'
    assert record['native_apply']['transaction_id']=='native-transaction-qa'
    assert record['native_apply']['operation_id']=='native-operation-qa'
    assert record['native_apply']['history_id']=='native-history-qa'
    observed=status(record['id'],state_root=state)
    assert observed['native_apply']==record['native_apply']
    assert 'never reapply' in resume(record['id'],state_root=state)['next_action']


def test_adoption_refuses_skipped_or_failed_required_step(tmp_path, monkeypatch):
    repo, overlay, state, evidence = repo_fixture(tmp_path, monkeypatch)
    step=evidence['validation_plan']['validation_step_outcomes'][0]
    for item in ({'skipped':True}, {'outcome':'failed'}, {'returncode':1}):
        step.update({'skipped':False,'outcome':'passed','returncode':0},**item)
        with pytest.raises(DeliverySessionError,match='unskipped step evidence'):
            adopt_applied(repo,overlay,state_root=state)
    assert not list(state.glob('*.json'))


def test_adoption_refuses_incomplete_native_transaction(tmp_path, monkeypatch):
    repo, overlay, state, evidence = repo_fixture(tmp_path, monkeypatch)
    evidence['transaction']['status']='backup-ready'
    with pytest.raises(DeliverySessionError,match='completed transaction'):
        adopt_applied(repo,overlay,state_root=state)
    assert not list(state.glob('*.json'))
