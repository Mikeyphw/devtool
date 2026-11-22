from __future__ import annotations
from datetime import datetime,timezone,timedelta
from pathlib import Path
import json
import os
import tempfile
import time
from unittest.mock import patch

import pytest

from devtool import m7_credentials as m7


REPO='Mikeyphw/devtool'

class FakeAppHTTP:
    def __init__(self,*, perms=None,install_id=300,selection='all',
                 account='Mikeyphw',account_id=400,repo=REPO,repo_id=700,
                 expires_in=3500,suspended=False):
        self.perms=perms if perms is not None else {
            'contents':'write','pull_requests':'write','actions':'write',
            'checks':'read'}
        self.install_id=install_id
        self.selection=selection
        self.account=account
        self.account_id=account_id
        self.repo=repo
        self.repo_id=repo_id
        self.expires_in=expires_in
        self.suspended=suspended
        self.calls=[]
    def request(self,method,path,credential,payload=None):
        self.calls.append((method,path,payload))
        if path in ('/app/installations/300','/repos/Mikeyphw/devtool/installation'):
            return {'id':self.install_id,'app_id':42,
                    'account':{'login':self.account,'id':self.account_id},
                    'repository_selection':self.selection,'permissions':self.perms,
                    'suspended_at':'now' if self.suspended else None}
        if path=='/app/installations/300/access_tokens':
            assert method=='POST'
            assert payload == {'repositories':['devtool'],
                               'permissions':{'contents':'read'}}
            return {'token':'SYNTHETIC_TEST_TOKEN_NOT_REAL',
                    'expires_at':datetime.fromtimestamp(int(time.time())+self.expires_in,
                                 timezone.utc).isoformat(),
                    'permissions':{'contents':'read'},
                    'repositories':[{'full_name':self.repo,'id':self.repo_id}]}
        if path=='/repos/Mikeyphw/devtool':
            return {'full_name':self.repo,'id':self.repo_id}
        raise AssertionError(('unexpected',method,path))


def cfg_env():
    return {'DEVTOOL_GITHUB_APP_ID':'42',
            'DEVTOOL_GITHUB_APP_INSTALLATION_ID':'300',
            'DEVTOOL_GITHUB_APP_ACCOUNT':'Mikeyphw',
            'DEVTOOL_GITHUB_APP_PRIVATE_KEY_PATH':'/private/test-only.pem'}


def audit(*,http=None,probe=False):
    with patch('devtool.m7_credentials._jwt',return_value='SYNTHETIC_JWT'), \
         patch('devtool.github_app._jwt',return_value='SYNTHETIC_JWT'), \
         patch('devtool.m7_credentials._public_key_fingerprint',return_value='a'*64):
        return m7.inspect_app(REPO,env=cfg_env(),http=http or FakeAppHTTP(),
                              probe=probe)


def test_installation_readonly_metadata_does_not_issue_token():
    http=FakeAppHTTP()
    report=audit(http=http)
    assert report['status']=='UNVERIFIED'
    assert report['identity_verified'] is True
    assert report['effective_read_verified'] is False
    assert report['repository_id'] is None
    assert report['capabilities']['actions_dispatch']['state']=='GRANTED_BY_INSTALLATION'
    assert report['write_execution_verified'] is False
    assert all(method=='GET' for method,_,__ in http.calls)


def test_explicit_read_scope_proves_repository_id_and_short_lived_expiry():
    http=FakeAppHTTP()
    report=audit(http=http,probe=True)
    assert report['status']=='VALID'
    assert report['repository_id']==700
    assert report['effective_read_verified']
    assert report['token_expiry_verified']
    assert 3000 < report['probe_token_ttl_seconds'] <= 3600
    assert sum(method=='POST' for method,_,__ in http.calls)==1
    assert 'SYNTHETIC_TEST_TOKEN_NOT_REAL' not in repr(report)


def test_missing_workflow_permission_is_not_implicitly_granted():
    report=audit(probe=True)
    assert report['workflow_file_update']=='UNVERIFIED_OR_NOT_GRANTED'


@pytest.mark.parametrize('settings,expected',[
    ({'install_id':301},'WRONG_INSTALLATION'),
    ({'account':'Other'},'WRONG_INSTALLATION'),
    ({'suspended':True},'WRONG_INSTALLATION'),
    ({'selection':'other'},'UNVERIFIED'),
    ({'account_id':None},'UNVERIFIED'),
])
def test_wrong_identity_and_malformed_installation_fail_closed(settings,expected):
    assert audit(http=FakeAppHTTP(**settings),probe=True)['status']==expected


