"""C2 consolidated status: one native truth, three read-only presentations."""
from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from rich.console import Console

from devtool.operator_status import (SCHEMA, empty_status, project_delivery_status,
                                    render_status_text, latest_for_repo)
from devtool.operator_rich import build_operator_view
from devtool.delivery_session import (start, status, record_intent)
from devtool.delivery_cli import run_delivery_command
from devtool.textual_control_center_runtime import (InMemoryControlCenterProvider,
    RecordingControlActionDispatcher, create_textual_control_center_app)

HEAD = 'a' * 40
PACKAGE = 'b' * 64
SESSION = 'c' * 32


def native(*, bound=True, committed=True, recorded=True):
    s = {
        'id':SESSION,'repo_root':'/repo','repository':'Mikeyphw/devtool',
        'head_repository':'Mikeyphw/devtool','base_repository':'Mikeyphw/devtool',
        'head':'feature/c2','base':'main','head_sha':HEAD,
        'source_binding':'matching' if bound else 'drifted',
        'resumable':True,'stage':'PLANNED','remote_verified':True,
        'overlay':{'path':'/packages/c2.zip','sha256':PACKAGE},
        'native_apply':{'state':'COMMITTED','commit':HEAD,'artifact_sha256':PACKAGE,
                        'transaction_status':'completed','validation_status':'ok',
                        'transaction_id':'transaction-c2','operation_id':'operation-c2',
                        'history_id':'history-c2'} if committed else {},
        'pr':{'number':29,'head_sha':HEAD,'head_repository':'Mikeyphw/devtool',
              'base_repository':'Mikeyphw/devtool',
              'url':'https://github.com/Mikeyphw/devtool/pull/29'},
        'ci':{'state':'REQUIRED_CHECKS_PASSED','source_sha':HEAD,'checks_qualified':True,
              'policy_sha256':PACKAGE,
              'runs':[{'id':123,'attempt':1,'name':'Required workflow',
                       'workflow_id':222,'status':'completed','conclusion':'success'}]},
        'intents':[],
        'event_stream':{'schema':'devtool.delivery-event-stream.v1','source_sha':HEAD,
                        'read_only':True,'events':[{'kind':'pr_snapshot','source_sha':HEAD,
                                                    'number':29,'historical':True}]},
    }
    if not recorded:
        s.pop('ci')
    return s


def proof(*, head=HEAD, good=True):
    return {
        'schema':'devtool.delivery-qualification.v1',
        'session_id':SESSION, 'head_sha':head,
        'read_only':True,'remote_mutation_performed':False,
        'pre_merge_qualification':'PASSED',
        'required_delivery_ci':{'state':'PASSED','passed':True},
        'repository_regression':{'state':'PASSED','passed':True,'source_sha':head,
                                 'shards_passed':24,'summary_passed':True,'jobs_passed':25,
                                 'artifacts_verified':25 if good else 24,
                                 'attribution_proven':True,
                                 'workflow_path':'.github/workflows/devtool-full-repository-pytest.yml',
                                 'run_id':930,'attempt':2,'request_id':'request-1',
                                 'url':'https://github.com/Mikeyphw/devtool/actions/runs/930'},
        'overall_acceptance':'PENDING_POST_MERGE_MAIN_HEAD',
    }


def test_c2_recorded_ci_is_not_live_qualified():
    p=project_delivery_status(native())
    assert p['schema']==SCHEMA and p['read_only']
    assert p['transaction']['state']=='COMMITTED'
    assert p['transaction']['id']=='transaction-c2'
    assert p['package']['sha256']==PACKAGE
    assert p['target']['head_sha']==HEAD
    assert p['session']['id']==SESSION
    assert p['pr']['number']==29
    assert p['ci']['required']['state']=='RECORDED_NEEDS_LIVE_RECHECK'
    assert not p['ci']['required']['passed']
    assert p['ci']['required']['runs'][0]['attempt']==1
    assert p['ci']['regression']['state']=='NOT_ATTESTED'
    assert p['ci']['overall_acceptance']=='BLOCKED_OR_PENDING'
    assert p['events'][0]['kind']=='pr_snapshot'
    assert 'REGRESSION_NOT_LIVE_QUALIFIED' in {x['code'] for x in p['blockers']}
    assert p['remote_mutation_performed'] is False


