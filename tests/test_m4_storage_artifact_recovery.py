"""DTB-014/015: genuine conflicts still fail; exact interrupted downloads recover.

Runs against the current source and native artifact-registry implementation.
No network, system credentials or mutations of external repositories.
"""
from __future__ import annotations
import fcntl
import hashlib
import io
import os
from pathlib import Path
import subprocess
import zipfile
from unittest.mock import patch

import pytest

from devtool import storage_layout as sl
from devtool.actions_artifacts import download_artifact, verify_download, _publish_verified_archive_no_clobber
from devtool.artifact_registry import load_registry
from devtool.github_actions import GitHubActionsError

SLUG = 'Mikeyphw/devtool'
RID = 44751861034
AID = 14635931855
HEAD = '0c3777ddf3b50b81531feaa0e0eb50c36e09b8d1'


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    monkeypatch.delenv('DEVTOOL_XDG_DIR', raising=False)
    directory = tmp_path / 'target'
    directory.mkdir()
    subprocess.run(['git', 'init', '-q', str(directory)], check=True)
    return directory


def legacy(root):
    folder = root / '.devtool/artifacts/github-downloads'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'sample.zip').write_bytes(b'unqualified-data')
    return folder


def lock_only(root):
    location = sl.external_family_dir(root, 'artifact-registry')
    sl.ensure_private_storage_dir(location)
    lock = location / 'registry.lock'
    lock.touch(mode=0o600)
    lock.chmod(0o600)
    return location, lock


def test_lock_only_is_not_split_brain_but_is_migration_candidate(root):
    legacy(root)
    location, lock = lock_only(root)
    assert sl.artifact_registry_file(root).parent == root / '.devtool/artifacts'
    inv = sl.storage_inventory(root)
    row = next(v for v in inv['families'] if v['family'] == 'artifact-registry')
    assert row['status'] == 'legacy'
    assert row['needs_migration']
    assert sl.migration_plan(root)['ready']
    assert lock.is_file() and location.is_dir()


def test_native_migration_preserves_original_and_lock_evidence(root):
    legacy(root)
    location, lock = lock_only(root)
    old = root / '.devtool/artifacts'
    original = sl._fingerprint_dir(old)
    before_lock = sl._fingerprint_dir(location)
    assert before_lock[1:] == (1, 0)
    with patch('devtool.transactions.find_active_transactions', return_value=[]), \
         patch('devtool.validation_sessions.load_active_session', return_value=None):
        evidence = sl.migrate_storage(root, confirmed=True)
    assert evidence['originals_preserved'] is True
    assert sl._fingerprint_dir(old) == original
    assert sl._fingerprint_dir(location) == original
    assert sl.artifact_registry_file(root) == location / 'registry.json'
    preserved = list((sl._marker(root, 'artifact-registry').parent).glob('artifact-registry-lock-only-*'))
    assert len(preserved) == 1
    assert sl._fingerprint_dir(preserved[0]) == before_lock
    assert any(row['family'] == 'artifact-registry' for row in evidence['migrated'])


def test_nonempty_registry_or_unexpected_file_still_refused(root):
    legacy(root)
    location, lock = lock_only(root)
    lock.write_bytes(b'held-identity')
    with pytest.raises(sl.StorageLayoutError, match='both contain data'):
        sl.artifact_registry_file(root)
    lock.write_bytes(b'')
    (location / 'registry.json').write_text('{"items": []}')
    with pytest.raises(sl.StorageLayoutError, match='both contain data'):
        sl.artifact_registry_file(root)


def test_symlink_or_held_lock_fails_closed(root):
    legacy(root)
    location, lock = lock_only(root)
    with lock.open('rb') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with patch('devtool.transactions.find_active_transactions', return_value=[]), \
             patch('devtool.validation_sessions.load_active_session', return_value=None):
            with pytest.raises(sl.StorageLayoutError, match='Active artifact registry lock'):
                sl.migrate_storage(root, confirmed=True)
    assert lock.is_file()
    assert (root / '.devtool/artifacts/github-downloads/sample.zip').is_file()
    lock.unlink()
    lock.symlink_to(root / '.devtool/artifacts/github-downloads/sample.zip')
    with pytest.raises(sl.StorageLayoutError, match='both contain data'):
        sl.artifact_registry_file(root)


def payload():
    out = io.BytesIO()
    item = zipfile.ZipInfo('summary.json', date_time=(2020, 1, 1, 0, 0, 0))
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr(item, b'{"schema_version":1,"status":"PASS"}')
    return out.getvalue()


class FakeGitHub:
    def __init__(self, blob, *, wrong_sha=False, expired=False):
        self.blob = blob
        self.wrong_sha = wrong_sha
        self.expired = expired
        self.download_attempted = False

    def get_run(self, slug, run_id):
        assert slug == SLUG and run_id == RID
        return {'id': RID, 'repository': {'full_name': SLUG},
                'status': 'completed', 'conclusion': 'success',
                'head_sha': HEAD, 'head_branch': 'feature/ci24',
                'run_attempt': 1, 'workflow_id': 99}

    def get_artifact(self, slug, artifact_id):
        assert slug == SLUG and artifact_id == AID
        return {'id': AID, 'name': 'devtool-full-pytest-summary',
                'workflow_run': {'id': RID, 'head_sha': HEAD},
                'size_in_bytes': len(self.blob),
                'digest': 'sha256:' + (('0' * 64) if self.wrong_sha else hashlib.sha256(self.blob).hexdigest()),
                'expired': self.expired}

    def artifact_download_location(self, slug, artifact_id):
        self.download_attempted = True
        raise AssertionError('Must reuse an exact existing ZIP without a network download')


