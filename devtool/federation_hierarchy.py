from __future__ import annotations

"""FRX-Y10 hierarchical federation composition.

A parent federation owns only its directly enrolled repository.  Nested
federation workflows/groups are therefore represented as one opaque child node;
this module validates the child-owned composition contract and cycles without
flattening grandchild execution nodes into the parent graph.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .federation_discovery import resolve_federated_repository
from .models import FederatedWorkflowStep, ProjectConfig


class FederationHierarchyError(ValueError):
    pass


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _load_child(parent: ProjectConfig, repository: str) -> ProjectConfig:
    resolution = resolve_federated_repository(parent, repository)
    if not resolution.available:
        raise FederationHierarchyError(
            f"Hierarchical federation requires available directly enrolled repository {repository!r}: {resolution.root}"
        )
    from .config import load_project_config
    try:
        child = load_project_config(
            resolution.root,
            resolution.config_path or None,
            target_name="",
            allow_ambiguous_target=True,
            allow_ambiguous_project_kind=True,
            allow_parent_config=False,
        )
    except SystemExit as exc:
        raise FederationHierarchyError(f"Cannot load hierarchical federation repository {repository!r}: {exc}") from exc
    return child


def _subject_steps(config: ProjectConfig, kind: str, name: str) -> tuple[FederatedWorkflowStep, ...]:
    if kind == "federation":
        workflow = config.federation.workflows.get(name)
        if workflow is None:
            available = ", ".join(sorted(config.federation.workflows)) or "<none>"
            raise FederationHierarchyError(
                f"Nested federation workflow {name!r} does not exist in {config.repo_root}. Available: {available}"
            )
        return workflow.steps
    if kind == "group":
        from .federation_plan import materialize_federation_group
        try:
            return materialize_federation_group(config, name).steps
        except Exception as exc:
            raise FederationHierarchyError(str(exc)) from exc
    raise FederationHierarchyError(f"Unsupported hierarchical federation contract type {kind!r}")


def _subject_identity(config: ProjectConfig, kind: str, name: str) -> str:
    identity = config.repository_identity
    fingerprint = str(identity.fingerprint if identity is not None else Path(config.repo_root).resolve(strict=False))
    return f"{fingerprint}::{kind}:{name}"


def _workflow_payload(steps: Iterable[FederatedWorkflowStep]) -> list[dict[str, Any]]:
    return [
        {
            "id": step.step_id,
            "ref": step.canonical_ref(step.target),
            "depends_on": list(step.depends_on),
            "contract_type": step.contract_type,
            "contract_name": step.contract_name,
            "stage": step.stage,
            "inputs": [item.as_dict() for item in step.artifact_inputs],
        }
        for step in steps
    ]


def _validate_cycles(config: ProjectConfig, kind: str, name: str, stack: tuple[str, ...]) -> tuple[str, ...]:
    identity = _subject_identity(config, kind, name)
    if identity in stack:
        start = stack.index(identity)
        cycle = (*stack[start:], identity)
        raise FederationHierarchyError(
            "hierarchical federation cycle detected: " + " -> ".join(cycle)
        )
    next_stack = (*stack, identity)
    direct: list[str] = []
    for step in _subject_steps(config, kind, name):
        if not step.hierarchical:
            continue
        child = _load_child(config, step.repository)
        direct.append(step.canonical_ref())
        _validate_cycles(child, step.contract_type, step.contract_name, next_stack)
    return tuple(direct)


@dataclass(frozen=True, slots=True)
class HierarchicalFederationContract:
    repository: str
    kind: str
    name: str
    canonical_ref: str
    child_root: str
    repository_identity: dict[str, Any]
    subject_digest: str
    direct_nested_refs: tuple[str, ...]
    ownership_depth: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "hierarchical-federation-contract",
            "repository": self.repository,
            "subject_kind": self.kind,
            "subject": self.name,
            "canonical_ref": self.canonical_ref,
            "child_root": self.child_root,
            "repository_identity": dict(self.repository_identity),
            "subject_digest": self.subject_digest,
            "direct_nested_refs": list(self.direct_nested_refs),
            "ownership_depth": self.ownership_depth,
            "flattened_grandchildren": False,
            "reentry_boundary": "direct-child-federation",
        }


def resolve_hierarchical_contract(parent: ProjectConfig, step: FederatedWorkflowStep) -> HierarchicalFederationContract:
    if not step.hierarchical:
        raise FederationHierarchyError(f"Step {step.step_id!r} is not a hierarchical federation contract")
    child = _load_child(parent, step.repository)
    steps = _subject_steps(child, step.contract_type, step.contract_name)
    direct_nested = _validate_cycles(child, step.contract_type, step.contract_name, ())
    identity = child.repository_identity.as_dict() if child.repository_identity else {}
    payload = {
        "repository_identity": str(identity.get("fingerprint") or identity.get("root") or child.repo_root),
        "kind": step.contract_type,
        "name": step.contract_name,
        "steps": _workflow_payload(steps),
    }
    return HierarchicalFederationContract(
        repository=step.repository,
        kind=step.contract_type,
        name=step.contract_name,
        canonical_ref=step.canonical_ref(),
        child_root=str(Path(child.repo_root).resolve(strict=False)),
        repository_identity=identity,
        subject_digest=_digest(payload),
        direct_nested_refs=direct_nested,
    )


__all__ = [
    "FederationHierarchyError",
    "HierarchicalFederationContract",
    "resolve_hierarchical_contract",
]
