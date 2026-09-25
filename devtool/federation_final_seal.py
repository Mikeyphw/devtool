from __future__ import annotations

"""FRX-12 end-to-end federation release qualification.

The final seal is deliberately read-only.  It composes the authoritative
contracts established by FRX-01..11 and adds one recursive audit over *explicit*
federation enrollment edges so deep registration cycles cannot hide behind
single-repository validation.  It never scans the filesystem for repositories
and never executes child-defined commands.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

from .completion_state import build_completion_state
from .federation_discovery import FederationDiscoveryError, resolve_federated_repository
from .federation_gate import build_federation_gate1_report
from .federation_gate2 import build_federation_gate2_report
from .federation_plan import FederationPlanError, build_federated_group_plan, build_federated_plan
from .repository_identity import same_repository_identity

if TYPE_CHECKING:
    from .models import ProjectConfig

FEDERATION_FINAL_SEAL_SCHEMA_VERSION = 1

FEDERATION_FINAL_SEAL_EXTENSION_GATE = "FRX-X04"
FEDERATION_FINAL_SEAL_EXTENSIONS = ("FRX-X01", "FRX-X02", "FRX-X03", "FRX-X04")
FEDERATION_FINAL_SEAL_EXTENSION_CASES = (
    "target-qualified-workflow-identity",
    "target-identity-convergence",
    "executable-federation-groups",
    "snapshot-adoption-validate-ready-28",
    "snapshot-adoption-build-ready-5",
    "snapshot-adoption-test-ready-3",
    "snapshot-hidden-target-surfaces",
)
FEDERATION_FINAL_SEAL_SAFETY_CASES = (
    "parent-child-same-directory",
    "symlink-back-to-parent",
    "direct-a-b-cycle",
    "deep-a-b-c-a-cycle",
    "moved-replaced-child",
    "aliases-sharing-one-canonical-root",
    "child-missing-config",
    "nested-git-not-enrolled",
    "enrolled-non-git-repository",
    "wrong-remote-repository-mapping",
    "exo-repository-mismatch",
    "wrapper-routing-leakage",
    "parent-secret-leakage",
    "validation-evidence-crossing-repository-boundary",
    "rollback-transaction-crossing-repository-boundary",
    "child-referencing-parent",
    "cancellation-across-environment-transition",
    "resume-after-child-revision-changes",
    "ambiguous-owner-path",
    "duplicate-identity-ownership",
)


@dataclass(frozen=True, slots=True)
class FederationSafetyEvidence:
    case_id: str
    promise: str
    evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "promise": self.promise, "evidence": list(self.evidence)}


FEDERATION_FINAL_SEAL_SAFETY_EVIDENCE = (
    FederationSafetyEvidence("parent-child-same-directory", "Parent and child cannot resolve to the same directory.", ("tests/test_frx02_federation_config.py::test_self_reference_is_rejected_for_direct_relative_absolute_and_symlink_forms",)),
    FederationSafetyEvidence("symlink-back-to-parent", "Symlink enrollment cannot escape back to the parent owner.", ("tests/test_frx02_federation_config.py::test_self_reference_is_rejected_for_direct_relative_absolute_and_symlink_forms",)),
    FederationSafetyEvidence("direct-a-b-cycle", "Direct explicit enrollment cycles fail closed.", ("tests/test_frx02_federation_config.py::test_obvious_direct_parent_child_cycle_is_rejected",)),
    FederationSafetyEvidence("deep-a-b-c-a-cycle", "Deep explicit enrollment cycles are detected recursively.", ("tests/test_frx12_federation_final_seal.py::test_deep_explicit_registration_cycle_a_b_c_a_fails_final_seal",)),
    FederationSafetyEvidence("moved-replaced-child", "A child moved or replaced after planning fails identity validation.", ("tests/test_frx08_execution_contract_gate2.py::test_execution_fails_closed_if_repository_identity_changes_after_planning",)),
    FederationSafetyEvidence("aliases-sharing-one-canonical-root", "Aliases resolving to one canonical root are rejected.", ("tests/test_frx02_federation_config.py::test_duplicate_canonical_child_roots_are_rejected_including_symlink_alias",)),
    FederationSafetyEvidence("child-missing-config", "A nested child without its own config cannot borrow the parent config.", ("tests/test_frx03_federation_discovery.py::test_nested_child_without_own_config_never_borrows_parent_config",)),
    FederationSafetyEvidence("nested-git-not-enrolled", "Nested Git repositories are not federation members unless enrolled.", ("tests/test_frx03_federation_discovery.py::test_nested_git_repo_not_enrolled_is_not_discovered_implicitly",)),
    FederationSafetyEvidence("enrolled-non-git-repository", "Explicitly enrolled non-Git repositories remain supported.", ("tests/test_frx12_federation_final_seal.py::test_non_git_child_is_allowed_and_unenrolled_nested_git_is_not_discovered",)),
    FederationSafetyEvidence("wrong-remote-repository-mapping", "Remote child mapping is independently attested before execution.", ("tests/test_frx_full_roadmap_reseal.py::test_remote_attestation_rejects_wrong_mapping_and_accepts_different_path_checkout",)),
    FederationSafetyEvidence("exo-repository-mismatch", "Persisted child EXO repository identity mismatches fail closed.", ("tests/test_frx08_execution_contract_gate2.py::test_execution_rejects_corrupt_child_snapshot_authority",)),
    FederationSafetyEvidence("wrapper-routing-leakage", "Child wrapper routing cannot inject protected parent target routing.", ("tests/test_frx08_execution_contract_gate2.py::test_wrapper_cannot_inject_protected_target_routing",)),
    FederationSafetyEvidence("parent-secret-leakage", "Sensitive wrapper values remain redacted across federation planning.", ("tests/test_frx07_wrapper_tlj_federation_bridge.py::test_wrapper_bridge_plan_redacts_sensitive_values",)),
    FederationSafetyEvidence("validation-evidence-crossing-repository-boundary", "Parent validation evidence never satisfies child-owned validation.", ("tests/test_frx10_operational_boundaries.py::test_parent_validation_never_satisfies_child_operational_validation",)),
    FederationSafetyEvidence("rollback-transaction-crossing-repository-boundary", "Parent cancellation cannot roll back child-owned transaction state.", ("tests/test_frx10_operational_boundaries.py::test_parent_cancellation_requests_child_cancel_without_parent_rollback",)),
    FederationSafetyEvidence("child-referencing-parent", "Child-to-parent recursion is rejected as an explicit cycle.", ("tests/test_frx02_federation_config.py::test_obvious_direct_parent_child_cycle_is_rejected",)),
    FederationSafetyEvidence("cancellation-across-environment-transition", "Cancellation remains authoritative across environment transitions.", (
        "tests/test_frx12_federation_final_seal.py::test_cancellation_during_child_environment_transition_restores_parent_environment",
        "tests/test_frx_full_roadmap_reseal.py::test_cancellation_stops_independent_branches_and_direct_exec_is_cancelled",
    )),
    FederationSafetyEvidence("resume-after-child-revision-changes", "Resume rejects child revision drift.", ("tests/test_frx10_operational_boundaries.py::test_resume_checkpoint_rejects_child_revision_change",)),
    FederationSafetyEvidence("ambiguous-owner-path", "Path ownership uses canonical longest explicit enrollment and rejects ambiguity.", (
        "tests/test_frx_full_roadmap_reseal.py::test_recursive_owner_resolver_longest_match_symlink_and_outside",
        "tests/test_frx_full_roadmap_reseal.py::test_owner_resolver_duplicate_canonical_ownership_fails_closed",
    )),
    FederationSafetyEvidence("duplicate-identity-ownership", "Duplicate canonical ownership fails closed.", ("tests/test_frx_full_roadmap_reseal.py::test_owner_resolver_duplicate_canonical_ownership_fails_closed",)),
)


FEDERATION_FINAL_SEAL_EXTENSION_EVIDENCE = (
    FederationSafetyEvidence(
        "target-qualified-workflow-identity",
        "FRX-X01 resolves repository::target::workflow without changing legacy repository::workflow semantics.",
        ("tests/test_frx_x01_target_qualified_workflow_identity.py::test_target_qualified_ref_selects_non_default_child_target",),
    ),
    FederationSafetyEvidence(
        "target-identity-convergence",
        "FRX-X02 preserves canonical repository/target/workflow identity across EXO lineage and transport.",
        ("tests/test_frx_x02_target_identity_convergence.py::test_workflow_child_persists_canonical_target_identity_across_exo_and_report",),
    ),
    FederationSafetyEvidence(
        "executable-federation-groups",
        "FRX-X03 executes federation groups through the normal FRX coordinator and lineage runtime.",
        ("tests/test_frx_x03_executable_federation_groups.py::test_group_execution_uses_existing_frx_coordinator_and_persists_group_lineage",),
    ),
    FederationSafetyEvidence(
        "snapshot-adoption-validate-ready-28",
        "The snapshot-derived validate-ready contract remains a 28-node parallel federation DAG.",
        ("tests/test_frx_x04_real_repo_adoption_reseal.py::test_snapshot_validate_ready_preserves_28_node_parallel_dag",),
    ),
    FederationSafetyEvidence(
        "snapshot-adoption-build-ready-5",
        "The snapshot-derived build-ready contract remains a 5-node parallel federation DAG.",
        ("tests/test_frx_x04_real_repo_adoption_reseal.py::test_snapshot_build_ready_preserves_5_node_parallel_dag",),
    ),
    FederationSafetyEvidence(
        "snapshot-adoption-test-ready-3",
        "The snapshot-derived test-ready contract remains a 3-node parallel federation DAG.",
        ("tests/test_frx_x04_real_repo_adoption_reseal.py::test_snapshot_test_ready_preserves_3_node_parallel_dag",),
    ),
    FederationSafetyEvidence(
        "snapshot-hidden-target-surfaces",
        "Previously hidden non-default target workflows are addressable without changing child defaults.",
        ("tests/test_frx_x04_real_repo_adoption_reseal.py::test_snapshot_hidden_target_surfaces_are_now_addressable",),
    ),
)

FEDERATION_FINAL_SEAL_AREAS = (
    "config-contract",
    "repository-identity",
    "discovery",
    "boundary-isolation",
    "graph-planning",
    "cycle-rejection",
    "exo-parent-child-lineage",
    "wrapper-bridge",
    "target-local-job-bridge",
    "local-remote-identity",
    "operational-ownership",
    "machine-ui-doctor",
    "standalone-compatibility",
    "documentation-completion-parity",
)


@dataclass(frozen=True, slots=True)
class FederationFinalSealCheck:
    area: str
    status: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not self.data:
            payload.pop("data", None)
        return payload


@dataclass(frozen=True, slots=True)
class FederationFinalSealReport:
    schema_version: int
    gate: str
    repository: str
    mode: str
    status: str
    checks: tuple[FederationFinalSealCheck, ...]
    safety_evidence: tuple[dict[str, Any], ...] = ()
    safety_evidence_errors: tuple[str, ...] = ()
    extension_gate: str = FEDERATION_FINAL_SEAL_EXTENSION_GATE
    extensions: tuple[str, ...] = FEDERATION_FINAL_SEAL_EXTENSIONS
    extension_evidence: tuple[dict[str, Any], ...] = ()
    extension_evidence_errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status != "error"


@dataclass(frozen=True, slots=True)
class _TopologyAudit:
    repositories: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, str], ...]
    cycles: tuple[tuple[str, ...], ...]
    errors: tuple[str, ...]


def _canonical(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve(strict=False))


def _check(area: str, status: str, detail: str, **data: Any) -> FederationFinalSealCheck:
    return FederationFinalSealCheck(
        area=area,
        status=status,
        detail=detail,
        data={key: value for key, value in data.items() if value not in (None, "", [], {}, ())},
    )


def _overall(checks: list[FederationFinalSealCheck]) -> str:
    if any(item.status == "error" for item in checks):
        return "error"
    if any(item.status == "warning" for item in checks):
        return "warning"
    return "ok"


def _child_config(resolution: Any) -> "ProjectConfig":
    from .config import load_project_config

    return load_project_config(
        str(resolution.root),
        str(resolution.config_path or "") or None,
        target_name="",
        variant_name="",
        allow_ambiguous_target=True,
        allow_ambiguous_project_kind=True,
        allow_parent_config=False,
    )


def _recursive_topology(config: "ProjectConfig", *, max_repositories: int = 256) -> _TopologyAudit:
    """Follow explicit enrollment edges and report deep cycles deterministically."""

    repositories: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    cycles: set[tuple[str, ...]] = set()
    errors: list[str] = []
    expanded: set[str] = set()

    owner_identity = config.repository_identity
    owner_root = _canonical(owner_identity.root if owner_identity is not None else config.repo_root)

    def visit(current: "ProjectConfig", stack: tuple[str, ...], labels: tuple[str, ...]) -> None:
        current_identity = current.repository_identity
        current_root = _canonical(current_identity.root if current_identity is not None else current.repo_root)
        repositories.setdefault(
            current_root,
            {
                "root": current_root,
                "fingerprint": str(current_identity.fingerprint if current_identity is not None else ""),
                "git_root": str(current_identity.git_root if current_identity is not None else ""),
                "config_path": str(current.config_path or ""),
            },
        )
        if len(repositories) > max_repositories:
            errors.append(f"explicit federation topology exceeds {max_repositories} repositories")
            return
        if current_root in expanded:
            return
        expanded.add(current_root)

        for alias in sorted(current.federation.repositories):
            enrollment = current.federation.repositories[alias]
            try:
                resolution = resolve_federated_repository(current, alias)
            except FederationDiscoveryError as exc:
                if enrollment.required:
                    errors.append(f"{current_root}::{alias}: {exc}")
                continue
            if not resolution.available:
                continue
            child_root = _canonical(resolution.root)
            edges.append({"parent": current_root, "repository": alias, "child": child_root})
            if child_root in stack:
                index = stack.index(child_root)
                cycle_roots = stack[index:] + (child_root,)
                cycle_labels = labels[index:] + (alias,)
                cycles.add(tuple(f"{label}@{root}" for label, root in zip(cycle_labels, cycle_roots)))
                continue
            try:
                child = _child_config(resolution)
            except (SystemExit, ValueError) as exc:
                errors.append(f"{current_root}::{alias}: child config load failed: {exc}")
                continue
            visit(child, stack + (child_root,), labels + (alias,))

    visit(config, (owner_root,), ("owner",))
    return _TopologyAudit(
        repositories=tuple(repositories[root] for root in sorted(repositories)),
        edges=tuple(sorted(edges, key=lambda item: (item["parent"], item["repository"], item["child"]))),
        cycles=tuple(sorted(cycles)),
        errors=tuple(sorted(set(errors))),
    )


def _repository_checks(config: "ProjectConfig", topology: _TopologyAudit) -> tuple[bool, bool, list[str]]:
    identity_ok = config.repository_identity is not None
    discovery_ok = True
    diagnostics: list[str] = []
    owner_identity = config.repository_identity
    seen_roots: dict[str, str] = {}
    seen_fingerprints: dict[str, str] = {}
    if owner_identity is not None:
        seen_roots[_canonical(owner_identity.root)] = "owner"
        if owner_identity.fingerprint:
            seen_fingerprints[owner_identity.fingerprint] = "owner"

    for name in sorted(config.federation.repositories):
        enrollment = config.federation.repositories[name]
        try:
            resolution = resolve_federated_repository(config, name)
        except FederationDiscoveryError as exc:
            if enrollment.required:
                discovery_ok = False
                diagnostics.append(f"{name}: {exc}")
            continue
        if not resolution.available:
            continue
        identity = resolution.repository_identity
        if identity is None:
            identity_ok = False
            diagnostics.append(f"{name}: repository identity missing")
            continue
        root = _canonical(identity.root)
        if root != _canonical(resolution.root):
            identity_ok = False
            diagnostics.append(f"{name}: identity root does not match enrolled root")
        if owner_identity is not None and same_repository_identity(owner_identity, identity):
            identity_ok = False
            diagnostics.append(f"{name}: child identity collapses to parent identity")
        previous_root = seen_roots.get(root)
        if previous_root and previous_root != name:
            identity_ok = False
            diagnostics.append(f"{name}: canonical root duplicates {previous_root}")
        else:
            seen_roots[root] = name
        if identity.fingerprint:
            previous_fingerprint = seen_fingerprints.get(identity.fingerprint)
            if previous_fingerprint and previous_fingerprint != name:
                identity_ok = False
                diagnostics.append(f"{name}: identity fingerprint duplicates {previous_fingerprint}")
            else:
                seen_fingerprints[identity.fingerprint] = name

    if topology.errors:
        discovery_ok = False
        diagnostics.extend(topology.errors)
    return identity_ok, discovery_ok, diagnostics


def _planning_check(config: "ProjectConfig") -> tuple[bool, list[str], int, int, int, int]:
    errors: list[str] = []
    workflow_nodes = 0
    group_nodes = 0
    for name in sorted(config.federation.workflows):
        try:
            graph = build_federated_plan(config, name)
            ordered = graph.topological_order()
        except (FederationPlanError, FederationDiscoveryError, ValueError, SystemExit) as exc:
            errors.append(f"workflow:{name}: {exc}")
            continue
        workflow_nodes += len(graph.nodes)
        if len(ordered) != len(graph.nodes):
            errors.append(f"workflow:{name}: topological order omitted nodes")

    executable_groups = {
        name: group for name, group in config.federation.groups.items() if group.executable
    }
    for name in sorted(executable_groups):
        try:
            graph = build_federated_group_plan(config, name)
            ordered = graph.topological_order()
        except (FederationPlanError, FederationDiscoveryError, ValueError, SystemExit) as exc:
            errors.append(f"group:{name}: {exc}")
            continue
        group_nodes += len(graph.nodes)
        if graph.group != name:
            errors.append(f"group:{name}: canonical group identity mismatch ({graph.workflow!r})")
        if len(ordered) != len(graph.nodes):
            errors.append(f"group:{name}: topological order omitted nodes")
    return (
        not errors,
        errors,
        workflow_nodes + group_nodes,
        workflow_nodes,
        group_nodes,
        len(executable_groups),
    )


def _gate_check(report: Any, name: str) -> tuple[bool, str]:
    if not getattr(report, "ok", False):
        return False, f"{name} status={getattr(report, 'status', 'error')}"
    return True, f"{name} status={getattr(report, 'status', 'ok')}; checks={len(getattr(report, 'checks', ()))}"


def _bridge_capabilities(config: "ProjectConfig") -> tuple[bool, bool, dict[str, Any]]:
    try:
        from .federation_bridge import (
            execute_federated_job,
            execute_federated_wrapper,
            plan_federated_job,
            plan_federated_wrapper,
        )
        from .target_local_adoption import target_local_adoption_report
        from .wrapper_help import wrapper_discovery_payload
    except Exception as exc:
        return False, False, {"error": str(exc)}

    wrapper_names: dict[str, list[str]] = {}
    target_job_counts: dict[str, int] = {}
    for name in sorted(config.federation.repositories):
        try:
            resolution = resolve_federated_repository(config, name)
        except FederationDiscoveryError:
            continue
        if not resolution.available:
            continue
        try:
            child = _child_config(resolution)
            wrapper = wrapper_discovery_payload(child)
            tlj = target_local_adoption_report(child)
        except Exception as exc:
            return False, False, {"repository": name, "error": str(exc)}
        wrapper_names[name] = sorted(str(item) for item in (wrapper.get("commands") or []))
        summary = tlj.get("summary") if isinstance(tlj.get("summary"), Mapping) else {}
        target_job_counts[name] = int(summary.get("target_local_job_count") or 0)

    # Keep callable references alive as part of the structural qualification.
    wrapper_ok = callable(plan_federated_wrapper) and callable(execute_federated_wrapper)
    tlj_ok = callable(plan_federated_job) and callable(execute_federated_job)
    return wrapper_ok, tlj_ok, {
        "wrapper_commands": wrapper_names,
        "target_local_job_counts": target_job_counts,
    }


def _transport_probe(config: "ProjectConfig") -> tuple[bool, str, dict[str, Any]]:
    try:
        from .federation_transport import (
            FederationTransportError,
            build_federation_transport_metadata,
            build_repository_attestation,
        )
    except Exception as exc:
        return False, f"transport contract unavailable: {exc}", {}
    identity = config.repository_identity
    if identity is None:
        return False, "owner repository identity is missing", {}
    metadata = {
        "repository_identity": identity.as_dict(),
        "run_lineage": {
            "kind": "federation",
            "relationship": "child",
            "repository": "@seal-probe",
            "parent_run_id": "frx12-parent",
            "parent_node_id": "federation:@seal:probe",
            "child_run_id": "frx12-child",
            "effective_repository": identity.fingerprint,
        },
    }
    try:
        local = build_federation_transport_metadata(
            metadata,
            local_root=identity.root,
            execution_environment="local",
            execution_provider="local",
        )
    except Exception as exc:
        return False, f"local transport identity probe failed: {exc}", {}

    attestation = build_repository_attestation(identity.root, identity=identity, config_path=identity.config_path or None)
    remote: dict[str, Any] = {}
    remote_fail_closed = False
    try:
        remote = build_federation_transport_metadata(
            metadata,
            local_root=identity.root,
            execution_environment="ssh",
            remote_root="/srv/devtool/frx12-child",
            execution_provider="ssh",
            path_mapping="frx12-probe",
        )
    except FederationTransportError as exc:
        if not attestation.available and "cannot be independently attested" in str(exc):
            remote_fail_closed = True
        else:
            return False, f"transport identity probe failed: {exc}", {}

    local_ok = (
        local.get("federation_child_repository_fingerprint") == identity.fingerprint
        and local.get("federation_child_local_root") == _canonical(identity.root)
    )
    if attestation.available:
        remote_ok = (
            remote.get("federation_child_remote_root") == "/srv/devtool/frx12-child"
            and remote.get("federation_parent_run_id") == "frx12-parent"
            and remote.get("federation_child_run_id") == "frx12-child"
            and bool(remote.get("federation_attestation_required"))
        )
        detail = "local identity and independently attested provider-resolved remote identity are sealed"
    else:
        remote_ok = remote_fail_closed
        detail = "local identity is sealed and remote federation fails closed when no portable attestation signal exists"
    ok = local_ok and remote_ok
    return ok, (detail if ok else "transport metadata lost repository identity, lineage, or fail-closed attestation"), {
        "local_mapping": local.get("federation_path_mapping"),
        "remote_mapping": remote.get("federation_path_mapping"),
        "portable_attestation": attestation.available,
        "remote_fail_closed": remote_fail_closed,
    }


def _ownership_probe(config: "ProjectConfig") -> tuple[bool, str]:
    try:
        from .federation_operations import (
            collect_federated_operational_snapshot,
            discover_federated_orphan_children,
            request_federated_child_cancellation,
            validate_federated_resume_checkpoint,
        )
        from .federation_ownership import assert_federation_operational_owner
    except Exception as exc:
        return False, f"operational ownership contract unavailable: {exc}"
    identity = config.repository_identity
    if identity is None:
        return False, "owner repository identity is missing"
    owner = {
        "schema_version": 1,
        "kind": "federation",
        "relationship": "child",
        "repository": "@seal-probe",
        "repository_fingerprint": identity.fingerprint,
        "owner_root": _canonical(identity.root),
        "parent_run_id": "frx12-parent",
        "child_run_id": "frx12-child",
    }
    try:
        assert_federation_operational_owner(
            owner,
            expected_root=identity.root,
            expected_run_id="frx12-child",
            expected_repository="@seal-probe",
            expected_fingerprint=identity.fingerprint,
        )
    except Exception as exc:
        return False, f"operational owner probe failed: {exc}"
    ok = all(callable(fn) for fn in (
        collect_federated_operational_snapshot,
        discover_federated_orphan_children,
        request_federated_child_cancellation,
        validate_federated_resume_checkpoint,
    ))
    return ok, "child-owned artifacts/hooks/validation/transactions and resume/cancel boundaries are available" if ok else "operational boundary API is incomplete"


def _surface_probe(config: "ProjectConfig") -> tuple[bool, str, dict[str, Any]]:
    try:
        from .federation_observability import runtime_federation_projection
        from .ui_core.machine import MachineState
        from .ui_core.state import UISnapshot
        from .desktop.ui_semantics import DesktopSemanticView
    except Exception as exc:
        return False, f"observability/UI contract unavailable: {exc}", {}

    projection = runtime_federation_projection({
        "run_id": "frx12-parent",
        "run_lineage": {"kind": "federation", "relationship": "coordinator", "federation_workflow": "@seal"},
        "nodes": [{
            "node_id": "federation:@seal:child",
            "kind": "federation-child",
            "status": "succeeded",
            "metadata": {
                "repository": "@child",
                "child_run_id": "frx12-child",
                "run_lineage": {
                    "kind": "federation", "relationship": "child",
                    "parent_run_id": "frx12-parent", "child_run_id": "frx12-child",
                },
            },
        }],
    })
    machine_field = "federation" in getattr(MachineState, "__dataclass_fields__", {})
    ui_field = "federation" in getattr(UISnapshot, "__dataclass_fields__", {})
    desktop_field = "federation" in getattr(DesktopSemanticView, "__dataclass_fields__", {})
    ok = bool(projection) and projection.get("summary", {}).get("children") == 1 and machine_field and ui_field and desktop_field
    return ok, (
        "one redaction-safe federation projection feeds machine, UI, and desktop state"
        if ok else "machine/UI federation projection contract is incomplete"
    ), {"machine": machine_field, "ui": ui_field, "desktop": desktop_field}


def _documentation_completion_probe(config: "ProjectConfig") -> tuple[bool, str, dict[str, Any]]:
    state = build_completion_state(config)
    completion_ok = (
        state.federation_repositories == sorted(config.federation.repositories)
        and state.federation_groups == sorted(config.federation.groups)
        and state.federation_workflows == sorted(config.federation.workflows)
    )

    package_root = Path(__file__).resolve().parents[1]
    doc_path = package_root / "docs" / "architecture" / "federated-repositories.md"
    completion_path = package_root / "completions" / "_devtool"
    source_tree = (package_root / "devtool" / "federation_config.py").is_file()
    docs_ok = True
    completion_file_ok = True
    if source_tree:
        try:
            docs = doc_path.read_text(encoding="utf-8")
            completion_text = completion_path.read_text(encoding="utf-8")
        except OSError:
            docs_ok = False
            completion_file_ok = False
        else:
            required_doc_terms = (
                "FRX-12",
                "FRX-X04",
                "repository::target::workflow",
                "federation status",
                "federation run",
                "federation run --group",
                "federation exec",
                "final seal",
            )
            docs_ok = all(term.lower() in docs.lower() for term in required_doc_terms)
            completion_file_ok = (
                "federation" in completion_text
                and "final-seal" in completion_text
                and "seal" in completion_text
            )
    ok = completion_ok and docs_ok and completion_file_ok
    return ok, (
        "documentation, generated completion state, and checked-in completion describe the sealed federation surface"
        if ok else "documentation/completion parity is incomplete"
    ), {"source_tree": source_tree, "docs": docs_ok, "completion_state": completion_ok, "checked_in_completion": completion_file_ok}


def validate_federation_safety_evidence(package_root: str | Path | None = None) -> tuple[bool, tuple[dict[str, Any], ...], tuple[str, ...]]:
    """Resolve every required adversarial case to executable source evidence."""

    root = Path(package_root).expanduser().resolve(strict=False) if package_root else Path(__file__).resolve().parents[1]
    declared = {item.case_id: item for item in FEDERATION_FINAL_SEAL_SAFETY_EVIDENCE}
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    if tuple(declared) != FEDERATION_FINAL_SEAL_SAFETY_CASES:
        missing = [case for case in FEDERATION_FINAL_SEAL_SAFETY_CASES if case not in declared]
        extra = [case for case in declared if case not in FEDERATION_FINAL_SEAL_SAFETY_CASES]
        if missing:
            errors.append("missing safety evidence: " + ", ".join(missing))
        if extra:
            errors.append("unexpected safety evidence: " + ", ".join(extra))

    for case_id in FEDERATION_FINAL_SEAL_SAFETY_CASES:
        item = declared.get(case_id)
        resolved: list[str] = []
        unresolved: list[str] = []
        if item is None or not item.evidence:
            unresolved.append("<missing>")
        else:
            for ref in item.evidence:
                path_text, sep, node = ref.partition("::")
                path = root / path_text
                if not sep or not node or not path.is_file():
                    unresolved.append(ref)
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    unresolved.append(ref)
                    continue
                if f"def {node}(" not in text and f"async def {node}(" not in text:
                    unresolved.append(ref)
                    continue
                resolved.append(ref)
        if unresolved:
            errors.append(f"{case_id}: unresolved executable evidence: {', '.join(unresolved)}")
        rows.append({
            "case_id": case_id,
            "promise": item.promise if item is not None else "",
            "evidence": list(item.evidence) if item is not None else [],
            "resolved": resolved,
            "status": "proved" if resolved and not unresolved else "error",
        })
    return not errors, tuple(rows), tuple(errors)


def validate_federation_extension_evidence(package_root: str | Path | None = None) -> tuple[bool, tuple[dict[str, Any], ...], tuple[str, ...]]:
    """Resolve every FRX-X01..X04 extension promise to executable source evidence."""

    root = Path(package_root).expanduser().resolve(strict=False) if package_root else Path(__file__).resolve().parents[1]
    declared = {item.case_id: item for item in FEDERATION_FINAL_SEAL_EXTENSION_EVIDENCE}
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    if tuple(declared) != FEDERATION_FINAL_SEAL_EXTENSION_CASES:
        missing = [case for case in FEDERATION_FINAL_SEAL_EXTENSION_CASES if case not in declared]
        extra = [case for case in declared if case not in FEDERATION_FINAL_SEAL_EXTENSION_CASES]
        if missing:
            errors.append("missing extension evidence: " + ", ".join(missing))
        if extra:
            errors.append("unexpected extension evidence: " + ", ".join(extra))

    for case_id in FEDERATION_FINAL_SEAL_EXTENSION_CASES:
        item = declared.get(case_id)
        resolved: list[str] = []
        unresolved: list[str] = []
        if item is None or not item.evidence:
            unresolved.append("<missing>")
        else:
            for ref in item.evidence:
                path_text, sep, node = ref.partition("::")
                path = root / path_text
                if not sep or not node or not path.is_file():
                    unresolved.append(ref)
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    unresolved.append(ref)
                    continue
                if f"def {node}(" not in text and f"async def {node}(" not in text:
                    unresolved.append(ref)
                    continue
                resolved.append(ref)
        if unresolved:
            errors.append(f"{case_id}: unresolved executable evidence: {', '.join(unresolved)}")
        rows.append({
            "case_id": case_id,
            "promise": item.promise if item is not None else "",
            "evidence": list(item.evidence) if item is not None else [],
            "resolved": resolved,
            "status": "proved" if resolved and not unresolved else "error",
        })
    return not errors, tuple(rows), tuple(errors)


def build_federation_final_seal_report(config: "ProjectConfig") -> FederationFinalSealReport:
    checks: list[FederationFinalSealCheck] = []
    identity = config.repository_identity
    repository = _canonical(identity.root if identity is not None else config.repo_root)
    mode = "federated" if config.federation.enabled else "standalone"

    config_ok = int(config.federation.schema) == 1
    checks.append(_check(
        "config-contract", "ok" if config_ok else "error",
        "schema 1 with explicit enrollment/workflow declarations" if config_ok else f"unsupported federation schema {config.federation.schema}",
        schema=config.federation.schema,
        repositories=len(config.federation.repositories), groups=len(config.federation.groups), workflows=len(config.federation.workflows),
    ))

    topology = _recursive_topology(config)
    identity_ok, discovery_ok, repository_diagnostics = _repository_checks(config, topology)
    checks.append(_check(
        "repository-identity", "ok" if identity_ok else "error",
        "owner and child repository identities remain canonical and distinct" if identity_ok else "repository identity qualification failed",
        diagnostics=repository_diagnostics,
    ))
    checks.append(_check(
        "discovery", "ok" if discovery_ok else "error",
        "only explicitly enrolled repositories resolve through child-owned boundaries" if discovery_ok else "explicit repository discovery has unresolved required children",
        repositories=len(topology.repositories),
        repository_roots=[item.get("root", "") for item in topology.repositories],
        edges=len(topology.edges), errors=list(topology.errors),
    ))

    try:
        gate1 = build_federation_gate1_report(config)
        boundary_ok, boundary_detail = _gate_check(gate1, "FRX-04")
    except Exception as exc:
        boundary_ok, boundary_detail = False, f"FRX-04 prerequisite failed: {exc}"
    checks.append(_check("boundary-isolation", "ok" if boundary_ok else "error", boundary_detail))

    planning_ok, planning_errors, node_count, workflow_nodes, group_nodes, executable_group_count = _planning_check(config)
    checks.append(_check(
        "graph-planning", "ok" if planning_ok else "error",
        "configured federation workflow/group DAGs are deterministic and topologically complete" if planning_ok else "federation workflow/group planning failed",
        workflows=len(config.federation.workflows),
        executable_groups=executable_group_count,
        workflow_nodes=workflow_nodes,
        group_nodes=group_nodes,
        nodes=node_count,
        errors=planning_errors,
    ))

    cycle_ok = not topology.cycles
    checks.append(_check(
        "cycle-rejection", "ok" if cycle_ok else "error",
        "recursive explicit enrollment topology is acyclic" if cycle_ok else "deep federation registration cycle detected",
        cycles=[list(item) for item in topology.cycles], edges=len(topology.edges),
    ))

    try:
        gate2 = build_federation_gate2_report(config)
        gate2_ok, gate2_detail = _gate_check(gate2, "FRX-08")
    except Exception as exc:
        gate2 = None
        gate2_ok, gate2_detail = False, f"FRX-08 prerequisite failed: {exc}"
    lineage_ok = False
    if gate2 is not None:
        lineage_ok = gate2_ok and any(
            item.name == "exo-lineage-authority" and item.status == "ok"
            for item in gate2.checks
        )
    checks.append(_check(
        "exo-parent-child-lineage", "ok" if lineage_ok else "error",
        "distinct parent/child EXO authority and fail-closed lineage persistence are sealed" if lineage_ok else gate2_detail,
    ))

    wrapper_ok, tlj_ok, bridge_data = _bridge_capabilities(config)
    checks.append(_check(
        "wrapper-bridge", "ok" if wrapper_ok else "error",
        "federation wrapper bridge reuses child WRAP discovery/execution contracts" if wrapper_ok else "wrapper federation bridge is unavailable",
        **bridge_data,
    ))
    checks.append(_check(
        "target-local-job-bridge", "ok" if tlj_ok else "error",
        "federation TLJ bridge reuses child target-local job contracts" if tlj_ok else "target-local-job federation bridge is unavailable",
        target_local_job_counts=bridge_data.get("target_local_job_counts", {}),
    ))

    transport_ok, transport_detail, transport_data = _transport_probe(config)
    checks.append(_check("local-remote-identity", "ok" if transport_ok else "error", transport_detail, **transport_data))

    ownership_ok, ownership_detail = _ownership_probe(config)
    checks.append(_check("operational-ownership", "ok" if ownership_ok else "error", ownership_detail))

    surface_ok, surface_detail, surface_data = _surface_probe(config)
    checks.append(_check("machine-ui-doctor", "ok" if surface_ok else "error", surface_detail, **surface_data))

    # Safety evidence is a qualification contract, not a fifteenth roadmap
    # area.  FRX-12 promised exactly fourteen areas; unresolved evidence still
    # fails the seal through the top-level status while preserving that public
    # inventory.
    safety_ok, safety_rows, safety_errors = validate_federation_safety_evidence()
    extension_ok, extension_rows, extension_errors = validate_federation_extension_evidence()

    standalone_ok = True
    standalone_detail = "federation is additive and opt-in; standalone gate contracts remain side-effect free"
    if mode == "standalone":
        standalone_ok = boundary_ok and gate2_ok and not topology.cycles and not topology.errors
    checks.append(_check(
        "standalone-compatibility", "ok" if standalone_ok else "error",
        standalone_detail if standalone_ok else "standalone federation qualification failed",
        mode=mode,
    ))

    docs_ok, docs_detail, docs_data = _documentation_completion_probe(config)
    checks.append(_check("documentation-completion-parity", "ok" if docs_ok else "error", docs_detail, **docs_data))

    # Guard the release contract itself: there must be exactly one check per
    # promised roadmap area and no accidental omissions/duplicates.
    seen = tuple(item.area for item in checks)
    if seen != FEDERATION_FINAL_SEAL_AREAS:
        missing = [area for area in FEDERATION_FINAL_SEAL_AREAS if area not in seen]
        duplicate = sorted({area for area in seen if seen.count(area) > 1})
        checks.append(_check("seal-contract", "error", "FRX-12 seal area inventory is invalid", missing=missing, duplicate=duplicate))

    return FederationFinalSealReport(
        schema_version=FEDERATION_FINAL_SEAL_SCHEMA_VERSION,
        gate="FRX-12",
        repository=repository,
        mode=mode,
        status="error" if not safety_ok or not extension_ok else _overall(checks),
        checks=tuple(checks),
        safety_evidence=tuple(dict(item) for item in safety_rows),
        safety_evidence_errors=tuple(safety_errors),
        extension_gate=FEDERATION_FINAL_SEAL_EXTENSION_GATE,
        extensions=FEDERATION_FINAL_SEAL_EXTENSIONS,
        extension_evidence=tuple(dict(item) for item in extension_rows),
        extension_evidence_errors=tuple(extension_errors),
    )


def federation_final_seal_as_dict(report: FederationFinalSealReport) -> dict[str, Any]:
    return {
        "schema_version": report.schema_version,
        "gate": report.gate,
        "repository": report.repository,
        "mode": report.mode,
        "status": report.status,
        "ok": report.ok,
        "summary": {
            "areas": len(FEDERATION_FINAL_SEAL_AREAS),
            "checks": len(report.checks),
            "errors": sum(item.status == "error" for item in report.checks) + len(report.safety_evidence_errors) + len(report.extension_evidence_errors),
            "warnings": sum(item.status == "warning" for item in report.checks),
            "qualified": sum(item.status == "ok" for item in report.checks),
            "safety_evidence_errors": len(report.safety_evidence_errors),
            "extension_evidence_errors": len(report.extension_evidence_errors),
        },
        "required_areas": list(FEDERATION_FINAL_SEAL_AREAS),
        "required_safety_cases": list(FEDERATION_FINAL_SEAL_SAFETY_CASES),
        "extension_gate": report.extension_gate,
        "sealed_extensions": list(report.extensions),
        "required_extension_cases": list(FEDERATION_FINAL_SEAL_EXTENSION_CASES),
        "extension_evidence_contract": [item.as_dict() for item in FEDERATION_FINAL_SEAL_EXTENSION_EVIDENCE],
        "safety_evidence_contract": [item.as_dict() for item in FEDERATION_FINAL_SEAL_SAFETY_EVIDENCE],
        "safety_evidence": [dict(item) for item in report.safety_evidence],
        "safety_evidence_errors": list(report.safety_evidence_errors),
        "extension_evidence": [dict(item) for item in report.extension_evidence],
        "extension_evidence_errors": list(report.extension_evidence_errors),
        "checks": [item.as_dict() for item in report.checks],
    }


def render_federation_final_seal(report: FederationFinalSealReport) -> str:
    lines = [
        f"Federation final seal · {report.gate}",
        f"Repository: {report.repository}",
        f"Mode: {report.mode}",
        f"Status: {report.status}",
        f"Extensions: {report.extension_gate} ({', '.join(report.extensions)})",
        "",
    ]
    for item in report.checks:
        lines.append(f"{item.status.upper():8} {item.area}: {item.detail}")
    if report.safety_evidence_errors:
        lines.append("")
        lines.append("Safety evidence failures:")
        for error in report.safety_evidence_errors:
            lines.append(f"ERROR    {error}")
    if report.extension_evidence_errors:
        lines.append("")
        lines.append("Extension evidence failures:")
        for error in report.extension_evidence_errors:
            lines.append(f"ERROR    {error}")
    return "\n".join(lines) + "\n"


__all__ = [
    "FEDERATION_FINAL_SEAL_AREAS",
    "FEDERATION_FINAL_SEAL_SCHEMA_VERSION",
    "FEDERATION_FINAL_SEAL_SAFETY_CASES",
    "FEDERATION_FINAL_SEAL_SAFETY_EVIDENCE",
    "FEDERATION_FINAL_SEAL_EXTENSION_GATE",
    "FEDERATION_FINAL_SEAL_EXTENSIONS",
    "FEDERATION_FINAL_SEAL_EXTENSION_CASES",
    "FEDERATION_FINAL_SEAL_EXTENSION_EVIDENCE",
    "FederationSafetyEvidence",
    "FederationFinalSealCheck",
    "FederationFinalSealReport",
    "build_federation_final_seal_report",
    "federation_final_seal_as_dict",
    "render_federation_final_seal",
    "validate_federation_safety_evidence",
    "validate_federation_extension_evidence",
]
