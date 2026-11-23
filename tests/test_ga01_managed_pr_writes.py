"""GA-01 operation-specific PR PATCH/PUT integration and adverse source tests.

All network I/O below is controlled in-memory. This suite never calls a live API.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.error import URLError, HTTPError
from uuid import uuid4

import pytest

from devtool import m8_automation as m8, ga01_scoped_auth as ga01
from devtool.github_actions import GitHubDispatchOutcomeUnknown, GitHubActionsError

REPO='Mikeyphw/devtool'
SHA='a'*40
BASE='b'*40
SID='c'*32


def policy(effect, *, body='', before='', labels=(), label_before='', include_new=True):
    raw={
        'schema':'devtool.m8.automation-policy.v1','policy_id':str(uuid4()),
        'repository':REPO,'repository_id':1276037219,'installation_id':54321,
        'head_sha':SHA,'head_branch':'feature/test','base_branch':'main','base_sha':BASE,
        'pr_number':51,'session_id':SID,
        'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'events':['pull_request:opened'],'effects':['observe',effect],
        'labels':list(labels),'comment':'','status_context':'','status_state':'',
        'workflow':'','rerun_run_id':0,'max_events':20,
    }
    if include_new:
        raw.update(pr_body=body,pr_body_before_sha256=before,pr_labels_before_sha256=label_before)
    return raw


class FakeResponse:
    def __init__(self,payload,code=200):
        self.raw=json.dumps(payload).encode('utf-8');self.status=code
    def __enter__(self):return self
    def __exit__(self,*exc):return None
    def read(self,size):return self.raw[:size]


class FakeOpener:
    def __init__(self,client):self.client=client;self.error=None
    def open(self,request,timeout):
        self.client.calls.append(request)
        if self.error is not None:raise self.error
        data=json.loads(request.data)
        if request.get_method()=='PATCH':
            assert request.full_url==f'https://api.github.com/repos/{REPO}/pulls/51'
            self.client.pr['body']=data['body']
            return FakeResponse(self.client.pr)
        if request.get_method()=='PUT':
            assert request.full_url==f'https://api.github.com/repos/{REPO}/issues/51/labels'
            self.client.labels=data['labels']
            return FakeResponse([{'name':x} for x in self.client.labels])
        raise AssertionError('Unexpected HTTP method')


class FakeClient:
    def __init__(self, *, body='old', labels=('old',)):
        self._token='synthetic-token-never-print';self.timeout=3
        self._opener=FakeOpener(self);self.calls=[];self.guards=[]
        self.labels=list(labels)
        self.pr={'number':51,'body':body,'state':'open',
                 'head':{'sha':SHA,'ref':'feature/test','repo':{'full_name':REPO}},
                 'base':{'ref':'main','sha':BASE,'repo':{'full_name':REPO}}}
    def assert_approved_pr_write(self,slug,operation,method,number):
        self.guards.append((slug,operation,method,number))
        if slug!=REPO or number!=51 or {'pr_body_set':'PATCH','pr_labels_replace':'PUT'}.get(operation)!=method:
            raise RuntimeError('FORBIDDEN_WRITE')
    def _get(self,path):
        if path==f'/repos/{REPO}':return {'id':1276037219,'full_name':REPO}
        if path==f'/repos/{REPO}/branches/feature%2Ftest':return {'commit':{'sha':SHA}}
        if path==f'/repos/{REPO}/pulls/51':return self.pr
        if path==f'/repos/{REPO}/issues/51':return {'labels':[{'name':x} for x in self.labels]}
        raise AssertionError('Unexpected read: '+path)


def _install(root: Path, effect: str, client: FakeClient, **kw):
    from devtool import m8_webhooks as wh
    policy_bytes=policy(effect,**kw)
    # Use M8's real secure pin writer before authorizing the immutable policy.
    wh.pin(root, wh.Pin(REPO,1276037219,54321,SHA,51,SID))
    with (mock.patch.object(m8,'_native_session',return_value={
           'repository':REPO,'head_sha':SHA,'head':'feature/test','base':'main',
           'pr':{'number':51},'source_binding':'matching','resumable':True}),
          mock.patch.object(m8,'_client',return_value=client)):
        m8.approve(root,policy_bytes,yes=True,client=client)
    return policy_bytes


def test_operation_map_and_unknown_refusal():
    assert ga01.capability_for_operation('m8.pr.body.set')=='pr_maintain'
    assert ga01.capability_for_operation('m8.pr.labels.replace')=='pr_maintain'
    with pytest.raises(ga01.AppOperationRefused,match='GA01_OPERATION_NOT_ADMITTED'):
        ga01.capability_for_operation('m8.pr.merge')


def test_legacy_policy_v1_has_no_new_write_authority():
    p=policy('pr_comment',include_new=False)
    p['comment']='allowed note'
    assert m8.Policy.parse(p).pr_body==''
    assert m8.Policy.parse(p).pr_labels_before_sha256==''
    with pytest.raises(m8.M8Refusal,match='M8_POLICY_PR_BODY_PREIMAGE_REQUIRED'):
        m8.Policy.parse(policy('pr_body_set',body='new',before=''))


def test_new_operations_require_exact_preimages():
    old=hashlib.sha256(b'old').hexdigest()
    new=policy('pr_body_set',body='new',before=old)
    assert m8.Policy.parse(new).pr_body=='new'
    new['pr_body_before_sha256']='invalid'
    with pytest.raises(m8.M8Refusal):m8.Policy.parse(new)
    new=policy('pr_labels_replace',labels=('fresh',))
    with pytest.raises(m8.M8Refusal,match='LABEL_PREIMAGE_REQUIRED'):
        m8.Policy.parse(new)
    new=policy('pr_labels_replace',labels=('fresh',),label_before='a'*64)
    assert m8.Policy.parse(new).labels==('fresh',)
    new['effects'].append('pr_labels_add')
    with pytest.raises(m8.M8Refusal,match='LABEL_SCOPE'):m8.Policy.parse(new)


def test_policy_receipt_never_prints_approved_body():
    raw=policy('pr_body_set',body='private customer text',before='a'*64)
    public=m8.Policy.parse(raw).public()
    assert 'private customer text' not in json.dumps(public)
    assert 'pr_body' not in public
    assert public['pr_body_sha256']==hashlib.sha256(b'private customer text').hexdigest()


def test_reject_cross_repo_and_unknown_operation_before_token():
    p=m8.Policy.parse(policy('pr_body_set',body='new',before='a'*64))
    client=FakeClient()
    with pytest.raises(m8.M8Refusal,match='WRITE_OPERATION_INVALID'):
        m8._approved_write(client,p,'merge','51',{'body':'new'})
    assert not client.calls
    client.assert_approved_pr_write=lambda *a: (_ for _ in ()).throw(RuntimeError('wrong repo'))
    with pytest.raises(RuntimeError,match='wrong repo'):
        m8._approved_write(client,p,'pr_body_set','51',{'body':'new'})
    assert not client.calls


def test_body_preimage_drift_has_zero_write_or_native_intent(tmp_path):
    client=FakeClient(body='changed')
    _install(tmp_path,'pr_body_set',client,body='approved',before=hashlib.sha256(b'old').hexdigest())
    with (mock.patch.object(m8,'_native_session',return_value={
               'repository':REPO,'head_sha':SHA,'head':'feature/test','base':'main',
               'pr':{'number':51},'source_binding':'matching','resumable':True}),
          mock.patch.object(m8,'_native_intent') as intent):
        with pytest.raises(m8.M8Refusal,match='BODY_PREIMAGE_DRIFT'):
            m8.maintain(tmp_path,'pr_body_set',yes=True,client=client)
        intent.assert_not_called()
    assert not client.calls


def test_body_patch_exact_postimage_and_native_intent(tmp_path):
    client=FakeClient()
    _install(tmp_path,'pr_body_set',client,body='approved',before=hashlib.sha256(b'old').hexdigest())
    with (mock.patch.object(m8,'_native_session',return_value={
             'repository':REPO,'head_sha':SHA,'head':'feature/test','base':'main',
             'pr':{'number':51},'source_binding':'matching','resumable':True}),
          mock.patch.object(m8,'_native_intent') as intent,
          mock.patch.object(m8,'_native_confirm') as confirm):
        result=m8.maintain(tmp_path,'pr_body_set',yes=True,client=client)
    assert result['state']=='NATIVE_CONFIRMED'
    assert len(client.calls)==1 and client.calls[0].get_method()=='PATCH'
    assert client.guards==[(REPO,'pr_body_set','PATCH',51)]
    intent.assert_called_once()
    confirm.assert_called_once()
    assert intent.call_args.args[1]=='pr_body_set'


def test_label_put_exact_set_preimage_and_confirmation(tmp_path):
    client=FakeClient(labels=('old','keep'))
    digest=m8._hash(m8._canonical({'labels':['keep','old']}))
    _install(tmp_path,'pr_labels_replace',client,labels=('new','keep'),label_before=digest)
    with (mock.patch.object(m8,'_native_session',return_value={
             'repository':REPO,'head_sha':SHA,'head':'feature/test','base':'main',
             'pr':{'number':51},'source_binding':'matching','resumable':True}),
          mock.patch.object(m8,'_native_intent') as intent,
          mock.patch.object(m8,'_native_confirm') as confirm):
        result=m8.maintain(tmp_path,'pr_labels_replace',yes=True,client=client)
    assert result['state']=='NATIVE_CONFIRMED'
    assert len(client.calls)==1 and client.calls[0].get_method()=='PUT'
    assert set(client.labels)=={'new','keep'}
    intent.assert_called_once();confirm.assert_called_once()
    assert intent.call_args.args[1]=='pr_labels_replace'


def test_label_drift_blocks_before_intent(tmp_path):
    client=FakeClient(labels=('another',))
    _install(tmp_path,'pr_labels_replace',client,labels=('new',),label_before='a'*64)
    with (mock.patch.object(m8,'_native_session',return_value={
             'repository':REPO,'head_sha':SHA,'head':'feature/test','base':'main',
             'pr':{'number':51},'source_binding':'matching','resumable':True}),
          mock.patch.object(m8,'_native_intent') as intent):
        with pytest.raises(m8.M8Refusal,match='LABEL_PREIMAGE_DRIFT'):
            m8.maintain(tmp_path,'pr_labels_replace',yes=True,client=client)
        intent.assert_not_called()
    assert not client.calls


def test_ambiguous_patch_keeps_durable_intent_unconfirmed(tmp_path):
    client=FakeClient();client._opener.error=URLError('outage, no leak')
    _install(tmp_path,'pr_body_set',client,body='new',before=hashlib.sha256(b'old').hexdigest())
    with (mock.patch.object(m8,'_native_session',return_value={
             'repository':REPO,'head_sha':SHA,'head':'feature/test','base':'main',
             'pr':{'number':51},'source_binding':'matching','resumable':True}),
          mock.patch.object(m8,'_native_intent') as intent,
          mock.patch.object(m8,'_native_confirm') as confirm):
        with pytest.raises(GitHubDispatchOutcomeUnknown,match='outcome uncertain') as err:
            m8.maintain(tmp_path,'pr_body_set',yes=True,client=client)
    assert 'synthetic-token-never-print' not in str(err.value)
    intent.assert_called_once()
    confirm.assert_not_called()
    assert len(client.calls)==1


def test_http_403_has_no_secret_in_error():
    client=FakeClient();p=m8.Policy.parse(policy('pr_body_set',body='new',before='a'*64))
    client._opener.error=HTTPError('https://api.github.com',403,'secret-payload',None,None)
    with pytest.raises(GitHubActionsError) as caught:
        m8._approved_write(client,p,'pr_body_set','51',{'body':'new'})
    assert 'secret' not in str(caught.value)
    assert len(client.calls)==1


def test_empty_label_set_is_explicitly_allowed_only_with_preimage(tmp_path):
    client=FakeClient(labels=('remove',))
    digest=m8._hash(m8._canonical({'labels':['remove']}))
    _install(tmp_path,'pr_labels_replace',client,labels=(),label_before=digest)
    with (mock.patch.object(m8,'_native_session',return_value={
             'repository':REPO,'head_sha':SHA,'head':'feature/test','base':'main',
             'pr':{'number':51},'source_binding':'matching','resumable':True}),
          mock.patch.object(m8,'_native_intent') as intent,
          mock.patch.object(m8,'_native_confirm') as confirm):
        result=m8.maintain(tmp_path,'pr_labels_replace',yes=True,client=client)
    assert result['state']=='NATIVE_CONFIRMED'
    assert client.labels==[]
    assert len(client.calls)==1 and client.calls[0].get_method()=='PUT'
    intent.assert_called_once(); confirm.assert_called_once()


def test_body_can_be_cleared_with_pinned_preimage(tmp_path):
    client=FakeClient(body='delete me')
    _install(tmp_path,'pr_body_set',client,body='',before=hashlib.sha256(b'delete me').hexdigest())
    with (mock.patch.object(m8,'_native_session',return_value={
             'repository':REPO,'head_sha':SHA,'head':'feature/test','base':'main',
             'pr':{'number':51},'source_binding':'matching','resumable':True}),
          mock.patch.object(m8,'_native_intent') as intent,
          mock.patch.object(m8,'_native_confirm') as confirm):
        result=m8.maintain(tmp_path,'pr_body_set',yes=True,client=client)
    assert result['state']=='NATIVE_CONFIRMED'
    assert client.pr['body']==''
    intent.assert_called_once(); confirm.assert_called_once()

def test_existing_label_add_still_requires_nonempty_labels():
    with pytest.raises(m8.M8Refusal,match='M8_POLICY_LABEL_SCOPE'):
        m8.Policy.parse(policy('pr_labels_add',labels=()))

@pytest.mark.parametrize('operation,payload,target', [
    ('pr_body_set', {'body':'approved'}, '52'),
    ('pr_body_set', {'body':'forged'}, '51'),
    ('pr_body_set', {'body':'approved','title':'unsafe'}, '51'),
    ('pr_labels_replace', {'labels':['forged']}, '51'),
    ('pr_labels_replace', {'labels':['approved']}, '52'),
])
def test_pr_managed_write_cannot_escape_approved_target_or_payload(operation,payload,target):
    p=m8.Policy.parse(policy(
        operation,body='approved' if operation=='pr_body_set' else '',
        before='a'*64 if operation=='pr_body_set' else '',
        labels=('approved',) if operation=='pr_labels_replace' else (),
        label_before='a'*64 if operation=='pr_labels_replace' else ''))
    client=FakeClient()
    with pytest.raises(m8.M8Refusal, match='M8_PR_WRITE_(?:TARGET|PAYLOAD)_NOT_APPROVED'):
        m8._approved_write(client,p,operation,target,payload)
    assert not client.guards and not client.calls


def test_old_post_transport_keeps_strict_2048_byte_budget():
    raw=policy('pr_comment')
    raw['comment']='approved'
    p=m8.Policy.parse(raw)
    client=FakeClient()
    with pytest.raises(m8.M8Refusal,match='M8_WRITE_BODY_TOO_LARGE'):
        m8._approved_write(client,p,'pr_comment','51',{'body':'z'*2100})
    assert not client.calls


def _native_confirm_fixture(monkeypatch, operation, key='approved-effect'):
    from contextlib import nullcontext
    from devtool import delivery_session as ds
    state={
        'head_sha':SHA,
        'pr':{'number':51},
        'intents':[{'id':'d'*32,'operation':operation,
                    'key_sha256':hashlib.sha256(key.encode()).hexdigest(),
                    'state':'unresolved'}],
        'events':[],
    }
    writes=[]
    monkeypatch.setattr(ds,'_state_root',lambda *a,**kw:Path('/mock/state'))
    monkeypatch.setattr(ds,'_path',lambda *a,**kw:Path('/mock/state/session.json'))
    monkeypatch.setattr(ds,'_locked',lambda *a,**kw:nullcontext())
    monkeypatch.setattr(ds,'_read',lambda *a,**kw:state)
    monkeypatch.setattr(ds,'_write',lambda path,record:writes.append(record.copy()))
    return ds,key,state,writes


@pytest.mark.parametrize('operation,proof', [
    ('pr_body_set',{'head_sha':SHA,'pr_number':52,'body_sha256':'a'*64}),
    ('pr_body_set',{'head_sha':SHA,'pr_number':51,'body_sha256':'x'}),
    ('pr_body_set',{'head_sha':SHA,'pr_number':51,'body_sha256':'a'*64,'title':'forged'}),
    ('pr_labels_replace',{'head_sha':SHA,'pr_number':52,'labels':['safe']}),
    ('pr_labels_replace',{'head_sha':SHA,'pr_number':51,'labels':['bad value']}),
    ('pr_labels_replace',{'head_sha':SHA,'pr_number':51,'labels':['same','same']}),
    ('pr_labels_replace',{'head_sha':SHA,'pr_number':51,'labels':['x']*9}),
])
def test_native_confirmation_rejects_forged_pr_or_payload(monkeypatch,operation,proof):
    ds,key,state,writes=_native_confirm_fixture(monkeypatch,operation)
    with pytest.raises(ds.DeliverySessionError,match='GA01'):
        ds.confirm_intent(SID,operation,key,proof)
    assert state['intents'][0]['state']=='unresolved'
    assert not writes


@pytest.mark.parametrize('operation,proof', [
    ('pr_body_set',{'head_sha':SHA,'pr_number':51,'body_sha256':'a'*64}),
    ('pr_labels_replace',{'head_sha':SHA,'pr_number':51,'labels':[]}),
    ('pr_labels_replace',{'head_sha':SHA,'pr_number':51,'labels':['qualified']}),
])
def test_native_confirmation_requires_valid_exact_pr_proof(monkeypatch,operation,proof):
    ds,key,state,writes=_native_confirm_fixture(monkeypatch,operation)
    result=ds.confirm_intent(SID,operation,key,proof)
    assert result['confirmed'] is True and result['reconciled'] is True
    assert state['intents'][0]['state']=='confirmed'
    assert state['intents'][0]['evidence']==proof
    assert len(writes)==1
