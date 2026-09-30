from __future__ import annotations

"""DIST-G1 authoritative identity/delivery/recovery milestone checks.

The gate audits the DIST-X01/X02 promise window and carries one real-process
transport proof.  It intentionally does not own SSH/tmux/hierarchy/control
adoption retained for later DIST work.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from .core.events import EventBus, NodeDiscovered, RunStarted
from .core.events.listeners import event_to_dict
from .distributed_execution import (
    DelegationLink,
    EventTransportEnvelope,
    ExecutionRunLink,
    ExternalEventEquivocationError,
    ExternalEventIngestor,
    canonical_event_payload_digest,
)
from .distributed_transport import (
    AsyncJournalForwarder,
    DeliveryCursorStore,
    DelegatedEventReceiver,
    LocalLoopbackTransport,
    OriginRunJournal,
    TransportFrame,
)

X01_REQUIRED_GATE_PARTIALS = {"DIST-P036", "DIST-P038", "DIST-P070"}
GATE_EXPORTED_TO_LATER_DIST = {
    "DIST-P008", "DIST-P023", "DIST-P024", "DIST-P040", "DIST-P045",
    "DIST-P065", "DIST-P066", "DIST-P075", "DIST-P085", "DIST-P086", "DIST-P096",
}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path.name} must contain a JSON object")
    return payload


def audit_window(repo_root: str | Path = ".") -> dict[str, Any]:
    root = Path(repo_root).resolve()
    x01 = _load_json(root / "DIST_X01_PROMISE_LEDGER.json")
    x02 = _load_json(root / "DIST_X02_PROMISE_LEDGER.json")
    exported = _load_json(root / "DIST_X02_EXPORTED_OBLIGATIONS_TO_DIST_G1.json")

    if x01.get("roadmap_id") != "DIST-X01" or x01.get("position") != "40/70":
        raise RuntimeError("DIST-X01 ledger identity/position mismatch")
    if x02.get("roadmap_id") != "DIST-X02" or x02.get("position") != "41/70":
        raise RuntimeError("DIST-X02 ledger identity/position mismatch")
    obligations = exported.get("obligations")
    if not isinstance(obligations, list) or len(obligations) < 10:
        raise RuntimeError("DIST-X02 did not export the authoritative G1 obligation set")

    x01_delivered = {str(value) for value in x01.get("delivered", ())}
    x02_delivered = {str(value) for value in x02.get("delivered", ())}
    x01_partial = {str(key) for key in dict(x01.get("partial") or {})}
    if not X01_REQUIRED_GATE_PARTIALS.issubset(x01_partial):
        missing = sorted(X01_REQUIRED_GATE_PARTIALS - x01_partial)
        raise RuntimeError("DIST-X01 gate partials missing from ledger: " + ", ".join(missing))

    x02_partial = {str(key) for key in dict(x02.get("partial") or {})}
    if not GATE_EXPORTED_TO_LATER_DIST.issubset(x02_partial):
        missing = sorted(GATE_EXPORTED_TO_LATER_DIST - x02_partial)
        raise RuntimeError("DIST-X02 later-owner partials missing from ledger: " + ", ".join(missing))

    closed = x01_delivered | x02_delivered | X01_REQUIRED_GATE_PARTIALS
    return {
        "schema_version": 1,
        "roadmap_id": "DIST-G1",
        "position": "42/70",
        "window": ["DIST-X01", "DIST-X02", "DIST-G1"],
        "closed_promise_count": len(closed),
        "closed_promises": sorted(closed, key=lambda value: int(value.split("P", 1)[1])),
        "later_dist_partial_promises": sorted(
            GATE_EXPORTED_TO_LATER_DIST, key=lambda value: int(value.split("P", 1)[1])
        ),
        "exported_obligation_count": len(obligations),
        "known_preexisting_baseline": list(x02.get("known_preexisting_baseline") or ()),
    }


def _link() -> DelegationLink:
    return DelegationLink(
        run_link=ExecutionRunLink("dist-g1-parent", "federation:proof", "dist-g1-child"),
        repository="proof-child",
        target="proof",
        child_contract="workflow:proof",
        contract_ref="proof-child::proof::workflow:proof",
        federation_ref="proof-child::proof::proof",
        repository_fingerprint="dist-g1-repository",
        source_lock_digest="dist-g1-lock",
        source_equivalence_digest="dist-g1-equivalence",
        git_lineage_digest="dist-g1-git-lineage",
        provider_identity={
            "provider_id": "dist-g1-loopback",
            "provider_kind": "local",
            "execution_environment": "host",
            "provider_evidence_digest": "dist-g1-provider-evidence",
        },
        attestation_digest="dist-g1-attestation",
    )


def _complete(event: Any, sequence: int) -> Any:
    event.complete_envelope(sequence=sequence)
    return event


def _write_journal(state: Path) -> None:
    state.mkdir(parents=True, exist_ok=True)
    events = [
        _complete(RunStarted(run_id="dist-g1-child", command="proof", source="dist-g1"), 1),
        _complete(
            NodeDiscovered(
                run_id="dist-g1-child",
                node_id="proof-node",
                label="proof-node",
                kind="job",
                source="dist-g1",
            ),
            2,
        ),
    ]
    (state / "execution-events.jsonl").write_text(
        "\n".join(json.dumps(event_to_dict(event), sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )


def _stage_one(state: Path) -> dict[str, Any]:
    link = _link()
    receiver = DelegatedEventReceiver(ExternalEventIngestor(EventBus()), link=link)
    loopback = LocalLoopbackTransport(receiver)
    store = DeliveryCursorStore(state / ".devtool-dist" / "cursor.json")
    forwarder = AsyncJournalForwarder(
        link=link,
        journal=OriginRunJournal(state, child_run_id=link.child_run_id, link=link),
        cursor_store=store,
        send_frame=loopback.send,
    )
    sent = forwarder.pump_once(delivery_mode="live", limit=1)
    cursor = store.load(link=link)
    if sent != 1 or cursor.acknowledged_sequence != 1:
        raise RuntimeError("stage one did not persist origin sequence 1")
    if not cursor.acknowledged_event_id or not cursor.acknowledged_content_digest:
        raise RuntimeError("stage one did not persist replay-integrity boundary")
    return {
        "stage": 1,
        "acknowledged_sequence": cursor.acknowledged_sequence,
        "acknowledged_event_id": cursor.acknowledged_event_id,
        "acknowledged_content_digest": cursor.acknowledged_content_digest,
    }


def _stage_two(state: Path) -> dict[str, Any]:
    link = _link()
    store = DeliveryCursorStore(state / ".devtool-dist" / "cursor.json")
    cursor = store.load(link=link)
    if cursor.acknowledged_sequence != 1:
        raise RuntimeError("fresh process did not restore acknowledged sequence 1")
    destination = EventBus()
    receiver = DelegatedEventReceiver.from_cursor(
        ExternalEventIngestor(destination), link=link, cursor=cursor
    )
    loopback = LocalLoopbackTransport(receiver)
    journal = OriginRunJournal(state, child_run_id=link.child_run_id, link=link)
    frames = journal.frames_after(0, delivery_mode="catch-up")
    if len(frames) != 2:
        raise RuntimeError("fresh-process proof journal does not contain two frames")

    duplicate = receiver.receive(frames[0])
    if not duplicate.duplicate or duplicate.acknowledged_sequence != 1:
        raise RuntimeError("fresh receiver did not verify identical boundary replay")

    original = frames[0]
    envelope = original.semantic_envelope
    if envelope is None:
        raise RuntimeError("stage one proof frame unexpectedly lacks semantic envelope")
    conflicting_payload = dict(envelope.payload)
    conflicting_payload["command"] = "conflicting-proof-command"
    conflicting_envelope = EventTransportEnvelope(
        delegation_id=envelope.delegation_id,
        child_run_id=envelope.child_run_id,
        producer_id=envelope.producer_id,
        event_id=envelope.event_id,
        sequence=envelope.sequence,
        payload=conflicting_payload,
        payload_digest=canonical_event_payload_digest(conflicting_payload),
        delivery_mode="catch-up",
        provider_identity=envelope.provider_identity,
        attestation_digest=envelope.attestation_digest,
    )
    conflicting = TransportFrame(
        delegation_id=original.delegation_id,
        child_run_id=original.child_run_id,
        event_id=original.event_id,
        sequence=original.sequence,
        content_class=original.content_class,
        semantic_envelope=conflicting_envelope,
    )
    try:
        receiver.receive(conflicting)
    except ExternalEventEquivocationError:
        pass
    else:
        raise RuntimeError("fresh receiver accepted same-sequence/different-body replay")

    forwarder = AsyncJournalForwarder(
        link=link,
        journal=journal,
        cursor_store=store,
        send_frame=loopback.send,
    )
    sent = forwarder.pump_once(delivery_mode="catch-up")
    final_cursor = store.load(link=link)
    if sent != 1 or final_cursor.acknowledged_sequence != 2:
        raise RuntimeError("fresh sender/receiver did not resume from exact next origin sequence")
    return {
        "stage": 2,
        "verified_duplicate": True,
        "verified_equivocation_rejection": True,
        "sent_after_restart": sent,
        "acknowledged_sequence": final_cursor.acknowledged_sequence,
    }


def _run_stage(stage: int, state: Path) -> dict[str, Any]:
    if stage == 1:
        return _stage_one(state)
    if stage == 2:
        return _stage_two(state)
    raise ValueError(stage)


def run_real_process_proof() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="devtool-dist-g1-") as raw:
        state = Path(raw)
        _write_journal(state)
        results: list[dict[str, Any]] = []
        for stage in (1, 2):
            proc = subprocess.run(
                [sys.executable, "-m", "devtool.dist_g1_gate", "--stage", str(stage), "--state", str(state)],
                cwd=str(Path.cwd()),
                env=dict(os.environ),
                text=True,
                capture_output=True,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"DIST-G1 fresh-process stage {stage} failed: "
                    f"{proc.stderr.strip() or proc.stdout.strip()}"
                )
            try:
                payload = json.loads(proc.stdout.strip().splitlines()[-1])
            except Exception as exc:
                raise RuntimeError(f"DIST-G1 fresh-process stage {stage} returned invalid JSON") from exc
            results.append(payload)
        return {
            "schema_version": 1,
            "kind": "dist-g1-fresh-process-proof",
            "stages": results,
            "ok": True,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", type=int, choices=(1, 2))
    parser.add_argument("--state")
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)

    if args.stage:
        if not args.state:
            parser.error("--state is required with --stage")
        print(json.dumps(_run_stage(args.stage, Path(args.state)), sort_keys=True))
        return 0

    result = {
        "audit": audit_window(args.repo),
        "real_process": run_real_process_proof(),
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