def test_missing_capability_is_distinct_from_effective_read():
    report=audit(http=FakeAppHTTP(perms={'contents':'read'}),probe=True)
    assert report['status']=='PERMISSION_DENIED'
    assert report['effective_read_verified']
    assert report['capabilities']['branch_write']['state']=='PERMISSION_DENIED'
    assert report['capabilities']['repository_read']['state']=='GRANTED_BY_INSTALLATION'


def test_expired_probe_does_not_report_valid():
    report=audit(http=FakeAppHTTP(expires_in=60),probe=True)
    assert report['status']=='EXPIRED'
    assert report['effective_read_verified'] is False


def test_same_name_wrong_repo_id_does_not_trust_unverified_baseline():
    old={'schema':'devtool.m7.credential-baseline.v1',
         'snapshot':{'repository':REPO,'repository_id':700,'app_id':42,
                     'installation_id':300,'permissions':{'contents':'write'}}}
    now=audit(http=FakeAppHTTP(repo_id=701),probe=True)
    drift=m7.compare_baseline(now,old)
    assert drift['state']=='DRIFT_DETECTED'
    assert any(change['field']=='repository_id' for change in drift['changes'])


def test_permission_reduction_and_public_key_rotation_detected():
    old={'schema':'devtool.m7.credential-baseline.v1',
         'snapshot':{'repository':REPO,'permissions':{'contents':'write'},
                     'public_key_sha256':'b'*64}}
    now=audit(http=FakeAppHTTP(perms={'contents':'read'}),probe=True)
    drift=m7.compare_baseline(now,old)
    assert {'PERMISSION_REDUCED','IDENTITY_CHANGED'} <= {z['kind'] for z in drift['changes']}


def test_baseline_private_no_overwrite_and_symlink_refused():
    report=audit(probe=True)
    with tempfile.TemporaryDirectory() as tmp:
        parent=Path(tmp)
        parent.chmod(0o700)
        path=parent/'baseline.json'
        m7.write_baseline(path,report)
        data=m7.load_baseline(path)
        assert data['snapshot']['repository_id']==700
        assert 'token' not in json.dumps(data).lower()
        with pytest.raises(m7.CredentialDoctorError,match='ALREADY_EXISTS'):
            m7.write_baseline(path,report)
        link=parent/'link.json'
        link.symlink_to(path)
        with pytest.raises(m7.CredentialDoctorError,match='UNSAFE'):
            m7.load_baseline(link)


def test_predelete_always_blocks_future_repository_authority_even_with_backup():
    report=audit(probe=True)
    v=m7.predelete_feasibility(report,backup_sha256='f'*64,dependencies_verified=True)
    assert v['state']=='BLOCKED'
    assert v['destructive_operation_authorized'] is False
    assert 'FUTURE_REPOSITORY_CREATION_AND_CREDENTIAL_AUTHORITY_UNVERIFIED' in v['blockers']


def test_postrecreate_requires_changed_numeric_identity_and_dependent_checks():
    report=audit(http=FakeAppHTTP(repo_id=701),probe=True)
    blocked=m7.postrecreate_acceptance(report,previous_repository_id=701)
    assert blocked['state']=='BLOCKED'
    pending=m7.postrecreate_acceptance(report,previous_repository_id=700)
    assert 'DEPENDENT_REPOSITORY_CAPABILITIES_PENDING' in pending['blockers']
    complete=m7.postrecreate_acceptance(report,previous_repository_id=700,
                                         dependencies_verified=True,federation_verified=True)
    assert complete['state']=='CAPABILITY_PROBES_VERIFIED'
    assert complete['delivery_or_release_qualified'] is False


def test_user_pat_read_probe_does_not_mislabel_write():
    class Client:
        def _request(self,path):
            return {'id':700,'full_name':REPO,'permissions':{'admin':True,'push':True}},{}
    report=m7.inspect_user_token(REPO,mode='gh',client=Client())
    assert report['status']=='VALID'
    assert report['effective_read_verified'] is True
    assert report['token_expiry_verified'] is False
    assert report['capabilities']['pr_create']['state']=='UNVERIFIED'
    assert report['write_execution_verified'] is False


