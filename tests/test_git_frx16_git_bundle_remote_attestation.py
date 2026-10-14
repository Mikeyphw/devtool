from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess

import pytest

from devtool.config import load_project_config
from devtool.federation_git_transport import (
    FederationGitTransportError,
    build_remote_bundle_materialization_shell,
    create_federated_git_bundle,
    normalize_source_transport,
    plan_federated_git_source,
)
from devtool.federation_lock import git_remote_locator
from devtool.federation_transport import (
    FederationTransportError,
    build_federation_attestation_shell,
    build_repository_attestation,
    federation_attestation_environment,
    verify_federation_environment_attestation,
)
from devtool.repository_identity import build_repository_identity
from devtool.oci_resilience import producer_identity
from devtool.ssh_tmux_runner import SSHRunnerConfig, resolve_ssh_config
from devtool.ssh_worker import SSHWorkerProbe
from devtool import ssh_workspace


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True, check=True,
    )
    return completed.stdout.strip()


def _repo(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / name
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _git(root, "config", "user.email", "devtool@example.invalid")
    _git(root, "config", "user.name", "Devtool")
    (root / ".devtool.toml").write_text('[project]\nrunner = "command"\n', encoding="utf-8")
    (root / "payload.txt").write_text("one\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "initial")
    _git(root, "remote", "add", "origin", "ssh://git@example.invalid/team/child.git")
    return root


def _lineage(root: Path) -> dict[str, object]:
    locator = git_remote_locator(root)
    return {
        "kind": "federation",
        "relationship": "child",
        "repository": "child",
        "parent_run_id": "parent-run",
        "parent_node_id": "parent-node",
        "child_run_id": "child-run",
        "federation_ref": "child::devtool::verify",
        "git_lineage": {
            "repository": "child",
            "start_commit": _git(root, "rev-parse", "HEAD"),
            "head_tree": _git(root, "rev-parse", "HEAD^{tree}"),
            "config_digest": hashlib.sha256((root / ".devtool.toml").read_bytes()).hexdigest(),
            "remote_locator": locator,
            "remote_locator_digest": hashlib.sha256(locator.encode("utf-8")).hexdigest(),
        },
    }


def _child_env(lineage: dict[str, object], *, effective_repository: str = "") -> dict[str, str]:
    return {
        "DEVTOOL_RUN_ID": str(lineage["child_run_id"]),
        "DEVTOOL_FEDERATION_RELATIONSHIP": "child",
        "DEVTOOL_FEDERATION_PARENT_RUN_ID": str(lineage["parent_run_id"]),
        "DEVTOOL_FEDERATION_PARENT_NODE_ID": str(lineage["parent_node_id"]),
        "DEVTOOL_FEDERATION_REPOSITORY": str(lineage["repository"]),
        "DEVTOOL_FEDERATION_REQUESTED_REPOSITORY": str(lineage["repository"]),
        "DEVTOOL_FEDERATION_EFFECTIVE_REPOSITORY": effective_repository or "test-repository-fingerprint",
        "DEVTOOL_FEDERATION_REF": str(lineage["federation_ref"]),
        "DEVTOOL_FEDERATION_GIT_START_COMMIT": str(lineage["git_lineage"]["start_commit"]),
        "DEVTOOL_FEDERATION_GIT_HEAD_TREE": str(lineage["git_lineage"]["head_tree"]),
        "DEVTOOL_FEDERATION_GIT_CONFIG_DIGEST": str(lineage["git_lineage"]["config_digest"]),
        "DEVTOOL_FEDERATION_GIT_REMOTE_LOCATOR": str(lineage["git_lineage"]["remote_locator"]),
        "DEVTOOL_FEDERATION_GIT_REMOTE_LOCATOR_DIGEST": str(lineage["git_lineage"]["remote_locator_digest"]),
    }


def test_source_transport_contract_and_invalid_value() -> None:
    assert normalize_source_transport("auto") == "auto"
    assert normalize_source_transport("git_bundle") == "git-bundle"
    assert normalize_source_transport("rsync") == "rsync"
    with pytest.raises(FederationGitTransportError, match="source transport"):
        normalize_source_transport("magic")


def test_bundle_verification_is_bound_to_source_repo_not_process_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path, "source")
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    plan = plan_federated_git_source(root, lineage=_lineage(root), requested_transport="git-bundle")
    bundle = create_federated_git_bundle(root, plan)
    try:
        assert bundle.path.is_file()
        assert bundle.start_commit == plan.start_commit
    finally:
        bundle.path.unlink(missing_ok=True)


def test_clean_frozen_child_creates_minimal_verified_bundle_without_global_bisect_state(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    plan = plan_federated_git_source(root, lineage=_lineage(root), requested_transport="git-bundle")
    assert plan.eligible is True and plan.effective_transport == "git-bundle"
    bundle = create_federated_git_bundle(root, plan)
    try:
        assert bundle.digest == hashlib.sha256(bundle.path.read_bytes()).hexdigest()
        heads = subprocess.run(["git", "bundle", "list-heads", str(bundle.path)], text=True, capture_output=True, check=True).stdout
        assert plan.start_commit in heads
        assert _git(root, "for-each-ref", "--format=%(refname)", "refs/devtool/transport") == ""
        assert not (root / ".git" / "BISECT_START").exists()
    finally:
        bundle.path.unlink(missing_ok=True)


def test_auto_falls_back_before_transport_but_strict_bundle_fails_closed_for_dirty_source(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    lineage = _lineage(root)
    (root / "payload.txt").write_text("dirty\n", encoding="utf-8")
    auto = plan_federated_git_source(root, lineage=lineage, requested_transport="auto")
    assert auto.effective_transport == "rsync"
    assert "dirty" in auto.reason
    with pytest.raises(FederationGitTransportError, match="Strict git-bundle"):
        plan_federated_git_source(root, lineage=lineage, requested_transport="git-bundle")


def test_remote_bundle_materialization_verifies_exact_commit_tree_config_and_remote(tmp_path: Path) -> None:
    source = _repo(tmp_path, "source")
    remote = tmp_path / "worker-home" / "managed"
    remote.mkdir(parents=True)
    plan = plan_federated_git_source(source, lineage=_lineage(source), requested_transport="git-bundle")
    bundle = create_federated_git_bundle(source, plan)
    shell = build_remote_bundle_materialization_shell(
        plan, bundle, remote_root=str(remote), remote_bundle=str(bundle.path),
    )
    completed = subprocess.run(["/bin/sh", "-c", shell], cwd=str(tmp_path), text=True, capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr
    assert _git(remote, "rev-parse", "HEAD") == plan.start_commit
    assert _git(remote, "rev-parse", "HEAD^{tree}") == plan.source_tree
    assert git_remote_locator(remote) == plan.remote_locator
    assert (remote / ".devtool-federation-attestation.json").is_file()
    assert not bundle.path.exists()


def test_remote_bundle_materialization_rejects_tampered_transfer(tmp_path: Path) -> None:
    source = _repo(tmp_path, "source")
    remote = tmp_path / "remote"; remote.mkdir()
    plan = plan_federated_git_source(source, lineage=_lineage(source), requested_transport="git-bundle")
    bundle = create_federated_git_bundle(source, plan)
    bundle.path.write_bytes(bundle.path.read_bytes() + b"tamper")
    shell = build_remote_bundle_materialization_shell(plan, bundle, remote_root=str(remote), remote_bundle=str(bundle.path))
    completed = subprocess.run(["/bin/sh", "-c", shell], cwd=str(tmp_path), text=True, capture_output=True, check=False)
    assert completed.returncode == 74
    assert "digest mismatch" in completed.stderr
    bundle.path.unlink(missing_ok=True)


def test_attestation_proves_frozen_start_and_federation_routing_identity(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    lineage = _lineage(root)
    identity = build_repository_identity(root, root / ".devtool.toml")
    attestation = build_repository_attestation(root, identity=identity, lineage=lineage)
    env = _child_env(lineage)
    env.update(federation_attestation_environment(attestation))
    verified = verify_federation_environment_attestation(root, config_path=root / ".devtool.toml", environment=env)
    assert verified.start_commit == lineage["git_lineage"]["start_commit"]
    bad = dict(env); bad["DEVTOOL_FEDERATION_PARENT_NODE_ID"] = "wrong-node"
    with pytest.raises(FederationTransportError, match="parent node"):
        verify_federation_environment_attestation(root, config_path=root / ".devtool.toml", environment=bad)
    _git(root, "commit", "--allow-empty", "-qm", "later")
    with pytest.raises(FederationTransportError, match="frozen start commit"):
        verify_federation_environment_attestation(root, config_path=root / ".devtool.toml", environment=env)


def test_command_provider_attestation_shell_enforces_frozen_start_commit(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    lineage = _lineage(root)
    identity = build_repository_identity(root, root / ".devtool.toml")
    attestation = build_repository_attestation(root, identity=identity, lineage=lineage)
    metadata = {
        "federation_attestation_required": True,
        "federation_attestation_start_commit": attestation.start_commit,
        "federation_attestation_config_sha256": attestation.config_sha256,
        "federation_attestation_git_remote_sha256": attestation.git_remote_sha256,
    }
    probe = build_federation_attestation_shell(metadata, remote_root=str(root))
    assert subprocess.run(["/bin/sh", "-c", probe], check=False).returncode == 0
    _git(root, "commit", "--allow-empty", "-qm", "later")
    stale = subprocess.run(["/bin/sh", "-c", probe], text=True, capture_output=True, check=False)
    assert stale.returncode == 74
    assert "frozen start commit" in stale.stderr


def test_nested_discovery_does_not_consume_outer_child_attestation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outer = _repo(tmp_path, "outer")
    nested = tmp_path / "nested"; nested.mkdir()
    (nested / ".devtool.toml").write_text('[project]\nrunner = "command"\n', encoding="utf-8")
    lineage = _lineage(outer)
    identity = build_repository_identity(outer, outer / ".devtool.toml")
    env = _child_env(lineage)
    env.update(federation_attestation_environment(build_repository_attestation(outer, identity=identity, lineage=lineage)))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    # Nested FRX discovery deliberately disables parent config/attestation
    # consumption. The outer child's attestation must not be misapplied here.
    loaded = load_project_config(nested, None, allow_parent_config=False)
    assert Path(loaded.repo_root) == nested.resolve()


def test_ssh_transport_config_accepts_first_class_source_transport(tmp_path: Path) -> None:
    (tmp_path / ".devtool.toml").write_text(
        '[project]\nrunner = "command"\n[ssh.transport]\nsource_transport = "git-bundle"\n', encoding="utf-8",
    )
    cfg = resolve_ssh_config(["--ssh", "worker", "--ssh-cwd", "/srv/repo"], cwd=str(tmp_path), env={})
    assert cfg.source_transport == "git-bundle"


def test_existing_workspace_cannot_use_strict_git_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    lineage = _lineage(root)
    identity = build_repository_identity(root, root / ".devtool.toml")
    for key, value in _child_env(lineage, effective_repository=identity.fingerprint).items():
        monkeypatch.setenv(key, value)
    cfg = SSHRunnerConfig(host="worker", cwd="/srv/repo", workspace_mode="existing", source_transport="git-bundle")
    spec = ssh_workspace.SSHWorkspaceSpec(
        "", "worker", "existing", str(root), "repo", "id", "id", "", "/srv/repo", False, "incremental", (),
    )
    worker = SSHWorkerProbe(profile="", destination="worker", port=22, platform="linux", os_name="Linux", architecture="x86_64", cpu_count=2, memory_kib=1_000_000, free_storage_kib=1_000_000, home="/home/u", prefix="/usr", shell="/bin/sh", package_manager="apt-get", tools={"git":"/usr/bin/git","rsync":"/usr/bin/rsync"}, versions={}, bootstrap_policy="never", tmux_required=False)
    with pytest.raises(ValueError, match="requires a frozen federated child execution"):
        ssh_workspace.sync_workspace(cfg, spec, dry_run=True, worker=worker)


def test_git_bundle_failure_never_falls_through_to_rsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    lineage = _lineage(root)
    identity = build_repository_identity(root, root / ".devtool.toml")
    for key, value in _child_env(lineage, effective_repository=identity.fingerprint).items():
        monkeypatch.setenv(key, value)
    cfg = SSHRunnerConfig(host="worker", profile="tablet", workspace_mode="managed", source_transport="git-bundle")
    spec = ssh_workspace.SSHWorkspaceSpec(
        "tablet", "worker", "managed", str(root), "repo", "id", "id", "/home/u/ws", "/home/u/ws/id", True, "incremental", (),
    )
    worker = SSHWorkerProbe(profile="tablet", destination="worker", port=22, platform="linux", os_name="Linux", architecture="x86_64", cpu_count=2, memory_kib=1_000_000, free_storage_kib=1_000_000, home="/home/u", prefix="/usr", shell="/bin/sh", package_manager="apt-get", tools={"git":"/usr/bin/git","rsync":"/usr/bin/rsync"}, versions={}, bootstrap_policy="never", tmux_required=True)
    monkeypatch.setattr(ssh_workspace, "_git_bundle_sync", lambda *a, **k: (_ for _ in ()).throw(ValueError("bundle failed")))
    called = {"rsync": False}
    monkeypatch.setattr(ssh_workspace, "_run_rsync", lambda *a, **k: called.__setitem__("rsync", True))
    with pytest.raises(ValueError, match="requires a frozen federated child execution"):
        ssh_workspace.sync_workspace(cfg, spec, dry_run=False, worker=worker)
    assert called["rsync"] is False


def test_strict_git_bundle_never_silently_falls_back_for_non_federated_execution(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    with pytest.raises(FederationGitTransportError, match="requires a frozen federated child"):
        plan_federated_git_source(root, lineage={}, requested_transport="git-bundle")
    auto = plan_federated_git_source(root, lineage={}, requested_transport="auto")
    assert auto.effective_transport == "rsync"


def test_auto_source_transport_falls_back_before_transfer_when_remote_git_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    lineage = _lineage(root)
    identity = build_repository_identity(root, root / ".devtool.toml")
    for key, value in _child_env(lineage, effective_repository=identity.fingerprint).items():
        monkeypatch.setenv(key, value)
    cfg = SSHRunnerConfig(host="worker", profile="tablet", workspace_mode="managed", source_transport="auto")
    spec = ssh_workspace.SSHWorkspaceSpec(
        "tablet", "worker", "managed", str(root), "repo", "id", "id", "/home/u/ws", "/home/u/ws/id", True, "incremental", (),
    )
    worker = SSHWorkerProbe(profile="tablet", destination="worker", port=22, platform="linux", os_name="Linux", architecture="x86_64", cpu_count=2, memory_kib=1_000_000, free_storage_kib=1_000_000, home="/home/u", prefix="/usr", shell="/bin/sh", package_manager="apt-get", tools={"rsync":"/usr/bin/rsync"}, versions={}, bootstrap_policy="never", tmux_required=True)
    monkeypatch.setattr(ssh_workspace, "workspace_status", lambda *a, **k: ssh_workspace.SSHWorkspaceStatus(
        False, False, False, True, spec.workspace_root, spec.remote_path, "", {}, "missing",
    ))
    monkeypatch.setattr(ssh_workspace, "_local_missing_workspace_plan", lambda *a, **k: (0, "", ""))
    planned = ssh_workspace.sync_workspace(cfg, spec, dry_run=True, worker=worker)
    assert planned["source_transport_requested"] == "auto"
    assert planned["source_transport_effective"] == "rsync"
    assert "not a federated child execution" in planned["transport"]["source_transport"]["reason"]


def test_strict_bundle_fails_before_transfer_when_remote_git_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    lineage = _lineage(root)
    identity = build_repository_identity(root, root / ".devtool.toml")
    for key, value in _child_env(lineage, effective_repository=identity.fingerprint).items():
        monkeypatch.setenv(key, value)
    cfg = SSHRunnerConfig(host="worker", profile="tablet", workspace_mode="managed", source_transport="git-bundle")
    spec = ssh_workspace.SSHWorkspaceSpec(
        "tablet", "worker", "managed", str(root), "repo", "id", "id", "/home/u/ws", "/home/u/ws/id", True, "incremental", (),
    )
    worker = SSHWorkerProbe(profile="tablet", destination="worker", port=22, platform="linux", os_name="Linux", architecture="x86_64", cpu_count=2, memory_kib=1_000_000, free_storage_kib=1_000_000, home="/home/u", prefix="/usr", shell="/bin/sh", package_manager="apt-get", tools={"rsync":"/usr/bin/rsync"}, versions={}, bootstrap_policy="never", tmux_required=True)
    with pytest.raises(ValueError, match="requires a frozen federated child execution"):
        ssh_workspace.sync_workspace(cfg, spec, dry_run=True, worker=worker)


def test_return_producer_identity_carries_bundle_and_attestation_evidence() -> None:
    producer = producer_identity(
        profile="tablet", destination="worker", run_id="child-run", session="phase", source_sync_id="sync",
        metadata={
            "execution_provider": "ssh-command",
            "federation_child_repository": "child",
            "federation_attestation_repository": "child",
            "federation_attestation_parent_run_id": "parent-run",
            "federation_attestation_parent_node_id": "parent-node",
            "federation_attestation_child_run_id": "child-run",
            "federation_attestation_federation_ref": "child::devtool::verify",
            "federation_attestation_start_commit": "a" * 40,
            "federation_attestation_config_sha256": "b" * 64,
            "federation_attestation_git_remote_sha256": "c" * 64,
            "federation_source_transport_requested": "auto",
            "federation_source_transport_effective": "git-bundle",
            "federation_source_transport_evidence_digest": "d" * 64,
        },
    )
    federation = producer["federation"]
    assert federation["source_transport_effective"] == "git-bundle"
    assert federation["source_transport_evidence_digest"] == "d" * 64
    assert federation["attestation_start_commit"] == "a" * 40
    assert federation["attestation_parent_node_id"] == "parent-node"
