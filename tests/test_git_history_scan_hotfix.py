"""Git-only batches must not reload the entire persistent EXO history."""
from pathlib import Path
import subprocess

import pytest

from devtool import execution_scheduler_truth as scheduler
from devtool.core.reuse import is_git_mutation_spec
from devtool.models import CommandSpec, ProjectConfig


def _config(tmp_path: Path) -> ProjectConfig:
    return ProjectConfig(repo_root=tmp_path, kind="unknown")


@pytest.mark.parametrize("name,verb", [
    ("git-add", "add"), ("git-commit", "commit"),
    ("git-index-reconcile", "reset"),
])
def test_nonreusable_git_only_batch_keeps_scheduler_without_history_scan(tmp_path, monkeypatch, name, verb):
    def forbidden(_history_root):
        raise AssertionError("Git-only mutation batch must not scan EXO history")
    monkeypatch.setattr(scheduler.ReuseHistoryIndex, "from_history_directory", forbidden)
    spec = CommandSpec(name, ["git", verb, "--help"])
    decisions = scheduler.decisions_for_specs(_config(tmp_path), [spec])
    decision = decisions[1]
    assert decision.disposition == "run"
    assert decision.authoritative is False
    assert decision.process_executed is True
    assert decision.fingerprint["reusable"] is False
    assert spec.metadata["reuse_contribution"]["environment"]["reason"] == "git-mutating-command"


def test_git_command_guard_blocks_name_or_argv_spoofing(tmp_path, monkeypatch):
    real = scheduler.ReuseHistoryIndex.from_history_directory
    calls = []
    def tracked(path):
        calls.append(path)
        return real(path)
    monkeypatch.setattr(scheduler.ReuseHistoryIndex, "from_history_directory", tracked)
    monkeypatch.setenv("DEVTOOL_EXECUTION_HISTORY_DIR", str(tmp_path / "history"))
    for spec in (
        CommandSpec("git-add", ["python", "-c", "print('not-git')"]),
        CommandSpec("git-add", ["git", "status"]),
        CommandSpec("normal", ["git", "add", "-n"]),
    ):
        assert not is_git_mutation_spec(spec)
        scheduler.decisions_for_specs(_config(tmp_path), [spec])
    assert len(calls) == 3


def test_mixed_git_and_reusable_job_still_loads_history(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVTOOL_EXECUTION_HISTORY_DIR", str(tmp_path / "history"))
    observed=[]
    real = scheduler.ReuseHistoryIndex.from_history_directory
    def tracked(path):
        observed.append(path)
        return real(path)
    monkeypatch.setattr(scheduler.ReuseHistoryIndex, "from_history_directory", tracked)
    specs=[
        CommandSpec("git-commit", ["git", "commit", "--dry-run"]),
        CommandSpec("build", ["python", "--version"], metadata={"reuse_inputs": ["missing.txt"]}),
    ]
    d=scheduler.decisions_for_specs(_config(tmp_path), specs)
    assert len(observed)==1
    assert d[1].disposition == "run"
    assert d[2].disposition == "run"


def test_opt_in_git_trace_preserves_execution_and_reports_stage_boundaries(tmp_path, monkeypatch, capsys):
    from devtool.runners import run_specs
    root = tmp_path / "git-repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "source.txt").write_text("unchanged")
    monkeypatch.setenv("DEVTOOL_GIT_TIMING", "1")
    results, _ = run_specs(_config(root), [
        CommandSpec("git-add", ["git", "add", "--dry-run", "--", "source.txt"])
    ], verbose=False)
    assert results[0].returncode == 0
    assert not subprocess.run(["git", "-C", str(root), "diff", "--cached", "--quiet"]).returncode
    assert "Devtool Git timing: scheduler=" in capsys.readouterr().out
    assert "Devtool Git timing: stage=git-add total=" in results[0].output
    assert "process-start-event=" in results[0].output
