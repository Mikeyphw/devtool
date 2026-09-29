from __future__ import annotations

"""Operational EXEC contract adoption primitives (EXEC-X04).

This module is deliberately generic.  It gives local workflows, native runner
frontends, transaction/apply validation, federation resume, roadmap gates and
remote/provider executions one shared projection for reuse/rerun/fresh/materialize
policy without becoming the owner of transport, rollback, presentation, Gradle
planning, pytest lane layout, or roadmap promise policy.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence
import hashlib
import json
import threading

from .models import CommandSpec
from .core.reuse import annotate_spec_reuse, fingerprint_for_spec, stable_digest
from .core.reuse_planner import ReusePlan, ReusePlanEntry, ReusePlanner


EXEC_X04_VERSION = "exec-x04-operational-adoption-v1"


@dataclass(frozen=True, slots=True)
class OperationalContext:
    """Typed caller context that preserves UX/policy while sharing EXEC predicates."""

    domain: str
    operation: str
    target: str = ""
    provider: str = "local"
    repository_id: str = ""
    workflow_id: str = ""
    transaction_id: str = ""
    validation_mode: str = "required"
    fresh: bool = False
    rerun: bool = False
    rerun_from: str = ""
    selected_tests: tuple[str, ...] = ()
    device_identity: str = ""
    remote_identity: str = ""
    policy: Mapping[str, Any] = field(default_factory=dict)

    def identity_payload(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "operation": self.operation,
            "target": self.target,
            "provider": self.provider,
            "repository_id": self.repository_id,
            "workflow_id": self.workflow_id,
            "transaction_id": self.transaction_id,
            "validation_mode": self.validation_mode,
            "fresh": self.fresh,
            "rerun": self.rerun,
            "rerun_from": self.rerun_from,
            "selected_tests": list(self.selected_tests),
            "device_identity": self.device_identity,
            "remote_identity": self.remote_identity,
            "policy": dict(sorted((self.policy or {}).items())),
        }


@dataclass(frozen=True, slots=True)
class SourceProofRef:
    run_id: str
    node_id: str
    evidence_digest: str
    pinned: bool = False
    unavailable_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "node_id": self.node_id,
            "evidence_digest": self.evidence_digest,
            "pinned": self.pinned,
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True, slots=True)
class OperationalDecision:
    context: OperationalContext
    spec_name: str
    node_definition_id: str
    disposition: str
    reason: str
    validation_proof_eligible: bool
    source_proof: SourceProofRef | None = None
    materialized: bool = False
    changed_predicates: tuple[str, ...] = ()
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "context": self.context.identity_payload(),
            "spec_name": self.spec_name,
            "node_definition_id": self.node_definition_id,
            "disposition": self.disposition,
            "reason": self.reason,
            "validation_proof_eligible": self.validation_proof_eligible,
            "source_proof": self.source_proof.as_dict() if self.source_proof else None,
            "materialized": self.materialized,
            "changed_predicates": list(self.changed_predicates),
            "metrics": dict(self.metrics),
        }


@dataclass(slots=True)
class OperationalAdoptionPlan:
    context: OperationalContext
    decisions: list[OperationalDecision]
    reuse_plan: ReusePlan
    identity_digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": EXEC_X04_VERSION,
            "identity_digest": self.identity_digest,
            "context": self.context.identity_payload(),
            "decisions": [d.as_dict() for d in self.decisions],
            "reuse_plan": self.reuse_plan.to_dict(),
        }


class SourceProofRetentionIndex:
    """Small generic retention ledger for reused/materialized source proofs."""

    def __init__(self) -> None:
        self._refs: dict[tuple[str, str, str], SourceProofRef] = {}

    def pin(self, proof: SourceProofRef) -> SourceProofRef:
        pinned = SourceProofRef(
            run_id=proof.run_id,
            node_id=proof.node_id,
            evidence_digest=proof.evidence_digest,
            pinned=True,
            unavailable_reason=proof.unavailable_reason,
        )
        self._refs[(pinned.run_id, pinned.node_id, pinned.evidence_digest)] = pinned
        return pinned

    def record_unavailable(self, *, run_id: str, node_id: str, reason: str) -> SourceProofRef:
        proof = SourceProofRef(run_id=run_id, node_id=node_id, evidence_digest="", pinned=False, unavailable_reason=reason)
        self._refs[(proof.run_id, proof.node_id, proof.evidence_digest)] = proof
        return proof

    def contains(self, proof: SourceProofRef) -> bool:
        return (proof.run_id, proof.node_id, proof.evidence_digest) in self._refs

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "refs": [ref.as_dict() for ref in self._refs.values()]}


class SingleflightQualificationIndex:
    """In-memory candidate qualification guard keyed by canonical identity."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, str] = {}
        self._counter = 0

    def acquire(self, identity: str) -> tuple[bool, str]:
        with self._lock:
            existing = self._active.get(identity)
            if existing:
                return False, existing
            self._counter += 1
            token = f"qualification-{self._counter}"
            self._active[identity] = token
            return True, token

    def release(self, identity: str, token: str) -> bool:
        with self._lock:
            if self._active.get(identity) != token:
                return False
            del self._active[identity]
            return True


