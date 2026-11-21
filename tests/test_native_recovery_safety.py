"""Pure/unit and safe negative regressions; never access GitHub or delete a repository."""
from __future__ import annotations
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from devtool.recovery import engine as rr
from devtool.recovery import safety as rs


def execgit(*cmd,cwd=None,**kwargs):
    return subprocess.run(['git',*map(str,cmd)],cwd=cwd,check=True,
                          text=True,capture_output=True,**kwargs).stdout.strip()

class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.cfg={'schema':1,'repo':'x/Repo', 'secrets':{
             'actions':[{'name':'TOKEN','updated_at':'x'}],
             'dependabot':[{'name':'BOT'}], 'codespaces':[],},
             'environments':{'prod':{'secrets':[{'name':'KEY'}], 'variables':[],
                  'definition':{'protection_rules':[]}}}}

    def test_requirements_are_scope_bound(self):
        self.assertEqual(rs.secret_requirements(self.cfg),
                         [('actions','','TOKEN'),('dependabot','','BOT'),('environment','prod','KEY')])

    def test_missing_vault_sources_cannot_be_claimed_restorable(self):
        required=rs.secret_requirements(self.cfg)
        vault={'schema':1,'repo':'x/Repo','secrets':{'actions':{'TOKEN':'abc'}}}
        missing=rs.validate_vault(vault,'x/Repo',required)
        self.assertEqual({x['name'] for x in missing},{'BOT','KEY'})

    def test_exact_vault_binding_and_no_empty_values(self):
        with self.assertRaises(rs.SafetyError):
            rs.validate_vault({'schema':1,'repo':'x/wrong','secrets':{}},'x/Repo',[])
        missing=rs.validate_vault({'schema':1,'repo':'x/Repo','secrets':{
             'actions':{'TOKEN':''}}},'x/Repo',[('actions','','TOKEN')])
        self.assertEqual(len(missing),1)

    def test_manual_obligations_not_silent(self):
        self.cfg.update(webhooks=[{'id':1}], deploy_keys=[{'id':2}],
                        codespaces_inventory='unsupported-or-inaccessible')
        self.assertEqual(len(rr.config_manual_obligations(self.cfg)),3)

    def test_standard_protection_projection(self):
        old={'required_status_checks':{'strict':True,'contexts':['build']},
             'enforce_admins':{'enabled':True},
             'required_pull_request_reviews':{'required_approving_review_count':2},
             'restrictions':None, 'allow_force_pushes':{'enabled':False}}
        expected=rr.normalized_protection_payload(old)
        self.assertEqual(expected['required_status_checks']['contexts'],['build'])
        self.assertTrue(expected['enforce_admins'])
        self.assertFalse(expected['allow_force_pushes'])
        rr.verify_protection(old,old)
        with self.assertRaises(rr.Refused):
            rr.verify_protection(old, {**old,'enforce_admins':{'enabled':False}})

