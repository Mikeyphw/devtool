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
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping


_SSH_VALUE_FLAGS = {
    "--ssh",
    "--ssh-host",
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
    mode: str = "devtool"
    stage_dir: str = "~/.cache/devtool/ssh-stage"
    copy_artifacts: bool = True
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
                merged.update(nested)
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
    cli_mode = _find_value(argv, {"--ssh-mode"}).strip()
    cli_stage_dir = _find_value(argv, {"--ssh-stage-dir"}).strip()

    host = cli_host or str(env.get("DEVTOOL_SSH_HOST", "") or "").strip() or str(file_options.get("host", "") or "").strip()
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
    copy_artifacts = not ("--ssh-no-artifact-copy" in argv or _truthy(env.get("DEVTOOL_SSH_NO_ARTIFACT_COPY")))
    if "copy_artifacts" in file_options and not _truthy(file_options.get("copy_artifacts")):
        copy_artifacts = False
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
        mode=mode,
        stage_dir=stage_dir,
        copy_artifacts=copy_artifacts,
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




_REMOTE_OWNED_ENV_PATH_KEYS = {
    "HOME", "PREFIX", "TMPDIR", "JAVA_HOME", "ANDROID_HOME",
    "ANDROID_SDK_ROOT", "GRADLE_USER_HOME", "VIRTUAL_ENV",
    "DEVTOOL_BUILD_CACHE_ROOT",
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
        argv.extend(["-i", config.identity])
    return argv


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
    """Render a configured remote path for a remote POSIX shell.

    ``~/...`` and ``$HOME/...`` are intentionally expanded by the *remote*
    shell. Everything else is quoted literally.
    """
    text = str(path or "")
    if text == "~":
        return '"$HOME"'
    if text.startswith("~/"):
        return '"$HOME/' + text[2:].replace('"', '\\"') + '"'
    if text == "$HOME":
        return '"$HOME"'
    if text.startswith("$HOME/"):
        return '"$HOME/' + text[6:].replace('"', '\\"') + '"'
    return shlex.quote(text)


def _remote_scp_path(path: str) -> str:
    """Return a remote scp operand path without local expansion."""
    text = str(path or "")
    if text == "$HOME":
        return "~/"
    if text.startswith("$HOME/"):
        return "~/" + text[6:]
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


def _ensure_remote_dir(config: SSHRunnerConfig, remote_dir: str) -> None:
    script = f"mkdir -p -- {_remote_shell_path(remote_dir)}"
    command = _build_ssh_remote_sh_argv(config, script, tty=False)
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise ValueError(f"could not create remote SSH staging directory: {remote_dir}")


def _copy_local_file_to_remote(config: SSHRunnerConfig, source: Path, remote_path: str) -> None:
    destination = f"{config.destination}:{_remote_scp_path(remote_path)}"
    command = [*build_scp_base_argv(config), str(source), destination]
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise ValueError(f"could not copy {source} to {config.destination}:{remote_path}")


def _copy_remote_tree_to_local(config: SSHRunnerConfig, remote_dir: str, local_dir: Path) -> int:
    local_dir.mkdir(parents=True, exist_ok=True)
    source = f"{config.destination}:{_remote_scp_path(remote_dir).rstrip('/')}/."
    command = [*build_scp_base_argv(config), "-r", source, str(local_dir)]
    return int(subprocess.run(command, check=False).returncode)


def _ensure_session(config: SSHRunnerConfig, forwarded: list[str]) -> str:
    config.session = _sanitize_session(config.session or _default_session(config, forwarded))
    return config.session


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
    remote_dir = _remote_join(config.stage_dir, config.session, "inputs")
    for index, token in enumerate(forwarded):
        local = _looks_like_local_artifact(token, cwd=cwd)
        if local is None:
            continue
        remote = seen.get(local)
        if remote is None:
            digest = hashlib.sha256(str(local).encode("utf-8")).hexdigest()[:10]
            remote = _remote_join(remote_dir, f"{digest}-{local.name}")
            seen[local] = remote
            staged.append((local, remote))
        rewritten[index] = remote
    if staged:
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
) -> tuple[list[str], list[tuple[Path, str]]]:
    """Stage local helper scripts referenced by a planned command.

    Files inside the repo are mapped through the explicit repo root mapping.
    External regular files (for example the staged Android builder and Gradle
    init script) are copied to the SSH stage directory. Directories and argv[0]
    are never copied; remote toolchains remain remote-owned.
    """
    rewritten: list[str] = []
    staged: list[tuple[Path, str]] = []
    seen: dict[Path, str] = {}
    remote_support_dir = _remote_join(config.stage_dir, session, "support")
    for index, token in enumerate(argv):
        mapped = _translate_repo_bound_token(token, local_root, remote_root)
        if mapped != token:
            rewritten.append(mapped)
            continue
        if index == 0 or token.startswith("-") or "=" in token:
            rewritten.append(token)
            continue
        path = Path(token).expanduser()
        if not path.is_absolute() or not path.is_file():
            rewritten.append(token)
            continue
        # Do not copy executables used as argv[0]; for non-leading file operands
        # a transient copy is safe and avoids requiring devtool's helper files.
        digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:10]
        remote = seen.get(path)
        if remote is None:
            remote = _remote_join(remote_support_dir, f"{digest}-{path.name}")
            seen[path] = remote
            staged.append((path, remote))
        rewritten.append(remote)
    if staged and not dry_run:
        _ensure_remote_dir(config, remote_support_dir)
        for local, remote in staged:
            _copy_local_file_to_remote(config, local, remote)
    return rewritten, staged


