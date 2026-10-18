"""RMP-C03: source-, session-, event- and attestation-bound cursor writes."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from devtool.core.events import EventBus, RunStarted
from devtool.core.events.listeners import event_to_dict
from devtool.distributed_execution import DelegationLink, ExecutionRunLink, ExternalEventIngestor
from devtool.distributed_transport import (
    AsyncJournalForwarder, DeliveryAck, DeliveryCursorStore, DelegatedEventReceiver,
    DistributedTransportError, LocalLoopbackTransport, OriginRunJournal,
)


def setup(tmp_path: Path, *, send=None):
    link = DelegationLink(
        run_link=ExecutionRunLink("parent", "node", "child"),
        repository="repo", target="app", source_lock_digest="source-lock",
        provider_identity={"provider_id": "worker-a", "provider_evidence_digest": "provider-proof"},
        attestation_digest="attestation-A",
    )
    event = RunStarted(run_id="child", command="verify")
    event.complete_envelope(sequence=1)
    (tmp_path / "execution-events.jsonl").write_text(json.dumps(event_to_dict(event)) + "\n")
    journal = OriginRunJournal(tmp_path, child_run_id="child", link=link)
    store = DeliveryCursorStore(tmp_path / "cursor.json")
    receiver = DelegatedEventReceiver(ExternalEventIngestor(EventBus()), link=link)
    transport = LocalLoopbackTransport(receiver)
    frame = journal.frames_after(0, delivery_mode="catch-up")[0]
    return link, journal, store, transport, frame


@pytest.mark.parametrize("variant", [
    "legacy-four-fields", "wrong-frame", "missing-frame", "wrong-session", "missing-session",
    "wrong-attestation", "missing-attestation", "wrong-child", "wrong-delegation",
    "wrong-event", "wrong-sequence", "bool-sequence", "float-sequence",
    "string-sequence", "invalid-duplicate",
])
def test_substituted_ack_does_not_advance_durable_cursor(tmp_path: Path, variant: str) -> None:
    link, journal, store, transport, frame = setup(tmp_path)
    valid = transport.send(frame)
    changes = {
        "wrong-frame": {"frame_digest": "wrong"},
        "missing-frame": {"frame_digest": ""},
        "wrong-session": {"session_digest": "wrong"},
        "missing-session": {"session_digest": ""},
        "wrong-attestation": {"attestation_digest": "other"},
        "missing-attestation": {"attestation_digest": ""},
        "wrong-child": {"child_run_id": "other"},
        "wrong-delegation": {"delegation_id": "other"},
        "wrong-event": {"event_id": "other"},
        "wrong-sequence": {"acknowledged_sequence": 2},
        "bool-sequence": {"acknowledged_sequence": True},
        "float-sequence": {"acknowledged_sequence": 1.0},
        "string-sequence": {"acknowledged_sequence": "1"},
        "invalid-duplicate": {"duplicate": "false"},
    }
    forged = (
        DeliveryAck(link.delegation_id, link.child_run_id, 1, frame.event_id)
        if variant == "legacy-four-fields" else replace(valid, **changes[variant])
    )
    forwarder = AsyncJournalForwarder(
        link=link, journal=journal, cursor_store=store, send_frame=lambda _: forged,
    )
    with pytest.raises(DistributedTransportError):
        forwarder.pump_once()
    assert forwarder.cursor.acknowledged_sequence == 0
    assert store.load(link=link).acknowledged_sequence == 0


@pytest.mark.parametrize("sequence", [True, 1.0, "1", None])
def test_decode_rejects_ambiguous_sequence(sequence) -> None:
    with pytest.raises(DistributedTransportError):
        DeliveryAck.from_dict({"acknowledged_sequence": sequence})


def test_acked_frame_roundtrip_and_reconnect(tmp_path: Path) -> None:
    link, journal, store, transport, frame = setup(tmp_path)
    ack = transport.send(frame)
    assert DeliveryAck.from_dict(json.loads(json.dumps(ack.as_dict()))) == ack
    assert ack.frame_digest == frame.frame_digest
    assert ack.attestation_digest == link.attestation_digest
    first = AsyncJournalForwarder(link=link, journal=journal, cursor_store=store, send_frame=transport.send)
    assert first.pump_once() == 1
    assert store.load(link=link).acknowledged_sequence == 1
    # Same delegated session context across legitimate restart is replay-safe.
    second = AsyncJournalForwarder(link=link, journal=journal, cursor_store=store, send_frame=transport.send)
    assert second.pump_once() == 0
    assert second.cursor.acknowledged_sequence == 1


def test_cross_provider_ack_substitution_rejected(tmp_path: Path) -> None:
    link, journal, store, transport, frame = setup(tmp_path)
    original = transport.send(frame)
    # The exact frame is not authorized for another delegation; simulated
    # provider reply carrying original event ID but different proof is rejected.
    forged = replace(original, session_digest="other-provider-session", attestation_digest="other-attestation")
    forwarder = AsyncJournalForwarder(link=link,journal=journal,cursor_store=store,send_frame=lambda _: forged)
    with pytest.raises(DistributedTransportError):
        forwarder.pump_once()
    assert store.load(link=link).acknowledged_sequence == 0
