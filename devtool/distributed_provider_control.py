from __future__ import annotations

"""DIST-X03 provider/session, control, hierarchy and SSH journal convergence.

RUN V3 remains execution/history authority.  This module contributes immutable
provider/session bindings, a typed control channel, route-cycle protection and
provider projections over the existing RUN/SSH/LIFE substrates.
"""

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence

from .cleanup_lifecycle import CleanupScope, GeneratedOutputDescriptor
from .distributed_execution import DelegationLink, canonical_digest, reconcile_terminal_truth
from .distributed_transport import (
    DeliveryAck,
    DeliveryCursorStore,
    DelegatedEventReceiver,
    DelegatedExecutionReceipt,
    EventDeliveryState,
    FEATURE_CURSOR_ACK,
    FEATURE_EVENT_ENVELOPE,
    FEATURE_GAP_CATCHUP,
    NegotiatedTransportProtocol,
    TransportFrame,
    TransportProtocolCapabilities,
    frames_from_origin_journal_text,
    negotiate_transport_protocol,
)

DIST_CONTROL_SCHEMA_VERSION = 1
DIST_ROUTE_SCHEMA_VERSION = 1


class DistributedProviderControlError(RuntimeError):
    pass


class DelegationRouteCycleError(DistributedProviderControlError):
    pass


class ProviderSessionBindingError(DistributedProviderControlError):
    pass


class DelegatedControlConflictError(DistributedProviderControlError):
    pass


def _stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_stable(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DelegationRouteHop:
    delegation_id: str
    child_run_id: str
    provider_id: str = ""
    repository: str = ""
    target: str = ""

    def __post_init__(self) -> None:
        if not self.delegation_id or not self.child_run_id:
            raise ValueError("delegation route hop requires delegation_id and child_run_id")

    def as_dict(self) -> dict[str, Any]:
        return {
            "delegation_id": self.delegation_id,
            "child_run_id": self.child_run_id,
            "provider_id": self.provider_id,
            "repository": self.repository,
            "target": self.target,
        }


@dataclass(frozen=True, slots=True)
class DelegationRoute:
    hops: tuple[DelegationRouteHop, ...] = ()
    max_hops: int = 16

    def __post_init__(self) -> None:
        if self.max_hops < 1 or self.max_hops > 128:
            raise ValueError("delegation route max_hops must be between 1 and 128")
        if len(self.hops) > self.max_hops:
            raise DelegationRouteCycleError("delegation route exceeds maximum hop count")
        delegations: set[str] = set()
        runs: set[str] = set()
        for hop in self.hops:
            if hop.delegation_id in delegations or hop.child_run_id in runs:
                raise DelegationRouteCycleError("delegation route contains a cycle/repeated child authority")
            delegations.add(hop.delegation_id)
            runs.add(hop.child_run_id)

    @classmethod
    def from_json(cls, raw: str | None, *, max_hops: int = 16) -> "DelegationRoute":
        text = str(raw or "").strip()
        if not text:
            return cls(max_hops=max_hops)
        payload = json.loads(text)
        if not isinstance(payload, Mapping):
            raise DelegationRouteCycleError("delegation route payload must be an object")
        rows = payload.get("hops") or []
        if not isinstance(rows, list):
            raise DelegationRouteCycleError("delegation route hops must be a list")
        hops = tuple(
            DelegationRouteHop(
                delegation_id=str(item.get("delegation_id") or ""),
                child_run_id=str(item.get("child_run_id") or ""),
                provider_id=str(item.get("provider_id") or ""),
                repository=str(item.get("repository") or ""),
                target=str(item.get("target") or ""),
            )
            for item in rows if isinstance(item, Mapping)
        )
        return cls(hops=hops, max_hops=int(payload.get("max_hops") or max_hops))

    def append(self, link: DelegationLink) -> "DelegationRoute":
        provider_id = str(link.provider_identity.get("provider_id") or link.provider_identity.get("profile") or "")
        hop = DelegationRouteHop(
            delegation_id=link.delegation_id,
            child_run_id=link.child_run_id,
            provider_id=provider_id,
            repository=link.repository,
            target=link.target,
        )
        return DelegationRoute((*self.hops, hop), max_hops=self.max_hops)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": DIST_ROUTE_SCHEMA_VERSION,
            "kind": "delegation-route",
            "max_hops": self.max_hops,
            "hops": [item.as_dict() for item in self.hops],
            "route_digest": _digest([item.as_dict() for item in self.hops]),
        }

    def to_json(self) -> str:
        return _stable(self.as_dict())


