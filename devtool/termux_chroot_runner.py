from __future__ import annotations

import contextlib
import datetime as dt
import errno
import io
import json
import os
import pty
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .clipboard import copy_text
from .apply_notifications import ApplyNotification, post_apply_notification
from .execution import build_chroot_exec_argv
from .execution_environments import (
    chroot_environment_assignments,
    default_termux_chroot_path_map,
    translate_guest_path,
    translate_host_path,
)
from .models import CommandSpec
from .xdg import cache_dir, state_dir

_PATH_VALUE_FLAGS = {
    "-r", "--repo", "-c", "--config", "--solution", "--project",
    "--machine-file",
    "-o", "--output",
}

_FLAGS_WITH_VALUES = _PATH_VALUE_FLAGS | {
    "--target", "--hud-mode",
    "--theme", "--stdout-payload", "--copy-via", "--commit", "--watch-interval",
    "--matrix", "--matrix-mode", "--format", "--repo-name", "--project-type",
    "--build-target", "--run-target", "--target-name", "--solution-target",
    "--delivery-mode", "--artifact-type", "--port", "--poll-interval",
    "--recent-events", "--from-ref", "--to-ref", "-m",
}

_APPLY_COMMANDS = {"apply-overlay", "ao", "apply-overlays", "aos", "apply-snapshot", "as"}
_BATCH_APPLY_PREFIXES = ("apply-overlay=", "ao=", "apply-overlays=", "aos=", "apply-snapshot=", "as=")
_PATH_SUFFIXES = (
    ".zip", ".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.zst", ".tzst", ".tar.bz2", ".tbz2", ".sln", ".csproj", ".fsproj",
    ".vbproj", ".py", ".toml", ".json", ".md", ".txt", ".log",
)

DESKTOP_HUD_MODES = {"auto", "desktop", "desktop-window", "gtk-window", "gnome", "gtk", "gtk3", "x11", "x11-gtk"}
ANDROID_GUI_ALIASES = {"android-gui", "android-overlay", "termux-gui", "termuxgui"}
ANDROID_BRIDGE_ALIASES = {"android-bridge", "termux-bridge", "android-chroot", "termux-chroot", "chroot-bridge", "android-notify", "notification"}
HOST_ONLY_COMMANDS = {
    "hud", "android-hud-bridge", "termux-hud-bridge", "logs", "completion",
    "ai-prompt", "ap", "bundle", "config", "init-config",
}
AUTO_CHROOT_COMMANDS = {
    "restore", "rs", "build", "b", "test", "t", "lint", "validate", "v", "wine-validate", "wv",
    "run", "rn", "package", "pkg", "clean", "cl", "inspect", "info", "apply-overlay", "ao", "apply-overlays", "aos",
    "apply-snapshot", "as", "batch", "commit", "ci", "tag", "status", "st",
    "doctor", "dr", "rollback", "history", "targets", "tg", "detect", "handoff",
}
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
OSC_RE = re.compile(r"\x1b\][^\a]*(?:\a|\x1b\\)")
TEST_FAILED_RE = re.compile(r"tests?:\s*\d+\s+passed,\s*([1-9]\d*)\s+failed", re.I)
TOTALS_ERRORS_RE = re.compile(r"errors\s*=\s*([1-9]\d*)", re.I)


@dataclass(slots=True)
class RunnerConfig:
    chroot_name: str = "androidos"
    tool: str = "devtool"
    chrootctl: str = "chrootctl"
    sudo: str = "sudo"
    translate_paths: bool = True
    host: bool = False
    print_command: bool = False
    cwd: str = ""
    copy_mode: str = ""
    copy_via: str = "auto"
    no_clipboard: bool = False
    path_map: list[tuple[str, str]] = field(default_factory=list)


def _split_path_map(raw: str) -> list[tuple[str, str]]:
    from .execution_environments import _split_path_map as shared_split
    return [item.as_tuple() for item in shared_split(raw)]


