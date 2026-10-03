from __future__ import annotations

"""REMED-R02A execution scheduler truth adoption.

This module is intentionally small and production-facing: it turns the
EXO-derived RER history index into concrete runner decisions before process
spawn.  It does not own command execution, presentation, or lifecycle state; it
only decides whether the runner may execute, reuse, materialize, or must rerun.
"""

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Sequence
import os
import shutil
import re

from .core.reuse import (
    ReuseHistoryIndex,
    ReuseQualification,
    annotate_spec_reuse,
    content_identity,
    fingerprint_for_spec,
    qualify_candidate,
)
from .logstore import state_dir
from .models import CommandSpec, ProjectConfig


@dataclass(frozen=True, slots=True)
class SchedulerReuseDecision:
    spec_index: int
    spec_name: str
    node_definition_id: str
    disposition: str
    reason: str
    authoritative: bool
    process_executed: bool
    source_run_id: str = ""
    source_node_id: str = ""
    proof_refs: tuple[str, ...] = ()
    fingerprint: Mapping[str, Any] = field(default_factory=dict)
    output_manifest: Mapping[str, Any] = field(default_factory=dict)
    result_identity: str = ""
    materialized_outputs: tuple[str, ...] = ()

    @property
    def terminal_without_spawn(self) -> bool:
        return self.authoritative and self.disposition in {"reuse", "materialize"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "spec_index": self.spec_index,
            "spec_name": self.spec_name,
            "node_definition_id": self.node_definition_id,
            "disposition": self.disposition,
            "reason": self.reason,
            "authoritative": self.authoritative,
            "process_executed": self.process_executed,
            "source_run_id": self.source_run_id,
            "source_node_id": self.source_node_id,
            "proof_refs": list(self.proof_refs),
            "fingerprint": dict(self.fingerprint),
            "output_manifest": dict(self.output_manifest),
            "result_identity": self.result_identity,
            "materialized_outputs": list(self.materialized_outputs),
        }


def execution_history_root() -> Path:
    raw = (
        os.environ.get("DEVTOOL_EXECUTION_HISTORY_DIR")
        or os.environ.get("DEVTOOL_EXEC_HISTORY_DIR")
        or ""
    ).strip()
    return Path(raw).expanduser() if raw else state_dir() / "history"


def execution_projection_root() -> Path:
    raw = (
        os.environ.get("DEVTOOL_EXECUTION_PROJECTION_DIR")
        or os.environ.get("DEVTOOL_EXEC_PROJECTION_DIR")
        or ""
    ).strip()
    return Path(raw).expanduser() if raw else state_dir() / "execution"


def run_projection_directory(run_id: str) -> Path:
    return execution_projection_root() / str(run_id)


def scheduler_enabled_for_spec(spec: CommandSpec) -> bool:
    """Return whether the canonical scheduler boundary is active for a spec.

    R02A's authority promise requires the scheduler boundary to run for every
    normal workflow/CLI/direct ``run_specs`` ingress.  Earlier WIP accepted a
    mutable ``exec_scheduler_enabled=false`` metadata value, which let a caller
    bypass the canonical boundary before any RUN/RER decision was made.
    Domain policy may still force the final disposition to RUN through
    ``reuse_disabled``/non-reusable fingerprints, but it must do so *inside*
    this scheduler rather than by disabling the scheduler itself.
    """
    return True


def authoritative_skip_allowed(spec: CommandSpec) -> bool:
    metadata = dict(spec.metadata or {})
    explicit = metadata.get("exec_authoritative_reuse")
    if isinstance(explicit, bool):
        return explicit
    text = str(explicit or "").strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    # A command with declared outputs can be safely skipped only if the output
    # verifier has proven those outputs still match the historical digest.
    return bool((metadata.get("workflow_outputs") or metadata.get("reuse_outputs") or ()))


def _history_candidates(index: ReuseHistoryIndex, node_definition_id: str) -> tuple[dict[str, Any], ...]:
    return index.candidates(node_definition_id)


