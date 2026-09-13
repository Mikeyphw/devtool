from __future__ import annotations

"""Generic workflow DAG primitives used by polyglot target execution.

MP03 intentionally keeps the graph model independent from process execution.
The same graph can therefore back planning, machine output, validation-session
bridges, and the future parallel scheduler without teaching individual runners
about workflow orchestration.
"""

from dataclasses import dataclass, field
import heapq
from typing import Iterable, Mapping

from .models import WorkflowConfig, WorkflowStepRef


class WorkflowGraphError(ValueError):
    """Raised when a configured workflow cannot form a valid DAG."""


@dataclass(frozen=True, slots=True)
class WorkflowNode:
    key: str
    target: str
    workflow: str
    index: int
    ref: WorkflowStepRef
    depends_on: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.ref.step_id or self.ref.raw

    @property
    def runner(self) -> str:
        return self.ref.name if self.ref.kind == "runner" else ""

    @property
    def action(self) -> str:
        return self.ref.action

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "id": self.ref.step_id,
            "target": self.target,
            "workflow": self.workflow,
            "index": self.index,
            "ref": self.ref.raw,
            "kind": self.ref.kind,
            "name": self.ref.name,
            "action": self.ref.action,
            "depends_on": list(self.depends_on),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ExecutionGraph:
    target: str
    workflow: str
    nodes: tuple[WorkflowNode, ...]
    targets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_nodes(self.nodes)

    @property
    def by_key(self) -> dict[str, WorkflowNode]:
        return {node.key: node for node in self.nodes}

    @property
    def roots(self) -> tuple[WorkflowNode, ...]:
        return tuple(node for node in self.nodes if not node.depends_on)

    @property
    def leaves(self) -> tuple[WorkflowNode, ...]:
        depended_on = {dependency for node in self.nodes for dependency in node.depends_on}
        return tuple(node for node in self.nodes if node.key not in depended_on)

    def topological_order(self) -> tuple[WorkflowNode, ...]:
        by_key = self.by_key
        indegree = {node.key: len(node.depends_on) for node in self.nodes}
        dependents: dict[str, list[str]] = {node.key: [] for node in self.nodes}
        for node in self.nodes:
            for dependency in node.depends_on:
                dependents[dependency].append(node.key)

        # Preserve declaration order for nodes that become ready together.
        index = {node.key: node.index for node in self.nodes}
        ready: list[tuple[int, str]] = [
            (index[key], key) for key, degree in indegree.items() if degree == 0
        ]
        heapq.heapify(ready)
        ordered: list[WorkflowNode] = []
        while ready:
            _, key = heapq.heappop(ready)
            ordered.append(by_key[key])
            for dependent in sorted(dependents[key], key=lambda value: index[value]):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    heapq.heappush(ready, (index[dependent], dependent))
        if len(ordered) != len(self.nodes):
            cycle = _cycle_path(self.nodes)
            detail = " -> ".join(cycle) if cycle else "unknown cycle"
            raise WorkflowGraphError(
                f"Workflow dependency cycle detected in {self.target}.{self.workflow}: {detail}"
            )
        return tuple(ordered)

    def dependency_closure(self, keys: Iterable[str]) -> frozenset[str]:
        by_key = self.by_key
        closure: set[str] = set()

        def visit(key: str) -> None:
            if key in closure:
                return
            if key not in by_key:
                raise WorkflowGraphError(
                    f"Unknown workflow node {key!r} in dependency closure for {self.target}.{self.workflow}."
                )
            closure.add(key)
            for dependency in by_key[key].depends_on:
                visit(dependency)

        for key in keys:
            visit(key)
        return frozenset(closure)

    def dependent_closure(self, keys: Iterable[str]) -> frozenset[str]:
        seeds = set(keys)
        unknown = seeds.difference(self.by_key)
        if unknown:
            raise WorkflowGraphError(
                f"Unknown workflow node(s) in dependent closure: {', '.join(sorted(unknown))}"
            )
        closure = set(seeds)
        changed = True
        while changed:
            changed = False
            for node in self.nodes:
                if node.key in closure:
                    continue
                if any(dependency in closure for dependency in node.depends_on):
                    closure.add(node.key)
                    changed = True
        return frozenset(closure)

    def as_dict(self) -> dict[str, object]:
        ordered = self.topological_order()
        return {
            "schema_version": 2 if self.targets else 1,
            "target": self.target,
            "targets": list(self.targets),
            "workflow": self.workflow,
            "roots": [node.key for node in self.roots],
            "leaves": [node.key for node in self.leaves],
            "order": [node.key for node in ordered],
            "nodes": [node.as_dict() for node in self.nodes],
        }