def default_path_map(env: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    return [item.as_tuple() for item in default_termux_chroot_path_map(env)]


def _translate_path(value: str, mappings: Iterable[tuple[str, str]], *, cwd: str | None = None) -> str:
    return translate_host_path(value, mappings, cwd=cwd)


def _translate_chroot_path_to_host(value: str, mappings: Iterable[tuple[str, str]]) -> str | None:
    return translate_guest_path(value, mappings)

def _strip_flag_value(argv: list[str], flag: str) -> tuple[list[str], str]:
    stripped: list[str] = []
    last_value = ""
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == flag:
            if index + 1 < len(argv):
                last_value = argv[index + 1]
                index += 2
            else:
                index += 1
            continue
        if token.startswith(flag + "="):
            last_value = token.split("=", 1)[1]
            index += 1
            continue
        stripped.append(token)
        index += 1
    return stripped, last_value


def _find_flag_value(argv: list[str], flags: set[str]) -> str:
    index = 0
    last_value = ""
    while index < len(argv):
        token = argv[index]
        matched = next((flag for flag in flags if token == flag or token.startswith(flag + "=")), "")
        if matched:
            if token == matched and index + 1 < len(argv):
                last_value = argv[index + 1]
                index += 2
                continue
            if token.startswith(matched + "="):
                last_value = token.split("=", 1)[1]
        index += 1
    return last_value


def _copy_label(copy_mode: str) -> str:
    return "Raw command output"


def _clipboard_text_from_machine_payload(payload: dict, copy_mode: str) -> str:
    clipboard = payload.get("clipboard", {}) if isinstance(payload, dict) else {}
    return str(clipboard.get("raw", "") or "")


def _looks_like_path(value: str) -> bool:
    if not value or value == "-":
        return False
    if value.startswith(("/", "~/", "./", "../")):
        return True
    if any(value.endswith(suffix) for suffix in _PATH_SUFFIXES):
        return True
    return "/" in value


def _consume_value(argv: list[str], index: int) -> tuple[str, str | None, int]:
    token = argv[index]
    if "=" in token and token.startswith("--"):
        flag, value = token.split("=", 1)
        return flag, value, index
    if token in _FLAGS_WITH_VALUES and index + 1 < len(argv):
        return token, argv[index + 1], index + 1
    return token, None, index


def _parse_runner_args(argv: list[str], env: Mapping[str, str]) -> tuple[RunnerConfig, list[str]]:
    config = RunnerConfig(
        chroot_name=env.get("DEVTOOL_TERMUX_CHROOT", "androidos").strip() or "androidos",
        tool=env.get("DEVTOOL_TERMUX_CHROOT_TOOL", "~/.local/bin/devtool").strip() or "~/.local/bin/devtool",
        chrootctl=env.get("DEVTOOL_CHROOTCTL", "chrootctl").strip() or "chrootctl",
        sudo=env.get("DEVTOOL_CHROOT_SUDO", "sudo"),
        path_map=default_path_map(env),
    )
    forwarded: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        def _take_value(default: str = "") -> str:
            nonlocal index
            if "=" in token:
                return token.split("=", 1)[1]
            if index + 1 < len(argv):
                index += 1
                return argv[index]
            return default
        if token == "--runner-chroot" or token.startswith("--runner-chroot="):
            config.chroot_name = _take_value(config.chroot_name).strip() or config.chroot_name
        elif token == "--runner-tool" or token.startswith("--runner-tool="):
            config.tool = _take_value(config.tool).strip() or config.tool
        elif token == "--runner-chrootctl" or token.startswith("--runner-chrootctl="):
            config.chrootctl = _take_value(config.chrootctl).strip() or config.chrootctl
        elif token == "--runner-sudo" or token.startswith("--runner-sudo="):
            config.sudo = _take_value(config.sudo)
        elif token == "--runner-map" or token.startswith("--runner-map="):
            config.path_map = _split_path_map(_take_value("")) + config.path_map
        elif token == "--runner-no-path-translate":
            config.translate_paths = False
        elif token == "--runner-print-command":
            config.print_command = True
        elif token == "--host":
            config.host = True
            forwarded.append(token)
        elif token == "--chroot" or token.startswith("--chroot="):
            config.chroot_name = _take_value(config.chroot_name).strip() or config.chroot_name
        elif token in {"-p", "--copy"}:
            config.copy_mode = "raw"
        elif token == "--copy-via" or token.startswith("--copy-via="):
            value = _take_value(config.copy_via).strip() or config.copy_via
            config.copy_via = value
            forwarded.append(token if "=" in token else "--copy-via")
            if "=" not in token:
                forwarded.append(value)
        elif token == "--no-clipboard":
            config.no_clipboard = True
            forwarded.append(token)
        else:
            forwarded.append(token)
        index += 1
    if config.no_clipboard and config.copy_mode:
        raise ValueError("--no-clipboard cannot be combined with --copy.")
    if config.copy_mode:
        forwarded, _ = _strip_flag_value(forwarded, "--copy-via")
    return config, forwarded

def translate_devtool_args(argv: list[str], *, env: Mapping[str, str] | None = None, cwd: str | None = None) -> tuple[RunnerConfig, list[str]]:
    env = env or os.environ
    cwd = cwd or os.getcwd()
    config, forwarded = _parse_runner_args(list(argv), env)
    explicit_repo = _find_flag_value(forwarded, {"-r", "--repo"})
    # When the whole devtool process is delegated into the chroot, start from
    # the requested repository rather than from Termux's current directory.
    # The repository can exist only in the chroot namespace (for example
    # /home/mike/Code/project), while the Termux host cwd cannot see it.
    start_directory = explicit_repo or cwd
    config.cwd = _translate_path(start_directory, config.path_map, cwd=cwd) if config.translate_paths else start_directory
    if not config.translate_paths:
        return config, forwarded
    translated: list[str] = []
    command = ""
    apply_artifact_seen = False
    index = 0
    while index < len(forwarded):
        token = forwarded[index]
        flag, value, value_index = _consume_value(forwarded, index)
        if value is not None and flag in _PATH_VALUE_FLAGS:
            translated_value = _translate_path(value, config.path_map, cwd=cwd)
            if "=" in token and token.startswith("--"):
                translated.append(f"{flag}={translated_value}")
            else:
                translated.append(token)
                translated.append(translated_value)
            index = value_index + 1
            continue
        if value is not None and value_index != index:
            translated.append(token)
            translated.append(value)
            index = value_index + 1
            continue
        if not command and not token.startswith("-"):
            command = token
            translated.append(token)
            index += 1
            continue
        if command in _APPLY_COMMANDS and not apply_artifact_seen and not token.startswith("-"):
            translated.append(_translate_path(token, config.path_map, cwd=cwd))
            apply_artifact_seen = True
        elif command == "batch" and any(token.startswith(prefix) for prefix in _BATCH_APPLY_PREFIXES):
            prefix, value = token.split("=", 1)
            translated.append(f"{prefix}={_translate_path(value, config.path_map, cwd=cwd)}")
        elif command == "init-config" and _looks_like_path(token):
            translated.append(_translate_path(token, config.path_map, cwd=cwd))
        elif _looks_like_path(token) and token.startswith(("/", "~/", "./", "../")):
            translated.append(_translate_path(token, config.path_map, cwd=cwd))
        else:
            translated.append(token)
        index += 1
    return config, translated


def _quote_shell_token(token: str) -> str:
    if token.startswith("~/") or token.startswith("$HOME/"):
        return token
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token):
        return token
    return shlex.quote(token)


def _shell_join_tool_argv(argv: list[str]) -> str:
    return " ".join(_quote_shell_token(part) for part in argv)


