from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from devtool import oci_resilience, ssh_resilience, ssh_returns, ssh_tmux_runner
from devtool.models import CommandSpec
from devtool.ssh_tmux_runner import SSHRunnerConfig
from devtool.execution_providers import SSH_PROOT_OCI


def _cfg(**overrides) -> SSHRunnerConfig:
    values = dict(
        host="tablet", port=8022, user="u0_a123", profile="tablet", mode="commands",
        worker_tmux=True, workspace_mode="managed", workspace_root="~/.cache/devtool/workspaces",
        sync_mode="incremental", sync_delete=True, sync_default_excludes=True,
        sync_excludes=(), bootstrap_policy="missing", execution_provider=SSH_PROOT_OCI,
    )
    values.update(overrides)
    return SSHRunnerConfig(**values)


def _oci_meta() -> dict[str, object]:
    return {
        "execution_mode": "ssh-proot-oci",
        "execution_provider": "ssh-proot-oci",
        "ssh_profile": "tablet",
        "ssh_destination": "u0_a123@tablet",
        "ssh_workspace_id": "repo-id",
        "ssh_workspace_path": "/remote/workspaces/repo-id",
        "ssh_source_sync_id": "sync-123",
        "ssh_source_revision": {"git_head": "abc", "working_tree_dirty": True},
        "oci_runtime": "proot-distro-oci",
        "oci_container": "devtool-android-api36",
        "oci_architecture": "aarch64",
        "oci_image_reference": "devtool/android-build:arm64-api36-v2",
        "oci_image_digest": "sha256:" + "a" * 64,
        "oci_image_archive_sha256": "b" * 64,
        "oci_image_archive_size": 123,
        "oci_definition_digest": "c" * 64,
        "oci_build_context_sha256": "d" * 64,
        "oci_guest_workspace": "/workspace",
        "oci_guest_cache": "/var/cache/devtool",
        "PD_DOCKER_AUTH": "top-secret-registry-json",
    }


def test_oci06_phase_metadata_persists_canonical_provenance_without_secrets() -> None:
    lines = oci_resilience.phase_metadata_lines(_oci_meta(), run_id="run-1", session="phase-1")
    joined = "\n".join(lines)
    assert "execution_provider=ssh-proot-oci" in joined
    assert "oci_image_reference=devtool/android-build:arm64-api36-v2" in joined
    assert "oci_build_context_sha256=" + "d" * 64 in joined
    assert "ssh_source_revision_digest=" in joined
    assert "oci_phase_node_id=oci-phase-" in joined
    assert "top-secret" not in joined and "PD_DOCKER_AUTH" not in joined


def test_oci06_remote_phase_record_includes_provider_node_and_image_identity() -> None:
    meta = _oci_meta()
    script = ssh_tmux_runner._build_remote_phase_script(
        _cfg(), session="phase-1", command="true", run_id="run-1",
        workspace_id="repo-id", workspace_path="/remote/workspaces/repo-id",
        lease_dir="/remote/workspaces/.leases/repo-id.lock", phase_metadata=meta,
    )
    assert "oci_phase_node_id=oci-phase-" in script
    assert "execution_provider=ssh-proot-oci" in script
    assert "oci_image_digest=sha256:" + "a" * 64 in script
    assert "top-secret" not in script and "PD_DOCKER_AUTH" not in script


def test_oci06_runs_payload_parses_extended_oci_record_and_node_target_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = (
        "run-1\tphase-a\trunning\t\t1\trepo\t/remote\t200\t/lease\tssh-proot-oci\tssh-proot-oci\tnode-a\tsync-a\tdevtool-android-api36\tref\tdigest\tdef\tctx\n"
        "run-1\tphase-b\trunning\t\t1\trepo\t/remote\t199\t/lease\tssh-proot-oci\tssh-proot-oci\tnode-b\tsync-b\tdevtool-android-api36\tref\tdigest\tdef\tctx\n"
    )
    monkeypatch.setattr(ssh_resilience, "_run_remote", lambda *a, **kw: subprocess.CompletedProcess([], 0, stdout=stdout, stderr=""))
    payload = ssh_resilience.runs_payload(_cfg())
    assert payload["records"][0]["phase_node_id"] == "node-a"
    assert payload["records"][0]["oci_build_context_sha256"] == "ctx"
    selected = ssh_resilience._select_records(payload, run_id="run-1", node_id="node-b")
    assert [item["session"] for item in selected] == ["phase-b"]


