from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

from devtool.artifacts import artifact_commit_policy
from devtool.cli import _log_identifier_for_args, build_parser
from devtool import log_commands, logstore


def test_overlay_log_filename_includes_sanitized_artifact_stem(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(logstore, "state_dir", lambda: tmp_path / "state")
    logstore.set_log_dir_override(None)
    path = logstore.new_log_path("studyflow ov17+fix.zip")
    assert path.parent == tmp_path / "state" / "logs"
    assert path.name.endswith("-studyflow-ov17-fix.log")


def test_log_dir_runtime_override_beats_environment(monkeypatch, tmp_path: Path) -> None:
    env_dir = tmp_path / "env"
    cli_dir = tmp_path / "cli"
    monkeypatch.setenv("DEVTOOL_LOG_DIR", str(env_dir))
    logstore.set_log_dir_override(cli_dir)
    try:
        assert logstore.new_log_path().parent == cli_dir
    finally:
        logstore.set_log_dir_override(None)
    assert logstore.new_log_path().parent == env_dir


def test_parser_exposes_global_log_dir() -> None:
    args = build_parser().parse_args(["--log-dir", "/tmp/devtool-logs", "history"])
    assert args.log_dir == "/tmp/devtool-logs"


def test_apply_log_identifier_uses_overlay_name() -> None:
    args = argparse.Namespace(command="apply-overlay", artifact="/sdcard/Download/devtool_ov17_logging_v1.zip")
    assert _log_identifier_for_args(args) == "devtool_ov17_logging_v1.zip"


def test_log_listing_uses_phone_mirror_only_as_fallback(monkeypatch, tmp_path: Path) -> None:
    state = tmp_path / "state"
    primary = state / "logs" / "20260907-100000-000001-primary.log"
    primary.parent.mkdir(parents=True)
    primary.write_text("primary", encoding="utf-8")
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / "20260907-110000-000001-mirror.log").write_text("mirror", encoding="utf-8")
    monkeypatch.setattr(logstore, "state_dir", lambda: state)
    logstore.set_log_dir_override(None)
    monkeypatch.setattr(log_commands, "_candidate_state_roots", lambda: [state])
    monkeypatch.setattr(log_commands, "_phone_log_mirror_dir", lambda: mirror)
    assert log_commands._all_log_paths() == [primary]
    primary.unlink()
    assert log_commands._all_log_paths() == [mirror / "20260907-110000-000001-mirror.log"]


def test_phone_mirror_uses_same_retention_policy(monkeypatch, tmp_path: Path) -> None:
    state = tmp_path / "state"
    mirror = tmp_path / "mirror"
    monkeypatch.setattr(logstore, "state_dir", lambda: state)
    logstore.set_log_dir_override(None)
    monkeypatch.setenv("DEVTOOL_LOG_MAX_FILES", "2")
    monkeypatch.setenv("DEVTOOL_LOG_MAX_TOTAL_MB", "100")
    monkeypatch.setenv("DEVTOOL_PHONE_LOG_MIRROR_DIR", str(mirror))
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    for index in range(4):
        path = state / "logs" / f"20260907-10000{index}-000001.log"
        logstore.write_log(str(index), path)
    assert len(list(mirror.glob("*.log"))) <= 2


def test_fzf_keeps_stderr_attached(monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(log_commands.shutil, "which", lambda name: "/bin/fzf" if name == "fzf" else None)
    monkeypatch.setattr(log_commands.sys.stdin, "isatty", lambda: True)

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="  1  entry\n")

    monkeypatch.setattr(log_commands.subprocess, "run", fake_run)
    assert log_commands._choose_with_fzf(["  1  entry"]) == 1
    assert seen.get("stdout") is subprocess.PIPE
    assert "stderr" not in seen
    assert "capture_output" not in seen


def test_bat_log_viewer_uses_auto_paging(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_text("x", encoding="utf-8")
    monkeypatch.setattr(log_commands.shutil, "which", lambda name: "/bin/bat" if name == "bat" else None)
    calls = []
    monkeypatch.setattr(log_commands.subprocess, "call", lambda argv: calls.append(argv) or 0)
    assert log_commands.open_log(path) == 0
    assert calls == [["bat", "--paging=auto", str(path)]]


def test_commit_message_implies_enabled_unless_explicitly_disabled() -> None:
    assert artifact_commit_policy({"apply": {"commit": {"message": "Apply it"}}})[:2] == (True, "Apply it")
    assert artifact_commit_policy({"commit_message": "Legacy apply"})[:2] == (True, "Legacy apply")
    assert artifact_commit_policy({"apply": {"commit": {"enabled": False, "message": "Do not apply"}}})[0] is False


def test_commit_policy_accepts_legacy_top_level_commit_object() -> None:
    assert artifact_commit_policy({"commit": {"enabled": True, "message": "Legacy object"}}) == (
        True,
        "Legacy object",
        "single",
    )


def test_commit_policy_accepts_legacy_git_commit_object() -> None:
    assert artifact_commit_policy({"git": {"commit": True, "message": "Legacy git"}}) == (
        True,
        "Legacy git",
        "single",
    )


def test_commit_policy_precedence_prefers_apply_commit() -> None:
    assert artifact_commit_policy({
        "apply": {"commit": {"enabled": False, "message": "Apply wins"}},
        "commit": {"enabled": True, "message": "Top-level loses"},
        "git": {"commit": True, "message": "Git loses"},
        "commit_message": "Fallback loses",
    }) == (False, "Apply wins", "single")


def test_commit_policy_prefers_top_level_commit_over_git_and_commit_message() -> None:
    assert artifact_commit_policy({
        "commit": {"enabled": True, "message": "Top-level wins", "strategy": "series"},
        "git": {"commit": True, "message": "Git loses"},
        "commit_message": "Fallback loses",
    }) == (True, "Top-level wins", "series")


def test_commit_policy_prefers_git_over_commit_message() -> None:
    assert artifact_commit_policy({
        "git": {"commit": True, "message": "Git wins"},
        "commit_message": "Fallback loses",
    }) == (True, "Git wins", "single")
