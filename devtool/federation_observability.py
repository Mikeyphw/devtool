from __future__ import annotations

"""FRX-11 federation observability and cross-surface machine state.

This module is intentionally read-only.  It projects already-authoritative
federation configuration, planning, EXO lineage, and operational ownership into
one stable payload that CLI/doctor/TUI/desktop/machine surfaces can share.
"""

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .core.events import get_execution_store
from .federation_discovery import (
    FederationDiscoveryError,
    federation_boundary_payload,
    resolve_federated_repository,
    resolve_federation_repository_owner,
)
from .federation_gate import build_federation_gate1_report, federation_gate1_as_dict
from .federation_gate2 import build_federation_gate2_report, federation_gate2_as_dict
from .federation_plan import FederationPlanError, build_federated_plan

if False:  # pragma: no cover - typing only
    from .models import ProjectConfig

FEDERATION_OBSERVABILITY_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class FederationObservation:
    enabled: bool
    owner: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    repositories: tuple[dict[str, Any], ...] = ()
    groups: dict[str, list[str]] = field(default_factory=dict)
    group_details: dict[str, dict[str, Any]] = field(default_factory=dict)
    workflows: tuple[dict[str, Any], ...] = ()
    gates: dict[str, Any] = field(default_factory=dict)
    runs: tuple[dict[str, Any], ...] = ()
    issues: tuple[dict[str, Any], ...] = ()
    schema_version: int = FEDERATION_OBSERVABILITY_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "enabled": self.enabled,
            "owner": dict(self.owner),
            "summary": dict(self.summary),
            "healthy": bool(self.summary.get("healthy", True)),
            "repositories": [dict(item) for item in self.repositories],
            "groups": {name: list(values) for name, values in self.groups.items()},
            "group_details": {name: dict(values) for name, values in self.group_details.items()},
            "workflows": [dict(item) for item in self.workflows],
            "gates": dict(self.gates),
            "runs": [dict(item) for item in self.runs],
            "issues": [dict(item) for item in self.issues],
        }


def _snapshot_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        payload = dict(value)
    elif hasattr(value, "to_dict"):
        payload = dict(value.to_dict())
    else:
        return {}
    nested = payload.get("execution")
    if isinstance(nested, Mapping):
        return dict(nested)
    return payload


def _safe_identity(value: Any) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, Mapping) else {}
    return {
        key: raw.get(key)
        for key in ("schema_version", "root", "fingerprint", "display_name", "config_path", "git_root")
        if raw.get(key) not in (None, "")
    }


def _safe_transport(metadata: Mapping[str, Any]) -> dict[str, Any]:
    allowed = (
        "federation_group",
        "federation_child_repository",
        "federation_child_target",
        "federation_child_workflow",
        "federation_ref",
        "federation_requested_ref",
        "federation_child_contract",
        "federation_contract_ref",
        "federation_child_repository_fingerprint",
        "federation_child_local_root",
        "federation_child_remote_root",
        "federation_execution_environment",
        "federation_execution_provider",
        "federation_parent_run_id",
        "federation_parent_node_id",
        "federation_child_run_id",
        "federation_artifact_return_root",
        "federation_path_mapping",
    )
    return {
        key.removeprefix("federation_"): metadata.get(key)
        for key in allowed
        if metadata.get(key) not in (None, "")
    }