def context_digest(context: OperationalContext) -> str:
    return stable_digest(context.identity_payload())


def prepare_operational_specs(specs: Sequence[CommandSpec], *, repo_root: Path, context: OperationalContext) -> list[CommandSpec]:
    prepared: list[CommandSpec] = []
    for spec in specs:
        metadata = dict(spec.metadata or {})
        metadata.setdefault("exec_contract_version", EXEC_X04_VERSION)
        metadata["exec_context_digest"] = context_digest(context)
        metadata["exec_domain"] = context.domain
        metadata["exec_operation"] = context.operation
        metadata["exec_provider"] = context.provider
        metadata["exec_validation_mode"] = context.validation_mode
        metadata["exec_fresh"] = bool(context.fresh)
        metadata["exec_rerun"] = bool(context.rerun)
        metadata["exec_rerun_from"] = context.rerun_from
        if context.selected_tests:
            metadata["test_selection"] = list(context.selected_tests)
        if context.device_identity:
            metadata["device_identity"] = context.device_identity
        if context.remote_identity:
            metadata["remote_identity"] = context.remote_identity
        domains = dict(metadata.get("environment_domains") or {})
        domains["exec_context"] = context_digest(context)
        if context.device_identity:
            domains["device"] = context.device_identity
        if context.remote_identity:
            domains["remote"] = context.remote_identity
        if context.selected_tests:
            domains["selected_tests"] = stable_digest(list(context.selected_tests))
        metadata["environment_domains"] = domains
        clone = CommandSpec(
            name=spec.name,
            argv=list(spec.argv),
            env=dict(spec.env),
            cwd=spec.cwd,
            destructive=spec.destructive,
            metadata=metadata,
        )
        annotate_spec_reuse(clone, repo_root=repo_root)
        fp, _contribution, _dep = fingerprint_for_spec(clone, repo_root=repo_root)
        clone.metadata["exec_rer_fingerprint"] = fp.to_dict()
        clone.metadata["node_definition_id"] = str(clone.metadata.get("node_definition_id") or fp.node_definition_id)
        prepared.append(clone)
    return prepared


def _proof_eligible(context: OperationalContext, disposition: str) -> bool:
    if str(context.validation_mode or "").lower() in {"none", "disabled", "no-validate", "skip"}:
        return False
    if context.fresh or context.rerun:
        return disposition == "RUN"
    return str(disposition).lower() in {"run", "reuse", "materialize"}


def _source_proof_for(entry: ReusePlanEntry, *, retention: SourceProofRetentionIndex | None) -> SourceProofRef | None:
    if str(entry.disposition).lower() not in {"reuse", "materialize"}:
        return None
    proof = SourceProofRef(
        run_id=entry.source_run_id or "unknown-source-run",
        node_id=entry.node_definition_id,
        evidence_digest=hashlib.sha256(f"{entry.node_definition_id}:{entry.reason}".encode("utf-8")).hexdigest(),
    )
    return retention.pin(proof) if retention else proof