def test_pat_expiry_header_verification():
    class Client:
        def _request(self,path):
            return {'id':700,'full_name':REPO},{
                'GitHub-Authentication-Token-Expiration':
                    (datetime.now(timezone.utc)+timedelta(days=1)).strftime('%a, %d %b %Y %H:%M:%S GMT')}
    assert m7.inspect_user_token(REPO,mode='env',client=Client())['token_expiry_verified']


def test_provider_error_secret_never_reflected():
    class BadHTTP:
        def request(self,*args,**kwargs):
            raise m7.GitHubAppError('APP_PROVIDER_UNAVAILABLE secret=do-not-expose')
    report=audit(http=BadHTTP(),probe=True)
    assert 'do-not-expose' not in repr(report)
    assert report['status']=='PROVIDER_UNAVAILABLE'


def test_guided_repair_never_mutates():
    report=m7.repair_guidance({'repository':REPO,'status':'REQUIRES_APPROVAL'})
    assert not report['installation_change_performed']
    assert not report['remote_repository_mutation']


def test_ssh_probe_is_opt_in_and_never_claims_push(monkeypatch):
    calls=[]
    class FakeCompleted:
        returncode=0
        stdout='a'*40+'\tHEAD\n'
    def execute(*argv,**kwargs):
        calls.append((argv,kwargs))
        return FakeCompleted()
    monkeypatch.setattr(m7, '_utc', lambda: '2026-11-21T00:00:00+00:00')
    with patch('subprocess.run',side_effect=execute):
        without=m7.inspect_ssh(REPO,probe=False)
        with_probe=m7.inspect_ssh(REPO,probe=True)
    assert without['status']=='UNVERIFIED'
    assert with_probe['status']=='VALID'
    assert with_probe['effective_read_verified']
    assert not with_probe['write_execution_verified']
    assert len(calls)==1
    assert calls[0][0][0][0]=='git'
    assert calls[0][0][0][1]=='ls-remote'
    assert 'StrictHostKeyChecking=yes' in calls[0][1]['env']['GIT_SSH_COMMAND']


def test_ssh_failure_does_not_claim_a_valid_access():
    from subprocess import CompletedProcess
    with patch('subprocess.run',return_value=CompletedProcess([],128,'','private output')):
        report=m7.inspect_ssh(REPO,probe=True)
    assert report['status']=='UNVERIFIED'
    assert 'private output' not in str(report)


def test_header_reported_expired_pat_is_not_valid():
    class Client:
        def _request(self,path):
            return {'id':700,'full_name':REPO},{
              'GitHub-Authentication-Token-Expiration':
                 (datetime.now(timezone.utc)-timedelta(days=1)).strftime('%a, %d %b %Y %H:%M:%S GMT')}
    report=m7.inspect_user_token(REPO,mode='env',client=Client())
    assert report['status']=='EXPIRED'
    assert report['token_expiry_verified'] is True


def test_m7_cli_does_not_report_credential_validity_from_metadata_only(capsys,monkeypatch):
    import argparse
    from devtool.m7_credential_cli import run
    monkeypatch.setattr('devtool.m7_credential_cli.inspect_app',
        lambda *a,**k:{'status':'UNVERIFIED','repository':REPO,'effective_read_verified':False})
    args=argparse.Namespace(credential_action='doctor',credential_repository=REPO,
                            credential_auth='app',credential_probe_read=False,
                            credential_save_baseline='')
    rc=run(args)
    assert rc==2
    output=capsys.readouterr().out
    assert 'UNVERIFIED' in output
    assert 'token' not in output.lower()


def test_fork_head_requires_independent_app_scope(monkeypatch,capsys):
    import argparse
    from devtool.m7_credential_cli import run
    calls=[]
    def inspect(repo,**kwargs):
        calls.append((repo,kwargs))
        return {'repository':repo,'status':'VALID' if kwargs.get('role')!='head' else 'UNVERIFIED',
                'identity_verified':True,'effective_read_verified':True}
    monkeypatch.setattr('devtool.m7_credential_cli.inspect_app',inspect)
    args=argparse.Namespace(credential_action='doctor',credential_repository=REPO,
           credential_head_repository='ForkOwner/devtool',credential_auth='app',
           credential_probe_read=True,credential_save_baseline='')
    rc=run(args)
    report=json.loads(capsys.readouterr().out)
    assert rc==2
    assert calls==[(REPO,{'probe':True}),('ForkOwner/devtool',{'role':'head','probe':True})]
    assert report['reason']=='FORK_HEAD_CREDENTIAL_NOT_INDEPENDENTLY_VERIFIED'
    assert report['fork_head_health']['status']=='UNVERIFIED'
