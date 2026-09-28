from __future__ import annotations

"""Canonical stable test inventory, semantic lanes, and immutable selections.

TEST-X02 deliberately keeps execution ownership outside this module.  It compiles
stable test/check identity and policy into immutable selections which EXO/runner
code can consume without inventing a pytest-specific scheduler or run store.
"""

from dataclasses import dataclass, field
import ast
import fnmatch
import hashlib
import json
import os
import platform
import re
from pathlib import Path
from functools import lru_cache
import shutil
import subprocess
import sys
import tomllib
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from .test_model import TestSpec
from .test_selection import TestDescriptor, discover_python_tests

TEST_LANES = ("fast", "integration", "external", "system", "full")
EXECUTION_LANES = ("fast", "integration", "external", "system")
PARALLEL_SAFETY = (
    "parallel-safe",
    "serial-process-global",
    "serial-repository",
    "serial-device",
    "serial-port/socket",
)
CONTRACT_LEVELS = ("public-contract", "white-box")
DEFAULT_INVENTORY_FILE = "devtool-tests.toml"


def _tuple(values: Iterable[Any] | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip() for value in (values or ()) if str(value).strip()))


def _frozen_map(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


def _sha_payload(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class TestClassificationRule:
    __test__ = False
    id: str
    pattern: str
    lanes: tuple[str, ...]
    resources: tuple[str, ...] = ()
    parallel_safety: str = "serial-process-global"
    required_tools: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    budget_class: str = "default"
    contract_level: str = "white-box"
    behavior: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", str(self.id).strip())
        object.__setattr__(self, "pattern", str(self.pattern).strip().replace("\\", "/"))
        object.__setattr__(self, "lanes", _tuple(self.lanes))
        object.__setattr__(self, "resources", _tuple(self.resources))
        object.__setattr__(self, "required_tools", _tuple(self.required_tools))
        object.__setattr__(self, "required_capabilities", _tuple(self.required_capabilities))
        if not self.id or not self.pattern or not self.lanes:
            raise ValueError("test classification rule requires id, pattern, and lanes")
        unknown = sorted(set(self.lanes) - set(TEST_LANES))
        if unknown:
            raise ValueError(f"classification rule {self.id!r} has invalid lanes: {', '.join(unknown)}")
        if self.parallel_safety not in PARALLEL_SAFETY:
            raise ValueError(f"classification rule {self.id!r} has invalid parallel_safety: {self.parallel_safety!r}")
        if self.contract_level not in CONTRACT_LEVELS:
            raise ValueError(f"classification rule {self.id!r} has invalid contract_level: {self.contract_level!r}")

    def matches(self, selector: str, file: str = "") -> bool:
        selector = str(selector).replace("\\", "/")
        file = str(file).replace("\\", "/")
        return fnmatch.fnmatchcase(selector, self.pattern) or (bool(file) and fnmatch.fnmatchcase(file, self.pattern))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "pattern": self.pattern,
            "lanes": list(self.lanes),
            "resources": list(self.resources),
            "parallel_safety": self.parallel_safety,
            "required_tools": list(self.required_tools),
            "required_capabilities": list(self.required_capabilities),
            "budget_class": self.budget_class,
            "contract_level": self.contract_level,
            "behavior": self.behavior,
        }


@dataclass(frozen=True, slots=True)
class ToolQualification:
    name: str
    available: bool
    path: str = ""
    version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "available": self.available, "path": self.path, "version": self.version}


@dataclass(frozen=True, slots=True)
class TestEnvironmentQualification:
    schema_version: int
    qualification_id: str
    selection_id: str
    ready: bool
    host: Mapping[str, Any]
    tools: tuple[ToolQualification, ...]
    required_capabilities: tuple[str, ...]
    missing_tools: tuple[str, ...]
    missing_capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "host", _frozen_map(self.host))
        object.__setattr__(self, "required_capabilities", _tuple(self.required_capabilities))
        object.__setattr__(self, "missing_tools", _tuple(self.missing_tools))
        object.__setattr__(self, "missing_capabilities", _tuple(self.missing_capabilities))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "qualification_id": self.qualification_id,
            "selection_id": self.selection_id,
            "ready": self.ready,
            "host": dict(self.host),
            "tools": [tool.to_dict() for tool in self.tools],
            "required_capabilities": list(self.required_capabilities),
            "missing_tools": list(self.missing_tools),
            "missing_capabilities": list(self.missing_capabilities),
        }


@dataclass(frozen=True, slots=True)
class TestCheckGroup:
    __test__ = False
    id: str
    checks: tuple[str, ...]
    description: str = ""
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", str(self.id).strip())
        object.__setattr__(self, "checks", _tuple(self.checks))
        object.__setattr__(self, "aliases", _tuple(self.aliases))
        if not self.id:
            raise ValueError("test check group id is required")
        if not self.checks:
            raise ValueError(f"test check group {self.id!r} must contain at least one check")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "checks": list(self.checks),
            "description": self.description,
            "aliases": list(self.aliases),
        }


@dataclass(frozen=True, slots=True)
class SelectionExclusion:
    __test__ = False
    check_id: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"check_id": self.check_id, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class TestShard:
    __test__ = False
    index: int
    shard_id: str
    check_ids: tuple[str, ...]
    pytest_nodes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "shard_id": self.shard_id,
            "check_ids": list(self.check_ids),
            "pytest_nodes": list(self.pytest_nodes),
        }