class VaultSecurityTests(unittest.TestCase):
    def test_age_decrypt_never_puts_values_in_argv(self):
        with tempfile.TemporaryDirectory() as td:
            a=Path(td)/'vault.age';b=Path(td)/'id';a.write_text('fake ciphertext');b.write_text('fake key')
            a.chmod(0o600);b.chmod(0o600)
            calls=[]
            def fake_run(args,**kw):
                calls.append((args,kw));return types.SimpleNamespace(stdout=json.dumps({
                     'schema':1,'repo':'owner/repo','secrets':{'actions':{'TOKEN':'super-secret'}}}))
            result=rs.parse_age_vault(a,b,'owner/repo',fake_run)
            self.assertEqual(result['secrets']['actions']['TOKEN'],'super-secret')
            self.assertNotIn('super-secret',' '.join(calls[0][0]))
            self.assertEqual(calls[0][0][0],'age')

    def test_untrusted_vault_permissions_refused(self):
        with tempfile.TemporaryDirectory() as td:
            a=Path(td)/'vault.age';b=Path(td)/'id';a.write_text('x');b.write_text('y')
            a.chmod(0o644);b.chmod(0o600)
            with self.assertRaises(rs.SafetyError):
                rs.parse_age_vault(a,b,'owner/repo',lambda x,**kw:None)

    def test_duplicate_keys_refused(self):
        with tempfile.TemporaryDirectory() as td:
            a=Path(td)/'vault.age';b=Path(td)/'id';a.write_text('x');b.write_text('y')
            a.chmod(0o600);b.chmod(0o600)
            fake=lambda *args,**kw:types.SimpleNamespace(stdout='{"schema":1,"schema":1}')
            with self.assertRaises(rs.SafetyError):rs.parse_age_vault(a,b,'x/y',fake)

    def test_secret_missing_preflight_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            session=Path(td);(session/'primary').mkdir()
            rr.write_json(session/'primary'/'repo.json',{'full_name':'x/y','fork':False})
            rr.write_json(session/'primary'/'source-refs.json',{})
            (session/'primary'/'metadata').mkdir()
            rr.write_json(session/'primary'/'metadata'/'pulls.json',[])
            (session/'primary'/'EMPTY_GIT_REPOSITORY').write_text('empty')
            rr.write_json(session/'primary'/'configuration.json',{'schema':1,
                   'secrets':{'actions':[{'name':'S'}]},'environments':{},
                   'codespaces_inventory':'available'})
            rr.write_json(session/'primary'/'dependency-audit.json',{'findings':[], 'scan_gaps':[]})
            state={'repo':'x/y','stage':'backup','session_id':'session','schema':1}
            with self.assertRaisesRegex(rr.Refused,'readiness BLOCKED'):
                rr.prepare_security(session,state)
            saved=rr.read_json(session/'readiness.json')
            self.assertEqual(saved['missing_secrets'][0]['name'],'S')
            self.assertFalse(saved['secret_values_in_snapshot'])
            rr.prepare_security(session,state,accept_missing_secrets=True)
            self.assertTrue(state['accept_missing_secrets'])

    def test_reference_preflight_requires_manual_waiver(self):
        with tempfile.TemporaryDirectory() as td:
            session=Path(td);(session/'primary').mkdir()
            rr.write_json(session/'primary'/'repo.json',{'full_name':'x/y','fork':False})
            rr.write_json(session/'primary'/'source-refs.json',{})
            (session/'primary'/'metadata').mkdir()
            rr.write_json(session/'primary'/'metadata'/'pulls.json',[])
            (session/'primary'/'EMPTY_GIT_REPOSITORY').write_text('empty')
            rr.write_json(session/'primary'/'configuration.json',{'schema':1,
                   'secrets':{'actions':[]},'environments':{},'codespaces_inventory':'available'})
            rr.write_json(session/'primary'/'dependency-audit.json',{'findings':[
                 {'status':'will-change','kind':'run-id','path':'workflow.yml'}], 'scan_gaps':[]})
            state={'repo':'x/y','stage':'backup','session_id':'session','schema':1}
            with self.assertRaises(rr.Refused):rr.prepare_security(session,state)
            rr.prepare_security(session,state,accept_manual_recovery=True)
            self.assertTrue(state['manual_obligations'])

