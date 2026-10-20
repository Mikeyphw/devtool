from __future__ import annotations

"""R06C: read-only cross-repository producer/consumer DAG contract projection.

This is a *view* of FRX's child-owned plan and frozen source-lock authority,
not a second orchestration engine or an external trust attestation.
"""

from hashlib import sha256
import json
from typing import Any

from .federation_lock import (
    FederationSourceLock, FederationSourceLockError,
    compare_federation_source_locks, read_federation_source_lock,
)
from .federation_plan import FederatedPlanGraph
from .models import ProjectConfig


class FederationDAGContractError(ValueError):
    pass


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True).encode("ascii")).hexdigest()


def project_dag_contract(
    parent: ProjectConfig,
    graph: FederatedPlanGraph,
    *,
    require_frozen: bool = False,
) -> dict[str, Any]:
    """Project resolved child-owned edges; refuse missing declared identity.

    ``require_frozen`` only upgrades *local exact-source lock matching*, never
    CI provenance, hosted execution, remote trust, or release qualification.
    """
    order = graph.topological_order()
    by_key = graph.by_key
    current = FederationSourceLock.from_dict(graph.source_lock)
    if not current.lock_digest or not current.local_digest or not current.graph_digest:
        raise FederationDAGContractError("Canonical FRX source-lock identity is incomplete")
    if len(set(graph.repositories)) != len(graph.repositories):
        raise FederationDAGContractError("Duplicate child repository in federation plan")
    lock = read_federation_source_lock(parent)
    comparison = (compare_federation_source_locks(lock, current, policy="fail")
                  if lock is not None else None)
    lock_state = "matching" if comparison and comparison["matched"] else ("drift" if comparison else "absent")
    if require_frozen and lock_state != "matching":
        raise FederationDAGContractError(
            f"Exact frozen federation source lock required: {lock_state}; "
            "run federation plan --freeze only after explicit review"
        )
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for node in order:
        if node.synthetic:
            continue  # the FRX aggregate is not an executable producer
        if node.repository not in graph.repositories:
            raise FederationDAGContractError(f"Node {node.key!r} has unenrolled owner")
        meta = node.metadata
        contract_ref = str(meta.get("contract_ref") or "")
        identity = meta.get("git_ownership")
        if not contract_ref or not isinstance(identity, dict) or not identity:
            raise FederationDAGContractError(f"Node {node.key!r} lacks an owned public contract")
        nodes.append({
            "key": node.key,
            "repository": node.repository,
            "target": node.target,
            "workflow": node.workflow,
            "contract_ref": contract_ref,
            "depends_on": list(node.depends_on),
            "artifact_inputs": list(meta.get("artifact_inputs") or []),
            "execution_contract": dict(meta.get("execution_contract") or {}),
        })
        for dependency in node.depends_on:
            parent_node = by_key[dependency]
            if parent_node.synthetic:
                raise FederationDAGContractError("Executable node depends on synthetic aggregate")
            # Same-repository edges are child-owned. Cross-repository edges are
            # produced by FRX from declared federation steps or capabilities.
            if parent_node.repository != node.repository:
                # Typed handoffs are declared intentions, not verified bytes.
                artifacts = [dict(item) for item in meta.get("artifact_inputs", ())
                             if isinstance(item, dict)
                             and item.get("producer_step") == parent_node.metadata.get("federation_step")]
                edges.append({
                    "artifact_bindings": artifacts,
                    "producer": parent_node.key,
                    "producer_repository": parent_node.repository,
                    "consumer": node.key,
                    "consumer_repository": node.repository,
                    "basis": "resolved_federation_dependency",
                })
    core = {
        "schema_version": 1,
        "kind": "federation_producer_consumer_dag_contract",
        "subject_kind": "group" if graph.group else "workflow",
        "workflow": graph.workflow,
        "group": graph.group,
        "repositories": list(graph.repositories),
        "nodes": nodes,
        "cross_repository_edges": edges,
        "source_lock_digest": current.lock_digest,
        "graph_digest": current.graph_digest,
    }
    return {
        **core,
        "contract_digest": _digest(core),
        "frozen_source_lock": {
            "status": lock_state,
            "required": require_frozen,
            "mode": "strict-local",
            "comparison": comparison,
        },
        "status": "locally_frozen" if lock_state == "matching" else "plan_only",
        "execution_performed": False,
        "cross_repo_consumer_qualified": False,
        "hosted_qualified": False,
        "device_qualified": False,
        "release_qualified": False,
        "provenance_note": "FRX source/graph projection, not an authenticated remote or execution receipt",
    }


def render_dag_contract_ascii(contract: dict[str, Any], *, width: int = 40) -> str:
    """Losslessly wrap producer/consumer IDs for a 40-column Termux terminal."""
    import textwrap

    def add(prefix: str, value: str) -> None:
        chunks = textwrap.wrap(value, width=max(8, width - len(prefix)),
                               break_long_words=True, break_on_hyphens=False,
                               drop_whitespace=False, replace_whitespace=False)
        lines.extend([prefix + (chunks[0] if chunks else "-")]
                     + [" " * len(prefix) + c for c in chunks[1:]])

    lines = ["FRX producer -> consumer (plan only)"]
    add("Workflow: ", str(contract["workflow"]))
    add("Frozen: ", str(contract["frozen_source_lock"]["status"]))
    for edge in contract["cross_repository_edges"]:
        add("  from: ", edge["producer"])
        add("    to: ", edge["consumer"])
    if not contract["cross_repository_edges"]:
        lines.append("No cross-repository edges")
    lines.append("No run/hosted/release qualification")
    return "\n".join(lines) + "\n"


__all__ = ["FederationDAGContractError", "project_dag_contract", "render_dag_contract_ascii"]
