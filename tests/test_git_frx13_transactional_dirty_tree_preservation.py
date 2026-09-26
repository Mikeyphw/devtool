from __future__ import annotations

import os
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest

from devtool.git_dirty_state import GitDirtyStateError, capture_dirty_tree, restore_dirty_tree
from devtool.transaction_worktree import transaction_execution_root
from devtool.transactions import (
    create_apply_transaction, integrate_transaction_worktree, record_transaction_git_commit, rollback_transaction,
)


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True, check=False, timeout=10)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return (proc.stdout or "").strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"; root.mkdir()
    _git(root, "init", "-q"); _git(root, "config", "user.email", "fixture@example.invalid"); _git(root, "config", "user.name", "Fixture")
    (root / "staged.txt").write_text("staged-base\n", encoding="utf-8")
    (root / "unstaged.txt").write_text("unstaged-base\n", encoding="utf-8")
    _git(root, "add", "."); _git(root, "commit", "-qm", "base")
    return root


def _dirty(root: Path) -> str:
    (root / "staged.txt").write_text("user-staged\n", encoding="utf-8"); _git(root, "add", "staged.txt")
    (root / "unstaged.txt").write_text("user-unstaged\n", encoding="utf-8")
    (root / "untracked.txt").write_text("user-untracked\n", encoding="utf-8")
    return _git(root, "status", "--porcelain=v1")


def test_capture_restore_roundtrips_exact_dirty_state_on_same_head(tmp_path: Path) -> None:
    root = _repo(tmp_path); before = _dirty(root)
    index = Path(_git(root, "rev-parse", "--git-path", "index")); index = index if index.is_absolute() else root / index
    before_index = index.read_bytes()
    snap = tmp_path / "snapshot"; capture_dirty_tree(root, snap)
    _git(root, "reset", "--hard", "-q", "HEAD"); _git(root, "clean", "-fdq")
    restored = restore_dirty_tree(snap)
    assert restored["index_restore_mode"] == "exact"
    assert index.read_bytes() == before_index
    assert _git(root, "status", "--porcelain=v1") == before
    assert (root / "untracked.txt").read_text() == "user-untracked\n"


def test_isolated_commit_preserves_staged_unstaged_and_untracked_user_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path); _dirty(root)
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg")); monkeypatch.setenv("DEVTOOL_TRANSACTION_ISOLATION", "worktree")
    monkeypatch.setenv("DEVTOOL_RUN_ID", "git-frx13"); monkeypatch.setenv("DEVTOOL_TRANSACTION_COMMIT_MODE", "new")
    artifact = tmp_path / "overlay.zip"
    with ZipFile(artifact, "w") as archive: archive.writestr("new.txt", "transaction\n")
    tx = create_apply_transaction(root, artifact, artifact_type="overlay", preview={"changes": [{"kind": "added", "path": "new.txt"}], "counts": {}})
    assert tx["isolation"]["effective"] == "worktree" and tx["git_state"]["dirty_tree_preserved"] is True
    worktree = transaction_execution_root(tx)
    (worktree / "new.txt").write_text("transaction\n", encoding="utf-8"); _git(worktree, "add", "new.txt")
    _git(worktree, "config", "user.email", "fixture@example.invalid"); _git(worktree, "config", "user.name", "Fixture")
    _git(worktree, "commit", "-qm", "transaction result"); result = _git(worktree, "rev-parse", "HEAD")
    record_transaction_git_commit(tx["path"], result)
    evidence = integrate_transaction_worktree(tx["path"])
    assert evidence["state"] == "integrated"
    assert _git(root, "rev-parse", "HEAD") == result
    status = _git(root, "status", "--porcelain=v1")
    assert "M  staged.txt" in status and " M unstaged.txt" in status and "?? untracked.txt" in status
    assert _git(root, "ls-files", "new.txt") == "new.txt"
    assert (root / "staged.txt").read_text() == "user-staged\n"
    assert (root / "unstaged.txt").read_text() == "user-unstaged\n"
    assert (root / "untracked.txt").read_text() == "user-untracked\n"


