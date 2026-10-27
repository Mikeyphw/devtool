"""ACTIONS-UX02: exact run/attempt and source proof, including negative routes."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from devtool.actions_handoff import review_run, collect_verified
from devtool.github_actions import GitHubActionsError, actions_cli
from devtool.actions_dispatch import GitHubDispatchPreflightError
from devtool.actions_pipeline import _execute_isolated

RID = 'a' * 32
SHA = 'b' * 40
SLUG = 'Mikeyphw/devtool'


class FakeClient:
    def __init__(self):
        self.head = SHA
        self.workflow = 77
        self.attempt = 2
        self.status = 'completed'
        self.conclusion = 'success'
        self.branch = 'main'
        self.event = 'workflow_dispatch'
        self.calls = []
        self.mixed_job = False
        self.artifact_names = ['candidate']

    def get_run(self, slug, identity):
        self.calls.append(('run', slug, identity))
        return {'id': 4321, 'repository': {'full_name': SLUG}, 'head_sha': self.head,
                'workflow_id': self.workflow, 'run_attempt': self.attempt,
                'status': self.status, 'conclusion': self.conclusion,
                'head_branch': self.branch, 'event': self.event}

    def run_attempt_jobs(self, slug, identity, attempt):
        self.calls.append(('attempt-jobs', attempt))
        return [{'id': 9001, 'run_attempt': 5 if self.mixed_job else attempt,
                 'status': 'completed', 'conclusion': 'success', 'steps': []}]

    def run_jobs(self, slug, identity):
        self.calls.append(('latest-jobs', identity))
        return []

    def run_artifacts(self, slug, identity):
        self.calls.append(('artifacts', identity))
        return [{'id': 333 + i, 'name': name, 'size_in_bytes': 30,
                 'digest': 'sha256:' + '1' * 64,
                 'workflow_run': {'id': identity, 'head_sha': self.head, 'run_attempt': self.attempt}}
                for i, name in enumerate(self.artifact_names)]


def receipt(root: Path, *, remote=True, slug=SLUG):
    root.mkdir(parents=True,exist_ok=True)
    path = root / (RID + '.json')
    path.write_text(json.dumps({'schema': 1,'request_id': RID, 'repository': slug,
                                'pre_dispatch_sha': SHA, 'workflow_id': 77, 'ref': 'main',
                                'run_id': 4321 if remote else None, 'state': 'correlated' if remote else 'accepted-uncorrelated'}))
    return path


def test_review_exact_success_not_release(tmp_path):
    receipt(tmp_path)
    client = FakeClient()
    x = review_run(client, RID, receipts=tmp_path)
    assert x['state'] == 'succeeded' and x['remote_verified'] and not x['qualified_release']
    assert not x['artifact_verified'] and x['run_attempt'] == 2
    assert x['identity_checks']['jobs_exact']
    assert client.calls == [('run',SLUG,4321), ('attempt-jobs',2)]


@pytest.mark.parametrize('field,value', [
    ('head','c'*40),('workflow',99),('branch','else'),
    ('event','push'),('attempt',0),('mixed_job',True),
    ('status','in_progress'),('conclusion','failure')])
def test_review_blocks_unproven_run(tmp_path,field,value):
    receipt(tmp_path)
    client=FakeClient()
    setattr(client,field,value)
    if field=='mixed_job':
        with pytest.raises(GitHubActionsError):review_run(client,RID,receipts=tmp_path)
        return
    result=review_run(client,RID,receipts=tmp_path)
    assert not result['remote_verified'] and not result['qualified_release']
    assert result['state']!='succeeded'


def test_uncorrelated_no_network_or_automatic_retry(tmp_path):
    receipt(tmp_path,remote=False)
    x=review_run(None,RID,receipts=tmp_path)
    assert x['state']=='uncorrelated' and x['run_id'] is None
    assert not x['automatic_retry']
    with pytest.raises(GitHubActionsError):collect_verified(None,RID,'candidate',tmp_path,yes=True,receipts=tmp_path)


def test_repository_and_symlink_fail_closed(tmp_path):
    path=receipt(tmp_path)
    with pytest.raises(GitHubActionsError):review_run(None,RID,repository='Other/repo',receipts=tmp_path)
    path.unlink()
    path.symlink_to('/etc/passwd')
    with pytest.raises(GitHubActionsError):review_run(None,RID,receipts=tmp_path)


def test_collect_rejects_unreviewed_and_duplicate_before_mutation(tmp_path,monkeypatch):
    receipt(tmp_path)
    c=FakeClient()
    with pytest.raises(GitHubActionsError):collect_verified(c,RID,'candidate',tmp_path,yes=False,receipts=tmp_path)
    assert not c.calls
    c.head='d'*40
    with pytest.raises(GitHubActionsError):collect_verified(c,RID,'candidate',tmp_path,yes=True,receipts=tmp_path)
    assert ('artifacts',4321) not in c.calls
    c.head=SHA
    c.artifact_names=['candidate','candidate']
    with pytest.raises(GitHubActionsError):collect_verified(c,RID,'candidate',tmp_path,yes=True,receipts=tmp_path)
    assert not (tmp_path/'.devtool/artifacts/github-downloads').exists()


def test_collect_reuses_valid_registry_and_checks_again(tmp_path,monkeypatch):
    receipt(tmp_path)
    c=FakeClient()
    seen=[]
    def verify(*args):
        seen.append('verify')
        return {'archive_verified':True}
    monkeypatch.setattr('devtool.actions_handoff.verify_download',verify)
    x=collect_verified(c,RID,'candidate',tmp_path,yes=True,receipts=tmp_path)
    assert x['archive_verified'] and x['existing_archive_reused'] and not x['qualified_release']
    assert seen==['verify']
    assert sum(kind=='run' for kind,*_ in c.calls)>=3


def test_collect_missing_downloads_once_then_verifies(tmp_path,monkeypatch):
    receipt(tmp_path)
    c=FakeClient()
    calls=[]
    def verify(*args):
        calls.append('verify')
        if len(calls)==1:raise GitHubActionsError('No registered download for this GitHub artifact')
        return {'archive_verified':True}
    monkeypatch.setattr('devtool.actions_handoff.verify_download',verify)
    monkeypatch.setattr('devtool.actions_handoff.download_artifact',lambda *args:calls.append('download'))
    x=collect_verified(c,RID,'candidate',tmp_path,yes=True,receipts=tmp_path)
    assert calls==['verify','download','verify']
    assert not x['existing_archive_reused']


def test_collect_bad_registry_is_never_treated_as_missing(tmp_path,monkeypatch):
    receipt(tmp_path)
    c=FakeClient()
    monkeypatch.setattr('devtool.actions_handoff.verify_download',lambda *_:(_ for _ in ()).throw(GitHubActionsError('Registered artifact archive integrity check failed')))
    monkeypatch.setattr('devtool.actions_handoff.download_artifact',lambda *_: (_ for _ in ()).throw(AssertionError('must not download')))
    with pytest.raises(GitHubActionsError,match='integrity'):
        collect_verified(c,RID,'candidate',tmp_path,yes=True,receipts=tmp_path)


def test_pre_dispatch_local_source_frozen_and_no_post(tmp_path,monkeypatch):
    invoked=[]
    monkeypatch.setattr('devtool.actions_pipeline._source',lambda root:SHA)
    def dispatch(*args,**kw):
        invoked.append((args,kw))
        assert kw.get('expected_source_sha') == SHA
        raise GitHubDispatchPreflightError('Remote ref moved; zero POSTs')
    monkeypatch.setattr('devtool.actions_pipeline.dispatch_workflow',dispatch)
    record={'kind':'github-dispatch','state':'running','detail':''}
    state,extra=_execute_isolated(object(),SLUG,tmp_path,
                 {'id':'ci','kind':'github-dispatch','workflow':'ci.yml','ref':'main','inputs':{}},
                 record, {'nodes':{}}, SHA)
    assert invoked and state=='failure' and 'zero POSTs' in extra['detail']


def test_dispatch_unknown_remains_uncertain(tmp_path,monkeypatch):
    monkeypatch.setattr('devtool.actions_pipeline._source',lambda root:SHA)
    monkeypatch.setattr('devtool.actions_pipeline.dispatch_workflow',
        lambda *args,**kw: (_ for _ in ()).throw(GitHubActionsError('Unknown POST outcome')))
    state,_=_execute_isolated(object(),SLUG,tmp_path,
                 {'id':'ci','kind':'github-dispatch','workflow':'ci.yml','ref':'main','inputs':{}},
                 {}, {'nodes':{}}, SHA)
    assert state=='uncertain'


def test_parser_and_cli_route(monkeypatch,tmp_path):
    from devtool.cli import build_parser
    p=build_parser()
    a=p.parse_args(['actions','review','--request-id',RID,'--json'])
    assert a.actions_command=='review' and a.request_id==RID
    b=p.parse_args(['actions','collect','--request-id',RID,'--artifact-name','candidate','--yes'])
    assert b.yes and b.artifact_name=='candidate'
    monkeypatch.setattr('devtool.github_actions.resolve_token',lambda *_,**__:('fake','test'))
    monkeypatch.setattr('devtool.actions_handoff.review_run',lambda *args,**kw:{'state':'uncorrelated','qualified_release':False})
    x=actions_cli(SimpleNamespace(actions_command='review',request_id=RID,github_repo='',auth='none'),tmp_path)
    assert x['state']=='uncorrelated'
