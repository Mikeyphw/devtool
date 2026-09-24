from __future__ import annotations

"""Adoption/reporting helpers for EXO target-local jobs.

The runtime contract is owned by workflow_config/workflow_runtime.  This
module intentionally stays read-only: it gives humans, AI agents, and repo
migration scripts one compact surface for deciding whether target-local jobs
are configured and how to exercise them safely.
"""

from typing import Any

from .models import JobConfig, ProjectConfig, TargetConfig, WorkflowConfig, WorkflowStepRef
from .workflow_config import job_local_name, job_scope, job_target


def _job_key(job: JobConfig) -> str:
    return str(job.metadata.get("job_key") or job.name)


def _target_local_job_payload(target_name: str, local_name: str, job: JobConfig) -> dict[str, Any]:
    key = _job_key(job) or f"{target_name}.{local_name}"
    mode = job.command_mode
    payload = job.action or " ".join(job.command) or job.shell_command
    return {
        "key": key,
        "target": target_name,
        "local_name": str(job.metadata.get("local_name") or local_name),
        "scope": "target",
        "target_local": True,
        "runner": job.runner,
        "mode": mode,
        "root": job.root,
        "command": list(job.command),
        "shell_command": job.shell_command,
        "action": job.action,
        "display": payload,
        "refs": {
            "owner_local": f"job:{local_name}",
            "owner_key": f"job:{key}",
            "qualified_local": f"{target_name}:job:{local_name}",
            "qualified_key": f"{target_name}:job:{key}",
        },
        "inputs": list(job.inputs),
        "outputs": list(job.outputs),
    }


def _repository_job_payload(name: str, job: JobConfig) -> dict[str, Any]:
    payload = job.action or " ".join(job.command) or job.shell_command
    return {
        "key": name,
        "scope": "repository",
        "target_local": False,
        "runner": job.runner,
        "mode": job.command_mode,
        "root": job.root,
        "display": payload,
        "refs": {"workflow": f"job:{name}"},
        "inputs": list(job.inputs),
        "outputs": list(job.outputs),
    }


def _workflow_job_steps(workflow: WorkflowConfig) -> list[WorkflowStepRef]:
    return [step for step in workflow.steps if step.kind == "job"]


def _workflow_target_steps(workflow: WorkflowConfig) -> list[WorkflowStepRef]:
    return [step for step in workflow.steps if step.kind == "target"]


def _workflow_payload(target_name: str, workflow_name: str, workflow: WorkflowConfig) -> dict[str, Any]:
    target_local_steps: list[dict[str, Any]] = []
    repository_steps: list[dict[str, Any]] = []
    for step in _workflow_job_steps(workflow):
        entry = {
            "raw": step.raw,
            "key": step.resolution_key or step.name,
            "scope": step.resolution_scope,
            "target": step.resolution_target,
            "local_name": step.resolution_local_name,
            "target_local": step.resolution_scope == "target",
        }
        if step.resolution_scope == "target":
            target_local_steps.append(entry)
        else:
            repository_steps.append(entry)
    child_targets = [
        {"target": step.name, "workflow": step.action, "raw": step.raw}
        for step in _workflow_target_steps(workflow)
    ]
    return {
        "name": workflow_name,
        "target": target_name,
        "step_count": len(workflow.steps),
        "target_local_job_count": len(target_local_steps),
        "repository_job_count": len(repository_steps),
        "target_local_jobs": target_local_steps,
        "repository_jobs": repository_steps,
        "child_workflows": child_targets,
        "commands": {
            "plan": f"devtool plan --target {target_name} {workflow_name} --json",
            "preflight": f"devtool preflight --target {target_name} --workflow {workflow_name} --json",
            "invoke": f"devtool invoke --target {target_name} {workflow_name}",
        },
    }


def _target_status(target: TargetConfig) -> str:
    if target.jobs:
        return "ready"
    if target.workflows:
        return "workflows-without-target-local-jobs"
    return "no-target-local-jobs"


def _target_payload(target_name: str, target: TargetConfig) -> dict[str, Any]:
    jobs = [
        _target_local_job_payload(target_name, local_name, job)
        for local_name, job in sorted(target.jobs.items())
    ]
    workflows = [
        _workflow_payload(target_name, workflow_name, workflow)
        for workflow_name, workflow in sorted(target.workflows.items())
    ]
    workflows_using_local = [
        workflow["name"]
        for workflow in workflows
        if int(workflow.get("target_local_job_count", 0)) > 0
    ]
    return {
        "name": target_name,
        "status": _target_status(target),
        "root": target.root,
        "runner": target.runner,
        "job_count": len(jobs),
        "workflow_count": len(workflows),
        "jobs": jobs,
        "workflows": workflows,
        "workflows_using_target_local_jobs": workflows_using_local,
        "commands": {
            "inspect_jobs": f"devtool jobs --target {target_name} --json",
            "inspect_workflows": "devtool workflows --json",
        },
        "recommended_next_steps": _target_recommendations(target_name, target, workflows_using_local),
    }


