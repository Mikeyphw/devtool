from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from devtool.models import CommandSpec
from devtool import ssh_execution, ssh_tmux_runner
from devtool.ssh_execution import SSHExecutionPreparation
from devtool.ssh_tmux_runner import SSHRunnerConfig
from devtool.ssh_worker import SSHWorkerProbe
from devtool.ssh_workspace import SSHWorkspaceSpec


def _probe(*, ready: bool = True, java: str = "", gradle: str = "") -> SSHWorkerProbe:
    return SSHWorkerProbe(
        profile="tablet", destination="u0_a123@tablet", port=8022,
        platform="termux", os_name="Linux", architecture="aarch64",
        cpu_count=8, memory_kib=12 * 1024 * 1024, free_storage_kib=64 * 1024 * 1024,
        home="/data/data/com.termux/files/home", prefix="/data/data/com.termux/files/usr",
        shell="/data/data/com.termux/files/usr/bin/bash", package_manager="pkg",
        tools={
            "python": "/data/data/com.termux/files/usr/bin/python" if ready else "",
            "tmux": "/data/data/com.termux/files/usr/bin/tmux" if ready else "",
            "rsync": "/data/data/com.termux/files/usr/bin/rsync" if ready else "",
            "git": "/data/data/com.termux/files/usr/bin/git" if ready else "",
            "java": java,
            "gradle": gradle,
        },
        versions={}, bootstrap_policy="missing", tmux_required=True,
    )


def _config(**overrides) -> SSHRunnerConfig:
    values = dict(
        host="tablet", port=8022, user="u0_a123", profile="tablet", mode="commands",
        worker_tmux=True, workspace_mode="managed", workspace_root="~/.cache/devtool/workspaces",
        sync_mode="incremental", sync_delete=True, sync_default_excludes=True,
        sync_excludes=(), bootstrap_policy="missing",
    )
    values.update(overrides)
    return SSHRunnerConfig(**values)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / "gradlew").chmod(0o755)
    return repo


def _workspace(repo: Path) -> SSHWorkspaceSpec:
    return SSHWorkspaceSpec(
        profile="tablet", destination="u0_a123@tablet", mode="managed",
        repo_root=str(repo), repo_name="repo", workspace_id="repo-deadbeef",
        instance_id="repo-deadbeef", workspace_root="/home/u/.cache/devtool/workspaces",
        remote_path="/home/u/.cache/devtool/workspaces/repo-deadbeef", delete=True,
        sync_mode="incremental", excludes=(".gradle/", "build/"),
    )


def test_managed_execution_bootstraps_then_syncs_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    cfg = _config()
    spec = CommandSpec("build", ["python3", "builder.py"], cwd=str(repo), metadata={"runner": "python"})
    calls: list[str] = []
    probes = iter([_probe(ready=False), _probe(ready=True)])
    monkeypatch.setattr(ssh_execution, "probe_worker", lambda config: next(probes))
    monkeypatch.setattr(ssh_execution, "bootstrap_worker", lambda config, dry_run=False: calls.append("bootstrap") or {"ok": True, "changed": True})
    ws = _workspace(repo)
    monkeypatch.setattr(ssh_execution, "resolve_workspace_spec", lambda *a, **kw: ws)
    monkeypatch.setattr(
        ssh_execution,
        "sync_workspace",
        lambda *a, **kw: calls.append("sync") or {
            "ok": True, "change_count": 7,
            "status_after": {"canonical_path": "/canonical/repo"},
        },
    )
    monkeypatch.setattr(ssh_execution, "ensure_remote_toolchain", lambda *a, **kw: calls.append("toolchain") or {"ok": True, "kind": "generic", "detail": "ready"})

    prepared = ssh_execution.prepare_ssh_command_execution(cfg, [spec], repo_root=repo)
    assert calls == ["bootstrap", "sync", "toolchain"]
    assert prepared.remote_root == "/canonical/repo"
    assert prepared.metadata()["ssh_sync_change_count"] == 7


