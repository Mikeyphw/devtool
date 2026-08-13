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


def _remote_script_from_ssh_argv(command: list[str]) -> str:
    remote = command[-1]
    parsed = __import__("shlex").split(remote)
    assert parsed[:2] == ["sh", "-lc"]
    assert len(parsed) == 3
    return parsed[2]


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
    assert command[:5] == ["ssh", "-tt", "-p", "2222", "mike@buildbox"]
    assert len(command) == 6
    remote_script = _remote_script_from_ssh_argv(command)
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
    remote_script = _remote_script_from_ssh_argv(command)
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
    remote_script = _remote_script_from_ssh_argv(command)
    assert "/opt/work/app" in remote_script
    assert "/mnt/incoming/fix.zip" in remote_script


def test_remote_home_expression_can_be_forwarded_for_remote_expansion() -> None:
    _, forwarded, command = build_ssh_tmux_argv(
        ["--ssh", "buildbox", "--repo", "~/Code/demo", "build"],
        cwd="/tmp",
        env={},
    )
    assert forwarded == ["--repo", "~/Code/demo", "build"]
    assert "~/Code/demo" in _remote_script_from_ssh_argv(command)


def test_detached_mode_does_not_allocate_tty_and_returns_launcher_status() -> None:
    argv = ["--ssh", "buildbox", "--ssh-detached", "--ssh-cwd", "/srv/demo", "build"]
    with patch("devtool.ssh_tmux_runner.subprocess.run") as run:
        run.return_value.returncode = 0
        rc = run_ssh_tmux(argv, cwd="/tmp", env={})
    assert rc == 0
    launched = run.call_args.args[0]
    assert "-tt" not in launched
    assert "tmux new-session -d" in _remote_script_from_ssh_argv(launched)


def test_invalid_port_is_rejected() -> None:
    with pytest.raises(ValueError, match="between 1 and 65535"):
        resolve_ssh_config(["--ssh", "host", "--ssh-port", "70000", "build"], cwd="/tmp", env={})


def test_whole_run_stages_local_overlay_over_scp_and_rewrites_operand(tmp_path: Path) -> None:
    from devtool.ssh_tmux_runner import stage_forwarded_artifacts

    artifact = tmp_path / "fix.zip"
    artifact.write_bytes(b"overlay")
    config = resolve_ssh_config(
        ["--ssh", "buildbox", "--ssh-session", "apply-fix", "apply-overlay", str(artifact)],
        cwd=str(tmp_path),
        env={},
    )
    forwarded = ["apply-overlay", str(artifact), "--no-validate"]
    with patch("devtool.ssh_tmux_runner.subprocess.run") as run:
        run.return_value.returncode = 0
        rewritten, staged = stage_forwarded_artifacts(config, forwarded, cwd=str(tmp_path))
    assert len(staged) == 1
    assert staged[0][0] == artifact.resolve()
    assert rewritten[0] == "apply-overlay"
    assert rewritten[1].startswith("~/.cache/devtool/ssh-stage/apply-fix/inputs/")
    assert rewritten[1].endswith("-fix.zip")
    commands = [call.args[0] for call in run.call_args_list]
    mkdir_commands = [command for command in commands if command[0] == "ssh" and "mkdir -p" in command[-1]]
    assert len(mkdir_commands) == 1
    mkdir_remote = mkdir_commands[0][-1]
    assert __import__("shlex").split(mkdir_remote) == [
        "sh", "-lc", 'mkdir -p -- "$HOME/.cache/devtool/ssh-stage/apply-fix/inputs"'
    ]
    # The complete remote shell invocation is one SSH argv item. This is what
    # prevents OpenSSH from flattening the -lc payload into mkdir's argv.
    assert mkdir_commands[0][-1].startswith("sh -lc ")
    assert any(command[0] == "scp" and str(artifact.resolve()) in command for command in commands)


def test_nonexistent_artifact_operand_is_assumed_remote_and_not_copied(tmp_path: Path) -> None:
    from devtool.ssh_tmux_runner import stage_forwarded_artifacts

    config = resolve_ssh_config(["--ssh", "buildbox", "apply-overlay", "/remote/fix.zip"], cwd=str(tmp_path), env={})
    with patch("devtool.ssh_tmux_runner.subprocess.run") as run:
        rewritten, staged = stage_forwarded_artifacts(config, ["apply-overlay", "/remote/fix.zip"], cwd=str(tmp_path))
    assert rewritten == ["apply-overlay", "/remote/fix.zip"]
    assert staged == []
    run.assert_not_called()


