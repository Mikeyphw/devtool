from __future__ import annotations

"""Canonical validation-reference projections for EXEC-X03.

Validation may keep a compatibility session/report for existing CLI and UI
clients, but its canonical durable state is a compact reference projection:
contract/checkpoint/control/verdict plus EXO/RER/RUN references.  It contains no
mutable executable DAG copy and no process/progress/event truth.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
import hashlib
import json

from .logstore import state_dir
from .models import CommandSpec
from .core.reuse import annotate_spec_reuse, fingerprint_for_spec, stable_digest
from .core.reuse_planner import ReusePlan, ReusePlanner
from .core.execution_semantics import ValidationControlAction, ValidationControlIntent


def _repo_key(repo_root: Path) -> str:
    return hashlib.sha256(str(repo_root.expanduser().resolve()).encode("utf-8")).hexdigest()[:24]


def private_validation_root(repo_root: Path) -> Path:
    return state_dir() / "validation" / _repo_key(repo_root)


def private_session_state_path(repo_root: Path, session_id: str) -> Path:
    return private_validation_root(repo_root) / "sessions" / session_id / "canonical-state.json"


def private_plan_attachment_path(repo_root: Path, session_id: str) -> Path:
    return private_validation_root(repo_root) / "sessions" / session_id / "plan-attachment.json"


def private_report_attachment_path(repo_root: Path, session_id: str) -> Path:
    return private_validation_root(repo_root) / "sessions" / session_id / "report-attachment.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    import os, uuid
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False, default=str)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def bind_validation_specs_to_authority(specs: Sequence[CommandSpec], *, repo_root: Path) -> list[CommandSpec]:
    """Attach canonical node/reuse metadata before a validation plan is pinned."""
    bound: list[CommandSpec] = []
    for spec in specs:
        metadata = dict(spec.metadata or {})
        metadata.setdefault("execution_intent_kind", "validation-proof")
        metadata.setdefault("validation_authority", "exo-rer-run")
        clone = CommandSpec(
            spec.name,
            list(spec.argv),
            env=dict(spec.env),
            cwd=spec.cwd,
            destructive=spec.destructive,
            metadata=metadata,
        )
        annotate_spec_reuse(clone, repo_root=repo_root)
        fp, _contribution, _dep = fingerprint_for_spec(clone, repo_root=repo_root)
        clone.metadata["validation_rer_fingerprint"] = fp.to_dict()
        clone.metadata["validation_node_definition_id"] = str(clone.metadata.get("node_definition_id") or fp.node_definition_id)
        bound.append(clone)
    return bound


def validation_reuse_plan(specs: Sequence[CommandSpec], *, repo_root: Path) -> ReusePlan:
    return ReusePlanner(repo_root=repo_root).plan_specs(list(specs))


def semantic_control_intent(
    action: str,
    *,
    scope: str = "next",
    selected_checks: Sequence[str] = (),
    through: str = "",
    expected_revision: int | None = None,
    compatibility_source: str = "",
) -> ValidationControlIntent:
    try:
        control_action = ValidationControlAction(str(action or "run"))
    except ValueError:
        control_action = ValidationControlAction.RUN
    intent = ValidationControlIntent(
        action=control_action,
        scope=str(scope or "next"),
        selected_checks=tuple(str(v) for v in selected_checks if str(v)),
        through=str(through or ""),
        expected_revision=expected_revision,
        compatibility_source=str(compatibility_source or ""),
    )
    intent.validate()
    return intent


def build_private_state(session: Any) -> dict[str, Any]:
    repo_root = Path(str(getattr(session, "repo_root", "") or "."))
    run_refs = dict((getattr(session, "apply_context", {}) or {}).get("run_refs") or {})
    checks: list[dict[str, Any]] = []
    specs = list(getattr(session, "plan_specs", []) or [])
    by_key = {str((item.get("metadata") or {}).get("validation_phase_key") or ""): item for item in specs if isinstance(item, dict)}
    for phase in getattr(session, "phases", []) or []:
        spec_payload = by_key.get(str(getattr(phase, "key", "")), {})
        metadata = spec_payload.get("metadata") if isinstance(spec_payload, dict) else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        fp = metadata.get("validation_rer_fingerprint") or metadata.get("reuse_fingerprint") or {}
        checks.append({
            "phase_key": str(getattr(phase, "key", "")),
            "check_id": str(getattr(phase, "validation_check_id", "") or getattr(phase, "key", "")),
            "status": str(getattr(phase, "status", "")),
            "required": bool(getattr(phase, "required", True)),
            "node_definition_id": str(metadata.get("validation_node_definition_id") or metadata.get("node_definition_id") or ""),
            "rer_fingerprint_digest": str(fp.get("digest") or "") if isinstance(fp, dict) else "",
            "exo_node_ref": str(metadata.get("exo_node_ref") or ""),
            "run_attachment_refs": list(metadata.get("run_attachment_refs") or []),
        })
    state = {
        "schema_version": 1,
        "authority": "EXEC-X03 validation-reference-state",
        "session_id": str(getattr(session, "session_id", "")),
        "repo_root": str(repo_root),
        "contract_ref": {
            "digest": str(((getattr(session, "resolved_validation_contract", {}) or {}).get("digest") or "")),
            "source": str(((getattr(session, "resolved_validation_contract", {}) or {}).get("source") or "")),
        },
        "checkpoint_ref": {
            "name": str(getattr(session, "required_checkpoint_name", "") or ""),
            "required_keys": list(getattr(session, "required_checkpoint_keys", []) or []),
        },
        "control_ref": dict(getattr(session, "validation_run_request", {}) or {}),
        "verdict_ref": dict(getattr(session, "validation_verdict", {}) or {}),
        "checks": checks,
        "exo_run_ref": str(run_refs.get("exo_run_ref") or ""),
        "run_private": True,
    }
    state["digest"] = stable_digest(state)
    return state


def write_private_state(session: Any) -> dict[str, str]:
    repo_root = Path(str(getattr(session, "repo_root", "") or "."))
    session_id = str(getattr(session, "session_id", ""))
    state = build_private_state(session)
    state_path = private_session_state_path(repo_root, session_id)
    plan_path = private_plan_attachment_path(repo_root, session_id)
    report_path = private_report_attachment_path(repo_root, session_id)
    _atomic_write_json(state_path, state)
    _atomic_write_json(plan_path, {
        "schema_version": 1,
        "session_id": session_id,
        "plan_fingerprint": str(getattr(session, "plan_fingerprint", "") or ""),
        "spec_count": len(getattr(session, "plan_specs", []) or []),
        "node_definition_ids": [str(row.get("node_definition_id") or row.get("metadata", {}).get("node_definition_id") or "") for row in getattr(session, "plan_specs", []) or [] if isinstance(row, dict)],
    })
    _atomic_write_json(report_path, {
        "schema_version": 1,
        "session_id": session_id,
        "status": str(getattr(session, "status", "") or ""),
        "verdict": dict(getattr(session, "validation_verdict", {}) or {}),
        "state_ref": str(state_path),
    })
    return {
        "canonical_state": str(state_path),
        "plan_attachment": str(plan_path),
        "report_attachment": str(report_path),
        "state_digest": str(state.get("digest") or ""),
    }


def exo_projection_from_directory(path: Path, *, limit: int = 100) -> dict[str, Any]:
    """Bounded read-only EXO/RUN projection for validation status consumers."""
    rows: list[dict[str, Any]] = []
    root = path.expanduser()
    if root.is_dir():
        for item in sorted(root.glob("*/execution.json"))[-max(1, min(int(limit or 100), 1000)):]:
            try:
                payload = json.loads(item.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            execution = payload.get("execution") if isinstance(payload, dict) else {}
            rows.append({
                "path": str(item),
                "run_id": str(execution.get("run_id") or payload.get("run_id") or item.parent.name) if isinstance(execution, dict) else item.parent.name,
                "status": str(execution.get("status") or payload.get("status") or "") if isinstance(execution, dict) else "",
                "node_count": len(execution.get("nodes") or []) if isinstance(execution, dict) and isinstance(execution.get("nodes"), list) else 0,
            })
    return {"source": "exo-run-history", "limit": limit, "runs": rows}
