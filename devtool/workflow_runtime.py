from __future__ import annotations

"""MP04 cross-runner workflow planning.

This module turns a validated MP03 graph into ordinary ``CommandSpec`` objects.
Individual project runners remain responsible only for their own toolchain.
Devtool's existing execution pipeline can therefore execute Python, Node,
Gradle, Go, etc. in one target workflow without cross-toolchain special cases
inside those runners.
"""

from dataclasses import dataclass, replace
from pathlib import Path
import copy
import os
from typing import Iterable, Sequence

from .models import CommandSpec, JobConfig, ProjectConfig, TargetConfig, WorkflowStepRef
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


@dataclass(frozen=True, slots=True)
class _InvocationExpansion:
    roots: tuple[str, ...]
    leaves: tuple[str, ...]


class _WorkspaceGraphPlanner:
    """Flatten target/action invocations into one deduplicated workspace DAG."""

    def __init__(self, config: ProjectConfig, action: str):
        self.config = config
        self.action = action
        self._nodes: dict[str, WorkflowNode] = {}
        self._dependencies: dict[str, list[str]] = {}
        self._cache: dict[tuple[str, str], _InvocationExpansion] = {}
        self._stack: list[tuple[str, str]] = []
        self._next_index = 1

    def _add_node(self, node: WorkflowNode, dependencies: Sequence[str]) -> str:
        deps = list(dict.fromkeys(str(item) for item in dependencies if str(item)))
        if node.key in self._nodes:
            current = self._dependencies.setdefault(node.key, [])
            for dependency in deps:
                if dependency not in current:
                    current.append(dependency)
            return node.key
        repository_identity = (
            self.config.repository_identity.as_dict()
            if self.config.repository_identity is not None
            else {}
        )
        self._nodes[node.key] = replace(
            node,
            index=self._next_index,
            depends_on=(),
            metadata={
                **dict(node.metadata),
                "workspace_graph": True,
                "repository_identity": repository_identity,
            },
        )
        self._dependencies[node.key] = deps
        self._next_index += 1
        return node.key

    def _target_runner(self, target_name: str) -> str:
        target = self.config.targets.get(target_name)
        if target is None:
            available = ", ".join(sorted(self.config.targets)) or "<none>"
            raise WorkflowRuntimeError(
                f"Workflow references unknown target '{target_name}'. Available targets: {available}"
            )
        runner = str(target.effective_runner or "").strip()
        if not runner:
            raise WorkflowRuntimeError(
                f"Target '{target_name}' cannot execute action '{self.action}' because it has no runner."
            )
        return runner

    def _add_prerequisites(self, expansion: _InvocationExpansion, prerequisites: Sequence[str]) -> None:
        for root in expansion.roots:
            current = self._dependencies.setdefault(root, [])
            for dependency in prerequisites:
                if dependency and dependency not in current:
                    current.append(dependency)

    def expand(self, target_name: str, action: str, prerequisites: Sequence[str] = ()) -> _InvocationExpansion:
        invocation = (str(target_name).strip(), str(action).strip())
        if invocation in self._stack:
            start = self._stack.index(invocation)
            cycle = [*self._stack[start:], invocation]
            detail = " -> ".join(f"{target}#{name}" for target, name in cycle)
            raise WorkflowRuntimeError(f"Cross-target workflow cycle detected: {detail}")
        cached = self._cache.get(invocation)
        if cached is not None:
            self._add_prerequisites(cached, prerequisites)
            return cached

        target = self.config.targets.get(invocation[0])
        if target is None:
            available = ", ".join(sorted(self.config.targets)) or "<none>"
            raise WorkflowRuntimeError(
                f"Workflow references unknown target '{invocation[0]}'. Available targets: {available}"
            )

        self._stack.append(invocation)
        try:
            workflow = target.workflows.get(invocation[1])
            if workflow is None:
                runner = str(target.effective_runner or "").strip()
                if not runner:
                    raise WorkflowRuntimeError(
                        f"Target '{invocation[0]}' has no workflow for action '{invocation[1]}' and no primary runner fallback."
                    )
                ref = WorkflowStepRef(
                    raw=f"runner:{runner}#{invocation[1]}",
                    kind="runner",
                    name=runner,
                    action=invocation[1],
                    step_id="$runner",
                    depends_on=(),
                    depends_on_declared=True,
                )
                key = f"{invocation[0]}.{invocation[1]}.$runner"
                node = WorkflowNode(
                    key=key,
                    target=invocation[0],
                    workflow=invocation[1],
                    index=0,
                    ref=ref,
                    metadata={"workspace_fallback": True, "target_action": f"{invocation[0]}#{invocation[1]}"},
                )
                self._add_node(node, prerequisites)
                expansion = _InvocationExpansion((key,), (key,))
                self._cache[invocation] = expansion
                return expansion

            try:
                local_graph = graph_from_workflow(invocation[0], workflow)
            except WorkflowGraphError as exc:
                raise WorkflowRuntimeError(str(exc)) from exc

            mapped_leaves: dict[str, tuple[str, ...]] = {}
            mapped_roots: dict[str, tuple[str, ...]] = {}
            for local in local_graph.topological_order():
                dependencies: list[str] = []
                for dependency in local.depends_on:
                    dependencies.extend(mapped_leaves[dependency])
                if not local.depends_on:
                    dependencies.extend(str(item) for item in prerequisites if str(item))

                if local.ref.kind == "target":
                    child = self.expand(local.ref.name, local.ref.action, dependencies)
                    mapped_roots[local.key] = child.roots
                    mapped_leaves[local.key] = child.leaves
                    continue

                key = self._add_node(
                    replace(
                        local,
                        metadata={
                            **dict(local.metadata),
                            "target_action": f"{invocation[0]}#{invocation[1]}",
                        },
                    ),
                    dependencies,
                )
                mapped_roots[local.key] = (key,)
                mapped_leaves[local.key] = (key,)

            roots: list[str] = []
            for local in local_graph.roots:
                roots.extend(mapped_roots[local.key])
            leaves: list[str] = []
            for local in local_graph.leaves:
                leaves.extend(mapped_leaves[local.key])
            expansion = _InvocationExpansion(
                tuple(dict.fromkeys(roots)),
                tuple(dict.fromkeys(leaves)),
            )
            self._cache[invocation] = expansion
            return expansion
        finally:
            self._stack.pop()

    def graph(self, target_names: Sequence[str]) -> ExecutionGraph:
        selected = tuple(dict.fromkeys(str(name).strip() for name in target_names if str(name).strip()))
        if not selected:
            raise WorkflowRuntimeError("Workspace workflow planning requires at least one target.")
        for target_name in selected:
            self.expand(target_name, self.action)
        nodes = tuple(
            replace(self._nodes[key], depends_on=tuple(self._dependencies.get(key, ())))
            for key in self._nodes
        )
        graph = ExecutionGraph(
            target=selected[0] if len(selected) == 1 else "workspace",
            workflow=self.action,
            nodes=nodes,
            targets=selected,
        )
        try:
            graph.topological_order()
        except WorkflowGraphError as exc:
            raise WorkflowRuntimeError(str(exc)) from exc
        return graph


