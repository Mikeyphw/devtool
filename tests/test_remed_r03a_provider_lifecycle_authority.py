from __future__ import annotations

import pytest
from pathlib import Path

from devtool.cleanup_lifecycle import (
    CleanupAuthorityUnavailableError,
    CleanupScope,
    GeneratedOutputDescriptor,
    apply_cleanup_plan,
    build_cleanup_ownership_view,
    compile_cleanup_plan,
)
from devtool.distributed_execution import DelegationLink, ExecutionRunLink
from devtool.distributed_provider_control import (
    provider_cleanup_authority_metadata,
    provider_cleanup_descriptors,
    ssh_phase_dist_metadata,
)
from devtool.oci_resilience import phase_metadata_lines


def _init_repo(path: Path) -> None:
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "devtool@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Devtool Tests"], cwd=path, check=True)


def _descriptor(path: Path) -> GeneratedOutputDescriptor:
    return GeneratedOutputDescriptor.create(
        path,
        owner="TEST",
        provider="pytest",
        category="scratch",
        producer_id="producer",
        lifecycle="disposable",
        rebuild_cost="low",
        scope=CleanupScope.PROJECT,
    )


def _link() -> DelegationLink:
    return DelegationLink(
        run_link=ExecutionRunLink("parent-run", "parent-node", "child-run"),
        repository="repo",
        target="devtool",
        child_contract="workflow:validate",
        contract_ref="repo::devtool::workflow:validate",
        federation_ref="repo::devtool::validate",
        provider_identity={
            "provider_id": "tablet",
            "provider_kind": "ssh",
            "profile": "tablet",
            "provider_evidence_digest": "provider-evidence",
        },
        attestation_digest="attestation",
    )


def test_destructive_cleanup_fails_closed_when_run_authority_is_corrupt(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    target = repo / "build-output"
    target.write_text("delete me", encoding="utf-8")
    view = build_cleanup_ownership_view(repo, [_descriptor(target)], policy_generation="r03a")
    plan = compile_cleanup_plan(view)

    catalog = repo / ".devtool" / "private" / "run-v3" / "catalog.json"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text("{not-json", encoding="utf-8")

    with pytest.raises(CleanupAuthorityUnavailableError, match="RUN/RER"):
        apply_cleanup_plan(plan, ownership=view, confirm=True)
    assert target.exists(), "cleanup must not mutate when canonical RUN protection roots are unreadable"


def test_destructive_cleanup_fails_closed_when_artifact_registry_is_corrupt(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    target = repo / "dist-output"
    target.write_text("delete me", encoding="utf-8")
    view = build_cleanup_ownership_view(repo, [_descriptor(target)], policy_generation="r03a")
    plan = compile_cleanup_plan(view)

    registry = repo / ".devtool" / "artifacts" / "registry.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text("{not-json", encoding="utf-8")

    with pytest.raises(CleanupAuthorityUnavailableError, match="artifact-registry"):
        apply_cleanup_plan(plan, ownership=view, confirm=True)
    assert target.exists(), "cleanup must not mutate when artifact registry protection roots are unreadable"


def test_provider_phase_metadata_carries_life_cleanup_authority_digest() -> None:
    link = _link()
    metadata = ssh_phase_dist_metadata(
        link=link,
        child_run_id="child-run",
        remote_root="~/Code/repo",
        session="session-1",
        profile="tablet",
        destination="termux@tablet",
        provider_kind="ssh",
        provider_evidence_digest="provider-evidence",
        original_argv=("devtool", "invoke", "validate"),
    )
    descriptors = provider_cleanup_descriptors(
        destination="termux@tablet",
        provider="ssh",
        run_id="child-run",
        session="session-1",
        stage_path="~/Code/repo",
        state_path="~/.cache/devtool/ssh-runs/child-run/session-1",
        cursor_path="~/.cache/devtool/ssh-runs/child-run/session-1/event-cursor.json",
        delegation_id=link.delegation_id,
    )
    expected = provider_cleanup_authority_metadata(
        destination="termux@tablet",
        provider="ssh",
        run_id="child-run",
        session="session-1",
        stage_path="~/Code/repo",
        state_path="~/.cache/devtool/ssh-runs/child-run/session-1",
        cursor_path="~/.cache/devtool/ssh-runs/child-run/session-1/event-cursor.json",
        delegation_id=link.delegation_id,
    )

    assert metadata["dist_provider_lifecycle_authority"] == "RUN/LIFE"
    assert metadata["dist_provider_cleanup_descriptor_count"] == len(descriptors) == 3
    assert metadata["dist_provider_cleanup_digest"] == expected["dist_provider_cleanup_digest"]
    assert metadata["dist_provider_session_binding_id"]

    lines = phase_metadata_lines(metadata, run_id="child-run", session="session-1")
    line_map = dict(line.split("=", 1) for line in lines)
    assert line_map["dist_provider_lifecycle_authority"] == "RUN/LIFE"
    assert line_map["dist_provider_cleanup_descriptor_count"] == "3"
    assert line_map["dist_provider_cleanup_digest"] == metadata["dist_provider_cleanup_digest"]
    cleanup_line = next(line for line in lines if line.startswith("dist_provider_cleanup_digest="))
    assert "~/Code/repo" not in cleanup_line


def test_non_delegated_ssh_provider_work_still_gets_cleanup_authority() -> None:
    metadata = ssh_phase_dist_metadata(
        link=None,
        child_run_id="standalone-run",
        remote_root="~/Code/repo",
        session="session-2",
        profile="tablet",
        destination="termux@tablet",
        provider_kind="ssh-native",
    )
    assert "dist_delegation_id" not in metadata
    assert metadata["dist_provider_lifecycle_authority"] == "RUN/LIFE"
    assert metadata["dist_provider_cleanup_descriptor_count"] == 3
    assert metadata["dist_provider_cleanup_digest"]


def test_destructive_cleanup_fails_closed_when_active_transaction_marker_is_corrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    repo = tmp_path / "repo"
    _init_repo(repo)
    target = repo / "generated-cache"
    target.write_text("delete me", encoding="utf-8")
    view = build_cleanup_ownership_view(repo, [_descriptor(target)], policy_generation="r03a")
    plan = compile_cleanup_plan(view)

    from devtool.transactions import transaction_root

    marker = transaction_root(repo) / "active" / "broken.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{not-json", encoding="utf-8")

    with pytest.raises(CleanupAuthorityUnavailableError, match="transaction"):
        apply_cleanup_plan(plan, ownership=view, confirm=True)
    assert target.exists(), "cleanup must not mutate when active transaction authority is unreadable"
    assert marker.exists(), "strict cleanup must not prune corrupt active transaction authority while deciding"


def test_destructive_cleanup_fails_closed_when_validation_marker_is_corrupt(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    target = repo / "validation-owned-output"
    target.write_text("delete me", encoding="utf-8")
    view = build_cleanup_ownership_view(repo, [_descriptor(target)], policy_generation="r03a")
    plan = compile_cleanup_plan(view)

    marker = repo / ".devtool" / "state" / "android-validation" / "active.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{not-json", encoding="utf-8")

    with pytest.raises(CleanupAuthorityUnavailableError, match="validation"):
        apply_cleanup_plan(plan, ownership=view, confirm=True)
    assert target.exists(), "cleanup must not mutate when validation authority is unreadable"
