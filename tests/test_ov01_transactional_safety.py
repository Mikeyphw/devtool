from __future__ import annotations

import os
import signal
import shlex
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import pytest

from devtool.models import CommandSpec, ProjectConfig
from devtool.runners import _wait_process_returncode, git_commit, run_specs
from devtool.transactions import checkpoint_transaction_applied, create_apply_transaction, record_transaction_git_commit, rollback_transaction
from devtool.watch import watch_loop
from tests._compat import portable_shell_argv


def _git(root: Path, *args: str, text: bool = True):
    return subprocess.check_output(["git", *args], cwd=root, text=text)


def _run_git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _init_repo(root: Path) -> str:
    _run_git(root, "init", "-q")
    _run_git(root, "config", "user.name", "Devtool OV01 Tests")
    _run_git(root, "config", "user.email", "devtool-ov01@example.invalid")
    (root / "file.txt").write_text("base\n", encoding="utf-8")
    (root / "overlay.txt").write_text("overlay-base\n", encoding="utf-8")
    (root / "user.txt").write_text("user-base\n", encoding="utf-8")
    _run_git(root, "add", "file.txt", "overlay.txt", "user.txt")
    _run_git(root, "commit", "-q", "-m", "base")
    return _git(root, "rev-parse", "HEAD").strip()


def _index_path(root: Path) -> Path:
    raw = _git(root, "rev-parse", "--git-path", "index").strip()
    path = Path(raw)
    return path if path.is_absolute() else root / path


def _index_bytes(root: Path) -> bytes:
    return _index_path(root).read_bytes()


def test_mr001_wait_timeout_records_authoritative_failure_before_term(tmp_path: Path) -> None:
    script = "trap 'exit 0' TERM; printf 'ready\n'; while :; do sleep 1; done"
    proc = subprocess.Popen(portable_shell_argv(script), stdout=subprocess.PIPE, text=True, start_new_session=True)
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "ready"
    assert _wait_process_returncode(proc, timeout=0.05) == 124
    assert getattr(proc, "_devtool_assumed_returncode") == 124


def test_mr001_assumed_supervisory_status_wins_when_term_trapping_child_exits_zero(tmp_path: Path) -> None:
    script = "trap 'exit 0' TERM; printf 'ready\n'; while :; do sleep 1; done"
    proc = subprocess.Popen(portable_shell_argv(script), stdout=subprocess.PIPE, text=True, start_new_session=True)
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "ready"
    setattr(proc, "_devtool_assumed_returncode", 124)
    os.killpg(proc.pid, signal.SIGTERM)
    assert _wait_process_returncode(proc) == 124


@pytest.mark.parametrize(("kind", "expected"), [("timeout", 124), ("resource", 75), ("interrupted", 130)])
def test_mr001_supervisor_stop_reason_survives_term_trapping_zero_exit(
    tmp_path: Path, kind: str, expected: int
) -> None:
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="python")
    script = "trap 'exit 0' TERM; printf 'ready\n'; while :; do sleep 1; done"
    results, _raw = run_specs(
        config,
        [CommandSpec("supervised", portable_shell_argv(script))],
        verbose=False,
        supervisor_probe=lambda: (kind, "OV-01 forced stop"),
    )
    assert results[0].returncode == expected


def test_mr001_idle_timeout_assigns_failure_before_term(tmp_path: Path) -> None:
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="python")
    script = "trap 'exit 0' TERM; printf 'ready\n'; while :; do sleep 1; done"
    with patch("devtool.runners._idle_timeout_for", return_value=0.2):
        results, _raw = run_specs(
            config,
            [CommandSpec("idle", portable_shell_argv(script))],
            verbose=False,
        )
    assert results[0].returncode == 124


def test_mr064_interruption_is_terminal_even_with_continue_on_error(tmp_path: Path) -> None:
    marker = tmp_path / "later-ran.txt"
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="python")
    results, _raw = run_specs(
        config,
        [
            CommandSpec("interrupt", portable_shell_argv("exit 130")),
            CommandSpec("later", portable_shell_argv(f"printf ran > {shlex.quote(str(marker))}")),
        ],
        verbose=False,
        continue_on_error=True,
    )
    assert [item.name for item in results] == ["interrupt"]
    assert results[0].returncode == 130
    assert not marker.exists()


