from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from devtool.affected import _pattern_from_base, git_changed_paths
from devtool.execution import build_chroot_exec_argv
from devtool.execution_environments import ExecutionPlan, preflight_execution
from devtool.models import CommandSpec, JobConfig, OperationResult, ProjectConfig, TargetConfig, WorkflowSchedulerConfig, WorkspaceConfig, WorkflowConfig, WorkflowStepRef
from devtool.reservations import _run_reservations
from devtool.ssh_tmux_runner import SSHRunnerConfig, _command_name, resolve_ssh_config, wrap_specs_for_ssh_commands
from devtool.termux_chroot_runner import _insert_global_flags_before_command, translate_argv_paths_for_chroot, translate_environment_for_chroot, build_runner_command, RunnerConfig
from devtool.workflow_graph import _node_key, graph_from_workflow
from devtool.workflow_runtime import _job_specs, _target_scoped_config
from devtool.workflow_scheduler import build_execution_plan_from_specs, scope_for_graph_node, run_workflow_specs, _node_terminal_state


def _config(tmp_path: Path) -> ProjectConfig:
    return ProjectConfig(
        repo_root=tmp_path,
        kind="python",
        runner="python",
        project_root=str(tmp_path),
        workspace=WorkspaceConfig(scheduler=WorkflowSchedulerConfig(parallel=True, max_parallel_nodes=4, max_gradle_nodes=1, max_dotnet_nodes=1)),
    )


