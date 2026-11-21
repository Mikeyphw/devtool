"""Offline failure injection; no live GitHub calls, secrets or deletion."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

from devtool.recovery import credentials as cr, engine as rr, health, external_dependencies as ex
from devtool.recovery import operational as op


class CredentialBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.session=Path(self.temp.name)/'session'
        (self.session/'primary').mkdir(parents=True)
        self.state={'schema':1,'session_id':self.session.name,'repo':'demo/recovery',
                    'stage':'backup','original_repo_id':17,'approved':True}
        rr.save_state(self.session,self.state)
        rr.write_json(self.session/'primary'/'configuration.json',{'secrets':{},'environments':{}})
        rr.write_json(self.session/'primary'/'dependency-audit.json',{})
        self.policy=self.session/'operator-policy.json'
        rr.write_json(self.policy,{'schema':1,'repository':self.state['repo'],'credentials':[]})
        self.inventory={'profile':'none-known','declared_repository_ids':{'demo/recovery':17}}
        self.admin={'repository':'demo/recovery','repository_id':17,'login':'demo','admin':True}

    def seal(self):
        cr.preflight(self.session,self.state,self.policy)
        with patch.object(ex,'observe',return_value=self.inventory), patch.object(cr,'_admin_observation',return_value=self.admin):
            cr.seal_preparation(self.session,self.state,17)
        self.state['stage']='prepared'
        rr.save_state(self.session,self.state)

    def test_absent_policy_is_not_explicit_empty_declaration(self):
        with self.assertRaisesRegex(rr.Refused,'explicit --credentials-policy'):
            cr.require_declaration(self.session,self.state,None)
        self.assertEqual(cr.require_declaration(self.session,self.state,self.policy)['credentials'],[])

    def test_legacy_prepared_session_cannot_delete(self):
        self.state['stage']='prepared'
        with patch.object(rr,'require_independent_backup'), patch.object(rr,'predelete_compare') as drift, patch.object(rr,'run') as run:
            with self.assertRaisesRegex(rr.Refused,'CREDENTIAL_PREPARATION_ANCHOR_REQUIRED'):
                rr.delete_original(self.session,self.state)
        drift.assert_not_called();run.assert_not_called()
        self.assertEqual(self.state['stage'],'prepared')

    def test_final_gate_stops_delete_after_current_authorization_loss(self):
        self.seal()
        for stage in ('prepared','delete-intent'):
            self.state['stage']=stage
            with patch.object(rr,'require_independent_backup'), patch.object(rr,'predelete_compare'), \
                 patch.object(rr,'repo_info',return_value={'id':17}), \
                 patch.object(cr,'recheck_before_delete',side_effect=rr.Refused('current admin lost')), \
                 patch.object(rr,'run') as run:
                with self.assertRaisesRegex(rr.Refused,'current admin lost'):
                    rr.delete_original(self.session,self.state)
                run.assert_not_called()

    def test_recheck_records_new_evidence_without_rewriting_preparation(self):
        self.seal()
        before=(self.session/'credential-readiness.json').read_bytes()
        state_before=json.loads(json.dumps(self.state))
        with patch.object(ex,'observe',return_value=self.inventory), patch.object(cr,'_admin_observation',return_value=self.admin):
            receipt=cr.recheck_before_delete(self.session,self.state)
        self.assertEqual(receipt['status'],'CURRENT_FEASIBILITY_REVERIFIED')
        self.assertEqual((self.session/'credential-readiness.json').read_bytes(),before)
        self.assertEqual(self.state,state_before)
        self.assertFalse(receipt['future_replacement_access_verified'])

    def test_changed_policy_or_readiness_cannot_be_reanchored_at_delete(self):
        self.seal()
        bound=self.session/'credential-policy.json'
        value=rr.read_json(bound);value['credentials']=[]
        bound.write_text(json.dumps(value))
        with patch.object(ex,'observe',side_effect=AssertionError('no network')):
            with self.assertRaisesRegex(rr.Refused,'CREDENTIAL_PREPARATION_ANCHOR_REQUIRED'):
                cr.recheck_before_delete(self.session,self.state)

    def test_current_readiness_drift_blocks_even_previously_accepted_manual_risk(self):
        self.seal()
        self.state['credential_manual_risk_accepted']=True
        original=rr.read_json(self.session/'credential-readiness.json')
        changed={**original,'manual_obligations':['new risk']}
        with patch.object(cr,'preflight',return_value=changed), patch.object(ex,'observe') as observe:
            with self.assertRaisesRegex(rr.Refused,'CREDENTIAL_READINESS_DRIFT'):
                cr.recheck_before_delete(self.session,self.state)
            observe.assert_not_called()

    def test_external_repo_drift_and_admin_actor_drift_block(self):
        self.seal()
        with patch.object(ex,'observe',return_value={**self.inventory,'declared_repository_ids':{'demo/recovery':19}}):
            with self.assertRaisesRegex(rr.Refused,'EXTERNAL_CREDENTIAL_INVENTORY_DRIFT'):
                cr.recheck_before_delete(self.session,self.state)
        with patch.object(ex,'observe',return_value=self.inventory), patch.object(cr,'_admin_observation',return_value={**self.admin,'login':'someoneelse'}):
            with self.assertRaisesRegex(rr.Refused,'RECOVERY_ADMIN_IDENTITY_DRIFT'):
                cr.recheck_before_delete(self.session,self.state)

    def test_delete_last_identity_recheck_refuses_replaced_target(self):
        self.seal()
        with patch.object(rr,'require_independent_backup'), patch.object(rr,'predelete_compare'), \
             patch.object(rr,'repo_info',side_effect=[{'id':17},{'id':33}]), \
             patch.object(cr,'recheck_before_delete'),patch.object(rr,'run') as run:
            with self.assertRaisesRegex(rr.Refused,'original repository changed'):
                rr.delete_original(self.session,self.state)
        run.assert_not_called()

    def test_interrupted_delete_already_absent_does_not_repeat_delete(self):
        self.state['stage']='delete-intent'
        with patch.object(rr,'repo_info',return_value=None),patch.object(rr,'run') as run, \
             patch.object(cr,'recheck_before_delete') as recheck:
            rr.delete_original(self.session,self.state)
        self.assertEqual(self.state['stage'],'deleted')
        run.assert_not_called();recheck.assert_not_called()

    def test_report_historical_verified_unverified_missing_and_tampered(self):
        self.state['created_new_id']=19
        self.assertEqual(health.credential_receipt(self.session,self.state)['status'],'NOT_ATTESTED')
        for status in ('verified','CREDENTIAL_ACCESS_UNVERIFIED'):
            result={'schema':1,'repo':self.state['repo'],'replacement_id':19,'policy_sha256':None,
                    'status':status,'federation':{'status':'not-applicable','qualified':False}}
            file=self.session/'credential-acceptance.json';rr.write_json(file,result)
            self.state.update(credential_recovery_status=status,credential_acceptance_sha256=rr.sha256(file))
            receipt=health.credential_receipt(self.session,self.state)
            self.assertEqual(receipt['status'],status);self.assertTrue(receipt['provenance_verified'])
            self.assertFalse(receipt['live_verified'])
        file.write_text('{}')
        self.assertFalse(health.credential_receipt(self.session,self.state)['provenance_verified'])

    def test_actual_admin_identity_must_match_lifecycle_cli(self):
        repo=self.state['repo'];admin={'id':17,'full_name':repo,'permissions':{'admin':True}}
        with patch.object(cr,'_token_api',side_effect=[{'login':'demo'},admin]), \
             patch.object(cr.subprocess,'run',side_effect=[types.SimpleNamespace(returncode=0,stdout='{"login":"different"}'),
                    types.SimpleNamespace(returncode=0,stdout=json.dumps(admin))]):
            with self.assertRaisesRegex(rr.Refused,'identities differ'):
                cr._admin_observation(repo,17)

    def test_final_verify_does_not_silently_authorize_app_grant(self):
        import inspect
        self.assertIn('credentials.post_restore(session,state,apply_apps=False,record=True)',inspect.getsource(rr.final_verify))
        self.assertNotIn('apply_apps=True',inspect.getsource(rr.final_verify))

    def test_actions_activation_cannot_bypass_unverified_credentials(self):
        self.state.update(stage='done-with-action-required',created_new_id=19,missing_secret_names=[])
        rr.save_state(self.session,self.state)
        (self.session/'primary'/'actions').mkdir()
        rr.write_json(self.session/'primary'/'actions'/'permissions.json',{'enabled':True,'allowed_actions':'all'})
        with patch.object(rr,'verify_pair'),patch.object(rr,'require_independent_backup'), \
             patch.object(op,'_exact_reconciled_refs'),patch.object(rr,'verify_recovery_configuration'), \
             patch.object(rr,'api',return_value={'enabled':False,'allowed_actions':'all'}), \
             patch.object(cr,'post_restore',return_value={'status':'CREDENTIAL_ACCESS_UNVERIFIED'}), \
             patch.object(rr,'restore_actions_enabled_after_safety') as enable:
            with self.assertRaisesRegex(rr.Refused,'Actions activation refused'):
                op.activate_actions(self.session,ack_repo=self.state['repo'])
            enable.assert_not_called()


class MMRLExternalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.session=Path(self.temp.name)/'session';(self.session/'primary').mkdir(parents=True)
        rr.write_json(self.session/'primary'/'configuration.json',{'secrets':{},'environments':{}})
        self.state={'schema':1,'session_id':self.session.name,'repo':ex.TARGET,'created_new_id':500,'stage':'done-with-action-required'}
        self.policy={'schema':1,'repository':ex.TARGET,'credentials':[{'name':ex.SECRET,'kind':'fine_grained_pat',
                      'repositories':[ex.TARGET,ex.OTHER],'secret_hosts':[ex.HOST],
                      'token_env':'MMRL_TEST_ONLY_TOKEN','probe':'actions-secrets'}]}
        self.ids={ex.TARGET:500,ex.HOST:300,ex.OTHER:400}
        self.sha='a'*40
        self.obs={'profile':'mmrl-federation-v1','repository_ids':self.ids,
                  'host_source_sha':self.sha,'secret_updated_at':'2026-11-20T02:00:00Z'}

    def test_known_inbound_dependency_cannot_be_waived_away(self):
        for items in ([],[{**self.policy['credentials'][0],'secret_hosts':[ex.OTHER]}]):
            with patch.object(rr,'repo_info',side_effect=AssertionError('no remote request')):
                with self.assertRaisesRegex(rr.Refused,'MMRL_EXTERNAL_POLICY'):
                    ex.observe({**self.policy,'credentials':items})

    def test_known_inbound_dependency_blocks_legacy_empty_snapshot(self):
        proof=cr.post_restore(self.session,self.state,record=True)
        self.assertEqual(proof['status'],'CREDENTIAL_ACCESS_UNVERIFIED')
        self.assertIn(ex.SECRET,proof['unclassified'])
        self.assertFalse(proof['federation']['qualified'])

    def test_known_dependent_id_drift_refused_before_probe(self):
        prior={'declared_repository_ids':{**self.ids,ex.OTHER:999}}
        with patch.object(rr,'repo_info',side_effect=lambda r:{'id':self.ids[r],'full_name':r}),patch.object(cr,'_token_api') as token:
            with self.assertRaisesRegex(rr.Refused,'DECLARED_EXTERNAL_REPOSITORY_ID_DRIFT'):
                ex.observe(self.policy,expected_target_id=500,prior=prior)
            token.assert_not_called()

    def test_wrong_host_policy_stops_before_any_credential_write(self):
        policy={**self.policy,'credentials':[{**self.policy['credentials'][0],'secret_hosts':[ex.OTHER]}]}
        file=self.session/'credential-policy.json';rr.write_json(file,policy)
        self.state['credential_policy_sha256']=rr.sha256(file)
        with patch.object(cr,'_pat_verify') as apply:
            with self.assertRaisesRegex(rr.Refused,'MMRL_EXTERNAL_POLICY_SCOPE_MISMATCH'):
                cr.post_restore(self.session,self.state,apply_pats=True)
            apply.assert_not_called()

    def payload(self):
        return {'schema':'mmrl.n05f.propagation.v2','mode':'apply','source':ex.HOST,'source_sha':self.sha,
                'auto_merge':False,'releases':False,'consumers':[
                    {'repository':name,'repository_id':self.ids[name],'target_sha':self.sha,
                     'base':'next' if name==ex.TARGET else 'main','branch':'feature',
                     'new_head':'b'*40,'pr_number':17,'result':'created-pr'} for name in (ex.TARGET,ex.OTHER)]}

    def pr(self, endpoint):
        name=endpoint.split('/pulls/')[0][len('repos/'):]
        return {'number':17,'head':{'sha':'b'*40,'ref':'feature','repo':{'id':self.ids[name]}},
                'base':{'ref':'next' if name==ex.TARGET else 'main','repo':{'id':self.ids[name]}}}

    def test_receipt_needs_exact_two_ids_and_actual_write_not_noop(self):
        with patch.object(rr,'api',side_effect=self.pr):
            self.assertEqual(len(ex._receipt(self.payload(),self.obs,{})),2)
            p=self.payload();p['consumers'][0]['repository_id']=17
            with self.assertRaisesRegex(rr.Refused,'ID_OR_SOURCE_MISMATCH'):
                ex._receipt(p,self.obs,{})
            p=self.payload();p['consumers'][0].pop('result');p['consumers'][0]['action']='unchanged'
            with self.assertRaisesRegex(rr.Refused,'ACTUAL_WRITE_NOT_PROVED'):
                ex._receipt(p,self.obs,{})

    def test_fresh_exact_run_job_artifact_and_receipt_required(self):
        archive=self.session/'mock-hosted.zip'
        with zipfile.ZipFile(archive,'w') as z:z.writestr('n05f-propagation.json',json.dumps(self.payload()))
        artifact={'id':89,'name':'mmrl-n05f-propagation-70-attempt1','size_in_bytes':archive.stat().st_size,
                  'digest':'sha256:'+rr.sha256(archive),'expired':False}
        run={'id':70,'workflow_id':22,'event':'workflow_run','head_branch':'main','head_sha':self.sha,
             'repository':{'id':300},'head_repository':{'id':300},'run_attempt':1,'status':'completed',
             'conclusion':'success','created_at':'2026-11-20T03:00:00Z'}
        steps=[{'name':n,'conclusion':'success'} for n in ('Verify exact validated source and plan both consumers',
                   'Open or update source-pinned consumer PRs (never merge)','Publish source-bound propagation receipt')]
        job={'name':'propagate','status':'completed','conclusion':'success','steps':steps}
        def collect(path,*,key):
            if path.endswith('/workflows'):return [{'id':22,'name':ex.WORKFLOW_NAME,'path':ex.WORKFLOW,'state':'active'}]
            if path.endswith('/jobs'):return [job]
            if path.endswith('/artifacts'):return [artifact]
            raise AssertionError(path)
        def api(path):
            if '/workflows/22/runs?' in path:return {'workflow_runs':[dict(run)]}
            if path.endswith('/runs/70'):return dict(run)
            if '/pulls/' in path:return self.pr(path)
            raise AssertionError(path)
        with patch.object(ex,'observe',return_value=self.obs), \
             patch.object(rr,'get_new_repo',return_value={'created_at':'2026-11-20T01:00:00Z'}), \
             patch.object(rr,'collect',side_effect=collect),patch.object(rr,'api',side_effect=api), \
             patch.object(rr,'run',return_value=types.SimpleNamespace(stdout='mock-read-token')), \
             patch.object(rr,'download_api_binary',side_effect=lambda e,d,**kw:d.write_bytes(archive.read_bytes())):
            result=ex.qualify(self.session,self.state,self.policy)
            self.assertTrue(result['qualified'],result)
            self.assertEqual(result['artifact_id'],89)
            artifact['digest']='sha256:'+'0'*64
            self.assertFalse(ex.qualify(self.session,self.state,self.policy)['qualified'])
            artifact['digest']='sha256:'+rr.sha256(archive)
            run['conclusion']='failure'
            self.assertFalse(ex.qualify(self.session,self.state,self.policy)['qualified'])
            run['conclusion']='success';run['created_at']='2026-11-20T01:30:00Z'
            self.assertFalse(ex.qualify(self.session,self.state,self.policy)['qualified'])

    def test_pat_read_and_secret_provision_alone_do_not_qualify_mmrl(self):
        file=self.session/'credential-policy.json';rr.write_json(file,self.policy)
        self.state['credential_policy_sha256']=rr.sha256(file)
        row={'name':ex.SECRET,'kind':'fine_grained_pat','status':'verified'}
        with patch.object(cr,'_pat_verify',return_value=row.copy()), \
             patch.object(ex,'qualify',return_value={'qualified':False,'status':'FEDERATION_NOT_VERIFIED'}):
            result=cr.post_restore(self.session,self.state,record=True)
        self.assertEqual(result['status'],'CREDENTIAL_ACCESS_UNVERIFIED')
        self.assertFalse(health.credential_receipt(self.session,self.state)['live_verified'])


class DisposableBoundaryTests(unittest.TestCase):
    def test_fixture_policy_refuses_production_consumer_and_host(self):
        path=Path(__file__).with_name('native_recovery_e2e_disposable.py')
        spec=importlib.util.spec_from_file_location('disposable_live_test',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        repo='demo/repo-rebuild-e2e-12345678'
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'policy.json'
            rr.write_json(p,{'schema':1,'repository':repo,'credentials':[{'name':'TEST_PAT','kind':'fine_grained_pat',
                'repositories':[repo],'secret_hosts':[ex.HOST],'token_env':'TEST_PAT_VALUE','probe':'actions-secrets'}]})
            with self.assertRaisesRegex(rr.Refused,'only the exact disposable'):
                module.checked_policy(p,repo)


if __name__=='__main__':unittest.main()
