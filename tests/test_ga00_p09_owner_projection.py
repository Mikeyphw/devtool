"""P09 production client/CLI integration. Transport and remote writes are fixtures.

No live credential, production keystore or Gradle is used. Exercise the real
GET normalization, LIFE registry, private-file resolver, journal, source pin,
policy writer and release verifier, not a client with a mocked repository().
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest

from devtool.actions_setup import SECRETS
from devtool.android.signing import AndroidSigningManager
from devtool.artifact_trust import SigningIdentityRegistry, SecretProviderRef
from devtool.cli import build_parser
from devtool.ga00_environment import SIGNING_ENVIRONMENT, inspect_environment
from devtool.github_actions import GitHubActionsClient, GitHubActionsError, actions_cli
from devtool.actions_release import release_policy
from tests.test_actions_00_01_github_provider import Response

ROOT = Path(__file__).resolve().parents[1]
REPO = 'FixtureOwner/fixture-repo'
OWNER = {'id': 12001, 'type': 'User', 'login': 'FixtureOwner'}
ENV_ID = 8021
WORKFLOW = '.github/workflows/devtool-android-build.yml'
OWNER_ID = OWNER['id']
SHA = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()


class Transport:
    """HTTP boundary only: real GitHubActionsClient performs all normalization."""
    def __init__(self):
        self.repo = {'full_name': REPO, 'id': 91, 'private': False, 'default_branch': 'main',
                     'owner': {**OWNER, 'unprojected_marker': 'fixture-sensitive-value'}}
        self.environment = {'id': ENV_ID, 'name': SIGNING_ENVIRONMENT, 'can_admins_bypass': False,
            'deployment_branch_policy': {'protected_branches': True, 'custom_branch_policies': False},
            'protection_rules': [{'type': 'required_reviewers', 'prevent_self_review': False,
                                  'reviewers': [{'type': 'User', 'reviewer': {'id': OWNER_ID}}]}]}
        self.workflow = {'id': 777, 'path': WORKFLOW, 'state': 'active'}
        self.run = {'id': 42, 'repository': {'full_name': REPO}, 'event': 'workflow_dispatch',
                    'run_attempt': 1, 'head_sha': SHA, 'head_branch': 'main', 'workflow_id': 777,
                    'status': 'completed', 'conclusion': 'success'}
        self.approvals = [{'state': 'approved', 'user': copy.deepcopy(OWNER),
                           'environments': [{'id': ENV_ID, 'name': SIGNING_ENVIRONMENT}],
                           'comment': 'fixture-private-comment'}]
        self.review_headers = {}
        self.requests = []
        self.client = GitHubActionsClient('fixture-token', opener=self)

    def open(self, req, timeout):
        assert req.get_method() == 'GET'
        assert req.get_header('Authorization') == 'Bearer fixture-token'
        prefix = 'https://api.github.com/repos/' + REPO
        assert req.full_url.startswith(prefix)
        path = req.full_url.removeprefix(prefix)
        self.requests.append(path)
        routes = {'': self.repo, '/commits/main': {'sha': SHA}, '/branches/main': {'protected': True},
                  '/environments/' + SIGNING_ENVIRONMENT: self.environment,
                  '/actions/workflows/devtool-android-build.yml': self.workflow,
                  '/actions/runs/42': self.run, '/actions/runs/42/approvals': self.approvals}
        assert path in routes, f'unplanned GET: {path}'
        return Response(copy.deepcopy(routes[path]), self.review_headers if path.endswith('/approvals') else None)


def test_projection_minimal_owner_and_standalone_parity():
    t = Transport()
    normalized = t.client.repository(REPO)
    assert normalized['owner'] == OWNER
    assert 'fixture-sensitive-value' not in json.dumps(normalized)
    from devtool.ga00_environment import evaluate_environment, personal_owner_id
    standalone = evaluate_environment(t.environment, approval_profile='single-owner',
                                      owner_id=personal_owner_id(t.repo, REPO))
    assert standalone == inspect_environment(t.client, REPO, approval_profile='single-owner')
    assert standalone['verified'] and standalone['owner_reviewer_id'] == OWNER_ID
    assert not standalone['deployment_approved'] and not standalone['secret_values_verified']


@pytest.mark.parametrize('owner', [None, {}, {'login': 'other', 'id': OWNER_ID, 'type': 'User'},
    {**OWNER, 'id': True}, {**OWNER, 'id': str(OWNER_ID)}, {**OWNER, 'id': 0},
    {**OWNER, 'type': 'Organization'}, {**OWNER, 'type': 'Bot'}, {**OWNER, 'id': 3}])
def test_owner_missing_malformed_or_not_reviewer_fails_closed(owner):
    t = Transport(); t.repo['owner'] = owner
    assert not inspect_environment(t.client, REPO, approval_profile='single-owner')['verified']


def test_pinned_owner_drift_and_unchanged_multi_reviewer():
    t = Transport()
    assert not inspect_environment(t.client, REPO, approval_profile='single-owner', expected_owner_id=999)['verified']
    assert not inspect_environment(t.client, REPO)['verified']
    t.repo.pop('owner')
    t.environment['protection_rules'][0]['prevent_self_review'] = True
    assert inspect_environment(t.client, REPO)['verified']


def test_canonical_workflow_path_exact_identity():
    t = Transport(); assert t.client.workflow(REPO, WORKFLOW)['path'] == WORKFLOW
    assert t.requests == ['/actions/workflows/devtool-android-build.yml']
    for bad in ('.github/workflows/../foo.yml', '.github/workflows/7', '.github/workflows/x/y.yml'):
        with pytest.raises(GitHubActionsError):
            t.client.workflow(REPO, bad)
    assert len(t.requests) == 1
    t.workflow['path'] = '.github/workflows/other.yml'
    with pytest.raises(GitHubActionsError, match='path differs'):
        t.client.workflow(REPO, WORKFLOW)


@pytest.fixture
def provision(tmp_path, monkeypatch):
    """Real private PKCS12/keytool and LIFE registry; only network is mocked."""
    if not shutil.which('keytool'):
        pytest.skip('Real JDK keytool unavailable; transport and negative tests remain mandatory')
    private = tmp_path / 'private'; private.mkdir(mode=0o700)
    key = private / 'fixture.p12'; pw = private / 'password'; secret = 'test-only-P09-password-123'
    pw.write_text(secret); pw.chmod(0o600)
    cert = AndroidSigningManager().create(key, alias='fixture-release', store_password=secret, key_size=2048).sha256
    assert len(cert) == 64 and key.stat().st_mode & 0o077 == 0
    registry = SigningIdentityRegistry(private/'identities.json')
    registry.create_reference(identity_id='fixture-release', provider='android-keystore-pkcs12',
        key_reference=str(key), alias='fixture-release', certificate_sha256=cert,
        purposes=('release-signing',), variants=('release',), secret_provider=SecretProviderRef('private-file', str(pw)))
    monkeypatch.setenv('DEVTOOL_SIGNING_IDENTITY_REGISTRY', str(registry.path))
    t = Transport(); names=set(); writes=[]; policy=private/'policy.json'
    def fake_gh(argv, *, input_text=None, **kwargs):
        assert '--repo' in argv and argv[argv.index('--repo')+1] == REPO
        if argv[:2] == ['secret','list']:
            assert input_text is None
            return json.dumps([{'name': n} for n in sorted(names)] if '--env' in argv else [])
        assert argv[:2] == ['secret','set'] and argv[-2:] == ['--env', SIGNING_ENVIRONMENT]
        assert input_text and input_text not in argv
        expected = dict(zip(SECRETS, (__import__('base64').b64encode(key.read_bytes()).decode(), secret, 'fixture-release', secret)))
        assert input_text == expected[argv[2]]
        writes.append(argv[2]); names.add(argv[2]); return ''
    monkeypatch.setattr('devtool.actions_setup._gh', fake_gh)
    # Actual parser and Actions dispatch; swap only the transport constructor.
    monkeypatch.setattr('devtool.github_actions._actions_client', lambda *a: (t.client,'fixture-transport'))
    def cli(sub, *options):
        args=build_parser().parse_args(['actions','release',sub,'--github-repo',REPO,'--policy',str(policy),*options])
        return actions_cli(args,ROOT)
    return t,cli,policy,cert,writes,names,secret,key


def test_real_client_life_private_file_setup_and_readiness(provision):
    t,cli,policy,cert,writes,names,secret,key=provision
    opts=('--approval-profile','single-owner','--signing-identity','fixture-release')
    plan=cli('setup',*opts)
    assert plan['gates']['signing_environment_protected'] and plan['gates']['signing_identity_configured']
    assert not plan['ready'] and not writes and not policy.exists()
    result=cli('setup',*opts,'--yes')
    assert result['ready'] and not result['qualified_release'] and writes==list(SECRETS)
    assert result['signer_sha256']==cert and not result['secret_values_verified']
    document=release_policy(ROOT,str(policy))
    assert document['schema']==2 and document['owner_reviewer_id']==OWNER_ID
    assert document['signer_sha256']==cert and policy.stat().st_mode&0o077==0
    assert cli('readiness',*opts)['ready']
    assert not cli('readiness','--approval-profile','multi-reviewer')['ready']
    assert cli('setup',*opts,'--yes')['ready'] and writes==list(SECRETS)
    from devtool.ga00_secret_journal import journal_path
    raw=journal_path(policy).read_text(); records=[json.loads(l) for l in raw.splitlines()]
    assert len(records)==12 and secret not in raw and key.read_bytes().hex() not in raw
    assert [r['event'] for r in records].count('INTENT')==4
    t.repo['owner']['id']=OWNER_ID+1
    assert not cli('readiness',*opts)['ready']
    with pytest.raises(GitHubActionsError, match='NOT_PROTECTED'):
        cli('setup',*opts,'--yes')
    assert writes==list(SECRETS)


def test_provisioning_refuses_drift_between_writes(provision, monkeypatch):
    t,cli,policy,cert,writes,names,secret,key=provision
    from devtool import actions_setup
    original=actions_setup._gh
    def gh(argv, **kw):
        result=original(argv,**kw)
        if argv[:2]==['secret','set']:
            t.environment['can_admins_bypass']=True
        return result
    monkeypatch.setattr(actions_setup,'_gh',gh)
    with pytest.raises(GitHubActionsError,match='environment changed'):
        cli('setup','--approval-profile','single-owner','--yes')
    assert writes==[SECRETS[0]] and not policy.exists()


@pytest.fixture
def verifier(tmp_path, monkeypatch):
    t=Transport(); policy=tmp_path/'policy.json'; signer='a'*64
    policy.write_text(json.dumps({'schema':2,'approval_profile':'single-owner','owner_reviewer_id':OWNER_ID,
        'repository':REPO,'workflow':WORKFLOW,'source_sha':SHA,'ref':'refs/heads/main',
        'application_id':'org.example.fixture','signer_sha256':signer,'format':'apk','minimum_version_code':1}))
    policy.chmod(0o600)
    archive=tmp_path/'fixture.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('fixture.apk',b'fixture signed package bytes')
    collected={'file':str(archive),'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
    monkeypatch.setattr('devtool.actions_android.verify_download',lambda *a,**k:collected)
    monkeypatch.setattr('devtool.actions_release.verify_download',lambda *a,**k:collected)
    monkeypatch.setattr('devtool.actions_android._inspect_apk',lambda p: {'package':'org.example.fixture',
        'signer_sha256':signer,'version_code':'1','debug_signing':False,'signature_verified':True,'manifest_verified':True})
    monkeypatch.setattr('devtool.actions_release._attestation',lambda *a: {'verified':True,'fixture':True})
    from devtool.actions_release import release_verify
    return t,lambda:release_verify(t.client,REPO,ROOT,run_id=42,artifact_id=12,policy_file=str(policy))


def test_release_verification_real_client_and_collection(verifier):
    t,verify=verifier; out=verify()
    assert out['qualified_release'] and out['deployment_approval']=={'deployment_approved':True,'run_id':42,
        'run_attempt':1,'environment':SIGNING_ENVIRONMENT,'environment_id':ENV_ID,'owner_reviewer_id':OWNER_ID}
    assert t.requests.count('/actions/workflows/devtool-android-build.yml')==2
    assert 'fixture-private-comment' not in json.dumps(out)


@pytest.mark.parametrize('fault', ['no_review','wrong_actor','wrong_env','rejected','duplicate','rerun','owner_changed','unprotected','pagination','wrong_workflow'])
def test_release_verification_refuses_unproven_approval(verifier,fault):
    t,verify=verifier
    if fault=='no_review':t.approvals=[]
    elif fault=='wrong_actor':t.approvals[0]['user']['id']=5
    elif fault=='wrong_env':t.approvals[0]['environments'][0]['id']=5
    elif fault=='rejected':t.approvals[0]['state']='rejected'
    elif fault=='duplicate':t.approvals.append(copy.deepcopy(t.approvals[0]))
    elif fault=='rerun':t.run['run_attempt']=2
    elif fault=='owner_changed':t.repo['owner']['id']=5
    elif fault=='unprotected':t.environment['can_admins_bypass']=True
    elif fault=='pagination':t.review_headers={'Link':'<https://api.github.com/next>; rel="next"'}
    elif fault=='wrong_workflow':t.workflow['path']='.github/workflows/wrong.yml'
    with pytest.raises(GitHubActionsError):verify()
