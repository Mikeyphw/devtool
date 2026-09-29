from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import pytest

from devtool.git_dirty_state import GitDirtyStateError
from devtool.cli import _complete_apply_transaction
from devtool.transaction_worktree import transaction_execution_root
from devtool.transactions import (
    _has_other_repository_reservations,
    _hash_path,
    _path_identity,
    create_apply_transaction,
    checkpoint_transaction_applied,
    integrate_transaction_worktree,
    transaction_recovery_diagnostics,
    record_transaction_git_commit,
    rollback_transaction,
)
from devtool.reservations import Reservation


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True, check=False, timeout=10)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return (proc.stdout or "").strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "Fixture")
    (root / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    return root


def _isolated_tx_for_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root: Path, content: str) -> dict[str, object]:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    monkeypatch.setenv("DEVTOOL_TRANSACTION_ISOLATION", "worktree")
    monkeypatch.setenv("DEVTOOL_RUN_ID", "life-x01")
    monkeypatch.setenv("DEVTOOL_TRANSACTION_COMMIT_MODE", "new")
    artifact = tmp_path / "overlay.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("tracked.txt", content)
    return create_apply_transaction(
        root,
        artifact,
        artifact_type="overlay",
        preview={"changes": [{"kind": "modified", "path": "tracked.txt"}], "counts": {}},
    )


def test_life_x01_allows_preserved_dirty_path_when_transaction_result_matches_user_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    (root / "tracked.txt").write_text("user-local\n", encoding="utf-8")
    start = _git(root, "rev-parse", "HEAD")
    tx = _isolated_tx_for_path(tmp_path, monkeypatch, root, "user-local\n")
    worktree = transaction_execution_root(tx)
    (worktree / "tracked.txt").write_text("user-local\n", encoding="utf-8")
    _git(worktree, "add", "tracked.txt")
    _git(worktree, "config", "user.email", "fixture@example.invalid")
    _git(worktree, "config", "user.name", "Fixture")
    _git(worktree, "commit", "-qm", "same user content")
    result = _git(worktree, "rev-parse", "HEAD")
    record_transaction_git_commit(tx["path"], result)

    evidence = integrate_transaction_worktree(tx["path"])

    assert evidence["state"] == "integrated"
    assert _git(root, "rev-parse", "HEAD") == result
    assert (root / "tracked.txt").read_text(encoding="utf-8") == "user-local\n"
    assert _git(root, "status", "--porcelain=v1") == ""
    assert result != start


def test_life_x01_same_content_uses_git_executable_semantics_not_full_posix_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    tracked = root / "tracked.txt"
    tracked.chmod(0o600)
    tracked.write_text("user-local\n", encoding="utf-8")
    tx = _isolated_tx_for_path(tmp_path, monkeypatch, root, "user-local\n")
    worktree = transaction_execution_root(tx)
    (worktree / "tracked.txt").write_text("user-local\n", encoding="utf-8")
    _git(worktree, "add", "tracked.txt")
    _git(worktree, "config", "user.email", "fixture@example.invalid")
    _git(worktree, "config", "user.name", "Fixture")
    _git(worktree, "commit", "-qm", "same content normalized git mode")
    result = _git(worktree, "rev-parse", "HEAD")
    record_transaction_git_commit(tx["path"], result)

    evidence = integrate_transaction_worktree(tx["path"])

    assert evidence["state"] == "integrated"
    assert tracked.read_text(encoding="utf-8") == "user-local\n"
    assert tracked.stat().st_mode & 0o777 == 0o600
    assert _git(root, "status", "--porcelain=v1") == ""


def test_life_x01_same_content_still_rejects_executable_bit_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    tracked = root / "tracked.txt"
    tracked.write_text("user-local\n", encoding="utf-8")
    tracked.chmod(0o755)
    start = _git(root, "rev-parse", "HEAD")
    tx = _isolated_tx_for_path(tmp_path, monkeypatch, root, "user-local\n")
    worktree = transaction_execution_root(tx)
    (worktree / "tracked.txt").write_text("user-local\n", encoding="utf-8")
    (worktree / "tracked.txt").chmod(0o644)
    _git(worktree, "add", "tracked.txt")
    _git(worktree, "config", "user.email", "fixture@example.invalid")
    _git(worktree, "config", "user.name", "Fixture")
    _git(worktree, "commit", "-qm", "same content different executable bit")
    record_transaction_git_commit(tx["path"], _git(worktree, "rev-parse", "HEAD"))

    with pytest.raises(GitDirtyStateError, match="transaction result also changed"):
        integrate_transaction_worktree(tx["path"])

    assert _git(root, "rev-parse", "HEAD") == start
    assert tracked.read_text(encoding="utf-8") == "user-local\n"
    assert tracked.stat().st_mode & 0o100


