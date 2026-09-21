from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from devtool.models import CommandSpec
from devtool import ssh_execution, ssh_resilience, ssh_tmux_runner, ssh_workspace, ssh_worker
from devtool.ssh_resilience import SSHWorkspaceLease
from devtool.ssh_tmux_runner import SSHRunnerConfig
from devtool.ssh_worker import SSHWorkerProbe
from devtool.ssh_workspace import SSHWorkspaceSpec, SSHWorkspaceStatus


def _cfg(**overrides) -> SSHRunnerConfig:
    values = dict(
        host="tablet", port=8022, user="u0_a123", profile="tablet", mode="commands",
        worker_tmux=True, workspace_mode="managed", workspace_root="/home/u/.cache/devtool/workspaces",
        sync_mode="incremental", sync_delete=True, sync_default_excludes=True, sync_excludes=(),
        bootstrap_policy="missing", host_key_policy="accept-new", run_state_root="~/.cache/devtool/ssh-runs",
        lease_timeout_seconds=900, min_free_mb=1024,
    )
    values.update(overrides)
    return SSHRunnerConfig(**values)


def _probe(*, free_mb: int = 8192) -> SSHWorkerProbe:
    return SSHWorkerProbe(
        profile="tablet", destination="u0_a123@tablet", port=8022,
        platform="termux", os_name="Linux", architecture="aarch64",
        cpu_count=8, memory_kib=12 * 1024 * 1024, free_storage_kib=free_mb * 1024,
        home="/home/u", prefix="/data/data/com.termux/files/usr", shell="/bin/sh", package_manager="pkg",
        tools={"python":"/bin/python","tmux":"/bin/tmux","rsync":"/bin/rsync","git":"/bin/git","java":"","gradle":""},
        versions={}, bootstrap_policy="missing", tmux_required=True,
    )


def _workspace(tmp_path: Path) -> SSHWorkspaceSpec:
    repo = tmp_path / "repo"; repo.mkdir(exist_ok=True); (repo / ".git").mkdir(exist_ok=True)
    return SSHWorkspaceSpec(
        profile="tablet", destination="u0_a123@tablet", mode="managed", repo_root=str(repo),
        repo_name="repo", workspace_id="repo-id", instance_id="repo-id",
        workspace_root="/home/u/.cache/devtool/workspaces", remote_path="/home/u/.cache/devtool/workspaces/repo-id",
        delete=True, sync_mode="incremental", excludes=(".gradle/", "build/", "/.devtool-sync-state.json"),
    )


def test_named_profile_defaults_to_accept_new_host_keys(tmp_path: Path) -> None:
    cfgfile = tmp_path / ".devtool.toml"
    cfgfile.write_text('''[ssh]\nprofile="tablet"\n[ssh.hosts.tablet]\nhost="tablet"\nuser="u"\nmode="commands"\n[ssh.hosts.tablet.worker]\ntmux=true\n''')
    cfg = ssh_tmux_runner.resolve_ssh_config(["--config", str(cfgfile), "--ssh-profile", "tablet", "build"], cwd=str(tmp_path), env={})
    assert cfg.host_key_policy == "accept-new"
    argv = ssh_tmux_runner.build_ssh_base_argv(cfg, tty=False)
    assert ["-o", "StrictHostKeyChecking=accept-new"] == argv[3:5]


def test_raw_ssh_preserves_system_host_key_policy(tmp_path: Path) -> None:
    cfg = ssh_tmux_runner.resolve_ssh_config(["--ssh", "tablet", "--ssh-mode", "commands", "--ssh-cwd", "/repo", "build"], cwd=str(tmp_path), env={})
    assert cfg.host_key_policy == "system"
    assert not any("StrictHostKeyChecking" in item for item in ssh_tmux_runner.build_ssh_base_argv(cfg, tty=False))


def test_strict_host_key_policy_and_known_hosts_apply_to_ssh_and_scp(tmp_path: Path) -> None:
    known = tmp_path / "known_hosts"
    cfg = _cfg(host_key_policy="strict", known_hosts=str(known))
    ssh = ssh_tmux_runner.build_ssh_base_argv(cfg, tty=False)
    scp = ssh_tmux_runner.build_scp_base_argv(cfg)
    for argv in (ssh, scp):
        assert "StrictHostKeyChecking=yes" in argv
        assert f"UserKnownHostsFile={known}" in argv