def test_oci06_cancel_targets_canonical_provider_node(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [
        {"run_id": "run-1", "session": "phase-a", "phase_node_id": "node-a", "active": True, "state": "running", "started_unix": 2},
        {"run_id": "run-1", "session": "phase-b", "phase_node_id": "node-b", "active": True, "state": "running", "started_unix": 1},
    ]
    monkeypatch.setattr(ssh_resilience, "runs_payload", lambda cfg: {"records": records})
    scripts: list[str] = []
    monkeypatch.setattr(ssh_resilience, "_run_remote", lambda cfg, script, **kw: scripts.append(script) or subprocess.CompletedProcess([], 0, stdout="", stderr=""))
    payload = ssh_resilience.cancel_remote_run(_cfg(), run_id="run-1", node_id="node-b")
    assert [item["session"] for item in payload["cancelled"]] == ["phase-b"]
    assert "phase-b" in scripts[0] and "phase-a" not in scripts[0]


def test_oci06_return_manifest_has_verified_producer_identity_and_redacts_registry_auth(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    bundle = ssh_returns.create_return_bundle(
        _cfg(operation_id="op"), local_root=repo, host_cwd=str(repo), remote_root="/remote/repo",
        session="phase-1", project_config=SimpleNamespace(runner_options={}), spec_metadata={},
        source_sync_id="sync-123", provider_metadata=_oci_meta(),
    )
    payload = json.loads(bundle.manifest_path.read_text())
    assert payload["producer"]["execution_provider"] == "ssh-proot-oci"
    assert payload["producer"]["oci_container"] == "devtool-android-api36"
    assert payload["producer"]["source_sync_id"] == "sync-123"
    assert payload["producer_sha256"] == oci_resilience.stable_json_digest(payload["producer"])
    assert bundle.metadata()["ssh_return_producer_sha256"] == payload["producer_sha256"]
    assert "top-secret" not in json.dumps(payload)
    ssh_returns._read_manifest(bundle.manifest_path)


def test_oci06_return_manifest_rejects_tampered_producer_identity(tmp_path: Path) -> None:
    root = tmp_path / ".devtool" / "ssh-return" / "run" / "phase"
    root.mkdir(parents=True)
    manifest = root / "manifest.json"
    payload = {
        "kind": "ssh-return-bundle", "destination": "u0_a123@tablet", "remote_root": "/remote/repo",
        "local_return_repo_root": str(root / "repo"), "patterns": [], "run_id": "run", "session": "phase",
        "producer": {"execution_provider": "ssh-proot-oci", "phase_node_id": "node"},
        "producer_sha256": "bad",
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="producer identity digest mismatch"):
        ssh_returns._read_manifest(manifest)


def test_oci06_secret_transport_keeps_pd_docker_auth_out_of_metadata_and_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], str | None]] = []
    monkeypatch.setattr(ssh_tmux_runner, "_ensure_remote_dir", lambda *a, **kw: None)

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs.get("input")))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    remote = ssh_tmux_runner._stage_sensitive_env(
        _cfg(), session="phase-1", run_id="run-1", values={"PD_DOCKER_AUTH": "top-secret-registry-json"}, dry_run=False,
    )
    argv, stdin = calls[0]
    assert remote.endswith("/secret-env.sh")
    assert "top-secret" not in " ".join(argv)
    assert stdin and "PD_DOCKER_AUTH" in stdin and "top-secret-registry-json" in stdin
    safe = oci_resilience.phase_metadata_lines({"PD_DOCKER_AUTH": "top-secret-registry-json"}, run_id="run-1", session="phase-1")
    assert safe == ("oci_phase_node_id=" + oci_resilience.phase_node_id("run-1", "phase-1"),)


def test_oci06_native_return_bundle_still_validates_without_oci_producer(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    cfg = _cfg(execution_provider="ssh-native")
    bundle = ssh_returns.create_return_bundle(
        cfg, local_root=repo, host_cwd=str(repo), remote_root="/remote/repo", session="native-phase",
        project_config=SimpleNamespace(runner_options={}), spec_metadata={"execution_mode": "ssh-command"}, source_sync_id="sync-native",
    )
    payload = json.loads(bundle.manifest_path.read_text())
    assert payload["producer"]["execution_provider"] == "ssh-command"
    ssh_returns._read_manifest(bundle.manifest_path)
