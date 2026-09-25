from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from devtool.config import load_project_config
from devtool.execution_providers import SSH_NATIVE
from devtool.federation_transport import (
    FederationTransportError,
    build_federation_transport_metadata,
    federation_transport_provenance,
)
from devtool.models import CommandSpec
from devtool.oci_resilience import producer_identity
from devtool.repository_identity import build_repository_identity
from devtool.runners import _execution_provenance
from devtool.ssh_tmux_runner import SSHRunnerConfig, build_remote_tmux_script, wrap_specs_for_ssh_commands
from devtool.termux_chroot_runner import wrap_specs_for_termux_host_chroot


def _child(tmp_path: Path) -> tuple[Path, dict, dict]:
    root = tmp_path / "child"
    root.mkdir()
    (root / ".devtool.toml").write_text('[project]\nrunner="command"\n', encoding="utf-8")
    identity = build_repository_identity(root, root / ".devtool.toml").as_dict()
    lineage = {
        "kind": "federation",
        "relationship": "child",
        "parent_run_id": "federation-parent",
        "parent_node_id": "federation:release:child",
        "child_run_id": "fed-child-1",
        "federation_workflow": "release",
        "federation_step": "child",
        "repository": "child",
        "source_repository": "source-parent-fingerprint",
        "requested_repository": "child",
        "effective_repository": identity["fingerprint"],
    }
    return root, identity, lineage


def _metadata(identity: dict, lineage: dict) -> dict:
    return {"repository_identity": dict(identity), "run_lineage": dict(lineage)}


def _config(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True, allow_ambiguous_project_kind=True)


def test_transport_contract_requires_provider_resolved_remote_root(tmp_path: Path) -> None:
    root, identity, lineage = _child(tmp_path)
    with pytest.raises(FederationTransportError, match="requires an explicit provider-resolved remote repository root"):
        build_federation_transport_metadata(
            _metadata(identity, lineage),
            local_root=root,
            execution_environment="ssh-command",
            remote_root="",
        )


def test_transport_contract_rejects_stale_identity_and_wrong_local_root(tmp_path: Path) -> None:
    root, identity, lineage = _child(tmp_path)
    stale = dict(lineage, effective_repository="0" * 64)
    with pytest.raises(FederationTransportError, match="identity mismatch"):
        build_federation_transport_metadata(
            _metadata(identity, stale), local_root=root, execution_environment="local", remote_root=root,
        )
    outside = tmp_path / "other"; outside.mkdir()
    with pytest.raises(FederationTransportError, match="outside repository identity root"):
        build_federation_transport_metadata(
            _metadata(identity, lineage), local_root=outside, execution_environment="local", remote_root=outside,
        )


def test_unmanaged_ssh_uses_explicit_remote_child_root_and_preserves_identity(tmp_path: Path) -> None:
    root, identity, lineage = _child(tmp_path)
    spec = CommandSpec("echo", ["echo", "ok"], cwd=str(root), metadata=_metadata(identity, lineage))
    ssh = SSHRunnerConfig(
        host="worker.example",
        user="mike",
        cwd="/srv/build/child",
        mode="commands",
        profile="",
        copy_artifacts=False,
        execution_provider=SSH_NATIVE,
    )
    wrapped = wrap_specs_for_ssh_commands([spec], ssh, project_config=_config(root), repo_root=root, dry_run=True)
    meta = wrapped[0].metadata
    assert meta["federation_child_local_root"] == str(root.resolve())
    assert meta["federation_child_remote_root"] == "/srv/build/child"
    assert meta["federation_path_mapping"] == "ssh-workspace"
    assert meta["federation_child_repository_fingerprint"] == identity["fingerprint"]
    provenance = _execution_provenance(meta, wrapped[0].argv)
    assert provenance["transport"] == "ssh"
    assert provenance["federation"]["child_repository"] == "child"
    assert provenance["federation"]["child_remote_root"] == "/srv/build/child"


