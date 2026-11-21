"""Regressions found during v1.2.0 promise audit. All network calls are mocked."""
from __future__ import annotations
import datetime as dt
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
import sys
import types
from devtool.recovery import engine as rr
from devtool.recovery import safety


class EnvironmentRestorationTests(TestCase):
    def test_inventory_fetches_full_environment_instead_of_list_stub(self):
        calls=[]
        def fake_api(path,**kw):
            calls.append(path)
            if path.endswith('/environments/prod'):
                return {'name':'prod','protection_rules':[
                    {'type':'wait_timer','wait_timer':15}],
                    'deployment_branch_policy':{'protected_branches':False,'custom_branch_policies':False}}
            if path.endswith('/actions/permissions/workflow'):
                return {'default_workflow_permissions':'read'}
            raise AssertionError('unexpected request: '+path)
        def optional(path,key):
            if path.endswith('/environments'):
                return [{'name':'prod'}]  # GitHub list endpoint is not enough
            return []
        with patch.object(rr,'api',side_effect=fake_api), \
             patch.object(rr,'collect_optional',side_effect=optional), \
             patch.object(rr,'collect',return_value=[]):
            config=rr.capture_configuration('x/y',{'id':7,'owner':{'type':'User'}})
        self.assertEqual(config['environments']['prod']['definition']['protection_rules'][0]['wait_timer'],15)
        self.assertIn('repos/x/y/environments/prod',calls)

    def test_cannot_claim_environment_restored_when_wait_timer_differs(self):
        old={'name':'prod','protection_rules':[{'type':'wait_timer','wait_timer':15}],
             'deployment_branch_policy':None}
        new={'name':'prod','protection_rules':[], 'deployment_branch_policy':None}
        with self.assertRaisesRegex(rr.Refused,'environment policy differs'):
            rr.verify_environment_policy(old,new)

    def test_cannot_claim_environment_restored_when_required_reviewer_differs(self):
        def env(user_id):
            return {'name':'prod','deployment_branch_policy':None,'protection_rules':[
                {'type':'required_reviewers','prevent_self_review':True,
                 'reviewers':[{'type':'User','reviewer':{'id':user_id}}]}]}
        with self.assertRaisesRegex(rr.Refused,'environment policy differs'):
            rr.verify_environment_policy(env(1),env(2))
        rr.verify_environment_policy(env(1),env(1))

    def test_missing_full_protection_rules_fails_closed(self):
        with self.assertRaisesRegex(rr.Refused,'full environment'):
            rr.restorable_environment_policy({'name':'prod'})


class ActionsEnablePolicyTests(TestCase):
    def test_reenable_restores_full_policy_not_only_enabled_bit(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'primary'/'actions').mkdir(parents=True)
            rr.write_json(root/'primary'/'actions'/'permissions.json',
                {'enabled':True,'allowed_actions':'selected','sha_pinning_required':True})
            rr.write_json(root/'primary'/'actions'/'selected-actions.json',
                {'github_owned_allowed':True,'verified_allowed':False,'patterns_allowed':['org/*']})
            rr.write_json(root/'primary'/'actions'/'workflows.json',[])
            state={'stage':'configuration-done','session_id':root.name,'repo':'x/y','schema':1}
            policy={'enabled':False,'allowed_actions':'all','sha_pinning_required':False}
            selected={'github_owned_allowed':False,'verified_allowed':False,'patterns_allowed':[]}
            def api(path,method='GET',payload=None):
                if path.endswith('/selected-actions'):
                    if method=='PUT':selected.update(payload)
                    return selected.copy()
                if method=='PUT':policy.update(payload)
                return policy.copy()
            with patch.object(rr,'api',side_effect=api), patch.object(rr,'collect',return_value=[]):
                rr.restore_actions_enabled_after_safety(root,state,unresolved=False)
            self.assertEqual(selected['patterns_allowed'],['org/*'])
            self.assertEqual(policy,{'enabled':True,'allowed_actions':'selected',
                                     'sha_pinning_required':True})
            self.assertFalse(state['actions_left_disabled_for_manual_recovery'])


