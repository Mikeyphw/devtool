"""Offline acceptance and failure injection for post-recreation CI qualification.

All GitHub API and mutation responses are simulated. No external network calls.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from devtool.recovery import engine as rr
from devtool.recovery import qualification as q

REPO='Mikeyphw/MMRL'
OLD='0'*40
BASE='a'*40
HEAD='b'*40
MERGE='c'*40
WORKFLOWS=[
    {'id':9001,'name':'MMRL Next - Android source build (no release)',
     'path':'.github/workflows/next-android-ci.yml','state':'active'},
    {'id':9002,'name':'N03 MMRL Next Native Devtool Android CI',
     'path':'.github/workflows/next-devtool-native.yml','state':'active'},
]


class FakeGitHub:
    def __init__(self):
        self.repo={'id':999,'default_branch':'master','created_at':'2026-11-18T20:33:00Z'}
        self.pr={'number':2,'state':'open','merged_at':None,
                 'head':{'sha':HEAD,'ref':'feat/n05f-shared-sync','repo':{'id':999}},
                 'base':{'sha':BASE,'ref':'next','repo':{'id':999}}}
        self.refs={'refs/heads/master':OLD,'refs/heads/next':BASE,
                   'refs/heads/feat/n05f-shared-sync':HEAD}
        self.active=WORKFLOWS.copy()
        self.policy=None
        self.runs={}
        self.events=[]
        self.calls=[]
        self.lost_close=False
        self.lost_reopen=False
        self.mutation_count=0
        self.jobs={}
        self.branch_source={'refs/heads/next':[
            {'path':w['path'],'name':w['path'].split('/')[-1],
             'sha':str(w['id']),'type':'file'} for w in WORKFLOWS]}

    def linked_run(self, workflow=WORKFLOWS[0], *, conclusion='success', pr_sha=HEAD,
                   run_sha=MERGE, ref='next', run_id=44454118137, created='2026-11-19T00:40:00Z'):
        item={'id':run_id,'workflow_id':workflow['id'],'event':'pull_request',
              'path':workflow['path']+'@refs/pull/2/merge',
              'repository':{'id':999},'status':'completed','conclusion':conclusion,
              'head_sha':run_sha,'head_branch':'feat/n05f-shared-sync',
              'created_at':created,'html_url':'https://github.com/Mikeyphw/MMRL/actions/runs/'+str(run_id),
              'run_attempt':1,'pull_requests':[
                  {'number':2,'head':{'sha':pr_sha},'base':{'ref':ref}}]}
        self.runs.setdefault(workflow['id'],[]).append(item)
        self.jobs[run_id]=[{'id':run_id+100,'name':workflow['name'],
                            'status':'completed','conclusion':conclusion}]
        return item

    def api(self, endpoint, *, method='GET',payload=None,missing_ok=False):
        self.calls.append((method,endpoint,payload))
        if endpoint==f'repos/{REPO}/pulls/2':
            if method=='PATCH':
                assert payload == {'state': payload['state']}
                self.mutation_count += 1
                self.pr['state']=payload['state']
                self.events.append({'event':'closed' if payload['state']=='closed' else 'reopened',
                                    'created_at':rr.now(),
                                    'actor':{'login':'Mikeyphw'}})
                if payload['state']=='closed' and self.lost_close:
                    self.lost_close=False
                    raise rr.Refused('lost close response')
                if payload['state']=='open' and self.lost_reopen:
                    self.lost_reopen=False
                    raise rr.Refused('lost reopen response')
            return json.loads(json.dumps(self.pr))
        if endpoint==f'repos/{REPO}/actions/permissions':return {'enabled':True}
        if endpoint=='user':return {'login':'Mikeyphw'}
        if endpoint.startswith(f'repos/{REPO}/contents/config/delivery-required-ci.json?ref='):
            if self.policy is None:return None
            return {'type':'file','encoding':'base64',
                    'content':base64.b64encode(json.dumps(self.policy).encode()).decode()}
        if endpoint.startswith(f'repos/{REPO}/contents/.github/workflows/') and '?ref=' in endpoint:
            path=endpoint.split('/contents/',1)[1].split('?',1)[0]
            return {'type':'file','path':path,'sha':'1'*40} if any(
                x['path']==path for x in WORKFLOWS) else None
        if endpoint.startswith(f'repos/{REPO}/contents/.github/workflows?ref='):
            sha=endpoint.rsplit('=',1)[1]
            match=[r for r in self.refs if self.refs[r]==sha]
            if not match:return None
            # All callers use ref SHAs rather than branch names.
            return self.branch_source.get(match[0],None)
        raise AssertionError((method,endpoint,payload))

    def collect(self, endpoint, *, key=None,**kwargs):
        if endpoint == f'repos/{REPO}/actions/workflows':return list(self.active)
        if endpoint.startswith(f'repos/{REPO}/actions/workflows/') and '/runs?' in endpoint:
            workflow_id=int(endpoint.split('/actions/workflows/')[1].split('/')[0])
            return list(self.runs.get(workflow_id,[]))
        if endpoint==f'repos/{REPO}/issues/2/events':return list(self.events)
        if endpoint.startswith(f'repos/{REPO}/actions/runs/') and endpoint.endswith('/jobs'):
            run_id=int(endpoint.split('/actions/runs/')[1].split('/')[0])
            return self.jobs.get(run_id,[])
        raise AssertionError(endpoint)


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.session=Path(self.temp.name)/'session-abc'
        (self.session/'primary'/'actions').mkdir(parents=True)
        rr.write_json(self.session/'primary'/'source-refs.json',{
            'refs/heads/master':OLD,'refs/heads/next':BASE,
            'refs/heads/feat/n05f-shared-sync':HEAD})
        rr.write_json(self.session/'primary'/'repo.json',{'default_branch':'master'})
        rr.write_json(self.session/'primary'/'actions'/'workflows.json',
                      [{**w,'state':'active'} for w in WORKFLOWS])
        self.state={'schema':1,'session_id':self.session.name,'repo':REPO,
                    'stage':'done-with-action-required','created_new_id':999,
                    'original_repo_id':123,'topology':'standalone','approved':True,
                    'accept_local_only_risk':True}
        rr.save_state(self.session,self.state)
        rr.write_json(self.session/'report.json',{'recovery_classification':'RESTORED_WITH_ACTION_REQUIRED'})
        self.gh=FakeGitHub()
        patches=[patch.object(rr,'verify_pair',return_value={}),
                 patch.object(rr,'require_independent_backup',return_value=None),
                 patch.object(rr,'get_new_repo',side_effect=lambda s:self.gh.repo),
                 patch.object(rr,'live_refs',side_effect=lambda r:self.gh.refs.copy()),
                 patch.object(rr,'api',side_effect=self.gh.api),
                 patch.object(rr,'collect',side_effect=self.gh.collect)]
        for p in patches:
            p.start();self.addCleanup(p.stop)

    def policy(self):
        self.gh.policy={'schema_version':1,'repository':REPO,'base_branch':'next',
                        'event':'pull_request','release_qualification':'separate',
                        'required_workflows':[{'name':w['name'],'workflow_id':100+i}
                                              for i,w in enumerate(WORKFLOWS)]}

    def qualified(self,**kwargs):
        return q.qualify_pr(self.session,number=2,
                            required=[w['name'] for w in WORKFLOWS],**kwargs)

    def test_missing_policy_requires_explicit_workflow_selection(self):
        with self.assertRaisesRegex(rr.Refused,'provide --required-workflow'):
            q.qualify_pr(self.session,number=2)
        self.assertEqual(self.gh.mutation_count,0)

    def test_exact_two_workflows_succeed_on_merge_sha_with_linked_pr_head(self):
        self.gh.linked_run(WORKFLOWS[0])
        self.gh.linked_run(WORKFLOWS[1],run_id=44454118115)
        result=self.qualified()
        self.assertTrue(result['verified_success'])
        self.assertEqual(result['head_sha'],HEAD)
        self.assertEqual(result['base_sha'],BASE)
        self.assertEqual(result['workflows'][0]['run_head_sha'],MERGE)
        self.assertFalse(rr.read_json(self.session/'report.json')['fully_operationally_verified'])
        self.assertEqual(self.gh.mutation_count,0)

    def test_failure_missing_or_skipped_is_not_pass(self):
        self.gh.linked_run(WORKFLOWS[0])
        self.assertFalse(self.qualified()['verified_success'])
        self.gh.linked_run(WORKFLOWS[1],conclusion='skipped',run_id=2)
        self.assertFalse(self.qualified()['verified_success'])
        self.gh.runs[9002][0]['conclusion']='failure'
        self.assertFalse(self.qualified()['verified_success'])

    def test_incorrect_pr_identity_is_not_qualifying_evidence(self):
        self.gh.linked_run(WORKFLOWS[0],pr_sha='f'*40)
        self.gh.linked_run(WORKFLOWS[1],ref='master',run_id=5)
        result=self.qualified()
        self.assertTrue(all(x['status']=='missing' for x in result['workflows']))

    def test_foreign_repo_or_wrong_workflow_id_is_not_qualifying_evidence(self):
        a=self.gh.linked_run(WORKFLOWS[0]);a['repository']['id']=777
        b=self.gh.linked_run(WORKFLOWS[1],run_id=9);b['workflow_id']=100
        self.assertFalse(self.qualified()['verified_success'])

    def test_non_pr_event_is_not_qualifying_evidence(self):
        a=self.gh.linked_run(WORKFLOWS[0]);a['event']='workflow_dispatch'
        self.gh.linked_run(WORKFLOWS[1],run_id=8)
        self.assertFalse(self.qualified()['verified_success'])

    def test_success_run_with_skipped_jobs_does_not_qualify(self):
        a=self.gh.linked_run(WORKFLOWS[0]);self.gh.linked_run(WORKFLOWS[1],run_id=10)
        self.gh.jobs[a['id']][0]['conclusion']='skipped'
        self.assertFalse(self.qualified()['verified_success'])

    def test_success_run_without_any_job_evidence_does_not_qualify(self):
        a=self.gh.linked_run(WORKFLOWS[0]);self.gh.linked_run(WORKFLOWS[1],run_id=10)
        self.gh.jobs[a['id']]=[]
        self.assertFalse(self.qualified()['verified_success'])

    def test_workflow_missing_on_exact_base_is_not_qualified(self):
        self.gh.linked_run(WORKFLOWS[0]);self.gh.linked_run(WORKFLOWS[1],run_id=10)
        old=self.gh.api
        def fake_api(endpoint,**kwargs):
            if endpoint.startswith(f'repos/{REPO}/contents/.github/workflows/next-devtool-native.yml?'):
                return None
            return old(endpoint,**kwargs)
        with patch.object(rr,'api',side_effect=fake_api):
            with self.assertRaisesRegex(rr.Refused,'not source-proven'):
                self.qualified()

    def test_base_policy_is_enforced_and_old_workflow_ids_ignored(self):
        self.policy()
        self.gh.linked_run(WORKFLOWS[0]);self.gh.linked_run(WORKFLOWS[1],run_id=8)
        result=q.qualify_pr(self.session,number=2)
        self.assertTrue(result['verified_success'])
        self.assertEqual(result['source'],'verified-base-policy')
        self.assertTrue(result['policy_sha256'])
        self.assertEqual(result['workflows'][0]['workflow']['id'],9001)

    def test_duplicate_registered_workflow_names_refused(self):
        self.gh.active.append({'id':9111,'name':WORKFLOWS[0]['name'],
                               'path':'.github/workflows/duplicate.yml','state':'active'})
        with self.assertRaisesRegex(rr.Refused,'absent or ambiguous'):
            self.qualified()

    def test_github_linewrapped_base64_policy_is_accepted(self):
        self.policy()
        original=self.gh.api
        def wrapped(endpoint,**kwargs):
            value=original(endpoint,**kwargs)
            if endpoint.startswith(f'repos/{REPO}/contents/config/delivery-required-ci.json?'):
                value['content']='\n'.join(value['content'][i:i+60]
                                            for i in range(0,len(value['content']),60))+'\n'
            return value
        with patch.object(rr,'api',side_effect=wrapped):
            result=q.qualify_pr(self.session,number=2)
        self.assertEqual(result['source'],'verified-base-policy')
        self.assertFalse(result['verified_success'])

    def test_operator_cannot_downgrade_base_policy(self):
        self.policy()
        with self.assertRaisesRegex(rr.Refused,'cannot weaken'):
            q.qualify_pr(self.session,number=2,required=[WORKFLOWS[0]['name']])

    def test_base_policy_wrong_repo_rejected(self):
        self.policy();self.gh.policy['repository']='someone/other'
        with self.assertRaisesRegex(rr.Refused,'required-CI policy must be schema'):
            q.qualify_pr(self.session,number=2)

    def test_disabled_workflow_refused(self):
        self.gh.active[0] = {**WORKFLOWS[0],'state':'disabled_manually'}
        with self.assertRaisesRegex(rr.Refused,'not enabled'):
            self.qualified()

    def test_close_reopen_requires_exact_ack(self):
        self.assertFalse(self.qualified()['verified_success'])
        with self.assertRaisesRegex(rr.Refused,'requires --ack-retrigger'):
            self.qualified(ack_retrigger='Mikeyphw/MMRL#1')
        self.assertEqual(self.gh.mutation_count,0)

    def test_approved_close_reopen_is_journaled_once(self):
        result=self.qualified(ack_retrigger=REPO+'#2')
        self.assertFalse(result['verified_success'])
        self.assertEqual(self.gh.mutation_count,2)
        self.assertEqual(rr.load_state(self.session)['pr_ci_retriggers']['2']['phase'],'reopened')
        self.qualified(ack_retrigger=REPO+'#2')
        self.assertEqual(self.gh.mutation_count,2)

    def test_interrupted_close_response_resumes_reopen(self):
        self.gh.lost_close=True
        with self.assertRaisesRegex(rr.Refused,'lost close'):
            self.qualified(ack_retrigger=REPO+'#2')
        self.assertEqual(self.gh.pr['state'],'closed')
        self.qualified(ack_retrigger=REPO+'#2')
        self.assertEqual(self.gh.pr['state'],'open')
        self.assertEqual(self.gh.mutation_count,2)

    def test_interrupted_reopen_response_does_not_close_again(self):
        self.gh.lost_reopen=True
        with self.assertRaisesRegex(rr.Refused,'lost reopen'):
            self.qualified(ack_retrigger=REPO+'#2')
        self.assertEqual(self.gh.pr['state'],'open')
        self.qualified(ack_retrigger=REPO+'#2')
        self.assertEqual(self.gh.mutation_count,2)

    def test_pr_head_drift_after_retrigger_refused(self):
        self.qualified(ack_retrigger=REPO+'#2')
        self.gh.pr['head']['sha']='f'*40
        with self.assertRaisesRegex(rr.Refused,'changed'):
            self.qualified()

    def test_failed_retrigger_ci_does_not_accept_prior_success(self):
        self.qualified(ack_retrigger=REPO+'#2')
        self.gh.linked_run(WORKFLOWS[0],created='2026-11-18T00:00:00Z')
        self.gh.linked_run(WORKFLOWS[1],created='2026-11-18T00:00:00Z',run_id=8)
        self.assertFalse(self.qualified()['verified_success'])

    def test_workflow_inventory_reports_branch_only_registration_gaps(self):
        self.gh.active = []
        # A branch workflow exists even if the registry does not list it.
        audit=q.workflow_inventory(self.session)
        paths={x['path']:x for x in audit['workflows']}
        self.assertEqual(paths[WORKFLOWS[0]['path']]['status'],'branch-only-unregistered')
        self.assertEqual(paths[WORKFLOWS[1]['path']]['source_branches'],['next'])
        self.assertEqual(self.gh.mutation_count,0)

    def test_workflow_inventory_ref_drift_is_reported_not_silently_accepted(self):
        self.gh.refs['refs/heads/next']='f'*40
        audit=q.workflow_inventory(self.session)
        self.assertFalse(audit['restoration_snapshot_matches_current_refs'])
        self.assertIn({'ref':'refs/heads/next','expected_sha':BASE,
                       'current_sha':'f'*40,'change':'advanced-or-replaced'},
                      audit['post_restoration_ref_changes'])
        self.assertEqual(audit['branches_checked'], 3)
        self.assertEqual(self.gh.mutation_count,0)

    def test_workflow_inventory_new_and_removed_refs_are_not_hidden(self):
        del self.gh.refs['refs/heads/feat/n05f-shared-sync']
        self.gh.refs['refs/heads/new-workstream']='e'*40
        audit=q.workflow_inventory(self.session)
        changes={item['ref']:item for item in audit['post_restoration_ref_changes']}
        self.assertEqual(changes['refs/heads/feat/n05f-shared-sync']['change'],'removed')
        self.assertEqual(changes['refs/heads/new-workstream']['change'],'added-after-restoration')
        self.assertFalse(audit['restoration_snapshot_matches_current_refs'])
        self.assertEqual(self.gh.mutation_count,0)

    def test_workflow_inventory_unchanged_snapshot_is_explicitly_verified(self):
        audit=q.workflow_inventory(self.session)
        self.assertTrue(audit['restoration_snapshot_matches_current_refs'])
        self.assertEqual(audit['post_restoration_ref_changes'],[])

    def test_merged_pr_historical_ci_evidence_is_read_only_and_separate(self):
        self.gh.pr['state']='closed'
        self.gh.pr['merged_at']='2026-11-19T04:12:45Z'
        self.gh.refs['refs/heads/next']='f'*40  # post-merge branch advancement
        self.gh.linked_run(WORKFLOWS[0])
        self.gh.linked_run(WORKFLOWS[1],run_id=44454118115)
        result=self.qualified()
        self.assertTrue(result['verified_success'])
        self.assertTrue(result['historical_merged_pr'])
        self.assertEqual(result['head_sha'],HEAD)
        self.assertEqual(result['base_sha'],BASE)
        self.assertFalse(result['live_pr_qualification'])
        self.assertEqual(self.gh.mutation_count,0)
        state=rr.load_state(self.session)
        self.assertIn('2',state['historical_merged_pr_ci_evidence'])
        self.assertNotIn('2',state.get('pr_ci_qualifications',{}))
        report=rr.read_json(self.session/'report.json')
        self.assertEqual(report['required_ci_qualified_for'],[])
        self.assertEqual(report['recovery_classification'],'RESTORED_WITH_ACTION_REQUIRED')

    def test_merged_pr_does_not_leave_stale_live_qualification_claim(self):
        self.gh.linked_run(WORKFLOWS[0])
        self.gh.linked_run(WORKFLOWS[1],run_id=44454118115)
        self.assertTrue(self.qualified()['verified_success'])
        self.assertEqual(rr.read_json(self.session/'report.json')['required_ci_qualified_for'],[2])
        self.gh.pr['state']='closed'
        self.gh.pr['merged_at']='2026-11-19T04:12:45Z'
        historical=self.qualified()
        self.assertTrue(historical['verified_success'])
        self.assertFalse(historical['live_pr_qualification'])
        report=rr.read_json(self.session/'report.json')
        self.assertEqual(report['required_ci_qualified_for'],[])
        self.assertIn('2',report['pr_ci_qualifications'])  # immutable earlier record retained
        self.assertIn('2',report['historical_merged_pr_ci_evidence'])

    def test_merged_pr_refuses_retrigger_and_watch_without_mutation(self):
        self.gh.pr['state']='closed'
        self.gh.pr['merged_at']='2026-11-19T04:12:45Z'
        with self.assertRaisesRegex(rr.Refused,'historical CI evidence only'):
            self.qualified(ack_retrigger=REPO+'#2')
        with self.assertRaisesRegex(rr.Refused,'historical CI evidence only'):
            self.qualified(watch=True)
        self.assertEqual(self.gh.mutation_count,0)

    def test_merged_pr_incomplete_ci_not_false_success(self):
        self.gh.pr['state']='closed'
        self.gh.pr['merged_at']='2026-11-19T04:12:45Z'
        self.gh.linked_run(WORKFLOWS[0])
        result=self.qualified()
        self.assertFalse(result['verified_success'])
        self.assertTrue(result['historical_merged_pr'])
        self.assertEqual(rr.read_json(self.session/'report.json')['required_ci_qualified_for'],[])

    def test_inconsistent_merged_pr_metadata_is_refused(self):
        self.gh.pr['merged_at']='2026-11-19T04:12:45Z'
        with self.assertRaisesRegex(rr.Refused,'inconsistent'):
            self.qualified()

    def test_merged_pr_sha_changes_during_audit_are_refused(self):
        self.gh.pr['state']='closed'
        self.gh.pr['merged_at']='2026-11-19T04:12:45Z'
        self.gh.linked_run(WORKFLOWS[0])
        original=self.gh.api
        counts={'read':0}
        def api_with_drift(endpoint,**kwargs):
            response=original(endpoint,**kwargs)
            if endpoint == f'repos/{REPO}/pulls/2':
                counts['read']+=1
                if counts['read'] > 1:
                    response['head']['sha']='d'*40
            return response
        with patch.object(rr,'api',side_effect=api_with_drift):
            with self.assertRaisesRegex(rr.Refused,'drifted'):
                self.qualified()
        self.assertEqual(self.gh.mutation_count,0)

    def test_wrong_recreated_id_fail_closed(self):
        self.gh.repo['id']=1000
        with self.assertRaisesRegex(rr.Refused,'replacement repository ID drifted'):
            self.qualified()


if __name__=='__main__':unittest.main()
