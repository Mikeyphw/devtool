from __future__ import annotations

"""MP09 safe workflow scheduling and reservation analysis.

The scheduler operates on the already-expanded MP05 workspace graph. It never
teaches runners about one another: it groups a node's ordinary CommandSpecs,
calculates conservative repository read/write scopes, and only overlaps nodes
when dependencies, scopes, and shared-resource limits all permit it.
"""

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass, field, replace
from pathlib import Path
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

from .core.execution_contract import RunId
from .models import CommandSpec, OperationResult, ProjectConfig
from .reservations import paths_overlap
from .output_scopes import (OutputScopeError, parse_output_scopes, reservation_scope,
                            provision_node_temp, cleanup_owned_temp, hash_owned_output)
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
    job_scope: str = ""
    job_target: str = ""
    job_local_name: str = ""
    job_key: str = ""
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    writes_known: bool = False
    read_only: bool = False
    resource_class: str = "normal"
    global_reservations: tuple[str, ...] = ()
    scope_reason: str = ""
    output_scopes: tuple[dict[str, str], ...] = ()

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
            "job_scope": self.job_scope,
            "job_target": self.job_target,
            "job_local_name": self.job_local_name,
            "job_key": self.job_key,
            "target_local_job": self.job_scope == "target",
            "reads": list(self.reads),
            "writes": list(self.writes),
            "writes_known": self.writes_known,
            "read_only": self.read_only,
            "parallel_eligible": self.parallel_eligible,
            "resource_class": self.resource_class,
            "global_reservations": list(self.global_reservations),
            "scope_reason": self.scope_reason,
            "output_scopes": list(self.output_scopes),
        }


@dataclass(frozen=True, slots=True)
class WorkflowScheduleNode:
    key: str
    target: str
    workflow: str
    runner: str
    action: str
    job_scope: str
    job_target: str
    job_local_name: str
    job_key: str
    depends_on: tuple[str, ...]
    spec_indexes: tuple[int, ...]
    spec_names: tuple[str, ...]
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    writes_known: bool
    read_only: bool
    resource_class: str
    global_reservations: tuple[str, ...]
    scope_reason: str
    output_scopes: tuple[dict[str, str], ...] = ()

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
            "job_scope": self.job_scope,
            "job_target": self.job_target,
            "job_local_name": self.job_local_name,
            "job_key": self.job_key,
            "target_local_job": self.job_scope == "target",
            "depends_on": list(self.depends_on),
            "spec_indexes": list(self.spec_indexes),
            "spec_names": list(self.spec_names),
            "reads": list(self.reads),
            "writes": list(self.writes),
            "writes_known": self.writes_known,
            "read_only": self.read_only,
            "parallel_eligible": self.parallel_eligible,
            "resource_class": self.resource_class,
            "global_reservations": list(self.global_reservations),
            "scope_reason": self.scope_reason,
            "output_scopes": list(self.output_scopes),
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
    # Populated by the live scheduler. Static waves remain a conservative
    # presentation artifact; they do not describe actual execution barriers.
    actual_start_order: tuple[str, ...] = ()
    actual_finish_order: tuple[str, ...] = ()
    node_outcomes: tuple[tuple[str, str], ...] = ()
    resource_wait_reasons: tuple[tuple[str, str], ...] = ()

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
            "static_waves": [wave.as_dict() for wave in self.waves],
            "runtime_scheduling": {
                "mode": "dependency-ready" if self.actual_finish_order else "not-executed",
                "actual_start_order": list(self.actual_start_order),
                "actual_finish_order": list(self.actual_finish_order),
                "node_outcomes": dict(self.node_outcomes),
                "resource_wait_reasons": dict(self.resource_wait_reasons),
            },
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


def _job_root(config: ProjectConfig, job_name: str, target_name: str = "") -> Path:
    job = config.jobs[job_name]
    default_root: object = config.repo_root
    if bool(job.metadata.get("target_local")) and target_name:
        default_root = _target_root(config, target_name)
    path = Path(job.root or default_root).expanduser()
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


def _coerce_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n", ""}:
        return False
    return default


