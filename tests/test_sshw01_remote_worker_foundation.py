from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devtool.cli import _maybe_delegate_ssh, build_parser
from devtool.ssh_tmux_runner import SSHRunnerConfig, resolve_ssh_config, ssh_requested, worker_session_name
from devtool import ssh_worker


def _write_config(repo: Path, *, policy: str = "missing") -> None:
    (repo / ".devtool.toml").write_text(
        f'''[project]\nrunner = "python"\n\n[ssh]\nprofile = "tablet"\n\n[ssh.hosts.tablet]\nhost = "10.0.0.22"\nport = 8022\nuser = "termux"\nmode = "commands"\ncwd = "~/Code/example"\n\n[ssh.hosts.tablet.bootstrap]\npolicy = "{policy}"\n\n[ssh.hosts.tablet.worker]\ntmux = true\n''',
        encoding="utf-8",
    )


def test_named_profile_resolves_and_raw_flags_override(tmp_path: Path) -> None:
    _write_config(tmp_path)
    cfg = resolve_ssh_config(
        ["--repo", str(tmp_path), "--ssh-profile", "tablet", "--ssh-user", "override", "--ssh-port", "2222"],
        cwd=str(tmp_path),
        env={},
    )
    assert cfg.profile == "tablet"
    assert cfg.host == "10.0.0.22"
    assert cfg.user == "override"
    assert cfg.port == 2222
    assert cfg.mode == "commands"
    assert cfg.cwd == "~/Code/example"
    assert cfg.bootstrap_policy == "missing"
    assert cfg.worker_tmux is True


def test_default_profile_is_selected_without_profile_flag(tmp_path: Path) -> None:
    _write_config(tmp_path)
    cfg = resolve_ssh_config(["--repo", str(tmp_path)], cwd=str(tmp_path), env={})
    assert cfg.profile == "tablet"
    assert cfg.destination == "termux@10.0.0.22"


def test_unknown_profile_fails_closed_with_available_names(tmp_path: Path) -> None:
    _write_config(tmp_path)
    with pytest.raises(ValueError, match="Available profiles: tablet"):
        resolve_ssh_config(["--repo", str(tmp_path), "--ssh-profile", "missing"], cwd=str(tmp_path), env={})


def test_profile_requests_ssh_without_raw_host(tmp_path: Path) -> None:
    _write_config(tmp_path)
    argv = ["--repo", str(tmp_path), "--ssh-profile", "tablet", "validate"]
    assert ssh_requested(argv, cwd=str(tmp_path), env={}) is True


def test_ssh_management_command_never_recurses_into_delegation(tmp_path: Path) -> None:
    _write_config(tmp_path)
    parser = build_parser()
    args = parser.parse_args(["--repo", str(tmp_path), "ssh", "doctor", "tablet"])
    assert _maybe_delegate_ssh(["--repo", str(tmp_path), "ssh", "doctor", "tablet"], args) is None


def _probe_stdout(*, tmux: str = "/data/data/com.termux/files/usr/bin/tmux", rsync: str = "/data/data/com.termux/files/usr/bin/rsync") -> str:
    return "\n".join(
        [
            "platform=termux",
            "os_name=Linux",
            "architecture=aarch64",
            "cpu_count=8",
            "memory_kib=8388608",
            "free_storage_kib=52428800",
            "home=/data/data/com.termux/files/home",
            "prefix=/data/data/com.termux/files/usr",
            "shell=/data/data/com.termux/files/usr/bin/bash",
            "package_manager=pkg",
            "tool_python=/data/data/com.termux/files/usr/bin/python",
            f"tool_tmux={tmux}",
            f"tool_rsync={rsync}",
            "tool_git=/data/data/com.termux/files/usr/bin/git",
            "tool_java=",
            "tool_gradle=",
            "version_python=Python 3.13.7",
            "version_tmux=tmux 3.5a",
            "version_rsync=rsync version 3.4.1",
            "version_git=git version 2.51.0",
            "version_java=",
            "version_gradle=",
        ]
    ) + "\n"


