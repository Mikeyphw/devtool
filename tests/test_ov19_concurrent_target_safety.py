from __future__ import annotations

import os
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest

from devtool.reservations import paths_overlap
from devtool.single_run import SingleRunLock, active_run_lock_records
from devtool.transactions import (
    create_apply_transaction,
    find_active_transactions,
    read_transaction,
    register_transaction_paths,
    rollback_transaction,
)


def _artifact(path: Path, name: str, body: str = "new\n") -> Path:
    artifact = path / f"{name}.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr(name, body)
    return artifact


def _init_git(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Devtool Tests"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "devtool-tests@example.invalid"], cwd=repo, check=True)
    (repo / "a.txt").write_text("a-old\n", encoding="utf-8")
    (repo / "b.txt").write_text("b-old\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt", "b.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)


def test_path_overlap_is_component_aware() -> None:
    assert paths_overlap("feature/workspace", "feature/workspace/Foo.kt")
    assert paths_overlap("feature/workspace/Foo.kt", "feature/workspace/Foo.kt")
    assert not paths_overlap("feature/a/Foo.kt", "feature/ab/Foo.kt")


def test_different_targets_with_disjoint_write_sets_can_run_together(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    first = SingleRunLock(
        repo, command="apply-overlay", run_id="android-run", target="android",
        reserved_paths=("feature/android/Main.kt",), paths_known=True,
    ).acquire()
    try:
        second = SingleRunLock(
            repo, command="apply-overlay", run_id="web-run", target="web",
            reserved_paths=("web/src/main.ts",), paths_known=True,
        ).acquire()
        try:
            records = active_run_lock_records()
            assert {item["run_id"] for item in records} == {"android-run", "web-run"}
        finally:
            second.release()
    finally:
        first.release()


@pytest.mark.parametrize(
    ("second_target", "second_path", "message"),
    [
        ("web", "feature/android/Main.kt", "Path conflict"),
        ("web", "feature/android", "Path conflict"),
        ("android", "web/src/main.ts", "same-target"),
    ],
)
def test_overlapping_or_same_target_runs_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    second_target: str,
    second_path: str,
    message: str,
) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    first = SingleRunLock(
        repo, run_id="one", target="android",
        reserved_paths=("feature/android/Main.kt",), paths_known=True,
    ).acquire()
    try:
        with pytest.raises(BlockingIOError, match=message):
            SingleRunLock(
                repo, run_id="two", target=second_target,
                reserved_paths=(second_path,), paths_known=True,
            ).acquire()
    finally:
        first.release()


def test_unknown_write_set_remains_exclusive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    first = SingleRunLock(repo, run_id="one", target="android", reserved_paths=("a.txt",), paths_known=True).acquire()
    try:
        with pytest.raises(BlockingIOError, match="unknown write set"):
            SingleRunLock(repo, run_id="two", target="web", paths_known=False).acquire()
    finally:
        first.release()


def test_transactions_use_independent_active_markers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("a-old\n", encoding="utf-8")
    (repo / "b.txt").write_text("b-old\n", encoding="utf-8")

    monkeypatch.setenv("DEVTOOL_ACTIVE_TARGET", "android")
    monkeypatch.setenv("DEVTOOL_RUN_ID", "run-a")
    tx_a = create_apply_transaction(repo, _artifact(tmp_path, "a.txt"), artifact_type="overlay", preview={"changes": [{"kind": "modified", "path": "a.txt"}]})
    monkeypatch.setenv("DEVTOOL_ACTIVE_TARGET", "web")
    monkeypatch.setenv("DEVTOOL_RUN_ID", "run-b")
    tx_b = create_apply_transaction(repo, _artifact(tmp_path, "b.txt"), artifact_type="overlay", preview={"changes": [{"kind": "modified", "path": "b.txt"}]})

    active = find_active_transactions(repo, include_live=True)
    assert {read_transaction(item)["transaction_id"] for item in active} == {tx_a["transaction_id"], tx_b["transaction_id"]}
    active_dir = Path(tx_a["path"]).parent.parent / "active"
    assert len(list(active_dir.glob("*.json"))) == 2


def test_transaction_extension_rejects_another_transactions_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("a-old\n", encoding="utf-8")
    (repo / "b.txt").write_text("b-old\n", encoding="utf-8")

    monkeypatch.setenv("DEVTOOL_ACTIVE_TARGET", "android")
    monkeypatch.setenv("DEVTOOL_RUN_ID", "run-a")
    tx_a = create_apply_transaction(repo, _artifact(tmp_path, "a.txt"), artifact_type="overlay", preview={"changes": [{"kind": "modified", "path": "a.txt"}]})
    monkeypatch.setenv("DEVTOOL_ACTIVE_TARGET", "web")
    monkeypatch.setenv("DEVTOOL_RUN_ID", "run-b")
    create_apply_transaction(repo, _artifact(tmp_path, "b.txt"), artifact_type="overlay", preview={"changes": [{"kind": "modified", "path": "b.txt"}]})

    monkeypatch.setenv("DEVTOOL_RUN_ID", "run-a")
    with pytest.raises(RuntimeError, match="Path conflict"):
        register_transaction_paths(tx_a["path"], ["b.txt"])


def test_concurrent_path_scoped_rollback_preserves_other_worktree_and_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git(repo)

    monkeypatch.setenv("DEVTOOL_ACTIVE_TARGET", "android")
    monkeypatch.setenv("DEVTOOL_RUN_ID", "run-a")
    tx_a = create_apply_transaction(repo, _artifact(tmp_path, "a.txt", "a-new\n"), artifact_type="overlay", preview={"changes": [{"kind": "modified", "path": "a.txt"}]})
    monkeypatch.setenv("DEVTOOL_ACTIVE_TARGET", "web")
    monkeypatch.setenv("DEVTOOL_RUN_ID", "run-b")
    tx_b = create_apply_transaction(repo, _artifact(tmp_path, "b.txt", "b-new\n"), artifact_type="overlay", preview={"changes": [{"kind": "modified", "path": "b.txt"}]})

    (repo / "a.txt").write_text("a-new\n", encoding="utf-8")
    (repo / "b.txt").write_text("b-new\n", encoding="utf-8")
    subprocess.run(["git", "add", "b.txt"], cwd=repo, check=True)
    cached_before = subprocess.check_output(["git", "diff", "--cached", "--binary", "--", "b.txt"], cwd=repo)

    monkeypatch.setenv("DEVTOOL_RUN_ID", "rollback-a")
    result = rollback_transaction(tx_a["path"], reason="concurrent rollback test")

    assert result.returncode == 0, result.output
    assert "git_state: path-scoped-concurrent" in result.output
    assert (repo / "a.txt").read_text(encoding="utf-8") == "a-old\n"
    assert (repo / "b.txt").read_text(encoding="utf-8") == "b-new\n"
    assert subprocess.check_output(["git", "diff", "--cached", "--binary", "--", "b.txt"], cwd=repo) == cached_before
    assert Path(tx_b["path"]).exists()