@dataclass(frozen=True, slots=True)
class TestShardPlan:
    __test__ = False
    schema_version: int
    plan_id: str
    selection_id: str
    shard_count: int
    shards: tuple[TestShard, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "selection_id": self.selection_id,
            "shard_count": self.shard_count,
            "shards": [shard.to_dict() for shard in self.shards],
        }

    def verify_complete(self, expected_check_ids: Sequence[str]) -> tuple[bool, str]:
        expected = tuple(sorted(dict.fromkeys(str(value) for value in expected_check_ids)))
        flattened = tuple(check for shard in self.shards for check in shard.check_ids)
        duplicates = sorted({check for check in flattened if flattened.count(check) > 1})
        missing = sorted(set(expected) - set(flattened))
        unexpected = sorted(set(flattened) - set(expected))
        if duplicates or missing or unexpected or len(flattened) != len(expected):
            return False, f"missing={missing} duplicate={duplicates} unexpected={unexpected}"
        return True, "complete"


@dataclass(frozen=True, slots=True)
class TestSelection:
    __test__ = False
    """Immutable canonical selection handed to execution/sharding boundaries."""

    schema_version: int
    selection_id: str
    requested_lanes: tuple[str, ...]
    requested_checks: tuple[str, ...]
    requested_groups: tuple[str, ...]
    resolved_check_ids: tuple[str, ...]
    resolved_pytest_nodes: tuple[str, ...]
    exclusions: tuple[SelectionExclusion, ...]
    resources: tuple[str, ...]
    serial_constraints: tuple[str, ...]
    cfg_provenance: Mapping[str, Any] = field(default_factory=dict)
    shard_plan: TestShardPlan | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "requested_lanes", _tuple(self.requested_lanes))
        object.__setattr__(self, "requested_checks", _tuple(self.requested_checks))
        object.__setattr__(self, "requested_groups", _tuple(self.requested_groups))
        object.__setattr__(self, "resolved_check_ids", _tuple(self.resolved_check_ids))
        object.__setattr__(self, "resolved_pytest_nodes", _tuple(self.resolved_pytest_nodes))
        object.__setattr__(self, "resources", _tuple(self.resources))
        object.__setattr__(self, "serial_constraints", _tuple(self.serial_constraints))
        object.__setattr__(self, "cfg_provenance", _frozen_map(self.cfg_provenance))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "selection_id": self.selection_id,
            "requested_lanes": list(self.requested_lanes),
            "requested_checks": list(self.requested_checks),
            "requested_groups": list(self.requested_groups),
            "resolved_check_ids": list(self.resolved_check_ids),
            "resolved_pytest_nodes": list(self.resolved_pytest_nodes),
            "exclusions": [item.to_dict() for item in self.exclusions],
            "resources": list(self.resources),
            "serial_constraints": list(self.serial_constraints),
            "cfg_provenance": dict(self.cfg_provenance),
            "shard_plan": self.shard_plan.to_dict() if self.shard_plan else None,
        }


