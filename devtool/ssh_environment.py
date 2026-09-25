from __future__ import annotations

"""Remote SSH environment inventory, provenance, and redaction.

SSH execution treats the login worker as the owner of host paths.  This module
therefore discovers environment/toolchains *on the worker* instead of projecting
absolute paths from the controller.  The payload is suitable for ``devtool ssh
env --format json`` and is also consumed by SSH-01 doctor/prepare evidence.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
from typing import Any, Mapping

from .ssh_multiplex import evidence as multiplex_evidence, run_with_multiplex_fallback
from .ssh_tmux_runner import SSHRunnerConfig, build_ssh_remote_sh_argv

_SECRET_RE = re.compile(r"(?:TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE|CREDENTIAL|API[_-]?KEY|AUTH|COOKIE)", re.I)


def _transport_timeout() -> float:
    try:
        return max(1.0, min(float(os.environ.get("DEVTOOL_SSH_TRANSPORT_TIMEOUT", "30") or 30), 3600.0))
    except ValueError:
        return 30.0


def _probe_script() -> str:
    # Deliberately POSIX-sh compatible. Optional tools are bounded and failures
    # are converted to empty values so environment inspection remains read-only.
    return r'''set +e
kv(){ printf '%s=%s\n' "$1" "$2"; }
platform=linux
case "${PREFIX:-}" in */com.termux/*|*/termux/*) platform=termux;; esac
[ -n "${TERMUX_VERSION:-}" ] && platform=termux
[ -d /data/data/com.termux/files/usr ] && platform=termux
prefix="${PREFIX:-}"
[ "$platform" = termux ] && [ -z "$prefix" ] && [ -d /data/data/com.termux/files/usr ] && prefix=/data/data/com.termux/files/usr
findcmd(){ command -v "$1" 2>/dev/null || { [ -n "$prefix" ] && [ -x "$prefix/bin/$1" ] && printf '%s/bin/%s\n' "$prefix" "$1"; }; }
firstdir(){ for p in "$@"; do [ -n "$p" ] && [ -d "$p" ] && { printf '%s\n' "$p"; return; }; done; }
sizek(){ [ -d "$1" ] && du -sk "$1" 2>/dev/null | awk '{print $1}' || printf 0; }
listdirs(){ [ -d "$1" ] && find "$1" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort | paste -sd, -; }
kv platform "$platform"
kv os_name "$(uname -s 2>/dev/null)"
kv architecture "$(uname -m 2>/dev/null)"
kv home "${HOME:-}"
kv prefix "$prefix"
kv path "${PATH:-}"
kv tmpdir "${TMPDIR:-}"
kv shell "${SHELL:-}"
for name in python3 python java javac gradle rsync git tmux tar zstd node npm go cmake ninja aapt2 aidl; do kv "tool_$name" "$(findcmd "$name" | head -n1)"; done
python="$(findcmd python3 | head -n1)"; [ -n "$python" ] || python="$(findcmd python | head -n1)"
kv tool_python "$python"
kv version_python "$([ -n "$python" ] && "$python" --version 2>&1 | head -n1)"
java="$(findcmd java | head -n1)"; gradle="$(findcmd gradle | head -n1)"; rsync="$(findcmd rsync | head -n1)"; node="$(findcmd node | head -n1)"; npm="$(findcmd npm | head -n1)"; go="$(findcmd go | head -n1)"; cmake="$(findcmd cmake | head -n1)"; zstd="$(findcmd zstd | head -n1)"; tar="$(findcmd tar | head -n1)"
kv version_java "$([ -n "$java" ] && (command -v timeout >/dev/null 2>&1 && timeout 5 "$java" -version 2>&1 || "$java" -version 2>&1) | head -n1)"
kv version_gradle "$([ -n "$gradle" ] && (command -v timeout >/dev/null 2>&1 && timeout 5 "$gradle" --version 2>&1 || "$gradle" --version 2>&1) | awk '/^Gradle /{print;exit}')"
kv version_rsync "$([ -n "$rsync" ] && "$rsync" --version 2>/dev/null | head -n1)"
kv version_node "$([ -n "$node" ] && "$node" --version 2>/dev/null | head -n1)"
kv version_npm "$([ -n "$npm" ] && "$npm" --version 2>/dev/null | head -n1)"
kv version_go "$([ -n "$go" ] && "$go" version 2>/dev/null | head -n1)"
kv version_cmake "$([ -n "$cmake" ] && "$cmake" --version 2>/dev/null | head -n1)"
kv version_zstd "$([ -n "$zstd" ] && "$zstd" --version 2>/dev/null | head -n1)"
kv version_tar "$([ -n "$tar" ] && "$tar" --version 2>/dev/null | head -n1)"
java_home="${JAVA_HOME:-}"
if [ -z "$java_home" ] && [ -n "$java" ]; then
  jreal="$(readlink -f "$java" 2>/dev/null)"; [ -n "$jreal" ] && java_home="$(dirname "$(dirname "$jreal")")"
fi
kv java_home "$java_home"
kv gradle_user_home "${GRADLE_USER_HOME:-${HOME:-}/.gradle}"
sdk="$(firstdir "${ANDROID_SDK_ROOT:-}" "${ANDROID_HOME:-}" "${prefix:+$prefix/opt/android-sdk}" "${HOME:-}/Android/Sdk" "${HOME:-}/android-sdk" /opt/android-sdk /opt/android-sdk-linux)"
kv android_sdk_root "$sdk"
kv android_platforms "$(listdirs "$sdk/platforms")"
kv android_build_tools "$(listdirs "$sdk/build-tools")"
kv android_ndks "$(listdirs "$sdk/ndk")"
kv android_cmake "$(listdirs "$sdk/cmake")"
if [ -n "$sdk" ]; then
  bt="$(find "$sdk/build-tools" -mindepth 2 -maxdepth 2 -type f -name aapt2 2>/dev/null | sort -V | tail -n1)"; [ -n "$bt" ] && kv sdk_aapt2 "$bt" || kv sdk_aapt2 ""
  ad="$(find "$sdk/build-tools" -mindepth 2 -maxdepth 2 -type f -name aidl 2>/dev/null | sort -V | tail -n1)"; [ -z "$ad" ] && ad="$(find "$sdk/platform-tools" -maxdepth 1 -type f -name aidl 2>/dev/null | head -n1)"; kv sdk_aidl "$ad"
else kv sdk_aapt2 ""; kv sdk_aidl ""; fi
kv devtool_cache_root "${DEVTOOL_CACHE_ROOT:-${HOME:-}/.cache/devtool}"
kv gradle_cache_root "${GRADLE_USER_HOME:-${HOME:-}/.gradle}"
kv npm_cache_root "${npm_config_cache:-${HOME:-}/.npm}"
kv go_cache_root "${GOCACHE:-${HOME:-}/.cache/go-build}"
kv devtool_cache_kib "$(sizek "${DEVTOOL_CACHE_ROOT:-${HOME:-}/.cache/devtool}")"
kv gradle_cache_kib "$(sizek "${GRADLE_USER_HOME:-${HOME:-}/.gradle}")"
kv workspace_root_accessible "$([ -d "${HOME:-}/.cache/devtool" ] || [ -w "${HOME:-}" ] && printf yes || printf no)"
kv run_root_accessible "$([ -d "${HOME:-}/.local/state/devtool" ] || [ -w "${HOME:-}" ] && printf yes || printf no)"
exit 0
'''


def _parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in str(text or "").splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        if key.strip():
            out[key.strip()] = value.strip()
    return out


def _provider(path: str, *, prefix: str, home: str, platform: str = "") -> str:
    value = str(path or "")
    if not value:
        return "missing"
    if "/.cache/devtool/" in value or value.startswith((home.rstrip("/") + "/.cache/devtool/") if home else "\0"):
        return "devtool-cache"
    if platform == "termux" and prefix and (value == prefix or value.startswith(prefix.rstrip("/") + "/")):
        return "termux-existing"
    return "remote-existing"


def _secret_redact(key: str, value: Any) -> Any:
    if _SECRET_RE.search(str(key)):
        return "<redacted>" if str(value or "") else ""
    return value


def _path_summary(path: str) -> dict[str, Any]:
    entries = [item for item in str(path or "").split(":") if item]
    return {"count": len(entries), "entries": entries[:12], "truncated": len(entries) > 12}


def environment_payload(config: SSHRunnerConfig, *, repo_root: str | Path = "", target: str = "") -> dict[str, Any]:
    started = time.monotonic()
    completed = run_with_multiplex_fallback(
        config,
        build_ssh_remote_sh_argv(config, _probe_script(), tty=False),
        capture_output=True,
        text=True,
        timeout=_transport_timeout(),
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"SSH environment probe failed with exit {completed.returncode}: {detail or 'no diagnostic output'}")
    v = _parse(completed.stdout)
    home, prefix = v.get("home", ""), v.get("prefix", "")
    platform = v.get("platform", "unknown")
    tools: dict[str, Any] = {}
    tool_names = ("python", "java", "javac", "gradle", "rsync", "git", "tmux", "tar", "zstd", "node", "npm", "go", "cmake", "ninja", "aapt2", "aidl")
    for name in tool_names:
        path = v.get(f"tool_{name}", "")
        if name == "aapt2" and not path:
            path = v.get("sdk_aapt2", "")
        if name == "aidl" and not path:
            path = v.get("sdk_aidl", "")
        tools[name] = {
            "path": path,
            "version": v.get(f"version_{name}", ""),
            "provenance": _provider(path, prefix=prefix, home=home, platform=platform),
        }
    overlay = {str(k): _secret_redact(str(k), str(val)) for k, val in dict(getattr(config, "environment_overlay", {}) or {}).items()}
    workspace_root = str(getattr(config, "workspace_root", "") or "")
    payload = {
        "schema_version": 1,
        "kind": "ssh-environment",
        "ok": True,
        "profile": config.profile,
        "destination": config.destination,
        "execution_mode": config.mode,
        "execution_provider": config.execution_provider,
        "environment_policy": config.environment_policy,
        "worker": {
            "platform": platform, "os_name": v.get("os_name", ""),
            "architecture": v.get("architecture", ""), "home": home, "prefix": prefix,
            "tmpdir": v.get("tmpdir", ""), "shell": v.get("shell", ""), "path": _path_summary(v.get("path", "")),
        },
        "tools": tools,
        "java": {"home": v.get("java_home", ""), "provenance": _provider(v.get("java_home", ""), prefix=prefix, home=home, platform=platform)},
        "gradle": {"user_home": v.get("gradle_user_home", ""), "provenance": "remote-discovery"},
        "android": {
            "sdk_root": v.get("android_sdk_root", ""), "platforms": [x for x in v.get("android_platforms", "").split(",") if x],
            "build_tools": [x for x in v.get("android_build_tools", "").split(",") if x],
            "ndk": [x for x in v.get("android_ndks", "").split(",") if x], "cmake": [x for x in v.get("android_cmake", "").split(",") if x],
            "aapt2": v.get("sdk_aapt2", "") or v.get("tool_aapt2", ""), "aidl": v.get("sdk_aidl", "") or v.get("tool_aidl", ""),
            "provenance": _provider(v.get("android_sdk_root", ""), prefix=prefix, home=home, platform=platform),
        },
        "caches": {
            "devtool": {"root": v.get("devtool_cache_root", ""), "size_kib": int(v.get("devtool_cache_kib", "0") or 0), "provenance": "remote-discovery"},
            "gradle": {"root": v.get("gradle_cache_root", ""), "size_kib": int(v.get("gradle_cache_kib", "0") or 0), "provenance": "remote-discovery"},
            "npm": {"root": v.get("npm_cache_root", ""), "provenance": "remote-discovery"},
            "go": {"root": v.get("go_cache_root", ""), "provenance": "remote-discovery"},
        },
        "workspace": {"root": workspace_root, "provenance": "devtool-config", "repo_root": str(repo_root or ""), "target": str(target or "")},
        "overlay": overlay,
        "access": {"workspace_root": v.get("workspace_root_accessible") == "yes", "run_root": v.get("run_root_accessible") == "yes"},
        "multiplex": multiplex_evidence(config).as_dict(),
        "latency_seconds": round(time.monotonic() - started, 4),
    }
    return payload


__all__ = ["environment_payload"]