def test_rollback_after_isolated_integration_restores_original_head_and_dirty_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path); start = _git(root, "rev-parse", "HEAD"); before = _dirty(root)
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg")); monkeypatch.setenv("DEVTOOL_TRANSACTION_ISOLATION", "worktree")
    monkeypatch.setenv("DEVTOOL_RUN_ID", "git-frx13-rollback"); monkeypatch.setenv("DEVTOOL_TRANSACTION_COMMIT_MODE", "new")
    artifact = tmp_path / "overlay.zip"
    with ZipFile(artifact, "w") as archive: archive.writestr("new.txt", "transaction\n")
    tx = create_apply_transaction(root, artifact, artifact_type="overlay", preview={"changes": [{"kind": "added", "path": "new.txt"}], "counts": {}})
    worktree = transaction_execution_root(tx); (worktree / "new.txt").write_text("transaction\n"); _git(worktree, "add", "new.txt")
    _git(worktree, "config", "user.email", "fixture@example.invalid"); _git(worktree, "config", "user.name", "Fixture"); _git(worktree, "commit", "-qm", "result")
    record_transaction_git_commit(tx["path"], _git(worktree, "rev-parse", "HEAD")); integrate_transaction_worktree(tx["path"])
    rollback = rollback_transaction(tx["path"], reason="fixture rollback")
    assert rollback.returncode == 0
    assert _git(root, "rev-parse", "HEAD") == start
    assert _git(root, "status", "--porcelain=v1") == before
    assert not (root / "new.txt").exists()


def test_untracked_path_that_becomes_tracked_fails_closed_before_primary_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path); (root / "collision.txt").write_text("user\n")
    start = _git(root, "rev-parse", "HEAD")
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg")); monkeypatch.setenv("DEVTOOL_TRANSACTION_ISOLATION", "worktree")
    monkeypatch.setenv("DEVTOOL_RUN_ID", "git-frx13-collision"); monkeypatch.setenv("DEVTOOL_TRANSACTION_COMMIT_MODE", "new")
    artifact = tmp_path / "overlay.zip"
    with ZipFile(artifact, "w") as archive: archive.writestr("collision.txt", "transaction\n")
    tx = create_apply_transaction(root, artifact, artifact_type="overlay", preview={"changes": [{"kind": "added", "path": "collision.txt"}], "counts": {}})
    worktree = transaction_execution_root(tx); (worktree / "collision.txt").write_text("transaction\n"); _git(worktree, "add", "collision.txt")
    _git(worktree, "config", "user.email", "fixture@example.invalid"); _git(worktree, "config", "user.name", "Fixture"); _git(worktree, "commit", "-qm", "result")
    record_transaction_git_commit(tx["path"], _git(worktree, "rev-parse", "HEAD"))
    with pytest.raises(GitDirtyStateError, match="starts tracking"):
        integrate_transaction_worktree(tx["path"])
    assert _git(root, "rev-parse", "HEAD") == start
    assert (root / "collision.txt").read_text() == "user\n"
    rollback_transaction(tx["path"], reason="fixture cleanup")


def test_dirty_tracked_path_changed_by_transaction_fails_closed_before_primary_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    (root / "unstaged.txt").write_text("user-local\n", encoding="utf-8")
    start = _git(root, "rev-parse", "HEAD")
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg")); monkeypatch.setenv("DEVTOOL_TRANSACTION_ISOLATION", "worktree")
    monkeypatch.setenv("DEVTOOL_RUN_ID", "git-frx13-overlap"); monkeypatch.setenv("DEVTOOL_TRANSACTION_COMMIT_MODE", "new")
    artifact = tmp_path / "overlay.zip"
    with ZipFile(artifact, "w") as archive: archive.writestr("unstaged.txt", "transaction\n")
    tx = create_apply_transaction(root, artifact, artifact_type="overlay", preview={"changes": [{"kind": "modified", "path": "unstaged.txt"}], "counts": {}})
    worktree = transaction_execution_root(tx)
    (worktree / "unstaged.txt").write_text("transaction\n", encoding="utf-8")
    _git(worktree, "add", "unstaged.txt"); _git(worktree, "config", "user.email", "fixture@example.invalid"); _git(worktree, "config", "user.name", "Fixture")
    _git(worktree, "commit", "-qm", "transaction overlap")
    record_transaction_git_commit(tx["path"], _git(worktree, "rev-parse", "HEAD"))
    with pytest.raises(GitDirtyStateError, match="transaction result also changed"):
        integrate_transaction_worktree(tx["path"])
    assert _git(root, "rev-parse", "HEAD") == start
    assert (root / "unstaged.txt").read_text(encoding="utf-8") == "user-local\n"
    rollback_transaction(tx["path"], reason="fixture cleanup")
