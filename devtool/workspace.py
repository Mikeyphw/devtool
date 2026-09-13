from __future__ import annotations

"""Workspace target-group primitives for MP06.

Workspace groups are configuration-time aliases for ordered sets of named
Devtool targets. Groups may include targets, ``@other-group`` references, or
``*`` for every configured target. Expansion is deterministic and rejects
cycles/unknown members before execution starts.
"""

from dataclasses import dataclass
import re
from typing import Iterable, Mapping

from .models import TargetConfig, TargetGroup, WorkflowSchedulerConfig, WorkspaceConfig


class WorkspaceConfigError(ValueError):
    """Raised when workspace/group configuration is invalid."""


_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_group_name(name: str) -> None:
    if not _NAME_RE.fullmatch(name):
        raise WorkspaceConfigError(
            f"Invalid workspace group name {name!r}. Use letters, numbers, '.', '_' or '-', "
            "start with a letter/number, and do not include @, group:, '/', or '*'."
        )
    if name.startswith("group:") or name.startswith("@") or name == "*":
        raise WorkspaceConfigError(f"Invalid workspace group name {name!r}; names cannot be references or wildcards.")


def _normalize_members(value: object, *, group: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise WorkspaceConfigError(f"workspace group '{group}' must be a TOML array of target/group names")
    members: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise WorkspaceConfigError(
                f"workspace.groups.{group}[{index}] must be a non-empty target name, @group, or '*'"
            )
        members.append(item.strip())
    if not members:
        raise WorkspaceConfigError(f"workspace group '{group}' cannot be empty")
    return tuple(members)


def load_workspace_config(
    section: object,
    targets: Mapping[str, TargetConfig],
    *,
    legacy_default_target: str = "",
) -> WorkspaceConfig:
    if section is None:
        section = {}
    if not isinstance(section, dict):
        raise WorkspaceConfigError("[workspace] must be a TOML table")

    canonical_default = str(section.get("default_target", "") or "").strip()
    legacy_default = str(legacy_default_target or "").strip()
    if canonical_default and legacy_default and canonical_default != legacy_default:
        raise WorkspaceConfigError(
            f"[workspace].default_target={canonical_default!r} conflicts with legacy "
            f"[project].default_target={legacy_default!r}; keep one value or make them equal"
        )
    default_target = canonical_default or legacy_default
    if default_target and default_target not in targets:
        available = ", ".join(sorted(targets)) or "<none>"
        raise WorkspaceConfigError(
            f"Configured default target '{default_target}' does not exist. Available targets: {available}"
        )

    raw_groups = section.get("groups", {})
    if raw_groups is None:
        raw_groups = {}
    if not isinstance(raw_groups, dict):
        raise WorkspaceConfigError("[workspace.groups] must be a TOML table")

    groups: dict[str, TargetGroup] = {}
    seen_group_names: set[str] = set()
    for raw_name, raw_members in raw_groups.items():
        if not isinstance(raw_name, str):
            raise WorkspaceConfigError(f"workspace group name {raw_name!r} must be a string")
        name = raw_name.strip()
        if not name:
            raise WorkspaceConfigError("workspace group names cannot be empty")
        _validate_group_name(name)
        if name in seen_group_names:
            raise WorkspaceConfigError(f"Duplicate workspace group name after trimming: {name!r}")
        seen_group_names.add(name)
        if name in targets:
            raise WorkspaceConfigError(
                f"workspace group '{name}' conflicts with a target of the same name; choose a distinct group name"
            )
        groups[name] = TargetGroup(name=name, members=_normalize_members(raw_members, group=name))

    raw_scheduler = section.get("scheduler", {})
    if raw_scheduler is None:
        raw_scheduler = {}
    if not isinstance(raw_scheduler, dict):
        raise WorkspaceConfigError("[workspace.scheduler] must be a TOML table")

    def _non_negative_int(name: str, default: int) -> int:
        raw = raw_scheduler.get(name, default)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            raise WorkspaceConfigError(f"workspace.scheduler.{name} must be a non-negative integer")
        return int(raw)

    def _bool(name: str, default: bool) -> bool:
        raw = raw_scheduler.get(name, default)
        if not isinstance(raw, bool):
            raise WorkspaceConfigError(f"workspace.scheduler.{name} must be true or false")
        return raw

    scheduler = WorkflowSchedulerConfig(
        parallel=_bool("parallel", False),
        max_parallel_nodes=_non_negative_int("max_parallel_nodes", 0),
        max_gradle_nodes=_non_negative_int("max_gradle_nodes", 1),
        max_dotnet_nodes=_non_negative_int("max_dotnet_nodes", 1),
    )
    workspace = WorkspaceConfig(default_target=default_target, groups=groups, scheduler=scheduler)
    # Force complete validation, including nested groups and cycles.
    for name in groups:
        expand_workspace_group(workspace, targets, name)
    return workspace


def expand_workspace_group(
    workspace: WorkspaceConfig,
    targets: Mapping[str, TargetConfig],
    name: str,
) -> tuple[str, ...]:
    group_name = str(name or "").strip()
    if group_name not in workspace.groups:
        available = ", ".join(sorted(workspace.groups)) or "<none>"
        raise WorkspaceConfigError(f"Unknown workspace group '{group_name}'. Available groups: {available}")

    result: list[str] = []
    seen_targets: set[str] = set()
    stack: list[str] = []

    def add_target(target_name: str) -> None:
        if target_name not in targets:
            available = ", ".join(sorted(targets)) or "<none>"
            chain = " -> ".join(stack) if stack else group_name
            raise WorkspaceConfigError(
                f"workspace group '{group_name}' references unknown target '{target_name}' via {chain}. "
                f"Available targets: {available}"
            )
        if target_name not in seen_targets:
            seen_targets.add(target_name)
            result.append(target_name)

    def visit(current: str) -> None:
        if current in stack:
            start = stack.index(current)
            cycle = [*stack[start:], current]
            raise WorkspaceConfigError(f"workspace group cycle detected: {' -> '.join(cycle)}")
        group = workspace.groups.get(current)
        if group is None:
            available = ", ".join(sorted(workspace.groups)) or "<none>"
            raise WorkspaceConfigError(f"Unknown workspace group '{current}'. Available groups: {available}")
        stack.append(current)
        try:
            for member in group.members:
                if member == "*":
                    for target_name in targets:
                        add_target(target_name)
                elif member.startswith("@"):
                    nested = member[1:].strip()
                    if not nested:
                        raise WorkspaceConfigError(
                            f"workspace group '{current}' contains an empty @group reference"
                        )
                    visit(nested)
                elif member.startswith("group:"):
                    nested = member.split(":", 1)[1].strip()
                    if not nested:
                        raise WorkspaceConfigError(
                            f"workspace group '{current}' contains an empty group: reference"
                        )
                    visit(nested)
                else:
                    add_target(member)
        finally:
            stack.pop()

    visit(group_name)
    return tuple(result)


def resolve_workspace_targets(
    workspace: WorkspaceConfig,
    targets: Mapping[str, TargetConfig],
    *,
    groups: Iterable[str] = (),
    all_targets: bool = False,
) -> tuple[str, ...]:
    if all_targets:
        return tuple(targets)
    ordered: list[str] = []
    seen: set[str] = set()
    for raw_name in groups:
        name = str(raw_name or "").strip()
        if not name:
            continue
        for target in expand_workspace_group(workspace, targets, name):
            if target not in seen:
                seen.add(target)
                ordered.append(target)
    return tuple(ordered)


def workspace_group_memberships(
    workspace: WorkspaceConfig,
    targets: Mapping[str, TargetConfig],
) -> dict[str, tuple[str, ...]]:
    memberships: dict[str, list[str]] = {name: [] for name in targets}
    for group_name in workspace.groups:
        for target in expand_workspace_group(workspace, targets, group_name):
            memberships.setdefault(target, []).append(group_name)
    return {name: tuple(values) for name, values in memberships.items()}


def workspace_as_dict(workspace: WorkspaceConfig, targets: Mapping[str, TargetConfig]) -> dict[str, object]:
    return {
        "default_target": workspace.default_target,
        "scheduler": {
            "parallel": workspace.scheduler.parallel,
            "max_parallel_nodes": workspace.scheduler.max_parallel_nodes,
            "max_gradle_nodes": workspace.scheduler.max_gradle_nodes,
            "max_dotnet_nodes": workspace.scheduler.max_dotnet_nodes,
        },
        "groups": {
            name: {
                "members": list(group.members),
                "targets": list(expand_workspace_group(workspace, targets, name)),
            }
            for name, group in sorted(workspace.groups.items())
        },
    }