@dataclass(frozen=True, slots=True)
class TestInventory:
    __test__ = False
    specs: tuple[TestSpec, ...]
    groups: tuple[TestCheckGroup, ...] = ()
    source: str = ""
    schema_version: int = 1
    classification_rules: tuple[TestClassificationRule, ...] = ()

    def __post_init__(self) -> None:
        specs = tuple(sorted(self.specs, key=lambda spec: spec.id))
        groups = tuple(sorted(self.groups, key=lambda group: group.id))
        rules = tuple(self.classification_rules)
        object.__setattr__(self, "specs", specs)
        object.__setattr__(self, "groups", groups)
        object.__setattr__(self, "classification_rules", rules)
        ids: dict[str, str] = {}
        for spec in specs:
            for identity in (spec.id, *spec.aliases):
                key = identity.casefold()
                previous = ids.get(key)
                if previous and previous != spec.id:
                    raise ValueError(f"duplicate test identity/alias {identity!r}: {previous!r} vs {spec.id!r}")
                ids[key] = spec.id
        group_aliases: dict[str, str] = {}
        known = {spec.id for spec in specs}
        for group in groups:
            for identity in (group.id, *group.aliases):
                key = identity.casefold()
                previous = group_aliases.get(key)
                if previous and previous != group.id:
                    raise ValueError(f"duplicate test group identity/alias {identity!r}")
                group_aliases[key] = group.id
            missing = sorted(set(group.checks) - known)
            if missing:
                raise ValueError(f"test group {group.id!r} references unknown checks: {', '.join(missing)}")
        rule_ids: set[str] = set()
        for rule in rules:
            key = rule.id.casefold()
            if key in rule_ids:
                raise ValueError(f"duplicate test classification rule id {rule.id!r}")
            rule_ids.add(key)
        for spec in specs:
            missing_dependencies = sorted(dep for dep in spec.dependencies if dep.casefold() not in ids)
            if missing_dependencies:
                raise ValueError(f"test check {spec.id!r} references unknown dependencies: {', '.join(missing_dependencies)}")
            for group_name in spec.groups:
                canonical = group_aliases.get(group_name.casefold())
                if not canonical:
                    raise ValueError(f"test check {spec.id!r} references unknown group {group_name!r}")
                group = next(group for group in groups if group.id == canonical)
                if spec.id not in group.checks:
                    raise ValueError(f"test check {spec.id!r} declares group {canonical!r} but the group does not include it")
            if "external" in spec.lanes and not (spec.required_tools or spec.required_capabilities):
                raise ValueError(f"external test check {spec.id!r} must declare an exact tool or host capability requirement")

        # Dependency identities are stable check IDs/aliases. Validate the graph
        # up front so selection cannot silently recurse forever or depend on an
        # ambiguous historical alias.
        canonical_dependencies: dict[str, tuple[str, ...]] = {}
        for spec in specs:
            canonical_dependencies[spec.id] = tuple(ids[dep.casefold()] for dep in spec.dependencies)
        visiting: set[str] = set()
        visited: set[str] = set()

        def _visit(check_id: str, stack: tuple[str, ...] = ()) -> None:
            if check_id in visited:
                return
            if check_id in visiting:
                cycle = " -> ".join((*stack, check_id))
                raise ValueError(f"test dependency cycle detected: {cycle}")
            visiting.add(check_id)
            for dep in canonical_dependencies.get(check_id, ()):
                _visit(dep, (*stack, check_id))
            visiting.remove(check_id)
            visited.add(check_id)

        for check_id in canonical_dependencies:
            _visit(check_id)

    @property
    def digest(self) -> str:
        return _sha_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "environment": _test_environment_contract(self),
            "specs": [test_spec_to_inventory_dict(spec) for spec in self.specs],
            "groups": [group.to_dict() for group in self.groups],
            "classification_rules": [rule.to_dict() for rule in self.classification_rules],
        }

    def resolve_check_id(self, value: str) -> str:
        wanted = str(value).strip().casefold()
        for spec in self.specs:
            if wanted in {spec.id.casefold(), *(alias.casefold() for alias in spec.aliases)}:
                return spec.id
        raise KeyError(f"unknown test check {value!r}")

    def resolve_group_id(self, value: str) -> str:
        wanted = str(value).strip().casefold()
        for group in self.groups:
            if wanted in {group.id.casefold(), *(alias.casefold() for alias in group.aliases)}:
                return group.id
        raise KeyError(f"unknown test check group {value!r}")

    def spec(self, value: str) -> TestSpec:
        check_id = self.resolve_check_id(value)
        return next(spec for spec in self.specs if spec.id == check_id)

    def explain(self, value: str) -> dict[str, Any]:
        spec = self.spec(value)
        return {
            **test_spec_to_inventory_dict(spec),
            "inventory_source": self.source,
            "identity_kind": "durable" if not bool(spec.metadata.get("selector_derived")) else "selector-derived",
        }

    def select(
        self,
        *,
        lanes: Sequence[str] = (),
        checks: Sequence[str] = (),
        groups: Sequence[str] = (),
        provenance: Mapping[str, Any] | None = None,
        shard_count: int = 0,
    ) -> TestSelection:
        requested_lanes = _tuple(lanes)
        unknown_lanes = sorted(set(requested_lanes) - set(TEST_LANES))
        if unknown_lanes:
            raise ValueError(f"unknown test lane(s): {', '.join(unknown_lanes)}")

        requested_checks = _tuple(checks)
        requested_groups = _tuple(groups)
        explicit_ids = {self.resolve_check_id(value) for value in requested_checks}
        for group_name in requested_groups:
            group_id = self.resolve_group_id(group_name)
            group = next(group for group in self.groups if group.id == group_id)
            explicit_ids.update(group.checks)

        lane_filter = set(requested_lanes)
        if "full" in lane_filter:
            lane_filter = set(EXECUTION_LANES)

        specs_by_id = {spec.id: spec for spec in self.specs}
        if explicit_ids:
            seed_ids = set(explicit_ids)
        else:
            seed_ids = {
                spec.id for spec in self.specs
                if not lane_filter or bool(lane_filter.intersection(spec.lanes))
            }

        # Expand stable dependencies. Dependencies are correctness requirements,
        # not optional metadata. A lane-filtered request may not silently drag a
        # check from a different lane into (for example) fast feedback; fail with
        # an actionable explanation instead. Group/check-only selection has no
        # lane filter and therefore includes its complete dependency closure.
        resolved_ids = set(seed_ids)
        pending = list(sorted(seed_ids))
        while pending:
            check_id = pending.pop()
            spec = specs_by_id[check_id]
            for dependency in spec.dependencies:
                dep_id = self.resolve_check_id(dependency)
                dep_spec = specs_by_id[dep_id]
                if lane_filter and not lane_filter.intersection(dep_spec.lanes):
                    raise ValueError(
                        f"test check {check_id!r} requires dependency {dep_id!r}, "
                        f"which is not eligible for requested lane(s): {', '.join(requested_lanes)}"
                    )
                if dep_id not in resolved_ids:
                    resolved_ids.add(dep_id)
                    pending.append(dep_id)

        selected: list[TestSpec] = []
        exclusions: list[SelectionExclusion] = []
        for spec in self.specs:
            lane_match = not lane_filter or bool(lane_filter.intersection(spec.lanes))
            if spec.id not in resolved_ids:
                reason = "not requested by check/group selector" if explicit_ids else f"not eligible for requested lane(s): {', '.join(requested_lanes)}"
                exclusions.append(SelectionExclusion(spec.id, reason))
                continue
            if lane_filter and not lane_match:
                # Dependency expansion above rejects this case; retain a guard
                # against future selection refactors weakening that invariant.
                raise ValueError(f"selected check {spec.id!r} violates requested lane policy")
            selected.append(spec)

        if (requested_checks or requested_groups) and not selected:
            raise ValueError("test selection resolved to zero checks")

        check_ids = tuple(spec.id for spec in selected)
        nodes = tuple(dict.fromkeys(spec.selector for spec in selected if spec.selector))
        resources = tuple(sorted({resource for spec in selected for resource in spec.resources}))
        serial = tuple(sorted({spec.parallel_safety for spec in selected if spec.parallel_safety != "parallel-safe"}))
        semantic_payload = {
            "schema_version": 1,
            "inventory_digest": self.digest,
            "requested_lanes": list(requested_lanes),
            "requested_checks": list(requested_checks),
            "requested_groups": list(requested_groups),
            "resolved_check_ids": list(check_ids),
            "resolved_pytest_nodes": list(nodes),
            "cfg_provenance": dict(provenance or {}),
        }
        selection_id = "tsel-" + _sha_payload(semantic_payload)[:24]
        base = TestSelection(
            schema_version=1,
            selection_id=selection_id,
            requested_lanes=requested_lanes,
            requested_checks=requested_checks,
            requested_groups=requested_groups,
            resolved_check_ids=check_ids,
            resolved_pytest_nodes=nodes,
            exclusions=tuple(exclusions),
            resources=resources,
            serial_constraints=serial,
            cfg_provenance=provenance or {},
        )
        if shard_count:
            return with_shard_plan(base, self, shard_count)
        return base