def test_termux_chroot_uses_translated_repository_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, identity, lineage = _child(tmp_path)
    spec = CommandSpec("echo", ["echo", "ok"], cwd=str(root), metadata=_metadata(identity, lineage))
    monkeypatch.setattr(
        "devtool.termux_chroot_runner.resolve_chroot_identity",
        lambda *args, **kwargs: SimpleNamespace(user="mike", home="/home/mike", shell="/bin/bash", source="test"),
    )
    wrapped = wrap_specs_for_termux_host_chroot(
        [spec], "androidos", repo_root=root,
        path_map=[(str(root.resolve()), "/home/mike/Code/child")],
        env={"DEVTOOL_CHROOTCTL": "chrootctl", "DEVTOOL_CHROOT_SUDO": ""},
    )
    meta = wrapped[0].metadata
    assert meta["federation_child_remote_root"] == "/home/mike/Code/child"
    assert meta["federation_path_mapping"] == "chroot-path-map"
    assert meta["federation_parent_run_id"] == "federation-parent"


def test_local_remote_parent_metadata_retains_child_identity(tmp_path: Path) -> None:
    root, identity, lineage = _child(tmp_path)
    meta = build_federation_transport_metadata(
        _metadata(identity, lineage),
        local_root=root,
        execution_environment="native-linux",
        remote_root=root,
        path_mapping="identity",
    )
    assert meta["federation_child_local_root"] == meta["federation_child_remote_root"]
    assert meta["federation_execution_environment"] == "native-linux"
    assert federation_transport_provenance(meta)["federation"]["parent_run_id"] == "federation-parent"


def test_ssh_profile_provenance_remains_orthogonal_to_child_identity(tmp_path: Path) -> None:
    root, identity, lineage = _child(tmp_path)
    base = _metadata(identity, lineage)
    transport = build_federation_transport_metadata(
        base, local_root=root, execution_environment="ssh-command", remote_root="/work/child",
        execution_provider="ssh-native",
    )
    meta = {**base, **transport, "execution_mode": "ssh-command", "ssh_profile": "builder-a", "ssh_destination": "a@example"}
    provenance = _execution_provenance(meta, ["echo"])
    assert provenance["profile"] == "builder-a"
    assert provenance["federation"]["child_repository_fingerprint"] == identity["fingerprint"]
    meta["ssh_profile"] = "builder-b"
    assert _execution_provenance(meta, ["echo"])["profile"] == "builder-b"
    assert _execution_provenance(meta, ["echo"])["federation"]["child_repository_fingerprint"] == identity["fingerprint"]


def test_return_producer_identity_associates_federation_child_and_actual_destination(tmp_path: Path) -> None:
    root, identity, lineage = _child(tmp_path)
    transport = build_federation_transport_metadata(
        _metadata(identity, lineage), local_root=root, execution_environment="ssh-command",
        remote_root="/work/child", execution_provider="ssh-native",
        artifact_return_root=root / ".devtool" / "ssh-return" / "fed-child-1" / "phase",
    )
    producer = producer_identity(
        profile="builder", destination="mike@worker", run_id="fed-child-1", session="phase",
        metadata={**_metadata(identity, lineage), **transport, "execution_mode": "ssh-command"},
    )
    assert producer["federation"]["child_repository"] == "child"
    assert producer["federation"]["child_repository_fingerprint"] == identity["fingerprint"]
    assert producer["federation"]["artifact_return_root"].endswith("/.devtool/ssh-return/fed-child-1/phase")


def test_remote_tmux_forwards_federation_lineage_environment(tmp_path: Path) -> None:
    root, identity, lineage = _child(tmp_path)
    ssh = SSHRunnerConfig(host="worker", user="mike", cwd="/srv/child", profile="builder")
    env = {
        "DEVTOOL_RUN_ID": "fed-child-1",
        "DEVTOOL_FEDERATION_RELATIONSHIP": "child",
        "DEVTOOL_FEDERATION_PARENT_RUN_ID": "federation-parent",
        "DEVTOOL_FEDERATION_REPOSITORY": "child",
        "DEVTOOL_FEDERATION_EFFECTIVE_REPOSITORY": identity["fingerprint"],
    }
    from devtool.federation_transport import build_repository_attestation, federation_attestation_environment
    env.update(federation_attestation_environment(build_repository_attestation(root, identity=identity)))
    with mock.patch.dict(os.environ, env, clear=False):
        script = build_remote_tmux_script(ssh, ["status"])
    assert "DEVTOOL_FEDERATION_PARENT_RUN_ID=federation-parent" in script
    assert "DEVTOOL_FEDERATION_REPOSITORY=child" in script
    assert f"DEVTOOL_FEDERATION_EFFECTIVE_REPOSITORY={identity['fingerprint']}" in script


