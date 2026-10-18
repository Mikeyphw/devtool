"""RMP-C02 phase-3: fail closed on ambiguous cleanup timeout."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from devtool import cli
from devtool.artifact_cleanup_worker import (
    cleanup_apply_source_artifacts, cleanup_source_snapshot,
    verified_external_cleanup_receipt,
)
from devtool.models import OperationResult
from tests._rmp_c02_durable_fixture import attach_durable_apply_evidence


def _case(tmp_path: Path) -> tuple[Path, Path, OperationResult]:
    repo = tmp_path / "repo"
    repo.mkdir()
    download = tmp_path / "Download"
    download.mkdir()
    artifact = download / "overlay.zip"
    artifact.write_bytes(b"test source")
    apply = OperationResult("apply-overlay", ["apply-overlay", str(artifact)], 0, 0.01, "ok", [],
        metadata={"artifact_path": str(artifact), "delete_artifact_on_success": True})
    attach_durable_apply_evidence(repo, apply)
    return repo, artifact, apply


def test_timeout_after_unlink_without_receipt_is_unknown(tmp_path: Path, monkeypatch):
    repo, artifact, applied = _case(tmp_path)
    def interrupted(*args, **kwargs):
        artifact.unlink()
        raise subprocess.TimeoutExpired("worker", 0.01)
    monkeypatch.setattr(cli.subprocess, "run", interrupted)
    result = cli._delete_original_artifact_after_apply([applied], repo_root=repo)
    assert result.returncode == 1
    assert result.failure_kind == "artifact_cleanup_outcome_unknown"
    assert result.metadata["cleanup_source_removed"] is True
    assert result.metadata["cleanup_status"] == "outcome-unknown"


def test_timeout_after_attested_receipt_recovers_without_retry(tmp_path: Path, monkeypatch):
    repo, artifact, applied = _case(tmp_path)
    original_run = subprocess.run
    def finished_then_interrupted(*args, **kwargs):
        # Git identity checks inside the worker also call subprocess.run;
        # emulate an independent worker without recursively intercepting git.
        monkeypatch.setattr(cli.subprocess, "run", original_run)
        try:
            snap = cleanup_source_snapshot(artifact)
            outcome = cleanup_apply_source_artifacts(repo, [artifact],
                reason="after successful apply", authorized_sources={str(artifact): snap})
            assert outcome["returncode"] == 0
        finally:
            monkeypatch.setattr(cli.subprocess, "run", finished_then_interrupted)
        raise subprocess.TimeoutExpired("worker", 0.01)
    monkeypatch.setattr(cli.subprocess, "run", finished_then_interrupted)
    result = cli._delete_original_artifact_after_apply([applied], repo_root=repo)
    assert result.returncode == 0, result.output
    assert result.metadata["cleanup_recovered_from_receipt"] is True
    assert not artifact.exists()


def test_worker_success_without_life_receipt_is_not_accepted(tmp_path: Path, monkeypatch):
    repo, artifact, applied = _case(tmp_path)
    class Pretend:
        returncode = 0
        stderr = ""
        stdout = json.dumps({"returncode": 0, "output": "deleted", "metadata": {}})
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: Pretend())
    result = cli._delete_original_artifact_after_apply([applied], repo_root=repo)
    assert result.returncode != 0
    assert result.failure_kind == "artifact_cleanup_receipt_unverified"
    assert artifact.exists()


def test_old_receipt_cannot_prove_new_file_with_reused_name(tmp_path: Path):
    repo, artifact, applied = _case(tmp_path)
    original = cleanup_source_snapshot(artifact)
    first = cleanup_apply_source_artifacts(repo, [artifact], reason="after successful apply",
        authorized_sources={str(artifact): original})
    assert first["returncode"] == 0
    artifact.write_bytes(b"new source with reused filename")
    replacement = cleanup_source_snapshot(artifact)
    assert replacement != original
    artifact.unlink()
    assert verified_external_cleanup_receipt(repo, [artifact], reason="after successful apply",
        run_id="", source_snapshots={str(artifact): replacement}) is None