def test_spec_to_inventory_dict(spec: TestSpec) -> dict[str, Any]:
    return {
        "check_id": spec.id,
        "name": spec.name,
        "runner": spec.runner,
        "selector": spec.selector,
        "aliases": list(spec.aliases),
        "behavior": spec.behavior,
        "lanes": list(spec.lanes),
        "resources": list(spec.resources),
        "parallel_safety": spec.parallel_safety,
        "required_tools": list(spec.required_tools),
        "required_capabilities": list(spec.required_capabilities),
        "timeout_seconds": spec.timeout_seconds,
        "budget_class": spec.budget_class,
        "contract_level": spec.contract_level,
        "groups": list(spec.groups),
        "dependencies": list(spec.dependencies),
        "metadata": dict(spec.metadata),
    }


def _lane_from_descriptor(descriptor: TestDescriptor) -> str:
    tags = {str(tag).casefold() for tag in descriptor.tags}
    path = descriptor.file.casefold()
    if "system" in tags or "seal" in tags or "final-seal" in tags or any(token in path for token in ("final_seal", "roadmap", "gate_seal")):
        return "system"
    # ``slow_external`` is a legacy performance exemption, not a semantic
    # external-tool declaration.  External selection requires an explicit
    # external/requires_tool contract so tool qualification cannot be skipped.
    if "external" in tags or "requires_tool" in tags:
        return "external"
    if "integration" in tags or "requires_git" in tags or any(token in path for token in ("git_", "transactions", "pty")):
        return "integration"
    return "fast"


def lane_for_pytest_item(*, marker_names: Iterable[str], path: str) -> str:
    descriptor = TestDescriptor(
        id="synthetic",
        name="synthetic",
        suite="synthetic",
        project="synthetic",
        framework="pytest",
        tags=_tuple(marker_names),
        file=str(path),
    )
    return _lane_from_descriptor(descriptor)


def _resource_policy(tags: Iterable[str]) -> tuple[tuple[str, ...], str]:
    values = {str(tag).casefold() for tag in tags}
    resources: list[str] = []
    # Conservative default: discovered tests are not parallel-safe until they
    # explicitly opt in or a central classification rule proves the boundary.
    safety = "parallel-safe" if "parallel_safe" in values else "serial-process-global"
    if safety != "parallel-safe":
        resources.append("serial-process-global")
    if "serial_process_global" in values or "stateful" in values or "serial" in values:
        resources = [resource for resource in resources if resource != "serial-process-global"]
        resources.append("serial-process-global")
        safety = "serial-process-global"
    elif "serial_repository" in values:
        resources.append("serial-repository")
        safety = "serial-repository"
    elif "serial_device" in values:
        resources.append("serial-device")
        safety = "serial-device"
    elif "serial_port_socket" in values:
        resources.append("serial-port/socket")
        safety = "serial-port/socket"
    if "requires_git" in values:
        resources.append("requires-git")
    return _tuple(resources), safety


def _decorator_marker(decorator: ast.AST) -> tuple[str, tuple[str, ...]] | None:
    call = decorator if isinstance(decorator, ast.Call) else None
    current = call.func if call is not None else decorator
    parts: list[str] = []
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    dotted = ".".join(reversed(parts))
    if ".mark." not in dotted:
        return None
    name = dotted.rsplit(".", 1)[-1]
    args: list[str] = []
    if call is not None:
        for arg in call.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, (str, int, float, bool)):
                args.append(str(arg.value))
    return name, tuple(args)


def _merge_marker_metadata(*rows: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "markers": [],
        "required_tools": [],
        "required_capabilities": [],
        "groups": [],
        "dependencies": [],
    }
    for row in rows:
        for key in ("markers", "required_tools", "required_capabilities", "groups", "dependencies"):
            merged[key].extend(row.get(key, ()))
        for key in ("check_id", "behavior", "budget_class", "contract_level"):
            value = row.get(key)
            if value:
                merged[key] = value
    for key in ("markers", "required_tools", "required_capabilities", "groups", "dependencies"):
        merged[key] = list(dict.fromkeys(str(value) for value in merged[key] if str(value)))
    return merged


