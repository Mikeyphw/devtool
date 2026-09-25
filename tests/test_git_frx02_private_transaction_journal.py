from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from zipfile import ZipFile

import pytest

from devtool.git_transaction_journal import (
    GitTransactionRecoveryError,
    verify_git_transaction_recovery,
)
from devtool.transactions import (
    abandon_transaction,
    checkpoint_transaction_applied,
    create_apply_transaction,
    mark_transaction_completed,
    read_transaction,
    read_transaction_summary,
    record_transaction_git_commit,
    rollback_transaction,
    transaction_root,
)


def _git(root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return proc.stdout.strip()


def _repo(tmp_path: Path, *, committed: bool = True) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "devtool@example.invalid")
    _git(root, "config", "user.name", "Devtool Test")
    (root / "tracked.txt").write_text("before\n", encoding="utf-8")
    if committed:
        _git(root, "add", "tracked.txt")
        _git(root, "commit", "-qm", "base")
    return root


def _artifact(tmp_path: Path) -> Path:
    path = tmp_path / "overlay.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("tracked.txt", "after\n")
    return path


def _create(root: Path, artifact: Path):
    return create_apply_transaction(
        root,
        artifact,
        artifact_type="overlay",
        preview={"changes": [{"kind": "modify", "path": "tracked.txt"}], "counts": {"modify": 1}},
    )


def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, run_id: str = "git-frx02-run") -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    monkeypatch.setenv("DEVTOOL_RUN_ID", run_id)
    monkeypatch.setenv("DEVTOOL_TRANSACTION_COMMIT_MODE", "none")


def test_private_refs_and_durable_journal_capture_authoritative_pre_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path)
    start_sha = _git(root, "rev-parse", "HEAD")

    tx = _create(root, artifact)
    journal = tx["git_recovery"]

    assert tx["schema_version"] == 4
    assert journal["schema_version"] == 1
    assert journal["kind"] == "git-transaction-recovery"
    assert journal["exo_run_id"] == "git-frx02-run"
    assert journal["repository_id"]
    assert journal["start"] == {
        "source_ref": "refs/heads/master",
        "sha": start_sha,
        "unborn": False,
        "detached": False,
        "branch": "master",
    }
    assert journal["intended_mutation"] == "apply-overlay"
    assert journal["worktree_path"] == str(root.resolve())
    assert journal["commit_mode"] == "none"
    assert journal["finalization"]["state"] == "pending"
    assert journal["recovery"]["state"] == "ready"

    refs = {item["role"]: item for item in journal["recovery"]["refs"]}
    assert set(refs) == {"run-start", "backup-head"}
    assert refs["run-start"]["ref"] == "refs/devtool/runs/git-frx02-run/start"
    assert refs["backup-head"]["ref"] == "refs/devtool/backups/git-frx02-run/head"
    for item in refs.values():
        assert item["sha"] == start_sha
        assert _git(root, "rev-parse", "--verify", item["ref"]) == start_sha

    persisted = read_transaction(tx["path"])
    assert persisted["git_recovery"] == journal
    assert verify_git_transaction_recovery(root, persisted["git_recovery"])["ok"] is True


def test_campaign_and_federation_identifiers_create_namespaced_start_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path, run_id="child-run")
    monkeypatch.setenv("DEVTOOL_CAMPAIGN_ID", "release train/42")
    monkeypatch.setenv("DEVTOOL_FEDERATION_PARENT_RUN_ID", "federation-parent")
    monkeypatch.setenv("DEVTOOL_FEDERATION_RELATIONSHIP", "parent")
    monkeypatch.setenv("DEVTOOL_FEDERATION_WORKFLOW", "release")
    monkeypatch.setenv("DEVTOOL_FEDERATION_GROUP", "ready")

    tx = _create(root, artifact)
    journal = tx["git_recovery"]

    assert journal["campaign_id"] == "release train/42"
    assert journal["federation_id"] == "federation-parent"
    assert journal["federation_workflow"] == "release"
    assert journal["federation_group"] == "ready"
    roles = {item["role"]: item["ref"] for item in journal["recovery"]["refs"]}
    assert roles["campaign-start"].startswith("refs/devtool/campaigns/release-train-42/")
    assert roles["federation-start"].startswith("refs/devtool/federations/federation-parent/repos/")
    assert verify_git_transaction_recovery(root, journal)["ok"] is True


def test_unborn_repository_records_explicit_recovery_state_without_fake_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path, committed=False)
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path, run_id="unborn-run")

    tx = _create(root, artifact)
    journal = tx["git_recovery"]

    assert journal["repository_id"]
    assert journal["start"]["unborn"] is True
    assert journal["start"]["sha"] == ""
    assert journal["start"]["source_ref"] == "refs/heads/master"
    assert journal["recovery"]["state"] == "ready-unborn"
    assert journal["recovery"]["refs"] == []
    assert verify_git_transaction_recovery(root, journal)["ok"] is True


def test_non_git_apply_journal_is_explicitly_not_applicable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "plain"
    root.mkdir()
    (root / "tracked.txt").write_text("before\n", encoding="utf-8")
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path, run_id="plain-run")

    tx = _create(root, artifact)
    journal = tx["git_recovery"]

    assert journal["repository_id"] == ""
    assert journal["recovery"] == {
        "required": False,
        "state": "not-applicable",
        "refs": [],
        "created_refs": [],
        "reused_refs": [],
    }
    assert verify_git_transaction_recovery(root, journal)["ok"] is True