def _object_sequence(value: object) -> tuple[object, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(value)
    return ()


def _safe_int(value: object, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _resource_class(runner: str, metadata: Mapping[str, object]) -> str:
    override = str(metadata.get("resource_class") or "").strip().lower().replace("_", "-")
    supported = {
        "normal", "gradle", "dotnet",
        "cpu-heavy", "gradle-heavy", "dotnet-heavy",
        "device-exclusive", "network-heavy", "generic-read-only",
    }
    if override in supported:
        return override
    name = str(runner or "").strip().lower()
    if name in {"android", "gradle", "maven"}:
        return "gradle"
    if name == "dotnet":
        return "dotnet"
    return "normal"


def _global_reservations(metadata: Mapping[str, object]) -> tuple[str, ...]:
    raw = metadata.get("global_reservations")
    if raw in (None, ""):
        return ()
    values = raw if isinstance(raw, (list, tuple, set)) else (raw,)
    normalized: list[str] = []
    for value in values:
        token = str(value or "").strip()
        if token and token not in normalized:
            normalized.append(token)
    return tuple(normalized)


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
        base = _job_root(config, job.name, node.target)

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
    typed = parse_output_scopes(metadata.get("output_scopes"), context=f"node {node.key} output_scopes")
    typed_reservations: list[str] = []
    for scope in typed:
        location = reservation_scope(scope, node_key=node.key, repo_root=config.repo_root, job_root=base)
        writes.append(location)
        if scope.reservation:
            typed_reservations.append(scope.reservation)
    for raw in declared_outputs:
        value, safe = _repo_scope(config.repo_root, base, raw)
        if safe:
            writes.append(value)
        else:
            unsafe_writes.append(value)

    read_only = _coerce_bool(metadata.get("read_only", False), False)
    if typed and read_only:
        raise WorkflowScheduleError(f"node {node.key}: read_only=true contradicts typed output scopes")
    if unsafe_reads or unsafe_writes:
        read_only = False
    writes_known = read_only or (bool(declared_outputs or typed) and not unsafe_writes)
    if unsafe_writes:
        writes_known = False
        reason = "declared output escapes repository: " + ", ".join(unsafe_writes)
    elif read_only:
        reason = "node explicitly declares read_only=true"
    elif declared_outputs or typed:
        reason = "write set comes from explicit outputs and typed reservations"
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
        job_scope=str(node.ref.resolution_scope or metadata.get("scope") or ""),
        job_target=str(node.ref.resolution_target or metadata.get("target") or ""),
        job_local_name=str(node.ref.resolution_local_name or metadata.get("local_name") or ""),
        job_key=str(node.ref.resolution_key or metadata.get("job_key") or ""),
        reads=_dedupe(reads),
        writes=_dedupe(writes),
        writes_known=writes_known,
        read_only=read_only,
        resource_class=_resource_class(runner, metadata),
        global_reservations=_dedupe((*_global_reservations(metadata), *typed_reservations)),
        scope_reason=reason,
        output_scopes=tuple(scope.as_dict() for scope in typed),
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
            "workflow_global_reservations": list(scope.global_reservations),
            "workflow_scope_reason": scope.scope_reason,
            "workflow_output_scopes": list(scope.output_scopes),
        })
        spec.metadata = metadata
    return specs


def _overlap(left: Iterable[str], right: Iterable[str]) -> tuple[str, str] | None:
    for a in left:
        for b in right:
            # External/tmp/cache scopes belong to disjoint namespaces, not to
            # repository-root '.' even when a job reads the whole checkout.
            left_ns = str(a).split('/', 1)[0] if str(a).startswith('@') else '@repository'
            right_ns = str(b).split('/', 1)[0] if str(b).startswith('@') else '@repository'
            if left_ns != right_ns:
                continue
            try:
                if paths_overlap(a, b):
                    return a, b
            except Exception:
                return a, b
    return None