def build_inner_command(config: RunnerConfig, forwarded: list[str]) -> str:
    devtool_argv = [config.tool, *forwarded]
    env_prefix = [
        "env",
        "DEVTOOL_EXECUTION_CONTEXT=chroot",
        "DEVTOOL_INSIDE_TERMUX_CHROOT=1",
        f"DEVTOOL_CHROOT_CONTEXT={config.chroot_name}",
    ]
    run_id = os.environ.get("DEVTOOL_RUN_ID", "").strip()
    if run_id:
        env_prefix.append(f"DEVTOOL_RUN_ID={run_id}")
    return "cd " + shlex.quote(config.cwd or "/") + " && exec " + _shell_join_tool_argv([*env_prefix, *devtool_argv])


def build_runner_command(config: RunnerConfig, forwarded: list[str]) -> list[str]:
    old_chrootctl = os.environ.get("DEVTOOL_CHROOTCTL")
    old_sudo = os.environ.get("DEVTOOL_CHROOT_SUDO")
    try:
        os.environ["DEVTOOL_CHROOTCTL"] = config.chrootctl
        os.environ["DEVTOOL_CHROOT_SUDO"] = config.sudo
        return build_chroot_exec_argv(build_inner_command(config, forwarded), config.chroot_name)
    finally:
        if old_chrootctl is None:
            os.environ.pop("DEVTOOL_CHROOTCTL", None)
        else:
            os.environ["DEVTOOL_CHROOTCTL"] = old_chrootctl
        if old_sudo is None:
            os.environ.pop("DEVTOOL_CHROOT_SUDO", None)
        else:
            os.environ["DEVTOOL_CHROOT_SUDO"] = old_sudo





def _is_termux_host_env(env: Mapping[str, str] | None = None) -> bool:
    env = env or os.environ
    prefix = env.get("PREFIX", "")
    home = env.get("HOME", "")
    return bool(
        env.get("TERMUX_VERSION")
        or env.get("TERMUX_APP_PID")
        or prefix.startswith("/data/data/com.termux/")
        or "com.termux" in prefix
        or home.startswith("/data/data/com.termux/files/home")
    )


def _truthy_env(name: str, default: str = "0", *, env: Mapping[str, str] | None = None) -> bool:
    env = env or os.environ
    return env.get(name, default).strip().lower() not in {"", "0", "false", "no", "off"}


def _falsey_env(name: str, default: str = "1", *, env: Mapping[str, str] | None = None) -> bool:
    env = env or os.environ
    return env.get(name, default).strip().lower() in {"", "0", "false", "no", "off"}


def chrootctl_available(env: Mapping[str, str] | None = None) -> bool:
    env = env or os.environ
    chrootctl = env.get("DEVTOOL_CHROOTCTL", "chrootctl").strip() or "chrootctl"
    if os.path.sep in chrootctl:
        return Path(chrootctl).exists()
    return shutil.which(chrootctl) is not None


def repo_path_from_argv(argv: list[str], *, cwd: str | None = None) -> str:
    """Return the repository path requested by the command, if any."""
    value = _find_flag_value(list(argv), {"-r", "--repo"}).strip()
    if not value:
        return ""
    expanded = os.path.expanduser(value)
    if not os.path.isabs(expanded):
        expanded = os.path.abspath(os.path.join(cwd or os.getcwd(), expanded))
    return expanded


def chroot_directory_exists(path: str, chroot_name: str, *, env: Mapping[str, str] | None = None) -> bool:
    """Check a directory in the chroot namespace without relying on host visibility."""
    if not path or not chroot_name.strip():
        return False
    env = env or os.environ
    old_chrootctl = os.environ.get("DEVTOOL_CHROOTCTL")
    old_sudo = os.environ.get("DEVTOOL_CHROOT_SUDO")
    try:
        if "DEVTOOL_CHROOTCTL" in env:
            os.environ["DEVTOOL_CHROOTCTL"] = str(env["DEVTOOL_CHROOTCTL"])
        if "DEVTOOL_CHROOT_SUDO" in env:
            os.environ["DEVTOOL_CHROOT_SUDO"] = str(env["DEVTOOL_CHROOT_SUDO"])
        command = "test -d " + shlex.quote(path)
        completed = subprocess.run(
            build_chroot_exec_argv(command, chroot_name),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
        )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        if old_chrootctl is None:
            os.environ.pop("DEVTOOL_CHROOTCTL", None)
        else:
            os.environ["DEVTOOL_CHROOTCTL"] = old_chrootctl
        if old_sudo is None:
            os.environ.pop("DEVTOOL_CHROOT_SUDO", None)
        else:
            os.environ["DEVTOOL_CHROOT_SUDO"] = old_sudo


