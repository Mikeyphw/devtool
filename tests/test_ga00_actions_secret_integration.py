"""Source-level Actions secret journal fault injection (no GitHub writes)."""
from __future__ import annotations
import json
from types import SimpleNamespace
import pytest
from devtool.actions_setup import release_setup, SECRETS
from devtool.ga00_secret_journal import journal_path
from devtool.github_actions import GitHubActionsError
from devtool.android.signing import AndroidSigningManager
from tests.test_actions_09_release_setup import canonical_signer, Client, SHA, SIGNER


def test_partial_upload_writes_durable_intent_and_refuses_automatic_retry(canonical_signer,monkeypatch,tmp_path):
    root, _, _ = canonical_signer
    policy=tmp_path/'policy.json'
    client=Client();client.protected=True
    names=set()
    attempts=[]
    monkeypatch.setattr('devtool.actions_setup._source',lambda _:SHA)
    monkeypatch.setattr('devtool.actions_setup._application',lambda *a:'org.example.release')
    monkeypatch.setattr('devtool.actions_setup._secrets',lambda slug:set(names))
    monkeypatch.setattr(AndroidSigningManager,'inspect_profile',lambda self,profile,*,store_password=None:SimpleNamespace(sha256=SIGNER,expired=False))
    def gh(args,*,input_text=None,**kwargs):
        attempts.append(args[2])
        if len(attempts)==2:
            raise GitHubActionsError('provider outcome unknown')
        names.add(args[2])
        return ''
    monkeypatch.setattr('devtool.actions_setup._gh',gh)
    with pytest.raises(GitHubActionsError,match='outcome unknown'):
        release_setup(client,'Owner/repo',root,policy_file=str(policy),execute=True)
    report=journal_path(policy)
    records=[json.loads(v) for v in report.read_text().splitlines()]
    assert [x['event'] for x in records]==['INTENT','ACK','INTENT']
    assert records[1]['name']==SECRETS[0] and records[2]['name']==SECRETS[1]
    assert 'existing-password' not in report.read_text()
    with pytest.raises(GitHubActionsError,match='unresolved prior intent'):
        release_setup(client,'Owner/repo',root,policy_file=str(policy),execute=True,rotate_secrets=True)
    assert attempts == [SECRETS[0],SECRETS[1]]
    assert not policy.exists()
