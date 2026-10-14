from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from devtool.models import CommandSpec
from devtool.ssh_tmux_runner import (
    SSHRunnerConfig,
    _default_session,
    _remote_shell_path,
    _stage_support_files_for_spec,
    resolve_ssh_config,
    run_ssh_tmux,
    stage_forwarded_artifacts,
    wrap_specs_for_ssh_commands,
)


class R:
    def __init__(self, rc: int): self.returncode = rc


def test_automatic_sessions_are_invocation_unique(monkeypatch) -> None:
    monkeypatch.delenv("DEVTOOL_RUN_ID", raising=False)
    cfg = SSHRunnerConfig(host="host", cwd="/srv/repo")
    assert _default_session(cfg, ["build"]) != _default_session(cfg, ["build"])


def test_remote_home_tail_is_quoted_as_data() -> None:
    rendered = _remote_shell_path("~/Code/a b/$(touch PWNED);x")
    assert rendered.startswith('"$HOME"/')
    assert "'Code/a b/$(touch PWNED);x'" in rendered


def test_missing_identity_fails_before_any_subprocess_side_effect(tmp_path: Path) -> None:
    missing = tmp_path / "missing-key"
    argv = ["--ssh", "host", "--ssh-identity", str(missing), "build"]
    with patch("devtool.ssh_tmux_runner.shutil.which", side_effect=lambda value: f"/usr/bin/{value}"), \
         patch("devtool.ssh_tmux_runner.subprocess.run") as run:
        with pytest.raises(ValueError, match="identity file"):
            run_ssh_tmux(argv, cwd=str(tmp_path), env={})
    run.assert_not_called()


def test_active_session_refuses_before_mkdir_or_scp(tmp_path: Path) -> None:
    artifact = tmp_path / "fix.zip"; artifact.write_bytes(b"x")
    cfg = resolve_ssh_config(["--ssh", "host", "--ssh-session", "same", "apply-overlay", str(artifact)], cwd=str(tmp_path), env={})
    with patch("devtool.ssh_tmux_runner.subprocess.run", return_value=R(0)) as run:
        with pytest.raises(ValueError, match="already active"):
            stage_forwarded_artifacts(cfg, ["apply-overlay", str(artifact)], cwd=str(tmp_path))
    assert len(run.call_args_list) == 1
    assert "has-session" in run.call_args.args[0][-1]


def test_unknown_session_state_refuses_before_staging(tmp_path: Path) -> None:
    artifact = tmp_path / "fix.zip"; artifact.write_bytes(b"x")
    cfg = resolve_ssh_config(["--ssh", "host", "--ssh-session", "same", "apply-overlay", str(artifact)], cwd=str(tmp_path), env={})
    with patch("devtool.ssh_tmux_runner.subprocess.run", return_value=R(255)) as run:
        with pytest.raises(ValueError, match="could not be verified"):
            stage_forwarded_artifacts(cfg, ["apply-overlay", str(artifact)], cwd=str(tmp_path))
    assert len(run.call_args_list) == 1


def test_only_typed_artifact_operands_are_staged_and_hash_is_content_addressed(tmp_path: Path) -> None:
    arbitrary = tmp_path / "tool.zip"; arbitrary.write_bytes(b"toolchain")
    artifact = tmp_path / "fix.zip"; artifact.write_bytes(b"one")
    cfg = resolve_ssh_config(["--ssh", "host", "--ssh-session", "s", "apply-overlay", str(artifact)], cwd=str(tmp_path), env={})
    with patch("devtool.ssh_tmux_runner.subprocess.run", side_effect=[R(1), R(0), R(0)]):
        rewritten, staged = stage_forwarded_artifacts(cfg, ["build", str(arbitrary), "apply-overlay", str(artifact)], cwd=str(tmp_path))
    assert rewritten[1] == str(arbitrary)
    first_remote = staged[0][1]
    artifact.write_bytes(b"two")
    cfg.session = "s2"
    with patch("devtool.ssh_tmux_runner.subprocess.run", side_effect=[R(1), R(0), R(0)]):
        _, staged2 = stage_forwarded_artifacts(cfg, ["apply-overlay", str(artifact)], cwd=str(tmp_path))
    assert staged2[0][1] != first_remote


