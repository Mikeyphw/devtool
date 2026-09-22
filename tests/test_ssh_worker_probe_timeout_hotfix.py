from __future__ import annotations

import subprocess

import pytest

from devtool import ssh_execution, ssh_worker
from devtool.ssh_tmux_runner import SSHRunnerConfig


def test_capability_probe_bounds_optional_java_gradle_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    stdout = "\n".join([
        "platform=termux", "os_name=Linux", "architecture=aarch64", "cpu_count=8",
        "memory_kib=8388608", "free_storage_kib=52428800",
        "home=/data/data/com.termux/files/home", "prefix=/data/data/com.termux/files/usr",
        "shell=/data/data/com.termux/files/usr/bin/bash", "package_manager=pkg",
        "tool_python=/data/data/com.termux/files/usr/bin/python",
        "tool_tmux=/data/data/com.termux/files/usr/bin/tmux",
        "tool_rsync=/data/data/com.termux/files/usr/bin/rsync",
        "tool_git=/data/data/com.termux/files/usr/bin/git",
        "tool_java=/data/data/com.termux/files/usr/bin/java",
        "tool_gradle=/data/data/com.termux/files/usr/bin/gradle",
        "version_python=Python 3.13", "version_tmux=tmux 3.5", "version_rsync=rsync 3.4",
        "version_git=git version 2.51", "version_java=", "version_gradle=",
    ]) + "\n"

    def fake_run(config, script, **kwargs):
        seen.append(script)
        return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")

    monkeypatch.setattr(ssh_worker, "_run_remote", fake_run)
    ssh_worker.probe_worker(SSHRunnerConfig(host="tablet", user="termux", profile="tablet", worker_tmux=True))
    script = seen[0]
    assert 'tool_timeout="$(find_cmd timeout)"' in script
    assert '"$tool_timeout" 5 "$tool_java" -version' in script
    assert '"$tool_timeout" 5 "$tool_gradle" --version' in script


def test_capability_probe_timeout_is_user_facing(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["ssh"], timeout=30.0)

    monkeypatch.setattr(ssh_worker, "_run_remote", timeout)
    with pytest.raises(ValueError, match=r"SSH worker capability probe timed out after 30s"):
        ssh_worker.probe_worker(SSHRunnerConfig(host="tablet", user="termux", profile="tablet", worker_tmux=True))


def test_gradle_toolchain_probe_bounds_heavy_version_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_run(config, script, **kwargs):
        seen.append(script)
        return subprocess.CompletedProcess([], 0, stdout="java=/x/java\ngradle=/x/gradle\njava_version=openjdk version 21\ngradle_version=9.7.1\n", stderr="")

    monkeypatch.setattr(ssh_execution, "_run_remote", fake_run)
    payload = ssh_execution._remote_gradle_probe(SSHRunnerConfig(host="tablet", user="termux", profile="tablet"))
    assert payload["gradle_version"] == "9.7.1"
    script = seen[0]
    assert 'timeout_cmd="$(command -v timeout 2>/dev/null || true)"' in script
    assert '"$timeout_cmd" 10 "$java_cmd" -version' in script
    assert '"$timeout_cmd" 10 "$gradle_cmd" --version' in script