def expected_archive(root, blob):
    folder = root / '.devtool/artifacts/github-downloads'
    folder.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(blob).hexdigest()
    path = folder / f'github-{SLUG.replace("/", "_")}-run{RID}-artifact{AID}-{digest[:16]}.zip'
    path.write_bytes(blob)
    path.chmod(0o600)
    return path


def test_adopt_exact_existing_zip_without_download_and_verify_again(root):
    blob = payload()
    path = expected_archive(root, blob)
    client = FakeGitHub(blob)
    result = download_artifact(client, SLUG, RID, AID, root)
    assert result['adopted_existing'] is True
    assert result['download_verified'] and not result['qualified_release']
    assert not client.download_attempted
    assert path.read_bytes() == blob
    registry = load_registry(root)['items']
    assert len(registry) == 1
    assert registry[0]['metadata']['remote_run_attempt'] == 1
    assert registry[0]['metadata']['remote_head_sha'] == HEAD
    verified = verify_download(client, SLUG, RID, AID, root)
    assert verified['archive_verified']
    with pytest.raises(GitHubActionsError, match='already downloaded'):
        download_artifact(client, SLUG, RID, AID, root)
    assert len(load_registry(root)['items']) == 1  # no duplicate event


def test_existing_zip_wrong_digest_preserved_without_registry_event(root):
    blob = payload()
    path = expected_archive(root, blob)
    path.write_bytes(b'tampered')
    client = FakeGitHub(blob)
    with pytest.raises(GitHubActionsError, match='SHA256 conflicts'):
        download_artifact(client, SLUG, RID, AID, root)
    assert path.read_bytes() == b'tampered'
    assert not load_registry(root)['items']
    assert not client.download_attempted


def test_existing_zip_symlink_and_expired_identity_refused(root, tmp_path):
    blob = payload()
    path = expected_archive(root, blob)
    path.unlink()
    path.symlink_to(tmp_path / 'external.zip')
    with pytest.raises(GitHubActionsError, match='symlink'):
        download_artifact(FakeGitHub(blob), SLUG, RID, AID, root)
    assert path.is_symlink()
    path.unlink()
    path.write_bytes(blob)
    with pytest.raises(GitHubActionsError, match='expired'):
        download_artifact(FakeGitHub(blob, expired=True), SLUG, RID, AID, root)
    assert not load_registry(root)['items']


def test_existing_zip_public_permissions_refused_without_mutation(root):
    blob = payload()
    path = expected_archive(root, blob)
    path.chmod(0o644)
    client = FakeGitHub(blob)
    with pytest.raises(GitHubActionsError, match='permissions or link'):
        download_artifact(client, SLUG, RID, AID, root)
    assert not client.download_attempted
    assert path.read_bytes() == blob
    assert not load_registry(root)['items']


def test_portable_publish_without_hardlink_and_exact_sha256(tmp_path, monkeypatch):
    """Real Termux Python 3.14 may not implement os.link at all."""
    content = payload()
    source = tmp_path / "private-stage.zip"
    target = tmp_path / "archive.zip"
    source.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()
    monkeypatch.delattr(os, "link", raising=False)
    _publish_verified_archive_no_clobber(source, target, expected)
    assert target.read_bytes() == content
    assert target.stat().st_nlink == 1
    assert target.stat().st_mode & 0o077 == 0
    assert source.is_file()  # consumed only by caller's finally, no implicit replacement


def test_portable_publish_never_overwrites_existing_or_symlink(tmp_path):
    source = tmp_path / "private-stage.zip"
    source.write_bytes(payload())
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    target = tmp_path / "archive.zip"
    target.write_bytes(b'other-archive')
    with pytest.raises(GitHubActionsError, match='already downloaded'):
        _publish_verified_archive_no_clobber(source, target, expected)
    assert target.read_bytes() == b'other-archive'
    target.unlink()
    target.symlink_to(source)
    with pytest.raises(GitHubActionsError, match='already downloaded'):
        _publish_verified_archive_no_clobber(source, target, expected)
    assert target.is_symlink()


def test_portable_publish_detects_digest_mismatch_and_preserves_evidence(tmp_path):
    source = tmp_path / 'stage.zip'
    target = tmp_path / 'archive.zip'
    source.write_bytes(payload())
    with pytest.raises(GitHubActionsError, match='integrity or exclusive identity'):
        _publish_verified_archive_no_clobber(source, target, '0' * 64)
    # Prepublication source tampering cannot be silently accepted. The failed
    # immutable target is preserved so future recovery refuses mismatched bytes.
    assert target.read_bytes() == source.read_bytes()
    with pytest.raises(GitHubActionsError, match='already downloaded'):
        _publish_verified_archive_no_clobber(source, target, '0' * 64)
