from __future__ import annotations

import os
from pathlib import Path
import subprocess
from unittest import mock

import pytest

from devtool.config import load_project_config
from devtool.core.events import EventBus
from devtool.federation_discovery import (
    FederationDiscoveryError,
    resolve_federation_repository_owner,
)
from devtool.federation_execution import run_federated_direct_child, run_federated_workflow
from devtool.federation_final_seal import (
    FEDERATION_FINAL_SEAL_SAFETY_CASES,
    FEDERATION_FINAL_SEAL_SAFETY_EVIDENCE,
    validate_federation_safety_evidence,
)
from devtool.federation_transport import (
    FederationTransportError,
    build_federation_attestation_shell,
    build_federation_transport_metadata,
    build_repository_attestation,
    federation_attestation_environment,
    verify_federation_environment_attestation,
)
from devtool.repository_identity import build_repository_identity


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True, allow_ambiguous_project_kind=True)


def _repo(root: Path, body: str = "") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".devtool.toml").write_text('[project]\nrunner="command"\n' + body, encoding="utf-8")


def test_recursive_owner_resolver_longest_match_symlink_and_outside(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    grand = child / "grand"
    outside = tmp_path / "outside"
    _repo(parent, '[federation]\nschema=1\n[federation.repositories.child]\npath="child"\n')
    _repo(child, '[federation]\nschema=1\n[federation.repositories.grand]\npath="grand"\n')
    _repo(grand)
    outside.mkdir()
    (child / "nested-unenrolled" / ".git").mkdir(parents=True)

    config = _load(parent)
    owner = resolve_federation_repository_owner(config, grand / "src" / "main.py")
    assert owner is not None
    assert owner.repository == "child::grand"
    assert owner.root == str(grand.resolve())

    nested_git_owner = resolve_federation_repository_owner(config, child / "nested-unenrolled" / "file.txt")
    assert nested_git_owner is not None
    assert nested_git_owner.repository == "child"

    link = tmp_path / "grand-link"
    link.symlink_to(grand, target_is_directory=True)
    via_link = resolve_federation_repository_owner(config, link / "thing")
    assert via_link is not None and via_link.root == str(grand.resolve())
    assert resolve_federation_repository_owner(config, outside / "thing") is None


def test_owner_resolver_duplicate_canonical_ownership_fails_closed(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    a = tmp_path / "a"
    b = tmp_path / "b"
    shared = tmp_path / "shared"
    _repo(parent, '[federation]\nschema=1\n[federation.repositories.a]\npath="../a"\n[federation.repositories.b]\npath="../b"\n')
    _repo(a, '[federation]\nschema=1\n[federation.repositories.shared]\npath="../shared"\n')
    _repo(b, '[federation]\nschema=1\n[federation.repositories.shared]\npath="../shared"\n')
    _repo(shared)
    config = _load(parent)
    with pytest.raises(FederationDiscoveryError, match="Ambiguous federation ownership"):
        resolve_federation_repository_owner(config, shared / "file")


def _cancellation_fixture(tmp_path: Path) -> Path:
    parent = tmp_path / "parent"
    one = tmp_path / "one"
    two = tmp_path / "two"
    _repo(one, '[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\nroot="."\n[targets.app.jobs.work]\nrunner="command"\ncommand=["printf","ok"]\n[targets.app.workflows]\nwork=["job:work"]\n')
    _repo(two, '[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\nroot="."\n[targets.app.jobs.work]\nrunner="command"\ncommand=["printf","ok"]\n[targets.app.workflows]\nwork=["job:work"]\n')
    _repo(
        parent,
        '[federation]\nschema=1\n'
        '[federation.repositories.one]\npath="../one"\n'
        '[federation.repositories.two]\npath="../two"\n'
        '[federation.workflows.release]\nsteps=['
        '{id="first",ref="one::work"},'
        '{id="independent",ref="two::work"}'
        ']\n',
    )
    return parent


def test_cancellation_stops_independent_branches_and_direct_exec_is_cancelled(tmp_path: Path) -> None:
    config = _load(_cancellation_fixture(tmp_path))
    calls: list[str] = []

    def reenter(argv: list[str]) -> int:
        calls.append(os.environ["DEVTOOL_FEDERATION_REPOSITORY"])
        return 130

    report = run_federated_workflow(config, "release", reenter=reenter, parent_run_id="cancel-parent")
    assert report.status == "cancelled" and report.exit_code == 130
    assert calls == ["one"]
    assert [child.status for child in report.children] == ["cancelled", "blocked"]
    assert report.children[1].run_id == ""

    bus = EventBus()
    cancelled: list[str] = []
    with pytest.raises(KeyboardInterrupt):
        run_federated_direct_child(
            config,
            "one",
            operation="wrapper:build",
            invocation_argv=("build",),
            execute=lambda _run_id, _lineage: (_ for _ in ()).throw(KeyboardInterrupt()),
            publish=bus.publish,
            snapshot_lookup=bus.execution_store.snapshot,
            parent_run_id="direct-cancel-parent",
            request_child_cancel=lambda run_id: cancelled.append(run_id),
        )
    parent_snapshot = bus.execution_store.snapshot("direct-cancel-parent")
    assert parent_snapshot is not None and parent_snapshot.status == "cancelled"
    assert parent_snapshot.exit_code == 130
    assert len(cancelled) == 1


def test_remote_attestation_rejects_wrong_mapping_and_accepts_different_path_checkout(tmp_path: Path) -> None:
    local = tmp_path / "local"
    good = tmp_path / "remote-good"
    wrong = tmp_path / "remote-wrong"
    config_bytes = b'[project]\nrunner="command"\n[env]\nFRX="same"\n'
    for root in (local, good, wrong):
        root.mkdir()
    (local / ".devtool.toml").write_bytes(config_bytes)
    (good / ".devtool.toml").write_bytes(config_bytes)
    (wrong / ".devtool.toml").write_text('[project]\nrunner="command"\n[env]\nFRX="wrong"\n', encoding="utf-8")

    identity = build_repository_identity(local, local / ".devtool.toml")
    lineage = {
        "kind": "federation",
        "relationship": "child",
        "repository": "child",
        "parent_run_id": "parent",
        "parent_node_id": "node",
        "child_run_id": "child-run",
        "effective_repository": identity.fingerprint,
    }
    metadata = build_federation_transport_metadata(
        {"repository_identity": identity.as_dict(), "run_lineage": lineage},
        local_root=local,
        execution_environment="ssh-command",
        remote_root=str(good),
    )
    assert metadata["federation_attestation_required"] is True
    assert metadata["federation_attestation_config_sha256"]

    good_probe = build_federation_attestation_shell(metadata, remote_root=str(good))
    wrong_probe = build_federation_attestation_shell(metadata, remote_root=str(wrong))
    assert subprocess.run(["/bin/sh", "-c", good_probe], check=False).returncode == 0
    assert subprocess.run(["/bin/sh", "-c", wrong_probe], check=False).returncode == 74

    home = tmp_path / "worker-home"
    managed = home / "managed-child"
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.mkdir()
    (managed / ".devtool.toml").write_bytes(config_bytes)
    home_probe = build_federation_attestation_shell(metadata, remote_root="~/managed-child")
    home_env = dict(os.environ, HOME=str(home))
    assert subprocess.run(["/bin/sh", "-c", home_probe], env=home_env, check=False).returncode == 0

    attestation = build_repository_attestation(local, identity=identity)
    env = federation_attestation_environment(attestation)
    verified = verify_federation_environment_attestation(good, config_path=good / ".devtool.toml", environment=env)
    assert verified.config_sha256 == attestation.config_sha256
    with pytest.raises(FederationTransportError, match="attestation mismatch"):
        verify_federation_environment_attestation(wrong, config_path=wrong / ".devtool.toml", environment=env)

    # Whole-run Devtool re-entry verifies the same portable signal at normal
    # top-level config load, even though the checkout path differs.
    reentry_env = dict(env)
    reentry_env["DEVTOOL_FEDERATION_RELATIONSHIP"] = "child"
    reentry_env["DEVTOOL_FEDERATION_PARENT_RUN_ID"] = "parent"
    with mock.patch.dict(os.environ, reentry_env, clear=False):
        assert _load(good).repository_identity is not None
        with pytest.raises(SystemExit, match="attestation mismatch"):
            _load(wrong)


def test_git_only_remote_attestation_is_path_independent_and_secret_neutral(tmp_path: Path) -> None:
    local = tmp_path / "local-git"
    remote = tmp_path / "remote-git"
    stale = tmp_path / "stale-git"
    for root in (local, remote, stale):
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)

    shared_origin = "ssh://git@example.invalid/team/child.git"
    stale_origin = "ssh://git@example.invalid/team/other.git"
    for root, origin in ((local, shared_origin), (remote, shared_origin), (stale, stale_origin)):
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin", origin], check=True)

    identity = build_repository_identity(local, None)
    lineage = {
        "kind": "federation",
        "relationship": "child",
        "repository": "child",
        "parent_run_id": "parent",
        "parent_node_id": "node",
        "child_run_id": "child-run",
        "effective_repository": identity.fingerprint,
    }
    metadata = build_federation_transport_metadata(
        {"repository_identity": identity.as_dict(), "run_lineage": lineage},
        local_root=local,
        execution_environment="ssh-command",
        remote_root=str(remote),
    )
    assert not metadata["federation_attestation_config_sha256"]
    assert len(metadata["federation_attestation_git_remote_sha256"]) == 64
    assert shared_origin not in repr(metadata)

    good_probe = build_federation_attestation_shell(metadata, remote_root=str(remote))
    stale_probe = build_federation_attestation_shell(metadata, remote_root=str(stale))
    assert subprocess.run(["/bin/sh", "-c", good_probe], check=False).returncode == 0
    assert subprocess.run(["/bin/sh", "-c", stale_probe], check=False).returncode == 74

    # A previously-valid checkout that changes after metadata creation is stale
    # and must fail the already-issued attestation probe.
    subprocess.run(["git", "-C", str(remote), "remote", "set-url", "origin", stale_origin], check=True)
    assert subprocess.run(["/bin/sh", "-c", good_probe], check=False).returncode == 74


def test_frx12_safety_evidence_is_machine_resolvable_and_complete() -> None:
    ok, rows, errors = validate_federation_safety_evidence(Path(__file__).resolve().parents[1])
    assert ok is True, errors
    assert [row["case_id"] for row in rows] == list(FEDERATION_FINAL_SEAL_SAFETY_CASES)
    assert len(FEDERATION_FINAL_SEAL_SAFETY_EVIDENCE) == len(FEDERATION_FINAL_SEAL_SAFETY_CASES) == 20
    assert all(row["status"] == "proved" and row["resolved"] for row in rows)


def test_federated_child_reentry_carries_portable_attestation_environment(tmp_path: Path) -> None:
    config = _load(_cancellation_fixture(tmp_path))
    seen: dict[str, str] = {}

    def reenter(_argv: list[str]) -> int:
        for key, value in os.environ.items():
            if key.startswith("DEVTOOL_FEDERATION_ATTESTATION_"):
                seen[key] = value
        return 130

    report = run_federated_workflow(config, "release", reenter=reenter, parent_run_id="attest-parent")
    assert report.status == "cancelled"
    assert seen["DEVTOOL_FEDERATION_ATTESTATION_AVAILABLE"] == "1"
    assert len(seen["DEVTOOL_FEDERATION_ATTESTATION_CONFIG_SHA256"]) == 64


def test_exit_130_overrides_stale_success_snapshot_status(tmp_path: Path) -> None:
    from types import SimpleNamespace

    config = _load(_cancellation_fixture(tmp_path))
    captured: dict[str, object] = {}

    def execute(child_run_id: str, lineage: dict[str, object]) -> int:
        captured["run_id"] = child_run_id
        captured["lineage"] = dict(lineage)
        return 130

    def snapshot(run_id: str):
        if run_id != captured.get("run_id"):
            return None
        resolution = __import__("devtool.federation_discovery", fromlist=["resolve_federated_repository"]).resolve_federated_repository(config, "one")
        return SimpleNamespace(
            status="succeeded",
            run_lineage=dict(captured["lineage"]),
            repository_identity=resolution.repository_identity.as_dict(),
            validation_summary={}, artifacts=(), hook_records=(),
        )

    result = run_federated_direct_child(
        config, "one", operation="wrapper:build", invocation_argv=("build",),
        execute=execute, snapshot_lookup=snapshot, parent_run_id="stale-status-parent",
    )
    assert result.status == "cancelled"
    assert result.exit_code == 130


def test_remote_attestation_applies_only_to_top_level_config_reentry(tmp_path: Path) -> None:
    child = tmp_path / "child"
    grand = tmp_path / "grand"
    _repo(child)
    _repo(grand, '[env]\nGRAND="1"\n')
    child_identity = build_repository_identity(child, child / ".devtool.toml")
    env = federation_attestation_environment(build_repository_attestation(child, identity=child_identity))
    env["DEVTOOL_FEDERATION_RELATIONSHIP"] = "child"
    env["DEVTOOL_FEDERATION_PARENT_RUN_ID"] = "parent"

    with mock.patch.dict(os.environ, env, clear=False):
        loaded = load_project_config(
            str(child), None, allow_ambiguous_target=True, allow_ambiguous_project_kind=True,
        )
        assert loaded.repository_identity is not None
        # Internal strict child loads are autonomous and do not reuse the outer
        # child's attestation token.
        nested = load_project_config(
            str(grand), None, allow_ambiguous_target=True, allow_ambiguous_project_kind=True,
            allow_parent_config=False,
        )
        assert nested.repository_identity is not None
        with pytest.raises(SystemExit, match="attestation mismatch"):
            load_project_config(
                str(grand), None, allow_ambiguous_target=True, allow_ambiguous_project_kind=True,
            )
