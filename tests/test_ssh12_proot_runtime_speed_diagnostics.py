from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import importlib.util
import os
import subprocess

import pytest

from devtool import ssh_workspace as ws
from devtool import ssh_transport as tr
from devtool import ssh_qualification as qa


def test_single_inventory_skips_nested_repositories_and_mutable_state(tmp_path, monkeypatch):
    for name in ('app/code.py', 'vendor/project/.git/config', 'vendor/project/deep/code.py',
                 'tests/test_app.py', '.devtool/private/catalog.json'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('hello', encoding='utf-8')
    # Observe that we only traverse once, not twice for nested discovery and stats.
    real_walk = tr.os.walk
    scanned = []
    def traced(root, *args, **kwargs):
        scanned.append(root)
        return real_walk(root, *args, **kwargs)
    monkeypatch.setattr(tr.os, 'walk', traced)
    report = tr.source_stats(tmp_path, ('/.devtool/private/',))
    assert report.nested_git_excludes == ('vendor/project/***',)
    assert report.files == 2
    assert len(scanned) == 1


def test_source_inventory_respects_symlink_escape(tmp_path):
    (tmp_path/'safe.py').write_text('x')
    (tmp_path/'leak').symlink_to('/no-such-private-path')
    report = tr.source_stats(tmp_path, ())
    assert report.unsafe_symlinks == ('leak',)


def test_rsync_uses_precomputed_nested_git_boundaries(monkeypatch, tmp_path):
    monkeypatch.setattr(ws, '_rsync_executable', lambda: 'rsync')
    monkeypatch.setattr(ws, '_rsync_remote_shell', lambda cfg: 'ssh')
    monkeypatch.setattr(ws, 'source_stats', lambda *_: (_ for _ in ()).throw(AssertionError('duplicate tree scan')))
    cfg = SimpleNamespace(destination='test@localhost')
    spec = SimpleNamespace(repo_root=str(tmp_path), remote_path='/root/mirror', delete=True,
                           excludes=ws._DEFAULT_EXCLUDES)
    worker = SimpleNamespace(tools={'rsync':'rsync'})
    argv = ws._rsync_argv(cfg, spec, dry_run=True, worker=worker, nested_git_excludes=('subproject/***',))
    assert 'subproject/***' in argv
    assert '--omit-dir-times' in argv
    assert '--delete-delay' in argv
    assert '--dry-run' in argv


def test_seed_plan_accepts_inventory_to_avoid_repeating_walk(monkeypatch, tmp_path):
    stats = SimpleNamespace(bytes=40, files=2, unsafe_symlinks=(), nested_git_excludes=('child/***',))
    monkeypatch.setattr(tr, 'source_stats', lambda *_: (_ for _ in ()).throw(AssertionError('repeat scan')))
    monkeypatch.setattr(tr, '_local_tool', lambda _: '')
    spec = SimpleNamespace(repo_root=str(tmp_path), excludes=(), mode='managed')
    status = SimpleNamespace(exists=True)
    worker = SimpleNamespace(tools={'tar': '', 'zstd': ''})
    planned = tr.seed_plan(spec, status, worker, stats=stats)
    assert planned.nested_git_excludes == ('child/***',)
    assert not planned.use_archive


@pytest.mark.parametrize('name, expected', [
    ('clean', '(no itemized details)'),
    ('changes', '<f.st...... .devtool/private/catalog.json; .d..t...... ./'),
])
def test_sync_diff_diagnostics(name, expected):
    plan = {'changes': []}
    if name == 'changes':
        plan['changes'] = [
            {'code': '<f.st......', 'path': '.devtool/private/catalog.json'},
            {'code': '.d..t......', 'path': './'},
        ]
    assert qa._sync_difference_detail(plan) == expected


def test_sync_diff_diagnostics_truncates_untrusted_strings():
    plan = {'changes': [{'code':'<f', 'path':'hello\nEVIL'}]*20}
    output = qa._sync_difference_detail(plan)
    assert '\n' not in output
    assert '... and 14 more' in output
    assert len(output) < 1300


def test_service_manager_refuses_stopping_unknown_guest_jobs(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'devtool-proot-worker.py'
    spec = importlib.util.spec_from_file_location('rs12_service', path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, 'status', lambda *_: {
        'guest_tmux_sessions': None, 'sessions': {mod.AGENT: True, mod.BRIDGE: True}})
    with pytest.raises(mod.Refusal, match='Cannot inspect guest jobs'):
        mod.stop(tmp_path, 2223, tmp_path / 'key', confirmed=True, force=False)
    with pytest.raises(mod.Refusal, match='requires --yes'):
        mod.stop(tmp_path, 2223, tmp_path / 'key', confirmed=False, force=True)


def test_service_manager_refuses_live_sessions(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'devtool-proot-worker.py'
    spec = importlib.util.spec_from_file_location('rs12_service_two', path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, 'status', lambda *_: {
        'guest_tmux_sessions': ['devtool-live-run'], 'sessions': {mod.AGENT: True, mod.BRIDGE: True}})
    with pytest.raises(mod.Refusal, match='may contain active work'):
        mod.stop(tmp_path, 2223, tmp_path / 'key', confirmed=True, force=False)
