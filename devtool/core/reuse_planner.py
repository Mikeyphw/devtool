from __future__ import annotations

"""Pure reusable-execution planning primitives (EXEC-X03).

This module deliberately does not execute work and does not persist truth.  EXO
owns terminal execution history, RER owns qualification/invalidation predicates,
and callers can rebuild these plans from their authoritative inputs.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .execution_semantics import NodeDefinitionIdentity, ReuseDisposition
from .reuse import ReuseHistoryIndex, ReuseQualification, annotate_spec_reuse, qualify_from_history


@dataclass(frozen=True, slots=True)
class ReusePlanEntry:
    node_definition_id: str
    spec_name: str
    disposition: str
    reason: str
    source_run_id: str = ""
    source_node_id: str = ""
    proof_refs: tuple[str, ...] = ()
    fingerprint_digest: str = ""
    check_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def runnable(self) -> bool:
        return self.disposition in {ReuseDisposition.RUN.value, ReuseDisposition.INVALIDATED.value, "run", "invalidated"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_definition_id": self.node_definition_id,
            "spec_name": self.spec_name,
            "disposition": self.disposition,
            "reason": self.reason,
            "source_run_id": self.source_run_id,
            "source_node_id": self.source_node_id,
            "proof_refs": list(self.proof_refs),
            "fingerprint_digest": self.fingerprint_digest,
            "check_id": self.check_id,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ReusePlan:
    entries: tuple[ReusePlanEntry, ...]
    source: str = "rer-exo-history"

    @property
    def runnable_node_ids(self) -> tuple[str, ...]:
        return tuple(entry.node_definition_id for entry in self.entries if entry.runnable)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "entries": [entry.to_dict() for entry in self.entries],
            "runnable_node_ids": list(self.runnable_node_ids),
        }


class ReusePlanner:
    """Build a side-effect-free plan from specs and a derived EXO history index."""

    def __init__(self, history_index: ReuseHistoryIndex | None = None, *, repo_root: Path | None = None) -> None:
        self.history_index = history_index or ReuseHistoryIndex()
        self.repo_root = repo_root

    def plan_specs(self, specs: Sequence[Any]) -> ReusePlan:
        entries: list[ReusePlanEntry] = []
        for spec in specs:
            metadata = dict(getattr(spec, "metadata", {}) or {})
            node_id = str(metadata.get("node_definition_id") or "").strip()
            if not node_id:
                node_id = NodeDefinitionIdentity.from_spec(spec).value
            annotate_spec_reuse(spec, repo_root=self.repo_root, node_definition_id=node_id)
            metadata = dict(getattr(spec, "metadata", {}) or {})
            fingerprint = metadata.get("reuse_fingerprint") if isinstance(metadata.get("reuse_fingerprint"), Mapping) else {}
            qualification: ReuseQualification = qualify_from_history(
                self.history_index,
                fingerprint if isinstance(fingerprint, Mapping) else {},
                cwd=Path(str(getattr(spec, "cwd", "") or self.repo_root or Path.cwd())),
            )
            entries.append(ReusePlanEntry(
                node_definition_id=str(metadata.get("node_definition_id") or node_id),
                spec_name=str(getattr(spec, "name", "") or ""),
                disposition=str(qualification.disposition),
                reason=str(qualification.reason),
                source_run_id=str(qualification.source_run_id),
                source_node_id=str(qualification.source_node_id),
                proof_refs=tuple(qualification.proof_refs),
                fingerprint_digest=str(fingerprint.get("digest") or "") if isinstance(fingerprint, Mapping) else "",
                check_id=str(metadata.get("validation_check_id") or metadata.get("validation_phase_key") or ""),
                metadata={
                    "reuse_fingerprint": dict(fingerprint) if isinstance(fingerprint, Mapping) else {},
                    "reuse_shadow_qualification": dict(metadata.get("reuse_shadow_qualification") or {}) if isinstance(metadata.get("reuse_shadow_qualification"), Mapping) else {},
                },
            ))
        return ReusePlan(tuple(entries))
