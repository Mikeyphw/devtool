from __future__ import annotations

"""Remote SSH build-worker discovery and bootstrap primitives.

This module deliberately keeps worker management separate from repository
synchronization. SSHW-01 establishes a trustworthy, tmux-capable remote worker;
managed workspaces and rsync planning arrive in SSHW-02.
"""

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Any, Mapping

from .ssh_tmux_runner import (
    SSHRunnerConfig,
    build_ssh_remote_sh_argv,
    list_tmux_sessions,
    resolve_ssh_config,
    tmux_session_status,
    worker_session_name,
)

_REQUIRED_TOOLS = ("python", "tmux", "rsync", "git")
_TOOL_COMMANDS = {
    "python": ("python3", "python"),
    "tmux": ("tmux",),
    "rsync": ("rsync",),
    "git": ("git",),
}
_TERMUX_PACKAGES = {"python": "python", "tmux": "tmux", "rsync": "rsync", "git": "git"}
_LINUX_PACKAGES = {"python": "python3", "tmux": "tmux", "rsync": "rsync", "git": "git"}


@dataclass(slots=True)
class SSHWorkerProbe:
    profile: str
    destination: str
    port: int
    platform: str
    os_name: str
    architecture: str
    cpu_count: int
    memory_kib: int
    free_storage_kib: int
    home: str
    prefix: str
    shell: str
    package_manager: str
    tools: dict[str, str]
    versions: dict[str, str]
    bootstrap_policy: str
    tmux_required: bool

    @property
    def missing_required(self) -> list[str]:
        return [name for name in _REQUIRED_TOOLS if not self.tools.get(name)]

    @property
    def ready(self) -> bool:
        return not self.missing_required

    @property
    def bootstrap_supported(self) -> bool:
        if self.platform == "termux":
            return self.package_manager == "pkg"
        return self.package_manager in {"apt-get", "dnf", "pacman", "apk"}

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(
            {
                "missing_required": self.missing_required,
                "ready": self.ready,
                "bootstrap_supported": self.bootstrap_supported,
            }
        )
        return payload


def _transport_timeout() -> float:
    raw = str(os.environ.get("DEVTOOL_SSH_TRANSPORT_TIMEOUT", "30") or "30").strip()
    try:
        return max(1.0, min(float(raw), 3600.0))
    except ValueError:
        return 30.0


def _ssh_executable() -> str:
    candidate = str(os.environ.get("DEVTOOL_SSH_COMMAND", "ssh") or "ssh").strip()
    resolved = shutil.which(candidate) if os.sep not in candidate else candidate
    if not resolved or not Path(resolved).is_file() or not os.access(resolved, os.X_OK):
        raise ValueError(f"SSH executable is unavailable or not executable: {candidate}")
    return str(resolved)


def _preflight_transport(config: SSHRunnerConfig) -> None:
    _ssh_executable()
    if config.identity:
        identity = Path(config.identity).expanduser()
        if not identity.is_file() or not os.access(identity, os.R_OK):
            raise ValueError(f"SSH identity file is unavailable or unreadable: {identity}")


def _remote_shell_argv(config: SSHRunnerConfig, script: str) -> list[str]:
    # OpenSSH joins remote argv into one shell command; use the transport's
    # canonical quoting helper instead of passing ``sh -lc`` as loose argv.
    return build_ssh_remote_sh_argv(config, script, tty=False)


