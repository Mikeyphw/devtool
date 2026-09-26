from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from devtool.cli import build_parser
from devtool.runners import apply_overlay
from devtool.transaction_worktree import _fixture_worktree, self_refresh_repo_root, transaction_execution_root
from devtool.transactions import (
    create_apply_transaction,
    mark_transaction_completed,
    read_transaction,
    record_transaction_git_commit,
    rollback_transaction,
)


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False
    )
    assert proc.returncode == 0, proc.stdout
    return (proc.stdout or "").strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test User")
    (root / "tracked.txt").write_text("before\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-qm", "baseline")
    return root


def _artifact(tmp_path: Path, value: str = "after\n") -> Path:
    artifact = tmp_path / "overlay.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("tracked.txt", value)
    return artifact


def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, isolation: str = "worktree") -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    monkeypatch.setenv("DEVTOOL_TRANSACTION_ISOLATION", isolation)
    monkeypatch.setenv("DEVTOOL_RUN_ID", "git-frx03-test")


def test_apply_cli_prefers_worktree_and_exposes_direct_escape_hatch() -> None:
    parser = build_parser()
    preferred = parser.parse_args(["apply-overlay", "demo.zip"])
    direct = parser.parse_args(["apply-overlay", "--isolation", "direct", "demo.zip"])
    assert preferred.isolation == "worktree"
    assert direct.isolation == "direct"


def test_clean_committed_repo_gets_isolated_transaction_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path)

    tx = create_apply_transaction(
        root, artifact, artifact_type="overlay",
        preview={"changes": [{"kind": "modified", "path": "tracked.txt"}], "counts": {}},
    )

    isolation = tx["isolation"]
    execution_root = transaction_execution_root(tx)
    assert isolation["requested"] == "worktree"
    assert isolation["effective"] == "worktree"
    assert execution_root != root
    assert execution_root.exists()
    assert _git(execution_root, "rev-parse", "HEAD") == _git(root, "rev-parse", "HEAD")
    assert tx["git_recovery"]["worktree_path"] == str(execution_root)
    assert tx["git_recovery"]["isolation"] == "worktree"

    rollback = rollback_transaction(tx["path"], reason="fixture cleanup")
    assert rollback.returncode == 0


def test_runner_applies_in_worktree_and_primary_changes_only_at_completion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path)

    result = apply_overlay(root, str(artifact), yes=True, skip_artifact_validation=True)
    assert result.returncode == 0
    assert result.metadata["transaction_isolation"] == "worktree"
    tx = read_transaction(result.metadata["transaction_path"])
    execution_root = transaction_execution_root(tx)

    assert (root / "tracked.txt").read_text(encoding="utf-8") == "before\n"
    assert (execution_root / "tracked.txt").read_text(encoding="utf-8") == "after\n"
    assert os.environ["DEVTOOL_TRANSACTION_REPO_ROOT"] == str(execution_root)

    mark_transaction_completed(result.metadata["transaction_path"])
    assert (root / "tracked.txt").read_text(encoding="utf-8") == "after\n"
    assert not Path(result.metadata["transaction_path"]).exists()
    worktree_list = _git(root, "worktree", "list", "--porcelain")
    assert str(execution_root) not in worktree_list


def test_isolated_no_commit_preserves_primary_head_and_index_semantics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path)
    start = _git(root, "rev-parse", "HEAD")

    result = apply_overlay(root, str(artifact), yes=True, skip_artifact_validation=True)
    mark_transaction_completed(result.metadata["transaction_path"])

    assert _git(root, "rev-parse", "HEAD") == start
    assert _git(root, "diff", "--cached", "--name-only") == ""
    assert _git(root, "status", "--porcelain").strip() == "M tracked.txt"


def test_isolated_commit_integrates_exact_transaction_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("DEVTOOL_TRANSACTION_COMMIT_MODE", "new")
    tx = create_apply_transaction(
        root, artifact, artifact_type="overlay",
        preview={"changes": [{"kind": "modified", "path": "tracked.txt"}], "counts": {}},
    )
    execution_root = transaction_execution_root(tx)
    (execution_root / "tracked.txt").write_text("after\n", encoding="utf-8")
    _git(execution_root, "add", "tracked.txt")
    _git(execution_root, "config", "user.email", "test@example.invalid")
    _git(execution_root, "config", "user.name", "Test User")
    _git(execution_root, "commit", "-qm", "isolated result")
    result_commit = _git(execution_root, "rev-parse", "HEAD")
    record_transaction_git_commit(tx["path"], result_commit)

    mark_transaction_completed(tx["path"])

    assert _git(root, "rev-parse", "HEAD") == result_commit
    assert (root / "tracked.txt").read_text(encoding="utf-8") == "after\n"
    assert _git(root, "status", "--porcelain") == ""


def test_rollback_before_integration_leaves_primary_checkout_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path)
    start = _git(root, "rev-parse", "HEAD")

    result = apply_overlay(root, str(artifact), yes=True, skip_artifact_validation=True)
    tx = read_transaction(result.metadata["transaction_path"])
    execution_root = transaction_execution_root(tx)
    assert (execution_root / "tracked.txt").read_text(encoding="utf-8") == "after\n"

    rollback = rollback_transaction(result.metadata["transaction_path"], reason="validation failed")
    assert rollback.returncode == 0
    assert _git(root, "rev-parse", "HEAD") == start
    assert (root / "tracked.txt").read_text(encoding="utf-8") == "before\n"
    assert _git(root, "status", "--porcelain") == ""
    assert not execution_root.exists()


