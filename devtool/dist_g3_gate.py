from __future__ import annotations

"""DIST-G3 final distributed-execution seal.

The seal derives closure from executable DIST/RUN/provider evidence.  It does
not create a scheduler, RunStore, provider registry, artifact authority, test
truth store, or presentation truth model.
"""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from .core.events import EventBus, NodeDiscovered, RunFinished, RunStarted
from .evidence_resolver import EvidenceResolver
from .core.events.listeners import event_to_dict
from .dist_g2_gate import run_parity_proof
from .distributed_execution import DelegationLink, ExecutionRunLink, ExternalEventIngestor
from .distributed_operational_adoption import (
    PROVIDERS,
    backpressure_policy,
    delivery_health,
    doctor_links,
    dogfood_matrix,
    historical_child_projection,
    machine_child_receipt,
    obligation_disposition,
    opaque_safe_event,
    provider_observation_migration,
    retention_projection,
)
from .distributed_provider_control import run_relation_from_delegation
from .distributed_transport import (
    AsyncJournalForwarder,
    DelegatedEventReceiver,
    DeliveryCursorStore,
    LocalLoopbackTransport,
    OriginRunJournal,
)
from .run_store import RunCatalog
from .ui_core.machine import machine_state_from_runtime
from .ui_core.viewmodels import runtime_state_to_ui_snapshot

ALLOWED_SOURCE_DISPOSITIONS = {
    "SATISFIED_BY_DIST",
    "SUPERSEDED_BY_DIST",
    "EXPORTED_TO",
    "ALREADY_SATISFIED_BY_RETAINED_SSH_FRX_SUBSTRATE",
    "RETIRED",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path.name} must contain a JSON object")
    return value


def audit_source_dispositions(repo_root: str | Path = ".") -> dict[str, Any]:
    root = Path(repo_root).resolve()
    ledger = _load(root / "DIST_G3_SOURCE_DISPOSITION_LEDGER.json")
    promises = ledger.get("promises") if isinstance(ledger.get("promises"), list) else []
    regressions = ledger.get("regressions") if isinstance(ledger.get("regressions"), list) else []
    expected_promises = [f"NEST-P{i:03d}" for i in range(1, 85)]
    expected_regressions = [f"NEST-R{i:03d}" for i in range(1, 158)]
    promise_ids = [str(row.get("id") or "") for row in promises if isinstance(row, dict)]
    regression_ids = [str(row.get("id") or "") for row in regressions if isinstance(row, dict)]
    if promise_ids != expected_promises:
        raise RuntimeError("DIST-G3 NEST promise ledger is incomplete or out of order")
    if regression_ids != expected_regressions:
        raise RuntimeError("DIST-G3 NEST regression ledger is incomplete or out of order")
    unresolved = []
    concrete_rows: list[dict[str, Any]] = []
    for row in [*promises, *regressions]:
        if not isinstance(row, dict):
            unresolved.append("<invalid-row>")
            continue
        disposition = str(row.get("disposition") or "")
        if disposition not in ALLOWED_SOURCE_DISPOSITIONS:
            unresolved.append(str(row.get("id") or "<missing-id>"))
        evidence = row.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            unresolved.append(str(row.get("id") or "<missing-id>"))
        concrete_rows.append(row)
    if unresolved:
        raise RuntimeError("DIST-G3 unmapped source obligations: " + ", ".join(unresolved[:12]))
    resolver = EvidenceResolver(root)
    evidence_results = resolver.require_all(resolver.disposition_evidence_rows(concrete_rows), execute_pytest=False)
    return {
        "schema_version": 1,
        "kind": "dist-g3-source-disposition-audit",
        "promise_count": len(promises),
        "regression_count": len(regressions),
        "unmapped": 0,
        "ok": True,
    }