def test_recovery_setup_failure_is_fail_closed_before_worktree_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path, run_id="fail-closed")

    def fail(*_args, **_kwargs):
        raise GitTransactionRecoveryError("simulated private-ref failure")

    monkeypatch.setattr("devtool.transactions.establish_git_transaction_recovery", fail)
    with pytest.raises(GitTransactionRecoveryError, match="private-ref failure"):
        _create(root, artifact)

    assert (root / "tracked.txt").read_text(encoding="utf-8") == "before\n"
    tx_root = transaction_root(root)
    assert not tx_root.exists() or not any(tx_root.iterdir())


def test_journal_persistence_failure_removes_new_private_refs_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path, run_id="journal-write-failure")
    real_atomic = __import__("devtool.transactions", fromlist=["_atomic_write_json"])._atomic_write_json
    calls = 0

    def fail_first(path: Path, data: dict) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated durable journal failure")
        real_atomic(path, data)

    monkeypatch.setattr("devtool.transactions._atomic_write_json", fail_first)
    with pytest.raises(OSError, match="durable journal failure"):
        _create(root, artifact)

    assert _git(root, "rev-parse", "--verify", "--quiet", "refs/devtool/runs/journal-write-failure/start", check=False) == ""
    assert _git(root, "rev-parse", "--verify", "--quiet", "refs/devtool/backups/journal-write-failure/head", check=False) == ""
    assert (root / "tracked.txt").read_text(encoding="utf-8") == "before\n"
    tx_root = transaction_root(root)
    assert not tx_root.exists() or not any(tx_root.iterdir())


def test_private_ref_collision_fails_closed_without_overwriting_existing_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path, run_id="collision")
    base = _git(root, "rev-parse", "HEAD")
    (root / "other.txt").write_text("other\n", encoding="utf-8")
    _git(root, "add", "other.txt")
    _git(root, "commit", "-qm", "other")
    other = _git(root, "rev-parse", "HEAD")
    _git(root, "update-ref", "refs/devtool/runs/collision/start", other)
    _git(root, "reset", "--hard", "-q", base)

    with pytest.raises(GitTransactionRecoveryError, match="ref collision"):
        _create(root, artifact)

    assert _git(root, "rev-parse", "refs/devtool/runs/collision/start") == other
    assert (root / "tracked.txt").read_text(encoding="utf-8") == "before\n"


def test_applied_checkpoint_and_commit_are_reflected_in_same_authoritative_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path, run_id="lifecycle")
    monkeypatch.setenv("DEVTOOL_TRANSACTION_COMMIT_MODE", "new")
    tx = _create(root, artifact)

    (root / "tracked.txt").write_text("after\n", encoding="utf-8")
    checkpoint_transaction_applied(tx["path"])
    current = read_transaction(tx["path"])
    assert current["git_recovery"]["recovery"]["state"] == "mutation-applied"

    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-qm", "apply")
    commit = _git(root, "rev-parse", "HEAD")
    record_transaction_git_commit(tx["path"], commit)
    current = read_transaction(tx["path"])
    assert current["git_recovery"]["commit_mode"] == "new"
    assert current["git_recovery"]["recovery"]["state"] == "commit-recorded"
    assert current["git_recovery"]["recovery"]["result_commit"] == commit


def test_completed_transaction_archives_finalization_state_and_keeps_recovery_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path, run_id="complete")
    tx = _create(root, artifact)
    start_ref = tx["git_recovery"]["recovery"]["refs"][0]

    mark_transaction_completed(tx["path"])
    archived = read_transaction_summary(root, tx["transaction_id"])

    assert archived["status"] == "completed"
    assert archived["git_recovery"]["recovery"]["state"] == "completed"
    assert archived["git_recovery"]["finalization"]["state"] == "completed"
    assert _git(root, "rev-parse", start_ref["ref"]) == start_ref["sha"]


def test_rollback_archives_rolled_back_git_recovery_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path, run_id="rollback")
    tx = _create(root, artifact)
    (root / "tracked.txt").write_text("after\n", encoding="utf-8")
    checkpoint_transaction_applied(tx["path"])

    result = rollback_transaction(tx["path"], reason="GIT-FRX-02 contract")
    archived = read_transaction_summary(root, tx["transaction_id"])

    assert result.returncode == 0
    assert (root / "tracked.txt").read_text(encoding="utf-8") == "before\n"
    assert archived["git_recovery"]["recovery"]["state"] == "rolled-back"
    assert archived["git_recovery"]["finalization"]["state"] == "rolled-back"


def test_abandon_archives_explicit_kept_changes_finalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path, run_id="abandon")
    tx = _create(root, artifact)

    abandon_transaction(tx["path"], reason="keep")
    archived = read_transaction_summary(root, tx["transaction_id"])

    assert archived["status"] == "abandoned-kept-changes"
    assert archived["git_recovery"]["recovery"]["state"] == "abandoned"
    assert archived["git_recovery"]["finalization"]["state"] == "abandoned"


def test_recovery_verifier_detects_private_ref_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path, run_id="drift")
    tx = _create(root, artifact)
    journal = tx["git_recovery"]
    start_ref = next(item for item in journal["recovery"]["refs"] if item["role"] == "run-start")

    (root / "other.txt").write_text("other\n", encoding="utf-8")
    _git(root, "add", "other.txt")
    _git(root, "commit", "-qm", "other")
    other = _git(root, "rev-parse", "HEAD")
    _git(root, "update-ref", start_ref["ref"], other)

    verdict = verify_git_transaction_recovery(root, journal)
    assert verdict["ok"] is False
    assert verdict["mismatches"] == [{"ref": start_ref["ref"], "expected": start_ref["sha"], "actual": other}]
