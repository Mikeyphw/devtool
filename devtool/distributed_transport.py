from __future__ import annotations

"""DIST-X02 bounded delegated-event transport and cursor recovery.

The origin RUN journal remains the durable spool and execution authority.  Live
publication only contributes bounded sequence hints; network/provider I/O and
cursor persistence happen outside EventBus publication locks.  The same
provider-neutral frame/cursor contract is reusable by local subprocess,
federation, chroot, PRoot and OCI adapters.
"""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import queue
import tempfile
import threading
import time
from typing import Any, Callable, Iterable, Mapping

from .core.events import DevtoolEvent, EventBus
from .core.execution_limits import bound_event_reference_fields
from .core.execution_subscribers import compact_event_document
from .distributed_execution import (
    DelegationLink,
    EventTransportEnvelope,
    ExternalEventEquivocationError,
    ExternalEventIngestor,
    TerminalReconciliationError,
    canonical_digest,
    reconcile_terminal_truth,
)
from .logstore import redact_sensitive_value

DIST_TRANSPORT_PROTOCOL_VERSION = 1
DIST_TRANSPORT_FRAME_SCHEMA_VERSION = 1

FEATURE_EVENT_ENVELOPE = "event-envelope-v1"
FEATURE_CURSOR_ACK = "cursor-ack-v1"
FEATURE_GAP_CATCHUP = "gap-catchup-v1"
FEATURE_RAW_STREAM_REF = "raw-stream-ref-v1"
FEATURE_SAFE_EVENT = "safe-event-redaction-v1"
FEATURE_UNKNOWN_SAFE_EVENT = "unknown-safe-event-v1"
DEFAULT_TRANSPORT_FEATURES = frozenset(
    {
        FEATURE_EVENT_ENVELOPE,
        FEATURE_CURSOR_ACK,
        FEATURE_GAP_CATCHUP,
        FEATURE_RAW_STREAM_REF,
        FEATURE_SAFE_EVENT,
        FEATURE_UNKNOWN_SAFE_EVENT,
    }
)

CONTENT_SEMANTIC_SAFE = "semantic-safe-v1"
CONTENT_RAW_STREAM_REFERENCE = "raw-stream-reference-v1"


class DistributedTransportError(RuntimeError):
    pass


class DeliveryGapError(DistributedTransportError):
    def __init__(self, *, expected_sequence: int, observed_sequence: int) -> None:
        self.expected_sequence = int(expected_sequence)
        self.observed_sequence = int(observed_sequence)
        super().__init__(
            f"delegated event gap: expected origin sequence {self.expected_sequence}, "
            f"observed {self.observed_sequence}"
        )


class ProtocolNegotiationError(DistributedTransportError):
    pass


@dataclass(frozen=True, slots=True)
class TransportProtocolCapabilities:
    protocol_versions: tuple[int, ...] = (DIST_TRANSPORT_PROTOCOL_VERSION,)
    envelope_schema_versions: tuple[int, ...] = (1,)
    frame_schema_versions: tuple[int, ...] = (DIST_TRANSPORT_FRAME_SCHEMA_VERSION,)
    features: frozenset[str] = DEFAULT_TRANSPORT_FEATURES

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TransportProtocolCapabilities":
        return cls(
            protocol_versions=tuple(int(value) for value in payload.get("protocol_versions", (1,))),
            envelope_schema_versions=tuple(int(value) for value in payload.get("envelope_schema_versions", (1,))),
            frame_schema_versions=tuple(int(value) for value in payload.get("frame_schema_versions", (1,))),
            features=frozenset(str(value) for value in payload.get("features", ())),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol_versions": list(self.protocol_versions),
            "envelope_schema_versions": list(self.envelope_schema_versions),
            "frame_schema_versions": list(self.frame_schema_versions),
            "features": sorted(self.features),
        }


@dataclass(frozen=True, slots=True)
class NegotiatedTransportProtocol:
    protocol_version: int
    envelope_schema_version: int
    frame_schema_version: int
    features: tuple[str, ...]
    degraded_features: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "envelope_schema_version": self.envelope_schema_version,
            "frame_schema_version": self.frame_schema_version,
            "features": list(self.features),
            "degraded_features": list(self.degraded_features),
        }