def audit_historical_substrate(repo_root: str | Path = ".") -> dict[str, Any]:
    root = Path(repo_root).resolve()
    payload = _load(root / "DIST_G3_HISTORICAL_SUBSTRATE_CROSSWALK.json")
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    required = {"SSH-01", "SSH-02", "SSH-03", "FRX", "GIT-FRX"}
    observed = {str(row.get("authority") or "") for row in rows if isinstance(row, dict)}
    if observed != required:
        raise RuntimeError(f"DIST-G3 historical authority crosswalk mismatch: {sorted(observed)}")
    for row in rows:
        if row.get("disposition") != "ALREADY_SATISFIED_BY_RETAINED_SSH_FRX_SUBSTRATE":
            raise RuntimeError(f"historical authority {row.get('authority')} is not retained substrate")
        for evidence in row.get("evidence") or ():
            if str(evidence).endswith((".md", ".py")) and not (root / str(evidence)).exists():
                raise RuntimeError(f"historical substrate evidence missing: {evidence}")
    return {"schema_version": 1, "authorities": sorted(observed), "ok": True}


def audit_dist_promises(repo_root: str | Path = ".") -> dict[str, Any]:
    root = Path(repo_root).resolve()
    payload = _load(root / "DIST_G3_PROMISE_LEDGER.json")
    rows = payload.get("promise_rows") if isinstance(payload.get("promise_rows"), list) else []
    expected = [f"DIST-P{i:03d}" for i in range(1, 105)]
    ids = [str(row.get("id") or "") for row in rows if isinstance(row, dict)]
    if ids != expected:
        raise RuntimeError("DIST-G3 promise ledger must explicitly cover DIST-P001..DIST-P104")
    by_id = {str(row["id"]): row for row in rows if isinstance(row, dict) and row.get("id")}
    if by_id["DIST-P100"].get("status") != "REMEDIATED_AFTER_HISTORICAL_DEVIATION":
        raise RuntimeError("DIST-P100 must retain historical-deviation remediation status")
    r01 = _load(root / "DIST_R01_PROMISE_LEDGER.json")
    if r01.get("remediation_status") != "CURRENT_CAMPAIGN_TWO_PHASE_INVARIANT_RESTORED":
        raise RuntimeError("DIST-R01 two-phase self-refresh remediation is not closed")
    historical = _load(root / "DIST_R01_HISTORICAL_ARTIFACT_AUDIT.json")
    if historical.get("historical_deviation_confirmed") is not True:
        raise RuntimeError("DIST-P100 historical deviation evidence was lost")
    return {"schema_version": 1, "promise_count": len(rows), "p100": by_id["DIST-P100"]["status"], "ok": True}


def _link() -> DelegationLink:
    return DelegationLink(
        run_link=ExecutionRunLink("dist-g3-parent", "delegate:child", "dist-g3-child"),
        repository="dist-g3-child-repo",
        target="app",
        child_contract="workflow:seal",
        contract_ref="dist-g3-child-repo::app::workflow:seal",
        federation_ref="dist-g3-child-repo::app::seal",
        repository_fingerprint="dist-g3-repository-fingerprint",
        source_lock_digest="dist-g3-source-lock",
        source_equivalence_digest="dist-g3-source-equivalence",
        git_lineage_digest="dist-g3-git-lineage",
        provider_identity={
            "provider_id": "dist-g3-local",
            "provider_kind": "local",
            "execution_environment": "host",
            "provider_evidence_digest": "dist-g3-provider-evidence",
        },
        attestation_digest="dist-g3-attestation",
    )