@dataclass(frozen=True, slots=True)
class ProviderSessionBinding:
    delegation_id: str
    child_run_id: str
    provider_id: str
    provider_kind: str
    profile: str
    destination: str
    session_id: str
    provider_evidence_digest: str
    attestation_digest: str
    source_lock_digest: str = ""
    binding_id: str = ""

    def __post_init__(self) -> None:
        if not self.delegation_id or not self.child_run_id or not self.provider_kind or not self.session_id:
            raise ValueError("provider session binding requires delegation/run/provider/session identity")
        expected = canonical_digest({
            "delegation_id": self.delegation_id,
            "child_run_id": self.child_run_id,
            "provider_id": self.provider_id,
            "provider_kind": self.provider_kind,
            "profile": self.profile,
            "destination": self.destination,
            "session_id": self.session_id,
            "provider_evidence_digest": self.provider_evidence_digest,
            "attestation_digest": self.attestation_digest,
            "source_lock_digest": self.source_lock_digest,
        })
        if self.binding_id and self.binding_id != expected:
            raise ProviderSessionBindingError("provider session binding digest mismatch")
        if not self.binding_id:
            object.__setattr__(self, "binding_id", expected)

    @classmethod
    def bind(
        cls,
        link: DelegationLink,
        *,
        session_id: str,
        profile: str = "",
        destination: str = "",
        provider_kind: str = "",
        provider_id: str = "",
        provider_evidence_digest: str = "",
        attestation_digest: str = "",
    ) -> "ProviderSessionBinding":
        planned = dict(link.provider_identity)
        actual_provider_id = provider_id or str(planned.get("provider_id") or profile or "")
        actual_kind = provider_kind or str(planned.get("provider_kind") or "ssh")
        actual_profile = profile or str(planned.get("profile") or "")
        actual_evidence = provider_evidence_digest or str(planned.get("provider_evidence_digest") or "")
        actual_attestation = attestation_digest or link.attestation_digest
        for label, actual, expected in (
            ("provider_id", actual_provider_id, str(planned.get("provider_id") or "")),
            ("provider_kind", actual_kind, str(planned.get("provider_kind") or "")),
            ("profile", actual_profile, str(planned.get("profile") or "")),
            ("provider_evidence_digest", actual_evidence, str(planned.get("provider_evidence_digest") or "")),
            ("attestation_digest", actual_attestation, link.attestation_digest),
        ):
            if expected and actual != expected:
                raise ProviderSessionBindingError(f"provider session {label} does not match planned delegation")
        return cls(
            delegation_id=link.delegation_id,
            child_run_id=link.child_run_id,
            provider_id=actual_provider_id,
            provider_kind=actual_kind,
            profile=actual_profile,
            destination=destination,
            session_id=session_id,
            provider_evidence_digest=actual_evidence,
            attestation_digest=actual_attestation,
            source_lock_digest=link.source_lock_digest,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "provider-session-binding",
            "binding_id": self.binding_id,
            "delegation_id": self.delegation_id,
            "child_run_id": self.child_run_id,
            "provider_id": self.provider_id,
            "provider_kind": self.provider_kind,
            "profile": self.profile,
            "destination": self.destination,
            "session_id": self.session_id,
            "provider_evidence_digest": self.provider_evidence_digest,
            "attestation_digest": self.attestation_digest,
            "source_lock_digest": self.source_lock_digest,
        }


