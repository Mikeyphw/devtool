from __future__ import annotations

"""FRX-Y02 parent federation resume/retry/checkpoint lifecycle.

This module persists only federation dependency/reuse truth.  It deliberately
references, rather than duplicates, child EXO/SSH history and recovery state.
"""

from dataclasses import asdict, dataclass, field, is_dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

from .federation_git_lineage import lineage_evidence_path
from .federation_artifacts import artifact_edges_identity, checkpoint_artifact_reasons
from .federation_plan import FederatedPlanGraph, build_federated_plan, project_with_federation_group
from .reservations import repo_key
from .xdg import state_dir

if TYPE_CHECKING:
    from .federation_execution import FederatedChildRun, FederatedRunReport
    from .models import FederatedWorkflowConfig, FederatedWorkflowStep, ProjectConfig


SCHEMA_VERSION = 1
TERMINAL_SUCCESS = "succeeded"


class FederationResumeError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe_run_id(value: str) -> str:
    text = str(value or "").strip()
    if not text or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for ch in text):
        raise FederationResumeError(f"Invalid federation run id: {text or '<empty>'}")
    return text


def checkpoint_root(parent: "ProjectConfig") -> Path:
    return state_dir() / "federation-runs" / repo_key(Path(parent.repo_root))


def checkpoint_path(parent: "ProjectConfig", run_id: str) -> Path:
    return checkpoint_root(parent) / f"{_safe_run_id(run_id)}.json"


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(data, encoding="utf-8")
    os.replace(temp, path)


def _plan_identity_payload(plan: FederatedPlanGraph) -> dict[str, Any]:
    # Keep only parent-owned DAG/contract structure here. Child source, repository
    # identity, capability/interface contracts, schedules, and artifact inputs are
    # independent reuse predicates so drift can invalidate only affected nodes.
    nodes: list[dict[str, Any]] = []
    for node in plan.nodes:
        meta = node.metadata if isinstance(node.metadata, Mapping) else {}
        nodes.append({
            "key": node.key, "repository": node.repository, "target": node.target,
            "workflow": node.workflow, "local_key": node.local_key,
            "depends_on": list(node.depends_on), "index": node.index, "synthetic": node.synthetic,
            "contract": {
                key: meta.get(key) for key in (
                    "federation_step", "federation_ref", "requested_ref", "contract_type",
                    "contract_name", "child_contract", "contract_ref", "federation_stage",
                    "integration_policy", "publish_barrier",
                )
            },
        })
    return {
        "workflow": plan.workflow, "group": plan.group, "repositories": list(plan.repositories),
        "order": [node.key for node in plan.topological_order()], "nodes": nodes,
    }


def plan_identity(plan: FederatedPlanGraph) -> str:
    return _digest(_plan_identity_payload(plan))


def _source_entry(plan: FederatedPlanGraph, repository: str) -> dict[str, Any]:
    rows = plan.source_lock.get("repositories", []) if isinstance(plan.source_lock, Mapping) else []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, Mapping) and str(row.get("repository") or "") == repository:
            return dict(row)
    return {}




def _devtool_owned_path(value: object) -> bool:
    text = str(value or "").replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text == ".devtool" or text.startswith(".devtool/")


def _source_identity_payload(plan: FederatedPlanGraph, repository: str) -> dict[str, Any]:
    row = _source_entry(plan, repository)
    if not row:
        return {}
    dirty = row.get("dirty_identity") if isinstance(row.get("dirty_identity"), Mapping) else {}
    def paths(name: str) -> list[str]:
        return [str(value) for value in dirty.get(name, []) or [] if not _devtool_owned_path(value)]
    worktree_rows = []
    for item in dirty.get("worktree_paths", []) or []:
        if not isinstance(item, Mapping) or _devtool_owned_path(item.get("path")):
            continue
        worktree_rows.append(dict(item))
    return {
        "repository": repository,
        "source_kind": str(row.get("source_kind") or ""),
        "repository_id": str(row.get("repository_id") or ""),
        "repository_fingerprint": str(row.get("repository_fingerprint") or ""),
        "config_digest": str(row.get("config_digest") or ""),
        "start_commit": str(row.get("start_commit") or ""),
        "head_tree": str(row.get("head_tree") or ""),
        "index_tree": str(row.get("index_tree") or ""),
        "index_fingerprint": str(row.get("index_fingerprint") or ""),
        "filesystem_tree_digest": str(row.get("filesystem_tree_digest") or ""),
        "dirty": {
            "staged": paths("staged"), "unstaged": paths("unstaged"),
            "untracked": paths("untracked"), "unmerged": paths("unmerged"),
            "worktree_paths": worktree_rows,
        },
    }