def test_worker_probe_is_read_only_and_reports_termux(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = SSHRunnerConfig(host="tablet", port=8022, user="termux", profile="tablet", worker_tmux=True)
    calls: list[str] = []

    def fake_run(config: SSHRunnerConfig, script: str, **kwargs):
        calls.append(script)
        return subprocess.CompletedProcess([], 0, stdout=_probe_stdout(), stderr="")

    monkeypatch.setattr(ssh_worker, "_run_remote", fake_run)
    probe = ssh_worker.probe_worker(cfg)
    assert probe.platform == "termux"
    assert probe.architecture == "aarch64"
    assert probe.cpu_count == 8
    assert probe.ready is True
    assert probe.bootstrap_supported is True
    assert len(calls) == 1
    assert "pkg install" not in calls[0]


def test_doctor_marks_missing_tmux_as_bootstrappable(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = SSHRunnerConfig(host="tablet", profile="tablet", worker_tmux=True)
    monkeypatch.setattr(
        ssh_worker,
        "_run_remote",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, stdout=_probe_stdout(tmux=""), stderr=""),
    )
    payload = ssh_worker.worker_doctor_payload(cfg)
    assert payload["healthy"] is False
    assert payload["probe"]["bootstrap_supported"] is True
    assert "tmux" in payload["probe"]["missing_required"]


def test_bootstrap_dry_run_installs_only_missing_termux_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = SSHRunnerConfig(host="tablet", profile="tablet", bootstrap_policy="missing", worker_tmux=True)
    monkeypatch.setattr(
        ssh_worker,
        "_run_remote",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, stdout=_probe_stdout(tmux="", rsync=""), stderr=""),
    )
    payload = ssh_worker.bootstrap_worker(cfg, dry_run=True)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["missing_before"] == ["tmux", "rsync"]
    assert payload["command"] == "set -eu; /data/data/com.termux/files/usr/bin/pkg install -y tmux rsync"


def test_bootstrap_policy_never_requires_force(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = SSHRunnerConfig(host="tablet", profile="tablet", bootstrap_policy="never", worker_tmux=True)
    monkeypatch.setattr(
        ssh_worker,
        "_run_remote",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, stdout=_probe_stdout(tmux=""), stderr=""),
    )
    payload = ssh_worker.bootstrap_worker(cfg)
    assert payload["ok"] is False
    assert payload["changed"] is False
    assert "--force" in payload["detail"]


def test_worker_session_name_is_stable_for_same_run_identity() -> None:
    cfg = SSHRunnerConfig(host="tablet", user="termux", profile="tablet", cwd="~/Code/xdm")
    first = worker_session_name(cfg, operation="build", repository="~/Code/xdm", run_id="abc123")
    second = worker_session_name(cfg, operation="build", repository="~/Code/xdm", run_id="abc123")
    other = worker_session_name(cfg, operation="build", repository="~/Code/xdm", run_id="def456")
    assert first == second
    assert first != other
    assert first.startswith("devtool-tablet-xdm-build-")


def test_profile_rejects_non_ssh_execution_selector(tmp_path: Path) -> None:
    _write_config(tmp_path)
    parser = build_parser()
    args = parser.parse_args(["--repo", str(tmp_path), "--chroot", "androidos", "--ssh-profile", "tablet", "validate"])
    with pytest.raises(SystemExit, match="non-SSH execution selectors"):
        _maybe_delegate_ssh(["--repo", str(tmp_path), "--chroot", "androidos", "--ssh-profile", "tablet", "validate"], args)


def test_named_worker_cannot_disable_tmux(tmp_path: Path) -> None:
    _write_config(tmp_path)
    path = tmp_path / ".devtool.toml"
    path.write_text(path.read_text(encoding="utf-8").replace("tmux = true", "tmux = false"), encoding="utf-8")
    with pytest.raises(ValueError, match="require tmux"):
        resolve_ssh_config(["--repo", str(tmp_path), "--ssh-profile", "tablet"], cwd=str(tmp_path), env={})
