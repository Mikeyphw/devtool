from __future__ import annotations

"""EXEC-G3 final execution / reuse / validation seal.

This gate keeps execution truth in the canonical EXO/RUN/reuse authorities.  It
only audits retained EXEC promises and exercises final mixed-provenance and
lifecycle-closure acceptance; it does not introduce another scheduler or run
store.
"""

import argparse
import json
from pathlib import Path
import tempfile
from typing import Any

from .core.events import EventBus, NodeDiscovered, NodeFinished, NodeStarted, RunStarted
from .core.execution_lifecycle import audit_execution_lifecycle, finalize_execution_run
from .core.execution_subscribers import ExecutionProjectionSubscriber
from .core.reuse import ReuseHistoryIndex, result_identity, stable_digest
from .exec_operational_adoption import (
    OperationalContext,
    SourceProofRetentionIndex,
    build_operational_adoption_plan,
    prepare_operational_specs,
)
from .models import CommandSpec


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path.name} must contain a JSON object")
    return value


def audit_exec_promises(repo_root: str | Path = ".") -> dict[str, Any]:
    root = Path(repo_root).resolve()
    x04 = _load(root / "EXEC_X04_PROMISE_LEDGER.json")
    covered = set(str(v) for v in x04.get("retained_from_previous_gates") or ())
    covered.update(str(v) for v in x04.get("satisfied_by_implementation") or ())
    expected = {f"EXEC-P{i:03d}" for i in range(1, 82)}
    missing = sorted(expected - covered)
    if missing:
        raise RuntimeError("EXEC-G3 retained EXEC-P001..P081 coverage gap: " + ", ".join(missing))

    # EXEC-P082 is an explicitly named X01 validation-policy promise in retained
    # evidence.  Do not invent meanings for the historical range-only P083..P087
    # references: final closure is expressed as gate obligations below instead.
    x01_report = (root / "EXEC_X01_EXECUTION_VALIDATION_FOUNDATION_REPORT.md").read_text(encoding="utf-8")
    if "EXEC-P082" not in x01_report:
        raise RuntimeError("EXEC-G3 retained EXEC-P082 evidence is missing")

    exports = _load(root / "EXEC_X04_EXPORTED_OBLIGATIONS_20260929.json").get("exports") or []
    rows = [row for row in exports if isinstance(row, dict) and row.get("owner") == "EXEC-G3"]
    if len(rows) != 1:
        raise RuntimeError("EXEC-G3 must have exactly one retained X04 gate obligation")
    obligation = str(rows[0].get("obligation") or "")
    required_phrases = (
        "mixed executed/reused/materialized graph",
        "late-failure selective reuse",
        "same-run interruption continuation",
        "final closure disposition",
    )
    missing_phrases = [phrase for phrase in required_phrases if phrase not in obligation]
    if missing_phrases:
        raise RuntimeError("EXEC-G3 retained obligation drift: " + ", ".join(missing_phrases))

    return {
        "schema_version": 1,
        "kind": "exec-g3-promise-audit",
        "explicit_promises": 82,
        "x04_gate_obligations": list(required_phrases),
        "unmapped": 0,
        "ok": True,
    }


def _spec(name: str, root: Path, input_name: str) -> CommandSpec:
    return CommandSpec(
        name,
        ["python", "-c", f"print({name!r})"],
        cwd=str(root),
        metadata={"workflow_inputs": [input_name]},
    )


def _reusable_candidate(spec: CommandSpec, *, run_id: str) -> dict[str, Any]:
    fingerprint = dict(spec.metadata["reuse_fingerprint"])
    output_manifest = {
        "digest": stable_digest({"outputs": [], "evidence": []}),
        "outputs": [],
        "evidence": [],
    }
    return {
        "schema_version": 2,
        "node_definition_id": spec.metadata["node_definition_id"],
        "qualification_complete": True,
        "reusable": True,
        "execution_status": "succeeded",
        "fingerprint": fingerprint,
        "source_run_id": run_id,
        "source_node_id": spec.metadata["node_definition_id"],
        "result_identity": result_identity(
            fingerprint_digest=fingerprint["digest"],
            output_manifest_digest=output_manifest["digest"],
            execution_status="succeeded",
            evidence_refs=(f"run:{run_id}",),
        ),
        "output_manifest": output_manifest,
        "evidence_refs": [f"run:{run_id}"],
    }


def _materialize_candidate(spec: CommandSpec) -> dict[str, Any]:
    fingerprint = dict(spec.metadata["reuse_fingerprint"])
    ref = "artifact:sha256:" + "a" * 64
    output_manifest = {
        "digest": stable_digest({"entries": [], "evidence": [], "materialization_refs": [ref]}),
        "entries": [{"path": "missing.bin", "kind": "file", "digest": "missing", "exists": True}],
        "evidence": [],
        "materialization_refs": [ref],
        "complete": True,
    }
    return {
        "schema_version": 2,
        "node_definition_id": spec.metadata["node_definition_id"],
        "qualification_complete": True,
        "reusable": True,
        "execution_status": "succeeded",
        "fingerprint": fingerprint,
        "source_run_id": "exec-g3-materialized-source",
        "source_node_id": spec.metadata["node_definition_id"],
        "result_identity": result_identity(
            fingerprint_digest=fingerprint["digest"],
            output_manifest_digest=output_manifest["digest"],
            execution_status="succeeded",
            evidence_refs=(ref,),
        ),
        "output_manifest": output_manifest,
        "evidence_refs": [ref],
    }