def source_identity(plan: FederatedPlanGraph, repository: str) -> str:
    return _digest(_source_identity_payload(plan, repository))

def _step_nodes(plan: FederatedPlanGraph, step_id: str) -> list[dict[str, Any]]:
    return [
        node.as_dict()
        for node in plan.nodes
        if not node.synthetic and str(node.metadata.get("federation_step") or "") == step_id
    ]


def contract_identity(plan: FederatedPlanGraph, step: "FederatedWorkflowStep") -> str:
    return _digest({
        "step_id": step.step_id,
        "repository": step.repository,
        "target": step.target,
        "workflow": step.workflow,
        "contract_type": step.contract_type,
        "contract_name": step.contract_name,
        "requested_ref": step.ref,
        "stage": step.stage,
        "nodes": _step_nodes(plan, step.step_id),
    })


def execution_contract_identity(plan: FederatedPlanGraph, step: "FederatedWorkflowStep") -> str:
    rows = []
    for node in plan.nodes:
        if node.synthetic or str(node.metadata.get("federation_step") or "") != step.step_id:
            continue
        contract = node.metadata.get("execution_contract") if isinstance(node.metadata.get("execution_contract"), Mapping) else {}
        rows.append({
            "target": str(node.target or ""),
            "contract_type": str(node.metadata.get("contract_type") or step.contract_type),
            "execution_contract": dict(contract),
        })
    return _digest(rows)


def capability_interface_identity(parent: "ProjectConfig", plan: FederatedPlanGraph, step: "FederatedWorkflowStep") -> str:
    graph = plan.capability_graph if isinstance(plan.capability_graph, Mapping) else {}
    repo = step.repository
    exports = dict(graph.get("exports") or {})
    requirements = dict(graph.get("requirements") or {})
    relevant_edges = [
        dict(row) for row in graph.get("edges", []) or []
        if isinstance(row, Mapping) and repo in {str(row.get("provider") or ""), str(row.get("consumer") or "")}
    ]
    relevant_resolutions = [
        dict(row) for row in graph.get("resolutions", []) or []
        if isinstance(row, Mapping) and str(row.get("repository") or row.get("consumer") or "") == repo
    ]
    interface_payload: dict[str, Any] = {}
    group_name = plan.group
    if group_name:
        group = parent.federation.groups.get(group_name)
        if group is not None:
            interface_name = str(group.interface or "")
            interface = parent.federation.interfaces.get(interface_name) if interface_name else None
            interface_payload = {
                "group": asdict(group) if is_dataclass(group) else str(group),
                "interface": asdict(interface) if interface is not None and is_dataclass(interface) else {},
            }
    return _digest({
        "repository": repo,
        "exports": exports.get(repo, []),
        "requirements": requirements.get(repo, []),
        "edges": relevant_edges,
        "resolutions": relevant_resolutions,
        "interface": interface_payload,
    })


def _artifact_identity_from_ownership(ownership: Any) -> str:
    refs = ownership.get("references", []) if isinstance(ownership, Mapping) else []
    artifacts: list[dict[str, Any]] = []
    for row in refs if isinstance(refs, list) else []:
        if not isinstance(row, Mapping):
            continue
        kind = str(row.get("kind") or "")
        if "artifact" not in kind:
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
        artifacts.append({
            "kind": kind,
            "identifier": str(row.get("identifier") or ""),
            "path": str(row.get("path") or ""),
            "status": str(row.get("status") or ""),
            "sha256": str(metadata.get("sha256") or ""),
            "record_ids": list(metadata.get("record_ids") or []),
            "artifact_paths": list(metadata.get("artifact_paths") or []),
        })
    return _digest(artifacts)


def _artifact_identity(child: "FederatedChildRun") -> str:
    return _artifact_identity_from_ownership(child.operational_ownership)


