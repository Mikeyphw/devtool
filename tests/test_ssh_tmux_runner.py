from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from devtool.ssh_tmux_runner import (
    build_ssh_tmux_argv,
    reattach_command,
    resolve_ssh_config,
    run_ssh_tmux,
    ssh_requested,
)


def test_cli_ssh_config_and_port_are_resolved() -> None:
    config = resolve_ssh_config(
        ["--ssh", "buildbox", "--ssh-port", "2222", "--ssh-user", "mike", "build"],
        cwd="/data/data/com.termux/files/home/Code/demo",
        env={},
    )
    assert config.host == "buildbox"
    assert config.port == 2222
    assert config.destination == "mike@buildbox"
    assert config.cwd == ""


def test_config_file_can_select_ssh_transport_and_remote_cwd() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".devtool.toml").write_text(
            """
[project]
kind = "python"
[execution]
environment = "ssh"
[ssh]
host = "192.168.1.50"
port = 2202
user = "builder"
cwd = "/srv/build/demo"
session = "demo-build"
""".strip()
            + "\n",
            encoding="utf-8",
        )
        argv = ["build"]
        assert ssh_requested(argv, cwd=str(root), env={})
        config = resolve_ssh_config(argv, cwd=str(root), env={})
    assert config.host == "192.168.1.50"
    assert config.port == 2202
    assert config.cwd == "/srv/build/demo"
    assert config.session == "demo-build"


def test_inside_ssh_suppresses_recursive_delegation() -> None:
    assert not ssh_requested(
        ["--execution-environment", "ssh", "--ssh", "host", "build"],
        cwd="/tmp",
        env={"DEVTOOL_INSIDE_SSH": "1"},
    )


def test_build_command_uses_explicit_remote_cwd_and_tmux_without_path_translation() -> None:
    local_repo = "/data/data/com.termux/files/home/Code/demo"
    config, forwarded, command = build_ssh_tmux_argv(
        [
            "--ssh", "buildbox",
            "--ssh-port", "2222",
            "--ssh-user", "mike",
            "--ssh-cwd", "/srv/projects/demo",
            "--repo", local_repo,
            "build",
        ],
        cwd=local_repo,
        env={},
    )
    assert config.cwd == "/srv/projects/demo"
    assert forwarded[:2] == ["--repo", local_repo]
    assert "--ssh" not in forwarded
    assert command[:6] == ["ssh", "-tt", "-p", "2222", "mike@buildbox", "sh"]
    remote_script = command[-1]
    assert "tmux new-session -d" in remote_script
    assert "tmux attach-session" in remote_script
    assert "DEVTOOL_INSIDE_SSH=1" in remote_script
    assert "cd /srv/projects/demo" in remote_script
    # The local repo is forwarded verbatim, never rewritten into an assumed remote home.
    assert local_repo in remote_script
    assert "/home/mike" not in remote_script
    assert "/home/termux" not in remote_script
    assert config.session.startswith("devtool-demo-build")
    assert reattach_command(config)[-4:] == ["tmux", "attach-session", "-t", config.session]


def test_no_remote_cwd_means_no_cd_or_local_cwd_inference() -> None:
    local_cwd = "/data/data/com.termux/files/home/Code/demo"
    config, forwarded, command = build_ssh_tmux_argv(
        ["--ssh", "buildbox", "build"],
        cwd=local_cwd,
        env={},
    )
    assert config.cwd == ""
    assert forwarded == ["build"]
    remote_script = command[-1]
    assert f"cd {local_cwd}" not in remote_script
    assert local_cwd not in remote_script
    assert "/home/termux" not in remote_script
    assert "/home/mike" not in remote_script


def test_remote_path_arguments_are_forwarded_verbatim() -> None:
    _, forwarded, command = build_ssh_tmux_argv(
        [
            "--ssh", "buildbox",
            "--ssh-cwd", "/opt/work/app",
            "--repo", "/opt/work/app",
            "apply-overlay",
            "/mnt/incoming/fix.zip",
        ],
        cwd="/data/data/com.termux/files/home/Code/app",
        env={},
    )
    assert forwarded == [
        "--repo", "/opt/work/app", "apply-overlay", "/mnt/incoming/fix.zip"
    ]
    remote_script = command[-1]
    assert "/opt/work/app" in remote_script
    assert "/mnt/incoming/fix.zip" in remote_script


def test_remote_home_expression_can_be_forwarded_for_remote_expansion() -> None:
    _, forwarded, command = build_ssh_tmux_argv(
        ["--ssh", "buildbox", "--repo", "~/Code/demo", "build"],
        cwd="/tmp",
        env={},
    )
    assert forwarded == ["--repo", "~/Code/demo", "build"]
    assert "~/Code/demo" in command[-1]


def test_detached_mode_does_not_allocate_tty_and_returns_launcher_status() -> None:
    argv = ["--ssh", "buildbox", "--ssh-detached", "--ssh-cwd", "/srv/demo", "build"]
    with patch("devtool.ssh_tmux_runner.subprocess.run") as run:
        run.return_value.returncode = 0
        rc = run_ssh_tmux(argv, cwd="/tmp", env={})
    assert rc == 0
    launched = run.call_args.args[0]
    assert "-tt" not in launched
    assert "tmux new-session -d" in launched[-1]


def test_invalid_port_is_rejected() -> None:
    with pytest.raises(ValueError, match="between 1 and 65535"):
        resolve_ssh_config(["--ssh", "host", "--ssh-port", "70000", "build"], cwd="/tmp", env={})
