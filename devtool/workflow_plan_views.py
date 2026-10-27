from __future__ import annotations

"""Read-only static DAG projections for the canonical workflow plan.

No runtime status or duration is inferred from the declaration.  Wave boundaries
are sourced exclusively from workflow_scheduler, not recomputed by this view.
"""

import json
import textwrap
from typing import Any

from .workflow_graph import ExecutionGraph
from .workflow_scheduler import WorkflowExecutionPlan


def static_dependency_analysis(graph: ExecutionGraph, schedule: WorkflowExecutionPlan | None) -> dict[str, Any]:
    """Describe dependency hops and conservative scheduling without predicting runtime."""
    order = graph.topological_order()  # also refuses cyclic/invalid graphs
    by_key = graph.by_key
    paths: dict[str, tuple[str, ...]] = {}
    for node in order:
        parents = [paths[key] for key in node.depends_on]
        # Stable tie-breaking: choose first topological predecessor on equal lengths.
        longest = max(parents, key=len, default=())
        paths[node.key] = (*longest, node.key)
    critical = max((paths[n.key] for n in order), key=len, default=())
    wave_by_key: dict[str, int] = {}
    reasons: dict[str, str] = {}
    if schedule is not None:
        for wave in schedule.waves:
            for key in wave.nodes:
                wave_by_key[key] = wave.index
                reasons[key] = wave.serialized_reason
    scope_by_key = schedule.by_key if schedule is not None else {}
    nodes = []
    for node in order:
        scope = scope_by_key.get(node.key)
        nodes.append({
            "id": node.key,
            "depends_on": list(node.depends_on),
            "ready_after": list(node.depends_on),
            "scheduled_wave": wave_by_key.get(node.key),
            "wave_serialization_reason": reasons.get(node.key) or "",
            "parallel_eligible": scope.parallel_eligible if scope is not None else None,
            "scope_reason": scope.scope_reason if scope is not None else "",
            "resource_class": scope.resource_class if scope is not None else "",
        })
    return {
        "schema_version": 1,
        "kind": "static_dependency_and_reservation_projection",
        "runtime_status_available": False,
        "timing_estimates_available": False,
        "critical_dependency_path": list(critical),
        "critical_dependency_hops": max(len(critical) - 1, 0),
        "critical_path_basis": "edge_count_not_elapsed_time",
        "node_blockers": nodes,
        "schedule_source": "canonical_workflow_scheduler" if schedule is not None else "not_available",
    }


def _wrap_line(prefix: str, value: str, *, width: int = 40) -> list[str]:
    # Preserve the exact machine identity by lossless wrapping: never truncate.
    indent = " " * len(prefix)
    chunks = textwrap.wrap(value, width=max(8, width - len(prefix)),
                           break_long_words=True, break_on_hyphens=False,
                           replace_whitespace=False, drop_whitespace=False)
    return [prefix + (chunks[0] if chunks else "-")] + [indent + chunk for chunk in chunks[1:]]


def render_plan_ascii(graph: ExecutionGraph, schedule: WorkflowExecutionPlan | None) -> str:
    analysis = static_dependency_analysis(graph, schedule)
    lines = ["Devtool DAG · static plan", "No execution or timing implied"]
    if schedule is None:
        lines.append("Schedule: unavailable")
        for node in graph.topological_order():
            lines += _wrap_line("  id: ", node.key)
            for dependency in node.depends_on:
                lines += _wrap_line("    <- ", dependency)
    else:
        for wave in schedule.waves:
            lines.append(f"Wave {wave.index}")
            if wave.serialized_reason:
                lines += _wrap_line("  why: ", wave.serialized_reason)
            for key in wave.nodes:
                node = graph.by_key[key]
                lines += _wrap_line("  id: ", node.key)
                for dependency in node.depends_on:
                    lines += _wrap_line("    <- ", dependency)
                scheduled = schedule.by_key.get(key)
                if scheduled and not scheduled.parallel_eligible:
                    lines.append("  exclusive: scope unknown")
    lines.append(f"Longest chain: {analysis['critical_dependency_hops']} edges (not time)")
    for key in analysis["critical_dependency_path"]:
        lines += _wrap_line("  > ", key)
    return "\n".join(lines) + "\n"


def render_plan_mermaid(graph: ExecutionGraph, schedule: WorkflowExecutionPlan | None) -> str:
    # Mermaid's stable printable aliases avoid treating arbitrary config IDs as DSL.
    order = graph.topological_order()
    aliases = {node.key: f"n{index}" for index, node in enumerate(order)}
    lines = ["flowchart TD"]
    for node in order:
        label = json.dumps(node.key, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e")
        lines.append(f"  {aliases[node.key]}[{label}]")
    for node in order:
        for parent in node.depends_on:
            lines.append(f"  {aliases[parent]} --> {aliases[node.key]}")
    # Waves are deliberately not modeled as dependencies; resource serialization
    # isn't an edge in the declared DAG.
    return "\n".join(lines) + "\n"


def render_plan_dot(graph: ExecutionGraph, schedule: WorkflowExecutionPlan | None) -> str:
    """Preserve existing DOT labels while escaping user-supplied node identities."""
    def quoted(value: str) -> str:
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r") + '"'

    lines = ["digraph devtool_workflow {", '  rankdir="LR";', '  node [shape="box"];']
    schedule_nodes = schedule.by_key if schedule is not None else {}
    for node in graph.nodes:
        scheduled = schedule_nodes.get(node.key)
        runner = scheduled.runner if scheduled is not None else (node.runner or node.ref.kind)
        resolution = node.ref.resolution_key or node.ref.name
        if node.ref.resolution_scope == "target" and resolution:
            resolution = f"job:{resolution}"
        label_parts = [node.target, node.label, runner]
        if resolution and resolution != node.label:
            label_parts.append(resolution)
        lines.append(f"  {quoted(node.key)} [label={quoted(chr(10).join(label_parts))}];")
    for node in graph.nodes:
        for parent in node.depends_on:
            lines.append(f"  {quoted(parent)} -> {quoted(node.key)};")
    lines.append("}")
    return "\n".join(lines)
