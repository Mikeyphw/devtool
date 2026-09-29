from __future__ import annotations

"""SSH + tmux execution transport.

Two modes are supported. ``devtool`` delegates the complete devtool invocation
to an already-installed remote devtool. ``commands`` keeps the outer devtool
local and wraps planned external command phases over SSH, matching the chroot
toolchain-runner architecture.

SSH never invents a chroot-style remote home. In command mode, ``ssh.cwd`` is
the *explicit* remote counterpart of the local repository root; only that
user-declared mapping is used. Local overlay/snapshot archives can be staged to
the remote host over SCP, and command-mode Android/Gradle output artifacts can
be pulled back over SCP before local artifact verification.
"""

import hashlib
import os
import re
import shlex
import subprocess
import shutil
import uuid
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from .execution_providers import SSH_NATIVE, SSH_PROOT_OCI, normalize_execution_provider
from .oci_resilience import phase_metadata_lines, phase_node_id


_SSH_VALUE_FLAGS = {
    "--ssh",
    "--ssh-host",
    "--ssh-profile",
    "--ssh-port",
    "--ssh-user",
    "--ssh-identity",
    "--ssh-session",
    "--ssh-cwd",
    "--ssh-tool",
    "--ssh-mode",
    "--ssh-stage-dir",
}
_GLOBAL_VALUE_FLAGS = {
    "-r", "--repo", "-c", "--config", "--solution", "--project",
    "--machine-file", "--android-sdk-root", "--java-home", "--gradle-command",
    "--gradle-user-home", "-o", "--output", "--target", "--variant",
    "--runner", "--project-type", "--performance-file", "--copy-via", "--theme",
    "--stdout-payload", "--hud-mode", "--chroot", "--execution-environment",
    "--matrix", "--matrix-mode", "--toolchain-profile", "--group",
}
_ARTIFACT_COMMAND_VALUE_FLAGS = _GLOBAL_VALUE_FLAGS | _SSH_VALUE_FLAGS | {
    "--test-profile", "--select", "--id", "--tag", "--exclude-tag", "--path",
    "--class", "--tests", "--method", "--filter", "--task", "--test-task",
    "--changed-from", "--phase", "--phase-only", "--through", "--from-phase",
    "--from", "--pause-after", "--max-phases", "--max-runtime",
    "--stop-below-battery", "--stop-above-temperature", "--stop-below-disk-mb",
    "--resource-profile", "--profile",
    "--max-infrastructure-retries", "--max-resource-retries", "--failure-action",
    "--session", "--scope", "--if-revision", "--after-event", "--event-limit",
    "--limit", "--max-workers", "--cpu-limit", "--gradle-heap-mb",
    "--memory-guard-mb", "--commit", "--group",
}
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}