def plan_workspace_targets(config: ProjectConfig, target_names: Sequence[str], action: str) -> ExecutionGraph:
    return _WorkspaceGraphPlanner(config, str(action or "").strip()).graph(target_names)


def validate_workspace_workflows(config: ProjectConfig) -> None:
    """Validate every declared target workflow after repository config load.

    Local graph validation happens while parsing each workflow. This pass uses
    the workspace planner so target-to-target references, recursive expansion,
    and cross-target cycles are validated consistently with runtime planning.
    """
    for target_name in sorted(config.targets):
        target = config.targets[target_name]
        for action in sorted(target.workflows):
            plan_workspace_targets(config, [target_name], action)


def plan_selected_workflow(config: ProjectConfig, action: str) -> ExecutionGraph:
    workflow = selected_workflow(config, action)
    if workflow is None:
        target = config.selected_target or "<default>"
        raise WorkflowRuntimeError(f"Target {target!r} has no configured workflow for action {action!r}.")
    return plan_workspace_targets(config, [config.selected_target], action)

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


def _target_scoped_config(config: ProjectConfig, target_name: str) -> ProjectConfig:
    """Resolve one referenced target without losing CLI-time runner overrides."""
    name = str(target_name or "").strip()
    if not name or name == config.selected_target:
        return config
    target = config.targets.get(name)
    if target is None:
        raise WorkflowRuntimeError(f"Unknown workflow target {name!r}.")

    child: ProjectConfig | None = None
    if config.config_path is not None:
        # A referenced target must be resolved through the same repository
        # config as a directly selected target. Configuration failures are not
        # swallowed: doing so could silently execute a different root/runner.
        from .config import load_project_config
        variant = config.variant if config.variant and config.variant in target.variants else ""
        child = load_project_config(
            str(config.repo_root),
            str(config.config_path),
            target_name=name,
            variant_name=variant,
            kind_override="auto",
        )

    if child is None:
        root = _root_for_target(config, target)
        child = replace(
            config,
            kind=target.effective_runner,
            runner=target.effective_runner,
            platform=target.platform or target.effective_runner,
            selected_target=name,
            project_root=root,
            restore_target=target.effective_restore_target() or root,
            build_target=target.effective_build_target() or root,
            test_target=target.effective_test_target() or root,
            run_target=target.effective_run_project() or root,
            restore_command=target.restore_command,
            build_command=target.build_command,
            test_command=target.test_command,
            package_command=target.package_command,
            run_command=target.run_command,
            env=dict(target.env) if "env" in target.declared_fields or target.env else dict(config.env),
            execution_environment=target.execution_environment or config.execution_environment,
            chroot_name=target.chroot_name or config.chroot_name,
            device_serial=target.device_serial or config.device_serial,
            runner_options=_merge_options(dict(config.configured_runner_options or config.runner_options), target.options),
        )

    overrides = dict(config.runtime_runner_overrides or {})
    if overrides:
        child.runner_options = _merge_options(child.runner_options, overrides)
        child.runtime_runner_overrides = copy.deepcopy(overrides)
    return child


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
    job_metadata = dict(job.metadata)
    job_scope = str(job_metadata.get("scope") or ("target" if job_metadata.get("target_local") else "repository"))
    job_target = str(job_metadata.get("target") or "")
    job_local_name = str(job_metadata.get("local_name") or job.name)
    job_key = str(job_metadata.get("job_key") or job.name)
    metadata: dict[str, object] = {
        "runner": job.runner,
        "project_type": job.runner,
        "project_root": job.root,
        "workflow_job": job.name,
        "workflow_job_mode": job.command_mode,
        "workflow_job_scope": job_scope,
        "workflow_job_target": job_target,
        "workflow_job_local_name": job_local_name,
        "workflow_job_key": job_key,
        "workflow_job_target_local": job_scope == "target" or bool(job_metadata.get("target_local")),
        **job_metadata,
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
    if job.root:
        root = job.root
    elif bool(job.metadata.get("target_local")):
        root = config.project_root or str(config.repo_root)
    else:
        root = str(config.repo_root)
    effective_env = {} if "env" in job.declared_fields else dict(config.env)
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


def _node_specs(config: ProjectConfig, node: WorkflowNode, *, validation_skip: tuple[bool, bool, bool] = (False, False, False)) -> list[CommandSpec]:
    ref = node.ref
    target_config = _target_scoped_config(config, node.target)
    if ref.kind == "target":
        raise WorkflowRuntimeError(f"Unexpanded target workflow node reached execution: {ref.raw}")
    if ref.kind == "test":
        from .test_declarations import command_specs as declared_test_specs
        specs = declared_test_specs(target_config, [ref.name])
        execution_environment = target_config.execution_environment
        chroot_name = target_config.chroot_name
        device_serial = target_config.device_serial
        runner_options = target_config.runner_options
        runner_name = str(config.tests[ref.name].runner)
        effective_action = "test"
        inputs = ()
        outputs = ()
    elif ref.kind == "job":
        job = config.jobs.get(ref.name)
        if job is None:
            raise WorkflowRuntimeError(f"Workflow references missing job {ref.name!r}.")
        specs = _job_specs(target_config, job)
        execution_environment = (
            job.execution_environment
            if job.execution_environment and job.execution_environment != "auto"
            else target_config.execution_environment
        )
        chroot_name = job.chroot_name or target_config.chroot_name
        device_serial = job.device_serial or target_config.device_serial
        runner_options = _merge_options(target_config.runner_options, job.options)
        runner_name = job.runner
        effective_action = job.action
        inputs = job.inputs
        outputs = job.outputs
    elif ref.kind == "runner":
        target = config.targets.get(node.target)
        root = _root_for_target(target_config, target)
        node_config = _runner_config(target_config, runner=ref.name, root=root)
        if ref.action in {"validate", "wine-validate"} and any(validation_skip) and bool(node.metadata.get("workspace_fallback")):
            skip_restore, skip_build, skip_test = validation_skip
            runner = get_project_runner(ref.name)
            specs = runner.build_validation_specs(
                node_config,
                wine=ref.action == "wine-validate",
                skip_restore=skip_restore,
                skip_build=skip_build,
                skip_test=skip_test,
            )
        else:
            specs = _runner_action_specs(node_config, ref.name, ref.action)
        execution_environment = target_config.execution_environment
        chroot_name = target_config.chroot_name
        device_serial = target_config.device_serial
        runner_options = dict(target_config.runner_options)
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
                "workflow_effective_cwd": spec.cwd or "",
                "workflow_name": node.workflow,
                "workflow_node_key": node.key,
                "workflow_node_id": ref.step_id,
                "workflow_node_index": node.index,
                "workflow_ref": ref.raw,
                "workflow_ref_kind": ref.kind,
                "workflow_resolution_scope": ref.resolution_scope,
                "workflow_resolution_target": ref.resolution_target,
                "workflow_resolution_local_name": ref.resolution_local_name,
                "workflow_resolution_key": ref.resolution_key or ref.name,
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
                "repository_identity": (
                    target_config.repository_identity.as_dict()
                    if target_config.repository_identity is not None
                    else {}
                ),
            }
        )
        spec.metadata = metadata
    return specs


