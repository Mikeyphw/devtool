"""ACTIONS-G2: bounded concurrency, durable lineage and non-replaying recovery."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from pathlib import Path

import pytest

from tests.test_actions_06_pipeline_dag import API, SLUG, commit, init_repo
from devtool.actions_pipeline import (
    _journal_path, _MAX_REMOTE_WORKERS, pipeline_run, pipeline_resume,
    pipeline_status, pipeline_cancel, pipeline_plan,
)
from devtool.cli import build_parser
from devtool.github_actions import GitHubActionsError, actions_cli


def graph():
    return {'schema':1,'name':'g2-pipeline','nodes':[
        {'id':'source','kind':'local-git'},
        {'id':'lane_a','kind':'github-dispatch','needs':['source'],
         'workflow':'devtool-android-build.yml','ref':'main','inputs':{}},
        {'id':'lane_b','kind':'github-dispatch','needs':['source'],
         'workflow':'devtool-android-build.yml','ref':'main','inputs':{}},
        {'id':'observe_a','kind':'github-result','from':'lane_a','needs':['lane_a']},
        {'id':'observe_b','kind':'github-result','from':'lane_b','needs':['lane_b']},
        {'id':'join','kind':'local-git','needs':['observe_a','observe_b']},
    ]}


def setup(tmp_path):
    repo=init_repo(tmp_path/'repo')
    (repo/'pipeline.json').write_text(json.dumps(graph()))
    head=commit(repo)
    return repo,head,API(head),tmp_path/'state'


def dispatch_factory(calls, *, barrier=None, error_lane=False):
    from itertools import count
    num=count()
    def dispatch(client,slug,workflow,ref,inputs,*,execute):
        if not execute:
            return {'pre_dispatch_sha':client.head,'workflow_id':99,
                    'workflow_path':'.github/workflows/devtool-android-build.yml',
                    'state':'validated-plan'}
        lane=next(num)
        calls.append(lane)
        if barrier is not None:barrier.wait(timeout=6)
        if error_lane and lane==0:raise GitHubActionsError('Uncertain POST')
        return {'pre_dispatch_sha':client.head,'workflow_id':99,'run_id':777,
                'request_id':str(lane).zfill(32),'state':'accepted-correlated'}
    return dispatch


def test_parallel_independent_remote_dispatches_and_joins_wait(tmp_path,monkeypatch):
    repo,head,api,state=setup(tmp_path)
    dispatched=[]
    monkeypatch.setattr('devtool.actions_pipeline.dispatch_workflow',
                        dispatch_factory(dispatched,barrier=threading.Barrier(2)))
    first=pipeline_run(api,SLUG,repo,'pipeline.json',state_root=state)
    nodes={x['id']:x for x in first['nodes']}
    assert len(dispatched)==2 and _MAX_REMOTE_WORKERS==4
    assert nodes['lane_a']['state']==nodes['lane_b']['state']=='success'
    assert nodes['observe_a']['state']==nodes['observe_b']['state']=='waiting'
    assert nodes['join']['state']=='pending'
    api.status='completed';api.conclusion='success'
    second=pipeline_resume(api,SLUG,repo,first['pipeline_id'],state_root=state)
    assert second['completed_successfully'] and second['nodes'][-1]['state']=='success'
    assert len(dispatched)==2  # no repeat POST on resume


def test_uncertain_parallel_dispatch_does_not_repeat_on_resume(tmp_path,monkeypatch):
    repo,head,api,state=setup(tmp_path)
    calls=[]
    monkeypatch.setattr('devtool.actions_pipeline.dispatch_workflow',
                        dispatch_factory(calls,error_lane=True))
    first=pipeline_run(api,SLUG,repo,'pipeline.json',state_root=state)
    assert len(calls)==2 and first['state']=='blocked'
    assert any(x['state']=='uncertain' for x in first['nodes'])
    second=pipeline_resume(api,SLUG,repo,first['pipeline_id'],state_root=state)
    assert len(calls)==2 and second['state']=='blocked'


def test_interrupted_observation_safe_but_dispatch_not_replayed(tmp_path,monkeypatch):
    repo,head,api,state=setup(tmp_path)
    calls=[]
    monkeypatch.setattr('devtool.actions_pipeline.dispatch_workflow',dispatch_factory(calls))
    first=pipeline_run(api,SLUG,repo,'pipeline.json',state_root=state)
    path=_journal_path(first['pipeline_id'],state_root=state)
    journal=json.loads(path.read_text())
    journal['nodes']['observe_a']['state']='running'
    journal['nodes']['lane_b']['state']='running'
    path.write_text(json.dumps(journal))
    second=pipeline_resume(api,SLUG,repo,first['pipeline_id'],state_root=state)
    ns={x['id']:x for x in second['nodes']}
    assert ns['lane_b']['state']=='uncertain'
    assert 'reconciliation' in ns['lane_b']['detail']
    assert ns['observe_a']['state']=='waiting'
    assert len(calls)==2


def test_interrupted_local_mutator_fails_closed(tmp_path,monkeypatch):
    repo,head,api,state=setup(tmp_path)
    g=graph();g['nodes'].append({'id':'invoke','kind':'local-invoke','needs':['join'],'action':'unit'})
    (repo/'pipeline.json').write_text(json.dumps(g));head=commit(repo);api.head=head
    monkeypatch.setattr('devtool.actions_pipeline.dispatch_workflow',dispatch_factory([]))
    first=pipeline_run(api,SLUG,repo,'pipeline.json',state_root=state)
    path=_journal_path(first['pipeline_id'],state_root=state)
    journal=json.loads(path.read_text());journal['nodes']['invoke']['state']='running'
    path.write_text(json.dumps(journal))
    second=pipeline_resume(api,SLUG,repo,first['pipeline_id'],state_root=state)
    assert second['nodes'][-1]['state']=='uncertain'
    assert not second['completed_successfully']


def test_cancel_marks_request_without_remote_job_cancellation(tmp_path,monkeypatch):
    repo,head,api,state=setup(tmp_path)
    calls=[]
    monkeypatch.setattr('devtool.actions_pipeline.dispatch_workflow',dispatch_factory(calls))
    first=pipeline_run(api,SLUG,repo,'pipeline.json',state_root=state)
    x=first['pipeline_id']
    plan=pipeline_cancel(x,state_root=state)
    assert plan['cancel_requested'] and 'not cancelled' in plan['cancel_note']
    assert pipeline_status(x,state_root=state)['cancel_requested']
    assert pipeline_cancel(x,state_root=state)['cancel_requested']
    api.status='completed';api.conclusion='success'
    second=pipeline_resume(api,SLUG,repo,x,state_root=state)
    assert second['state']=='cancelled'
    assert second['nodes'][-1]['state']=='cancelled'
    assert len(calls)==2


def test_cancel_during_inflight_dispatch_finishes_dispatch_without_replay(tmp_path,monkeypatch):
    repo,head,api,state=setup(tmp_path)
    began=threading.Event();release=threading.Event();calls=[]
    def dispatch(client,slug,workflow,ref,inputs,*,execute):
        if not execute:
            return {'pre_dispatch_sha':client.head,'workflow_id':99,
                    'workflow_path':'.github/workflows/devtool-android-build.yml'}
        began.set();assert release.wait(timeout=8)
        calls.append(1)
        return {'pre_dispatch_sha':client.head,'workflow_id':99,'run_id':777,
                'request_id':'e'*32,'state':'accepted-correlated'}
    monkeypatch.setattr('devtool.actions_pipeline.dispatch_workflow',dispatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future=pool.submit(pipeline_run,api,SLUG,repo,'pipeline.json',state_root=state)
        assert began.wait(timeout=8)
        ids=[x.stem for x in state.glob('*.json')]
        assert len(ids)==1
        assert pipeline_cancel(ids[0],state_root=state)['cancel_requested']
        release.set()
        out=future.result(timeout=8)
    assert out['state']=='cancelled' and len(calls)==2  # both already in flight
    assert not out['completed_successfully']
    assert out['nodes'][-1]['state']=='cancelled'


def test_parser_cancel_requires_confirmation_and_no_remote_auth(tmp_path,monkeypatch):
    parser=build_parser()
    args=parser.parse_args(['actions','pipeline','cancel','--pipeline-id','e'*32,'--yes','--json'])
    assert (args.pipeline_command,args.pipeline_id,args.yes)==('cancel','e'*32,True)
    # Uses only local journal state and does not request GitHub token.
    repo,head,api,state=setup(tmp_path)
    monkeypatch.setattr('devtool.actions_pipeline.dispatch_workflow',dispatch_factory([]))
    first=pipeline_run(api,SLUG,repo,'pipeline.json',state_root=state)
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path))
    # explicit missing cancellation target is an error, not a success receipt
    with pytest.raises(GitHubActionsError):
        actions_cli(args,repo)


def test_cancel_marker_symlink_rejected(tmp_path,monkeypatch):
    repo,head,api,state=setup(tmp_path)
    monkeypatch.setattr('devtool.actions_pipeline.dispatch_workflow',dispatch_factory([]))
    first=pipeline_run(api,SLUG,repo,'pipeline.json',state_root=state)
    marker=_journal_path(first['pipeline_id'],state_root=state).with_suffix('.cancel')
    marker.symlink_to(repo/'pipeline.json')
    with pytest.raises(GitHubActionsError,match='symlink'):
        pipeline_cancel(first['pipeline_id'],state_root=state)
    with pytest.raises(GitHubActionsError,match='symlink'):
        pipeline_status(first['pipeline_id'],state_root=state)


def test_bounded_parallel_remote_limit_and_local_serialization(tmp_path,monkeypatch):
    repo=init_repo(tmp_path/'repo')
    body={'schema':1,'name':'g2-bounded','nodes':[{'id':'source','kind':'local-git'}]}
    body['nodes'] += [{'id':f'r{i}','kind':'github-dispatch','needs':['source'],
                       'workflow':'devtool-android-build.yml','ref':'main','inputs':{}} for i in range(7)]
    (repo/'pipeline.json').write_text(json.dumps(body))
    head=commit(repo);api=API(head)
    active=0;maximum=0;count=0;lock=threading.Lock()
    def dispatch(client,slug,workflow,ref,inputs,*,execute):
        nonlocal active,maximum,count
        if not execute:
            return {'pre_dispatch_sha':head,'workflow_id':99,'workflow_path':'.github/workflows/devtool-android-build.yml'}
        with lock:
            count+=1;number=count;active+=1;maximum=max(maximum,active)
        time.sleep(.05)
        with lock:active-=1
        return {'pre_dispatch_sha':head,'workflow_id':99,'run_id':number,
                'request_id':str(number).zfill(32),'state':'accepted-correlated'}
    monkeypatch.setattr('devtool.actions_pipeline.dispatch_workflow',dispatch)
    out=pipeline_run(api,SLUG,repo,'pipeline.json',state_root=tmp_path/'state')
    assert out['completed_successfully']
    assert maximum == _MAX_REMOTE_WORKERS and count == 7


def test_cancel_completed_pipeline_is_noop(tmp_path,monkeypatch):
    repo=init_repo(tmp_path/'repo')
    (repo/'pipeline.json').write_text(json.dumps({'schema':1,'name':'done','nodes':[{'id':'source','kind':'local-git'}]}))
    head=commit(repo);api=API(head)
    state=tmp_path/'state'
    done=pipeline_run(api,SLUG,repo,'pipeline.json',state_root=state)
    assert done['completed_successfully']
    result=pipeline_cancel(done['pipeline_id'],state_root=state)
    assert result['completed_successfully'] and not result['cancel_requested']
    assert not _journal_path(done['pipeline_id'],state_root=state).with_suffix('.cancel').exists()