def discover_chroot_home_for_termux_path(
    host_path: str,
    chroot_name: str,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Find the chroot home containing the same path relative to Termux HOME."""
    env = env or os.environ
    termux_home = os.path.expanduser(str(env.get("HOME", "") or "")).rstrip("/")
    expanded = os.path.abspath(os.path.expanduser(host_path))
    if not termux_home or not (expanded == termux_home or expanded.startswith(termux_home + "/")):
        return ""
    suffix = expanded[len(termux_home):].lstrip("/")
    candidate_expr = '"$base"' if not suffix else '"$base"/' + shlex.quote(suffix)
    command = (
        'for base in /home/* /root; do '
        '[ -d "$base" ] || continue; '
        f'candidate={candidate_expr}; '
        '[ -d "$candidate" ] || continue; '
        "printf '%s\\n' \"$base\"; "
        'done'
    )
    try:
        completed = subprocess.run(
            build_chroot_exec_argv(command, chroot_name),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=8,
            check=False,
            env=dict(os.environ),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    candidates = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return candidates[0] if candidates else ""


def repair_termux_home_mapping(
    mappings: Iterable[tuple[str, str]],
    *,
    host_path: str,
    chroot_name: str,
    env: Mapping[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Repair a guessed /home/termux mapping when the repo lives elsewhere."""
    env = env or os.environ
    current = list(mappings)
    translated = _translate_path(host_path, current, cwd=os.getcwd())
    if chroot_directory_exists(translated, chroot_name, env=env):
        return current
    discovered_home = discover_chroot_home_for_termux_path(host_path, chroot_name, env=env)
    termux_home = os.path.expanduser(str(env.get("HOME", "") or "")).rstrip("/")
    if not discovered_home or not termux_home:
        return current
    repaired = [(host, target) for host, target in current if host.rstrip("/") != termux_home]
    repaired.insert(0, (termux_home, discovered_home.rstrip("/")))
    return repaired


def should_delegate_full_for_chroot_only_repo(
    argv: list[str],
    *,
    chroot_name: str,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> bool:
    """Return True when the repo is invisible to Termux but exists in the chroot."""
    env = env or os.environ
    host_repo = repo_path_from_argv(argv, cwd=cwd)
    if not host_repo or Path(host_repo).is_dir():
        return False
    try:
        config, forwarded = translate_devtool_args(argv, env=env, cwd=cwd or os.getcwd())
    except ValueError:
        return False
    chroot_repo = _find_flag_value(forwarded, {"-r", "--repo"}).strip() or config.cwd
    return chroot_directory_exists(chroot_repo, chroot_name, env=env)


def should_auto_chroot(argv: list[str], *, command: str | None = None, env: Mapping[str, str] | None = None) -> bool:
    env = env or os.environ
    if _falsey_env("DEVTOOL_AUTO_CHROOT", "1", env=env):
        return False
    if env.get("DEVTOOL_AUTO_CHROOT_ACTIVE") == "1" or env.get("DEVTOOL_INSIDE_TERMUX_CHROOT") == "1":
        return False
    if not _is_termux_host_env(env):
        return False
    if "--no-auto-chroot" in argv or "--host" in argv:
        return False
    if "--chroot" in argv or any(token.startswith("--chroot=") for token in argv):
        return False
    cmd = (command or "").strip()
    if not cmd:
        for token in argv:
            if token == "--":
                break
            if token.startswith("--") and "=" in token:
                continue
            if token.startswith("-"):
                continue
            cmd = token
            break
    if cmd in HOST_ONLY_COMMANDS:
        return False
    if AUTO_CHROOT_COMMANDS and cmd and cmd not in AUTO_CHROOT_COMMANDS:
        return False
    return chrootctl_available(env)


def normalize_hud_mode_for_chroot(mode: str | None) -> str:
    if not mode:
        return "android-bridge"
    normalized = mode.strip().lower()
    if normalized in DESKTOP_HUD_MODES or normalized in ANDROID_GUI_ALIASES or normalized in ANDROID_BRIDGE_ALIASES:
        return "android-bridge"
    return mode


def normalize_hud_mode_for_termux_host(mode: str | None) -> str:
    """Map desktop/chroot HUD names to the Devtool Android app bridge."""
    if not mode:
        return "android-bridge"
    normalized = mode.strip().lower()
    if normalized in DESKTOP_HUD_MODES or normalized in ANDROID_GUI_ALIASES or normalized in ANDROID_BRIDGE_ALIASES:
        return "android-bridge"
    return mode


_CHROOT_PATH_LIST_ENV_KEYS = {
    "PATH",
    "LD_LIBRARY_PATH",
    "LIBRARY_PATH",
    "CPATH",
    "C_INCLUDE_PATH",
    "CPLUS_INCLUDE_PATH",
    "PKG_CONFIG_PATH",
    "PYTHONPATH",
}


def _translate_env_value_for_chroot(
    key: str,
    value: str,
    *,
    mappings: Iterable[tuple[str, str]],
    cwd: str | None = None,
) -> str:
    """Translate host paths embedded in an environment value for chroot use.

    Build policies are resolved before the execution backend is selected. On a
    Termux host this means cache variables can initially contain
    ``/data/data/com.termux/...`` even though the command will run inside the
    chroot. Command argv paths were already translated; environment paths need
    the same treatment.
    """

    text = str(value)
    if not text:
        return text

    if key in _CHROOT_PATH_LIST_ENV_KEYS:
        parts: list[str] = []
        for part in text.split(os.pathsep):
            if part.startswith(("/", "~/")):
                parts.append(_translate_path(part, mappings, cwd=cwd))
            else:
                parts.append(part)
        return os.pathsep.join(parts)

    if text.startswith(("/", "~/")):
        translated = _translate_path(text, mappings, cwd=cwd)
        if key == "DEVTOOL_BUILD_CACHE_ROOT":
            translated = translated.replace(
                "/devtool/build/native-termux/",
                "/devtool/build/chroot/",
            )
        return translated

    return text


def translate_environment_for_chroot(
    values: Mapping[str, str],
    *,
    mappings: Iterable[tuple[str, str]],
    cwd: str | None = None,
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Return chroot-safe environment values and names that changed."""

    translated: dict[str, str] = {}
    changed: list[str] = []
    for raw_key, raw_value in values.items():
        key = str(raw_key)
        value = str(raw_value)
        updated = _translate_env_value_for_chroot(
            key,
            value,
            mappings=mappings,
            cwd=cwd,
        )
        if key == "DEVTOOL_BUILD_ENVIRONMENT" and updated == "native-termux":
            updated = "chroot"
        translated[key] = updated
        if updated != value:
            changed.append(key)
    return translated, tuple(sorted(changed))


def translate_argv_paths_for_chroot(argv: list[str], *, mappings: Iterable[tuple[str, str]], cwd: str | None = None) -> list[str]:
    """Translate absolute host paths inside a command argv to chroot paths."""
    translated: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        flag, value, value_index = _consume_value(argv, index)
        if value is not None and flag in _PATH_VALUE_FLAGS:
            translated_value = _translate_path(value, mappings, cwd=cwd)
            if "=" in token and token.startswith("--"):
                translated.append(f"{flag}={translated_value}")
            else:
                translated.append(token)
                translated.append(translated_value)
            index = value_index + 1
            continue
        if value is not None and value_index != index:
            translated.append(token)
            translated.append(value)
            index = value_index + 1
            continue
        if _looks_like_path(token) and token.startswith(("/", "~/")):
            translated.append(_translate_path(token, mappings, cwd=cwd))
        else:
            translated.append(token)
        index += 1
    return translated


def wrap_specs_for_termux_host_chroot(
    specs: list[CommandSpec],
    chroot_name: str,
    *,
    repo_root: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    path_map: Iterable[tuple[str, str]] | None = None,
) -> list[CommandSpec]:
    """Wrap command phases for chroot while keeping devtool itself on Termux.

    This is the toolchain/backend mode: the outer devtool keeps Rich UI,
    native Android app HUD bridge, clipboard, artifact matching, and command summaries on the
    Android host.  Only each external command phase is run through:

        chrootctl exec -- zsh -lc 'cd <translated cwd> && <command>'

    It is intentionally separate from build_inner_command/run_auto_chroot,
    which still delegates the whole devtool process for fallback/legacy cases.
    """
    env = env or os.environ
    mappings = list(path_map or default_path_map(env))
    host_repo = Path(repo_root).expanduser() if repo_root is not None else None
    wrapped: list[CommandSpec] = []
    for spec in specs:
        host_cwd = str(spec.cwd or host_repo or os.getcwd())
        chroot_cwd = _translate_path(host_cwd, mappings, cwd=os.getcwd())
        argv = translate_argv_paths_for_chroot(list(spec.argv), mappings=mappings, cwd=host_cwd)
        assignment_values = list(chroot_environment_assignments(chroot_name))
        translated_env, translated_env_keys = translate_environment_for_chroot(
            spec.env,
            mappings=mappings,
            cwd=host_cwd,
        )
        for key, value in sorted(translated_env.items()):
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(key)):
                assignment_values.append(f"{key}={value}")
        assignments = " ".join(shlex.quote(item) for item in assignment_values)
        command = "cd " + shlex.quote(chroot_cwd or "/") + " && exec env " + assignments + " " + shlex.join(argv)
        metadata = dict(spec.metadata)
        metadata["execution_mode"] = "termux-host-chroot"
        metadata["chroot"] = chroot_name
        metadata["chroot_runner"] = "termux-host-toolchain"
        metadata["chroot_cwd"] = chroot_cwd
        if translated_env_keys:
            metadata["chroot_translated_env"] = list(translated_env_keys)
        # Execute the translated command in the chroot, but plan/render dotnet
        # progress from the original host-side command. This lets the parser
        # inspect the real solution/project path and estimate project totals.
        metadata.setdefault("progress_argv", list(spec.argv))
        metadata.setdefault("progress_cwd", host_cwd)
        wrapped.append(replace(spec, argv=build_chroot_exec_argv(command, chroot_name), cwd=None, metadata=metadata))
    return wrapped


def _strip_ansi(text: str) -> str:
    text = ANSI_RE.sub("", text)
    text = OSC_RE.sub("", text)
    return re.sub(r"\r$", "", text, flags=re.MULTILINE)


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _append_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(data)
        handle.flush()


def _append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(text)
        handle.flush()


def _width(default: int = 78) -> int:
    try:
        return max(53, shutil.get_terminal_size((default, 24)).columns)
    except Exception:
        return default


def _rule(label: str = "", ch: str = "─") -> str:
    width = _width()
    if not label:
        return ch * width
    text = f" {label} "
    left = max(0, (width - len(text)) // 2)
    right = max(0, width - len(text) - left)
    return ch * left + text + ch * right


def _echo_log(path: Path, text: str = "") -> None:
    print(text, flush=True)
    _append_text(path, text + "\n")


def _render_to_log(renderable: Any) -> str:
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, width=_width())
    console.print(renderable)
    return _strip_ansi(stream.getvalue()).rstrip()


def _print_rich(renderable: Any, raw_logfile: Path) -> None:
    Console().print(renderable)
    rendered = _render_to_log(renderable)
    if rendered:
        _append_text(raw_logfile, rendered + "\n")


def _compact_command(argv: list[str]) -> str:
    # Human-facing summary: avoid dumping the full nested shell unless the user asks.
    if not argv:
        return ""
    try:
        if "zsh" in argv and "-lc" in argv:
            idx = argv.index("-lc")
            return argv[idx + 1] if idx + 1 < len(argv) else shlex.join(argv)
    except Exception:
        pass
    return shlex.join(argv)


def _make_start_panel(config: RunnerConfig, meta: Mapping[str, str], raw_logfile: Path, plain_logfile: Path, cmd: list[str], *, env: Mapping[str, str]) -> Panel:
    mode = meta.get("hud_mode") or ("disabled" if meta.get("hud_requested") != "1" else "android-bridge")
    if meta.get("hud_requested") != "1":
        mode = "disabled"
    elif meta.get("hud_mode_adapted") == "1":
        mode = f"{mode} [dim](adapted from desktop)[/dim]"
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="white")
    table.add_row("Chroot", config.chroot_name)
    table.add_row("Repo", config.cwd or "/")
    table.add_row("Tool", config.tool)
    table.add_row("HUD", mode)
    table.add_row("Logs", f"{plain_logfile}")
    if _truthy_env("DEVTOOL_TERMUX_CHROOT_VERBOSE", "0", env=env) or config.print_command:
        table.add_row("Command", _compact_command(cmd))
    title = Text("Android chroot", style="bold green")
    subtitle = Text(f"started { _now() }", style="dim")
    return Panel(table, title=title, subtitle=subtitle, border_style="green")


def _make_end_panel(rc: int, analysis: Mapping[str, int | str], raw_logfile: Path, plain_logfile: Path, *, copied: bool | None = None) -> Panel:
    has_parsed_failure = bool(analysis.get("errors", 0) or analysis.get("failed_tests", 0))
    ok = rc == 0 and not has_parsed_failure
    status = "Complete" if ok else "Failed"
    style = "green" if ok else "red"
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="white")
    table.add_row("Status", f"[{style}]{status}[/{style}]")
    table.add_row("Exit code", str(rc))
    if analysis.get("reason"):
        table.add_row("Reason", str(analysis["reason"]))
    if copied is not None:
        table.add_row("Clipboard", "copied" if copied else "skipped")
    table.add_row("Log", str(plain_logfile))
    title = Text("Android chroot", style=f"bold {style}")
    subtitle = Text(f"ended { _now() }", style="dim")
    return Panel(table, title=title, subtitle=subtitle, border_style=style)


def _make_live_rule(raw_logfile: Path) -> None:
    rule_text = Text("live chroot output", style="bold cyan")
    _print_rich(rule_text, raw_logfile)


def _plain_log_path_for(raw_logfile: Path) -> Path:
    name = raw_logfile.name
    if name.endswith(".ansi.log"):
        return raw_logfile.with_name(name[:-len(".ansi.log")] + ".log")
    return raw_logfile.with_suffix(".log")


def _adapt_forwarded_for_chroot(forwarded: list[str]) -> tuple[list[str], dict[str, str]]:
    args = list(forwarded)
    out: list[str] = []
    meta = {"hud_requested": "0", "hud_disabled": "0", "hud_mode": "", "hud_mode_adapted": "0", "needs_android_bridge": "0"}
    has_copy_via = False
    has_no_clipboard = "--no-clipboard" in args
    has_auto_hud = False
    has_no_hud = False
    has_hud_mode = False
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--auto-hud":
            has_auto_hud = True
            meta["hud_requested"] = "1"
            out.append(token)
        elif token == "--no-hud":
            has_no_hud = True
            meta["hud_disabled"] = "1"
            out.append(token)
        elif token == "--hud-mode":
            has_hud_mode = True
            meta["hud_requested"] = "1"
            out.append(token)
            i += 1
            original = args[i] if i < len(args) else ""
            adapted = normalize_hud_mode_for_chroot(original)
            meta["hud_mode"] = adapted
            if adapted == "android-bridge":
                meta["needs_android_bridge"] = "1"
            if adapted != original:
                meta["hud_mode_adapted"] = "1"
            out.append(adapted)
        elif token.startswith("--hud-mode="):
            has_hud_mode = True
            meta["hud_requested"] = "1"
            original = token.split("=", 1)[1]
            adapted = normalize_hud_mode_for_chroot(original)
            meta["hud_mode"] = adapted
            if adapted == "android-bridge":
                meta["needs_android_bridge"] = "1"
            if adapted != original:
                meta["hud_mode_adapted"] = "1"
            out.append(f"--hud-mode={adapted}")
        elif token == "--copy-via":
            has_copy_via = True
            out.append(token)
            i += 1
            if i < len(args):
                out.append(args[i])
        elif token.startswith("--copy-via="):
            has_copy_via = True
            out.append(token)
        else:
            out.append(token)
        i += 1
    if not has_copy_via:
        out = _insert_global_flags_before_command(out, ["--copy-via", "termux"])
    if not has_no_clipboard:
        out = _insert_global_flags_before_command(out, ["--no-clipboard"])
    if has_no_hud:
        meta["hud_requested"] = "0"
    elif meta["hud_requested"] == "1" or has_auto_hud:
        if not has_auto_hud:
            out = _insert_global_flags_before_command(out, ["--auto-hud"])
        if not has_hud_mode:
            out = _insert_global_flags_before_command(out, ["--hud-mode", "android-bridge"])
            meta["hud_mode"] = "android-bridge"
            meta["needs_android_bridge"] = "1"
    else:
        out = _insert_global_flags_before_command(out, ["--no-hud"])
    return out, meta


def _apply_command_from_argv(argv: list[str]) -> tuple[str, str]:
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            break
        if token.startswith("--") and "=" in token:
            index += 1
            continue
        if token in _FLAGS_WITH_VALUES:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        command = token
        if command == "apply" and index + 1 < len(argv):
            subcommand = argv[index + 1]
            mapped = {
                "overlay": "apply-overlay",
                "overlays": "apply-overlays",
                "snapshot": "apply-snapshot",
            }.get(subcommand, "")
            if mapped:
                artifact = argv[index + 2] if index + 2 < len(argv) else ""
                return mapped, artifact
        if command in _APPLY_COMMANDS:
            artifact = argv[index + 1] if index + 1 < len(argv) else ""
            return command, artifact
        return "", ""
    return "", ""


def _apply_notification_enabled_for_argv(argv: list[str], env: Mapping[str, str]) -> bool:
    if env.get("DEVTOOL_APPLY_NOTIFICATION", "1").strip().lower() in {"0", "false", "no", "off"}:
        return False
    return "--no-apply-notification" not in argv


def _post_chroot_apply_notification(argv: list[str], rc: int, plain: str, env: Mapping[str, str]) -> None:
    if not _apply_notification_enabled_for_argv(argv, env):
        return
    command, artifact = _apply_command_from_argv(argv)
    if not command:
        return
    label = "snapshot" if command in {"apply-snapshot", "as"} else ("overlay bundle" if command in {"apply-overlays", "aos"} else "overlay")
    artifact_name = Path(artifact).name if artifact else label
    lower = plain.lower()
    rolled_back = "rolled back" in lower or "rollback" in lower and rc != 0
    validation_paused = "validation paused" in lower or "validation session paused" in lower
    if rolled_back:
        title = f"Devtool {label} rolled back"
        status = "Rolled back"
    elif validation_paused:
        title = f"Devtool {label} validation paused"
        status = "Validation paused"
    elif rc == 0:
        title = f"Devtool {label} applied"
        status = "Applied"
    else:
        title = f"Devtool {label} failed"
        status = "Failed"
    body = f"{artifact_name} · exit {rc}"
    post_apply_notification(ApplyNotification(title=title, body=body, toast=f"{status}: {artifact_name}"))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _start_android_hud_bridge(raw_logfile: Path, state_dir: Path) -> None:
    if _falsey_env("DEVTOOL_ANDROID_HUD_AUTOSTART", "1"):
        _echo_log(raw_logfile, "HUD bridge: autostart disabled")
        return
    state_dir.mkdir(parents=True, exist_ok=True)
    pidfile = state_dir / "devtool-android-hud-bridge.pid"
    logfile = state_dir / "devtool-android-hud-bridge.log"
    if pidfile.exists():
        try:
            pid = int(pidfile.read_text(encoding="utf-8", errors="replace").strip())
            if pid > 0 and _pid_alive(pid):
                _echo_log(raw_logfile, f"HUD bridge: already running (pid {pid})")
                _echo_log(raw_logfile, f"HUD bridge log: {logfile}")
                return
        except Exception:
            pass
    argv = [sys.executable, "-m", "devtool", "android-hud-bridge", "--state-dir", str(state_dir)]
    env = os.environ.copy()
    env["DEVTOOL_AUTO_CHROOT"] = "0"
    env["DEVTOOL_AUTO_CHROOT_ACTIVE"] = "0"
    try:
        handle = logfile.open("ab")
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True, env=env)
        pidfile.write_text(str(proc.pid), encoding="utf-8")
        _echo_log(raw_logfile, f"HUD bridge: started Android app forwarder (pid {proc.pid})")
        _echo_log(raw_logfile, f"HUD bridge log: {logfile}")
    except Exception as exc:
        _echo_log(raw_logfile, f"HUD bridge: could not start ({exc})")


def _run_live_pty(argv: list[str], raw_logfile: Path, env: dict[str, str]) -> int:
    master_fd = None
    slave_fd = None
    proc: subprocess.Popen[bytes] | None = None
    try:
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(argv, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, env=env, close_fds=True, start_new_session=True)
        os.close(slave_fd)
        slave_fd = None
        while True:
            if proc.poll() is not None:
                while True:
                    try:
                        ready, _, _ = select.select([master_fd], [], [], 0)
                        if not ready:
                            break
                        data = os.read(master_fd, 8192)
                        if not data:
                            break
                        sys.stdout.buffer.write(data)
                        sys.stdout.buffer.flush()
                        _append_bytes(raw_logfile, data)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            break
                        raise
                return int(proc.returncode or 0)
            try:
                ready, _, _ = select.select([master_fd], [], [], 0.2)
                if ready:
                    data = os.read(master_fd, 8192)
                    if data:
                        sys.stdout.buffer.write(data)
                        sys.stdout.buffer.flush()
                        _append_bytes(raw_logfile, data)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    proc.wait(timeout=2)
                    return int(proc.returncode or 0)
                raise
    except KeyboardInterrupt:
        if proc and proc.poll() is None:
            with contextlib.suppress(Exception):
                os.killpg(proc.pid, signal.SIGINT)
            try:
                return int(proc.wait(timeout=8) or 130)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(Exception):
                    os.killpg(proc.pid, signal.SIGKILL)
                return 130
        return 130
    finally:
        for fd in (slave_fd, master_fd):
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)


def _analyze_plain_log(text: str) -> dict[str, int | str]:
    errors = 0
    failed_tests = 0
    reason = ""
    for match in TOTALS_ERRORS_RE.finditer(text):
        value = int(match.group(1))
        errors = max(errors, value)
    for match in TEST_FAILED_RE.finditer(text):
        value = int(match.group(1))
        failed_tests = max(failed_tests, value)
    if errors:
        reason = f"parsed errors={errors}"
    if failed_tests:
        reason = (reason + "; " if reason else "") + f"parsed failed tests={failed_tests}"
    return {"errors": errors, "failed_tests": failed_tests, "reason": reason}


def run_auto_chroot(argv: list[str], *, env: Mapping[str, str] | None = None, cwd: str | None = None) -> int:
    env = dict(env or os.environ)
    cwd = cwd or os.getcwd()
    try:
        config, forwarded = translate_devtool_args(argv, env=env, cwd=cwd)
    except ValueError as exc:
        print(f"devtool android chroot: {exc}", file=sys.stderr)
        return 2
    adapted_forwarded, meta = _adapt_forwarded_for_chroot(forwarded)
    forwarded, machine_path, cleanup_machine_path = prepare_host_clipboard_run(config, adapted_forwarded)
    cmd = build_runner_command(config, forwarded)

    log_root = Path(env.get("DEVTOOL_TERMUX_CHROOT_LOG_DIR", str(cache_dir() / "termux-chroot-runner"))).expanduser()
    log_root.mkdir(parents=True, exist_ok=True)
    raw_logfile = log_root / f"devtool-chroot-{_stamp()}.ansi.log"
    plain_logfile = _plain_log_path_for(raw_logfile)

    _print_rich(_make_start_panel(config, meta, raw_logfile, plain_logfile, cmd, env=env), raw_logfile)

    child_env = env.copy()
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["DEVTOOL_AUTO_CHROOT_ACTIVE"] = "1"
    child_env["DEVTOOL_AUTO_CHROOT"] = "0"
    child_env["DEVTOOL_CHROOT_CONTEXT"] = config.chroot_name
    if _apply_command_from_argv(forwarded)[0] and _apply_notification_enabled_for_argv(forwarded, env):
        child_env["DEVTOOL_APPLY_NOTIFICATION"] = "0"
    if meta.get("needs_android_bridge") == "1" and meta.get("hud_disabled") != "1":
        bridge_dir = child_env.get("DEVTOOL_HUD_BRIDGE_DIR", "/home/termux/.local/state/devtool-android-hud")
        child_env["DEVTOOL_HUD_BRIDGE_DIR"] = bridge_dir
        child_env.setdefault("DEVTOOL_HUD_SINGLETON", "kill")
        host_bridge_dir = Path(env.get("DEVTOOL_ANDROID_HUD_STATE_DIR", str(state_dir().parent / "devtool-android-hud"))).expanduser()
        _start_android_hud_bridge(raw_logfile, host_bridge_dir)

    _make_live_rule(raw_logfile)
    rc = _run_live_pty(cmd, raw_logfile, child_env)
    try:
        plain = _strip_ansi(raw_logfile.read_text(encoding="utf-8", errors="replace"))
        plain_logfile.write_text(plain, encoding="utf-8")
    except Exception:
        plain = ""
    analysis = _analyze_plain_log(plain)
    if rc == 0 and _truthy_env("DEVTOOL_FAIL_ON_PARSED_ERRORS", "1") and (analysis.get("errors", 0) or analysis.get("failed_tests", 0)):
        rc = 1
    _post_chroot_apply_notification(forwarded, rc, plain, env)
    copied: bool | None = None
    if config.copy_mode:
        # Failed runs often do not produce the machine summary. Avoid noisy JSON
        # tracebacks; the failure reason and logs are already shown below.
        copied = copy_from_machine_file(machine_path, config.copy_mode, config.copy_via, quiet_missing=(rc != 0))
    _print_rich(_make_end_panel(rc, analysis, raw_logfile, plain_logfile, copied=copied), raw_logfile)
    if cleanup_machine_path and machine_path is not None:
        with contextlib.suppress(OSError):
            machine_path.unlink()
    return int(rc)

def _insert_global_flags_before_command(argv: list[str], flags: list[str]) -> list[str]:
    """Insert devtool global flags before the subcommand.

    argparse only accepts --machine-file / --no-clipboard before commands like
    apply-overlay, so host-copy runner flags must not be appended at the end.
    """
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            break
        if token.startswith("--") and "=" in token:
            index += 1
            continue
        if token in _FLAGS_WITH_VALUES:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    return [*argv[:index], *flags, *argv[index:]]

def prepare_host_clipboard_run(config: RunnerConfig, forwarded: list[str]) -> tuple[list[str], Path | None, bool]:
    """Augment forwarded args so host runner can copy after chroot execution.

    The chrooted devtool writes its machine envelope to a host-readable file.
    The Termux-side runner reads that file and performs the clipboard write on
    the host, which avoids relying on clipboard binaries/sockets inside chroot.
    """
    if not config.copy_mode:
        return forwarded, None, False

    machine_path = _find_flag_value(forwarded, {"--machine-file"})
    cleanup = False
    host_machine_path: Path | None = None
    chroot_machine_path = ""
    if machine_path:
        chroot_machine_path = machine_path
        translated_back = _translate_chroot_path_to_host(machine_path, config.path_map) if config.translate_paths else machine_path
        host_machine_path = Path(translated_back).expanduser() if translated_back else None
    else:
        host_root = Path(os.environ.get("DEVTOOL_TERMUX_CHROOT_RUNNER_STATE", str(cache_dir() / "termux-chroot-runner"))).expanduser()
        host_root.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(prefix="machine-", suffix=".json", dir=host_root, delete=False)
        handle.close()
        host_machine_path = Path(handle.name)
        chroot_machine_path = _translate_path(str(host_machine_path), config.path_map, cwd=os.getcwd()) if config.translate_paths else str(host_machine_path)
        cleanup = True
        forwarded = _insert_global_flags_before_command(forwarded, ["--machine-file", chroot_machine_path])

    if "--no-clipboard" not in forwarded:
        forwarded = _insert_global_flags_before_command(forwarded, ["--no-clipboard"])
    return forwarded, host_machine_path, cleanup


def copy_from_machine_file(path: Path | None, copy_mode: str, copy_via: str = "auto", *, quiet_missing: bool = False) -> bool:
    if path is None:
        if not quiet_missing:
            print("Clipboard: skipped because the machine summary path is not host-readable.", file=sys.stderr)
        return False
    try:
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            raise ValueError("machine summary was empty")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        if not quiet_missing:
            print(f"Clipboard: skipped because devtool did not write a readable machine summary ({exc}).", file=sys.stderr)
        return False
    text = _clipboard_text_from_machine_payload(payload, copy_mode)
    result = copy_text(text) if copy_via == "auto" else copy_text(text, preferred=copy_via)
    label = _copy_label(copy_mode)
    if result.success:
        print(f"{label} copied to clipboard via {result.backend}.")
        return True
    print(result.reason or "Clipboard copy failed.", file=sys.stderr)
    return False


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        config, forwarded = translate_devtool_args(argv)
    except ValueError as exc:
        print(f"devtool-termux-chroot: {exc}", file=sys.stderr)
        return 2
    if config.host:
        cmd = [config.tool, *forwarded]
        if config.print_command:
            print(shlex.join(cmd))
            return 0
        return subprocess.run(cmd).returncode

    machine_path: Path | None = None
    cleanup_machine_path = False
    forwarded, machine_path, cleanup_machine_path = prepare_host_clipboard_run(config, forwarded)
    cmd = build_runner_command(config, forwarded)
    if config.print_command:
        print(shlex.join(cmd))
        return 0
    try:
        completed = subprocess.run(cmd)
        if config.copy_mode:
            copy_from_machine_file(machine_path, config.copy_mode, config.copy_via)
        return completed.returncode
    finally:
        if cleanup_machine_path and machine_path is not None:
            try:
                machine_path.unlink()
            except OSError:
                pass

if __name__ == "__main__":
    raise SystemExit(main())