def runtime_federation_projection(execution: Mapping[str, Any] | Any | None) -> dict[str, Any]:
    """Return a safe, renderer-ready federation projection for one EXO snapshot.

    Raw argv and arbitrary invocation metadata are deliberately excluded so UI
    surfaces cannot accidentally undo WRAP redaction guarantees.
    """

    payload = _snapshot_dict(execution)
    if not payload:
        return {}
    lineage = dict(payload.get("run_lineage")) if isinstance(payload.get("run_lineage"), Mapping) else {}
    nodes = [dict(item) for item in payload.get("nodes", []) if isinstance(item, Mapping)]
    federation_nodes = [
        item for item in nodes
        if str(item.get("kind") or "") == "federation-child"
        or str(item.get("node_id") or "").startswith("federation:")
    ]
    if not lineage and not federation_nodes:
        return {}

    children: list[dict[str, Any]] = []
    for node in federation_nodes:
        metadata = dict(node.get("metadata")) if isinstance(node.get("metadata"), Mapping) else {}
        child_lineage = dict(metadata.get("run_lineage")) if isinstance(metadata.get("run_lineage"), Mapping) else {}
        operational = dict(metadata.get("operational_ownership")) if isinstance(metadata.get("operational_ownership"), Mapping) else {}
        refs = [dict(item) for item in operational.get("references", []) if isinstance(item, Mapping)]
        transport = _safe_transport(metadata)
        if not transport:
            for ref in refs:
                ref_meta = ref.get("metadata")
                if isinstance(ref_meta, Mapping):
                    transport = _safe_transport(ref_meta)
                    if transport:
                        break
        children.append({
            "node_id": str(node.get("node_id") or ""),
            "step": str(metadata.get("federation_step") or child_lineage.get("federation_step") or ""),
            "repository": str(metadata.get("repository") or child_lineage.get("repository") or ""),
            "target": str(metadata.get("target") or child_lineage.get("target") or ""),
            "workflow": str(metadata.get("workflow") or child_lineage.get("child_workflow") or ""),
            "group": str(child_lineage.get("federation_group") or ""),
            "federation_ref": str(metadata.get("federation_ref") or child_lineage.get("federation_ref") or ""),
            "requested_ref": str(metadata.get("requested_ref") or child_lineage.get("requested_ref") or ""),
            "child_contract": str(metadata.get("child_contract") or child_lineage.get("child_contract") or ""),
            "contract_ref": str(metadata.get("contract_ref") or child_lineage.get("contract_ref") or ""),
            "child_run_id": str(metadata.get("child_run_id") or child_lineage.get("child_run_id") or ""),
            "status": str(node.get("status") or "planned"),
            "depends_on": [str(value) for value in node.get("depends_on", []) or []],
            "blocked_by": [str(value) for value in node.get("blocked_by", []) or []],
            "repository_identity": _safe_identity(metadata.get("repository_identity")),
            "operational_reference_count": len(refs),
            "operational_reference_kinds": sorted({str(ref.get("kind") or "") for ref in refs if ref.get("kind")}),
            "transport": transport,
        })

    statuses = Counter(str(item.get("status") or "unknown") for item in children)
    identity = _safe_identity(payload.get("repository_identity"))
    relationship = str(lineage.get("relationship") or ("coordinator" if children else ""))
    return {
        "schema_version": 1,
        "relationship": relationship,
        "workflow": str(lineage.get("federation_workflow") or ""),
        "group": str(lineage.get("federation_group") or ""),
        "repository": str(lineage.get("repository") or lineage.get("requested_repository") or ""),
        "target": str(lineage.get("target") or ""),
        "child_workflow": str(lineage.get("child_workflow") or ""),
        "federation_ref": str(lineage.get("federation_ref") or ""),
        "requested_ref": str(lineage.get("requested_ref") or ""),
        "child_contract": str(lineage.get("child_contract") or ""),
        "contract_ref": str(lineage.get("contract_ref") or ""),
        "parent_run_id": str(lineage.get("parent_run_id") or ""),
        "parent_node_id": str(lineage.get("parent_node_id") or ""),
        "child_run_id": str(lineage.get("child_run_id") or payload.get("run_id") or ""),
        "source_repository": str(lineage.get("source_repository") or ""),
        "effective_repository": str(lineage.get("effective_repository") or ""),
        "execution_environment": str(lineage.get("execution_environment") or ""),
        "repository_identity": identity,
        "children": children,
        "summary": {
            "children": len(children),
            "running": statuses.get("running", 0),
            "succeeded": statuses.get("succeeded", 0),
            "failed": statuses.get("failed", 0),
            "blocked": statuses.get("blocked", 0),
            "cancelled": statuses.get("cancelled", 0),
        },
    }


