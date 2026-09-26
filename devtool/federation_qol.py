from __future__ import annotations

"""GIT-FRX-17 federation inspection/QoL projections.

These helpers are deliberately read-only. They project the authoritative
capability graph, FRX-Y02 checkpoint, EXO history, and child evidence rather
than creating another federation state store.
"""

from pathlib import Path
from typing import Any, Mapping
import json

from .federation_capabilities import FederationCapabilityError, build_capability_graph
from .federation_resume import load_checkpoint
from .xdg import state_dir

SCHEMA_VERSION = 1


def federation_graph_payload(config: Any, *, group: str = "") -> dict[str, Any]:
    graph = build_capability_graph(config, strict=True).as_dict()
    name = str(group or "").strip()
    if not name:
        return {**graph, "kind": "devtool.federation-graph", "group": ""}
    group_cfg = config.federation.groups.get(name)
    if group_cfg is None:
        raise FederationCapabilityError(f"Unknown federation group {name!r}")
    members = tuple(dict.fromkeys((*tuple(group_cfg.repositories or ()), *tuple(group_cfg.members or ()))))
    allowed = set(members)
    exports = {key: value for key, value in (graph.get("exports") or {}).items() if key in allowed}
    requirements = {key: value for key, value in (graph.get("requirements") or {}).items() if key in allowed}
    resolutions = [row for row in graph.get("resolutions", []) if str(row.get("consumer") or "") in allowed]
    edges = [
        row for row in graph.get("edges", [])
        if str(row.get("provider") or "") in allowed and str(row.get("consumer") or "") in allowed
    ]
    unresolved = [row for row in resolutions if str(row.get("status") or "") not in {"resolved", "optional-missing"}]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "devtool.federation-graph",
        "group": name,
        "members": list(members),
        "interface": str(group_cfg.interface or ""),
        "healthy": not unresolved,
        "exports": exports,
        "requirements": requirements,
        "resolutions": resolutions,
        "edges": edges,
    }


def _children_by_step(checkpoint: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("step_id") or ""): dict(row)
        for row in checkpoint.get("children", [])
        if isinstance(row, Mapping) and str(row.get("step_id") or "")
    }


def federation_diff_payload(config: Any, old_run: str, new_run: str) -> dict[str, Any]:
    old = load_checkpoint(config, str(old_run))
    new = load_checkpoint(config, str(new_run))
    left = _children_by_step(old); right = _children_by_step(new)
    rows: list[dict[str, Any]] = []
    changed = 0
    for step in sorted(set(left) | set(right)):
        a = left.get(step, {}); b = right.get(step, {})
        fields: list[str] = []
        for field in ("status", "exit_code", "run_id", "repository", "target", "workflow", "evidence_digest"):
            if a.get(field) != b.get(field): fields.append(field)
        for field in ("git_lineage", "artifact_identity", "result_identity", "predicates", "placement"):
            if a.get(field) != b.get(field): fields.append(field)
        if fields: changed += 1
        rows.append({
            "step_id": step,
            "change": "added" if not a else "removed" if not b else "changed" if fields else "unchanged",
            "changed_fields": fields,
            "old": a,
            "new": b,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "devtool.federation-diff",
        "old_run": str(old_run), "new_run": str(new_run),
        "changed_steps": changed,
        "source_lock_changed": str(old.get("source_lock_digest") or "") != str(new.get("source_lock_digest") or ""),
        "plan_changed": str(old.get("plan_digest") or "") != str(new.get("plan_digest") or ""),
        "steps": rows,
    }


def federation_artifacts_payload(config: Any, run_id: str) -> dict[str, Any]:
    checkpoint = load_checkpoint(config, str(run_id))
    rows: list[dict[str, Any]] = []
    for child in checkpoint.get("children", []):
        if not isinstance(child, Mapping): continue
        artifact_inputs = child.get("artifact_inputs") if isinstance(child.get("artifact_inputs"), list) else []
        artifact_identity = child.get("artifact_identity") if isinstance(child.get("artifact_identity"), Mapping) else {}
        result_identity = child.get("result_identity") if isinstance(child.get("result_identity"), Mapping) else {}
        if artifact_inputs or artifact_identity or result_identity:
            rows.append({
                "step_id": str(child.get("step_id") or ""),
                "repository": str(child.get("repository") or ""),
                "run_id": str(child.get("run_id") or ""),
                "inputs": [dict(item) for item in artifact_inputs if isinstance(item, Mapping)],
                "artifact_identity": dict(artifact_identity),
                "result_identity": dict(result_identity),
                "evidence_digest": str(child.get("evidence_digest") or ""),
            })
    return {"schema_version": SCHEMA_VERSION, "kind": "devtool.federation-artifacts", "run_id": str(run_id), "artifacts": rows}


def _collect_path_values(value: Any, *, prefix: str = "") -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            lowered = str(key).lower()
            if isinstance(item, str) and any(token in lowered for token in ("log", "stdout", "stderr", "evidence_path")) and item:
                result.append({"field": name, "path": item})
            else:
                result.extend(_collect_path_values(item, prefix=name))
    elif isinstance(value, list):
        for index, item in enumerate(value): result.extend(_collect_path_values(item, prefix=f"{prefix}[{index}]"))
    return result


def federation_logs_payload(config: Any, run_id: str) -> dict[str, Any]:
    checkpoint = load_checkpoint(config, str(run_id))
    child_run_ids = [str(row.get("run_id") or "") for row in checkpoint.get("children", []) if isinstance(row, Mapping) and row.get("run_id")]
    candidates: list[dict[str, str]] = _collect_path_values(checkpoint)
    # Canonical EXO history is authoritative when present.  Search only the
    # Devtool state root and report paths; do not parse arbitrary stdout.
    root = state_dir()
    for child_run in child_run_ids:
        for path in root.glob(f"**/history/{child_run}/execution.json"):
            candidates.append({"field": f"execution.{child_run}", "path": str(path)})
    dedup: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in candidates:
        key = (row["field"], row["path"])
        if key not in seen:
            seen.add(key); dedup.append(row)
    return {"schema_version": SCHEMA_VERSION, "kind": "devtool.federation-logs", "run_id": str(run_id), "child_run_ids": child_run_ids, "logs": dedup}


def federation_failures_payload(config: Any, run_id: str) -> dict[str, Any]:
    checkpoint = load_checkpoint(config, str(run_id))
    failures: list[dict[str, Any]] = []
    for row in checkpoint.get("children", []):
        if not isinstance(row, Mapping): continue
        status = str(row.get("status") or "")
        exit_code = int(row.get("exit_code", 0) or 0)
        if status == "succeeded" and exit_code == 0: continue
        failures.append({
            "step_id": str(row.get("step_id") or ""), "repository": str(row.get("repository") or ""),
            "run_id": str(row.get("run_id") or ""), "status": status, "exit_code": exit_code,
            "lifecycle": dict(row.get("lifecycle") or {}) if isinstance(row.get("lifecycle"), Mapping) else {},
            "evidence_digest": str(row.get("evidence_digest") or ""),
        })
    return {"schema_version": SCHEMA_VERSION, "kind": "devtool.federation-failures", "run_id": str(run_id), "failures": failures}


__all__ = [
    "federation_artifacts_payload", "federation_diff_payload", "federation_failures_payload",
    "federation_graph_payload", "federation_logs_payload",
]