def test_life_x01_allows_untracked_path_to_become_tracked_when_result_is_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    collision = root / "collision.txt"
    collision.write_text("same-user-content\n", encoding="utf-8")
    collision.chmod(0o600)
    start = _git(root, "rev-parse", "HEAD")

    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg-untracked-same"))
    monkeypatch.setenv("DEVTOOL_TRANSACTION_ISOLATION", "worktree")
    monkeypatch.setenv("DEVTOOL_RUN_ID", "life-x01-untracked-same")
    monkeypatch.setenv("DEVTOOL_TRANSACTION_COMMIT_MODE", "new")
    artifact = tmp_path / "untracked-same.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("collision.txt", "same-user-content\n")
    tx = create_apply_transaction(
        root, artifact, artifact_type="overlay",
        preview={"changes": [{"kind": "added", "path": "collision.txt"}], "counts": {}},
    )
    worktree = transaction_execution_root(tx)
    (worktree / "collision.txt").write_text("same-user-content\n", encoding="utf-8")
    _git(worktree, "add", "collision.txt")
    _git(worktree, "config", "user.email", "fixture@example.invalid")
    _git(worktree, "config", "user.name", "Fixture")
    _git(worktree, "commit", "-qm", "track convergent untracked path")
    result = _git(worktree, "rev-parse", "HEAD")
    record_transaction_git_commit(tx["path"], result)

    evidence = integrate_transaction_worktree(tx["path"])

    assert evidence["state"] == "integrated"
    assert result != start
    assert _git(root, "ls-files", "collision.txt") == "collision.txt"
    assert collision.read_text(encoding="utf-8") == "same-user-content\n"
    assert _git(root, "status", "--porcelain=v1") == ""


def test_life_x01_untracked_to_tracked_still_rejects_real_content_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    collision = root / "collision.txt"
    collision.write_text("user-content\n", encoding="utf-8")
    start = _git(root, "rev-parse", "HEAD")

    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg-untracked-conflict"))
    monkeypatch.setenv("DEVTOOL_TRANSACTION_ISOLATION", "worktree")
    monkeypatch.setenv("DEVTOOL_RUN_ID", "life-x01-untracked-conflict")
    monkeypatch.setenv("DEVTOOL_TRANSACTION_COMMIT_MODE", "new")
    artifact = tmp_path / "untracked-conflict.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("collision.txt", "transaction-content\n")
    tx = create_apply_transaction(
        root, artifact, artifact_type="overlay",
        preview={"changes": [{"kind": "added", "path": "collision.txt"}], "counts": {}},
    )
    worktree = transaction_execution_root(tx)
    (worktree / "collision.txt").write_text("transaction-content\n", encoding="utf-8")
    _git(worktree, "add", "collision.txt")
    _git(worktree, "config", "user.email", "fixture@example.invalid")
    _git(worktree, "config", "user.name", "Fixture")
    _git(worktree, "commit", "-qm", "conflicting tracked add")
    record_transaction_git_commit(tx["path"], _git(worktree, "rev-parse", "HEAD"))

    with pytest.raises(GitDirtyStateError, match="starts tracking"):
        integrate_transaction_worktree(tx["path"])

    assert _git(root, "rev-parse", "HEAD") == start
    assert collision.read_text(encoding="utf-8") == "user-content\n"


def test_life_x01_completion_conflict_is_classified_rolled_back_and_not_stranded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    collision = root / "collision.txt"
    collision.write_text("user-content\n", encoding="utf-8")
    start = _git(root, "rev-parse", "HEAD")

    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg-finalize-conflict"))
    monkeypatch.setenv("DEVTOOL_TRANSACTION_ISOLATION", "worktree")
    monkeypatch.setenv("DEVTOOL_RUN_ID", "life-x01-finalize-conflict")
    monkeypatch.setenv("DEVTOOL_TRANSACTION_COMMIT_MODE", "new")
    artifact = tmp_path / "finalize-conflict.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("collision.txt", "transaction-content\n")
    tx = create_apply_transaction(
        root, artifact, artifact_type="overlay",
        preview={"changes": [{"kind": "added", "path": "collision.txt"}], "counts": {}},
    )
    worktree = transaction_execution_root(tx)
    (worktree / "collision.txt").write_text("transaction-content\n", encoding="utf-8")
    _git(worktree, "add", "collision.txt")
    _git(worktree, "config", "user.email", "fixture@example.invalid")
    _git(worktree, "config", "user.name", "Fixture")
    _git(worktree, "commit", "-qm", "conflicting tracked add")
    record_transaction_git_commit(tx["path"], _git(worktree, "rev-parse", "HEAD"))

    results, raw = _complete_apply_transaction(tx["path"])

    assert [item.name for item in results] == ["apply-finalize", "apply-rollback"]
    assert results[0].returncode == 1
    assert results[0].failure_kind == "transaction_finalization"
    assert results[1].returncode == 0, raw
    assert _git(root, "rev-parse", "HEAD") == start
    assert collision.read_text(encoding="utf-8") == "user-content\n"
    assert not Path(str(tx["path"])).exists()


