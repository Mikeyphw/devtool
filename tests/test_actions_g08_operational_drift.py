"""G8 cross-campaign qualification: genuine primary drift vs own receipts."""
from __future__ import annotations

import subprocess
from pathlib import Path

from devtool.transaction_worktree import git_status_identity


def _git(root: Path, *args: str) -> None:
    subprocess.run(['git', *args], cwd=root, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / 'repo'
    repo.mkdir()
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.email', 'g8@devtool.invalid')
    _git(repo, 'config', 'user.name', 'G8 Fixture')
    (repo / 'base.txt').write_text('baseline\n')
    _git(repo, 'add', 'base.txt')
    _git(repo, 'commit', '-qm', 'baseline')
    return repo


def _write(repo: Path, rel: str, contents: str = '{}') -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents)


def test_devtool_generated_untracked_run_and_cleanup_receipts_do_not_break_isolated_apply(tmp_path):
    repo = _repo(tmp_path)
    baseline = git_status_identity(repo)
    _write(repo, '.devtool/private/run-v3/catalog.json')
    _write(repo, '.devtool/private/run-v3/session/artifacts/qualification-a1.json')
    _write(repo, '.devtool/private/run-v3/session/artifacts/artifact-execution-a1.json')
    _write(repo, '.devtool/private/cleanup/receipts/cleanup-receipt-a1.json')
    _write(repo, '.devtool/state/execution-state.json')
    _write(repo, '.devtool/reports/validation/run/report.json')
    assert git_status_identity(repo) == baseline


def test_untracked_user_checkout_changes_still_block_integration(tmp_path):
    repo = _repo(tmp_path)
    baseline = git_status_identity(repo)
    _write(repo, '.devtool/private/other/custom.json')
    assert git_status_identity(repo) != baseline
    baseline = git_status_identity(repo)
    _write(repo, 'src/real-user-edit.py', 'print(1)\n')
    assert git_status_identity(repo) != baseline


def test_tracked_receipt_edits_are_never_ignored(tmp_path):
    repo = _repo(tmp_path)
    rel = '.devtool/private/run-v3/catalog.json'
    _write(repo, rel, '{"version":1}')
    _git(repo, 'add', rel)
    _git(repo, 'commit', '-qm', 'tracked receipt')
    baseline = git_status_identity(repo)
    _write(repo, rel, '{"version":2}')
    assert git_status_identity(repo) != baseline
    _git(repo, 'add', rel)
    assert git_status_identity(repo) != baseline


def test_arbitrary_files_outside_narrow_cleanup_receipts_are_not_exempt(tmp_path):
    repo = _repo(tmp_path)
    baseline = git_status_identity(repo)
    _write(repo, '.devtool/private/cleanup/other/foreign-note.txt')
    assert git_status_identity(repo) != baseline
