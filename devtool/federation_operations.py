from __future__ import annotations

"""FRX-10 operational ownership, checkpoints, and orphan child references."""

from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
from typing import Any, Iterable, Mapping

from .artifact_registry import load_registry, registry_path
from .federation_discovery import FederatedRepositoryResolution, resolve_federated_repository
from .federation_ownership import FederationOwnershipError, assert_federation_operational_owner
from .repository_identity import RepositoryIdentity, same_repository_identity
from .transactions import list_active_transaction_records, transaction_root
from .validation_sessions import session_root

if False:  # pragma: no cover - typing-only without import cycle at runtime
    from .models import ProjectConfig


FEDERATION_OPERATIONAL_SNAPSHOT_SCHEMA_VERSION = 1


class FederationOperationalBoundaryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FederatedOperationalReference:
    kind: str
    owner_repository: str
    owner_root: str
    owner_fingerprint: str
    child_run_id: str
    identifier: str = ""
    path: str = ""
    status: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "owner_repository": self.owner_repository,
            "owner_root": self.owner_root,
            "owner_fingerprint": self.owner_fingerprint,
            "child_run_id": self.child_run_id,
            "identifier": self.identifier,
            "path": self.path,
            "status": self.status,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class FederatedOperationalSnapshot:
    repository: str
    root: str
    repository_fingerprint: str
    child_run_id: str
    references: tuple[FederatedOperationalReference, ...] = ()
    resume_checkpoint: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FEDERATION_OPERATIONAL_SNAPSHOT_SCHEMA_VERSION,
            "repository": self.repository,
            "root": self.root,
            "repository_fingerprint": self.repository_fingerprint,
            "child_run_id": self.child_run_id,
            "references": [item.as_dict() for item in self.references],
            "resume_checkpoint": dict(self.resume_checkpoint),
        }


