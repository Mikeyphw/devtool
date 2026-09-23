from __future__ import annotations

from pathlib import Path
import subprocess
import io

import pytest
from rich.console import Console

from devtool.cli import build_parser
from devtool.execution_providers import (
    NATIVE,
    SSH_NATIVE,
    SSH_PROOT_OCI,
    ensure_execution_provider_ready,
    execution_provider,
    execution_provider_registry,
    normalize_execution_provider,
    ExecutionProviderContext,
    ExecutionProviderAdapter,
)
from devtool.ssh_profiles import add_profile, list_profiles
from devtool.ssh_tmux_runner import SSHRunnerConfig, resolve_ssh_config
from devtool import ssh_execution, ssh_worker
from devtool.tui import render_page


def _probe_stdout() -> str:
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
            "tool_tmux=/data/data/com.termux/files/usr/bin/tmux",
            "tool_rsync=/data/data/com.termux/files/usr/bin/rsync",
            "tool_git=/data/data/com.termux/files/usr/bin/git",
            "tool_java=",
            "tool_gradle=",
            "version_python=Python 3.14.0",
            "version_tmux=tmux 3.5a",
            "version_rsync=rsync version 3.4.1",
            "version_git=git version 2.51.0",
            "version_java=",
            "version_gradle=",
        ]
    ) + "\n"



def test_provider_context_requires_canonical_exo_run_and_node_identity() -> None:
    context = ExecutionProviderContext(
        provider=SSH_PROOT_OCI,
        repo_root="/repo",
        working_root="/workspace",
        profile="tablet",
        run_id="run-123",
        node_id="workflow:validate",
        parent_node_id="workflow:root",
    )
    assert context.require_exo_identity() == ("run-123", "workflow:validate")
    assert context.parent_node_id == "workflow:root"

    missing = ExecutionProviderContext(provider=SSH_PROOT_OCI, repo_root="/repo")
    with pytest.raises(ValueError, match="canonical EXO run_id and node_id"):
        missing.require_exo_identity()


def test_provider_contract_forbids_provider_local_execution_truth() -> None:
    import inspect

    contract = inspect.getdoc(ExecutionProviderAdapter) or ""
    assert "second execution state machine" in contract
    assert "canonical EXO events" in contract
    assert "Provider provenance is attached" in contract

def test_provider_registry_has_stable_native_ssh_and_proot_oci_contracts() -> None:
    providers = {item.name: item for item in execution_provider_registry()}
    assert set(providers) == {NATIVE, SSH_NATIVE, SSH_PROOT_OCI}
    assert providers[SSH_NATIVE].capabilities.requires_tmux is True
    assert providers[SSH_NATIVE].capabilities.containerized is False
    assert providers[SSH_PROOT_OCI].capabilities.containerized is True
    assert providers[SSH_PROOT_OCI].capabilities.requires_image is True
    assert providers[SSH_PROOT_OCI].executable is True


def test_remote_provider_aliases_are_canonical_and_native_means_ssh_native() -> None:
    assert normalize_execution_provider("", remote=True) == SSH_NATIVE
    assert normalize_execution_provider("native", remote=True) == SSH_NATIVE
    assert normalize_execution_provider("ssh", remote=True) == SSH_NATIVE
    assert normalize_execution_provider("proot-oci", remote=True) == SSH_PROOT_OCI
    assert normalize_execution_provider("ssh_proot_oci", remote=True) == SSH_PROOT_OCI
    with pytest.raises(ValueError, match="Unknown execution provider"):
        normalize_execution_provider("docker-magic", remote=True)


def test_proot_oci_provider_is_configuration_visible_and_executable() -> None:
    provider = execution_provider("proot-oci", remote=True)
    assert provider.name == SSH_PROOT_OCI
    assert ensure_execution_provider_ready(provider.name, remote=True) is provider