def scheduling_conflict(left: WorkflowScheduleNode, right: WorkflowScheduleNode) -> str:
    shared_reservations = sorted(set(left.global_reservations).intersection(right.global_reservations))
    if shared_reservations:
        return f"global reservation conflict: {', '.join(shared_reservations)}"
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
            return ()
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
            job_scope=str(metadata.get("workflow_resolution_scope") or ""),
            job_target=str(metadata.get("workflow_resolution_target") or ""),
            job_local_name=str(metadata.get("workflow_resolution_local_name") or ""),
            job_key=str(metadata.get("workflow_resolution_key") or ""),
            depends_on=tuple(str(item) for item in _object_sequence(metadata.get("workflow_depends_on", [])) if str(item)),
            spec_indexes=tuple(index for index, _ in parts),
            spec_names=tuple(spec.name for _, spec in parts),
            reads=tuple(str(item) for item in _object_sequence(metadata.get("workflow_read_scopes", [])) if str(item)),
            writes=tuple(str(item) for item in _object_sequence(metadata.get("workflow_write_scopes", [])) if str(item)),
            writes_known=_coerce_bool(metadata.get("workflow_write_scope_known", False), False),
            read_only=_coerce_bool(metadata.get("workflow_read_only", False), False),
            resource_class=_resource_class(str(metadata.get("workflow_runner") or metadata.get("runner") or ""), {**metadata, "resource_class": metadata.get("workflow_resource_class") or metadata.get("resource_class")}),
            global_reservations=tuple(str(item) for item in _object_sequence(metadata.get("workflow_global_reservations", [])) if str(item)),
            scope_reason=str(metadata.get("workflow_scope_reason") or ""),
            output_scopes=tuple(item for item in _object_sequence(metadata.get("workflow_output_scopes", [])) if isinstance(item, dict)),
        ))
    return tuple(nodes)


def _resource_allowed(selected: Sequence[WorkflowScheduleNode], candidate: WorkflowScheduleNode, *, max_gradle: int, max_dotnet: int) -> bool:
    gradle_classes = {"gradle", "gradle-heavy"}
    dotnet_classes = {"dotnet", "dotnet-heavy"}
    if candidate.resource_class in gradle_classes and max_gradle > 0:
        if sum(1 for node in selected if node.resource_class in gradle_classes) >= max_gradle:
            return False
    if candidate.resource_class in dotnet_classes and max_dotnet > 0:
        if sum(1 for node in selected if node.resource_class in dotnet_classes) >= max_dotnet:
            return False
    if candidate.resource_class in {"device-exclusive", "network-heavy"}:
        if any(node.resource_class == candidate.resource_class for node in selected):
            return False
    return True