def _run_remote(
    config: SSHRunnerConfig,
    script: str,
    *,
    capture_output: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    _preflight_transport(config)
    return subprocess.run(
        _remote_shell_argv(config, script),
        check=False,
        capture_output=capture_output,
        text=True,
        timeout=_transport_timeout() if timeout is None else timeout,
    )


def _probe_script() -> str:
    # POSIX shell only: doctor must work before Python/tmux/rsync/git are installed.
    return r'''set +e
kv() { printf '%s=%s\n' "$1" "$2"; }
platform=linux
case "${PREFIX:-}" in
  */com.termux/*|*/termux/*) platform=termux ;;
esac
if [ -n "${TERMUX_VERSION:-}" ] || [ -d /data/data/com.termux/files/usr ]; then platform=termux; fi
termux_prefix="${PREFIX:-}"
if [ "$platform" = termux ] && [ -z "$termux_prefix" ] && [ -d /data/data/com.termux/files/usr ]; then
  termux_prefix=/data/data/com.termux/files/usr
fi
find_cmd() {
  found="$(command -v "$1" 2>/dev/null || true)"
  if [ -z "$found" ] && [ "$platform" = termux ] && [ -n "$termux_prefix" ] && [ -x "$termux_prefix/bin/$1" ]; then
    found="$termux_prefix/bin/$1"
  fi
  printf '%s' "$found"
}
kv platform "$platform"
kv os_name "$(uname -s 2>/dev/null || printf unknown)"
kv architecture "$(uname -m 2>/dev/null || printf unknown)"
cpus="$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || grep -c '^processor' /proc/cpuinfo 2>/dev/null || printf 0)"
case "$cpus" in ''|*[!0-9]*) cpus=0;; esac
kv cpu_count "$cpus"
mem="$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null)"
case "$mem" in ''|*[!0-9]*) mem=0;; esac
kv memory_kib "$mem"
free="$(df -Pk "$HOME" 2>/dev/null | awk 'NR==2 {print $4; exit}')"
case "$free" in ''|*[!0-9]*) free=0;; esac
kv free_storage_kib "$free"
kv home "${HOME:-}"
kv prefix "$termux_prefix"
kv shell "${SHELL:-}"
pm=""
if [ "$platform" = termux ] && [ -n "$(find_cmd pkg)" ]; then
  pm=pkg
else
  for candidate in apt-get dnf pacman apk; do
    if [ -n "$(find_cmd "$candidate")" ]; then pm="$candidate"; break; fi
  done
fi
kv package_manager "$pm"
tool_python="$(find_cmd python3)"; [ -n "$tool_python" ] || tool_python="$(find_cmd python)"
tool_tmux="$(find_cmd tmux)"
tool_rsync="$(find_cmd rsync)"
tool_git="$(find_cmd git)"
tool_java="$(find_cmd java)"
tool_gradle="$(find_cmd gradle)"
for logical in python tmux rsync git java gradle; do eval "value=\${tool_$logical}"; kv "tool_$logical" "$value"; done
version_python=""; [ -n "$tool_python" ] && version_python="$("$tool_python" --version 2>&1 | head -n1)"
version_tmux=""; [ -n "$tool_tmux" ] && version_tmux="$("$tool_tmux" -V 2>/dev/null | head -n1)"
version_rsync=""; [ -n "$tool_rsync" ] && version_rsync="$("$tool_rsync" --version 2>/dev/null | head -n1)"
version_git=""; [ -n "$tool_git" ] && version_git="$("$tool_git" --version 2>/dev/null | head -n1)"
version_java=""; [ -n "$tool_java" ] && version_java="$("$tool_java" -version 2>&1 | head -n1)"
version_gradle=""; [ -n "$tool_gradle" ] && version_gradle="$("$tool_gradle" --version 2>/dev/null | awk 'NF {print; exit}')"
for logical in python tmux rsync git java gradle; do eval "value=\${version_$logical}"; kv "version_$logical" "$value"; done
exit 0
'''


def _parse_kv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in str(text or "").splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def _int_value(value: object) -> int:
    try:
        return max(0, int(str(value or "0").strip()))
    except ValueError:
        return 0


def probe_worker(config: SSHRunnerConfig) -> SSHWorkerProbe:
    completed = _run_remote(config, _probe_script())
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"SSH worker probe failed with exit {completed.returncode}: {detail or 'no diagnostic output'}")
    values = _parse_kv(completed.stdout)
    tools = {name: values.get(f"tool_{name}", "") for name in ("python", "tmux", "rsync", "git", "java", "gradle")}
    versions = {name: values.get(f"version_{name}", "") for name in ("python", "tmux", "rsync", "git", "java", "gradle")}
    return SSHWorkerProbe(
        profile=config.profile,
        destination=config.destination,
        port=config.port,
        platform=values.get("platform", "unknown") or "unknown",
        os_name=values.get("os_name", "unknown") or "unknown",
        architecture=values.get("architecture", "unknown") or "unknown",
        cpu_count=_int_value(values.get("cpu_count")),
        memory_kib=_int_value(values.get("memory_kib")),
        free_storage_kib=_int_value(values.get("free_storage_kib")),
        home=values.get("home", ""),
        prefix=values.get("prefix", ""),
        shell=values.get("shell", ""),
        package_manager=values.get("package_manager", ""),
        tools=tools,
        versions=versions,
        bootstrap_policy=config.bootstrap_policy,
        tmux_required=True if config.profile else bool(config.worker_tmux),
    )


def worker_doctor_payload(config: SSHRunnerConfig) -> dict[str, Any]:
    probe = probe_worker(config)
    sessions: list[str] = []
    sessions_error = ""
    if probe.tools.get("tmux"):
        try:
            sessions = list_tmux_sessions(config)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            sessions_error = str(exc)
    return {
        "schema_version": 1,
        "kind": "ssh-worker-doctor",
        "profile": config.profile,
        "destination": config.destination,
        "port": config.port,
        "mode": config.mode,
        "bootstrap_policy": config.bootstrap_policy,
        "tmux_required": True if config.profile else bool(config.worker_tmux),
        "probe": probe.as_dict(),
        "sessions": sessions,
        "sessions_error": sessions_error,
        "healthy": probe.ready and not sessions_error and (not probe.tmux_required or bool(probe.tools.get("tmux"))),
    }


