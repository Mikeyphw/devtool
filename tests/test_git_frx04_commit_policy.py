from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from zipfile import ZipFile

import pytest

from devtool.cli import build_parser, _validation_apply_context
from devtool.config import ProjectConfig, load_project_config
from devtool.git_commit_policy import (
    GitCommitPolicyError,
    DEVTOOL_NOTES_REF,
    migrate_devtool_note,
    preflight_commit_policy,
    probe_commit_policy,
)
from devtool.runners import git_commit_with_policy
from devtool.transaction_worktree import transaction_execution_root
from devtool.transactions import create_apply_transaction, mark_transaction_completed, read_transaction, record_transaction_git_commit, rollback_transaction

ROOT = Path(__file__).resolve().parents[1]


def _git(root: Path, *args: str, check: bool = True, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, env=env)
    if check and proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return proc.stdout.strip()


def _repo(tmp_path: Path, *, author: str = "Original Author") -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Current User")
    _git(root, "config", "user.email", "current@example.invalid")
    (root / "tracked.txt").write_text("before\n", encoding="utf-8")
    env = os.environ.copy()
    env.update({"GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": "original@example.invalid", "GIT_COMMITTER_NAME": "Current User", "GIT_COMMITTER_EMAIL": "current@example.invalid"})
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-qm", "base", env=env)
    return root


def _artifact(tmp_path: Path) -> Path:
    path = tmp_path / "overlay.zip"
    with ZipFile(path, "w") as zf:
        zf.writestr("tracked.txt", "after\n")
    return path


def _tx(root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, mode: str = "amend") -> dict:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    monkeypatch.setenv("DEVTOOL_RUN_ID", "git-frx04-test")
    monkeypatch.setenv("DEVTOOL_TRANSACTION_COMMIT_MODE", mode)
    monkeypatch.setenv("DEVTOOL_TRANSACTION_ISOLATION", "direct")
    return create_apply_transaction(root, _artifact(tmp_path), artifact_type="overlay", preview={"changes": [{"kind": "modify", "path": "tracked.txt"}], "counts": {"modify": 1}})


def test_cli_exposes_all_commit_policy_modes_and_safety_aliases() -> None:
    parser = build_parser()
    args = parser.parse_args(["apply-overlay", "--commit-mode", "fixup", "--fixup-target", "HEAD~1", "demo.zip"])
    assert args.commit_mode == "fixup"
    assert args.fixup_target == "HEAD~1"
    amend = parser.parse_args(["apply-overlay", "--amend", "--reset-author", "--allow-merge-amend", "--allow-published-rewrite", "demo.zip"])
    assert amend.amend and amend.reset_author and amend.allow_merge_amend and amend.allow_published_rewrite