def test_workspace_lease_acquires_and_records_owner(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    scripts: list[str] = []
    def runner(config, script, **kwargs):
        scripts.append(script)
        return subprocess.CompletedProcess([], 0, stdout="reclaimed=0\nacquired_unix=123\n", stderr="")
    lease = ssh_resilience.acquire_workspace_lease(_cfg(), ws, run_id="run-1", session_prefix="devtool-tablet-repo", runner=runner)
    assert lease.run_id == "run-1"
    assert lease.acquired_unix == 123
    assert "/.leases/repo-id.lock" in lease.lease_dir
    assert "mkdir \"$lease\"" in scripts[0]
    assert "tmux list-sessions" in scripts[0]


def test_workspace_lease_conflict_fails_closed(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    def runner(config, script, **kwargs):
        return subprocess.CompletedProcess([], 76, stdout="owner_run=run-old\nowner_session=devtool-old\nage_seconds=12\nactive=1\n", stderr="")
    with pytest.raises(ValueError, match="leased by another Devtool run.*run-old.*active=yes"):
        ssh_resilience.acquire_workspace_lease(_cfg(), ws, run_id="run-new", session_prefix="devtool-new", runner=runner)


def test_capacity_gate_blocks_low_storage_before_build() -> None:
    with pytest.raises(ValueError, match="only 256 MiB free.*at least 1024 MiB"):
        ssh_resilience.ensure_remote_capacity(_cfg(min_free_mb=1024), free_storage_kib=256 * 1024)


def test_sensitive_env_split_does_not_put_secret_value_in_dry_run_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); (repo / ".git").mkdir()
    cfg = _cfg(workspace_mode="existing", cwd="/remote/repo")
    prepared = ssh_execution.SSHExecutionPreparation(
        remote_root="/remote/repo", worker=_probe(), workspace=None, workspace_status={}, bootstrap={}, sync={},
        toolchain={"ok":True,"kind":"generic"}, run_id="run-1", session_prefix="devtool-tablet-run-1",
    )
    monkeypatch.setattr(ssh_execution, "prepare_ssh_command_execution", lambda *a, **kw: prepared)
    spec = CommandSpec("build", ["sh", "-c", "true"], cwd=str(repo), env={"API_TOKEN":"super-secret-value", "SAFE":"yes"}, metadata={})
    wrapped = ssh_tmux_runner.wrap_specs_for_ssh_commands([spec], cfg, project_config=SimpleNamespace(runner_options={}), repo_root=repo, dry_run=True)
    shell = wrapped[0].argv[-1]
    assert "super-secret-value" not in shell
    assert "API_TOKEN" not in shell
    assert "SAFE=yes" in shell
    assert wrapped[0].metadata["ssh_sensitive_env_keys"] == ["API_TOKEN"]


def test_remote_phase_script_persists_run_state_and_status() -> None:
    script = ssh_tmux_runner._build_remote_phase_script(
        _cfg(), session="devtool-tablet-build", command="echo ok", run_id="run-77", workspace_id="repo-id", workspace_path="/remote/repo",
    )
    assert "ssh-runs" in script
    assert "run_id=run-77" in script
    assert "workspace_id=repo-id" in script
    assert "running" in script and "completed" in script
    assert "finished_unix" in script


def test_transport_loss_maps_255_to_detached_125_without_releasing_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); (repo / ".git").mkdir()
    cfg = _cfg()
    ws = _workspace(tmp_path)
    lease = SSHWorkspaceLease("tablet", cfg.destination, ws.workspace_id, ws.instance_id, ws.workspace_root+"/.leases/repo-id.lock", "run-1", "devtool-base", 1)
    prepared = ssh_execution.SSHExecutionPreparation(
        remote_root=ws.remote_path, worker=_probe(), workspace=ws, workspace_status={}, bootstrap={}, sync={"ok":True},
        toolchain={"ok":True,"kind":"generic"}, lease=lease.as_dict(), run_id="run-1", session_prefix="devtool-base",
    )
    monkeypatch.setattr(ssh_execution, "prepare_ssh_command_execution", lambda *a, **kw: prepared)
    spec = CommandSpec("build", ["sh", "-c", "true"], cwd=str(repo), metadata={})
    wrapped = ssh_tmux_runner.wrap_specs_for_ssh_commands([spec], cfg, project_config=SimpleNamespace(runner_options={}), repo_root=repo, dry_run=True)
    shell = wrapped[0].argv[-1]
    assert 'if [ "$rc" -eq 255 ]' in shell
    assert "rc=125" in shell
    assert 'if [ "$rc" -ne 125 ]' in shell


