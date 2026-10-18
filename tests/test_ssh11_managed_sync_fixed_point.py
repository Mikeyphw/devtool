from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import os
import shutil
import subprocess

import pytest

from devtool import ssh_workspace as ws
from devtool.ssh_transport import _excluded, source_stats


def test_private_runtime_and_python_caches_default_exclusions():
    excludes = ws._DEFAULT_EXCLUDES
    assert '/.devtool/private/' in excludes
    assert '/.devtool/tmp/' in excludes
    assert '__pycache__/' in excludes
    assert '*.py[co]' in excludes
    for rel in (
        '.devtool/private/run-v3/catalog.json',
        '.devtool/tmp/python/pytest-of-user/pytest-0/file.txt',
        'tests/__pycache__/test_module.cpython-314.pyc',
        'tests/.pytest_cache/v/cache/lastfailed',
    ):
        assert _excluded(rel, excludes), rel
    assert not _excluded('devtool/ssh_workspace.py', excludes)
    assert not _excluded('tests/test_functional.py', excludes)


def test_source_stats_exclude_mutable_private_files(tmp_path):
    for rel in ('devtool/ssh_workspace.py', '.devtool/private/run-v3/catalog.json',
                '.devtool/tmp/foo.log', 'tests/__pycache__/x.pyc',
                'tests/test_functional.py'):
        file = tmp_path / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('x', encoding='utf-8')
    stats = source_stats(tmp_path, ws._DEFAULT_EXCLUDES)
    assert stats.files == 2


@pytest.mark.parametrize('dry_run', [False, True])
def test_rsync_argv_ignores_directory_timestamps_but_not_real_changes(monkeypatch, tmp_path, dry_run):
    monkeypatch.setattr(ws, '_rsync_executable', lambda: 'rsync')
    monkeypatch.setattr(ws, '_rsync_remote_shell', lambda config: 'ssh')
    config = SimpleNamespace(destination='fake@example')
    spec = SimpleNamespace(repo_root=str(tmp_path), remote_path='/root/.cache/devtool/workspaces/example',
                           delete=True, excludes=ws._DEFAULT_EXCLUDES)
    worker = SimpleNamespace(tools={'rsync': '/usr/bin/rsync'})
    argv = ws._rsync_argv(config, spec, dry_run=dry_run, worker=worker)
    assert '--omit-dir-times' in argv
    assert '--delete-delay' in argv  # deletion semantics are not disabled
    assert ('--dry-run' in argv) is dry_run
    for pattern in ('/.devtool/private/', '/.devtool/tmp/', '__pycache__/'):
        assert ['--exclude', pattern] == argv[argv.index(pattern)-1:argv.index(pattern)+1]
    assert '--itemize-changes' in argv
    assert '-a' in argv


@pytest.mark.skipif(shutil.which('rsync') is None, reason='rsync unavailable')
def test_real_rsync_fixed_point_ignores_dir_mtime_but_reports_content(tmp_path):
    src = tmp_path / 'src'
    dst = tmp_path / 'dst'
    for p in (src, dst):
        (p / 'pkg').mkdir(parents=True)
        (p / 'pkg' / 'tracked.py').write_text('hello\n', encoding='utf-8')
    # Let both copies have identical file metadata, but diverge directory times.
    for rel in ('pkg/tracked.py',):
        stat = (src / rel).stat()
        os.utime(dst / rel, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    os.utime(src, (1000000000, 1000000000))
    os.utime(dst, (1100000000, 1100000000))
    os.utime(src / 'pkg', (1000000000, 1000000000))
    os.utime(dst / 'pkg', (1100000000, 1100000000))
    base=['rsync', '-a', '--omit-dir-times', '--itemize-changes', '--out-format=%i|%n',
          '--dry-run', f'{src}/', f'{dst}/']
    result = subprocess.run(base, capture_output=True, text=True, check=True)
    assert not result.stdout.strip(), result.stdout
    (src / 'pkg/tracked.py').write_text('changed content\n', encoding='utf-8')
    result = subprocess.run(base, capture_output=True, text=True, check=True)
    assert 'pkg/tracked.py' in result.stdout, result.stdout