def run_end_to_end_proof() -> dict[str, Any]:
    link = _link()
    with tempfile.TemporaryDirectory(prefix="devtool-dist-g3-") as raw:
        root = Path(raw)
        origin = root / "origin"
        origin.mkdir(parents=True, exist_ok=True)
        events = [
            RunStarted(run_id=link.child_run_id, command="seal"),
            NodeDiscovered(run_id=link.child_run_id, node_id="compile", label="Compile", kind="job"),
            RunFinished(run_id=link.child_run_id, status="failed", exit_code=7),
        ]
        for sequence, event in enumerate(events, 1):
            event.complete_envelope(sequence=sequence)
        origin_payloads = [event_to_dict(event) for event in events]
        (origin / "execution-events.jsonl").write_text(
            "\n".join(json.dumps(item, sort_keys=True) for item in origin_payloads) + "\n",
            encoding="utf-8",
        )

        destination = EventBus()
        ingested: list[dict[str, Any]] = []
        destination.subscribe_all(lambda event: ingested.append(event_to_dict(event)))
        receiver = DelegatedEventReceiver(ExternalEventIngestor(destination), link=link)
        transport = LocalLoopbackTransport(receiver)
        forwarder = AsyncJournalForwarder(
            link=link,
            journal=OriginRunJournal(origin, child_run_id=link.child_run_id, link=link),
            cursor_store=DeliveryCursorStore(root / "cursor.json"),
            send_frame=transport.send,
        )
        if forwarder.pump_once(delivery_mode="live") != len(events):
            raise RuntimeError("DIST-G3 did not forward the complete origin semantic journal")
        if [(row["run_id"], row["event_id"], row["sequence"]) for row in ingested] != [
            (row["run_id"], row["event_id"], row["sequence"]) for row in origin_payloads
        ]:
            raise RuntimeError("DIST-G3 semantic event identity changed across transport/ingest")

        snapshot = destination.execution_store.snapshot(link.child_run_id)
        if snapshot is None or snapshot.status != "failed" or int(snapshot.exit_code or 0) != 7:
            raise RuntimeError("DIST-G3 canonical RUN child terminal truth was not preserved")
        receipt = forwarder.receipt(child_snapshot=snapshot, transport_exit_code=7)
        if not receipt.reconciled or receipt.child_status != "failed" or receipt.child_exit_code != 7:
            raise RuntimeError("DIST-G3 terminal reconciliation replaced primary child failure truth")

        catalog = RunCatalog.open(root / "catalog")
        catalog.add_run({"run_id": link.run_link.parent_run_id, "status": "failed"})
        catalog.add_run({"run_id": link.child_run_id, "status": "failed"})
        relation = run_relation_from_delegation(link, delivery_health="reconciled", pinned=True)
        catalog.add_relation(relation)
        catalog.save()
        hierarchy = catalog.parent_progress(link.run_link.parent_run_id)
        if hierarchy["child_count"] != 1 or hierarchy["flattened_child_identity"] is not False:
            raise RuntimeError("DIST-G3 parent hierarchy flattened or lost canonical child identity")

        receipt_projection = machine_child_receipt({
            "run_id": link.child_run_id,
            "parent_run_id": link.run_link.parent_run_id,
            "status": "failed",
            "delivery_health": delivery_health(connected=True, terminal_reconciled=True).as_dict(),
            "receipt": receipt.as_dict(),
            "retention": {"pinned": True},
        })
        federation = {
            "relationship": "coordinator",
            "workflow": "seal",
            "repository": link.repository,
            "target": link.target,
            "federation_ref": link.federation_ref,
            "parent_run_id": link.run_link.parent_run_id,
            "children": [{
                "child_run_id": link.child_run_id,
                "repository": link.repository,
                "target": link.target,
                "workflow": "seal",
                "federation_ref": link.federation_ref,
                "status": "failed",
                "delegation_id": link.delegation_id,
            }],
        }
        runtime = {
            "run_id": link.run_link.parent_run_id,
            "command": "invoke",
            "status": "failed",
            "target": "devtool",
            "federation": federation,
        }
        machine = machine_state_from_runtime(runtime).federation
        tui = runtime_state_to_ui_snapshot(runtime, surface="tui").federation
        desktop = runtime_state_to_ui_snapshot(runtime, surface="desktop").federation
        for label, projection in (("machine", machine), ("tui", tui), ("desktop", desktop)):
            children = projection.get("children") if isinstance(projection, dict) else None
            if not isinstance(children, list) or not children or children[0].get("child_run_id") != link.child_run_id:
                raise RuntimeError(f"DIST-G3 {label} projection lost canonical child run identity")

        historical = historical_child_projection({
            "run_id": link.child_run_id,
            "status": "failed",
            "repository": link.repository,
            "target": link.target,
            "workflow": "seal",
            "evidence_digest": receipt.terminal_snapshot_digest,
        })
        if historical["live_replay"] is not False or historical["run_id"] != link.child_run_id:
            raise RuntimeError("DIST-G3 reuse projection fabricated live child execution")
        links = doctor_links(run_id=link.run_link.parent_run_id, child_run_id=link.child_run_id)
        if links["child"] != f"run://{link.child_run_id}":
            raise RuntimeError("DIST-G3 drill-down does not resolve canonical child run")

        binding_payload = {"id": link.delegation_id}
        migration = provider_observation_migration({
            "provider_observation": {"legacy": True},
            "provider_binding": binding_payload,
        })
        retention = retention_projection(run_id=link.child_run_id, pinned=True, retained=True, reason="DIST-G3 proof")
        if migration["migration"] != "canonical-provider-binding" or retention["deletion_allowed"]:
            raise RuntimeError("DIST-G3 operational adoption helpers were not consumed by canonical evidence")
        dogfood = dogfood_matrix(PROVIDERS)
        obligations = obligation_disposition((
            {"id": "DIST-X04-history", "owner": "DIST-X04", "source": "dist-g3", "status": "delivered"},
            {"id": "DIST-X04-doctor", "owner": "DIST-X04", "source": "dist-g3", "status": "delivered"},
            {"id": "DIST-X04-migration", "owner": "DIST-X04", "source": "dist-g3", "status": "delivered"},
        ))
        if not dogfood["complete"] or not obligations["complete"]:
            raise RuntimeError("DIST-G3 operational adoption fixed point is incomplete")
        opaque = opaque_safe_event({"event_type": "future.dist.event", "run_id": link.child_run_id}, known_types={"run.started"})
        pressure = backpressure_policy(backlog=0, capacity=1024)

        return {
            "schema_version": 1,
            "kind": "dist-g3-end-to-end-proof",
            "delegation_id": link.delegation_id,
            "child_run_id": link.child_run_id,
            "origin_event_identity": [
                {"run_id": row["run_id"], "event_id": row["event_id"], "sequence": row["sequence"]}
                for row in origin_payloads
            ],
            "ingested_event_identity": [
                {"run_id": row["run_id"], "event_id": row["event_id"], "sequence": row["sequence"]}
                for row in ingested
            ],
            "receipt": {"status": receipt.child_status, "exit_code": receipt.child_exit_code, "reconciled": receipt.reconciled},
            "hierarchy": hierarchy,
            "machine_child_receipt": receipt_projection,
            "provider_observation_migration": migration,
            "provider_binding": binding_payload,
            "retention_projection": retention,
            "dogfood_matrix": dogfood,
            "obligation_disposition": obligations,
            "opaque_safe_event": opaque,
            "backpressure_policy": pressure,
            "surface_child_ids": {
                "machine": machine["children"][0]["child_run_id"],
                "tui": tui["children"][0]["child_run_id"],
                "desktop": desktop["children"][0]["child_run_id"],
            },
            "historical_live_replay": historical["live_replay"],
            "doctor_child": links["child"],
            "ok": True,
        }


def run_fresh_process_proof() -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "devtool.dist_g3_gate", "--stage", "end-to-end"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "DIST-G3 fresh-process proof failed")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def final_seal(repo_root: str | Path = ".") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "roadmap_id": "DIST-G3",
        "position": "66/76",
        "source_dispositions": audit_source_dispositions(repo_root),
        "historical_substrate": audit_historical_substrate(repo_root),
        "dist_promises": audit_dist_promises(repo_root),
        "provider_parity": run_parity_proof(),
        "fresh_process": run_fresh_process_proof(),
        "ok": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("end-to-end",))
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)
    if args.stage == "end-to-end":
        print(json.dumps(run_end_to_end_proof(), sort_keys=True))
        return 0
    print(json.dumps(final_seal(args.repo), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
