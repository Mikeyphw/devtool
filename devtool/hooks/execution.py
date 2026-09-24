from __future__ import annotations

"""EXO adapter for artifact-controlled lifecycle scripts.

Artifact hooks historically fed Rich/Tasker compatibility progress directly.
This adapter makes the hook lifecycle authoritative EXO work as well: every
script is a canonical execution node and its spawned runner is a canonical
process. Rendering remains a projection of the execution stream.
"""

from dataclasses import dataclass, field
from typing import Any, Iterable

from ..core.events import NodeDiscovered, NodeFinished, NodeProgressChanged, NodeStarted, get_execution_store, publish_event
from ..core.execution_contract import ExecutionStatus, NodeId
from ..progress_events import RunnerExecutionAdapter


@dataclass(slots=True)
class HookExecutionAdapter:
    run_id: str
    operation_name: str
    kind: str
    phase: str
    label: str
    argv: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = "artifact-hook"
    node_id: str = ""
    _runner: RunnerExecutionAdapter | None = None
    _started: bool = False
    _finished: bool = False

    def __post_init__(self) -> None:
        self.run_id = str(self.run_id or "").strip()
        if not self.run_id:
            return
        # Lifecycle nodes are top-level children of the current run.  The
        # deterministic id keeps validation/finalize phases stable across UI
        # projections without pretending they belong to a workflow DAG node.
        snapshot = get_execution_store().snapshot(self.run_id)
        prior_attempts = 0
        if snapshot is not None:
            prior_attempts = sum(
                1
                for node in snapshot.nodes
                if node.kind == "artifact-hook"
                and node.metadata.get("hook_kind") == self.kind
                and node.metadata.get("hook_phase") == self.phase
                and node.metadata.get("operation_name") == self.operation_name
            )
        attempt = prior_attempts + 1
        self.metadata.setdefault("hook_attempt", attempt)
        self.node_id = str(NodeId.dynamic(
            parent_id="execution-lifecycle",
            kind="artifact-hook",
            key=f"{self.kind}:{self.phase}:{self.operation_name}",
            instance=attempt,
        ))
        self._runner = RunnerExecutionAdapter(
            run_id=self.run_id,
            parent_node_id=self.node_id,
            tool="artifact-hook",
            source=self.source,
            provenance={
                "hook_kind": self.kind,
                "hook_phase": self.phase,
                "operation_name": self.operation_name,
            },
        )

    @property
    def enabled(self) -> bool:
        return bool(self.run_id and self.node_id and self._runner is not None)

    def start(self) -> None:
        if not self.enabled or self._started:
            return
        metadata = {
            "hook_kind": self.kind,
            "hook_phase": self.phase,
            "operation_name": self.operation_name,
            "lifecycle_authority": "exo",
            **dict(self.metadata or {}),
        }
        publish_event(NodeDiscovered(
            run_id=self.run_id,
            node_id=self.node_id,
            label=self.label,
            kind="artifact-hook",
            source=self.source,
            metadata=metadata,
        ))
        publish_event(NodeStarted(
            run_id=self.run_id,
            node_id=self.node_id,
            label=self.label,
            kind="artifact-hook",
            argv=self.argv,
            source=self.source,
            metadata=metadata,
        ))
        self._started = True

    def process_started(self, *, pid: int, argv: Iterable[object], cwd: str) -> None:
        if not self.enabled:
            return
        self.start()
        assert self._runner is not None
        self._runner.process_started(pid=pid, argv=argv, cwd=cwd)

    def process_exited(self, *, exit_code: int, duration_seconds: float, failure_kind: str = "", log_path: str = "") -> None:
        if not self.enabled or self._runner is None:
            return
        self._runner.process_exited(
            exit_code=exit_code,
            duration_seconds=duration_seconds,
            failure_kind=failure_kind,
            log_path=log_path,
        )

    def progress(self, *, detail: str, indeterminate: bool = True) -> None:
        if not self.enabled:
            return
        self.start()
        publish_event(NodeProgressChanged(
            run_id=self.run_id,
            node_id=self.node_id,
            indeterminate=bool(indeterminate),
            detail=str(detail or ""),
            source=self.source,
            metadata={"hook_kind": self.kind, "hook_phase": self.phase},
        ))

    def diagnostic(self, *, severity: str, code: str, message: str, failure_kind: str = "", log_path: str = "") -> str:
        if not self.enabled or self._runner is None:
            return ""
        self.start()
        return self._runner.diagnostic(
            severity=severity,
            code=code,
            message=message,
            node_id=self.node_id,
            failure_kind=failure_kind,
            log_path=log_path,
        )

    def finish(self, *, exit_code: int, duration_seconds: float = 0.0, skipped: bool = False, failure_kind: str = "", evidence: Iterable[object] = ()) -> None:
        if not self.enabled or self._finished:
            return
        self.start()
        if skipped:
            status = ExecutionStatus.SKIPPED.value
        elif int(exit_code or 0) == 0:
            status = ExecutionStatus.SUCCEEDED.value
        else:
            status = ExecutionStatus.FAILED.value
        diagnostic_ids = tuple(self._runner.diagnostic_ids) if self._runner is not None else ()
        process_id = self._runner.active_process_id if self._runner is not None else ""
        publish_event(NodeFinished(
            run_id=self.run_id,
            node_id=self.node_id,
            status=status,
            exit_code=int(exit_code or 0),
            duration_seconds=float(duration_seconds or 0.0),
            failure_kind=str(failure_kind or ""),
            process_id=str(process_id or ""),
            diagnostic_ids=diagnostic_ids,
            evidence=tuple(str(value) for value in evidence if str(value)),
            source=self.source,
            metadata={
                "hook_kind": self.kind,
                "hook_phase": self.phase,
                "operation_name": self.operation_name,
                "lifecycle_authority": "exo",
                **dict(self.metadata or {}),
            },
        ))
        self._finished = True