def test_command_mode_attestation_probe_covers_ssh_chroot_proot_and_oci(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every remote command provider must attest the mapped child before phase execution."""
    from devtool.execution_providers import SSH_PROOT_OCI
    from devtool.oci_workspace import OciWorkspaceBind, OciWorkspacePlan
    from devtool.proot_distro_runner import ProotRunnerConfig, wrap_specs_for_proot_distro

    root, identity, lineage = _child(tmp_path)
    spec = CommandSpec("echo", ["echo", "ok"], cwd=str(root), metadata=_metadata(identity, lineage))
    project = _config(root)

    native = wrap_specs_for_ssh_commands(
        [spec],
        SSHRunnerConfig(
            host="worker.example", user="mike", cwd="/srv/build/child", mode="commands",
            profile="", copy_artifacts=False, execution_provider=SSH_NATIVE,
        ),
        project_config=project, repo_root=root, dry_run=True,
    )[0]
    assert "frx_expected_config" in " ".join(native.argv)
    assert native.metadata["federation_attestation_required"] is True

    monkeypatch.setattr(
        "devtool.termux_chroot_runner.resolve_chroot_identity",
        lambda *args, **kwargs: SimpleNamespace(user="mike", home="/home/mike", shell="/bin/bash", source="test"),
    )
    chroot = wrap_specs_for_termux_host_chroot(
        [spec], "androidos", repo_root=root,
        path_map=[(str(root.resolve()), "/home/mike/Code/child")],
        env={"DEVTOOL_CHROOTCTL": "chrootctl", "DEVTOOL_CHROOT_SUDO": ""},
    )[0]
    assert "frx_expected_config" in " ".join(chroot.argv)
    assert chroot.metadata["federation_attestation_required"] is True

    proot = wrap_specs_for_proot_distro(
        [spec], ProotRunnerConfig(distro="ubuntu"), repo_root=root, dry_run=True,
    )[0]
    assert "frx_expected_config" in " ".join(proot.argv)
    assert proot.metadata["federation_attestation_required"] is True

    workspace = SimpleNamespace(workspace_id="wid", instance_id="wid", mode="managed")

    class _Preparation:
        remote_root = "/remote/repo"
        lease = {}
        run_id = "run-oci"
        sync = {"would_create_workspace": False}

        def __init__(self) -> None:
            self.workspace = workspace

        def metadata(self) -> dict:
            return {
                "ssh_source_sync_id": "sync",
                "ssh_source_revision": {},
                "ssh_workspace_id": "wid",
                "ssh_workspace_path": self.remote_root,
            }

    plan = OciWorkspacePlan(
        "tablet", "wid", "/remote/repo", "/remote/cache", "sync", "/workspace",
        (
            OciWorkspaceBind("/remote/repo", "/workspace", "repository"),
            OciWorkspaceBind("/remote/cache", "/var/cache/devtool", "cache"),
        ),
        {"DEVTOOL_WORKSPACE": "/workspace"}, {},
    )

    class _OciState:
        image_reference = "devtool/android-build:arm64-api36-v2"
        image_digest = ""
        build_context_sha256 = "ctx"

        def __init__(self) -> None:
            self.plan = plan

        def metadata(self) -> dict:
            return {"oci_runtime": "proot-distro-oci", "oci_container": "devtool-android-api36"}

    monkeypatch.setattr("devtool.ssh_execution.prepare_ssh_command_execution", lambda *args, **kwargs: _Preparation())
    monkeypatch.setattr("devtool.oci_execution.prepare_oci_execution", lambda *args, **kwargs: _OciState())
    monkeypatch.setattr("devtool.oci_reuse.oci08_execution_metadata", lambda *args, **kwargs: {})
    monkeypatch.setattr("devtool.oci_diagnostics.oci09_phase_metadata", lambda *args, **kwargs: {})
    oci = wrap_specs_for_ssh_commands(
        [spec],
        SSHRunnerConfig(
            host="worker.example", user="mike", profile="tablet", workspace_mode="managed",
            mode="commands", copy_artifacts=False, execution_provider=SSH_PROOT_OCI, operation_id="run-oci",
        ),
        project_config=SimpleNamespace(runner_options={}), repo_root=root, dry_run=True,
    )[0]
    assert "frx_expected_config" in " ".join(oci.argv)
    assert oci.metadata["federation_execution_environment"] == "ssh-proot-oci"
    assert oci.metadata["federation_attestation_required"] is True


def test_whole_run_chroot_forwards_federation_attestation_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.termux_chroot_runner import RunnerConfig, build_inner_command

    cfg = RunnerConfig(chroot_name="androidos", cwd="/home/mike/Code/child", tool="devtool")
    identity = SimpleNamespace(user="mike", home="/home/mike", shell="/bin/bash", source="test")
    values = {
        "DEVTOOL_RUN_ID": "child-run",
        "DEVTOOL_FEDERATION_RELATIONSHIP": "child",
        "DEVTOOL_FEDERATION_ATTESTATION_SCHEMA_VERSION": "1",
        "DEVTOOL_FEDERATION_ATTESTATION_AVAILABLE": "1",
        "DEVTOOL_FEDERATION_ATTESTATION_CONFIG_SHA256": "a" * 64,
    }
    with mock.patch.dict(os.environ, values, clear=False):
        inner = build_inner_command(cfg, ["status"], identity=identity)
    assert "DEVTOOL_FEDERATION_RELATIONSHIP=child" in inner
    assert "DEVTOOL_FEDERATION_ATTESTATION_CONFIG_SHA256=" + ("a" * 64) in inner


def test_whole_run_remote_federation_refuses_missing_or_truncated_attestation(monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.federation_transport import FederationTransportError
    from devtool.termux_chroot_runner import RunnerConfig, build_inner_command

    child_env = {
        "DEVTOOL_FEDERATION_RELATIONSHIP": "child",
        "DEVTOOL_FEDERATION_PARENT_RUN_ID": "parent",
        "DEVTOOL_FEDERATION_ATTESTATION_SCHEMA_VERSION": "1",
        "DEVTOOL_FEDERATION_ATTESTATION_AVAILABLE": "0",
        "DEVTOOL_FEDERATION_ATTESTATION_CONFIG_SHA256": "",
        "DEVTOOL_FEDERATION_ATTESTATION_GIT_REMOTE_SHA256": "",
    }
    with mock.patch.dict(os.environ, child_env, clear=False):
        with pytest.raises(FederationTransportError, match="requires a portable repository attestation"):
            build_remote_tmux_script(SSHRunnerConfig(host="worker", user="mike", cwd="/srv/child"), ["status"])
        with pytest.raises(FederationTransportError, match="requires a portable repository attestation"):
            build_inner_command(
                RunnerConfig(chroot_name="androidos", cwd="/home/mike/child", tool="devtool"),
                ["status"],
                identity=SimpleNamespace(user="mike", home="/home/mike", shell="/bin/bash", source="test"),
            )

    truncated = dict(child_env)
    truncated.update({
        "DEVTOOL_FEDERATION_ATTESTATION_AVAILABLE": "1",
        "DEVTOOL_FEDERATION_ATTESTATION_CONFIG_SHA256": "",
    })
    with mock.patch.dict(os.environ, truncated, clear=False):
        with pytest.raises(FederationTransportError, match="availability marker"):
            build_remote_tmux_script(SSHRunnerConfig(host="worker", user="mike", cwd="/srv/child"), ["status"])
