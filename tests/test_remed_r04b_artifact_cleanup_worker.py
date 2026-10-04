from __future__ import annotations

import json
import subprocess
from pathlib import Path

from devtool.artifact_cleanup_worker import cleanup_apply_source_artifacts
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


def test_worker_deletes_external_consumed_apply_source_despite_active_session(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_running_schema6_session(repo)
    artifact = tmp_path / "Download" / "bootstrap-a.zip"
    artifact.parent.mkdir()
    artifact.write_bytes(b"overlay")

    result = cleanup_apply_source_artifacts(
        repo,
        [artifact],
        reason="after successful apply",
        run_id="worker-a",
    )

    assert result["returncode"] == 0, result["output"]
    assert result["metadata"]["cleanup_worker"] == "fresh-process"
    assert not artifact.exists()


def test_worker_does_not_bypass_repository_active_session_for_internal_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_running_schema6_session(repo)
    artifact = repo / ".devtool" / "tmp" / "internal-overlay.zip"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"overlay")

    result = cleanup_apply_source_artifacts(
        repo,
        [artifact],
        reason="after successful apply",
        run_id="worker-a-internal",
    )

    assert result["returncode"] == 1
    assert artifact.exists()
    assert "active validation session" in result["output"]
