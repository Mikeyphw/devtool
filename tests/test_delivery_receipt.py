"""S6 receipts: persisted observations never qualify new work or replay actions."""
from __future__ import annotations
import copy
import pytest
from devtool.delivery_receipt import build_stream, build_receipt, render_human

SHA = 'a' * 40
REPO = 'Mikeyphw/devtool'
RUN = f'https://github.com/{REPO}/actions/runs/321'

def record():
    return {'repository': REPO, 'head_sha': SHA, 'updated_at': '2026-11-14T00:00:00Z',
            'events': [{'kind': 'session_started', 'stage': 'PLANNED', 'at': '2026-11-14T00:00:00Z'},
                       {'kind': 'mutation_intent', 'operation': 'push', 'at': '2026-11-14T00:01:00Z'},
                       {'kind': 'mutation_confirmed', 'operation': 'push', 'at': '2026-11-14T00:02:00Z'},
                       {'kind': 'ci_observed', 'state': 'REQUIRED_CHECKS_PASSED', 'at': '2026-11-14T00:03:00Z'}],
            'pr': {'number': 17, 'head_sha': SHA, 'url': f'https://github.com/{REPO}/pull/17'},
            'ci': {'source_sha': SHA, 'state': 'REQUIRED_CHECKS_PASSED', 'checks_qualified': True,
                   'required': ['CI'],
                   'runs': [{'id': 321, 'attempt': 2, 'workflow_id': 99, 'name': 'CI',
                             'status': 'completed', 'conclusion': 'success', 'event': 'pull_request',
                             'url': RUN}], 'diagnosis': {'classification': 'required-ci-passed', 'failures': []}}}

def test_unified_event_stream_has_exact_pr_run_attempt_and_no_live_claim():
    r = record()
    stream = build_stream(r)
    assert stream['source_sha'] == SHA and stream['read_only']
    assert [x['kind'] for x in stream['events']][-2:] == ['pr_snapshot', 'workflow_snapshot']
    s = {'id': 'b'*32, 'head_sha': SHA, 'repository': REPO, 'head': 'feature/foo',
         'base': 'main', 'source_binding': 'matching', 'resumable': True,
         'intents': [], 'pr': r['pr'], 'ci': r['ci'], 'event_stream': stream}
    receipt = build_receipt(s)
    assert receipt['required_ci_passed_at_last_observation']
    assert receipt['current_remote_ci_reverified'] is False
    assert receipt['release_qualified'] is False
    human = render_human(receipt)
    assert 'run=321 attempt=2' in human and 'Release qualification: NOT PERFORMED' in human

def test_source_drift_prevents_unqualified_resume():
    r = record()
    s = {'head_sha': SHA, 'repository': REPO, 'source_binding': 'drifted',
         'resumable': True, 'intents': [], 'ci': r['ci'], 'event_stream': build_stream(r)}
    assert build_receipt(s)['resumable_without_replay'] is False
    assert 'Reconcile' in build_receipt(s)['next_safe_action']

def test_uncertain_mutation_never_auto_retried():
    r = record()
    s = {'head_sha': SHA, 'source_binding': 'matching', 'resumable': True,
         'intents': [{'operation': 'pr_create', 'state': 'unresolved'}],
         'ci': r['ci'], 'event_stream': build_stream(r)}
    result = build_receipt(s)
    assert result['unresolved_intents'] == ['pr_create']
    assert not result['resumable_without_replay']

def test_malicious_and_unbound_evidence_is_rejected():
    r = record()
    r['ci']['runs'][0]['url'] = 'https://example.org/p?token=secret'
    with pytest.raises(ValueError, match='Noncanonical'):
        build_stream(r)
    r = record()
    r['ci']['source_sha'] = 'b' * 40
    with pytest.raises(ValueError, match='differs'):
        build_stream(r)
    r = record()
    r['ci']['runs'].append(copy.deepcopy(r['ci']['runs'][0]))
    with pytest.raises(ValueError, match='duplicate'):
        build_stream(r)

