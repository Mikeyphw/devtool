"""Post-restoration tests use a fake GitHub API: NEVER contact or modify GitHub."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from devtool.recovery import engine as rr
from devtool.recovery import operational as op

REPO='Mikeyphw/MMRL'
OLD='000f55c102e2a46967521ba6d99995a78082a1f3'
SEED='6c2b778867f7f66ee2d208ee82887aa2cf9bb0bb'
ARCHIVE=f'refs/heads/archive/pre-recreate-1298790213-{OLD[:12]}/master'
PRESERVED=f'refs/heads/archive/upstream-pre-reconcile-1463338730-{SEED[:12]}/master'


class FakeGitHub:
    def __init__(self):
        self.refs={'refs/heads/master':SEED,ARCHIVE:OLD,'refs/heads/next':'a'*40}
        self.default='master'
        self.enabled=False
        self.policy={'allowed_actions':'all','sha_pinning_required':False}
        self.protected=False
        self.rules=[]
        self.pulls=[]
        self.calls=[]
        self.fail_once_on=None
        self.fail_after_rename_once=False

    def info(self,state):
        return {'id':1463338730,'default_branch':self.default,'fork':True,
                'parent':{'full_name':'MMRLApp/MMRL'},'visibility':'public'}

    def api(self,endpoint,*,method='GET',payload=None,**kwargs):
        self.calls.append((method,endpoint,payload))
        if self.fail_once_on == (method,endpoint):
            self.fail_once_on=None
            raise rr.Refused('simulated interrupted API request')
        if endpoint.endswith('/actions/permissions'):
            if method=='PUT':
                self.enabled=payload['enabled']
                self.policy.update({k:v for k,v in payload.items() if k!='enabled'})
            return {'enabled':self.enabled,**self.policy}
        if endpoint.endswith('/branches/master'):
            return {'name':'master','protected':self.protected}
        if endpoint.endswith('/branches/master/rename'):
            if method!='POST':raise AssertionError(method)
            assert self.refs.get('refs/heads/master')==SEED
            self.refs[PRESERVED]=self.refs.pop('refs/heads/master')
            self.default=payload['new_name']
            if self.fail_after_rename_once:
                self.fail_after_rename_once=False
                raise rr.Refused('GitHub performed rename but response was lost')
            return {'name':payload['new_name']}
        if '/rulesets?' in endpoint:return self.rules
        if endpoint.endswith('/git/refs'):
            assert method=='POST' and payload['ref']=='refs/heads/master'
            assert payload['sha']==OLD and 'refs/heads/master' not in self.refs
            self.refs['refs/heads/master']=payload['sha']
            return {}
        if endpoint==f'repos/{REPO}' and method=='PATCH':
            assert payload=={'default_branch':'master'}
            self.default='master'
            return {}
        raise AssertionError((method,endpoint,payload))


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s=Path(self.tmp.name)/'session-0001'
        (self.s/'primary'/'actions').mkdir(parents=True)
        rr.write_json(self.s/'primary'/'source-refs.json',{'refs/heads/master':OLD,'refs/heads/next':'a'*40})
        rr.write_json(self.s/'primary'/'repo.json',{'default_branch':'master'})
        rr.write_json(self.s/'primary'/'actions'/'permissions.json',{
            'enabled':True,'allowed_actions':'all','sha_pinning_required':False})
        rr.write_json(self.s/'primary'/'actions'/'workflows.json',[
            {'path':'.github/workflows/mmrl-release-seal.yml','state':'active'}])
        self.state={'schema':1,'session_id':self.s.name,'repo':REPO,
            'stage':'done-with-action-required','original_repo_id':1298790213,
            'created_new_id':1463338730,'topology':'fork',
            'fork_seed_refs':{'refs/heads/master':SEED},'master_archive':ARCHIVE,
            'missing_secret_names':[], 'actions_suspended':True,'approved':True,
            'accept_local_only_risk':True}
        rr.save_state(self.s,self.state)
        rr.write_json(self.s/'report.json',{'recovery_classification':'RESTORED_WITH_ACTION_REQUIRED'})
        self.gh=FakeGitHub()
        patches=[
            patch.object(rr,'verify_pair',return_value={}),
            patch.object(rr,'require_independent_backup',return_value=None),
            patch.object(rr,'get_new_repo',side_effect=self.gh.info),
            patch.object(rr,'live_refs',side_effect=lambda r:self.gh.refs.copy()),
            patch.object(rr,'api',side_effect=self.gh.api),
            patch.object(rr,'collect',side_effect=lambda endpoint,**kw:self.gh.pulls if '/pulls?' in endpoint else []),
        ]
        for p in patches:
            p.start();self.addCleanup(p.stop)

    def test_preview_is_read_only(self):
        op.reconcile_default(self.s)
        self.assertFalse(rr.load_state(self.s).get('default_reconciliation'))
        self.assertTrue(all(m=='GET' for m,_,_ in self.gh.calls))
        self.assertEqual(self.gh.refs['refs/heads/master'],SEED)

    def test_verified_rename_restores_original_and_keeps_both_histories(self):
        op.reconcile_default(self.s,ack_repo=REPO)
        state=rr.load_state(self.s)
        self.assertEqual(self.gh.refs['refs/heads/master'],OLD)
        self.assertEqual(self.gh.refs[PRESERVED],SEED)
        self.assertEqual(self.gh.refs[ARCHIVE],OLD)
        self.assertEqual(self.gh.default,'master')
        self.assertFalse(self.gh.enabled)
        self.assertEqual(state['default_reconciliation']['phase'],'verified')
        expected,_=rr.expected_ref_mapping(state,self.s/'primary',self.gh.refs)
        self.assertEqual(expected['refs/heads/master'],OLD)
        self.assertEqual(expected[PRESERVED],SEED)
        self.assertEqual(rr.read_json(self.s/'report.json')['default_branch_reconciliation']['seed_sha'],SEED)
        self.assertNotIn(('PATCH', f'repos/{REPO}', {'default_branch':'master','force':True}),self.gh.calls)

    def test_interrupted_after_rename_resumes_without_renaming_twice(self):
        self.gh.fail_once_on=('POST',f'repos/{REPO}/git/refs')
        with self.assertRaisesRegex(rr.Refused,'simulated'):
            op.reconcile_default(self.s,ack_repo=REPO)
        self.assertEqual(self.gh.refs.get('refs/heads/master'),None)
        self.assertEqual(self.gh.refs[PRESERVED],SEED)
        self.assertEqual(rr.load_state(self.s)['default_reconciliation']['phase'],'renamed')
        op.reconcile_default(self.s,ack_repo=REPO)
        self.assertEqual(self.gh.refs['refs/heads/master'],OLD)
        renames=[(m,e) for m,e,_ in self.gh.calls if '/rename' in e and m=='POST']
        self.assertEqual(len(renames),1)

    def test_rename_succeeded_but_response_lost_is_resumable(self):
        self.gh.fail_after_rename_once=True
        with self.assertRaisesRegex(rr.Refused,'response was lost'):
            op.reconcile_default(self.s,ack_repo=REPO)
        self.assertEqual(self.gh.refs[PRESERVED],SEED)
        self.assertNotIn('refs/heads/master',self.gh.refs)
        op.reconcile_default(self.s,ack_repo=REPO)
        self.assertEqual(self.gh.refs['refs/heads/master'],OLD)
        self.assertEqual(self.gh.default,'master')
        renames=[x for x in self.gh.calls if x[0]=='POST' and x[1].endswith('/rename')]
        self.assertEqual(len(renames),1)

    def test_interrupted_default_patch_resumes(self):
        self.gh.fail_once_on=('PATCH',f'repos/{REPO}')
        with self.assertRaisesRegex(rr.Refused,'simulated'):
            op.reconcile_default(self.s,ack_repo=REPO)
        self.assertEqual(rr.load_state(self.s)['default_reconciliation']['phase'],'original-ref-restored')
        op.reconcile_default(self.s,ack_repo=REPO)
        self.assertEqual(self.gh.default,'master')

    def test_wrong_repo_id_refused(self):
        self.state['created_new_id']=1463338731
        rr.save_state(self.s,self.state)
        with self.assertRaises(rr.Refused):op.reconcile_default(self.s,ack_repo=REPO)

    def test_ref_drift_refused(self):
        self.gh.refs['refs/heads/master']='f'*40
        with self.assertRaisesRegex(rr.Refused,'unrecognized'):
            op.reconcile_default(self.s,ack_repo=REPO)
        self.assertTrue(all(m=='GET' for m,_,_ in self.gh.calls))

    def test_protected_default_refused(self):
        self.gh.protected=True
        with self.assertRaisesRegex(rr.Refused,'protected'):
            op.reconcile_default(self.s,ack_repo=REPO)

    def test_rulesets_refused(self):
        self.gh.rules=[{'name':'protected'}]
        with self.assertRaisesRegex(rr.Refused,'rulesets'):
            op.reconcile_default(self.s,ack_repo=REPO)

    def test_open_pr_to_master_refused(self):
        self.gh.pulls=[{'base':{'ref':'master'}}]
        with self.assertRaisesRegex(rr.Refused,'open PR'):
            op.reconcile_default(self.s,ack_repo=REPO)

    def test_actions_activation_requires_explicit_ack_and_verified_refs(self):
        with self.assertRaisesRegex(rr.Refused,'original default-branch'):
            op.activate_actions(self.s,ack_repo=REPO)
        op.reconcile_default(self.s,ack_repo=REPO)
        with patch.object(rr,'verify_recovery_configuration',return_value=None):
            op.activate_actions(self.s)
            self.assertFalse(self.gh.enabled)
            op.activate_actions(self.s,ack_repo=REPO)
        self.assertTrue(self.gh.enabled)
        self.assertEqual(self.gh.default,'master')
        report=rr.read_json(self.s/'report.json')
        self.assertFalse(report['fully_operationally_verified'])
        self.assertFalse(report['actions_workflow_registration_complete'])
        self.assertEqual(rr.load_state(self.s)['post_recovery_actions_activation'],'enabled-unqualified')

    def test_ci_options_without_pr_do_not_trigger_mutations(self):
        with self.assertRaisesRegex(rr.Refused,'need --pr NUMBER'):
            op.recover_operational(self.s,required=('N03',),
                                   ack_reconcile=REPO,ack_actions=REPO)
        self.assertEqual(self.gh.refs['refs/heads/master'],SEED)
        self.assertFalse(self.gh.enabled)

    def test_consolidated_recovery_requires_separate_approvals(self):
        with patch('devtool.recovery.qualification.audit_workflows',return_value={'workflows':[]}):
            op.recover_operational(self.s)
            self.assertEqual(self.gh.refs['refs/heads/master'],SEED)
            self.assertFalse(self.gh.enabled)
            with patch.object(rr,'verify_recovery_configuration',return_value=None):
                op.recover_operational(self.s,ack_reconcile=REPO,ack_actions=REPO)
        self.assertEqual(self.gh.refs['refs/heads/master'],OLD)
        self.assertEqual(self.gh.refs[PRESERVED],SEED)
        self.assertTrue(self.gh.enabled)
        self.assertEqual(rr.load_state(self.s)['post_recovery_actions_activation'],'enabled-unqualified')

    def test_consolidated_recovery_already_activated_is_idempotent(self):
        op.reconcile_default(self.s,ack_repo=REPO)
        with patch.object(rr,'verify_recovery_configuration',return_value=None):
            op.activate_actions(self.s,ack_repo=REPO)
        before=len(self.gh.calls)
        with patch('devtool.recovery.qualification.audit_workflows',return_value={'workflows':[]}):
            op.recover_operational(self.s)
        self.assertTrue(all(method=='GET' for method,_,_ in self.gh.calls[before:]))
        self.assertTrue(self.gh.enabled)

    def test_nonconflicting_restoration_never_requires_fork_rename(self):
        self.state['topology']='standalone'
        self.state.pop('master_archive')
        self.state.pop('fork_seed_refs')
        rr.save_state(self.s,self.state)
        self.gh.refs={'refs/heads/master':OLD,'refs/heads/next':'a'*40}
        self.gh.enabled=True
        with patch('devtool.recovery.qualification.audit_workflows',return_value={'workflows':[]}):
            op.recover_operational(self.s)
        self.assertTrue(self.gh.enabled)
        self.assertFalse(any('/rename' in path for _,path,_ in self.gh.calls))

    def test_actions_refuse_new_default_drift(self):
        op.reconcile_default(self.s,ack_repo=REPO)
        self.gh.refs['refs/heads/master']='f'*40
        with self.assertRaises(rr.Refused):op.activate_actions(self.s,ack_repo=REPO)
        self.assertFalse(self.gh.enabled)

    def test_unreviewed_extra_ref_refused(self):
        self.gh.refs['refs/heads/user-added']='b'*40
        with self.assertRaisesRegex(rr.Refused,'unreviewed Git references'):
            op.reconcile_default(self.s,ack_repo=REPO)
        self.assertEqual(self.gh.refs['refs/heads/master'],SEED)

    def test_actions_crash_after_api_before_journal_can_resume(self):
        op.reconcile_default(self.s,ack_repo=REPO)
        state=rr.load_state(self.s)
        state['post_recovery_actions_activation']='intent'
        rr.save_state(self.s,state)
        self.gh.enabled=True
        with patch.object(rr,'verify_recovery_configuration',return_value=None):
            op.activate_actions(self.s,ack_repo=REPO)
        self.assertTrue(self.gh.enabled)
        self.assertEqual(rr.load_state(self.s)['post_recovery_actions_activation'],'enabled-unqualified')

    def test_actions_mismatched_policy_fail_closed(self):
        op.reconcile_default(self.s,ack_repo=REPO)
        state=rr.load_state(self.s)
        state['post_recovery_actions_activation']='intent'
        rr.save_state(self.s,state)
        self.gh.enabled=True
        self.gh.policy['allowed_actions']='local_only'
        with patch.object(rr,'verify_recovery_configuration',return_value=None):
            with self.assertRaisesRegex(rr.Refused,'permission policy differs'):
                op.activate_actions(self.s,ack_repo=REPO)

    def test_unauthorized_actions_enable_refused(self):
        op.reconcile_default(self.s,ack_repo=REPO)
        self.gh.enabled=True
        with patch.object(rr,'verify_recovery_configuration',return_value=None):
            with self.assertRaisesRegex(rr.Refused,'outside journal'):
                op.activate_actions(self.s,ack_repo=REPO)

    def test_wrong_ack_only_prints_preview(self):
        op.reconcile_default(self.s,ack_repo='OTHER/REPO')
        self.assertEqual(self.gh.refs['refs/heads/master'],SEED)

    def test_cli_exposes_safe_recovery_commands(self):
        import json
        subtree=next(x for x in json.loads((Path(__file__).resolve().parents[1]/'devtool/core_cli_schema.json').read_text())['parser']['subparsers']['children'] if x['name']=='recovery')
        cmd=next(x for x in subtree['parser']['subparsers']['children'] if x['name']=='reconcile-default')
        opts={o for a in cmd['parser']['actions'] for o in a['option_strings']}
        self.assertIn('--ack-reconcile-default',opts)


if __name__=='__main__':unittest.main()
