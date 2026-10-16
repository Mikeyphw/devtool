"""Phase E02: FS03 may optimize discovery, but not cleanup authority."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from devtool import repository_cleanup as cleanup
from devtool import final_seal as seal


def _fixture(tmp_path: Path) -> Path:
    root = tmp_path / 'source'
    root.mkdir()
    (root / '.git').mkdir()
    (root / '.devtool').mkdir()
    (root / '.git' / '.devtool-python-autofix-backup-secret').mkdir()
    (root / '.devtool' / '.diagnostic-surface-backup-secret').mkdir()
    (root / 'nested' / '.diagnostic-surface-backup-owned').mkdir(parents=True)
    (root / '.warning-fix-backup-root').mkdir()
    (root / '.warning-fix-backup-root' / '.no-device-config-cache-fix-child').mkdir()
    (root / '.archived-backup-heuristic').mkdir()
    (root / 'nested' / 'user-data.backup').write_text('private data')
    (root / 'nested' / 'legacy.before-2023').write_text('user data')
    (root / 'node_modules' / 'pkg').mkdir(parents=True)
    (root / 'node_modules' / 'pkg' / 'artifact.orig').write_text('do not delete')
    return root


def test_fast_discovery_matches_original_eligible_set(tmp_path):
    root = _fixture(tmp_path)
    # Both paths must go through the same LIFE-X03 planner and preserve
    # protected file/directory heuristics, Git exclusions, and nested owners.
    expected = [p.relative_to(root).as_posix() for p in cleanup.discover_backup_candidates(root)]
    fast = [p.relative_to(root).as_posix() for p in cleanup.discover_backup_candidates(root, managed_only=True)]
    assert fast == expected
    assert '.warning-fix-backup-root' in fast
    assert 'nested/.diagnostic-surface-backup-owned' in fast
    assert not any('orig' in p or 'user-data' in p or 'secret' in p for p in fast)


def test_fast_scan_fails_closed_on_nonzero_find(tmp_path, monkeypatch):
    root = _fixture(tmp_path)
    monkeypatch.setattr(cleanup.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=1, stderr=b'permission denied', stdout=b''))
    with pytest.raises(RuntimeError, match='incomplete'):
        cleanup.discover_backup_candidates(root, managed_only=True)
    assert any(row.startswith('backup-scan-error:') for row in seal._workspace_backup_paths(root))


def test_fast_scan_fails_closed_on_timeout(tmp_path, monkeypatch):
    import subprocess
    root = _fixture(tmp_path)
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired('find', 120)
    monkeypatch.setattr(cleanup.subprocess, 'run', timeout)
    with pytest.raises(RuntimeError, match='timed out'):
        cleanup.discover_backup_candidates(root, managed_only=True)


def test_fast_scan_portable_fallback_matches_original(tmp_path, monkeypatch):
    root = _fixture(tmp_path)
    original = cleanup.discover_backup_candidates(root)
    def not_installed(*a, **kw):
        raise FileNotFoundError('find')
    monkeypatch.setattr(cleanup.subprocess, 'run', not_installed)
    assert cleanup.discover_backup_candidates(root, managed_only=True) == original


def test_fs03_requests_managed_only_and_does_not_disable_gate(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    root.mkdir()
    seen = []
    def discover(path, *, managed_only=False):
        seen.append(managed_only)
        return [root / '.devtool-python-autofix-backup-example']
    (root / '.devtool-python-autofix-backup-example').mkdir()
    monkeypatch.setattr(cleanup, 'discover_backup_candidates', discover)
    assert seal._workspace_backup_paths(root) == ['.devtool-python-autofix-backup-example']
    assert seen == [True]


def test_fast_native_scan_avoids_stat_on_irrelevant_files(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    root.mkdir()
    for i in range(2000):
        (root / f'irrelevant_{i:04}.txt').touch()
    (root / '.warning-fix-backup-real').mkdir()
    # In the optimized path only matching directory names are stat'ed.
    count = 0
    actual = Path.is_dir
    def tracked(self):
        nonlocal count
        if self.parent == root:
            count += 1
        return actual(self)
    monkeypatch.setattr(Path, 'is_dir', tracked)
    paths = cleanup._managed_backup_dirs(root)
    assert [p.name for p in paths] == ['.warning-fix-backup-real']
    assert count < 20


def test_no_managed_dirs_avoids_expensive_life_ownership_load(tmp_path, monkeypatch):
    from devtool import cleanup_lifecycle
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'non_eligible_file.bak').write_text('preserve')
    def should_not_execute(*a, **kw):
        raise AssertionError('LIFE view must not open when there are zero managed candidates')
    monkeypatch.setattr(cleanup_lifecycle, 'build_cleanup_ownership_view', should_not_execute)
    assert cleanup.discover_backup_candidates(root, managed_only=True) == []