@dataclass(frozen=True, slots=True)
class DelegatedControlRequest:
    delegation_id: str
    child_run_id: str
    action: str
    reason: str = ""
    provider_session_binding_id: str = ""
    requested_by_run_id: str = ""
    request_id: str = ""
    schema_version: int = DIST_CONTROL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        action = str(self.action or "").strip().lower()
        if action not in {"cancel", "interrupt"}:
            raise ValueError(f"unsupported delegated control action {self.action!r}")
        object.__setattr__(self, "action", action)
        if not self.delegation_id or not self.child_run_id:
            raise ValueError("delegated control request requires delegation_id and child_run_id")
        expected = canonical_digest({
            "delegation_id": self.delegation_id,
            "child_run_id": self.child_run_id,
            "action": action,
            "reason": self.reason,
            "provider_session_binding_id": self.provider_session_binding_id,
            "requested_by_run_id": self.requested_by_run_id,
        })
        if self.request_id and self.request_id != expected:
            raise DelegatedControlConflictError("delegated control request id/body mismatch")
        if not self.request_id:
            object.__setattr__(self, "request_id", expected)

    @classmethod
    def cancel(
        cls,
        *,
        delegation_id: str,
        child_run_id: str,
        reason: str,
        provider_session_binding_id: str = "",
        requested_by_run_id: str = "",
    ) -> "DelegatedControlRequest":
        return cls(
            delegation_id=delegation_id,
            child_run_id=child_run_id,
            action="cancel",
            reason=reason,
            provider_session_binding_id=provider_session_binding_id,
            requested_by_run_id=requested_by_run_id,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": "delegated-control-request",
            "request_id": self.request_id,
            "delegation_id": self.delegation_id,
            "child_run_id": self.child_run_id,
            "action": self.action,
            "reason": self.reason,
            "provider_session_binding_id": self.provider_session_binding_id,
            "requested_by_run_id": self.requested_by_run_id,
        }


@dataclass(frozen=True, slots=True)
class DelegatedControlAck:
    request_id: str
    delegation_id: str
    child_run_id: str
    action: str
    accepted: bool
    detail: str
    provider_session_binding_id: str = ""
    ack_id: str = ""

    def __post_init__(self) -> None:
        expected = canonical_digest({
            "request_id": self.request_id,
            "delegation_id": self.delegation_id,
            "child_run_id": self.child_run_id,
            "action": self.action,
            "accepted": bool(self.accepted),
            "detail": self.detail,
            "provider_session_binding_id": self.provider_session_binding_id,
        })
        if self.ack_id and self.ack_id != expected:
            raise DelegatedControlConflictError("delegated control acknowledgement digest mismatch")
        if not self.ack_id:
            object.__setattr__(self, "ack_id", expected)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "delegated-control-ack",
            "ack_id": self.ack_id,
            "request_id": self.request_id,
            "delegation_id": self.delegation_id,
            "child_run_id": self.child_run_id,
            "action": self.action,
            "accepted": self.accepted,
            "detail": self.detail,
            "provider_session_binding_id": self.provider_session_binding_id,
        }


class DelegatedControlChannel:
    """Typed, idempotent control path independent of telemetry health."""

    def __init__(self) -> None:
        self._requests: dict[str, DelegatedControlRequest] = {}
        self._acks: dict[str, DelegatedControlAck] = {}

    def dispatch(
        self,
        request: DelegatedControlRequest,
        *,
        cancel: Callable[[str, str], tuple[bool, str] | bool],
        telemetry_health: str = "live",
    ) -> DelegatedControlAck:
        previous = self._requests.get(request.request_id)
        if previous is not None:
            if previous != request:
                raise DelegatedControlConflictError("conflicting delegated control request replay")
            return self._acks[request.request_id]
        # telemetry_health is deliberately observed but never gates cancellation.
        _ = str(telemetry_health or "unknown")
        if request.action not in {"cancel", "interrupt"}:
            raise DistributedProviderControlError(f"unsupported delegated control action {request.action}")
        outcome = cancel(request.child_run_id, request.reason)
        if isinstance(outcome, tuple):
            accepted, detail = bool(outcome[0]), str(outcome[1])
        else:
            accepted, detail = bool(outcome), "accepted" if outcome else "rejected"
        ack = DelegatedControlAck(
            request_id=request.request_id,
            delegation_id=request.delegation_id,
            child_run_id=request.child_run_id,
            action=request.action,
            accepted=accepted,
            detail=detail,
            provider_session_binding_id=request.provider_session_binding_id,
        )
        self._requests[request.request_id] = request
        self._acks[request.request_id] = ack
        return ack