def test_c2_premerge_green_does_not_claim_merged_main_device_or_release():
    p=project_delivery_status(native(),qualification=proof())
    assert p['state']=='PRE_MERGE_QUALIFIED_PENDING_MAIN'
    assert p['ci']['required']['passed'] is True
    assert p['ci']['regression']['passed'] is True
    assert p['ci']['regression']['run_id']==930
    assert p['ci']['regression']['attempt']==2
    assert p['ci']['overall_acceptance']=='PENDING_POST_MERGE_MAIN_HEAD'
    assert p['ci']['release_qualification']=='NOT_PERFORMED'
    assert p['ci']['device_qualification']=='NOT_PERFORMED'
    assert [x['code'] for x in p['blockers']]==['POST_MERGE_PENDING']


@pytest.mark.parametrize('change',[
    {'head_sha':'d'*40},{'session_id':'f'*32},{'remote_mutation_performed':True},
    {'read_only':False},{'schema':'unknown'},
])
def test_c2_rejects_mismatched_or_mutating_qualification(change):
    q={**proof(),**change}
    p=project_delivery_status(native(),qualification=q)
    assert not p['ci']['required']['passed']
    assert not p['ci']['regression']['passed']
    assert p['state']=='BLOCKED'


@pytest.mark.parametrize('property,value',[
    ('shards_passed',23),('summary_passed',False),('artifacts_verified',24),
    ('jobs_passed',24),('attribution_proven',False),('workflow_path','another.yml'),
    ('source_sha','d'*40),
])
def test_c2_rejects_incomplete_regression_proofs(property,value):
    q=proof()
    q['repository_regression'][property]=value
    p=project_delivery_status(native(),qualification=q)
    assert p['ci']['required']['passed'] is True
    assert p['ci']['regression']['passed'] is False
    assert p['ci']['overall_acceptance']=='BLOCKED_OR_PENDING'


def test_c2_source_drift_blocks_stale_pass_and_replay():
    s=native(bound=False)
    s['resumable']=False
    s['intents']=[{'operation':'pr_create','state':'unresolved','id':'z'*32}]
    p=project_delivery_status(s,qualification=proof())
    assert not p['ci']['required']['passed']
    assert not p['ci']['regression']['passed']
    codes=[x['code'] for x in p['blockers']]
    assert codes[:2]==['SOURCE_BINDING_UNVERIFIED','MUTATION_RECONCILIATION_REQUIRED']
    assert p['next_safe_action'].startswith('Inspect the native source')
    assert 'reapply' in p['next_safe_action']




def test_c2_refuses_pr_namespace_spoofing_even_with_a_green_provider_proof():
    s=native()
    s['pr']['url']='https://github.com/attacker/devtool/pull/29'
    p=project_delivery_status(s,qualification=proof())
    assert p['pr']['state']=='NOT_OBSERVED'
    assert not p['ci']['required']['passed']
    assert 'PR_IDENTITY_UNVERIFIED' in [x['code'] for x in p['blockers']]


def test_c2_missing_run_attempt_cannot_advertise_regression_success():
    q=proof()
    del q['repository_regression']['attempt']
    p=project_delivery_status(native(),qualification=q)
    assert p['ci']['regression']['passed'] is False


def test_c2_commit_mismatch_and_overlay_digest_fail_closed():
    s=native()
    s['native_apply']['commit']='d'*40
    p=project_delivery_status(s)
    assert 'NATIVE_COMMIT_UNVERIFIED' in [x['code'] for x in p['blockers']]
    assert p['package']['verified']=='unknown'
    s=native()
    s['native_apply']['artifact_sha256']='d'*64
    p=project_delivery_status(s)
    assert 'PACKAGE_RECEIPT_UNVERIFIED' in [x['code'] for x in p['blockers']]


def test_c2_plain_and_rich_render_same_authoritative_status():
    p=project_delivery_status(native(),qualification=proof())
    plain=render_status_text(p)
    out=io.StringIO()
    Console(file=out,width=120,color_system=None,force_terminal=False).print(
        build_operator_view('operator_status',p,width=120))
    rich=out.getvalue()
    for item in ('transaction-c2','930','POST_MERGE_PENDING','744d'):
        if item=='744d': continue
        assert item in plain and item in rich
    assert 'NOT_PERFORMED' in plain and rich