def build_safe_schedule_waves(
    nodes: Sequence[WorkflowScheduleNode],
    *,
    parallel_requested: bool,
    max_parallel_nodes: int,
    max_gradle_nodes: int = 1,
    max_dotnet_nodes: int = 1,
) -> tuple[tuple[WorkflowScheduleWave, ...], tuple[str, ...]]:
    """Build deterministic dependency-ready waves using shared scope/resource safety.

    This is the reusable scheduling kernel for both local workflow nodes and
    FRX-Y01 federation child operations.  Callers own only how nodes/scopes are
    derived and how each selected wave is executed.
    """
    by_key = {node.key: node for node in nodes}
    for node in nodes:
        missing = [key for key in node.depends_on if key not in by_key]
        if missing:
            raise WorkflowScheduleError(
                f"Workflow scheduling node {node.key!r} depends on unknown nodes: {', '.join(missing)}"
            )

    requested = bool(parallel_requested)
    max_nodes = max(1, int(max_parallel_nodes or 1))
    gradle_limit = max(0, int(max_gradle_nodes or 0))
    dotnet_limit = max(0, int(max_dotnet_nodes or 0))

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
                if not _resource_allowed(
                    selected, candidate, max_gradle=gradle_limit, max_dotnet=dotnet_limit
                ):
                    continue
                conflict = next(
                    (
                        scheduling_conflict(existing, candidate)
                        for existing in selected
                        if scheduling_conflict(existing, candidate)
                    ),
                    "",
                )
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
                reason = (
                    candidate.scope_reason
                    if not candidate.parallel_eligible
                    else "resource/scope safety serialization"
                )
        waves.append(
            WorkflowScheduleWave(
                index=len(waves) + 1,
                nodes=tuple(node.key for node in selected),
                serialized_reason=reason,
            )
        )
        for node in selected:
            remaining.remove(node.key)
            for dependent in dependents[node.key]:
                indegree[dependent] -= 1

    if requested and not any(len(wave.nodes) > 1 for wave in waves):
        notes.append(
            "Parallel scheduling was requested, but dependency/scope/resource safety produced only serial waves."
        )
    if any(not node.parallel_eligible for node in nodes):
        notes.append(
            "Nodes without explicit outputs remain serialized because their write set is unknown."
        )
    return tuple(waves), tuple(notes)


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

    policy = config.workspace.scheduler
    requested = policy.parallel if parallel_requested is None else _coerce_bool(parallel_requested, False)
    max_nodes = max(1, int(max_parallel_nodes or policy.max_parallel_nodes or 2))
    gradle_limit = max(0, policy.max_gradle_nodes if max_gradle_nodes is None else _safe_int(max_gradle_nodes, 0))
    dotnet_limit = max(0, policy.max_dotnet_nodes if max_dotnet_nodes is None else _safe_int(max_dotnet_nodes, 0))
    waves, notes = build_safe_schedule_waves(
        nodes,
        parallel_requested=requested,
        max_parallel_nodes=max_nodes,
        max_gradle_nodes=gradle_limit,
        max_dotnet_nodes=dotnet_limit,
    )

    parallel_effective = requested and any(len(wave.nodes) > 1 for wave in waves)
    reservation_known = all(node.parallel_eligible for node in nodes)
    reservation_paths = _dedupe(
        path for node in nodes for path in (*node.reads, *node.writes)
    ) if reservation_known else ()
    first = nodes[0]
    workspace_targets = tuple(
        dict.fromkeys(
            str(spec.metadata.get("workflow_target") or "")
            for spec in specs
            if str(spec.metadata.get("workflow_target") or "")
        )
    )
    return WorkflowExecutionPlan(
        target=workspace_targets[0] if len(workspace_targets) == 1 else "workspace",
        targets=workspace_targets,
        workflow=str(getattr(config.workspace, "action", "") or first.workflow),
        nodes=nodes,
        waves=waves,
        parallel_requested=requested,
        parallel_effective=parallel_effective,
        max_parallel_nodes=max_nodes,
        max_gradle_nodes=gradle_limit,
        max_dotnet_nodes=dotnet_limit,
        reservation_paths=reservation_paths,
        reservation_paths_known=reservation_known,
        notes=notes,
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
        # Worker-local run_specs calls must not clear the shared cancellation
        # token while sibling workflow nodes are still running.  The outer
        # scheduler owns the lifecycle of the real sink cancellation state.
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sink, name)


