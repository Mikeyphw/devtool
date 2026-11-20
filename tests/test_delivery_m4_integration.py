"""M4 native delivery integration contracts: no extra journal or write engine."""
from __future__ import annotations
from types import SimpleNamespace
import subprocess
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

    def test_app_push_runs_native_journal_once_and_reverifies_head(self):
        class Access:
            repository='Mikeyphw/devtool'
            def public_receipt(self):
                return {'mode':'app','repository':self.repository,'capability':'branch_write'}
        class Provider:
            def issue(self,slug,capability):
                assert slug=='Mikeyphw/devtool'
                assert capability=='branch_write'
                return Access()
        sequence=[2,0,0]  # verified absent remote, Git push, matching remote
        calls=[]
        def one_git(root, argv, access, *, timeout):
            calls.append(argv)
            status=sequence.pop(0)
            return subprocess.CompletedProcess(argv,status,
                stdout=(SHA+'\trefs/heads/'+BRANCH+'\n') if len(calls)==3 else '',stderr='')
        with (patch.object(delivery_github,'_checked',return_value=BOUND),
              patch('devtool.github_app.AppInstallationProvider.from_environment',return_value=Provider()),
              patch('devtool.github_app.run_git_authenticated',side_effect=one_git),
              patch.object(delivery_github,'record_intent',return_value={'new':True}) as intent,
              patch.object(delivery_github,'confirm_intent') as confirmed):
            result=delivery_github.push(SESSION,yes=True,auth='app')
        self.assertEqual(result['state'],'PUSH_VERIFIED')
        self.assertEqual(result['credential']['capability'],'branch_write')
        self.assertEqual([row[0] for row in calls],['ls-remote','push','ls-remote'])
        self.assertEqual(sum('--force' in row for row in calls),0)
        self.assertEqual(intent.call_count,1)
        self.assertEqual(confirmed.call_count,1)

    def test_unapproved_app_push_cannot_write_or_journal_intent(self):
        class Access:
            def public_receipt(self): return {'mode':'app'}
        class Provider:
            def issue(self,repo,capability): return Access()
        with (patch.object(delivery_github,'_checked',return_value=BOUND),
              patch('devtool.github_app.AppInstallationProvider.from_environment',return_value=Provider()),
              patch('devtool.github_app.run_git_authenticated',return_value=
                    subprocess.CompletedProcess([],2,stdout='',stderr='')) as git,
              patch.object(delivery_github,'record_intent') as intent):
            result=delivery_github.push(SESSION,yes=False,auth='app')
        self.assertEqual(result['state'],'PUSH_REQUIRES_APPROVAL')
        self.assertEqual(git.call_count,1)
        intent.assert_not_called()

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
              patch.object(delivery_github,'pull_requests',return_value=[])):
            result=delivery_github.pr(SESSION,yes=False,auth='app')
        self.assertEqual(result['state'],'PR_REQUIRES_APPROVAL')
        self.assertIn(('Other/upstream','pr_create','base'),calls)
        self.assertIn(('Mikeyphw/fork','repository_read','head'),calls)

    def test_unverified_provider_does_not_replay_mutation(self):
        with patch.object(delivery_github,'_checked',return_value=BOUND):
            with self.assertRaises(delivery_github.DeliverySessionError):
                delivery_github.push(SESSION,auth='invalid')

if __name__=='__main__': unittest.main()