@dataclass(frozen=True, slots=True)
class PrivateTransportHook:
    secret_reference_ids: tuple[str, ...]
    secret_key_names: tuple[str, ...]
    transport: str
    hook_id: str = ""

    def __post_init__(self) -> None:
        refs = tuple(dict.fromkeys(str(item) for item in self.secret_reference_ids if str(item)))
        keys = tuple(dict.fromkeys(str(item) for item in self.secret_key_names if str(item)))
        object.__setattr__(self, "secret_reference_ids", refs)
        object.__setattr__(self, "secret_key_names", keys)
        expected = canonical_digest({"refs": refs, "keys": keys, "transport": self.transport})
        if not self.hook_id:
            object.__setattr__(self, "hook_id", expected)
        elif self.hook_id != expected:
            raise ValueError("private transport hook digest mismatch")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "private-transport-hook",
            "hook_id": self.hook_id,
            "secret_reference_ids": list(self.secret_reference_ids),
            "secret_key_names": list(self.secret_key_names),
            "transport": self.transport,
            "contains_secret_values": False,
        }


def typed_tool_argv_evidence(argv: Sequence[str]) -> dict[str, Any]:
    tokens = tuple(str(item) for item in argv)
    return {
        "kind": "typed-tool-argv-evidence",
        "argv_digest": canonical_digest({"argv": list(tokens)}),
        "token_count": len(tokens),
        "argv": list(tokens),
        "shell_reparsed": False,
    }


def provider_cleanup_authority_metadata(
    *,
    destination: str,
    provider: str,
    run_id: str,
    session: str,
    stage_path: str = "",
    state_path: str = "",
    cursor_path: str = "",
    delegation_id: str = "",
) -> dict[str, Any]:
    """Return lifecycle metadata proving provider resources are LIFE-owned.

    Production SSH/federation provider phases persist only a digest and count in
    ordinary phase metadata.  Full path descriptors remain reconstructable via
    ``provider_cleanup_descriptors`` and provider status queries, avoiding a
    second cleanup authority or public leakage of remote staging paths.
    """
    descriptors = provider_cleanup_descriptors(
        destination=destination,
        provider=provider,
        run_id=run_id,
        session=session,
        stage_path=stage_path,
        state_path=state_path,
        cursor_path=cursor_path,
        delegation_id=delegation_id,
    )
    payload = [item.as_dict() for item in descriptors]
    return {
        "dist_provider_lifecycle_authority": "RUN/LIFE",
        "dist_provider_cleanup_descriptor_count": len(payload),
        "dist_provider_cleanup_digest": canonical_digest(payload),
    }


def ssh_phase_dist_metadata(
    *,
    link: DelegationLink | None,
    child_run_id: str,
    remote_root: str,
    session: str,
    profile: str,
    destination: str,
    provider_kind: str,
    provider_evidence_digest: str = "",
    stage_path: str = "",
    provider_state_root: str = "~/.cache/devtool/ssh-runs",
    secret_keys: Iterable[str] = (),
    secret_reference_ids: Iterable[str] = (),
    original_argv: Sequence[str] = (),
) -> dict[str, Any]:
    if not child_run_id:
        return {}
    root = str(remote_root or "").rstrip("/") or "."
    journal = str(PurePosixPath(root) / ".devtool" / "private" / "run-v3" / child_run_id / "execution-events.jsonl")
    state_root = str(PurePosixPath(str(provider_state_root or "~/.cache/devtool/ssh-runs").rstrip("/")) / child_run_id / session)
    cursor_path = str(PurePosixPath(state_root) / "event-cursor.json")
    result: dict[str, Any] = {
        "dist_child_run_id": child_run_id,
        "dist_origin_journal_path": journal,
        "dist_provider_state_path": state_root,
        "dist_event_cursor_path": cursor_path,
        "dist_tool_argv_digest": typed_tool_argv_evidence(original_argv)["argv_digest"],
        "dist_tool_argv_token_count": len(tuple(original_argv)),
    }
    if link is not None:
        binding = ProviderSessionBinding.bind(
            link,
            session_id=session,
            profile=profile,
            destination=destination,
            provider_kind=provider_kind,
            provider_evidence_digest=provider_evidence_digest,
        )
        result.update({
            "dist_delegation_id": link.delegation_id,
            "dist_attestation_digest": link.attestation_digest,
            "dist_provider_session_binding_id": binding.binding_id,
        })
    result.update(provider_cleanup_authority_metadata(
        destination=destination,
        provider=provider_kind,
        run_id=child_run_id,
        session=session,
        stage_path=stage_path,
        state_path=state_root,
        cursor_path=cursor_path,
        delegation_id=str(link.delegation_id if link is not None else ""),
    ))
    hook = PrivateTransportHook(
        secret_reference_ids=tuple(secret_reference_ids),
        secret_key_names=tuple(secret_keys),
        transport=provider_kind,
    )
    if hook.secret_key_names or hook.secret_reference_ids:
        result["dist_private_transport_hook_id"] = hook.hook_id
        result["dist_private_secret_count"] = len(hook.secret_key_names)
    return result