def _node_marker_metadata(node: ast.AST) -> dict[str, Any]:
    row: dict[str, Any] = {
        "markers": [],
        "required_tools": [],
        "required_capabilities": [],
        "groups": [],
        "dependencies": [],
    }
    for decorator in getattr(node, "decorator_list", []):
        parsed = _decorator_marker(decorator)
        if parsed is None:
            continue
        name, args = parsed
        row["markers"].append(name)
        if name == "requires_tool" and args:
            row["required_tools"].extend(args)
        elif name == "requires_host_capability" and args:
            row["required_capabilities"].extend(args)
        elif name == "requires_posix":
            row["required_capabilities"].append("posix")
        elif name == "devtool_check" and args:
            row["check_id"] = args[0]
        elif name == "devtool_group" and args:
            row["groups"].extend(args)
        elif name == "behavior" and args:
            row["behavior"] = args[0]
        elif name == "test_dependency" and args:
            row["dependencies"].extend(args)
        elif name == "devtool_budget" and args:
            row["budget_class"] = args[0]
        elif name == "public_contract":
            row["contract_level"] = "public-contract"
        elif name == "white_box":
            row["contract_level"] = "white-box"
    return _merge_marker_metadata(row)


def _pytest_source_metadata(root: Path) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    candidates = sorted({*root.glob("test_*.py"), *root.glob("**/test_*.py"), *root.glob("**/*_test.py")})
    ignored = {".git", ".venv", "venv", "build", "dist", ".tox", ".nox", "__pycache__"}
    for path in candidates:
        if any(part in ignored for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        try:
            rel = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = path.as_posix()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                metadata[f"{rel}::{node.name}"] = _node_marker_metadata(node)
            elif isinstance(node, ast.ClassDef):
                class_meta = _node_marker_metadata(node)
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test"):
                        metadata[f"{rel}::{node.name}::{child.name}"] = _merge_marker_metadata(class_meta, _node_marker_metadata(child))
    return metadata


def _matching_rule(rules: Sequence[TestClassificationRule], descriptor: TestDescriptor) -> TestClassificationRule | None:
    for rule in rules:
        if rule.matches(descriptor.id, descriptor.file):
            return rule
    return None


def _synthetic_spec(
    descriptor: TestDescriptor,
    marker_meta: Mapping[str, Any] | None = None,
    classification: TestClassificationRule | None = None,
) -> TestSpec:
    marker_meta = dict(marker_meta or {})
    tags = tuple(dict.fromkeys([*descriptor.tags, *marker_meta.get("markers", ())]))
    fallback_lane = _lane_from_descriptor(TestDescriptor(
        id=descriptor.id,
        name=descriptor.name,
        suite=descriptor.suite,
        project=descriptor.project,
        framework=descriptor.framework,
        test_type=descriptor.test_type,
        tags=tags,
        file=descriptor.file,
        line=descriptor.line,
        environment=descriptor.environment,
    ))
    marker_resources, marker_safety = _resource_policy(tags)
    required_tools = _tuple([*(classification.required_tools if classification else ()), *marker_meta.get("required_tools", ())])
    required_capabilities = _tuple([*(classification.required_capabilities if classification else ()), *marker_meta.get("required_capabilities", ())])
    lanes = classification.lanes if classification else (fallback_lane,)
    resources = _tuple([
        *(classification.resources if classification else ()),
        *marker_resources,
        *(f"requires-tool:{tool}" for tool in required_tools),
        *(f"requires-host-capability:{cap}" for cap in required_capabilities),
    ])
    safety = classification.parallel_safety if classification else marker_safety
    # Explicit per-test markers can tighten a central classification but never
    # silently loosen a serial rule into parallel-safe.
    if marker_safety != "parallel-safe":
        safety = marker_safety
    explicit_check = str(marker_meta.get("check_id") or "").strip()
    check_id = explicit_check or ("pytest:" + descriptor.id)
    return TestSpec(
        id=check_id,
        name=descriptor.name,
        runner="pytest",
        selector=descriptor.id,
        behavior=str(marker_meta.get("behavior") or (classification.behavior if classification else "") or ("selector-derived" if not explicit_check else "pytest-explicit")),
        lanes=lanes,
        resources=resources,
        parallel_safety=safety,
        required_tools=required_tools,
        required_capabilities=required_capabilities,
        budget_class=str(marker_meta.get("budget_class") or (classification.budget_class if classification else "") or lanes[0]),
        contract_level=str(marker_meta.get("contract_level") or (classification.contract_level if classification else "white-box")),
        groups=_tuple(marker_meta.get("groups")),
        dependencies=_tuple(marker_meta.get("dependencies")),
        metadata={
            "selector_derived": not bool(explicit_check),
            "file": descriptor.file,
            "line": descriptor.line,
            "tags": list(tags),
            "identity_source": "pytest-marker" if explicit_check else "selector-fallback",
            "classification_rule": classification.id if classification else "",
        },
    )


def _load_manifest(root: Path, path: Path) -> tuple[list[TestSpec], list[TestCheckGroup], list[TestClassificationRule]]:
    if not path.is_file():
        return [], [], []
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    if int(raw.get("schema_version", 1)) != 1:
        raise ValueError(f"unsupported test inventory schema in {path}")
    specs: list[TestSpec] = []
    for row in raw.get("checks", []):
        if not isinstance(row, dict):
            raise ValueError("[[checks]] entries must be tables")
        check_id = str(row.get("id") or "").strip()
        selector = str(row.get("selector") or "").strip()
        if not check_id or not selector:
            raise ValueError("each [[checks]] entry requires id and selector")
        specs.append(TestSpec(
            id=check_id,
            name=str(row.get("name") or check_id),
            runner=str(row.get("runner") or "pytest"),
            selector=selector,
            aliases=_tuple(row.get("aliases")),
            behavior=str(row.get("behavior") or "").strip(),
            lanes=_tuple(row.get("lanes") or ("fast",)),
            resources=_tuple(row.get("resources")),
            parallel_safety=str(row.get("parallel_safety") or "serial-process-global"),
            required_tools=_tuple(row.get("required_tools")),
            required_capabilities=_tuple(row.get("required_capabilities")),
            timeout_seconds=float(row["timeout_seconds"]) if row.get("timeout_seconds") is not None else None,
            budget_class=str(row.get("budget_class") or "default"),
            contract_level=str(row.get("contract_level") or "public-contract"),
            groups=_tuple(row.get("groups")),
            dependencies=_tuple(row.get("dependencies")),
            metadata={
                "selector_derived": False,
                "migration": dict(row.get("migration") or {}),
                "inventory_file": path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path),
            },
        ))
    groups = [
        TestCheckGroup(
            id=str(row.get("id") or ""),
            checks=_tuple(row.get("checks")),
            description=str(row.get("description") or ""),
            aliases=_tuple(row.get("aliases")),
        )
        for row in raw.get("groups", [])
        if isinstance(row, dict)
    ]
    rules = [
        TestClassificationRule(
            id=str(row.get("id") or ""),
            pattern=str(row.get("pattern") or ""),
            lanes=_tuple(row.get("lanes")),
            resources=_tuple(row.get("resources")),
            parallel_safety=str(row.get("parallel_safety") or "serial-process-global"),
            required_tools=_tuple(row.get("required_tools")),
            required_capabilities=_tuple(row.get("required_capabilities")),
            budget_class=str(row.get("budget_class") or "default"),
            contract_level=str(row.get("contract_level") or "white-box"),
            behavior=str(row.get("behavior") or ""),
        )
        for row in raw.get("classifications", [])
        if isinstance(row, dict)
    ]
    return specs, groups, rules


