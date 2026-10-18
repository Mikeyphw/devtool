"""RMP-C02 phase 4: abnormal worker exit retains cleanup evidence truth."""
from __future__ import annotations

import subprocess
from pathlib import Path

from devtool import cli
from devtool.artifact_cleanup_worker import (
    cleanup_apply_source_artifacts, cleanup_source_snapshot,
)
from devtool.models import OperationResult
from tests._rmp_c02_durable_fixture import attach_durable_apply_evidence


def _setup(tmp_path: Path) -> tuple[Path, Path, OperationResult]:
    repo = tmp_path / "repo"
    repo.mkdir()
    folder = tmp_path / "Download"
    folder.mkdir()
    artifact = folder / "overlay.zip"
    artifact.write_bytes(b"disposable overlay")
    applied = OperationResult("apply-overlay", ["apply-overlay", str(artifact)], 0, 0.01,
        "applied", [], metadata={"artifact_path": str(artifact), "delete_artifact_on_success": True})
    attach_durable_apply_evidence(repo, applied)
    return repo, artifact, applied


class _DeadWorker:
    returncode = -9
    stdout = ""
    stderr = "killed"


class _BadJson:
    returncode = 0
    stdout = "<broken json"
    stderr = ""


def test_abnormal_exit_after_unlink_without_receipt_is_unknown(tmp_path: Path, monkeypatch):
    repo, artifact, applied = _setup(tmp_path)
    def dead(*args, **kwargs):
        artifact.unlink()
        return _DeadWorker()
    monkeypatch.setattr(cli.subprocess, "run", dead)
    result = cli._delete_original_artifact_after_apply([applied], repo_root=repo)
    assert result.returncode == 1
    assert result.failure_kind == "artifact_cleanup_outcome_unknown"
    assert result.metadata["cleanup_source_removed"] is True
    assert result.metadata["cleanup_worker_return_unavailable"] is True
    assert not artifact.exists()


def test_abnormal_exit_with_verified_receipt_reconciles_read_only(tmp_path: Path, monkeypatch):
    repo, artifact, applied = _setup(tmp_path)
    original_run = subprocess.run
    calls = []
    def dead(*args, **kwargs):
        if isinstance(args[0], list) and "devtool.artifact_cleanup_worker" in args[0]:
            calls.append(1)
        monkeypatch.setattr(cli.subprocess, "run", original_run)
        try:
            expected = cleanup_source_snapshot(artifact)
            outcome = cleanup_apply_source_artifacts(repo, [artifact],
                reason="after successful apply", authorized_sources={str(artifact): expected})
            assert outcome["returncode"] == 0
        finally:
            monkeypatch.setattr(cli.subprocess, "run", dead)
        return _DeadWorker()
    monkeypatch.setattr(cli.subprocess, "run", dead)
    result = cli._delete_original_artifact_after_apply([applied], repo_root=repo)
    assert result.returncode == 0, result.output
    assert result.metadata["cleanup_recovered_from_receipt"] is True
    assert len(calls) == 1
    assert not artifact.exists()


def test_malformed_success_json_cannot_claim_cleanup(tmp_path: Path, monkeypatch):
    repo, artifact, applied = _setup(tmp_path)
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: _BadJson())
    result = cli._delete_original_artifact_after_apply([applied], repo_root=repo)
    assert result.returncode != 0
    assert result.failure_kind == "artifact_cleanup_outcome_unknown"
    assert artifact.read_bytes() == b"disposable overlay"


def test_nonzero_worker_without_structured_response_preserves_file(tmp_path: Path, monkeypatch):
    repo, artifact, applied = _setup(tmp_path)
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: _DeadWorker())
    result = cli._delete_original_artifact_after_apply([applied], repo_root=repo)
    assert result.failure_kind == "artifact_cleanup_outcome_unknown"
    assert result.metadata["cleanup_source_removed"] is False
    assert artifact.is_file()


def test_empty_object_is_not_worker_success(tmp_path: Path, monkeypatch):
    repo, artifact, applied = _setup(tmp_path)
    class Empty:
        returncode = 0
        stdout = "{}"
        stderr = ""
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: Empty())
    result = cli._delete_original_artifact_after_apply([applied], repo_root=repo)
    assert result.failure_kind == "artifact_cleanup_outcome_unknown"
    assert artifact.is_file()


def test_invalid_status_type_is_not_worker_success(tmp_path: Path, monkeypatch):
    repo, artifact, applied = _setup(tmp_path)
    class Invalid:
        returncode = 0
        stdout = '{"returncode": false, "output": "deleted", "metadata": {}}'
        stderr = ""
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: Invalid())
    result = cli._delete_original_artifact_after_apply([applied], repo_root=repo)
    assert result.failure_kind == "artifact_cleanup_outcome_unknown"
    assert artifact.is_file()