def child_result_identity(child: "FederatedChildRun") -> str:
    return _digest({
        "repository": child.repository,
        "target": child.target,
        "workflow": child.workflow,
        "child_contract": child.child_contract,
        "contract_ref": child.contract_ref,
        "status": child.status,
        "exit_code": child.exit_code,
        "repository_fingerprint": str(child.repository_identity.get("fingerprint") or ""),
        "result_commit": str(child.git_lineage.get("result_commit") or ""),
        "result_ref": str(child.git_lineage.get("result_ref") or ""),
        "artifact_identity": _artifact_identity(child),
        "consumed_artifact_inputs": artifact_edges_identity(
            getattr(child, "artifact_inputs", {}) or {}
        ),
    })


def _checkpoint_success_evidence_valid(child: Mapping[str, Any]) -> bool:
    """Recompute producer identities; a claimed success is not sufficient for reuse.

    Digests detect stale/accidentally modified checkpoint fields. They are not
    signatures or authentication against a writer who can recompute hashes.
    """
    if child.get("status") != TERMINAL_SUCCESS or type(child.get("exit_code")) is not int or child["exit_code"] != 0:
        return False
    if not str(child.get("step_id") or "").strip() or not str(child.get("run_id") or "").strip():
        return False
    evidence_payload = {
        "child_run_id": child["run_id"],
        "git_lineage": child.get("git_lineage") or {},
        "operational_ownership": child.get("operational_ownership") or {},
        "run_lineage": child.get("run_lineage") or {},
        "artifact_inputs": child.get("artifact_inputs") or {},
    }
    if str(child.get("evidence_digest") or "") != _digest(evidence_payload):
        return False
    repository_identity = child.get("repository_identity") or {}
    git_lineage = child.get("git_lineage") or {}
    if not isinstance(repository_identity, Mapping) or not isinstance(git_lineage, Mapping):
        return False
    if str(child.get("artifact_identity") or "") != _artifact_identity_from_ownership(child.get("operational_ownership")):
        return False
    result_payload = {
        "repository": child.get("repository"), "target": child.get("target"),
        "workflow": child.get("workflow"), "child_contract": child.get("child_contract"),
        "contract_ref": child.get("contract_ref"), "status": child["status"],
        "exit_code": child["exit_code"],
        "repository_fingerprint": str(repository_identity.get("fingerprint") or ""),
        "result_commit": str(git_lineage.get("result_commit") or ""),
        "result_ref": str(git_lineage.get("result_ref") or ""),
        "artifact_identity": str(child.get("artifact_identity") or ""),
        "consumed_artifact_inputs": artifact_edges_identity(child.get("artifact_inputs") or {}),
    }
    return bool(child.get("artifact_identity")) and str(child.get("result_identity") or "") == _digest(result_payload)


def _evidence_available(parent_run_id: str, child: Mapping[str, Any]) -> bool:
    if not _checkpoint_success_evidence_valid(child):
        return False
    run_id = str(child.get("run_id") or "")
    git_lineage = child.get("git_lineage") if isinstance(child.get("git_lineage"), Mapping) else {}
    if git_lineage:
        path = lineage_evidence_path(parent_run_id, run_id)
        if not path.is_file():
            return False
    # SSH/EXO output remains owned by the canonical child/SSH history stores;
    # this checkpoint carries only stable references to those records.
    return True