def _translate_env_for_repo(env: Mapping[str, str], local_root: Path, remote_root: str) -> dict[str, str]:
    translated: dict[str, str] = {}
    for key, raw in env.items():
        key_text = str(key)
        value = str(raw)
        if key_text in _HOST_EXECUTION_ENV_KEYS:
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


def _build_remote_phase_script(
    config: SSHRunnerConfig,
    *,
    session: str,
    command: str,
) -> str:
    state_dir = _remote_join(config.stage_dir, session, "state")
    status_path = _remote_join(state_dir, "status")
    payload = command + f"; rc=$?; printf '%s\\n' \"$rc\" > {_remote_shell_path(status_path)}; exit \"$rc\""
    lines = [
        "set -eu",
        f"session={shlex.quote(session)}",
        f"state_dir={_remote_shell_path(state_dir)}",
        f"status={_remote_shell_path(status_path)}",
        'mkdir -p "$state_dir"',
        "command -v tmux >/dev/null 2>&1 || { echo 'devtool ssh: tmux is required on the remote host' >&2; exit 127; }",
        'if ! tmux has-session -t "$session" 2>/dev/null; then',
        '  rm -f "$status"',
        "  tmux new-session -d -s \"$session\" " + shlex.quote("sh -lc " + shlex.quote(payload)),
        "fi",
        'echo "Devtool SSH command session: $session (detach: Ctrl-b d)"',
        'tmux attach-session -t "$session" || true',
        'if [ -f "$status" ]; then rc=$(cat "$status" 2>/dev/null || printf 1); rm -f "$status"; exit "$rc"; fi',
        'echo "Detached; remote command continues in tmux session: $session" >&2',
        # Returning a distinct non-zero status prevents the local devtool from
        # incorrectly advancing to later phases while the remote command lives.
        "exit 125",
    ]
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
) -> tuple[list[str], Path, str]:
    """Rewrite an artifact output directory into the independent remote FS."""
    rewritten = list(argv)
    value = _find_value(argv, {"--output-dir", "-o"}).strip()
    local_default = _configured_local_artifact_dir(config_obj, local_root)
    if not value:
        return rewritten, local_default, _translate_repo_bound_path(str(local_default), local_root, remote_root)

    if value.startswith(("~/", "$HOME/")):
        local_output = local_default
        remote_output = value
    else:
        local_path = Path(value).expanduser()
        if not local_path.is_absolute():
            local_path = Path(host_cwd) / local_path
        local_output = local_path.resolve()
        mapped = _translate_repo_bound_path(str(local_output), local_root, remote_root)
        if mapped != str(local_output):
            remote_output = mapped
        elif not os.path.isabs(os.path.expanduser(value)):
            remote_output = _remote_join(remote_cwd, value)
        else:
            # An absolute output path outside the local repo has no remote
            # counterpart. Use session staging and pull it back to the requested
            # local destination rather than assuming that path exists remotely.
            remote_output = _remote_join(config.stage_dir, session, "outputs")

    index = 0
    while index < len(rewritten):
        token = rewritten[index]
        if token == "--output-dir" or token == "-o":
            if index + 1 < len(rewritten):
                rewritten[index + 1] = remote_output
            break
        if token.startswith("--output-dir="):
            rewritten[index] = "--output-dir=" + remote_output
            break
        index += 1
    return rewritten, local_output, remote_output


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
    remote_root = str(config.cwd or "").strip()
    if not remote_root:
        raise ValueError(
            "--ssh-mode commands requires --ssh-cwd (or [ssh].cwd) as the explicit remote repository root"
        )
    base_session = _sanitize_session(config.session or f"devtool-{local_root.name}-commands")
    wrapped: list[Any] = []

    for ordinal, spec in enumerate(specs, 1):
        metadata = dict(getattr(spec, "metadata", {}) or {})
        # Internal validation pseudo-specs are executed by local devtool logic,
        # not by a remote shell.
        if metadata.get("devtool_internal_validation"):
            wrapped.append(spec)
            continue
        original_argv = list(getattr(spec, "argv", []) or [])
        remote_base_argv = _drop_host_owned_toolchain_paths(original_argv, local_root)
        phase_name = _sanitize_session(str(getattr(spec, "name", "phase") or "phase"))
        session = _sanitize_session(f"{base_session}-{ordinal:02d}-{phase_name}")
        host_cwd = str(getattr(spec, "cwd", "") or local_root)
        remote_cwd = _translate_repo_bound_path(host_cwd, local_root, remote_root)
        if remote_cwd == host_cwd and Path(host_cwd).is_absolute():
            # A cwd outside the repository has no declared SSH mapping. Use the
            # explicit remote repo root rather than inventing a remote path.
            remote_cwd = remote_root
        remote_base_argv, local_artifact_dir, remote_artifact_dir = _rewrite_artifact_output_for_remote(
            remote_base_argv,
            config_obj=project_config,
            config=config,
            local_root=local_root,
            host_cwd=host_cwd,
            remote_cwd=remote_cwd,
            remote_root=remote_root,
            session=session,
        )
        translated_argv, staged = _stage_support_files_for_spec(
            config,
            remote_base_argv,
            local_root=local_root,
            remote_root=remote_root,
            session=session,
            dry_run=dry_run,
        )
        translated_env = _translate_env_for_repo(getattr(spec, "env", {}) or {}, local_root, remote_root)
        assignments = [f"{key}={value}" for key, value in sorted(translated_env.items())]
        env_prefix = "env " + " ".join(shlex.quote(item) for item in assignments) + " " if assignments else ""
        command = "cd " + _remote_shell_path(remote_cwd) + " && exec " + env_prefix + _shell_join_remote(translated_argv)
        remote_script = _build_remote_phase_script(config, session=session, command=command)
        ssh_command = _build_ssh_remote_sh_argv(config, remote_script, tty=True)

        local_script = shlex.join(ssh_command) + "; rc=$?"
        if bool(metadata.get("collect_artifacts") or metadata.get("verify_artifacts")) and config.copy_artifacts:
            # Pull before the outer runner performs its normal local artifact
            # discovery/verification.
            source = f"{config.destination}:{_remote_scp_path(remote_artifact_dir).rstrip('/')}/."
            scp = [*build_scp_base_argv(config), "-r", source, str(local_artifact_dir)]
            local_script += (
                "; if [ \"$rc\" -eq 0 ]; then mkdir -p " + shlex.quote(str(local_artifact_dir))
                + " && " + shlex.join(scp) + " || rc=$?; fi"
            )
        local_script += '; exit "$rc"'
        metadata["execution_mode"] = "ssh-command"
        metadata["ssh_mode"] = "commands"
        metadata["ssh_destination"] = config.destination
        metadata["ssh_port"] = config.port
        metadata["ssh_session"] = session
        metadata["ssh_cwd"] = remote_cwd
        metadata["ssh_staged_files"] = [str(local) for local, _remote in staged]
        metadata.setdefault("progress_argv", original_argv)
        metadata.setdefault("progress_cwd", host_cwd)
        wrapped.append(replace(spec, argv=["sh", "-lc", local_script], cwd=None, metadata=metadata))
    return wrapped

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
    completed = subprocess.run(command, check=False)
    return int(completed.returncode)
