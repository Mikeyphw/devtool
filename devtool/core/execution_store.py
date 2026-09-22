from __future__ import annotations

"""Authoritative reducer/store for typed Devtool execution events."""

from collections.abc import Iterable
from copy import deepcopy
from threading import RLock
from typing import TYPE_CHECKING, Any

from .execution_contract import ExecutionStatus, NodeId, RunId, canonical_execution_status
from .execution_state import (
    ExecutionArtifactState,
    ExecutionDiagnosticState,
    ExecutionNodeState,
    ExecutionProcessState,
    ExecutionRunState,
    ExecutionSnapshot,
)
if TYPE_CHECKING:
    from ..workflow_graph import ExecutionGraph
    from .events.events import DevtoolEvent


class ExecutionStoreError(RuntimeError):
    pass


class ExecutionSequenceError(ExecutionStoreError):
    pass


class ExecutionTransitionError(ExecutionStoreError):
    pass


class ExecutionReducer:
    """Apply a typed event to one mutable run state.

    The reducer deliberately does not synthesize success for unobserved work.
    A terminal run and its child-node accounting are separate facts; later UI
    surfaces can therefore expose unresolved/planned nodes rather than turning
    them into false positives.
    """

    def apply(self, run: ExecutionRunState, event: "DevtoolEvent") -> None:
        from .events.events import (
            ArtifactProduced,
            DiagnosticEmitted,
            MetricChanged,
            NodeBlocked,
            NodeDiscovered,
            NodeFinished,
            NodeProgressChanged,
            NodeStarted,
            ProcessExited,
            ProcessOutputReceived,
            ProcessStarted,
            RunFinished,
            RunStarted,
        )
        if event.event_id in run.applied_event_ids:
            return
        if event.sequence < 0:
            raise ExecutionSequenceError("execution events must have a completed non-negative sequence")
        if run.last_sequence >= 0 and event.sequence <= run.last_sequence:
            raise ExecutionSequenceError(
                f"execution event sequence regression for run {run.run_id!r}: "
                f"{event.sequence} <= {run.last_sequence}"
            )

        if isinstance(event, RunStarted):
            self._run_started(run, event)
        elif isinstance(event, RunFinished):
            self._run_finished(run, event)
        elif isinstance(event, NodeDiscovered):
            self._node_discovered(run, event)
        elif isinstance(event, NodeStarted):
            self._node_started(run, event)
        elif isinstance(event, NodeProgressChanged):
            self._node_progress(run, event)
        elif isinstance(event, NodeBlocked):
            self._node_blocked(run, event)
        elif isinstance(event, NodeFinished):
            self._node_finished(run, event)
        elif isinstance(event, ProcessStarted):
            self._process_started(run, event)
        elif isinstance(event, ProcessOutputReceived):
            self._process_output(run, event)
        elif isinstance(event, ProcessExited):
            self._process_exited(run, event)
        elif isinstance(event, DiagnosticEmitted):
            self._diagnostic(run, event)
        elif isinstance(event, ArtifactProduced):
            self._artifact(run, event)
        elif isinstance(event, MetricChanged):
            self._metric(run, event)

        run.last_sequence = int(event.sequence)
        run.last_event_id = str(event.event_id)
        run.applied_event_ids.add(str(event.event_id))

    def plan_graph(self, run: ExecutionRunState, graph: "ExecutionGraph") -> None:
        """Seed runtime nodes from the existing workflow DAG without cloning it.

        Node ids are the workflow graph keys verbatim. Dependency edges are
        copied only as mutable runtime state so status/progress can evolve.
        """
        source = {
            "target": str(graph.target),
            "workflow": str(graph.workflow),
            "targets": [str(value) for value in graph.targets],
        }
        if source not in run.graph_sources:
            run.graph_sources.append(source)
        for workflow_node in graph.topological_order():
            node_id = str(NodeId.workflow(workflow_node.key))
            node = run.nodes.get(node_id)
            if node is None:
                node = ExecutionNodeState(node_id=node_id)
                run.nodes[node_id] = node
            node.label = node.label or str(workflow_node.label)
            node.kind = str(workflow_node.ref.kind or "operation")
            node.depends_on = tuple(str(value) for value in workflow_node.depends_on)
            node.metadata.update(
                {
                    "target": str(workflow_node.target),
                    "workflow": str(workflow_node.workflow),
                    "index": int(workflow_node.index),
                    "runner": str(workflow_node.runner),
                    "action": str(workflow_node.action),
                }
            )

    def _run_started(self, run: ExecutionRunState, event: "RunStarted") -> None:
        if run.status.terminal:
            raise ExecutionTransitionError(
                f"cannot start terminal run {run.run_id!r} with status {run.status.value!r}"
            )
        run.status = ExecutionStatus.RUNNING
        run.command = str(event.command or run.command)
        run.project = str(event.project or run.project)
        run.started_at = run.started_at or event.timestamp

    def _run_finished(self, run: ExecutionRunState, event: "RunFinished") -> None:
        status = canonical_execution_status(event.status)
        if not status.terminal:
            raise ExecutionTransitionError(f"run.finished requires terminal status, got {status.value!r}")
        if run.status.terminal and run.status is not status:
            raise ExecutionTransitionError(
                f"run {run.run_id!r} already ended as {run.status.value!r}, cannot become {status.value!r}"
            )
        run.status = status
        run.exit_code = int(event.exit_code)
        run.duration_seconds = float(event.duration_seconds or 0.0)
        run.finished_at = event.timestamp

    def _node_discovered(self, run: ExecutionRunState, event: "NodeDiscovered") -> None:
        node = self._ensure_event_node(run, event, label=event.label, kind=event.kind)
        if event.parent_id:
            node.parent_id = str(event.parent_id)
        if event.label:
            node.label = str(event.label)
        if event.kind:
            node.kind = str(event.kind)
        if event.depends_on:
            node.depends_on = tuple(str(value) for value in event.depends_on)
        node.discovered_at = node.discovered_at or event.timestamp
        node.metadata.update(dict(event.metadata or {}))

    def _node_started(self, run: ExecutionRunState, event: "NodeStarted") -> None:
        node = self._ensure_event_node(run, event, label=event.label, kind=event.kind)
        self._require_nonterminal(node, "start")
        node.status = ExecutionStatus.RUNNING
        if event.parent_id:
            node.parent_id = str(event.parent_id)
        if event.label:
            node.label = str(event.label)
        if event.kind:
            node.kind = str(event.kind)
        if event.argv:
            node.argv = tuple(str(value) for value in event.argv)
        node.discovered_at = node.discovered_at or event.timestamp
        node.started_at = node.started_at or event.timestamp
        node.metadata.update(dict(event.metadata or {}))

    def _node_progress(self, run: ExecutionRunState, event: "NodeProgressChanged") -> None:
        node = self._ensure_event_node(run, event, kind="operation", allow_active_match=True)
        if not node.status.terminal:
            node.status = ExecutionStatus.RUNNING
            node.started_at = node.started_at or event.timestamp
        progress = node.progress
        progress.current = event.current
        progress.total = event.total
        progress.percent = event.percent
        progress.unit = str(event.unit or "")
        progress.indeterminate = bool(event.indeterminate)
        progress.detail = str(event.detail or "")

    def _node_blocked(self, run: ExecutionRunState, event: "NodeBlocked") -> None:
        node = self._ensure_event_node(run, event, allow_active_match=True)
        self._set_terminal_node_status(node, ExecutionStatus.BLOCKED)
        node.blocked_by = tuple(str(value) for value in event.blocked_by)
        node.blocked_reason = str(event.reason or "")
        node.finished_at = event.timestamp

    def _node_finished(self, run: ExecutionRunState, event: "NodeFinished") -> None:
        node = self._ensure_event_node(run, event, label=self._event_label(event), allow_active_match=True)
        status = canonical_execution_status(event.status)
        # Compatibility TaskFinished events can represent one command/spec of a
        # multi-spec workflow node. In that case the aggregate node correctly
        # remains RUNNING until the last part ends. EXO-05 will model the parts
        # as child operations, after which canonical NodeFinished can be
        # terminal-only again.
        if not status.terminal:
            self._require_nonterminal(node, "update")
            node.status = status
            node.exit_code = int(event.exit_code)
            node.duration_seconds += float(event.duration_seconds or 0.0)
            node.failure_kind = str(event.failure_kind or node.failure_kind or "")
            node.metadata.update(dict(event.metadata or {}))
            return
        self._set_terminal_node_status(node, status)
        node.exit_code = int(event.exit_code)
        node.duration_seconds += float(event.duration_seconds or 0.0)
        node.failure_kind = str(event.failure_kind or "")
        node.finished_at = event.timestamp
        node.metadata.update(dict(event.metadata or {}))

    def _process_started(self, run: ExecutionRunState, event: "ProcessStarted") -> None:
        process_id = str(event.process_id or "").strip()
        if not process_id:
            if event.pid is None:
                process_id = f"{run.run_id}:process:event:{event.sequence}"
            else:
                process_id = f"{run.run_id}:pid:{int(event.pid)}"
            event.process_id = process_id
        node_id = self._resolve_existing_node_id(run, event, allow_active_match=True)
        process = run.processes.get(process_id)
        if process is None:
            process = ExecutionProcessState(process_id=process_id)
            run.processes[process_id] = process
        process.node_id = node_id
        process.pid = event.pid
        process.argv = tuple(str(value) for value in event.argv)
        process.cwd = str(event.cwd or "")
        process.status = "running"
        process.started_at = process.started_at or event.timestamp
        if node_id:
            node = run.nodes[node_id]
            if process_id not in node.process_ids:
                node.process_ids.append(process_id)

    def _process_output(self, run: ExecutionRunState, event: "ProcessOutputReceived") -> None:
        process_id = str(event.process_id or "").strip()
        if not process_id:
            return
        process = run.processes.get(process_id)
        if process is None:
            process = ExecutionProcessState(process_id=process_id, node_id=str(event.node_id or ""))
            run.processes[process_id] = process
        stream = str(event.stream or "stdout").strip().lower()
        if stream == "stderr":
            process.stderr_events += 1
        else:
            process.stdout_events += 1
        process.last_output = str(event.text or "")[-4096:]

    def _process_exited(self, run: ExecutionRunState, event: "ProcessExited") -> None:
        process_id = str(event.process_id or "").strip()
        if not process_id:
            return
        process = run.processes.get(process_id)
        if process is None:
            process = ExecutionProcessState(process_id=process_id, node_id=str(event.node_id or ""))
            run.processes[process_id] = process
        process.exit_code = int(event.exit_code)
        process.duration_seconds = float(event.duration_seconds or 0.0)
        process.finished_at = event.timestamp
        process.status = "succeeded" if process.exit_code == 0 else ("cancelled" if process.exit_code == 130 else "failed")

    def _diagnostic(self, run: ExecutionRunState, event: "DiagnosticEmitted") -> None:
        node_id = self._resolve_existing_node_id(run, event, allow_active_match=True)
        diagnostic_id = f"{event.event_id}:diagnostic"
        diagnostic = ExecutionDiagnosticState(
            diagnostic_id=diagnostic_id,
            node_id=node_id,
            severity=str(event.severity or "warning"),
            code=str(event.code or ""),
            message=str(event.message or ""),
            file=str(event.file or ""),
            line=event.line,
            column=event.column,
            evidence=tuple(str(value) for value in event.evidence),
            timestamp=event.timestamp,
            source=str(event.source or ""),
        )
        run.diagnostics[diagnostic_id] = diagnostic
        if node_id:
            node = run.nodes[node_id]
            if diagnostic_id not in node.diagnostic_ids:
                node.diagnostic_ids.append(diagnostic_id)

    def _artifact(self, run: ExecutionRunState, event: "ArtifactProduced") -> None:
        node_id = self._resolve_existing_node_id(
            run,
            event,
            label=str(event.producer_task or ""),
            allow_active_match=True,
        )
        artifact_id = str(event.artifact_id or "").strip() or f"{event.event_id}:artifact"
        artifact = ExecutionArtifactState(
            artifact_id=artifact_id,
            node_id=node_id,
            path=str(event.path or ""),
            sha256=str(event.sha256 or ""),
            size=event.size,
            artifact_type=str(event.artifact_type or ""),
            producer_task=str(event.producer_task or ""),
            timestamp=event.timestamp,
            source=str(event.source or ""),
        )
        run.artifacts[artifact_id] = artifact
        if node_id:
            node = run.nodes[node_id]
            if artifact_id not in node.artifact_ids:
                node.artifact_ids.append(artifact_id)

    def _metric(self, run: ExecutionRunState, event: "MetricChanged") -> None:
        node_id = self._resolve_existing_node_id(run, event, allow_active_match=True)
        if node_id:
            run.nodes[node_id].metrics[str(event.name)] = event.value
        else:
            run.metrics[str(event.name)] = event.value

    def _event_label(self, event: "DevtoolEvent") -> str:
        for attr in ("label", "task", "producer_task"):
            value = str(getattr(event, attr, "") or "").strip()
            if value:
                return value
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        for key in ("task", "name", "phase", "command"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value
        return ""

    def _ensure_event_node(
        self,
        run: ExecutionRunState,
        event: "DevtoolEvent",
        *,
        label: str = "",
        kind: str = "operation",
        allow_active_match: bool = False,
    ) -> ExecutionNodeState:
        node_id = self._resolve_existing_node_id(
            run,
            event,
            label=label,
            allow_active_match=allow_active_match,
        )
        if not node_id:
            parent = str(event.parent_id or run.run_id)
            local_label = str(label or self._event_label(event) or event.protocol_type)
            node_id = str(
                NodeId.dynamic(
                    parent_id=parent,
                    kind=kind or "operation",
                    key=local_label,
                    instance=str(event.sequence),
                )
            )
            event.node_id = node_id
        node = run.nodes.get(node_id)
        if node is None:
            node = ExecutionNodeState(
                node_id=node_id,
                parent_id=str(event.parent_id or ""),
                label=str(label or self._event_label(event) or ""),
                kind=str(kind or "operation"),
                discovered_at=event.timestamp,
            )
            run.nodes[node_id] = node
        return node

    def _resolve_existing_node_id(
        self,
        run: ExecutionRunState,
        event: "DevtoolEvent",
        *,
        label: str = "",
        allow_active_match: bool = False,
    ) -> str:
        explicit = str(event.node_id or "").strip()
        if explicit:
            event.node_id = str(NodeId(explicit))
            return event.node_id
        if not allow_active_match:
            return ""
        desired_label = str(label or self._event_label(event) or "").strip()
        candidates = [node for node in run.nodes.values() if node.status is ExecutionStatus.RUNNING]
        if desired_label:
            labelled = [node for node in candidates if node.label == desired_label]
            if len(labelled) == 1:
                event.node_id = labelled[0].node_id
                return event.node_id
        if len(candidates) == 1:
            event.node_id = candidates[0].node_id
            return event.node_id
        return ""

    @staticmethod
    def _require_nonterminal(node: ExecutionNodeState, action: str) -> None:
        if node.status.terminal:
            raise ExecutionTransitionError(
                f"cannot {action} terminal node {node.node_id!r} with status {node.status.value!r}"
            )

    @staticmethod
    def _set_terminal_node_status(node: ExecutionNodeState, status: ExecutionStatus) -> None:
        if node.status.terminal and node.status is not status:
            raise ExecutionTransitionError(
                f"node {node.node_id!r} already ended as {node.status.value!r}, cannot become {status.value!r}"
            )
        node.status = status


class ExecutionStore:
    """Thread-safe multi-run execution state store."""

    def __init__(self, *, reducer: ExecutionReducer | None = None) -> None:
        self.reducer = reducer or ExecutionReducer()
        self._runs: dict[str, ExecutionRunState] = {}
        self._last_run_id: str = ""
        self._lock = RLock()

    def apply(self, event: "DevtoolEvent") -> ExecutionSnapshot:
        run_id = str(event.run_id or "").strip()
        if not run_id:
            raise ExecutionStoreError("execution event must have run_id before store reduction")
        if int(event.sequence) < 0:
            raise ExecutionSequenceError("execution event must have sequence before store reduction")
        if not str(event.event_id or "").strip():
            raise ExecutionStoreError("execution event must have event_id before store reduction")
        with self._lock:
            current = self._runs.get(run_id)
            if current is None:
                current = ExecutionRunState(run_id=str(RunId(run_id)))
            # Reduction is transactional: an invalid transition/event cannot
            # leave partially-created nodes, diagnostics, or process state in
            # the authoritative store.
            candidate = deepcopy(current)
            self.reducer.apply(candidate, event)
            self._runs[run_id] = candidate
            self._last_run_id = run_id
            return ExecutionSnapshot.from_state(candidate)

    def apply_many(self, events: Iterable["DevtoolEvent"]) -> ExecutionSnapshot | None:
        snapshot: ExecutionSnapshot | None = None
        for event in events:
            snapshot = self.apply(event)
        return snapshot

    def plan_graph(self, graph: "ExecutionGraph", *, run_id: str | None = None) -> ExecutionSnapshot:
        resolved_run_id = str(run_id or self._last_run_id or RunId.current(create=True))
        with self._lock:
            run = self._runs.get(resolved_run_id)
            if run is None:
                run = ExecutionRunState(run_id=str(RunId(resolved_run_id)))
                self._runs[resolved_run_id] = run
            self.reducer.plan_graph(run, graph)
            self._last_run_id = resolved_run_id
            return ExecutionSnapshot.from_state(run)

    def snapshot(self, run_id: str | None = None) -> ExecutionSnapshot | None:
        with self._lock:
            resolved = str(run_id or self._last_run_id or "")
            if not resolved:
                return None
            run = self._runs.get(resolved)
            return ExecutionSnapshot.from_state(run) if run is not None else None

    def snapshots(self) -> tuple[ExecutionSnapshot, ...]:
        with self._lock:
            return tuple(ExecutionSnapshot.from_state(run) for run in self._runs.values())

    def clear(self, run_id: str | None = None) -> None:
        with self._lock:
            if run_id is None:
                self._runs.clear()
                self._last_run_id = ""
                return
            resolved = str(run_id)
            self._runs.pop(resolved, None)
            if self._last_run_id == resolved:
                self._last_run_id = next(reversed(self._runs), "") if self._runs else ""


__all__ = [
    "ExecutionStoreError",
    "ExecutionSequenceError",
    "ExecutionTransitionError",
    "ExecutionReducer",
    "ExecutionStore",
]
