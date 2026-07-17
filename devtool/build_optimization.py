from __future__ import annotations

"""Shared build resource and cache policy.

The policy is deliberately conservative on Android/Termux and chroot hosts,
where oversubscribing CPU usually causes memory pressure and makes builds
slower. Explicit project configuration and DEVTOOL_MAX_WORKERS always win.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
from typing import Mapping

from .execution_environments import (
    AUTO,
    CHROOT,
    DESKTOP_LINUX,
    NATIVE_TERMUX,
    detect_current_environment,
    normalize_execution_environment,
)
from .models import ProjectConfig


@dataclass(frozen=True, slots=True)
class BuildResourcePolicy:
    environment: str
    cpu_count: int
    memory_mb: int | None
    max_workers: int
    cache_root: Path
    low_memory: bool


def available_memory_mb() -> int | None:
    """Return currently available memory when Linux exposes it."""

    try:
        values: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace").splitlines():
            if ":" not in line:
                continue
            key, raw = line.split(":", 1)
            token = raw.strip().split()[0]
            if token.isdigit():
                values[key] = int(token)
        kib = values.get("MemAvailable") or values.get("MemFree")
        return max(1, kib // 1024) if kib else None
    except OSError:
        return None


def _option_dict(config: ProjectConfig, name: str) -> dict[str, object]:
    value = config.runner_options.get(name, {})
    return dict(value) if isinstance(value, dict) else {}


def _explicit_workers(config: ProjectConfig, kind: str) -> int | None:
    raw_env = os.environ.get("DEVTOOL_MAX_WORKERS", "").strip()
    candidates: list[object] = [raw_env] if raw_env else []
    candidates.extend([
        _option_dict(config, kind).get("max_workers"),
        _option_dict(config, "build").get("max_workers"),
    ])
    if kind in {"android", "gradle"}:
        candidates.extend([
            _option_dict(config, "gradle").get("max_workers"),
            _option_dict(config, "android").get("max_workers"),
        ])
    for value in candidates:
        if value in (None, "", "auto"):
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _effective_environment(config: ProjectConfig) -> str:
    configured = str(config.execution_environment or AUTO).strip().lower()
    if configured and configured != AUTO:
        try:
            return normalize_execution_environment(configured)
        except ValueError:
            pass
    return detect_current_environment()


def _cache_root(config: ProjectConfig, kind: str, environment: str, env: Mapping[str, str] | None = None) -> Path:
    env = env or os.environ
    configured = str(_option_dict(config, "build").get("cache_root") or "").strip()
    if configured:
        base = Path(configured).expanduser()
    elif environment == CHROOT and detect_current_environment(env) != CHROOT:
        chroot_home = str(env.get("DEVTOOL_TERMUX_CHROOT_HOME") or "").strip()
        if not chroot_home:
            user = str(env.get("DEVTOOL_TERMUX_CHROOT_USER") or "mike").strip() or "mike"
            chroot_home = f"/home/{user}"
        base = Path(chroot_home) / ".cache"
    else:
        xdg = str(env.get("XDG_CACHE_HOME") or "").strip()
        if xdg:
            base = Path(xdg).expanduser()
        else:
            base = Path(str(env.get("HOME") or "~")).expanduser() / ".cache"
    return base / "devtool" / "build" / environment / kind


def resolve_build_policy(config: ProjectConfig, kind: str) -> BuildResourcePolicy:
    environment = _effective_environment(config)
    cpus = max(1, int(os.cpu_count() or 1))
    memory = available_memory_mb()
    explicit = _explicit_workers(config, kind)

    if explicit is not None:
        workers = explicit
    else:
        environment_cap = {
            DESKTOP_LINUX: 8,
            CHROOT: 4,
            NATIVE_TERMUX: 3,
        }.get(environment, 4)
        workers = min(cpus, environment_cap)

        # Compiler-heavy ecosystems need roughly 1.1-1.5 GiB per concurrent
        # worker on constrained devices. Lighter scripting ecosystems can use
        # more concurrency without forcing Android to reclaim the build.
        heavy = kind in {"android", "gradle", "dotnet", "maven", "rust", "cmake", "meson", "make"}
        memory_per_worker = 1200 if heavy else 700
        if memory is not None:
            workers = min(workers, max(1, memory // memory_per_worker))
        if environment in {CHROOT, NATIVE_TERMUX} and memory is not None and memory < 3000:
            workers = min(workers, 2)

    workers = max(1, min(cpus, workers))
    low_memory = bool(memory is not None and memory < 3500)
    return BuildResourcePolicy(
        environment=environment,
        cpu_count=cpus,
        memory_mb=memory,
        max_workers=workers,
        cache_root=_cache_root(config, kind, environment),
        low_memory=low_memory,
    )


def optimized_build_env(
    config: ProjectConfig,
    kind: str,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return environment additions for an incremental, bounded build."""

    policy = resolve_build_policy(config, kind)
    env = {str(key): str(value) for key, value in (base or {}).items()}
    env.setdefault("DEVTOOL_MAX_WORKERS", str(policy.max_workers))
    env.setdefault("DEVTOOL_BUILD_ENVIRONMENT", policy.environment)
    env.setdefault("DEVTOOL_BUILD_CACHE_ROOT", str(policy.cache_root))

    if kind == "dotnet":
        env.setdefault("DOTNET_CLI_TELEMETRY_OPTOUT", "1")
        env.setdefault("DOTNET_NOLOGO", "1")
        env.setdefault("NUGET_XMLDOC_MODE", "skip")
        env.setdefault("DOTNET_PROCESSOR_COUNT", str(policy.max_workers))
    elif kind in {"android", "gradle"}:
        env.setdefault("GRADLE_USER_HOME", str(Path.home() / ".gradle"))
    elif kind == "python":
        env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
        env.setdefault("PIP_NO_INPUT", "1")
        env.setdefault("UV_CACHE_DIR", str(policy.cache_root / "uv"))
    elif kind == "node":
        env.setdefault("npm_config_cache", str(policy.cache_root / "npm"))
        env.setdefault("npm_config_audit", "false")
        env.setdefault("npm_config_fund", "false")
        env.setdefault("YARN_CACHE_FOLDER", str(policy.cache_root / "yarn"))
        env.setdefault("BUN_INSTALL_CACHE_DIR", str(policy.cache_root / "bun"))
    elif kind == "rust":
        env.setdefault("CARGO_BUILD_JOBS", str(policy.max_workers))
        env.setdefault("CARGO_INCREMENTAL", "1")
        env.setdefault("CARGO_NET_RETRY", "2")
        if policy.environment == detect_current_environment() and shutil.which("sccache"):
            env.setdefault("RUSTC_WRAPPER", "sccache")
    elif kind == "go":
        env.setdefault("GOMAXPROCS", str(policy.max_workers))
    elif kind in {"cmake", "meson", "make"}:
        env.setdefault("CMAKE_BUILD_PARALLEL_LEVEL", str(policy.max_workers))
        env.setdefault("CTEST_PARALLEL_LEVEL", str(policy.max_workers))

    return env