def _bootstrap_script(probe: SSHWorkerProbe, missing: list[str]) -> str:
    package_map = _TERMUX_PACKAGES if probe.platform == "termux" else _LINUX_PACKAGES
    packages = [package_map[name] for name in missing]
    quoted = " ".join(shlex.quote(item) for item in packages)
    if not quoted:
        return "exit 0"
    if probe.platform == "termux":
        if probe.package_manager != "pkg":
            raise ValueError("Termux worker bootstrap requires the pkg command")
        pkg = (probe.prefix.rstrip("/") + "/bin/pkg") if probe.prefix else "pkg"
        return f"set -eu; {shlex.quote(pkg)} install -y {quoted}"
    pm = probe.package_manager
    if pm not in {"apt-get", "dnf", "pacman", "apk"}:
        raise ValueError(f"Linux worker bootstrap is unsupported for package manager {pm or 'unknown'}")
    if pm == "apt-get":
        install = f"apt-get update && apt-get install -y {quoted}"
    elif pm == "dnf":
        install = f"dnf install -y {quoted}"
    elif pm == "pacman":
        install = f"pacman -S --needed --noconfirm {quoted}"
    else:
        install = f"apk add {quoted}"
    return (
        "set -eu; "
        "if [ \"$(id -u)\" = 0 ]; then " + install + "; "
        "elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then sudo -n sh -lc "
        + shlex.quote(install) + "; "
        "else echo 'devtool ssh bootstrap: root or passwordless sudo is required on Linux' >&2; exit 126; fi"
    )


def bootstrap_worker(
    config: SSHRunnerConfig,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    before = probe_worker(config)
    missing = before.missing_required
    if not missing:
        return {
            "schema_version": 1,
            "kind": "ssh-worker-bootstrap",
            "ok": True,
            "changed": False,
            "profile": config.profile,
            "destination": config.destination,
            "missing_before": [],
            "installed": [],
            "probe": before.as_dict(),
            "detail": "worker runtime already ready",
        }
    if config.bootstrap_policy == "never" and not force:
        return {
            "schema_version": 1,
            "kind": "ssh-worker-bootstrap",
            "ok": False,
            "changed": False,
            "profile": config.profile,
            "destination": config.destination,
            "missing_before": missing,
            "installed": [],
            "probe": before.as_dict(),
            "detail": "bootstrap policy is never; pass --force to override an explicit bootstrap request",
        }
    script = _bootstrap_script(before, missing)
    if dry_run:
        return {
            "schema_version": 1,
            "kind": "ssh-worker-bootstrap",
            "ok": True,
            "changed": False,
            "dry_run": True,
            "profile": config.profile,
            "destination": config.destination,
            "missing_before": missing,
            "installed": missing,
            "probe": before.as_dict(),
            "command": script,
            "detail": "bootstrap plan only; remote host was not modified",
        }
    completed = _run_remote(config, script, capture_output=True, timeout=max(_transport_timeout(), 600.0))
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return {
            "schema_version": 1,
            "kind": "ssh-worker-bootstrap",
            "ok": False,
            "changed": False,
            "profile": config.profile,
            "destination": config.destination,
            "missing_before": missing,
            "installed": [],
            "probe": before.as_dict(),
            "detail": detail or f"package installation failed with exit {completed.returncode}",
        }
    after = probe_worker(config)
    return {
        "schema_version": 1,
        "kind": "ssh-worker-bootstrap",
        "ok": after.ready,
        "changed": True,
        "profile": config.profile,
        "destination": config.destination,
        "missing_before": missing,
        "installed": [name for name in missing if after.tools.get(name)],
        "probe": after.as_dict(),
        "detail": "worker runtime ready" if after.ready else "bootstrap completed but required tools remain missing",
    }


def resolve_worker_profile(
    profile: str,
    *,
    repo: str = "",
    config_path: str = "",
    env: Mapping[str, str] | None = None,
) -> SSHRunnerConfig:
    argv: list[str] = []
    if repo:
        argv.extend(["--repo", repo])
    if config_path:
        argv.extend(["--config", config_path])
    argv.extend(["--ssh-profile", profile])
    return resolve_ssh_config(argv, cwd=os.getcwd(), env=env)


def session_payload(config: SSHRunnerConfig) -> dict[str, Any]:
    probe = probe_worker(config)
    if not probe.tools.get("tmux"):
        return {
            "schema_version": 1,
            "kind": "ssh-worker-sessions",
            "ok": False,
            "profile": config.profile,
            "destination": config.destination,
            "sessions": [],
            "detail": "tmux is not installed on the remote worker",
        }
    sessions = list_tmux_sessions(config)
    return {
        "schema_version": 1,
        "kind": "ssh-worker-sessions",
        "ok": True,
        "profile": config.profile,
        "destination": config.destination,
        "sessions": [{"name": name, "status": tmux_session_status(config, name)} for name in sessions],
        "example_session": worker_session_name(config, operation="build", repository=config.cwd, run_id="example"),
    }