@lru_cache(maxsize=16)
def _registered_specs(root_text: str) -> tuple[TestSpec, ...]:
    root = Path(root_text).expanduser().resolve()
    specs, _groups, _rules = _load_manifest(root, root / DEFAULT_INVENTORY_FILE)
    return tuple(specs)


@lru_cache(maxsize=16)
def _registered_rules(root_text: str) -> tuple[TestClassificationRule, ...]:
    root = Path(root_text).expanduser().resolve()
    _specs, _groups, rules = _load_manifest(root, root / DEFAULT_INVENTORY_FILE)
    return tuple(rules)


def policy_for_pytest_item(*, root: Path, nodeid: str, marker_names: Iterable[str], path: str) -> dict[str, Any]:
    """Resolve durable/rule/fallback policy for one collected pytest item."""

    root = Path(root).expanduser().resolve()
    normalized_node = str(nodeid).replace("\\", "/")
    try:
        relative_path = Path(path).expanduser().resolve().relative_to(root).as_posix()
    except ValueError:
        relative_path = str(path).replace("\\", "/")
    matched: list[TestSpec] = []
    for spec in _registered_specs(str(root)):
        selector = spec.selector.replace("\\", "/").rstrip("/")
        if normalized_node == selector or normalized_node.startswith(selector + "::") or relative_path == selector:
            matched.append(spec)
    if matched:
        return {
            "source": "durable-check",
            "check_ids": [spec.id for spec in matched],
            "lanes": list(_tuple(lane for spec in matched for lane in spec.lanes if lane != "full")),
            "resources": list(_tuple(resource for spec in matched for resource in spec.resources)),
            "parallel_safety": matched[0].parallel_safety,
            "required_tools": list(_tuple(tool for spec in matched for tool in spec.required_tools)),
            "required_capabilities": list(_tuple(cap for spec in matched for cap in spec.required_capabilities)),
        }
    pseudo = TestDescriptor(
        id=normalized_node,
        name=normalized_node.rsplit("::", 1)[-1],
        suite="pytest",
        project=str(root),
        framework="pytest",
        tags=_tuple(marker_names),
        file=relative_path,
    )
    rule = _matching_rule(_registered_rules(str(root)), pseudo)
    marker_resources, marker_safety = _resource_policy(marker_names)
    if rule:
        return {
            "source": f"classification:{rule.id}",
            "check_ids": [],
            "lanes": [lane for lane in rule.lanes if lane != "full"],
            "resources": list(_tuple([*rule.resources, *marker_resources])),
            "parallel_safety": marker_safety if marker_safety != "parallel-safe" else rule.parallel_safety,
            "required_tools": list(rule.required_tools),
            "required_capabilities": list(rule.required_capabilities),
        }
    lane = lane_for_pytest_item(marker_names=marker_names, path=relative_path)
    return {
        "source": "fallback",
        "check_ids": [],
        "lanes": [lane],
        "resources": list(marker_resources),
        "parallel_safety": marker_safety,
        "required_tools": [],
        "required_capabilities": [],
    }