def _canonical(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


def _git_head(root: str | Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_canonical(root),
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def capture_federated_resume_checkpoint(
    resolution: FederatedRepositoryResolution,
) -> dict[str, Any]:
    identity = resolution.repository_identity
    if identity is None:
        raise FederationOperationalBoundaryError(
            f"Federation repository {resolution.name!r} has no repository identity for resume checkpoint"
        )
    return {
        "schema_version": 1,
        "repository": resolution.name,
        "root": _canonical(resolution.root),
        "repository_fingerprint": identity.fingerprint,
        "git_head": _git_head(resolution.root),
    }


def validate_federated_resume_checkpoint(parent: "ProjectConfig", checkpoint: Mapping[str, Any]) -> FederatedRepositoryResolution:
    repository = str(checkpoint.get("repository") or "").strip()
    if not repository:
        raise FederationOperationalBoundaryError("Federation resume checkpoint is missing repository")
    resolution = resolve_federated_repository(parent, repository)
    if not resolution.available or resolution.repository_identity is None:
        raise FederationOperationalBoundaryError(f"Federation resume repository {repository!r} is unavailable")
    if _canonical(resolution.root) != _canonical(str(checkpoint.get("root") or "")):
        raise FederationOperationalBoundaryError(
            f"Federation resume repository {repository!r} moved since checkpoint"
        )
    if resolution.repository_identity.fingerprint != str(checkpoint.get("repository_fingerprint") or ""):
        raise FederationOperationalBoundaryError(
            f"Federation resume repository {repository!r} identity changed since checkpoint"
        )
    expected_head = str(checkpoint.get("git_head") or "")
    current_head = _git_head(resolution.root)
    if expected_head and current_head and expected_head != current_head:
        raise FederationOperationalBoundaryError(
            f"Federation resume repository {repository!r} revision changed: expected {expected_head}, got {current_head}"
        )
    return resolution


def _reference(
    *,
    kind: str,
    repository: str,
    root: str,
    fingerprint: str,
    run_id: str,
    identifier: str = "",
    path: str = "",
    status: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> FederatedOperationalReference:
    return FederatedOperationalReference(
        kind=kind,
        owner_repository=repository,
        owner_root=root,
        owner_fingerprint=fingerprint,
        child_run_id=run_id,
        identifier=identifier,
        path=path,
        status=status,
        metadata=dict(metadata or {}),
    )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _owned(owner: Any, *, root: str, run_id: str, repository: str, fingerprint: str) -> bool:
    try:
        assert_federation_operational_owner(
            owner if isinstance(owner, Mapping) else None,
            expected_root=root,
            expected_run_id=run_id,
            expected_repository=repository,
            expected_fingerprint=fingerprint,
        )
    except FederationOwnershipError:
        return False
    return True


def _hook_events_from_snapshot(snapshot: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if snapshot is None:
        return events
    for node in getattr(snapshot, "nodes", ()) or ():
        metadata = getattr(node, "metadata", {}) or {}
        event = metadata.get("hook_event") if isinstance(metadata, Mapping) else None
        if isinstance(event, Mapping):
            events.append(dict(event))
        many = metadata.get("hook_events") if isinstance(metadata, Mapping) else None
        if isinstance(many, list):
            events.extend(dict(item) for item in many if isinstance(item, Mapping))
    return events


def _ssh_return_lifecycle_by_digest(execution_snapshot: Any) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    if execution_snapshot is None:
        return result
    for process in getattr(execution_snapshot, "processes", ()) or ():
        provenance = getattr(process, "provenance", {}) or {}
        if not isinstance(provenance, Mapping) or str(provenance.get("transport") or "") != "ssh":
            continue
        manifest = str(provenance.get("return_manifest") or "").strip()
        if not manifest or manifest in seen:
            continue
        seen.add(manifest)
        try:
            from .ssh_returns import load_return_manifest_state
            state = load_return_manifest_state(manifest)
        except Exception:
            continue
        producer = dict(state.get("producer") or {}) if isinstance(state.get("producer"), Mapping) else {}
        for row in state.get("artifacts", []) if isinstance(state.get("artifacts"), list) else []:
            if not isinstance(row, Mapping):
                continue
            digest = str(row.get("sha256") or "").strip().lower()
            if not digest:
                continue
            result[digest] = {
                "produced": bool(row.get("produced")),
                "staged": bool(row.get("staged")),
                "transferred": bool(row.get("transferred")),
                "verified": bool(row.get("verified")),
                "restored_locally": bool(row.get("restored_locally")),
                "failed": bool(row.get("failed")),
                "recoverable": bool(row.get("recoverable")),
                "state": str(state.get("state") or ""),
                "transport_mode": str(state.get("transport_mode") or ""),
                "producer": producer,
                "producer_sha256": str(state.get("producer_sha256") or ""),
                "source_sync_id": str(state.get("source_sync_id") or ""),
            }
    return result


def collect_federated_operational_snapshot(
    resolution: FederatedRepositoryResolution,
    *,
    child_run_id: str,
    execution_snapshot: Any = None,
    resume_checkpoint: Mapping[str, Any] | None = None,
) -> FederatedOperationalSnapshot:
    identity = resolution.repository_identity
    if identity is None:
        raise FederationOperationalBoundaryError(
            f"Federation repository {resolution.name!r} has no repository identity"
        )
    root = _canonical(resolution.root)
    fingerprint = identity.fingerprint
    run_id = str(child_run_id or "").strip()
    if not run_id:
        raise FederationOperationalBoundaryError("Federated operational snapshot requires child_run_id")

    refs: list[FederatedOperationalReference] = []
    remote_lifecycle = _ssh_return_lifecycle_by_digest(execution_snapshot)

    registry = load_registry(Path(root))
    artifact_items = []
    for item in registry.get("items", []) if isinstance(registry, dict) else []:
        if not isinstance(item, dict) or str(item.get("run_id") or "") != run_id:
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        if metadata.get("federation_owner") and not _owned(
            metadata.get("federation_owner"), root=root, run_id=run_id,
            repository=resolution.name, fingerprint=fingerprint,
        ):
            raise FederationOperationalBoundaryError(
                f"Artifact record {item.get('id') or '<unknown>'} has mismatched federation owner"
            )
        artifact_items.append(item)
    if artifact_items:
        refs.append(_reference(
            kind="artifact-registry",
            repository=resolution.name,
            root=root,
            fingerprint=fingerprint,
            run_id=run_id,
            identifier=run_id,
            path=str(registry_path(Path(root))),
            status="owned",
            metadata={
                "record_ids": [str(item.get("id") or "") for item in artifact_items],
                "artifact_paths": [str(item.get("path") or "") for item in artifact_items],
                "records": [
                    {
                        "id": str(item.get("id") or ""),
                        "name": str(item.get("name") or ""),
                        "path": str(item.get("path") or ""),
                        "type": str(item.get("type") or ""),
                        "size": int(item.get("size") or 0),
                        "sha256": str(item.get("sha256") or ""),
                        "state": str(item.get("state") or ""),
                        "valid": bool(item.get("valid", True)),
                        "verification_status": str(item.get("verification_status") or ""),
                        "schema": str((item.get("metadata") or {}).get("artifact_schema") or (item.get("metadata") or {}).get("schema") or "")
                            if isinstance(item.get("metadata"), Mapping) else "",
                        "event_key": str(item.get("event_key") or ""),
                        "remote_transfer": dict(remote_lifecycle.get(str(item.get("sha256") or "").lower()) or {}),
                    }
                    for item in artifact_items
                ],
            },
        ))

    tx_root = transaction_root(Path(root))
    tx_candidates = list_active_transaction_records(Path(root))
    history = tx_root / "history"
    if history.is_dir():
        for path in sorted(history.glob("*.json")):
            tx = _load_json(path)
            if tx:
                tx.setdefault("path", str(path))
                tx_candidates.append(tx)
    seen_tx: set[str] = set()
    for tx in tx_candidates:
        if str(tx.get("run_id") or "") != run_id:
            continue
        identity_key = str(tx.get("transaction_id") or tx.get("path") or "")
        if identity_key in seen_tx:
            continue
        seen_tx.add(identity_key)
        owner = tx.get("federation_owner")
        if owner and not _owned(owner, root=root, run_id=run_id, repository=resolution.name, fingerprint=fingerprint):
            raise FederationOperationalBoundaryError(f"Transaction {identity_key!r} has mismatched federation owner")
        if _canonical(str(tx.get("repo_root") or root)) != root:
            raise FederationOperationalBoundaryError(f"Transaction {identity_key!r} escapes child repository ownership")
        refs.append(_reference(
            kind="transaction",
            repository=resolution.name,
            root=root,
            fingerprint=fingerprint,
            run_id=run_id,
            identifier=str(tx.get("transaction_id") or ""),
            path=str(tx.get("path") or ""),
            status=str(tx.get("status") or ""),
            metadata={
                "failure_action": str(tx.get("failure_action") or ""),
                "rollback_scope": str((tx.get("git_state") or {}).get("rollback_scope") or "") if isinstance(tx.get("git_state"), dict) else "",
            },
        ))

    validation_root = session_root(Path(root))
    if validation_root.is_dir():
        for path in sorted(validation_root.glob("*/state.json")):
            session = _load_json(path)
            if not session:
                continue
            context = session.get("apply_context") if isinstance(session.get("apply_context"), dict) else {}
            owner = context.get("federation_owner") if isinstance(context, dict) else None
            if not owner:
                continue
            if not _owned(owner, root=root, run_id=run_id, repository=resolution.name, fingerprint=fingerprint):
                continue
            if _canonical(str(session.get("repo_root") or root)) != root:
                raise FederationOperationalBoundaryError(
                    f"Validation session {session.get('session_id') or path.parent.name!r} escapes child repository ownership"
                )
            refs.append(_reference(
                kind="validation-session",
                repository=resolution.name,
                root=root,
                fingerprint=fingerprint,
                run_id=run_id,
                identifier=str(session.get("session_id") or path.parent.name),
                path=str(path),
                status=str(session.get("status") or ""),
                metadata={
                    "required_checkpoint_key": str(session.get("required_checkpoint_key") or ""),
                    "required_checkpoint_keys": list(session.get("required_checkpoint_keys") or []),
                    "failure_action": str(session.get("failure_action") or ""),
                },
            ))

    for index, event in enumerate(_hook_events_from_snapshot(execution_snapshot)):
        owner = event.get("federation_owner")
        if not owner:
            continue
        if not _owned(owner, root=root, run_id=run_id, repository=resolution.name, fingerprint=fingerprint):
            raise FederationOperationalBoundaryError(f"Hook event {index} has mismatched federation owner")
        refs.append(_reference(
            kind="hook-event",
            repository=resolution.name,
            root=root,
            fingerprint=fingerprint,
            run_id=run_id,
            identifier=f"hook-{index}",
            status=str(event.get("status") or ""),
            metadata={
                "hook_kind": str(event.get("kind") or ""),
                "phase": str(event.get("phase") or ""),
                "script_path": str(event.get("script_path") or ""),
            },
        ))

    if execution_snapshot is not None:
        for artifact in getattr(execution_snapshot, "artifacts", ()) or ():
            refs.append(_reference(
                kind="execution-artifact",
                repository=resolution.name,
                root=root,
                fingerprint=fingerprint,
                run_id=run_id,
                identifier=str(getattr(artifact, "artifact_id", "") or ""),
                path=str(getattr(artifact, "path", "") or ""),
                status=str(getattr(artifact, "verification_status", "") or "reference"),
                metadata={
                    "sha256": str(getattr(artifact, "sha256", "") or ""),
                    "registry_artifact_id": str(getattr(artifact, "registry_artifact_id", "") or ""),
                    "artifact_type": str(getattr(artifact, "artifact_type", "") or ""),
                    "size": int(getattr(artifact, "size", 0) or 0),
                    "remote_transfer": dict(remote_lifecycle.get(str(getattr(artifact, "sha256", "") or "").lower()) or {}),
                },
            ))
        for diagnostic in getattr(execution_snapshot, "diagnostics", ()) or ():
            refs.append(_reference(
                kind="diagnostic",
                repository=resolution.name,
                root=root,
                fingerprint=fingerprint,
                run_id=run_id,
                identifier=str(getattr(diagnostic, "diagnostic_id", "") or ""),
                path=str(getattr(diagnostic, "log_path", "") or ""),
                status=str(getattr(diagnostic, "severity", "") or ""),
            ))
        log_paths = []
        for process in getattr(execution_snapshot, "processes", ()) or ():
            value = str(getattr(process, "log_path", "") or "")
            if value and value not in log_paths:
                log_paths.append(value)
        for index, path in enumerate(log_paths):
            refs.append(_reference(
                kind="log",
                repository=resolution.name,
                root=root,
                fingerprint=fingerprint,
                run_id=run_id,
                identifier=f"log-{index}",
                path=path,
                status="reference",
            ))

    checkpoint = dict(resume_checkpoint or capture_federated_resume_checkpoint(resolution))
    return FederatedOperationalSnapshot(
        repository=resolution.name,
        root=root,
        repository_fingerprint=fingerprint,
        child_run_id=run_id,
        references=tuple(refs),
        resume_checkpoint=checkpoint,
    )


def discover_federated_orphan_children(
    snapshots: Iterable[Any],
    *,
    parent_run_id: str,
) -> tuple[str, ...]:
    parent = str(parent_run_id or "").strip()
    children: list[str] = []
    parent_present = False
    for snapshot in snapshots:
        run_id = str(getattr(snapshot, "run_id", "") or "")
        if run_id == parent:
            parent_present = True
        lineage = getattr(snapshot, "run_lineage", {}) or {}
        if not isinstance(lineage, Mapping):
            continue
        if str(lineage.get("relationship") or "") != "child":
            continue
        if str(lineage.get("parent_run_id") or "") == parent and run_id:
            children.append(run_id)
    return tuple(sorted(set(children))) if not parent_present else ()


def request_federated_child_control(
    child_run_id: str,
    *,
    delegation_id: str,
    reason: str = "Federation parent cancellation",
    provider_session_binding_id: str = "",
    requested_by_run_id: str = "",
    telemetry_health: str = "unknown",
):
    """Send canonical typed child control independently of event-channel health."""
    run_id = str(child_run_id or "").strip()
    if not run_id:
        raise FederationOperationalBoundaryError("No active federated child run is available for control.")
    from .distributed_provider_control import DelegatedControlChannel, DelegatedControlRequest
    from .ipc import request_run_cancel

    request = DelegatedControlRequest.cancel(
        delegation_id=str(delegation_id or "").strip(),
        child_run_id=run_id,
        reason=reason,
        provider_session_binding_id=provider_session_binding_id,
        requested_by_run_id=requested_by_run_id,
    )
    channel = DelegatedControlChannel()
    return channel.dispatch(
        request,
        cancel=lambda candidate, why: request_run_cancel(candidate, reason=why),
        telemetry_health=telemetry_health,
    )


def request_federated_child_cancellation(
    child_run_id: str,
    *,
    reason: str = "Federation parent cancellation",
    delegation_id: str = "",
    provider_session_binding_id: str = "",
    requested_by_run_id: str = "",
    telemetry_health: str = "unknown",
) -> tuple[bool, str]:
    """Compatibility surface over typed control, with legacy IPC fallback.

    Cleanup/rollback remains the child command's responsibility.  Callers with
    canonical delegation identity use the typed request/ack channel; legacy
    callers remain behavior-compatible until their owning adoption boundary.
    """
    run_id = str(child_run_id or "").strip()
    if not run_id:
        return False, "No active federated child run is available for cancellation."
    if delegation_id:
        ack = request_federated_child_control(
            run_id,
            delegation_id=delegation_id,
            reason=reason,
            provider_session_binding_id=provider_session_binding_id,
            requested_by_run_id=requested_by_run_id,
            telemetry_health=telemetry_health,
        )
        return ack.accepted, ack.detail
    from .ipc import request_run_cancel
    return request_run_cancel(run_id, reason=reason)


__all__ = [
    "FEDERATION_OPERATIONAL_SNAPSHOT_SCHEMA_VERSION",
    "FederatedOperationalReference",
    "FederatedOperationalSnapshot",
    "FederationOperationalBoundaryError",
    "capture_federated_resume_checkpoint",
    "collect_federated_operational_snapshot",
    "discover_federated_orphan_children",
    "request_federated_child_control",
    "request_federated_child_cancellation",
    "validate_federated_resume_checkpoint",
]