def test_android_toolchain_is_delegated_to_staged_remote_builder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = CommandSpec(
        "build", ["python3", "/tmp/build-apk", str(tmp_path)], cwd=str(tmp_path),
        metadata={"runner": "android", "android_builder": "/tmp/build-apk"},
    )
    result = ssh_execution.ensure_remote_toolchain(_config(), [spec], worker=_probe(), dry_run=False)
    assert result["kind"] == "android"
    assert result["owner"] == "staged-android-builder"
    assert "$HOME/.gradle" in result["persistent_caches"]


def test_gradle_toolchain_with_wrapper_provisions_only_required_jdk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    spec = CommandSpec(
        "build", ["/phone/bin/gradle", "build"], cwd=str(repo),
        metadata={"runner": "gradle", "gradle_direct": True, "java_requirement_min": 17},
    )
    monkeypatch.setattr(ssh_execution, "_remote_gradle_probe", lambda config: {"java": "", "java_major": 0, "gradle": "", "gradle_version": ""})
    seen: list[list[str]] = []
    monkeypatch.setattr(
        ssh_execution,
        "_install_termux_packages",
        lambda config, worker, packages, dry_run: seen.append(list(packages)) or {"ok": True, "changed": False, "dry_run": True, "packages": packages},
    )
    result = ssh_execution.ensure_remote_toolchain(_config(), [spec], worker=_probe(), dry_run=True)
    assert seen == [["openjdk-17"]]
    assert result["wrapper"] is True


def test_gradle_toolchain_without_wrapper_plans_jdk_and_gradle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = CommandSpec(
        "build", ["/phone/bin/gradle", "build"], cwd=str(repo),
        metadata={"runner": "gradle", "gradle_direct": True, "java_requirement_min": 21},
    )
    monkeypatch.setattr(ssh_execution, "_remote_gradle_probe", lambda config: {"java": "", "java_major": 0, "gradle": "", "gradle_version": ""})
    seen: list[list[str]] = []
    monkeypatch.setattr(
        ssh_execution,
        "_install_termux_packages",
        lambda config, worker, packages, dry_run: seen.append(list(packages)) or {"ok": True, "changed": False, "dry_run": True, "packages": packages},
    )
    result = ssh_execution.ensure_remote_toolchain(_config(), [spec], worker=_probe(), dry_run=True)
    assert seen == [["openjdk-21", "gradle"]]
    assert result["wrapper"] is False


def test_exact_unsupported_java_fails_closed() -> None:
    with pytest.raises(ValueError, match="exact Java 11"):
        ssh_execution._termux_jdk_package(11, 11)


def test_named_managed_wrap_uses_synced_root_and_project_wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    cfg = _config()
    workspace = _workspace(repo)
    prepared = SSHExecutionPreparation(
        remote_root="/remote/work/repo", worker=_probe(), workspace=workspace,
        workspace_status={"canonical_path": "/remote/work/repo"}, bootstrap={},
        sync={"ok": True, "change_count": 3},
        toolchain={"ok": True, "kind": "gradle", "detail": "ready"},
    )
    monkeypatch.setattr(ssh_execution, "prepare_ssh_command_execution", lambda *a, **kw: prepared)
    spec = CommandSpec(
        "build", ["/data/data/com.termux/files/usr/bin/gradle", "build"], cwd=str(repo),
        metadata={"runner": "gradle", "gradle_direct": True},
    )
    wrapped = ssh_tmux_runner.wrap_specs_for_ssh_commands(
        [spec], cfg, project_config=SimpleNamespace(runner_options={}), repo_root=repo, dry_run=True,
    )
    assert len(wrapped) == 1
    script = wrapped[0].argv[-1]
    assert "/remote/work/repo/gradlew" in script
    assert "sh /remote/work/repo/gradlew" in script or "sh '/remote/work/repo/gradlew'" in script
    assert wrapped[0].metadata["ssh_workspace_path"] == "/remote/work/repo"
    assert wrapped[0].metadata["ssh_sync_change_count"] == 3
    assert wrapped[0].metadata["ssh_gradle_launcher"] == "wrapper-via-sh"
    assert wrapped[0].metadata["ssh_worker_cpu_count"] == 8


