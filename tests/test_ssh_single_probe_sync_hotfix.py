from __future__ import annotations

from pathlib import Path

from devtool import ssh_execution, ssh_workspace
from devtool.models import CommandSpec
from devtool.ssh_tmux_runner import SSHRunnerConfig
from devtool.ssh_worker import SSHWorkerProbe
from devtool.ssh_workspace import SSHWorkspaceSpec


def _probe() -> SSHWorkerProbe:
    return SSHWorkerProbe(
        profile="tablet", destination="u0_a123@tablet", port=8022,
        platform="termux", os_name="Linux", architecture="aarch64",
        cpu_count=8, memory_kib=8 * 1024 * 1024, free_storage_kib=64 * 1024 * 1024,
        home="/data/data/com.termux/files/home", prefix="/data/data/com.termux/files/usr",
        shell="/data/data/com.termux/files/usr/bin/bash", package_manager="pkg",
        tools={"python": "/p/python", "tmux": "/p/tmux", "rsync": "/p/rsync", "git": "/p/git", "java": "", "gradle": ""},
        versions={}, bootstrap_policy="missing", tmux_required=True,
    )


def _config() -> SSHRunnerConfig:
    return SSHRunnerConfig(
        host="tablet", port=8022, user="u0_a123", profile="tablet", mode="commands",
        worker_tmux=True, workspace_mode="managed", workspace_root="~/.cache/devtool/workspaces",
        sync_mode="incremental", sync_delete=True, sync_default_excludes=True, sync_excludes=(),
        bootstrap_policy="missing",
    )


def _workspace(repo: Path) -> SSHWorkspaceSpec:
    return SSHWorkspaceSpec(
        profile="tablet", destination="u0_a123@tablet", mode="managed",
        repo_root=str(repo), repo_name="repo", workspace_id="repo-deadbeef",
        instance_id="repo-deadbeef", workspace_root="/home/u/.cache/devtool/workspaces",
        remote_path="/home/u/.cache/devtool/workspaces/repo-deadbeef", delete=True,
        sync_mode="incremental", excludes=(".gradle/", "build/"),
    )


def test_sync_payload_probes_once_and_threads_worker(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    probe = _probe()
    calls = {"probe": 0}
    seen = {}

    def fake_probe(config):
        calls["probe"] += 1
        return probe

    monkeypatch.setattr(ssh_workspace, "probe_worker", fake_probe)
    monkeypatch.setattr(ssh_workspace, "resolve_workspace_spec", lambda *a, **k: _workspace(repo))

    def fake_sync(config, spec, **kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(ssh_workspace, "sync_workspace", fake_sync)
    result = ssh_workspace.sync_payload(_config(), repo, dry_run=False)

    assert result == {"ok": True}
    assert calls["probe"] == 1
    assert seen["worker"] is probe


def test_rsync_argv_uses_supplied_worker_without_reprobe(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    worker = _probe()
    monkeypatch.setattr(ssh_workspace, "probe_worker", lambda config: (_ for _ in ()).throw(AssertionError("unexpected probe")))
    monkeypatch.setattr(ssh_workspace, "_rsync_executable", lambda: "/usr/bin/rsync")

    argv = ssh_workspace._rsync_argv(
        _config(), _workspace(repo), dry_run=True,
        destination_override="u0_a123@tablet:/remote/repo/", worker=worker,
    )

    assert "--rsync-path=/p/rsync" in argv
    assert "--dry-run" in argv


def test_execution_reuses_initial_worker_for_managed_sync(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    worker = _probe()
    calls = {"probe": 0}
    seen = {}

    def fake_probe(config):
        calls["probe"] += 1
        return worker

    monkeypatch.setattr(ssh_execution, "probe_worker", fake_probe)
    monkeypatch.setattr(ssh_execution, "ensure_remote_capacity", lambda *a, **k: {"ok": True})
    ws = _workspace(repo)
    monkeypatch.setattr(ssh_execution, "resolve_workspace_spec", lambda *a, **k: ws)

    def fake_sync(config, spec, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "status_after": {"canonical_path": "/canonical/repo"}, "lease": {}}

    monkeypatch.setattr(ssh_execution, "sync_workspace", fake_sync)
    monkeypatch.setattr(ssh_execution, "ensure_remote_toolchain", lambda *a, **k: {"ok": True, "kind": "generic"})
    spec = CommandSpec("build", ["python", "builder.py"], cwd=str(repo), metadata={"runner": "python"})

    result = ssh_execution.prepare_ssh_command_execution(_config(), [spec], repo_root=repo)

    assert result.remote_root == "/canonical/repo"
    assert calls["probe"] == 1
    assert seen["worker"] is worker