def test_amend_requires_verified_transaction_backup(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    with pytest.raises(GitCommitPolicyError, match="backup-head"):
        preflight_commit_policy(root, mode="amend", transaction={})


def test_published_amend_refuses_by_default_and_explicit_override_never_pushes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    root = _repo(tmp_path)
    _git(root, "remote", "add", "origin", str(remote))
    branch = _git(root, "branch", "--show-current")
    _git(root, "push", "-q", "-u", "origin", branch)
    start = _git(root, "rev-parse", "HEAD")
    tx = _tx(root, tmp_path, monkeypatch)
    with pytest.raises(GitCommitPolicyError, match="protected upstream/remote ref"):
        preflight_commit_policy(root, mode="amend", transaction=tx)
    evidence = preflight_commit_policy(root, mode="amend", transaction=tx, allow_published_rewrite=True)
    assert evidence.supersedes_commit == start
    assert evidence.published_refs
    (root / "tracked.txt").write_text("after\n", encoding="utf-8")
    results, _ = git_commit_with_policy(root, "", mode="amend", paths=["tracked.txt"])
    assert all(r.returncode == 0 for r in results)
    assert _git(root, "rev-parse", "HEAD") != start
    assert _git(root, "rev-parse", f"origin/{branch}") == start
    rollback_transaction(tx["path"], reason="fixture cleanup")




def test_isolated_amend_runs_on_transaction_worktree_and_integrates_exact_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    start = _git(root, "rev-parse", "HEAD")
    start_parent = _git(root, "rev-parse", "HEAD^") if _git(root, "rev-list", "--count", "HEAD") != "1" else ""
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg-isolated"))
    monkeypatch.setenv("DEVTOOL_RUN_ID", "git-frx04-isolated")
    monkeypatch.setenv("DEVTOOL_TRANSACTION_COMMIT_MODE", "amend")
    monkeypatch.setenv("DEVTOOL_TRANSACTION_ISOLATION", "worktree")
    tx = create_apply_transaction(root, _artifact(tmp_path), artifact_type="overlay", preview={"changes": [{"kind": "modify", "path": "tracked.txt"}], "counts": {"modify": 1}})
    execution_root = transaction_execution_root(tx)
    assert execution_root != root
    assert _git(root, "rev-parse", "HEAD") == start
    evidence = preflight_commit_policy(execution_root, mode="amend", transaction=tx)
    assert evidence.backup_sha == start
    (execution_root / "tracked.txt").write_text("after isolated amend\n", encoding="utf-8")
    results, _ = git_commit_with_policy(execution_root, "", mode="amend", paths=["tracked.txt"])
    assert all(r.returncode == 0 for r in results)
    result = _git(execution_root, "rev-parse", "HEAD")
    assert result != start
    assert _git(root, "rev-parse", "HEAD") == start
    record_transaction_git_commit(tx["path"], result, commit_mode="amend", supersedes_commit=start)
    mark_transaction_completed(tx["path"])
    assert _git(root, "rev-parse", "HEAD") == result
    assert _git(root, "rev-parse", "refs/devtool/backups/git-frx04-isolated/head") == start
    assert (root / "tracked.txt").read_text(encoding="utf-8") == "after isolated amend\n"

def test_merge_amend_refused_unless_explicitly_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    base_branch = _git(root, "branch", "--show-current")
    _git(root, "checkout", "-qb", "side")
    (root / "side.txt").write_text("side\n", encoding="utf-8")
    _git(root, "add", "side.txt"); _git(root, "commit", "-qm", "side")
    _git(root, "checkout", "-q", base_branch)
    (root / "main.txt").write_text("main\n", encoding="utf-8")
    _git(root, "add", "main.txt"); _git(root, "commit", "-qm", "main")
    _git(root, "merge", "--no-ff", "-qm", "merge", "side")
    tx = _tx(root, tmp_path, monkeypatch)
    with pytest.raises(GitCommitPolicyError, match="merge commit"):
        preflight_commit_policy(root, mode="amend", transaction=tx)
    evidence = preflight_commit_policy(root, mode="amend", transaction=tx, allow_merge_amend=True)
    assert evidence.merge_commit is True
    rollback_transaction(tx["path"], reason="fixture cleanup")


def test_amend_preserves_author_unless_reset_requested(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    start_author = _git(root, "show", "-s", "--format=%an <%ae>", "HEAD")
    (root / "tracked.txt").write_text("after\n", encoding="utf-8")
    results, _ = git_commit_with_policy(root, "", mode="amend", paths=["tracked.txt"])
    assert all(r.returncode == 0 for r in results)
    assert _git(root, "show", "-s", "--format=%an <%ae>", "HEAD") == start_author
    (root / "tracked.txt").write_text("after2\n", encoding="utf-8")
    results, _ = git_commit_with_policy(root, "", mode="amend", reset_author=True, paths=["tracked.txt"])
    assert all(r.returncode == 0 for r in results)
    assert _git(root, "show", "-s", "--format=%an <%ae>", "HEAD") == "Current User <current@example.invalid>"


def test_fixup_creates_fixup_commit_without_rewriting_target(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    target = _git(root, "rev-parse", "HEAD")
    (root / "tracked.txt").write_text("after\n", encoding="utf-8")
    results, _ = git_commit_with_policy(root, "", mode="fixup", fixup_target=target, paths=["tracked.txt"])
    assert all(r.returncode == 0 for r in results)
    head = _git(root, "rev-parse", "HEAD")
    assert head != target
    assert _git(root, "rev-parse", "HEAD^") == target
    assert _git(root, "show", "-s", "--format=%s", "HEAD") == "fixup! base"


def test_devtool_note_is_migrated_to_amended_commit_without_deleting_old_note(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    old = _git(root, "rev-parse", "HEAD")
    _git(root, "notes", f"--ref={DEVTOOL_NOTES_REF}", "add", "-m", "run evidence", old)
    (root / "tracked.txt").write_text("after\n", encoding="utf-8")
    git_commit_with_policy(root, "", mode="amend", paths=["tracked.txt"])
    new = _git(root, "rev-parse", "HEAD")
    evidence = migrate_devtool_note(root, old, new)
    assert evidence["migrated"] is True
    assert _git(root, "notes", f"--ref={DEVTOOL_NOTES_REF}", "show", old) == "run evidence"
    assert _git(root, "notes", f"--ref={DEVTOOL_NOTES_REF}", "show", new) == "run evidence"


def test_transaction_journal_records_commit_policy_and_supersession(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    old = _git(root, "rev-parse", "HEAD")
    tx = _tx(root, tmp_path, monkeypatch)
    (root / "tracked.txt").write_text("after\n", encoding="utf-8")
    _git(root, "add", "tracked.txt"); _git(root, "commit", "--amend", "--no-edit", "-q")
    new = _git(root, "rev-parse", "HEAD")
    record_transaction_git_commit(tx["path"], new, commit_mode="amend", supersedes_commit=old, published_rewrite=True, signing_required=True)
    journal = read_transaction(tx["path"])
    assert journal["supersedes_commit"] == old
    assert journal["commit_policy"]["mode"] == "amend"
    assert journal["commit_policy"]["result_commit"] == new
    assert journal["commit_policy"]["automatic_push"] is False
    assert journal["git_recovery"]["commit_policy"]["supersedes_commit"] == old
    rollback_transaction(tx["path"], reason="fixture cleanup")


def test_signing_policy_is_observed_and_commit_runner_does_not_disable_signing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    _git(root, "config", "commit.gpgSign", "true")
    probe = probe_commit_policy(root)
    assert probe["signing"]["required"] is True
    assert probe["automatic_push"] is False
    tx = _tx(root, tmp_path, monkeypatch, mode="new")
    evidence = preflight_commit_policy(root, mode="new", transaction=tx)
    assert evidence.signing_required is True
    rollback_transaction(tx["path"], reason="fixture cleanup")


def test_deferred_validation_context_preserves_commit_policy_fields() -> None:
    args = build_parser().parse_args(["apply-overlay", "--amend", "--reset-author", "--allow-merge-amend", "--allow-published-rewrite", "demo.zip"])
    args.commit_mode = "amend"
    args.commit = ""
    context = _validation_apply_context(args, transaction_path="/tmp/tx.json", manifest={})
    assert context["commit_mode"] == "amend"
    assert context["reset_author"] is True
    assert context["allow_merge_amend"] is True
    assert context["allow_published_rewrite"] is True


def test_native_roadmap_declares_git_frx04_jobs_test_and_workflow() -> None:
    cfg = load_project_config(str(ROOT), None, target_name="devtool")
    target = cfg.targets["devtool"]
    assert target.jobs["commit-policy-fixture"].metadata["roadmap_id"] == "GIT-FRX-04"
    assert target.jobs["commit-policy-fixture"].metadata["read_only"] is True
    assert target.jobs["signing-policy-probe"].metadata["read_only"] is True
    assert "roadmap-git-frx04" in cfg.tests
    assert "roadmap-git-frx04" in target.workflows
    refs = {node.ref.raw for node in __import__("devtool.workflow_runtime", fromlist=["plan_selected_workflow"]).plan_selected_workflow(cfg, "roadmap-git-frx04").nodes}
    assert "job:commit-policy-fixture" in refs
    assert "job:signing-policy-probe" in refs
    assert "test:roadmap-git-frx04" in refs


def test_checked_in_zsh_completion_exposes_commit_policy_surface() -> None:
    for path in (ROOT / "devtool/completion.py", ROOT / "completions/_devtool"):
        text = path.read_text(encoding="utf-8")
        for token in ("--commit-mode", "--amend", "--fixup-target", "--reset-author", "--allow-merge-amend", "--allow-published-rewrite"):
            assert token in text
