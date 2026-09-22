from __future__ import annotations

"""Translate runner progress producers into canonical execution events.

EXO-05 keeps the existing Rich/Tasker snapshot callbacks as compatibility
projections, but makes parsed sub-operations first-class nodes in the EXO
execution graph.  Adapters deliberately own no rendering.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .core.events import (
    NodeDiscovered,
    NodeFinished,
    NodeProgressChanged,
    NodeStarted,
    publish_event,
)
from .core.execution_contract import ExecutionStatus, NodeId


def _status_for_gradle_outcome(value: object) -> str:
    outcome = str(value or "success").strip().lower().replace("_", "-")
    if outcome == "failed":
        return ExecutionStatus.FAILED.value
    if outcome == "skipped":
        return ExecutionStatus.SKIPPED.value
    # up-to-date/no-source/from-cache are successful Gradle outcomes, but the
    # outcome itself is preserved in metadata rather than flattened away.
    return ExecutionStatus.SUCCEEDED.value


@dataclass(slots=True)
class RunnerExecutionAdapter:
    run_id: str
    parent_node_id: str
    tool: str = "generic"
    source: str = "runner-progress"
    _children: dict[tuple[str, str], str] = field(default_factory=dict)
    _states: dict[str, str] = field(default_factory=dict)
    _npm_plan_ids: list[tuple[str, str]] = field(default_factory=list)
    _npm_started: int = 0
    _npm_active: str = ""
    _dotnet_active: set[str] = field(default_factory=set)
    _gradle_test_ids: dict[str, str] = field(default_factory=dict)

    def _child_id(self, *, kind: str, key: str, instance: object = "") -> str:
        identity_key = (kind, f"{key}\x1f{instance}")
        existing = self._children.get(identity_key)
        if existing:
            return existing
        child = str(NodeId.dynamic(parent_id=self.parent_node_id, kind=kind, key=key, instance=instance))
        self._children[identity_key] = child
        return child

    def _discover(
        self,
        *,
        kind: str,
        key: str,
        label: str | None = None,
        instance: object = "",
        parent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        child = self._child_id(kind=kind, key=key, instance=instance)
        if child not in self._states:
            publish_event(NodeDiscovered(
                run_id=self.run_id,
                node_id=child,
                parent_id=str(parent_id or self.parent_node_id),
                label=str(label or key),
                kind=kind,
                source=self.source,
                metadata={"tool": self.tool, **dict(metadata or {})},
            ))
            self._states[child] = ExecutionStatus.PLANNED.value
        return child

    def _start(self, child: str, *, label: str, kind: str, metadata: dict[str, Any] | None = None) -> None:
        if self._states.get(child) in {
            ExecutionStatus.RUNNING.value,
            ExecutionStatus.SUCCEEDED.value,
            ExecutionStatus.FAILED.value,
            ExecutionStatus.SKIPPED.value,
            ExecutionStatus.CANCELLED.value,
            ExecutionStatus.BLOCKED.value,
        }:
            return
        publish_event(NodeStarted(
            run_id=self.run_id,
            node_id=child,
            parent_id=self.parent_node_id,
            label=label,
            kind=kind,
            source=self.source,
            metadata={"tool": self.tool, **dict(metadata or {})},
        ))
        self._states[child] = ExecutionStatus.RUNNING.value

    def _finish(
        self,
        child: str,
        *,
        status: str,
        exit_code: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        current = self._states.get(child, "")
        if current in {
            ExecutionStatus.SUCCEEDED.value,
            ExecutionStatus.FAILED.value,
            ExecutionStatus.SKIPPED.value,
            ExecutionStatus.CANCELLED.value,
            ExecutionStatus.BLOCKED.value,
        }:
            return
        publish_event(NodeFinished(
            run_id=self.run_id,
            node_id=child,
            parent_id=self.parent_node_id,
            status=status,
            exit_code=int(exit_code),
            source=self.source,
            metadata={"tool": self.tool, **dict(metadata or {})},
        ))
        self._states[child] = status

    def progress_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Publish measured parent progress while retaining legacy callbacks."""
        if self.parent_node_id:
            total = snapshot.get("total")
            percent = snapshot.get("percent")
            publish_event(NodeProgressChanged(
                run_id=self.run_id,
                node_id=self.parent_node_id,
                current=snapshot.get("completed"),
                total=total,
                percent=percent,
                unit=str(snapshot.get("unit_label") or ""),
                indeterminate=total in (None, 0),
                detail=str(snapshot.get("detail") or ""),
                source=self.source,
                metadata={
                    "tool": self.tool,
                    "progress_finalization": snapshot.get("progress_finalization"),
                    "measured_completed": snapshot.get("measured_completed"),
                    "measured_percent": snapshot.get("measured_percent"),
                },
            ))
        if snapshot.get("final"):
            try:
                returncode = int(snapshot.get("returncode") or 0)
            except (TypeError, ValueError):
                returncode = 1
            self.finish(returncode)

    def gradle_event(self, event: dict[str, Any]) -> None:
        kind = str(event.get("kind") or "")
        if kind == "task-graph":
            tasks = event.get("tasks") if isinstance(event.get("tasks"), list) else []
            for task in tasks:
                task_name = str(task or "").strip()
                if task_name:
                    self._discover(kind="gradle-task", key=task_name, label=task_name)
            return
        if kind in {"task-start", "task-finish"}:
            task = str(event.get("task") or "").strip()
            if not task:
                return
            child = self._discover(kind="gradle-task", key=task, label=task)
            self._start(child, label=task, kind="gradle-task")
            if kind == "task-finish":
                outcome = str(event.get("outcome") or "success").strip().lower().replace("_", "-")
                status = _status_for_gradle_outcome(outcome)
                publish_event(NodeProgressChanged(
                    run_id=self.run_id,
                    node_id=child,
                    parent_id=self.parent_node_id,
                    current=1,
                    total=1,
                    percent=100.0,
                    unit="task",
                    detail=f"{task}: {outcome}",
                    source=self.source,
                ))
                self._finish(
                    child,
                    status=status,
                    exit_code=1 if status == ExecutionStatus.FAILED.value else 0,
                    metadata={
                        "outcome": outcome,
                        "did_work": event.get("didWork"),
                        "failure": event.get("failure"),
                        "artifact_outputs": list(event.get("artifactOutputs") or []),
                    },
                )
            return
        if kind in {"test-suite-start", "test-suite-finish"}:
            suite = str(event.get("suite") or "Gradle test suite").strip()
            child = self._discover(kind="gradle-test-suite", key=suite, label=suite)
            self._start(child, label=suite, kind="gradle-test-suite")
            if kind == "test-suite-finish":
                status = ExecutionStatus.FAILED.value if int(event.get("failed") or 0) else ExecutionStatus.SUCCEEDED.value
                self._finish(child, status=status, exit_code=1 if status == "failed" else 0, metadata={
                    "outcome": event.get("result"),
                    "passed": event.get("passed"),
                    "failed": event.get("failed"),
                    "skipped": event.get("skipped"),
                    "total": event.get("total"),
                })
            return
        if kind in {"test-start", "test-finish"}:
            test = str(event.get("test") or "Gradle test").strip()
            child = self._gradle_test_ids.get(test)
            if not child:
                child = self._discover(kind="gradle-test", key=test, label=test, instance=len(self._gradle_test_ids))
                self._gradle_test_ids[test] = child
            self._start(child, label=test, kind="gradle-test")
            if kind == "test-finish":
                outcome = str(event.get("outcome") or "success").strip().lower()
                status = ExecutionStatus.FAILED.value if outcome in {"failure", "failed"} else (
                    ExecutionStatus.SKIPPED.value if outcome == "skipped" else ExecutionStatus.SUCCEEDED.value
                )
                self._finish(child, status=status, exit_code=1 if status == "failed" else 0, metadata={
                    "outcome": outcome,
                    "duration_ms": event.get("durationMs"),
                })

    def register_npm_plan(self, steps: Iterable[Any]) -> None:
        self._npm_plan_ids.clear()
        for index, step in enumerate(steps):
            label = str(getattr(step, "label", "") or getattr(step, "script", "") or f"script-{index + 1}")
            child = self._discover(
                kind="npm-script",
                key=label,
                label=label,
                instance=index,
                metadata={"script_index": index + 1},
            )
            self._npm_plan_ids.append((label, child))

    def npm_script_event(self, event: dict[str, Any]) -> None:
        kind = str(event.get("kind") or "")
        label = str(event.get("label") or event.get("script") or "npm-script")
        if kind == "script-start":
            child = ""
            for candidate_label, candidate_id in self._npm_plan_ids[self._npm_started:]:
                if candidate_label == label:
                    child = candidate_id
                    break
            if not child:
                child = self._discover(kind="npm-script", key=label, label=label, instance=self._npm_started)
            self._npm_started += 1
            self._npm_active = child
            self._start(child, label=label, kind="npm-script")
        elif kind == "script-finish":
            child = self._npm_active
            if child:
                status = str(event.get("status") or ExecutionStatus.SUCCEEDED.value)
                self._finish(child, status=status, exit_code=int(event.get("exit_code") or 0))
                self._npm_active = ""

    def dotnet_unit_event(self, event: dict[str, Any]) -> None:
        unit = str(event.get("unit") or "").strip()
        if not unit:
            return
        label = str(event.get("label") or unit)
        unit_kind = unit.split(":", 1)[0] if ":" in unit else "unit"
        child = self._discover(kind=f"dotnet-{unit_kind}", key=unit, label=label)
        kind = str(event.get("kind") or "unit-finish")
        if kind == "unit-start":
            self._start(child, label=label, kind=f"dotnet-{unit_kind}")
            self._dotnet_active.add(child)
            return
        self._start(child, label=label, kind=f"dotnet-{unit_kind}")
        status = str(event.get("status") or ExecutionStatus.SUCCEEDED.value)
        self._finish(child, status=status, exit_code=int(event.get("exit_code") or (1 if status == "failed" else 0)))
        self._dotnet_active.discard(child)

    def finish(self, returncode: int) -> None:
        status = (
            ExecutionStatus.CANCELLED.value if int(returncode) == 130
            else ExecutionStatus.SUCCEEDED.value if int(returncode) == 0
            else ExecutionStatus.FAILED.value
        )
        if self._npm_active:
            self._finish(self._npm_active, status=status, exit_code=int(returncode))
            self._npm_active = ""
        for child in tuple(self._dotnet_active):
            self._finish(child, status=status, exit_code=int(returncode))
            self._dotnet_active.discard(child)


__all__ = ["RunnerExecutionAdapter"]