def lanes_for_pytest_item(*, root: Path, nodeid: str, marker_names: Iterable[str], path: str) -> tuple[str, ...]:
    return _tuple(policy_for_pytest_item(root=root, nodeid=nodeid, marker_names=marker_names, path=path).get("lanes", ()))


def compile_pytest_inventory(root: Path, *, manifest_path: Path | None = None) -> TestInventory:
    root = Path(root).expanduser().resolve()
    path = Path(manifest_path) if manifest_path else root / DEFAULT_INVENTORY_FILE
    explicit, groups, rules = _load_manifest(root, path)
    descriptors = discover_python_tests(root)
    source_metadata = _pytest_source_metadata(root)
    covered: set[str] = set()
    for spec in explicit:
        selector = spec.selector.rstrip("/")
        for descriptor in descriptors:
            if descriptor.id == selector or descriptor.id.startswith(selector + "::") or descriptor.file == selector:
                covered.add(descriptor.id)
    synthetic = [
        _synthetic_spec(item, source_metadata.get(item.id), _matching_rule(rules, item))
        for item in descriptors
        if item.id not in covered
    ]
    return TestInventory(
        tuple([*explicit, *synthetic]),
        tuple(groups),
        source=str(path if path.is_file() else "source-discovery"),
        classification_rules=tuple(rules),
    )


def _tool_version(name: str, path: str) -> str:
    probes = {
        "java": [path, "-version"],
        "javac": [path, "-version"],
        "keytool": [path, "-help"],
        "gradle": [path, "--version"],
        "go": [path, "version"],
        "git": [path, "--version"],
    }
    argv = probes.get(name, [path, "--version"])
    try:
        proc = subprocess.run(argv, text=True, capture_output=True, check=False, timeout=8)
    except Exception:
        return ""
    text = "\n".join(part.strip() for part in (proc.stdout, proc.stderr) if part.strip())
    return text.splitlines()[0][:300] if text else ""


def _python_requirement_satisfied(requirement: str) -> bool:
    """Evaluate the small interpreter contract used by test inventory manifests.

    TEST-X02 intentionally avoids adding a packaging dependency just to qualify
    the host. The supported contract is a comma-separated set of ordinary
    Python version comparisons (>=, >, <=, <, ==) against major/minor[/patch].
    """

    text = str(requirement or "").strip()
    if not text:
        return True
    current = tuple(sys.version_info[:3])
    for raw_clause in text.split(","):
        clause = raw_clause.strip()
        match = re.fullmatch(r"(>=|<=|==|>|<)\s*(\d+(?:\.\d+){0,2})", clause)
        if match is None:
            return False
        op, version_text = match.groups()
        parts = tuple(int(part) for part in version_text.split("."))
        wanted = parts + (0,) * (3 - len(parts))
        checks = {
            ">=": current >= wanted,
            ">": current > wanted,
            "<=": current <= wanted,
            "<": current < wanted,
            "==": current == wanted,
        }
        if not checks[op]:
            return False
    return True


