from __future__ import annotations

"""FRX-10 child-owned operational state metadata.

Federation coordinates repositories, but it must never make the parent the
owner of child artifacts, hook state, validation sessions, or transactions.
This module provides one small environment-derived ownership record that those
existing subsystems can persist without learning federation orchestration.
"""

import os
from pathlib import Path
from typing import Any, Mapping

from .invocation_origin import federation_lineage_from_environment


FEDERATION_OPERATION_OWNER_SCHEMA_VERSION = 1


class FederationOwnershipError(RuntimeError):
    pass


def _canonical(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


def federation_operational_owner_from_environment(
    repo_root: str | Path,
    *,
    run_id: str = "",
) -> dict[str, Any]:
    """Return explicit child operational ownership for the active federation run.

    Non-federated executions intentionally return an empty mapping.  A child
    federation environment must be internally consistent with ``DEVTOOL_RUN_ID``;
    otherwise operational state would be attributed to the wrong child run and
    execution fails closed.
    """

    lineage = federation_lineage_from_environment()
    if not lineage:
        return {}
    if str(lineage.get("relationship") or "") != "child":
        return {}

    child_run_id = str(lineage.get("child_run_id") or os.environ.get("DEVTOOL_RUN_ID") or "").strip()
    active_run_id = str(run_id or os.environ.get("DEVTOOL_RUN_ID") or "").strip()
    if not child_run_id:
        raise FederationOwnershipError("Federated child operational state is missing child_run_id")
    if active_run_id and active_run_id != child_run_id:
        raise FederationOwnershipError(
            f"Federated child operational run mismatch: active={active_run_id!r}, lineage={child_run_id!r}"
        )

    repository = str(lineage.get("repository") or lineage.get("requested_repository") or "").strip()
    fingerprint = str(lineage.get("effective_repository") or "").strip()
    if not repository or not fingerprint:
        raise FederationOwnershipError("Federated child operational ownership is missing repository identity")

    return {
        "schema_version": FEDERATION_OPERATION_OWNER_SCHEMA_VERSION,
        "kind": "federation",
        "relationship": "child",
        "repository": repository,
        "repository_fingerprint": fingerprint,
        "owner_root": _canonical(repo_root),
        "parent_run_id": str(lineage.get("parent_run_id") or ""),
        "parent_node_id": str(lineage.get("parent_node_id") or ""),
        "child_run_id": child_run_id,
        "federation_workflow": str(lineage.get("federation_workflow") or ""),
        "federation_step": str(lineage.get("federation_step") or ""),
        "execution_environment": str(lineage.get("execution_environment") or ""),
    }


def attach_federation_operational_owner(
    metadata: Mapping[str, Any] | None,
    repo_root: str | Path,
    *,
    run_id: str = "",
) -> dict[str, Any]:
    payload = dict(metadata or {})
    owner = federation_operational_owner_from_environment(repo_root, run_id=run_id)
    if owner:
        existing = payload.get("federation_owner")
        if isinstance(existing, Mapping) and dict(existing) != owner:
            raise FederationOwnershipError("Conflicting federation operational owner metadata")
        payload["federation_owner"] = owner
    return payload


def assert_federation_operational_owner(
    owner: Mapping[str, Any] | None,
    *,
    expected_root: str | Path,
    expected_run_id: str,
    expected_repository: str = "",
    expected_fingerprint: str = "",
) -> dict[str, Any]:
    if not isinstance(owner, Mapping) or not owner:
        raise FederationOwnershipError("Missing federation operational owner metadata")
    payload = {str(key): value for key, value in owner.items()}
    if str(payload.get("kind") or "") != "federation" or str(payload.get("relationship") or "") != "child":
        raise FederationOwnershipError("Operational owner is not a federated child")
    if _canonical(str(payload.get("owner_root") or "")) != _canonical(expected_root):
        raise FederationOwnershipError(
            f"Operational owner root mismatch: expected {_canonical(expected_root)}, got {payload.get('owner_root')!r}"
        )
    if str(payload.get("child_run_id") or "") != str(expected_run_id or ""):
        raise FederationOwnershipError(
            f"Operational owner run mismatch: expected {expected_run_id!r}, got {payload.get('child_run_id')!r}"
        )
    if expected_repository and str(payload.get("repository") or "") != expected_repository:
        raise FederationOwnershipError(
            f"Operational owner repository mismatch: expected {expected_repository!r}, got {payload.get('repository')!r}"
        )
    if expected_fingerprint and str(payload.get("repository_fingerprint") or "") != expected_fingerprint:
        raise FederationOwnershipError("Operational owner repository fingerprint mismatch")
    return payload


__all__ = [
    "FEDERATION_OPERATION_OWNER_SCHEMA_VERSION",
    "FederationOwnershipError",
    "assert_federation_operational_owner",
    "attach_federation_operational_owner",
    "federation_operational_owner_from_environment",
]
