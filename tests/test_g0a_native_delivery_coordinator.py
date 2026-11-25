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


def _legacy_history_fixture(tmp_path, monkeypatch):
    repo, overlay, state, receipt = repo_fixture(tmp_path, monkeypatch)
    receipt['manifest']['validation'] = {
        'required': True, 'restore': False, 'build': False, 'test': False,
        'tests': [{'id': 'prebound-native-session-and-security', 'runner': 'python',
                   'required': True, 'command': ['python3', '-B', '-m', 'pytest', '-q', 'tests/test.py']}],
    }
    receipt['validation_plan'] = {}
    receipt['child_outcomes'] = [
        {'name': 'apply-overlay', 'returncode': 0, 'skipped': False, 'failure_kind': ''},
        {'name': 'artifact-test:prebound-native-session-and-security',
         'returncode': 0, 'skipped': False, 'failure_kind': ''},
        {'name': 'git-commit', 'returncode': 0, 'skipped': False, 'failure_kind': ''},
    ]
    return repo, overlay, state, receipt


def test_native_schema_v2_prebound_recovery_uses_original_child_evidence(tmp_path, monkeypatch):
    repo, overlay, state, receipt = _legacy_history_fixture(tmp_path, monkeypatch)
    prebound = start(repo, overlay=overlay, state_root=state)
    journal = state / (prebound['id'] + '.json')
    first = adopt_applied(repo, overlay, state_root=state)
    assert first['id'] == prebound['id'] and first['reused']
    assert first['native_apply']['state'] == 'COMMITTED'
    assert first['native_apply']['validation_evidence_source'] == 'original-native-child-outcomes'
    assert first['native_apply']['validation_steps'][0]['name'] == 'artifact-test:prebound-native-session-and-security'
    assert receipt['validation_plan'] == {}  # original native evidence is never edited
    assert len(list(state.glob('*.json'))) == 1
    saved = journal.read_bytes()
    again = adopt_applied(repo, overlay, state_root=state)
    assert again['id'] == prebound['id'] and journal.read_bytes() == saved
    assert git(repo, 'rev-parse', 'HEAD') == receipt['commit_hash']


@pytest.mark.parametrize('fault', [
    'missing_test', 'duplicate_test', 'extra_test', 'skipped_test',
    'failed_test', 'missing_commit', 'failed_commit', 'bad_step_kind',
    'v3_schema', 'changed_digest', 'incomplete_transaction', 'extra_required_test',
])
def test_native_schema_v2_prebound_refuses_unproven_child_evidence(tmp_path, monkeypatch, fault):
    repo, overlay, state, receipt = _legacy_history_fixture(tmp_path, monkeypatch)
    prebound = start(repo, overlay=overlay, state_root=state)
    journal = state / (prebound['id'] + '.json')
    before = journal.read_bytes()
    children = receipt['child_outcomes']
    if fault == 'missing_test':
        children.pop(1)
    elif fault == 'duplicate_test':
        children.append(dict(children[1]))
    elif fault == 'extra_test':
        children.append(dict(children[1], name='artifact-test:unlisted'))
    elif fault == 'skipped_test':
        children[1]['skipped'] = True
    elif fault == 'failed_test':
        children[1]['returncode'] = 1
    elif fault == 'missing_commit':
        children.pop(2)
    elif fault == 'failed_commit':
        children[2]['returncode'] = 1
    elif fault == 'bad_step_kind':
        children[1]['failure_kind'] = 'validation_failure'
    elif fault == 'v3_schema':
        receipt['manifest']['schema_version'] = 3
    elif fault == 'changed_digest':
        receipt['sha256'] = '0' * 64
    elif fault == 'incomplete_transaction':
        receipt['transaction']['status'] = 'pending'
    elif fault == 'extra_required_test':
        receipt['manifest']['validation']['tests'].append({
            'id': 'another-required-test', 'runner': 'python', 'required': True,
            'command': ['python3', '-B', '-m', 'pytest', '-q', 'tests/other.py'],
        })
    with pytest.raises(DeliverySessionError):
        adopt_applied(repo, overlay, state_root=state)
    assert journal.read_bytes() == before
    assert len(list(state.glob('*.json'))) == 1