def test_preflight_expected_paths_are_repo_relative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "build.gradle").write_text("", encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    plan = ExecutionPlan(requested="native", current="native", effective="native", reason="test", path_map=())
    check = preflight_execution(repo, plan, expected_paths=["build.gradle"], require_expected_paths=True)
    target = next(item for item in check.checks if item.name == "target")
    assert target.status == "ready"
    assert target.detail == str(repo / "build.gradle")


def test_chroot_exec_argv_uses_supplied_env_without_mutating_global(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVTOOL_CHROOTCTL", "globalctl")
    argv = build_chroot_exec_argv("echo ok", "ubuntu", env={"DEVTOOL_CHROOTCTL": "localctl", "DEVTOOL_CHROOT_SUDO": "", "DEVTOOL_CHROOT_PATH_PREFIX": ""})
    assert argv[:4] == ["localctl", "--name", "ubuntu", "exec"]
    assert os.environ["DEVTOOL_CHROOTCTL"] == "globalctl"


def test_scheduler_string_false_and_unsafe_external_scope_are_not_parallel_safe(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.targets["app"] = TargetConfig(
        name="app",
        runner="gradle",
        root=str(tmp_path),
        inputs=("/outside",),
        outputs=("build",),
        metadata={"read_only": "false"},
    )
    node = graph_from_workflow("app", WorkflowConfig("build", (WorkflowStepRef("runner:gradle#build", "runner", "gradle", "build"),))).nodes[0]
    scope = scope_for_graph_node(config, node)
    assert scope.read_only is False
    assert scope.writes_known is False
    assert scope.parallel_eligible is False


def test_scheduler_parallel_false_string_does_not_enable_parallel(tmp_path: Path) -> None:
    config = _config(tmp_path)
    specs = [
        CommandSpec("a", ["true"], metadata={"workflow_node_key": "a", "workflow_write_scope_known": True, "workflow_write_scopes": ["a"]}),
        CommandSpec("b", ["true"], metadata={"workflow_node_key": "b", "workflow_write_scope_known": True, "workflow_write_scopes": ["b"]}),
    ]
    plan = build_execution_plan_from_specs(config, specs, parallel_requested="false", max_parallel_nodes=2)
    assert plan is not None
    assert plan.parallel_requested is False
    assert [len(w.nodes) for w in plan.waves] == [1, 1]


def test_scheduler_unkeyed_spec_disables_parallel_plan_instead_of_dropping(tmp_path: Path) -> None:
    config = _config(tmp_path)
    specs = [
        CommandSpec("a", ["true"], metadata={"workflow_node_key": "a", "workflow_write_scope_known": True, "workflow_write_scopes": ["a"]}),
        CommandSpec("lost", ["true"], metadata={}),
    ]
    assert build_execution_plan_from_specs(config, specs, parallel_requested=True) is None


def test_scheduler_reserves_reads_and_writes_for_cross_process_safety(tmp_path: Path) -> None:
    config = _config(tmp_path)
    specs = [
        CommandSpec("reader", ["true"], metadata={"workflow_node_key": "reader", "workflow_read_only": True, "workflow_read_scopes": ["shared"]}),
        CommandSpec("writer", ["true"], metadata={"workflow_node_key": "writer", "workflow_write_scope_known": True, "workflow_write_scopes": ["other"]}),
    ]
    plan = build_execution_plan_from_specs(config, specs, parallel_requested=True)
    assert plan is not None
    assert set(plan.reservation_paths) == {"shared", "other"}


def test_gradle_resource_class_ignores_typo_override(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.workspace.scheduler.max_gradle_nodes = 1
    specs = [
        CommandSpec("a", ["true"], metadata={"workflow_node_key": "a", "workflow_runner": "gradle", "resource_class": "gralde", "workflow_write_scope_known": True, "workflow_write_scopes": ["a"]}),
        CommandSpec("b", ["true"], metadata={"workflow_node_key": "b", "workflow_runner": "gradle", "resource_class": "gralde", "workflow_write_scope_known": True, "workflow_write_scopes": ["b"]}),
    ]
    plan = build_execution_plan_from_specs(config, specs, parallel_requested=True, max_parallel_nodes=2)
    assert plan is not None
    assert all(len(w.nodes) == 1 for w in plan.waves)


def test_node_terminal_state_honors_skipped_result() -> None:
    assert _node_terminal_state([OperationResult("s", [], 0, 0.0, "", [], skipped=True)]) == "skipped"


def test_workflow_worker_exception_becomes_failed_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import devtool.runners as runners

    config = _config(tmp_path)
    specs = [
        CommandSpec("a", ["boom"], metadata={"workflow_node_key": "a", "workflow_write_scope_known": True, "workflow_write_scopes": ["a"]}),
        CommandSpec("b", ["ok"], metadata={"workflow_node_key": "b", "workflow_write_scope_known": True, "workflow_write_scopes": ["b"]}),
    ]

    def fake_run_specs(*args, **kwargs):
        local_specs = args[1]
        if local_specs[0].name == "a":
            raise RuntimeError("boom")
        return [OperationResult("b", [], 0, 0.0, "", [])], ""

    monkeypatch.setattr(runners, "run_specs", fake_run_specs)
    results, _raw, plan = run_workflow_specs(config, specs, verbose=False, dry_run=False, continue_on_error=True, log_path=None, parallel_requested=True, max_parallel_nodes=2)
    assert plan and plan.parallel_effective
    failed = [r for r in results if r.failure_kind == "workflow_worker_exception"]
    assert failed and failed[0].returncode == 1


def test_workflow_node_keys_escape_dotted_components() -> None:
    ref = WorkflowStepRef("runner:python#test", "runner", "python", "test", step_id="d")
    assert _node_key("a.b", "c", 1, ref) != _node_key("a", "b.c", 1, ref)


def test_affected_rename_includes_source_and_destination(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "a@b.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)
    (repo / "old.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "old.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "mv", "old.txt", "new.txt"], cwd=repo, check=True)
    paths = git_changed_paths(repo)
    assert "old.txt" in paths and "new.txt" in paths


def test_dot_github_pattern_keeps_leading_dot(tmp_path: Path) -> None:
    assert _pattern_from_base(tmp_path, None, ".github/**") == ".github/**"


def test_malformed_stale_reservation_pid_does_not_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import devtool.reservations as reservations
    root = tmp_path / "state"
    monkeypatch.setattr(reservations, "state_dir", lambda: root)
    run_dir = root / "locks" / reservations.repo_key(tmp_path) / "runs"
    run_dir.mkdir(parents=True)
    (run_dir / "bad.lock").write_text('{"pid":"abc","paths_known":true,"reserved_paths":["src"]}', encoding="utf-8")
    assert _run_reservations(tmp_path) == []


def test_termux_host_copy_insertion_treats_group_value_as_option_value() -> None:
    result = _insert_global_flags_before_command(["--group", "android", "apply", "overlay", "x.tar.gz"], ["--machine-file", "/tmp/m.json"])
    assert result[:4] == ["--group", "android", "--machine-file", "/tmp/m.json"]


def test_chroot_path_translation_is_boundary_safe_and_classpath_is_path_list() -> None:
    env, changed = translate_environment_for_chroot({"CLASSPATH": "/host/lib:/hostile/lib"}, mappings=[("/host", "/guest")], cwd="/")
    assert env["CLASSPATH"] == "/guest/lib:/hostile/lib"
    argv = translate_argv_paths_for_chroot(["cmd", "/hostile/file", "/host/file"], mappings=[("/host", "/guest")], cwd="/")
    assert argv == ["cmd", "/hostile/file", "/guest/file"]


def test_ssh_config_env_boolean_overrides_file_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "devtool.toml").write_text('[ssh]\nhost="example"\ncopy_artifacts=false\n', encoding="utf-8")
    cfg = resolve_ssh_config(["-c", str(tmp_path / "devtool.toml"), "--ssh-mode", "commands"], env={"DEVTOOL_SSH_NO_ARTIFACT_COPY": "0"}, cwd=str(tmp_path))
    assert cfg.copy_artifacts is True


def test_ssh_command_name_skips_group_option_value() -> None:
    assert _command_name(["--group", "android", "validate"]) == "validate"


def test_ssh_command_mode_local_trap_kills_remote_tmux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import devtool.ssh_tmux_runner as ssh
    cfg = SSHRunnerConfig(host="h", user="u", cwd="~/repo", mode="commands")
    monkeypatch.setattr(ssh, "_preflight_local_transport", lambda config: None)
    monkeypatch.setattr(ssh, "_remote_tmux_preflight", lambda config: None)
    monkeypatch.setattr(ssh, "_assert_session_inactive", lambda config, session: None)
    spec = CommandSpec("build", ["echo", "ok"], cwd=str(tmp_path), metadata={})
    wrapped = wrap_specs_for_ssh_commands([spec], cfg, project_config=object(), repo_root=tmp_path, dry_run=False)
    script = wrapped[0].argv[-1]
    assert "trap " in script and "tmux kill-session" in script and "exit 130" in script


def test_job_explicit_empty_env_clears_parent_env(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.env = {"KEEP": "1"}
    job = JobConfig("j", "command", command=("env",), env={}, declared_fields=frozenset({"env", "command", "runner"}))
    specs = _job_specs(config, job)
    assert specs[0].env == {}

def test_chroot_home_discovery_rejects_multiple_user_matches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from devtool.termux_chroot_runner import discover_chroot_home_for_termux_path
    import devtool.termux_chroot_runner as tcr

    home = tmp_path / "termux"
    repo = home / "Code" / "repo"
    repo.mkdir(parents=True)

    class Completed:
        returncode = 0
        stdout = "/home/alice\n/home/bob\n"

    monkeypatch.setattr(tcr.subprocess, "run", lambda *a, **k: Completed())
    assert discover_chroot_home_for_termux_path(str(repo), "ubuntu", env={"HOME": str(home), "DEVTOOL_CHROOT_SUDO": ""}) == ""