def _test_environment_contract(inventory: TestInventory) -> dict[str, Any]:
    source = Path(str(inventory.source))
    if not source.is_file():
        return {"declared": False, "ready": False, "reason": "inventory manifest unavailable"}
    try:
        raw = tomllib.loads(source.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return {"declared": False, "ready": False, "reason": str(exc)}
    row = raw.get("environment") if isinstance(raw.get("environment"), dict) else {}
    manager = str(row.get("manager") or "").strip()
    lock_name = str(row.get("requirements_lock") or "").strip()
    python_requirement = str(row.get("python") or "").strip()
    if not manager or not lock_name:
        return {"declared": False, "ready": False, "manager": manager, "requirements_lock": lock_name, "python": python_requirement}
    lock_path = (source.parent / lock_name).resolve()
    exists = lock_path.is_file()
    digest = hashlib.sha256(lock_path.read_bytes()).hexdigest() if exists else ""
    manager_path = shutil.which(manager) or ""
    python_satisfied = _python_requirement_satisfied(python_requirement)
    return {
        "declared": True,
        "ready": bool(exists and manager_path and python_satisfied),
        "manager": manager,
        "manager_available": bool(manager_path),
        "manager_path": manager_path,
        "requirements_lock": lock_name,
        "requirements_lock_sha256": digest,
        "requirements_lock_exists": exists,
        "python": python_requirement,
        "python_satisfied": python_satisfied,
    }


def _host_capabilities(environ: Mapping[str, str] | None = None) -> set[str]:
    env = dict(os.environ if environ is None else environ)
    caps = {platform.system().lower()}
    if os.name == "posix":
        caps.add("posix")
    if env.get("TERMUX_VERSION") or "/com.termux/" in str(Path(sys.executable)):
        caps.add("termux")
    if platform.machine():
        caps.add("arch:" + platform.machine().lower())
    return caps


def qualify_selection_environment(
    selection: TestSelection,
    inventory: TestInventory,
    *,
    environ: Mapping[str, str] | None = None,
    probe_versions: bool = True,
) -> TestEnvironmentQualification:
    """Qualify exact external tools/capabilities for an immutable selection."""

    selected = {spec.id: spec for spec in inventory.specs if spec.id in set(selection.resolved_check_ids)}
    required_tools = sorted({tool for spec in selected.values() for tool in spec.required_tools})
    required_capabilities = tuple(sorted({cap for spec in selected.values() for cap in spec.required_capabilities}))
    env = dict(os.environ if environ is None else environ)
    tool_rows: list[ToolQualification] = []
    for name in required_tools:
        path = shutil.which(name, path=env.get("PATH")) or ""
        tool_rows.append(ToolQualification(name, bool(path), path, _tool_version(name, path) if path and probe_versions else ""))
    available_caps = _host_capabilities(env)
    missing_tools = tuple(tool.name for tool in tool_rows if not tool.available)
    missing_caps = tuple(cap for cap in required_capabilities if cap.casefold() not in {item.casefold() for item in available_caps})
    environment_contract = _test_environment_contract(inventory)
    host = {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "capabilities": sorted(available_caps),
        "test_environment": environment_contract,
    }
    payload = {
        "selection_id": selection.selection_id,
        "host": host,
        "tools": [tool.to_dict() for tool in tool_rows],
        "required_capabilities": list(required_capabilities),
        "missing_tools": list(missing_tools),
        "missing_capabilities": list(missing_caps),
    }
    return TestEnvironmentQualification(
        1,
        "tenv-" + _sha_payload(payload)[:24],
        selection.selection_id,
        not missing_tools and not missing_caps and bool(environment_contract.get("ready")),
        host,
        tuple(tool_rows),
        required_capabilities,
        missing_tools,
        missing_caps,
    )

def with_shard_plan(selection: TestSelection, inventory: TestInventory, shard_count: int) -> TestSelection:
    count = int(shard_count)
    if count < 1:
        raise ValueError("shard_count must be >= 1")
    specs = {spec.id: spec for spec in inventory.specs}
    buckets: list[list[str]] = [[] for _ in range(count)]
    # Stable hash assignment is order-independent and keeps the same check on the
    # same shard when unrelated checks are added before it in lexical order.
    for check_id in selection.resolved_check_ids:
        index = int(hashlib.sha256(check_id.encode("utf-8")).hexdigest()[:16], 16) % count
        buckets[index].append(check_id)
    shards: list[TestShard] = []
    for index, check_ids in enumerate(buckets):
        ordered = tuple(sorted(check_ids))
        nodes = tuple(dict.fromkeys(specs[check].selector for check in ordered if specs[check].selector))
        shard_id = f"{selection.selection_id}/shard-{index + 1:02d}-of-{count:02d}"
        shards.append(TestShard(index=index, shard_id=shard_id, check_ids=ordered, pytest_nodes=nodes))
    payload = {
        "selection_id": selection.selection_id,
        "shard_count": count,
        "shards": [{"index": shard.index, "check_ids": list(shard.check_ids)} for shard in shards],
    }
    plan = TestShardPlan(1, "tshard-" + _sha_payload(payload)[:24], selection.selection_id, count, tuple(shards))
    complete, detail = plan.verify_complete(selection.resolved_check_ids)
    if not complete:
        raise ValueError(f"invalid deterministic shard plan: {detail}")
    return TestSelection(
        schema_version=selection.schema_version,
        selection_id=selection.selection_id,
        requested_lanes=selection.requested_lanes,
        requested_checks=selection.requested_checks,
        requested_groups=selection.requested_groups,
        resolved_check_ids=selection.resolved_check_ids,
        resolved_pytest_nodes=selection.resolved_pytest_nodes,
        exclusions=selection.exclusions,
        resources=selection.resources,
        serial_constraints=selection.serial_constraints,
        cfg_provenance=selection.cfg_provenance,
        shard_plan=plan,
    )


def render_inventory_text(inventory: TestInventory, *, lane: str = "", group: str = "") -> str:
    selection = inventory.select(lanes=(lane,) if lane else (), groups=(group,) if group else ())
    selected = set(selection.resolved_check_ids)
    lines = [f"Test inventory: {len(selected)} selected / {len(inventory.specs)} checks", f"digest: {inventory.digest}"]
    for spec in inventory.specs:
        if spec.id not in selected:
            continue
        identity = "durable" if not spec.metadata.get("selector_derived") else "selector-derived"
        lines.append(f"- {spec.id} [{','.join(spec.lanes) or '-'}] {spec.selector} ({identity}; {spec.parallel_safety})")
    return "\n".join(lines)


def render_selection_text(selection: TestSelection) -> str:
    lines = [
        f"Test selection: {selection.selection_id}",
        f"checks: {len(selection.resolved_check_ids)}",
        "lanes: " + (", ".join(selection.requested_lanes) or "all"),
        "resources: " + (", ".join(selection.resources) or "none"),
        "serial constraints: " + (", ".join(selection.serial_constraints) or "none"),
    ]
    if selection.shard_plan:
        lines.append(f"shards: {selection.shard_plan.shard_count} ({selection.shard_plan.plan_id})")
        for shard in selection.shard_plan.shards:
            lines.append(f"  {shard.shard_id}: {len(shard.check_ids)} check(s)")
    for check_id, node in zip(selection.resolved_check_ids, selection.resolved_pytest_nodes):
        lines.append(f"- {check_id} -> {node}")
    return "\n".join(lines)