def negotiate_transport_protocol(
    local: TransportProtocolCapabilities,
    remote: TransportProtocolCapabilities,
    *,
    required_features: Iterable[str] = (),
) -> NegotiatedTransportProtocol:
    protocols = sorted(set(local.protocol_versions) & set(remote.protocol_versions), reverse=True)
    envelopes = sorted(
        set(local.envelope_schema_versions) & set(remote.envelope_schema_versions), reverse=True
    )
    frames = sorted(set(local.frame_schema_versions) & set(remote.frame_schema_versions), reverse=True)
    if not protocols or not envelopes or not frames:
        raise ProtocolNegotiationError("delegated transport has no common protocol/schema version")
    common_features = set(local.features) & set(remote.features)
    required = {str(value) for value in required_features if str(value)}
    missing = sorted(required - common_features)
    if missing:
        raise ProtocolNegotiationError(
            "delegated transport missing required feature(s): " + ", ".join(missing)
        )
    degraded = sorted((set(local.features) | set(remote.features)) - common_features)
    return NegotiatedTransportProtocol(
        protocol_version=protocols[0],
        envelope_schema_version=envelopes[0],
        frame_schema_version=frames[0],
        features=tuple(sorted(common_features)),
        degraded_features=tuple(degraded),
    )


def safe_semantic_payload(event_or_payload: DevtoolEvent | Mapping[str, Any]) -> dict[str, Any]:
    """Return the secret-safe bounded semantic representation used on transport.

    Raw process output is never placed on the ordinary semantic channel.  For
    DevtoolEvent values we reuse the durable RUN journal representation so
    transport and recovery apply the same redaction/bounding contract.
    """

    if isinstance(event_or_payload, DevtoolEvent):
        payload = compact_event_document(event_or_payload)
    elif isinstance(event_or_payload, Mapping):
        payload = bound_event_reference_fields(
            redact_sensitive_value({str(key): value for key, value in event_or_payload.items()})
        )
    else:
        raise TypeError("semantic transport payload must be a DevtoolEvent or mapping")
    if str(payload.get("event_type") or "") == "process.output":
        raise ValueError("raw process output must use a protected stream reference")
    return payload


