"""Regression coverage for the observed MMRL 409 and durable recovery.

All GitHub network calls are mocked. The original repository cannot be deleted
or modified by this test suite.
"""
from __future__ import annotations
import datetime as dt
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from devtool.recovery import engine as rr


class Actions409RecoveryTests(unittest.TestCase):
    def test_suspension_never_sends_policy_with_enabled_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            session=Path(tmp)
            state={'repo':'Mikeyphw/MMRL','stage':'create-intent','session_id':session.name}
            observed=[]
            values={'enabled':True,'allowed_actions':'all','sha_pinning_required':False}
            def api(path,*,method='GET',payload=None,**_):
                if method=='PUT':
                    observed.append(payload.copy())
                    if payload.get('enabled') is False and len(payload)>1:
                        raise rr.Refused("409: You can't specify allowed_actions unless enabled is true")
                    values.update(payload)
                return values.copy()
            with patch.object(rr,'api',side_effect=api):
                rr.suspend_actions_for_restore(session,state)
                rr.suspend_actions_for_restore(session,state)
            self.assertEqual(observed,[{'enabled':False}])
            self.assertTrue(state['actions_suspended'])
            self.assertFalse(values['enabled'])

    def test_while_disabled_actions_policy_update_is_deferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            session=Path(tmp)
            (session/'primary'/'actions').mkdir(parents=True)
            rr.write_json(session/'primary'/'actions'/'workflows.json',[])
            state={'stage':'releases-done','repo':'Mikeyphw/MMRL',
                   'actions_suspended':True}
            calls=[]
            def api(path,*,method='GET',payload=None,**_):
                calls.append((path,method,payload))
                if method!='GET':
                    raise AssertionError('No Actions API mutations allowed while suspended')
                return {'enabled':False,'allowed_actions':'all'}
            with patch.object(rr,'api',side_effect=api), patch.object(rr,'collect',return_value=[]):
                rr.restore_actions_permissions(session,state)
            self.assertTrue(state['actions_policy_deferred_until_safe_activation'])
            self.assertEqual(state['stage'],'actions-done')
            self.assertTrue(all(method=='GET' for _,method,_ in calls))

    def test_creation_checkpoints_new_id_before_actions_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            session=Path(tmp);(session/'primary').mkdir()
            rr.write_json(session/'primary'/'repo.json',{'visibility':'public'})
            state={'schema':1,'stage':'deleted','repo':'Mikeyphw/MMRL',
                   'original_repo_id':1298790213,'original_visibility':'public',
                   'topology':'standalone','created_new_id':None,
                   'session_id':session.name}
            candidate={'id':1463338730,'fork':False,'parent':None,
                       'visibility':'public','created_at':rr.now(),'default_branch':'master'}
            with patch.object(rr,'get_new_repo',side_effect=[None,candidate]), \
                 patch.object(rr,'run',return_value=types.SimpleNamespace(returncode=0)), \
                 patch.object(rr,'suspend_actions_for_restore',side_effect=rr.Refused('409 conflict')):
                with self.assertRaisesRegex(rr.Refused,'409 conflict'):
                    rr.create_replacement(session,state)
            journal=rr.load_state(session)
            self.assertEqual(journal['created_new_id'],1463338730)
            self.assertEqual(journal['stage'],'create-intent')
            with patch.object(rr,'get_new_repo',return_value=candidate), \
                 patch.object(rr,'suspend_actions_for_restore') as suspend, \
                 patch.object(rr,'run',side_effect=AssertionError('re-creation forbidden')):
                rr.create_replacement(session,journal)
            self.assertEqual(suspend.call_count,1)
            self.assertEqual(rr.load_state(session)['stage'],'created')

    def test_adoption_of_existing_mmrl_fork_uses_explicit_id_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            session=Path(tmp)
            state={'schema':1,'session_id':session.name,'repo':'Mikeyphw/MMRL',
                   'stage':'create-intent','approved':True,'original_repo_id':1298790213,
                   'created_new_id':None,'original_visibility':'public','topology':'fork',
                   'parent_repo':'MMRLApp/MMRL','creation_attempted_at':rr.now(),
                   'preflight_blockers':[]}
            rr.save_state(session,state)
            candidate={'id':1463338730,'fork':True,
                       'parent':{'full_name':'MMRLApp/MMRL'},
                       'visibility':'public','created_at':rr.now(),'default_branch':'master'}
            with patch.object(rr,'verify_pair'),patch.object(rr,'require_independent_backup'), \
                 patch.object(rr,'get_new_repo',return_value=candidate), \
                 patch.object(rr,'live_refs',return_value={'refs/heads/master':'a'*40}), \
                 patch.object(rr,'suspend_actions_for_restore') as suspend, \
                 patch.object(rr,'delete_original'), \
                 patch.object(rr,'restore_refs'),patch.object(rr,'restore_labels_and_milestones'), \
                 patch.object(rr,'restore_objects'),patch.object(rr,'restore_releases'), \
                 patch.object(rr,'restore_actions_permissions'), \
                 patch.object(rr,'restore_security_configuration',side_effect=lambda s,st,**kw:st.update(stage='configuration-done')), \
                 patch.object(rr,'run',side_effect=AssertionError('must not create again')), \
                 patch.object(rr,'final_verify',side_effect=lambda s,st:(st.update(stage='done'),rr.save_state(s,st))):
                rr.resume(session,adopt_created_repo_id=1463338730)
            self.assertEqual(suspend.call_count,1)
            self.assertEqual(rr.load_state(session)['created_new_id'],1463338730)
            self.assertEqual(rr.load_state(session)['stage'],'done')

    def test_wrong_adopted_id_fails_without_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            session=Path(tmp)
            state={'schema':1,'session_id':session.name,'repo':'Mikeyphw/MMRL',
                   'stage':'create-intent','approved':True,'original_repo_id':1298790213,
                   'created_new_id':None,'topology':'fork','parent_repo':'MMRLApp/MMRL',
                   'creation_attempted_at':rr.now(),'preflight_blockers':[]}
            rr.save_state(session,state)
            candidate={'id':1463338730}
            with patch.object(rr,'verify_pair'),patch.object(rr,'require_independent_backup'), \
                 patch.object(rr,'get_new_repo',return_value=candidate), \
                 patch.object(rr,'delete_original',side_effect=AssertionError('unsafe')):
                with self.assertRaisesRegex(rr.Refused,'candidate is not the intended new repository'):
                    rr.resume(session,adopt_created_repo_id=123456789)
            self.assertIsNone(rr.load_state(session)['created_new_id'])

    def test_policy_reenable_cannot_later_send_enabled_false_with_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            session=Path(tmp);(session/'primary'/'actions').mkdir(parents=True)
            rr.write_json(session/'primary'/'actions'/'permissions.json',
                         {'enabled':False,'allowed_actions':'selected','sha_pinning_required':True})
            state={'repo':'x/y','session_id':session.name,'stage':'configuration-done'}
            calls=[]
            def api(path,*,method='GET',payload=None,**_):
                calls.append((method,payload))
                if method!='GET':raise AssertionError('disabled policy cannot be mutated')
                return {'enabled':False,'allowed_actions':'all','sha_pinning_required':False}
            with patch.object(rr,'api',side_effect=api):
                rr.restore_actions_enabled_after_safety(session,state,unresolved=False)
            self.assertEqual(state['actions_policy_deferred_while_disabled'],
                             {'allowed_actions':'selected','sha_pinning_required':True})
            self.assertTrue(all(m=='GET' for m,_ in calls))

    def test_activation_error_rolls_back_with_only_enabled_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            session=Path(tmp);(session/'primary'/'actions').mkdir(parents=True)
            rr.write_json(session/'primary'/'actions'/'permissions.json',
                         {'enabled':True,'allowed_actions':'selected'})
            rr.write_json(session/'primary'/'actions'/'selected-actions.json',
                         {'github_owned_allowed':True,'verified_allowed':False,
                          'patterns_allowed':['org/*']})
            state={'repo':'x/y','session_id':session.name,'stage':'configuration-done'}
            policy={'enabled':False,'allowed_actions':'all'}
            writes=[]
            def api(path,*,method='GET',payload=None,**_):
                if method=='PUT':
                    writes.append((path,payload.copy()))
                    if path.endswith('/selected-actions'):
                        raise rr.Refused('remote rejects selected allowlist')
                    policy.update(payload)
                if path.endswith('/selected-actions'):
                    return {'patterns_allowed':[]}
                return policy.copy()
            with patch.object(rr,'api',side_effect=api):
                with self.assertRaisesRegex(rr.Refused,'re-suspended'):
                    rr.restore_actions_enabled_after_safety(session,state,unresolved=False)
            self.assertFalse(policy['enabled'])
            self.assertEqual(writes[-1][1],{'enabled':False})
            self.assertTrue(state['actions_left_disabled_for_manual_recovery'])

    def test_manual_ack_archives_missing_nondefault_registration_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            session=Path(tmp);(session/'primary'/'actions').mkdir(parents=True)
            rr.write_json(session/'primary'/'actions'/'workflows.json',[
                {'path':'.github/workflows/next-ci.yml','state':'active'}])
            rr.write_json(session/'primary'/'configuration.json',
                          {'secrets':{'actions':[],'dependabot':[],'codespaces':[]},
                           'environments':{}})
            rr.write_json(session/'report.json',{})
            state={'schema':1,'session_id':session.name,'repo':'x/y',
                   'stage':'done-with-action-required',
                   'unregistered_workflows':{'.github/workflows/next-ci.yml':
                     {'original_default_source_present':False}}}
            rr.save_state(session,state)
            with patch.object(rr,'verify_pair'),patch.object(rr,'require_independent_backup'), \
                 patch.object(rr,'get_new_repo',return_value={'id':2}), \
                 patch.object(rr,'expected_ref_mapping',return_value=({},None)), \
                 patch.object(rr,'live_refs',return_value={}), \
                 patch.object(rr,'verify_recovery_configuration'), \
                 patch.object(rr,'collect',return_value=[]), \
                 patch.object(rr,'api',return_value={'enabled':False}), \
                 patch.object(rr,'restore_actions_enabled_after_safety'):
                rr.attest_manual_recovery(session,ack_external_recovery='x/y')
            report=rr.read_json(session/'report.json')
            self.assertEqual(report['archived_only_workflow_registrations_acknowledged'],
                             ['.github/workflows/next-ci.yml'])
            self.assertFalse(report['fully_operationally_verified'])

    def test_missing_original_default_workflow_cannot_be_archived_by_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            session=Path(tmp);(session/'primary'/'actions').mkdir(parents=True)
            rr.write_json(session/'primary'/'actions'/'workflows.json',[
                {'path':'.github/workflows/required.yml','state':'active'}])
            rr.write_json(session/'primary'/'configuration.json',
                          {'secrets':{'actions':[],'dependabot':[],'codespaces':[]},
                           'environments':{}})
            state={'schema':1,'session_id':session.name,'repo':'x/y',
                   'stage':'done-with-action-required',
                   'unregistered_workflows':{'.github/workflows/required.yml':
                     {'original_default_source_present':True}}}
            rr.save_state(session,state)
            with patch.object(rr,'verify_pair'),patch.object(rr,'require_independent_backup'), \
                 patch.object(rr,'get_new_repo',return_value={'id':2}), \
                 patch.object(rr,'expected_ref_mapping',return_value=({},None)), \
                 patch.object(rr,'live_refs',return_value={}), \
                 patch.object(rr,'verify_recovery_configuration'), \
                 patch.object(rr,'collect',return_value=[]), \
                 patch.object(rr,'api',return_value={'enabled':False}), \
                 patch.object(rr,'restore_actions_enabled_after_safety'):
                with self.assertRaisesRegex(rr.Refused,'default-branch workflow'):
                    rr.attest_manual_recovery(session,ack_external_recovery='x/y')