def decision_for_spec(
    spec: CommandSpec,
    *,
    spec_index: int,
    repo_root: Path,
    history: ReuseHistoryIndex,
) -> SchedulerReuseDecision:
    if not scheduler_enabled_for_spec(spec):
        return SchedulerReuseDecision(
            spec_index=spec_index, spec_name=spec.name, node_definition_id="",
            disposition="run", reason="scheduler-disabled", authoritative=False, process_executed=True,
        )
    annotate_spec_reuse(spec, repo_root=repo_root)
    metadata = dict(spec.metadata or {})
    fp = metadata.get("reuse_fingerprint") if isinstance(metadata.get("reuse_fingerprint"), Mapping) else {}
    node_definition_id = str(metadata.get("node_definition_id") or (fp.get("node_definition_id") if isinstance(fp, Mapping) else "") or "")
    qualification = ReuseQualification("run", "no-history-candidate")
    candidate_payload: Mapping[str, Any] = {}
    cwd = Path(str(spec.cwd or repo_root)).expanduser().resolve(strict=False)
    for candidate in reversed(_history_candidates(history, node_definition_id)):
        current = qualify_candidate(fp if isinstance(fp, Mapping) else {}, candidate, cwd=cwd)
        if current.disposition in {"reuse", "materialize"}:
            qualification = current
            candidate_payload = dict(candidate)
            break
        if qualification.reason == "no-history-candidate":
            qualification = current
            candidate_payload = dict(candidate) if isinstance(candidate, Mapping) else {}
    authoritative = authoritative_skip_allowed(spec) and qualification.disposition in {"reuse", "materialize"}
    output_manifest = candidate_payload.get("output_manifest") if isinstance(candidate_payload.get("output_manifest"), Mapping) else {}
    return SchedulerReuseDecision(
        spec_index=spec_index,
        spec_name=spec.name,
        node_definition_id=node_definition_id,
        disposition=str(qualification.disposition or "run"),
        reason=str(qualification.reason or ""),
        authoritative=bool(authoritative),
        process_executed=not bool(authoritative),
        source_run_id=str(qualification.source_run_id or ""),
        source_node_id=str(qualification.source_node_id or ""),
        proof_refs=tuple(str(v) for v in qualification.proof_refs if str(v)),
        fingerprint=dict(fp) if isinstance(fp, Mapping) else {},
        output_manifest=dict(output_manifest) if isinstance(output_manifest, Mapping) else {},
        result_identity=str(candidate_payload.get("result_identity") or "") if isinstance(candidate_payload, Mapping) else "",
    )


def decisions_for_specs(config: ProjectConfig, specs: Sequence[CommandSpec]) -> dict[int, SchedulerReuseDecision]:
    history = ReuseHistoryIndex.from_history_directory(execution_history_root())
    return {
        index: decision_for_spec(spec, spec_index=index, repo_root=config.repo_root, history=history)
        for index, spec in enumerate(specs, start=1)
    }


_FILE_REF = re.compile(r"^file:(?P<path>.+)#sha256=(?P<sha>[0-9a-fA-F]{64})$")


def _source_for_ref(ref: str) -> Path | None:
    match = _FILE_REF.match(str(ref or ""))
    if not match:
        return None
    path = Path(match.group("path")).expanduser()
    if not path.is_file():
        return None
    if content_identity(path).digest.lower() != match.group("sha").lower():
        return None
    return path



def source_proof_refs_from_execution_document(document: Mapping[str, Any]) -> tuple[str, ...]:
    """Extract verified reusable source references from canonical EXO history.

    The result is intentionally rebuildable from ``execution.json`` so RUN/LIFE
    retention does not depend on an auxiliary mutable index.
    """
    execution = document.get("execution") if isinstance(document, Mapping) else None
    nodes = execution.get("nodes") if isinstance(execution, Mapping) else None
    refs: list[str] = []
    if not isinstance(nodes, list):
        return ()
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        metadata = node.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        for key in ("reuse_evidence_refs", "reuse_source_proof_refs"):
            values = metadata.get(key) or ()
            if isinstance(values, str):
                values = (values,)
            if isinstance(values, (list, tuple, set)):
                refs.extend(str(value) for value in values if str(value))
        record = metadata.get("reusable_node_record")
        if isinstance(record, Mapping):
            values = record.get("evidence_refs") or record.get("proof_refs") or ()
            if isinstance(values, str):
                values = (values,)
            if isinstance(values, (list, tuple, set)):
                refs.extend(str(value) for value in values if str(value))
    return tuple(dict.fromkeys(refs))


def source_proof_refs_from_history(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    try:
        import json
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    return source_proof_refs_from_execution_document(document if isinstance(document, Mapping) else {})

def materialize_outputs(decision: SchedulerReuseDecision, *, cwd: Path) -> SchedulerReuseDecision:
    if decision.disposition != "materialize" or not decision.authoritative:
        return decision
    entries = [row for row in (decision.output_manifest.get("entries") or ()) if isinstance(row, Mapping)]
    sources = [_source_for_ref(ref) for ref in decision.proof_refs]
    sources = [source for source in sources if source is not None]
    if not entries:
        return replace(decision, process_executed=False)
    if len(sources) != len(entries):
        return replace(decision, disposition="invalidated", reason=decision.reason + "; materialization-source-count-mismatch", authoritative=False, process_executed=True)
    written: list[str] = []
    for row, source in zip(entries, sources):
        target = Path(str(row.get("path") or ""))
        if not target.is_absolute():
            target = cwd / target
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        expected = str(row.get("digest") or "").lower()
        if expected and content_identity(target, base=cwd).digest.lower() != expected:
            return replace(decision, disposition="invalidated", reason=decision.reason + "; materialized-output-digest-mismatch", authoritative=False, process_executed=True)
        written.append(str(target))
    return replace(decision, process_executed=False, materialized_outputs=tuple(written))
