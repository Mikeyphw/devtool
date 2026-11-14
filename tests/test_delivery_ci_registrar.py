"""Current S4 registrar acceptance: strict event, identity, and visibility tests."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from pathlib import Path
import subprocess
from copy import deepcopy

import pytest

from devtool import delivery_ci_registrar as reg, delivery_github as github
from devtool.delivery_session import start, record_pr_observation, status, DeliverySessionError
from devtool.github_actions import GitHubActionsError


def git(root, *args):
    p=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True)
    assert p.returncode==0,(args,p.stderr)
    return p.stdout.strip()


@pytest.fixture
def ctx(tmp_path):
    root=tmp_path/'repo'; root.mkdir()
    git(root,'init','-b','main'); git(root,'config','user.name','Test')
    git(root,'config','user.email','test@example.invalid')
    name='Required Test Workflow'
    workflow=root/'.github/workflows/test.yml'
    workflow.parent.mkdir(parents=True)
    workflow.write_text('name: Required Test Workflow\non:\n  pull_request:\n    branches: [main]\n  push:\n    branches: [feature/tests]\n  workflow_dispatch:\njobs:\n  check:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo yes\n')
    (root/'README.md').write_text('main\n')
    git(root,'add','.');git(root,'commit','-m','initial')
    git(root,'switch','-c','feature/tests')
    git(root,'remote','add','origin','git@github.com:Mikeyphw/devtool.git')
    (root/'README.md').write_text('current\n')
    git(root,'add','.');git(root,'commit','-m','source')
    state=tmp_path/'state'
    item=start(root,state_root=state)
    record_pr_observation(item['id'],{'number':17,'head':item['head'],
       'base':item['base'],'head_sha':item['head_sha'],
       'url':'https://github.com/Mikeyphw/devtool/pull/17'},state_root=state)
    return item,state,workflow


def row(item, *, rid=101, event='pull_request', attempt=1, created_at=None,
        sha=None, status='completed', conclusion='success', workflow_id=77):
    return {'id':rid,'name':'Required Test Workflow','workflow_id':workflow_id,
      'event':event,'run_attempt':attempt,'head_sha':sha or item['head_sha'],
      'head_branch':item['head'],'created_at':created_at or item['created_at'],
      'status':status,'conclusion':conclusion,
      'repository':{'full_name':item['repository']},
      'pull_requests':[{'number':17,'head':{'sha':item['head_sha']}}]}


class Client:
    def __init__(self,item,rows=None):
        self.item=item;self.rows=list(rows or [])
        self._token='fake';self.calls=0
        self.fail=False;self.force_head=None;self.merge_sha='a'*40
        self.workflow_name='Required Test Workflow'
        self.workflow_path='.github/workflows/test.yml'
    def workflow(self,slug,identity):
        assert slug==self.item['repository'] and identity=='77'
        return {'id':77,'name':self.workflow_name,'path':self.workflow_path,'state':'active'}
    def _get(self,path):
        if path.endswith('/branches/main'):
            return {'protected':False}
        if path.endswith('/pulls/17'):
            return {'number':17,'state':'open',
                    'head':{'sha':self.item['head_sha'],'ref':self.item['head'],
                            'repo':{'full_name':self.item['repository']}},
                    'base':{'ref':self.item['base'],'repo':{'full_name':self.item['repository']}}}
        if path.endswith('/merge'):
            return {'object':{'sha':self.merge_sha}}
        return {'commit':{'sha':self.force_head or self.item['head_sha']}}
    def branch_rules(self,slug,base):
        assert slug==self.item['repository'] and base=='main'
        return []
    def _paged(self,slug,path,key,*,max_results):
        assert max_results==500 and key=='workflow_runs'
        self.calls+=1
        if self.fail:raise GitHubActionsError('rate limited')
        return deepcopy(self.rows)
    def get_run(self,slug,identity):
        return next(deepcopy(x) for x in self.rows if x['id']==identity)
    def run_attempt_jobs(self,slug,identity,attempt):
        return [{'id':400+identity,'run_id':identity,'run_attempt':attempt,
                 'name':'tests','status':'completed','conclusion':'success'}]


def strict(ctx, cli, **kwargs):
    item,state,_=ctx
    return github.observe_ci(item['id'],client=cli,state_root=state,
         required=('Required Test Workflow',), workflow_ids={'Required Test Workflow':77},
         required_event='pull_request',policy_sha256='b'*64,
         strict_registration=True, checks=1, **kwargs)


def test_strict_positive_real_git_trigger_and_exact_attempt(ctx):
    item,state,_=ctx
    cli=Client(item,[row(item)])
    result=strict(ctx,cli)
    assert result['state']=='REQUIRED_CHECKS_PASSED'
    assert result['registration']['event']=='pull_request'
    assert result['registration']['run_count']==1
    assert result['exact_run_evidence']['verified'] is True
    assert result['release_qualified'] is False
    assert status(item['id'],state_root=state)['ci']['registration']['deadline_at']


def test_delayed_registration_and_out_of_order_listing(ctx):
    item,_,_=ctx
    cli=Client(item)
    sequence=[[],[row(item,rid=101),row(item,rid=10,workflow_id=12)]]
    def pages(*a,**kw):
        cli.rows = sequence.pop(0)
        return list(cli.rows)
    cli._paged=pages
    result=github.observe_ci(item['id'],client=cli,state_root=ctx[1],
        required=('Required Test Workflow',),workflow_ids={'Required Test Workflow':77},
        required_event='pull_request',policy_sha256='b'*64,strict_registration=True,
        checks=2,interval=1,sleep=lambda *_:None)
    assert result['state']=='REQUIRED_CHECKS_PASSED'
    assert [x['state'] for x in result['history']]==['WAITING_REGISTRATION','REQUIRED_CHECKS_PASSED']


def test_concurrent_same_branch_runs_are_ambiguous(ctx):
    item,_,_=ctx
    cli=Client(item,[row(item,rid=10),row(item,rid=11)])
    result=strict(ctx,cli)
    assert result['state']=='BLOCKED_AMBIGUOUS'
    assert result['registration']['ambiguous']==['Required Test Workflow']
    assert not result['checks_qualified']


def test_old_or_future_run_never_qualifies(ctx):
    item,_,_=ctx
    start_time=reg._date(item['created_at'])
    cli=Client(item,[row(item,created_at=(start_time-timedelta(hours=3)).isoformat())])
    assert strict(ctx,cli)['state']=='WAITING_REGISTRATION'
    cli.rows=[row(item,created_at=(start_time+timedelta(hours=1)).isoformat())]
    assert strict(ctx,cli)['state']=='WAITING_REGISTRATION'


def test_missing_only_times_out_after_horizon(ctx):
    item,_,_=ctx
    cli=Client(item)
    began=reg._date(item['created_at'])
    assert strict(ctx,cli,clock=lambda:began+timedelta(seconds=10))['state']=='WAITING_REGISTRATION'
    expired=strict(ctx,cli,clock=lambda:began+timedelta(seconds=1801))
    assert expired['state']=='REGISTRATION_TIMED_OUT'
    assert expired['missing']==['Required Test Workflow']
    assert not expired['checks_qualified']


def test_wrong_event_id_or_source_is_not_selected(ctx):
    item,_,_=ctx
    cli=Client(item,[row(item,event='workflow_dispatch'),row(item,workflow_id=99),
                     row(item,sha='d'*40,rid=99)])
    assert strict(ctx,cli)['state']=='WAITING_REGISTRATION'


def test_synthetic_merge_only_when_current(ctx):
    item,_,_=ctx
    cli=Client(item,[row(item,sha='a'*40)])
    assert strict(ctx,cli)['state']=='REQUIRED_CHECKS_PASSED'
    cli.merge_sha='b'*40
    assert strict(ctx,cli)['state']=='WAITING_REGISTRATION'


def test_pr_head_drift_refuses_before_passing(ctx):
    item,_,_=ctx
    cli=Client(item,[row(item)])
    cli.force_head='c'*40
    with pytest.raises(DeliverySessionError,match='Remote branch moved'):
        strict(ctx,cli)


def test_trigger_drift_or_missing_workflow_blocks(ctx):
    item,_,workflow=ctx
    cli=Client(item,[row(item)])
    cli.workflow_name='Wrong name'
    with pytest.raises(DeliverySessionError,match='name/ID'):
        strict(ctx,cli)
    cli.workflow_name='Required Test Workflow'
    cli.workflow_path='../secrets.yml'
    with pytest.raises(DeliverySessionError,match='escaped'):
        strict(ctx,cli)


def test_required_not_expected_branch_blocks_not_skips(ctx):
    item,_,wf=ctx
    # The workflow bytes are committed at current HEAD; alter via commit in
    # an independent Git fixture so the active session SHA is not invalidated.
    cli=Client(item)
    source=reg.workflow_expectations(cli,item,{'Required Test Workflow':77},'pull_request')
    assert source=={'Required Test Workflow':'expected'}
    source=reg.workflow_expectations(cli,item,{'Required Test Workflow':77},'push')
    assert source=={'Required Test Workflow':'expected'}


def test_unsupported_conditional_paths_not_falsely_skipped(ctx,monkeypatch):
    item,_,_=ctx
    cli=Client(item,[row(item)])
    original=reg._workflow_yaml
    def filtered(*a):
        doc=deepcopy(original(*a))
        triggers=doc.get('on',doc.get(True))
        triggers['pull_request']['paths']=['docs/**']
        return doc
    monkeypatch.setattr(reg,'_workflow_yaml',filtered)
    result=strict(ctx,cli)
    assert result['state']=='BLOCKED_CONDITIONAL'
    assert not result['checks_qualified']


def test_paginated_but_truncated_and_rate_limit_fail_closed(ctx):
    item,_,_=ctx
    cli=Client(item,[row(item)])
    cli._paged=lambda *a,**kw:[row(item)]*500
    with pytest.raises(DeliverySessionError,match='pagination'):
        strict(ctx,cli)
    cli=Client(item,[row(item)]);cli.fail=True
    result=strict(ctx,cli)
    assert result['state']=='REGISTRATION_PROVIDER_UNAVAILABLE'
    assert result['checks_qualified'] is False
    assert result['registration']['phase']=='workflow-run-pages'


def test_dispatch_204_is_unlinked_even_if_a_matching_sha_is_visible(ctx):
    item,_,_=ctx
    cli=Client(item,[row(item,event='workflow_dispatch')])
    w=reg.registration_window(item,1800)
    candidate=reg.select_registration(cli.rows,item,event='workflow_dispatch',
         required={'Required Test Workflow':77},expected={'Required Test Workflow':'expected'},
         merge_sha=None,pr_info=None,window=w,
         match_run=lambda x,*a: {'id':x['id'],'attempt':x['run_attempt'],'name':x['name']})
    assert candidate['dispatch_uncorrelated'] is True
    assert candidate['missing']==['Required Test Workflow']


def test_run_pagination_requires_complete_inventory(ctx):
    item,_,_=ctx
    cli=Client(item,[row(item)])
    assert len(reg.collect_runs(cli,item['repository']))==1
    class ShortClient:
        def runs(self,*a,**kw):return [row(item)]*100
    with pytest.raises(DeliverySessionError,match='truncated'):
        reg.collect_runs(ShortClient(),item['repository'])


def test_backoff_bounded_and_repeatable():
    delays=[reg.next_delay(8,i,'session') for i in range(8)]
    assert 6<=delays[0]<=10
    assert all(1<=x<=120 for x in delays)
    assert delays==[reg.next_delay(8,i,'session') for i in range(8)]


def test_branch_rules_verifies_required_actions_job_context(ctx):
    item,_,_=ctx
    cli=Client(item,[row(item)])
    original=cli._get
    def protected(path):
        if path.endswith('/branches/main'):return {'protected':True}
        if path.endswith('/required_status_checks'):
            return {'contexts':['tests'], 'checks':[{'context':'tests','app_id':15368}]}
        return original(path)
    cli._get=protected
    assert strict(ctx,cli)['state']=='REQUIRED_CHECKS_PASSED'
    cli.run_attempt_jobs=lambda *a: [{'id':1,'run_id':101,'run_attempt':1,
                                     'name':'tests','status':'completed','conclusion':'failure'}]
    # Exact job corroboration already blocks before branch protection can pass.
    assert strict(ctx,cli)['state']=='CHECKS_EVIDENCE_PENDING'


def test_extra_ruleset_context_blocks_green_workflow(ctx):
    item,_,_=ctx
    cli=Client(item,[row(item)])
    cli.branch_rules=lambda *a:[{'type':'required_status_checks', 'parameters': {
        'required_status_checks':[{'context':'unrelated-job','integration_id':15368}]}}]
    result=strict(ctx,cli)
    assert result['state']=='BRANCH_RULES_UNVERIFIED'
    assert 'not fully satisfied' in result['branch_rules_issue']
    assert not result['checks_qualified']


def test_provider_ruleset_outage_is_never_a_green_ci_receipt(ctx):
    item,state,_=ctx
    cli=Client(item,[row(item)])
    def forbidden(*a):raise GitHubActionsError('GitHub API access denied')
    cli.branch_rules=forbidden
    result=strict(ctx,cli)
    assert result['state']=='BRANCH_RULES_UNVERIFIED'
    assert result['release_qualified'] is False
    assert status(item['id'],state_root=state)['ci']['checks_qualified'] is False


def test_protected_branch_unknown_status_blocks(ctx):
    item,_,_=ctx
    cli=Client(item,[row(item)])
    orig=cli._get
    cli._get=lambda path: {'is_protected': False} if path.endswith('/branches/main') else orig(path)
    result=strict(ctx,cli)
    assert result['state']=='BRANCH_RULES_UNVERIFIED'


def test_protected_external_app_context_refuses(ctx):
    item,_,_=ctx
    cli=Client(item,[row(item)])
    original=cli._get
    def protected(path):
        if path.endswith('/branches/main'):return {'protected':True}
        if path.endswith('/required_status_checks'):
            return {'contexts':[], 'checks':[{'context':'external','app_id':99999}]}
        return original(path)
    cli._get=protected
    result=strict(ctx,cli)
    assert result['state']=='BRANCH_RULES_UNVERIFIED'
    assert 'external app' in result['branch_rules_issue']


def test_different_rule_type_not_mistaken_for_required_ci(ctx):
    item,_,_=ctx
    cli=Client(item,[row(item)])
    cli.branch_rules=lambda *a:[{'type':'pull_request','parameters':{'required_approving_review_count':1}}]
    result=strict(ctx,cli)
    assert result['state']=='REQUIRED_CHECKS_PASSED'
    assert result['branch_rules']['required_status_contexts']==[]
    assert result['release_qualified'] is False


def test_ruleset_required_workflow_not_claimed_satisfied_without_provider_proof(ctx):
    item,_,_=ctx
    cli=Client(item,[row(item)])
    cli.branch_rules=lambda *a:[{'type':'required_workflows', 'parameters': {'workflows':[{'workflow_id':77}]}}]
    result=strict(ctx,cli)
    assert result['state']=='BRANCH_RULES_UNVERIFIED'
    assert 'required workflows' in result['branch_rules_issue']


def test_exact_run_get_cannot_replace_in_window_registration_with_old_run(ctx):
    item,_,_=ctx
    cli=Client(item,[row(item)])
    original=cli.get_run
    def stale(slug, rid):
        current=original(slug,rid)
        current['created_at']=(reg._date(item['created_at'])-timedelta(days=1)).isoformat()
        return current
    cli.get_run=stale
    result=strict(ctx,cli)
    assert result['state']=='CHECKS_EVIDENCE_PENDING'
    assert not result['checks_qualified']


def test_exact_run_get_rejects_invalid_attempt_even_with_green_job(ctx):
    item,_,_=ctx
    cli=Client(item,[row(item)])
    original=cli.get_run
    def invalid(slug,rid):
        current=original(slug,rid)
        current['run_attempt']=True
        return current
    cli.get_run=invalid
    result=strict(ctx,cli)
    assert result['state']=='CHECKS_EVIDENCE_PENDING'
