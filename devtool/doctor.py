from __future__ import annotations

"""Structured project/environment health checks used by ``devtool doctor``."""

from dataclasses import asdict, dataclass
from importlib import metadata
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from .models import ProjectConfig
from .features import resolve_feature_flags
from .wrapper_config import wrapper_as_dict
from .wrapper_launcher import launcher_status, launcher_status_as_dict
from .federation_discovery import FederationDiscoveryError, federation_boundary_payload, resolve_federated_repository
from .federation_gate import build_federation_gate1_report
from .android.capabilities import resolve_android_execution_context
from .android.devices import AndroidDeviceManager

@dataclass(slots=True)
class DoctorCheck:
    section: str
    name: str
    status: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _version(executable: str, *args: str) -> str:
    try:
        proc = subprocess.run([executable, *args], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    text = (proc.stdout + "\n" + proc.stderr).strip()
    return text.splitlines()[0] if text else f"exit {proc.returncode}"


def _package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "missing"


def _latest_dir(path: Path) -> Path | None:
    if not path.is_dir():
        return None
    dirs = [item for item in path.iterdir() if item.is_dir()]
    return sorted(dirs, key=lambda p: p.name)[-1] if dirs else None


def collect_doctor_checks(config: ProjectConfig) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    python = shutil.which(config.python_executable) or shutil.which("python3")
    checks.append(DoctorCheck("runtime", "python", "ok" if python else "missing", _version(python, "--version") if python else config.python_executable))
    for package in ("pip", "setuptools", "wheel"):
        value = _package_version(package)
        checks.append(DoctorCheck("runtime", package, "ok" if value != "missing" else "missing", value))

    android_context = resolve_android_execution_context(config)
    for name in ("java", "gradle", "build-tools", "cmake", "aapt2", "apksigner", "zipalign", "ndk"):
        tool = android_context.tool(name)
        if tool.available and tool.compatible is not False:
            detail = tool.path
            if tool.version:
                detail += f" ({tool.version})"
            if tool.provider:
                detail += f" [{tool.provider}]"
            checks.append(DoctorCheck("android", name, "ok", detail))
        else:
            detail = tool.rejection_reason or "not discovered"
            checks.append(DoctorCheck("android", name, "optional", detail))

    if android_context.sdk_root:
        checks.append(DoctorCheck("android", "sdk", "ok", f"{android_context.sdk_root} [{android_context.sdk_source or 'discovered'}]"))
    else:
        checks.append(DoctorCheck("android", "sdk", "optional", "Android SDK not discovered"))

    adb = android_context.adb
    checks.append(DoctorCheck(
        "device", "adb",
        "ok" if adb.available and adb.compatible is not False else "optional",
        (f"{adb.path} [{adb.provider}]" if adb.path else (adb.rejection_reason or "unavailable")),
    ))
    device_report = AndroidDeviceManager(adb).doctor()
    checks.append(DoctorCheck(
        "device", "devices",
        "ok" if device_report.status == "ok" else "optional",
        f"{device_report.code}: {device_report.detail}",
    ))

    su = shutil.which("su")
    root_detail = "su unavailable"
    root_status = "optional"
    if su:
        try:
            proc = subprocess.run([su, "-c", "id -u"], capture_output=True, text=True, timeout=3, check=False)
            root_detail = (proc.stdout or proc.stderr).strip() or f"exit {proc.returncode}"
            root_status = "ok" if proc.returncode == 0 and proc.stdout.strip() == "0" else "optional"
        except (OSError, subprocess.TimeoutExpired) as exc:
            root_detail = str(exc)
    checks.append(DoctorCheck("device", "root", root_status, root_detail))

    checks.append(DoctorCheck("project", "git", "ok" if (config.repo_root / ".git").exists() else "missing", str(config.repo_root / ".git")))
    checks.append(DoctorCheck("project", "config", "ok" if config.config_path else "optional", str(config.config_path) if config.config_path else "using detected defaults"))
    target_job_count = sum(len(target.jobs) for target in config.targets.values())
    target_job_targets = [name for name, target in sorted(config.targets.items()) if target.jobs]
    checks.append(DoctorCheck(
        "project",
        "target-local-jobs",
        "ok" if target_job_count else "optional",
        (f"{target_job_count} jobs across targets: {', '.join(target_job_targets)}" if target_job_count else "not configured"),
    ))
    dangling_workflows = [
        f"{target_name}.{workflow_name}"
        for target_name, target in sorted(config.targets.items())
        for workflow_name, workflow in sorted(target.workflows.items())
        if any(step.kind == "job" and step.resolution_scope == "target" for step in workflow.steps)
    ]
    checks.append(DoctorCheck(
        "project",
        "target-local-workflows",
        "ok" if dangling_workflows else "optional",
        ("workflows using target-local jobs: " + ", ".join(dangling_workflows) if dangling_workflows else "not configured"),
    ))
    wrapper_command_names = sorted(config.wrapper.commands)
    checks.append(DoctorCheck(
        "project",
        "wrapper",
        "ok" if wrapper_command_names else "optional",
        (f"schema {config.wrapper.schema}; commands: {', '.join(wrapper_command_names)}" if wrapper_command_names else "not configured"),
    ))
    federation_names = sorted(config.federation.repositories)
    checks.append(DoctorCheck(
        "federation",
        "enrollment",
        "ok" if federation_names else "optional",
        (f"repositories: {', '.join(federation_names)}" if federation_names else "not configured"),
    ))
    for federation_name in federation_names:
        enrollment = config.federation.repositories[federation_name]
        try:
            resolution = resolve_federated_repository(config, federation_name)
        except FederationDiscoveryError as exc:
            checks.append(DoctorCheck(
                "federation",
                f"repository:{federation_name}",
                "missing",
                str(exc),
            ))
        else:
            if not resolution.available:
                status = "optional" if not enrollment.required else "missing"
                detail = f"{resolution.root}; {resolution.boundary_detail}"
            else:
                status = "ok"
                short_identity = (resolution.repository_identity.fingerprint[:12] if resolution.repository_identity else "missing")
                detail = (
                    f"{resolution.root}; relation={resolution.relation}; identity={short_identity}; "
                    f"target={resolution.selected_target or '-'}"
                )
            checks.append(DoctorCheck("federation", f"repository:{federation_name}", status, detail))
    try:
        federation_gate = build_federation_gate1_report(config)
    except Exception as exc:
        checks.append(DoctorCheck("federation", "gate1", "missing", f"FRX-04 preflight failed: {exc}"))
    else:
        checks.append(DoctorCheck(
            "federation",
            "gate1",
            "ok" if federation_gate.ok else "missing",
            f"FRX-04 status={federation_gate.status}; checks={len(federation_gate.checks)}",
        ))
    if config.wrapper.commands:
        launcher_report = launcher_status(config.repo_root)
        checks.append(DoctorCheck(
            "project",
            "wrapper-launchers",
            "ok" if launcher_report.ok else "optional",
            "current" if launcher_report.ok else ", ".join(f"{item.name}:{item.reason or 'not current'}" for item in launcher_report.files),
        ))
        if config.wrapper.default_command:
            checks.append(DoctorCheck("project", "wrapper-default", "ok", config.wrapper.default_command))
        missing_descriptions = [name for name, command in sorted(config.wrapper.commands.items()) if not command.description]
        checks.append(DoctorCheck(
            "project",
            "wrapper-docs",
            "ok" if not missing_descriptions else "optional",
            "all wrapper commands have descriptions" if not missing_descriptions else "commands without descriptions: " + ", ".join(missing_descriptions),
        ))
    runner_source = str(config.config_provenance.get("runner", "") or "")
    if "legacy" in runner_source:
        checks.append(DoctorCheck(
            "project",
            "identity-migration",
            "optional",
            'Legacy kind/type identity is still supported; prefer runner = "..." and platform = "..." for new configuration.',
        ))
    legacy_targets = [
        name for name, item in sorted(config.targets.items())
        if "runner" not in item.declared_fields and {"type", "kind"}.intersection(item.declared_fields)
    ]
    if legacy_targets:
        checks.append(DoctorCheck(
            "project",
            "target-identity-migration",
            "optional",
            "Legacy target type/kind aliases: " + ", ".join(legacy_targets),
        ))
    target = config.build_target or config.project_root or str(config.repo_root)
    checks.append(DoctorCheck("project", "build-target", "ok" if target else "optional", target or "not configured"))
    return checks


def doctor_payload(config: ProjectConfig) -> dict[str, Any]:
    checks = collect_doctor_checks(config)
    features = resolve_feature_flags(config.repo_root, config_path=config.config_path)
    return {
        "schema_version": 1,
        "repository": str(config.repo_root),
        "kind": config.kind,
        "runner": config.runner,
        "platform": config.platform,
        "selected_target": config.selected_target,
        "jobs": sorted(config.jobs),
        "target_jobs": {
            target_name: sorted(target.jobs)
            for target_name, target in sorted(config.targets.items())
            if target.jobs
        },
        "target_job_keys": sorted(
            str(job.metadata.get("job_key") or f"{target_name}.{local_name}")
            for target_name, target in sorted(config.targets.items())
            for local_name, job in sorted(target.jobs.items())
        ),
        "workflows": {
            name: sorted(target.workflows)
            for name, target in sorted(config.targets.items())
            if target.workflows
        },
        "wrapper": wrapper_as_dict(config.wrapper),
        "wrapper_launchers": launcher_status_as_dict(launcher_status(config.repo_root)),
        "federation": federation_boundary_payload(config, tolerate_errors=True),
        "healthy": not any(item.status == "missing" for item in checks),
        "features": features.to_dict(),
        "checks": [item.as_dict() for item in checks],
    }