def test_support_staging_requires_explicit_helper_type(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    external = tmp_path / "helper.gradle"; external.write_text("// helper", encoding="utf-8")
    cfg = SSHRunnerConfig(host="host", session="s")
    plain, staged = _stage_support_files_for_spec(cfg, ["gradle", str(external)], local_root=repo, remote_root="/srv/repo", session="s", dry_run=True)
    assert plain[1] == str(external) and staged == []
    typed, staged = _stage_support_files_for_spec(cfg, ["gradle", "--init-script", str(external)], local_root=repo, remote_root="/srv/repo", session="s", dry_run=True)
    assert staged and typed[2].endswith("-helper.gradle")


def test_explicit_session_reuse_still_gets_new_operation_staging_root(tmp_path: Path) -> None:
    artifact = tmp_path / "fix.zip"; artifact.write_bytes(b"same")
    remote_paths = []
    for _ in range(2):
        cfg = resolve_ssh_config(["--ssh", "host", "--ssh-session", "stable", "apply-overlay", str(artifact)], cwd=str(tmp_path), env={})
        with patch("devtool.ssh_tmux_runner.subprocess.run", side_effect=[R(1), R(0), R(0)]):
            _rewritten, staged = stage_forwarded_artifacts(cfg, ["apply-overlay", str(artifact)], cwd=str(tmp_path))
        remote_paths.append(staged[0][1])
    assert remote_paths[0] != remote_paths[1]
    assert remote_paths[0].endswith("-fix.zip") and remote_paths[1].endswith("-fix.zip")


def test_artifact_output_is_operation_scoped_and_host_tool_is_remote_resolved(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    host_python = tmp_path / "host-bin" / "python3.13"
    host_python.parent.mkdir(); host_python.write_text("#!/bin/sh\n", encoding="utf-8"); host_python.chmod(0o755)
    local_out = repo / "dist"
    project = SimpleNamespace(runner_options={"artifacts": {"output_directory": "dist"}})
    shells = []
    for _ in range(2):
        cfg = resolve_ssh_config(["--ssh", "host", "--ssh-mode", "commands", "--ssh-session", "stable", "--ssh-cwd", "/srv/repo", "build"], cwd=str(repo), env={})
        spec = CommandSpec("build", [str(host_python), "build", "--out-dir", str(local_out)], cwd=str(repo), metadata={"collect_artifacts": True, "verify_artifacts": True})
        wrapped = wrap_specs_for_ssh_commands([spec], cfg, project_config=project, repo_root=repo, dry_run=True)[0]
        shells.append(wrapped.argv[-1])
        assert str(host_python) not in wrapped.argv[-1]
        assert "python3" in wrapped.argv[-1]
        assert "/outputs" in wrapped.argv[-1]
    assert shells[0] != shells[1]


def test_command_mode_clears_stale_local_outputs_then_cleans_stage_after_retrieval(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    helper = tmp_path / "init.gradle"; helper.write_text("// init", encoding="utf-8")
    cfg = resolve_ssh_config(["--ssh", "host", "--ssh-mode", "commands", "--ssh-cwd", "/srv/repo", "build"], cwd=str(repo), env={})
    spec = CommandSpec("build", ["gradle", "--init-script", str(helper)], cwd=str(repo), metadata={"collect_artifacts": True})
    project = SimpleNamespace(runner_options={"artifacts": {"output_directory": ".devtool/build-artifacts"}})
    wrapped = wrap_specs_for_ssh_commands([spec], cfg, project_config=project, repo_root=repo, dry_run=True)[0]
    shell = wrapped.argv[-1]
    assert ".devtool/build-artifacts" in shell
    assert "ssh -p 22 host" in shell
    assert "rm -rf --" not in shell, "remote worker stage is recovery-owned"
    assert "retained SSH operation staging for provider lifecycle recovery" in shell


def test_remote_owned_absolute_file_is_not_copied_by_host_existence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    remote_owned = tmp_path / "remote-tool"; remote_owned.write_text("host coincidence", encoding="utf-8")
    cfg = resolve_ssh_config(["--ssh", "host", "--ssh-mode", "commands", "--ssh-cwd", "/srv/repo", "build"], cwd=str(repo), env={})
    spec = CommandSpec("build", ["tool", str(remote_owned)], cwd=str(repo))
    wrapped = wrap_specs_for_ssh_commands([spec], cfg, project_config=SimpleNamespace(runner_options={}), repo_root=repo, dry_run=True)[0]
    assert wrapped.metadata["ssh_staged_files"] == []
    assert str(remote_owned) in wrapped.argv[-1]


def test_artifact_operand_parser_skips_value_taking_flags(tmp_path: Path) -> None:
    artifact = tmp_path / "fix.zip"
    artifact.write_bytes(b"payload")
    cfg = resolve_ssh_config(
        ["--ssh", "host", "--ssh-session", "s", "apply-overlay", "--target", "devtool", str(artifact)],
        cwd=str(tmp_path),
        env={},
    )
    with patch("devtool.ssh_tmux_runner.subprocess.run", side_effect=[R(1), R(0), R(0)]):
        rewritten, staged = stage_forwarded_artifacts(
            cfg,
            ["apply-overlay", "--target", "devtool", str(artifact)],
            cwd=str(tmp_path),
        )
    assert len(staged) == 1
    assert rewritten[-1] == staged[0][1]
    assert rewritten[2] == "devtool"


def test_artifact_phase_without_helpers_cleans_operation_output_after_retrieval(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = resolve_ssh_config(
        ["--ssh", "host", "--ssh-mode", "commands", "--ssh-cwd", "/srv/repo", "build"],
        cwd=str(repo),
        env={},
    )
    spec = CommandSpec(
        "build",
        ["tool", "build", "--out-dir", str(repo / "dist")],
        cwd=str(repo),
        metadata={"collect_artifacts": True},
    )
    project = SimpleNamespace(runner_options={"artifacts": {"output_directory": "dist"}})
    wrapped = wrap_specs_for_ssh_commands([spec], cfg, project_config=project, repo_root=repo, dry_run=True)[0]
    shell = wrapped.argv[-1]
    assert wrapped.metadata["ssh_staged_files"] == []
    assert "scp" in shell
    assert "/outputs" in shell
    assert "rm -rf --" not in shell, "stage disposal requires lifecycle-owned proof"
    assert "retained SSH operation staging for provider lifecycle recovery" in shell
    assert shell.count("ssh -p 22 host") >= 1
    assert "sh -lc" in shell, "remote command remains shell-qualified"


def test_command_phase_without_helpers_refuses_active_session_before_launch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = resolve_ssh_config(
        ["--ssh", "host", "--ssh-mode", "commands", "--ssh-session", "stable", "--ssh-cwd", "/srv/repo", "build"],
        cwd=str(repo),
        env={},
    )
    spec = CommandSpec("build", ["tool", "build"], cwd=str(repo))
    with patch("devtool.ssh_tmux_runner.shutil.which", side_effect=lambda value: f"/usr/bin/{value}"), \
         patch("devtool.ssh_tmux_runner.subprocess.run", side_effect=[R(0), R(0)]) as run:
        with pytest.raises(ValueError, match="already active"):
            wrap_specs_for_ssh_commands(
                [spec], cfg, project_config=SimpleNamespace(runner_options={}), repo_root=repo, dry_run=False
            )
    assert len(run.call_args_list) == 2
    assert "has-session" in run.call_args_list[-1].args[0][-1]
