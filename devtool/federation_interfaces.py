from __future__ import annotations

"""FRX-Y06 federation interface / conformance contracts.

Interfaces are parent-owned requirements over child-owned public Devtool
surfaces. Inspection is read-only: the coordinator never injects child
configuration or rewrites a child contract to make it conform.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .federation_discovery import FederationDiscoveryError, resolve_federated_repository
from .models import FederationInterface, ProjectConfig


class FederationInterfaceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FederationInterfaceMember:
    repository: str
    interface: str
    status: str
    root: str
    missing_workflows: tuple[str, ...] = ()
    missing_wrapper_commands: tuple[str, ...] = ()
    missing_artifacts: tuple[str, ...] = ()
    workflows: dict[str, tuple[str, ...]] = field(default_factory=dict)
    wrapper_commands: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "interface": self.interface,
            "status": self.status,
            "root": self.root,
            "missing": {
                "workflows": list(self.missing_workflows),
                "wrapper_commands": list(self.missing_wrapper_commands),
                "artifacts": list(self.missing_artifacts),
            },
            "public_surface": {
                "workflows": {key: list(value) for key, value in sorted(self.workflows.items())},
                "wrapper_commands": list(self.wrapper_commands),
                "artifacts": list(self.artifacts),
            },
        }


@dataclass(frozen=True, slots=True)
class FederationInterfaceReport:
    interface: str
    groups: tuple[str, ...]
    members: tuple[FederationInterfaceMember, ...]

    @property
    def ok(self) -> bool:
        return all(member.ok for member in self.members)

    @property
    def status(self) -> str:
        return "ok" if self.ok else "error"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "interface": self.interface,
            "groups": list(self.groups),
            "status": self.status,
            "members": [member.as_dict() for member in self.members],
        }


def _load_child(parent: ProjectConfig, repository: str) -> ProjectConfig:
    from .config import load_project_config

    try:
        resolution = resolve_federated_repository(parent, repository)
    except FederationDiscoveryError as exc:
        raise FederationInterfaceError(str(exc)) from exc
    if not resolution.available:
        raise FederationInterfaceError(f"Federation repository {repository!r} is unavailable")
    try:
        return load_project_config(
            resolution.root,
            resolution.config_path or None,
            target_name="",
            variant_name="",
            allow_ambiguous_target=True,
            allow_ambiguous_project_kind=True,
            allow_parent_config=False,
        )
    except SystemExit as exc:
        raise FederationInterfaceError(f"Cannot load federation repository {repository!r}: {exc}") from exc


def _artifact_token(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    name = Path(text).name
    if name.endswith(".tar.gz"):
        return "tar.gz"
    if name.endswith(".tar.zst"):
        return "tar.zst"
    suffix = Path(name).suffix.lower().lstrip(".")
    return suffix or text


def _child_artifacts(child: ProjectConfig) -> tuple[str, ...]:
    values: list[str] = []

    def add(value: object) -> None:
        text = _artifact_token(str(value or ""))
        if text and text not in values:
            values.append(text)

    for target in child.targets.values():
        for output in target.outputs:
            add(output)
        raw = target.options.get("artifacts") if isinstance(target.options, dict) else None
        if not isinstance(raw, dict):
            continue
        for key in ("types", "kinds", "names", "paths", "artifact_paths", "outputs"):
            item = raw.get(key)
            if isinstance(item, str):
                add(item)
            elif isinstance(item, (list, tuple)):
                for value in item:
                    add(value)
    raw = child.runner_options.get("artifacts") if isinstance(child.runner_options, dict) else None
    if isinstance(raw, dict):
        for key in ("types", "kinds", "names", "paths", "artifact_paths", "outputs"):
            item = raw.get(key)
            if isinstance(item, str):
                add(item)
            elif isinstance(item, (list, tuple)):
                for value in item:
                    add(value)
    return tuple(sorted(values))


def _child_workflows(child: ProjectConfig) -> dict[str, tuple[str, ...]]:
    result: dict[str, list[str]] = {}
    for target_name, target in sorted(child.targets.items()):
        for workflow in sorted(target.workflows):
            result.setdefault(workflow, []).append(target_name)
    return {key: tuple(value) for key, value in sorted(result.items())}


def inspect_interface_member(
    parent: ProjectConfig,
    repository: str,
    interface: FederationInterface,
) -> FederationInterfaceMember:
    child = _load_child(parent, repository)
    workflows = _child_workflows(child)
    wrappers = tuple(sorted(child.wrapper.commands))
    artifacts = _child_artifacts(child)
    missing_workflows = tuple(name for name in interface.required_workflows if name not in workflows)
    missing_wrappers = tuple(name for name in interface.required_wrapper_commands if name not in wrappers)
    missing_artifacts = tuple(name for name in interface.required_artifacts if _artifact_token(name) not in artifacts)
    return FederationInterfaceMember(
        repository=repository,
        interface=interface.name,
        status="ok" if not (missing_workflows or missing_wrappers or missing_artifacts) else "error",
        root=str(child.repo_root),
        missing_workflows=missing_workflows,
        missing_wrapper_commands=missing_wrappers,
        missing_artifacts=missing_artifacts,
        workflows=workflows,
        wrapper_commands=wrappers,
        artifacts=artifacts,
    )


def _repositories_for_group(config: ProjectConfig, group_name: str) -> tuple[str, ...]:
    group = config.federation.groups.get(group_name)
    if group is None:
        raise FederationInterfaceError(f"Unknown federation group {group_name!r}")
    result: list[str] = list(group.repositories)
    if group.members:
        # Reuse the executable-group materializer so nested groups, named
        # workflows, and direct public-contract refs share one ownership truth.
        from .federation_plan import materialize_federation_group

        try:
            workflow = materialize_federation_group(config, group_name)
        except Exception as exc:
            raise FederationInterfaceError(str(exc)) from exc
        for step in workflow.steps:
            if step.repository not in result:
                result.append(step.repository)
    return tuple(result)


def build_interface_report(
    config: ProjectConfig,
    *,
    interface_name: str = "",
    group_name: str = "",
) -> FederationInterfaceReport:
    group_name = str(group_name or "").strip()
    interface_name = str(interface_name or "").strip()
    groups: list[str] = []
    repositories: list[str] = []

    if group_name:
        group = config.federation.groups.get(group_name)
        if group is None:
            raise FederationInterfaceError(f"Unknown federation group {group_name!r}")
        bound = str(group.interface or "").strip()
        if not bound:
            raise FederationInterfaceError(f"Federation group {group_name!r} has no interface contract")
        if interface_name and interface_name != bound:
            raise FederationInterfaceError(
                f"Federation group {group_name!r} binds interface {bound!r}, not {interface_name!r}"
            )
        interface_name = bound
        groups.append(group_name)
        repositories.extend(_repositories_for_group(config, group_name))
    else:
        if not interface_name:
            raise FederationInterfaceError("Interface conformance requires --interface or --group")
        for name, group in sorted(config.federation.groups.items()):
            if group.interface == interface_name:
                groups.append(name)
                for repository in _repositories_for_group(config, name):
                    if repository not in repositories:
                        repositories.append(repository)

    interface = config.federation.interfaces.get(interface_name)
    if interface is None:
        available = ", ".join(sorted(config.federation.interfaces)) or "<none>"
        raise FederationInterfaceError(
            f"Unknown federation interface {interface_name!r}. Available interfaces: {available}"
        )
    if not repositories:
        raise FederationInterfaceError(
            f"No federation repositories are bound to interface {interface_name!r}"
        )
    members = tuple(inspect_interface_member(config, repository, interface) for repository in repositories)
    return FederationInterfaceReport(interface=interface_name, groups=tuple(groups), members=members)


def all_interface_reports(config: ProjectConfig) -> tuple[FederationInterfaceReport, ...]:
    reports: list[FederationInterfaceReport] = []
    for group_name, group in sorted(config.federation.groups.items()):
        if group.interface:
            reports.append(build_interface_report(config, group_name=group_name))
    return tuple(reports)


def interface_catalog(config: ProjectConfig) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "interfaces": {
            name: {
                "required_workflows": list(interface.required_workflows),
                "required_wrapper_commands": list(interface.required_wrapper_commands),
                "required_artifacts": list(interface.required_artifacts),
                "groups": [
                    group_name for group_name, group in sorted(config.federation.groups.items())
                    if group.interface == name
                ],
            }
            for name, interface in sorted(config.federation.interfaces.items())
        },
    }


__all__ = [
    "FederationInterfaceError",
    "FederationInterfaceMember",
    "FederationInterfaceReport",
    "inspect_interface_member",
    "build_interface_report",
    "all_interface_reports",
    "interface_catalog",
]
