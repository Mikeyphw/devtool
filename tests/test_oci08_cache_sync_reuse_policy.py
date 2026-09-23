from __future__ import annotations

from devtool.execution_providers import SSH_PROOT_OCI
from devtool.oci_reuse import (
    cache_reuse_policy,
    oci08_execution_metadata,
    source_sync_reuse_policy,
    worker_reuse_policy,
)
from devtool.oci_workspace import APPROVED_CACHES, CACHE_GUEST, OciWorkspaceBind, OciWorkspacePlan, WORKSPACE_GUEST
from devtool.ssh_tmux_runner import SSHRunnerConfig


def _config() -> SSHRunnerConfig:
    return SSHRunnerConfig(
        host="tablet.lan",
        user="u0_a123",
        profile="tablet",
        workspace_mode="managed",
        execution_provider=SSH_PROOT_OCI,
        mode="commands",
        oci_cache_root="~/.cache/devtool/oci/workspace-caches",
        oci_image="devtool/android-build:arm64-api36-v2",
    )


def _plan() -> OciWorkspacePlan:
    return OciWorkspacePlan(
        profile="tablet",
        workspace_id="repo-abc123",
        remote_workspace="/home/u0_a123/.cache/devtool/workspaces/repo-abc123",
        remote_cache_root="/home/u0_a123/.cache/devtool/oci/workspace-caches/repo-abc123",
        source_sync_id="sync-1",
        working_directory=WORKSPACE_GUEST,
        binds=(
            OciWorkspaceBind("/home/u0_a123/.cache/devtool/workspaces/repo-abc123", WORKSPACE_GUEST, "workspace"),
            OciWorkspaceBind("/home/u0_a123/.cache/devtool/oci/workspace-caches/repo-abc123", CACHE_GUEST, "persistent-cache"),
        ),
        managed_environment={},
        forwarded_environment={},
    )


def _prep(change_count: int = 0) -> dict[str, object]:
    return {
        "ssh_workspace_id": "repo-abc123",
        "ssh_sync_change_count": change_count,
        "ssh_sync_automatic": True,
        "ssh_source_sync_id": "sync-1",
        "ssh_source_revision": {
            "git_head": "abc",
            "working_tree_dirty": False,
            "working_tree_fingerprint": "00" * 32,
        },
        "ssh_session_prefix": "devtool-tablet-run",
        "ssh_workspace_lease": {"lease_id": "lease-1"},
    }


def test_oci08_source_sync_reuse_marks_zero_change_managed_mirror_as_reused() -> None:
    payload = source_sync_reuse_policy(_prep(change_count=0))
    assert payload["oci08_source_sync_policy"] == "reuse-verified-managed-source"
    assert payload["oci08_source_sync_reused"] is True
    assert payload["oci08_source_sync_change_count"] == 0
    assert payload["oci08_source_revision_digest"]


def test_oci08_source_sync_policy_distinguishes_incremental_updates() -> None:
    payload = source_sync_reuse_policy(_prep(change_count=3))
    assert payload["oci08_source_sync_policy"] == "incremental-managed-source-sync"
    assert payload["oci08_source_sync_reused"] is False
    assert payload["oci08_source_sync_change_count"] == 3


def test_oci08_cache_namespace_is_stable_and_source_sync_excluded() -> None:
    config = _config()
    plan = _plan()
    first = cache_reuse_policy(
        config,
        plan=plan,
        image_reference=config.oci_image,
        image_digest="sha256:image",
        build_context_sha256="ctx1",
    )
    second = cache_reuse_policy(
        config,
        plan=plan,
        image_reference=config.oci_image,
        image_digest="sha256:image",
        build_context_sha256="ctx1",
    )
    changed = cache_reuse_policy(
        config,
        plan=plan,
        image_reference=config.oci_image,
        image_digest="sha256:image",
        build_context_sha256="ctx2",
    )
    assert first["oci08_cache_namespace"] == second["oci08_cache_namespace"]
    assert first["oci08_cache_namespace"] != changed["oci08_cache_namespace"]
    assert first["oci08_cache_source_sync_excluded"] is True
    assert first["oci08_cache_mutates_source_tree"] is False
    assert first["oci08_cache_approved"] == list(APPROVED_CACHES)


def test_oci08_worker_reuse_requires_named_managed_oci_worker() -> None:
    config = _config()
    payload = worker_reuse_policy(config, _prep(), external_phase_count=4)
    assert payload["oci08_worker_reuse_policy"] == "single-preparation-per-run"
    assert payload["oci08_worker_reuse_enabled"] is True
    assert payload["oci08_worker_reuse_phase_count"] == 4
    disabled = worker_reuse_policy(
        SSHRunnerConfig(host="tablet.lan", profile="", workspace_mode="existing", execution_provider="ssh-native"),
        {},
        external_phase_count=2,
    )
    assert disabled["oci08_worker_reuse_enabled"] is False
    assert disabled["oci08_worker_reuse_policy"] == "disabled"
    assert "ssh-proot-oci" in disabled["oci08_worker_reuse_detail"]


def test_oci08_execution_metadata_combines_cache_source_and_worker_policy() -> None:
    config = _config()
    payload = oci08_execution_metadata(
        config,
        preparation_metadata=_prep(),
        plan=_plan(),
        image_reference=config.oci_image,
        image_digest="sha256:image",
        build_context_sha256="ctx1",
        external_phase_count=2,
    )
    assert payload["oci08_schema_version"] == 1
    assert payload["oci08_source_sync_reused"] is True
    assert payload["oci08_cache_policy"] == "preserve-outside-source-sync"
    assert payload["oci08_worker_reuse_enabled"] is True
    assert payload["oci08_cache_invalidation_inputs"]["workspace_id"] == "repo-abc123"