def test_c2_textual_inmemory_preserves_same_projection_without_mutation():
    p=project_delivery_status(native(),qualification=proof())
    provider=InMemoryControlCenterProvider({'operator_status':p})
    dispatcher=RecordingControlActionDispatcher()
    first=provider.snapshot()['operator_status']
    assert first==p
    assert dispatcher.calls==[]
    assert render_status_text(first,compact=True).startswith('Devtool operator status:')


def git(repo: Path,*args: str)->str:
    return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True,text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path):
    root=tmp_path/'repo';root.mkdir()
    git(root,'init','-b','main');git(root,'config','user.name','Test')
    git(root,'config','user.email','test@example.invalid')
    (root/'README.md').write_text('x\n');git(root,'add','README.md');git(root,'commit','-m','base')
    git(root,'switch','-c','feature/test')
    git(root,'remote','add','origin','git@github.com:Mikeyphw/devtool.git')
    return root


def test_c2_latest_dashboard_finds_only_matching_source_bound_session(repo,tmp_path):
    state=tmp_path/'sessions'
    s=start(repo,state_root=state)
    ui=latest_for_repo(repo,state_root=state)
    assert ui['session']['id']==s['id']
    assert ui['target']['source_binding']=='matching'
    assert ui['ci']['required']['passed'] is False
    assert ui['state']=='BLOCKED'
    other=latest_for_repo(tmp_path/'other',state_root=state)
    assert other['session']['id'] is None
    assert other['state']=='UNVERIFIED'


def test_c2_corrupt_newest_session_refuses_older_success(repo,tmp_path):
    state=tmp_path/'sessions'
    original=start(repo,state_root=state)
    newer=start(repo,state_root=state)
    path=state/(newer['id']+'.json')
    obj=json.loads(path.read_text());obj['head_sha']='z'*40
    path.write_text(json.dumps(obj))
    p=latest_for_repo(repo,state_root=state)
    assert p['state']=='UNVERIFIED'
    assert p['session']['id'] is None
    assert 'unreadable' in p['blockers'][0]['reason']


def test_c2_missing_status_does_not_initialize_a_journal(tmp_path):
    state=tmp_path/'nothing'
    p=latest_for_repo(tmp_path/'repo',state_root=state)
    assert not state.exists()
    assert p['schema']==SCHEMA
    assert p['ci']['regression']['passed'] is False


def test_c2_session_cli_json_includes_projection_without_changing_legacy_keys(repo,tmp_path,monkeypatch,capsys):
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'xdg'))
    item=start(repo)
    args=SimpleNamespace(delivery_session_command='status',repo=str(repo),delivery_id=item['id'],json=True)
    assert run_delivery_command(args)==0
    result=json.loads(capsys.readouterr().out)
    assert result['id']==item['id']
    assert result['head_sha']==item['head_sha']
    assert result['operator_status']['schema']==SCHEMA
    assert result['operator_status']['ci']['required']['passed'] is False
    args.json=False
    assert run_delivery_command(args)==0
    text=capsys.readouterr().out
    assert item['id'] in text
    assert 'Required CI:' in text
    assert 'Next:' in text


@pytest.mark.skipif(__import__('importlib').util.find_spec('textual') is None,
                    reason='Optional Textual distribution unavailable')
def test_c2_real_textual_dashboard_renders_the_shared_readonly_projection(tmp_path):
    from textual.widgets import Static
    p=project_delivery_status(native(),qualification=proof())
    provider=InMemoryControlCenterProvider({'operator_status':p})
    actions=RecordingControlActionDispatcher()
    app=create_textual_control_center_app(repo_root=tmp_path,page='dashboard',provider=provider,
                                          dispatcher=actions,refresh_interval=10)
    async def run():
        async with app.run_test(size=(110,45)) as pilot:
            await pilot.pause()
            content=str(app.query_one('#dashboard-delivery',Static).render())
            assert 'transaction-c2' in content
            assert 'POST_MERGE_PENDING' in content
            assert 'Next:' in content
            assert actions.calls==[]
    asyncio.run(run())