def _child_capabilities(parent: "ProjectConfig", name: str) -> dict[str, Any]:
    resolution = resolve_federated_repository(parent, name)
    if not resolution.available:
        return {"wrapper_commands": [], "target_local_jobs": [], "workflow_count": 0}
    from .config import load_project_config
    child = load_project_config(
        resolution.root,
        resolution.config_path or None,
        target_name="",
        allow_ambiguous_target=True,
        allow_ambiguous_project_kind=True,
        allow_parent_config=False,
    )
    jobs: list[str] = []
    workflow_count = 0
    for target_name, target in sorted(child.targets.items()):
        workflow_count += len(target.workflows)
        for local_name in sorted(target.jobs):
            jobs.append(f"{target_name}:job:{local_name}")
    return {
        "wrapper_commands": sorted(child.wrapper.commands),
        "target_local_jobs": jobs,
        "workflow_count": workflow_count,
    }


def _workflow_observations(config: "ProjectConfig", issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for name, workflow in sorted(config.federation.workflows.items()):
        entry: dict[str, Any] = {
            "name": name,
            "repositories": sorted({step.repository for step in workflow.steps}),
            "steps": [
                {
                    "id": step.step_id,
                    "ref": step.ref,
                    "repository": step.repository,
                    "target": step.target,
                    "target_qualified": step.target_qualified,
                    "workflow": step.workflow,
                    "depends_on": list(step.depends_on),
                }
                for step in workflow.steps
            ],
        }
        try:
            graph = build_federated_plan(config, name)
        except (FederationPlanError, FederationDiscoveryError, SystemExit) as exc:
            entry.update({"status": "error", "error": str(exc), "node_count": 0, "topological_order": []})
            issues.append({"severity": "error", "code": "federation.workflow.plan", "subject": name, "message": str(exc)})
        else:
            ordered = graph.topological_order()
            step_refs: dict[str, str] = {}
            for node in graph.nodes:
                if node.synthetic:
                    continue
                step_id = str(node.metadata.get("federation_step") or "")
                if step_id and node.federation_ref:
                    step_refs.setdefault(step_id, node.federation_ref)
            for step in entry["steps"]:
                step["canonical_ref"] = step_refs.get(str(step.get("id") or ""), "")
            entry.update({
                "status": "ok",
                "node_count": len(graph.nodes),
                "topological_order": [node.key for node in ordered],
                "federation_refs": sorted(set(step_refs.values())),
            })
        result.append(entry)
    return result


def _run_observations(snapshots: Iterable[Any]) -> list[dict[str, Any]]:
    raw = [_snapshot_dict(item) for item in snapshots]
    raw = [item for item in raw if item]
    run_ids = {str(item.get("run_id") or "") for item in raw}
    rows: list[dict[str, Any]] = []
    for item in raw:
        projection = runtime_federation_projection(item)
        if not projection:
            continue
        relationship = str(projection.get("relationship") or "")
        parent_run_id = str(projection.get("parent_run_id") or "")
        rows.append({
            "run_id": str(item.get("run_id") or ""),
            "status": str(item.get("status") or ""),
            "command": str(item.get("command") or ""),
            "relationship": relationship,
            "workflow": str(projection.get("workflow") or ""),
            "repository": str(projection.get("repository") or ""),
            "target": str(projection.get("target") or ""),
            "federation_ref": str(projection.get("federation_ref") or ""),
            "contract_ref": str(projection.get("contract_ref") or ""),
            "parent_run_id": parent_run_id,
            "orphan": bool(relationship == "child" and parent_run_id and parent_run_id not in run_ids),
            "federation": projection,
        })
    return sorted(rows, key=lambda item: (item.get("parent_run_id") or item.get("run_id") or "", item.get("run_id") or ""))


def build_federation_observability(
    config: "ProjectConfig",
    *,
    execution_snapshots: Iterable[Any] | None = None,
    tolerate_errors: bool = True,
) -> FederationObservation:
    issues: list[dict[str, Any]] = []
    boundary = federation_boundary_payload(config, tolerate_errors=True)
    repositories: list[dict[str, Any]] = []
    for item in boundary.get("repositories", []):
        if not isinstance(item, Mapping):
            continue
        row = dict(item)
        name = str(row.get("name") or "")
        try:
            row["public_contracts"] = _child_capabilities(config, name) if row.get("available") else {
                "wrapper_commands": [], "target_local_jobs": [], "workflow_count": 0,
            }
            if row.get("available") and row.get("root"):
                owner = resolve_federation_repository_owner(config, str(row.get("root")))
                row["path_owner"] = owner.as_dict() if owner is not None else None
                if owner is None or owner.repository != name:
                    raise FederationDiscoveryError(
                        f"Federation owner resolver mapped enrolled root {row.get('root')} to "
                        f"{owner.repository if owner is not None else '<none>'!r}, expected {name!r}"
                    )
        except Exception as exc:
            row["public_contracts"] = {"wrapper_commands": [], "target_local_jobs": [], "workflow_count": 0}
            row["observability_error"] = str(exc)
            issues.append({"severity": "error", "code": "federation.repository.contracts", "subject": name, "message": str(exc)})
        if str(row.get("boundary_status") or "") == "error":
            issues.append({
                "severity": "error",
                "code": "federation.repository.boundary",
                "subject": name,
                "message": str(row.get("boundary_detail") or "boundary error"),
            })
        repositories.append(row)

    workflows = _workflow_observations(config, issues)
    gates: dict[str, Any] = {}
    try:
        gates["gate1"] = federation_gate1_as_dict(build_federation_gate1_report(config))
    except Exception as exc:
        gates["gate1"] = {"status": "error", "ok": False, "error": str(exc)}
        issues.append({"severity": "error", "code": "federation.gate1", "subject": "gate1", "message": str(exc)})
    try:
        gates["gate2"] = federation_gate2_as_dict(build_federation_gate2_report(config))
    except Exception as exc:
        gates["gate2"] = {"status": "error", "ok": False, "error": str(exc)}
        issues.append({"severity": "error", "code": "federation.gate2", "subject": "gate2", "message": str(exc)})

    snapshots = tuple(execution_snapshots) if execution_snapshots is not None else get_execution_store().snapshots()
    runs = _run_observations(snapshots)
    required_missing = sum(1 for item in repositories if item.get("required") and not item.get("available"))
    unhealthy_repositories = sum(1 for item in repositories if str(item.get("boundary_status") or "ok") not in {"ok", "unavailable"})
    workflow_errors = sum(1 for item in workflows if item.get("status") != "ok")
    orphan_runs = sum(1 for item in runs if item.get("orphan"))
    summary = {
        "repositories": len(repositories),
        "available_repositories": sum(1 for item in repositories if item.get("available")),
        "required_missing": required_missing,
        "unhealthy_repositories": unhealthy_repositories,
        "groups": len(config.federation.groups),
        "executable_groups": sum(1 for group in config.federation.groups.values() if group.executable),
        "workflows": len(workflows),
        "workflow_errors": workflow_errors,
        "runs": len(runs),
        "orphan_child_runs": orphan_runs,
        "healthy": not issues and required_missing == 0 and workflow_errors == 0,
    }
    if not tolerate_errors and not summary["healthy"]:
        first = issues[0]["message"] if issues else "federation observability is unhealthy"
        raise RuntimeError(first)
    owner = _safe_identity(config.repository_identity.as_dict() if config.repository_identity else {"root": str(config.repo_root)})
    return FederationObservation(
        enabled=config.federation.enabled,
        owner=owner,
        summary=summary,
        repositories=tuple(repositories),
        groups={name: list(group.repositories) for name, group in sorted(config.federation.groups.items())},
        group_details={
            name: {
                "repositories": list(group.repositories),
                "members": list(group.members),
                "executable": group.executable,
            }
            for name, group in sorted(config.federation.groups.items())
        },
        workflows=tuple(workflows),
        gates=gates,
        runs=tuple(runs),
        issues=tuple(issues),
    )


__all__ = [
    "FEDERATION_OBSERVABILITY_SCHEMA_VERSION",
    "FederationObservation",
    "build_federation_observability",
    "runtime_federation_projection",
]
