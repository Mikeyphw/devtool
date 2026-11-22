"""Current M8 security and durable-inbox regression tests (no GitHub network)."""
from __future__ import annotations
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

SOURCE = Path(__file__).resolve().parents[1] / 'devtool/m8_webhooks.py'
spec = importlib.util.spec_from_file_location('m8_webhooks_exact', SOURCE)
mod = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

KEY = b'm8-tests-secret-32-bytes-aaaaaaaaaaaaaaaa'
REPO = 'Mikeyphw/devtool'
SHA = 'a' * 40
PIN = mod.Pin(REPO, 1276037219, 121212, SHA, 51, 'b'*32)


def make_body(event='pull_request', action='opened', sha=SHA, fork=False, repo=REPO,
              installation_id=121212, pr_number=51):
    payload = {'action':action,'repository':{'id':1276037219,'full_name':repo},
               'installation':{'id':installation_id}}
    if event == 'pull_request':
        payload.update({'number':pr_number,'pull_request':{'head':{'sha':sha,'repo':{'full_name':
                       'other/fork' if fork else REPO}},'base':{'repo':{'full_name':REPO}},'merged':False}})
    if event == 'workflow_run':
        payload['workflow_run'] = {'id':7876,'run_attempt':1,'head_sha':sha,'status':'completed',
                                  'conclusion':'success','pull_requests':[]}
    if event == 'check_run':
        payload['check_run'] = {'id':888,'head_sha':sha,'pull_requests':[{'number':pr_number}]}
    return json.dumps(payload,sort_keys=True,separators=(',',':')).encode()


def headers(body, event='pull_request', delivery=None):
    return {'X-Hub-Signature-256':'sha256='+hmac.new(KEY,body,hashlib.sha256).hexdigest(),
            'X-GitHub-Event':event,'X-GitHub-Delivery': delivery or str(uuid4())}

