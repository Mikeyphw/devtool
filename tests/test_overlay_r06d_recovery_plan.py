"""R06D genuine read-only recovery projection and fail-closed negative contracts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from devtool.core_runtime import private_run_root
from devtool.overlay.recovery import overlay_recovery_plan
from devtool.transactions import transaction_root
from devtool.cli import build_parser


def _repo(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "state"))
    return repo


def _marker(repo: Path, identity: str, *, status: str = "rollback-failed", pid: int = -1, entries=None):
    root = transaction_root(repo)
    journal = root / identity / "transaction.json"
    journal.parent.mkdir(parents=True)
    journal.write_text(json.dumps({"schema_version": 3, "repo_root": str(repo),
        "transaction_id": identity, "status": status, "pid": pid,
        "affected_paths": entries if entries is not None else []}), encoding="utf-8")
    marker = root / "active" / f"{identity}.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"path": str(journal), "pid": pid}), encoding="utf-8")
    return journal, marker


def test_no_state_read_only_does_not_create_storage(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    result = overlay_recovery_plan(repo)
    assert result["read_only"] and not result["mutation_performed"]
    assert not result["safe_to_auto_recover"] and not result["safe_to_delete_artifact"]
    assert result["active_transactions"] == []
    assert result["life"]["catalog"] == "absent"
    assert not transaction_root(repo).exists()
    assert not private_run_root(repo).exists()


def test_rollback_failure_without_checkpoints_reported_no_mutation(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    journal, marker = _marker(repo, "tx01", entries=[{"path": "a.txt", "restore_state": "restored", "verified": False}])
    before = {str(p): p.read_bytes() for p in (journal, marker)}
    result = overlay_recovery_plan(repo)
    row = result["active_transactions"][0]
    assert row["status"] == "rollback-failed"
    assert row["classification"] == "blocked"
    assert "missing persisted rollback checkpoint" in row["blockers"]
    assert not row["existing_command"]
    assert before == {str(p): p.read_bytes() for p in (journal, marker)}


def test_corrupt_marker_retained_and_reported(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    root = transaction_root(repo)
    path = root / "active" / "bad.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    result = overlay_recovery_plan(repo)
    assert result["marker_issues"] and result["decision"] == "manual_review"
    assert path.read_text() == "{broken"


def test_dangling_and_foreign_pointer_retained(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    root = transaction_root(repo)
    path = root / "active" / "bad.json"
    path.parent.mkdir(parents=True)
    foreign = tmp_path / "foreign.json"
    foreign.write_text('{}')
    path.write_text(json.dumps({"path": str(foreign)}))
    result = overlay_recovery_plan(repo)
    assert len(result["marker_issues"]) == 1
    assert foreign.read_text() == '{}'
    assert path.exists()


def test_symlink_marker_refused_without_following(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    root = transaction_root(repo)
    marker = root / "active" / "bad.json"
    marker.parent.mkdir(parents=True)
    outside = tmp_path / "external"
    outside.write_text('{}')
    marker.symlink_to(outside)
    assert overlay_recovery_plan(repo)["marker_issues"]
    assert marker.is_symlink() and outside.read_text() == '{}'


def test_live_owner_is_protected(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _marker(repo, "live01", pid=os.getpid())
    row = overlay_recovery_plan(repo)["active_transactions"][0]
    assert row["owner_live"]
    assert row["classification"] == "live_owner"
    assert row["existing_command"] == ""


def test_pending_validation_not_advertised_as_rollback(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _marker(repo, "pending01", status="applied-pending-validation")
    row = overlay_recovery_plan(repo)["active_transactions"][0]
    assert row["classification"] == "blocked"
    assert row["existing_command"] == ""


def test_exact_selector_no_substring_fallback(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _marker(repo, "abcd-123")
    result = overlay_recovery_plan(repo, transaction="abcd")
    assert result["active_transactions"] == []
    assert result["marker_issues"][0]["kind"] == "transaction_not_found"


def test_life_catalog_metadata_no_qualification_or_deletion(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    root = private_run_root(repo)
    root.mkdir(parents=True)
    (root / "catalog.json").write_text(json.dumps({"runs": {
        "bad-cleanup": {"run_id": "bad-cleanup", "status": "failed", "failure_kind": "artifact_cleanup_failure"}},
        "attachments": {}, "streams": {}, "events": {}, "references": {}, "relations": {}}))
    result = overlay_recovery_plan(repo)
    row = result["life"]["failures"][0]
    assert row["run_id"] == "bad-cleanup"
    assert not row["artifact_deletion_authorized"]
    assert row["classification"] == "historical_failure_unresolved"


def test_real_cli_command_dispatch_is_available(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    args = build_parser().parse_args(["overlay", "recovery-plan", "--json", "--limit", "4"])
    assert args.overlay_command == "recovery-plan" and args.limit == 4
    proc = subprocess.run(["python", "-m", "devtool", "--repo", str(repo), "overlay", "recovery-plan", "--json"],
                           capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)["data"]
    assert payload["producer"] == "devtool-overlay-r06d-recovery-plan"
    assert payload["read_only"]
    assert not transaction_root(repo).exists()


def test_missing_original_backup_blocks_even_if_journal_says_ready(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    journal, _ = _marker(repo, "backupless", entries=[{"path": "tracked.txt", "existed": True,
        "restore_state": "pending", "applied_identity": {"exists": False}}])
    content = json.loads(journal.read_text())
    content["backup_dir"] = str(journal.parent / "backup")
    journal.write_text(json.dumps(content))
    row = overlay_recovery_plan(repo)["active_transactions"][0]
    assert "original-file rollback backup is absent" in row["blockers"]
    assert row["existing_command"] == ""


def test_post_apply_path_drift_keeps_retry_blocked(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    tracked = repo / "tracked.txt"
    tracked.write_text("new user edits")
    _marker(repo, "changed", entries=[{"path": "tracked.txt", "existed": False,
           "restore_state": "pending", "applied_identity": {"exists": False}}])
    row = overlay_recovery_plan(repo)["active_transactions"][0]
    assert any("no longer matches" in item for item in row["blockers"])
    assert row["classification"] == "blocked"
