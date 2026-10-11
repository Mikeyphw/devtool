"""ACTIONS-08 negative gates: release trust is independent of CI dispatch."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import zipfile

import pytest
from devtool.cli import build_parser
from devtool.github_actions import GitHubActionsError
from devtool.actions_release import release_policy, release_verify, _attestation

SHA = 'a' * 40
SIGNER = 'b' * 64
PKG = 'org.example.release'


def policy(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    name = tmp_path / 'private-policy.json'
    doc = {'schema': 1, 'repository': 'Owner/repo',
           'workflow': '.github/workflows/devtool-android-build.yml',
           'ref': 'refs/heads/main', 'source_sha': SHA,
           'application_id': PKG, 'signer_sha256': SIGNER,
           'format': 'apk', 'minimum_version_code': 7}
    name.write_text(json.dumps(doc))
    name.chmod(0o600)
    return root, name, doc


def test_release_parser_and_canonical_registry():
    args = build_parser().parse_args('actions release verify --run-id 12 --artifact-id 34 --policy /tmp/trust.json --json'.split())
    assert args.release_command == 'verify' and args.policy == '/tmp/trust.json'
    from devtool.core_registry import build_core_registry
    assert build_core_registry().resolve_command(['actions', 'release', 'verify'])['command'].stable_id == 'cli.command.actions.release.verify'


def test_policy_is_independent_owned_private_and_strict(tmp_path):
    root, file, spec = policy(tmp_path)
    result = release_policy(root, str(file))
    assert result['_policy_sha256'] == hashlib.sha256(file.read_bytes()).hexdigest()
    file.chmod(0o644)
    with pytest.raises(GitHubActionsError, match='private'):
        release_policy(root, str(file))
    file.chmod(0o600)
    (root / 'policy.json').write_bytes(file.read_bytes())
    (root / 'policy.json').chmod(0o600)
    with pytest.raises(GitHubActionsError, match='inside'):
        release_policy(root, str(root / 'policy.json'))
    (tmp_path / 'link.json').symlink_to(file)
    with pytest.raises(GitHubActionsError, match='non-symlink'):
        release_policy(root, str(tmp_path / 'link.json'))
    for field, value in [('schema', True), ('source_sha', 'f'), ('signer_sha256', 'bad'),
                         ('workflow', '.github/workflows/other.yml'), ('minimum_version_code', 0),
                         ('ref', 'refs/heads/main/../bad'), ('application_id', 'bad')]:
        bad = dict(spec, **{field: value})
        file.write_text(json.dumps(bad))
        with pytest.raises(GitHubActionsError):
            release_policy(root, str(file))
    file.write_text(json.dumps(dict(spec, signing_password='secret')))
    with pytest.raises(GitHubActionsError, match='fields'):
        release_policy(root, str(file))


class Client:
    def __init__(self, sha=SHA, branch='main', status='completed', conclusion='success', workflow=77):
        self.sha, self.branch, self.status, self.conclusion, self.workflow_id = sha, branch, status, conclusion, workflow
    def get_run(self, *args):
        return {'id': 42, 'status': self.status, 'conclusion': self.conclusion,
                'head_sha': self.sha, 'head_branch': self.branch, 'workflow_id': self.workflow_id,
                'event': 'workflow_dispatch', 'run_attempt': 1}
    def workflow(self, *args):
        return {'id': 77}


def archive_file(tmp_path):
    p = tmp_path / 'artifact.zip'
    with zipfile.ZipFile(p, 'w') as archive:
        archive.writestr('release.apk', b'exact-signed-package')
    return p


def patched(monkeypatch, archive, *, signer=SIGNER, debug=False, code='8'):
    def collect(*args, **kwargs):
        assert kwargs['materialize'] is False and kwargs['variant'] == 'release'
        assert kwargs['expected_cert'] == SIGNER and kwargs['expected_package'] == PKG
        return {'android': {'member': 'release.apk', 'sha256': hashlib.sha256(b'exact-signed-package').hexdigest(),
                            'size': len(b'exact-signed-package'), 'signer_sha256': signer, 'package': PKG,
                            'version_code': code, 'debug_signing': debug}}
    monkeypatch.setattr('devtool.actions_release.android_collect', collect)
    monkeypatch.setattr('devtool.actions_release.verify_download', lambda *a, **kw: {
        'file': str(archive), 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()})


def test_release_rejects_remote_mismatch_before_package(tmp_path):
    root, name, _ = policy(tmp_path)
    for client, pattern in [(Client(status='in_progress', conclusion=None), 'completed'),
                            (Client(sha='f'*40), 'source SHA'),
                            (Client(branch='feature'), 'branch'),
                            (Client(workflow=78), 'workflow identity')]:
        with pytest.raises(GitHubActionsError, match=pattern):
            release_verify(client, 'Owner/repo', root, run_id=42, artifact_id=11, policy_file=str(name))
    with pytest.raises(GitHubActionsError, match='repository'):
        release_verify(Client(), 'Other/repo', root, run_id=42, artifact_id=11, policy_file=str(name))


def test_release_qualification_requires_non_debug_signature_and_version(tmp_path, monkeypatch):
    root, name, _ = policy(tmp_path)
    archive = archive_file(tmp_path)
    for debug, code, fail in [(True, '8', 'Android Debug'), (False, '5', 'minimum'), (False, None, 'versionCode')]:
        patched(monkeypatch, archive, debug=debug, code=code)
        with pytest.raises(GitHubActionsError, match=fail):
            release_verify(Client(), 'Owner/repo', root, run_id=42, artifact_id=11, policy_file=str(name))


def test_gh_attestation_requires_actual_verified_result(tmp_path, monkeypatch):
    root, name, _ = policy(tmp_path)
    spec = release_policy(root, str(name))
    artifact = tmp_path / 'release.apk'
    artifact.write_bytes(b'package')
    monkeypatch.setattr('devtool.actions_release.shutil.which', lambda name: '/usr/bin/gh')
    seen = []
    def fake(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, '[]', '')
    monkeypatch.setattr('devtool.actions_release.subprocess.run', fake)
    with pytest.raises(GitHubActionsError, match='no signed'):
        _attestation(artifact, spec)
    assert '--source-digest' in seen[0] and SHA in seen[0]
    assert '--signer-workflow' in seen[0] and '--source-ref' in seen[0]
    assert '--deny-self-hosted-runners' in seen[0]
    monkeypatch.setattr('devtool.actions_release.subprocess.run',
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, '', 'secret'))
    with pytest.raises(GitHubActionsError, match='could not be verified'):
        _attestation(artifact, spec)


def test_release_success_requires_attestation_and_does_not_publish(tmp_path, monkeypatch):
    root, name, _ = policy(tmp_path)
    archive = archive_file(tmp_path)
    patched(monkeypatch, archive)
    trace = []
    def sign(path, policy):
        trace.append(path.read_bytes())
        return {'verified': True, 'signer_workflow': 'Owner/repo/.github/workflows/devtool-android-build.yml'}
    monkeypatch.setattr('devtool.actions_release._attestation', sign)
    verified = release_verify(Client(), 'Owner/repo', root, run_id=42, artifact_id=11, policy_file=str(name))
    assert verified['qualified_release'] and verified['read_only']
    assert trace == [b'exact-signed-package']
    assert not (root/'.devtool').exists()
    assert verified['scope'].startswith('verified-package')


def test_release_fails_when_archive_member_changed(tmp_path, monkeypatch):
    root, name, _ = policy(tmp_path)
    archive = archive_file(tmp_path)
    patched(monkeypatch, archive)
    monkeypatch.setattr('devtool.actions_release.android_collect', lambda *a, **kw: {
        'android': {'member': 'release.apk', 'sha256':'c'*64, 'size':len(b'exact-signed-package'),
                    'version_code':'10', 'debug_signing':False}})
    monkeypatch.setattr('devtool.actions_release._attestation', lambda *a: pytest.fail('must never check tampered bytes'))
    with pytest.raises(GitHubActionsError, match='bytes changed'):
        release_verify(Client(), 'Owner/repo', root, run_id=42, artifact_id=11, policy_file=str(name))


def test_workflow_requires_real_signing_and_attests_only_releases():
    from pathlib import Path
    live = Path('.github/workflows/devtool-android-build.yml').read_text()
    example = Path('docs/templates/devtool-android-build.yml').read_text()
    assert live == example
    assert 'GITHUB_REF_PROTECTED' in live
    assert 'DEVTOOL_RELEASE_KEYSTORE_B64' in live
    assert 'apksigner" sign' in live and 'jarsigner -verify -strict' in live
    assert 'actions/attest@v4' in live and "inputs.variant == 'release'" in live
    assert 'artifact-metadata: write' in live
    assert 'path: ${{ steps.package.outputs.package }}' in live
    assert '**/build/outputs/apk' not in live