def _bool_value(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return default

def _transport_timeout() -> float:
    raw = str(os.environ.get("DEVTOOL_SSH_TRANSPORT_TIMEOUT", "30") or "30").strip()
    try:
        return max(1.0, min(float(raw), 3600.0))
    except ValueError:
        return 30.0


@dataclass(slots=True)
class SSHRunnerConfig:
    host: str = ""
    port: int = 22
    user: str = ""
    identity: str = ""
    session: str = ""
    cwd: str = ""
    tool: str = "devtool"
    mode: str = "devtool"
    stage_dir: str = "~/.cache/devtool/ssh-stage"
    copy_artifacts: bool = True
    detached: bool = False
    config_path: str = ""
    operation_id: str = ""
    profile: str = ""
    bootstrap_policy: str = "missing"
    worker_tmux: bool = False
    workspace_mode: str = "existing"
    workspace_root: str = "~/.cache/devtool/workspaces"
    sync_mode: str = "incremental"
    source_transport: str = "auto"
    sync_delete: bool = False
    sync_default_excludes: bool = True
    sync_excludes: tuple[str, ...] = ()
    host_key_policy: str = "system"
    known_hosts: str = ""
    run_state_root: str = "~/.cache/devtool/ssh-runs"
    lease_timeout_seconds: int = 900
    min_free_mb: int = 1024
    execution_provider: str = SSH_NATIVE
    multiplex: bool = True
    control_persist_seconds: int = 90
    control_root: str = ""
    environment_policy: str = "remote"
    environment_allowlist: tuple[str, ...] = ("CI", "TERM", "COLORTERM", "LANG", "LC_ALL", "TZ")
    environment_overlay: dict[str, str] = field(default_factory=dict)
    oci_cache_root: str = "~/.cache/devtool/oci/workspace-caches"
    oci_env_allowlist: tuple[str, ...] = ("CI", "TERM", "COLORTERM", "LANG", "LC_ALL", "TZ")
    oci_image: str = "devtool/android-build:arm64-api36-v2"
    oci_container: str = "devtool-android-api36"
    oci_architecture: str = "aarch64"
    oci_pull_policy: str = "if-missing"
    oci_digest_policy: str = "record"

    @property
    def destination(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in _TRUE


def _positive_port(value: Any, default: int = 22) -> int:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        port = int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"SSH port must be an integer, got {value!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"SSH port must be between 1 and 65535, got {port}")
    return port


def _find_value(argv: list[str], flags: set[str]) -> str:
    last = ""
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in flags:
            if index + 1 < len(argv):
                last = argv[index + 1]
                index += 2
                continue
        for flag in flags:
            if flag.startswith("--") and token.startswith(flag + "="):
                last = token.split("=", 1)[1]
                break
        index += 1
    return last


def _config_path_from_args(argv: list[str], cwd: str) -> Path | None:
    """Find only a *local* config that may contain SSH launcher settings.

    An explicit --config/--repo is considered only when that path exists on the
    local machine. This avoids treating a remote path as if it belonged to the
    local filesystem.
    """
    explicit = _find_value(argv, {"-c", "--config"}).strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = Path(cwd) / path
        if path.is_file():
            return path

    repo = _find_value(argv, {"-r", "--repo"}).strip()
    if repo:
        root = Path(repo).expanduser()
        if not root.is_absolute():
            root = Path(cwd) / root
        candidate = root / ".devtool.toml"
        if candidate.is_file():
            return candidate

    candidate = Path(cwd) / ".devtool.toml"
    return candidate if candidate.is_file() else None


def _merge_config_maps(defaults: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _merge_config_maps(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _read_ssh_toml(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Unable to read SSH settings from {path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _load_raw_config(argv: list[str], cwd: str) -> tuple[dict[str, Any], Path | None]:
    # SSH workers are machine/user resources first and repository overrides
    # second.  A globally-added tablet can therefore be reused by every repo,
    # while a repo-local [ssh] or [ssh.hosts.*] table wins field-by-field.
    from .config import global_config_path

    global_path = global_config_path()
    local_path = _config_path_from_args(argv, cwd)
    global_data = _read_ssh_toml(global_path)
    local_data = _read_ssh_toml(local_path)
    merged = _merge_config_maps(global_data, local_data)
    source = local_path if local_path is not None else (global_path if global_data else None)
    return merged, source


def _profile_name(data: Mapping[str, Any], explicit: str = "") -> str:
    if explicit:
        return explicit.strip()
    top = data.get("ssh", {})
    if isinstance(top, dict):
        return str(top.get("profile", top.get("default_profile", "")) or "").strip()
    return ""


def _ssh_profile_section(data: Mapping[str, Any], profile: str) -> dict[str, Any]:
    if not profile:
        return {}
    top = data.get("ssh", {})
    if not isinstance(top, dict):
        return {}
    containers = []
    for key in ("hosts", "profiles", "workers"):
        value = top.get(key, {})
        if isinstance(value, dict):
            containers.append(value)
    for container in containers:
        if profile not in container:
            continue
        candidate = container.get(profile)
        if isinstance(candidate, dict):
            result = dict(candidate)
            bootstrap = candidate.get("bootstrap", {})
            if isinstance(bootstrap, dict) and "policy" in bootstrap:
                result["bootstrap_policy"] = bootstrap.get("policy")
            worker = candidate.get("worker", {})
            if isinstance(worker, dict) and "tmux" in worker:
                result["worker_tmux"] = worker.get("tmux")
            resilience = candidate.get("resilience", {})
            if isinstance(resilience, dict):
                if "host_key_policy" in resilience:
                    result["host_key_policy"] = resilience.get("host_key_policy")
                if "known_hosts" in resilience:
                    result["known_hosts"] = resilience.get("known_hosts")
                if "run_root" in resilience:
                    result["run_state_root"] = resilience.get("run_root")
                if "lease_timeout_seconds" in resilience:
                    result["lease_timeout_seconds"] = resilience.get("lease_timeout_seconds")
                if "min_free_mb" in resilience:
                    result["min_free_mb"] = resilience.get("min_free_mb")
            workspace = candidate.get("workspace", {})
            if isinstance(workspace, dict):
                if "mode" in workspace:
                    result["workspace_mode"] = workspace.get("mode")
                if "root" in workspace:
                    result["workspace_root"] = workspace.get("root")
            sync = candidate.get("sync", {})
            if isinstance(sync, dict):
                if "mode" in sync:
                    result["sync_mode"] = sync.get("mode")
                if "delete" in sync:
                    result["sync_delete"] = sync.get("delete")
                if "default_excludes" in sync:
                    result["sync_default_excludes"] = sync.get("default_excludes")
                if "exclude" in sync:
                    result["sync_excludes"] = sync.get("exclude")
            transport = candidate.get("transport", {})
            if isinstance(transport, dict):
                if "multiplex" in transport:
                    result["multiplex"] = transport.get("multiplex")
                if "control_persist_seconds" in transport:
                    result["control_persist_seconds"] = transport.get("control_persist_seconds")
                if "control_root" in transport:
                    result["control_root"] = transport.get("control_root")
                if "source_transport" in transport:
                    result["source_transport"] = transport.get("source_transport")
            environment = candidate.get("environment", {})
            if isinstance(environment, dict):
                if "policy" in environment:
                    result["environment_policy"] = environment.get("policy")
                if "allowlist" in environment:
                    result["environment_allowlist"] = environment.get("allowlist")
                if "overlay" in environment:
                    result["environment_overlay"] = environment.get("overlay")
            execution = candidate.get("execution", {})
            if isinstance(execution, dict) and "provider" in execution:
                result["execution_provider"] = execution.get("provider")
            oci = candidate.get("oci", {})
            if isinstance(oci, dict):
                if "cache_root" in oci:
                    result["oci_cache_root"] = oci.get("cache_root")
                if "env_allowlist" in oci:
                    result["oci_env_allowlist"] = oci.get("env_allowlist")
                for key in ("image", "container", "architecture", "pull_policy", "digest_policy"):
                    if key in oci:
                        result["oci_" + key] = oci.get(key)
            return result
    available = sorted({str(name) for container in containers for name in container})
    suffix = f" Available profiles: {', '.join(available)}." if available else ""
    raise ValueError(f"SSH profile {profile!r} is not defined under [ssh.hosts.*].{suffix}")


def _ssh_section(data: Mapping[str, Any], target_name: str = "", profile: str = "") -> dict[str, Any]:
    merged: dict[str, Any] = {}
    top = data.get("ssh", {})
    if isinstance(top, dict):
        # Do not leak profile containers into scalar runner options.
        merged.update({k: v for k, v in top.items() if k not in {"hosts", "profiles", "workers", "profile", "default_profile", "oci", "transport", "environment"}})
        transport = top.get("transport", {})
        if isinstance(transport, dict):
            if "multiplex" in transport:
                merged["multiplex"] = transport.get("multiplex")
            if "control_persist_seconds" in transport:
                merged["control_persist_seconds"] = transport.get("control_persist_seconds")
            if "control_root" in transport:
                merged["control_root"] = transport.get("control_root")
            if "source_transport" in transport:
                merged["source_transport"] = transport.get("source_transport")
        environment = top.get("environment", {})
        if isinstance(environment, dict):
            if "policy" in environment:
                merged["environment_policy"] = environment.get("policy")
            if "allowlist" in environment:
                merged["environment_allowlist"] = environment.get("allowlist")
            if "overlay" in environment:
                merged["environment_overlay"] = environment.get("overlay")
        oci = top.get("oci", {})
        if isinstance(oci, dict):
            if "cache_root" in oci:
                merged["oci_cache_root"] = oci.get("cache_root")
            if "env_allowlist" in oci:
                merged["oci_env_allowlist"] = oci.get("env_allowlist")
            for key in ("image", "container", "architecture", "pull_policy", "digest_policy"):
                if key in oci:
                    merged["oci_" + key] = oci.get(key)

    # A named profile provides the worker baseline. Explicit execution/target
    # settings still override it for backward-compatible per-target tuning.
    merged.update(_ssh_profile_section(data, profile))

    execution = data.get("execution", {})
    if isinstance(execution, dict):
        nested = execution.get("ssh", {})
        if isinstance(nested, dict):
            merged.update(nested)
        for key in (
            "ssh_host", "ssh_port", "ssh_user", "ssh_identity",
            "ssh_session", "ssh_cwd", "ssh_tool", "ssh_mode", "ssh_stage_dir",
            "ssh_copy_artifacts",
        ):
            if key in execution:
                merged[key.removeprefix("ssh_")] = execution[key]

    if target_name:
        targets = data.get("targets", {})
        target = targets.get(target_name, {}) if isinstance(targets, dict) else {}
        if isinstance(target, dict):
            nested = target.get("ssh", {})
            if isinstance(nested, dict):
                merged.update({k: v for k, v in nested.items() if k != "oci"})
                oci = nested.get("oci", {})
                if isinstance(oci, dict):
                    if "cache_root" in oci:
                        merged["oci_cache_root"] = oci.get("cache_root")
                    if "env_allowlist" in oci:
                        merged["oci_env_allowlist"] = oci.get("env_allowlist")
                    for key in ("image", "container", "architecture", "pull_policy", "digest_policy"):
                        if key in oci:
                            merged["oci_" + key] = oci.get(key)
            for key in (
                "ssh_host", "ssh_port", "ssh_user", "ssh_identity",
                "ssh_session", "ssh_cwd", "ssh_tool", "ssh_mode", "ssh_stage_dir",
                "ssh_copy_artifacts",
            ):
                if key in target:
                    merged[key.removeprefix("ssh_")] = target[key]
    return merged


def configured_ssh_environment(data: Mapping[str, Any], target_name: str = "") -> str:
    if target_name:
        targets = data.get("targets", {})
        target = targets.get(target_name, {}) if isinstance(targets, dict) else {}
        if isinstance(target, dict):
            value = str(target.get("execution_environment", target.get("environment", "")) or "").strip().lower().replace("_", "-")
            if value:
                return value
    execution = data.get("execution", {})
    if isinstance(execution, dict):
        value = str(execution.get("environment", execution.get("mode", "")) or "").strip().lower().replace("_", "-")
        if value:
            return value
    project = data.get("project", {})
    if isinstance(project, dict):
        return str(project.get("execution_environment", "") or "").strip().lower().replace("_", "-")
    return ""


def ssh_requested(argv: list[str], *, cwd: str | None = None, env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    if _truthy(env.get("DEVTOOL_INSIDE_SSH")):
        return False
    if _find_value(argv, {"--ssh", "--ssh-host", "--ssh-profile"}).strip():
        return True
    requested = _find_value(argv, {"--execution-environment"}).strip().lower().replace("_", "-")
    if requested == "ssh":
        return True
    data, _ = _load_raw_config(argv, cwd or os.getcwd())
    target = _find_value(argv, {"--target"}).strip()
    return configured_ssh_environment(data, target) == "ssh"


def resolve_ssh_config(
    argv: list[str],
    *,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> SSHRunnerConfig:
    env = os.environ if env is None else env
    cwd = cwd or os.getcwd()
    data, config_path = _load_raw_config(argv, cwd)
    target_name = _find_value(argv, {"--target"}).strip()
    cli_profile = _find_value(argv, {"--ssh-profile"}).strip()
    env_profile = str(env.get("DEVTOOL_SSH_PROFILE", "") or "").strip()
    cli_host = _find_value(argv, {"--ssh", "--ssh-host"}).strip()
    env_host = str(env.get("DEVTOOL_SSH_HOST", "") or "").strip()
    # A user-global default profile is convenient for `devtool ssh ...` and
    # --execution-environment=ssh, but it must not change the semantics of the
    # legacy raw `--ssh HOST` / DEVTOOL_SSH_HOST path. Explicit --ssh-profile
    # may still be combined with a raw host override intentionally.
    if (cli_host or env_host) and not (cli_profile or env_profile):
        profile = ""
    else:
        profile = _profile_name(data, cli_profile or env_profile)
    file_options = _ssh_section(data, target_name, profile)

    cli_port = _find_value(argv, {"--ssh-port"}).strip()
    cli_user = _find_value(argv, {"--ssh-user"}).strip()
    cli_identity = _find_value(argv, {"--ssh-identity"}).strip()
    cli_session = _find_value(argv, {"--ssh-session"}).strip()
    cli_cwd = _find_value(argv, {"--ssh-cwd"}).strip()
    cli_tool = _find_value(argv, {"--ssh-tool"}).strip()
    cli_mode = _find_value(argv, {"--ssh-mode"}).strip()
    cli_stage_dir = _find_value(argv, {"--ssh-stage-dir"}).strip()

    host = cli_host or env_host or str(file_options.get("host", "") or "").strip()
    port_value = cli_port or str(env.get("DEVTOOL_SSH_PORT", "") or "").strip() or file_options.get("port", 22)
    user = cli_user or str(env.get("DEVTOOL_SSH_USER", "") or "").strip() or str(file_options.get("user", "") or "").strip()
    identity = cli_identity or str(env.get("DEVTOOL_SSH_IDENTITY", "") or "").strip() or str(file_options.get("identity", "") or "").strip()
    session = cli_session or str(env.get("DEVTOOL_SSH_SESSION", "") or "").strip() or str(file_options.get("session", "") or "").strip()
    remote_cwd = cli_cwd or str(env.get("DEVTOOL_SSH_CWD", "") or "").strip() or str(file_options.get("cwd", "") or "").strip()
    tool = cli_tool or str(env.get("DEVTOOL_SSH_TOOL", "") or "").strip() or str(file_options.get("tool", "") or "").strip() or "devtool"
    mode = (cli_mode or str(env.get("DEVTOOL_SSH_MODE", "") or "").strip() or str(file_options.get("mode", "") or "").strip() or "devtool").lower().replace("_", "-")
    if mode in {"remote", "whole", "whole-process"}:
        mode = "devtool"
    if mode in {"command", "phase", "phases", "runner", "toolchain"}:
        mode = "commands"
    if mode not in {"devtool", "commands"}:
        raise ValueError(f"SSH mode must be devtool or commands, got {mode!r}")
    stage_dir = cli_stage_dir or str(env.get("DEVTOOL_SSH_STAGE_DIR", "") or "").strip() or str(file_options.get("stage_dir", "") or "").strip() or "~/.cache/devtool/ssh-stage"
    copy_artifacts = _bool_value(file_options.get("copy_artifacts"), True) if "copy_artifacts" in file_options else True
    if "DEVTOOL_SSH_NO_ARTIFACT_COPY" in env:
        copy_artifacts = not _truthy(env.get("DEVTOOL_SSH_NO_ARTIFACT_COPY"))
    if "--ssh-no-artifact-copy" in argv:
        copy_artifacts = False
    detached = _bool_value(file_options.get("detached"), False) if "detached" in file_options else False
    if "DEVTOOL_SSH_DETACHED" in env:
        detached = _truthy(env.get("DEVTOOL_SSH_DETACHED"))
    if "--ssh-detached" in argv:
        detached = True

    bootstrap_policy = str(file_options.get("bootstrap_policy", "missing") or "missing").strip().lower().replace("_", "-")
    if bootstrap_policy in {"auto", "if-missing", "needed"}:
        bootstrap_policy = "missing"
    if bootstrap_policy in {"off", "disabled", "none"}:
        bootstrap_policy = "never"
    if bootstrap_policy not in {"missing", "never"}:
        raise ValueError(f"SSH bootstrap policy must be missing or never, got {bootstrap_policy!r}")
    configured_worker_tmux = _bool_value(file_options.get("worker_tmux"), bool(profile))
    if profile and "worker_tmux" in file_options and not configured_worker_tmux:
        raise ValueError("Named SSH workers require tmux; [ssh.hosts.*.worker].tmux cannot be false")
    worker_tmux = True if profile else configured_worker_tmux

    workspace_mode = str(file_options.get("workspace_mode", "existing") or "existing").strip().lower().replace("_", "-")
    if workspace_mode not in {"existing", "managed", "ephemeral"}:
        raise ValueError(f"SSH workspace mode must be existing, managed, or ephemeral, got {workspace_mode!r}")
    workspace_root = str(file_options.get("workspace_root", "~/.cache/devtool/workspaces") or "~/.cache/devtool/workspaces").strip()
    if workspace_mode != "existing" and not profile:
        raise ValueError("Managed/ephemeral SSH workspaces require a named --ssh-profile worker")
    if workspace_mode != "existing" and cli_cwd:
        raise ValueError("--ssh-cwd is only valid for workspace.mode=existing; managed/ephemeral paths are Devtool-owned")
    sync_mode = str(file_options.get("sync_mode", "incremental") or "incremental").strip().lower().replace("_", "-")
    if sync_mode != "incremental":
        raise ValueError(f"SSH sync mode must be incremental, got {sync_mode!r}")
    from .federation_git_transport import normalize_source_transport
    source_transport = normalize_source_transport(file_options.get("source_transport", "auto"))
    sync_delete = _bool_value(file_options.get("sync_delete"), workspace_mode != "existing")
    sync_default_excludes = _bool_value(file_options.get("sync_default_excludes"), True)
    raw_sync_excludes = file_options.get("sync_excludes", ())
    if raw_sync_excludes is None:
        sync_excludes: tuple[str, ...] = ()
    elif isinstance(raw_sync_excludes, str):
        sync_excludes = (raw_sync_excludes.strip(),) if raw_sync_excludes.strip() else ()
    elif isinstance(raw_sync_excludes, (list, tuple)):
        sync_excludes = tuple(str(item).strip() for item in raw_sync_excludes if str(item).strip())
    else:
        raise ValueError("SSH sync.exclude must be a string or list of strings")

    multiplex = _bool_value(file_options.get("multiplex"), bool(profile)) if "multiplex" in file_options else bool(profile)
    try:
        control_persist_seconds = int(file_options.get("control_persist_seconds", 90) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("SSH transport.control_persist_seconds must be an integer") from exc
    if control_persist_seconds < 0:
        raise ValueError("SSH transport.control_persist_seconds cannot be negative")
    control_root = str(file_options.get("control_root", "") or "").strip()

    environment_policy = str(file_options.get("environment_policy", "remote") or "remote").strip().lower().replace("_", "-")
    if environment_policy not in {"remote", "overlay", "hermetic"}:
        raise ValueError(f"SSH environment.policy must be remote, overlay, or hermetic, got {environment_policy!r}")
    raw_environment_allowlist = file_options.get("environment_allowlist", ("CI", "TERM", "COLORTERM", "LANG", "LC_ALL", "TZ"))
    if isinstance(raw_environment_allowlist, str):
        environment_allowlist = tuple(item.strip() for item in raw_environment_allowlist.split(",") if item.strip())
    elif isinstance(raw_environment_allowlist, (list, tuple)):
        environment_allowlist = tuple(str(item).strip() for item in raw_environment_allowlist if str(item).strip())
    else:
        raise ValueError("SSH environment.allowlist must be a comma-separated string or list")
    raw_environment_overlay = file_options.get("environment_overlay", {})
    if raw_environment_overlay is None:
        environment_overlay: dict[str, str] = {}
    elif isinstance(raw_environment_overlay, Mapping):
        environment_overlay = {str(key): str(value) for key, value in raw_environment_overlay.items()}
    else:
        raise ValueError("SSH environment.overlay must be a TOML table")

    host_key_policy = str(file_options.get("host_key_policy", "accept-new" if profile else "system") or "system").strip().lower().replace("_", "-")
    if host_key_policy not in {"system", "strict", "accept-new"}:
        raise ValueError(f"SSH host-key policy must be system, strict, or accept-new, got {host_key_policy!r}")
    known_hosts = str(file_options.get("known_hosts", "") or "").strip()
    if known_hosts:
        known_hosts = str(Path(known_hosts).expanduser())
    run_state_root = str(file_options.get("run_state_root", "~/.cache/devtool/ssh-runs") or "~/.cache/devtool/ssh-runs").strip()
    execution_provider = normalize_execution_provider(
        str(file_options.get("execution_provider", "") or ""),
        remote=True,
    )
    oci_cache_root = str(file_options.get("oci_cache_root", "~/.cache/devtool/oci/workspace-caches") or "~/.cache/devtool/oci/workspace-caches").strip()
    raw_oci_allowlist = file_options.get("oci_env_allowlist", ("CI", "TERM", "COLORTERM", "LANG", "LC_ALL", "TZ"))
    if isinstance(raw_oci_allowlist, str):
        oci_env_allowlist = tuple(item.strip() for item in raw_oci_allowlist.split(",") if item.strip())
    elif isinstance(raw_oci_allowlist, (list, tuple)):
        oci_env_allowlist = tuple(str(item).strip() for item in raw_oci_allowlist if str(item).strip())
    else:
        raise ValueError("SSH OCI env_allowlist must be a comma-separated string or list of variable names")
    oci_image = str(file_options.get("oci_image", "devtool/android-build:arm64-api36-v2") or "devtool/android-build:arm64-api36-v2").strip()
    oci_container = str(file_options.get("oci_container", "devtool-android-api36") or "devtool-android-api36").strip()
    oci_architecture = str(file_options.get("oci_architecture", "aarch64") or "aarch64").strip().lower().replace("_", "-")
    if oci_architecture == "arm64":
        oci_architecture = "aarch64"
    if oci_architecture != "aarch64":
        raise ValueError(f"SSH OCI architecture must be aarch64/arm64, got {oci_architecture!r}")
    oci_pull_policy = str(file_options.get("oci_pull_policy", "if-missing") or "if-missing").strip().lower().replace("_", "-")
    if oci_pull_policy not in {"never", "if-missing"}:
        raise ValueError(f"SSH OCI pull policy must be never or if-missing, got {oci_pull_policy!r}")
    oci_digest_policy = str(file_options.get("oci_digest_policy", "record") or "record").strip().lower().replace("_", "-")
    if oci_digest_policy not in {"record", "require"}:
        raise ValueError(f"SSH OCI digest policy must be record or require, got {oci_digest_policy!r}")
    explicit_mode = bool(cli_mode or str(env.get("DEVTOOL_SSH_MODE", "") or "").strip() or str(file_options.get("mode", "") or "").strip())
    if execution_provider == "ssh-proot-oci":
        if not profile:
            raise ValueError("ssh-proot-oci requires a named --ssh-profile worker")
        if workspace_mode != "managed":
            raise ValueError("ssh-proot-oci requires workspace.mode='managed'")
        if explicit_mode and mode != "commands":
            raise ValueError("ssh-proot-oci uses local Devtool orchestration with remote command phases; --ssh-mode must be 'commands'")
        mode = "commands"

    try:
        lease_timeout_seconds = int(file_options.get("lease_timeout_seconds", 900) or 900)
        min_free_mb = int(file_options.get("min_free_mb", 1024) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("SSH resilience lease_timeout_seconds/min_free_mb must be integers") from exc
    if lease_timeout_seconds < 30:
        raise ValueError("SSH resilience lease_timeout_seconds must be at least 30")
    if min_free_mb < 0:
        raise ValueError("SSH resilience min_free_mb cannot be negative")

    if not host:
        source = f" in {config_path}" if config_path else ""
        raise ValueError(f"SSH execution requested but no host is configured{source}. Use --ssh HOST, --ssh-profile NAME, or set [ssh].host.")
    if identity:
        # Identity belongs to the local SSH client, so local ~ expansion is correct.
        identity = str(Path(identity).expanduser())

    return SSHRunnerConfig(
        host=host,
        port=_positive_port(port_value),
        user=user,
        identity=identity,
        session=session,
        cwd=remote_cwd,
        tool=tool,
        mode=mode,
        stage_dir=stage_dir,
        copy_artifacts=copy_artifacts,
        detached=detached,
        config_path=str(config_path or ""),
        profile=profile,
        bootstrap_policy=bootstrap_policy,
        worker_tmux=worker_tmux,
        workspace_mode=workspace_mode,
        workspace_root=workspace_root,
        sync_mode=sync_mode,
        source_transport=source_transport,
        sync_delete=sync_delete,
        sync_default_excludes=sync_default_excludes,
        sync_excludes=sync_excludes,
        host_key_policy=host_key_policy,
        known_hosts=known_hosts,
        run_state_root=run_state_root,
        lease_timeout_seconds=lease_timeout_seconds,
        min_free_mb=min_free_mb,
        execution_provider=execution_provider,
        multiplex=multiplex,
        control_persist_seconds=control_persist_seconds,
        control_root=control_root,
        environment_policy=environment_policy,
        environment_allowlist=environment_allowlist,
        environment_overlay=environment_overlay,
        oci_cache_root=oci_cache_root,
        oci_env_allowlist=oci_env_allowlist,
        oci_image=oci_image,
        oci_container=oci_container,
        oci_architecture=oci_architecture,
        oci_pull_policy=oci_pull_policy,
        oci_digest_policy=oci_digest_policy,
    )


def _sanitize_session(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip()).strip("-_")
    return text[:80] or "devtool"


def _command_name(argv: list[str]) -> str:
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            break
        if token in _SSH_VALUE_FLAGS or token in _GLOBAL_VALUE_FLAGS:
            index += 2
            continue
        if any(token.startswith(flag + "=") for flag in (_SSH_VALUE_FLAGS | _GLOBAL_VALUE_FLAGS) if flag.startswith("--")):
            index += 1
            continue
        if token in {"--ssh-detached", "--ssh-no-artifact-copy"} or token.startswith("-"):
            index += 1
            continue
        return token
    return "run"


def _forwarded_argv(argv: list[str]) -> list[str]:
    """Strip local SSH transport flags and preserve every remote argument verbatim."""
    forwarded: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in _SSH_VALUE_FLAGS:
            index += 2 if index + 1 < len(argv) else 1
            continue
        if any(token.startswith(flag + "=") for flag in _SSH_VALUE_FLAGS):
            index += 1
            continue
        if token in {"--ssh-detached", "--ssh-no-artifact-copy"}:
            index += 1
            continue
        if token == "--execution-environment":
            if index + 1 < len(argv) and argv[index + 1].strip().lower().replace("_", "-") == "ssh":
                index += 2
                continue
        if token.startswith("--execution-environment=") and token.split("=", 1)[1].strip().lower().replace("_", "-") == "ssh":
            index += 1
            continue
        forwarded.append(token)
        index += 1
    return forwarded


def _default_session(config: SSHRunnerConfig, forwarded: list[str]) -> str:
    # A configured remote cwd is remote-owned data, so using its basename for a
    # human-readable session name is safe. With no cwd, do not infer one.
    location = ""
    if config.cwd and config.cwd not in {"~", "$HOME"}:
        location = config.cwd.rstrip("/").rsplit("/", 1)[-1]
    command = _command_name(forwarded)
    run_id = str(os.environ.get("DEVTOOL_RUN_ID") or "").strip()
    nonce = _sanitize_session(run_id)[-24:] if run_id else f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    parts = ["devtool", location, command, nonce]
    return _sanitize_session("-".join(part for part in parts if part))


def _host_key_argv(config: SSHRunnerConfig) -> list[str]:
    argv: list[str] = []
    policy = str(config.host_key_policy or "system").strip().lower()
    if policy == "strict":
        argv.extend(["-o", "StrictHostKeyChecking=yes"])
    elif policy == "accept-new":
        argv.extend(["-o", "StrictHostKeyChecking=accept-new"])
    if config.known_hosts:
        argv.extend(["-o", f"UserKnownHostsFile={str(Path(config.known_hosts).expanduser())}"])
    return argv


def build_ssh_base_argv(config: SSHRunnerConfig, *, tty: bool = True) -> list[str]:
    ssh = os.environ.get("DEVTOOL_SSH_COMMAND", "ssh").strip() or "ssh"
    argv = [ssh]
    if tty:
        argv.append("-tt")
    argv.extend(["-p", str(config.port)])
    if config.identity:
        argv.extend(["-i", str(Path(config.identity).expanduser())])
    argv.extend(_host_key_argv(config))
    from .ssh_multiplex import multiplex_options
    argv.extend(multiplex_options(config))
    argv.append(config.destination)
    return argv


def _quote_remote_token(token: str) -> str:
    text = str(token or "")
    if text in {"~", "$HOME"}:
        return '"$HOME"'
    if text.startswith("~/"):
        return '"$HOME"/' + shlex.quote(text[2:])
    if text.startswith("$HOME/"):
        return '"$HOME"/' + shlex.quote(text[6:])
    return shlex.quote(text)


def _shell_join_remote(argv: list[str]) -> str:
    return " ".join(_quote_remote_token(token) for token in argv)




_REMOTE_OWNED_ENV_PATH_KEYS = {
    "HOME", "PREFIX", "PATH", "PWD", "TMPDIR", "JAVA_HOME", "ANDROID_HOME",
    "ANDROID_SDK_ROOT", "GRADLE_USER_HOME", "VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH",
    "CONDA_PREFIX", "CONDA_DEFAULT_ENV", "XDG_CACHE_HOME", "CCACHE_DIR",
    "PIP_CACHE_DIR", "UV_CACHE_DIR", "PDM_CACHE_DIR", "POETRY_CACHE_DIR",
    "RUFF_CACHE_DIR", "MYPY_CACHE_DIR", "TOX_WORK_DIR", "CARGO_TARGET_DIR",
    "PYTHONPYCACHEPREFIX", "SKBUILD_BUILD_DIR", "npm_config_cache",
    "YARN_CACHE_FOLDER", "BUN_INSTALL_CACHE_DIR", "DEVTOOL_BUILD_CACHE_ROOT",
    "UV_PROJECT_ENVIRONMENT", "GOPATH", "GOMODCACHE", "GOCACHE", "GOTMPDIR", "GOBIN",
}
_HOST_EXECUTION_ENV_KEYS = {
    "DEVTOOL_BUILD_ENVIRONMENT", "DEVTOOL_CHROOT_CONTEXT",
    "DEVTOOL_INSIDE_TERMUX_CHROOT", "DEVTOOL_AUTO_CHROOT_ACTIVE",
    "DEVTOOL_TERMUX_CHROOT", "DEVTOOL_TERMUX_CHROOT_HOME",
    "DEVTOOL_TERMUX_CHROOT_USER", "DEVTOOL_TERMUX_CHROOT_PATH_MAP",
}
_REMOTE_OWNED_ARG_PATH_FLAGS = {
    "--sdk-root", "--android-sdk-root", "--java-home",
    "--gradle-user-home", "--gradle-command",
}


def _path_is_under_root(value: str, root: Path) -> bool:
    try:
        path = Path(value).expanduser()
        if not path.is_absolute():
            return False
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _drop_host_owned_toolchain_paths(argv: list[str], local_root: Path) -> list[str]:
    """Remove host-only absolute toolchain paths before SSH command execution.

    Remote expressions such as ``~/android-sdk`` and paths inside the explicitly
    mapped repository are retained. Absolute paths outside the repo belong to
    the local machine and must not be projected onto the SSH host.
    """
    result: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        flag = token.split("=", 1)[0] if token.startswith("--") else ""
        if flag in _REMOTE_OWNED_ARG_PATH_FLAGS:
            if "=" in token:
                value = token.split("=", 1)[1]
                if value.startswith(("~/", "$HOME/")) or _path_is_under_root(value, local_root) or not os.path.isabs(os.path.expanduser(value)):
                    result.append(token)
                index += 1
                continue
            if index + 1 < len(argv):
                value = argv[index + 1]
                if value.startswith(("~/", "$HOME/")) or _path_is_under_root(value, local_root) or not os.path.isabs(os.path.expanduser(value)):
                    result.extend([token, value])
                index += 2
                continue
        result.append(token)
        index += 1
    return result

_ARCHIVE_SUFFIXES = (
    ".zip", ".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.zst",
    ".tzst", ".tar.bz2", ".tbz2",
)


def build_scp_base_argv(config: SSHRunnerConfig) -> list[str]:
    scp = os.environ.get("DEVTOOL_SCP_COMMAND", "scp").strip() or "scp"
    argv = [scp, "-P", str(config.port)]
    if config.identity:
        argv.extend(["-i", str(Path(config.identity).expanduser())])
    # scp accepts OpenSSH -o options too; keep host-key/multiplex behavior identical to ssh.
    argv.extend(_host_key_argv(config))
    from .ssh_multiplex import multiplex_options
    argv.extend(multiplex_options(config))
    return argv


def _resolve_local_executable(value: str, *, label: str) -> str:
    candidate = str(value or "").strip()
    resolved = shutil.which(candidate) if os.sep not in candidate else (candidate if Path(candidate).is_file() and os.access(candidate, os.X_OK) else "")
    if not resolved:
        raise ValueError(f"SSH {label} executable is unavailable or not executable: {candidate}")
    return str(resolved)


def _preflight_local_transport(config: SSHRunnerConfig) -> None:
    _resolve_local_executable(os.environ.get("DEVTOOL_SSH_COMMAND", "ssh"), label="ssh")
    if config.copy_artifacts:
        _resolve_local_executable(os.environ.get("DEVTOOL_SCP_COMMAND", "scp"), label="scp")
    if config.identity:
        identity = Path(config.identity).expanduser()
        if not identity.is_file() or not os.access(identity, os.R_OK):
            raise ValueError(f"SSH identity file is unavailable or unreadable: {identity}")


def _remote_tmux_preflight(config: SSHRunnerConfig) -> None:
    command = _build_ssh_remote_sh_argv(config, "command -v tmux >/dev/null 2>&1", tty=False)
    if subprocess.run(command, check=False, timeout=_transport_timeout()).returncode != 0:
        raise ValueError("SSH preflight failed: tmux is unavailable on the remote host")


def _session_status(config: SSHRunnerConfig, session: str) -> str:
    command = _build_ssh_remote_sh_argv(config, f"tmux has-session -t {shlex.quote(session)} >/dev/null 2>&1", tty=False)
    rc = int(subprocess.run(command, check=False, timeout=_transport_timeout()).returncode)
    if rc == 0:
        return "active"
    if rc == 1:
        return "inactive"
    return "unknown"


def _session_active(config: SSHRunnerConfig, session: str) -> bool:
    # Unknown is conservatively treated as active for cleanup decisions.
    return _session_status(config, session) != "inactive"


def worker_session_name(
    config: SSHRunnerConfig,
    *,
    operation: str = "worker",
    repository: str = "",
    run_id: str = "",
) -> str:
    """Return a stable tmux session name for an identified remote worker run.

    When ``run_id`` is supplied the result is deterministic across reconnects.
    The profile/repository parts are descriptive only; a short digest prevents
    ambiguous repositories with the same basename from colliding.
    """
    repo_text = str(repository or config.cwd or "").strip().rstrip("/")
    repo_name = repo_text.rsplit("/", 1)[-1] if repo_text else ""
    identity = "|".join((config.profile, config.destination, repo_text, operation, run_id))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
    parts = ["devtool", config.profile, repo_name, operation, run_id[-16:] if run_id else digest]
    return _sanitize_session("-".join(part for part in parts if part))


def tmux_session_status(config: SSHRunnerConfig, session: str) -> str:
    """Public worker-management wrapper around tmux session discovery."""
    return _session_status(config, _sanitize_session(session))


def list_tmux_sessions(config: SSHRunnerConfig, *, prefix: str = "devtool-") -> list[str]:
    """List Devtool-owned tmux sessions without attaching to them."""
    script = "tmux list-sessions -F '#{session_name}' 2>/dev/null || true"
    command = _build_ssh_remote_sh_argv(config, script, tty=False)
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=_transport_timeout())
    if completed.returncode != 0:
        raise ValueError(f"SSH tmux session discovery failed with exit {completed.returncode}")
    return sorted({line.strip() for line in completed.stdout.splitlines() if line.strip().startswith(prefix)})


def _assert_session_inactive(config: SSHRunnerConfig, session: str) -> None:
    status = _session_status(config, session)
    if status == "active":
        raise ValueError(f"SSH tmux session is already active; refusing to reuse mutable staging: {session}")
    if status == "unknown":
        raise ValueError(f"SSH tmux session state could not be verified before staging: {session}")


def _remove_remote_tree(config: SSHRunnerConfig, remote_dir: str) -> None:
    command = _build_ssh_remote_sh_argv(config, f"rm -rf -- {_remote_shell_path(remote_dir)}", tty=False)
    subprocess.run(command, check=False, timeout=_transport_timeout())


def _artifact_operand_indices(forwarded: list[str]) -> set[int]:
    """Return positional artifact operands without mistaking option values for paths."""
    commands = {
        "apply-overlay", "ao", "apply-overlays", "aos", "apply-snapshot", "as",
        "validate-overlay", "vo", "validate-overlays", "vos", "inspect-artifact", "ia",
    }
    command_index = next((i for i, token in enumerate(forwarded) if token in commands), None)
    if command_index is None:
        return set()
    command = forwarded[command_index]
    multi = command in {"apply-overlays", "aos", "validate-overlays", "vos"}
    indices: set[int] = set()
    skip_value = False
    for i in range(command_index + 1, len(forwarded)):
        token = forwarded[i]
        if skip_value:
            skip_value = False
            continue
        if token in _ARTIFACT_COMMAND_VALUE_FLAGS:
            skip_value = True
            continue
        if any(
            flag.startswith("--") and token.startswith(flag + "=")
            for flag in _ARTIFACT_COMMAND_VALUE_FLAGS
        ):
            continue
        if token.startswith("-"):
            continue
        indices.add(i)
        if not multi:
            break
    return indices


def _looks_like_local_artifact(token: str, *, cwd: str) -> Path | None:
    text = str(token or "").strip()
    if not text or text.startswith("-"):
        return None
    lower = text.lower()
    if not any(lower.endswith(suffix) for suffix in _ARCHIVE_SUFFIXES):
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = Path(cwd) / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = candidate.absolute()
    return resolved if resolved.is_file() else None


def _remote_join(base: str, *parts: str) -> str:
    root = str(base or "").rstrip("/")
    suffix = "/".join(str(part).strip("/") for part in parts if str(part))
    return root + ("/" + suffix if suffix else "")


def _remote_shell_path(path: str) -> str:
    """Render a remote path while expanding HOME only, never its tail."""
    text = str(path or "")
    if text in {"~", "$HOME"}:
        return '"$HOME"'
    if text.startswith("~/"):
        return '"$HOME"/' + shlex.quote(text[2:])
    if text.startswith("$HOME/"):
        return '"$HOME"/' + shlex.quote(text[6:])
    return shlex.quote(text)


def _remote_scp_path(path: str) -> str:
    """Return an scp remote path with HOME expansion and an escaped tail."""
    text = str(path or "")
    if text == "$HOME":
        text = "~"
    elif text.startswith("$HOME/"):
        text = "~/" + text[6:]
    if text.startswith("~/"):
        tail = text[2:]
        escaped = "".join(ch if ch.isalnum() or ch in "._/-" else "\\" + ch for ch in tail)
        return "~/" + escaped
    return text


def _remote_sh_command(script: str) -> str:
    """Build one remote command string for OpenSSH.

    OpenSSH does not preserve argv boundaries for the remote command. It joins
    everything after the destination with spaces before handing it to the
    remote login shell. Therefore ``ssh host sh -lc SCRIPT`` is unsafe when
    SCRIPT is passed as a separate local argv item: the remote shell can turn
    it into ``sh -lc <first-word> ...``. Quote the complete ``-c`` payload and
    send the remote command as a single SSH argv item instead.
    """
    return "sh -lc " + shlex.quote(str(script))


def _build_ssh_remote_sh_argv(
    config: SSHRunnerConfig,
    script: str,
    *,
    tty: bool,
) -> list[str]:
    return [*build_ssh_base_argv(config, tty=tty), _remote_sh_command(script)]


def build_ssh_remote_sh_argv(
    config: SSHRunnerConfig,
    script: str,
    *,
    tty: bool = False,
) -> list[str]:
    """Build a safely quoted OpenSSH remote ``sh -lc`` invocation."""
    return _build_ssh_remote_sh_argv(config, script, tty=tty)


def _ensure_remote_dir(config: SSHRunnerConfig, remote_dir: str) -> None:
    script = f"mkdir -p -- {_remote_shell_path(remote_dir)}"
    command = _build_ssh_remote_sh_argv(config, script, tty=False)
    completed = subprocess.run(command, check=False, timeout=_transport_timeout())
    if completed.returncode != 0:
        raise ValueError(f"could not create remote SSH staging directory: {remote_dir}")


def _copy_local_file_to_remote(config: SSHRunnerConfig, source: Path, remote_path: str) -> None:
    destination = f"{config.destination}:{_remote_scp_path(remote_path)}"
    command = [*build_scp_base_argv(config), str(source), destination]
    completed = subprocess.run(command, check=False, timeout=_transport_timeout())
    if completed.returncode != 0:
        raise ValueError(f"could not copy {source} to {config.destination}:{remote_path}")


def _copy_remote_tree_to_local(config: SSHRunnerConfig, remote_dir: str, local_dir: Path) -> int:
    local_dir.mkdir(parents=True, exist_ok=True)
    source = f"{config.destination}:{_remote_scp_path(remote_dir).rstrip('/')}/."
    command = [*build_scp_base_argv(config), "-r", source, str(local_dir)]
    return int(subprocess.run(command, check=False, timeout=_transport_timeout()).returncode)


def _ensure_session(config: SSHRunnerConfig, forwarded: list[str]) -> str:
    config.session = _sanitize_session(config.session or _default_session(config, forwarded))
    return config.session


def _ensure_operation_id(config: SSHRunnerConfig) -> str:
    """Return one immutable staging identity for this local invocation."""
    if not config.operation_id:
        run_id = _sanitize_session(str(os.environ.get("DEVTOOL_RUN_ID") or "run"))
        config.operation_id = _sanitize_session(f"{run_id}-{os.getpid()}-{uuid.uuid4().hex[:10]}")
    return config.operation_id


def _operation_stage_root(config: SSHRunnerConfig, session: str) -> str:
    return _remote_join(config.stage_dir, session, _ensure_operation_id(config))


def _remote_resolve_tool_executable(token: str, local_root: Path) -> str:
    """Resolve a host-owned executable by name on the destination host.

    argv[0] is a typed tool-executable operand. Repository-owned executables
    are translated through the declared repo mapping elsewhere; absolute host
    tools outside that mapping must never leak their host pathname to SSH.
    """
    text = str(token or "")
    if not text or not os.path.isabs(os.path.expanduser(text)) or _path_is_under_root(text, local_root):
        return text
    name = Path(text).name
    if name.startswith("python"):
        return "python3"
    return name


def stage_forwarded_artifacts(
    config: SSHRunnerConfig,
    forwarded: list[str],
    *,
    cwd: str,
) -> tuple[list[str], list[tuple[Path, str]]]:
    """SCP local overlay/snapshot archives and rewrite only those operands.

    Non-existent paths are assumed to already be remote paths and are forwarded
    verbatim. This preserves the independent-remote-filesystem contract.
    """
    if not config.copy_artifacts:
        return list(forwarded), []
    _ensure_session(config, forwarded)
    staged: list[tuple[Path, str]] = []
    rewritten = list(forwarded)
    seen: dict[Path, str] = {}
    remote_dir = _remote_join(_operation_stage_root(config, config.session), "inputs")
    artifact_indices = _artifact_operand_indices(forwarded)
    for index, token in enumerate(forwarded):
        if index not in artifact_indices:
            continue
        local = _looks_like_local_artifact(token, cwd=cwd)
        if local is None:
            continue
        remote = seen.get(local)
        if remote is None:
            digest = hashlib.sha256(local.read_bytes()).hexdigest()[:16]
            remote = _remote_join(remote_dir, f"{digest}-{local.name}")
            seen[local] = remote
            staged.append((local, remote))
        rewritten[index] = remote
    if staged:
        _assert_session_inactive(config, config.session)
        _ensure_remote_dir(config, remote_dir)
        for local, remote in staged:
            _copy_local_file_to_remote(config, local, remote)
    return rewritten, staged


def _translate_repo_bound_path(value: str, local_root: Path, remote_root: str) -> str:
    """Translate only paths proven to be under the local repo root.

    This is an explicit one-root mapping supplied by ``--ssh-cwd``. No Termux,
    chroot, HOME, or username assumptions are involved.
    """
    text = str(value or "")
    if not text or not remote_root:
        return text
    try:
        root = local_root.resolve()
    except OSError:
        root = local_root.absolute()
    expanded = Path(text).expanduser()
    if not expanded.is_absolute():
        return text
    try:
        resolved = expanded.resolve()
    except OSError:
        resolved = expanded.absolute()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return text
    if str(relative) == ".":
        return remote_root
    return _remote_join(remote_root, relative.as_posix())


def _translate_repo_bound_token(token: str, local_root: Path, remote_root: str) -> str:
    text = str(token)
    if "=" in text and text.startswith("--"):
        flag, value = text.split("=", 1)
        translated = _translate_repo_bound_path(value, local_root, remote_root)
        return f"{flag}={translated}" if translated != value else text
    return _translate_repo_bound_path(text, local_root, remote_root)


def _stage_support_files_for_spec(
    config: SSHRunnerConfig,
    argv: list[str],
    *,
    local_root: Path,
    remote_root: str,
    session: str,
    dry_run: bool,
    metadata: Mapping[str, Any] | None = None,
) -> tuple[list[str], list[tuple[Path, str]]]:
    """Translate typed operands and stage only explicitly local helper files."""
    metadata = metadata or {}
    rewritten: list[str] = []
    staged: list[tuple[Path, str]] = []
    seen: dict[Path, str] = {}
    remote_support_dir = _remote_join(_operation_stage_root(config, session), "support")
    android_builder_value = str(metadata.get("android_builder") or "").strip()
    helper_values = {android_builder_value} - {""}
    android_builder_path = Path(android_builder_value).expanduser() if android_builder_value else None
    raw_helpers = metadata.get("ssh_local_helper_files")
    if isinstance(raw_helpers, (list, tuple)):
        helper_values.update(str(value).strip() for value in raw_helpers if str(value).strip())
    # Every command-mode phase gets a fresh phase session. Refuse an active or
    # unverifiable collision before uploads *or* command launch, even when the
    # phase has no local helper files to stage.
    if not dry_run:
        _assert_session_inactive(config, session)

    helper_indices: set[int] = set()
    for index, token in enumerate(argv[:-1]):
        if token in {"--init-script", "--script", "--helper-file"}:
            helper_indices.add(index + 1)
    for index, token in enumerate(argv):
        mapped = _translate_repo_bound_token(token, local_root, remote_root)
        if mapped != token:
            rewritten.append(mapped)
            continue
        if config.execution_provider == "ssh-proot-oci" and str(token).startswith("/usr/local/lib/devtool/"):
            rewritten.append(str(token))
            continue
        path = Path(token).expanduser()
        explicitly_local = index in helper_indices or token in helper_values
        if not explicitly_local:
            rewritten.append(token)
            continue
        if not path.is_absolute() or not path.is_file():
            raise ValueError(f"SSH local helper operand does not exist: {token}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        remote = seen.get(path)
        if remote is None:
            remote = _remote_join(remote_support_dir, f"{digest}-{path.name}")
            seen[path] = remote
            staged.append((path, remote))
            if android_builder_path is not None:
                try:
                    is_android_builder = path.resolve() == android_builder_path.resolve()
                except OSError:
                    is_android_builder = path == android_builder_path
                if is_android_builder:
                    companion = path.with_name("termux_toolchains.py")
                    if not companion.is_file():
                        raise ValueError(
                            "SSH Android builder runtime is incomplete: missing local toolchain companion "
                            f"{companion}. Re-stage the Android builder before remote execution."
                        )
                    companion_remote = _remote_join(remote_support_dir, "termux_toolchains.py")
                    if not any(existing_remote == companion_remote for _local, existing_remote in staged):
                        staged.append((companion, companion_remote))
        rewritten.append(remote)
    if staged and not dry_run:
        _ensure_remote_dir(config, remote_support_dir)
        for local, remote in staged:
            _copy_local_file_to_remote(config, local, remote)
    return rewritten, staged


def _translate_env_for_repo(
    env: Mapping[str, str],
    local_root: Path,
    remote_root: str,
    *,
    config: SSHRunnerConfig | None = None,
) -> dict[str, str]:
    translated: dict[str, str] = {}
    policy = str(getattr(config, "environment_policy", "remote") or "remote").strip().lower().replace("_", "-")
    allowlist = set(getattr(config, "environment_allowlist", ()) or ())
    overlay = dict(getattr(config, "environment_overlay", {}) or {})
    source: dict[str, str] = {str(key): str(value) for key, value in env.items()}
    if policy == "overlay":
        # Explicit remote overlay wins over the spec environment. Absolute host
        # paths are still subjected to the same remote-owned path filter below.
        source.update({str(key): str(value) for key, value in overlay.items()})
    run_id = str(os.environ.get("DEVTOOL_RUN_ID") or "").strip()
    if run_id:
        translated["DEVTOOL_RUN_ID"] = run_id
    for key_text, value in source.items():
        if key_text in _HOST_EXECUTION_ENV_KEYS:
            continue
        if policy == "hermetic" and key_text not in allowlist and not key_text.startswith("DEVTOOL_"):
            continue
        mapped = _translate_repo_bound_path(value, local_root, remote_root)
        if mapped != value:
            translated[key_text] = mapped
            continue
        if key_text in _REMOTE_OWNED_ENV_PATH_KEYS and os.path.isabs(os.path.expanduser(value)):
            # The remote login/toolchain owns these paths. An absolute local
            # value outside the explicit repo mapping must not leak over SSH.
            continue
        translated[key_text] = value
    return translated




def _stage_sensitive_env(
    config: SSHRunnerConfig,
    *,
    session: str,
    run_id: str,
    values: Mapping[str, str],
    dry_run: bool,
) -> str:
    """Stage secret-like environment values through stdin, never the ssh argv.

    Secrets live inside the persistent run-state directory so resume/cancel/
    recover can deterministically remove them after a disconnected phase.
    """
    if not values:
        return ""
    from .ssh_resilience import remote_phase_state_paths
    remote_dir = remote_phase_state_paths(config, run_id, session)["root"]
    remote_file = _remote_join(remote_dir, "secret-env.sh")
    if dry_run:
        return remote_file
    _ensure_remote_dir(config, remote_dir)
    body = "\n".join(f"export {key}={shlex.quote(str(value))}" for key, value in sorted(values.items())) + "\n"
    script = f"set -eu; umask 077; cat > {_remote_shell_path(remote_file + '.tmp')}; chmod 600 {_remote_shell_path(remote_file + '.tmp')}; mv -f -- {_remote_shell_path(remote_file + '.tmp')} {_remote_shell_path(remote_file)}"
    completed = subprocess.run(
        _build_ssh_remote_sh_argv(config, script, tty=False),
        check=False,
        input=body,
        text=True,
        capture_output=True,
        timeout=_transport_timeout(),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"SSH sensitive environment staging failed with exit {completed.returncode}: {detail or 'no diagnostic output'}")
    return remote_file


def _cleanup_oci_construction_state(
    config: SSHRunnerConfig,
    *,
    secret_files: Iterable[str],
    operation_roots: Iterable[str],
) -> None:
    """Best-effort cleanup when OCI command wrapping fails before tmux owns it.

    Once a wrapped phase is returned, its local/tmux cleanup and recovery rules
    own these paths. If construction aborts first, there is no resumable phase,
    so retaining staged helpers or secret files would be both misleading and a
    secret-lifecycle leak. Cleanup failures never replace the construction error.
    """
    files = tuple(dict.fromkeys(str(value) for value in secret_files if str(value).strip()))
    roots = tuple(dict.fromkeys(str(value) for value in operation_roots if str(value).strip()))
    if not files and not roots:
        return
    fragments: list[str] = ["set +e"]
    if files:
        fragments.append("rm -f -- " + " ".join(_remote_shell_path(path) for path in files))
    if roots:
        fragments.append("rm -rf -- " + " ".join(_remote_shell_path(path) for path in roots))
    try:
        subprocess.run(
            _build_ssh_remote_sh_argv(config, "; ".join(fragments), tty=False),
            check=False,
            text=True,
            capture_output=True,
            timeout=_transport_timeout(),
        )
    except (OSError, subprocess.SubprocessError):
        # The original construction failure remains authoritative. A transport
        # outage here is OCI-06 recovery territory and must not mask its cause.
        pass


def _build_remote_phase_script(
    config: SSHRunnerConfig,
    *,
    session: str,
    command: str,
    run_id: str = "",
    workspace_id: str = "",
    workspace_path: str = "",
    lease_dir: str = "",
    phase_metadata: Mapping[str, Any] | None = None,
    attach: bool = True,
    release_lease_on_completion: bool = False,
) -> str:
    """Build the durable tmux phase launcher used by SSH command execution.

    SSH-02 makes the remote run directory the persistence boundary rather than
    tmux itself.  stdout/stderr therefore live beneath the run directory from
    process start and are mirrored back to the tmux pane with FIFOs/tee.  A
    dead tmux server can no longer erase historical output, and reconnecting
    clients can follow the persisted streams without attaching to tmux.
    """
    from .ssh_resilience import remote_phase_state_paths

    run_identity = str(run_id or config.operation_id or os.environ.get("DEVTOOL_RUN_ID") or session).strip()
    paths = remote_phase_state_paths(config, run_identity, session)
    state_dir = paths["root"]
    status_path = paths["status"]
    state_path = paths["state"]
    metadata_path = paths["metadata"]
    stdout_path = paths["stdout"]
    stderr_path = paths["stderr"]
    runtime_log = _remote_join(
        str(workspace_path or config.cwd or "~").rstrip("/"),
        ".devtool", "ssh-runtime-logs", _sanitize_session(run_identity), _sanitize_session(session) + ".log",
    )
    runtime_log_dir = runtime_log.rsplit("/", 1)[0]
    command_status = _remote_join(state_dir, "command-status")
    stdout_fifo = _remote_join(state_dir, ".stdout.pipe")
    stderr_fifo = _remote_join(state_dir, ".stderr.pipe")
    release_fragment = ""
    if release_lease_on_completion and lease_dir:
        # A standalone ssh exec is one phase, so it may safely release its own
        # lease remotely even when the launcher is detached. Multi-phase normal
        # Devtool runs keep their existing local run-level lease lifecycle.
        release_fragment = (
            f"; lease={_remote_shell_path(lease_dir)}; "
            f"if [ -d \"$lease\" ]; then owner=$(sed -n 's/^run_id=//p' \"$lease/owner\" 2>/dev/null | head -n1); "
            f"[ \"$owner\" = {shlex.quote(_sanitize_session(run_identity))} ] && rm -rf -- \"$lease\" || true; fi"
        )
    payload = (
        "set +e; "
        f"rm -f -- {_remote_shell_path(command_status)} {_remote_shell_path(command_status + '.tmp')} "
        f"{_remote_shell_path(stdout_fifo)} {_remote_shell_path(stderr_fifo)}; "
        f": > {_remote_shell_path(stdout_path)}; : > {_remote_shell_path(stderr_path)}; "
        f"mkfifo {_remote_shell_path(stdout_fifo)} {_remote_shell_path(stderr_fifo)}; "
        f"tee -a {_remote_shell_path(stdout_path)} {_remote_shell_path(runtime_log)} < {_remote_shell_path(stdout_fifo)} & out_pid=$!; "
        f"tee -a {_remote_shell_path(stderr_path)} {_remote_shell_path(runtime_log)} < {_remote_shell_path(stderr_fifo)} >&2 & err_pid=$!; "
        "( " + command + " ) "
        f"> {_remote_shell_path(stdout_fifo)} 2> {_remote_shell_path(stderr_fifo)}; rc=$?; "
        "wait \"$out_pid\" 2>/dev/null || true; wait \"$err_pid\" 2>/dev/null || true; "
        f"rm -f -- {_remote_shell_path(stdout_fifo)} {_remote_shell_path(stderr_fifo)}; "
        f"printf '%s\\n' \"$rc\" > {_remote_shell_path(command_status + '.tmp')}; "
        f"mv -f -- {_remote_shell_path(command_status + '.tmp')} {_remote_shell_path(command_status)}; "
        f"printf '%s\\n' \"$rc\" > {_remote_shell_path(status_path + '.tmp')}; "
        f"mv -f -- {_remote_shell_path(status_path + '.tmp')} {_remote_shell_path(status_path)}; "
        f"printf 'completed\\n' > {_remote_shell_path(state_path + '.tmp')}; "
        f"mv -f -- {_remote_shell_path(state_path + '.tmp')} {_remote_shell_path(state_path)}; "
        f"printf 'finished_unix=%s\\n' \"$(date +%s 2>/dev/null || printf 0)\" >> {_remote_shell_path(metadata_path)}"
        + release_fragment
        + "; exit \"$rc\""
    )
    metadata_lines = [
        "run_id=" + run_identity,
        "session=" + session,
        "workspace_id=" + str(workspace_id or ""),
        "workspace_path=" + str(workspace_path or ""),
        "lease_dir=" + str(lease_dir or ""),
        "remote_log_path=" + runtime_log,
        "stdout_path=" + stdout_path,
        "stderr_path=" + stderr_path,
        "output_schema=split-v1",
        "tmux_window=0",
        "tmux_pane=0",
    ]
    if phase_metadata:
        metadata_lines.extend(phase_metadata_lines(phase_metadata, run_id=run_identity, session=session))
    metadata_printf = " ".join(shlex.quote(line) for line in metadata_lines)
    lines = [
        "set -eu",
        f"session={shlex.quote(session)}",
        f"state_dir={_remote_shell_path(state_dir)}",
        f"status={_remote_shell_path(status_path)}",
        f"state={_remote_shell_path(state_path)}",
        f"metadata={_remote_shell_path(metadata_path)}",
        'mkdir -p "$state_dir"',
        "command -v tmux >/dev/null 2>&1 || { echo 'devtool ssh: tmux is required on the remote host' >&2; exit 127; }",
        'if ! tmux has-session -t "$session" 2>/dev/null; then',
        '  rm -f "$status"',
        f"  mkdir -p -- {_remote_shell_path(runtime_log_dir)}",
        f"  : > {_remote_shell_path(runtime_log)}",
        f"  printf '%s\\n' {metadata_printf} > \"$metadata.tmp\"",
        '  printf \'started_unix=%s\\n\' "$(date +%s 2>/dev/null || printf 0)" >> "$metadata.tmp"',
        '  mv -f -- "$metadata.tmp" "$metadata"',
        '  printf \'running\\n\' > "$state.tmp"; mv -f -- "$state.tmp" "$state"',
        "  tmux new-session -d -s \"$session\" " + shlex.quote("sh -lc " + shlex.quote(payload)),
        "fi",
    ]
    if attach:
        lines.extend([
            'echo "Devtool SSH command session: $session (detach: Ctrl-b d)"',
            'tmux attach-session -t "$session" || true',
            'if [ -f "$status" ]; then rc=$(cat "$status" 2>/dev/null || printf 1); tmux kill-session -t "$session" >/dev/null 2>&1 || true; exit "$rc"; fi',
            'echo "Detached; remote command continues in tmux session: $session" >&2',
            "exit 125",
        ])
    else:
        lines.extend([
            'echo "Devtool SSH command started in tmux session: $session"',
            "exit 125",
        ])
    return "\n".join(lines) + "\n"

def _configured_local_artifact_dir(config_obj: Any, local_root: Path) -> Path:
    options = getattr(config_obj, "runner_options", {}) or {}
    artifacts = options.get("artifacts", {}) if isinstance(options, dict) else {}
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    value = str(artifacts.get("output_directory") or artifacts.get("output_dir") or ".devtool/build-artifacts").strip()
    local = Path(value).expanduser()
    if not local.is_absolute():
        local = local_root / local
    return local.resolve()


def _rewrite_artifact_output_for_remote(
    argv: list[str],
    *,
    config_obj: Any,
    config: SSHRunnerConfig,
    local_root: Path,
    host_cwd: str,
    remote_cwd: str,
    remote_root: str,
    session: str,
    artifact_phase: bool,
) -> tuple[list[str], Path, str]:
    """Route declared artifact output into one immutable operation directory."""
    rewritten = list(argv)
    local_default = _configured_local_artifact_dir(config_obj, local_root)
    output_flags = {"--output-dir", "-o", "--out-dir", "--outdir"}
    value = _find_value(argv, output_flags).strip()
    if not value:
        if artifact_phase:
            # Tools without an output flag may still populate the configured
            # generated-artifact directory. Retrieval is always from an
            # operation-owned immutable snapshot; the wrapper below snapshots
            # the freshly-cleared native directory after the command succeeds.
            return rewritten, local_default, _remote_join(_operation_stage_root(config, session), "outputs")
        return rewritten, local_default, _translate_repo_bound_path(str(local_default), local_root, remote_root)

    if value.startswith(("~/", "$HOME/")):
        local_output = local_default
    else:
        local_path = Path(value).expanduser()
        if not local_path.is_absolute():
            local_path = Path(host_cwd) / local_path
        local_output = local_path.resolve()

    if not artifact_phase:
        mapped = _translate_repo_bound_path(str(local_output), local_root, remote_root)
        remote_output = mapped if mapped != str(local_output) else (
            _remote_join(remote_cwd, value) if not os.path.isabs(os.path.expanduser(value)) else value
        )
    else:
        # Artifact-producing phases must never reuse an output directory from a
        # prior invocation, even when the caller explicitly reuses a tmux name.
        remote_output = _remote_join(_operation_stage_root(config, session), "outputs")

    index = 0
    while index < len(rewritten):
        token = rewritten[index]
        if token in output_flags:
            if index + 1 < len(rewritten):
                rewritten[index + 1] = remote_output
            break
        matched = next((flag for flag in output_flags if flag.startswith("--") and token.startswith(flag + "=")), None)
        if matched:
            rewritten[index] = matched + "=" + remote_output
            break
        index += 1
    return rewritten, local_output, remote_output


def _oci_replace_option(argv: list[str], flag: str, value: str) -> list[str]:
    result = list(argv)
    boundary = result.index("--") if "--" in result else len(result)
    index = 0
    while index < boundary:
        token = result[index]
        if token == flag:
            if index + 1 < boundary:
                result[index + 1] = value
                return result
            break
        if token.startswith(flag + "="):
            result[index] = flag + "=" + value
            return result
        index += 1
    result[boundary:boundary] = [flag, value]
    return result


def _oci_rewrite_runtime_argv(argv: list[str], metadata: dict[str, Any]) -> list[str]:
    """Replace standard staged Android runtime inputs with immutable image v2 paths."""
    if not argv:
        return []
    result = list(argv)
    builder = str(metadata.get("android_builder") or "").strip()
    if builder:
        try:
            builder_resolved = Path(builder).expanduser().resolve()
        except OSError:
            builder_resolved = Path(builder).expanduser()
        for index, token in enumerate(result):
            try:
                candidate = Path(str(token)).expanduser().resolve()
            except (OSError, RuntimeError):
                candidate = Path(str(token)).expanduser()
            if candidate == builder_resolved:
                result[index] = "/usr/local/lib/devtool/build-apk"
        metadata.pop("android_builder", None)
        metadata["oci_embedded_android_runtime"] = True

    event_runtime_paths = {
        "9e0a0aa733ffeb41ce76e4b51b5bda4eadfcf52d01d49b3faf1dd4f33b2e72d8": "/usr/local/lib/devtool/devtool-events.init.gradle",
        "48529c7e3b7a2a4409aacbf102ffb47048165cd4badf3d73ba82e2b309c05788": "/usr/local/lib/devtool/devtool-events-full.init.gradle",
    }
    for index, token in enumerate(result[:-1]):
        if token != "--init-script":
            continue
        candidate = Path(str(result[index + 1])).expanduser()
        if not candidate.is_file():
            continue
        try:
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        except OSError:
            continue
        embedded_path = event_runtime_paths.get(digest)
        if embedded_path:
            result[index + 1] = embedded_path
            metadata["oci_embedded_gradle_events"] = True
            metadata["oci_embedded_gradle_events_level"] = ("full" if "-full." in embedded_path else "standard")

    if metadata.get("oci_embedded_android_runtime"):
        # The image is the single owner of standard Android toolchain selection.
        for flag, value in (
            ("--java-home", "/usr/lib/jvm/java-21-openjdk-arm64"),
            ("--sdk-root", "/opt/android-sdk"),
            ("--build-tools", "36.0.0"),
            ("--ndk-version", "29.0.14206865"),
            ("--toolchain-downloads", "never"),
            ("--bootstrap-network", "deny"),
            ("--system-deps", "never"),
            ("--qemu-install", "never"),
            ("--gradle-provider", "wrapper"),
        ):
            result = _oci_replace_option(result, flag, value)
        # Remove setup flags that would contradict image immutability.
        result = [token for token in result if token not in {"--auto-sdk", "--force-setup"}]
        boundary = result.index("--") if "--" in result else len(result)
        if "--skip-sdk-install" not in result[:boundary]:
            result.insert(boundary, "--skip-sdk-install")
    return result


def _oci_guest_translate(value: str, *, remote_root: str, operation_root: str, operation_guest: str) -> str:
    text = str(value)
    root = remote_root.rstrip("/")
    op = operation_root.rstrip("/")
    if text == root:
        return "/workspace"
    if root and text.startswith(root + "/"):
        return "/workspace/" + text[len(root) + 1:]
    if op and text == op:
        return operation_guest
    if op and text.startswith(op + "/"):
        return operation_guest.rstrip("/") + "/" + text[len(op) + 1:]
    return text


def _oci_guest_translate_token(token: str, *, remote_root: str, operation_root: str, operation_guest: str) -> str:
    text = str(token)
    if text.startswith("--") and "=" in text:
        flag, value = text.split("=", 1)
        mapped = _oci_guest_translate(value, remote_root=remote_root, operation_root=operation_root, operation_guest=operation_guest)
        return flag + "=" + mapped
    return _oci_guest_translate(text, remote_root=remote_root, operation_root=operation_root, operation_guest=operation_guest)


def _oci_bind_shell_arg(host: str, guest: str) -> str:
    # Keep HOME-relative sources expandable by the remote shell while passing a
    # single host:guest operand to proot-distro.
    return _remote_shell_path(host) + ":" + shlex.quote(guest)


def _oci_wrap_guest_command(
    config: SSHRunnerConfig,
    *,
    plan: Any,
    guest_cwd: str,
    command: str,
    public_env: Mapping[str, str],
    sensitive_keys: Iterable[str],
    operation_host: str,
    operation_guest: str,
    bind_operation: bool,
    secret_host_dir: str,
    secret_guest_dir: str,
) -> str:
    options: list[str] = []
    for bind in plan.binds:
        options.extend(["--bind", bind.host + ":" + bind.guest])
    if bind_operation:
        options.extend(["--bind", operation_host + ":" + operation_guest])
    if secret_host_dir:
        options.extend(["--bind", secret_host_dir + ":" + secret_guest_dir])
    options.extend(["--work-dir", guest_cwd])

    env_values = dict(plan.managed_environment)
    env_values.update(plan.forwarded_environment)
    env_values.update({str(k): str(v) for k, v in public_env.items()})
    env_assignments = [f"{key}={value}" for key, value in sorted(env_values.items())]
    env_shell = " ".join(shlex.quote(item) for item in env_assignments)
    secret_shell = " ".join(f'{key}="${{{key}-}}"' for key in sorted(set(sensitive_keys)))
    inner_parts = []
    if secret_host_dir:
        inner_parts.append(". " + shlex.quote(secret_guest_dir.rstrip("/") + "/secret-env.sh"))
    env_command = 'exec env -i HOME="$HOME" PATH="$PATH"'
    if env_shell:
        env_command += " " + env_shell
    if secret_shell:
        env_command += " " + secret_shell
    env_command += " /bin/bash -lc " + shlex.quote(command)
    inner_parts.append(env_command)
    inner = " && ".join(inner_parts)

    option_shell: list[str] = []
    index = 0
    while index < len(options):
        flag = options[index]
        value = options[index + 1]
        if flag == "--bind":
            host, guest = value.split(":", 1)
            option_shell.extend([flag, _oci_bind_shell_arg(host, guest)])
        else:
            option_shell.extend([flag, shlex.quote(value)])
        index += 2
    return (
        'pd_bin="$(command -v proot-distro 2>/dev/null || command -v pd 2>/dev/null || true)"; '
        '[ -n "$pd_bin" ] || { echo "PRoot-Distro is unavailable for OCI repository execution" >&2; exit 127; }; '
        'exec "$pd_bin" login ' + shlex.quote(config.oci_container) + " " + " ".join(option_shell)
        + " -- /bin/bash -lc " + shlex.quote(inner)
    )

def wrap_specs_for_ssh_commands(
    specs: Iterable[Any],
    config: SSHRunnerConfig,
    *,
    project_config: Any,
    repo_root: str | Path,
    dry_run: bool = False,
) -> list[Any]:
    """Wrap external CommandSpecs with SSH+tmux while devtool stays local."""
    if config.detached:
        raise ValueError(
            "--ssh-detached cannot be used with --ssh-mode commands because the local devtool "
            "must wait for each remote phase; use --ssh-mode devtool for detachable whole-run execution"
        )
    local_root = Path(repo_root).expanduser().resolve()
    spec_list = list(specs)
    preparation = None
    if config.profile:
        # Named workers own their execution substrate. Managed/ephemeral workers
        # bootstrap and synchronize here once, before any phase is wrapped.
        from .ssh_execution import prepare_ssh_command_execution
        preparation = prepare_ssh_command_execution(
            config, spec_list, repo_root=local_root, dry_run=dry_run,
        )
        remote_root = str(preparation.remote_root or "").strip()
    else:
        remote_root = str(config.cwd or "").strip()
    if not remote_root:
        raise ValueError(
            "--ssh-mode commands requires --ssh-cwd/[ssh].cwd for unmanaged SSH, "
            "or a named managed/ephemeral --ssh-profile worker"
        )
    if preparation is not None:
        run_identity = str(preparation.run_id or os.environ.get("DEVTOOL_RUN_ID") or "").strip()
        if not run_identity and preparation.workspace is not None:
            run_identity = preparation.workspace.instance_id
        base_session = _sanitize_session(
            config.session
            or worker_session_name(
                config, operation="commands", repository=remote_root, run_id=run_identity
            )
        )
        preparation_metadata = preparation.metadata()
    else:
        run_identity = str(config.operation_id or os.environ.get("DEVTOOL_RUN_ID") or "").strip()
        base_session = _sanitize_session(config.session or _default_session(config, ["commands"]))
        preparation_metadata = {}
    from .ssh_multiplex import evidence as _ssh_multiplex_evidence
    preparation_metadata["ssh_multiplex"] = _ssh_multiplex_evidence(config).as_dict()
    oci_state = None
    if config.execution_provider == SSH_PROOT_OCI:
        if preparation is None or preparation.workspace is None:
            raise ValueError("ssh-proot-oci requires a prepared named managed SSH workspace")
        if not run_identity:
            raise ValueError("ssh-proot-oci repository execution requires the caller's canonical EXO RunId")
        from .oci_execution import prepare_oci_execution
        oci_state = prepare_oci_execution(
            config, repo_root=str(local_root), run_id=run_identity,
            workspace_id=str(preparation.workspace.workspace_id), remote_root=remote_root,
            source_sync_id=str(preparation_metadata.get("ssh_source_sync_id") or ""),
            source_revision=(preparation_metadata.get("ssh_source_revision") if isinstance(preparation_metadata.get("ssh_source_revision"), Mapping) else {}),
            dry_run=dry_run, environment=os.environ,
            workspace_planned=bool(dry_run and preparation.sync.get("would_create_workspace")),
        )
        preparation_metadata.update(oci_state.metadata())
        from .oci_reuse import oci08_execution_metadata
        preparation_metadata.update(
            oci08_execution_metadata(
                config,
                preparation_metadata=preparation_metadata,
                plan=oci_state.plan,
                image_reference=oci_state.image_reference,
                image_digest=oci_state.image_digest,
                build_context_sha256=oci_state.build_context_sha256,
                external_phase_count=len([
                    item for item in spec_list
                    if not bool((getattr(item, "metadata", {}) or {}).get("devtool_internal_validation"))
                ]),
                dry_run=dry_run,
            )
        )
        from .oci_diagnostics import oci09_phase_metadata
        preparation_metadata.update(oci09_phase_metadata(preparation_metadata))
    wrapped: list[Any] = []
    external_ordinals = [
        i for i, item in enumerate(spec_list, 1)
        if not bool((getattr(item, "metadata", {}) or {}).get("devtool_internal_validation"))
    ]
    last_external_ordinal = external_ordinals[-1] if external_ordinals else 0
    lease_release_argv: list[str] = []
    if preparation is not None and preparation.lease:
        from .ssh_resilience import SSHWorkspaceLease, build_release_lease_ssh_argv
        lease_release_argv = build_release_lease_ssh_argv(config, SSHWorkspaceLease(**preparation.lease))
    wrapping_complete = False
    oci_construction_secret_files: list[str] = []
    oci_construction_operation_roots: list[str] = []
    try:
        if not dry_run:
            _preflight_local_transport(config)
            _remote_tmux_preflight(config)

        for ordinal, spec in enumerate(spec_list, 1):
            metadata = dict(getattr(spec, "metadata", {}) or {})
            # Internal validation pseudo-specs are executed by local devtool logic,
            # not by a remote shell.
            if metadata.get("devtool_internal_validation"):
                wrapped.append(spec)
                continue
            metadata.update(preparation_metadata)
            metadata["execution_mode"] = ("ssh-proot-oci" if config.execution_provider == SSH_PROOT_OCI else "ssh-command")
            metadata["execution_provider"] = config.execution_provider
            metadata["ssh_mode"] = "commands"
            metadata["ssh_destination"] = config.destination
            metadata["ssh_port"] = config.port
            original_argv = list(getattr(spec, "argv", []) or [])
            host_cwd = str(getattr(spec, "cwd", "") or local_root)
            remote_base_argv = _drop_host_owned_toolchain_paths(original_argv, local_root)
            if remote_base_argv and metadata.get("gradle_direct"):
                # A local PATH Gradle selected during planning belongs to the phone.
                # On a synchronized worker prefer the repository wrapper so the
                # project itself owns the Gradle distribution/version contract.
                wrapper = Path(host_cwd).expanduser() / ("gradlew.bat" if os.name == "nt" else "gradlew")
                if wrapper.is_file():
                    # Native Termux does not promise a conventional /bin/sh, while
                    # Gradle wrappers normally carry #!/bin/sh. Invoke the synced
                    # wrapper through the remote login shell explicitly.
                    remote_base_argv = ["sh", str(wrapper.resolve()), *remote_base_argv[1:]]
                    metadata["ssh_gradle_launcher"] = "wrapper-via-sh"
            if remote_base_argv:
                remote_base_argv[0] = _remote_resolve_tool_executable(remote_base_argv[0], local_root)
            phase_name = _sanitize_session(str(getattr(spec, "name", "phase") or "phase"))
            session = _sanitize_session(f"{base_session}-{ordinal:02d}-{phase_name}")
            metadata["ssh_session"] = session
            metadata["oci_phase_node_id"] = phase_node_id(run_identity, session, provider=config.execution_provider)
            if config.execution_provider == SSH_PROOT_OCI and not dry_run:
                # Record before staging: _stage_support_files_for_spec may copy one
                # helper and then fail on a later operand. No tmux phase exists yet
                # to own cleanup in that construction-failure case.
                oci_construction_operation_roots.append(_operation_stage_root(config, session))
            remote_cwd = _translate_repo_bound_path(host_cwd, local_root, remote_root)
            if remote_cwd == host_cwd and Path(host_cwd).is_absolute():
                # A cwd outside the repository has no declared SSH mapping. Use the
                # explicit remote repo root rather than inventing a remote path.
                remote_cwd = remote_root

            from .federation_transport import attach_federation_transport_metadata, build_federation_attestation_shell
            federation_environment = "ssh-proot-oci" if config.execution_provider == SSH_PROOT_OCI else "ssh-command"
            metadata = attach_federation_transport_metadata(
                metadata,
                local_root=local_root,
                execution_environment=federation_environment,
                remote_root=remote_root,
                execution_provider=config.execution_provider,
                path_mapping="oci-workspace" if config.execution_provider == SSH_PROOT_OCI else "ssh-workspace",
            )
            return_bundle = None
            return_fragment = ""
            if config.profile and config.copy_artifacts and not dry_run:
                # Named workers always have rsync after preparation. Snapshot the
                # generated reports/distributables back to an immutable local return
                # bundle before local report parsing/artifact verification runs.
                from .ssh_returns import create_return_bundle, shell_return_fragment
                return_bundle = create_return_bundle(
                    config,
                    local_root=local_root,
                    host_cwd=host_cwd,
                    remote_root=remote_root,
                    session=session,
                    project_config=project_config,
                    spec_metadata=metadata,
                    source_sync_id=str(preparation_metadata.get("ssh_source_sync_id") or ""),
                    provider_metadata={**preparation_metadata, **metadata},
                )
                metadata.update(return_bundle.metadata())
                metadata = attach_federation_transport_metadata(
                    metadata,
                    local_root=local_root,
                    execution_environment=federation_environment,
                    remote_root=remote_root,
                    execution_provider=config.execution_provider,
                    artifact_return_root=str(return_bundle.root),
                    path_mapping="oci-workspace" if config.execution_provider == SSH_PROOT_OCI else "ssh-workspace",
                )
                remote_rsync = preparation.worker.tools.get("rsync", "") if preparation is not None else "rsync"
                try:
                    remote_zstd = preparation.worker.tools.get("zstd", "") if preparation is not None else ""
                    remote_python = preparation.worker.tools.get("python", "") if preparation is not None else ""
                    return_fragment = shell_return_fragment(
                        config, return_bundle, remote_rsync=remote_rsync or "rsync",
                        remote_zstd=remote_zstd, remote_python=remote_python,
                    )
                except ValueError as exc:
                    # Existing named checkouts predate managed rsync workspaces. Do
                    # not make them un-runnable solely because local return-rsync is
                    # unavailable; retain the manifest and surface the limitation.
                    metadata["ssh_return_disabled_reason"] = str(exc)
                    return_fragment = ""

            federation_attestation_probe = build_federation_attestation_shell(metadata, remote_root=remote_root)

            if config.execution_provider == SSH_PROOT_OCI:
                remote_base_argv = _oci_rewrite_runtime_argv(remote_base_argv, metadata)
            artifact_phase = bool(metadata.get("collect_artifacts") or metadata.get("verify_artifacts"))
            explicit_artifact_output = bool(_find_value(remote_base_argv, {"--output-dir", "-o", "--out-dir", "--outdir"}).strip())
            remote_base_argv, local_artifact_dir, remote_artifact_dir = _rewrite_artifact_output_for_remote(
                remote_base_argv,
                config_obj=project_config,
                config=config,
                local_root=local_root,
                host_cwd=host_cwd,
                remote_cwd=remote_cwd,
                remote_root=remote_root,
                session=session,
                artifact_phase=artifact_phase,
            )
            translated_argv, staged = _stage_support_files_for_spec(
                config,
                remote_base_argv,
                local_root=local_root,
                remote_root=remote_root,
                session=session,
                dry_run=dry_run,
                metadata=metadata,
            )
            translated_env = _translate_env_for_repo(getattr(spec, "env", {}) or {}, local_root, remote_root, config=config)
            if config.execution_provider == SSH_PROOT_OCI:
                translated_env = {
                    key: value for key, value in translated_env.items()
                    if not str(key).startswith(("PD_", "PROOT_"))
                }
            from .ssh_resilience import split_sensitive_env
            public_env, sensitive_env = split_sensitive_env(translated_env)
            if config.execution_provider == SSH_PROOT_OCI and sensitive_env and not dry_run:
                from .ssh_resilience import remote_phase_state_paths
                expected_secret_file = _remote_join(remote_phase_state_paths(config, run_identity, session)["root"], "secret-env.sh")
                # Track both final and atomic temporary names before the write so a
                # failed staging command cannot leave either behind.
                oci_construction_secret_files.extend((expected_secret_file, expected_secret_file + ".tmp"))
            secret_env_file = _stage_sensitive_env(
                config, session=session, run_id=run_identity, values=sensitive_env, dry_run=dry_run
            )
            if sensitive_env:
                metadata["ssh_sensitive_env_keys"] = sorted(sensitive_env)
                metadata["ssh_sensitive_env_staged"] = True

            if config.execution_provider == SSH_PROOT_OCI:
                if oci_state is None:
                    raise ValueError("OCI command wrapping requires prepared OCI execution state")
                operation_host = _operation_stage_root(config, session)
                operation_guest = "/run/devtool/" + session
                guest_cwd = _oci_guest_translate(remote_cwd, remote_root=remote_root, operation_root=operation_host, operation_guest=operation_guest)
                translated_argv = [
                    _oci_guest_translate_token(token, remote_root=remote_root, operation_root=operation_host, operation_guest=operation_guest)
                    for token in translated_argv
                ]
                public_env = {
                    key: _oci_guest_translate(value, remote_root=remote_root, operation_root=operation_host, operation_guest=operation_guest)
                    for key, value in public_env.items()
                }
                bind_operation = bool(staged) or artifact_phase
                if bind_operation and not dry_run:
                    _ensure_remote_dir(config, operation_host)
                secret_host_dir = secret_env_file.rsplit("/", 1)[0] if secret_env_file else ""
                secret_guest_dir = "/run/devtool-secret/" + session if secret_env_file else ""
                command_core = _shell_join_remote(translated_argv)
                if artifact_phase and not explicit_artifact_output:
                    native_output_host = _translate_repo_bound_path(str(local_artifact_dir), local_root, remote_root)
                    if native_output_host == str(local_artifact_dir) and os.path.isabs(str(local_artifact_dir)):
                        raise ValueError("SSH artifact output without an explicit tool output flag must map inside the declared remote repository")
                    if native_output_host.rstrip("/") == remote_root.rstrip("/"):
                        raise ValueError("SSH artifact output directory cannot be the repository root")
                    native_output = _oci_guest_translate(native_output_host, remote_root=remote_root, operation_root=operation_host, operation_guest=operation_guest)
                    guest_artifact_dir = _oci_guest_translate(remote_artifact_dir, remote_root=remote_root, operation_root=operation_host, operation_guest=operation_guest)
                    guest_command = (
                        "cd " + shlex.quote(guest_cwd)
                        + " && rm -rf -- " + shlex.quote(native_output)
                        + " && mkdir -p -- " + shlex.quote(native_output)
                        + " && " + command_core
                        + '; rc=$?; if [ "$rc" -eq 0 ]; then '
                        + "rm -rf -- " + shlex.quote(guest_artifact_dir)
                        + " && mkdir -p -- " + shlex.quote(guest_artifact_dir)
                        + " && cp -a -- " + shlex.quote(native_output).rstrip("/") + "/. " + shlex.quote(guest_artifact_dir)
                        + ' || rc=$?; fi; exit "$rc"'
                    )
                else:
                    guest_command = "cd " + shlex.quote(guest_cwd) + " && " + command_core
                command = _oci_wrap_guest_command(
                    config, plan=oci_state.plan, guest_cwd=guest_cwd, command=guest_command,
                    public_env=public_env, sensitive_keys=sensitive_env.keys(),
                    operation_host=operation_host, operation_guest=operation_guest, bind_operation=bind_operation,
                    secret_host_dir=secret_host_dir, secret_guest_dir=secret_guest_dir,
                )
                metadata["oci_guest_cwd"] = guest_cwd
                metadata["oci_operation_bind"] = bool(bind_operation)
                metadata["oci_secret_bind"] = bool(secret_env_file)
            else:
                assignments = [f"{key}={value}" for key, value in sorted(public_env.items())]
                env_prefix = "env " + " ".join(shlex.quote(item) for item in assignments) + " " if assignments else ""
                secret_prefix = (". " + _remote_shell_path(secret_env_file) + " && ") if secret_env_file else ""
                actual_command = secret_prefix + env_prefix + _shell_join_remote(translated_argv)
                if artifact_phase and not explicit_artifact_output:
                    native_output = _translate_repo_bound_path(str(local_artifact_dir), local_root, remote_root)
                    if native_output == str(local_artifact_dir) and os.path.isabs(str(local_artifact_dir)):
                        raise ValueError("SSH artifact output without an explicit tool output flag must map inside the declared remote repository")
                    if native_output.rstrip("/") == remote_root.rstrip("/"):
                        raise ValueError("SSH artifact output directory cannot be the repository root")
                    inner = (
                        "cd " + _remote_shell_path(remote_cwd)
                        + " && rm -rf -- " + _remote_shell_path(native_output)
                        + " && mkdir -p -- " + _remote_shell_path(native_output)
                        + " && " + actual_command
                        + '; rc=$?; if [ "$rc" -eq 0 ]; then '
                        + "rm -rf -- " + _remote_shell_path(remote_artifact_dir)
                        + " && mkdir -p -- " + _remote_shell_path(remote_artifact_dir)
                        + " && cp -a -- " + _remote_shell_path(native_output).rstrip("/") + "/. " + _remote_shell_path(remote_artifact_dir)
                        + ' || rc=$?; fi; exit "$rc"'
                    )
                    command = "sh -c " + shlex.quote(inner)
                else:
                    command = "cd " + _remote_shell_path(remote_cwd) + " && " + actual_command
            if federation_attestation_probe:
                command = federation_attestation_probe + " && " + command
            workspace_id = str(preparation_metadata.get("ssh_workspace_id") or "")
            lease_meta = preparation_metadata.get("ssh_workspace_lease") or {}
            lease_dir = str(lease_meta.get("lease_dir") or "") if isinstance(lease_meta, dict) else ""
            phase_meta = {**preparation_metadata, **metadata}
            phase_meta["execution_mode"] = ("ssh-proot-oci" if config.execution_provider == SSH_PROOT_OCI else "ssh-command")
            phase_meta["execution_provider"] = config.execution_provider
            phase_meta["ssh_session"] = session
            phase_meta["ssh_phase"] = str(getattr(spec, "name", "phase") or "phase")
            phase_meta["ssh_target"] = str(metadata.get("target") or getattr(project_config, "selected_target", "") or "")
            phase_meta["ssh_repository_root"] = str(remote_root)
            repository_identity = getattr(project_config, "repository_identity", None)
            phase_meta["ssh_repository_fingerprint"] = str(getattr(repository_identity, "fingerprint", "") or "")
            phase_meta["ssh_frozen_source_lock_digest"] = str(metadata.get("frozen_source_lock_digest") or metadata.get("ssh_frozen_source_lock_digest") or "")
            phase_meta["ssh_contract_ref"] = str(metadata.get("federation_contract_ref") or metadata.get("contract_ref") or "")
            child_contract = str(metadata.get("federation_child_contract") or metadata.get("child_contract") or "")
            if child_contract and ":" in child_contract:
                phase_meta["ssh_contract_type"], phase_meta["ssh_contract_name"] = child_contract.split(":", 1)
            else:
                phase_meta["ssh_contract_type"] = str(metadata.get("contract_type") or "")
                phase_meta["ssh_contract_name"] = str(metadata.get("contract_name") or "")
            phase_meta["ssh_output_schema"] = "split-v1"
            phase_meta["ssh_confirmation_mode"] = str(metadata.get("ssh_confirmation_mode") or "interactive")
            phase_meta["oci_phase_node_id"] = phase_node_id(run_identity, session, provider=str(phase_meta.get("execution_provider") or "ssh-command"))
            remote_script = _build_remote_phase_script(
                config, session=session, command=command, run_id=run_identity,
                workspace_id=workspace_id, workspace_path=remote_root, lease_dir=lease_dir,
                phase_metadata=phase_meta,
                attach=not bool(metadata.get("ssh_exec_detached")),
                release_lease_on_completion=bool(metadata.get("ssh_exec_release_lease")),
            )
            ssh_command = _build_ssh_remote_sh_argv(config, remote_script, tty=True)

            kill_command = _build_ssh_remote_sh_argv(
                config, f"tmux kill-session -t {shlex.quote(session)} >/dev/null 2>&1 || true", tty=False
            )
            trap_parts = [shlex.join(kill_command)]
            if secret_env_file:
                trap_secret_cleanup = _build_ssh_remote_sh_argv(
                    config, f"rm -f -- {_remote_shell_path(secret_env_file)}", tty=False
                )
                trap_parts.append(shlex.join(trap_secret_cleanup))
            if lease_release_argv:
                trap_parts.append(shlex.join(lease_release_argv))
            trap_parts.append("exit 130")
            local_script = (
                "trap " + shlex.quote("; ".join(trap_parts)) + " INT TERM; "
                + shlex.join(ssh_command) + "; rc=$?; trap - INT TERM; "
                + 'if [ "$rc" -eq 255 ]; then echo "Devtool SSH transport lost; remote tmux phase may still be running. Use the SSH worker runs/resume commands" >&2; rc=125; fi'
            )
            if return_fragment:
                local_script += return_fragment
            if bool(metadata.get("collect_artifacts") or metadata.get("verify_artifacts")) and config.copy_artifacts:
                # Pull before local verification and clear the destination first so
                # a previous invocation cannot contaminate current artifact truth.
                source = f"{config.destination}:{_remote_scp_path(remote_artifact_dir).rstrip('/')}/."
                scp = [*build_scp_base_argv(config), "-r", source, str(local_artifact_dir)]
                local_script += (
                    "; if [ \"$rc\" -eq 0 ]; then rm -rf -- " + shlex.quote(str(local_artifact_dir))
                    + " && mkdir -p " + shlex.quote(str(local_artifact_dir))
                    + " && " + shlex.join(scp) + " || rc=$?; fi"
                )
            # Secret material is never evidence. Remove its mode-600 remote file on
            # every definitive completion/failure, but retain it across rc=125 because
            # the transport may have dropped before the tmux shell sourced it.
            if secret_env_file:
                secret_cleanup = _build_ssh_remote_sh_argv(
                    config, f"rm -f -- {_remote_shell_path(secret_env_file)}", tty=False
                )
                local_script += '; if [ "$rc" -ne 125 ]; then ' + shlex.join(secret_cleanup) + ' || rc=$?; fi'
            # Inputs and copied artifact outputs are operation-owned. Delete them
            # only after the command and any requested artifact retrieval succeed;
            # retain them on failure so recovery/retrieval remains possible.
            cleanup_operation = bool(staged) or (artifact_phase and config.copy_artifacts)
            if cleanup_operation:
                cleanup_remote = _build_ssh_remote_sh_argv(
                    config, f"rm -rf -- {_remote_shell_path(_operation_stage_root(config, session))}", tty=False
                )
                local_script += "; if [ \"$rc\" -eq 0 ]; then " + shlex.join(cleanup_remote) + " || rc=$?; fi"
            if lease_release_argv:
                release = shlex.join(lease_release_argv)
                if ordinal == last_external_ordinal:
                    local_script += '; if [ "$rc" -ne 125 ]; then ' + release + ' || rc=$?; fi'
                else:
                    local_script += '; if [ "$rc" -ne 0 ] && [ "$rc" -ne 125 ]; then ' + release + ' || rc=$?; fi'
            local_script += '; exit "$rc"'
            metadata.update(preparation_metadata)
            metadata["execution_mode"] = ("ssh-proot-oci" if config.execution_provider == SSH_PROOT_OCI else "ssh-command")
            metadata["execution_provider"] = config.execution_provider
            metadata["ssh_mode"] = "commands"
            metadata["ssh_destination"] = config.destination
            metadata["ssh_port"] = config.port
            metadata["ssh_session"] = session
            metadata["oci_phase_node_id"] = phase_meta.get("oci_phase_node_id", "")
            metadata["ssh_cwd"] = remote_cwd
            metadata["ssh_staged_files"] = [str(local) for local, _remote in staged]
            metadata.setdefault("progress_argv", original_argv)
            metadata.setdefault("progress_cwd", host_cwd)
            wrapped.append(replace(spec, argv=["sh", "-lc", local_script], cwd=None, env={}, metadata=metadata))
        wrapping_complete = True
        return wrapped
    finally:
        if not wrapping_complete and config.execution_provider == SSH_PROOT_OCI and not dry_run:
            _cleanup_oci_construction_state(
                config,
                secret_files=oci_construction_secret_files,
                operation_roots=oci_construction_operation_roots,
            )
        # Preparation may already have acquired the managed workspace lease. If
        # local wrapping/staging/preflight fails before the shell plan owns that
        # lease, release it here so a construction error cannot deadlock the
        # worker until stale-lease expiry.
        if not wrapping_complete and preparation is not None and preparation.lease and not dry_run:
            from .ssh_resilience import SSHWorkspaceLease, release_workspace_lease
            release_workspace_lease(config, SSHWorkspaceLease(**preparation.lease))


def build_remote_tmux_script(config: SSHRunnerConfig, forwarded: list[str]) -> str:
    from .federation_transport import require_federation_remote_attestation_environment
    require_federation_remote_attestation_environment()
    session = _sanitize_session(config.session or _default_session(config, forwarded))
    config.session = session
    state_dir = '${XDG_STATE_HOME:-$HOME/.local/state}/devtool/ssh-tmux'
    status_path = f'$state_dir/{session}.status'
    inner_status_path = f'${{XDG_STATE_HOME:-$HOME/.local/state}}/devtool/ssh-tmux/{session}.status'
    devtool_argv = [config.tool, *forwarded]

    run_id = str(os.environ.get("DEVTOOL_RUN_ID") or "").strip()
    forwarded_env: list[str] = []
    if run_id:
        forwarded_env.append("DEVTOOL_RUN_ID=" + run_id)
    for key, value in sorted(os.environ.items()):
        if key.startswith("DEVTOOL_FEDERATION_") and str(value).strip():
            forwarded_env.append(f"{key}={value}")
    env_suffix = (" " + " ".join(shlex.quote(item) for item in forwarded_env)) if forwarded_env else ""
    command = (
        "env DEVTOOL_EXECUTION_CONTEXT=ssh DEVTOOL_INSIDE_SSH=1 DEVTOOL_SSH_CONTEXT="
        + shlex.quote(config.destination)
        + env_suffix
        + " " + _shell_join_remote(devtool_argv)
    )
    if config.cwd:
        remote_cd = '"$HOME"' if config.cwd in {"~", "$HOME"} else _quote_remote_token(config.cwd)
        command = "cd " + remote_cd + " && " + command

    tmux_payload = (
        command
        + "; rc=$?; printf '%s\\n' \"$rc\" > \""
        + inner_status_path
        + "\"; exit \"$rc\""
    )

    lines = [
        "set -eu",
        f"session={shlex.quote(session)}",
        f"state_dir={state_dir}",
        f"status=\"{status_path}\"",
        'mkdir -p "$state_dir"',
        "command -v tmux >/dev/null 2>&1 || { echo 'devtool ssh: tmux is required on the remote host' >&2; exit 127; }",
        'if ! tmux has-session -t "$session" 2>/dev/null; then',
        '  rm -f "$status"',
        "  tmux new-session -d -s \"$session\" " + shlex.quote("sh -lc " + shlex.quote(tmux_payload)),
        "fi",
    ]
    if config.detached:
        lines.extend([
            'echo "Devtool SSH tmux session started: $session"',
            'echo "Remote reattach: tmux attach-session -t $session"',
            "exit 0",
        ])
    else:
        lines.extend([
            'echo "Devtool SSH tmux session: $session (detach: Ctrl-b d)"',
            'tmux attach-session -t "$session" || true',
            'if [ -f "$status" ]; then rc=$(cat "$status" 2>/dev/null || printf 1); rm -f "$status"; exit "$rc"; fi',
            'echo "Detached; remote devtool continues in tmux session: $session"',
            "exit 0",
        ])
    return "\n".join(lines) + "\n"


def build_ssh_tmux_argv(
    argv: list[str],
    *,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[SSHRunnerConfig, list[str], list[str]]:
    config = resolve_ssh_config(argv, cwd=cwd or os.getcwd(), env=env)
    forwarded = _forwarded_argv(argv)
    remote_script = build_remote_tmux_script(config, forwarded)
    command = _build_ssh_remote_sh_argv(config, remote_script, tty=not config.detached)
    return config, forwarded, command


def reattach_command(config: SSHRunnerConfig) -> list[str]:
    return [*build_ssh_base_argv(config, tty=True), "tmux", "attach-session", "-t", config.session]


def run_ssh_tmux(
    argv: list[str],
    *,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    local_cwd = cwd or os.getcwd()
    config = resolve_ssh_config(argv, cwd=local_cwd, env=env)
    if config.mode != "devtool":
        raise ValueError("run_ssh_tmux is the whole-process devtool transport; use command wrapping for ssh.mode=commands")
    forwarded = _forwarded_argv(argv)
    _ensure_session(config, forwarded)
    _preflight_local_transport(config)
    _remote_tmux_preflight(config)
    forwarded, staged = stage_forwarded_artifacts(config, forwarded, cwd=local_cwd)
    remote_script = build_remote_tmux_script(config, forwarded)
    command = _build_ssh_remote_sh_argv(config, remote_script, tty=not config.detached)
    print(f"Devtool SSH: {config.destination}:{config.port} (mode: {config.mode})")
    if config.cwd:
        print(f"Remote cwd: {config.cwd}")
    else:
        print("Remote cwd: login default (not inferred from local paths)")
    for local, remote in staged:
        print(f"SSH artifact copy: {local} -> {config.destination}:{remote}")
    print(f"tmux session: {config.session} (detach with Ctrl-b then d)")
    print("Reattach: " + shlex.join(reattach_command(config)))
    from .ssh_multiplex import close_master, run_with_multiplex_fallback
    try:
        completed = run_with_multiplex_fallback(config, command, check=False)
        if staged and not _session_active(config, config.session):
            _remove_remote_tree(config, _operation_stage_root(config, config.session))
        return int(completed.returncode)
    finally:
        # Whole-process SSH has a concrete operation boundary, so close the
        # ControlMaster eagerly instead of waiting for ControlPersist/atexit.
        # Command-mode executions retain the process-level atexit cleanup.
        close_master(config)
