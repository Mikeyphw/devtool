"""S4 current contract: exact GitHub run+attempt corroboration and safe diagnostics."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import subprocess

import pytest

from devtool import delivery_ci_evidence as evidence, delivery_github as delivery
from devtool.delivery_session import start, record_pr_observation, status
from devtool.github_actions import GitHubActionsError


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def ctx(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.invalid')
    (root / 'test.txt').write_text('baseline')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'baseline')
    git(root, 'switch', '-c', 'feature/s4')
    git(root, 'remote', 'add', 'origin', 'git@github.com:Mikeyphw/devtool.git')
    sessions = tmp_path / 'sessions'
    item = start(root, state_root=sessions)
    record_pr_observation(item['id'], {'number':17, 'head':item['head'],
        'base':item['base'], 'head_sha':item['head_sha'],
        'url':'https://github.com/Mikeyphw/devtool/pull/17'}, state_root=sessions)
    return item, sessions


def run(item, name='Hosted', oid=117, rid=111, status='completed', conclusion='success'):
    return {'id':rid,'workflow_id':oid,'name':name,
            'event':'pull_request','head_sha':item['head_sha'],
            'head_branch':item['head'],'run_attempt':1,
            'repository':{'full_name':item['repository']},
            'pull_requests':[{'number':17,'head':{'sha':item['head_sha']}}],
            'status':status,'conclusion':conclusion}


class Client:
    def __init__(self, item):
        self.item=item
        self.rows=[run(item)]
        self.live=None
        self.jobs=[{'id':211,'run_id':111,'run_attempt':1,
                    'name':'unit','status':'completed','conclusion':'success'}]
        self.run_calls=[]
        self.job_calls=[]
    def _get(self, path):
        if path.endswith('/pulls/17'):
            return {'number':17,'state':'open',
                'head':{'sha':self.item['head_sha'],'ref':self.item['head'],
                            'label':self.item['repository'].split('/')[0]+':'+self.item['head'],
                        'repo':{'full_name':self.item['repository']}},
                'base':{'ref':self.item['base'],
                        'repo':{'full_name':self.item['repository']}}}
        if path.endswith('/merge'):
            return {'object':{'sha':'b'*40}}
        return {'commit':{'sha':self.item['head_sha']}}
    def runs(self, slug, *, limit):
        assert slug==self.item['repository'] and limit==100
        return list(self.rows)
    def get_run(self, slug, run_id):
        self.run_calls.append(run_id)
        if isinstance(self.live, BaseException):
            raise self.live
        return deepcopy(self.live or self.rows[0])
    def run_attempt_jobs(self, slug, run_id, attempt):
        self.job_calls.append((run_id,attempt))
        return deepcopy(self.jobs)


def observe(ctx, client, **kw):
    item, folder=ctx
    return delivery.observe_ci(item['id'],state_root=folder,client=client,
                               required=('Hosted',),workflow_ids={'Hosted':117},
                               required_event='pull_request',policy_sha256='a'*64,
                               checks=1,**kw)


def test_positive_pass_is_exact_run_and_exact_attempt(ctx):
    item, root=ctx
    c=Client(item)
    data=observe(ctx,c)
    assert data['state']=='REQUIRED_CHECKS_PASSED'
    assert data['release_qualified'] is False
    assert data['exact_run_evidence']['verified'] is True
    assert c.run_calls==[111] and c.job_calls==[(111,1)]
    assert status(item['id'],state_root=root)['ci']['exact_run_evidence']['runs'][0]['jobs_scope']=='exact-run-attempt'


@pytest.mark.parametrize('change', [
    lambda c: setattr(c, 'live', dict(c.rows[0], workflow_id=999)),
    lambda c: setattr(c, 'live', dict(c.rows[0], head_sha='a'*40)),
    lambda c: setattr(c, 'live', dict(c.rows[0], run_attempt=2)),
    lambda c: setattr(c, 'jobs', []),
    lambda c: setattr(c, 'jobs', [dict(c.jobs[0], run_attempt=2)]),
    lambda c: setattr(c, 'jobs', [dict(c.jobs[0], run_id=777)]),
    lambda c: setattr(c, 'jobs', [dict(c.jobs[0], status='in_progress')]),
    lambda c: setattr(c, 'jobs', [dict(c.jobs[0], conclusion='failure')]),
    lambda c: setattr(c, 'live', GitHubActionsError('rate limit')),
])
def test_failed_corrob_does_not_qualify(ctx, change):
    item, root=ctx
    c=Client(item)
    change(c)
    result=observe(ctx,c)
    assert result['state']=='CHECKS_EVIDENCE_PENDING'
    assert result['checks_qualified'] is False
    assert result['exact_run_evidence']['issues']
    assert status(item['id'],state_root=root)['ci']['checks_qualified'] is False


def test_completed_list_but_live_provider_rerun_does_not_qualify(ctx):
    item, folder = ctx
    c=Client(item)
    c.live=run(item,status='in_progress',conclusion=None)
    result=observe(ctx,c)
    assert result['state']=='CHECKS_EVIDENCE_PENDING'
    assert result['checks_qualified'] is False
    assert result['exact_run_evidence']['issues'][0]['reason']=='run-not-successful'
    assert c.job_calls==[]


def test_mutable_list_to_live_metadata_drift_still_qualifies_same_attempt(ctx):
    item, folder=ctx
    c=Client(item)
    c.rows[0]['updated_at']='2026-11-22T01:00:00Z'
    c.live=dict(c.rows[0],updated_at='2026-11-22T01:02:00Z')
    result=observe(ctx,c)
    assert result['state']=='REQUIRED_CHECKS_PASSED'
    assert result['exact_run_evidence']['verified'] is True
    assert c.job_calls==[(111,1)]


def test_failure_diagnostics_are_exact_attempt_and_do_not_copy_logs(ctx):
    item, root=ctx
    c=Client(item)
    c.rows=[run(item,status='completed',conclusion='failure')]
    c.jobs=[{'id':211,'run_id':111,'run_attempt':1,'name':'Unit tests',
             'status':'completed','conclusion':'failure',
             'steps':[{'name':'pytest regression', 'conclusion':'failure'}]}]
    result=observe(ctx,c)
    assert result['state']=='CHECKS_FAILED'
    detailed=result['failures'][0]['detailed_jobs'][0]
    assert detailed['classification']=='possible-test-failure'
    assert detailed['steps'][0]['name']=='pytest regression'
    assert detailed['url'].startswith('https://github.com/Mikeyphw/devtool/actions/runs/111/attempts/1/job/211')
    assert detailed['log_content_collected'] is False
    assert c.job_calls==[(111,1),(111,1)]


def test_job_excerpts_not_fetched_and_oversized_refused(ctx):
    item,_=ctx
    c=Client(item)
    c.jobs=c.jobs*200
    result=observe(ctx,c)
    assert result['state']=='CHECKS_EVIDENCE_PENDING'
    assert 'incomplete-job-inventory' in [e['reason'] for e in result['exact_run_evidence']['issues']]


def test_evidence_can_recover_after_delayed_provider_jobs(ctx):
    item,folder=ctx
    c=Client(item)
    old=list(c.jobs)
    c.jobs=[]
    def after_first(delay):
        c.jobs=old
    result=delivery.observe_ci(item['id'],state_root=folder,client=c,
                               required=('Hosted',),workflow_ids={'Hosted':117},
                               required_event='pull_request',policy_sha256='a'*64,
                               checks=2,interval=1,sleep=after_first)
    assert result['state']=='REQUIRED_CHECKS_PASSED'
    assert [h['state'] for h in result['history']]==['CHECKS_EVIDENCE_PENDING','REQUIRED_CHECKS_PASSED']
