from __future__ import annotations

"""MP04 cross-runner workflow planning.

This module turns a validated MP03 graph into ordinary ``CommandSpec`` objects.
Individual project runners remain responsible only for their own toolchain.
Devtool's existing execution pipeline can therefore execute Python, Node,
Gradle, Go, etc. in one target workflow without cross-toolchain special cases
inside those runners.
"""

from dataclasses import replace
from pathlib import Path
import os
from typing import Iterable

from .models import CommandSpec, JobConfig, ProjectConfig, TargetConfig
from .project_runners import get_project_runner
from .workflow_graph import ExecutionGraph, WorkflowGraphError, WorkflowNode, graph_from_workflow


class WorkflowRuntimeError(RuntimeError):
    pass


def selected_workflow(config: ProjectConfig, action: str):
    target_name = str(config.selected_target or "").strip()
    if not target_name:
        return None
    target = config.targets.get(target_name)
    if target is None:
        return None
    return target.workflows.get(str(action or "").strip())


def has_selected_workflow(config: ProjectConfig, action: str) -> bool:
    return selected_workflow(config, action) is not None


def plan_selected_workflow(config: ProjectConfig, action: str) -> ExecutionGraph:
    workflow = selected_workflow(config, action)
    if workflow is None:
        target = config.selected_target or "<default>"
        raise WorkflowRuntimeError(f"Target {target!r} has no configured workflow for action {action!r}.")
    try:
        return graph_from_workflow(config.selected_target, workflow)
    except WorkflowGraphError as exc:
        raise WorkflowRuntimeError(str(exc)) from exc


def _merge_options(base: dict[str, object], extra: dict[str, object]) -> dict[str, object]:
    merged: dict[str, object] = dict(base)
    for key, value in extra.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _merge_options(dict(current), dict(value))
        else:
            merged[key] = value
    return merged


def _root_for_target(config: ProjectConfig, target: TargetConfig | None = None) -> str:
    if target is not None:
        value = target.root or target.cwd or target.build_target or target.restore_target
        if value:
            path = Path(value).expanduser()
            if path.is_file():
                path = path.parent
            return str(path.resolve(strict=False))
    return str(Path(config.project_root or config.repo_root).expanduser().resolve(strict=False))


def _runner_config(
    config: ProjectConfig,
    *,
    runner: str,
    root: str = "",
    env: dict[str, str] | None = None,
    execution_environment: str = "",
    chroot_name: str = "",
    device_serial: str = "",
    options: dict[str, object] | None = None,
) -> ProjectConfig:
    project_root = str(Path(root or config.project_root or config.repo_root).expanduser().resolve(strict=False))
    runner_options = _merge_options(config.runner_options, options or {})
    return replace(
        config,
        kind=runner,
        runner=runner,
        project_root=project_root,
        build_target=project_root,
        restore_target=project_root,
        test_target=project_root,
        run_target=project_root,
        env=dict(env if env is not None else config.env),
        execution_environment=execution_environment or config.execution_environment,
        chroot_name=chroot_name or config.chroot_name,
        device_serial=device_serial or config.device_serial,
        runner_options=runner_options,
    )


def _runner_action_specs(config: ProjectConfig, runner_name: str, action: str) -> list[CommandSpec]:
    runner = get_project_runner(runner_name)
    if action in {"validate", "wine-validate"}:
        specs = runner.build_validation_specs(config, wine=action == "wine-validate")
    else:
        specs = runner.build_specs(config, action)
    if not specs:
        raise WorkflowRuntimeError(
            f"Runner '{runner_name}' produced no commands for workflow action '{action}'."
        )
    return specs


def _structured_job_metadata(job: JobConfig) -> dict[str, object]:
    metadata: dict[str, object] = {
        "runner": job.runner,
        "project_type": job.runner,
        "project_root": job.root,
        "workflow_job": job.name,
        "workflow_job_mode": job.command_mode,
        **dict(job.metadata),
    }
    if "progress_parser" not in metadata:
        if job.runner == "node":
            metadata["progress_parser"] = "npm"
        elif job.runner in {"android", "gradle"}:
            metadata["progress_parser"] = "gradle"
        else:
            metadata["progress_parser"] = "generic"
    return metadata


def _job_specs(config: ProjectConfig, job: JobConfig) -> list[CommandSpec]:
    root = job.root or str(config.repo_root)
    effective_env = dict(config.env)
    effective_env.update(job.env)
    job_config = _runner_config(
        config,
        runner=job.runner,
        root=root,
        env=effective_env,
        execution_environment=job.execution_environment if job.execution_environment != "auto" else config.execution_environment,
        chroot_name=job.chroot_name,
        device_serial=job.device_serial,
        options=job.options,
    )
    if job.action:
        return _runner_action_specs(job_config, job.runner, job.action)
    if job.command:
        return [
            CommandSpec(
                name=f"job:{job.name}",
                argv=list(job.command),
                env=effective_env,
                cwd=str(Path(root).expanduser().resolve(strict=False)),
                metadata=_structured_job_metadata(job),
            )
        ]
    if job.shell_command:
        shell = os.environ.get("DEVTOOL_WORKFLOW_SHELL", "").strip() or "sh"
        shell_flag = "-lc" if Path(shell).name in {"bash", "zsh", "ksh"} else "-c"
        metadata = _structured_job_metadata(job)
        metadata["explicit_shell"] = True
        metadata["shell_command"] = job.shell_command
        return [
            CommandSpec(
                name=f"job:{job.name}",
                argv=[shell, shell_flag, job.shell_command],
                env=effective_env,
                cwd=str(Path(root).expanduser().resolve(strict=False)),
                metadata=metadata,
            )
        ]
    raise WorkflowRuntimeError(f"Job {job.name!r} has no execution mode.")