if __name__=='__main__':
    unittest.main()

class HistoricalReferenceTests(unittest.TestCase):
    def test_run_urls_in_documents_do_not_block_live_ci_recovery(self):
        import subprocess
        from devtool.recovery import safety
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);work=root/'work';work.mkdir()
            def git(*args):
                return subprocess.run(['git',*map(str,args)],check=True,
                                      capture_output=True,text=True).stdout.strip()
            git('init','-q',work)
            git('-C',work,'config','user.email','tester@example.org')
            git('-C',work,'config','user.name','Tester')
            docs=work/'docs';docs.mkdir()
            (docs/'history.md').write_text(
                'https://github.com/Mikeyphw/MMRL/actions/runs/41262381830\n'
                'https://github.com/Mikeyphw/dustui/actions/runs/41258640269\n')
            workflows=work/'.github/workflows';workflows.mkdir(parents=True)
            (workflows/'ci.yml').write_text('run: gh run view 99 --repo Mikeyphw/MMRL\n'
                'url: https://github.com/Mikeyphw/MMRL/actions/runs/41262381830\n')
            git('-C',work,'add','.')
            git('-C',work,'commit','-qm','baseline')
            mirror=root/'mirror.git'
            git('clone','-q','--mirror',work,mirror)
            scanned=safety.scan_dependencies(mirror,'Mikeyphw/MMRL',rr.refs_from_git(mirror),rr.run)
            statuses={(x['path'],x['status']) for x in scanned['findings'] if x['kind']=='run-id'}
            self.assertIn(('docs/history.md','historical-run-archived-only'),statuses)
            self.assertIn(('docs/history.md','external-repository-unaffected'),statuses)
            self.assertIn(('.github/workflows/ci.yml','will-change'),statuses)
            self.assertEqual(scanned['referenced_run_ids'],[41262381830])