class DependencyScanTests(unittest.TestCase):
    def test_reachable_sha_and_nonportable_pr_run_refs(self):
        with tempfile.TemporaryDirectory() as td:
            working=Path(td)/'work';working.mkdir()
            execgit('init','-q',working)
            execgit('-C',working,'config','user.email','test@example.org')
            execgit('-C',working,'config','user.name','Test')
            (working/'README.md').write_text('hello')
            (working/'.github/workflows').mkdir(parents=True,exist_ok=True)
            (working/'.github/workflows/ci.yml').write_text('name: original\n')
            execgit('-C',working,'add','.')
            execgit('-C',working,'commit','-qm','base')
            sha=execgit('-C',working,'rev-parse','HEAD')
            repo='demo/app'
            (working/'.github/workflows').mkdir(parents=True,exist_ok=True)
            (working/'.github/workflows/ci.yml').write_text(f'''name: ci
jobs:
  test:
    uses: {repo}/.github/workflows/ci.yml@{sha}
# refs/pull/15/merge
# gh pr view 15
# https://github.com/demo/app/actions/runs/12345
# repo:demo/app:ref:refs/heads/main
''')
            execgit('-C',working,'add','.')
            execgit('-C',working,'commit','-qm','workflow')
            mirror=Path(td)/'mirror.git'
            execgit('clone','-q','--mirror',working,mirror)
            refs=rr.refs_from_git(mirror)
            scan=rs.scan_dependencies(mirror,repo,refs,rr.run)
            kinds={x['kind'] for x in scan['findings']}
            self.assertTrue({'pull-request-git-ref','pull-request-number','run-id',
                             'repository-action-ref','oidc-or-repo-identity'}<=kinds)
            self.assertEqual(scan['referenced_pr_numbers'],[15])
            self.assertIn('verified-reachable',{x['status'] for x in scan['findings']})
            self.assertNotIn('repo:demo/app:ref:refs/heads/main',str(scan['findings']))
            (working/'.github/workflows/ci.yml').write_text(
                f'jobs:\n  bad:\n    uses: {repo}/.github/workflows/MISSING.yml@{sha}\n')
            execgit('-C',working,'add','.')
            execgit('-C',working,'commit','-qm','bad workflow')
            execgit('clone','-q','--mirror',working,Path(td)/'missing.git')
            missing=Path(td)/'missing.git'
            scan_missing=rs.scan_dependencies(missing,repo,rr.refs_from_git(missing),rr.run)
            self.assertIn('pinned-workflow-missing',
                          {x['status'] for x in scan_missing['findings']})

    def test_migration_ids_dont_invent_run_identity(self):
        deps={'referenced_run_ids':[77], 'scan_gaps':[],
              'findings':[{'kind':'run-id','status':'will-change','path':'a','line':1}]}
        result=rs.migration_plan(deps,{'pr:4':{'number':9},'pr:5':{'archived_only':True}},
                      100,200,[{'path':'a.yml','id':5}],[{'path':'a.yml','id':8}])
        self.assertEqual(result['pull_requests']['4'],9)
        self.assertIsNone(result['pull_requests']['5'])
        self.assertEqual(result['workflow_ids']['5'],8)
        self.assertIsNone(result['run_ids']['77'])

class RecoveryTests(unittest.TestCase):
    def test_interrupted_closed_pr_recovers_through_intermediate_state(self):
        old={'title':'test','head':{'ref':'feature','sha':'a'*40},
             'base':{'ref':'main','sha':'b'*40},'draft':False,'state':'closed'}
        new={**old,'state':'open'}
        rr.verify_import('owner/repo','pr',new,old,allow_intermediate_state=True)
        with self.assertRaises(rr.Refused):rr.verify_import('owner/repo','pr',new,old)

    def test_unjournaled_replacement_does_not_auto_adopt(self):
        with tempfile.TemporaryDirectory() as td:
            session=Path(td);(session/'primary').mkdir()
            state={'repo':'owner/repo','original_repo_id':10,'topology':'standalone',
                   'parent_repo':None,'stage':'create-intent','created_new_id':None,
                   'original_visibility':'private'}
            new={'id':20,'fork':False,'visibility':'private','parent':None,
                 'created_at':dt.datetime.now(dt.timezone.utc).isoformat()}
            with patch.object(rr,'repo_info',return_value=new):
                with self.assertRaisesRegex(rr.Refused,'no journal provenance'):
                    rr.create_replacement(session,state)
            self.assertIsNone(state['created_new_id'])

    def test_local_only_deletion_gate_refuses(self):
        with tempfile.TemporaryDirectory() as td:
            state={'accept_local_only_risk':False}
            with self.assertRaisesRegex(rr.Refused,'independent-filesystem'):
                rr.require_independent_backup(Path(td),state)

    def test_no_forcepush_or_auto_merge_in_new_changes(self):
        src=Path(rr.__file__).read_text()
        self.assertNotIn('force-push',src.split('def restore_security_configuration')[1])
        self.assertNotIn('gh pr merge',src)