class WebhookM8Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'inbox'
        self.assertEqual(mod.pin(self.root,PIN),'CREATED')

    def test_pin_idempotent_and_drift_refused(self):
        self.assertEqual(mod.pin(self.root,PIN),'EXISTING')
        with self.assertRaisesRegex(mod.Refusal,'M8_PIN_CHANGE_REQUIRES_NEW_INBOX'):
            mod.pin(self.root,mod.Pin(REPO,1276037219,121212,SHA,52,'b'*32))

    def test_valid_pr_accepted_only_as_signal(self):
        body=make_body(); result=mod.ingest(self.root,headers(body),body,KEY)
        self.assertEqual(result['status'],'ACCEPTED')
        self.assertEqual(result['signal']['authority'],'wakeup-only')
        self.assertEqual(len(mod.inbox(self.root)['events']),1)

    def test_signed_duplicate_idempotent(self):
        body=make_body(); h=headers(body)
        self.assertEqual(mod.ingest(self.root,h,body,KEY)['status'],'ACCEPTED')
        self.assertEqual(mod.ingest(self.root,h,body,KEY)['status'],'DUPLICATE')
        self.assertEqual(len(list((self.root/'events').glob('*.json'))),1)

    def test_uuid_reuse_different_signed_payload_rejected(self):
        body=make_body(); h=headers(body)
        mod.ingest(self.root,h,body,KEY)
        changed=make_body(action='synchronize')
        with self.assertRaisesRegex(mod.Refusal,'M8_DELIVERY_COLLISION'):
            mod.ingest(self.root,headers(changed,delivery=h['X-GitHub-Delivery']),changed,KEY)

    def test_bad_hmac_precedes_json(self):
        body=b'not json'
        with self.assertRaisesRegex(mod.Refusal,'M8_SIGNATURE_MISMATCH'):
            mod.ingest(self.root,{'X-Hub-Signature-256':'sha256='+'0'*64,
                         'X-GitHub-Event':'pull_request','X-GitHub-Delivery':str(uuid4())},body,KEY)

    def test_missing_signature(self):
        with self.assertRaisesRegex(mod.Refusal,'M8_SIGNATURE_FORMAT'):
            mod.ingest(self.root,{'X-GitHub-Event':'pull_request','X-GitHub-Delivery':str(uuid4())},make_body(),KEY)

    def test_oversized_payload(self):
        body=b'a'*(mod.MAX_BODY+1)
        with self.assertRaisesRegex(mod.Refusal,'M8_BODY_SIZE'):
            mod.ingest(self.root,headers(body),body,KEY)

    def test_repo_installation_sha_and_pr_mismatch(self):
        values=[('repo',dict(repo='evil/other'),'M8_REPOSITORY_DRIFT'),
                ('installation',dict(installation_id=12),'M8_INSTALLATION_DRIFT'),
                ('sha',dict(sha='b'*40),'M8_PR_HEAD_DRIFT'),
                ('pr',dict(pr_number=52),'M8_PR_IDENTITY_DRIFT'),
                ('fork',dict(fork=True),'M8_FORK_REQUIRES_SEPARATE_POLICY')]
        for name,changes,code in values:
            with self.subTest(name=name), self.assertRaisesRegex(mod.Refusal,code):
                body=make_body(**changes)
                mod.ingest(self.root,headers(body),body,KEY)

    def test_workflow_success_never_qualifies_itself(self):
        body=make_body('workflow_run','completed')
        result=mod.ingest(self.root,headers(body,'workflow_run'),body,KEY)
        self.assertEqual(result['signal']['conclusion_signal'],'success')
        self.assertEqual(result['signal']['authority'],'wakeup-only')
        self.assertNotIn('passed',result['signal'])

    def test_check_run_matching_pr(self):
        body=make_body('check_run','completed')
        result=mod.ingest(self.root,headers(body,'check_run'),body,KEY)
        self.assertEqual(result['signal']['check_run_id'],888)

    def test_unknown_event_refused(self):
        body=make_body()
        with self.assertRaisesRegex(mod.Refusal,'M8_EVENT_NOT_ALLOWED'):
            mod.ingest(self.root,headers(body,'issue_comment'),body,KEY)

    def test_corrupted_journal_fails_closed(self):
        body=make_body(); h=headers(body)
        mod.ingest(self.root,h,body,KEY)
        receipt=self.root/'events'/(h['X-GitHub-Delivery']+'.json')
        receipt.write_text('broken',encoding='utf-8')
        with self.assertRaisesRegex(mod.Refusal,'M8_RECORD_INVALID'):
            mod.ingest(self.root,h,body,KEY)

    def test_state_symlink_refused(self):
        bogus=Path(self.temp.name)/'linked'
        bogus.symlink_to(self.root,target_is_directory=True)
        with self.assertRaisesRegex(mod.Refusal,'M8_STATE_SYMLINK'):
            mod.inbox(bogus)

    def test_secret_permissions(self):
        secret=Path(self.temp.name)/'secret';secret.write_bytes(KEY);secret.chmod(0o600)
        self.assertEqual(mod.secret_from(secret),KEY)
        secret.chmod(0o644)
        with self.assertRaisesRegex(mod.Refusal,'M8_SECRET_PERMISSIONS'):
            mod.secret_from(secret)

    def test_only_readonly_b3_is_exposed(self):
        self.assertFalse(hasattr(mod,'dispatch_workflow'))
        self.assertFalse(hasattr(mod,'merge_pr'))
        self.assertFalse(hasattr(mod,'push'))
        with self.assertRaisesRegex(mod.Refusal,'M8_REQUEST_ID_INVALID'):
            mod.qualify(self.root,'invalid')

    def test_event_driven_follow_uses_only_native_b3(self):
        from unittest.mock import patch
        body=make_body('workflow_run','completed')
        mod.ingest(self.root,headers(body,'workflow_run'),body,KEY)
        native={'head_sha':SHA,'session_id':'b'*32,'pre_merge_qualification':'PASSED',
                'required_delivery_ci':{'state':'PASSED'},
                'repository_regression':{'state':'PASSED'}}
        with patch.object(mod,'qualify',return_value={'native':native}) as observed:
            seen=set()
            result=mod.follow(self.root,'c'*32,observed=seen)
            self.assertEqual(result['status'],'OBSERVED')
            self.assertEqual(result['observations'][0]['pre_merge_qualification'],'PASSED')
            self.assertTrue(result['read_only'])
            self.assertFalse(result['automatic_mutation'])
            self.assertEqual(mod.follow(self.root,'c'*32,observed=seen)['status'],'NO_NEW_EVENTS')
            observed.assert_called_once()
        self.assertEqual(len(list((self.root/'observations').glob('*.json'))),1)

    def test_pr_maintenance_requires_explicit_authorization(self):
        with self.assertRaisesRegex(mod.Refusal,'M8_PR_MAINTENANCE_NOT_APPROVED'):
            mod.maintain_pr(self.root,approved=False)

if __name__=='__main__':unittest.main(verbosity=2)
