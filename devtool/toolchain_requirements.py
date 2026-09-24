from __future__ import annotations

"""Expanded-workflow toolchain and preflight analysis (MP08)."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
from typing import Iterable, Sequence

from .execution_environments import (
    AUTO,
    ExecutionPlan,
    detect_current_environment,
    preflight_execution,
    resolve_execution_plan,
)
from .models import CommandSpec, ProjectConfig, TargetConfig
from .project_runners import DEFAULT_RUNNER_REGISTRY
from .toolchains import apply_destination_toolchain_wrapper
from .workflow_runtime import WorkflowRuntimeError, build_workspace_specs, plan_workspace_targets


@dataclass(frozen=True, slots=True)
class RequirementCheck:
    section: str
    name: str
    status: str
    detail: str
    target: str = ""
    workflow: str = ""
    node: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "section": self.section,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "target": self.target,
            "workflow": self.workflow,
            "node": self.node,
        }


@dataclass(frozen=True, slots=True)
class ToolRequirement:
    executable: str
    status: str
    detail: str
    runners: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()
    environments: tuple[str, ...] = ()
    nodes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "executable": self.executable,
            "status": self.status,
            "detail": self.detail,
            "runners": list(self.runners),
            "targets": list(self.targets),
            "environments": list(self.environments),
            "nodes": list(self.nodes),
        }


@dataclass(slots=True)
class ToolchainReport:
    targets: tuple[str, ...]
    invocations: tuple[tuple[str, str], ...]
    runners: tuple[str, ...]
    tools: tuple[ToolRequirement, ...]
    environments: tuple[str, ...]
    checks: tuple[RequirementCheck, ...]
    graph_nodes: tuple[dict[str, object], ...] = ()
    target_local_jobs: tuple[dict[str, object], ...] = ()
    repository_jobs: tuple[dict[str, object], ...] = ()

    @property
    def healthy(self) -> bool:
        bad = {"missing", "error", "incompatible", "unsafe", "unsupported"}
        return not any(item.status in bad for item in self.checks) and not any(item.status in bad for item in self.tools)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "healthy": self.healthy,
            "targets": list(self.targets),
            "invocations": [{"target": target, "workflow": action} for target, action in self.invocations],
            "summary": {
                "target_local_job_count": len(self.target_local_jobs),
                "repository_job_count": len(self.repository_jobs),
                "targets_with_target_local_jobs": sorted({
                    str(item.get("target") or "")
                    for item in self.target_local_jobs
                    if str(item.get("target") or "")
                }),
            },
            "runners": list(self.runners),
            "tools": [item.as_dict() for item in self.tools],
            "environments": list(self.environments),
            "checks": [item.as_dict() for item in self.checks],
            "graph_nodes": list(self.graph_nodes),
            "target_local_jobs": list(self.target_local_jobs),
            "repository_jobs": list(self.repository_jobs),
        }


def _target_root(config: ProjectConfig, target: TargetConfig) -> Path:
    raw = target.root or target.cwd or target.build_target or target.restore_target or str(config.repo_root)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = config.repo_root / path
    if path.is_file():
        path = path.parent
    return path.resolve(strict=False)


def _job_root(config: ProjectConfig, name: str, target_name: str = "") -> Path:
    job = config.jobs[name]
    default_root: object = config.repo_root
    if bool(job.metadata.get("target_local")):
        owner = str(job.metadata.get("target") or target_name or "").strip()
        target = config.targets.get(owner)
        if target is not None:
            default_root = _target_root(config, target)
    path = Path(job.root or default_root).expanduser()
    if not path.is_absolute():
        path = config.repo_root / path
    return path.resolve(strict=False)


def _scope_path(repo: Path, base: Path, raw: str) -> tuple[bool, str]:
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        return True, str(base)
    # Remove glob suffix after the first metacharacter so lexical safety can be
    # checked without requiring the output to exist.
    prefix = text
    for marker in ("*", "?", "["):
        if marker in prefix:
            prefix = prefix.split(marker, 1)[0]
    prefix = prefix.rstrip("/") or "."
    candidate = Path(prefix).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = candidate.resolve(strict=False)
    try:
        relative = resolved.relative_to(repo.resolve()).as_posix()
    except ValueError:
        return False, str(resolved)
    return True, relative or "."


def _executable_status(
    spec: CommandSpec,
    environment: str,
    *,
    planned_executables: set[str] | None = None,
) -> tuple[str, str, str]:
    if not spec.argv:
        return "<empty>", "error", "command has no executable"
    executable = str(spec.argv[0])
    current = detect_current_environment()
    if environment != current:
        return executable, "deferred", f"checked on destination at execution time ({environment})"
    cwd = Path(spec.cwd or os.getcwd()).expanduser()
    if "/" in executable or executable.startswith("."):
        path = Path(executable).expanduser()
        if not path.is_absolute():
            path = cwd / path
        path = path.resolve(strict=False)
        normalized = str(path)
        if not path.exists() and planned_executables and normalized in planned_executables:
            return executable, "planned", f"will be provisioned by an earlier workflow step: {path}"
        if not path.exists():
            return executable, "missing", str(path)
        if path.is_dir():
            return executable, "incompatible", f"{path} is a directory, not an executable file"
        if not path.is_file():
            return executable, "incompatible", f"{path} is not a regular executable file"
        if not os.access(path, os.X_OK):
            return executable, "incompatible", f"{path} is not executable"
        return executable, "ready", str(path)
    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in spec.env.items()})
    found = shutil.which(executable, path=env.get("PATH"))
    return executable, ("ready" if found else "missing"), found or f"{executable} not found on PATH"


def _planned_executables(specs: Sequence[CommandSpec]) -> set[str]:
    """Return executable paths that earlier specs explicitly provision.

    PythonRunner creates managed venvs through ``python_support ensure-venv``
    before later specs invoke that venv's interpreter. Treating that future
    interpreter as a missing host tool would make graph preflight lie about a
    healthy, self-provisioning workflow.
    """
    planned: set[str] = set()
    for spec in specs:
        argv = [str(item) for item in spec.argv]
        try:
            marker = argv.index("ensure-venv")
        except ValueError:
            continue
        if marker + 1 >= len(argv):
            continue
        venv = Path(argv[marker + 1]).expanduser()
        if not venv.is_absolute():
            venv = Path(spec.cwd or os.getcwd()).expanduser() / venv
        venv = venv.resolve(strict=False)
        planned.add(str((venv / "bin" / "python").resolve(strict=False)))
        planned.add(str((venv / "bin" / "python3").resolve(strict=False)))
        planned.add(str((venv / "Scripts" / "python.exe").resolve(strict=False)))
    return planned


def _dedupe_checks(checks: Sequence[RequirementCheck]) -> tuple[RequirementCheck, ...]:
    unique: list[RequirementCheck] = []
    seen: set[tuple[str, ...]] = set()
    for item in checks:
        key = (item.section, item.name, item.status, item.detail, item.target, item.workflow, item.node)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return tuple(unique)

def _plan_for_spec(config: ProjectConfig, spec: CommandSpec, requested_environment: str = AUTO) -> ExecutionPlan:
    configured = str(spec.metadata.get("workflow_execution_environment") or config.execution_environment or AUTO)
    chroot_name = str(spec.metadata.get("workflow_chroot_name") or config.chroot_name or "")
    device_serial = str(spec.metadata.get("workflow_device_serial") or config.device_serial or "")
    return resolve_execution_plan(
        requested=requested_environment or AUTO,
        configured=configured,
        chroot_name=chroot_name,
        device_serial=device_serial,
    )


def workflow_invocations(
    config: ProjectConfig,
    target_names: Sequence[str],
    *,
    workflow: str = "",
) -> tuple[tuple[str, str], ...]:
    explicit = str(workflow or "").strip()
    invocations: list[tuple[str, str]] = []
    for name in target_names:
        target = config.targets[name]
        if explicit and explicit != "all":
            actions = (explicit,)
        elif target.workflows:
            actions = tuple(sorted(target.workflows))
        else:
            actions = ("validate",)
        for action in actions:
            pair = (name, action)
            if pair not in invocations:
                invocations.append(pair)
    return tuple(invocations)


def analyze_toolchain_requirements(
    config: ProjectConfig,
    target_names: Sequence[str],
    *,
    workflow: str = "",
    requested_environment: str = AUTO,
) -> ToolchainReport:
    selected = tuple(dict.fromkeys(str(name).strip() for name in target_names if str(name).strip()))
    if not selected:
        raise WorkflowRuntimeError("Toolchain analysis requires at least one target.")
    unknown = [name for name in selected if name not in config.targets]
    if unknown:
        raise WorkflowRuntimeError(f"Unknown toolchain target(s): {', '.join(unknown)}")

    invocations = workflow_invocations(config, selected, workflow=workflow)
    checks: list[RequirementCheck] = []
    runners: list[str] = []
    environments: list[str] = []
    graph_nodes: list[dict[str, object]] = []
    tool_rows: dict[str, dict[str, object]] = {}
    target_local_jobs: dict[str, dict[str, object]] = {}
    repository_jobs: dict[str, dict[str, object]] = {}
    registered = set(DEFAULT_RUNNER_REGISTRY.registered_kinds()) | {"command"}

    seen_roots: set[tuple[str, str]] = set()
    seen_outputs: set[tuple[str, str, str]] = set()
    seen_graph_nodes: set[str] = set()
    seen_specs: set[tuple[str, tuple[str, ...], str, str]] = set()

    for target_name, action in invocations:
        try:
            graph = plan_workspace_targets(config, [target_name], action)
        except Exception as exc:
            checks.append(RequirementCheck("workflow", f"{target_name}:{action}", "error", str(exc), target=target_name, workflow=action))
            continue

        for node in graph.topological_order():
            if node.key not in seen_graph_nodes:
                seen_graph_nodes.add(node.key)
                graph_nodes.append(node.as_dict())
            runner = ""
            if node.ref.kind == "runner":
                runner = node.ref.name
            elif node.ref.kind == "job" and node.ref.name in config.jobs:
                runner = config.jobs[node.ref.name].runner
            if runner:
                if runner not in runners:
                    runners.append(runner)
                checks.append(RequirementCheck(
                    "runner",
                    runner,
                    "ready" if runner in registered else "unsupported",
                    "registered runner" if runner in registered else "runner is not registered",
                    target=node.target,
                    workflow=node.workflow,
                    node=node.key,
                ))

            target = config.targets.get(node.target)
            if target is not None:
                root = _target_root(config, target)
                root_key = (node.target, str(root))
                if root_key not in seen_roots:
                    seen_roots.add(root_key)
                    checks.append(RequirementCheck(
                        "root", node.target, "ready" if root.is_dir() else "missing", str(root),
                        target=node.target, workflow=node.workflow, node=node.key,
                    ))
                for raw in target.outputs:
                    output_key = (node.target, "target", raw)
                    if output_key in seen_outputs:
                        continue
                    seen_outputs.add(output_key)
                    safe, detail = _scope_path(config.repo_root, root, raw)
                    checks.append(RequirementCheck(
                        "output", f"{node.target}:{raw}", "ready" if safe else "unsafe",
                        detail if safe else f"declared output escapes repository: {detail}",
                        target=node.target, workflow=node.workflow, node=node.key,
                    ))
            if node.ref.kind == "job" and node.ref.name in config.jobs:
                job = config.jobs[node.ref.name]
                job_metadata = dict(job.metadata)
                job_scope = str(job_metadata.get("scope") or ("target" if job_metadata.get("target_local") else "repository"))
                job_target = str(job_metadata.get("target") or node.target or "")
                job_local_name = str(job_metadata.get("local_name") or job.name)
                job_key = str(job_metadata.get("job_key") or job.name)
                job_entry = {
                    "key": job_key,
                    "scope": job_scope,
                    "target": job_target if job_scope == "target" else "",
                    "local_name": job_local_name if job_scope == "target" else job.name,
                    "runner": job.runner,
                    "node": node.key,
                    "workflow": node.workflow,
                }
                if job_scope == "target" or bool(job_metadata.get("target_local")):
                    target_local_jobs[job_key] = job_entry
                    checks.append(RequirementCheck(
                        "job", job_key, "ready",
                        f"target-local job owned by {job_target or node.target}",
                        target=node.target, workflow=node.workflow, node=node.key,
                    ))
                else:
                    repository_jobs[job_key] = job_entry
                root = _job_root(config, job.name, node.target)
                root_key = (f"job:{job.name}", str(root))
                if root_key not in seen_roots:
                    seen_roots.add(root_key)
                    checks.append(RequirementCheck(
                        "root", f"job:{job.name}", "ready" if root.is_dir() else "missing", str(root),
                        target=node.target, workflow=node.workflow, node=node.key,
                    ))
                for raw in job.outputs:
                    output_key = (job.name, "job", raw)
                    if output_key in seen_outputs:
                        continue
                    seen_outputs.add(output_key)
                    safe, detail = _scope_path(config.repo_root, root, raw)
                    checks.append(RequirementCheck(
                        "output", f"job:{job.name}:{raw}", "ready" if safe else "unsafe",
                        detail if safe else f"declared output escapes repository: {detail}",
                        target=node.target, workflow=node.workflow, node=node.key,
                    ))
                if job.shell_command:
                    checks.append(RequirementCheck(
                        "command", f"job:{job.name}", "opaque",
                        "explicit shell command: inner tool requirements cannot be inferred statically",
                        target=node.target, workflow=node.workflow, node=node.key,
                    ))

        try:
            specs = build_workspace_specs(config, [target_name], action)
        except (WorkflowRuntimeError, SystemExit, ValueError) as exc:
            checks.append(RequirementCheck("workflow", f"{target_name}:{action}", "error", str(exc), target=target_name, workflow=action))
            continue

        specs = apply_destination_toolchain_wrapper(config, specs, env=os.environ)
        planned_executables = _planned_executables(specs)
        for spec in specs:
            spec_key = (spec.name, tuple(spec.argv), str(spec.cwd or ""), str(spec.metadata.get("workflow_node_key") or ""))
            if spec_key in seen_specs:
                continue
            seen_specs.add(spec_key)
            plan = _plan_for_spec(config, spec, requested_environment=requested_environment)
            if plan.effective not in environments:
                environments.append(plan.effective)
            expected = [str(spec.cwd or "")]
            preflight = preflight_execution(config.repo_root, plan, expected_paths=expected, require_expected_paths=True)
            for item in preflight.checks:
                checks.append(RequirementCheck(
                    "environment", f"{plan.effective}:{item.name}", item.status, item.detail,
                    target=str(spec.metadata.get("workflow_target") or target_name),
                    workflow=str(spec.metadata.get("workflow_name") or action),
                    node=str(spec.metadata.get("workflow_node_key") or ""),
                ))

            executable, status, detail = _executable_status(
                spec, plan.effective, planned_executables=planned_executables
            )
            row = tool_rows.setdefault(executable, {
                "statuses": [], "details": [], "runners": [], "targets": [], "environments": [], "nodes": [],
            })
            row["statuses"].append(status)
            row["details"].append(detail)
            runner_name = str(spec.metadata.get("workflow_runner") or spec.metadata.get("runner") or "")
            spec_target = str(spec.metadata.get("workflow_target") or target_name)
            spec_node = str(spec.metadata.get("workflow_node_key") or "")
            for key, value in (("runners", runner_name), ("targets", spec_target), ("environments", plan.effective), ("nodes", spec_node)):
                if value and value not in row[key]:
                    row[key].append(value)

    severity = {"missing": 5, "error": 5, "incompatible": 5, "unsafe": 5, "unsupported": 5, "deferred": 2, "planned": 1, "optional": 1, "opaque": 1, "ready": 0, "ok": 0}
    tools: list[ToolRequirement] = []
    for executable, row in tool_rows.items():
        statuses = list(row["statuses"])
        status = max(statuses, key=lambda value: severity.get(value, 3)) if statuses else "ready"
        details = tuple(dict.fromkeys(str(item) for item in row["details"] if str(item)))
        tools.append(ToolRequirement(
            executable=executable,
            status=status,
            detail="; ".join(details),
            runners=tuple(row["runners"]),
            targets=tuple(row["targets"]),
            environments=tuple(row["environments"]),
            nodes=tuple(row["nodes"]),
        ))

    return ToolchainReport(
        targets=selected,
        invocations=invocations,
        runners=tuple(runners),
        tools=tuple(tools),
        environments=tuple(environments),
        checks=_dedupe_checks(checks),
        graph_nodes=tuple(graph_nodes),
        target_local_jobs=tuple(target_local_jobs[key] for key in sorted(target_local_jobs)),
        repository_jobs=tuple(repository_jobs[key] for key in sorted(repository_jobs)),
    )
