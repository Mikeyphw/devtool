from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from devtool.core.events import EventBus, NodeDiscovered, RunFinished, RunStarted
from devtool.core.events.listeners import event_to_dict
from devtool.dist_g1_gate import audit_window, run_real_process_proof
from devtool.distributed_execution import (
    DelegationLink,
    ExecutionRunLink,
    ExternalEventEquivocationError,
    ExternalEventIngestor,
)
from devtool.distributed_transport import (
    AsyncJournalForwarder,
    DeliveryCursor,
    DeliveryCursorStore,
    DelegatedEventReceiver,
    DistributedTransportError,
    LocalLoopbackTransport,
    OriginRunJournal,
)
from devtool import federation_execution


def _link(child: str = "dist-g1-child") -> DelegationLink:
    return DelegationLink(
        run_link=ExecutionRunLink("dist-g1-parent", "federation:gate", child),
        repository="gate-child",
        target="app",
        child_contract="workflow:check",
        contract_ref="gate-child::app::workflow:check",
        federation_ref="gate-child::app::check",
        repository_fingerprint="repo-fingerprint",
        source_lock_digest="source-lock",
        source_equivalence_digest="source-equivalence",
        git_lineage_digest="git-lineage",
        provider_identity={
            "provider_id": "gate-provider",
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


def test_gate_audit_closes_x01_x02_window_without_claiming_later_dist() -> None:
    result = audit_window(Path.cwd())
    assert result["roadmap_id"] == "DIST-G1"
    assert result["position"] == "42/70"
    assert result["window"] == ["DIST-X01", "DIST-X02", "DIST-G1"]
    assert "DIST-P036" in result["closed_promises"]
    assert "DIST-P038" in result["closed_promises"]
    assert "DIST-P070" in result["closed_promises"]
    assert "DIST-P065" in result["later_dist_partial_promises"]


def test_fresh_process_replay_integrity_and_exact_resume() -> None:
    result = run_real_process_proof()
    assert result["ok"] is True
    assert result["stages"][0]["acknowledged_sequence"] == 1
    assert result["stages"][1]["verified_duplicate"] is True
    assert result["stages"][1]["verified_equivocation_rejection"] is True
    assert result["stages"][1]["acknowledged_sequence"] == 2


def test_cursor_persists_replay_integrity_boundary(tmp_path: Path) -> None:
    link = _link()
    cursor = DeliveryCursor(
        link.delegation_id,
        link.child_run_id,
        acknowledged_sequence=7,
        acknowledged_event_id="event-7",
        acknowledged_content_digest="digest-7",
        replay_count=3,
        health="reconciled",
    )
    store = DeliveryCursorStore(tmp_path / "cursor.json")
    store.save(cursor)
    restored = store.load(link=link)
    assert restored == cursor


def test_fresh_receiver_rejects_unverifiable_older_replay(tmp_path: Path) -> None:
    link = _link()
    events = [
        _complete(RunStarted(run_id=link.child_run_id, command="gate"), 1),
        _complete(NodeDiscovered(run_id=link.child_run_id, node_id="n", label="n"), 2),
    ]
    _write_journal(tmp_path, events)
    frames = OriginRunJournal(tmp_path, child_run_id=link.child_run_id, link=link).frames_after(0, delivery_mode="catch-up")
    cursor = DeliveryCursor(
        link.delegation_id,
        link.child_run_id,
        acknowledged_sequence=2,
        acknowledged_event_id=frames[1].event_id,
        acknowledged_content_digest=frames[1].semantic_envelope.payload_digest,
    )
    receiver = DelegatedEventReceiver.from_cursor(
        ExternalEventIngestor(EventBus()), link=link, cursor=cursor
    )
    with pytest.raises(DistributedTransportError, match="cannot verify replay older"):
        receiver.receive(frames[0])


def test_terminal_receipt_fails_closed_for_cursor_ahead_behind_and_rc_contradiction(tmp_path: Path) -> None:
    link = _link()
    event = _complete(RunFinished(run_id=link.child_run_id, status="succeeded", exit_code=0), 1)
    _write_journal(tmp_path, [event])

    bus = EventBus()
    receiver = DelegatedEventReceiver(ExternalEventIngestor(bus), link=link)
    transport = LocalLoopbackTransport(receiver)
    store = DeliveryCursorStore(tmp_path / "cursor.json")
    forwarder = AsyncJournalForwarder(
        link=link,
        journal=OriginRunJournal(tmp_path, child_run_id=link.child_run_id, link=link),
        cursor_store=store,
        send_frame=transport.send,
    )
    assert forwarder.pump_once(delivery_mode="live") == 1
    snapshot = bus.execution_store.snapshot(link.child_run_id)
    assert snapshot is not None
    ok = forwarder.receipt(child_snapshot=snapshot, transport_exit_code=0)
    assert ok.ok

    forwarder._cursor = DeliveryCursor(link.delegation_id, link.child_run_id, acknowledged_sequence=2, health="live")
    ahead = forwarder.receipt(child_snapshot=snapshot, transport_exit_code=0)
    assert not ahead.ok and "exceeds terminal snapshot" in ahead.integrity_error

    forwarder._cursor = DeliveryCursor(link.delegation_id, link.child_run_id, acknowledged_sequence=0, health="live")
    behind = forwarder.receipt(child_snapshot=snapshot, transport_exit_code=0)
    assert not behind.ok and "has not reached terminal snapshot" in behind.integrity_error

    forwarder._cursor = DeliveryCursor(link.delegation_id, link.child_run_id, acknowledged_sequence=1, health="live")
    contradiction = forwarder.receipt(child_snapshot=snapshot, transport_exit_code=9)
    assert not contradiction.ok and "transport rc/child snapshot contradiction" in contradiction.integrity_error


def test_one_live_subscriber_update_per_accepted_semantic_event(tmp_path: Path) -> None:
    link = _link()
    events = [
        _complete(RunStarted(run_id=link.child_run_id, command="gate"), 1),
        _complete(NodeDiscovered(run_id=link.child_run_id, node_id="n", label="n"), 2),
    ]
    _write_journal(tmp_path, events)
    destination = EventBus()
    seen: list[str] = []
    destination.subscribe_all(lambda event: seen.append(event.event_id))
    receiver = DelegatedEventReceiver(ExternalEventIngestor(destination), link=link)
    transport = LocalLoopbackTransport(receiver)
    frames = OriginRunJournal(tmp_path, child_run_id=link.child_run_id, link=link).frames_after(0, delivery_mode="live")
    for frame in frames:
        transport.send(frame)
    # Boundary replay is accepted as duplicate, but must not publish twice.
    duplicate = transport.send(frames[-1])
    assert duplicate.duplicate
    assert len(seen) == 2 and len(set(seen)) == 2


def test_primary_failure_survives_git_lineage_finalization_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args, **kwargs):
        raise RuntimeError("lineage-finalization-broke")

    published = []
    monkeypatch.setattr(federation_execution, "_finalize_child_git_lineage", explode)
    monkeypatch.setattr(
        federation_execution,
        "_publish_child_git_lineage",
        lambda record, *, publish: (published.append(dict(record)) or dict(record)),
    )
    result = federation_execution._finalize_child_git_lineage_preserving_primary_failure(
        SimpleNamespace(root="/missing"),
        {"child_run_id": "child", "state": "planned"},
        terminal_status="failed",
        terminal_exit_code=7,
        publish=lambda event: None,
    )
    assert result["primary_failure_preserved"] is True
    assert result["terminal_status"] == "failed"
    assert result["terminal_exit_code"] == 7
    assert "lineage-finalization-broke" in result["finalization_error"]
    assert published and published[0]["state"] == "finalization-error-secondary"



def test_primary_failure_survives_secondary_lineage_publish_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        federation_execution,
        "_finalize_child_git_lineage",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("finalization-broke")),
    )
    monkeypatch.setattr(
        federation_execution,
        "_publish_child_git_lineage",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("secondary-publish-broke")),
    )
    result = federation_execution._finalize_child_git_lineage_preserving_primary_failure(
        SimpleNamespace(root="/missing"),
        {"child_run_id": "child"},
        terminal_status="failed",
        terminal_exit_code=11,
        publish=lambda event: None,
    )
    assert result["primary_failure_preserved"] is True
    assert result["terminal_exit_code"] == 11
    assert "secondary-publish-broke" in result["secondary_evidence_publish_error"]

def test_success_still_fails_closed_if_git_lineage_finalization_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        federation_execution,
        "_finalize_child_git_lineage",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("lineage-finalization-broke")),
    )
    with pytest.raises(RuntimeError, match="lineage-finalization-broke"):
        federation_execution._finalize_child_git_lineage_preserving_primary_failure(
            SimpleNamespace(root="/missing"),
            {"child_run_id": "child"},
            terminal_status="succeeded",
            terminal_exit_code=0,
            publish=lambda event: None,
        )
