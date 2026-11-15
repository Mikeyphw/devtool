"""Current delivery-session safety and recovery behavior (no historical phase pins)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from devtool.delivery_session import (DeliverySessionError, plan, start, status,
                                      resume, record_intent, report)
from devtool.delivery_cli import run_delivery_command


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], check=True,
                            capture_output=True, text=True)
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    p = tmp_path / "source"
    p.mkdir()
    git(p, "init", "-b", "main")
    git(p, "config", "user.name", "Test")
    git(p, "config", "user.email", "test@example.invalid")
    (p / "sample.txt").write_text("one\n")
    git(p, "add", ".")
    git(p, "commit", "-m", "init")
    git(p, "checkout", "-b", "feature/test")
    git(p, "remote", "add", "origin", "git@github.com:Mikeyphw/devtool.git")
    return p


def test_plan_read_only_and_start_private_journal(repo, tmp_path):
    state = tmp_path / "state"
    overlay = tmp_path / "overlay.zip"
    overlay.write_bytes(b"overlay-example")
    p = plan(repo, overlay=overlay)
    assert p["head"] == "feature/test" and p["stage"] == "PLANNED"
    assert p["repository"] == "Mikeyphw/devtool"
    assert p["overlay"]["sha256"] and not state.exists()
    saved = start(repo, overlay=overlay, state_root=state)
    assert saved["head_sha"] == p["head_sha"]
    journal = state / (saved["id"] + ".json")
    assert journal.is_file() and journal.stat().st_mode & 0o777 == 0o600
    assert state.stat().st_mode & 0o777 == 0o700
    assert status(saved["id"], state_root=state)["resumable"] is True
    assert report(saved["id"], state_root=state)["delivery_completed"] is False


def test_restart_resume_inspection_does_not_replay(repo, tmp_path):
    state = tmp_path / "state"
    identity = start(repo, state_root=state)["id"]
    first = record_intent(identity, "push", "feature/test@sha", approved=True, state_root=state)
    second = record_intent(identity, "push", "feature/test@sha", approved=True, state_root=state)
    assert first["new"] is True and second["new"] is False
    assert first["intent_id"] == second["intent_id"]
    assert second["safe_to_replay"] is False
    resumed = resume(identity, state_root=state)
    assert resumed["operation_replayed"] is False
    assert resumed["disposition"] == "BLOCKED_RECONCILIATION"
    assert len(status(identity, state_root=state)["intents"]) == 1


def test_no_implicit_authorization(repo, tmp_path):
    identity = start(repo, state_root=tmp_path / "state")["id"]
    with pytest.raises(DeliverySessionError, match="Explicit authorization"):
        record_intent(identity, "dispatch", "workflow1", approved=False, state_root=tmp_path / "state")


def test_source_drift_blocks_resume(repo, tmp_path):
    state = tmp_path / "state"
    identity = start(repo, state_root=state)["id"]
    (repo / "sample.txt").write_text("two\n")
    git(repo, "add", "sample.txt")
    git(repo, "commit", "-m", "drift")
    assert resume(identity, state_root=state)["disposition"] == "BLOCKED_RECONCILIATION"
    assert status(identity, state_root=state)["source_binding"] == "drifted"


def test_corruption_and_symlink_fail_closed(repo, tmp_path):
    state = tmp_path / "state"
    identity = start(repo, state_root=state)["id"]
    path = state / (identity + ".json")
    obj = json.loads(path.read_text())
    obj["head_sha"] = "b" * 40
    path.write_text(json.dumps(obj))
    with pytest.raises(DeliverySessionError, match="integrity"):
        status(identity, state_root=state)
    path.unlink()
    path.symlink_to(tmp_path / "other.json")
    with pytest.raises(DeliverySessionError, match="symlink"):
        status(identity, state_root=state)


def test_cli_human_default_and_json_opt_in(repo, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    args = SimpleNamespace(delivery_session_command="start", repo=str(repo), base="main",
                           head=None, overlay=None, json=False)
    assert run_delivery_command(args) == 0
    captured = capsys.readouterr().out
    assert "Devtool delivery: start" in captured and "Release qualification: NOT PERFORMED" in captured
    assert "{\"schema\"" not in captured
    args.delivery_session_command = "plan"
    args.json = True
    assert run_delivery_command(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["remote_verified"] is False


def test_second_distinct_mutation_blocked_until_reconciliation(repo, tmp_path):
    state = tmp_path / "state"
    identity = start(repo, state_root=state)["id"]
    record_intent(identity, "push", "branch@first", approved=True, state_root=state)
    with pytest.raises(DeliverySessionError, match="Unresolved mutation"):
        record_intent(identity, "pr_create", "pr@second", approved=True, state_root=state)


def test_source_drift_blocks_new_mutation_intent(repo, tmp_path):
    state = tmp_path / "state"
    identity = start(repo, state_root=state)["id"]
    (repo / "sample.txt").write_text("changed\n")
    git(repo, "add", "sample.txt")
    git(repo, "commit", "-m", "new")
    with pytest.raises(DeliverySessionError, match="Source drift"):
        record_intent(identity, "push", "branch@different", approved=True, state_root=state)


def test_cli_refuses_session_for_other_repo(repo, tmp_path, capsys, monkeypatch):
    state = tmp_path / "xdg"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    identity = start(repo)["id"]
    other = tmp_path / "other"
    other.mkdir()
    args = SimpleNamespace(delivery_session_command="status", repo=str(other), delivery_id=identity, json=False)
    assert run_delivery_command(args) == 2
    assert "another repository" in capsys.readouterr().err


def test_system_level_symlink_ancestor_does_not_break_termux_like_home(repo, tmp_path):
    target = tmp_path / "real-home"
    target.mkdir()
    alias = tmp_path / "alias-data"
    alias.symlink_to(target, target_is_directory=True)
    state = alias / "files" / "home" / ".local" / "state" / "devtool" / "delivery" / "sessions"
    identity = start(repo, state_root=state)["id"]
    assert status(identity, state_root=state)["resumable"] is True