def provider_cleanup_descriptors(
    *,
    destination: str,
    provider: str,
    run_id: str,
    session: str,
    stage_path: str = "",
    state_path: str = "",
    cursor_path: str = "",
    delegation_id: str = "",
) -> tuple[GeneratedOutputDescriptor, ...]:
    rows: list[tuple[str, str]] = []
    if stage_path:
        rows.append(("provider-staging", stage_path))
    if state_path:
        rows.append(("provider-session-state", state_path))
    if cursor_path:
        rows.append(("event-cursor", cursor_path))
    descriptors: list[GeneratedOutputDescriptor] = []
    for category, path in rows:
        rid = "generated:" + canonical_digest({
            "destination": destination,
            "provider": provider,
            "run_id": run_id,
            "session": session,
            "category": category,
            "path": path,
            "delegation_id": delegation_id,
        })[:32]
        descriptors.append(GeneratedOutputDescriptor(
            resource_id=rid,
            path=path,
            owner="DIST",
            provider=provider,
            category=category,
            producer_id=run_id or session,
            lifecycle="transient",
            rebuild_cost="low",
            scope=CleanupScope.REMOTE,
            destination=destination,
            network_cost="remote",
            provenance={
                "run_id": run_id,
                "session": session,
                "delegation_id": delegation_id,
                "authority": "RUN/LIFE",
            },
            managed=True,
            published=False,
        ))
    return tuple(descriptors)


