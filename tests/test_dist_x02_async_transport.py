from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from devtool.core.events import EventBus, MetricChanged, NodeDiscovered, ProcessOutputReceived, RunFinished, RunStarted
from devtool.core.events.listeners import event_to_dict
from devtool.distributed_execution import DelegationLink, ExecutionRunLink, ExternalEventIngestor
from devtool.distributed_transport import (
    AsyncJournalForwarder,
    CONTENT_RAW_STREAM_REFERENCE,
    CONTENT_SEMANTIC_SAFE,
    DeliveryCursorStore,
    DeliveryGapError,
    DelegatedEventReceiver,
    FEATURE_GAP_CATCHUP,
    LocalLoopbackTransport,
    OriginRunJournal,
    ProtocolNegotiationError,
    TransportFrame,
    TransportProtocolCapabilities,
    distributed_provider_adapter,
    negotiate_transport_protocol,
    safe_semantic_payload,
)


def _link(child: str = "child-run") -> DelegationLink:
    return DelegationLink(
        run_link=ExecutionRunLink("parent-run", "parent-node", child),
        repository="repo",
        target="app",
        child_contract="workflow:check",
        contract_ref="repo::app::workflow:check",
        federation_ref="repo::app::check",
        repository_fingerprint="repo-fingerprint",
        source_lock_digest="source-lock",
        source_equivalence_digest="source-equivalence",
        provider_identity={
            "provider_id": "provider-1",
            "provider_kind": "local",
            "execution_environment": "host",
            "provider_evidence_digest": "provider-evidence",
        },
        attestation_digest="attestation",
    )


def _complete(event, sequence: int):
    event.complete_envelope(sequence=sequence)
    return event