@dataclass(frozen=True, slots=True)
class RawStreamReference:
    child_run_id: str
    event_id: str
    sequence: int
    process_id: str
    stream: str
    reference_id: str = ""

    def __post_init__(self) -> None:
        if not self.child_run_id or not self.event_id or int(self.sequence) < 0:
            raise ValueError("raw stream reference requires run/event/sequence identity")
        if not self.reference_id:
            object.__setattr__(
                self,
                "reference_id",
                canonical_digest(
                    {
                        "child_run_id": self.child_run_id,
                        "event_id": self.event_id,
                        "sequence": int(self.sequence),
                        "process_id": self.process_id,
                        "stream": self.stream,
                    }
                ),
            )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "RawStreamReference":
        if str(payload.get("event_type") or "") != "process.output":
            raise ValueError("raw stream reference requires process.output payload")
        return cls(
            child_run_id=str(payload.get("run_id") or ""),
            event_id=str(payload.get("event_id") or ""),
            sequence=int(payload.get("sequence", -1)),
            process_id=str(payload.get("process_id") or ""),
            stream=str(payload.get("stream") or "stdout"),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RawStreamReference":
        return cls(
            child_run_id=str(payload.get("child_run_id") or ""),
            event_id=str(payload.get("event_id") or ""),
            sequence=int(payload.get("sequence", -1)),
            process_id=str(payload.get("process_id") or ""),
            stream=str(payload.get("stream") or "stdout"),
            reference_id=str(payload.get("reference_id") or ""),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "raw-stream-reference",
            "content_class": CONTENT_RAW_STREAM_REFERENCE,
            "reference_id": self.reference_id,
            "child_run_id": self.child_run_id,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "process_id": self.process_id,
            "stream": self.stream,
            # Consumers use RUN retention/history to query bytes. No raw text,
            # credentials, local filesystem path or attachment bytes travel here.
            "run_ref": f"run://{self.child_run_id}/process/{self.process_id}/{self.stream}",
        }


@dataclass(frozen=True, slots=True)
class TransportFrame:
    delegation_id: str
    child_run_id: str
    event_id: str
    sequence: int
    content_class: str
    semantic_envelope: EventTransportEnvelope | None = None
    raw_stream_reference: RawStreamReference | None = None
    schema_version: int = DIST_TRANSPORT_FRAME_SCHEMA_VERSION
    frame_digest: str = ""

    def __post_init__(self) -> None:
        semantic = self.semantic_envelope is not None
        raw = self.raw_stream_reference is not None
        if semantic == raw:
            raise ValueError("transport frame must carry exactly one content payload")
        if semantic:
            envelope = self.semantic_envelope
            assert envelope is not None
            if self.content_class != CONTENT_SEMANTIC_SAFE:
                raise ValueError("semantic frame content class mismatch")
            if (
                envelope.delegation_id != self.delegation_id
                or envelope.child_run_id != self.child_run_id
                or envelope.event_id != self.event_id
                or int(envelope.sequence) != int(self.sequence)
            ):
                raise ValueError("semantic frame identity does not match envelope")
            body: Mapping[str, Any] = envelope.as_dict()
        else:
            reference = self.raw_stream_reference
            assert reference is not None
            if self.content_class != CONTENT_RAW_STREAM_REFERENCE:
                raise ValueError("raw-stream frame content class mismatch")
            if (
                reference.child_run_id != self.child_run_id
                or reference.event_id != self.event_id
                or int(reference.sequence) != int(self.sequence)
            ):
                raise ValueError("raw-stream frame identity does not match reference")
            body = reference.as_dict()
        digest = canonical_digest(
            {
                "schema_version": int(self.schema_version),
                "delegation_id": self.delegation_id,
                "child_run_id": self.child_run_id,
                "event_id": self.event_id,
                "sequence": int(self.sequence),
                "content_class": self.content_class,
                "body": body,
            }
        )
        if self.frame_digest and self.frame_digest != digest:
            raise ValueError("transport frame digest mismatch")
        if not self.frame_digest:
            object.__setattr__(self, "frame_digest", digest)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TransportFrame":
        semantic_raw = payload.get("semantic_envelope")
        raw_ref = payload.get("raw_stream_reference")
        return cls(
            delegation_id=str(payload.get("delegation_id") or ""),
            child_run_id=str(payload.get("child_run_id") or ""),
            event_id=str(payload.get("event_id") or ""),
            sequence=int(payload.get("sequence", -1)),
            content_class=str(payload.get("content_class") or ""),
            semantic_envelope=(
                EventTransportEnvelope.from_dict(semantic_raw)
                if isinstance(semantic_raw, Mapping) else None
            ),
            raw_stream_reference=(
                RawStreamReference.from_dict(raw_ref)
                if isinstance(raw_ref, Mapping) else None
            ),
            schema_version=int(payload.get("schema_version") or DIST_TRANSPORT_FRAME_SCHEMA_VERSION),
            frame_digest=str(payload.get("frame_digest") or ""),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": "delegated-event-frame",
            "delegation_id": self.delegation_id,
            "child_run_id": self.child_run_id,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "content_class": self.content_class,
            "semantic_envelope": self.semantic_envelope.as_dict() if self.semantic_envelope else None,
            "raw_stream_reference": self.raw_stream_reference.as_dict() if self.raw_stream_reference else None,
            "frame_digest": self.frame_digest,
        }


@dataclass(frozen=True, slots=True)
class DeliveryAck:
    delegation_id: str
    child_run_id: str
    acknowledged_sequence: int
    event_id: str
    duplicate: bool = False

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DeliveryAck":
        return cls(
            delegation_id=str(payload.get("delegation_id") or ""),
            child_run_id=str(payload.get("child_run_id") or ""),
            acknowledged_sequence=int(payload.get("acknowledged_sequence") or 0),
            event_id=str(payload.get("event_id") or ""),
            duplicate=bool(payload.get("duplicate", False)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "delegated-event-ack",
            "delegation_id": self.delegation_id,
            "child_run_id": self.child_run_id,
            "acknowledged_sequence": self.acknowledged_sequence,
            "event_id": self.event_id,
            "duplicate": self.duplicate,
        }




def _frame_content_digest(frame: "TransportFrame") -> str:
    if frame.semantic_envelope is not None:
        return str(frame.semantic_envelope.payload_digest)
    reference = frame.raw_stream_reference
    assert reference is not None
    return str(reference.reference_id)


class DelegatedEventReceiver:
    """Gap-detecting receiver over X01 ExternalEventIngestor."""

    def __init__(
        self,
        ingestor: ExternalEventIngestor,
        *,
        link: DelegationLink,
        acknowledged_sequence: int = 0,
        raw_reference_sink: Callable[[RawStreamReference], Any] | None = None,
    ) -> None:
        self.ingestor = ingestor
        self.link = ingestor.register(link)
        self.acknowledged_sequence = int(acknowledged_sequence)
        self.raw_reference_sink = raw_reference_sink
        self._digest_by_sequence: dict[int, str] = {}
        self.raw_references: list[RawStreamReference] = []
        self.replay_count = 0

    def receive(self, frame: TransportFrame) -> DeliveryAck:
        if frame.delegation_id != self.link.delegation_id or frame.child_run_id != self.link.child_run_id:
            raise DistributedTransportError("transport frame is outside the planned delegation")
        sequence = int(frame.sequence)
        content_digest = _frame_content_digest(frame)
        previous_digest = self._digest_by_sequence.get(sequence)
        if sequence <= self.acknowledged_sequence:
            if previous_digest is not None and previous_digest != content_digest:
                raise ExternalEventEquivocationError(
                    f"conflicting transport frame at origin sequence {sequence}"
                )
            self.replay_count += 1
            return DeliveryAck(
                self.link.delegation_id,
                self.link.child_run_id,
                self.acknowledged_sequence,
                frame.event_id,
                duplicate=True,
            )
        expected = self.acknowledged_sequence + 1
        if sequence != expected:
            raise DeliveryGapError(expected_sequence=expected, observed_sequence=sequence)
        if previous_digest is not None and previous_digest != content_digest:
            raise ExternalEventEquivocationError(
                f"conflicting transport frame at origin sequence {sequence}"
            )
        if frame.semantic_envelope is not None:
            self.ingestor.ingest(frame.semantic_envelope)
        else:
            reference = frame.raw_stream_reference
            assert reference is not None
            self.raw_references.append(reference)
            if self.raw_reference_sink is not None:
                self.raw_reference_sink(reference)
        self._digest_by_sequence[sequence] = content_digest
        self.acknowledged_sequence = sequence
        return DeliveryAck(
            self.link.delegation_id,
            self.link.child_run_id,
            sequence,
            frame.event_id,
            duplicate=False,
        )


@dataclass(frozen=True, slots=True)
class DeliveryCursor:
    delegation_id: str
    child_run_id: str
    acknowledged_sequence: int = 0
    replay_count: int = 0
    unresolved_gaps: tuple[tuple[int, int], ...] = ()
    health: str = "idle"
    last_error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "delegated-event-delivery-cursor",
            "delegation_id": self.delegation_id,
            "child_run_id": self.child_run_id,
            "acknowledged_sequence": self.acknowledged_sequence,
            "replay_count": self.replay_count,
            "unresolved_gaps": [list(value) for value in self.unresolved_gaps],
            "health": self.health,
            "last_error": self.last_error,
        }


class DeliveryCursorStore:
    """Private transport cursor; never a second execution-history store."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self, *, link: DelegationLink) -> DeliveryCursor:
        if not self.path.is_file():
            return DeliveryCursor(link.delegation_id, link.child_run_id)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return DeliveryCursor(
                link.delegation_id,
                link.child_run_id,
                health="degraded",
                last_error="delivery cursor unreadable",
            )
        if str(payload.get("delegation_id") or "") != link.delegation_id:
            raise DistributedTransportError("delivery cursor delegation identity mismatch")
        if str(payload.get("child_run_id") or "") != link.child_run_id:
            raise DistributedTransportError("delivery cursor child run identity mismatch")
        gaps = tuple(
            (int(value[0]), int(value[1]))
            for value in payload.get("unresolved_gaps", ())
            if isinstance(value, (list, tuple)) and len(value) == 2
        )
        return DeliveryCursor(
            link.delegation_id,
            link.child_run_id,
            acknowledged_sequence=int(payload.get("acknowledged_sequence") or 0),
            replay_count=int(payload.get("replay_count") or 0),
            unresolved_gaps=gaps,
            health=str(payload.get("health") or "idle"),
            last_error=str(payload.get("last_error") or ""),
        )

    def save(self, cursor: DeliveryCursor) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(cursor.as_dict(), handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
            os.replace(raw, self.path)
        finally:
            try:
                os.unlink(raw)
            except FileNotFoundError:
                pass


@dataclass(frozen=True, slots=True)
class EventDeliveryState:
    delegation_id: str
    child_run_id: str
    health: str
    acknowledged_sequence: int
    pending_hint_count: int
    replay_count: int
    unresolved_gaps: tuple[tuple[int, int], ...]
    overflow_count: int
    last_error: str = ""

    @property
    def healthy(self) -> bool:
        return self.health in {"idle", "live", "reconciled"} and not self.unresolved_gaps

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "event-delivery-state",
            "delegation_id": self.delegation_id,
            "child_run_id": self.child_run_id,
            "health": self.health,
            "acknowledged_sequence": self.acknowledged_sequence,
            "pending_hint_count": self.pending_hint_count,
            "replay_count": self.replay_count,
            "unresolved_gaps": [list(value) for value in self.unresolved_gaps],
            "overflow_count": self.overflow_count,
            "last_error": self.last_error,
        }


@dataclass(frozen=True, slots=True)
class DelegatedExecutionReceipt:
    delegation_id: str
    child_run_id: str
    delivery: EventDeliveryState
    first_origin_sequence: int
    last_origin_sequence: int
    terminal_sequence: int
    terminal_snapshot_digest: str
    child_status: str
    child_exit_code: int | None
    transport_exit_code: int | None
    reconciled: bool
    integrity_error: str
    provider_identity: dict[str, Any]
    protocol: dict[str, Any]
    raw_stream_references: int = 0
    cleanup_owner: str = "LIFE"

    @property
    def ok(self) -> bool:
        return self.reconciled and not self.integrity_error

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "delegated-execution-receipt",
            "delegation_id": self.delegation_id,
            "child_run_id": self.child_run_id,
            "delivery": self.delivery.as_dict(),
            "first_origin_sequence": self.first_origin_sequence,
            "last_origin_sequence": self.last_origin_sequence,
            "terminal_sequence": self.terminal_sequence,
            "terminal_snapshot_digest": self.terminal_snapshot_digest,
            "child_status": self.child_status,
            "child_exit_code": self.child_exit_code,
            "transport_exit_code": self.transport_exit_code,
            "reconciled": self.reconciled,
            "integrity_error": self.integrity_error,
            "provider_identity": dict(self.provider_identity),
            "protocol": dict(self.protocol),
            "raw_stream_references": self.raw_stream_references,
            "cleanup_owner": self.cleanup_owner,
        }


class OriginRunJournal:
    def __init__(self, directory: str | Path, *, child_run_id: str, link: DelegationLink) -> None:
        self.directory = Path(directory)
        self.child_run_id = child_run_id
        self.link = link

    @property
    def path(self) -> Path:
        return self.directory / "execution-events.jsonl"

    def frames_after(
        self,
        acknowledged_sequence: int,
        *,
        delivery_mode: str,
        limit: int | None = None,
    ) -> tuple[TransportFrame, ...]:
        if not self.path.is_file():
            return ()
        rows: list[TransportFrame] = []
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            payload = json.loads(raw)
            if str(payload.get("run_id") or "") != self.child_run_id:
                raise DistributedTransportError("origin journal contains foreign run_id")
            sequence = int(payload.get("sequence", -1))
            if sequence <= int(acknowledged_sequence):
                continue
            event_id = str(payload.get("event_id") or "")
            if str(payload.get("event_type") or "") == "process.output":
                reference = RawStreamReference.from_payload(payload)
                frame = TransportFrame(
                    delegation_id=self.link.delegation_id,
                    child_run_id=self.child_run_id,
                    event_id=event_id,
                    sequence=sequence,
                    content_class=CONTENT_RAW_STREAM_REFERENCE,
                    raw_stream_reference=reference,
                )
            else:
                safe_payload = safe_semantic_payload(payload)
                envelope = EventTransportEnvelope.for_event(
                    safe_payload,
                    link=self.link,
                    producer_id=self.link.delegation_id,
                    delivery_mode=delivery_mode,
                )
                frame = TransportFrame(
                    delegation_id=self.link.delegation_id,
                    child_run_id=self.child_run_id,
                    event_id=event_id,
                    sequence=sequence,
                    content_class=CONTENT_SEMANTIC_SAFE,
                    semantic_envelope=envelope,
                )
            rows.append(frame)
            if limit is not None and len(rows) >= int(limit):
                break
        return tuple(rows)

    def sequence_bounds(self) -> tuple[int, int]:
        frames = self.frames_after(0, delivery_mode="recovery")
        if not frames:
            return (0, 0)
        return (int(frames[0].sequence), int(frames[-1].sequence))


@dataclass(frozen=True, slots=True)
class DistributedProviderAdapter:
    provider_kind: str
    state_directory: Path
    provider_identity: dict[str, Any] = field(default_factory=dict)
    protocol_capabilities: TransportProtocolCapabilities = field(
        default_factory=TransportProtocolCapabilities
    )
    channel_kind: str = "run-journal"
    cleanup_owner: str = "LIFE"

    def origin_journal(self, link: DelegationLink) -> OriginRunJournal:
        return OriginRunJournal(self.state_directory, child_run_id=link.child_run_id, link=link)

    def cursor_store(self, link: DelegationLink) -> DeliveryCursorStore:
        return DeliveryCursorStore(
            self.state_directory / ".devtool-dist" / f"delivery-{link.delegation_id}.json"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_kind": self.provider_kind,
            "channel_kind": self.channel_kind,
            "provider_identity": dict(self.provider_identity),
            "protocol_capabilities": self.protocol_capabilities.as_dict(),
            "cleanup_owner": self.cleanup_owner,
        }


def distributed_provider_adapter(
    provider_kind: str,
    state_directory: str | Path,
    *,
    provider_identity: Mapping[str, Any] | None = None,
    protocol_capabilities: TransportProtocolCapabilities | None = None,
) -> DistributedProviderAdapter:
    normalized = str(provider_kind or "local-subprocess").strip().lower().replace("_", "-")
    aliases = {
        "local": "local-subprocess",
        "subprocess": "local-subprocess",
        "parallel-federation": "federation",
        "termux-chroot": "chroot",
        "proot-distro": "proot",
        "proot-oci": "oci",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"local-subprocess", "federation", "chroot", "proot", "oci", "ssh"}:
        raise ValueError(f"unsupported distributed provider adapter {provider_kind!r}")
    return DistributedProviderAdapter(
        provider_kind=normalized,
        state_directory=Path(state_directory),
        provider_identity={str(k): v for k, v in dict(provider_identity or {}).items()},
        protocol_capabilities=protocol_capabilities or TransportProtocolCapabilities(),
    )


class LocalLoopbackTransport:
    """Provider-neutral test/local bridge using the same receiver protocol."""

    def __init__(
        self,
        receiver: DelegatedEventReceiver,
        *,
        local_capabilities: TransportProtocolCapabilities | None = None,
        remote_capabilities: TransportProtocolCapabilities | None = None,
    ) -> None:
        self.receiver = receiver
        self.negotiated = negotiate_transport_protocol(
            local_capabilities or TransportProtocolCapabilities(),
            remote_capabilities or TransportProtocolCapabilities(),
            required_features={FEATURE_EVENT_ENVELOPE, FEATURE_CURSOR_ACK, FEATURE_GAP_CATCHUP},
        )

    def send(self, frame: TransportFrame) -> DeliveryAck:
        return self.receiver.receive(frame)


class AsyncJournalForwarder:
    """Journal-backed at-least-once forwarding with a bounded live hint queue.

    Queue overflow never drops canonical events: the queue contains only hints,
    while the origin RUN journal is the durable source.  The worker catches up
    from the last acknowledged sequence. Network/provider I/O and cursor file
    writes happen only on the worker/pump path, never in ``notify_event``.
    """

    def __init__(
        self,
        *,
        link: DelegationLink,
        journal: OriginRunJournal,
        cursor_store: DeliveryCursorStore,
        send_frame: Callable[[TransportFrame], DeliveryAck | int],
        protocol: NegotiatedTransportProtocol | None = None,
        queue_capacity: int = 64,
        poll_interval: float = 0.05,
    ) -> None:
        self.link = link
        self.journal = journal
        self.cursor_store = cursor_store
        self.send_frame = send_frame
        self.protocol = protocol or negotiate_transport_protocol(
            TransportProtocolCapabilities(), TransportProtocolCapabilities()
        )
        self.queue_capacity = max(1, int(queue_capacity))
        self.poll_interval = max(0.005, float(poll_interval))
        self._hints: queue.Queue[int] = queue.Queue(maxsize=self.queue_capacity)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._bus: EventBus | None = None
        self._cursor = self.cursor_store.load(link=self.link)
        self._overflow_count = 0
        self._first_origin_sequence = 0
        self._last_origin_sequence = 0
        self._raw_stream_references = 0
        self._lock = threading.RLock()

    @property
    def cursor(self) -> DeliveryCursor:
        with self._lock:
            return self._cursor

    def attach(self, bus: EventBus) -> None:
        if self._bus is bus:
            return
        if self._bus is not None:
            self._bus.unsubscribe_all(self.notify_event)
        self._bus = bus
        bus.subscribe_all(self.notify_event)

    def detach(self) -> None:
        if self._bus is not None:
            self._bus.unsubscribe_all(self.notify_event)
        self._bus = None

    def notify_event(self, event: DevtoolEvent) -> None:
        """Bounded, non-blocking EventBus listener; performs no provider I/O."""
        if str(getattr(event, "run_id", "") or "") != self.link.child_run_id:
            return
        try:
            self._hints.put_nowait(int(getattr(event, "sequence", -1)))
        except queue.Full:
            with self._lock:
                self._overflow_count += 1

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker,
            name=f"devtool-dist-{self.link.child_run_id[:12]}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, drain: bool = True, timeout: float = 2.0) -> None:
        if drain:
            deadline = time.monotonic() + max(0.0, float(timeout))
            while time.monotonic() < deadline:
                sent = self.pump_once(delivery_mode="catch-up")
                if sent == 0:
                    break
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, float(timeout)))
        self.detach()

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                self._hints.get(timeout=self.poll_interval)
            except queue.Empty:
                pass
            try:
                self.pump_once(delivery_mode="live")
            except Exception as exc:
                self._mark_error(exc)
                # At-least-once retry remains journal-backed; avoid a hot loop.
                self._stop.wait(self.poll_interval)

    def _mark_error(self, exc: Exception) -> None:
        with self._lock:
            gap_rows = self._cursor.unresolved_gaps
            if isinstance(exc, DeliveryGapError):
                gap_rows = (*gap_rows, (exc.expected_sequence, exc.observed_sequence - 1))
            self._cursor = DeliveryCursor(
                self.link.delegation_id,
                self.link.child_run_id,
                acknowledged_sequence=self._cursor.acknowledged_sequence,
                replay_count=self._cursor.replay_count,
                unresolved_gaps=gap_rows,
                health="reconnecting" if not isinstance(exc, DeliveryGapError) else "degraded",
                last_error=str(exc),
            )
            self.cursor_store.save(self._cursor)

    def pump_once(self, *, delivery_mode: str = "catch-up", limit: int | None = None) -> int:
        with self._lock:
            start = self._cursor.acknowledged_sequence
        frames = self.journal.frames_after(start, delivery_mode=delivery_mode, limit=limit)
        if not frames:
            return 0
        sent = 0
        for frame in frames:
            if not self._first_origin_sequence:
                self._first_origin_sequence = int(frame.sequence)
            self._last_origin_sequence = max(self._last_origin_sequence, int(frame.sequence))
            try:
                ack = self.send_frame(frame)
            except Exception as exc:
                self._mark_error(exc)
                raise
            acknowledged = int(
                ack.acknowledged_sequence if isinstance(ack, DeliveryAck) else ack
            )
            if acknowledged != int(frame.sequence):
                raise DistributedTransportError(
                    f"transport acknowledged sequence {acknowledged}, expected {frame.sequence}"
                )
            if frame.raw_stream_reference is not None:
                self._raw_stream_references += 1
            with self._lock:
                replay = self._cursor.replay_count + (1 if delivery_mode == "catch-up" else 0)
                self._cursor = DeliveryCursor(
                    self.link.delegation_id,
                    self.link.child_run_id,
                    acknowledged_sequence=acknowledged,
                    replay_count=replay,
                    unresolved_gaps=(),
                    health="live" if delivery_mode == "live" else "reconciled",
                    last_error="",
                )
                self.cursor_store.save(self._cursor)
            sent += 1
        return sent

    def delivery_state(self) -> EventDeliveryState:
        with self._lock:
            cursor = self._cursor
            return EventDeliveryState(
                delegation_id=self.link.delegation_id,
                child_run_id=self.link.child_run_id,
                health=cursor.health,
                acknowledged_sequence=cursor.acknowledged_sequence,
                pending_hint_count=self._hints.qsize(),
                replay_count=cursor.replay_count,
                unresolved_gaps=cursor.unresolved_gaps,
                overflow_count=self._overflow_count,
                last_error=cursor.last_error,
            )

    def receipt(
        self,
        *,
        child_snapshot: Any | None,
        transport_exit_code: int | None = None,
    ) -> DelegatedExecutionReceipt:
        state = self.delivery_state()
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
        integrity_error = ""
        reconciled = False
        if child_snapshot is None:
            integrity_error = "child terminal snapshot unavailable"
        elif state.acknowledged_sequence > terminal_sequence:
            integrity_error = "acknowledged origin cursor exceeds terminal snapshot sequence"
        elif state.acknowledged_sequence < terminal_sequence:
            integrity_error = "delivery cursor has not reached terminal snapshot sequence"
        elif state.unresolved_gaps:
            integrity_error = "delivery cursor retains unresolved origin gaps"
        else:
            try:
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
            except TerminalReconciliationError as exc:
                integrity_error = str(exc)
        if integrity_error:
            reconciled = False
        final_health = "reconciled" if reconciled else (state.health if state.health != "idle" else "degraded")
        if final_health != state.health:
            state = EventDeliveryState(
                state.delegation_id,
                state.child_run_id,
                final_health,
                state.acknowledged_sequence,
                state.pending_hint_count,
                state.replay_count,
                state.unresolved_gaps,
                state.overflow_count,
                state.last_error,
            )
        return DelegatedExecutionReceipt(
            delegation_id=self.link.delegation_id,
            child_run_id=self.link.child_run_id,
            delivery=state,
            first_origin_sequence=self._first_origin_sequence,
            last_origin_sequence=max(self._last_origin_sequence, terminal_sequence),
            terminal_sequence=terminal_sequence,
            terminal_snapshot_digest=snapshot_digest,
            child_status=child_status,
            child_exit_code=(int(child_exit) if child_exit is not None else None),
            transport_exit_code=(int(transport_exit_code) if transport_exit_code is not None else None),
            reconciled=reconciled,
            integrity_error=integrity_error,
            provider_identity=dict(self.link.provider_identity),
            protocol=self.protocol.as_dict(),
            raw_stream_references=self._raw_stream_references,
        )


__all__ = [
    "DIST_TRANSPORT_PROTOCOL_VERSION",
    "DIST_TRANSPORT_FRAME_SCHEMA_VERSION",
    "FEATURE_EVENT_ENVELOPE",
    "FEATURE_CURSOR_ACK",
    "FEATURE_GAP_CATCHUP",
    "FEATURE_RAW_STREAM_REF",
    "FEATURE_SAFE_EVENT",
    "FEATURE_UNKNOWN_SAFE_EVENT",
    "CONTENT_SEMANTIC_SAFE",
    "CONTENT_RAW_STREAM_REFERENCE",
    "DistributedTransportError",
    "DeliveryGapError",
    "ProtocolNegotiationError",
    "TransportProtocolCapabilities",
    "NegotiatedTransportProtocol",
    "negotiate_transport_protocol",
    "safe_semantic_payload",
    "RawStreamReference",
    "TransportFrame",
    "DeliveryAck",
    "DelegatedEventReceiver",
    "DeliveryCursor",
    "DeliveryCursorStore",
    "EventDeliveryState",
    "DelegatedExecutionReceipt",
    "OriginRunJournal",
    "DistributedProviderAdapter",
    "distributed_provider_adapter",
    "LocalLoopbackTransport",
    "AsyncJournalForwarder",
]