def test_life_x01_still_rejects_real_dirty_path_content_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    (root / "tracked.txt").write_text("user-local\n", encoding="utf-8")
    start = _git(root, "rev-parse", "HEAD")
    tx = _isolated_tx_for_path(tmp_path, monkeypatch, root, "transaction\n")
    worktree = transaction_execution_root(tx)
    (worktree / "tracked.txt").write_text("transaction\n", encoding="utf-8")
    _git(worktree, "add", "tracked.txt")
    _git(worktree, "config", "user.email", "fixture@example.invalid")
    _git(worktree, "config", "user.name", "Fixture")
    _git(worktree, "commit", "-qm", "conflicting content")
    record_transaction_git_commit(tx["path"], _git(worktree, "rev-parse", "HEAD"))

    with pytest.raises(GitDirtyStateError, match="transaction result also changed"):
        integrate_transaction_worktree(tx["path"])

    assert _git(root, "rev-parse", "HEAD") == start
    assert (root / "tracked.txt").read_text(encoding="utf-8") == "user-local\n"


@pytest.mark.parametrize("artifact_type", ["overlay", "overlay_bundle", "snapshot"])
def test_life_x01_retries_preflight_failure_without_fabricating_resume_checkpoint(
    tmp_path: Path, artifact_type: str
) -> None:
    repo = tmp_path / "plain-repo"
    repo.mkdir()
    tx_dir = tmp_path / "tx"
    backup_dir = tx_dir / "backup"
    backup_dir.mkdir(parents=True)
    tx_path = tx_dir / "transaction.json"
    target = repo / "file.txt"
    backup = backup_dir / "file.txt"
    target.write_text("transaction-result\n", encoding="utf-8")
    backup.write_text("before\n", encoding="utf-8")
    tx = {
        "schema_version": 4,
        "artifact_type": artifact_type,
        "status": "rollback-failed",
        "transaction_id": "life-x01-rollback",
        "path": str(tx_path),
        "repo_root": str(repo),
        "backup_dir": str(backup_dir),
        "devtool_xdg_dir": str(tmp_path / "xdg"),
        "affected_paths": [
            {
                "path": "file.txt",
                "existed": True,
                "restore_state": "pending",
                "backup_sha256": _hash_path(backup),
                "backup_identity": _path_identity(backup),
                "applied_identity": _path_identity(target),
                "verified": False,
                "staged_path": "",
                "displaced_path": "",
                "error": "",
                "created_parent_dirs": [],
            }
        ],
        "git_state": {"is_git_repo": False},
    }
    tx_path.write_text(json.dumps(tx, indent=2), encoding="utf-8")

    result = rollback_transaction(tx_path, reason="life-x01 fixture")

    assert result.returncode == 0, result.output
    assert target.read_text(encoding="utf-8") == "before\n"
    matches = list(tmp_path.glob("xdg-data/devtool/transactions/*/history/life-x01-rollback.json"))
    assert len(matches) == 1
    data = json.loads(matches[0].read_text(encoding="utf-8"))
    entry = data["affected_paths"][0]
    assert "recovered_resume_target_identities" not in data
    assert "resume_target_identity_recovered_from" not in entry