def _slug(value: str) -> str:
    chars: list[str] = []
    previous_dash = False
    for char in value.lower():
        if char.isalnum() or char in {"_", "."}:
            chars.append(char)
            previous_dash = False
        elif not previous_dash:
            chars.append("-")
            previous_dash = True
    return "".join(chars).strip("-") or "step"


def _key_component(value: str) -> str:
    """Escape a node-key component without changing ordinary slugs.

    Target, workflow, and step ids may legally contain dots.  Joining raw
    components with dots made target ``a.b`` + workflow ``c`` collide with
    target ``a`` + workflow ``b.c``.  Backslash-escaping keeps existing keys
    stable for common names while making dotted components unambiguous.
    """
    return str(value or "").replace("\\", "\\\\").replace(".", "\\.")


def _node_key(target: str, workflow: str, index: int, ref: WorkflowStepRef) -> str:
    identity = ref.step_id or f"{index:02d}-{_slug(ref.raw)}"
    return f"{_key_component(target)}.{_key_component(workflow)}.{_key_component(identity)}"


def graph_from_workflow(target: str, workflow: WorkflowConfig) -> ExecutionGraph:
    """Build a stable DAG from one normalized workflow declaration.

    Compatibility rule: a step without an explicit ``depends_on`` declaration
    depends on the immediately preceding step. This preserves the MP02 ordered
    list contract. An inline table with ``depends_on = []`` explicitly creates
    another root and therefore enables branches.
    """

    if not workflow.steps:
        raise WorkflowGraphError(f"Workflow {target}.{workflow.name} cannot be empty.")

    step_ids: dict[str, int] = {}
    generated_keys: list[str] = []
    for index, ref in enumerate(workflow.steps, 1):
        if ref.step_id:
            if ref.step_id in step_ids:
                raise WorkflowGraphError(
                    f"Workflow {target}.{workflow.name} declares duplicate step id {ref.step_id!r}."
                )
            step_ids[ref.step_id] = index - 1
        generated_keys.append(_node_key(target, workflow.name, index, ref))

    nodes: list[WorkflowNode] = []
    for index, ref in enumerate(workflow.steps, 1):
        if ref.depends_on_declared:
            dependencies: list[str] = []
            for dependency_id in ref.depends_on:
                dependency_index = step_ids.get(dependency_id)
                if dependency_index is None:
                    available = ", ".join(sorted(step_ids)) or "<none>"
                    raise WorkflowGraphError(
                        f"Workflow {target}.{workflow.name} step {ref.step_id or index!r} depends on unknown "
                        f"step id {dependency_id!r}. Available ids: {available}"
                    )
                dependencies.append(generated_keys[dependency_index])
        elif index > 1:
            dependencies = [generated_keys[index - 2]]
        else:
            dependencies = []
        nodes.append(
            WorkflowNode(
                key=generated_keys[index - 1],
                target=target,
                workflow=workflow.name,
                index=index,
                ref=ref,
                depends_on=tuple(dict.fromkeys(dependencies)),
            )
        )

    graph = ExecutionGraph(target=target, workflow=workflow.name, nodes=tuple(nodes))
    graph.topological_order()  # force cycle validation at planning time
    return graph


def _validate_nodes(nodes: tuple[WorkflowNode, ...]) -> None:
    keys = [node.key for node in nodes]
    if len(keys) != len(set(keys)):
        raise WorkflowGraphError("Workflow graph contains duplicate node keys.")
    known = set(keys)
    for node in nodes:
        if node.key in node.depends_on:
            raise WorkflowGraphError(f"Workflow node {node.key!r} cannot depend on itself.")
        missing = [dependency for dependency in node.depends_on if dependency not in known]
        if missing:
            raise WorkflowGraphError(
                f"Workflow node {node.key!r} depends on unknown node(s): {', '.join(missing)}"
            )


def _cycle_path(nodes: tuple[WorkflowNode, ...]) -> list[str]:
    by_key = {node.key: node for node in nodes}
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(key: str) -> list[str] | None:
        if key in visiting:
            try:
                start = stack.index(key)
            except ValueError:
                start = 0
            return [*stack[start:], key]
        if key in visited:
            return None
        visiting.add(key)
        stack.append(key)
        for dependency in by_key[key].depends_on:
            found = visit(dependency)
            if found:
                return found
        stack.pop()
        visiting.remove(key)
        visited.add(key)
        return None

    for node in nodes:
        found = visit(node.key)
        if found:
            return found
    return []