def test_native_history_projects_artifact_test_as_validation_step(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from devtool import cli
    repo, overlay, state, receipt = repo_fixture(tmp_path, monkeypatch)
    captured = []
    # Use the actual native make_history_entry() constructor.  A minimal
    # dictionary stub formerly omitted validation_status and hid the field's
    # provenance; this assertion must test the native recorder, not a mock.
    monkeypatch.setattr(cli, 'append_history', lambda root, item, **kw:
                        (captured.append(item) or state / 'native-history.json'))
    monkeypatch.setattr(cli, 'latest_commit_hash', lambda root: receipt['commit_hash'])
    monkeypatch.setattr(cli, 'collect_hook_events', lambda results: [])
    monkeypatch.setattr(cli, '_apply_transaction_path', lambda results: '')
    monkeypatch.setattr(cli, '_repo_local_artifact_history_enabled', lambda root: False)
    monkeypatch.setattr(cli, 'publish_event', lambda event: None)
    args = SimpleNamespace(command='apply-overlay', validate=True, validate_only=False,
                           preview=False, artifact=str(overlay), commit='source-bound native',
                           _devtool_artifact_manifest=receipt['manifest'], _devtool_run_id='test-run')
    results = [
        cli.OperationResult('apply-overlay', [], 0, 0.0, '', []),
        cli.OperationResult('artifact-test:prebound-native-session-and-security', [], 0, 0.0, '', []),
        cli.OperationResult('git-commit', [], 0, 0.0, '', []),
    ]
    cli._record_apply_history(SimpleNamespace(repo_root=repo), args, results)
    assert len(captured) == 1
    steps = captured[0]['validation_plan']['validation_step_outcomes']
    assert len(steps) == 1 and steps[0]['name'] == 'artifact-test:prebound-native-session-and-security'
    assert steps[0]['outcome'] == 'passed' and steps[0]['skipped'] is False
    assert captured[0]['validation_status'] == 'ok'


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


def test_static_history_records_each_lifecycle_child(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from devtool import cli
    repo,overlay,state,receipt=repo_fixture(tmp_path,monkeypatch)
    captured=[]
    monkeypatch.setattr(cli,'append_history',lambda root,item,**kw:(captured.append(item) or state/'history.json'))
    monkeypatch.setattr(cli,'latest_commit_hash',lambda root:receipt['commit_hash'])
    monkeypatch.setattr(cli,'collect_hook_events',lambda results:[])
    monkeypatch.setattr(cli,'_apply_transaction_path',lambda results:'')
    monkeypatch.setattr(cli,'_repo_local_artifact_history_enabled',lambda root:False)
    monkeypatch.setattr(cli,'publish_event',lambda event:None)
    args=SimpleNamespace(command='apply-overlay',validate=False,static_validate=True,validate_only=False,preview=False,
                         artifact=str(overlay),commit='source-bound native',_devtool_artifact_manifest=receipt['manifest'],_devtool_run_id='static-run')
    row=cli.OperationResult('artifact-test:second',[],0,0.0,'',[])
    row.metadata.update(lifecycle_steps_replace_parent=True,lifecycle_steps=[{'name':name,'returncode':0,'skipped':False,'warnings':0} for name in ('artifact-test:first','artifact-test:second')])
    cli._record_apply_history(SimpleNamespace(repo_root=repo),args,[cli.OperationResult('apply-overlay',[],0,0.0,'',[]),row,cli.OperationResult('git-commit',[],0,0.0,'',[])])
    assert captured[0]['validation_status']=='ok'
    assert [x['name'] for x in captured[0]['validation_plan']['validation_step_outcomes']]==['artifact-test:first','artifact-test:second']
