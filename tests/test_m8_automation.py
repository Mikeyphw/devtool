"""M8 finite-policy, native intent, PR maintenance and poll fallback acceptance."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, mock
from urllib.error import URLError
from uuid import uuid4

from devtool import m8_automation as au, m8_webhooks as wh

REPO='Mikeyphw/devtool'
SHA='a'*40
BASE='b'*40
SID='c'*32


def policy(*,effects=('observe',),events=('pull_request:opened',),labels=(),comment='',
           status_context='',status_state='',workflow='',rerun_run_id=0,max_events=20):
    return {'schema':'devtool.m8.automation-policy.v1','policy_id':str(uuid4()),
            'repository':REPO,'repository_id':1276037219,'installation_id':121212,
            'head_sha':SHA,'head_branch':'feature/test','base_branch':'main','base_sha':BASE,
            'pr_number':51,'session_id':SID,
            'expires_at':(datetime.now(timezone.utc)+timedelta(hours=6)).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'events':list(events),'effects':list(effects),'labels':list(labels),'comment':comment,
            'status_context':status_context,'status_state':status_state,'workflow':workflow,
            'rerun_run_id':rerun_run_id,'max_events':max_events}


class Client:
    def __init__(self):
        self._token='test-only-fake-token'; self.timeout=4
        self.posts=[];self.comment=None;self.labels=[];self.status=None
        self._opener=Opener(self)
        self.pr={'number':51,'state':'open','draft':True,'updated_at':'2026-11-22T02:00:00Z',
                 'head':{'sha':SHA,'ref':'feature/test','repo':{'full_name':REPO}},
                 'base':{'sha':BASE,'ref':'main','repo':{'full_name':REPO}}}
    def _get(self,path):
        if path==f'/repos/{REPO}':return {'id':1276037219,'full_name':REPO}
        if path==f'/repos/{REPO}/branches/feature%2Ftest':return {'commit':{'sha':SHA}}
        if path==f'/repos/{REPO}/pulls/51':return self.pr
        if path==f'/repos/{REPO}/issues/51':return {'labels':[{'name':x} for x in self.labels]}
        if path==f'/repos/{REPO}/issues/comments/1001':
            return {'id':1001,'body':self.comment,'issue_url':f'https://api.github.com/repos/{REPO}/issues/51'}
        if path==f'/repos/{REPO}/commits/{SHA}/status':
            return {'statuses':[self.status] if self.status is not None else []}
        raise AssertionError(f'unexpected fake API GET {path}')
    def get_run(self,repo,run_id):
        return {'id':run_id,'workflow_id':383977915,'head_sha':SHA,'head_branch':'feature/test',
                'event':'workflow_dispatch','status':'completed','conclusion':'failure'}


class Response:
    def __init__(self,raw):self.raw=raw;self.status=201
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,count):return self.raw[:count]


class Opener:
    def __init__(self,client):self.client=client;self.error=False
    def open(self,req,timeout):
        if self.error:raise URLError('synthetic outage')
        self.client.posts.append(req)
        data=json.loads(req.data)
        if req.full_url.endswith('/comments'):
            self.client.comment=data['body'];return Response(json.dumps({'id':1001}).encode())
        if req.full_url.endswith('/labels'):
            self.client.labels.extend(data['labels']);return Response(json.dumps([{'name':x} for x in self.client.labels]).encode())
        if f'/statuses/{SHA}' in req.full_url:
            self.client.status={'id':3001,'context':data['context'],'state':data['state'],
                                'description':data['description']}
            return Response(json.dumps({'id':3001}).encode())
        raise AssertionError('wrong POST')


class M8AutomationTests(TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)/'m8'
        self.worktree=Path(temp.name)/'worktree';self.worktree.mkdir()
        self.client=Client()
        provider_stub=SimpleNamespace(API_ORIGIN='https://api.github.com',API_VERSION='2022-11-28',
            GitHubActionsError=type('GitHubActionsError',(RuntimeError,),{}),
            GitHubDispatchOutcomeUnknown=type('GitHubDispatchOutcomeUnknown',(RuntimeError,),{}))
        try:import devtool.github_actions
        except ModuleNotFoundError:
            patch_provider=mock.patch.dict(sys.modules,{'devtool.github_actions':provider_stub})
            patch_provider.start();self.addCleanup(patch_provider.stop)
        self.patch_client=mock.patch.object(au,'_client',return_value=self.client)
        self.patch_client.start();self.addCleanup(self.patch_client.stop)
        self.session={'repository':REPO,'head_sha':SHA,'head':'feature/test','base':'main',
                      'pr':{'number':51},'source_binding':'matching','resumable':True,
                      'repo_root':str(self.worktree)}
        self.patch_session=mock.patch.object(au,'_native_session',return_value=self.session)
        self.patch_session.start();self.addCleanup(self.patch_session.stop)
        self.pin=wh.Pin(REPO,1276037219,121212,SHA,51,SID)
        wh.pin(self.root,self.pin)
    def approve(self,p):
        self.assertEqual(au.approve(self.root,p,yes=True,client=self.client)['state'],'APPROVED')
    def fake_observe(self):return mock.patch.object(au,'_observe',return_value={
        'required_ci':'PASSED','regression':'PASSED','pre_merge':'PASSED','read_only':True})

    def test_policy_missing_fields(self):
        p=policy();p.pop('pr_number')
        with self.assertRaisesRegex(au.M8Refusal,'SCHEMA'):au.Policy.parse(p)
    def test_policy_needs_observe(self):
        p=policy(effects=('pr_comment',),comment='Hi')
        with self.assertRaisesRegex(au.M8Refusal,'EFFECTS'):au.Policy.parse(p)
    def test_policy_rejects_unknown_event(self):
        with self.assertRaisesRegex(au.M8Refusal,'EVENTS'):au.Policy.parse(policy(events=('push:all',)))
    def test_policy_rejects_expired(self):
        p=policy();p['expires_at']='2020-01-01T00:00:00Z'
        with self.assertRaisesRegex(au.M8Refusal,'EXPIRED'):au.Policy.parse(p)
    def test_policy_rejects_excessive_expiry(self):
        p=policy();p['expires_at']=(datetime.now(timezone.utc)+timedelta(days=8)).strftime('%Y-%m-%dT%H:%M:%SZ')
        with self.assertRaisesRegex(au.M8Refusal,'TOO_LONG'):au.Policy.parse(p)
    def test_policy_rejects_duplicate_effects(self):
        with self.assertRaisesRegex(au.M8Refusal,'EFFECTS'):au.Policy.parse(policy(effects=('observe','observe')))
    def test_policy_rejects_unapproved_mutation_scope(self):
        with self.assertRaisesRegex(au.M8Refusal,'COMMENT_SCOPE'):au.Policy.parse(policy(comment='Surprise!'))
    def test_policy_rejects_merge(self):
        with self.assertRaisesRegex(au.M8Refusal,'EFFECTS'):au.Policy.parse(policy(effects=('observe','merge')))
    def test_policy_rejects_bad_labels(self):
        with self.assertRaisesRegex(au.M8Refusal,'LABELS'):au.Policy.parse(policy(effects=('observe','pr_labels_add'),labels=('label a',)))
    def test_policy_rejects_wrong_rerun_id(self):
        with self.assertRaisesRegex(au.M8Refusal,'RERUN_ID'):au.Policy.parse(policy(effects=('observe','ci_rerun'),workflow='devtool-full-repository-pytest.yml'))
    def test_approve_requires_explicit_yes(self):
        with self.assertRaisesRegex(au.M8Refusal,'APPROVAL_REQUIRED'):
            au.approve(self.root,policy(),yes=False,client=self.client)
    def test_approve_is_immutable(self):
        p=policy();self.approve(p)
        self.assertEqual(au.approve(self.root,p,yes=True,client=self.client)['state'],'EXISTING')
        new=policy()
        with self.assertRaisesRegex(au.M8Refusal,'POLICY_IMMUTABLE'):
            au.approve(self.root,new,yes=True,client=self.client)
    def test_reject_wrong_pin(self):
        p=policy();p['head_sha']='d'*40
        with self.assertRaisesRegex(au.M8Refusal,'PIN_MISMATCH'):
            au.approve(self.root,p,yes=True,client=self.client)
    def test_reject_live_pr_drift(self):
        self.client.pr['head']['sha']='d'*40
        with self.assertRaisesRegex(au.M8Refusal,'PR_SOURCE_OR_BASE_DRIFT'):
            au.approve(self.root,policy(),yes=True,client=self.client)
    def test_revoke_fail_closed(self):
        self.approve(policy())
        with self.assertRaisesRegex(au.M8Refusal,'CONFIRMATION_REQUIRED'):
            au.revoke(self.root,yes=False)
        self.assertEqual(au.revoke(self.root,yes=True)['state'],'REVOKED')
        self.assertEqual(au.revoke(self.root,yes=True)['state'],'ALREADY_REVOKED')
        with self.assertRaisesRegex(au.M8Refusal,'REVOKED'):au.load_policy(self.root)
        self.assertTrue(au.state(self.root)['revoked'])
    def test_poll_deduplicated_and_read_only(self):
        self.approve(policy())
        with self.fake_observe():
            self.assertEqual(au.poll_once(self.root,request_id='f'*32,client=self.client)['state'],'CHANGED')
            self.assertEqual(au.poll_once(self.root,request_id='f'*32,client=self.client)['state'],'UNCHANGED')
        self.assertEqual(au.state(self.root)['poll_count'],1)
        self.assertEqual(self.client.posts,[])
    def test_follow_ignores_non_policy_event(self):
        self.approve(policy())
        body=b'{"action":"completed","repository":{"id":1276037219,"full_name":"Mikeyphw/devtool"},"installation":{"id":121212},"workflow_run":{"id":1,"run_attempt":1,"head_sha":"'+SHA.encode()+b'"}}'
        import hashlib,hmac
        hs={'X-GitHub-Event':'workflow_run','X-GitHub-Delivery':str(uuid4()),
            'X-Hub-Signature-256':'sha256='+hmac.new(b'k'*32,body,hashlib.sha256).hexdigest()}
        wh.ingest(self.root,hs,body,b'k'*32)
        with self.fake_observe():
            res=au.process_events(self.root,request_id='f'*32,auth='auto')
        self.assertEqual(res['ignored'],1);self.assertEqual(res['observed'],0)
        self.assertEqual(au.process_events.__name__,'process_events')
    def test_follow_deduplicates_events(self):
        p=policy(events=('pull_request:opened',));self.approve(p)
        body=json.dumps({'action':'opened','repository':{'id':1276037219,'full_name':REPO},
                         'installation':{'id':121212},'number':51,
                         'pull_request':{'head':{'sha':SHA,'repo':{'full_name':REPO}},
                                         'base':{'repo':{'full_name':REPO}},'merged':False}}).encode()
        import hashlib,hmac
        hs={'X-GitHub-Event':'pull_request','X-GitHub-Delivery':str(uuid4()),
            'X-Hub-Signature-256':'sha256='+hmac.new(b'k'*32,body,hashlib.sha256).hexdigest()}
        wh.ingest(self.root,hs,body,b'k'*32)
        with self.fake_observe():
            self.assertEqual(au.process_events(self.root,request_id='f'*32)['observed'],1)
            self.assertEqual(au.process_events(self.root,request_id='f'*32)['duplicate'],1)

    def test_write_requires_yes(self):
        self.approve(policy(effects=('observe','pr_comment'),comment='Approved'))
        with self.assertRaisesRegex(au.M8Refusal,'EXPLICIT_YES'):
            au.maintain(self.root,'pr_comment',yes=False,client=self.client)
        self.assertEqual(self.client.posts,[])
    def test_comment_intent_and_postimage(self):
        self.approve(policy(effects=('observe','pr_comment'),comment='Approved note'))
        with mock.patch.object(au,'_native_intent') as intent, mock.patch.object(au,'_native_confirm') as confirm:
            res=au.maintain(self.root,'pr_comment',yes=True,client=self.client)
        self.assertEqual(res['state'],'NATIVE_CONFIRMED');self.assertEqual(len(self.client.posts),1)
        self.assertEqual(self.client.posts[0].get_method(),'POST')
        self.assertIn('devtool-m8:',self.client.comment)
        self.assertEqual(intent.call_args.args[1],'pr_comment')
        self.assertEqual(confirm.call_args.args[1],'pr_comment')
    def test_comment_transport_failure_keeps_intent(self):
        self.approve(policy(effects=('observe','pr_comment'),comment='Approved note'))
        self.client._opener.error=True
        with mock.patch.object(au,'_native_intent') as intent:
            with self.assertRaisesRegex(Exception,'outcome uncertain'):
                au.maintain(self.root,'pr_comment',yes=True,client=self.client)
            intent.assert_called_once()
    def test_labels_idempotent_read_only(self):
        self.approve(policy(effects=('observe','pr_labels_add'),labels=('reviewed',)))
        self.client.labels=['reviewed']
        result=au.maintain(self.root,'pr_labels_add',yes=True,client=self.client)
        self.assertEqual(result['state'],'ALREADY_PRESENT_READ_ONLY')
        self.assertEqual(self.client.posts,[])
    def test_labels_intent_and_postimage(self):
        self.approve(policy(effects=('observe','pr_labels_add'),labels=('reviewed',)))
        with mock.patch.object(au,'_native_intent') as intent,mock.patch.object(au,'_native_confirm') as confirm:
            result=au.maintain(self.root,'pr_labels_add',yes=True,client=self.client)
        self.assertEqual(result['state'],'NATIVE_CONFIRMED')
        self.assertEqual(self.client.labels,['reviewed'])
        intent.assert_called_once();confirm.assert_called_once()
    def test_status_success_requires_native_B3(self):
        self.approve(policy(effects=('observe','commit_status'),status_context='devtool/m8/qualified',status_state='success'))
        with mock.patch.object(au,'_observe',return_value={'pre_merge':'BLOCKED_OR_PENDING'}):
            with self.assertRaisesRegex(au.M8Refusal,'REQUIRES_NATIVE_B3'):
                au.maintain(self.root,'commit_status',yes=True,client=self.client,request_id='f'*32)
        self.assertEqual(self.client.posts,[])
    def test_status_with_native_B3(self):
        self.approve(policy(effects=('observe','commit_status'),status_context='devtool/m8/qualified',status_state='success'))
        with self.fake_observe(),mock.patch.object(au,'_native_intent'),mock.patch.object(au,'_native_confirm'):
            result=au.maintain(self.root,'commit_status',yes=True,client=self.client,request_id='f'*32)
        self.assertEqual(result['state'],'NATIVE_CONFIRMED')
        self.assertEqual(self.client.status['state'],'success')
    def test_rerun_requires_exact_failed_run(self):
        self.approve(policy(effects=('observe','ci_rerun'),workflow='devtool-full-repository-pytest.yml',rerun_run_id=100))
        class BadRun(Client):
            def get_run(self,repo,run_id):
                d=super().get_run(repo,run_id);d['head_sha']='d'*40;return d
        self.client.get_run=BadRun().get_run
        with self.assertRaisesRegex(au.M8Refusal,'SOURCE_OR_STATE_NOT_ELIGIBLE'):
            au.maintain(self.root,'ci_rerun',yes=True,client=self.client)
    def test_ci_dispatch_native_cli_one_use(self):
        self.approve(policy(effects=('observe','ci_dispatch'),workflow='devtool-full-repository-pytest.yml'))
        receipt={'ok':True,'exit_code':0,'data':{'repository':REPO,'ref':'feature/test',
                 'pre_dispatch_sha':SHA,'workflow_id':383977915,'dispatched':True,
                 'request_id':'f'*32,'run_id':999}}
        fake=SimpleNamespace(returncode=0,stdout=json.dumps(receipt),stderr='')
        with mock.patch.object(au.subprocess,'run',return_value=fake) as runner:
            result=au.maintain(self.root,'ci_dispatch',yes=True,client=self.client)
            self.assertEqual(result['state'],'NATIVE_ACTIONS_RECEIPT_OBSERVED')
            self.assertIn('--yes',runner.call_args.args[0])
            with self.assertRaisesRegex(au.M8Refusal,'INTENT_ALREADY_EXISTS'):
                au.maintain(self.root,'ci_dispatch',yes=True,client=self.client)
        self.assertEqual(runner.call_count,1)

if __name__=='__main__':
    import unittest
    unittest.main()