class RefCompatibilityTests(TestCase):
    def _entry(self,ref):
        return {'kind':'repository-action-ref','path':'.github/workflows/ci.yml',
                'line':8,'value':'demo/app/.github/workflows/test.yml@'+ref}

    def test_fork_collision_is_a_predelete_risk(self):
        s={'findings':[self._entry('main')]}
        result=safety.assess_named_repo_refs(s,'demo/app',{'refs/heads/main':'a'*40},
                                             {'refs/heads/main':'b'*40})
        self.assertEqual(result[0]['replacement_name_status'],'fork-name-conflict')

    def test_named_ref_unchanged_is_compatible(self):
        result=safety.assess_named_repo_refs({'findings':[self._entry('v1')]},
                    'demo/app',{'refs/tags/v1':'a'*40},{})
        self.assertEqual(result[0]['replacement_name_status'],'verified-original-named-ref')

    def test_ambiguous_and_missing_named_refs_are_flagged(self):
        found=safety.assess_named_repo_refs({'findings':[self._entry('v1'),self._entry('v2')]},
                    'demo/app',{'refs/tags/v1':'a'*40,'refs/heads/v1':'b'*40},{})
        self.assertEqual([x['replacement_name_status'] for x in found],
                         ['ambiguous-branch-or-tag','unavailable-named-ref'])

    def test_migration_includes_issues_assets_and_never_recreates_historical_runs(self):
        deps={'referenced_run_ids':[11], 'scan_gaps':[],
              'findings':[{'kind':'issue-number','status':'will-change','path':'script.sh',
                           'line':4,'value':'gh issue view 5'},
                          {'kind':'run-id','status':'will-change','path':'script.sh',
                           'line':5,'value':'actions/runs/11'}]}
        result=safety.migration_plan(deps,{'issue:5':{'number':20},'pr:4':{'number':19}},
                100,200,[],[],release_asset_ids={'333':444},ruleset_ids={'5':6})
        self.assertEqual(result['issues'],{'5':20})
        self.assertEqual(result['release_asset_ids'],{'333':444})
        self.assertEqual(result['ruleset_ids'],{'5':6})
        self.assertIsNone(result['run_ids']['11'])
        self.assertTrue(all(not x['automatically_applied'] for x in result['reviewed_rewrite_candidates']))


