from __future__ import annotations

"""Repository-owned federation configuration contract.

FRX-02 parses and validates explicit repository enrollment only.  It does not
perform child repository discovery, load child Devtool configs, build
cross-repository graphs, or execute child work.  Those capabilities belong to
FRX-03 and later overlays.
"""

from pathlib import Path
import re
import tomllib
from typing import Any, Mapping

from .models import (
    FederationCapabilityRequirement, FederationConfig, FederationGroup, FederatedRepositoryConfig,
    FederatedWorkflowConfig, FederatedWorkflowStep,
)

FEDERATION_SCHEMA_VERSION = 1
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_FEDERATION_KEYS = frozenset({"schema", "repositories", "groups", "workflows"})
_REPOSITORY_KEYS = frozenset({"path", "required", "exports", "requires"})
_GROUP_KEYS = frozenset({"repositories", "members"})
_WORKFLOW_KEYS = frozenset({"depends_on", "steps"})
_WORKFLOW_STEP_KEYS = frozenset({"id", "ref", "depends_on"})
_CAPABILITY_TABLE_KEYS = frozenset({"capabilities"})
_CAPABILITY_REQUIREMENT_KEYS = frozenset({"name", "optional", "compatible", "policy"})
_CAPABILITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*/v[0-9]+$")


class FederationConfigError(ValueError):
    """Raised when a repository-owned federation contract is invalid."""


def _fail(message: str) -> FederationConfigError:
    return FederationConfigError(message)


