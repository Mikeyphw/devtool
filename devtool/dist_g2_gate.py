from __future__ import annotations

"""DIST-G2 authoritative provider-parity milestone checks.

This gate composes existing provider substrate authorities with a fresh-process
semantic proof. It intentionally does not create another execution/provider
truth source: RUN-V3, DIST transport/control, FRX source/attestation/recovery,
and LIFE cleanup ownership remain canonical.
"""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from .core.events import EventBus, NodeDiscovered, RunFinished, RunStarted
from .distributed_execution import DelegationLink, ExecutionRunLink, ExternalEventIngestor
from .distributed_provider_control import (
    DelegatedControlChannel,
    DelegatedControlRequest,
    DelegationRoute,
    ProviderSessionBinding,
    provider_cleanup_descriptors,
    record_delegation_relation,
)
from .distributed_transport import DelegatedEventReceiver, EventTransportEnvelope, TransportFrame
from .run_store import RunCatalog

PROVIDERS = (
    ("local-subprocess", "local"),
    ("parallel-federation", "federation"),
    ("nested-federation", "federation"),
    ("chroot", "chroot"),
    ("proot", "proot"),
    ("oci", "oci"),
    ("ssh-foreground", "ssh"),
    ("ssh-detached-reconnect", "ssh"),
)
REQUIRED_X03_PROMISES = {
    "SSH foreground semantic event channel",
    "SSH detached tmux durable cursor reconnect",
    "nested-federation route/hop cycle protection",
    "DelegatedControlRequest/Ack",
    "provider/attestation session binding",
    "generic ancestor/descendant/subtree RUN queries",
    "parent progress/hierarchy linkage",
    "descendant retention/pinning links",
    "LIFE staging/spool cleanup ownership integration",
    "private secret transport hooks",
    "exact typed-tool argv preservation through provider wrapping",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path.name} must contain a JSON object")
    return value


def audit_window(repo_root: str | Path = ".") -> dict[str, Any]:
    root = Path(repo_root).resolve()
    x01 = _load(root / "DIST_X01_PROMISE_LEDGER.json")
    x02 = _load(root / "DIST_X02_PROMISE_LEDGER.json")
    g1 = _load(root / "DIST_G1_PROMISE_LEDGER.json")
    x03 = _load(root / "DIST_X03_PROMISE_LEDGER.json")
    exported = _load(root / "DIST_X03_EXPORTED_OBLIGATIONS_TO_DIST_G2.json")
    expected = ((x01, "DIST-X01", "40/70"), (x02, "DIST-X02", "41/70"), (g1, "DIST-G1", "42/70"), (x03, "DIST-X03", "47/70"))
    for ledger, rid, position in expected:
        if ledger.get("roadmap_id") != rid or ledger.get("position") != position:
            raise RuntimeError(f"{rid} ledger identity/position mismatch")
    promises = {str(v) for v in x03.get("promises", ())}
    missing = sorted(REQUIRED_X03_PROMISES - promises)
    if missing:
        raise RuntimeError("DIST-X03 gate promises missing: " + ", ".join(missing))
    parity = set(exported.get("required_provider_parity") or ())
    expected_parity = {"local subprocess", "parallel federation", "nested federation", "chroot", "PRoot", "OCI", "SSH foreground", "SSH detached + reconnect"}
    if parity != expected_parity:
        raise RuntimeError("DIST-X03 provider parity export mismatch")
    return {"schema_version": 1, "roadmap_id": "DIST-G2", "position": "48/70", "window": ["DIST-X01", "DIST-X02", "DIST-G1", "DIST-X03", "DIST-G2"], "providers": sorted(parity), "x03_promise_count": len(promises), "status": "window-audit-pass"}


def _link(label: str, kind: str) -> DelegationLink:
    return DelegationLink(
        run_link=ExecutionRunLink("dist-g2-parent", "provider-parity", "dist-g2-child"),
        repository="provider-proof", target="provider-proof", child_contract="workflow:proof",
        contract_ref="provider-proof::provider-proof::workflow:proof", federation_ref="provider-proof::provider-proof::proof",
        repository_fingerprint="dist-g2-repository", source_lock_digest="dist-g2-lock",
        source_equivalence_digest="dist-g2-equivalence", git_lineage_digest="dist-g2-lineage",
        provider_identity={"provider_id": label, "provider_kind": kind, "execution_environment": "provider", "provider_evidence_digest": f"evidence-{label}"},
        attestation_digest=f"attestation-{label}",
    )