def test_runs_payload_parses_active_completed_and_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = (
        "run-2\ts2\trunning\t\t1\trepo\t/remote/repo\t200\t/remote/.leases/repo.lock\n"
        "run-1\ts1\tcompleted\t0\t0\trepo\t/remote/repo\t100\t/remote/.leases/repo.lock\n"
        "run-0\ts0\tstale\t\t0\trepo\t/remote/repo\t50\t/remote/.leases/repo.lock\n"
    )
    monkeypatch.setattr(ssh_resilience, "_run_remote", lambda *a, **kw: subprocess.CompletedProcess([], 0, stdout=stdout, stderr=""))
    payload = ssh_resilience.runs_payload(_cfg())
    assert payload["active_count"] == 1
    assert payload["stale_count"] == 1
    assert payload["records"][0]["run_id"] == "run-2"
    assert payload["records"][1]["status"] == 0


def test_cancel_operates_only_on_discovered_devtool_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_resilience, "runs_payload", lambda cfg: {"records":[{"run_id":"run-1","session":"devtool-safe","state":"running","active":True,"started_unix":1}]})
    scripts: list[str] = []
    monkeypatch.setattr(ssh_resilience, "_run_remote", lambda config, script, **kw: scripts.append(script) or subprocess.CompletedProcess([], 0, stdout="", stderr=""))
    payload = ssh_resilience.cancel_remote_run(_cfg(), run_id="run-1")
    assert payload["cancelled"][0]["session"] == "devtool-safe"
    assert "tmux kill-session -t \"$session\"" in scripts[0]
    assert "cancelled" in scripts[0]


def test_stale_run_recovery_marks_interrupted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_resilience, "runs_payload", lambda cfg: {"records":[{"run_id":"run-1","session":"devtool-stale","state":"stale","active":False,"started_unix":1}]})
    monkeypatch.setattr(ssh_resilience, "_run_remote", lambda *a, **kw: subprocess.CompletedProcess([], 0, stdout="", stderr=""))
    payload = ssh_resilience.recover_stale_runs(_cfg())
    assert payload["recovered"] == [{"run_id":"run-1","session":"devtool-stale","state":"interrupted","status":255}]


def test_sync_partial_is_explicitly_recoverable_and_releases_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _workspace(tmp_path); cfg = _cfg()
    ready = SSHWorkspaceStatus(True, True, False, True, ws.workspace_root, ws.remote_path, ws.instance_id, {}, "ready")
    monkeypatch.setattr(ssh_workspace, "_rsync_executable", lambda: "/usr/bin/rsync")
    monkeypatch.setattr(ssh_workspace, "probe_worker", lambda c: _probe())
    monkeypatch.setattr(ssh_workspace, "workspace_status", lambda *a, **kw: ready)
    monkeypatch.setattr(ssh_workspace, "prepare_workspace", lambda *a, **kw: ready)
    monkeypatch.setattr(ssh_workspace, "_rsync_argv", lambda *a, **kw: ["rsync"])
    monkeypatch.setattr(ssh_workspace, "_source_revision", lambda root: {"git_head":"abc"})
    monkeypatch.setattr(ssh_workspace, "_write_sync_state", lambda *a, **kw: None)
    lease = SSHWorkspaceLease("tablet", cfg.destination, ws.workspace_id, ws.instance_id, ws.workspace_root+"/.leases/repo-id.lock", "run", "sess", 1)
    import devtool.ssh_resilience as resil
    monkeypatch.setattr(resil, "acquire_workspace_lease", lambda *a, **kw: lease)
    released: list[str] = []
    monkeypatch.setattr(resil, "release_workspace_lease", lambda c, l, **kw: released.append(l.run_id) or True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess([], 23, stdout=">f.st......|src/Main.kt\n", stderr="link lost"))
    result = ssh_workspace.sync_workspace(cfg, ws)
    assert result["ok"] is False and result["recoverable"] is True and result["state"] == "partial"
    assert released == ["run"]