def test_life_x01_missing_resume_checkpoint_still_fails_after_user_edit(tmp_path: Path) -> None:
    repo = tmp_path / "plain-repo"
    repo.mkdir()
    tx_dir = tmp_path / "tx"
    backup_dir = tx_dir / "backup"
    backup_dir.mkdir(parents=True)
    tx_path = tx_dir / "transaction.json"
    target = repo / "file.txt"
    backup = backup_dir / "file.txt"
    target.write_text("transaction-result\n", encoding="utf-8")
    applied = _path_identity(target)
    target.write_text("user-edited-after-failure\n", encoding="utf-8")
    backup.write_text("before\n", encoding="utf-8")
    tx = {
        "schema_version": 4,
        "status": "rollback-failed",
        "transaction_id": "life-x01-conflict",
        "path": str(tx_path),
        "repo_root": str(repo),
        "backup_dir": str(backup_dir),
        "devtool_xdg_dir": str(tmp_path / "xdg"),
        "affected_paths": [
            {
                "path": "file.txt",
                "existed": True,
                "restore_state": "pending",
                "backup_sha256": _hash_path(backup),
                "backup_identity": _path_identity(backup),
                "applied_identity": applied,
                "verified": False,
                "staged_path": "",
                "displaced_path": "",
                "error": "",
                "created_parent_dirs": [],
            }
        ],
        "git_state": {"is_git_repo": False},
    }
    tx_path.write_text(json.dumps(tx, indent=2), encoding="utf-8")

    result = rollback_transaction(tx_path, reason="life-x01 fixture")

    assert result.returncode == 1
    assert "durable checkpoint" in result.output
    assert target.read_text(encoding="utf-8") == "user-edited-after-failure\n"


@pytest.mark.parametrize("artifact_type", ["overlay", "overlay_bundle", "snapshot"])
def test_life_x01_partial_rollback_requires_real_persisted_resume_checkpoint(
    tmp_path: Path, artifact_type: str
) -> None:
    repo = tmp_path / "plain-repo"
    repo.mkdir()
    tx_dir = tmp_path / "tx-partial"
    backup_dir = tx_dir / "backup"
    backup_dir.mkdir(parents=True)
    tx_path = tx_dir / "transaction.json"
    target = repo / "file.txt"
    backup = backup_dir / "file.txt"
    target.write_text("transaction-result\n", encoding="utf-8")
    backup.write_text("before\n", encoding="utf-8")
    tx = {
        "schema_version": 4, "artifact_type": artifact_type, "status": "rollback-failed", "transaction_id": "life-x01-partial",
        "path": str(tx_path), "repo_root": str(repo), "backup_dir": str(backup_dir),
        "affected_paths": [{
            "path": "file.txt", "existed": True, "restore_state": "target-displaced",
            "backup_sha256": _hash_path(backup), "backup_identity": _path_identity(backup),
            "applied_identity": _path_identity(target), "verified": False,
            "staged_path": "", "displaced_path": "", "error": "", "created_parent_dirs": [],
        }],
        "git_state": {"is_git_repo": False},
    }
    tx_path.write_text(json.dumps(tx, indent=2), encoding="utf-8")

    result = rollback_transaction(tx_path, reason="partial checkpoint fixture")

    assert result.returncode == 1
    assert "missing persisted rollback checkpoint" in result.output
    receipt = result.metadata["rollback_receipt"]
    assert receipt["state"] == "partial"
    assert receipt["partial"] is True


@pytest.mark.parametrize("artifact_type", ["overlay", "overlay_bundle", "snapshot"])
def test_life_x01_partial_rollback_resumes_from_persisted_checkpoint(
    tmp_path: Path, artifact_type: str
) -> None:
    repo = tmp_path / "plain-repo"
    repo.mkdir()
    tx_dir = tmp_path / "tx-resume"
    backup_dir = tx_dir / "backup"
    backup_dir.mkdir(parents=True)
    tx_path = tx_dir / "transaction.json"
    target = repo / "file.txt"
    backup = backup_dir / "file.txt"
    target.write_text("before\n", encoding="utf-8")
    backup.write_text("before\n", encoding="utf-8")
    tx = {
        "schema_version": 4, "artifact_type": artifact_type, "status": "rollback-failed", "transaction_id": "life-x01-resume",
        "path": str(tx_path), "repo_root": str(repo), "backup_dir": str(backup_dir),
        "affected_paths": [{
            "path": "file.txt", "existed": True, "restore_state": "restored",
            "backup_sha256": _hash_path(backup), "backup_identity": _path_identity(backup),
            "applied_identity": {"kind": "file", "mode": 0o644, "sha256": "different", "target": ""},
            "resume_target_identity": _path_identity(target), "verified": False,
            "staged_path": "", "displaced_path": "", "error": "", "created_parent_dirs": [],
        }],
        "git_state": {"is_git_repo": False},
    }
    tx_path.write_text(json.dumps(tx, indent=2), encoding="utf-8")

    result = rollback_transaction(tx_path, reason="resume checkpoint fixture")

    assert result.returncode == 0, result.output
    assert result.metadata["rollback_receipt"]["state"] == "verified"
    assert target.read_text(encoding="utf-8") == "before\n"