def _table(value: object, *, path: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _fail(f"{path} must be a TOML table")
    return dict(value)


def _reject_unknown(raw: Mapping[str, Any], allowed: frozenset[str], *, path: str) -> None:
    unknown = sorted(str(key) for key in raw if str(key) not in allowed)
    if unknown:
        raise _fail(f"{path} contains unknown field(s): {', '.join(unknown)}")


def _name(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(f"{path} must be a non-empty name")
    name = value.strip()
    if not _NAME_RE.fullmatch(name):
        raise _fail(
            f"Invalid {path} {name!r}. Use letters, numbers, '.', '_' or '-', "
            "and start with a letter/number"
        )
    if name in {".", ".."}:
        raise _fail(f"Invalid {path} {name!r}")
    return name


def _canonical(base: Path, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = base / path
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise _fail(f"Cannot canonicalize federation repository path {raw_path!r}: {exc}") from exc


def _direct_back_reference(child_root: Path, owner_root: Path) -> bool:
    """Best-effort FRX-02 guard for the obvious A -> B -> A config cycle.

    This intentionally reads only the standard child ``.devtool.toml`` and only
    enough of ``[federation.repositories]`` to recognize a direct path back to
    the owner.  Full child config discovery/ownership remains FRX-03.
    """

    child_config = child_root / ".devtool.toml"
    if not child_config.is_file():
        return False
    try:
        parsed = tomllib.loads(child_config.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return False
    federation = parsed.get("federation")
    if not isinstance(federation, dict):
        return False
    repositories = federation.get("repositories")
    if not isinstance(repositories, dict):
        return False
    for raw in repositories.values():
        if not isinstance(raw, dict):
            continue
        raw_path = raw.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        try:
            target = _canonical(child_root, raw_path.strip())
        except FederationConfigError:
            continue
        if target == owner_root:
            return True
    return False


def _bool(value: object, *, path: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise _fail(f"{path} must be true or false")
    return value




def _capability_name(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(f"{path} must be a non-empty versioned capability identifier")
    name = value.strip()
    if not _CAPABILITY_RE.fullmatch(name):
        raise _fail(
            f"{path} must be a versioned capability identifier such as 'securefs/v1'; got {name!r}"
        )
    return name


def _capability_exports(value: object, *, path: str) -> tuple[str, ...]:
    table = _table(value, path=path)
    if not table:
        return ()
    _reject_unknown(table, _CAPABILITY_TABLE_KEYS, path=path)
    raw = table.get("capabilities", [])
    if not isinstance(raw, list):
        raise _fail(f"{path}.capabilities must be an array")
    result: list[str] = []
    for index, item in enumerate(raw):
        name = _capability_name(item, path=f"{path}.capabilities[{index}]")
        if name in result:
            raise _fail(f"{path}.capabilities contains duplicate capability {name!r}")
        result.append(name)
    return tuple(result)


def _capability_requires(value: object, *, path: str) -> tuple[FederationCapabilityRequirement, ...]:
    table = _table(value, path=path)
    if not table:
        return ()
    _reject_unknown(table, _CAPABILITY_TABLE_KEYS, path=path)
    raw = table.get("capabilities", [])
    if not isinstance(raw, list):
        raise _fail(f"{path}.capabilities must be an array")
    result: list[FederationCapabilityRequirement] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        item_path = f"{path}.capabilities[{index}]"
        if isinstance(item, str):
            req = FederationCapabilityRequirement(name=_capability_name(item, path=item_path))
        elif isinstance(item, dict):
            row = dict(item)
            _reject_unknown(row, _CAPABILITY_REQUIREMENT_KEYS, path=item_path)
            name = _capability_name(row.get("name"), path=f"{item_path}.name")
            optional = _bool(row.get("optional"), path=f"{item_path}.optional", default=False)
            compatible_raw = row.get("compatible", [])
            if not isinstance(compatible_raw, list):
                raise _fail(f"{item_path}.compatible must be an array")
            compatible: list[str] = []
            for compat_index, compat in enumerate(compatible_raw):
                candidate = _capability_name(compat, path=f"{item_path}.compatible[{compat_index}]")
                if candidate != name and candidate not in compatible:
                    compatible.append(candidate)
            policy = str(row.get("policy") or ("explicit-list" if compatible else "exact")).strip()
            if policy not in {"exact", "explicit-list"}:
                raise _fail(f"{item_path}.policy must be 'exact' or 'explicit-list'")
            if compatible and policy != "explicit-list":
                raise _fail(f"{item_path}.compatible requires policy='explicit-list'")
            req = FederationCapabilityRequirement(
                name=name, optional=optional, compatible=tuple(compatible), policy=policy
            )
        else:
            raise _fail(f"{item_path} must be a capability string or inline table")
        if req.name in seen:
            raise _fail(f"{path}.capabilities contains duplicate requirement {req.name!r}")
        seen.add(req.name)
        result.append(req)
    return tuple(result)


def _repo_members(value: object, *, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _fail(f"{path} must be a TOML array of federation repository names")
    if not value:
        raise _fail(f"{path} cannot be empty")
    result: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        name = _name(raw, path=f"{path}[{index}]")
        if name in seen:
            raise _fail(f"{path} contains duplicate repository {name!r}")
        seen.add(name)
        result.append(name)
    return tuple(result)




def _group_members(value: object, *, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _fail(f"{path} must be a TOML array of federation workflow/group/ref members")
    if not value:
        raise _fail(f"{path} cannot be empty")
    result: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, str) or not raw.strip():
            raise _fail(
                f"{path}[{index}] must be a non-empty federation workflow name, group reference, "
                "or repository::workflow / repository::target::workflow reference"
            )
        member = raw.strip()
        if member in seen:
            raise _fail(f"{path} contains duplicate member {member!r}")
        seen.add(member)
        result.append(member)
    return tuple(result)


def _normalize_group_members(
    groups: Mapping[str, FederationGroup],
    workflows: Mapping[str, FederatedWorkflowConfig],
    repositories: Mapping[str, FederatedRepositoryConfig],
) -> dict[str, FederationGroup]:
    """Resolve executable group members to explicit stable member kinds.

    Normalized forms are ``workflow:<name>``, ``group:<name>``, and canonical
    child workflow references. Bare names are accepted only when they resolve
    unambiguously to exactly one federation workflow or federation group.
    """

    normalized: dict[str, FederationGroup] = {}
    for group_name, group in groups.items():
        members: list[str] = []
        seen: set[str] = set()
        for index, raw_member in enumerate(group.members):
            path = f"federation.groups.{group_name}.members[{index}]"
            member = str(raw_member or "").strip()
            resolved = ""
            if "::" in member:
                ref, _repository, _target, _workflow = _workflow_ref(
                    member, path=path, repositories=repositories
                )
                resolved = ref
            elif member.startswith("workflow:"):
                name = _name(member.split(":", 1)[1], path=f"{path} workflow")
                if name not in workflows:
                    available = ", ".join(sorted(workflows)) or "<none>"
                    raise _fail(
                        f"{path} references unknown federation workflow {name!r}. "
                        f"Available workflows: {available}"
                    )
                resolved = f"workflow:{name}"
            elif member.startswith("group:") or member.startswith("@"):
                raw_name = member.split(":", 1)[1] if member.startswith("group:") else member[1:]
                name = _name(raw_name, path=f"{path} group")
                if name not in groups:
                    available = ", ".join(sorted(groups)) or "<none>"
                    raise _fail(
                        f"{path} references unknown federation group {name!r}. "
                        f"Available groups: {available}"
                    )
                resolved = f"group:{name}"
            else:
                name = _name(member, path=f"{path} member")
                is_workflow = name in workflows
                is_group = name in groups
                if is_workflow and is_group:
                    raise _fail(
                        f"{path} member {name!r} is ambiguous because both a federation workflow and group "
                        f"use that name. Use workflow:{name} or group:{name}."
                    )
                if is_workflow:
                    resolved = f"workflow:{name}"
                elif is_group:
                    resolved = f"group:{name}"
                else:
                    available_workflows = ", ".join(sorted(workflows)) or "<none>"
                    available_groups = ", ".join(sorted(groups)) or "<none>"
                    raise _fail(
                        f"{path} references unknown federation workflow/group {name!r}. "
                        f"Available workflows: {available_workflows}; groups: {available_groups}"
                    )
            if resolved in seen:
                raise _fail(
                    f"federation group {group_name!r} resolves duplicate executable member {resolved!r}"
                )
            seen.add(resolved)
            members.append(resolved)
        normalized[group_name] = FederationGroup(
            name=group.name, repositories=group.repositories, members=tuple(members)
        )

    # Validate nested-group cycles at configuration time. Metadata-only groups
    # have no executable members and therefore cannot participate in a cycle.
    stack: list[str] = []
    complete: set[str] = set()

    def visit(name: str) -> None:
        if name in complete:
            return
        if name in stack:
            start = stack.index(name)
            cycle = [*stack[start:], name]
            raise _fail(f"federation group cycle detected: {' -> '.join(cycle)}")
        stack.append(name)
        try:
            for member in normalized[name].members:
                if member.startswith("group:"):
                    nested_name = member.split(":", 1)[1]
                    if not normalized[nested_name].members:
                        raise _fail(
                            f"executable federation group {name!r} references metadata-only group {nested_name!r}; "
                            "add executable members to the nested group"
                        )
                    visit(nested_name)
        finally:
            stack.pop()
        complete.add(name)

    for name in normalized:
        visit(name)
    return normalized

def _workflow_ref(
    value: object,
    *,
    path: str,
    repositories: Mapping[str, FederatedRepositoryConfig],
) -> tuple[str, str, str, str]:
    """Parse a federation child-workflow reference.

    Backward-compatible references use ``repository::workflow`` and defer
    target resolution to the child repository's selected/default target.
    Target-qualified references use ``repository::target::workflow`` and
    select that target explicitly.  Parsing remains repository-local: target
    existence is verified by the FRX planner when the child config is loaded.
    """

    if not isinstance(value, str) or not value.strip():
        raise _fail(
            f"{path} must be a non-empty repository::workflow or "
            "repository::target::workflow reference"
        )
    raw = value.strip()
    parts = [part.strip() for part in raw.split("::")]
    if len(parts) not in {2, 3}:
        raise _fail(
            f"{path} must use repository::workflow or repository::target::workflow syntax, "
            f"got {raw!r}"
        )
    if any(not part for part in parts):
        raise _fail(
            f"{path} must use non-empty repository, target (when present), and workflow names, "
            f"got {raw!r}"
        )

    repository = _name(parts[0], path=f"{path} repository")
    if len(parts) == 2:
        target = ""
        workflow = _name(parts[1], path=f"{path} workflow")
        normalized = f"{repository}::{workflow}"
    else:
        target = _name(parts[1], path=f"{path} target")
        workflow = _name(parts[2], path=f"{path} workflow")
        normalized = f"{repository}::{target}::{workflow}"

    if repository not in repositories:
        available = ", ".join(sorted(repositories)) or "<none>"
        raise _fail(
            f"{path} references unknown federation repository {repository!r}. "
            f"Available repositories: {available}"
        )
    return normalized, repository, target, workflow


def _workflow_steps(name: str, raw: Mapping[str, Any], repositories: Mapping[str, FederatedRepositoryConfig]) -> tuple[FederatedWorkflowStep, ...]:
    _reject_unknown(raw, _WORKFLOW_KEYS, path=f"[federation.workflows.{name}]")
    shorthand = raw.get("depends_on")
    structured = raw.get("steps")
    if shorthand is not None and structured is not None:
        raise _fail(f"federation workflow {name!r} cannot combine depends_on and steps")
    steps: list[FederatedWorkflowStep] = []
    if shorthand is not None:
        if not isinstance(shorthand, list) or not shorthand:
            raise _fail(f"federation.workflows.{name}.depends_on must be a non-empty array")
        for index, value in enumerate(shorthand, 1):
            ref, repository, target, workflow = _workflow_ref(
                value,
                path=f"federation.workflows.{name}.depends_on[{index - 1}]",
                repositories=repositories,
            )
            step_id = f"step-{index:02d}"
            steps.append(FederatedWorkflowStep(
                step_id=step_id,
                ref=ref,
                repository=repository,
                workflow=workflow,
                depends_on=(),
                depends_on_declared=True,
                target=target,
            ))
    elif structured is not None:
        if not isinstance(structured, list) or not structured:
            raise _fail(f"federation.workflows.{name}.steps must be a non-empty array of inline tables")
        seen_ids: set[str] = set()
        for index, value in enumerate(structured):
            if not isinstance(value, dict):
                raise _fail(f"federation.workflows.{name}.steps[{index}] must be an inline table")
            item = dict(value)
            _reject_unknown(item, _WORKFLOW_STEP_KEYS, path=f"federation.workflows.{name}.steps[{index}]")
            step_id = _name(item.get("id"), path=f"federation.workflows.{name}.steps[{index}].id")
            if step_id in seen_ids:
                raise _fail(f"federation workflow {name!r} declares duplicate step id {step_id!r}")
            seen_ids.add(step_id)
            ref, repository, target, workflow = _workflow_ref(
                item.get("ref"),
                path=f"federation.workflows.{name}.steps[{index}].ref",
                repositories=repositories,
            )
            deps_raw = item.get("depends_on", [])
            if not isinstance(deps_raw, list):
                raise _fail(f"federation.workflows.{name}.steps[{index}].depends_on must be an array of step ids")
            deps: list[str] = []
            for dep_index, dep in enumerate(deps_raw):
                dep_name = _name(dep, path=f"federation.workflows.{name}.steps[{index}].depends_on[{dep_index}]")
                if dep_name in deps:
                    raise _fail(f"federation workflow {name!r} step {step_id!r} repeats dependency {dep_name!r}")
                deps.append(dep_name)
            steps.append(FederatedWorkflowStep(
                step_id=step_id,
                ref=ref,
                repository=repository,
                workflow=workflow,
                depends_on=tuple(deps),
                depends_on_declared="depends_on" in item,
                target=target,
            ))
        known = {step.step_id for step in steps}
        for step in steps:
            missing = [dep for dep in step.depends_on if dep not in known]
            if missing:
                raise _fail(f"federation workflow {name!r} step {step.step_id!r} depends on unknown step id(s): {', '.join(missing)}")
            if step.step_id in step.depends_on:
                raise _fail(f"federation workflow {name!r} step {step.step_id!r} cannot depend on itself")
    else:
        raise _fail(f"federation workflow {name!r} must declare depends_on or steps")
    return tuple(steps)


def load_federation_config(section: object, *, owner_root: str | Path) -> FederationConfig:
    """Parse repository-owned ``[federation]`` configuration.

    ``owner_root`` is the config-owning repository root, not necessarily the
    requested project root.  This preserves the FRX-01 parent-workspace model
    and makes every relative child path stable regardless of launch cwd.
    """

    raw = _table(section, path="[federation]")
    if not raw:
        return FederationConfig(schema=FEDERATION_SCHEMA_VERSION)
    _reject_unknown(raw, _FEDERATION_KEYS, path="[federation]")

    schema_raw = raw.get("schema")
    if isinstance(schema_raw, bool) or not isinstance(schema_raw, int):
        raise _fail("[federation].schema must be integer 1")
    schema = int(schema_raw)
    if schema != FEDERATION_SCHEMA_VERSION:
        raise _fail(
            f"Unsupported [federation].schema={schema}; supported schema is {FEDERATION_SCHEMA_VERSION}"
        )

    owner = Path(owner_root).expanduser().resolve(strict=False)
    repositories_raw = _table(raw.get("repositories"), path="[federation.repositories]")
    repositories: dict[str, FederatedRepositoryConfig] = {}
    canonical_roots: dict[str, str] = {}

    for raw_name, raw_repository in repositories_raw.items():
        name = _name(raw_name, path="federation repository name")
        if name in repositories:
            raise _fail(f"Duplicate federation repository name after normalization: {name!r}")
        item = _table(raw_repository, path=f"[federation.repositories.{name}]")
        _reject_unknown(item, _REPOSITORY_KEYS, path=f"[federation.repositories.{name}]")
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise _fail(f"federation.repositories.{name}.path must be a non-empty string")
        declared_path = raw_path.strip()
        if "\x00" in declared_path or "\n" in declared_path or "\r" in declared_path:
            raise _fail(f"federation.repositories.{name}.path contains unsafe control characters")
        root = _canonical(owner, declared_path)
        if root == owner:
            raise _fail(
                f"federation repository {name!r} resolves to the owning repository itself ({owner}); "
                "self federation is not allowed"
            )
        root_key = str(root)
        previous = canonical_roots.get(root_key)
        if previous is not None:
            raise _fail(
                f"federation repositories {previous!r} and {name!r} resolve to the same canonical root {root}"
            )
        canonical_roots[root_key] = name
        required = _bool(item.get("required"), path=f"federation.repositories.{name}.required", default=True)
        if root.exists() and not root.is_dir():
            raise _fail(f"federation repository {name!r} exists but is not a directory: {root}")
        available = root.is_dir()
        if required and not available:
            raise _fail(f"required federation repository {name!r} does not exist or is not a directory: {root}")
        if available and _direct_back_reference(root, owner):
            raise _fail(
                f"direct federation cycle detected: owning repository {owner} enrolls {name!r} at {root}, "
                "and that child directly enrolls the owner"
            )
        repositories[name] = FederatedRepositoryConfig(
            name=name,
            declared_path=declared_path,
            root=str(root),
            required=required,
            available=available,
            exports=_capability_exports(item.get("exports"), path=f"[federation.repositories.{name}.exports]"),
            requires=_capability_requires(item.get("requires"), path=f"[federation.repositories.{name}.requires]"),
        )

    groups_raw = _table(raw.get("groups"), path="[federation.groups]")
    groups: dict[str, FederationGroup] = {}
    for raw_name, raw_group in groups_raw.items():
        name = _name(raw_name, path="federation group name")
        if name in groups:
            raise _fail(f"Duplicate federation group name after normalization: {name!r}")
        if name in repositories:
            raise _fail(
                f"federation group {name!r} conflicts with a repository of the same name; choose a distinct name"
            )

        # X03 adds executable group members while retaining FRX-02's
        # repository-only metadata group tables. A list shorthand is treated
        # as executable members; table form can carry both repositories and
        # members.
        if isinstance(raw_group, list):
            repository_members: tuple[str, ...] = ()
            executable_members = _group_members(raw_group, path=f"federation.groups.{name}")
        else:
            group = _table(raw_group, path=f"[federation.groups.{name}]")
            _reject_unknown(group, _GROUP_KEYS, path=f"[federation.groups.{name}]")
            has_repositories = "repositories" in group
            has_members = "members" in group
            if not has_repositories and not has_members:
                raise _fail(
                    f"federation group {name!r} must declare repositories and/or executable members"
                )
            repository_members = (
                _repo_members(group.get("repositories"), path=f"federation.groups.{name}.repositories")
                if has_repositories else ()
            )
            executable_members = (
                _group_members(group.get("members"), path=f"federation.groups.{name}.members")
                if has_members else ()
            )
        unknown = [member for member in repository_members if member not in repositories]
        if unknown:
            available = ", ".join(sorted(repositories)) or "<none>"
            raise _fail(
                f"federation group {name!r} references unknown repository/repositories: {', '.join(unknown)}. "
                f"Available repositories: {available}"
            )
        groups[name] = FederationGroup(
            name=name, repositories=repository_members, members=executable_members
        )

    workflows_raw = _table(raw.get("workflows"), path="[federation.workflows]")
    workflows: dict[str, FederatedWorkflowConfig] = {}
    for raw_name, raw_workflow in workflows_raw.items():
        name = _name(raw_name, path="federation workflow name")
        if name in workflows:
            raise _fail(f"Duplicate federation workflow name after normalization: {name!r}")
        item = _table(raw_workflow, path=f"[federation.workflows.{name}]")
        workflows[name] = FederatedWorkflowConfig(name=name, steps=_workflow_steps(name, item, repositories))

    groups = _normalize_group_members(groups, workflows, repositories)
    return FederationConfig(schema=schema, repositories=repositories, groups=groups, workflows=workflows)


def federation_as_dict(config: FederationConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": config.schema,
        "enabled": config.enabled,
        "repositories": {
            name: {
                "name": repo.name,
                "path": repo.declared_path,
                "root": repo.root,
                "required": repo.required,
                "available": repo.available,
                "exports": {"capabilities": list(repo.exports)},
                "requires": {
                    "capabilities": [
                        {
                            "name": requirement.name,
                            "optional": requirement.optional,
                            "compatible": list(requirement.compatible),
                            "policy": requirement.policy,
                        }
                        for requirement in repo.requires
                    ]
                },
            }
            for name, repo in sorted(config.repositories.items())
        },
        "groups": {
            name: {
                "name": group.name,
                "repositories": list(group.repositories),
                "members": list(group.members),
                "executable": group.executable,
            }
            for name, group in sorted(config.groups.items())
        },
    }
    if config.workflows:
        payload["workflows"] = {
            name: {
                "name": workflow.name,
                "steps": [
                    {
                        "id": step.step_id,
                        "ref": step.ref,
                        "repository": step.repository,
                        "target": step.target,
                        "target_qualified": step.target_qualified,
                        "workflow": step.workflow,
                        "depends_on": list(step.depends_on),
                    }
                    for step in workflow.steps
                ],
            }
            for name, workflow in sorted(config.workflows.items())
        }
    return payload


__all__ = [
    "FEDERATION_SCHEMA_VERSION",
    "FederationConfigError",
    "federation_as_dict",
    "load_federation_config",
]