def test_mr064_control_center_cancel_is_terminal_even_if_child_exits_zero(tmp_path: Path) -> None:
    class CancelProgress:
        current_phase: dict | None = None

        def __init__(self) -> None:
            self.requested = True
            self.finished_codes: list[int] = []

        def plan_phases(self, phases, *, append=True) -> None:
            pass

        def phase_started(self, name, *, index, total, argv=None, metadata=None) -> None:
            self.current_phase = {"name": name, "target": ""}

        def progress(self, **kwargs) -> None:
            pass

        def cancel_requested(self) -> bool:
            return self.requested

        def clear_cancel(self) -> None:
            self.requested = False

        def phase_finished(self, result) -> None:
            self.finished_codes.append(result.returncode)

    marker = tmp_path / "later-ran.txt"
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="python")
    progress = CancelProgress()
    script = "trap 'exit 0' INT; printf 'ready\n'; while :; do sleep 1; done"
    results, _raw = run_specs(
        config,
        [
            CommandSpec("cancelled", portable_shell_argv(script)),
            CommandSpec("later", portable_shell_argv(f"printf ran > {shlex.quote(str(marker))}")),
        ],
        verbose=False,
        continue_on_error=True,
        tasker_progress=progress,
    )
    assert [item.name for item in results] == ["cancelled"]
    assert results[0].returncode == 130
    assert progress.finished_codes == [130]
    assert not marker.exists()


def test_mr064_watch_ctrl_c_never_reuses_previous_success(tmp_path: Path) -> None:
    with patch("devtool.watch.time.sleep", side_effect=KeyboardInterrupt):
        assert watch_loop(tmp_path, lambda: 0, interval=0.2) == 130


def test_mr064_watch_stops_immediately_when_child_reports_interrupt(tmp_path: Path) -> None:
    with patch("devtool.watch.snapshot_mtimes", side_effect=AssertionError("watch must stop before snapshotting")):
        assert watch_loop(tmp_path, lambda: 130, interval=0.2) == 130


