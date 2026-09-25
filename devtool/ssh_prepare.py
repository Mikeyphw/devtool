from __future__ import annotations

"""Explicit SSH worker/runtime and target-toolchain preparation.

``devtool ssh prepare`` deliberately does not synchronize, clone, fetch, or
switch repository source.  It classifies the existing remote worker first,
bootstraps only missing worker-runtime pieces when policy permits, then delegates
target-specific toolchain preparation to the same execution contract used by
normal SSH execution.
"""

from pathlib import Path
from typing import Any

from .config import load_project_config
from .runners import build_specs
from .ssh_environment import environment_payload
from .ssh_execution import ensure_remote_toolchain
from .ssh_multiplex import evidence as multiplex_evidence
from .ssh_lifecycle import ensure_execution_capacity
from .ssh_tmux_runner import SSHRunnerConfig
from .ssh_worker import bootstrap_worker, probe_worker
from .ssh_workspace import resolve_workspace_spec, workspace_status


def _classify_worker(probe: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for name in ("python", "tmux", "rsync", "git"):
        path = str(probe.tools.get(name) or "")
        result.append({
            "component": name,
            "classification": "reusable" if path else ("needs-install" if probe.bootstrap_supported else "blocked"),
            "path": path,
            "provenance": "termux-existing" if path and probe.platform == "termux" else ("remote-existing" if path else "missing"),
        })
    return result


def _classify_environment(env: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for name, item in dict(env.get("tools") or {}).items():
        path = str((item or {}).get("path") or "")
        result.append({
            "component": name,
            "classification": "reusable" if path else "needs-configuration",
            "path": path,
            "version": str((item or {}).get("version") or ""),
            "provenance": str((item or {}).get("provenance") or "missing"),
        })
    sdk = dict(env.get("android") or {})
    result.append({
        "component": "android-sdk",
        "classification": "reusable" if sdk.get("sdk_root") else "needs-configuration",
        "path": str(sdk.get("sdk_root") or ""),
        "provenance": str(sdk.get("provenance") or "missing"),
    })
    return result


def prepare_worker_target(
    config: SSHRunnerConfig,
    repo_root: str | Path,
    *,
    target: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    repo = Path(repo_root).expanduser().resolve()
    before = probe_worker(config)
    capacity = ensure_execution_capacity(config, repo_root=repo, free_storage_kib=before.free_storage_kib)
    worker_plan = _classify_worker(before)

    missing = list(before.missing_required)
    bootstrap: dict[str, Any]
    if missing:
        bootstrap = bootstrap_worker(config, dry_run=dry_run, force=False)
        if not bootstrap.get("ok"):
            return {
                "schema_version": 1, "kind": "ssh-prepare", "ok": False,
                "dry_run": dry_run, "profile": config.profile, "destination": config.destination,
                "target": target, "worker_runtime": worker_plan, "bootstrap": bootstrap,
                "capacity": capacity, "multiplex": multiplex_evidence(config).as_dict(),
                "detail": str(bootstrap.get("detail") or "worker runtime preparation blocked"),
            }
        worker = before if dry_run else probe_worker(config)
    else:
        worker = before
        bootstrap = {
            "ok": True, "changed": False, "dry_run": dry_run,
            "missing_before": [], "installed": [], "detail": "worker runtime already reusable",
        }

    env = environment_payload(config, repo_root=repo, target=target)
    try:
        project = load_project_config(
            str(repo), None,
            target_name=target,
            allow_ambiguous_target=not bool(target),
            allow_ambiguous_project_kind=True,
        )
        specs = build_specs(project, "build")
    except SystemExit as exc:
        raise ValueError(str(exc)) from exc

    toolchain = ensure_remote_toolchain(config, specs, worker=worker, dry_run=dry_run)
    spec = resolve_workspace_spec(
        config, repo,
        operation_id="prepare-preview" if config.workspace_mode == "ephemeral" else "",
        remote_home=worker.home,
    )
    status = workspace_status(config, spec)
    toolchain_classification = "reusable"
    if not bool(toolchain.get("ok", True)):
        toolchain_classification = "blocked"
    elif dry_run and bool(toolchain.get("changed")):
        toolchain_classification = "needs-install"
    return {
        "schema_version": 1,
        "kind": "ssh-prepare",
        "ok": bool(toolchain.get("ok", True)) and bool(bootstrap.get("ok", True)),
        "dry_run": bool(dry_run),
        "profile": config.profile,
        "destination": config.destination,
        "target": target,
        "worker_runtime": _classify_worker(worker),
        "bootstrap": bootstrap,
        "target_toolchain": toolchain,
        "target_toolchain_classification": toolchain_classification,
        "environment": env,
        "toolchain_inventory": _classify_environment(env),
        "classification_vocabulary": [
            "reusable", "needs-configuration", "needs-install", "blocked", "incompatible",
        ],
        "workspace": {"spec": spec.as_dict(), "status": status.as_dict(), "mutated": False},
        "capacity": capacity,
        "multiplex": multiplex_evidence(config).as_dict(),
        "source_operations": {"clone": False, "fetch": False, "checkout": False, "sync": False},
        "detail": "preparation plan ready" if dry_run else "worker runtime and target toolchain prepared without changing repository source",
    }


__all__ = ["prepare_worker_target"]
