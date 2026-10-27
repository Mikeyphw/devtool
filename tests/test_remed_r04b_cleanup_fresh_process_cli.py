from __future__ import annotations

from tests._rmp_c02_durable_fixture import attach_durable_apply_evidence

import json
import subprocess
from pathlib import Path

import devtool.cleanup_lifecycle as cleanup_lifecycle
from devtool.cli import _delete_original_artifact_after_apply
from devtool.models import OperationResult
from devtool.validation_sessions import active_session_marker, session_path


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "devtool-test@example.invalid")
    _git(repo, "config", "user.name", "Devtool Test")
    (repo / "payload.txt").write_text("payload\n", encoding="utf-8")
    _git(repo, "add", "payload.txt")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def _write_running_schema6_session(repo: Path, session_id: str = "stale-parent-policy") -> None:
    state = session_path(repo, session_id)
    state.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 6,
        "session_id": session_id,
        "repo_root": str(repo),
        "project_root": str(repo),
        "project_type": "python",
        "target": "devtool",
        "variant": "",
        "status": "running",
        "created_at": "2026-10-03T00:00:00Z",
        "updated_at": "2026-10-03T00:00:00Z",
        "fingerprints": {},
        "revision": 1,
        "phases": [],
        "apply_context": {},
        "options": {},
    }
    state.write_text(json.dumps(payload), encoding="utf-8")
    marker = active_session_marker(repo)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"schema_version": 6, "session_id": session_id, "status": "running"}),
        encoding="utf-8",
    )


def test_cli_post_apply_cleanup_uses_fresh_worker_when_parent_policy_is_stale(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_running_schema6_session(repo)
    artifact = tmp_path / "Download" / "self-update.zip"
    artifact.parent.mkdir()
    artifact.write_bytes(b"overlay")
    result = OperationResult(
        "apply-overlay",
        ["apply-overlay", str(artifact)],
        0,
        0.01,
        "applied",
        [],
        metadata={"artifact_path": str(artifact), "delete_artifact_on_success": True},
    )

    attach_durable_apply_evidence(repo, result)
    original = cleanup_lifecycle._cleanup_global_blocker_for_candidate
    cleanup_lifecycle._cleanup_global_blocker_for_candidate = (
        lambda ownership, descriptor, repo_root: ownership.global_blockers[0]
        if ownership.global_blockers
        else ""
    )
    try:
        cleanup = _delete_original_artifact_after_apply([result], repo_root=repo)
    finally:
        cleanup_lifecycle._cleanup_global_blocker_for_candidate = original

    assert cleanup is not None
    assert cleanup.returncode == 0, cleanup.output
    assert cleanup.metadata["cleanup_authority"] == "LIFE"
    assert cleanup.metadata["cleanup_process"] == "fresh-worker"
    assert cleanup.metadata["cleanup_worker"] == "fresh-process"
    assert not artifact.exists()


def test_cli_worker_failure_is_classified_as_artifact_cleanup_failure(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    artifact = tmp_path / "Download" / "failure.zip"
    artifact.parent.mkdir()
    artifact.write_bytes(b"overlay")
    result = OperationResult(
        "apply-overlay",
        ["apply-overlay", str(artifact)],
        0,
        0.01,
        "applied",
        [],
        metadata={"artifact_path": str(artifact), "delete_artifact_on_success": True},
    )

    attach_durable_apply_evidence(repo, result)
    class Proc:
        returncode = 9
        stdout = ""
        stderr = "synthetic worker failure"

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: Proc())
    cleanup = _delete_original_artifact_after_apply([result], repo_root=repo)

    assert cleanup is not None
    assert cleanup.returncode != 0
    assert cleanup.failure_kind == "artifact_cleanup_failure"
    assert cleanup.metadata["cleanup_process"] == "fresh-worker"
    assert "synthetic worker failure" in cleanup.output
    assert artifact.exists()
