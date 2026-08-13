from __future__ import annotations

"""SSH + tmux whole-process execution transport.

SSH is intentionally modelled as a transport, not as a chroot-like filesystem
backend. The local process owns connection/session settings only. Devtool
arguments are forwarded unchanged to the remote host (apart from stripping the
local SSH transport flags), and remote paths are never derived from local paths.

If ``ssh.cwd``/``--ssh-cwd`` is set, the remote tmux command starts there.
Otherwise the SSH login shell's normal working directory is used.
"""

import os
import re
import shlex
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


_SSH_VALUE_FLAGS = {
    "--ssh",
    "--ssh-host",
    "--ssh-port",
    "--ssh-user",
    "--ssh-identity",
    "--ssh-session",
    "--ssh-cwd",
    "--ssh-tool",
}
_GLOBAL_VALUE_FLAGS = {
    "-r", "--repo", "-c", "--config", "--solution", "--project",
    "--machine-file", "--android-sdk-root", "--java-home", "--gradle-command",
    "--gradle-user-home", "-o", "--output", "--target", "--variant",
    "--project-type", "--performance-file", "--copy-via", "--theme",
    "--stdout-payload", "--hud-mode", "--chroot", "--execution-environment",
    "--matrix", "--matrix-mode", "--toolchain-profile",
}
_TRUE = {"1", "true", "yes", "on"}


@dataclass(slots=True)
class SSHRunnerConfig:
    host: str = ""
    port: int = 22
    user: str = ""
    identity: str = ""
    session: str = ""
    cwd: str = ""
    tool: str = "devtool"
    detached: bool = False
    config_path: str = ""

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


def _load_raw_config(argv: list[str], cwd: str) -> tuple[dict[str, Any], Path | None]:
    path = _config_path_from_args(argv, cwd)
    if path is None:
        return {}, None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Unable to read SSH settings from {path}: {exc}") from exc
    return (data if isinstance(data, dict) else {}), path