@dataclass(slots=True)
class SSHProviderEventChannel:
    link: DelegationLink
    binding: ProviderSessionBinding
    cursor_store: DeliveryCursorStore
    read_journal_text: Callable[[], str]
    protocol: NegotiatedTransportProtocol

    def frames_after_ack(self, *, delivery_mode: str = "catch-up") -> tuple[TransportFrame, ...]:
        cursor = self.cursor_store.load(link=self.link)
        return frames_from_origin_journal_text(
            self.read_journal_text(),
            child_run_id=self.link.child_run_id,
            link=self.link,
            acknowledged_sequence=cursor.acknowledged_sequence,
            delivery_mode=delivery_mode,
        )

    def catch_up(self, receiver: DelegatedEventReceiver) -> tuple[DeliveryAck, ...]:
        acks: list[DeliveryAck] = []
        cursor = self.cursor_store.load(link=self.link)
        for frame in frames_from_origin_journal_text(
            self.read_journal_text(),
            child_run_id=self.link.child_run_id,
            link=self.link,
            acknowledged_sequence=cursor.acknowledged_sequence,
            delivery_mode="catch-up",
        ):
            ack = receiver.receive(frame)
            from .distributed_transport import DeliveryCursor
            self.cursor_store.save(DeliveryCursor(
                self.link.delegation_id,
                self.link.child_run_id,
                acknowledged_sequence=receiver.acknowledged_sequence,
                acknowledged_event_id=receiver.acknowledged_event_id,
                acknowledged_content_digest=receiver.acknowledged_content_digest,
                replay_count=receiver.replay_count,
                unresolved_gaps=(),
                health="reconciled",
                last_error="",
            ))
            acks.append(ack)
        return tuple(acks)

    def health(self) -> EventDeliveryState:
        cursor = self.cursor_store.load(link=self.link)
        return EventDeliveryState(
            delegation_id=self.link.delegation_id,
            child_run_id=self.link.child_run_id,
            health=cursor.health,
            acknowledged_sequence=cursor.acknowledged_sequence,
            pending_hint_count=0,
            replay_count=cursor.replay_count,
            unresolved_gaps=cursor.unresolved_gaps,
            overflow_count=0,
            last_error=cursor.last_error,
        )

    def receipt(
        self,
        *,
        child_snapshot: Any | None,
        transport_exit_code: int | None = None,
    ) -> DelegatedExecutionReceipt:
        state = self.health()
        terminal_sequence = int(getattr(child_snapshot, "last_sequence", 0) or 0) if child_snapshot is not None else 0
        child_status = str(getattr(child_snapshot, "status", "") or "") if child_snapshot is not None else ""
        child_exit = getattr(child_snapshot, "exit_code", None) if child_snapshot is not None else None
        snapshot_payload = (
            child_snapshot.to_dict() if child_snapshot is not None and hasattr(child_snapshot, "to_dict") else {
                "run_id": self.link.child_run_id,
                "status": child_status,
                "exit_code": child_exit,
                "last_sequence": terminal_sequence,
            }
        )
        snapshot_digest = canonical_digest(snapshot_payload) if child_snapshot is not None else ""
        frames = frames_from_origin_journal_text(
            self.read_journal_text(),
            child_run_id=self.link.child_run_id,
            link=self.link,
            acknowledged_sequence=0,
            delivery_mode="receipt",
        )
        first_sequence = int(frames[0].sequence) if frames else 0
        last_sequence = int(frames[-1].sequence) if frames else 0
        integrity_error = ""
        reconciled = False
        if child_snapshot is None:
            integrity_error = "child terminal snapshot unavailable"
        elif state.acknowledged_sequence != terminal_sequence:
            integrity_error = "SSH delivery cursor has not reached the child terminal snapshot"
        elif state.unresolved_gaps:
            integrity_error = "SSH delivery cursor retains unresolved origin gaps"
        else:
            terminal = reconcile_terminal_truth(
                transport_exit_code=(
                    int(transport_exit_code)
                    if transport_exit_code is not None
                    else int(child_exit if child_exit is not None else 0)
                ),
                child_snapshot=child_snapshot,
            )
            reconciled = terminal.ok
            integrity_error = terminal.integrity_error
        if integrity_error:
            reconciled = False
        final_health = "reconciled" if reconciled else (state.health if state.health != "idle" else "degraded")
        if final_health != state.health:
            state = EventDeliveryState(
                state.delegation_id, state.child_run_id, final_health,
                state.acknowledged_sequence, state.pending_hint_count, state.replay_count,
                state.unresolved_gaps, state.overflow_count, state.last_error,
            )
        provider_identity = dict(self.link.provider_identity)
        provider_identity["provider_session_binding"] = self.binding.as_dict()
        provider_identity["transport"] = "ssh"
        return DelegatedExecutionReceipt(
            delegation_id=self.link.delegation_id,
            child_run_id=self.link.child_run_id,
            delivery=state,
            first_origin_sequence=first_sequence,
            last_origin_sequence=max(last_sequence, terminal_sequence),
            terminal_sequence=terminal_sequence,
            terminal_snapshot_digest=snapshot_digest,
            child_status=child_status,
            child_exit_code=(int(child_exit) if child_exit is not None else None),
            transport_exit_code=(int(transport_exit_code) if transport_exit_code is not None else None),
            reconciled=reconciled,
            integrity_error=integrity_error,
            provider_identity=provider_identity,
            protocol=self.protocol.as_dict(),
            raw_stream_references=sum(1 for frame in frames if frame.content_class == "raw-stream-reference-v1"),
        )


def run_relation_from_delegation(
    link: DelegationLink,
    *,
    delivery_health: str = "unknown",
    pinned: bool = False,
):
    """Project a delegation into RUN V3 without creating a DIST hierarchy store."""
    from .run_store import RunRelation

    return RunRelation(
        relation_id="",
        parent_run_id=link.run_link.parent_run_id,
        child_run_id=link.child_run_id,
        parent_node_id=link.run_link.parent_node_id,
        relationship="delegated-child",
        delegation_id=link.delegation_id,
        provider_identity=dict(link.provider_identity),
        delivery_health=str(delivery_health or "unknown"),
        pinned=bool(pinned),
    )


