"""B0 recovery: real Git ancestry + durable journal reseal; no remote write."""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import types
from unittest.mock import patch

import pytest
from devtool import delivery_session as ds
from devtool.delivery_base_reconcile import advance_base_pin, BasePinRefused

REPO='Mikeyphw/devtool'


def git(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], check=True,
                            capture_output=True, text=True)
    return result.stdout.strip()


class Client:
    def __init__(self, base, head):
        self.base=base
        self.head=head
        self.calls=[]
        self.pr_number=48
    def _get(self, path):
        self.calls.append(path)
        if path == f'/repos/{REPO}/branches/main':
            return {'commit': {'sha':self.base}}
        if path == f'/repos/{REPO}/pulls/{self.pr_number}':
            return {
                'number': self.pr_number, 'state':'open',
                'head': {'ref':'feature/ga02', 'sha':self.head,
                         'repo':{'full_name':REPO}},
                'base': {'ref':'main', 'sha':self.base,
                         'repo':{'full_name':REPO}}
            }
        raise AssertionError(f'Unexpected GET {path}')


@pytest.fixture
def session(tmp_path):
    repo=tmp_path/'repo'
    repo.mkdir()
    git(repo,'init','-b','feature/ga02')
    git(repo,'config','user.email','runner@example.invalid')
    git(repo,'config','user.name','Runner')
    git(repo,'remote','add','origin','https://github.com/Mikeyphw/devtool.git')
    f=repo/'proof.txt'
    f.write_text('old')
    git(repo,'add','proof.txt')
    git(repo,'commit','-m','old')
    old=git(repo,'rev-parse','HEAD')
    f.write_text('new')
    git(repo,'commit','-am','new')
    new=git(repo,'rev-parse','HEAD')
    git(repo,'branch','main',new)
    f.write_text('source')
    git(repo,'commit','-am','source')
    source=git(repo,'rev-parse','HEAD')
    state=tmp_path/'delivery'/'sessions'
    created=ds.start(repo,head='feature/ga02',base='main',base_sha=old,state_root=state)
    sid=created['id']
    with ds._locked(state):
        path=ds._path(state,sid)
        rec=ds._read(path)
        rec['native_apply']={'state':'COMMITTED', 'transaction_id':'local-test-commit'}
        rec['remote_verified']=True
        rec['intents']=[{'id':'f'*32,'operation':'push','state':'confirmed',
                         'key_sha256':'0'*64,'evidence':{'head_sha':source}}]
        rec['events'].append({'kind':'mutation_confirmed','at':ds._utc(),
                              'operation':'push','intent_id':'f'*32})
        ds._write(path,rec)
    return repo,state,sid,old,new,source


def do(session,client=None,**kwargs):
    repo,state,sid,old,new,source=session
    return advance_base_pin(sid, expected_old_sha=old,
      verified_new_sha=new, existing_pr_number=48,
      approved=True, state_root=state, client=client or Client(new,source),**kwargs)


def test_advances_and_reseals_without_mutating_existing_history(session):
    repo,state,sid,old,new,source=session
    before=ds._read(ds._path(state,sid))
    client=Client(new,source)
    report=do(session,client=client)
    after=ds._read(ds._path(state,sid))
    assert report['state']=='BASE_PIN_ADVANCED'
    assert report['remote_mutation'] is False
    assert len(client.calls)==4
    assert all('/branches/main' in x or '/pulls/48' in x for x in client.calls)
    assert after['base_sha']==new
    assert after['intents']==before['intents']
    assert after['native_apply']==before['native_apply']
    assert after['head_sha']==source
    assert report['pr_recorded'] is True and report['pr_state']=='PR_VERIFIED'
    assert after['pr']['number']==48 and after['pr']['head_sha']==source
    assert after['pr']['base_sha']==new
    events=[x for x in after['events'] if x['kind']=='upstream_base_pin_advanced']
    assert len(events)==1 and events[0]['from_sha']==old and events[0]['to_sha']==new
    assert after['integrity_sha256']!=before['integrity_sha256']
    assert do(session)['state']=='ALREADY_ADVANCED'
    after_repeat=ds._read(ds._path(state,sid))
    assert after_repeat['events']==after['events']


def test_refuses_upstream_divergence(session):
    repo,state,sid,old,new,source=session
    git(repo,'branch','side',old)
    git(repo,'checkout','side')
    (repo/'side.txt').write_text('different')
    git(repo,'add','side.txt')
    git(repo,'commit','-m','side')
    divergent=git(repo,'rev-parse','HEAD')
    git(repo,'checkout','feature/ga02')
    with pytest.raises(BasePinRefused, match='BASE_PIN_NEW_MAIN_NOT_IN_SOURCE'):
        advance_base_pin(sid,expected_old_sha=old,verified_new_sha=divergent,
          existing_pr_number=48, approved=True,state_root=state,client=Client(divergent,source))
    assert ds._read(ds._path(state,sid))['base_sha']==old


def test_denies_unresolved_intent(session):
    repo,state,sid,old,new,source=session
    with ds._locked(state):
        path=ds._path(state,sid);record=ds._read(path)
        record['intents'][0]['state']='unresolved';ds._write(path,record)
    with pytest.raises(BasePinRefused, match='BASE_PIN_UNRESOLVED_INTENTS'):
        do(session)
    assert ds._read(ds._path(state,sid))['base_sha']==old


def test_wrong_remote_pr_does_not_modify_journal(session):
    repo,state,sid,old,new,source=session
    client=Client(new,source)
    client.pr_number=47
    with pytest.raises(BasePinRefused,match='BASE_PIN_PR_READ_UNAVAILABLE'):
        do(session,client=client)
    assert ds._read(ds._path(state,sid))['base_sha']==old


def test_remote_base_drift_rejected(session):
    repo,state,sid,old,new,source=session
    client=Client(old,source)
    with pytest.raises(BasePinRefused,match='BASE_PIN_REMOTE_BRANCH_CHANGED'):
        do(session,client=client)
    assert ds._read(ds._path(state,sid))['base_sha']==old


def test_wrong_source_or_missing_approval_refused(session):
    repo,state,sid,old,new,source=session
    with pytest.raises(BasePinRefused,match='BASE_PIN_EXPLICIT_APPROVAL_REQUIRED'):
        advance_base_pin(sid,expected_old_sha=old,verified_new_sha=new,
           existing_pr_number=48,approved=False,state_root=state,client=Client(new,source))
    (repo/'proof.txt').write_text('dirty is allowed but HEAD cannot drift')
    git(repo,'commit','-am','drift')
    with pytest.raises(BasePinRefused,match='BASE_PIN_SOURCE_IDENTITY_DRIFT'):
        do(session)


def test_wrong_owner_or_no_native_commit_refused(session):
    repo,state,sid,old,new,source=session
    with ds._locked(state):
        path=ds._path(state,sid);record=ds._read(path)
        record['base_repository']='Other/repo';ds._write(path,record)
    with pytest.raises(BasePinRefused,match='BASE_PIN_CROSS_REPOSITORY'):
        do(session)


def test_original_journal_has_exact_source_binding(session):
    repo,state,sid,old,new,source=session
    status=ds.status(sid,state_root=state)
    assert status['source_binding']=='matching'
    assert status['resumable'] is True
    assert status['base_sha']==old