def _write_journal(root: Path, events) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "execution-events.jsonl").write_text(
        "\n".join(json.dumps(event_to_dict(event), sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )


def test_safe_semantic_transport_redacts_secrets_and_raw_output_is_reference_only(tmp_path: Path):
    link = _link()
    semantic = _complete(
        RunStarted(
            run_id="child-run",
            command="check",
            metadata={"token": "super-secret", "nested": {"password": "pw"}},
        ),
        1,
    )
    raw = _complete(
        ProcessOutputReceived(
            run_id="child-run",
            process_id="p1",
            stream="stderr",
            text="raw secret bytes",
        ),
        2,
    )
    _write_journal(tmp_path, [semantic, raw])
    frames = OriginRunJournal(tmp_path, child_run_id="child-run", link=link).frames_after(
        0, delivery_mode="live"
    )
    assert [frame.content_class for frame in frames] == [CONTENT_SEMANTIC_SAFE, CONTENT_RAW_STREAM_REFERENCE]
    payload = frames[0].semantic_envelope.payload
    assert payload["metadata"]["token"] == "<redacted>"
    assert payload["metadata"]["nested"]["password"] == "<redacted>"
    raw_payload = frames[1].raw_stream_reference.as_dict()
    assert "text" not in raw_payload
    assert "raw secret bytes" not in json.dumps(raw_payload)
    with pytest.raises(ValueError, match="protected stream reference"):
        safe_semantic_payload(event_to_dict(raw))



def test_transport_frame_and_capabilities_roundtrip_across_process_boundary(tmp_path: Path):
    link = _link()
    event = _complete(RunStarted(run_id="child-run", command="roundtrip"), 1)
    _write_journal(tmp_path, [event])
    frame = OriginRunJournal(tmp_path, child_run_id="child-run", link=link).frames_after(
        0, delivery_mode="live"
    )[0]
    restored = TransportFrame.from_dict(json.loads(json.dumps(frame.as_dict())))
    assert restored == frame
    capabilities = TransportProtocolCapabilities()
    assert TransportProtocolCapabilities.from_dict(capabilities.as_dict()) == capabilities

def test_receiver_detects_origin_gap_and_acknowledges_raw_reference_without_ingesting_output(tmp_path: Path):
    link = _link()
    events = [
        _complete(RunStarted(run_id="child-run", command="check"), 1),
        _complete(ProcessOutputReceived(run_id="child-run", process_id="p", text="hello"), 2),
        _complete(NodeDiscovered(run_id="child-run", node_id="n", label="n"), 3),
    ]
    _write_journal(tmp_path, events)
    frames = OriginRunJournal(tmp_path, child_run_id="child-run", link=link).frames_after(0, delivery_mode="live")
    bus = EventBus()
    receiver = DelegatedEventReceiver(ExternalEventIngestor(bus), link=link)
    receiver.receive(frames[0])
    with pytest.raises(DeliveryGapError, match="expected origin sequence 2"):
        receiver.receive(frames[2])
    receiver.receive(frames[1])
    receiver.receive(frames[2])
    assert receiver.acknowledged_sequence == 3
    assert len(receiver.raw_references) == 1
    snapshot = bus.execution_store.snapshot("child-run")
    assert snapshot is not None and snapshot.last_sequence == 3
    assert not snapshot.processes


def test_cursor_reconnect_catches_up_from_origin_journal_without_duplicate_live_notifications(tmp_path: Path):
    link = _link()
    events = [
        _complete(RunStarted(run_id="child-run", command="child"), 1),
        _complete(NodeDiscovered(run_id="child-run", node_id="n", label="compile"), 2),
        _complete(ProcessOutputReceived(run_id="child-run", node_id="n", process_id="p", text="raw"), 3),
        _complete(RunFinished(run_id="child-run", status="succeeded", exit_code=0), 4),
    ]
    _write_journal(tmp_path, events)
    bus = EventBus()
    seen = []
    bus.subscribe_all(lambda event: seen.append(event.event_id))
    receiver = DelegatedEventReceiver(ExternalEventIngestor(bus), link=link)
    loopback = LocalLoopbackTransport(receiver)
    cursor_store = DeliveryCursorStore(tmp_path / ".devtool-dist" / "cursor.json")
    calls = []

    def flaky(frame):
        calls.append(frame.sequence)
        if frame.sequence == 2:
            raise OSError("simulated disconnect")
        return loopback.send(frame)

    first = AsyncJournalForwarder(
        link=link,
        journal=OriginRunJournal(tmp_path, child_run_id="child-run", link=link),
        cursor_store=cursor_store,
        send_frame=flaky,
    )
    with pytest.raises(OSError, match="disconnect"):
        first.pump_once(delivery_mode="live")
    assert first.cursor.acknowledged_sequence == 1
    assert first.delivery_state().health == "reconnecting"

    reconnect = AsyncJournalForwarder(
        link=link,
        journal=OriginRunJournal(tmp_path, child_run_id="child-run", link=link),
        cursor_store=cursor_store,
        send_frame=loopback.send,
    )
    assert reconnect.pump_once(delivery_mode="catch-up") == 3
    assert reconnect.cursor.acknowledged_sequence == 4
    assert receiver.acknowledged_sequence == 4
    # RunStarted, NodeDiscovered and RunFinished each notify once; raw bytes do not.
    assert len(seen) == 3 and len(set(seen)) == 3
    snapshot = bus.execution_store.snapshot("child-run")
    receipt = reconnect.receipt(child_snapshot=snapshot, transport_exit_code=0)
    assert receipt.ok
    assert receipt.delivery.health == "reconciled"
    assert receipt.terminal_sequence == 4
    assert receipt.raw_stream_references == 1
    assert receipt.cleanup_owner == "LIFE"


def test_eventbus_publication_only_enqueues_bounded_hint_while_transport_blocks(tmp_path: Path):
    link = _link()
    bus = EventBus()
    gate = threading.Event()
    sender_entered = threading.Event()

    def journal_writer(event):
        if event.run_id != "child-run":
            return
        path = tmp_path / "execution-events.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event_to_dict(event), sort_keys=True) + "\n")

    bus.subscribe_all(journal_writer)
    receiver = DelegatedEventReceiver(ExternalEventIngestor(EventBus()), link=link)
    loopback = LocalLoopbackTransport(receiver)

    def slow_send(frame):
        sender_entered.set()
        gate.wait(1.0)
        return loopback.send(frame)

    forwarder = AsyncJournalForwarder(
        link=link,
        journal=OriginRunJournal(tmp_path, child_run_id="child-run", link=link),
        cursor_store=DeliveryCursorStore(tmp_path / ".devtool-dist" / "cursor.json"),
        send_frame=slow_send,
        queue_capacity=1,
        poll_interval=0.01,
    )
    forwarder.attach(bus)
    forwarder.start()
    started = time.monotonic()
    bus.publish(RunStarted(run_id="child-run", command="check"))
    elapsed = time.monotonic() - started
    assert elapsed < 0.25
    assert sender_entered.wait(0.5)
    gate.set()
    forwarder.stop(drain=True, timeout=1.0)
    assert forwarder.cursor.acknowledged_sequence == 1



def test_bounded_hint_overflow_recovers_every_canonical_event_from_journal(tmp_path: Path):
    link = _link()
    events = [_complete(RunStarted(run_id="child-run", command="load"), 1)]
    events.extend(
        _complete(MetricChanged(run_id="child-run", name=f"m{sequence}", value=sequence), sequence)
        for sequence in range(2, 101)
    )
    _write_journal(tmp_path, events)
    destination = EventBus()
    receiver = DelegatedEventReceiver(ExternalEventIngestor(destination), link=link)
    transport = LocalLoopbackTransport(receiver)
    forwarder = AsyncJournalForwarder(
        link=link,
        journal=OriginRunJournal(tmp_path, child_run_id="child-run", link=link),
        cursor_store=DeliveryCursorStore(tmp_path / ".devtool-dist" / "cursor.json"),
        send_frame=transport.send,
        queue_capacity=2,
    )
    for event in events:
        forwarder.notify_event(event)
    assert forwarder.delivery_state().overflow_count > 0
    assert forwarder.pump_once(delivery_mode="catch-up") == 100
    assert forwarder.cursor.acknowledged_sequence == 100
    snapshot = destination.execution_store.snapshot("child-run")
    assert snapshot is not None and snapshot.last_sequence == 100


def test_unknown_future_safe_event_is_preserved_when_negotiated_schema_can_carry_it():
    link = _link()
    base = _complete(NodeDiscovered(run_id="child-run", node_id="future", label="future"), 1)
    payload = event_to_dict(base)
    payload["event_type"] = "future.semantic.safe"
    from devtool.distributed_execution import EventTransportEnvelope
    from devtool.distributed_transport import CONTENT_SEMANTIC_SAFE, TransportFrame

    envelope = EventTransportEnvelope.for_event(payload, link=link, delivery_mode="live")
    frame = TransportFrame(
        delegation_id=link.delegation_id,
        child_run_id="child-run",
        event_id=payload["event_id"],
        sequence=1,
        content_class=CONTENT_SEMANTIC_SAFE,
        semantic_envelope=envelope,
    )
    bus = EventBus()
    receiver = DelegatedEventReceiver(ExternalEventIngestor(bus), link=link)
    ack = receiver.receive(frame)
    assert ack.acknowledged_sequence == 1
    snapshot = bus.execution_store.snapshot("child-run")
    assert snapshot is not None and snapshot.last_sequence == 1

def test_protocol_negotiation_is_explicit_and_provider_adapters_share_one_contract(tmp_path: Path):
    local = TransportProtocolCapabilities()
    remote = TransportProtocolCapabilities(features=frozenset({FEATURE_GAP_CATCHUP}))
    negotiated = negotiate_transport_protocol(local, remote, required_features={FEATURE_GAP_CATCHUP})
    assert negotiated.protocol_version == 1
    assert negotiated.features == (FEATURE_GAP_CATCHUP,)
    with pytest.raises(ProtocolNegotiationError, match="missing required"):
        negotiate_transport_protocol(local, remote, required_features={"raw-stream-ref-v1"})

    link = _link()
    adapters = [
        distributed_provider_adapter(kind, tmp_path / kind, provider_identity={"provider_id": kind})
        for kind in ("local-subprocess", "federation", "chroot", "proot", "oci")
    ]
    assert {adapter.channel_kind for adapter in adapters} == {"run-journal"}
    assert {adapter.cleanup_owner for adapter in adapters} == {"LIFE"}
    assert all(adapter.origin_journal(link).child_run_id == "child-run" for adapter in adapters)
    assert all(adapter.cursor_store(link).path.name.startswith("delivery-") for adapter in adapters)


def test_receipt_keeps_transport_health_secondary_to_child_failure(tmp_path: Path):
    link = _link()
    event = _complete(RunFinished(run_id="child-run", status="failed", exit_code=7), 1)
    _write_journal(tmp_path, [event])
    bus = EventBus()
    receiver = DelegatedEventReceiver(ExternalEventIngestor(bus), link=link)
    transport = LocalLoopbackTransport(receiver)
    forwarder = AsyncJournalForwarder(
        link=link,
        journal=OriginRunJournal(tmp_path, child_run_id="child-run", link=link),
        cursor_store=DeliveryCursorStore(tmp_path / ".devtool-dist" / "cursor.json"),
        send_frame=transport.send,
    )
    forwarder.pump_once(delivery_mode="live")
    snapshot = bus.execution_store.snapshot("child-run")
    receipt = forwarder.receipt(child_snapshot=snapshot, transport_exit_code=7)
    assert receipt.ok
    assert receipt.child_status == "failed"
    assert receipt.child_exit_code == 7
    assert receipt.delivery.health == "reconciled"


def test_parallel_federation_default_publish_live_tails_child_run_journal(tmp_path: Path, monkeypatch):
    from devtool.config import load_project_config
    from devtool.federation_execution import run_federated_workflow
    from devtool.repository_identity import build_repository_identity

    child = tmp_path / "child"
    child.mkdir()
    (child / ".devtool.toml").write_text(
        '''[project]\nrunner = "command"\n\n[workspace]\ndefault_target = "app"\n\n[jobs.one]\nrunner = "command"\ncommand = ["true"]\n\n[targets.app]\nrunner = "command"\nroot = "."\n\n[targets.app.workflows]\nwork = ["job:one"]\n''',
        encoding="utf-8",
    )
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner = "command"\n\n[federation]\nschema = 1\n\n[federation.repositories.child]\npath = "../child"\n\n[federation.workflows.release]\nsteps = [\n  { id = "child", ref = "child::work" }\n]\n''',
        encoding="utf-8",
    )
    monkeypatch.setenv("DEVTOOL_GLOBAL_CONFIG", str(tmp_path / "missing-global.toml"))
    config = load_project_config(
        str(parent), None, allow_ambiguous_target=True, allow_ambiguous_project_kind=True
    )

    def executor(argv: list[str], env: dict[str, str]) -> int:
        root = Path(argv[argv.index("--repo") + 1])
        lineage = {
            "kind": "federation",
            "relationship": "child",
            "parent_run_id": env["DEVTOOL_FEDERATION_PARENT_RUN_ID"],
            "parent_node_id": env["DEVTOOL_FEDERATION_PARENT_NODE_ID"],
            "child_run_id": env["DEVTOOL_RUN_ID"],
            "federation_workflow": env["DEVTOOL_FEDERATION_WORKFLOW"],
            "federation_step": env["DEVTOOL_FEDERATION_STEP"],
            "repository": env["DEVTOOL_FEDERATION_REPOSITORY"],
            "target": env["DEVTOOL_FEDERATION_TARGET"],
            "child_workflow": env["DEVTOOL_FEDERATION_CHILD_WORKFLOW"],
            "federation_ref": env["DEVTOOL_FEDERATION_REF"],
            "requested_ref": env["DEVTOOL_FEDERATION_REQUESTED_REF"],
            "delegation_id": env["DEVTOOL_DELEGATION_LINK_ID"],
        }
        identity = build_repository_identity(root, root / ".devtool.toml").as_dict()
        started = _complete(
            RunStarted(
                run_id=env["DEVTOOL_RUN_ID"],
                command="work",
                metadata={"run_lineage": lineage, "repository_identity": identity},
                source="federation-child",
            ),
            1,
        )
        finished = _complete(
            RunFinished(
                run_id=env["DEVTOOL_RUN_ID"], status="succeeded", exit_code=0, source="federation-child"
            ),
            2,
        )
        directory = Path(env["DEVTOOL_FEDERATION_CHILD_STATE_DIR"])
        _write_journal(directory, [started, finished])
        return 0

    report = run_federated_workflow(
        config,
        "release",
        reenter=lambda _argv: 99,
        parent_run_id="dist-x02-live-parent",
        parallel=True,
        max_parallel=1,
        child_executor=executor,
    )
    assert report.ok and len(report.children) == 1
    child_result = report.children[0]
    receipt = child_result.delivery_receipt
    assert receipt["reconciled"] is True
    assert receipt["delivery"]["acknowledged_sequence"] == 2
    assert receipt["child_status"] == "succeeded"
    assert receipt["protocol"]["protocol_version"] == 1
    assert child_result.as_dict()["delivery_receipt"]["reconciled"] is True
    assert child_result.run_lineage["child_run_id"] == child_result.run_id