class InterruptedOperationTests(TestCase):
    def test_creation_failure_requires_explicit_adoption_not_silent_reuse(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'primary').mkdir()
            rr.write_json(root/'primary'/'repo.json',{'visibility':'public'})
            state={'stage':'create-intent','schema':1,'session_id':root.name,
                   'repo':'x/y','topology':'standalone','created_new_id':None,
                   'original_repo_id':1,'original_visibility':'public'}
            with patch.object(rr,'get_new_repo',return_value=None), \
                 patch.object(rr,'run',return_value=types.SimpleNamespace(returncode=1,stderr='conflict',stdout='')):
                with self.assertRaisesRegex(rr.Refused,'--adopt-created-repo-id'):
                    rr.create_replacement(root,state)
            self.assertIsNone(state.get('created_new_id'))

    def test_journaled_object_id_cannot_be_replaced_by_marker_search(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);meta=root/'primary'/'metadata';meta.mkdir(parents=True)
            rr.write_json(root/'primary'/'repo.json',{'id':4,'default_branch':'main'})
            rr.write_json(meta/'issues.json',[{'number':1,'title':'Issue','state':'open'}])
            rr.write_json(meta/'pulls.json',[])
            state={'stage':'objects-intent','schema':1,'session_id':root.name,'repo':'x/y',
                   'topology':'standalone','objects':{'issue:1':{'number':2}}}
            with patch.object(rr,'get_new_repo',return_value={'id':20}), \
                 patch.object(rr,'find_import',return_value={'number':3,'title':'Issue'}):
                with self.assertRaisesRegex(rr.Refused,'journaled imported object identity changed'):
                    rr.restore_objects(root,state)

    def test_creation_refuses_old_repository_even_after_successful_cli(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'primary').mkdir()
            rr.write_json(root/'primary'/'repo.json',{'visibility':'public'})
            state={'stage':'create-intent','schema':1,'session_id':root.name,
                   'repo':'x/y','topology':'standalone','created_new_id':None,
                   'original_repo_id':1,'original_visibility':'public'}
            old={'id':22,'created_at':'2020-01-01T00:00:00Z','visibility':'public'}
            # Mock the pre-create lookup as missing, then a newly visible but OLD repo.
            with patch.object(rr,'get_new_repo',side_effect=[None,old]), \
                 patch.object(rr,'run',return_value=types.SimpleNamespace(returncode=0,stderr='',stdout='')):
                with self.assertRaisesRegex(rr.Refused,'predates this recreation attempt'):
                    rr.create_replacement(root,state)

    def test_manual_attestation_can_unblock_after_explicit_ack_without_claiming_ci(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'primary'/'actions').mkdir(parents=True)
            rr.write_json(root/'primary'/'actions'/'workflows.json',
                          [{'path':'.github/workflows/ci.yml','state':'active'}])
            # Modern post-recreation credential gate requires the captured
            # configuration snapshot; an empty inventory is distinct from missing.
            rr.write_json(root/'primary'/'configuration.json',
                          {'secrets':{'actions':[],'dependabot':[],'codespaces':[]},
                           'environments':{}})
            rr.write_json(root/'report.json',{'recovery_classification':'RESTORED_WITH_ACTION_REQUIRED'})
            state={'stage':'done-with-action-required','schema':1,'session_id':root.name,'repo':'x/y',
                   'actions_left_disabled_for_manual_recovery':True}
            rr.write_json(root/'state.json',state)
            def fake_api(path,**kwargs):return {'enabled':False}
            with patch.object(rr,'verify_pair'),patch.object(rr,'require_independent_backup'), \
                 patch.object(rr,'get_new_repo',return_value={'id':4}), \
                 patch.object(rr,'expected_ref_mapping',return_value=({},None)), \
                 patch.object(rr,'live_refs',return_value={}), \
                 patch.object(rr,'verify_recovery_configuration') as verify, \
                 patch.object(rr,'collect',return_value=[{'path':'.github/workflows/ci.yml'}]), \
                 patch.object(rr,'api',side_effect=fake_api), \
                 patch.object(rr,'restore_actions_enabled_after_safety') as enable:
                rr.attest_manual_recovery(root,ack_external_recovery='x/y')
            self.assertEqual(verify.call_count,1)
            self.assertEqual(verify.call_args.kwargs,{'require_all_secrets':True})
            self.assertEqual(enable.call_count,1)
            self.assertFalse(enable.call_args.kwargs['unresolved'])
            self.assertEqual(rr.load_state(root)['stage'],'done-manual-attested')
            report=rr.read_json(root/'report.json')
            self.assertFalse(report['fully_operationally_verified'])
            self.assertFalse(report['manual_recovery_attestation']['secret_values_verified'])

    def test_manual_attestation_requires_exact_recovery_ack(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            state={'stage':'done-with-action-required','schema':1,'session_id':root.name,'repo':'x/y'}
            rr.write_json(root/'state.json',state)
            with patch.object(rr,'verify_pair',side_effect=AssertionError('must not reach verification')):
                with self.assertRaisesRegex(rr.Refused,'acknowledgement'):
                    rr.attest_manual_recovery(root,ack_external_recovery='wrong/repo')

    def test_post_restoration_verifier_refuses_secret_or_var_drift(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'primary').mkdir()
            rr.write_json(root/'primary'/'configuration.json',{
               'workflow_permissions':{'default_workflow_permissions':'read'},
               'variables':[{'name':'REGION','value':'us-west'}],
               'secrets':{'actions':[],'dependabot':[],'codespaces':[]},
               'codespaces_inventory':'available','environments':{},'deploy_keys':[],
               'branch_protections':{},'organization':{'status':'not-applicable','selected':[]}})
            def api(path,**kwargs):
                return {'default_workflow_permissions':'read'} if path.endswith('/workflow') else {'value':'wrong'}
            with patch.object(rr,'api',side_effect=api):
                with self.assertRaisesRegex(rr.Refused,'repository variable drift'):
                    rr.verify_recovery_configuration(root,{'repo':'x/y'})

class CommandErrorHandlingTests(TestCase):
    def test_invalid_vault_is_normal_refusal_not_unhandled_traceback(self):
        from argparse import Namespace
        from devtool.recovery import native_cli
        with patch.object(native_cli,'inspect_repo',side_effect=safety.SafetyError('invalid vault')):
            self.assertEqual(native_cli.run_native(Namespace(recovery_command='inspect',recovery_repository='x/y')),2)