def run_mixed_provenance_proof() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="devtool-exec-g3-mixed-") as raw:
        root = Path(raw)
        for name in ("execute.txt", "reuse.txt", "materialize.txt"):
            (root / name).write_text("v1\n", encoding="utf-8")
        context = OperationalContext(
            domain="roadmap",
            operation="exec-g3-final-seal",
            target="devtool",
            workflow_id="roadmap:exec-g3",
        )
        specs = [
            _spec("executed", root, "execute.txt"),
            _spec("reused", root, "reuse.txt"),
            _spec("materialized", root, "materialize.txt"),
        ]
        prepared = prepare_operational_specs(specs, repo_root=root, context=context)
        history = ReuseHistoryIndex()
        history.add(_reusable_candidate(prepared[1], run_id="exec-g3-reuse-source"))
        history.add(_materialize_candidate(prepared[2]))
        retention = SourceProofRetentionIndex()

        first = build_operational_adoption_plan(
            specs, repo_root=root, context=context, history=history, retention=retention
        )
        first_by_name = {row.spec_name: row for row in first.decisions}
        observed = {name: first_by_name[name].disposition for name in first_by_name}
        expected = {"executed": "run", "reused": "reuse", "materialized": "materialize"}
        if observed != expected:
            raise RuntimeError(f"EXEC-G3 mixed provenance mismatch: {observed!r}")
        if not first_by_name["reused"].source_proof or not first_by_name["reused"].source_proof.pinned:
            raise RuntimeError("EXEC-G3 reused source proof was not retained")
        if not first_by_name["materialized"].source_proof or not first_by_name["materialized"].source_proof.pinned:
            raise RuntimeError("EXEC-G3 materialized source proof was not retained")

        # A late semantic input change may invalidate only its own reusable
        # frontier; unrelated candidate history remains reusable/materializable.
        (root / "reuse.txt").write_text("v2\n", encoding="utf-8")
        late = build_operational_adoption_plan(
            specs, repo_root=root, context=context, history=history, retention=retention
        )
        late_by_name = {row.spec_name: row.disposition for row in late.decisions}
        expected_late = {"executed": "run", "reused": "invalidated", "materialized": "materialize"}
        if late_by_name != expected_late:
            raise RuntimeError(f"EXEC-G3 selective invalidation mismatch: {late_by_name!r}")

        return {
            "schema_version": 1,
            "kind": "exec-g3-mixed-provenance-proof",
            "initial": observed,
            "late_input_change": late_by_name,
            "source_proofs_retained": True,
            "ok": True,
        }


def run_finalization_proof() -> dict[str, Any]:
    run_id = "exec-g3-finalization-proof"
    bus = EventBus()
    with tempfile.TemporaryDirectory(prefix="devtool-exec-g3-life-") as raw:
        bus.subscribe_all(ExecutionProjectionSubscriber(store=bus.execution_store, run_id=run_id, directory=Path(raw)))
        bus.publish(RunStarted(run_id=run_id, command="validate", project="devtool"))
        bus.publish(NodeDiscovered(run_id=run_id, node_id="primary.failure", label="primary failure"))
        bus.publish(NodeStarted(run_id=run_id, node_id="primary.failure", label="primary failure"))
        bus.publish(NodeFinished(
            run_id=run_id,
            node_id="primary.failure",
            status="failed",
            exit_code=7,
            failure_kind="validation_failure",
        ))
        bus.publish(NodeDiscovered(
            run_id=run_id,
            node_id="xdm_modern.validate.$runner",
            label="XDM runner",
        ))
        bus.publish(NodeStarted(
            run_id=run_id,
            node_id="xdm_modern.validate.$runner",
            label="XDM runner",
        ))
        bus.publish(NodeDiscovered(
            run_id=run_id,
            node_id="xgo_final_wave12.validate.wave12_final_gate",
            label="XGO final gate",
            depends_on=("primary.failure",),
        ))
        snapshot = finalize_execution_run(run_id, status="failed", exit_code=7, bus=bus)
        audit = audit_execution_lifecycle(run_id, bus=bus)
        if not audit.clean:
            raise RuntimeError(f"EXEC-G3 finalization lifecycle remained dirty: {audit!r}")
        if snapshot.status != "failed" or snapshot.exit_code != 7:
            raise RuntimeError("EXEC-G3 reconciliation replaced primary failure truth")
        if snapshot.node("xdm_modern.validate.$runner").status != "cancelled":
            raise RuntimeError("EXEC-G3 running delegated descendant was not cancelled")
        if snapshot.node("xgo_final_wave12.validate.wave12_final_gate").status != "blocked":
            raise RuntimeError("EXEC-G3 unstarted dependent gate was not blocked")

        return {
            "schema_version": 1,
            "kind": "exec-g3-finalization-proof",
            "run_status": snapshot.status,
            "exit_code": snapshot.exit_code,
            "xdm_descendant": snapshot.node("xdm_modern.validate.$runner").status,
            "xgo_descendant": snapshot.node("xgo_final_wave12.validate.wave12_final_gate").status,
            "audit_clean": audit.clean,
            "ok": True,
        }


def run_gate(repo_root: str | Path = ".") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "exec-g3-final-execution-reuse-validation-seal",
        "promise_audit": audit_exec_promises(repo_root),
        "mixed_provenance": run_mixed_provenance_proof(),
        "finalization": run_finalization_proof(),
        "ok": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    payload = run_gate(args.repo_root)
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
