from __future__ import annotations

import json
import subprocess
from pathlib import Path

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


def _write_running_schema6_session(repo: Path, session_id: str = "active-schema6") -> None:
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


def test_post_apply_source_artifact_cleanup_ignores_repository_active_session_blocker(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_running_schema6_session(repo)
    artifact = tmp_path / "Download" / "overlay.zip"
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

    cleanup = _delete_original_artifact_after_apply([result], repo_root=repo)

    assert cleanup is not None
    assert cleanup.returncode == 0, cleanup.output
    assert cleanup.metadata["cleanup_authority"] == "LIFE"
    assert not artifact.exists()
    assert "active validation session" not in cleanup.output


def test_active_session_still_blocks_repository_cleanup_candidate(tmp_path: Path) -> None:
    from devtool.cleanup_lifecycle import (
        CleanupScope,
        GeneratedOutputDescriptor,
        build_cleanup_ownership_view,
        compile_cleanup_plan,
    )

    repo = _repo(tmp_path)
    _write_running_schema6_session(repo)
    generated = repo / ".devtool" / "tmp" / "generated.bin"
    generated.parent.mkdir(parents=True)
    generated.write_bytes(b"generated")
    descriptor = GeneratedOutputDescriptor.create(
        generated,
        owner="test",
        provider="test",
        category="generated-output",
        producer_id="unit",
        lifecycle="transient",
        scope=CleanupScope.PROJECT,
        managed=True,
    )

    ownership = build_cleanup_ownership_view(repo, [descriptor], strict_authorities=True)
    candidate = compile_cleanup_plan(ownership, scope=CleanupScope.PROJECT).candidates[0]

    assert candidate.selected is False
    assert "active validation session" in candidate.protection_reason
