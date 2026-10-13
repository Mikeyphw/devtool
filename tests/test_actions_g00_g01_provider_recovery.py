"""G0/G1 regressions: 204 correlation is impossible without a unique server receipt.

All HTTP is injected. These tests neither contact GitHub nor require secrets.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
import io
import pytest

from devtool.actions_dispatch import _durable_record, inspect_uncorrelated_dispatch
from devtool.actions_runs import _safe_receipt, snapshot
from devtool.github_actions import GitHubActionsClient, GitHubActionsError, actions_cli

REPO = 'owner/repo'
SHA = 'a'*40
REF = 'main'
REQUEST = 'e'*32
CREATED = datetime(2026, 10, 13, 3, 0, tzinfo=timezone.utc)

class Response:
    def __init__(self, payload, status=200):
        self.status = status
        self.headers = {}
        self.stream = io.BytesIO(json.dumps(payload).encode())
    def __enter__(self):return self
    def __exit__(self, *args):return None
    def read(self, n):return self.stream.read(n)

class Opener:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []
    def open(self, request, timeout):
        self.requests.append(request)
        if not self.replies:raise AssertionError('Unexpected HTTP request')
        response = self.replies.pop(0)
        if isinstance(response, Exception):raise response
        return response

def receipt(root: Path, **kwargs):
    data = {'schema':1,'request_id':REQUEST,'repository':REPO,
            'workflow_id':11,'ref':REF,'pre_dispatch_sha':SHA,
            'run_id':None,'state':'accepted-uncorrelated',
            'created_at':CREATED.isoformat(),'input_names':['mode']}
    data.update(kwargs)
    p = root / (REQUEST + '.json')
    _durable_record(p, data)
    return p

def remote(run_id: int, *, branch=REF, sha=SHA, workflow=11, event='workflow_dispatch',
           offset=1, attempt=1, repo=REPO):
    return {'id':run_id,'repository':{'full_name':repo},'workflow_id':workflow,
            'head_sha':sha,'head_branch':branch,'event':event,'run_attempt':attempt,
            'created_at':(CREATED+timedelta(seconds=offset)).isoformat(),
            'status':'completed','conclusion':'success'}

def client(*runs):
    return GitHubActionsClient('sensitive',opener=Opener(Response({'workflow_runs':list(runs)})))

def test_uncorrelated_204_returns_possible_candidates_without_auto_binding(tmp_path):
    path = receipt(tmp_path)
    before = path.read_bytes()
    obj = inspect_uncorrelated_dispatch(client(remote(41),remote(42)),REQUEST,state_root=tmp_path)
    assert [c['run_id'] for c in obj['candidates']] == [41,42]
    assert obj['run_id'] is None and obj['candidate_count'] == 2
    assert not obj['correlated'] and not obj['automatically_reconciled']
    assert not obj['safe_to_redispatch'] and not obj['qualified_release']
    assert path.read_bytes() == before
    with pytest.raises(GitHubActionsError,match='guessing'):
        _safe_receipt(REQUEST,tmp_path)

def test_uncorrelated_candidates_filtered_by_exact_identity_and_time(tmp_path):
    receipt(tmp_path)
    options = [remote(1),remote(2,branch='other'),remote(3,sha='b'*40),
               remote(4,workflow=12),remote(5,event='push'),
               remote(6,offset=1300),remote(7,offset=-90),
               remote(8,repo='evil/repo')]
    # Source-level identity checks exclude all except #1; a foreign repository
    # must also be excluded even if a malicious API fixture embeds one.
    obj = inspect_uncorrelated_dispatch(client(*options),REQUEST,state_root=tmp_path)
    assert [c['run_id'] for c in obj['candidates']] == [1]

def test_unresolved_uncertain_post_can_be_inspected_without_mutation(tmp_path):
    receipt(tmp_path,state='dispatch-outcome-unknown')
    out = inspect_uncorrelated_dispatch(client(remote(5)),REQUEST,state_root=tmp_path)
    assert out['dispatch_state'] == 'dispatch-outcome-unknown'
    assert out['candidate_count'] == 1

def test_rejected_or_already_correlated_receipts_cannot_be_inspected(tmp_path):
    receipt(tmp_path,state='rejected-by-api')
    with pytest.raises(GitHubActionsError,match='unresolved'):
        inspect_uncorrelated_dispatch(client(remote(5)),REQUEST,state_root=tmp_path)
    receipt(tmp_path,run_id=5,state='accepted-correlated')
    with pytest.raises(GitHubActionsError,match='already correlated'):
        inspect_uncorrelated_dispatch(client(remote(5)),REQUEST,state_root=tmp_path)

def test_receipt_symlink_rejected_even_on_readonly_candidate_inspection(tmp_path):
    p=receipt(tmp_path)
    target=tmp_path/'copy.json';p.rename(target);p.symlink_to(target)
    with pytest.raises(GitHubActionsError,match='not found'):
        inspect_uncorrelated_dispatch(client(remote(5)),REQUEST,state_root=tmp_path)

def test_resume_cli_returns_uncorrelated_candidates_without_post(monkeypatch,tmp_path):
    receipt(tmp_path)
    opener=Opener(Response({'workflow_runs':[remote(15)]}))
    monkeypatch.setattr('devtool.actions_dispatch._state_root',lambda:tmp_path)
    # The source of the original state root is in the runs module.
    monkeypatch.setattr('devtool.actions_runs._state_root',lambda:tmp_path)
    monkeypatch.setattr('devtool.github_actions.resolve_token',lambda *a,**k:('sensitive','fake'))
    monkeypatch.setattr('devtool.github_actions.GitHubActionsClient',lambda *a,**k:GitHubActionsClient('sensitive',opener=opener))
    args=SimpleNamespace(actions_command='resume',request_id=REQUEST,github_repo='',auth='none')
    out=actions_cli(args,tmp_path)
    assert out['candidate_count']==1 and out['correlated'] is False
    assert len(opener.requests)==1 and opener.requests[0].get_method()=='GET'
    args.github_repo='evil/repo'
    with pytest.raises(GitHubActionsError,match='another repository'):
        actions_cli(args,tmp_path)

def test_job_attempt_fetch_scoped_url_and_latest_snapshot(tmp_path):
    run = remote(33,attempt=3)
    job = {'id':99,'run_id':33,'run_attempt':3,'steps':[]}
    opener=Opener(Response(run),Response({'jobs':[job]}))
    out=snapshot(GitHubActionsClient('sensitive',opener=opener),REPO,33,with_jobs=True)
    assert out['jobs_scope']=='exact-run-attempt'
    assert out['jobs'][0]['run_attempt']==3
    assert '/actions/runs/33/attempts/3/jobs?' in opener.requests[1].full_url
    assert len(opener.requests)==2

def test_job_attempt_mismatch_rejected(tmp_path):
    opener=Opener(Response(remote(33,attempt=2)),Response({'jobs':[{'id':100,'run_attempt':1,'steps':[]}]}))
    with pytest.raises(GitHubActionsError,match='different workflow attempt'):
        snapshot(GitHubActionsClient(opener=opener),REPO,33,with_jobs=True)

def test_dispatch_source_binding_rejects_foreign_workflow_and_branch(tmp_path):
    from devtool.actions_dispatch import _durable_record
    for field, replacement in [('workflow_id',14),('head_branch','other')]:
        p = receipt(tmp_path,run_id=33,state='accepted-correlated')
        data = json.loads(p.read_text())
        # remote() uses `workflow`, not `workflow_id` keyword
        if field=='workflow_id':bad=remote(33,workflow=replacement)
        else:bad=remote(33,branch=replacement)
        out=snapshot(GitHubActionsClient(opener=Opener(Response(bad))),REPO,33,p,data)
        assert out['source_binding']=='not-confirmed'
        assert out['warning']

def test_repository_permission_probe_is_a_hint_not_authorization():
    payload={'full_name':REPO,'id':6,'default_branch':'main',
             'permissions':{'push':False,'pull':True,'administration':'sensitive'}}
    obj=GitHubActionsClient(opener=Opener(Response(payload))).repository(REPO)
    assert obj['permissions']=={'push':False,'pull':True}
