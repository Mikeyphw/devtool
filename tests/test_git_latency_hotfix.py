"""Regression contracts for expensive artifact Git mutation paths on Termux.

These tests do not assert wall-clock thresholds: process startup, /sdcard fsync,
Git hooks and runner load are environment dependent. They check that each hot
path makes the bounded number of calls while preserving Git index semantics.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devtool.core import reuse
from devtool.models import CommandSpec
from devtool import runners


def git(root: Path, *args: str) -> bytes:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)
    return proc.stdout


def initialize(root: Path) -> Path:
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Latency Test")
    git(root, "config", "user.email", "latency@example.invalid")
    (root / "base.txt").write_text("base\n")
    git(root, "add", "base.txt")
    git(root, "commit", "-qm", "baseline")
    return root


@pytest.mark.parametrize(
    ("name", "verb"),
    [("git-add", "add"), ("git-commit", "commit"), ("git-index-reconcile", "reset")],
)
def test_git_mutation_scheduler_is_nonreusable_without_repository_scan(tmp_path, monkeypatch, name, verb):
    root = initialize(tmp_path / "repo")
    def must_not_scan(_root):
        raise AssertionError("Git mutation erroneously scans the complete source repository")
    monkeypatch.setattr(reuse, "conservative_repository_identity", must_not_scan)
    spec = CommandSpec(name, ["git", verb, "--help"])
    fingerprint, contribution, _ = reuse.fingerprint_for_spec(spec, repo_root=root)
    assert fingerprint.reusable is False
    assert contribution.reusable is False
    assert contribution.input_manifest.complete is False
    assert contribution.input_manifest.entries == ()
    assert contribution.environment.reason == "git-mutating-command"


def test_non_git_command_retains_conservative_source_fingerprint(tmp_path, monkeypatch):
    root = initialize(tmp_path / "repo")
    called = []
    original = reuse.conservative_repository_identity
    def traced(path):
        called.append(path)
        return original(path)
    monkeypatch.setattr(reuse, "conservative_repository_identity", traced)
    _, contribution, _ = reuse.fingerprint_for_spec(CommandSpec("build", ["python", "build.py"]), repo_root=root)
    assert called == [root]
    assert contribution.input_manifest.complete is True


def test_batched_preflight_matches_literal_filenames_and_directory(tmp_path, monkeypatch):
    root = initialize(tmp_path / "repo")
    (root / "dir").mkdir()
    weird = "literal[1] with\nnewline.txt"
    for name in (weird, "dir/child.txt", "ordinary.txt"):
        (root / name).write_text("staged\n")
    git(root, "add", "--", weird, "dir/child.txt")
    real_run = runners.subprocess.run
    calls = []
    def tracking_run(*args, **kwargs):
        argv = args[0]
        calls.append(list(argv))
        return real_run(*args, **kwargs)
    monkeypatch.setattr(runners.subprocess, "run", tracking_run)
    protected, error = runners._artifact_staged_paths(root, [weird, "dir", "ordinary.txt"], has_head=True)
    assert error == ""
    assert protected == {weird, "dir"}
    assert len(calls) == 1
    assert "-z" in calls[0] and "--no-renames" in calls[0]


def test_unborn_repository_preflight_uses_staged_index(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    (root / "new.txt").write_text("a\n")
    git(root, "add", "new.txt")
    protected, error = runners._artifact_staged_paths(root, ["new.txt", "missing.txt"], has_head=False)
    assert error == ""
    assert protected == {"new.txt"}


def test_batched_preflight_fails_closed_before_git_commit(tmp_path, monkeypatch):
    root = initialize(tmp_path / "repo")
    (root / "base.txt").write_text("changed\n")
    starting_head = git(root, "rev-parse", "HEAD")
    original = runners._artifact_staged_paths
    def fail(*args, **kwargs):
        return set(), "simulated unreadable index"
    monkeypatch.setattr(runners, "_artifact_staged_paths", fail)
    results, _ = runners.git_commit(root, "should not commit", paths=["base.txt"])
    assert [item.name for item in results] == ["git-index-preflight"]
    assert results[0].returncode != 0
    assert git(root, "rev-parse", "HEAD") == starting_head
    monkeypatch.setattr(runners, "_artifact_staged_paths", original)
