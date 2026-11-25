"""DT-20261125-01: verified absence, native journal, no replay, real disposable Git.

Fixtures model the persisted v3 native-adoption contract. Actual Git pushes
use a private local bare remote; the GitHub origin identity remains unchanged.
No network, credentials, or user journal are used.
"""
from pathlib import Path
import hashlib
import json
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from devtool import delivery_session as s, delivery_github as d


def git(root, *args, check=True):
    return subprocess.run(['git','-C',str(root),*args], check=check, capture_output=True, text=True, timeout=15)


class AbsentBranchRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='dt25-absent-')
        self.addCleanup(self.tmp.cleanup)
        p=Path(self.tmp.name);self.repo=p/'repo';self.repo.mkdir();self.remote=p/'remote.git';self.remote.mkdir();self.state=p/'state'
        git(self.repo,'init','-q','-b','main');git(self.repo,'config','user.name','Fixture');git(self.repo,'config','user.email','fixture@example.invalid')
        git(self.remote,'init','-q','--bare');(self.repo/'README.md').write_text('base\n');git(self.repo,'add','.');git(self.repo,'commit','-qm','base')
        self.base=git(self.repo,'rev-parse','HEAD').stdout.strip();git(self.repo,'push',str(self.remote),'refs/heads/main:refs/heads/main')
        git(self.repo,'switch','-c','feature/recovery');(self.repo/'README.md').write_text('native applied\n');git(self.repo,'add','.');git(self.repo,'commit','-qm','source')
        self.head=git(self.repo,'rev-parse','HEAD').stdout.strip();self.tree=git(self.repo,'rev-parse','HEAD^{tree}').stdout.strip();git(self.repo,'remote','add','origin','https://github.com/Example/repo.git')
        self.sid=s.start(self.repo,state_root=self.state,base_sha=self.base)['id'];self.key=f'Example/repo:refs/heads/feature/recovery:{self.head}';self.writes=0
        # Seed only a test v3 adoption record; production adopts native history.
        path=self.state/(self.sid+'.json');record=s._read(path)
        record['native_apply']={'state':'COMMITTED','commit':self.head,'result_commit':self.head,'result_parent':self.base,'result_tree':self.tree,'validation_status':'ok','transaction_status':'completed','transaction_id':'fixture-tx','history_id':'fixture-history'}
        s._write(path,record)
        self.prior=s.record_intent(self.sid,'push',self.key,approved=True,state_root=self.state)['intent_id']
        s.record_push_diagnostic(self.sid,self.prior,'GIT_EXIT_NONZERO',1,state_root=self.state)
    def probe(self,root,branch):
        p=git(root,'ls-remote','--exit-code',str(self.remote),'refs/heads/'+branch,check=False)
        if p.returncode==2 and not p.stdout.strip():return None
        if p.returncode!=0:raise s.DeliverySessionError('fixture transport failure')
        rows=p.stdout.strip().splitlines();self.assertEqual(len(rows),1);sha,ref=rows[0].split('\t');self.assertEqual(ref,'refs/heads/'+branch);return sha
    def push(self,root,branch):
        self.writes+=1;ref='refs/heads/'+branch
        return git(root,'push','--porcelain',f'--force-with-lease={ref}:',str(self.remote),ref+':'+ref,check=False)
    def recover(self,**kwargs):
        return d.push(self.sid,yes=True,state_root=self.state,remote_probe=self.probe,git_push=self.push,recovery_intent_id=self.prior,prior_process_finished=True,prior_branch_absent=True,**kwargs)
    def status(self):return s.status(self.sid,state_root=self.state)
    def assert_unchanged(self):
        self.assertEqual(self.writes,0);self.assertEqual(self.status()['intents'],[{'id':self.prior,'operation':'push','state':'unresolved'}])
    def test_real_creation_and_followup_pr_intent(self):
        out=self.recover();self.assertEqual(out['state'],'PUSH_VERIFIED');self.assertEqual(self.writes,1);self.assertEqual(self.probe(str(self.repo),'feature/recovery'),self.head)
        rec=self.status();self.assertTrue(rec['resumable']);self.assertTrue(rec['remote_verified']);self.assertEqual([x['state'] for x in rec['intents']],['reconciled_no_remote_change','confirmed'])
        raw=s._read(self.state/(self.sid+'.json'));self.assertEqual(raw['intents'][0]['reconciliation']['remote_observation'],'VERIFIED_ABSENT');self.assertIsNone(raw['intents'][0]['reconciliation']['remote_head_sha']);self.assertEqual(raw['intents'][1]['predecessor_intent_id'],self.prior)
        out=self.recover();self.assertFalse(out['pushed_now']);self.assertEqual(self.writes,1)
        self.assertTrue(s.record_intent(self.sid,'pr_create','fixture-pr',approved=True,state_root=self.state)['new'])
    def test_racing_branch_refused_without_new_intent(self):
        def probe(root,branch):
            if branch=='main':return self.base
            self.calls=getattr(self,'calls',0)+1;return None if self.calls==1 else self.base
        with self.assertRaisesRegex(s.DeliverySessionError,'Remote changed'):d.push(self.sid,yes=True,state_root=self.state,remote_probe=probe,git_push=self.push,recovery_intent_id=self.prior,prior_process_finished=True,prior_branch_absent=True)
        self.assert_unchanged()
    def test_base_changed(self):
        with self.assertRaisesRegex(s.DeliverySessionError,'unchanged exact'):d.push(self.sid,yes=True,state_root=self.state,remote_probe=lambda _,branch:None if branch!='main' else 'f'*40,git_push=self.push,recovery_intent_id=self.prior,prior_process_finished=True,prior_branch_absent=True)
        self.assert_unchanged()
    def test_unknown_transport(self):
        def probe(root,branch):raise s.DeliverySessionError('unverified remote')
        with self.assertRaisesRegex(s.DeliverySessionError,'unverified remote'):d.push(self.sid,yes=True,state_root=self.state,remote_probe=probe,git_push=self.push,recovery_intent_id=self.prior,prior_process_finished=True,prior_branch_absent=True)
        self.assert_unchanged()
    def test_no_approval_and_no_process_ack(self):
        out=d.push(self.sid,yes=False,state_root=self.state,remote_probe=self.probe,git_push=self.push,recovery_intent_id=self.prior,prior_process_finished=True,prior_branch_absent=True);self.assertEqual(out['state'],'PUSH_REQUIRES_APPROVAL');self.assert_unchanged()
        with self.assertRaisesRegex(s.DeliverySessionError,'finished previous'):d.push(self.sid,yes=True,state_root=self.state,remote_probe=self.probe,git_push=self.push,recovery_intent_id=self.prior)
        self.assert_unchanged()
    def test_no_prior_absence_ack(self):
        with self.assertRaisesRegex(s.DeliverySessionError,'prior absence'):d.push(self.sid,yes=True,state_root=self.state,remote_probe=self.probe,git_push=self.push,recovery_intent_id=self.prior,prior_process_finished=True)
        self.assert_unchanged()
    def test_atomic_create_only_rejects_racing_parent(self):
        ref='refs/heads/feature/recovery'
        def race(root,branch):
            git(self.repo,'push',str(self.remote),self.base+':'+ref)
            return self.push(root,branch)
        with self.assertRaisesRegex(s.DeliverySessionError,'GIT_NON_FAST_FORWARD'):d.push(self.sid,yes=True,state_root=self.state,remote_probe=self.probe,git_push=race,recovery_intent_id=self.prior,prior_process_finished=True,prior_branch_absent=True)
        self.assertEqual(self.writes,1);self.assertEqual(self.probe(str(self.repo),'feature/recovery'),self.base);self.assertFalse(self.status()['resumable'])
    def test_create_only_app_scope(self):
        from devtool.github_app import _check_git_argv, GitHubAppError
        ref='refs/heads/feature/recovery';argv=d._push_argv('feature/recovery',create_only=True)
        _check_git_argv(argv,'branch_write_workflows')
        for bad in (['push','--porcelain','--force','origin',ref+':'+ref],['push','--porcelain',f'--force-with-lease={ref}:{self.base}','origin',ref+':'+ref],['push','--porcelain','--force-with-lease=refs/heads/main:','origin',ref+':'+ref]):
            with self.assertRaises(GitHubAppError):_check_git_argv(bad,'branch_write_workflows')
        with self.assertRaises(GitHubAppError):_check_git_argv(argv,'repository_read')
    def test_prior_absent_branch_now_present_at_parent_refused(self):
        ref='refs/heads/feature/recovery';git(self.repo,'push',str(self.remote),self.base+':'+ref)
        with self.assertRaisesRegex(s.DeliverySessionError,'Previously absent branch now exists'):self.recover()
        self.assert_unchanged()
    def test_app_capability_and_exact_creation_transport(self):
        from unittest.mock import patch
        from devtool import github_app
        scopes=[];calls=[]
        class Provider:
            def issue(_,repo,capability):
                scopes.append(capability)
                return SimpleNamespace(capability=capability,public_receipt=lambda:{'capability':capability})
        def runner(root,argv,access,timeout):
            github_app._check_git_argv(argv,access.capability);calls.append((argv,access.capability))
            if argv[0]=='push' and '--dry-run' not in argv:self.writes+=1
            return git(root,*[str(self.remote) if a=='origin' else a for a in argv],check=False)
        with patch.object(github_app.AppInstallationProvider,'from_environment',return_value=Provider()),patch.object(github_app,'run_git_authenticated',side_effect=runner):
            result=d.push(self.sid,yes=True,auth='app',state_root=self.state,recovery_intent_id=self.prior,prior_process_finished=True,prior_branch_absent=True)
        self.assertEqual(result['state'],'PUSH_VERIFIED');self.assertEqual(self.writes,1);self.assertTrue(self.status()['resumable'])
        self.assertEqual(scopes,['repository_read','branch_write']);push=d._push_argv('feature/recovery',create_only=True);self.assertEqual([a for a,c in calls if a[0]=='push'],[[*push[:2],'--dry-run',*push[2:]],push])
    def test_app_dry_run_auth_failure_spends_no_intent(self):
        from unittest.mock import patch
        from devtool import github_app
        class Provider:
            def issue(_,repo,capability):return SimpleNamespace(capability=capability,public_receipt=lambda:{})
        def runner(root,argv,access,timeout):
            github_app._check_git_argv(argv,access.capability)
            if argv[0]=='push':
                self.assertIn('--dry-run',argv)
                return SimpleNamespace(returncode=128,stdout='',stderr='fatal: Authentication failed fixture-secret')
            return git(root,*[str(self.remote) if a=='origin' else a for a in argv],check=False)
        with patch.object(github_app.AppInstallationProvider,'from_environment',return_value=Provider()),patch.object(github_app,'run_git_authenticated',side_effect=runner):
            with self.assertRaisesRegex(s.DeliverySessionError,'GIT_AUTH_REJECTED; no new intent'):d.push(self.sid,yes=True,auth='app',state_root=self.state,recovery_intent_id=self.prior,prior_process_finished=True,prior_branch_absent=True)
        self.assert_unchanged()
    def test_missing_completed_attempt_refused(self):
        path=self.state/(self.sid+'.json');rec=s._read(path);rec['events']=[e for e in rec['events'] if e.get('kind')!='push_attempt_result'];s._write(path,rec)
        with self.assertRaisesRegex(s.DeliverySessionError,'completed Git attempt'):self.recover()
        self.assert_unchanged()
    def test_source_drift_and_dirty(self):
        (self.repo/'README.md').write_text('dirty\n')
        with self.assertRaisesRegex(s.DeliverySessionError,'validated native'):self.recover()
        self.assert_unchanged()
        git(self.repo,'add','.');git(self.repo,'commit','-qm','drift')
        with self.assertRaisesRegex(s.DeliverySessionError,'source drift'):self.recover()
        self.assert_unchanged()
    def test_second_failure_preserved_and_no_third_attempt(self):
        def fail(*args):self.writes+=1;return SimpleNamespace(returncode=128,stderr='fatal: Authentication failed for secret-fixture',stdout='')
        with self.assertRaisesRegex(s.DeliverySessionError,'GIT_AUTH_REJECTED'):d.push(self.sid,yes=True,state_root=self.state,remote_probe=self.probe,git_push=fail,recovery_intent_id=self.prior,prior_process_finished=True,prior_branch_absent=True)
        self.assertEqual(self.writes,1);self.assertFalse(self.status()['resumable']);self.assertEqual([i['state'] for i in self.status()['intents']],['reconciled_no_remote_change','unresolved'])
        with self.assertRaisesRegex(s.DeliverySessionError,'Original unresolved'):self.recover()
        self.assertEqual(self.writes,1);self.assertNotIn('secret-fixture',(self.state/(self.sid+'.json')).read_text())
        with self.assertRaisesRegex(s.DeliverySessionError,'Unresolved mutation'):s.record_intent(self.sid,'pr_create','fixture-pr',approved=True,state_root=self.state)
    def test_exact_remote_already_present_reconcile_only(self):
        ref='refs/heads/feature/recovery';git(self.repo,'push',str(self.remote),ref+':'+ref)
        out=self.recover();self.assertFalse(out['pushed_now']);self.assertEqual(self.writes,0);self.assertTrue(self.status()['resumable'])
    def test_missing_native_evidence(self):
        path=self.state/(self.sid+'.json');rec=s._read(path);rec.pop('native_apply');s._write(path,rec)
        with self.assertRaisesRegex(s.DeliverySessionError,'validated native'):self.recover()
        self.assert_unchanged()
    def test_classification(self):
        for msg,expected in [('fatal: unable to access URL: The requested URL returned error: 403','GIT_HTTP_403'),('fatal: could not read Username for URL: terminal prompts disabled','GIT_AUTH_REJECTED'),('GH013: Repository rule violations','GIT_REMOTE_REJECTED'),('fatal: remote end hung up unexpectedly','GIT_TRANSPORT_FAILURE')]:self.assertEqual(d._git_error_code(msg,'',1),expected)

if __name__=='__main__':unittest.main(verbosity=2)