def _specs_from_graph(
    config: ProjectConfig,
    graph: ExecutionGraph,
    *,
    validation_skip: tuple[bool, bool, bool] = (False, False, False),
) -> list[CommandSpec]:
    ordered = graph.topological_order()
    specs: list[CommandSpec] = []
    for node in ordered:
        specs.extend(_node_specs(config, node, validation_skip=validation_skip))
    if not specs:
        scope = ",".join(graph.targets) if graph.targets else graph.target
        raise WorkflowRuntimeError(f"Workflow {scope}.{graph.workflow} produced no executable commands.")

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
        metadata["workflow_graph_schema_version"] = 2
        metadata["workspace_targets"] = list(graph.targets or ((graph.target,) if graph.target != "workspace" else ()))
        spec.metadata = metadata
    from .workflow_scheduler import annotate_specs_with_scopes
    return annotate_specs_with_scopes(config, graph, specs)


def build_workspace_specs(config: ProjectConfig, target_names: Sequence[str], action: str) -> list[CommandSpec]:
    graph = plan_workspace_targets(config, target_names, action)
    return _specs_from_graph(config, graph)


def build_workspace_validation_specs(
    config: ProjectConfig,
    target_names: Sequence[str],
    *,
    wine: bool = False,
    skip_restore: bool = False,
    skip_build: bool = False,
    skip_test: bool = False,
) -> list[CommandSpec]:
    action = "wine-validate" if wine else "validate"
    selected = tuple(dict.fromkeys(str(name).strip() for name in target_names if str(name).strip()))
    if any((skip_restore, skip_build, skip_test)):
        explicit = [name for name in selected if config.targets.get(name) and action in config.targets[name].workflows]
        if explicit:
            raise SystemExit(
                "Validation skip flags cannot rewrite explicit target workflows for workspace targets: "
                + ", ".join(explicit)
            )
    graph = plan_workspace_targets(config, selected, action)
    return _specs_from_graph(
        config,
        graph,
        validation_skip=(skip_restore, skip_build, skip_test),
    )


def build_workflow_specs(config: ProjectConfig, action: str) -> list[CommandSpec]:
    graph = plan_selected_workflow(config, action)
    return _specs_from_graph(config, graph)


def workflow_graph_metadata(config: ProjectConfig, action: str) -> dict[str, object]:
    return plan_selected_workflow(config, action).as_dict()


def workflow_runner_kinds(config: ProjectConfig, action: str) -> tuple[str, ...]:
    graph = plan_selected_workflow(config, action)
    runners: list[str] = []
    for node in graph.nodes:
        if node.ref.kind == "runner":
            runners.append(node.ref.name)
        elif node.ref.kind == "test" and node.ref.name in config.tests:
            runners.append(str(config.tests[node.ref.name].runner))
        elif node.ref.kind == "job" and node.ref.name in config.jobs:
            runners.append(config.jobs[node.ref.name].runner)
    return tuple(dict.fromkeys(runners))
