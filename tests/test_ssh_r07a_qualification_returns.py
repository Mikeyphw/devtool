from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from devtool import ssh_qualification, ssh_returns
from devtool.ssh_tmux_runner import SSHRunnerConfig


def _cfg() -> SSHRunnerConfig:
    return SSHRunnerConfig(host="worker", user="u", profile="termux", port=8022, mode="commands", operation_id="run-07")


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"; repo.mkdir()
    bundle = ssh_returns.create_return_bundle(
        _cfg(), local_root=repo, host_cwd=str(repo), remote_root="/home/u/workspace",
        session="session-07", project_config=SimpleNamespace(runner_options={}),
        spec_metadata={}, source_sync_id="sync-07",
    )
    bundle.status_path.write_text("returncode=0\nstate=complete\n", encoding="utf-8")
    return repo, bundle.manifest_path, bundle.status_path


def test_source_bound_complete_receipt_is_accepted(tmp_path):
    repo, manifest, status = _fixture(tmp_path)
    result = ssh_qualification._verify_new_project_returns(_cfg(), repo, [str(manifest)], ordinal=1)
    assert len(result) == 1
    receipt = result[0]
    assert receipt["state"] == "complete" and receipt["returncode"] == 0
    assert receipt["source_sync_id"] == "sync-07"
    assert len(receipt["manifest_sha256"]) == len(receipt["status_sha256"]) == 64
    assert receipt["producer_sha256"]


@pytest.mark.parametrize("status_text", [
    "returncode=23\nstate=partial\n", "returncode=0\nstate=pending\n",
    "returncode=1\nstate=complete\n", "state=complete\n", "returncode=0\n",
    "returncode=0\nreturncode=1\nstate=complete\n",
])
def test_incomplete_or_ambiguous_status_fails_closed(tmp_path, status_text):
    repo, manifest, status = _fixture(tmp_path)
    status.write_text(status_text, encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete|ambiguous"):
        ssh_qualification._verify_new_project_returns(_cfg(), repo, [str(manifest)], ordinal=1)


def test_missing_status_refused_without_mutating_return_bundle(tmp_path):
    repo, manifest, status = _fixture(tmp_path)
    status.unlink()
    original = manifest.read_bytes()
    with pytest.raises(ValueError, match="no completed return status"):
        ssh_qualification._verify_new_project_returns(_cfg(), repo, [str(manifest)], ordinal=2)
    assert manifest.read_bytes() == original


def test_tampered_producer_identity_is_rejected(tmp_path):
    repo, manifest, status = _fixture(tmp_path)
    data = json.loads(manifest.read_text())
    data["source_sync_id"] = "forged"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="producer|identity|provenance|digest|hash|mismatch"):
        ssh_qualification._verify_new_project_returns(_cfg(), repo, [str(manifest)], ordinal=1)


def test_foreign_profile_and_identity_are_rejected(tmp_path):
    repo, manifest, status = _fixture(tmp_path)
    other = SSHRunnerConfig(host="other", user="u", profile="other", port=8022, mode="commands")
    with pytest.raises(ValueError, match="profile/destination"):
        ssh_qualification._verify_new_project_returns(other, repo, [str(manifest)], ordinal=1)


def test_manifest_symlink_and_status_symlink_refused(tmp_path):
    repo, manifest, status = _fixture(tmp_path)
    alias = tmp_path / "foreign.json"
    alias.symlink_to(manifest)
    with pytest.raises(ValueError, match="unsafe"):
        ssh_qualification._verify_new_project_returns(_cfg(), repo, [str(alias)], ordinal=1)
    status.unlink()
    external = tmp_path / "external.status"
    external.write_text("returncode=0\nstate=complete\n")
    status.symlink_to(external)
    with pytest.raises(ValueError, match="no completed return status"):
        ssh_qualification._verify_new_project_returns(_cfg(), repo, [str(manifest)], ordinal=1)


def test_foreign_layout_rejected_even_with_valid_manifest(tmp_path):
    repo, manifest, status = _fixture(tmp_path)
    other = tmp_path / "other"; other.mkdir()
    copied = other / "manifest.json"; copied.write_bytes(manifest.read_bytes())
    with pytest.raises(ValueError, match="escapes repository"):
        ssh_qualification._verify_new_project_returns(_cfg(), repo, [str(copied)], ordinal=1)


def test_project_validation_command_must_produce_verified_status(tmp_path, monkeypatch):
    repo, manifest, status = _fixture(tmp_path)
    # One new return bundle is created by child validation. The subprocess itself
    # can exit 0 without the remote return having succeeded.
    states = iter([set(), {str(manifest)}])
    monkeypatch.setattr(ssh_qualification, "_return_manifest_snapshot", lambda _: next(states))
    monkeypatch.setattr(ssh_qualification.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a[0], 0))
    completed = ssh_qualification._run_project_validation(_cfg(), repo, ordinal=1)
    assert completed["return_evidence_verified"] == 1
    assert completed["return_evidence"][0]["source_sync_id"] == "sync-07"


def test_project_validation_exits_zero_but_return_fails(tmp_path, monkeypatch):
    repo, manifest, status = _fixture(tmp_path)
    status.write_text("returncode=23\nstate=partial\n")
    states = iter([set(), {str(manifest)}])
    monkeypatch.setattr(ssh_qualification, "_return_manifest_snapshot", lambda _: next(states))
    monkeypatch.setattr(ssh_qualification.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a[0], 0))
    with pytest.raises(ValueError, match="transfer incomplete"):
        ssh_qualification._run_project_validation(_cfg(), repo, ordinal=1)
    assert manifest.exists() and status.exists()  # forensic evidence retained