def record_delegation_relation(
    repo_root: str | Path,
    link: DelegationLink,
    *,
    delivery_health: str = "unknown",
    pinned: bool = False,
):
    """Persist one canonical parent/child link into the repository RUN V3 catalog."""
    from .core_runtime import private_run_root
    from .run_store import RunCatalog

    # Parallel federation may create multiple children in worker threads.  Keep
    # catalog open/read/write atomic within the coordinator process so one child
    # cannot overwrite a sibling relation.
    import threading
    lock = getattr(record_delegation_relation, "_lock", None)
    if lock is None:
        lock = threading.Lock()
        setattr(record_delegation_relation, "_lock", lock)
    relation = run_relation_from_delegation(link, delivery_health=delivery_health, pinned=pinned)
    with lock:
        catalog = RunCatalog.open(private_run_root(repo_root))
        catalog.update_relation_state(
            relation,
            delivery_health=str(delivery_health or "unknown"),
            pinned=bool(pinned) if pinned else None,
        )
        catalog.save()
        if str(delivery_health or "").strip().lower() in {"reconciled", "completed", "terminal"}:
            from .provider_resource_registry import release_provider_resources
            release_provider_resources(
                repo_root,
                delegation_id=link.delegation_id,
                run_id=link.child_run_id,
                reason=f"RUN relation delivery health {delivery_health}",
            )
    return relation


def _ssh_peer_capabilities_from_journal(text: str) -> TransportProtocolCapabilities:
    for raw in str(text or "").splitlines():
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, Mapping) or str(payload.get("event_type") or "") != "run.started":
            continue
        metadata = payload.get("metadata")
        capabilities = metadata.get("dist_transport_capabilities") if isinstance(metadata, Mapping) else None
        peer = str(metadata.get("dist_transport_peer") or "") if isinstance(metadata, Mapping) else ""
        if peer != "ssh-remote-devtool" or not isinstance(capabilities, Mapping):
            raise ProviderSessionBindingError(
                "SSH remote RUN journal lacks authenticated remote Devtool transport capabilities"
            )
        return TransportProtocolCapabilities.from_dict(capabilities)
    raise ProviderSessionBindingError(
        "SSH remote RUN journal has no RunStarted peer-capability record yet"
    )


def ssh_provider_event_channel(
    config: Any,
    *,
    link: DelegationLink,
    session: str = "",
    cursor_path: str | Path,
) -> SSHProviderEventChannel:
    """Bind the *actual* SSH worker RUN journal to DIST delivery.

    ``session`` may be omitted by a federation parent.  In that case the
    provider is queried by canonical child run id and the observed worker
    session becomes the binding identity.  Planned delegation/session identity
    is then checked before any remote journal is admitted.
    """
    from .ssh_resilience import read_remote_run_journal

    observation = read_remote_run_journal(config, run_id=link.child_run_id, session=session)
    if not isinstance(observation, Mapping):
        raise ProviderSessionBindingError("SSH provider journal observation is malformed")
    observed_child = str(observation.get("child_run_id") or observation.get("run_id") or "").strip()
    if observed_child and observed_child != link.child_run_id:
        raise ProviderSessionBindingError("SSH provider journal child run does not match delegation")
    observed_delegation = str(observation.get("delegation_id") or "").strip()
    if observed_delegation and observed_delegation != link.delegation_id:
        raise ProviderSessionBindingError("SSH provider journal delegation does not match planned delegation")
    observed_session = str(observation.get("session") or session or "").strip()
    if not observed_session:
        raise ProviderSessionBindingError("SSH provider journal did not identify its worker session")
    binding = ProviderSessionBinding.bind(
        link,
        session_id=observed_session,
        profile=str(getattr(config, "profile", "") or ""),
        destination=str(getattr(config, "destination", "") or ""),
        provider_kind=str(link.provider_identity.get("provider_kind") or "ssh"),
        provider_evidence_digest=str(link.provider_identity.get("provider_evidence_digest") or ""),
        attestation_digest=link.attestation_digest,
    )
    observed_binding = str(observation.get("provider_session_binding_id") or "").strip()
    if observed_binding and observed_binding != binding.binding_id:
        raise ProviderSessionBindingError("SSH provider session binding does not match remote RUN evidence")

    initial_journal = str(observation.get("journal") or "")
    remote_capabilities = _ssh_peer_capabilities_from_journal(initial_journal)
    protocol = negotiate_transport_protocol(
        TransportProtocolCapabilities(),
        remote_capabilities,
        required_features={FEATURE_EVENT_ENVELOPE, FEATURE_CURSOR_ACK, FEATURE_GAP_CATCHUP},
    )

    def read() -> str:
        payload = read_remote_run_journal(config, run_id=link.child_run_id, session=observed_session)
        return str(payload.get("journal") or "") if isinstance(payload, Mapping) else ""

    return SSHProviderEventChannel(
        link=link,
        binding=binding,
        cursor_store=DeliveryCursorStore(cursor_path),
        read_journal_text=read,
        protocol=protocol,
    )


