from __future__ import annotations

"""FRX-07 federation bridge for repository wrapper and target-local job contracts."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from .config import load_project_config
from .core.events import GraphSealed, RunFinished, RunStarted, get_execution_store, publish_event
from .federation_discovery import FederationDiscoveryError, resolve_federated_repository
from .federation_execution import FederatedChildRun, FederationExecutionError, run_federated_direct_child
from .models import ProjectConfig
from .runners import run_specs
from .workflow_runtime import WorkflowRuntimeError, build_direct_job_specs
from .wrapper_execution import (
    build_wrapper_canonical_argv_with_provenance,
    execute_wrapper_resolution,
)
from .wrapper_resolution import (
    WrapperResolution,
    WrapperResolutionError,
    resolve_wrapper_argv,
    wrapper_resolution_as_dict,
)


class FederationBridgeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FederatedBridgePlan:
    repository: str
    contract: str
    child_root: str
    repository_identity: dict[str, Any]
    target: str = ""
    child_contract: str = ""
    contract_ref: str = ""
    job_reference: str = ""
    spec_count: int = 0
    wrapper: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "repository": self.repository,
            "contract": self.contract,
            "child_root": self.child_root,
            "repository_identity": dict(self.repository_identity),
            "target": self.target,
            "child_contract": self.child_contract,
            "contract_ref": self.contract_ref,
            "job_reference": self.job_reference,
            "spec_count": self.spec_count,
            "wrapper": dict(self.wrapper),
        }


def _load_child(parent: ProjectConfig, repository: str) -> ProjectConfig:
    try:
        resolution = resolve_federated_repository(parent, repository)
    except FederationDiscoveryError as exc:
        raise FederationBridgeError(str(exc)) from exc
    if not resolution.available:
        raise FederationBridgeError(f"Federation repository {repository!r} is unavailable: {resolution.root}")
    try:
        child = load_project_config(
            resolution.root,
            resolution.config_path or None,
            target_name="",
            variant_name="",
            allow_ambiguous_target=True,
            allow_ambiguous_project_kind=True,
            allow_parent_config=False,
        )
    except SystemExit as exc:
        raise FederationBridgeError(f"Cannot load federation repository {repository!r}: {exc}") from exc
    if child.repository_identity is None:
        raise FederationBridgeError(f"Federation repository {repository!r} has no repository identity")
    if Path(child.repository_identity.root).resolve(strict=False) != Path(resolution.root).resolve(strict=False):
        raise FederationBridgeError(f"Federation repository {repository!r} changed ownership during bridge resolution")
    return child


def _selected_target(child: ProjectConfig) -> str:
    selected = str(child.selected_target or "").strip()
    if selected:
        return selected
    if len(child.targets) == 1:
        return next(iter(child.targets))
    return ""


def plan_federated_wrapper(
    parent: ProjectConfig,
    repository: str,
    wrapper_argv: Sequence[str],
) -> tuple[FederatedBridgePlan, ProjectConfig, WrapperResolution]:
    child = _load_child(parent, repository)
    try:
        resolution = resolve_wrapper_argv(child, wrapper_argv, launch_cwd=child.repo_root)
    except WrapperResolutionError as exc:
        raise FederationBridgeError(str(exc)) from exc
    payload = wrapper_resolution_as_dict(resolution)
    return (
        FederatedBridgePlan(
            repository=repository,
            contract="wrapper",
            child_root=str(child.repo_root),
            repository_identity=child.repository_identity.as_dict() if child.repository_identity else {},
            target=str(resolution.target or _selected_target(child)),
            child_contract=f"wrapper:{resolution.command}",
            contract_ref=(
                f"{repository}::{str(resolution.target or _selected_target(child))}::wrapper:{resolution.command}"
                if str(resolution.target or _selected_target(child)) else ""
            ),
            wrapper=payload,
        ),
        child,
        resolution,
    )


def execute_federated_wrapper(
    parent: ProjectConfig,
    repository: str,
    wrapper_argv: Sequence[str],
    *,
    reenter: Callable[[list[str]], int],
    publish: Callable[[Any], None] = publish_event,
    snapshot_lookup: Callable[[str], Any] | None = None,
) -> FederatedChildRun:
    plan, child, resolution = plan_federated_wrapper(parent, repository, wrapper_argv)
    if resolution.mode != "execute":
        raise FederationBridgeError(
            f"Wrapper request resolved to {resolution.mode!r}; use federation exec --plan for help/version inspection"
        )
    redacted_argv = tuple(resolution.redacted_canonical_argv or resolution.canonical_argv)

    def execute(child_run_id: str, lineage: dict[str, Any]) -> int:
        # The planned wrapper child publishes the authoritative EXO RunStarted
        # with the planned federation lineage before reentering the wrapper.
        # A reentered command may publish its own RunStarted without lineage;
        # it must not be the sole source of federation parent authority.
        return execute_planned_federated_wrapper_child(
            child, resolution, child_run_id=child_run_id,
            lineage=lineage, reenter=reenter, publish=publish,
        )

    return run_federated_direct_child(
        parent,
        repository,
        operation=f"wrapper:{resolution.command}",
        invocation_argv=redacted_argv,
        execute=execute,
        target=plan.target,
        child_contract=plan.child_contract,
        publish=publish,
        snapshot_lookup=snapshot_lookup,
    )


def execute_planned_federated_wrapper_child(
    child: ProjectConfig,
    resolution: WrapperResolution,
    *,
    child_run_id: str,
    lineage: dict[str, Any],
    reenter: Callable[[list[str]], int],
    publish: Callable[[Any], None] = publish_event,
) -> int:
    """Execute a bridge-resolved wrapper inside the planned child EXO run."""
    if resolution.mode != "execute":
        raise FederationBridgeError(
            f"Wrapper request resolved to {resolution.mode!r}; a federation DAG node requires executable mode"
        )
    identity = child.repository_identity.as_dict() if child.repository_identity else {}
    command = f"wrapper {resolution.command}"
    publish(RunStarted(
        run_id=child_run_id,
        command=command,
        project=str(child.repo_root),
        source="federation-wrapper",
        metadata={
            "invocation_source": "federation-wrapper",
            "invocation_command": str(resolution.command),
            "repository_identity": identity,
            "run_lineage": lineage,
        },
    ))
    try:
        rc = int(execute_wrapper_resolution(resolution, reenter=reenter))
        status = "succeeded" if rc == 0 else ("cancelled" if rc == 130 else "failed")
        publish(GraphSealed(run_id=child_run_id, reason="direct wrapper execution complete", source="federation-wrapper"))
        publish(RunFinished(run_id=child_run_id, status=status, exit_code=rc, source="federation-wrapper"))
        return rc
    except KeyboardInterrupt:
        publish(GraphSealed(run_id=child_run_id, reason="direct wrapper execution cancelled", source="federation-wrapper"))
        publish(RunFinished(run_id=child_run_id, status="cancelled", exit_code=130, source="federation-wrapper"))
        raise
    except BaseException:
        publish(GraphSealed(run_id=child_run_id, reason="direct wrapper execution aborted", source="federation-wrapper"))
        publish(RunFinished(run_id=child_run_id, status="failed", exit_code=1, source="federation-wrapper"))
        raise


def _federated_job_result_code(specs: Sequence[Any], results: Sequence[Any]) -> int:
    """Fail closed unless every planned job command has one terminal matching result."""
    if not specs or len(results) != len(specs):
        return 1
    for spec, result in zip(specs, results):
        if (getattr(result, "name", None) != getattr(spec, "name", None)
            or getattr(result, "argv", None) != getattr(spec, "argv", None)
            or getattr(result, "skipped", False)
            or (getattr(result, "failure_kind", "") and getattr(result, "returncode", None) == 0)
            or getattr(result, "returncode", None) is None
            or isinstance(getattr(result, "returncode", None), bool)
            or not isinstance(getattr(result, "returncode", None), int)):
            return 1
    return next((result.returncode for result in results if result.returncode != 0), 0)


def execute_planned_federated_job_child(
    child: ProjectConfig,
    specs: Sequence[Any],
    *,
    child_run_id: str,
    lineage: dict[str, Any],
    job_reference: str,
    verbose: bool = False,
    dry_run: bool = False,
    continue_on_error: bool = False,
    publish: Callable[[Any], None] = publish_event,
    run_specs_fn: Callable[..., tuple[list[Any], str]] = run_specs,
) -> int:
    """Execute a bridge-resolved target-local job in an existing child EXO run.

    Both direct ``federation exec`` and FRX-Y07 DAG nodes use this exact child
    execution service so contract type does not fork the runtime semantics.
    """
    identity = child.repository_identity.as_dict() if child.repository_identity else {}
    publish(RunStarted(
        run_id=child_run_id,
        command=f"job {job_reference}",
        project=str(child.repo_root),
        source="federation-job",
        metadata={
            "invocation_source": "federation-job",
            "invocation_command": str(job_reference),
            "repository_identity": identity,
            "run_lineage": lineage,
        },
    ))
    try:
        results, _ = run_specs_fn(
            child,
            list(specs),
            verbose=verbose,
            dry_run=dry_run,
            continue_on_error=continue_on_error,
            event_run_id=child_run_id,
            publish_workflow_plan=False,
        )
        rc = _federated_job_result_code(specs, results)
        status = "succeeded" if rc == 0 else ("cancelled" if rc == 130 else "failed")
        publish(GraphSealed(
            run_id=child_run_id,
            reason="direct target-local job execution complete",
            source="federation-job",
        ))
        publish(RunFinished(run_id=child_run_id, status=status, exit_code=rc, source="federation-job"))
        return rc
    except KeyboardInterrupt:
        publish(GraphSealed(
            run_id=child_run_id,
            reason="direct target-local job execution cancelled",
            source="federation-job",
        ))
        publish(RunFinished(run_id=child_run_id, status="cancelled", exit_code=130, source="federation-job"))
        raise
    except BaseException:
        publish(GraphSealed(
            run_id=child_run_id,
            reason="direct target-local job execution aborted",
            source="federation-job",
        ))
        publish(RunFinished(run_id=child_run_id, status="failed", exit_code=1, source="federation-job"))
        raise


def plan_federated_job(
    parent: ProjectConfig,
    repository: str,
    job_reference: str,
) -> tuple[FederatedBridgePlan, ProjectConfig, list[Any]]:
    child = _load_child(parent, repository)
    selected_target = _selected_target(child)
    try:
        specs = build_direct_job_specs(child, job_reference, target_name=selected_target)
    except WorkflowRuntimeError as exc:
        raise FederationBridgeError(str(exc)) from exc
    resolved_target = selected_target
    if specs:
        resolved_target = str(specs[0].metadata.get("direct_job_target") or selected_target).strip()
    raw_job_reference = str(job_reference).strip()
    if ":job:" in raw_job_reference:
        _target_part, local_job_name = raw_job_reference.split(":job:", 1)
    elif raw_job_reference.startswith("job:"):
        local_job_name = raw_job_reference[4:]
    else:
        local_job_name = raw_job_reference
    child_contract = f"job:{local_job_name.strip()}"
    contract_ref = f"{repository}::{resolved_target}::{child_contract}" if resolved_target else ""
    return (
        FederatedBridgePlan(
            repository=repository,
            contract="job",
            child_root=str(child.repo_root),
            repository_identity=child.repository_identity.as_dict() if child.repository_identity else {},
            target=resolved_target,
            child_contract=child_contract,
            contract_ref=contract_ref,
            job_reference=str(job_reference),
            spec_count=len(specs),
        ),
        child,
        specs,
    )


def execute_federated_job(
    parent: ProjectConfig,
    repository: str,
    job_reference: str,
    *,
    verbose: bool = False,
    dry_run: bool = False,
    continue_on_error: bool = False,
    publish: Callable[[Any], None] = publish_event,
    snapshot_lookup: Callable[[str], Any] | None = None,
    run_specs_fn: Callable[..., tuple[list[Any], str]] = run_specs,
) -> FederatedChildRun:
    plan, child, specs = plan_federated_job(parent, repository, job_reference)
    snapshot_lookup = snapshot_lookup or get_execution_store().snapshot

    def execute(child_run_id: str, lineage: dict[str, Any]) -> int:
        return execute_planned_federated_job_child(
            child,
            specs,
            child_run_id=child_run_id,
            lineage=lineage,
            job_reference=job_reference,
            verbose=verbose,
            dry_run=dry_run,
            continue_on_error=continue_on_error,
            publish=publish,
            run_specs_fn=run_specs_fn,
        )

    return run_federated_direct_child(
        parent,
        repository,
        operation=f"job:{job_reference}",
        invocation_argv=("job", str(job_reference)),
        execute=execute,
        target=plan.target,
        child_contract=plan.child_contract,
        publish=publish,
        snapshot_lookup=snapshot_lookup,
    )


__all__ = [
    "FederationBridgeError",
    "FederatedBridgePlan",
    "plan_federated_wrapper",
    "execute_federated_wrapper",
    "execute_planned_federated_wrapper_child",
    "plan_federated_job",
    "execute_federated_job",
    "execute_planned_federated_job_child",
]
