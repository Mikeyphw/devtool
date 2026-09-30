from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from devtool.core.events import EventBus, RunFinished, RunStarted, NodeDiscovered, ProcessOutputReceived
from devtool.core.events.listeners import event_to_dict
from devtool.distributed_execution import (
    DelegationAuthorizationError,
    DelegationLink,
    DelegationRegistry,
    EventTransportEnvelope,
    ExecutionRunLink,
    ExternalEventEquivocationError,
    ExternalEventIngestor,
    canonical_event_payload_digest,
    delegation_links_from_snapshot,
    import_child_run_journal,
    reconcile_terminal_truth,
)


def _link(*, child: str = "child-run", provider: str = "local") -> DelegationLink:
    return DelegationLink(
        run_link=ExecutionRunLink("parent-run", "federation:w:s", child),
        repository="child",
        target="app",
        child_contract="workflow:check",
        contract_ref="child::app::workflow:check",
        federation_ref="child::app::check",
        repository_fingerprint="repo-fingerprint",
        source_lock_digest="lock-digest",
        source_equivalence_digest="equiv-digest",
        git_lineage_digest="git-lineage-digest",
        provider_identity={
            "provider_id": provider,
            "provider_kind": "local" if provider == "local" else "ssh",
            "execution_environment": "host",
            "provider_evidence_digest": "provider-evidence",
        },
        attestation_digest="attestation-digest",
    )


def _complete(event, sequence: int):
    event.complete_envelope(sequence=sequence)
    return event


def test_run_and_delegation_link_are_semantic_and_path_independent():
    first = _link()
    second = _link()
    assert first == second
    assert first.run_link.parent_run_id == "parent-run"
    assert first.run_link.child_run_id == "child-run"
    assert "/" not in first.delegation_id
    payload = first.as_dict()
    assert payload["run_link"]["relationship"] == "delegated-child"
    assert payload["repository_fingerprint"] == "repo-fingerprint"
    assert "root" not in payload


def test_transport_envelope_preserves_origin_event_identity_and_digest():
    event = _complete(RunStarted(run_id="child-run", command="check", source="child"), 1)
    link = _link()
    envelope = EventTransportEnvelope.for_event(event, link=link, producer_id="origin")
    assert envelope.event_id == event.event_id
    assert envelope.sequence == event.sequence
    assert envelope.child_run_id == event.run_id
    assert envelope.payload_digest == canonical_event_payload_digest(event)
    assert envelope.payload["source"] == "child"


def test_live_external_ingest_is_exactly_once_and_conflict_is_equivocation():
    bus = EventBus()
    seen = []
    bus.subscribe_all(lambda event: seen.append(event.event_id))
    ingestor = ExternalEventIngestor(bus)
    link = ingestor.register(_link())
    event = _complete(RunStarted(run_id="child-run", command="check", source="child"), 1)
    envelope = EventTransportEnvelope.for_event(event, link=link, producer_id="origin", delivery_mode="live")

    first = ingestor.ingest(envelope)
    duplicate = ingestor.ingest(envelope)
    assert first.accepted and first.live_notified
    assert duplicate.duplicate and not duplicate.accepted
    assert seen == [event.event_id]
    assert bus.execution_store.snapshot("child-run").command == "check"

    # A reconnect can construct a fresh ingestor. Idempotence lives at the
    # canonical bus/store boundary, so the same accepted event is still not
    # republished to listeners.
    reconnect = ExternalEventIngestor(bus)
    reconnect.register(link)
    reconnect_duplicate = reconnect.ingest(envelope)
    assert reconnect_duplicate.duplicate and not reconnect_duplicate.accepted
    assert seen == [event.event_id]

    conflicting = dict(envelope.payload)
    conflicting["command"] = "different-command"
    conflict_envelope = EventTransportEnvelope(
        delegation_id=envelope.delegation_id,
        child_run_id=envelope.child_run_id,
        producer_id=envelope.producer_id,
        event_id=envelope.event_id,
        sequence=envelope.sequence,
        payload=conflicting,
        payload_digest=canonical_event_payload_digest(conflicting),
        delivery_mode="live",
        provider_identity=envelope.provider_identity,
        attestation_digest=envelope.attestation_digest,
    )
    with pytest.raises(ExternalEventEquivocationError):
        ingestor.ingest(conflict_envelope)


def test_recovery_ingest_updates_canonical_store_without_live_listener():
    bus = EventBus()
    seen = []
    bus.subscribe_all(lambda event: seen.append(event.event_id))
    ingestor = ExternalEventIngestor(bus)
    link = ingestor.register(_link())
    event = _complete(RunStarted(run_id="child-run", command="recover", source="child"), 1)
    result = ingestor.ingest(EventTransportEnvelope.for_event(event, link=link, delivery_mode="recovery"))
    assert result.accepted and not result.live_notified
    assert seen == []
    assert bus.execution_store.snapshot("child-run").command == "recover"