def _child_checkpoint(
    parent: "ProjectConfig",
    plan: FederatedPlanGraph,
    workflow: "FederatedWorkflowConfig",
    child: "FederatedChildRun",
    result_ids: Mapping[str, str],
    artifact_ids: Mapping[str, str],
) -> dict[str, Any]:
    step = next(step for step in workflow.steps if step.step_id == child.step_id)
    upstream = {dep: str(result_ids.get(dep) or "") for dep in step.depends_on}
    upstream_artifacts = {dep: str(artifact_ids.get(dep) or "") for dep in step.depends_on}
    result_identity = child_result_identity(child)
    artifact_identity = _artifact_identity(child)
    artifact_inputs = dict(getattr(child, "artifact_inputs", {}) or {})
    evidence_payload = {
        "child_run_id": child.run_id,
        "git_lineage": child.git_lineage,
        "operational_ownership": child.operational_ownership,
        "run_lineage": child.run_lineage,
        "artifact_inputs": artifact_inputs,
    }
    return {
        "step_id": child.step_id,
        "repository": child.repository,
        "workflow": child.workflow,
        "target": child.target,
        "run_id": child.run_id,
        "parent_node_id": child.parent_node_id,
        "status": child.status,
        "exit_code": child.exit_code,
        "lifecycle": str(getattr(child, "lifecycle", "") or "executed"),
        "repository_identity": dict(child.repository_identity),
        "run_lineage": dict(child.run_lineage),
        "operational_ownership": dict(child.operational_ownership),
        "federation_ref": child.federation_ref,
        "requested_ref": child.requested_ref,
        "child_contract": child.child_contract,
        "contract_ref": child.contract_ref,
        "git_lineage": dict(child.git_lineage),
        "predicates": {
            "repository_fingerprint": str(child.repository_identity.get("fingerprint") or ""),
            "source_identity": source_identity(plan, child.repository),
            "contract_identity": contract_identity(plan, step),
            "capability_interface_identity": capability_interface_identity(parent, plan, step),
            "execution_contract_identity": execution_contract_identity(plan, step),
            "upstream_result_identities": upstream,
            "artifact_input_identities": upstream_artifacts,
            "consumed_artifact_inputs_identity": artifact_edges_identity(artifact_inputs),
        },
        "artifact_inputs": artifact_inputs,
        "placement": dict(getattr(child, "placement", {}) or {}),
        "result_identity": result_identity,
        "artifact_identity": artifact_identity,
        "evidence_digest": _digest(evidence_payload),
    }


def _workflow_for(parent: "ProjectConfig", plan: FederatedPlanGraph) -> tuple["ProjectConfig", "FederatedWorkflowConfig"]:
    if plan.group:
        scoped, workflow = project_with_federation_group(parent, plan.group)
        return scoped, workflow
    workflow = parent.federation.workflows.get(plan.workflow)
    if workflow is None:
        raise FederationResumeError(f"Unknown federation workflow {plan.workflow!r} while checkpointing")
    from .federation_capabilities import project_capability_dependencies
    return parent, project_capability_dependencies(parent, workflow)


def checkpoint_report(parent: "ProjectConfig", report: "FederatedRunReport", *, resumed_from: str = "", mode: str = "run") -> dict[str, Any]:
    scoped, workflow = _workflow_for(parent, report.plan)
    by_step = {child.step_id: child for child in report.children}
    known_steps = {step.step_id for step in workflow.steps}
    child_ids = [str(child.run_id or "") for child in report.children]
    if (len(by_step) != len(report.children) or
        any(not child.step_id or child.step_id not in known_steps for child in report.children) or
        any(not run_id for run_id in child_ids) or len(set(child_ids)) != len(child_ids)):
        raise FederationResumeError("Federation checkpoint has duplicate, unknown, or missing child identity")
    result_ids: dict[str, str] = {}
    artifact_ids: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    for step in workflow.steps:
        child = by_step.get(step.step_id)
        if child is None:
            continue
        row = _child_checkpoint(scoped, report.plan, workflow, child, result_ids, artifact_ids)
        result_ids[step.step_id] = str(row["result_identity"])
        artifact_ids[step.step_id] = str(row["artifact_identity"])
        rows.append(row)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "devtool.federation.resume-checkpoint",
        "parent_run_id": report.parent_run_id,
        "resumed_from": str(resumed_from or ""),
        "mode": str(mode or "run"),
        "subject_kind": "group" if report.group else "workflow",
        "group": report.group,
        "workflow": report.workflow,
        "status": report.status,
        "exit_code": report.exit_code,
        "plan_identity": plan_identity(report.plan),
        "source_lock": dict(report.plan.source_lock),
        "source_lock_digest": str(report.plan.source_lock.get("lock_digest") or ""),
        "schedule": dict(getattr(report, "schedule", {}) or {}),
        "lifecycle": dict(getattr(report, "lifecycle", {}) or {}),
        "children": rows,
    }
    path = checkpoint_path(parent, report.parent_run_id)
    _atomic_json(path, payload)
    return payload


