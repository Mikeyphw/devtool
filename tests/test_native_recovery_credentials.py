"""Credential recovery: permission-gated PAT proof and idempotent GitHub App repair.

Every GitHub API response is mocked; no live credential, deletion or mutation.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from devtool.recovery import engine as rr
from devtool.recovery import credentials as cr

REPO='Mikeyphw/MMRL'
OTHER='Mikeyphw/MMRL-LSPosed'
PAT={'name':'MMRL_FEDERATION_TOKEN','kind':'fine_grained_pat',
     'repositories':[REPO,OTHER], 'token_env':'REPO_REBUILD_FEDERATION_TEST_TOKEN',
     'probe':'actions-secrets','secret_hosts':[OTHER]}
APP={'name':'MMRL_APP','kind':'github_app','installation_id':321,
     'repositories':[REPO,OTHER],'admin_token_env':'REPO_REBUILD_APP_TEST_TOKEN',
     'auto_repair':True,'required_permissions':{'contents':'read'}}


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.session=Path(self.temp.name)/'ses-abc'
        (self.session/'primary').mkdir(parents=True)
        rr.write_json(self.session/'primary'/'configuration.json',{'secrets':{
            'actions':[{'name':'MMRL_FEDERATION_TOKEN'}], 'dependabot':[], 'codespaces':[]},
            'environments':{}})
        self.state={'schema':1,'repo':REPO,'session_id':'ses-abc','stage':'backup',
                    'original_repo_id':111,'created_new_id':999}
        rr.write_json(self.session/'state.json',self.state)

    def policy(self, *items):
        path=Path(self.temp.name)/'credentials.json'
        rr.write_json(path,{'schema':1,'repository':REPO,'credentials':list(items)})
        return path

    def test_pat_missing_value_not_proof_and_guided_repair(self):
        with patch.dict(os.environ,{'REPO_REBUILD_FEDERATION_TEST_TOKEN':''}), patch.object(rr,'api',return_value={'name':'MMRL_FEDERATION_TOKEN'}):
            p=cr._pat_verify(PAT,source='replacement')
        self.assertEqual(p['status'],'unverified')
        self.assertIn('cannot reveal',p['reason'])
        self.assertIn('personal-access-tokens',p['guided_repair']['url'])

    def test_pat_authentication_alone_not_repository_authorization(self):
        pat={**PAT,'probe':None,'secret_hosts':[]}
        with patch.dict(os.environ,{'REPO_REBUILD_FEDERATION_TEST_TOKEN':'dummy'}),\
             patch.object(cr,'_token_api',return_value={'login':'tester'}) as call:
            p=cr._pat_verify(pat,source='original')
        self.assertEqual(p['status'],'unverified')
        self.assertIn('unproved',p['reason'])
        call.assert_called_once_with('user',pat['token_env'])

    def test_pat_proves_each_protected_endpoint_not_public_repo_metadata(self):
        pat={**PAT,'secret_hosts':[]}
        called=[]
        def fake_api(path,env,**kw):
            called.append(path)
            if path=='user':return {'login':'tester'}
            if path in (f'repos/{REPO}/actions/secrets',f'repos/{OTHER}/actions/secrets'):
                return {'total_count':0,'secrets':[]}
            raise AssertionError(path)
        with patch.dict(os.environ,{'REPO_REBUILD_FEDERATION_TEST_TOKEN':'dummy'}),\
             patch.object(cr,'_token_api',side_effect=fake_api),\
             patch.object(rr,'repo_info',side_effect=lambda x:{'id':999 if x==REPO else 222,'permissions':{'admin':True}}):
            p=cr._pat_verify(pat,source='replacement')
        self.assertEqual(p['status'],'verified')
        self.assertEqual(len(p['repositories']),2)
        self.assertNotIn('repos/'+REPO,called)

    def test_pat_missing_repo_permission_never_succeeds(self):
        def forbidden(path,env,**kw):
            if path=='user':return {'login':'tester'}
            raise rr.Refused('403')
        with patch.dict(os.environ,{'REPO_REBUILD_FEDERATION_TEST_TOKEN':'dummy'}),\
             patch.object(cr,'_token_api',side_effect=forbidden),\
             patch.object(rr,'api',return_value={'name':'MMRL_FEDERATION_TOKEN'}):
            self.assertEqual(cr._pat_verify(PAT,source='replacement')['status'],'unverified')

    def test_strict_policy_schema_and_cross_repo_requirements(self):
        with self.assertRaises(rr.Refused):cr._verify_policy({'schema':1,'repository':REPO,'credentials':[
            {**PAT,'repositories':[OTHER]}]},REPO)
        with self.assertRaises(rr.Refused):cr._verify_policy({'schema':1,'repository':REPO,'credentials':[
            PAT,PAT]},REPO)
        with self.assertRaises(rr.Refused):cr._verify_policy({'schema':1,'repository':REPO,'credentials':[
            {**PAT,'probe':'contents'}]},REPO)
        cr._verify_policy({'schema':1,'repository':REPO,'credentials':[PAT,APP]},REPO)

    def test_unknown_high_risk_secret_blocks_predelete_by_default(self):
        with self.assertRaisesRegex(rr.Refused,'pre-deletion feasibility BLOCKED'):
            cr.preflight(self.session,self.state,None)
        data=rr.read_json(self.session/'credential-readiness.json')
        self.assertIn('MMRL_FEDERATION_TOKEN',data['unclassified'])
        self.assertEqual(data['status'],'blocked')

    def test_generic_manual_acceptance_is_not_credential_risk_acceptance(self):
        with self.assertRaises(rr.Refused):cr.preflight(self.session,self.state,None,accept_manual=False)
        result=cr.preflight(self.session,self.state,None,accept_manual=True)
        self.assertIn('credential-obligations',result['status'])
        self.assertNotEqual(result['status'],'verified')

    def test_pat_preflight_requires_manual_risk_even_if_original_access_works(self):
        path=self.policy({**PAT,'secret_hosts':[]})
        with patch.object(cr,'_pat_verify',return_value={'name':'MMRL_FEDERATION_TOKEN',
              'kind':'fine_grained_pat','status':'verified'}):
            with self.assertRaises(rr.Refused):
                cr.preflight(self.session,self.state,path)
            x=cr.preflight(self.session,self.state,path,accept_manual=True)
        self.assertIn('PAT selection cannot be expanded',str(x['manual_obligations']))

    def test_policy_immutable_for_resumed_preparation(self):
        path=self.policy({**PAT,'secret_hosts':[]})
        cr.preflight(self.session,self.state,path,accept_manual=True)
        rr.write_json(path,{'schema':1,'repository':REPO,'credentials':[APP]})
        with self.assertRaisesRegex(rr.Refused,'immutable'):
            cr.preflight(self.session,self.state,path,accept_manual=True)

    def test_app_verified_with_correct_existing_installation(self):
        with patch.dict(os.environ,{'REPO_REBUILD_APP_TEST_TOKEN':'dummy'}),\
             patch.object(rr,'repo_info',side_effect=lambda x:{'id':999 if x==REPO else 222,'permissions':{'admin':True}}),\
             patch.object(cr,'_app_inventory',side_effect=lambda *a,**kw:{
                 'selection':'selected','current_repo_selected':True}):
            out=cr._app_verify(APP,REPO,apply=True)
        self.assertEqual(out['status'],'verified')
        self.assertEqual(len(out['repositories']),2)

    def test_app_update_is_idempotent_and_session_bound(self):
        grants={222};writes=[]
        def inventory(item, *, expected_repo,expected_id):
            return {'selection':'selected','current_repo_selected':expected_id in grants}
        def api(endpoint,var,*,method='GET'):
            self.assertEqual(method,'PUT')
            self.assertEqual(endpoint,'user/installations/321/repositories/999')
            writes.append(endpoint);grants.add(999)
            return {}
        with patch.dict(os.environ,{'REPO_REBUILD_APP_TEST_TOKEN':'dummy'}),\
             patch.object(rr,'repo_info',side_effect=lambda x:{'id':999 if x==REPO else 222,'permissions':{'admin':True}}),\
             patch.object(cr,'_app_inventory',side_effect=inventory),\
             patch.object(cr,'_token_api',side_effect=api):
            first=cr._app_verify(APP,REPO,apply=True,journal=self.state,session=self.session)
            second=cr._app_verify(APP,REPO,apply=True,journal=self.state,session=self.session)
        self.assertEqual(len(writes),1)
        self.assertEqual(first['status'],second['status'])
        self.assertEqual(self.state['app_intents']['MMRL_APP']['state'],'verified')

    def test_app_lost_response_recovers_by_verifying_prior_put(self):
        grants={222};writes=[]
        def lost(endpoint,env,*,method='GET'):
            writes.append(endpoint);grants.add(999)
            raise rr.Refused('lost GitHub API response')
        with patch.dict(os.environ,{'REPO_REBUILD_APP_TEST_TOKEN':'dummy'}),\
             patch.object(rr,'repo_info',side_effect=lambda x:{'id':999 if x==REPO else 222,'permissions':{'admin':True}}),\
             patch.object(cr,'_app_inventory',side_effect=lambda *a,**kw:{'selection':'selected',
                            'current_repo_selected':kw['expected_id'] in grants}),\
             patch.object(cr,'_token_api',side_effect=lost):
            with self.assertRaises(rr.Refused):
                cr._app_verify(APP,REPO,apply=True,journal=self.state,session=self.session)
            recovered=cr._app_verify(APP,REPO,apply=True,journal=self.state,session=self.session)
        self.assertEqual(recovered['status'],'verified')
        self.assertEqual(len(writes),1)
        self.assertEqual(self.state['app_intents']['MMRL_APP']['state'],'verified')

    def test_app_refuses_different_repository_id_after_intent(self):
        self.state['app_intents']={'MMRL_APP':{'repository':REPO,'repository_id':123,
                                                  'installation_id':321,'state':'intent'}}
        with patch.dict(os.environ,{'REPO_REBUILD_APP_TEST_TOKEN':'dummy'}),\
             patch.object(rr,'repo_info',return_value={'id':999,'permissions':{'admin':True}}),\
             patch.object(cr,'_app_inventory') as inventory:
            with self.assertRaisesRegex(rr.Refused,'journal'):
                cr._app_verify(APP,REPO,apply=True,journal=self.state,session=self.session)
            inventory.assert_not_called()

    def test_app_refuses_wrong_installation_owner_or_permissions(self):
        def fake_pages(path,env,*,key):
            if key=='installations':return [{'id':321,'account':{'login':'WrongOwner'},
                   'repository_selection':'selected','permissions':{'contents':'read'},'suspended_at':None}]
            raise AssertionError(path)
        with patch.dict(os.environ,{'REPO_REBUILD_APP_TEST_TOKEN':'dummy'}),\
             patch.object(cr,'_pages',side_effect=fake_pages):
            with self.assertRaisesRegex(rr.Refused,'account mismatch'):
                cr._app_inventory(APP,expected_repo=REPO,expected_id=999)

    def test_app_missing_authorization_stays_unverified_without_mutation(self):
        with patch.dict(os.environ,{'REPO_REBUILD_APP_TEST_TOKEN':'dummy'}),\
             patch.object(rr,'repo_info',side_effect=lambda x:{'id':999 if x==REPO else 222,'permissions':{'admin':True}}),\
             patch.object(cr,'_app_inventory',return_value={'selection':'selected',
                     'current_repo_selected':False}),\
             patch.object(cr,'_token_api') as attempt:
            result=cr._app_verify(APP,REPO,apply=False)
        self.assertEqual(result['status'],'unverified')
        attempt.assert_not_called()

    def test_no_token_appears_in_argv_or_diagnostic(self):
        fake=type('Proc',(),{'returncode':1,'stdout':'',
                   'stderr':'Authorization: Bearer secret-token-1234'})()
        with patch.dict(os.environ,{'SECRET_TEST_TOKEN':'secret-token-1234'}),\
             patch.object(cr.subprocess,'run',return_value=fake) as launched:
            with self.assertRaises(rr.Refused) as cm:
                cr._token_api('user','SECRET_TEST_TOKEN')
        self.assertNotIn('secret-token-1234',str(cm.exception))
        args,kw=launched.call_args
        self.assertNotIn('secret-token-1234',repr(args))
        self.assertEqual(kw['env']['GH_TOKEN'],'secret-token-1234')

    def test_legacy_session_requires_policy_if_high_risk_secret_in_snapshot(self):
        with patch.object(rr,'repo_info',return_value={'id':999}):
            result=cr.post_restore(self.session,self.state,record=False)
        self.assertEqual(result['status'],'CREDENTIAL_ACCESS_UNVERIFIED')
        self.assertEqual(result['unclassified'],['MMRL_FEDERATION_TOKEN'])

    def test_previously_bound_policy_cannot_be_replaced(self):
        file=self.policy({**PAT,'secret_hosts':['Mikeyphw/MMRL-Shared']})
        cr.bind_post_restore_policy(self.session,self.state,file)
        digest=self.state['credential_policy_sha256']
        rr.write_json(file,{'schema':1,'repository':REPO,'credentials':[APP]})
        with self.assertRaises(rr.Refused):cr.bind_post_restore_policy(self.session,self.state,file)
        self.assertEqual(self.state['credential_policy_sha256'],digest)

    def test_post_access_unverified_is_not_reported_operational(self):
        rr.write_json(self.session/'report.json',{'recovery_classification':'RESTORED_NOT_OPERATIONALLY_TESTED',
                                                  'fully_operationally_verified':False})
        result=cr.post_restore(self.session,self.state,record=True)
        self.assertEqual(result['status'],'CREDENTIAL_ACCESS_UNVERIFIED')
        report=rr.read_json(self.session/'report.json')
        self.assertFalse(report['fully_operationally_verified'])
        self.assertEqual(report['recovery_classification'],'RESTORED_WITH_ACTION_REQUIRED')
        self.assertIn('credential authorization unverified',str(report['manual_recovery_requirements']))

    def test_stored_secret_not_verified_just_because_token_can_access_repositories(self):
        with patch.dict(os.environ,{'REPO_REBUILD_FEDERATION_TEST_TOKEN':'dummy'}),\
             patch.object(cr,'_token_api',return_value={'login':'tester'}),\
             patch.object(rr,'repo_info',return_value={'id':999}),\
             patch.object(rr,'api',return_value={'name':'MMRL_FEDERATION_TOKEN'}):
            result=cr._pat_verify(PAT,source='replacement')
        self.assertEqual(result['status'],'unverified')
        self.assertIn('stored Actions secret value cannot be read',result['reason'])

    def test_pat_reprovision_uses_stdin_only_and_requires_admin(self):
        with patch.object(rr,'repo_info',return_value={'id':222,'permissions':{'admin':True}}),\
             patch.object(rr,'api',return_value={'name':'MMRL_FEDERATION_TOKEN'}),\
             patch.object(cr.subprocess,'run',return_value=type('R',(),{'returncode':0})()) as subprocess_call:
            cr._set_actions_secret(OTHER,'MMRL_FEDERATION_TOKEN','token-not-for-argv')
        args,kwargs=subprocess_call.call_args
        self.assertNotIn('token-not-for-argv',repr(args))
        self.assertEqual(kwargs['input'],'token-not-for-argv')
        with patch.object(rr,'repo_info',return_value={'id':222,'permissions':{'admin':False}}):
            with self.assertRaisesRegex(rr.Refused,'admin required'):
                cr._set_actions_secret(OTHER,'MMRL_FEDERATION_TOKEN','token')

    def test_pat_reprovision_evidence_only_after_verified_token_and_secret_write(self):
        fake={**PAT,'secret_hosts':[OTHER]}
        with patch.dict(os.environ,{'REPO_REBUILD_FEDERATION_TEST_TOKEN':'dummy'}),\
             patch.object(cr,'_token_api',return_value={'login':'tester'}),\
             patch.object(rr,'repo_info',return_value={'id':999}),\
             patch.object(rr,'api',return_value={'name':'MMRL_FEDERATION_TOKEN'}),\
             patch.object(cr,'_set_actions_secret') as setter:
            result=cr._pat_verify(fake,source='replacement',provision=True,
                                  session=self.session,journal=self.state)
        self.assertEqual(result['status'],'verified')
        setter.assert_called_once_with(OTHER,'MMRL_FEDERATION_TOKEN','dummy')
        self.assertEqual(self.state['pat_provision_intents']['MMRL_FEDERATION_TOKEN@'+OTHER]['phase'],
                         'verified')
        self.assertNotIn('dummy',str(rr.read_json(self.session/'state.json')))

    def test_embedded_token_property_is_rejected_and_not_written_to_journal(self):
        with self.assertRaisesRegex(rr.Refused,'unknown credential properties'):
            cr._verify_policy({'schema':1,'repository':REPO,'credentials':[
                {**PAT,'token':'plain-secret'}]},REPO)

    def test_builtin_github_token_does_not_force_pat_recovery(self):
        config={'secrets':{'actions':[{'name':'GITHUB_TOKEN'}], 'dependabot':[],
                           'codespaces':[]},'environments':{}}
        self.assertEqual(cr.secret_candidates(config),[])

    def test_source_reference_detected_even_if_secret_not_in_target_inventory(self):
        rr.write_json(self.session/'primary'/'configuration.json',
                      {'secrets':{'actions':[],'dependabot':[],'codespaces':[]},
                       'environments':{}})
        rr.write_json(self.session/'primary'/'dependency-audit.json',
                      {'referenced_secret_names':['MMRL_FEDERATION_TOKEN','GITHUB_TOKEN']})
        with self.assertRaisesRegex(rr.Refused,'pre-deletion feasibility'):
            cr.preflight(self.session,self.state,None)
        saved=rr.read_json(self.session/'credential-readiness.json')
        self.assertEqual(saved['candidates'],['MMRL_FEDERATION_TOKEN'])

    def test_workflow_source_scan_records_external_credential_name(self):
        from devtool.recovery import safety
        self.assertEqual(safety.SECRET_EXPR.findall('${{ secrets.MMRL_FEDERATION_TOKEN }}'),
                         ['MMRL_FEDERATION_TOKEN'])

    def test_app_cannot_apply_without_current_repository_admin_rights(self):
        with patch.dict(os.environ,{'REPO_REBUILD_APP_TEST_TOKEN':'dummy'}),\
             patch.object(rr,'repo_info',return_value={'id':999,'permissions':{'admin':False}}),\
             patch.object(cr,'_app_inventory') as inventory:
            result=cr._app_verify(APP,REPO,apply=True)
        self.assertEqual(result['status'],'unverified')
        inventory.assert_not_called()

    def test_app_mutation_requires_specific_ack_before_api_access(self):
        from argparse import Namespace
        from devtool.recovery import native_cli
        with patch.object(native_cli,'locked') as locked:
            exit_code=native_cli.run_native(Namespace(recovery_command='credentials',
                        session=str(self.session), apply_app=True, apply_pat=False,
                        ack_app_repair=None,ack_pat_provision=None))
        self.assertEqual(exit_code,2)
        locked.assert_not_called()

    def test_pat_mutation_requires_specific_ack_before_api_access(self):
        from argparse import Namespace
        from devtool.recovery import native_cli
        with patch.object(native_cli,'locked') as locked:
            exit_code=native_cli.run_native(Namespace(recovery_command='credentials',
                        session=str(self.session), apply_app=False, apply_pat=True,
                        ack_app_repair=None,ack_pat_provision=None))
        self.assertEqual(exit_code,2)
        locked.assert_not_called()

    def test_main_cli_has_credential_gate(self):
        import json
        subtree=next(x for x in json.loads((Path(__file__).resolve().parents[1]/'devtool/core_cli_schema.json').read_text())['parser']['subparsers']['children'] if x['name']=='recovery')
        cmd=next(x for x in subtree['parser']['subparsers']['children'] if x['name']=='credentials')
        opts={o for a in cmd['parser']['actions'] for o in a['option_strings']}
        self.assertIn('--ack-app-repair',opts)
        self.assertIn('--ack-pat-provision',opts)
