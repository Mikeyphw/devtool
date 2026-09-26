from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from devtool.cli import (
    _maybe_commit_after_apply,
    _record_transaction_commit_from_results,
    _verify_applied_artifact_effects,
)
from devtool.config import ProjectConfig
from devtool.models import OperationResult


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=root, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    return proc.stdout.strip()


def _repo_with_isolated_worktree(tmp: str) -> tuple[Path, Path, str]:
    root = Path(tmp)
    primary = root / "primary"
    primary.mkdir()
    _git(primary, "init", "-q")
    _git(primary, "config", "user.email", "devtool@example.invalid")
    _git(primary, "config", "user.name", "Devtool Tests")
    (primary / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(primary, "add", "tracked.txt")
    _git(primary, "commit", "-q", "-m", "base")
    start = _git(primary, "rev-parse", "HEAD")
    worktree = root / "transaction"
    _git(primary, "worktree", "add", "-q", "--detach", str(worktree), "HEAD")
    return primary, worktree, start


def _args(worktree: Path) -> SimpleNamespace:
    return SimpleNamespace(
        command="apply-overlay", commit="artifact commit", commit_mode="new",
        fixup_target="", allow_merge_amend=False, allow_published_rewrite=False,
        reset_author=False, signing="off", verbose=False, dry_run=False,
        validate=False, validate_only=False, target="", solution="", artifact="",
        ignore_checksum=False, _devtool_overlay_already_applied=False,
        _devtool_artifact_preview={"changes": [{"kind": "add", "path": "added.txt"}]},
        _devtool_artifact_manifest={}, _devtool_transaction_repo_root=str(worktree),
        _devtool_run_id="test-run",
    )


def test_maybe_commit_stages_action_add_from_transaction_worktree() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        primary, worktree, start = _repo_with_isolated_worktree(tmp)
        (worktree / "added.txt").write_text("new file\n", encoding="utf-8")
        config = ProjectConfig(repo_root=primary, kind="unknown")
        args = _args(worktree)
        initial = OperationResult("apply-overlay", ["apply-overlay"], 0, 0.0, "ok", [])
        results, _ = _maybe_commit_after_apply(config, args, [initial], "")
        assert not [item for item in results if item.returncode != 0]
        assert _git(primary, "rev-parse", "HEAD") == start
        worktree_head = _git(worktree, "rev-parse", "HEAD")
        assert worktree_head != start
        assert _git(worktree, "show", f"{worktree_head}:added.txt") == "new file"


def test_record_transaction_commit_uses_transaction_worktree_head() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        primary, worktree, start = _repo_with_isolated_worktree(tmp)
        (worktree / "added.txt").write_text("new file\n", encoding="utf-8")
        _git(worktree, "add", "added.txt")
        _git(worktree, "commit", "-q", "-m", "transaction result")
        result_head = _git(worktree, "rev-parse", "HEAD")
        assert result_head != start
        committed = OperationResult("git-commit", ["git", "commit"], 0, 0.0, "ok", [])
        captured: dict[str, str] = {}
        def record(_path, commit_hash, **_kwargs):
            captured["commit"] = commit_hash
        transaction = {
            "repo_root": str(primary),
            "isolation": {"effective": "worktree", "execution_root": str(worktree)},
        }
        with patch("devtool.cli.read_transaction", return_value=transaction), patch(
            "devtool.cli.record_transaction_git_commit", side_effect=record
        ):
            _record_transaction_commit_from_results("/tmp/fake-transaction.json", primary, [committed])
        assert captured["commit"] == result_head


def test_apply_verification_reads_transaction_worktree() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        primary, worktree, _ = _repo_with_isolated_worktree(tmp)
        (worktree / "added.txt").write_text("new file\n", encoding="utf-8")
        config = ProjectConfig(repo_root=primary, kind="unknown")
        args = _args(worktree)
        args._devtool_artifact_type = "overlay"
        result = _verify_applied_artifact_effects(config, args)
        assert result is not None
        assert result.returncode == 0, result.output
        assert "1 path checked" in result.output