def test_primary_drift_blocks_integration_without_overwriting_user_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path)

    result = apply_overlay(root, str(artifact), yes=True, skip_artifact_validation=True)
    (root / "user.txt").write_text("user change\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Primary checkout worktree changed"):
        mark_transaction_completed(result.metadata["transaction_path"])

    assert (root / "tracked.txt").read_text(encoding="utf-8") == "before\n"
    assert (root / "user.txt").read_text(encoding="utf-8") == "user change\n"
    assert Path(result.metadata["transaction_path"]).exists()
    # Recovery remains possible and must not delete unrelated user drift.
    rollback = rollback_transaction(result.metadata["transaction_path"], reason="drift recovery")
    assert rollback.returncode == 0
    assert (root / "user.txt").read_text(encoding="utf-8") == "user change\n"


def test_dirty_repo_uses_worktree_isolation_with_transactional_preservation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path)
    (root / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    tx = create_apply_transaction(
        root, artifact, artifact_type="overlay",
        preview={"changes": [{"kind": "modified", "path": "tracked.txt"}], "counts": {}},
    )
    assert tx["isolation"]["requested"] == "worktree"
    assert tx["isolation"]["effective"] == "worktree"
    assert tx["isolation"]["dirty_tree_preserved"] is True
    assert tx["git_state"]["dirty_tree_preserved"] is True
    assert "dirty.txt" in tx["git_state"]["dirty_tree_paths"]
    rollback_transaction(tx["path"], reason="fixture cleanup")


def test_unborn_repo_truthfully_falls_back_to_direct_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path)
    tx = create_apply_transaction(root, artifact, artifact_type="overlay", preview={"changes": [], "counts": {}})
    assert tx["isolation"]["effective"] == "direct"
    assert tx["isolation"]["fallback_reason"] == "unborn repository"
    rollback_transaction(tx["path"], reason="fixture cleanup")


def test_explicit_direct_mode_keeps_legacy_in_place_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path, isolation="direct")

    result = apply_overlay(root, str(artifact), yes=True, skip_artifact_validation=True)
    assert result.returncode == 0
    assert result.metadata["transaction_isolation"] == "direct"
    assert (root / "tracked.txt").read_text(encoding="utf-8") == "after\n"
    mark_transaction_completed(result.metadata["transaction_path"])


def test_self_refresh_root_prefers_active_transaction_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fallback = tmp_path / "primary"
    transaction = tmp_path / "isolated"
    fallback.mkdir()
    transaction.mkdir()
    monkeypatch.delenv("DEVTOOL_TRANSACTION_REPO_ROOT", raising=False)
    assert self_refresh_repo_root(fallback=fallback) == fallback.resolve()
    monkeypatch.setenv("DEVTOOL_TRANSACTION_REPO_ROOT", str(transaction))
    assert self_refresh_repo_root(fallback=fallback) == transaction.resolve()


def test_lower_level_transaction_api_remains_direct_without_explicit_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    artifact = _artifact(tmp_path)
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    monkeypatch.delenv("DEVTOOL_TRANSACTION_ISOLATION", raising=False)
    tx = create_apply_transaction(
        root, artifact, artifact_type="overlay",
        preview={"changes": [{"kind": "modified", "path": "tracked.txt"}], "counts": {}},
    )
    assert tx["isolation"]["requested"] == "direct"
    assert tx["isolation"]["effective"] == "direct"
    rollback_transaction(tx["path"], reason="fixture cleanup")


def test_transaction_worktree_fixture_creates_and_removes_real_linked_worktree(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    before = _git(root, "worktree", "list", "--porcelain")
    payload = _fixture_worktree(root)
    after = _git(root, "worktree", "list", "--porcelain")
    assert payload["created"] is True
    assert payload["removed"] is True
    assert payload["head"] == _git(root, "rev-parse", "HEAD")
    assert before == after
    assert payload["fixture_worktree"] not in after


def test_apply_isolation_option_is_exposed_in_checked_in_zsh_completion() -> None:
    root = Path(__file__).resolve().parents[1]
    completion_source = (root / "devtool" / "completion.py").read_text(encoding="utf-8")
    checked_in = (root / "completions" / "_devtool").read_text(encoding="utf-8")
    contract = "--isolation[apply isolation mode]:mode:(worktree direct)"
    assert contract in completion_source
    assert contract in checked_in


def test_tracked_devtool_state_path_still_counts_as_primary_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    operational = root / ".devtool" / "state" / "tracked.txt"
    operational.parent.mkdir(parents=True)
    operational.write_text("baseline\n", encoding="utf-8")
    _git(root, "add", str(operational.relative_to(root)))
    _git(root, "commit", "-qm", "track operational fixture")
    artifact = _artifact(tmp_path)
    _configure(monkeypatch, tmp_path)

    result = apply_overlay(root, str(artifact), yes=True, skip_artifact_validation=True)
    operational.write_text("user drift\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Primary checkout worktree changed"):
        mark_transaction_completed(result.metadata["transaction_path"])
    rollback = rollback_transaction(result.metadata["transaction_path"], reason="tracked operational drift")
    assert rollback.returncode == 0
    assert operational.read_text(encoding="utf-8") == "user drift\n"
