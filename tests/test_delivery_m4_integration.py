"""M4 native delivery integration contracts: no extra journal or write engine."""
from __future__ import annotations
from types import SimpleNamespace
import subprocess
import tempfile
from pathlib import Path
from contextlib import contextmanager
import unittest
from unittest.mock import patch

from devtool import delivery_github
from devtool.cli import build_parser

SESSION = 'e' * 32
SHA = '1' * 40
BRANCH = 'feature/m4-native-github-app-delivery-20261120'
BOUND = {'repository':'Mikeyphw/devtool','head_repository':'Mikeyphw/devtool',
         'base_repository':'Mikeyphw/devtool','head':BRANCH,'base':'main',
         'head_sha':SHA,'repo_root':'/safe/worktree','base_sha':None}


class NativeM4DeliveryTests(unittest.TestCase):
    def test_canonical_auth_parser_accepts_app_for_push_pr_ci(self):
        parser=build_parser()
        for operation in ('push','pr','ci'):
            with self.subTest(operation=operation):
                parsed=parser.parse_args(['delivery','session',operation,'--id',SESSION,
                                          '--auth','app'])
                self.assertEqual(parsed.auth,'app')

    def test_default_auth_identity_preserved(self):
        parser=build_parser()
        parsed=parser.parse_args(['delivery','session','pr','--id',SESSION])
        self.assertEqual(parsed.auth,'auto')

    @contextmanager
    def _push_source(self, *, workflow=False):
        # Exercise the production Git preflight against a real isolated source
        # rather than bypassing it with a nonexistent /safe/worktree.
        with tempfile.TemporaryDirectory(prefix='m4-push-source-') as temp:
            root=Path(temp)
            def git(*args):
                return subprocess.run(['git','-C',str(root),*args],check=True,
                    capture_output=True,text=True).stdout.strip()
            git('init','-q')
            (root/'README.md').write_text('base\n')
            git('add','.')
            git('-c','user.name=Fixture','-c','user.email=fixture@example.test','commit','-qm','base')
            old=git('rev-parse','HEAD')
            path=root/('.github/workflows/check.yml' if workflow else 'data.txt')
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text('name: check\n' if workflow else 'data\n')
            git('add','.')
            git('-c','user.name=Fixture','-c','user.email=fixture@example.test','commit','-qm','change')
            yield {**BOUND,'repo_root':str(root),'head_sha':git('rev-parse','HEAD')},old

    def _approved_push(self, *, workflow, existing):
        with self._push_source(workflow=workflow) as (bound,old):
            source=bound['head_sha']; expected_write='branch_write_workflows' if workflow else 'branch_write'
            issued=[]; calls=[]; chronology=[]
            class Provider:
                def issue(self,slug,capability):
                    issued.append((slug,capability)); chronology.append('issue:'+capability)
                    return SimpleNamespace(repository=slug,capability=capability,
                        public_receipt=lambda: {'mode':'app','repository':slug,'capability':capability})
            def one_git(root,argv,access,*,timeout):
                self.assertEqual(root,bound['repo_root'])
                calls.append((list(argv),access.capability)); chronology.append('git:'+argv[0])
                if argv[0]=='push':
                    self.assertEqual(access.capability,expected_write)
                    self.assertEqual(argv,['push','--porcelain','origin',f'refs/heads/{BRANCH}:refs/heads/{BRANCH}'])
                    return subprocess.CompletedProcess(argv,0,stdout='',stderr='')
                self.assertEqual(argv,['ls-remote','--exit-code','origin',f'refs/heads/{BRANCH}'])
                remote=source if len(calls)==4 else old if existing else None
                return subprocess.CompletedProcess(argv,0 if remote else 2,
                    stdout=f'{remote}\trefs/heads/{BRANCH}\n' if remote else '',stderr='')
            def journal(*a,**kw):
                chronology.append('intent')
                return {'new':True,'intent_id':'a'*32}
            def diagnostic(*a,**kw): chronology.append('diagnostic')
            def confirmation(*a,**kw): chronology.append('confirm')
            with (patch.object(delivery_github,'_checked',return_value=bound),
                  patch('devtool.github_app.AppInstallationProvider.from_environment',return_value=Provider()),
                  patch('devtool.github_app.run_git_authenticated',side_effect=one_git),
                  patch.object(delivery_github,'record_intent',side_effect=journal) as intent,
                  patch.object(delivery_github,'record_push_diagnostic',side_effect=diagnostic) as diag,
                  patch.object(delivery_github,'confirm_intent',side_effect=confirmation) as confirmed):
                result=delivery_github.push(SESSION,yes=True,auth='app')
            self.assertEqual(result['state'],'PUSH_VERIFIED')
            self.assertEqual(result['head_sha'],source)
            self.assertIs(result['workflow_change_permission_required'],workflow)
            self.assertEqual(result['credential']['capability'],expected_write)
            self.assertEqual(issued,[('Mikeyphw/devtool','repository_read'),('Mikeyphw/devtool',expected_write)])
            self.assertEqual([(a[0],cap) for a,cap in calls],[('ls-remote','repository_read'),('ls-remote',expected_write),('push',expected_write),('ls-remote',expected_write)])
            self.assertEqual(chronology,['issue:repository_read','git:ls-remote','issue:'+expected_write,'git:ls-remote','intent','git:push','diagnostic','git:ls-remote','confirm'])
            self.assertEqual(intent.call_count,1)
            diag.assert_called_once_with(SESSION,'a'*32,'GIT_EXIT_ZERO',0,state_root=None)
            confirmed.assert_called_once_with(SESSION,'push',f'Mikeyphw/devtool:refs/heads/{BRANCH}:{source}',{'head_sha':source},state_root=None)

    def test_app_push_runs_native_journal_once_and_reverifies_head(self):
        for workflow in (False,True):
            for existing in (False,True):
                with self.subTest(workflow=workflow,existing=existing):
                    self._approved_push(workflow=workflow,existing=existing)

    def test_unapproved_app_push_cannot_write_or_journal_intent(self):
        for workflow in (False,True):
            with self.subTest(workflow=workflow), self._push_source(workflow=workflow) as (bound,old):
                issued=[]; calls=[]
                class Provider:
                    def issue(self,repo,capability):
                        issued.append((repo,capability))
                        if capability!='repository_read': raise AssertionError('write credential issued without approval')
                        return SimpleNamespace(repository=repo,capability=capability)
                def probe(root,argv,credential,*,timeout):
                    calls.append(argv)
                    self.assertEqual(credential.capability,'repository_read')
                    self.assertEqual(argv[0],'ls-remote')
                    return subprocess.CompletedProcess(argv,2,stdout='',stderr='')
                with (patch.object(delivery_github,'_checked',return_value=bound),
                      patch('devtool.github_app.AppInstallationProvider.from_environment',return_value=Provider()),
                      patch('devtool.github_app.run_git_authenticated',side_effect=probe),
                      patch.object(delivery_github,'record_intent') as intent,
                      patch.object(delivery_github,'record_push_diagnostic') as diagnostic,
                      patch.object(delivery_github,'confirm_intent') as confirm):
                    result=delivery_github.push(SESSION,yes=False,auth='app')
                self.assertEqual(result['state'],'PUSH_REQUIRES_APPROVAL')
                self.assertEqual(issued,[('Mikeyphw/devtool','repository_read')])
                self.assertEqual(len(calls),2)
                self.assertIs(result['workflow_write_scope_required'],workflow)
                intent.assert_not_called(); diagnostic.assert_not_called(); confirm.assert_not_called()

    def test_app_pr_scopes_upstream_and_head_installations_separately(self):
        item={**BOUND,'repository':'Mikeyphw/fork','head_repository':'Mikeyphw/fork',
              'base_repository':'Other/upstream','base_sha':'f'*40}
        calls=[]
        fake=SimpleNamespace(_token='scoped')
        def factory(slug, capability, *, role='base'):
            calls.append((slug,capability,role))
            return fake
        def remote(client,slug,branch):
            return item['head_sha'] if slug=='Mikeyphw/fork' else item['base_sha']
        with (patch.object(delivery_github,'_checked',return_value=item),
              patch('devtool.github_app.make_app_client',side_effect=factory),
              patch.object(delivery_github,'_remote_head',side_effect=remote),
              patch.object(delivery_github,'pull_requests',return_value=[]),
              patch.object(delivery_github,'pr_intent_status',return_value=None)):
            result=delivery_github.pr(SESSION,yes=False,auth='app')
        self.assertEqual(result['state'],'PR_REQUIRES_APPROVAL')
        self.assertIn(('Other/upstream','pr_create','base'),calls)
        self.assertIn(('Mikeyphw/fork','repository_read','head'),calls)

    def test_unverified_provider_does_not_replay_mutation(self):
        with patch.object(delivery_github,'_checked',return_value=BOUND):
            with self.assertRaises(delivery_github.DeliverySessionError):
                delivery_github.push(SESSION,auth='invalid')

if __name__=='__main__': unittest.main()