def dispatch_ssh_provider_control(
    config: Any,
    *,
    link: DelegationLink,
    reason: str = "Federation parent cancellation",
    requested_by_run_id: str = "",
    telemetry_health: str = "unknown",
) -> DelegatedControlAck:
    """Dispatch typed cancellation through the real SSH provider substrate.

    The remote worker record is first observed to bind the concrete session.
    Cancellation then calls ``ssh_resilience.cancel_remote_run`` rather than
    local IPC.  The resulting ack therefore cannot claim provider control when
    no matching remote run/session exists.
    """
    from .ssh_resilience import cancel_remote_run, read_remote_run_journal

    observation = read_remote_run_journal(config, run_id=link.child_run_id)
    if not isinstance(observation, Mapping):
        raise ProviderSessionBindingError("SSH provider control observation is malformed")
    observed_delegation = str(observation.get("delegation_id") or "").strip()
    if observed_delegation and observed_delegation != link.delegation_id:
        raise ProviderSessionBindingError("SSH provider control delegation does not match remote RUN evidence")
    session = str(observation.get("session") or "").strip()
    if not session:
        raise ProviderSessionBindingError("SSH provider control could not resolve a remote worker session")
    binding = ProviderSessionBinding.bind(
        link,
        session_id=session,
        profile=str(getattr(config, "profile", "") or ""),
        destination=str(getattr(config, "destination", "") or ""),
        provider_kind=str(link.provider_identity.get("provider_kind") or "ssh"),
        provider_evidence_digest=str(link.provider_identity.get("provider_evidence_digest") or ""),
        attestation_digest=link.attestation_digest,
    )
    observed_binding = str(observation.get("provider_session_binding_id") or "").strip()
    if observed_binding and observed_binding != binding.binding_id:
        raise ProviderSessionBindingError("SSH provider control binding does not match remote RUN evidence")
    request = DelegatedControlRequest.cancel(
        delegation_id=link.delegation_id,
        child_run_id=link.child_run_id,
        reason=reason,
        provider_session_binding_id=binding.binding_id,
        requested_by_run_id=requested_by_run_id,
    )
    channel = DelegatedControlChannel()

    def cancel(candidate: str, why: str) -> tuple[bool, str]:
        payload = cancel_remote_run(config, run_id=candidate, session=session)
        ok = bool(payload.get("ok")) if isinstance(payload, Mapping) else False
        cancelled = payload.get("cancelled") if isinstance(payload, Mapping) else ()
        detail = (
            f"SSH provider cancelled {len(cancelled or ())} remote phase(s) in session {session}"
            if ok else "SSH provider rejected cancellation"
        )
        return ok, detail

    return channel.dispatch(request, cancel=cancel, telemetry_health=telemetry_health)


__all__ = [
    "DIST_CONTROL_SCHEMA_VERSION",
    "DIST_ROUTE_SCHEMA_VERSION",
    "DistributedProviderControlError",
    "DelegationRouteCycleError",
    "ProviderSessionBindingError",
    "DelegatedControlConflictError",
    "DelegationRouteHop",
    "DelegationRoute",
    "ProviderSessionBinding",
    "DelegatedControlRequest",
    "DelegatedControlAck",
    "DelegatedControlChannel",
    "PrivateTransportHook",
    "typed_tool_argv_evidence",
    "ssh_phase_dist_metadata",
    "provider_cleanup_authority_metadata",
    "provider_cleanup_descriptors",
    "SSHProviderEventChannel",
    "run_relation_from_delegation",
    "record_delegation_relation",
    "ssh_provider_event_channel",
    "dispatch_ssh_provider_control",
]
