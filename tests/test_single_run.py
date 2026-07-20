from __future__ import annotations

from pathlib import Path

import pytest

from devtool.single_run import SingleRunLock, active_run_lock_records


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


def test_lock_record_contains_log_path_and_is_discoverable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    repo = tmp_path / "repo"
    repo.mkdir()
    log_path = tmp_path / "state" / "logs" / "run.log"
    lock = SingleRunLock(repo, command="build", run_id="one", log_path=str(log_path)).acquire()
    try:
        records = active_run_lock_records()
        assert len(records) == 1
        assert records[0]["run_id"] == "one"
        assert records[0]["log_path"] == str(log_path)
        assert records[0]["status"] == "running"
    finally:
        lock.release()


def test_released_lock_is_not_reported_even_while_owner_pid_is_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    repo = tmp_path / "repo"
    repo.mkdir()
    lock = SingleRunLock(repo, command="build", run_id="released").acquire()
    lock.release()

    assert active_run_lock_records() == []