class SecurityRestorationTests(unittest.TestCase):
    def test_actions_remain_disabled_while_manual_recovery_is_needed(self):
        with tempfile.TemporaryDirectory() as td:
            session=Path(td);(session/'primary'/'actions').mkdir(parents=True)
            rr.write_json(session/'primary'/'actions'/'permissions.json',{'enabled':True})
            state={'schema':1,'session_id':session.name,'repo':'x/y','stage':'actions-done'}
            calls=[]
            def fake_api(endpoint,*,method='GET',payload=None,**kw):
                calls.append((endpoint,method,payload))
                if method!='GET':raise AssertionError('unsafe Actions enable call')
                return {'enabled':False}
            with patch.object(rr,'api',side_effect=fake_api):
                rr.restore_actions_enabled_after_safety(session,state,unresolved=True)
            self.assertTrue(state['actions_left_disabled_for_manual_recovery'])
            self.assertTrue(all(x[1]=='GET' for x in calls))

    def test_actions_suspend_before_ref_push(self):
        with tempfile.TemporaryDirectory() as td:
            session=Path(td);state={'schema':1,'session_id':session.name,
                                    'repo':'x/y','stage':'create-intent'}
            enabled={'value':True}
            calls=[]
            def fake_api(endpoint,*,method='GET',payload=None,**kw):
                calls.append((method,payload))
                if method=='PUT':enabled['value']=payload['enabled']
                return {'enabled':enabled['value'],'allowed_actions':'all'}
            with patch.object(rr,'api',side_effect=fake_api):
                rr.suspend_actions_for_restore(session,state)
            self.assertFalse(enabled['value'])
            self.assertTrue(state['actions_suspended'])
            self.assertTrue(any(x[0]=='PUT' and x[1]['enabled'] is False for x in calls))

    def test_secret_reprovision_only_uses_stdin(self):
        with tempfile.TemporaryDirectory() as td:
            session=Path(td);(session/'primary').mkdir()
            repo='x/y'
            vault=Path(td)/'vault.age';identity=Path(td)/'age.key'
            vault.write_text('encrypted bytes');identity.write_text('key')
            vault.chmod(0o600);identity.chmod(0o600)
            cfg={'schema':1,'repo':repo,'repo_id':10,
                 'workflow_permissions':{'default_workflow_permissions':'read',
                                         'can_approve_pull_request_reviews':False},
                 'variables':[],'secrets':{'actions':[{'name':'TEST_SECRET'}],
                     'dependabot':[],'codespaces':[]},'environments':{},
                 'organization':{'status':'not-applicable','selected':[]},
                 'deploy_keys':[],'branch_protections':{},'rulesets':[]}
            rr.write_json(session/'primary'/'configuration.json',cfg)
            state={'schema':1,'session_id':session.name,'repo':repo,'stage':'actions-done',
                   'created_new_id':20,'vault_sha256':rr.sha256(vault),'missing_secret_names':[]}
            calls=[]
            def fake_run(argv,**kw):
                calls.append((argv,kw))
                return types.SimpleNamespace(returncode=0,stdout='',stderr='')
            with patch.object(rr,'api',return_value=cfg['workflow_permissions']),\
                 patch.object(rr,'get_new_repo',return_value={'id':20}),\
                 patch.object(rr,'collect',return_value=[{'name':'TEST_SECRET'}]),\
                 patch.object(rr,'run',side_effect=fake_run),\
                 patch.object(rr.safety,'parse_age_vault',return_value={
                      'schema':1,'repo':repo,'secrets':{'actions':{'TEST_SECRET':'TOP_SECRET_VALUE'}}}):
                rr.restore_security_configuration(session,state,vault_file=vault,age_identity=identity)
            self.assertEqual(state['stage'],'configuration-done')
            self.assertEqual(len(calls),1)
            self.assertEqual(calls[0][1]['input_data'],'TOP_SECRET_VALUE')
            self.assertNotIn('TOP_SECRET_VALUE',' '.join(calls[0][0]))
            self.assertNotIn('TOP_SECRET_VALUE',str(rr.read_json(session/'state.json')))

    def test_ci_requires_explicit_ack_before_remote_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            session=Path(td);state={'schema':1,'session_id':session.name,'repo':'x/y',
                                    'stage':'done','actions_left_disabled_for_manual_recovery':False}
            rr.write_json(session/'state.json',state)
            with patch.object(rr,'get_new_repo',side_effect=AssertionError('must not reach network')):
                with self.assertRaisesRegex(rr.Refused,'--ack-dispatch'):
                    rr.verified_ci_dispatch(session,workflow='.github/workflows/ci.yml',
                                            ref='main',ack_dispatch='wrong')

if __name__=='__main__':unittest.main()
