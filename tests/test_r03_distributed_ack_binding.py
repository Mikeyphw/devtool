"""R03: a durable delivery cursor is advanced only by a bound acknowledgement."""
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


def _link() -> DelegationLink:
    return DelegationLink(
        run_link=ExecutionRunLink("parent", "node", "child"),
        repository="repo", target="app", child_contract="workflow:check",
        contract_ref="repo::app::workflow:check", federation_ref="repo::app::check",
        repository_fingerprint="repo-fingerprint", source_lock_digest="source-lock",
        source_equivalence_digest="equivalence", provider_identity={
            "provider_id": "local", "provider_kind": "local",
            "execution_environment": "host", "provider_evidence_digest": "provider-proof",
        }, attestation_digest="attestation",
    )


def _setup(tmp_path: Path):
    link = _link()
    event = RunStarted(run_id="child", command="check")
    event.complete_envelope(sequence=1)
    (tmp_path / "execution-events.jsonl").write_text(json.dumps(event_to_dict(event)) + "\n")
    journal = OriginRunJournal(tmp_path, child_run_id="child", link=link)
    store = DeliveryCursorStore(tmp_path / "cursor.json")
    receiver = DelegatedEventReceiver(ExternalEventIngestor(EventBus()), link=link)
    return link, journal, store, LocalLoopbackTransport(receiver)


@pytest.mark.parametrize("corruption", [
    "delegation_id", "child_run_id", "event_id", "acknowledged_sequence", "bare-integer",
])
def test_invalid_transport_ack_cannot_advance_cursor(tmp_path: Path, corruption: str):
    link, journal, store, loopback = _setup(tmp_path)
    frame = journal.frames_after(0, delivery_mode="catch-up")[0]
    valid = DeliveryAck(link.delegation_id, link.child_run_id, frame.sequence, frame.event_id)

    def forged(current):
        if corruption == "bare-integer":
            return current.sequence
        if corruption == "acknowledged_sequence":
            return replace(valid, acknowledged_sequence=current.sequence + 1)
        return replace(valid, **{corruption: "incorrect"})

    forwarder = AsyncJournalForwarder(
        link=link, journal=journal, cursor_store=store, send_frame=forged,
    )
    with pytest.raises(DistributedTransportError):
        forwarder.pump_once()
    assert forwarder.cursor.acknowledged_sequence == 0
    assert store.load(link=link).acknowledged_sequence == 0


def test_valid_ack_updates_durable_cursor(tmp_path: Path):
    link, journal, store, loopback = _setup(tmp_path)
    forwarder = AsyncJournalForwarder(
        link=link, journal=journal, cursor_store=store, send_frame=loopback.send,
    )
    assert forwarder.pump_once() == 1
    assert store.load(link=link).acknowledged_sequence == 1


def test_receiver_verified_duplicate_is_allowed_after_lost_sender_cursor(tmp_path: Path):
    link, journal, store, loopback = _setup(tmp_path)
    frame = journal.frames_after(0, delivery_mode="catch-up")[0]
    assert not loopback.send(frame).duplicate
    assert loopback.send(frame).duplicate
    forwarder = AsyncJournalForwarder(
        link=link, journal=journal, cursor_store=store, send_frame=loopback.send,
    )
    assert forwarder.pump_once() == 1
    assert store.load(link=link).acknowledged_sequence == 1
