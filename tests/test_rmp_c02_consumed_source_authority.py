"""RMP-C02: source-bound apply cleanup must not be a general delete primitive."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from devtool.artifact_cleanup_worker import cleanup_apply_source_artifacts, cleanup_source_snapshot
from devtool.cli import _delete_original_artifact_after_apply
from devtool.models import OperationResult


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / 'repo'
    root.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    return root


def _worker(root: Path, target: Path, *, fd: int | None = None, reason: str = 'after successful apply'):
    command = [sys.executable, '-m', 'devtool.artifact_cleanup_worker',
               '--repo-root', str(root), '--reason', reason]
    with tempfile.TemporaryFile(mode='w+b') as unused:
        if fd is not None:
            command += ['--authority-fd', str(fd)]
        command += ['--', str(target)]
        return subprocess.run(command, capture_output=True, text=True,
                              pass_fds=((fd,) if fd is not None else ()), check=False)


def _authority(repo: Path, target: Path, *, reason: str = 'after successful apply') -> bytes:
    return json.dumps({
        'schema_version': 1, 'repository': str(repo.resolve()),
        'reason': reason, 'run_id': '', 'apply_completed': True,
        'sources': [cleanup_source_snapshot(target)],
    }).encode('utf-8')


def test_worker_refuses_unauthorized_direct_invocation(tmp_path: Path):
    repo = _repo(tmp_path)
    victim = tmp_path / 'Download' / 'unapplied.zip'
    victim.parent.mkdir()
    victim.write_bytes(b'no apply')
    proc = _worker(repo, victim)
    assert proc.returncode != 0
    assert 'authority' in proc.stdout
    assert victim.read_bytes() == b'no apply'


def test_consumed_source_symlink_never_deletes_its_target(tmp_path: Path):
    repo = _repo(tmp_path)
    victim = tmp_path / 'private' / 'victim.zip'
    victim.parent.mkdir()
    victim.write_bytes(b'important')
    link = tmp_path / 'Download' / 'overlay.zip'
    link.parent.mkdir()
    link.symlink_to(victim)
    # Even the privileged in-process helper may not dereference the last link.
    payload = cleanup_apply_source_artifacts(repo, [link], reason='after successful apply')
    assert payload['returncode'] != 0
    assert victim.read_bytes() == b'important'
    assert link.is_symlink()


def test_consumed_artifact_cleanup_does_not_delete_unconsumed_siblings(tmp_path: Path):
    repo = _repo(tmp_path)
    folder = tmp_path / 'Download'
    folder.mkdir()
    consumed = folder / 'overlay.zip'
    other = folder / 'overlay(1).zip'
    sidecar = folder / 'overlay.zip.manifest.json'
    for file in (consumed, other, sidecar):
        file.write_text(file.name)
    payload = cleanup_apply_source_artifacts(repo, [consumed], reason='after successful apply')
    assert payload['returncode'] == 0
    assert not consumed.exists()
    assert other.read_text() == other.name
    assert sidecar.read_text() == sidecar.name


def test_worker_rejects_replaced_file_before_unlink(tmp_path: Path):
    repo = _repo(tmp_path)
    victim = tmp_path / 'Download' / 'swap.zip'
    victim.parent.mkdir()
    victim.write_bytes(b'old archive')
    authorization = _authority(repo, victim)
    victim.unlink()
    victim.write_bytes(b'new archive')
    with tempfile.TemporaryFile(mode='w+b') as auth:
        auth.write(authorization)
        auth.seek(0)
        proc = _worker(repo, victim, fd=auth.fileno())
    assert proc.returncode != 0
    assert 'identity changed' in proc.stdout
    assert victim.read_bytes() == b'new archive'


def test_worker_rejects_unrelated_file_in_authority(tmp_path: Path):
    repo = _repo(tmp_path)
    folder = tmp_path / 'Download'
    folder.mkdir()
    authorized = folder / 'apply.zip'
    requested = folder / 'private.zip'
    authorized.write_bytes(b'overlay')
    requested.write_bytes(b'private')
    with tempfile.TemporaryFile(mode='w+b') as auth:
        auth.write(_authority(repo, authorized))
        auth.seek(0)
        proc = _worker(repo, requested, fd=auth.fileno())
    assert proc.returncode != 0
    assert requested.read_bytes() == b'private'


def test_regular_apply_result_cleanup_preserves_life_receipt(tmp_path: Path):
    repo = _repo(tmp_path)
    artifact = tmp_path / 'Download' / 'apply.zip'
    artifact.parent.mkdir()
    artifact.write_bytes(b'consumed')
    applied = OperationResult('apply-overlay', ['apply-overlay', str(artifact)], 0, .01, 'applied', [],
                              metadata={'artifact_path': str(artifact), 'delete_artifact_on_success': True})
    result = _delete_original_artifact_after_apply([applied], repo_root=repo)
    assert result is not None
    assert result.returncode == 0, result.output
    assert not artifact.exists()
    assert result.metadata['cleanup_authority'] == 'LIFE'
    assert Path(result.metadata['cleanup_receipt_path']).is_file()


def test_no_apply_result_cannot_authorize_source_deletion(tmp_path: Path):
    repo = _repo(tmp_path)
    victim = tmp_path / 'Download' / 'not-applied.zip'
    victim.parent.mkdir()
    victim.write_bytes(b'keep')
    arbitrary = OperationResult('build', ['build'], 0, .01, 'success', [],
                                metadata={'artifact_path': str(victim), 'delete_artifact_on_success': True})
    result = _delete_original_artifact_after_apply([arbitrary], repo_root=repo)
    assert result is not None
    assert result.returncode != 0
    assert result.failure_kind == 'artifact_cleanup_authority_missing'
    assert victim.read_bytes() == b'keep'
