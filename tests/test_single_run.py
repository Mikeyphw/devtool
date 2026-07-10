from __future__ import annotations

from pathlib import Path

import pytest

from devtool.single_run import SingleRunLock


def test_second_run_for_same_repository_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    repo.mkdir()
    first = SingleRunLock(repo, command="build", run_id="one").acquire()
    try:
        with pytest.raises(BlockingIOError, match="already active"):
            SingleRunLock(repo, command="test", run_id="two").acquire()
    finally:
        first.release()


def test_lock_can_be_reacquired_after_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    repo.mkdir()
    first = SingleRunLock(repo).acquire()
    first.release()
    second = SingleRunLock(repo).acquire()
    second.release()


def test_separate_repositories_use_separate_serial_locks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "state"))
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    with SingleRunLock(repo_a), SingleRunLock(repo_b):
        pass