def _node_specs(config: ProjectConfig, node: WorkflowNode) -> list[CommandSpec]:
    ref = node.ref
    if ref.kind == "target":
        raise WorkflowRuntimeError(
            f"Workflow {node.target}.{node.workflow} contains {ref.raw!r}. Cross-target execution is introduced "
            "by MP05; MP03+MP04 represent and validate this node but will not execute it implicitly."
        )
    if ref.kind == "job":
        job = config.jobs.get(ref.name)
        if job is None:
            raise WorkflowRuntimeError(f"Workflow references missing job {ref.name!r}.")
        specs = _job_specs(config, job)
        execution_environment = job.execution_environment
        chroot_name = job.chroot_name
        device_serial = job.device_serial
        runner_options = job.options
        runner_name = job.runner
        effective_action = job.action
        inputs = job.inputs
        outputs = job.outputs
    elif ref.kind == "runner":
        target = config.targets.get(config.selected_target)
        root = _root_for_target(config, target)
        node_config = _runner_config(config, runner=ref.name, root=root)
        specs = _runner_action_specs(node_config, ref.name, ref.action)
        execution_environment = config.execution_environment
        chroot_name = config.chroot_name
        device_serial = config.device_serial
        runner_options = {}
        runner_name = ref.name
        effective_action = ref.action
        inputs = ()
        outputs = ()
    else:
        raise WorkflowRuntimeError(f"Unsupported workflow node kind: {ref.kind}")

    total = len(specs)
    for spec_index, spec in enumerate(specs, 1):
        metadata = dict(spec.metadata)
        metadata.update(
            {
                "workflow_graph": True,
                "workflow_target": node.target,
                "workflow_name": node.workflow,
                "workflow_node_key": node.key,
                "workflow_node_id": ref.step_id,
                "workflow_node_index": node.index,
                "workflow_ref": ref.raw,
                "workflow_ref_kind": ref.kind,
                "workflow_depends_on": list(node.depends_on),
                "workflow_runner": runner_name,
                "workflow_action": effective_action,
                "workflow_node_spec_index": spec_index,
                "workflow_node_spec_total": total,
                "workflow_execution_environment": execution_environment,
                "workflow_chroot_name": chroot_name,
                "workflow_device_serial": device_serial,
                "workflow_runner_options": dict(runner_options),
                "workflow_inputs": list(inputs),
                "workflow_outputs": list(outputs),
            }
        )
        spec.metadata = metadata
    return specs


def build_workflow_specs(config: ProjectConfig, action: str) -> list[CommandSpec]:
    graph = plan_selected_workflow(config, action)
    ordered = graph.topological_order()
    specs: list[CommandSpec] = []
    for node in ordered:
        specs.extend(_node_specs(config, node))
    if not specs:
        raise WorkflowRuntimeError(
            f"Workflow {config.selected_target}.{action} produced no executable commands."
        )
    # Bridge the generic workflow DAG into the durable validation-session DAG.
    # A workflow node may expand to multiple runner commands, so the first
    # command depends on the *last* command of each prerequisite node and later
    # commands depend on the previous command within the same node.
    node_specs: dict[str, list[CommandSpec]] = {}
    for spec in specs:
        key = str(spec.metadata.get("workflow_node_key") or "")
        node_specs.setdefault(key, []).append(spec)
    phase_keys: dict[str, list[str]] = {
        key: [f"workflow:{key}:part-{index}" for index in range(1, len(values) + 1)]
        for key, values in node_specs.items()
        if key
    }

    total = len(specs)
    for index, spec in enumerate(specs, 1):
        metadata = dict(spec.metadata)
        node_key = str(metadata.get("workflow_node_key") or "")
        part_index = int(metadata.get("workflow_node_spec_index") or 1)
        keys = phase_keys.get(node_key, [])
        if keys:
            metadata["validation_phase_key"] = keys[part_index - 1]
            if part_index > 1:
                validation_dependencies = [keys[part_index - 2]]
            else:
                validation_dependencies = []
                for dependency in metadata.get("workflow_depends_on", []) or []:
                    dependency_keys = phase_keys.get(str(dependency), [])
                    if dependency_keys:
                        validation_dependencies.append(dependency_keys[-1])
            metadata["validation_depends_on"] = validation_dependencies
        metadata["workflow_spec_index"] = index
        metadata["workflow_spec_total"] = total
        metadata["workflow_graph_schema_version"] = 1
        spec.metadata = metadata
    return specs


def workflow_graph_metadata(config: ProjectConfig, action: str) -> dict[str, object]:
    return plan_selected_workflow(config, action).as_dict()


def workflow_runner_kinds(config: ProjectConfig, action: str) -> tuple[str, ...]:
    graph = plan_selected_workflow(config, action)
    runners: list[str] = []
    for node in graph.nodes:
        if node.ref.kind == "runner":
            runners.append(node.ref.name)
        elif node.ref.kind == "job" and node.ref.name in config.jobs:
            runners.append(config.jobs[node.ref.name].runner)
    return tuple(dict.fromkeys(runners))