def test_failed_job_attribution_and_bounded_view():
    r = record()
    r['ci']['state'] = 'CHECKS_FAILED'
    r['ci']['runs'][0]['conclusion'] = 'failure'
    r['ci']['diagnosis'] = {'classification':'required-check-failed','failures':[
        {'run_id':321, 'attempt':2, 'jobs':[{'job_id':71,'name':'Unit tests','classification':'source-or-test-failure','conclusion':'failure'}]}]}
    rows = build_stream(r)['events']
    failure = [x for x in rows if x['kind']=='failed_job_snapshot'][0]
    assert failure['url'].endswith('/attempts/2/job/71')
    r['ci']['diagnosis']['failures'][0]['run_id'] = 900
    with pytest.raises(ValueError, match='not bound'):
        build_stream(r)

def test_no_secret_labels_or_external_network():
    r = record()
    r['ci']['runs'][0]['name'] = 'token=github_pat_PRIVATE'
    rows = build_stream(r)['events']
    assert rows[-1]['name'] == '[provider label omitted]'
    assert 'github_pat_PRIVATE' not in render_human(build_receipt({
        'head_sha':SHA, 'ci':r['ci'], 'event_stream':build_stream(r),
        'source_binding':'matching','resumable':True, 'intents':[]}))

def test_long_journal_does_not_truncate_original():
    r = record()
    r['events'].extend({'kind':'ci_observed','state':'CHECKS_RUNNING','at':'2026-11-14T00:04:00Z'} for _ in range(520))
    assert len(build_stream(r)['events']) <= 160
    assert len(r['events']) == 524


def test_local_only_session_without_origin_is_displayable():
    r = {'head_sha': SHA, 'repository': None, 'updated_at': '2026-11-14T00:00:00Z',
         'events': [{'kind':'session_started','stage':'PLANNED','at':'2026-11-14T00:00:00Z'}]}
    stream = build_stream(r)
    assert stream['events'][0]['stage'] == 'source'
    assert stream['events'][0]['source_sha'] == SHA
    r['ci']={'source_sha':SHA, 'runs':[{'id':1, 'attempt':1}]}
    with pytest.raises(ValueError, match='canonical repository'):
        build_stream(r)


def test_rich_frontend_renders_the_same_exact_run_ids():
    from rich.console import Console
    from devtool.delivery_receipt import render_rich
    r = record()
    status = {'head_sha': SHA, 'repository':REPO, 'source_binding':'matching',
              'resumable':True, 'intents':[], 'ci':r['ci'],
              'event_stream':build_stream(r)}
    console = Console(record=True, width=160, force_terminal=False)
    render_rich(build_receipt(status),console=console)
    output = console.export_text()
    assert '321 / 2' in output
    assert 'NOT PERFORMED' in output


def test_actual_s1_session_restart_report_never_replays(tmp_path):
    """Exercises the real S1/S6 integration when the full Devtool source is present."""
    import importlib.util
    if importlib.util.find_spec('devtool.delivery_session') is None:
        pytest.skip('Integration runs against complete Devtool source in staged/hosted acceptance')
    from pathlib import Path
    import subprocess
    from devtool.delivery_session import start, report, resume, record_intent
    repo = tmp_path / 'repo'
    repo.mkdir()
    def command(*args):
        return subprocess.run(['git','-C',str(repo),*args], check=True,
                              capture_output=True, text=True).stdout.strip()
    command('init','-b','main')
    command('config','user.name','CI')
    command('config','user.email','ci@example.invalid')
    (repo/'example.txt').write_text('example\n')
    command('add','example.txt')
    command('commit','-m','init')
    command('switch','-c','feature/s6')
    command('remote','add','origin','git@github.com:Mikeyphw/devtool.git')
    state = tmp_path / 'state'
    session = start(repo,state_root=state)
    identity = session['id']
    first = report(identity,state_root=state)
    assert first['receipt']['session_id'] == identity
    assert first['receipt']['event_stream']['events'][0]['kind'] == 'session_started'
    record_intent(identity,'push','feature/s6@sha',approved=True,state_root=state)
    second = report(identity,state_root=state)
    assert second['receipt']['resumable_without_replay'] is False
    assert 'push' in second['receipt']['unresolved_intents']
    assert resume(identity,state_root=state)['operation_replayed'] is False
