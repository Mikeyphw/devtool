from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .state import UIOperationState, UIProgressState

PROGRESS_SCHEMA_VERSION = 1
_TERMINAL = {"ok", "passed", "success", "completed", "failed", "rollback-failed", "rollback_failed", "skipped", "cancelled", "interrupted", "blocked", "cached", "warning", "warnings"}
_SUCCESS = {"ok", "passed", "success", "completed", "cached"}
_PROBLEM = {"failed", "rollback-failed", "rollback_failed"}
_CANCELLED = {"cancelled", "interrupted"}


@dataclass(slots=True)
class UIProgressTreeNode:
    id: str
    label: str
    kind: str
    status: str
    token: str
    percent: int | None = None
    current: int | None = None
    total: int | None = None
    detail: str = ""
    target: str = ""
    workflow: str = ""
    runner: str = ""
    execution_environment: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int | None = None
    warnings: int = 0
    errors: int = 0
    blocked_by: list[str] = field(default_factory=list)
    children: list["UIProgressTreeNode"] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class UIProgressSummary:
    total_nodes: int = 0
    completed_nodes: int = 0
    running_nodes: int = 0
    pending_nodes: int = 0
    failed_nodes: int = 0
    blocked_nodes: int = 0
    skipped_nodes: int = 0
    cancelled_nodes: int = 0
    overall_percent: int | None = None
    active_ids: list[str] = field(default_factory=list)
    schema_version: int = PROGRESS_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize_progress(nodes: Iterable[UIProgressState]) -> UIProgressSummary:
    rows = list(nodes)
    summary = UIProgressSummary(total_nodes=len(rows))
    weighted: list[int] = []
    for node in rows:
        status = str(node.status or "pending").lower()
        if status in _TERMINAL:
            summary.completed_nodes += 1
        if status == "running":
            summary.running_nodes += 1
            if node.id not in summary.active_ids:
                summary.active_ids.append(node.id)
        elif status in {"pending", "ready", "idle", "waiting"}:
            summary.pending_nodes += 1
        elif status in _PROBLEM:
            summary.failed_nodes += 1
        elif status == "blocked":
            summary.blocked_nodes += 1
        elif status == "skipped":
            summary.skipped_nodes += 1
        elif status in _CANCELLED:
            summary.cancelled_nodes += 1
        if node.percent is not None:
            weighted.append(max(0, min(100, int(node.percent))))
        elif status in _SUCCESS or status in _PROBLEM or status in {"skipped", "cancelled", "interrupted", "blocked", "warning", "warnings"}:
            weighted.append(100)
        else:
            weighted.append(0)
    if weighted:
        summary.overall_percent = int(round(sum(weighted) / len(weighted)))
    return summary


def _tree_node(item: UIProgressState) -> UIProgressTreeNode:
    return UIProgressTreeNode(
        id=item.id,
        label=item.label,
        kind=item.kind,
        status=item.status,
        token=item.token,
        percent=item.percent,
        current=item.current,
        total=item.total,
        detail=item.detail,
        target=item.target,
        workflow=item.workflow,
        runner=item.runner,
        execution_environment=item.execution_environment,
        started_at=item.started_at,
        finished_at=item.finished_at,
        duration_ms=item.duration_ms,
        warnings=item.warnings,
        errors=item.errors,
        blocked_by=list(item.blocked_by),
    )


def build_progress_tree(nodes: Iterable[UIProgressState], operation: UIOperationState) -> list[UIProgressTreeNode]:
    rows = list(nodes)
    operation_id = f"operation:{operation.run_id or operation.command or 'current'}"
    root = UIProgressTreeNode(
        id=operation_id,
        label=operation.title or operation.command or "Devtool operation",
        kind="operation",
        status=operation.status,
        token=operation.token,
        detail=operation.detail,
        target=operation.target,
        runner=operation.runner,
        execution_environment=operation.execution_environment,
        started_at=operation.started_at,
        finished_at=operation.finished_at,
    )
    if not rows:
        return [root]

    target_nodes: dict[str, UIProgressTreeNode] = {}
    workflow_nodes: dict[tuple[str, str], UIProgressTreeNode] = {}
    explicit: dict[str, UIProgressTreeNode] = {}
    parent_by_id: dict[str, str] = {}

    for item in rows:
        node = _tree_node(item)
        explicit[item.id] = node
        if item.parent_id:
            parent_by_id[item.id] = item.parent_id
        if item.parent_id:
            continue
        target = item.target or operation.target
        parent = root
        if item.workflow:
            key = ("", item.workflow)
            workflow_node = workflow_nodes.get(key)
            if workflow_node is None:
                workflow_node = UIProgressTreeNode(
                    id=f"workflow:{item.workflow}", label=item.workflow, kind="workflow", status="running", token="status.running", workflow=item.workflow
                )
                workflow_nodes[key] = workflow_node
                root.children.append(workflow_node)
            parent = workflow_node
        if target:
            target_key = f"{item.workflow}\x1f{target}" if item.workflow else target
            target_node = target_nodes.get(target_key)
            if target_node is None:
                target_node = UIProgressTreeNode(
                    id=f"target:{item.workflow}:{target}" if item.workflow else f"target:{target}",
                    label=target, kind="target", status="running", token="status.running", target=target, workflow=item.workflow
                )
                target_nodes[target_key] = target_node
                parent.children.append(target_node)
            parent = target_node
        parent.children.append(node)

    # Explicit parent links take precedence over synthesized target/workflow parents.
    def would_cycle(child_id: str, parent_id: str) -> bool:
        seen = {child_id}
        cursor = parent_id
        while cursor:
            if cursor in seen:
                return True
            seen.add(cursor)
            cursor = parent_by_id.get(cursor, "")
        return False

    for item in rows:
        if not item.parent_id:
            continue
        node = explicit[item.id]
        parent = explicit.get(item.parent_id)
        if parent is not None and not would_cycle(item.id, item.parent_id):
            parent.children.append(node)
        else:
            root.children.append(node)

    summary = summarize_progress(rows)
    root.percent = summary.overall_percent

    def fold(node: UIProgressTreeNode) -> str:
        for child in node.children:
            fold(child)
        if node.kind in {"target", "workflow"} and node.children:
            statuses = [child.status for child in node.children]
            if any(value in _PROBLEM for value in statuses):
                node.status, node.token = "failed", "status.failed"
            elif any(value in _CANCELLED for value in statuses):
                node.status, node.token = "cancelled", "status.cancelled"
            elif any(value == "running" for value in statuses):
                node.status, node.token = "running", "status.running"
            elif any(value == "blocked" for value in statuses):
                node.status, node.token = "blocked", "status.blocked"
            elif any(value in {"warning", "warnings"} for value in statuses):
                node.status, node.token = "warning", "status.warning"
            elif all(value in _TERMINAL for value in statuses):
                node.status, node.token = "ok", "status.success"
            percents = [value.percent for value in node.children if value.percent is not None]
            if percents:
                node.percent = int(round(sum(percents) / len(percents)))
        return node.status

    fold(root)
    return [root]