def test_mr002_unsafe_head_refusal_is_zero_worktree_and_index_mutation(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    artifact = tmp_path / "overlay.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("file.txt", "overlay\n")
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        tx = create_apply_transaction(
            root,
            artifact,
            artifact_type="overlay",
            preview={"changes": [{"kind": "modified", "path": "file.txt"}]},
        )
        tx_path = Path(tx["path"])
        (root / "file.txt").write_text("overlay\n", encoding="utf-8")
        _run_git(root, "add", "file.txt")
        _run_git(root, "commit", "-q", "-m", "devtool apply")
        applied = _git(root, "rev-parse", "HEAD").strip()
        record_transaction_git_commit(tx_path, applied)
        (root / "later.txt").write_text("later\n", encoding="utf-8")
        _run_git(root, "add", "later.txt")
        _run_git(root, "commit", "-q", "-m", "later user commit")

        before_head = _git(root, "rev-parse", "HEAD").strip()
        before_index = _index_bytes(root)
        before_file = (root / "file.txt").read_bytes()
        before_status = _git(root, "status", "--porcelain=v1", "-z", text=False)

        result = rollback_transaction(tx_path, reason="unsafe head must refuse before mutation")

        assert result.returncode == 1
        assert "HEAD changed after the devtool apply commit" in result.output
        assert _git(root, "rev-parse", "HEAD").strip() == before_head
        assert _index_bytes(root) == before_index
        assert (root / "file.txt").read_bytes() == before_file
        assert _git(root, "status", "--porcelain=v1", "-z", text=False) == before_status


def test_mr002_index_only_change_refuses_before_any_rollback_mutation(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    artifact = tmp_path / "overlay.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("file.txt", "overlay\n")
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        tx = create_apply_transaction(
            root, artifact, artifact_type="overlay",
            preview={"changes": [{"kind": "modified", "path": "file.txt"}]},
        )
        tx_path = Path(tx["path"])
        (root / "file.txt").write_text("overlay\n", encoding="utf-8")
        _run_git(root, "add", "file.txt")
        _run_git(root, "commit", "-q", "-m", "devtool apply")
        applied = _git(root, "rev-parse", "HEAD").strip()
        record_transaction_git_commit(tx_path, applied)

        # User stages new work without changing HEAD. Rollback must not silently
        # replace that live index with the transaction-start backup.
        (root / "user.txt").write_text("new staged user work\n", encoding="utf-8")
        _run_git(root, "add", "user.txt")
        before_head = _git(root, "rev-parse", "HEAD").strip()
        before_index = _index_bytes(root)
        before_file = (root / "file.txt").read_bytes()
        before_status = _git(root, "status", "--porcelain=v1", "-z", text=False)

        result = rollback_transaction(tx_path, reason="index-only user change must refuse")

        assert result.returncode == 1
        assert "repository index changed after the transaction checkpoint" in result.output
        assert _git(root, "rev-parse", "HEAD").strip() == before_head
        assert _index_bytes(root) == before_index
        assert (root / "file.txt").read_bytes() == before_file
        assert _git(root, "status", "--porcelain=v1", "-z", text=False) == before_status


def test_mr002_uncommitted_transaction_index_change_also_refuses_zero_mutation(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    artifact = tmp_path / "overlay.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("file.txt", "overlay\n")
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        tx = create_apply_transaction(
            root, artifact, artifact_type="overlay",
            preview={"changes": [{"kind": "modified", "path": "file.txt"}]},
        )
        tx_path = Path(tx["path"])
        (root / "file.txt").write_text("overlay\n", encoding="utf-8")
        (root / "user.txt").write_text("staged while transaction active\n", encoding="utf-8")
        _run_git(root, "add", "user.txt")
        before_index = _index_bytes(root)
        before_file = (root / "file.txt").read_bytes()

        result = rollback_transaction(tx_path, reason="uncommitted index change must refuse")

        assert result.returncode == 1
        assert "repository index changed after the transaction checkpoint" in result.output
        assert _index_bytes(root) == before_index
        assert (root / "file.txt").read_bytes() == before_file


def test_mr002_successful_rollback_restores_content_mode_head_and_index(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    (root / "file.txt").chmod(0o755)
    _run_git(root, "add", "file.txt")
    _run_git(root, "commit", "-q", "-m", "executable baseline")
    initial_head = _git(root, "rev-parse", "HEAD").strip()
    initial_index = _index_bytes(root)
    initial_bytes = (root / "file.txt").read_bytes()
    initial_mode = (root / "file.txt").stat().st_mode & 0o777
    artifact = tmp_path / "overlay.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("file.txt", "overlay\n")
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        tx = create_apply_transaction(
            root, artifact, artifact_type="overlay",
            preview={"changes": [{"kind": "modified", "path": "file.txt"}]},
        )
        tx_path = Path(tx["path"])
        (root / "file.txt").write_text("overlay\n", encoding="utf-8")
        (root / "file.txt").chmod(0o600)
        _run_git(root, "add", "file.txt")
        _run_git(root, "commit", "-q", "-m", "devtool apply")
        record_transaction_git_commit(tx_path, _git(root, "rev-parse", "HEAD").strip())

        result = rollback_transaction(tx_path, reason="restore exact baseline")

        assert result.returncode == 0
        assert _git(root, "rev-parse", "HEAD").strip() == initial_head
        assert _index_bytes(root) == initial_index
        assert (root / "file.txt").read_bytes() == initial_bytes
        assert (root / "file.txt").stat().st_mode & 0o777 == initial_mode
        assert _git(root, "status", "--porcelain=v1").strip() == ""


def test_mr003_artifact_commit_uses_isolated_index_and_preserves_user_stage(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    base = _init_repo(root)
    (root / "user.txt").write_text("user-staged\n", encoding="utf-8")
    _run_git(root, "add", "user.txt")
    before_user_index = _git(root, "ls-files", "-s", "--", "user.txt").strip()
    (root / "overlay.txt").write_text("overlay-new\n", encoding="utf-8")

    results, _raw = git_commit(root, "artifact only", paths=["overlay.txt"])

    assert all(item.returncode == 0 for item in results)
    head = _git(root, "rev-parse", "HEAD").strip()
    assert head != base
    committed = _git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()
    assert committed == ["overlay.txt"]
    staged = _git(root, "diff", "--cached", "--name-only").splitlines()
    assert staged == ["user.txt"]
    assert _git(root, "ls-files", "-s", "--", "user.txt").strip() == before_user_index
    assert _git(root, "status", "--short", "--", "overlay.txt").strip() == ""


def test_mr003_overlapping_user_stage_on_artifact_path_is_preserved(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    (root / "user.txt").write_text("unrelated user stage\n", encoding="utf-8")
    _run_git(root, "add", "user.txt")
    (root / "overlay.txt").write_text("user staged overlay version\n", encoding="utf-8")
    _run_git(root, "add", "overlay.txt")
    before_overlay_index = _git(root, "ls-files", "-s", "--", "overlay.txt").strip()
    before_user_index = _git(root, "ls-files", "-s", "--", "user.txt").strip()
    (root / "overlay.txt").write_text("artifact version\n", encoding="utf-8")

    results, _raw = git_commit(root, "artifact only", paths=["overlay.txt"])

    assert all(item.returncode == 0 for item in results)
    assert _git(root, "show", "HEAD:overlay.txt") == "artifact version\n"
    assert _git(root, "ls-files", "-s", "--", "overlay.txt").strip() == before_overlay_index
    assert _git(root, "ls-files", "-s", "--", "user.txt").strip() == before_user_index
    staged = set(_git(root, "diff", "--cached", "--name-only").splitlines())
    assert staged == {"overlay.txt", "user.txt"}


def test_mr003_artifact_paths_are_literal_not_git_pathspec_patterns(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    (root / "artifact[1].txt").write_text("base literal\n", encoding="utf-8")
    (root / "artifact1.txt").write_text("base neighbor\n", encoding="utf-8")
    _run_git(root, "add", "artifact[1].txt", "artifact1.txt")
    _run_git(root, "commit", "-q", "-m", "add literal names")
    (root / "artifact[1].txt").write_text("artifact literal change\n", encoding="utf-8")
    (root / "artifact1.txt").write_text("unrelated neighbor change\n", encoding="utf-8")

    results, _raw = git_commit(root, "literal artifact path", paths=["artifact[1].txt"])

    assert all(item.returncode == 0 for item in results)
    committed = _git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()
    assert committed == ["artifact[1].txt"]
    assert _git(root, "status", "--short", "--", "artifact1.txt").strip().endswith("artifact1.txt")


def test_mr003_failed_artifact_commit_restores_user_index_exactly(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    base = _init_repo(root)
    (root / "user.txt").write_text("user-staged\n", encoding="utf-8")
    _run_git(root, "add", "user.txt")
    before_index = _index_bytes(root)
    (root / "overlay.txt").write_text("overlay-new\n", encoding="utf-8")
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    results, _raw = git_commit(root, "must fail", paths=["overlay.txt"])

    assert any(item.returncode != 0 for item in results)
    assert _git(root, "rev-parse", "HEAD").strip() == base
    assert _index_bytes(root) == before_index




def test_mr003_successful_isolated_commit_then_transaction_rollback_restores_user_stage(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    base = _init_repo(root)
    (root / "user.txt").write_text("user-staged\n", encoding="utf-8")
    _run_git(root, "add", "user.txt")
    before_index = _index_bytes(root)
    before_user_index = _git(root, "ls-files", "-s", "--", "user.txt").strip()
    artifact = tmp_path / "overlay.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("overlay.txt", "overlay-new\n")

    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        tx = create_apply_transaction(
            root, artifact, artifact_type="overlay",
            preview={"changes": [{"kind": "modified", "path": "overlay.txt"}]},
        )
        tx_path = Path(tx["path"])
        (root / "overlay.txt").write_text("overlay-new\n", encoding="utf-8")

        results, _raw = git_commit(root, "artifact only", paths=["overlay.txt"])
        assert all(item.returncode == 0 for item in results)
        applied = _git(root, "rev-parse", "HEAD").strip()
        assert applied != base
        assert _git(root, "ls-files", "-s", "--", "user.txt").strip() == before_user_index
        record_transaction_git_commit(tx_path, applied)

        result = rollback_transaction(tx_path, reason="rollback isolated artifact commit")

        assert result.returncode == 0
        assert _git(root, "rev-parse", "HEAD").strip() == base
        assert (root / "overlay.txt").read_text(encoding="utf-8") == "overlay-base\n"
        assert _index_bytes(root) == before_index
        assert _git(root, "ls-files", "-s", "--", "user.txt").strip() == before_user_index
        assert _git(root, "diff", "--cached", "--name-only").splitlines() == ["user.txt"]


def test_mr003_failed_isolated_commit_then_transaction_rollback_restores_user_stage(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    base = _init_repo(root)
    (root / "user.txt").write_text("user-staged\n", encoding="utf-8")
    _run_git(root, "add", "user.txt")
    before_index = _index_bytes(root)
    before_user_index = _git(root, "ls-files", "-s", "--", "user.txt").strip()
    artifact = tmp_path / "overlay.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("overlay.txt", "overlay-new\n")

    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        tx = create_apply_transaction(
            root, artifact, artifact_type="overlay",
            preview={"changes": [{"kind": "modified", "path": "overlay.txt"}]},
        )
        tx_path = Path(tx["path"])
        (root / "overlay.txt").write_text("overlay-new\n", encoding="utf-8")
        hook = root / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)

        results, _raw = git_commit(root, "must fail", paths=["overlay.txt"])
        assert any(item.returncode != 0 for item in results)
        assert _git(root, "rev-parse", "HEAD").strip() == base
        assert _index_bytes(root) == before_index

        result = rollback_transaction(tx_path, reason="rollback after isolated commit failure")

        assert result.returncode == 0
        assert _git(root, "rev-parse", "HEAD").strip() == base
        assert (root / "overlay.txt").read_text(encoding="utf-8") == "overlay-base\n"
        assert _index_bytes(root) == before_index
        assert _git(root, "ls-files", "-s", "--", "user.txt").strip() == before_user_index
        assert _git(root, "diff", "--cached", "--name-only").splitlines() == ["user.txt"]

def test_mr085_resume_refuses_mode_only_edit_after_failed_rollback(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    artifact = tmp_path / "overlay.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("file.txt", "overlay\n")
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        tx = create_apply_transaction(
            root, artifact, artifact_type="overlay",
            preview={"changes": [{"kind": "modified", "path": "file.txt"}]},
        )
        tx_path = Path(tx["path"])
        (root / "file.txt").write_text("overlay\n", encoding="utf-8")
        checkpoint_transaction_applied(tx_path)
        with patch("devtool.transactions._copy_path", side_effect=RuntimeError("forced rollback staging failure")):
            first = rollback_transaction(tx_path, reason="forced failure")
        assert first.returncode == 1

        original_bytes = (root / "file.txt").read_bytes()
        before_mode = (root / "file.txt").stat().st_mode & 0o777
        changed_mode = 0o600 if before_mode != 0o600 else 0o644
        (root / "file.txt").chmod(changed_mode)
        second = rollback_transaction(tx_path, reason="resume after mode-only user edit")

        assert second.returncode == 1
        assert "no longer matches its durable checkpoint" in second.output
        assert (root / "file.txt").read_bytes() == original_bytes
        assert (root / "file.txt").stat().st_mode & 0o777 == changed_mode


def test_mr085_resume_refuses_user_edit_after_failed_rollback(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    artifact = tmp_path / "overlay.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("file.txt", "overlay\n")
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        tx = create_apply_transaction(
            root,
            artifact,
            artifact_type="overlay",
            preview={"changes": [{"kind": "modified", "path": "file.txt"}]},
        )
        tx_path = Path(tx["path"])
        (root / "file.txt").write_text("overlay\n", encoding="utf-8")
        checkpoint_transaction_applied(tx_path)

        with patch("devtool.transactions._copy_path", side_effect=RuntimeError("forced rollback staging failure")):
            first = rollback_transaction(tx_path, reason="forced failure")
        assert first.returncode == 1

        (root / "file.txt").write_text("user edit after failure\n", encoding="utf-8")
        before = (root / "file.txt").read_bytes()
        second = rollback_transaction(tx_path, reason="resume")

        assert second.returncode == 1
        assert "no longer matches its durable checkpoint" in second.output
        assert (root / "file.txt").read_bytes() == before