def load_checkpoint(parent: "ProjectConfig", run_id: str) -> dict[str, Any]:
    path = checkpoint_path(parent, run_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FederationResumeError(f"No federation resume checkpoint for run {run_id!r}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise FederationResumeError(f"Cannot read federation resume checkpoint for run {run_id!r}: {exc}") from exc
    if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != SCHEMA_VERSION:
        raise FederationResumeError(f"Unsupported federation resume checkpoint for run {run_id!r}")
    if str(payload.get("parent_run_id") or "") != str(run_id):
        raise FederationResumeError(f"Federation resume checkpoint run identity mismatch for {run_id!r}")
    return payload


@dataclass(frozen=True, slots=True)
class ResumeDecision:
    step_id: str
    action: str
    reasons: tuple[str, ...] = ()
    prior_child: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"step_id": self.step_id, "action": self.action, "reasons": list(self.reasons), "prior_child": dict(self.prior_child)}


@dataclass(frozen=True, slots=True)
class ResumePlan:
    prior_run_id: str
    workflow: str
    group: str
    plan: FederatedPlanGraph
    decisions: tuple[ResumeDecision, ...]
    mode: str
    failed_only: bool = False

    @property
    def reused(self) -> tuple[str, ...]:
        return tuple(row.step_id for row in self.decisions if row.action == "reused")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "prior_run_id": self.prior_run_id,
            "mode": self.mode,
            "failed_only": self.failed_only,
            "workflow": self.workflow,
            "group": self.group,
            "plan_identity": plan_identity(self.plan),
            "decisions": [row.as_dict() for row in self.decisions],
        }


def build_resume_plan(parent: "ProjectConfig", checkpoint: Mapping[str, Any], *, mode: str, failed_only: bool = False) -> ResumePlan:
    prior_run_id = str(checkpoint.get("parent_run_id") or "")
    group = str(checkpoint.get("group") or "")
    workflow_name = str(checkpoint.get("workflow") or "")
    if group:
        scoped, workflow = project_with_federation_group(parent, group)
        current = build_federated_plan(scoped, workflow.name)
        working_parent = scoped
    else:
        from .federation_capabilities import project_capability_dependencies
        workflow = parent.federation.workflows.get(workflow_name)
        if workflow is None:
            raise FederationResumeError(f"Federation workflow {workflow_name!r} no longer exists; create a new plan")
        workflow = project_capability_dependencies(parent, workflow)
        current = build_federated_plan(parent, workflow_name)
        working_parent = parent
    expected_plan_identity = str(checkpoint.get("plan_identity") or "")
    current_plan_identity = plan_identity(current)
    if not expected_plan_identity or expected_plan_identity != current_plan_identity:
        raise FederationResumeError("Federation parent plan identity changed; create a new plan/run instead of reusing the checkpoint")

    child_rows = checkpoint.get("children", []) or []
    if not isinstance(child_rows, list) or any(not isinstance(row, Mapping) for row in child_rows):
        raise FederationResumeError("Federation checkpoint has invalid child rows")
    step_ids = [str(row.get("step_id") or "") for row in child_rows]
    if not all(step_ids) or len(set(step_ids)) != len(step_ids):
        raise FederationResumeError("Federation checkpoint has missing or duplicate child step identity")
    prior_rows = {str(row["step_id"]): dict(row) for row in child_rows}
    prior_result_ids = {key: str(value.get("result_identity") or "") for key, value in prior_rows.items()}
    current_result_ids: dict[str, str] = {}
    current_artifact_ids: dict[str, str] = {}
    decisions: list[ResumeDecision] = []

    for step in workflow.steps:
        prior = prior_rows.get(step.step_id, {})
        reasons: list[str] = []
        if not prior:
            decisions.append(ResumeDecision(step.step_id, "retried", ("no prior child result",), {}))
            current_result_ids[step.step_id] = ""
            current_artifact_ids[step.step_id] = ""
            continue
        status = str(prior.get("status") or "")
        predicates = prior.get("predicates") if isinstance(prior.get("predicates"), Mapping) else {}
        current_repo_fp = ""
        for node in current.nodes:
            if not node.synthetic and str(node.metadata.get("federation_step") or "") == step.step_id:
                identity = node.metadata.get("repository_identity") if isinstance(node.metadata.get("repository_identity"), Mapping) else {}
                current_repo_fp = str(identity.get("fingerprint") or "")
                break
        if status != TERMINAL_SUCCESS:
            reasons.append(f"prior child status is {status or 'unknown'}")
        if str(predicates.get("repository_fingerprint") or "") != current_repo_fp:
            reasons.append("repository identity changed")
        if str(predicates.get("source_identity") or "") != source_identity(current, step.repository):
            reasons.append("frozen Git/tree/config state changed")
        if str(predicates.get("contract_identity") or "") != contract_identity(current, step):
            reasons.append("target/workflow/contract identity changed")
        if str(predicates.get("capability_interface_identity") or "") != capability_interface_identity(working_parent, current, step):
            reasons.append("capability/interface contract changed")
        expected_execution_identity = str(predicates.get("execution_contract_identity") or "")
        if expected_execution_identity and expected_execution_identity != execution_contract_identity(current, step):
            reasons.append("execution/provider contract changed")
        prior_inputs = prior.get("artifact_inputs") if isinstance(prior.get("artifact_inputs"), Mapping) else {}
        expected_inputs_identity = str(predicates.get("consumed_artifact_inputs_identity") or "")
        if expected_inputs_identity and expected_inputs_identity != artifact_edges_identity(prior_inputs):
            reasons.append("consumed artifact input identity changed")
        reasons.extend(checkpoint_artifact_reasons(prior_inputs, prior_rows))
        expected_upstream = predicates.get("upstream_result_identities") if isinstance(predicates.get("upstream_result_identities"), Mapping) else {}
        expected_artifacts = predicates.get("artifact_input_identities") if isinstance(predicates.get("artifact_input_identities"), Mapping) else {}
        for dep in step.depends_on:
            current_dep = current_result_ids.get(dep, "")
            if not current_dep or str(expected_upstream.get(dep) or "") != current_dep:
                reasons.append(f"upstream child result changed: {dep}")
            current_art = current_artifact_ids.get(dep, "")
            if not current_art or str(expected_artifacts.get(dep) or "") != current_art:
                reasons.append(f"required artifact inputs changed: {dep}")
        if status == TERMINAL_SUCCESS and not _evidence_available(prior_run_id, prior):
            reasons.append("prior child evidence is unavailable")

        if not reasons:
            action = "reused"
            current_result_ids[step.step_id] = str(prior.get("result_identity") or "")
            current_artifact_ids[step.step_id] = str(prior.get("artifact_identity") or "")
        else:
            if failed_only and status == TERMINAL_SUCCESS:
                raise FederationResumeError(
                    f"Successful child {step.step_id!r} cannot be reused under --failed: {'; '.join(reasons)}. "
                    "Retry without --failed or create a new plan."
                )
            action = "retried" if status != TERMINAL_SUCCESS else "rerun_due_to_drift"
            current_result_ids[step.step_id] = ""
            current_artifact_ids[step.step_id] = ""
        decisions.append(ResumeDecision(step.step_id, action, tuple(dict.fromkeys(reasons)), prior))
    return ResumePlan(prior_run_id, workflow_name, group, current, tuple(decisions), mode, failed_only)