def test_raw_unmanaged_ssh_keeps_legacy_explicit_cwd(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    cfg = SSHRunnerConfig(host="tablet", user="u", mode="commands", cwd="/remote/legacy")
    spec = CommandSpec("test", ["python3", "-c", "print('ok')"], cwd=str(repo), metadata={})
    wrapped = ssh_tmux_runner.wrap_specs_for_ssh_commands(
        [spec], cfg, project_config=SimpleNamespace(runner_options={}), repo_root=repo, dry_run=True,
    )
    assert "/remote/legacy" in wrapped[0].argv[-1]
    assert "ssh_workspace_id" not in wrapped[0].metadata


def test_remote_gradle_probe_parses_java_and_gradle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ssh_execution,
        "_run_remote",
        lambda *a, **kw: subprocess.CompletedProcess([], 0, stdout='java=/bin/java\ngradle=/bin/gradle\njava_version=openjdk version "21.0.8"\ngradle_version=9.1.0\n', stderr=""),
    )
    result = ssh_execution._remote_gradle_probe(_config())
    assert result["java_major"] == 21
    assert result["gradle_version"] == "9.1.0"

def test_gradle_without_wrapper_rejects_wrong_remote_gradle_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo-no-wrapper"
    repo.mkdir()
    spec = CommandSpec(
        "build", ["gradle", "build"], cwd=str(repo),
        metadata={
            "runner": "gradle", "gradle_direct": True,
            "java_requirement_min": 17, "gradle_required_version": "9.7.0",
        },
    )
    monkeypatch.setattr(
        ssh_execution,
        "_remote_gradle_probe",
        lambda config: {
            "java": "/bin/java", "java_major": 17,
            "gradle": "/bin/gradle", "gradle_version": "8.12",
        },
    )
    with pytest.raises(ValueError, match="Gradle 8.12.*9.7.0"):
        ssh_execution.ensure_remote_toolchain(_config(), [spec], worker=_probe(), dry_run=False)

def test_managed_multi_phase_plan_prepares_worker_only_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    cfg = _config()
    workspace = _workspace(repo)
    calls = 0

    def fake_prepare(*args, **kwargs):
        nonlocal calls
        calls += 1
        return SSHExecutionPreparation(
            remote_root="/remote/work/repo", worker=_probe(), workspace=workspace,
            workspace_status={"canonical_path": "/remote/work/repo"}, bootstrap={},
            sync={"ok": True, "change_count": 2},
            toolchain={"ok": True, "kind": "generic", "detail": "ready"},
        )

    monkeypatch.setattr(ssh_execution, "prepare_ssh_command_execution", fake_prepare)
    specs = [
        CommandSpec("compile", ["python3", "-c", "print('compile')"], cwd=str(repo), metadata={}),
        CommandSpec("test", ["python3", "-c", "print('test')"], cwd=str(repo), metadata={}),
    ]
    wrapped = ssh_tmux_runner.wrap_specs_for_ssh_commands(
        specs, cfg, project_config=SimpleNamespace(runner_options={}), repo_root=repo, dry_run=True,
    )
    assert calls == 1
    assert len(wrapped) == 2
    assert all(item.metadata["ssh_workspace_path"] == "/remote/work/repo" for item in wrapped)
    assert wrapped[0].metadata["ssh_session"] != wrapped[1].metadata["ssh_session"]
    assert wrapped[0].metadata["ssh_session"].split("-01-")[0] == wrapped[1].metadata["ssh_session"].split("-02-")[0]
