from __future__ import annotations

"""Transparent preparation for SSH command-mode build workers.

SSHW-03 connects the SSHW-01 worker runtime and SSHW-02 managed workspace to
normal Devtool execution.  The local checkout remains authoritative: managed
workers bootstrap their minimum runtime, synchronize exactly once per Devtool
execution plan, then execute phases from the verified remote workspace.  Android
uses the staged canonical builder for target-aware JDK/SDK/Gradle provisioning;
plain JVM Gradle uses the project wrapper where available and provisions only
missing Termux JDK/Gradle packages when policy permits.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import shlex
import subprocess
import uuid
from typing import Any, Iterable

from .execution_providers import SSH_PROOT_OCI, ensure_execution_provider_ready
from .ssh_tmux_runner import SSHRunnerConfig, build_ssh_remote_sh_argv, worker_session_name
from .ssh_multiplex import run_with_multiplex_fallback
from .ssh_worker import SSHWorkerProbe, bootstrap_worker, probe_worker
from .ssh_workspace import (
    SSHWorkspaceSpec,
    plan_workspace_sync,
    resolve_workspace_spec,
    sync_workspace,
    workspace_status,
)
from .ssh_resilience import SSHWorkspaceLease, ensure_remote_capacity, release_workspace_lease
from .ssh_lifecycle import ensure_execution_capacity


@dataclass(slots=True)
class SSHExecutionPreparation:
    remote_root: str
    worker: SSHWorkerProbe
    workspace: SSHWorkspaceSpec | None
    workspace_status: dict[str, Any]
    bootstrap: dict[str, Any]
    sync: dict[str, Any]
    toolchain: dict[str, Any]
    lease: dict[str, Any] = field(default_factory=dict)
    capacity: dict[str, Any] = field(default_factory=dict)
    run_id: str = ""
    session_prefix: str = ""

    def metadata(self) -> dict[str, Any]:
        return {
            "ssh_profile": self.worker.profile,
            "ssh_worker_platform": self.worker.platform,
            "ssh_worker_architecture": self.worker.architecture,
            "ssh_worker_cpu_count": self.worker.cpu_count,
            "ssh_worker_memory_kib": self.worker.memory_kib,
            "ssh_workspace_mode": self.workspace.mode if self.workspace else "existing",
            "ssh_workspace_id": self.workspace.workspace_id if self.workspace else "",
            "ssh_workspace_instance_id": self.workspace.instance_id if self.workspace else "",
            "ssh_workspace_path": self.remote_root,
            "ssh_sync_change_count": int(self.sync.get("change_count") or 0),
            "ssh_sync_automatic": bool(self.sync),
            "ssh_source_sync_id": str(self.sync.get("sync_id") or ""),
            "ssh_source_revision": dict(self.sync.get("source_revision") or {}),
            "ssh_source_transport_requested": str(self.sync.get("source_transport_requested") or ""),
            "ssh_source_transport_effective": str(self.sync.get("source_transport_effective") or ""),
            "federation_source_transport_requested": str(self.sync.get("source_transport_requested") or ""),
            "federation_source_transport_effective": str(self.sync.get("source_transport_effective") or ""),
            "federation_source_transport_evidence_digest": str(self.sync.get("source_transport_evidence_digest") or ""),
            "ssh_toolchain_kind": str(self.toolchain.get("kind") or "generic"),
            "ssh_toolchain_detail": str(self.toolchain.get("detail") or ""),
            "ssh_workspace_lease": dict(self.lease),
            "ssh_capacity": dict(self.capacity),
            "ssh_run_id": self.run_id,
            "ssh_session_prefix": self.session_prefix,
        }


def _external_specs(specs: Iterable[Any]) -> list[Any]:
    return [
        spec
        for spec in specs
        if not bool((getattr(spec, "metadata", {}) or {}).get("devtool_internal_validation"))
    ]


def _execution_kind(specs: Iterable[Any]) -> str:
    for spec in specs:
        metadata = dict(getattr(spec, "metadata", {}) or {})
        runner = str(metadata.get("runner") or metadata.get("project_type") or "").strip().lower()
        if metadata.get("android_builder") or runner == "android":
            return "android"
    for spec in specs:
        metadata = dict(getattr(spec, "metadata", {}) or {})
        runner = str(metadata.get("runner") or metadata.get("project_type") or "").strip().lower()
        if metadata.get("gradle_direct") or runner == "gradle":
            return "gradle"
    return "generic"


def _run_remote(config: SSHRunnerConfig, script: str, *, timeout: float = 90.0) -> subprocess.CompletedProcess[str]:
    return run_with_multiplex_fallback(
        config, build_ssh_remote_sh_argv(config, script, tty=False),
        check=False, capture_output=True, text=True, timeout=timeout,
    )


def _java_major(version_line: str) -> int:
    text = str(version_line or "")
    match = re.search(r'(?:version\s+"|openjdk\s+)(\d+)(?:\.(\d+))?', text, re.IGNORECASE)
    if not match:
        return 0
    first = int(match.group(1))
    second = int(match.group(2) or 0)
    return second if first == 1 else first


def _required_gradle_version(specs: Iterable[Any]) -> str:
    versions = {
        str((getattr(spec, "metadata", {}) or {}).get("gradle_required_version") or "").strip()
        for spec in specs
    } - {""}
    if len(versions) > 1:
        raise ValueError("SSH Gradle execution plan contains conflicting required Gradle versions: " + ", ".join(sorted(versions)))
    return next(iter(versions), "")


def _required_java(specs: Iterable[Any]) -> tuple[int, int | None]:
    # JvmGradleRunner carries the inferred project contract. Keep the fallback
    # permissive for custom gradle_direct specs rather than inventing Java 17.
    minimum = 8
    exacts: list[int] = []
    for spec in specs:
        metadata = dict(getattr(spec, "metadata", {}) or {})
        try:
            value = int(metadata.get("java_requirement_min") or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            minimum = max(minimum, value)
        try:
            exact = int(metadata.get("java_requirement_exact") or 0)
        except (TypeError, ValueError):
            exact = 0
        if exact:
            exacts.append(exact)
    return minimum, max(exacts) if exacts else None


def _termux_jdk_package(minimum: int, exact: int | None) -> str:
    requested = exact or minimum
    # Current Termux repositories commonly expose the LTS JDK lines. Do not
    # silently install a newer JDK when the project declares an exact toolchain.
    if exact is not None:
        if exact in {17, 21}:
            return f"openjdk-{exact}"
        raise ValueError(
            f"SSH Gradle worker requires exact Java {exact}; automatic Termux provisioning supports Java 17 or 21. "
            "Install the requested JDK on the worker or use an Android target whose staged builder owns toolchain provisioning."
        )
    if requested <= 17:
        return "openjdk-17"
    if requested <= 21:
        return "openjdk-21"
    raise ValueError(
        f"SSH Gradle worker requires Java {requested}+; automatic Termux provisioning currently supports up to Java 21"
    )


def _local_gradle_wrapper(spec: Any) -> Path | None:
    metadata = dict(getattr(spec, "metadata", {}) or {})
    if not metadata.get("gradle_direct"):
        return None
    cwd = Path(str(getattr(spec, "cwd", "") or ".")).expanduser()
    wrapper = cwd / ("gradlew.bat" if os.name == "nt" else "gradlew")
    return wrapper.resolve() if wrapper.is_file() else None


def _remote_gradle_probe(config: SSHRunnerConfig) -> dict[str, Any]:
    script = r'''set +e
java_cmd="$(command -v java 2>/dev/null || true)"
gradle_cmd="$(command -v gradle 2>/dev/null || true)"
timeout_cmd="$(command -v timeout 2>/dev/null || true)"
printf 'java=%s\n' "$java_cmd"
printf 'gradle=%s\n' "$gradle_cmd"
if [ -n "$java_cmd" ]; then
  if [ -n "$timeout_cmd" ]; then
    "$timeout_cmd" 10 "$java_cmd" -version 2>&1 | awk 'NF {print "java_version=" $0; exit}'
  else
    "$java_cmd" -version 2>&1 | awk 'NF {print "java_version=" $0; exit}'
  fi
fi
if [ -n "$gradle_cmd" ] && [ -n "$timeout_cmd" ]; then
  "$timeout_cmd" 10 "$gradle_cmd" --version 2>/dev/null | awk '/^Gradle / {print "gradle_version=" $2; exit}'
fi
'''
    completed = _run_remote(config, script)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"SSH remote Gradle toolchain probe failed with exit {completed.returncode}: {detail or 'no diagnostic output'}")
    values: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return {
        "java": values.get("java", ""),
        "java_version": values.get("java_version", ""),
        "java_major": _java_major(values.get("java_version", "")),
        "gradle": values.get("gradle", ""),
        "gradle_version": values.get("gradle_version", ""),
    }


def _install_termux_packages(config: SSHRunnerConfig, worker: SSHWorkerProbe, packages: list[str], *, dry_run: bool) -> dict[str, Any]:
    packages = list(dict.fromkeys(item for item in packages if item))
    if not packages:
        return {"ok": True, "changed": False, "packages": [], "detail": "remote toolchain already ready"}
    if worker.platform != "termux" or worker.package_manager != "pkg":
        raise ValueError(
            "Automatic SSH Gradle toolchain provisioning currently requires a Termux worker; "
            f"remote platform is {worker.platform} with package manager {worker.package_manager or 'unknown'}"
        )
    if config.bootstrap_policy == "never":
        raise ValueError(
            "SSH worker toolchain is incomplete and bootstrap policy is 'never'; install "
            + ", ".join(packages)
            + " on the remote worker or allow bootstrap.policy='missing'"
        )
    pkg = (worker.prefix.rstrip("/") + "/bin/pkg") if worker.prefix else "pkg"
    command = f"set -eu; {shlex.quote(pkg)} install -y " + " ".join(shlex.quote(item) for item in packages)
    if dry_run:
        return {"ok": True, "changed": False, "dry_run": True, "packages": packages, "command": command, "detail": "toolchain provisioning plan only"}
    completed = _run_remote(config, command, timeout=1200.0)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"SSH Termux toolchain provisioning failed with exit {completed.returncode}: {detail or 'no diagnostic output'}")
    return {"ok": True, "changed": True, "packages": packages, "detail": "installed missing remote toolchain packages"}


def ensure_remote_toolchain(
    config: SSHRunnerConfig,
    specs: Iterable[Any],
    *,
    worker: SSHWorkerProbe,
    dry_run: bool,
) -> dict[str, Any]:
    external = _external_specs(specs)
    kind = _execution_kind(external)
    if kind == "android":
        # The staged Android builder is deliberately the single owner of Android
        # JDK/SDK/NDK/Gradle resolution. It runs on the tablet, so its managed
        # caches live under the tablet HOME and survive source synchronization.
        return {
            "ok": True,
            "kind": "android",
            "owner": "staged-android-builder",
            "persistent_caches": ["$HOME/.gradle", "$HOME/.cache/devtool"],
            "detail": "Android JDK/SDK/NDK/Gradle provisioning delegated to the staged remote builder",
        }
    if kind != "gradle":
        return {"ok": True, "kind": kind, "detail": "no additional remote build toolchain provisioning required"}

    minimum, exact = _required_java(external)
    required_gradle = _required_gradle_version(external)
    wrappers = [wrapper for spec in external if (wrapper := _local_gradle_wrapper(spec)) is not None]
    probe = _remote_gradle_probe(config)
    java_major = int(probe.get("java_major") or 0)
    java_ok = java_major >= minimum and (exact is None or java_major == exact)
    packages: list[str] = []
    if not java_ok:
        packages.append(_termux_jdk_package(minimum, exact))
    if not wrappers and not probe.get("gradle"):
        packages.append("gradle")
    install = _install_termux_packages(config, worker, packages, dry_run=dry_run)
    if dry_run:
        return {
            "ok": True,
            "kind": "gradle",
            "java_minimum": minimum,
            "java_exact": exact,
            "wrapper": bool(wrappers),
            "gradle_required_version": required_gradle,
            "probe": probe,
            "provision": install,
            "detail": "Gradle/JVM remote toolchain plan ready",
        }

    after = _remote_gradle_probe(config) if packages else probe
    after_java = int(after.get("java_major") or 0)
    if after_java < minimum or (exact is not None and after_java != exact):
        requirement = f"exact Java {exact}" if exact is not None else f"Java {minimum}+"
        raise ValueError(f"SSH Gradle worker does not provide {requirement} after provisioning")
    if not wrappers and not after.get("gradle"):
        raise ValueError("SSH Gradle worker has no project wrapper and no usable gradle executable after provisioning")
    if not wrappers and required_gradle:
        from . import android_build
        actual_gradle = str(after.get("gradle_version") or "")
        if not android_build.gradle_version_matches(actual_gradle, required_gradle):
            raise ValueError(
                f"SSH Gradle worker provides Gradle {actual_gradle or 'unknown'}, but {required_gradle} is required and the project has no gradlew wrapper. "
                "Install the required Gradle version on the worker or add/use the project Gradle wrapper."
            )
    return {
        "ok": True,
        "kind": "gradle",
        "java_minimum": minimum,
        "java_exact": exact,
        "wrapper": bool(wrappers),
        "gradle_required_version": required_gradle,
        "probe": after,
        "provision": install,
        "persistent_caches": ["$HOME/.gradle", "$HOME/.cache/devtool"],
        "detail": "Gradle/JVM remote toolchain ready; project wrapper preferred when present",
    }


def prepare_ssh_command_execution(
    config: SSHRunnerConfig,
    specs: Iterable[Any],
    *,
    repo_root: str | Path,
    dry_run: bool = False,
) -> SSHExecutionPreparation:
    ensure_execution_provider_ready(config.execution_provider, remote=True)
    if config.execution_provider == SSH_PROOT_OCI:
        if not config.profile:
            raise ValueError("ssh-proot-oci repository execution requires a named SSH profile")
        if config.workspace_mode != "managed":
            raise ValueError("ssh-proot-oci repository execution requires workspace.mode='managed'")
    spec_list = list(specs)
    external = _external_specs(spec_list)
    if not external:
        worker = probe_worker(config)
        return SSHExecutionPreparation(
            remote_root=str(config.cwd or ""), worker=worker, workspace=None,
            workspace_status={}, bootstrap={}, sync={},
            toolchain={"ok": True, "kind": "internal", "detail": "no remote command phases"},
        )

    bootstrap: dict[str, Any] = {}
    worker = probe_worker(config)
    if config.profile and not worker.ready:
        bootstrap = bootstrap_worker(config, dry_run=dry_run)
        if not bool(bootstrap.get("ok")):
            raise ValueError(str(bootstrap.get("detail") or "SSH worker bootstrap failed"))
        if not dry_run:
            worker = probe_worker(config)

    capacity = ensure_execution_capacity(config, repo_root=repo_root, free_storage_kib=worker.free_storage_kib)
    workspace: SSHWorkspaceSpec | None = None
    sync: dict[str, Any] = {}
    status_payload: dict[str, Any] = {}
    lease_obj = None
    run_identity = str(config.operation_id or os.environ.get("DEVTOOL_RUN_ID") or "").strip()
    session_prefix = ""

    try:
        if config.workspace_mode == "existing":
            workspace = resolve_workspace_spec(config, repo_root, remote_home=worker.home)
            status = workspace_status(config, workspace)
            if not status.exists or not status.is_directory or not status.safe:
                raise ValueError(f"Existing SSH workspace is unavailable or unsafe: {workspace.remote_path}: {status.detail}")
            remote_root = status.canonical_path or workspace.remote_path
            status_payload = status.as_dict()
            if not run_identity:
                run_identity = workspace.instance_id
            session_prefix = worker_session_name(config, operation="commands", repository=remote_root, run_id=run_identity)
        else:
            if config.workspace_mode == "ephemeral" and not run_identity:
                run_identity = uuid.uuid4().hex[:12]
            workspace = resolve_workspace_spec(
                config,
                repo_root,
                operation_id=run_identity,
                remote_home=worker.home,
            )
            if not run_identity:
                run_identity = workspace.instance_id
            if dry_run:
                if worker.tools.get("rsync"):
                    sync = sync_workspace(config, workspace, dry_run=True, worker=worker)
                else:
                    # Strict/eligible git-bundle transport does not depend on rsync.
                    # Ask the source-transport planner first so dry-run/preflight
                    # truth does not incorrectly describe a bundle child as rsync.
                    from .federation_git_transport import plan_federated_git_source
                    from .invocation_origin import federation_lineage_from_environment
                    source_plan = plan_federated_git_source(
                        repo_root,
                        lineage=federation_lineage_from_environment() or {},
                        requested_transport=config.source_transport,
                    )
                    if source_plan.effective_transport == "git-bundle":
                        sync = sync_workspace(config, workspace, dry_run=True, worker=worker)
                    else:
                        sync = {
                            "schema_version": 1,
                            "kind": "ssh-workspace-sync-plan",
                            "ok": True,
                            "dry_run": True,
                            "workspace": workspace.as_dict(),
                            "changes": [],
                            "change_count": 0,
                            "would_create_workspace": True,
                            "source_transport_requested": source_plan.requested_transport,
                            "source_transport_effective": source_plan.effective_transport,
                            "transport": {"source_transport": source_plan.as_dict()},
                            "detail": "rsync planning deferred until the planned worker bootstrap installs rsync",
                        }
                remote_root = workspace.remote_path
                session_prefix = worker_session_name(config, operation="commands", repository=remote_root, run_id=run_identity)
            else:
                sync = sync_workspace(config, workspace, dry_run=False, retain_lease=True, worker=worker)
                if not bool(sync.get("ok")):
                    raise ValueError(str(sync.get("detail") or "SSH managed workspace synchronization failed"))
                after = dict(sync.get("status_after") or {})
                remote_root = str(after.get("canonical_path") or workspace.remote_path)
                status_payload = after
                lease_payload = sync.get("lease") if isinstance(sync.get("lease"), dict) else {}
                if lease_payload:
                    lease_obj = SSHWorkspaceLease(**lease_payload)
                session_prefix = str((lease_payload or {}).get("session_prefix") or "") or worker_session_name(
                    config, operation="commands", repository=remote_root, run_id=run_identity
                )

        if config.execution_provider == SSH_PROOT_OCI:
            toolchain = {
                "ok": True,
                "kind": _execution_kind(external),
                "owner": "immutable-oci-image",
                "host_provisioning": False,
                "persistent_caches": [
                    "/var/cache/devtool/gradle", "/var/cache/devtool/xdg", "/var/cache/devtool/npm",
                    "/var/cache/devtool/pip", "/var/cache/devtool/uv",
                ],
                "detail": "Repository toolchain owned by immutable OCI image; SSH host toolchain provisioning disabled",
            }
        else:
            toolchain = ensure_remote_toolchain(config, external, worker=worker, dry_run=dry_run)
    except Exception:
        if lease_obj is not None:
            release_workspace_lease(config, lease_obj)
        raise

    return SSHExecutionPreparation(
        remote_root=remote_root,
        worker=worker,
        workspace=workspace,
        workspace_status=status_payload,
        bootstrap=bootstrap,
        sync=sync,
        toolchain=toolchain,
        lease=lease_obj.as_dict() if lease_obj is not None else {},
        capacity=capacity,
        run_id=run_identity,
        session_prefix=session_prefix,
    )
