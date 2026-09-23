from __future__ import annotations

"""OCI-08 cache, source-sync, and worker-reuse policy helpers.

The OCI execution path already has one source of truth for SSH workspace
synchronization, OCI image validation, and tmux phase ownership.  This module
adds *policy metadata* only: it classifies when source mirrors/caches/workers can
be safely reused and records the immutable inputs that make that reuse safe.  It
must not perform remote I/O or create a second lifecycle authority.
"""

from hashlib import sha256
from typing import Any, Mapping

from .execution_providers import SSH_PROOT_OCI
from .oci_workspace import APPROVED_CACHES, CACHE_GUEST, OciWorkspacePlan
from .ssh_tmux_runner import SSHRunnerConfig


def _text(value: Any) -> str:
    return str(value or "").strip()


def _digest(*parts: Any, length: int = 16) -> str:
    h = sha256()
    for part in parts:
        h.update(_text(part).encode("utf-8", errors="replace"))
        h.update(b"\0")
    return h.hexdigest()[:length]


def _source_revision_digest(revision: Mapping[str, Any] | None) -> str:
    if not isinstance(revision, Mapping):
        return ""
    return _digest(
        revision.get("git_head"),
        revision.get("working_tree_fingerprint"),
        bool(revision.get("working_tree_dirty")),
        length=32,
    )


def source_sync_reuse_policy(preparation_metadata: Mapping[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    """Classify the authoritative SSH source mirror for OCI reuse.

    Source synchronization stays owned by the SSH workspace layer.  OCI-08 only
    records whether the mirror was reused unchanged, incrementally updated, or
    merely planned.  Caches must never be used as a substitute for source truth.
    """

    sync_id = _text(preparation_metadata.get("ssh_source_sync_id"))
    change_count = int(preparation_metadata.get("ssh_sync_change_count") or 0)
    automatic = bool(preparation_metadata.get("ssh_sync_automatic"))
    revision = preparation_metadata.get("ssh_source_revision")
    if not isinstance(revision, Mapping):
        revision = {}
    workspace_id = _text(preparation_metadata.get("ssh_workspace_id"))
    if dry_run and not sync_id:
        policy = "planned-managed-source-sync"
        reused = False
        detail = "dry-run source synchronization is planned but not materialized"
    elif sync_id and change_count == 0:
        policy = "reuse-verified-managed-source"
        reused = True
        detail = "managed SSH source mirror was verified and did not require file changes"
    elif sync_id:
        policy = "incremental-managed-source-sync"
        reused = False
        detail = "managed SSH source mirror was updated incrementally before OCI execution"
    elif automatic:
        policy = "managed-source-sync-without-id"
        reused = False
        detail = "managed SSH source synchronization ran without a durable sync id"
    else:
        policy = "no-managed-source-sync"
        reused = False
        detail = "no managed SSH source synchronization evidence was present"
    return {
        "oci08_source_sync_policy": policy,
        "oci08_source_sync_reused": reused,
        "oci08_source_sync_id": sync_id,
        "oci08_source_sync_change_count": change_count,
        "oci08_source_revision_digest": _source_revision_digest(revision),
        "oci08_source_dirty": bool(revision.get("working_tree_dirty")),
        "oci08_workspace_id": workspace_id,
        "oci08_source_sync_detail": detail,
    }


def cache_reuse_policy(
    config: SSHRunnerConfig,
    *,
    plan: OciWorkspacePlan,
    image_reference: str,
    image_digest: str,
    build_context_sha256: str,
) -> dict[str, Any]:
    """Return durable cache namespace metadata for a prepared OCI workspace."""

    namespace = "oci-cache-" + _digest(
        config.profile,
        config.destination,
        plan.workspace_id,
        plan.remote_cache_root,
        image_reference,
        image_digest,
        build_context_sha256,
        length=20,
    )
    return {
        "oci08_cache_policy": "preserve-outside-source-sync",
        "oci08_cache_namespace": namespace,
        "oci08_cache_host_root": plan.remote_cache_root,
        "oci08_cache_guest_root": CACHE_GUEST,
        "oci08_cache_approved": list(APPROVED_CACHES),
        "oci08_cache_source_sync_excluded": True,
        "oci08_cache_mutates_source_tree": False,
        "oci08_cache_invalidation_inputs": {
            "profile": config.profile,
            "workspace_id": plan.workspace_id,
            "image_reference": image_reference,
            "image_digest": image_digest,
            "build_context_sha256": build_context_sha256,
        },
        "oci08_cache_detail": "approved caches are persistent worker-local binds and are excluded from managed source synchronization",
    }


def worker_reuse_policy(
    config: SSHRunnerConfig,
    preparation_metadata: Mapping[str, Any],
    *,
    external_phase_count: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Describe whether one prepared worker/session may be reused across phases."""

    lease = preparation_metadata.get("ssh_workspace_lease")
    if not isinstance(lease, Mapping):
        lease = {}
    reasons: list[str] = []
    if config.execution_provider != SSH_PROOT_OCI:
        reasons.append("execution provider is not ssh-proot-oci")
    if not config.profile:
        reasons.append("worker is not a named profile")
    if config.workspace_mode != "managed":
        reasons.append("workspace mode is not managed")
    if config.detached:
        reasons.append("detached command-mode execution cannot reuse a local phase worker")
    reusable = not reasons
    policy = "single-preparation-per-run" if reusable else "disabled"
    return {
        "oci08_worker_reuse_policy": policy,
        "oci08_worker_reuse_enabled": reusable,
        "oci08_worker_reuse_phase_count": max(0, int(external_phase_count or 0)),
        "oci08_worker_reuse_session_prefix": _text(preparation_metadata.get("ssh_session_prefix")),
        "oci08_worker_reuse_lease_id": _text(lease.get("lease_id") or lease.get("id")),
        "oci08_worker_reuse_dry_run": bool(dry_run),
        "oci08_worker_reuse_detail": (
            "one named managed worker preparation is reused for all OCI command phases"
            if reusable else "worker reuse disabled: " + "; ".join(reasons)
        ),
    }


def oci08_execution_metadata(
    config: SSHRunnerConfig,
    *,
    preparation_metadata: Mapping[str, Any],
    plan: OciWorkspacePlan,
    image_reference: str,
    image_digest: str,
    build_context_sha256: str,
    external_phase_count: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Combine OCI-08 cache/source-sync/worker-reuse truth for phase metadata."""

    payload: dict[str, Any] = {
        "oci08_schema_version": 1,
        "oci08_policy_owner": "local-devtool-orchestrator",
    }
    payload.update(source_sync_reuse_policy(preparation_metadata, dry_run=dry_run))
    payload.update(cache_reuse_policy(
        config,
        plan=plan,
        image_reference=image_reference,
        image_digest=image_digest,
        build_context_sha256=build_context_sha256,
    ))
    payload.update(worker_reuse_policy(
        config,
        preparation_metadata,
        external_phase_count=external_phase_count,
        dry_run=dry_run,
    ))
    return payload
