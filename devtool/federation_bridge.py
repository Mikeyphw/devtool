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
    plan, _child, resolution = plan_federated_wrapper(parent, repository, wrapper_argv)
    if resolution.mode != "execute":
        raise FederationBridgeError(
            f"Wrapper request resolved to {resolution.mode!r}; use federation exec --plan for help/version inspection"
        )
    redacted_argv = tuple(resolution.redacted_canonical_argv or resolution.canonical_argv)

    def execute(_child_run_id: str, _lineage: dict[str, Any]) -> int:
        return execute_wrapper_resolution(resolution, reenter=reenter)

    return run_federated_direct_child(
        parent,
        repository,
        operation=f"wrapper:{resolution.command}",
        invocation_argv=redacted_argv,
        execute=execute,
        publish=publish,
        snapshot_lookup=snapshot_lookup,
    )


def plan_federated_job(
    parent: ProjectConfig,
    repository: str,
    job_reference: str,
) -> tuple[FederatedBridgePlan, ProjectConfig, list[Any]]:
    child = _load_child(parent, repository)
    target = _selected_target(child)
    try:
        specs = build_direct_job_specs(child, job_reference, target_name=target)
    except WorkflowRuntimeError as exc:
        raise FederationBridgeError(str(exc)) from exc
    return (
        FederatedBridgePlan(
            repository=repository,
            contract="job",
            child_root=str(child.repo_root),
            repository_identity=child.repository_identity.as_dict() if child.repository_identity else {},
            target=target,
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
    identity = child.repository_identity.as_dict() if child.repository_identity else {}
    snapshot_lookup = snapshot_lookup or get_execution_store().snapshot

    def execute(child_run_id: str, lineage: dict[str, Any]) -> int:
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
                specs,
                verbose=verbose,
                dry_run=dry_run,
                continue_on_error=continue_on_error,
                event_run_id=child_run_id,
                publish_workflow_plan=False,
            )
            rc = next((int(result.returncode) for result in results if int(result.returncode or 0) != 0), 0)
            status = "succeeded" if rc == 0 else ("cancelled" if rc == 130 else "failed")
            publish(GraphSealed(run_id=child_run_id, reason="direct target-local job execution complete", source="federation-job"))
            publish(RunFinished(run_id=child_run_id, status=status, exit_code=rc, source="federation-job"))
            return rc
        except BaseException:
            publish(GraphSealed(run_id=child_run_id, reason="direct target-local job execution aborted", source="federation-job"))
            publish(RunFinished(run_id=child_run_id, status="failed", exit_code=1, source="federation-job"))
            raise

    return run_federated_direct_child(
        parent,
        repository,
        operation=f"job:{job_reference}",
        invocation_argv=("job", str(job_reference)),
        execute=execute,
        publish=publish,
        snapshot_lookup=snapshot_lookup,
    )


__all__ = [
    "FederationBridgeError",
    "FederatedBridgePlan",
    "plan_federated_wrapper",
    "execute_federated_wrapper",
    "plan_federated_job",
    "execute_federated_job",
]
