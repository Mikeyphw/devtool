from __future__ import annotations

"""DIST-X01 canonical delegation and external-event ingest contracts.

This module deliberately owns identity, authorization, idempotence and terminal
reconciliation only.  Async delivery, cursor/ack/gap recovery and provider
adapters belong to later DIST overlays.
"""

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .core.events import DevtoolEvent, EventBus, event_from_dict
from .core.events.listeners import event_to_dict
from .core.execution_contract import EventId, ExecutionStatus, canonical_execution_status

DIST_EVENT_TRANSPORT_SCHEMA_VERSION = 1


class DistributedExecutionError(RuntimeError):
    pass


class DelegationAuthorizationError(DistributedExecutionError):
    pass


class ExternalEventEquivocationError(DistributedExecutionError):
    pass


class TerminalReconciliationError(DistributedExecutionError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def canonical_event_payload(event_or_payload: DevtoolEvent | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(event_or_payload, DevtoolEvent):
        payload = event_to_dict(event_or_payload)
    elif isinstance(event_or_payload, Mapping):
        payload = {str(key): value for key, value in event_or_payload.items()}
    else:
        raise TypeError("event payload must be a DevtoolEvent or mapping")
    return payload


def canonical_event_payload_digest(event_or_payload: DevtoolEvent | Mapping[str, Any]) -> str:
    return canonical_digest(canonical_event_payload(event_or_payload))


def _normalized_provider_identity(value: Mapping[str, Any] | None) -> dict[str, Any]:
    row = dict(value or {})
    return {
        key: row.get(key)
        for key in (
            "provider_id",
            "provider_kind",
            "profile",
            "execution_environment",
            "provider_evidence_digest",
        )
        if row.get(key) not in (None, "")
    }


@dataclass(frozen=True, slots=True)
class ExecutionRunLink:
    parent_run_id: str
    parent_node_id: str
    child_run_id: str
    relationship: str = "delegated-child"
    link_id: str = ""

    def __post_init__(self) -> None:
        if not self.parent_run_id or not self.parent_node_id or not self.child_run_id:
            raise ValueError("run link requires parent_run_id, parent_node_id and child_run_id")
        if self.parent_run_id == self.child_run_id:
            raise ValueError("delegated child run must be distinct from parent run")
        if not self.link_id:
            object.__setattr__(
                self,
                "link_id",
                canonical_digest(
                    {
                        "parent_run_id": self.parent_run_id,
                        "parent_node_id": self.parent_node_id,
                        "child_run_id": self.child_run_id,
                        "relationship": self.relationship,
                    }
                ),
            )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExecutionRunLink":
        return cls(
            parent_run_id=str(payload.get("parent_run_id") or ""),
            parent_node_id=str(payload.get("parent_node_id") or ""),
            child_run_id=str(payload.get("child_run_id") or ""),
            relationship=str(payload.get("relationship") or "delegated-child"),
            link_id=str(payload.get("link_id") or ""),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "execution-run-link",
            "link_id": self.link_id,
            "relationship": self.relationship,
            "parent_run_id": self.parent_run_id,
            "parent_node_id": self.parent_node_id,
            "child_run_id": self.child_run_id,
        }


@dataclass(frozen=True, slots=True)
class DelegationLink:
    run_link: ExecutionRunLink
    repository: str = ""
    target: str = ""
    child_contract: str = ""
    contract_ref: str = ""
    federation_ref: str = ""
    repository_fingerprint: str = ""
    source_lock_digest: str = ""
    source_equivalence_digest: str = ""
    git_lineage_digest: str = ""
    provider_identity: dict[str, Any] = field(default_factory=dict)
    attestation_digest: str = ""
    delegation_id: str = ""

    def __post_init__(self) -> None:
        provider = _normalized_provider_identity(self.provider_identity)
        object.__setattr__(self, "provider_identity", provider)
        if not self.delegation_id:
            object.__setattr__(
                self,
                "delegation_id",
                canonical_digest(
                    {
                        "run_link": self.run_link.as_dict(),
                        "repository": self.repository,
                        "target": self.target,
                        "child_contract": self.child_contract,
                        "contract_ref": self.contract_ref,
                        "federation_ref": self.federation_ref,
                        "repository_fingerprint": self.repository_fingerprint,
                        "source_lock_digest": self.source_lock_digest,
                        "source_equivalence_digest": self.source_equivalence_digest,
                        "git_lineage_digest": self.git_lineage_digest,
                        "provider_identity": provider,
                        "attestation_digest": self.attestation_digest,
                    }
                ),
            )

    @property
    def child_run_id(self) -> str:
        return self.run_link.child_run_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "delegation-link",
            "delegation_id": self.delegation_id,
            "run_link": self.run_link.as_dict(),
            "repository": self.repository,
            "target": self.target,
            "child_contract": self.child_contract,
            "contract_ref": self.contract_ref,
            "federation_ref": self.federation_ref,
            "repository_fingerprint": self.repository_fingerprint,
            "source_lock_digest": self.source_lock_digest,
            "source_equivalence_digest": self.source_equivalence_digest,
            "git_lineage_digest": self.git_lineage_digest,
            "provider_identity": dict(self.provider_identity),
            "attestation_digest": self.attestation_digest,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DelegationLink":
        raw_link = payload.get("run_link")
        if not isinstance(raw_link, Mapping):
            raise ValueError("delegation link payload requires run_link")
        return cls(
            run_link=ExecutionRunLink.from_dict(raw_link),
            repository=str(payload.get("repository") or ""),
            target=str(payload.get("target") or ""),
            child_contract=str(payload.get("child_contract") or ""),
            contract_ref=str(payload.get("contract_ref") or ""),
            federation_ref=str(payload.get("federation_ref") or ""),
            repository_fingerprint=str(payload.get("repository_fingerprint") or ""),
            source_lock_digest=str(payload.get("source_lock_digest") or ""),
            source_equivalence_digest=str(payload.get("source_equivalence_digest") or ""),
            git_lineage_digest=str(payload.get("git_lineage_digest") or ""),
            provider_identity=dict(payload.get("provider_identity") or {}),
            attestation_digest=str(payload.get("attestation_digest") or ""),
            delegation_id=str(payload.get("delegation_id") or ""),
        )

    @classmethod
    def from_federation(
        cls,
        *,
        parent_run_id: str,
        parent_node_id: str,
        child_run_id: str,
        lineage: Mapping[str, Any],
        repository_identity: Mapping[str, Any] | None,
        provider_identity: Mapping[str, Any] | None,
        attestation: Mapping[str, Any] | None,
    ) -> "DelegationLink":
        git_lineage = lineage.get("git_lineage") if isinstance(lineage.get("git_lineage"), Mapping) else {}
        identity = dict(repository_identity or {})
        attestation_payload = dict(attestation or {})
        return cls(
            run_link=ExecutionRunLink(parent_run_id, parent_node_id, child_run_id),
            repository=str(lineage.get("repository") or ""),
            target=str(lineage.get("target") or ""),
            child_contract=str(lineage.get("child_contract") or ""),
            contract_ref=str(lineage.get("contract_ref") or ""),
            federation_ref=str(lineage.get("federation_ref") or ""),
            repository_fingerprint=str(identity.get("fingerprint") or identity.get("repository_id") or ""),
            source_lock_digest=str(lineage.get("frozen_source_lock_digest") or ""),
            source_equivalence_digest=str(lineage.get("frozen_source_equivalence_digest") or ""),
            git_lineage_digest=canonical_digest(dict(git_lineage)) if git_lineage else "",
            provider_identity=_normalized_provider_identity(provider_identity),
            attestation_digest=canonical_digest(attestation_payload) if attestation_payload else "",
        )


@dataclass(frozen=True, slots=True)
class EventTransportEnvelope:
    delegation_id: str
    child_run_id: str
    producer_id: str
    event_id: str
    sequence: int
    payload: dict[str, Any]
    payload_digest: str
    delivery_mode: str = "live"
    provider_identity: dict[str, Any] = field(default_factory=dict)
    attestation_digest: str = ""
    schema_version: int = DIST_EVENT_TRANSPORT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        mode = str(self.delivery_mode or "live").strip().lower()
        if mode not in {"live", "recovery", "catch-up", "historical-reuse"}:
            raise ValueError(f"unsupported external ingest mode {self.delivery_mode!r}")
        object.__setattr__(self, "delivery_mode", mode)
        object.__setattr__(self, "provider_identity", _normalized_provider_identity(self.provider_identity))
        if int(self.sequence) < 0:
            raise ValueError("transport envelope sequence must be non-negative")
        expected_event_id = str(EventId.for_sequence(self.child_run_id, int(self.sequence)))
        if self.event_id != expected_event_id:
            raise ValueError("transport event identity does not match child run/sequence")
        actual = canonical_event_payload_digest(self.payload)
        if self.payload_digest != actual:
            raise ValueError("transport payload digest does not match canonical event payload")

    @classmethod
    def for_event(
        cls,
        event_or_payload: DevtoolEvent | Mapping[str, Any],
        *,
        link: DelegationLink,
        producer_id: str = "",
        delivery_mode: str = "live",
    ) -> "EventTransportEnvelope":
        payload = canonical_event_payload(event_or_payload)
        run_id = str(payload.get("run_id") or "")
        event_id = str(payload.get("event_id") or "")
        sequence = int(payload.get("sequence", -1))
        if run_id != link.child_run_id:
            raise DelegationAuthorizationError(
                f"event run {run_id!r} does not match delegated child {link.child_run_id!r}"
            )
        return cls(
            delegation_id=link.delegation_id,
            child_run_id=link.child_run_id,
            producer_id=str(producer_id or link.delegation_id),
            event_id=event_id,
            sequence=sequence,
            payload=payload,
            payload_digest=canonical_event_payload_digest(payload),
            delivery_mode=delivery_mode,
            provider_identity=dict(link.provider_identity),
            attestation_digest=link.attestation_digest,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EventTransportEnvelope":
        return cls(
            delegation_id=str(payload.get("delegation_id") or ""),
            child_run_id=str(payload.get("child_run_id") or ""),
            producer_id=str(payload.get("producer_id") or ""),
            event_id=str(payload.get("event_id") or ""),
            sequence=int(payload.get("sequence", -1)),
            payload=dict(payload.get("payload") or {}),
            payload_digest=str(payload.get("payload_digest") or ""),
            delivery_mode=str(payload.get("delivery_mode") or "live"),
            provider_identity=dict(payload.get("provider_identity") or {}),
            attestation_digest=str(payload.get("attestation_digest") or ""),
            schema_version=int(payload.get("schema_version") or DIST_EVENT_TRANSPORT_SCHEMA_VERSION),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": "event-transport-envelope",
            "delegation_id": self.delegation_id,
            "child_run_id": self.child_run_id,
            "producer_id": self.producer_id,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "payload": dict(self.payload),
            "payload_digest": self.payload_digest,
            "delivery_mode": self.delivery_mode,
            "provider_identity": dict(self.provider_identity),
            "attestation_digest": self.attestation_digest,
        }


@dataclass(frozen=True, slots=True)
class ExternalIngestResult:
    accepted: bool
    duplicate: bool
    live_notified: bool
    run_id: str
    event_id: str
    sequence: int
    payload_digest: str


class DelegationRegistry:
    def __init__(self) -> None:
        self._links: dict[str, DelegationLink] = {}
        self._delegation_by_run: dict[str, str] = {}
        self._producer_by_run: dict[str, str] = {}

    def register(self, link: DelegationLink) -> DelegationLink:
        existing = self._links.get(link.delegation_id)
        if existing is not None and existing != link:
            raise DelegationAuthorizationError(f"delegation id collision: {link.delegation_id}")
        prior_id = self._delegation_by_run.get(link.child_run_id)
        if prior_id is not None and prior_id != link.delegation_id:
            raise DelegationAuthorizationError(
                f"run {link.child_run_id!r} is already owned by delegation {prior_id!r}"
            )
        self._links[link.delegation_id] = link
        self._delegation_by_run[link.child_run_id] = link.delegation_id
        return link

    def authorize(self, envelope: EventTransportEnvelope) -> DelegationLink:
        link = self._links.get(envelope.delegation_id)
        if link is None:
            raise DelegationAuthorizationError("external event references an unplanned delegation")
        if envelope.child_run_id != link.child_run_id:
            raise DelegationAuthorizationError("external event child run does not match delegation")
        if _normalized_provider_identity(envelope.provider_identity) != link.provider_identity:
            raise DelegationAuthorizationError("external event provider identity does not match planned placement")
        if str(envelope.attestation_digest or "") != str(link.attestation_digest or ""):
            raise DelegationAuthorizationError("external event attestation does not match planned child")
        producer = self._producer_by_run.get(link.child_run_id)
        if producer is None:
            self._producer_by_run[link.child_run_id] = envelope.producer_id
        elif producer != envelope.producer_id:
            raise DelegationAuthorizationError(
                f"run {link.child_run_id!r} already has authoritative producer {producer!r}"
            )
        return link


class ExternalEventIngestor:
    """Authorize and ingest origin events without rewriting semantic identity."""

    def __init__(self, bus: EventBus, *, registry: DelegationRegistry | None = None) -> None:
        self.bus = bus
        self.registry = registry or DelegationRegistry()
        self._accepted_digests: dict[tuple[str, str], str] = {}

    def register(self, link: DelegationLink) -> DelegationLink:
        return self.registry.register(link)

    def ingest(self, envelope: EventTransportEnvelope) -> ExternalIngestResult:
        self.registry.authorize(envelope)
        payload = canonical_event_payload(envelope.payload)
        if str(payload.get("run_id") or "") != envelope.child_run_id:
            raise DelegationAuthorizationError("external payload run_id does not match envelope")
        if str(payload.get("event_id") or "") != envelope.event_id:
            raise DelegationAuthorizationError("external payload event_id does not match envelope")
        if int(payload.get("sequence", -1)) != int(envelope.sequence):
            raise DelegationAuthorizationError("external payload sequence does not match envelope")
        key = (envelope.child_run_id, envelope.event_id)
        previous = self._accepted_digests.get(key)
        if previous is not None:
            if previous == envelope.payload_digest:
                return ExternalIngestResult(
                    accepted=False,
                    duplicate=True,
                    live_notified=False,
                    run_id=envelope.child_run_id,
                    event_id=envelope.event_id,
                    sequence=envelope.sequence,
                    payload_digest=envelope.payload_digest,
                )
            raise ExternalEventEquivocationError(
                f"conflicting payload for external event {envelope.event_id!r}"
            )
        event = event_from_dict(payload, allow_unknown=True)
        try:
            applied = self.bus.ingest_preserved(
                event,
                notify=envelope.delivery_mode in {"live", "catch-up"},
                payload_digest=envelope.payload_digest,
            )
        except ValueError as exc:
            if "equivocation" in str(exc):
                raise ExternalEventEquivocationError(str(exc)) from exc
            raise
        self._accepted_digests[key] = envelope.payload_digest
        return ExternalIngestResult(
            accepted=bool(applied),
            duplicate=not bool(applied),
            live_notified=bool(applied and envelope.delivery_mode == "live"),
            run_id=envelope.child_run_id,
            event_id=envelope.event_id,
            sequence=envelope.sequence,
            payload_digest=envelope.payload_digest,
        )


def delegation_links_from_snapshot(snapshot: Any) -> tuple[DelegationLink, ...]:
    """Project generic DelegationLink values from canonical RUN state."""

    found: dict[str, DelegationLink] = {}
    lineage = getattr(snapshot, "run_lineage", {}) if snapshot is not None else {}
    if isinstance(lineage, Mapping):
        raw = lineage.get("delegation_link")
        if isinstance(raw, Mapping):
            link = DelegationLink.from_dict(raw)
            found[link.delegation_id] = link
    for node in tuple(getattr(snapshot, "nodes", ()) or ()):
        metadata = getattr(node, "metadata", {})
        if not isinstance(metadata, Mapping):
            continue
        raw = metadata.get("delegation_link")
        if isinstance(raw, Mapping):
            link = DelegationLink.from_dict(raw)
            found[link.delegation_id] = link
    return tuple(found[key] for key in sorted(found))


@dataclass(frozen=True, slots=True)
class TerminalReconciliation:
    child_status: str
    child_exit_code: int
    transport_exit_code: int
    authoritative_status: str
    authoritative_exit_code: int
    reconciled: bool
    integrity_error: str = ""

    @property
    def ok(self) -> bool:
        return self.reconciled and not self.integrity_error

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "delegated-terminal-reconciliation",
            "child_status": self.child_status,
            "child_exit_code": self.child_exit_code,
            "transport_exit_code": self.transport_exit_code,
            "authoritative_status": self.authoritative_status,
            "authoritative_exit_code": self.authoritative_exit_code,
            "reconciled": self.reconciled,
            "integrity_error": self.integrity_error,
        }


def reconcile_terminal_truth(*, transport_exit_code: int, child_snapshot: Any | None) -> TerminalReconciliation:
    transport_rc = int(transport_exit_code)
    if child_snapshot is None:
        status = (
            ExecutionStatus.SUCCEEDED
            if transport_rc == 0
            else ExecutionStatus.CANCELLED
            if transport_rc == 130
            else ExecutionStatus.FAILED
        )
        return TerminalReconciliation(
            child_status="",
            child_exit_code=transport_rc,
            transport_exit_code=transport_rc,
            authoritative_status=status.value,
            authoritative_exit_code=transport_rc,
            reconciled=True,
        )

    status = canonical_execution_status(getattr(child_snapshot, "status", ""), default=ExecutionStatus.FAILED)
    if not status.terminal:
        raise TerminalReconciliationError("delegated child snapshot is not terminal")
    snapshot_exit = getattr(child_snapshot, "exit_code", None)
    if snapshot_exit is None:
        snapshot_exit = (
            0 if status is ExecutionStatus.SUCCEEDED
            else 130 if status is ExecutionStatus.CANCELLED
            else transport_rc if transport_rc != 0
            else 1
        )
    snapshot_exit = int(snapshot_exit)
    coherent = (
        (status is ExecutionStatus.SUCCEEDED and snapshot_exit == 0)
        or (status is ExecutionStatus.CANCELLED and snapshot_exit == 130)
        or (status in {ExecutionStatus.FAILED, ExecutionStatus.BLOCKED} and snapshot_exit != 0)
        or status is ExecutionStatus.SKIPPED
    )
    integrity_error = ""
    if not coherent:
        integrity_error = "child snapshot status/exit contradiction"
    elif transport_rc != snapshot_exit:
        integrity_error = "transport rc/child snapshot contradiction"
    return TerminalReconciliation(
        child_status=status.value,
        child_exit_code=snapshot_exit,
        transport_exit_code=transport_rc,
        authoritative_status=(status.value if not integrity_error else ExecutionStatus.FAILED.value),
        authoritative_exit_code=(snapshot_exit if not integrity_error else (transport_rc or snapshot_exit or 1)),
        reconciled=not integrity_error,
        integrity_error=integrity_error,
    )


def import_child_run_journal(
    directory: str | Path,
    *,
    child_run_id: str,
    link: DelegationLink,
    bus: EventBus,
    ingestor: ExternalEventIngestor | None = None,
) -> Any | None:
    """Recovery-import one child journal into the canonical local ExecutionStore.

    Process output is intentionally not imported through the semantic channel;
    raw stream/attachment references are a DIST-X02 concern. All other origin
    event identities are retained byte/semantic-identically from the durable
    child journal and listeners are not notified during recovery import.
    """

    path = Path(directory) / "execution-events.jsonl"
    if not path.is_file():
        return bus.execution_store.snapshot(child_run_id)
    consumer = ingestor or ExternalEventIngestor(bus)
    consumer.register(link)
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        payload = json.loads(raw)
        if str(payload.get("run_id") or "") != child_run_id:
            raise DelegationAuthorizationError("child journal contains a foreign run_id")
        if str(payload.get("event_type") or "") == "process.output":
            continue
        envelope = EventTransportEnvelope.for_event(
            payload,
            link=link,
            producer_id=link.delegation_id,
            delivery_mode="recovery",
        )
        consumer.ingest(envelope)
    return bus.execution_store.snapshot(child_run_id)


__all__ = [
    "DIST_EVENT_TRANSPORT_SCHEMA_VERSION",
    "DistributedExecutionError",
    "DelegationAuthorizationError",
    "ExternalEventEquivocationError",
    "TerminalReconciliationError",
    "canonical_digest",
    "canonical_event_payload",
    "canonical_event_payload_digest",
    "ExecutionRunLink",
    "DelegationLink",
    "EventTransportEnvelope",
    "ExternalIngestResult",
    "DelegationRegistry",
    "ExternalEventIngestor",
    "delegation_links_from_snapshot",
    "TerminalReconciliation",
    "reconcile_terminal_truth",
    "import_child_run_journal",
]