def _ssh_section(data: Mapping[str, Any], target_name: str = "") -> dict[str, Any]:
    merged: dict[str, Any] = {}
    top = data.get("ssh", {})
    if isinstance(top, dict):
        merged.update(top)

    execution = data.get("execution", {})
    if isinstance(execution, dict):
        nested = execution.get("ssh", {})
        if isinstance(nested, dict):
            merged.update(nested)
        for key in (
            "ssh_host", "ssh_port", "ssh_user", "ssh_identity",
            "ssh_session", "ssh_cwd", "ssh_tool",
        ):
            if key in execution:
                merged[key.removeprefix("ssh_")] = execution[key]

    if target_name:
        targets = data.get("targets", {})
        target = targets.get(target_name, {}) if isinstance(targets, dict) else {}
        if isinstance(target, dict):
            nested = target.get("ssh", {})
            if isinstance(nested, dict):
                merged.update(nested)
            for key in (
                "ssh_host", "ssh_port", "ssh_user", "ssh_identity",
                "ssh_session", "ssh_cwd", "ssh_tool",
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
    env = env or os.environ
    if _truthy(env.get("DEVTOOL_INSIDE_SSH")):
        return False
    if _find_value(argv, {"--ssh", "--ssh-host"}).strip():
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
    env = env or os.environ
    cwd = cwd or os.getcwd()
    data, config_path = _load_raw_config(argv, cwd)
    target_name = _find_value(argv, {"--target"}).strip()
    file_options = _ssh_section(data, target_name)

    cli_host = _find_value(argv, {"--ssh", "--ssh-host"}).strip()
    cli_port = _find_value(argv, {"--ssh-port"}).strip()
    cli_user = _find_value(argv, {"--ssh-user"}).strip()
    cli_identity = _find_value(argv, {"--ssh-identity"}).strip()
    cli_session = _find_value(argv, {"--ssh-session"}).strip()
    cli_cwd = _find_value(argv, {"--ssh-cwd"}).strip()
    cli_tool = _find_value(argv, {"--ssh-tool"}).strip()

    host = cli_host or str(env.get("DEVTOOL_SSH_HOST", "") or "").strip() or str(file_options.get("host", "") or "").strip()
    port_value = cli_port or str(env.get("DEVTOOL_SSH_PORT", "") or "").strip() or file_options.get("port", 22)
    user = cli_user or str(env.get("DEVTOOL_SSH_USER", "") or "").strip() or str(file_options.get("user", "") or "").strip()
    identity = cli_identity or str(env.get("DEVTOOL_SSH_IDENTITY", "") or "").strip() or str(file_options.get("identity", "") or "").strip()
    session = cli_session or str(env.get("DEVTOOL_SSH_SESSION", "") or "").strip() or str(file_options.get("session", "") or "").strip()
    remote_cwd = cli_cwd or str(env.get("DEVTOOL_SSH_CWD", "") or "").strip() or str(file_options.get("cwd", "") or "").strip()
    tool = cli_tool or str(env.get("DEVTOOL_SSH_TOOL", "") or "").strip() or str(file_options.get("tool", "") or "").strip() or "devtool"
    detached = "--ssh-detached" in argv or _truthy(env.get("DEVTOOL_SSH_DETACHED")) or _truthy(file_options.get("detached"))

    if not host:
        source = f" in {config_path}" if config_path else ""
        raise ValueError(f"SSH execution requested but no host is configured{source}. Use --ssh HOST or set [ssh].host.")
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
        detached=detached,
        config_path=str(config_path or ""),
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
        if token == "--ssh-detached" or token.startswith("-"):
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
        if token == "--ssh-detached":
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
    parts = ["devtool", location, command]
    return _sanitize_session("-".join(part for part in parts if part))


def build_ssh_base_argv(config: SSHRunnerConfig, *, tty: bool = True) -> list[str]:
    ssh = os.environ.get("DEVTOOL_SSH_COMMAND", "ssh").strip() or "ssh"
    argv = [ssh]
    if tty:
        argv.append("-tt")
    argv.extend(["-p", str(config.port)])
    if config.identity:
        argv.extend(["-i", config.identity])
    argv.append(config.destination)
    return argv


def _quote_remote_token(token: str) -> str:
    # Preserve explicit remote-home expressions. Users should quote them in the
    # local shell (for example '--repo', '~/Code/app') to prevent local expansion.
    if token.startswith("~/") or token.startswith("$HOME/"):
        return token
    return shlex.quote(token)


def _shell_join_remote(argv: list[str]) -> str:
    return " ".join(_quote_remote_token(token) for token in argv)


def build_remote_tmux_script(config: SSHRunnerConfig, forwarded: list[str]) -> str:
    session = _sanitize_session(config.session or _default_session(config, forwarded))
    config.session = session
    state_dir = '${XDG_STATE_HOME:-$HOME/.local/state}/devtool/ssh-tmux'
    status_path = f'$state_dir/{session}.status'
    inner_status_path = f'${{XDG_STATE_HOME:-$HOME/.local/state}}/devtool/ssh-tmux/{session}.status'
    devtool_argv = [config.tool, *forwarded]

    command = (
        "env DEVTOOL_EXECUTION_CONTEXT=ssh DEVTOOL_INSIDE_SSH=1 DEVTOOL_SSH_CONTEXT="
        + shlex.quote(config.destination)
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
    command = [*build_ssh_base_argv(config, tty=not config.detached), "sh", "-lc", remote_script]
    return config, forwarded, command


def reattach_command(config: SSHRunnerConfig) -> list[str]:
    return [*build_ssh_base_argv(config, tty=True), "tmux", "attach-session", "-t", config.session]


def run_ssh_tmux(
    argv: list[str],
    *,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    config, _forwarded, command = build_ssh_tmux_argv(argv, cwd=cwd, env=env)
    print(f"Devtool SSH: {config.destination}:{config.port}")
    if config.cwd:
        print(f"Remote cwd: {config.cwd}")
    else:
        print("Remote cwd: login default (not inferred from local paths)")
    print(f"tmux session: {config.session} (detach with Ctrl-b then d)")
    print("Reattach: " + shlex.join(reattach_command(config)))
    completed = subprocess.run(command, check=False)
    return int(completed.returncode)