def _node_terminal_state(results: Sequence[OperationResult]) -> str:
    states = [str(result.metadata.get("workflow_node_state") or "") for result in results]
    for state in ("cancelled", "failed", "blocked", "skipped"):
        if state in states:
            return state
    if any(getattr(result, "skipped", False) for result in results):
        return "skipped"
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
    node_observer: Callable[[str, str, Sequence[OperationResult]], None] | None = None,
) -> tuple[list[OperationResult], str, WorkflowExecutionPlan | None]:
    """Execute workflow specs with resource-safe dependency-ready admission.

    Planning still exposes conservative static waves. When parallelism is
    requested, execution admits safe ready nodes after *each* predecessor
    completes, rather than waiting on unrelated siblings from the same wave.
    A node's command parts remain sequential and a single reservation unit.
    """
    from .runners import _publish_workflow_plan_events, run_specs

    workflow_run_id = str(RunId.current(create=False) or RunId.new(prefix="workflow"))
    _publish_workflow_plan_events(specs, workflow_run_id)
    # RM-04: freeze the native graph/source/input identities before spawn.
    # Ordinary execution is never blocked when the repository is not eligible
    # for optional recovery records (e.g. non-Git fixtures or dirty build trees).
    retry_record = None
    if not dry_run and specs :
        try:
            from .workflow_retry import capture_start, _record_path
            if not _record_path(workflow_run_id).exists():
                retry_record = capture_start(config.repo_root, workflow_run_id, specs)
        except (Exception, OSError):
            retry_record = None
    plan = build_execution_plan_from_specs(
        config,
        specs,
        parallel_requested=parallel_requested,
        max_parallel_nodes=max_parallel_nodes,
    )
    # RM-01: an execution-scoped root is minted per node, outside the repo.
    # Only nodes declaring run-temp receive an environment variable.
    owned_tmp: list[tuple[str, Path, tuple[int, int]]] = []
    if plan is not None and not dry_run:
        try:
            for node in plan.nodes:
                scopes = parse_output_scopes(list(node.output_scopes), context=f"node {node.key}")
                if not any(scope.kind == "run-temp" for scope in scopes):
                    continue
                tmp_root, identity = provision_node_temp(config.repo_root, workflow_run_id, node.key)
                owned_tmp.append((node.key, tmp_root, identity))
                for spec_index in node.spec_indexes:
                    spec = specs[spec_index]
                    if "DEVTOOL_WORKFLOW_TMPDIR" in spec.env:
                        raise WorkflowScheduleError("DEVTOOL_WORKFLOW_TMPDIR is reserved for owned output scopes")
                    spec.env = {**spec.env, "DEVTOOL_WORKFLOW_TMPDIR": str(tmp_root)}
        except BaseException:
            for _, root_path, identity in reversed(owned_tmp):
                cleanup_owned_temp(root_path, identity)
            raise

    def _cleanup_tmp(outcomes: Mapping[str, Sequence[OperationResult]] | None = None) -> None:
        for key, root_path, identity in reversed(owned_tmp):
            results = (outcomes or {}).get(key, ())
            node = plan.by_key[key] if plan is not None else None
            if node is not None and results:
                retained: list[dict[str, object]] = []
                for scope in parse_output_scopes(list(node.output_scopes), context=f"node {key}"):
                    if scope.kind != "run-temp" or scope.hash_retention == "never":
                        continue
                    if scope.hash_retention == "on-success" and _node_terminal_state(results) != "passed":
                        continue
                    proof = hash_owned_output(root_path, scope.path, identity=identity)
                    proof["policy"] = scope.hash_retention
                    retained.append(proof)
                for result in results:
                    result.metadata["workflow_temporary_output_hashes"] = retained
            cleaned = cleanup_owned_temp(root_path, identity)
            for result in results:
                result.metadata["workflow_temporary_cleanup"] = {
                    "status": "cleaned" if cleaned else "refused",
                    "root_identity": [int(identity[0]), int(identity[1])],
                    "reason": "" if cleaned else "ownership_or_concurrent_mutation",
                }

    if plan is None or (not plan.parallel_requested and node_observer is None) or (len(plan.nodes) < 2 and node_observer is None) or dry_run:
        try:
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
            event_run_id=workflow_run_id,
            publish_workflow_plan=False,
            )
            if retry_record is not None:
                try:
                    from .workflow_retry import capture_finish
                    capture_finish(config.repo_root, retry_record, results)
                except (Exception, OSError):
                    pass  # Recovery metadata cannot change execution authority.
            return results, raw, plan
        finally:
            _cleanup_tmp({str(item.metadata.get("workflow_node_key") or ""): [item] for item in results} if 'results' in locals() else {})

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

    def execute_node(
        key: str,
        initial_statuses: dict[str, str],
        initial_fail_fast: bool = False,
        initial_cancelled: bool = False,
    ) -> tuple[str, list[OperationResult], str]:
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

        try:
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
                workflow_initial_statuses=initial_statuses,
                workflow_initial_fail_fast=initial_fail_fast,
                workflow_initial_cancelled=initial_cancelled,
                event_run_id=workflow_run_id,
                publish_workflow_plan=False,
            )
            return key, local_results, local_raw
        except BaseException as exc:
            metadata = dict(local_specs[0].metadata) if local_specs else {}
            metadata["workflow_node_state"] = "failed"
            metadata["workflow_worker_exception"] = type(exc).__name__
            message = f"Workflow worker failed: {type(exc).__name__}: {exc}"
            # The full workflow plan was published before worker execution. If
            # run_specs aborts before its TaskFinished event, explicitly close
            # this scheduled node so CLI finalization never inherits a dangling
            # RUNNING/PLANNED descendant.
            from .core.events import NodeFinished, get_execution_store, publish_event
            snapshot = get_execution_store().snapshot(workflow_run_id)
            node = snapshot.node(key) if snapshot is not None else None
            if node is not None and node.status not in {"succeeded", "failed", "blocked", "skipped", "cancelled"}:
                publish_event(NodeFinished(
                    run_id=workflow_run_id,
                    node_id=key,
                    parent_id=node.parent_id,
                    status="failed",
                    exit_code=1,
                    failure_kind="workflow_worker_exception",
                    source="workflow-scheduler",
                    transition_origin="scheduler_inferred",
                    transition_reason=message,
                    metadata={"workflow_worker_exception": type(exc).__name__},
                ))
            result = OperationResult(
                name=f"workflow:{key}",
                argv=[],
                returncode=1,
                duration_seconds=0.0,
                output=message,
                issues=[],
                failure_kind="workflow_worker_exception",
                metadata=metadata,
            )
            return key, [result], message

    # One executor for the entire DAG, not one executor per static wave.
    # Positions follow the graph's stable topological expansion. A later node
    # cannot overtake an eligible earlier node merely because another worker
    # finished first; incompatible reservations always retain the earlier
    # node's priority.
    node_order = tuple(node.key for node in plan.nodes)
    pending = set(node_order)
    active: dict[Any, str] = {}
    active_keys: set[str] = set()
    actual_starts: list[str] = []
    actual_finishes: list[str] = []
    wait_reasons: dict[str, str] = {}
    monotonic_starts: dict[str, float] = {}
    completed_statuses: dict[str, str] = {}

    def finish(key: str, local_results: list[OperationResult], local_raw: str) -> None:
        nonlocal fail_fast, cancelled
        if node_observer is not None:
            node_observer(key, 'terminal', local_results)
        results_by_node[key] = local_results
        raw_by_node[key] = local_raw
        state = _node_terminal_state(local_results)
        statuses[key] = state
        completed_statuses[key] = state
        actual_finishes.append(key)
        if key in monotonic_starts:
            elapsed = max(0., time.monotonic() - monotonic_starts[key])
            for result in local_results:
                result.metadata["workflow_scheduler_node_elapsed_seconds"] = elapsed
        for result in local_results:
            result.metadata["workflow_scheduler_terminal_order"] = len(actual_finishes)
        if state == "cancelled":
            cancelled = True
        if state == "failed" and not continue_on_error:
            fail_fast = True

    def external_cancelled() -> bool:
        if tasker_progress is None:
            return False
        try:
            return bool(tasker_progress.cancel_requested())
        except Exception:
            return False

    def harvest_completions(done: Iterable[Any]) -> None:
        """Record already-terminal workers *before* the next admission pass.

        A wait(FIRST_COMPLETED) snapshot can report B first while A fails
        before the scheduler loops again. Prefer processing every completion
        already visible to the scheduler over launching newly ready successors.
        """
        for future in sorted(done, key=lambda item: node_order.index(active[item])):
            key = active.pop(future)
            active_keys.remove(key)
            try:
                _, local_results, local_raw = future.result()
            except BaseException as exc:
                # execute_node already handles worker exceptions;
                # this path guards Future/Executor infrastructure.
                metadata = {**node_specs[key][0].metadata,
                            "workflow_node_state": "failed",
                            "workflow_worker_exception": type(exc).__name__}
                local_raw = f"Workflow future failed: {type(exc).__name__}: {exc}"
                local_results = [OperationResult(
                    name=f"workflow:{key}", argv=[], returncode=1,
                    duration_seconds=0.0, output=local_raw, issues=[],
                    failure_kind="workflow_worker_exception", metadata=metadata)]
            finish(key, local_results, local_raw)

    try:
        with ThreadPoolExecutor(max_workers=plan.max_parallel_nodes, thread_name_prefix="devtool-workflow") as executor:
            while pending or active:
                # Harvest everything that is already done before evaluating
                # newly ready nodes. This is especially important for fail-fast
                # when one completion was omitted from a prior wait snapshot.
                already_done = tuple(future for future in active if future.done())
                if already_done:
                    harvest_completions(already_done)
                    continue
                if external_cancelled():
                    cancelled = True
                admitted = False
                # Blocked/skipped nodes still go through the existing runner's
                # synthetic result/event path. No subprocess is launched.
                for key in node_order:
                    if key not in pending:
                        continue
                    # A worker might have become terminal during this scan.
                    # Drain it first rather than admitting a successor using
                    # an already-stale dependency/fail-fast snapshot.
                    if any(future.done() for future in active):
                        break
                    node = node_by_key[key]
                    if any(dep not in completed_statuses for dep in node.depends_on):
                        continue
                    blocked_deps = [dep for dep in node.depends_on if completed_statuses[dep] != "passed"]
                    if blocked_deps or cancelled or fail_fast:
                        initial = dict(completed_statuses)
                        pending.remove(key)
                        # The native runner prioritizes dependency blocking over
                        # fail-fast and cancellation (cancellation takes precedence).
                        _, local_results, local_raw = execute_node(key, initial, fail_fast, cancelled)
                        finish(key, local_results, local_raw)
                        admitted = True
                        continue
                    if len(active) >= plan.max_parallel_nodes:
                        wait_reasons[key] = "all worker slots occupied"
                        continue
                    running = [node_by_key[entry] for entry in node_order if entry in active_keys]
                    if not _resource_allowed(running, node,
                                             max_gradle=plan.max_gradle_nodes,
                                             max_dotnet=plan.max_dotnet_nodes):
                        wait_reasons[key] = "resource class capacity/exclusivity"
                        continue
                    conflict = next((reason for other in running
                                     if (reason := scheduling_conflict(other, node))), "")
                    if conflict:
                        wait_reasons[key] = conflict
                        continue
                    # Unknown write sets are serialized by scheduling_conflict,
                    # including an unknown writer launched first.
                    pending.remove(key)
                    initial = dict(completed_statuses)
                    monotonic_starts[key] = time.monotonic()
                    admitted_index = len(actual_starts) + 1
                    actual_starts.append(key)
                    for spec in node_specs[key]:
                        spec.metadata["workflow_scheduler_admission_order"] = admitted_index
                        spec.metadata["workflow_scheduler_admission_mode"] = "dependency-ready"
                    if node_observer is not None:
                        node_observer(key, 'started', ())
                    future = executor.submit(execute_node, key, initial, False, False)
                    active[future] = key
                    active_keys.add(key)
                    admitted = True
                if active:
                    # Revisit the queue after *each* completion (or short cancel
                    # poll); never wait for a whole precomputed wave.
                    done, _ = wait(tuple(active), timeout=0.1 if tasker_progress is not None else None,
                                   return_when=FIRST_COMPLETED)
                    harvest_completions(done)
                elif pending and not admitted:
                    # A valid topo graph always has a root or a completed
                    # dependency; this is an explicit invariant, not a spin.
                    raise WorkflowScheduleError("Ready queue stalled with pending workflow nodes")
    finally:
        _cleanup_tmp(results_by_node)

    plan = replace(
        plan,
        actual_start_order=tuple(actual_starts),
        actual_finish_order=tuple(actual_finishes),
        node_outcomes=tuple((key, statuses[key]) for key in node_order if key in statuses),
        resource_wait_reasons=tuple((key, wait_reasons[key]) for key in node_order if key in wait_reasons),
    )

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
    if retry_record is not None:
        try:
            from .workflow_retry import capture_finish
            capture_finish(config.repo_root, retry_record, ordered_results)
        except (Exception, OSError):
            pass  # Recovery metadata cannot change execution authority.
    return ordered_results, raw, plan
