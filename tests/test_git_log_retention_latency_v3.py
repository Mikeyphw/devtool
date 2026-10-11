from __future__ import annotations

import json
from pathlib import Path

import pytest

from devtool import logstore, log_retention_worker as worker


def test_async_writes_and_schedules_retention_without_blocking_life(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / 'logs'
    root.mkdir()
    target = root / 'new.log'
    monkeypatch.setenv('DEVTOOL_LOG_RETENTION_MODE', 'async')
    monkeypatch.setenv('DEVTOOL_LOG_DIR', str(root))
    monkeypatch.delenv('PREFIX', raising=False)
    monkeypatch.delenv('TERMUX_VERSION', raising=False)
    calls = []
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *a, **kw: calls.append((a,kw)))
    monkeypatch.setattr(logstore, '_rotate_log_root', lambda *a, **kw: pytest.fail('synchronous LIFE blocked log write'))
    assert logstore.write_log('safe text', target) == target
    assert target.read_text() == 'safe text'
    assert len(calls) == 1
    request = json.loads((root / worker._REQUEST).read_text())
    assert request['protected'] == 'new.log'
    assert calls[0][0][0][1:3] == ['-m', 'devtool.log_retention_worker']
    assert calls[0][1]['start_new_session'] is True


def test_worker_coalesces_racing_requests_and_preserves_last_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / 'logs'
    root.mkdir()
    first, second = root / 'first.log', root / 'second.log'
    first.write_text('a')
    second.write_text('b')
    worker._persist_request(root, first)
    processed = []
    def fake_rotate(path: Path, *, protected: Path | None = None) -> None:
        processed.append(protected)
        if len(processed) == 1:
            worker._persist_request(root, second)
    monkeypatch.setattr(logstore, '_rotate_log_root', fake_rotate)
    assert worker.drain_requests(root) == 0
    assert processed == [first, second]
    assert not (root / worker._REQUEST).exists()
    assert first.exists() and second.exists()


def test_failed_async_spawn_reverts_to_sync_retention(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / 'logs'
    monkeypatch.setenv('DEVTOOL_LOG_RETENTION_MODE', 'async')
    monkeypatch.setenv('DEVTOOL_LOG_DIR', str(root))
    monkeypatch.delenv('TERMUX_VERSION', raising=False)
    monkeypatch.delenv('PREFIX', raising=False)
    def broken(*args, **kwargs):
        raise OSError('not available')
    monkeypatch.setattr(worker.subprocess, 'Popen', broken)
    rotated = []
    monkeypatch.setattr(logstore, '_rotate_log_root', lambda path, *, protected=None: rotated.append((path, protected)))
    target = root / 'fallback.log'
    logstore.write_log('persisted', target)
    assert rotated == [(root, target)]
    assert target.read_text() == 'persisted'


def test_sync_override_keeps_original_retention_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / 'logs'
    monkeypatch.setenv('DEVTOOL_LOG_RETENTION_MODE', 'sync')
    monkeypatch.setenv('DEVTOOL_LOG_DIR', str(root))
    calls = []
    monkeypatch.setattr(logstore, '_rotate_log_root', lambda path, *, protected=None: calls.append(protected))
    target = root / 'a.log'
    logstore.write_log('hi', target)
    assert calls == [target]
    assert not (root / worker._REQUEST).exists()


def test_drain_is_idempotent_and_active_lock_defers_safely(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / 'logs'
    root.mkdir()
    assert worker.drain_requests(root) == 0
    worker._persist_request(root, root / 'new.log')
    lock_fd = worker._lock(root / worker._WORKER_LOCK)
    assert lock_fd is not None
    try:
        assert worker.drain_requests(root) == 0
        assert (root / worker._REQUEST).exists()
    finally:
        worker._unlock(lock_fd)
    monkeypatch.setattr(logstore, '_rotate_log_root', lambda path, *, protected=None: None)
    assert worker.drain_requests(root) == 0
    assert not (root / worker._REQUEST).exists()
