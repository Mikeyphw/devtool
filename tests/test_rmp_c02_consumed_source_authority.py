"""RMP-C02: source-bound apply cleanup must not be a general delete primitive."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from devtool.artifact_cleanup_worker import cleanup_apply_source_artifacts, cleanup_source_snapshot
from devtool.cli import _delete_original_artifact_after_apply
from devtool.models import OperationResult
from devtool.artifact_lifecycle import ArtifactExecutionRecord
from devtool.core_runtime import private_run_root
from devtool.run_store import AttachmentDescriptor, RunCatalog


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


def _record(repo: Path, source: Path, *, status: str = 'succeeded') -> tuple[str, str]:
    run_id = uuid.uuid4().hex
    record = ArtifactExecutionRecord.create(
        artifact_id='artifact:test', plan_id='plan:test',
        run_id=run_id, qualification_receipt_id='qualification:test',
        execution_status=status,
    )
    run_root = private_run_root(repo)
    record_path = run_root / run_id / 'artifacts' / (record.record_id.replace(':', '-') + '.json')
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record.as_dict(), sort_keys=True))
    catalog = RunCatalog.open(run_root)
    catalog.add_run({
        'run_id': run_id, 'artifact_id': record.artifact_id,
        'artifact_plan_id': record.plan_id,
        'artifact_qualification_receipt_id': record.qualification_receipt_id,
        'status': status,
    })
    catalog.add_attachment(AttachmentDescriptor.from_file(
        run_id=run_id, path=record_path,
        kind='artifact-execution-record', media_type='application/json',
    ))
    evidence_path = record_path.with_name(record_path.stem + '-sources.json')
    evidence_path.write_text(json.dumps({
        'schema_version': 1, 'repository': str(repo.resolve()),
        'run_id': run_id, 'artifact_execution_record_id': record.record_id,
        'sources': [cleanup_source_snapshot(source)],
    }))
    catalog.add_attachment(AttachmentDescriptor.from_file(
        run_id=run_id, path=evidence_path,
        kind='artifact-apply-source-provenance', media_type='application/json',
    ))
    catalog.save()
    return run_id, record.record_id


def _authority(repo: Path, target: Path, *, reason: str = 'after successful apply') -> bytes:
    run_id, record_id = _record(repo, target)
    return json.dumps({
        'schema_version': 1, 'repository': str(repo.resolve()),
        'reason': reason, 'run_id': '', 'apply_completed': True,
        'sources': [cleanup_source_snapshot(target)],
        'execution_records': [{'run_id': run_id, 'record_id': record_id,
                               'sources': [str(target)]}],
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
    # R1R28 reports redacted, structured identity drift rather than echoing
    # untrusted filesystem exception text into the worker's public output.
    result = json.loads(proc.stdout)
    assert result['failure_kind'] == 'artifact_cleanup_failure'
    metadata = result['metadata']
    assert metadata['cleanup_policy_relaxed'] is False
    assert metadata['cleanup_status'] == 'failed'
    diagnostics = metadata['source_failure_diagnostics']
    assert len(diagnostics) == 1
    assert diagnostics[0]['category'] == 'identity-drift'
    assert diagnostics[0]['safe_to_retry_unlink'] is False
    assert diagnostics[0]['source_deleted'] is False
    assert diagnostics[0]['mismatched_identity_fields']
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
    run_id, record_id = _record(repo, artifact)
    applied = OperationResult('apply-overlay', ['apply-overlay', str(artifact)], 0, .01, 'applied', [],
                              metadata={'artifact_path': str(artifact), 'delete_artifact_on_success': True,
                                        'run_id': run_id, 'artifact_execution_record_id': record_id})
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


def test_unrelated_operation_cannot_supply_cleanup_path_for_real_apply(tmp_path: Path):
    repo = _repo(tmp_path)
    directory = tmp_path / 'Download'
    directory.mkdir()
    consumed = directory / 'consumed.zip'
    victim = directory / 'unrelated.zip'
    consumed.write_text('consumed')
    victim.write_text('private')
    run_id, record_id = _record(repo, consumed)
    applied = OperationResult('apply-overlay', ['apply-overlay', str(consumed)], 0, .01, 'applied', [],
                              metadata={'artifact_path': str(consumed), 'delete_artifact_on_success': True,
                                        'run_id': run_id, 'artifact_execution_record_id': record_id})
    unrelated = OperationResult('validate', ['validate'], 0, .01, 'ok', [],
                                metadata={'artifact_path': str(victim), 'delete_artifact_on_success': True})
    outcome = _delete_original_artifact_after_apply([applied, unrelated], repo_root=repo)
    assert outcome is not None and outcome.returncode != 0
    assert outcome.failure_kind == 'artifact_cleanup_authority_missing'
    assert consumed.exists() and victim.read_text() == 'private'


def test_worker_refuses_missing_durable_receipt(tmp_path: Path):
    repo = _repo(tmp_path)
    target = tmp_path / 'Download' / 'victim.zip'
    target.parent.mkdir()
    target.write_text('keep')
    payload = json.loads(_authority(repo, target))
    payload.pop('execution_records')
    with tempfile.TemporaryFile(mode='w+b') as authority:
        authority.write(json.dumps(payload).encode())
        authority.seek(0)
        outcome = _worker(repo, target, fd=authority.fileno())
    assert outcome.returncode != 0
    assert 'durable execution provenance' in outcome.stdout
    assert target.read_text() == 'keep'


def test_worker_refuses_tampered_durable_receipt(tmp_path: Path):
    repo = _repo(tmp_path)
    target = tmp_path / 'Download' / 'victim.zip'
    target.parent.mkdir()
    target.write_text('keep')
    payload = json.loads(_authority(repo, target))
    execution = payload['execution_records'][0]
    path = (private_run_root(repo) / execution['run_id'] / 'artifacts'
            / (execution['record_id'].replace(':', '-') + '.json'))
    data = json.loads(path.read_text())
    data['execution_status'] = 'failed'
    path.write_text(json.dumps(data))
    with tempfile.TemporaryFile(mode='w+b') as authority:
        authority.write(json.dumps(payload).encode())
        authority.seek(0)
        outcome = _worker(repo, target, fd=authority.fileno())
    assert outcome.returncode != 0
    assert target.read_text() == 'keep'


def test_symlink_artifact_source_is_rejected_at_resolution(tmp_path: Path):
    from devtool.artifacts import resolve_artifact_path
    archive = tmp_path / 'real.zip'
    archive.write_bytes(b'archive')
    link = tmp_path / 'linked.zip'
    link.symlink_to(archive)
    import pytest
    with pytest.raises(ValueError, match='symlinks'):
        resolve_artifact_path(link)
    assert archive.exists() and link.is_symlink()


def test_worker_refuses_durable_record_for_another_source(tmp_path: Path):
    repo = _repo(tmp_path)
    directory = tmp_path / 'Download'
    directory.mkdir()
    applied = directory / 'applied.zip'
    victim = directory / 'private.zip'
    applied.write_text('applied')
    victim.write_text('secret')
    payload = json.loads(_authority(repo, applied))
    payload['sources'] = [cleanup_source_snapshot(victim)]
    payload['execution_records'][0]['sources'] = [str(victim)]
    with tempfile.TemporaryFile(mode='w+b') as authority:
        authority.write(json.dumps(payload).encode())
        authority.seek(0)
        outcome = _worker(repo, victim, fd=authority.fileno())
    assert outcome.returncode != 0
    assert 'durable consumed-source inventory' in outcome.stdout
    assert victim.read_text() == 'secret' and applied.read_text() == 'applied'


def test_worker_refuses_missing_persisted_source_attachment(tmp_path: Path):
    repo = _repo(tmp_path)
    target = tmp_path / 'Download' / 'consumed.zip'
    target.parent.mkdir()
    target.write_text('keep')
    payload = json.loads(_authority(repo, target))
    item = payload['execution_records'][0]
    store = private_run_root(repo)
    catalog = RunCatalog.open(store, create=False)
    for attachment_id, attachment in list(catalog.attachments.items()):
        if attachment.kind == 'artifact-apply-source-provenance':
            del catalog.attachments[attachment_id]
    catalog.save()
    with tempfile.TemporaryFile(mode='w+b') as authority:
        authority.write(json.dumps(payload).encode())
        authority.seek(0)
        outcome = _worker(repo, target, fd=authority.fileno())
    assert outcome.returncode != 0
    assert 'attachment integrity' in outcome.stdout
    assert target.read_text() == 'keep'