def child_from_checkpoint(row: Mapping[str, Any]):
    if not _checkpoint_success_evidence_valid(row):
        raise FederationResumeError("Cannot reuse an unverified or non-success child checkpoint")
    from .federation_execution import FederatedChildRun
    return FederatedChildRun(
        step_id=str(row.get("step_id") or ""), repository=str(row.get("repository") or ""),
        workflow=str(row.get("workflow") or ""), target=str(row.get("target") or ""), run_id=str(row.get("run_id") or ""),
        parent_node_id=str(row.get("parent_node_id") or ""), status=str(row.get("status") or ""), exit_code=int(row.get("exit_code") or 0),
        repository_identity=dict(row.get("repository_identity") or {}), run_lineage=dict(row.get("run_lineage") or {}),
        operational_ownership=dict(row.get("operational_ownership") or {}), federation_ref=str(row.get("federation_ref") or ""),
        requested_ref=str(row.get("requested_ref") or ""), child_contract=str(row.get("child_contract") or ""),
        contract_ref=str(row.get("contract_ref") or ""), git_lineage=dict(row.get("git_lineage") or {}), lifecycle="reused",
        artifact_inputs=dict(row.get("artifact_inputs") or {}),
        placement=dict(row.get("placement") or {}),
    )


__all__ = [
    "FederationResumeError", "ResumeDecision", "ResumePlan", "build_resume_plan", "checkpoint_path", "checkpoint_report",
    "child_from_checkpoint", "child_result_identity", "execution_contract_identity", "load_checkpoint", "plan_identity",
]