def _target_recommendations(target_name: str, target: TargetConfig, workflows_using_local: list[str]) -> list[str]:
    if not target.jobs:
        return [
            f"Add [targets.{target_name}.jobs.<name>] entries for work that belongs to this target root.",
            f"Reference them from [targets.{target_name}.workflows] as job:<name>.",
        ]
    if not workflows_using_local:
        return [
            f"Wire target-local jobs into [targets.{target_name}.workflows] using job:<name> references.",
            f"Use devtool plan --target {target_name} <workflow> --json to confirm target-local identity.",
        ]
    return [
        f"Use devtool preflight --target {target_name} --workflow <workflow> --json before migration gates.",
        f"Use devtool jobs --target {target_name} --json for AI-agent handoffs and editor completion data.",
    ]


def target_local_adoption_report(config: ProjectConfig, *, selected_target: str = "") -> dict[str, Any]:
    selected_target = str(selected_target or "").strip()
    if selected_target and selected_target not in config.targets:
        raise SystemExit(f"Unknown target for target-local adoption report: {selected_target}")
    targets = {
        name: target
        for name, target in sorted(config.targets.items())
        if not selected_target or name == selected_target
    }
    target_payloads = {name: _target_payload(name, target) for name, target in targets.items()}
    repository_jobs = {
        name: _repository_job_payload(name, job)
        for name, job in sorted(config.jobs.items())
        if job_scope(job) == "repository"
    }
    target_local_jobs = [
        job
        for target in target_payloads.values()
        for job in target["jobs"]
    ]
    workflows_using_target_local_jobs = {
        target_name: target["workflows_using_target_local_jobs"]
        for target_name, target in target_payloads.items()
        if target["workflows_using_target_local_jobs"]
    }
    return {
        "schema_version": 1,
        "selected_target": selected_target,
        "summary": {
            "targets": len(target_payloads),
            "targets_with_target_local_jobs": [
                name for name, target in target_payloads.items() if target["job_count"]
            ],
            "target_local_job_count": len(target_local_jobs),
            "repository_job_count": len(repository_jobs),
            "workflows_using_target_local_jobs": workflows_using_target_local_jobs,
        },
        "targets": target_payloads,
        "target_local_jobs": target_local_jobs,
        "repository_jobs": repository_jobs,
        "checklist": [
            "Declare target-owned work under [targets.<target>.jobs.<name>] instead of repository [jobs.*].",
            "Reference same-target jobs as job:<name>; avoid hard-coding <target>.<job> in authored workflows.",
            "Use target:<target>#<workflow> for cross-target orchestration.",
            "Confirm with jobs, plan, preflight, and run-summary JSON before sealing repo migrations.",
        ],
    }


def render_target_local_adoption_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    selected = str(payload.get("selected_target") or "")
    title = "Target-local jobs adoption"
    if selected:
        title += f" · {selected}"
    lines.append(title)
    lines.append("=" * len(title))
    lines.append("")
    lines.append(f"Targets inspected: {summary.get('targets', 0)}")
    lines.append(f"Target-local jobs: {summary.get('target_local_job_count', 0)}")
    lines.append(f"Repository jobs: {summary.get('repository_job_count', 0)}")
    targets_with_jobs = summary.get("targets_with_target_local_jobs", [])
    lines.append("Targets with target-local jobs: " + (", ".join(targets_with_jobs) if targets_with_jobs else "none"))
    lines.append("")
    targets = payload.get("targets", {}) if isinstance(payload.get("targets"), dict) else {}
    for target_name, target in targets.items():
        if not isinstance(target, dict):
            continue
        lines.append(f"[{target_name}] {target.get('status', '')}")
        lines.append(f"  root: {target.get('root') or '-'}")
        jobs = target.get("jobs", [])
        if jobs:
            lines.append("  jobs:")
            for job in jobs:
                if isinstance(job, dict):
                    lines.append(f"    - {job.get('key')} ref=job:{job.get('local_name')} runner={job.get('runner')} mode={job.get('mode')}")
        else:
            lines.append("  jobs: none")
        workflows = target.get("workflows", [])
        if workflows:
            lines.append("  workflows:")
            for workflow in workflows:
                if not isinstance(workflow, dict):
                    continue
                local_keys = [str(item.get("key")) for item in workflow.get("target_local_jobs", []) if isinstance(item, dict)]
                rendered = ", ".join(local_keys) if local_keys else "no target-local jobs"
                lines.append(f"    - {workflow.get('name')}: {rendered}")
        else:
            lines.append("  workflows: none")
        steps = target.get("recommended_next_steps", [])
        if steps:
            lines.append("  next:")
            for step in steps:
                lines.append(f"    - {step}")
        lines.append("")
    checklist = payload.get("checklist", [])
    if checklist:
        lines.append("Checklist:")
        for item in checklist:
            lines.append(f"  - {item}")
    return "\n".join(lines).rstrip() + "\n"