def build_operational_adoption_plan(
    specs: Sequence[CommandSpec],
    *,
    repo_root: Path,
    context: OperationalContext,
    history: Any | None = None,
    retention: SourceProofRetentionIndex | None = None,
) -> OperationalAdoptionPlan:
    prepared = prepare_operational_specs(specs, repo_root=repo_root, context=context)
    planner = ReusePlanner(history_index=history, repo_root=repo_root)
    reuse_plan = planner.plan_specs(prepared)
    prepared_by_name = {spec.name: spec for spec in prepared}
    decisions: list[OperationalDecision] = []
    for entry in reuse_plan.entries:
        disposition = str(entry.disposition).lower()
        reason = entry.reason
        changed: tuple[str, ...] = ()
        if context.fresh:
            disposition = "run"
            reason = "explicit fresh policy overrides reusable evidence"
            changed = ("fresh",)
        elif context.rerun or (context.rerun_from and entry.spec_name >= context.rerun_from):
            disposition = "run"
            reason = "explicit rerun policy overrides reusable evidence"
            changed = tuple(v for v in ("rerun", context.rerun_from and "rerun_from") if v)
        proof = _source_proof_for(entry, retention=retention) if disposition in {"reuse", "materialize"} else None
        spec = prepared_by_name.get(entry.spec_name)
        metrics = {
            "argv_count": len(spec.argv) if spec else 0,
            "avoided_work": disposition in {"reuse", "materialize"},
            "hashing_policy": "node-scoped",
        }
        decisions.append(OperationalDecision(
            context=context,
            spec_name=entry.spec_name,
            node_definition_id=entry.node_definition_id,
            disposition=disposition,
            reason=reason,
            validation_proof_eligible=_proof_eligible(context, disposition),
            source_proof=proof,
            materialized=disposition == "materialize",
            changed_predicates=changed,
            metrics=metrics,
        ))
    digest_payload = {"context": context.identity_payload(), "decisions": [d.as_dict() for d in decisions]}
    return OperationalAdoptionPlan(context=context, decisions=decisions, reuse_plan=reuse_plan, identity_digest=stable_digest(digest_payload))


def explain_decision(decision: OperationalDecision) -> dict[str, Any]:
    why = "why-reuse" if str(decision.disposition).lower() in {"reuse", "materialize"} else "why-rerun"
    return {
        "schema_version": 1,
        "kind": why,
        "domain": decision.context.domain,
        "operation": decision.context.operation,
        "spec_name": decision.spec_name,
        "node_definition_id": decision.node_definition_id,
        "disposition": decision.disposition,
        "reason": decision.reason,
        "changed_predicates": list(decision.changed_predicates),
        "source_proof": decision.source_proof.as_dict() if decision.source_proof else None,
        "metrics": dict(decision.metrics),
    }


def diff_operational_plans(before: OperationalAdoptionPlan, after: OperationalAdoptionPlan) -> dict[str, Any]:
    before_by_name = {d.spec_name: d for d in before.decisions}
    after_by_name = {d.spec_name: d for d in after.decisions}
    rows: list[dict[str, Any]] = []
    for name in sorted(set(before_by_name) | set(after_by_name)):
        old = before_by_name.get(name)
        new = after_by_name.get(name)
        rows.append({
            "spec_name": name,
            "before": old.disposition if old else "absent",
            "after": new.disposition if new else "absent",
            "identity_changed": (old.node_definition_id if old else "") != (new.node_definition_id if new else ""),
            "presentation_only": False,
        })
    return {"schema_version": 1, "kind": "exec-operational-diff", "rows": rows}


def rebuild_reuse_index_from_history(history_payloads: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    for payload in history_payloads:
        for row in payload.get("decisions", []) if isinstance(payload, Mapping) else []:
            if not isinstance(row, Mapping):
                continue
            node_id = str(row.get("node_definition_id") or "")
            if node_id:
                records[node_id] = {
                    "disposition": str(row.get("disposition") or ""),
                    "source_proof": row.get("source_proof"),
                    "reason": str(row.get("reason") or ""),
                }
    return {"schema_version": 1, "kind": "rebuildable-reuse-index", "records": records}
