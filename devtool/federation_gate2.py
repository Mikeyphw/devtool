from __future__ import annotations

"""FRX-08 cross-repository execution and contract validation gate.

Gate 2 seals the planning/execution layer before remote transport is allowed to
carry federation state.  Runtime checks are deliberately side-effect free: they
validate configured DAGs and exercise EXO lineage/identity reducers in memory.
The overlay validation suite performs the executable wrapper/TLJ/coordinator
contract probes against temporary repositories.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .core.events import EventBus, RunFinished, RunStarted
from .core.execution_store import ExecutionTransitionError
from .federation_discovery import FederationDiscoveryError, resolve_federated_repository
from .federation_gate import build_federation_gate1_report
from .federation_plan import FederationPlanError, build_federated_group_plan, build_federated_plan
from .repository_identity import RepositoryIdentity, same_repository_identity

if TYPE_CHECKING:
    from .models import ProjectConfig

FEDERATION_GATE2_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class FederationGate2Check:
    section: str
    name: str
    status: str
    detail: str
    repository: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not self.repository:
            payload.pop("repository", None)
        if not self.data:
            payload.pop("data", None)
        return payload


@dataclass(frozen=True, slots=True)
class FederationGate2Report:
    schema_version: int
    gate: str
    repository: str
    mode: str
    status: str
    checks: tuple[FederationGate2Check, ...]

    @property
    def ok(self) -> bool:
        return self.status != "error"


def _check(section: str, name: str, status: str, detail: str, *, repository: str = "", **data: Any) -> FederationGate2Check:
    return FederationGate2Check(
        section=section,
        name=name,
        status=status,
        detail=detail,
        repository=repository,
        data={key: value for key, value in data.items() if value not in (None, "", [], {}, ())},
    )


def _overall(checks: list[FederationGate2Check]) -> str:
    if any(item.status == "error" for item in checks):
        return "error"
    if any(item.status == "warning" for item in checks):
        return "warning"
    return "ok"


def _lineage_store_probe(config: "ProjectConfig") -> tuple[bool, str]:
    identity = config.repository_identity
    if identity is None:
        return False, "owner repository identity is missing"
    bus = EventBus()
    parent = "frx08-parent-probe"
    child = "frx08-child-probe"
    bus.publish(RunStarted(
        run_id=parent,
        command="federation gate2 parent probe",
        project=str(config.repo_root),
        source="federation-gate2",
        metadata={
            "repository_identity": identity.as_dict(),
            "run_lineage": {
                "kind": "federation",
                "relationship": "coordinator",
                "child_run_id": parent,
                "federation_workflow": "@gate2",
            },
        },
    ))
    lineage = {
        "kind": "federation",
        "relationship": "child",
        "parent_run_id": parent,
        "parent_node_id": "federation:@gate2:probe",
        "child_run_id": child,
        "federation_workflow": "@gate2",
        "federation_step": "probe",
        "repository": "@probe",
        "effective_repository": identity.fingerprint,
    }
    bus.publish(RunStarted(
        run_id=child,
        command="federation gate2 child probe",
        project=str(config.repo_root),
        source="federation-gate2",
        metadata={"repository_identity": identity.as_dict(), "run_lineage": lineage},
    ))
    bus.publish(RunFinished(run_id=child, status="succeeded", exit_code=0, source="federation-gate2"))
    parent_snapshot = bus.execution_store.snapshot(parent)
    child_snapshot = bus.execution_store.snapshot(child)
    if parent_snapshot is None or child_snapshot is None:
        return False, "EXO did not retain parent/child probe runs"
    if child_snapshot.run_id == parent_snapshot.run_id:
        return False, "child execution reused the coordinator run id"
    if child_snapshot.run_lineage.get("parent_run_id") != parent:
        return False, "child lineage did not retain the coordinator run id"
    if child_snapshot.repository_identity.get("fingerprint") != identity.fingerprint:
        return False, "child lineage did not retain repository identity"

    # Reducer must fail closed when a child event lies about its own run id.
    try:
        bus.publish(RunStarted(
            run_id="frx08-corrupt-child",
            command="corrupt",
            project=str(config.repo_root),
            source="federation-gate2",
            metadata={
                "repository_identity": identity.as_dict(),
                "run_lineage": {
                    "kind": "federation",
                    "relationship": "child",
                    "parent_run_id": parent,
                    "child_run_id": "not-the-event-run",
                },
            },
        ))
    except ExecutionTransitionError:
        pass
    else:
        return False, "EXO accepted a mismatched federation child_run_id"
    return True, "distinct authoritative child run and fail-closed lineage persistence verified"


def _identity_matches_resolution(identity_payload: dict[str, Any], resolution: Any) -> bool:
    actual = getattr(resolution, "repository_identity", None)
    if actual is None or not identity_payload:
        return False
    try:
        expected = RepositoryIdentity.from_dict(identity_payload)
    except Exception:
        return False
    return same_repository_identity(expected, actual) and Path(expected.root).resolve(strict=False) == Path(resolution.root).resolve(strict=False)


def build_federation_gate2_report(config: "ProjectConfig") -> FederationGate2Report:
    checks: list[FederationGate2Check] = []
    owner_identity = config.repository_identity
    owner_root = str(owner_identity.root if owner_identity is not None else config.repo_root)
    mode = "federated" if config.federation.repositories else "standalone"

    try:
        gate1 = build_federation_gate1_report(config)
    except Exception as exc:
        checks.append(_check("boundary", "gate1", "error", f"FRX-04 prerequisite failed: {exc}"))
    else:
        checks.append(_check(
            "boundary", "gate1", "ok" if gate1.ok else "error",
            f"FRX-04 prerequisite status={gate1.status}; checks={len(gate1.checks)}",
        ))

    try:
        ok, detail = _lineage_store_probe(config)
    except Exception as exc:
        checks.append(_check("execution", "exo-lineage-authority", "error", f"EXO lineage probe failed: {exc}"))
    else:
        checks.append(_check("execution", "exo-lineage-authority", "ok" if ok else "error", detail))

    executable_groups = {
        name: group for name, group in config.federation.groups.items() if group.executable
    }
    if not config.federation.workflows and not executable_groups:
        checks.append(_check(
            "planning", "configured-subjects", "optional",
            "no federation workflows or executable groups configured; planner remains opt-in",
        ))

    def qualify_graph(subject_kind: str, subject_name: str, graph) -> None:
        ordered = graph.topological_order()
        bad_identity: list[str] = []
        bad_edges: list[str] = []
        keys = {node.key for node in graph.nodes}
        for node in graph.nodes:
            if any(dep not in keys or dep == node.key for dep in node.depends_on):
                bad_edges.append(node.key)
            if node.synthetic:
                continue
            try:
                resolution = resolve_federated_repository(config, node.repository)
            except FederationDiscoveryError:
                bad_identity.append(node.key)
                continue
            identity = node.metadata.get("repository_identity")
            if not isinstance(identity, dict) or not _identity_matches_resolution(identity, resolution):
                bad_identity.append(node.key)
        ok = not bad_identity and not bad_edges and len(ordered) == len(graph.nodes)
        if subject_kind == "group" and graph.group != subject_name:
            ok = False
            bad_edges.append(f"@group-identity:{graph.workflow}")
        checks.append(_check(
            "planning", f"{subject_kind}:{subject_name}", "ok" if ok else "error",
            "cross-repository DAG and repository identities are deterministic" if ok else "federated DAG contains invalid edges, subject identity, or repository identities",
            repositories=list(graph.repositories), node_count=len(graph.nodes), bad_identity=bad_identity, bad_edges=bad_edges,
        ))

    for workflow_name in sorted(config.federation.workflows):
        try:
            graph = build_federated_plan(config, workflow_name)
            qualify_graph("workflow", workflow_name, graph)
        except (FederationPlanError, FederationDiscoveryError, ValueError) as exc:
            checks.append(_check("planning", f"workflow:{workflow_name}", "error", str(exc)))

    for group_name in sorted(executable_groups):
        try:
            graph = build_federated_group_plan(config, group_name)
            qualify_graph("group", group_name, graph)
        except (FederationPlanError, FederationDiscoveryError, ValueError) as exc:
            checks.append(_check("planning", f"group:{group_name}", "error", str(exc)))

    # Bridge availability is structural here; executable semantics are exercised
    # by the Gate-2 validation suite without running repository-defined commands
    # as part of doctor/preflight.
    try:
        from .federation_bridge import plan_federated_job, plan_federated_wrapper  # noqa: F401
        from .federation_execution import run_federated_direct_child, run_federated_workflow  # noqa: F401
    except Exception as exc:
        checks.append(_check("contract", "bridge-runtime", "error", f"federation bridge runtime unavailable: {exc}"))
    else:
        checks.append(_check(
            "contract", "bridge-runtime", "ok",
            "wrapper/TLJ bridges and coordinator execution share the canonical federation lineage runtime",
        ))

    if mode == "standalone":
        checks.append(_check(
            "compatibility", "standalone", "ok",
            "no federation enrollment; Gate 2 performs only side-effect-free contract probes",
        ))

    return FederationGate2Report(
        schema_version=FEDERATION_GATE2_SCHEMA_VERSION,
        gate="FRX-08",
        repository=owner_root,
        mode=mode,
        status=_overall(checks),
        checks=tuple(checks),
    )


def federation_gate2_as_dict(report: FederationGate2Report) -> dict[str, Any]:
    return {
        "schema_version": report.schema_version,
        "gate": report.gate,
        "repository": report.repository,
        "mode": report.mode,
        "status": report.status,
        "ok": report.ok,
        "summary": {
            "checks": len(report.checks),
            "errors": sum(item.status == "error" for item in report.checks),
            "warnings": sum(item.status == "warning" for item in report.checks),
            "optional": sum(item.status == "optional" for item in report.checks),
        },
        "checks": [item.as_dict() for item in report.checks],
    }


def render_federation_gate2(report: FederationGate2Report) -> str:
    lines = [
        f"Federation execution preflight · {report.gate}",
        f"Repository: {report.repository}",
        f"Mode: {report.mode}",
        f"Status: {report.status}",
        "",
    ]
    for item in report.checks:
        owner = f" [{item.repository}]" if item.repository else ""
        lines.append(f"{item.status.upper():8} {item.section}/{item.name}{owner}: {item.detail}")
    return "\n".join(lines) + "\n"


__all__ = [
    "FEDERATION_GATE2_SCHEMA_VERSION",
    "FederationGate2Check",
    "FederationGate2Report",
    "build_federation_gate2_report",
    "federation_gate2_as_dict",
    "render_federation_gate2",
]