def test_named_profile_execution_section_resolves_and_lists_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    global_path = tmp_path / "global.toml"
    monkeypatch.setenv("DEVTOOL_GLOBAL_CONFIG", str(global_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    result = add_profile(
        "tablet",
        host="192.168.15.13",
        port=8022,
        user="u0_a123",
        repo=repo,
        execution_provider="proot-oci",
    )
    assert result["execution_provider"] == SSH_PROOT_OCI
    text = global_path.read_text(encoding="utf-8")
    assert "[ssh.hosts.tablet.execution]" in text
    assert 'provider = "ssh-proot-oci"' in text

    cfg = resolve_ssh_config(["--repo", str(repo), "--ssh-profile", "tablet"], cwd=str(repo), env={})
    assert cfg.execution_provider == SSH_PROOT_OCI
    listing = list_profiles(repo=repo)
    assert listing["records"][0]["execution_provider"] == SSH_PROOT_OCI


def test_existing_named_profiles_default_to_ssh_native(tmp_path: Path) -> None:
    (tmp_path / ".devtool.toml").write_text(
        '''[ssh]\nprofile = "tablet"\n\n[ssh.hosts.tablet]\nhost = "tablet"\nmode = "commands"\n\n[ssh.hosts.tablet.worker]\ntmux = true\n\n[ssh.hosts.tablet.workspace]\nmode = "managed"\n''',
        encoding="utf-8",
    )
    cfg = resolve_ssh_config(["--repo", str(tmp_path)], cwd=str(tmp_path), env={})
    assert cfg.execution_provider == SSH_NATIVE


def test_ssh_add_cli_accepts_provider_alias() -> None:
    args = build_parser().parse_args(
        ["ssh", "add", "tablet", "--host", "tablet", "--execution-provider", "proot-oci"]
    )
    assert args.execution_provider == "proot-oci"


def test_doctor_reports_active_provider_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = SSHRunnerConfig(
        host="tablet",
        profile="tablet",
        worker_tmux=True,
        workspace_mode="managed",
        execution_provider=SSH_PROOT_OCI,
    )
    monkeypatch.setattr(
        ssh_worker,
        "_run_remote",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, stdout=_probe_stdout(), stderr=""),
    )
    monkeypatch.setattr(ssh_worker, "list_tmux_sessions", lambda _config: [])
    payload = ssh_worker.worker_doctor_payload(cfg)
    assert payload["execution"]["provider"]["name"] == SSH_PROOT_OCI
    assert payload["execution"]["provider"]["capabilities"]["containerized"] is True
    assert payload["execution"]["provider"]["executable"] is True
    assert payload["healthy"] is True


def test_command_execution_rejects_proot_provider_without_managed_workspace_before_remote_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = SSHRunnerConfig(
        host="tablet",
        profile="tablet",
        execution_provider=SSH_PROOT_OCI,
        workspace_mode="existing",
    )
    called = False

    def unexpected_probe(_config):
        nonlocal called
        called = True
        raise AssertionError("probe should not run before OCI workspace validation")

    monkeypatch.setattr(ssh_execution, "probe_worker", unexpected_probe)
    with pytest.raises(ValueError, match="workspace.mode='managed'"):
        ssh_execution.prepare_ssh_command_execution(cfg, [], repo_root=tmp_path)
    assert called is False


def test_workers_tui_surfaces_execution_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from devtool import ssh_profiles

    repo = tmp_path / "repo-tui"
    repo.mkdir()
    (repo / ".devtool.toml").write_text(
        """[ssh]\nprofile = \"tablet\"\n\n[ssh.hosts.tablet]\nhost = \"10.0.0.2\"\nport = 8022\nmode = \"commands\"\n\n[ssh.hosts.tablet.worker]\ntmux = true\n\n[ssh.hosts.tablet.workspace]\nmode = \"managed\"\n\n[ssh.hosts.tablet.execution]\nprovider = \"proot-oci\"\n""",
        encoding="utf-8",
    )
    monkeypatch.setattr(ssh_profiles, "global_config_path", lambda: tmp_path / "missing-global.toml")
    renderable, _profile = render_page(repo, page="workers", width=120, height=30)
    stream = io.StringIO()
    Console(file=stream, width=120, color_system=None, force_terminal=False).print(renderable)
    assert "ssh-proot-oci" in stream.getvalue()
