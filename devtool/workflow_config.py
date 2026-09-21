from __future__ import annotations

"""Configuration primitives for MP01/MP02 target identity and reusable workflows.

This module intentionally stops at *representation and validation*.  MP03 adds
execution-graph planning. Keeping the parser independent lets config, CLI,
desktop, and machine-output surfaces share one normalized truth model now.
"""

import re
from pathlib import Path
from typing import Any, Mapping

from .execution_environments import normalize_execution_environment
from .models import JobConfig, TargetConfig, WorkflowConfig, WorkflowStepRef

_RUNNER_ALIASES = {"generic": "command"}
_PLATFORM_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_WORKFLOW_ACTION_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


def normalize_runner_name(value: object) -> str:
    name = str(value or "").strip().lower()
    return _RUNNER_ALIASES.get(name, name)


def normalize_platform_name(value: object, *, context: str = "platform") -> str:
    name = str(value or "").strip().lower()
    if not name:
        return ""
    if not _PLATFORM_RE.fullmatch(name):
        raise SystemExit(
            f"Invalid {context} {value!r}: use a lowercase slug containing letters, digits, '.', '_' or '-'."
        )
    return name


def _resolve_optional_path(root: Path, value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = root / path
    return str(path.resolve(strict=False))


def _string_tuple(value: object, *, context: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list):
        raise SystemExit(f"Invalid {context}: expected a TOML array of strings.")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise SystemExit(f"Invalid {context}[{index}]: expected a non-empty string.")
        result.append(item.strip())
    return tuple(result)


def _command_tuple(value: object, *, job_name: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        raise SystemExit(
            f"Job '{job_name}' uses command as a string. Use command = [\"tool\", \"arg\"] for structured argv "
            "or shell_command = \"...\" when shell syntax is intentionally required."
        )
    argv = _string_tuple(value, context=f"jobs.{job_name}.command")
    if not argv:
        raise SystemExit(f"Job '{job_name}' command cannot be empty.")
    return argv


def _job_options(raw: Mapping[str, Any], runner_option_sections: tuple[str, ...]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for option_name in runner_option_sections:
        value = raw.get(option_name)
        if isinstance(value, dict):
            options[option_name] = dict(value)
    return options


def load_jobs(
    repo_root: Path,
    section: object,
    base_env: Mapping[str, str],
    *,
    supported_runners: set[str],
    runner_option_sections: tuple[str, ...],
) -> dict[str, JobConfig]:
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise SystemExit("[jobs] must be a TOML table")

    jobs: dict[str, JobConfig] = {}
    seen_names: set[str] = set()
    for raw_name, raw_value in section.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, dict):
            raise SystemExit(f"Job {raw_name!r} must be a TOML table")
        name = raw_name.strip()
        if not name:
            raise SystemExit("Job names cannot be empty")
        normalized_name = name.casefold()
        if normalized_name in seen_names:
            raise SystemExit(f"Duplicate job name after normalization: {name!r}")
        seen_names.add(normalized_name)

        runner = normalize_runner_name(raw_value.get("runner", raw_value.get("type", raw_value.get("kind", ""))))
        if not runner:
            raise SystemExit(f"Job '{name}' must declare runner = \"...\".")
        if runner not in supported_runners or runner in {"auto", "unknown"}:
            supported = ", ".join(sorted(item for item in supported_runners if item not in {"auto", "unknown"}))
            raise SystemExit(f"Job '{name}' declares unsupported runner '{runner}'. Supported runners: {supported}")

        command = _command_tuple(raw_value.get("command"), job_name=name)
        shell_command = str(raw_value.get("shell_command", "") or "").strip()
        action = str(raw_value.get("action", "") or "").strip()
        modes = sum(bool(item) for item in (command, shell_command, action))
        if modes == 0:
            raise SystemExit(
                f"Job '{name}' must declare exactly one of action, structured command, or shell_command."
            )
        if modes > 1:
            raise SystemExit(
                f"Job '{name}' cannot combine action, command, and shell_command; choose exactly one execution mode."
            )
        if action and not _WORKFLOW_ACTION_RE.fullmatch(action):
            raise SystemExit(f"Job '{name}' has invalid runner action {action!r}.")

        execution_raw = raw_value.get("execution_environment", raw_value.get("environment", "auto"))
        try:
            execution_environment = normalize_execution_environment(str(execution_raw or "auto"))
        except ValueError as exc:
            raise SystemExit(f"Invalid execution environment for job '{name}': {exc}") from exc

        raw_env = raw_value.get("env", {})
        if raw_env is not None and not isinstance(raw_env, dict):
            raise SystemExit(f"[jobs.{name}.env] must be a TOML table")
        env = {} if "env" in raw_value and isinstance(raw_env, dict) and not raw_env else {str(k): str(v) for k, v in base_env.items()}
        if isinstance(raw_env, dict):
            env.update({str(k): str(v) for k, v in raw_env.items()})

        metadata_raw = raw_value.get("metadata", {})
        if metadata_raw is not None and not isinstance(metadata_raw, dict):
            raise SystemExit(f"[jobs.{name}.metadata] must be a TOML table")

        jobs[name] = JobConfig(
            name=name,
            runner=runner,
            root=_resolve_optional_path(repo_root, raw_value.get("root", "")),
            action=action,
            command=command,
            shell_command=shell_command,
            env=env,
            execution_environment=execution_environment,
            chroot_name=str(raw_value.get("chroot_name", "") or "").strip(),
            device_serial=str(raw_value.get("device_serial", "") or "").strip(),
            inputs=_string_tuple(raw_value.get("inputs"), context=f"jobs.{name}.inputs"),
            outputs=_string_tuple(raw_value.get("outputs"), context=f"jobs.{name}.outputs"),
            metadata=dict(metadata_raw) if isinstance(metadata_raw, dict) else {},
            options=_job_options(raw_value, runner_option_sections),
            declared_fields=frozenset(str(key) for key in raw_value),
        )
    return jobs


def _parse_workflow_reference(
    raw: str,
    *,
    target_name: str,
    workflow_name: str,
    jobs: Mapping[str, JobConfig],
    targets: Mapping[str, TargetConfig],
    supported_runners: set[str],
    tests: Mapping[str, Any] | None = None,
    step_id: str = "",
    depends_on: tuple[str, ...] = (),
    depends_on_declared: bool = False,
) -> WorkflowStepRef:
    prefix, separator, payload = raw.partition(":")
    kind = prefix.strip().lower()
    payload = payload.strip()
    if not separator or kind not in {"job", "runner", "target", "test"} or not payload:
        raise SystemExit(
            f"Invalid workflow step {raw!r} in target '{target_name}' workflow '{workflow_name}'. "
            "Use job:<name>, test:<id>, runner:<runner>#<action>, or target:<target>#<action>."
        )

    if kind == "test":
        if "#" in payload:
            raise SystemExit(f"Test workflow reference {raw!r} must not include an action suffix.")
        declared = dict(tests or {})
        if payload not in declared:
            available = ", ".join(sorted(declared)) or "<none>"
            raise SystemExit(
                f"Workflow target '{target_name}' references unknown test '{payload}'. Available tests: {available}"
            )
        return WorkflowStepRef(
            raw=raw, kind="test", name=payload, step_id=step_id,
            depends_on=depends_on, depends_on_declared=depends_on_declared,
        )

    if kind == "job":
        if "#" in payload:
            raise SystemExit(f"Job workflow reference {raw!r} must not include an action suffix.")
        if payload not in jobs:
            available = ", ".join(sorted(jobs)) or "<none>"
            raise SystemExit(
                f"Workflow target '{target_name}' references unknown job '{payload}'. Available jobs: {available}"
            )
        return WorkflowStepRef(
            raw=raw,
            kind="job",
            name=payload,
            step_id=step_id,
            depends_on=depends_on,
            depends_on_declared=depends_on_declared,
        )

    name, hash_sep, action = payload.partition("#")
    name = name.strip()
    action = action.strip()
    if not hash_sep or not name or not action:
        raise SystemExit(f"Workflow reference {raw!r} must use the form {kind}:<name>#<action>.")
    if not _WORKFLOW_ACTION_RE.fullmatch(action):
        raise SystemExit(f"Workflow reference {raw!r} has invalid action {action!r}.")

    if kind == "runner":
        runner = normalize_runner_name(name)
        if runner not in supported_runners or runner in {"auto", "unknown"}:
            raise SystemExit(f"Workflow target '{target_name}' references unsupported runner '{runner}'.")
        return WorkflowStepRef(
            raw=raw,
            kind="runner",
            name=runner,
            action=action,
            step_id=step_id,
            depends_on=depends_on,
            depends_on_declared=depends_on_declared,
        )

    if name not in targets:
        available = ", ".join(sorted(targets)) or "<none>"
        raise SystemExit(
            f"Workflow target '{target_name}' references unknown target '{name}'. Available targets: {available}"
        )
    return WorkflowStepRef(
        raw=raw,
        kind="target",
        name=name,
        action=action,
        step_id=step_id,
        depends_on=depends_on,
        depends_on_declared=depends_on_declared,
    )


def parse_workflow_step(
    raw_step: object,
    *,
    target_name: str,
    workflow_name: str,
    jobs: Mapping[str, JobConfig],
    targets: Mapping[str, TargetConfig],
    supported_runners: set[str],
    tests: Mapping[str, Any] | None = None,
) -> WorkflowStepRef:
    """Normalize one workflow step.

    MP02 string references remain valid. MP03 adds an inline-table form:

        { id = "test", ref = "runner:python#test", depends_on = ["generate"] }

    ``depends_on`` refers to step IDs, not raw reference strings. Omitting it
    preserves sequential-list behavior; declaring an empty list creates a new
    DAG root.
    """

    if isinstance(raw_step, str):
        raw = raw_step.strip()
        if not raw:
            raise SystemExit(
                f"Workflow targets.{target_name}.workflows.{workflow_name} contains an empty step."
            )
        return _parse_workflow_reference(
            raw,
            target_name=target_name,
            workflow_name=workflow_name,
            jobs=jobs,
            targets=targets,
            supported_runners=supported_runners,
            tests=tests,
        )

    if not isinstance(raw_step, dict):
        raise SystemExit(
            f"Workflow targets.{target_name}.workflows.{workflow_name} contains an invalid step; "
            "expected a reference string or inline table."
        )
    raw = str(raw_step.get("ref", "") or "").strip()
    if not raw:
        raise SystemExit(
            f"Workflow targets.{target_name}.workflows.{workflow_name} inline step must declare ref = \"...\"."
        )
    step_id = str(raw_step.get("id", "") or "").strip()
    if step_id and not _WORKFLOW_ACTION_RE.fullmatch(step_id):
        raise SystemExit(
            f"Workflow targets.{target_name}.workflows.{workflow_name} has invalid step id {step_id!r}."
        )
    depends_on_declared = "depends_on" in raw_step
    depends_on = _string_tuple(
        raw_step.get("depends_on"),
        context=f"targets.{target_name}.workflows.{workflow_name}.depends_on",
    ) if depends_on_declared else ()
    if depends_on_declared and not step_id:
        raise SystemExit(
            f"Workflow targets.{target_name}.workflows.{workflow_name} step {raw!r} declares depends_on "
            "but has no id. Give DAG steps stable ids when declaring dependencies."
        )
    return _parse_workflow_reference(
        raw,
        target_name=target_name,
        workflow_name=workflow_name,
        jobs=jobs,
        targets=targets,
        supported_runners=supported_runners,
        tests=tests,
        step_id=step_id,
        depends_on=depends_on,
        depends_on_declared=depends_on_declared,
    )

def load_target_workflows(
    raw_targets: object,
    targets: dict[str, TargetConfig],
    jobs: Mapping[str, JobConfig],
    *,
    supported_runners: set[str],
    tests: Mapping[str, Any] | None = None,
) -> None:
    if not isinstance(raw_targets, dict):
        return
    for name, target in targets.items():
        raw_target = raw_targets.get(name, {})
        if not isinstance(raw_target, dict):
            continue
        raw_workflows = raw_target.get("workflows", {})
        if raw_workflows in (None, {}):
            continue
        if not isinstance(raw_workflows, dict):
            raise SystemExit(f"[targets.{name}.workflows] must be a TOML table")
        workflows: dict[str, WorkflowConfig] = {}
        seen_actions: set[str] = set()
        for raw_action, raw_steps in raw_workflows.items():
            action = str(raw_action or "").strip()
            if not action or not _WORKFLOW_ACTION_RE.fullmatch(action):
                raise SystemExit(f"Target '{name}' has invalid workflow name {raw_action!r}.")
            normalized_action = action.casefold()
            if normalized_action in seen_actions:
                raise SystemExit(f"Target '{name}' declares duplicate workflow after normalization: {action!r}")
            seen_actions.add(normalized_action)
            if not isinstance(raw_steps, list):
                raise SystemExit(
                    f"targets.{name}.workflows.{action} must be a TOML array of step references or inline step tables"
                )
            refs = tuple(
                parse_workflow_step(
                    step,
                    target_name=name,
                    workflow_name=action,
                    jobs=jobs,
                    targets=targets,
                    supported_runners=supported_runners,
                    tests=tests,
                )
                for step in raw_steps
            )
            if not refs:
                raise SystemExit(f"Target '{name}' workflow '{action}' cannot be empty.")
            workflow = WorkflowConfig(name=action, steps=refs)
            # Validate ids/dependencies/cycles while loading config so malformed
            # DAGs fail before any build/test command is planned.
            from .workflow_graph import WorkflowGraphError, graph_from_workflow
            try:
                graph_from_workflow(name, workflow)
            except WorkflowGraphError as exc:
                raise SystemExit(str(exc)) from exc
            workflows[action] = workflow
        target.workflows = workflows


def workflow_steps_as_strings(workflow: WorkflowConfig) -> list[str]:
    return [step.raw for step in workflow.steps]


def workflow_step_as_dict(step: WorkflowStepRef) -> dict[str, Any]:
    return {
        "ref": step.raw,
        "kind": step.kind,
        "name": step.name,
        "action": step.action,
        "id": step.step_id,
        "depends_on": list(step.depends_on),
        "depends_on_declared": step.depends_on_declared,
    }


def workflow_as_dict(target_name: str, workflow: WorkflowConfig) -> dict[str, Any]:
    from .workflow_graph import graph_from_workflow
    graph = graph_from_workflow(target_name, workflow)
    return {
        "name": workflow.name,
        "steps": [workflow_step_as_dict(step) for step in workflow.steps],
        "graph": graph.as_dict(),
    }


def job_as_dict(job: JobConfig) -> dict[str, Any]:
    return {
        "runner": job.runner,
        "root": job.root,
        "action": job.action,
        "command": list(job.command),
        "shell_command": job.shell_command,
        "command_mode": job.command_mode,
        "env": dict(sorted(job.env.items())),
        "execution_environment": job.execution_environment,
        "chroot_name": job.chroot_name,
        "device_serial": job.device_serial,
        "inputs": list(job.inputs),
        "outputs": list(job.outputs),
        "metadata": dict(job.metadata),
        "options": dict(job.options),
    }