def test_doctor_reports_resilience_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_worker, "probe_worker", lambda cfg: _probe())
    monkeypatch.setattr(ssh_worker, "list_tmux_sessions", lambda cfg: [])
    payload = ssh_worker.worker_doctor_payload(_cfg(host_key_policy="strict", min_free_mb=2048))
    assert payload["execution"]["resumable_runs"] is True
    assert payload["execution"]["workspace_leases"] is True
    assert payload["resilience"]["host_key_policy"] == "strict"
    assert payload["resilience"]["min_free_mb"] == 2048


def test_reserved_sync_state_is_excluded(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    cfg = _cfg()
    spec = ssh_workspace.resolve_workspace_spec(cfg, repo, remote_home="/home/u")
    assert "/.devtool-sync-state.json" in spec.excludes


def test_resume_json_style_probe_does_not_attach_active_tmux(monkeypatch: pytest.MonkeyPatch) -> None:
    record = {
        "run_id": "run-1", "session": "devtool-active", "state": "running",
        "active": True, "status": None, "started_unix": 1, "lease_dir": "",
    }
    monkeypatch.setattr(ssh_resilience, "runs_payload", lambda cfg: {"records": [record], "active_count": 1, "stale_count": 0})
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("non-interactive resume must not attach tmux"))
    payload = ssh_resilience.resume_remote_run(_cfg(), run_id="run-1", attach=False)
    assert payload["record"]["active"] is True
    assert payload["detail"] == "active tmux phase available for reattach"


def test_cancel_releases_owned_lease_and_secret_but_not_completed_history(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [
        {
            "run_id": "run-1", "session": "devtool-active", "state": "running", "active": True,
            "started_unix": 2, "lease_dir": "/home/u/.cache/devtool/workspaces/.leases/repo-id.lock",
        },
        {
            "run_id": "run-1", "session": "devtool-done", "state": "completed", "active": False,
            "status": 0, "started_unix": 1, "lease_dir": "/home/u/.cache/devtool/workspaces/.leases/repo-id.lock",
        },
    ]
    monkeypatch.setattr(ssh_resilience, "runs_payload", lambda cfg: {"records": records})
    scripts: list[str] = []
    monkeypatch.setattr(ssh_resilience, "_run_remote", lambda config, script, **kw: scripts.append(script) or subprocess.CompletedProcess([], 0, stdout="", stderr=""))
    payload = ssh_resilience.cancel_remote_run(_cfg(), run_id="run-1")
    assert [item["session"] for item in payload["cancelled"]] == ["devtool-active"]
    assert len(scripts) == 1
    assert "secret-env.sh" in scripts[0]
    assert '"$root_real/.leases"' in scripts[0]
    assert "refusing to release a lease owned by another run" in scripts[0]
    assert "devtool-done" not in scripts[0]


def test_recover_stale_releases_owned_lease_and_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    record = {
        "run_id": "run-1", "session": "devtool-stale", "state": "stale", "active": False,
        "started_unix": 1, "lease_dir": "/home/u/.cache/devtool/workspaces/.leases/repo-id.lock",
    }
    monkeypatch.setattr(ssh_resilience, "runs_payload", lambda cfg: {"records": [record]})
    scripts: list[str] = []
    monkeypatch.setattr(ssh_resilience, "_run_remote", lambda config, script, **kw: scripts.append(script) or subprocess.CompletedProcess([], 0, stdout="", stderr=""))
    payload = ssh_resilience.recover_stale_runs(_cfg())
    assert payload["recovered"][0]["state"] == "interrupted"
    assert "secret-env.sh" in scripts[0]
    assert "/.leases/repo-id.lock" in scripts[0]


def test_run_state_secret_staging_uses_mode_600_and_never_argv_value(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], str | None]] = []
    monkeypatch.setattr(ssh_tmux_runner, "_ensure_remote_dir", lambda *a, **kw: None)
    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs.get("input")))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    remote = ssh_tmux_runner._stage_sensitive_env(
        _cfg(), session="devtool-phase", run_id="run-1", values={"API_TOKEN": "top-secret"}, dry_run=False,
    )
    assert remote.endswith("/run-1/devtool-phase/secret-env.sh")
    argv, stdin = calls[0]
    assert "top-secret" not in " ".join(argv)
    assert "chmod 600" in " ".join(argv)
    assert stdin is not None and "top-secret" in stdin