def test_commands_mode_wraps_actual_command_not_remote_devtool(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from devtool.models import CommandSpec
    from devtool.ssh_tmux_runner import wrap_specs_for_ssh_commands

    repo = tmp_path / "repo"
    repo.mkdir()
    script = repo / "gradlew"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    config = resolve_ssh_config(
        ["--ssh", "buildbox", "--ssh-mode", "commands", "--ssh-cwd", "/srv/demo", "build"],
        cwd=str(repo),
        env={},
    )
    project = SimpleNamespace(runner_options={"artifacts": {"output_directory": ".devtool/build-artifacts"}})
    spec = CommandSpec("build", [str(script), "assembleDebug"], cwd=str(repo), metadata={"collect_artifacts": True})
    wrapped = wrap_specs_for_ssh_commands([spec], config, project_config=project, repo_root=repo, dry_run=True)
    assert len(wrapped) == 1
    command = wrapped[0]
    assert command.argv[:2] == ["sh", "-lc"]
    shell = command.argv[-1]
    assert "ssh -tt -p 22 buildbox" in shell
    assert "tmux new-session -d" in shell
    assert "cd /srv/demo" in shell
    assert "/srv/demo/gradlew" in shell
    assert " devtool " not in shell
    assert "scp -P 22 -r buildbox:/srv/demo/.devtool/build-artifacts/." in shell
    assert command.metadata["ssh_mode"] == "commands"


def test_commands_mode_requires_explicit_remote_repo_root(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from devtool.models import CommandSpec
    from devtool.ssh_tmux_runner import wrap_specs_for_ssh_commands

    config = resolve_ssh_config(["--ssh", "buildbox", "--ssh-mode", "commands", "build"], cwd=str(tmp_path), env={})
    with pytest.raises(ValueError, match="requires --ssh-cwd"):
        wrap_specs_for_ssh_commands(
            [CommandSpec("build", ["make"], cwd=str(tmp_path))],
            config,
            project_config=SimpleNamespace(runner_options={}),
            repo_root=tmp_path,
            dry_run=True,
        )


def test_commands_mode_rejects_detached_outer_orchestration(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from devtool.models import CommandSpec
    from devtool.ssh_tmux_runner import wrap_specs_for_ssh_commands

    config = resolve_ssh_config(
        ["--ssh", "buildbox", "--ssh-mode", "commands", "--ssh-cwd", "/srv/demo", "--ssh-detached", "build"],
        cwd=str(tmp_path),
        env={},
    )
    with pytest.raises(ValueError, match="whole-run"):
        wrap_specs_for_ssh_commands(
            [CommandSpec("build", ["make"], cwd=str(tmp_path))],
            config,
            project_config=SimpleNamespace(runner_options={}),
            repo_root=tmp_path,
            dry_run=True,
        )


def test_commands_mode_drops_host_chroot_env_and_maps_android_artifact_output(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from devtool.models import CommandSpec
    from devtool.ssh_tmux_runner import wrap_specs_for_ssh_commands

    repo = tmp_path / "repo"
    project = repo / "app"
    project.mkdir(parents=True)
    helper = tmp_path / "build-apk"
    helper.write_text("print('ok')\n", encoding="utf-8")
    config = resolve_ssh_config(
        ["--ssh", "buildbox", "--ssh-mode", "commands", "--ssh-cwd", "/srv/repo", "build"],
        cwd=str(repo),
        env={},
    )
    project_config = SimpleNamespace(runner_options={"artifacts": {"output_directory": ".devtool/build-artifacts"}})
    spec = CommandSpec(
        "build",
        ["python3", str(helper), str(project), "--output-dir", ".devtool/build-artifacts"],
        cwd=str(project),
        env={
            "DEVTOOL_BUILD_ENVIRONMENT": "chroot",
            "DEVTOOL_BUILD_CACHE_ROOT": "/home/local/.cache/devtool/build/chroot/android",
            "GRADLE_USER_HOME": "/home/local/.gradle",
            "DEVTOOL_MAX_WORKERS": "1",
        },
        metadata={"collect_artifacts": True},
    )
    wrapped = wrap_specs_for_ssh_commands([spec], config, project_config=project_config, repo_root=repo, dry_run=True)[0]
    shell = wrapped.argv[-1]
    assert "DEVTOOL_BUILD_ENVIRONMENT" not in shell
    assert "DEVTOOL_BUILD_CACHE_ROOT" not in shell
    assert "GRADLE_USER_HOME" not in shell
    assert "DEVTOOL_MAX_WORKERS=1" in shell
    assert "--output-dir /srv/repo/app/.devtool/build-artifacts" in shell
    assert f"buildbox:/srv/repo/app/.devtool/build-artifacts/. {project / '.devtool/build-artifacts'}" in shell
