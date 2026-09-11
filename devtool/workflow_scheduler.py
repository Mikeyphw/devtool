from __future__ import annotations

"""MP09 safe workflow scheduling and reservation analysis.

The scheduler operates on the already-expanded MP05 workspace graph. It never
teaches runners about one another: it groups a node's ordinary CommandSpecs,
calculates conservative repository read/write scopes, and only overlaps nodes
when dependencies, scopes, and shared-resource limits all permit it.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path
import threading
from typing import Any, Iterable, Mapping, Sequence

from .models import CommandSpec, OperationResult, ProjectConfig
from .reservations import paths_overlap
from .workflow_graph import ExecutionGraph, WorkflowNode


class WorkflowScheduleError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WorkflowNodeScope:
    key: str
    target: str
    workflow: str
    runner: str
    action: str
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    writes_known: bool = False
    read_only: bool = False
    resource_class: str = "normal"
    scope_reason: str = ""

    @property
    def parallel_eligible(self) -> bool:
        return self.read_only or self.writes_known

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "target": self.target,
            "workflow": self.workflow,
            "runner": self.runner,
            "action": self.action,
            "reads": list(self.reads),
            "writes": list(self.writes),
            "writes_known": self.writes_known,
            "read_only": self.read_only,
            "parallel_eligible": self.parallel_eligible,
            "resource_class": self.resource_class,
            "scope_reason": self.scope_reason,
        }


@dataclass(frozen=True, slots=True)
class WorkflowScheduleNode:
    key: str
    target: str
    workflow: str
    runner: str
    action: str
    depends_on: tuple[str, ...]
    spec_indexes: tuple[int, ...]
    spec_names: tuple[str, ...]
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    writes_known: bool
    read_only: bool
    resource_class: str
    scope_reason: str

    @property
    def parallel_eligible(self) -> bool:
        return self.read_only or self.writes_known

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "target": self.target,
            "workflow": self.workflow,
            "runner": self.runner,
            "action": self.action,
            "depends_on": list(self.depends_on),
            "spec_indexes": list(self.spec_indexes),
            "spec_names": list(self.spec_names),
            "reads": list(self.reads),
            "writes": list(self.writes),
            "writes_known": self.writes_known,
            "read_only": self.read_only,
            "parallel_eligible": self.parallel_eligible,
            "resource_class": self.resource_class,
            "scope_reason": self.scope_reason,
        }


@dataclass(frozen=True, slots=True)
class WorkflowScheduleWave:
    index: int
    nodes: tuple[str, ...]
    serialized_reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {"index": self.index, "nodes": list(self.nodes), "serialized_reason": self.serialized_reason}


@dataclass(frozen=True, slots=True)
class WorkflowExecutionPlan:
    target: str
    workflow: str
    targets: tuple[str, ...]
    nodes: tuple[WorkflowScheduleNode, ...]
    waves: tuple[WorkflowScheduleWave, ...]
    parallel_requested: bool
    parallel_effective: bool
    max_parallel_nodes: int
    max_gradle_nodes: int
    max_dotnet_nodes: int
    reservation_paths: tuple[str, ...]
    reservation_paths_known: bool
    notes: tuple[str, ...] = ()

    @property
    def by_key(self) -> dict[str, WorkflowScheduleNode]:
        return {node.key: node for node in self.nodes}

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "target": self.target,
            "targets": list(self.targets),
            "workflow": self.workflow,
            "parallel_requested": self.parallel_requested,
            "parallel_effective": self.parallel_effective,
            "max_parallel_nodes": self.max_parallel_nodes,
            "resource_limits": {
                "gradle": self.max_gradle_nodes,
                "dotnet": self.max_dotnet_nodes,
            },
            "reservation": {
                "paths_known": self.reservation_paths_known,
                "paths": list(self.reservation_paths),
            },
            "waves": [wave.as_dict() for wave in self.waves],
            "nodes": [node.as_dict() for node in self.nodes],
            "notes": list(self.notes),
        }


_GLOB_CHARS = set("*?[")


def _target_root(config: ProjectConfig, target_name: str) -> Path:
    target = config.targets.get(target_name)
    raw = ""
    if target is not None:
        raw = target.root or target.cwd or target.build_target or target.restore_target
    path = Path(raw or config.project_root or config.repo_root).expanduser()
    if not path.is_absolute():
        path = config.repo_root / path
    if path.is_file():
        path = path.parent
    return path.resolve(strict=False)


def _job_root(config: ProjectConfig, job_name: str) -> Path:
    job = config.jobs[job_name]
    path = Path(job.root or config.repo_root).expanduser()
    if not path.is_absolute():
        path = config.repo_root / path
    return path.resolve(strict=False)


def _static_glob_prefix(raw: str) -> str:
    text = str(raw or "").replace("\\", "/").strip()
    if not text:
        return "."
    parts = []
    for part in Path(text).parts:
        if any(char in part for char in _GLOB_CHARS):
            break
        parts.append(part)
    if not parts:
        return "."
    return str(Path(*parts))


def _repo_scope(repo_root: Path, base: Path, raw: str) -> tuple[str, bool]:
    prefix = _static_glob_prefix(raw)
    path = Path(prefix)
    if not path.is_absolute():
        path = base / path
    resolved = path.expanduser().resolve(strict=False)
    try:
        relative = resolved.relative_to(repo_root.resolve())
    except ValueError:
        return str(resolved), False
    text = relative.as_posix()
    return text if text and text != "." else ".", True


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


def _resource_class(runner: str, metadata: Mapping[str, object]) -> str:
    override = str(metadata.get("resource_class") or "").strip().lower()
    if override:
        return override
    name = str(runner or "").strip().lower()
    if name in {"android", "gradle", "maven"}:
        return "gradle"
    if name == "dotnet":
        return "dotnet"
    return "normal"


def scope_for_graph_node(config: ProjectConfig, node: WorkflowNode) -> WorkflowNodeScope:
    target = config.targets.get(node.target)
    runner = node.runner
    action = node.action
    metadata: dict[str, object] = dict(target.metadata) if target is not None else {}
    declared_inputs: Sequence[str] = target.inputs if target is not None else ()
    declared_outputs: Sequence[str] = target.outputs if target is not None else ()
    base = _target_root(config, node.target)

    if node.ref.kind == "job":
        job = config.jobs.get(node.ref.name)
        if job is None:
            raise WorkflowScheduleError(f"Workflow node {node.key!r} references missing job {node.ref.name!r}.")
        runner = job.runner
        action = job.action or "command"
        metadata = {**metadata, **dict(job.metadata)}
        declared_inputs = job.inputs
        declared_outputs = job.outputs
        base = _job_root(config, job.name)

    base_scope, base_safe = _repo_scope(config.repo_root, base, ".")
    # Explicit inputs are a promise precise enough for safe scheduling. When
    # inputs are omitted we conservatively assume the node may read its whole
    # target/job root.
    reads: list[str] = [] if declared_inputs else ([base_scope] if base_safe else [])
    unsafe_reads: list[str] = []
    for raw in declared_inputs:
        value, safe = _repo_scope(config.repo_root, base, raw)
        if safe:
            reads.append(value)
        else:
            unsafe_reads.append(value)

    writes: list[str] = []
    unsafe_writes: list[str] = []
    for raw in declared_outputs:
        value, safe = _repo_scope(config.repo_root, base, raw)
        if safe:
            writes.append(value)
        else:
            unsafe_writes.append(value)

    read_only = bool(metadata.get("read_only", False))
    writes_known = read_only or (bool(declared_outputs) and not unsafe_writes)
    if unsafe_writes:
        reason = "declared output escapes repository: " + ", ".join(unsafe_writes)
    elif read_only:
        reason = "node explicitly declares read_only=true"
    elif declared_outputs:
        reason = "write set comes from explicit outputs"
    else:
        reason = "write set is unknown because no outputs are declared"
    if unsafe_reads:
        reason += "; input scope outside repository: " + ", ".join(unsafe_reads)
        writes_known = False

    return WorkflowNodeScope(
        key=node.key,
        target=node.target,
        workflow=node.workflow,
        runner=runner,
        action=action,
        reads=_dedupe(reads),
        writes=_dedupe(writes),
        writes_known=writes_known,
        read_only=read_only,
        resource_class=_resource_class(runner, metadata),
        scope_reason=reason,
    )


def annotate_specs_with_scopes(config: ProjectConfig, graph: ExecutionGraph, specs: list[CommandSpec]) -> list[CommandSpec]:
    scopes = {node.key: scope_for_graph_node(config, node) for node in graph.nodes}
    for spec in specs:
        key = str(spec.metadata.get("workflow_node_key") or "")
        scope = scopes.get(key)
        if scope is None:
            continue
        metadata = dict(spec.metadata)
        metadata.update({
            "workflow_read_scopes": list(scope.reads),
            "workflow_write_scopes": list(scope.writes),
            "workflow_write_scope_known": scope.writes_known,
            "workflow_read_only": scope.read_only,
            "workflow_resource_class": scope.resource_class,
            "workflow_scope_reason": scope.scope_reason,
        })
        spec.metadata = metadata
    return specs


def _overlap(left: Iterable[str], right: Iterable[str]) -> tuple[str, str] | None:
    for a in left:
        for b in right:
            try:
                if paths_overlap(a, b):
                    return a, b
            except Exception:
                return a, b
    return None


def scheduling_conflict(left: WorkflowScheduleNode, right: WorkflowScheduleNode) -> str:
    if not left.parallel_eligible:
        return f"{left.key} has an unknown write set"
    if not right.parallel_eligible:
        return f"{right.key} has an unknown write set"
    collision = _overlap(left.writes, right.writes)
    if collision:
        return f"write/write overlap {collision[0]} <-> {collision[1]}"
    collision = _overlap(left.writes, right.reads)
    if collision:
        return f"write/read overlap {collision[0]} <-> {collision[1]}"
    collision = _overlap(right.writes, left.reads)
    if collision:
        return f"write/read overlap {collision[1]} <-> {collision[0]}"
    return ""


def _nodes_from_specs(specs: Sequence[CommandSpec]) -> tuple[WorkflowScheduleNode, ...]:
    grouped: dict[str, list[tuple[int, CommandSpec]]] = {}
    order: list[str] = []
    for index, spec in enumerate(specs):
        key = str(spec.metadata.get("workflow_node_key") or "").strip()
        if not key:
            continue
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append((index, spec))
    nodes: list[WorkflowScheduleNode] = []
    for key in order:
        parts = grouped[key]
        first = parts[0][1]
        metadata = first.metadata
        nodes.append(WorkflowScheduleNode(
            key=key,
            target=str(metadata.get("workflow_target") or ""),
            workflow=str(metadata.get("workflow_name") or ""),
            runner=str(metadata.get("workflow_runner") or metadata.get("runner") or ""),
            action=str(metadata.get("workflow_action") or ""),
            depends_on=tuple(str(item) for item in metadata.get("workflow_depends_on", []) or [] if str(item)),
            spec_indexes=tuple(index for index, _ in parts),
            spec_names=tuple(spec.name for _, spec in parts),
            reads=tuple(str(item) for item in metadata.get("workflow_read_scopes", []) or [] if str(item)),
            writes=tuple(str(item) for item in metadata.get("workflow_write_scopes", []) or [] if str(item)),
            writes_known=bool(metadata.get("workflow_write_scope_known", False)),
            read_only=bool(metadata.get("workflow_read_only", False)),
            resource_class=str(metadata.get("workflow_resource_class") or "normal"),
            scope_reason=str(metadata.get("workflow_scope_reason") or ""),
        ))
    return tuple(nodes)


def _resource_allowed(selected: Sequence[WorkflowScheduleNode], candidate: WorkflowScheduleNode, *, max_gradle: int, max_dotnet: int) -> bool:
    if candidate.resource_class == "gradle" and max_gradle > 0:
        if sum(1 for node in selected if node.resource_class == "gradle") >= max_gradle:
            return False
    if candidate.resource_class == "dotnet" and max_dotnet > 0:
        if sum(1 for node in selected if node.resource_class == "dotnet") >= max_dotnet:
            return False
    return True


def build_execution_plan_from_specs(
    config: ProjectConfig,
    specs: Sequence[CommandSpec],
    *,
    parallel_requested: bool | None = None,
    max_parallel_nodes: int = 0,
    max_gradle_nodes: int | None = None,
    max_dotnet_nodes: int | None = None,
) -> WorkflowExecutionPlan | None:
    nodes = _nodes_from_specs(specs)
    if not nodes:
        return None
    by_key = {node.key: node for node in nodes}
    for node in nodes:
        missing = [key for key in node.depends_on if key not in by_key]
        if missing:
            raise WorkflowScheduleError(f"Workflow scheduling node {node.key!r} depends on unknown nodes: {', '.join(missing)}")

    policy = config.workspace.scheduler
    requested = policy.parallel if parallel_requested is None else bool(parallel_requested)
    max_nodes = int(max_parallel_nodes or policy.max_parallel_nodes or 2)
    max_nodes = max(1, max_nodes)
    gradle_limit = policy.max_gradle_nodes if max_gradle_nodes is None else int(max_gradle_nodes)
    dotnet_limit = policy.max_dotnet_nodes if max_dotnet_nodes is None else int(max_dotnet_nodes)
    gradle_limit = max(0, gradle_limit)
    dotnet_limit = max(0, dotnet_limit)

    indegree = {node.key: len(node.depends_on) for node in nodes}
    dependents: dict[str, list[str]] = {node.key: [] for node in nodes}
    position = {node.key: index for index, node in enumerate(nodes)}
    for node in nodes:
        for dependency in node.depends_on:
            dependents[dependency].append(node.key)
    remaining = set(by_key)
    waves: list[WorkflowScheduleWave] = []
    notes: list[str] = []
    while remaining:
        ready = [by_key[key] for key in remaining if indegree[key] == 0]
        ready.sort(key=lambda node: position[node.key])
        if not ready:
            raise WorkflowScheduleError("Workflow scheduling graph contains a dependency cycle.")
        selected: list[WorkflowScheduleNode] = []
        reason = ""
        if not requested:
            selected = [ready[0]]
            reason = "sequential policy"
        else:
            for candidate in ready:
                if len(selected) >= max_nodes:
                    break
                if not _resource_allowed(selected, candidate, max_gradle=gradle_limit, max_dotnet=dotnet_limit):
                    continue
                conflict = next((scheduling_conflict(existing, candidate) for existing in selected if scheduling_conflict(existing, candidate)), "")
                if conflict:
                    continue
                if not candidate.parallel_eligible and selected:
                    continue
                if not candidate.parallel_eligible and not selected:
                    selected = [candidate]
                    reason = candidate.scope_reason or "unknown write set"
                    break
                selected.append(candidate)
            if not selected:
                selected = [ready[0]]
                reason = "resource/scope safety serialization"
            elif len(selected) == 1 and len(ready) > 1 and not reason:
                candidate = selected[0]
                reason = candidate.scope_reason if not candidate.parallel_eligible else "resource/scope safety serialization"
        waves.append(WorkflowScheduleWave(index=len(waves) + 1, nodes=tuple(node.key for node in selected), serialized_reason=reason))
        for node in selected:
            remaining.remove(node.key)
            for dependent in dependents[node.key]:
                indegree[dependent] -= 1

    parallel_effective = requested and any(len(wave.nodes) > 1 for wave in waves)
    if requested and not parallel_effective:
        notes.append("Parallel scheduling was requested, but dependency/scope/resource safety produced only serial waves.")
    if any(not node.parallel_eligible for node in nodes):
        notes.append("Nodes without explicit outputs remain serialized because their write set is unknown.")

    reservation_known = all(node.parallel_eligible for node in nodes)
    reservation_paths = _dedupe(path for node in nodes for path in node.writes) if reservation_known else ()
    first = nodes[0]
    workspace_targets = tuple(dict.fromkeys(str(spec.metadata.get("workflow_target") or "") for spec in specs if str(spec.metadata.get("workflow_target") or "")))
    return WorkflowExecutionPlan(
        target=workspace_targets[0] if len(workspace_targets) == 1 else "workspace",
        targets=workspace_targets,
        workflow=first.workflow,
        nodes=nodes,
        waves=tuple(waves),
        parallel_requested=requested,
        parallel_effective=parallel_effective,
        max_parallel_nodes=max_nodes,
        max_gradle_nodes=gradle_limit,
        max_dotnet_nodes=dotnet_limit,
        reservation_paths=reservation_paths,
        reservation_paths_known=reservation_known,
        notes=tuple(notes),
    )


class _NodeProgressProxy:
    """Give concurrent nodes independent phase identity over one live HUD sink."""

    def __init__(self, sink: Any, lock: threading.RLock):
        self._sink = sink
        self._lock = lock
        self.current_phase: dict[str, Any] | None = None

    def plan_phases(self, phases: list[dict[str, Any]], *, append: bool = True) -> None:
        # The scheduler publishes the complete plan once before worker threads.
        return None

    def phase_started(self, name: str, *, index: int, total: int, argv: list[str] | None = None, metadata: dict[str, Any] | None = None) -> None:
        metadata = dict(metadata or {})
        index = int(metadata.get("workflow_spec_index") or index)
        total = int(metadata.get("workflow_spec_total") or total)
        with self._lock:
            previous = self._sink.current_phase
            self._sink.current_phase = self.current_phase
            self._sink.phase_started(name, index=index, total=total, argv=argv, metadata=metadata)
            self.current_phase = dict(self._sink.current_phase or {})
            self._sink.current_phase = previous

    def progress(self, **kwargs: Any) -> None:
        with self._lock:
            previous = self._sink.current_phase
            self._sink.current_phase = self.current_phase
            self._sink.progress(**kwargs)
            self.current_phase = dict(self._sink.current_phase or {})
            self._sink.current_phase = previous

    def phase_finished(self, result: OperationResult) -> None:
        with self._lock:
            previous = self._sink.current_phase
            self._sink.current_phase = self.current_phase
            self._sink.phase_finished(result)
            self.current_phase = dict(self._sink.current_phase or {})
            self._sink.current_phase = previous

    def cancel_requested(self) -> bool:
        return bool(self._sink.cancel_requested())

    def clear_cancel(self) -> None:
        self._sink.clear_cancel()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sink, name)


def _node_terminal_state(results: Sequence[OperationResult]) -> str:
    states = [str(result.metadata.get("workflow_node_state") or "") for result in results]
    for state in ("cancelled", "failed", "blocked", "skipped"):
        if state in states:
            return state
    if any(int(result.returncode or 0) != 0 for result in results):
        return "failed"
    return "passed"


def run_workflow_specs(
    config: ProjectConfig,
    specs: list[CommandSpec],
    *,
    verbose: bool,
    dry_run: bool,
    continue_on_error: bool,
    log_path: Path | str | None,
    tasker_progress: Any = None,
    shutdown_dotnet: bool | None = None,
    supervisor_probe: Any = None,
    heartbeat_callback: Any = None,
    progress_callback: Any = None,
    parallel_requested: bool | None = None,
    max_parallel_nodes: int = 0,
) -> tuple[list[OperationResult], str, WorkflowExecutionPlan | None]:
    """Execute workflow specs using safe dependency/scope-aware waves.

    Sequential execution delegates unchanged to ``runners.run_specs``. Parallel
    execution runs each workflow node atomically (its command parts remain
    sequential) and never starts a node until all dependency states are known.
    """
    from .runners import run_specs

    plan = build_execution_plan_from_specs(
        config,
        specs,
        parallel_requested=parallel_requested,
        max_parallel_nodes=max_parallel_nodes,
    )
    if plan is None or not plan.parallel_effective:
        results, raw = run_specs(
            config,
            specs,
            verbose=verbose,
            dry_run=dry_run,
            continue_on_error=continue_on_error,
            log_path=log_path,
            tasker_progress=tasker_progress,
            shutdown_dotnet=shutdown_dotnet,
            supervisor_probe=supervisor_probe,
            heartbeat_callback=heartbeat_callback,
            progress_callback=progress_callback,
        )
        return results, raw, plan

    by_index = {index: spec for index, spec in enumerate(specs)}
    node_specs = {node.key: [by_index[index] for index in node.spec_indexes] for node in plan.nodes}
    node_by_key = plan.by_key
    results_by_node: dict[str, list[OperationResult]] = {}
    raw_by_node: dict[str, str] = {}
    statuses: dict[str, str] = {}
    fail_fast = False
    cancelled = False
    progress_lock = threading.RLock()

    if tasker_progress is not None and not dry_run:
        tasker_progress.plan_phases([
            {
                "name": spec.name,
                "target": str(spec.metadata.get("workflow_target") or spec.metadata.get("bundle_target") or ""),
                "workspace": str(spec.metadata.get("workspace") or ""),
                "workspace_root": str(spec.metadata.get("workspace_root") or ""),
                "workflow": str(spec.metadata.get("workflow_name") or ""),
                "node_id": str(spec.metadata.get("workflow_node_key") or ""),
                "runner": str(spec.metadata.get("workflow_runner") or spec.metadata.get("runner") or ""),
                "execution_environment": str(spec.metadata.get("execution_environment") or spec.metadata.get("effective_execution_environment") or ""),
                "depends_on": [str(value) for value in spec.metadata.get("workflow_depends_on", []) or [] if str(value).strip()],
            }
            for spec in specs
        ])

    def execute_node(key: str) -> tuple[str, list[OperationResult], str]:
        local_specs = node_specs[key]
        sink = _NodeProgressProxy(tasker_progress, progress_lock) if tasker_progress is not None else None
        # Shared final log writes are serialized after workers finish. Per-node
        # run_specs logs stay independent and cannot interleave/corrupt JSON/text.
        def locked_progress(snapshot: Any) -> None:
            if progress_callback is None:
                return
            with progress_lock:
                progress_callback(snapshot)

        def locked_heartbeat(*args: Any, **kwargs: Any) -> Any:
            if heartbeat_callback is None:
                return None
            with progress_lock:
                return heartbeat_callback(*args, **kwargs)

        local_results, local_raw = run_specs(
            config,
            local_specs,
            verbose=False,
            dry_run=dry_run,
            continue_on_error=continue_on_error,
            log_path=None,
            tasker_progress=sink,
            shutdown_dotnet=False,
            supervisor_probe=supervisor_probe,
            heartbeat_callback=locked_heartbeat if heartbeat_callback is not None else None,
            progress_callback=locked_progress if progress_callback is not None else None,
            workflow_initial_statuses=dict(statuses),
            workflow_initial_fail_fast=fail_fast,
            workflow_initial_cancelled=cancelled,
        )
        return key, local_results, local_raw

    for wave in plan.waves:
        keys = list(wave.nodes)
        if len(keys) == 1:
            key, local_results, local_raw = execute_node(keys[0])
            results_by_node[key] = local_results
            raw_by_node[key] = local_raw
        else:
            with ThreadPoolExecutor(max_workers=min(plan.max_parallel_nodes, len(keys)), thread_name_prefix="devtool-workflow") as executor:
                futures = {executor.submit(execute_node, key): key for key in keys}
                for future in as_completed(futures):
                    key, local_results, local_raw = future.result()
                    results_by_node[key] = local_results
                    raw_by_node[key] = local_raw
        for key in keys:
            statuses[key] = _node_terminal_state(results_by_node.get(key, ()))
        if any(state == "cancelled" for state in statuses.values()):
            cancelled = True
        if not continue_on_error and any(state == "failed" for state in statuses.values()):
            fail_fast = True

    ordered_results: list[OperationResult] = []
    ordered_raw: list[str] = []
    for node in plan.nodes:
        ordered_results.extend(results_by_node.get(node.key, ()))
        text = raw_by_node.get(node.key, "")
        if text:
            ordered_raw.append(text)
    raw = "\n\n".join(ordered_raw)
    if log_path is not None and raw:
        from .logstore import write_log
        write_log(raw, log_path, append=True)
        for result in ordered_results:
            result.log_path = str(log_path)
    if verbose and raw:
        print(raw)
    return ordered_results, raw, plan