def test_remote_phase_metadata_carries_lease_for_recovery() -> None:
    script = ssh_tmux_runner._build_remote_phase_script(
        _cfg(), session="devtool-phase", command="true", run_id="run-1",
        workspace_id="repo-id", workspace_path="/home/u/.cache/devtool/workspaces/repo-id",
        lease_dir="/home/u/.cache/devtool/workspaces/.leases/repo-id.lock",
    )
    assert "lease_dir=/home/u/.cache/devtool/workspaces/.leases/repo-id.lock" in script


def test_resume_completed_phase_reconciles_secret_and_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    record = {
        "run_id": "run-1", "session": "devtool-done", "state": "completed", "active": False,
        "status": 0, "started_unix": 1,
        "lease_dir": "/home/u/.cache/devtool/workspaces/.leases/repo-id.lock",
    }
    monkeypatch.setattr(ssh_resilience, "runs_payload", lambda cfg: {"records": [record]})
    scripts: list[str] = []
    monkeypatch.setattr(ssh_resilience, "_run_remote", lambda config, script, **kw: scripts.append(script) or subprocess.CompletedProcess([], 0, stdout="", stderr=""))
    payload = ssh_resilience.resume_remote_run(_cfg(), run_id="run-1", attach=False)
    assert payload["record"]["status"] == 0
    assert "secret-env.sh" in scripts[0]
    assert "/.leases/repo-id.lock" in scripts[0]
    assert "lease reconciled" in payload["detail"]


def test_wrapping_failure_releases_prepared_workspace_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); (repo / ".git").mkdir()
    cfg = _cfg()
    ws = _workspace(tmp_path)
    lease = SSHWorkspaceLease(
        "tablet", cfg.destination, ws.workspace_id, ws.instance_id,
        ws.workspace_root + "/.leases/repo-id.lock", "run-1", "devtool-base", 1,
    )
    prepared = ssh_execution.SSHExecutionPreparation(
        remote_root=ws.remote_path, worker=_probe(), workspace=ws, workspace_status={}, bootstrap={}, sync={"ok": True},
        toolchain={"ok": True, "kind": "generic"}, lease=lease.as_dict(), run_id="run-1", session_prefix="devtool-base",
    )
    monkeypatch.setattr(ssh_execution, "prepare_ssh_command_execution", lambda *a, **kw: prepared)
    monkeypatch.setattr(ssh_tmux_runner, "_preflight_local_transport", lambda cfg: (_ for _ in ()).throw(ValueError("local ssh missing")))
    released: list[str] = []
    monkeypatch.setattr(ssh_resilience, "release_workspace_lease", lambda c, l, **kw: released.append(l.run_id) or True)
    spec = CommandSpec("build", ["true"], cwd=str(repo), metadata={})
    with pytest.raises(ValueError, match="local ssh missing"):
        ssh_tmux_runner.wrap_specs_for_ssh_commands(
            [spec], cfg, project_config=SimpleNamespace(runner_options={}), repo_root=repo, dry_run=False,
        )
    assert released == ["run-1"]


def test_interrupt_trap_cleans_secret_and_releases_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); (repo / ".git").mkdir()
    cfg = _cfg()
    ws = _workspace(tmp_path)
    lease = SSHWorkspaceLease(
        "tablet", cfg.destination, ws.workspace_id, ws.instance_id,
        ws.workspace_root + "/.leases/repo-id.lock", "run-1", "devtool-base", 1,
    )
    prepared = ssh_execution.SSHExecutionPreparation(
        remote_root=ws.remote_path, worker=_probe(), workspace=ws, workspace_status={}, bootstrap={}, sync={"ok": True},
        toolchain={"ok": True, "kind": "generic"}, lease=lease.as_dict(), run_id="run-1", session_prefix="devtool-base",
    )
    monkeypatch.setattr(ssh_execution, "prepare_ssh_command_execution", lambda *a, **kw: prepared)
    spec = CommandSpec("build", ["true"], cwd=str(repo), env={"API_TOKEN": "secret"}, metadata={})
    wrapped = ssh_tmux_runner.wrap_specs_for_ssh_commands(
        [spec], cfg, project_config=SimpleNamespace(runner_options={}), repo_root=repo, dry_run=True,
    )
    shell = wrapped[0].argv[-1]
    assert "trap " in shell and " INT TERM" in shell
    assert "secret-env.sh" in shell
    assert "/.leases/repo-id.lock" in shell
    assert "exit 130" in shell