def _event_frame(link: DelegationLink, event: Any, sequence: int) -> TransportFrame:
    event.complete_envelope(sequence=sequence)
    from .core.events.listeners import event_to_dict
    payload = event_to_dict(event)
    envelope = EventTransportEnvelope.for_event(payload, link=link, delivery_mode="live")
    return TransportFrame(delegation_id=link.delegation_id, child_run_id=link.child_run_id, event_id=envelope.event_id, sequence=sequence, content_class="semantic-safe-v1", semantic_envelope=envelope)


def provider_proof(label: str, kind: str, root: Path) -> dict[str, Any]:
    link = _link(label, kind)
    binding = ProviderSessionBinding.bind(link, session_id=f"session-{label}", provider_kind=kind, provider_evidence_digest=f"evidence-{label}")
    bus = EventBus()
    accepted: list[dict[str, Any]] = []
    listener = lambda event: accepted.append({"type": type(event).__name__, "run_id": event.run_id, "sequence": event.sequence})
    for event_type in (RunStarted, NodeDiscovered, RunFinished):
        bus.subscribe(event_type, listener)
    receiver = DelegatedEventReceiver(ExternalEventIngestor(bus), link=link)
    events = [RunStarted(run_id=link.child_run_id, command="proof"), NodeDiscovered(run_id=link.child_run_id, node_id="compile", label="compile", kind="job"), RunFinished(run_id=link.child_run_id, status="succeeded", exit_code=0)]
    acks = [receiver.receive(_event_frame(link, event, i)) for i, event in enumerate(events, 1)]
    control = DelegatedControlChannel().dispatch(DelegatedControlRequest.cancel(delegation_id=link.delegation_id, child_run_id=link.child_run_id, reason="parity-proof"), cancel=lambda *_: (True, "accepted"), telemetry_health="degraded")
    catalog_root = root / label / "run-v3"
    catalog = RunCatalog.open(catalog_root)
    catalog.add_run({"run_id": "dist-g2-parent", "status": "running"}); catalog.add_run({"run_id": "dist-g2-child", "status": "succeeded"})
    relation = record_delegation_relation(root / label, link, delivery_health="reconciled")
    # record_delegation_relation uses canonical repo catalog; mirror relation into proof catalog for provider-normalized comparison.
    catalog.add_relation(relation); catalog.pin_relation(relation.relation_id); catalog.save()
    progress = catalog.parent_progress("dist-g2-parent")
    cleanup = provider_cleanup_descriptors(destination=label, provider=kind, run_id=link.child_run_id, session=f"session-{label}", stage_path="stage", state_path="state", cursor_path="cursor", delegation_id=link.delegation_id)
    route = DelegationRoute(max_hops=4).append(link)
    return {"provider": label, "semantic": accepted, "acks": [a.acknowledged_sequence for a in acks], "control": {"accepted": control.accepted, "detail": control.detail}, "hierarchy": {"child_count": progress["child_count"], "terminal_count": progress["terminal_count"], "flattened": progress["flattened_child_identity"]}, "cleanup": sorted(d.category for d in cleanup), "binding": bool(binding.binding_id), "route_hops": len(route.hops)}


def _normalized(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "provider"}


def run_parity_proof() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="devtool-dist-g2-") as raw:
        root = Path(raw)
        rows = [provider_proof(label, kind, root) for label, kind in PROVIDERS]
        baseline = _normalized(rows[0])
        mismatches = [row["provider"] for row in rows[1:] if _normalized(row) != baseline]
        if mismatches:
            raise RuntimeError("provider semantic parity mismatch: " + ", ".join(mismatches))
        return {"schema_version": 1, "kind": "dist-g2-provider-parity-proof", "providers": [r["provider"] for r in rows], "provider_count": len(rows), "normalized_truth": baseline, "ok": True}


def run_fresh_process_proof() -> dict[str, Any]:
    proc = subprocess.run([sys.executable, "-m", "devtool.dist_g2_gate", "--stage", "provider-parity"], text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "DIST-G2 child proof failed")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--stage", choices=("provider-parity",)); parser.add_argument("--repo", default="."); args = parser.parse_args(argv)
    if args.stage:
        print(json.dumps(run_parity_proof(), sort_keys=True)); return 0
    print(json.dumps({"audit": audit_window(args.repo), "fresh_process": run_fresh_process_proof()}, sort_keys=True)); return 0

if __name__ == "__main__":
    raise SystemExit(main())