@pytest.mark.parametrize("artifact_type", ["overlay", "overlay_bundle", "snapshot"])
def test_life_x01_verification_failure_remains_typed_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact_type: str
) -> None:
    repo = tmp_path / "plain-repo"
    repo.mkdir()
    target = repo / "file.txt"
    target.write_text("before\n", encoding="utf-8")
    artifact = tmp_path / "overlay.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("file.txt", "after\n")
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    monkeypatch.setenv("DEVTOOL_TRANSACTION_ISOLATION", "direct")
    tx = create_apply_transaction(
        repo,
        artifact,
        artifact_type=artifact_type,
        preview={"changes": [{"kind": "modified", "path": "file.txt"}], "counts": {}},
    )
    target.write_text("after\n", encoding="utf-8")
    checkpoint_transaction_applied(tx["path"])

    with patch("devtool.transactions._restore_git_state", side_effect=RuntimeError("git verification fixture")):
        failed = rollback_transaction(tx["path"], reason="verification fixture")

    receipt = failed.metadata["rollback_receipt"]
    assert failed.returncode == 1
    assert receipt["state"] == "partial"
    assert receipt["phase"] == "verification"
    assert receipt["resumable"] is True
    persisted = json.loads(Path(tx["path"]).read_text(encoding="utf-8"))
    diagnostics = transaction_recovery_diagnostics(persisted)
    assert diagnostics["phase"] == "verification"
    assert diagnostics["reserved_paths"] == ["file.txt"]
    assert diagnostics["checkpoint_paths"] == ["file.txt"]
    assert "git verification fixture" in diagnostics["blocker"]
    assert "rollback --transaction" in diagnostics["action"]

    retried = rollback_transaction(tx["path"], reason="verification retry")
    assert retried.returncode == 0, retried.output
    assert retried.metadata["rollback_receipt"]["state"] == "verified"


def test_life_x01_transaction_reservation_never_self_blocks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tx = {"transaction_id": "tx-self", "run_id": "run-self"}
    own = Reservation(
        source="transaction", reservation_id="tx-self", target="devtool", paths=("file.txt",),
        paths_known=True, transaction_id="tx-self", run_id="run-self",
    )
    same_run = Reservation(
        source="run", reservation_id="run-self", target="devtool", paths=("other.txt",),
        paths_known=True, run_id="run-self",
    )
    other = Reservation(
        source="transaction", reservation_id="tx-other", target="devtool", paths=("other.txt",),
        paths_known=True, transaction_id="tx-other", run_id="run-other",
    )
    monkeypatch.setattr("devtool.transactions.active_reservations", lambda _repo: [own, same_run])
    assert _has_other_repository_reservations(tmp_path, tx) is False
    monkeypatch.setattr("devtool.transactions.active_reservations", lambda _repo: [own, other])
    assert _has_other_repository_reservations(tmp_path, tx) is True


def test_life_x01_repaired_git_index_preflight_retries_without_synthetic_resume_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    (root / "unrelated.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", "unrelated.txt")
    _git(root, "commit", "-qm", "add unrelated")
    (root / "unrelated.txt").write_text("staged-user-work\n", encoding="utf-8")
    _git(root, "add", "unrelated.txt")
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    monkeypatch.setenv("DEVTOOL_TRANSACTION_ISOLATION", "direct")
    artifact = tmp_path / "overlay-index.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("tracked.txt", "transaction-result\n")
    tx = create_apply_transaction(
        root, artifact, artifact_type="overlay",
        preview={"changes": [{"kind": "modified", "path": "tracked.txt"}], "counts": {}},
    )
    tx_path = Path(tx["path"])
    (root / "tracked.txt").write_text("transaction-result\n", encoding="utf-8")
    checkpoint_transaction_applied(tx_path)
    _git(root, "add", "tracked.txt")  # unknown live-index drift after the durable apply checkpoint

    failed = rollback_transaction(tx_path, reason="index drift fixture")
    assert failed.returncode == 1
    assert "repository index changed" in failed.output
    failed_tx = json.loads(tx_path.read_text(encoding="utf-8"))
    assert failed_tx["status"] == "rollback-failed"
    assert "resume_target_identity" not in failed_tx["affected_paths"][0]

    _git(root, "reset", "HEAD", "--", "tracked.txt")  # restore the exact transaction checkpoint index
    retried = rollback_transaction(tx_path, reason="index drift repaired")
    assert retried.returncode == 0, retried.output
    assert (root / "tracked.txt").read_text(encoding="utf-8") == "base\n"
    assert _git(root, "diff", "--cached", "--name-only") == "unrelated.txt"