def test_external_ingest_is_bound_to_planned_provider_attestation_and_one_producer():
    bus = EventBus()
    registry = DelegationRegistry()
    ingestor = ExternalEventIngestor(bus, registry=registry)
    link = ingestor.register(_link())
    first = _complete(RunStarted(run_id="child-run", command="check"), 1)
    ingestor.ingest(EventTransportEnvelope.for_event(first, link=link, producer_id="producer-a"))

    second = _complete(NodeDiscovered(run_id="child-run", node_id="n", label="n"), 2)
    with pytest.raises(DelegationAuthorizationError, match="authoritative producer"):
        ingestor.ingest(EventTransportEnvelope.for_event(second, link=link, producer_id="producer-b"))

    bad_provider = EventTransportEnvelope(
        delegation_id=link.delegation_id,
        child_run_id="child-run",
        producer_id="producer-a",
        event_id=second.event_id,
        sequence=second.sequence,
        payload=event_to_dict(second),
        payload_digest=canonical_event_payload_digest(second),
        provider_identity={"provider_id": "ssh", "provider_kind": "ssh", "execution_environment": "host", "provider_evidence_digest": "provider-evidence"},
        attestation_digest=link.attestation_digest,
    )
    with pytest.raises(DelegationAuthorizationError, match="provider identity"):
        ingestor.ingest(bad_provider)

    unknown = ExternalEventIngestor(EventBus())
    with pytest.raises(DelegationAuthorizationError, match="unplanned delegation"):
        unknown.ingest(EventTransportEnvelope.for_event(first, link=link))


def test_terminal_reconciliation_uses_child_snapshot_and_fails_closed_on_contradiction():
    ok = reconcile_terminal_truth(
        transport_exit_code=0,
        child_snapshot=SimpleNamespace(status="succeeded", exit_code=0),
    )
    assert ok.ok and ok.authoritative_status == "succeeded" and ok.authoritative_exit_code == 0

    failed = reconcile_terminal_truth(
        transport_exit_code=3,
        child_snapshot=SimpleNamespace(status="failed", exit_code=3),
    )
    assert failed.ok and failed.authoritative_status == "failed" and failed.authoritative_exit_code == 3

    contradiction = reconcile_terminal_truth(
        transport_exit_code=7,
        child_snapshot=SimpleNamespace(status="succeeded", exit_code=0),
    )
    assert not contradiction.ok
    assert contradiction.authoritative_status == "failed"
    assert "contradiction" in contradiction.integrity_error


def test_parallel_child_journal_recovery_imports_canonical_run_without_republishing(tmp_path: Path):
    link = _link()
    events = [
        _complete(RunStarted(run_id="child-run", command="child", source="child"), 1),
        _complete(NodeDiscovered(run_id="child-run", node_id="n1", label="compile", kind="job", source="child"), 2),
        _complete(ProcessOutputReceived(run_id="child-run", node_id="n1", process_id="p1", text="raw output", source="child"), 3),
        _complete(RunFinished(run_id="child-run", status="succeeded", exit_code=0, source="child"), 4),
    ]
    journal = tmp_path / "execution-events.jsonl"
    journal.write_text("\n".join(json.dumps(event_to_dict(event), sort_keys=True) for event in events) + "\n", encoding="utf-8")

    bus = EventBus()
    seen = []
    bus.subscribe_all(lambda event: seen.append(event.event_id))
    snapshot = import_child_run_journal(tmp_path, child_run_id="child-run", link=link, bus=bus)
    assert snapshot is not None
    assert snapshot.run_id == "child-run"
    assert snapshot.status == "succeeded"
    assert snapshot.last_sequence == 4
    assert snapshot.node("n1") is not None
    assert seen == []
    # Raw process output is intentionally not duplicated through the semantic bridge.
    assert not snapshot.processes


def test_transport_envelope_rejects_forged_event_identity():
    link = _link()
    event = _complete(RunStarted(run_id="child-run", command="x"), 1)
    payload = event_to_dict(event)
    payload["event_id"] = "evt-forged"
    with pytest.raises(ValueError, match="identity"):
        EventTransportEnvelope(
            delegation_id=link.delegation_id,
            child_run_id="child-run",
            producer_id="producer",
            event_id="evt-forged",
            sequence=1,
            payload=payload,
            payload_digest=canonical_event_payload_digest(payload),
            provider_identity=link.provider_identity,
            attestation_digest=link.attestation_digest,
        )


def test_delegation_registry_rejects_two_links_for_one_child_run():
    registry = DelegationRegistry()
    registry.register(_link())
    other = DelegationLink(
        run_link=ExecutionRunLink("other-parent", "other-node", "child-run"),
        repository="child",
        repository_fingerprint="repo-fingerprint",
        provider_identity={"provider_id": "local"},
        attestation_digest="attestation-digest",
    )
    with pytest.raises(DelegationAuthorizationError, match="already owned"):
        registry.register(other)


def test_generic_run_link_query_projects_parent_node_metadata():
    link = _link()
    node = SimpleNamespace(metadata={"delegation_link": link.as_dict()})
    snapshot = SimpleNamespace(run_lineage={}, nodes=(node,))
    links = delegation_links_from_snapshot(snapshot)
    assert links == (link,)
    assert links[0].run_link.parent_run_id == "parent-run"
