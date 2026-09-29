from __future__ import annotations

"""Shared build resource and cache policy.

The policy is deliberately conservative on Android/Termux and chroot hosts,
where oversubscribing CPU usually causes memory pressure and makes builds
slower. Explicit project configuration and DEVTOOL_MAX_WORKERS always win.
"""

from dataclasses import dataclass, replace
import os
from pathlib import Path
import shutil
import shlex
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


@dataclass(frozen=True, slots=True)
class AndroidValidationResourcePolicy:
    enabled: bool
    environment: str
    max_workers: int
    cpu_limit: int
    gradle_heap_mb: int
    memory_guard_mb: int
    split_gradle: bool
    no_daemon: bool
    low_priority: bool


def memory_info_mb() -> tuple[int | None, int | None]:
    """Return ``(available, total)`` memory from Linux procfs."""

    try:
        values: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace").splitlines():
            if ":" not in line:
                continue
            key, raw = line.split(":", 1)
            token = raw.strip().split()[0]
            if token.isdigit():
                values[key] = int(token)
        available_kib = values.get("MemAvailable") or values.get("MemFree")
        total_kib = values.get("MemTotal")
        available = max(1, available_kib // 1024) if available_kib else None
        total = max(1, total_kib // 1024) if total_kib else None
        return available, total
    except OSError:
        return None, None


def available_memory_mb() -> int | None:
    """Return currently available memory when Linux exposes it."""

    return memory_info_mb()[0]


def _option_dict(config: ProjectConfig, name: str) -> dict[str, object]:
    value = config.runner_options.get(name, {})
    return dict(value) if isinstance(value, dict) else {}


def _cli_resource_overrides(validation: Mapping[str, object]) -> dict[str, dict[str, object]]:
    raw = validation.get("_cli_resource_overrides")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, object]] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            result[str(key)] = dict(value)
    return result


def _cli_override_value(
    overrides: Mapping[str, Mapping[str, object]],
    key: str,
) -> object | None:
    record = overrides.get(key)
    return record.get("value") if isinstance(record, Mapping) else None


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
    # Resolve from an explicit environment snapshot rather than letting the
    # ambient Python platform leak into policy selection.  This matters on
    # Termux/Android where tests and delegated execution can model a desktop
    # Linux environment by replacing os.environ while the parent interpreter
    # still reports Android.  detect_host_context() treats an explicit mapping
    # as the target process environment and still recognizes real Termux via
    # TERMUX/PREFIX/HOME markers.
    return detect_current_environment(dict(os.environ))


def _bool_option(value: object, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _positive_int(*values: object, allow_zero: bool = False) -> int | None:
    for value in values:
        if value in (None, "", "auto"):
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0 or (allow_zero and parsed == 0):
            return parsed
    return None


def resolve_android_validation_policy(config: ProjectConfig) -> AndroidValidationResourcePolicy:
    """Choose a phone-safe Gradle profile for validation.

    Android-hosted validation still owns outer-process CPU/priority/watchdog
    controls, but ENV-X03 no longer equates a phone with one Gradle worker, no
    daemon, no VFS, or forced split topology. Core Gradle reuse/resource policy
    is selected by the canonical Gradle planner.
    """

    environment = _effective_environment(config)
    mobile_host = environment in {CHROOT, NATIVE_TERMUX}
    validation = _option_dict(config, "validation")
    gradle = _option_dict(config, "gradle")
    android = _option_dict(config, "android")
    cli_overrides = _cli_resource_overrides(validation)

    env_enabled = os.environ.get("DEVTOOL_ANDROID_SAFE_VALIDATION", "").strip()
    enabled = _bool_option(
        env_enabled if env_enabled else validation.get("android_safe"),
        mobile_host,
    )
    base = resolve_build_policy(config, config.kind or "android")
    workers = _positive_int(
        _cli_override_value(cli_overrides, "max_workers"),
        os.environ.get("DEVTOOL_VALIDATION_MAX_WORKERS", ""),
        validation.get("max_workers"),
        gradle.get("validation_max_workers"),
        android.get("validation_max_workers"),
    )
    if workers is None:
        workers = base.max_workers
    workers = max(1, min(base.cpu_count, workers))

    cpu_limit = _positive_int(
        _cli_override_value(cli_overrides, "cpu_limit"),
        os.environ.get("DEVTOOL_VALIDATION_CPU_LIMIT", ""),
        validation.get("cpu_limit"),
        android.get("validation_cpu_limit"),
    )
    if cpu_limit is None:
        cpu_limit = min(2, base.cpu_count) if enabled and mobile_host else base.cpu_count
    cpu_limit = max(1, min(base.cpu_count, cpu_limit))

    available, total = memory_info_mb()
    heap = _positive_int(
        _cli_override_value(cli_overrides, "gradle_heap_mb"),
        os.environ.get("DEVTOOL_GRADLE_VALIDATION_HEAP_MB", ""),
        validation.get("gradle_heap_mb"),
        gradle.get("validation_heap_mb"),
    )
    if heap is None:
        basis = available or total or 4096
        heap = max(768, min(2048, int(basis * 0.35)))

    guard = _positive_int(
        _cli_override_value(cli_overrides, "memory_guard_mb"),
        os.environ.get("DEVTOOL_VALIDATION_MEMORY_GUARD_MB", ""),
        validation.get("memory_guard_mb"),
        allow_zero=True,
    )
    if guard is None:
        # The memory guard is opt-in.  Android-safe validation still constrains
        # workers, CPU, heap, daemon behavior, and phase splitting, but it no
        # longer enables an implicit watchdog unless the user/config/env asks
        # for a positive threshold.
        guard = 0

    return AndroidValidationResourcePolicy(
        enabled=enabled,
        environment=environment,
        max_workers=workers,
        cpu_limit=cpu_limit,
        gradle_heap_mb=heap,
        memory_guard_mb=guard if enabled else 0,
        split_gradle=_bool_option(validation.get("split_gradle"), False),
        no_daemon=_bool_option(
            _cli_override_value(cli_overrides, "no_daemon"),
            _bool_option(validation.get("no_daemon"), False),
        ),
        low_priority=_bool_option(
            _cli_override_value(cli_overrides, "low_priority"),
            _bool_option(validation.get("low_priority"), enabled and mobile_host),
        ),
    )



def _android_validation_phase_builtins(
    base: AndroidValidationResourcePolicy,
) -> dict[str, dict[str, object]]:
    return {
        "preflight": {"cpu_limit": 1, "gradle_heap_mb": 768},
        "dependencies": {"cpu_limit": 1, "gradle_heap_mb": 768},
        "compile": {"cpu_limit": min(2, base.cpu_limit)},
        "resources": {"cpu_limit": min(2, base.cpu_limit), "gradle_heap_mb": max(1024, base.gradle_heap_mb)},
        "unit-test": {"cpu_limit": min(2, base.cpu_limit)},
        "package": {"cpu_limit": min(2, base.cpu_limit), "gradle_heap_mb": max(1280, base.gradle_heap_mb)},
        "lint": {"cpu_limit": min(2, base.cpu_limit), "gradle_heap_mb": max(1536, base.gradle_heap_mb)},
        "instrumentation": {"cpu_limit": 1, "gradle_heap_mb": max(768, base.gradle_heap_mb)},
        "finalize": {"cpu_limit": 1, "gradle_heap_mb": 768},
    }


def resolve_android_validation_phase_policy(
    config: ProjectConfig,
    phase: str,
) -> AndroidValidationResourcePolicy:
    """Return a phase-specific Android validation resource profile.

    The base mobile-safe policy remains authoritative.  Phase profiles only
    narrow concurrency or raise bounded heap floors for workloads that need it.
    Projects can override any built-in value under
    ``[validation.phase_profiles.<phase>]``.
    """

    base = resolve_android_validation_policy(config)
    normalized = str(phase or "").strip().lower().replace("_", "-")
    builtins = _android_validation_phase_builtins(base)
    values = dict(builtins.get(normalized, {}))
    validation = _option_dict(config, "validation")
    profiles = validation.get("phase_profiles")
    configured: dict[str, object] = {}
    if isinstance(profiles, dict):
        raw_configured = profiles.get(normalized) or profiles.get(normalized.replace("-", "_"))
        if isinstance(raw_configured, dict):
            configured = dict(raw_configured)
            values.update(configured)

    cli_overrides = _cli_resource_overrides(validation)
    # Explicit CLI flags are invocation-level intent and therefore outrank
    # built-in Android-safe phase defaults and project phase profiles. Real
    # host limits (for example os.cpu_count()) may still clamp the request.
    for key in (
        "max_workers",
        "cpu_limit",
        "gradle_heap_mb",
        "memory_guard_mb",
        "no_daemon",
        "low_priority",
    ):
        cli_value = _cli_override_value(cli_overrides, key)
        if cli_value is not None:
            values[key] = cli_value

    max_workers = _positive_int(values.get("max_workers")) or base.max_workers
    cpu_limit = _positive_int(values.get("cpu_limit")) or base.cpu_limit
    heap = _positive_int(values.get("gradle_heap_mb")) or base.gradle_heap_mb
    guard = _positive_int(values.get("memory_guard_mb"), allow_zero=True)
    no_daemon = _bool_option(values.get("no_daemon"), base.no_daemon)
    low_priority = _bool_option(values.get("low_priority"), base.low_priority)
    split_gradle = _bool_option(values.get("split_gradle"), base.split_gradle)

    available, total = memory_info_mb()
    host_basis = available or total or max(base.gradle_heap_mb * 3, 4096)
    host_safe_heap_ceiling = max(base.gradle_heap_mb, int(host_basis * 0.45))
    return replace(
        base,
        max_workers=max(1, min(base.max_workers, max_workers)),
        cpu_limit=max(1, min(base.cpu_limit, cpu_limit)),
        # Phase floors may raise the default. Only the independently computed
        # host-safe ceiling may clamp them; the old code incorrectly used the
        # default heap itself as a ceiling.
        gradle_heap_mb=max(256, min(host_safe_heap_ceiling, heap)),
        memory_guard_mb=base.memory_guard_mb if guard is None else max(0, guard),
        no_daemon=no_daemon,
        low_priority=low_priority,
        split_gradle=split_gradle,
    )

def android_validation_cli_resolution_notes(
    config: ProjectConfig,
    phase: str,
) -> list[str]:
    """Describe how explicit validation CLI flags resolved for one phase."""

    validation = _option_dict(config, "validation")
    overrides = _cli_resource_overrides(validation)
    if not overrides:
        return []

    policy = resolve_android_validation_phase_policy(config, phase)
    base = resolve_android_validation_policy(config)
    normalized = str(phase or "").strip().lower().replace("_", "-")
    builtin = _android_validation_phase_builtins(base).get(normalized, {})
    profiles = validation.get("phase_profiles")
    phase_profile: dict[str, object] = {}
    if isinstance(profiles, dict):
        raw = profiles.get(normalized) or profiles.get(normalized.replace("-", "_"))
        if isinstance(raw, dict):
            phase_profile = dict(raw)

    gradle = _option_dict(config, "gradle")
    effective: dict[str, object] = {
        "max_workers": policy.max_workers,
        "cpu_limit": policy.cpu_limit,
        "gradle_heap_mb": policy.gradle_heap_mb,
        "memory_guard_mb": policy.memory_guard_mb,
        "no_daemon": policy.no_daemon,
        "low_priority": policy.low_priority,
        "parallel": _bool_option(validation.get("parallel"), False),
        "build_cache": _bool_option(gradle.get("build_cache"), True),
    }

    notes: list[str] = []
    for key, record in overrides.items():
        if key not in effective:
            continue
        requested = record.get("value")
        resolved = effective[key]
        flag = str(record.get("flag") or key)
        prior = record.get("prior")
        displaced: list[str] = []
        if isinstance(prior, Mapping):
            for source, value in prior.items():
                if value not in (None, "", "auto") and value != requested:
                    displaced.append(f"{source}={value}")
        if key in phase_profile and phase_profile.get(key) != requested:
            displaced.append(f"phase-profile[{normalized}]={phase_profile.get(key)}")
        elif key in builtin and builtin.get(key) != requested:
            displaced.append(f"android-safe[{normalized}]={builtin.get(key)}")

        status = "applied" if resolved == requested else "clamped"
        detail = (
            f"{key}: {flag} requested={requested} -> effective={resolved} ({status})"
        )
        if displaced:
            detail += "; overrides " + ", ".join(displaced)
        if status == "clamped" and key in {"max_workers", "cpu_limit"}:
            detail += f"; host cpu_count={resolve_build_policy(config, config.kind or 'android').cpu_count}"
        notes.append(detail)
    return notes


def _bounded_makeflags(existing: str, workers: int) -> str:
    """Replace only job-count tokens while preserving user make flags."""
    try:
        tokens = shlex.split(str(existing or ""))
    except ValueError:
        tokens = str(existing or "").split()
    kept: list[str] = []
    skip = False
    for token in tokens:
        if skip:
            skip = False
            continue
        if token in {"-j", "--jobs"}:
            skip = True
            continue
        if token.startswith("-j") and token[2:].isdigit():
            continue
        if token.startswith("--jobs="):
            continue
        kept.append(token)
    kept.append(f"-j{max(1, int(workers))}")
    return " ".join(shlex.quote(token) for token in kept)


def optimized_validation_env(config: ProjectConfig, kind: str) -> dict[str, str]:
    env = optimized_build_env(config, kind, config.env)
    if kind in {"android", "gradle"}:
        policy = resolve_android_validation_policy(config)
        env["DEVTOOL_MAX_WORKERS"] = str(policy.max_workers)
        env["DEVTOOL_VALIDATION_CPU_LIMIT"] = str(policy.cpu_limit)
        env["DEVTOOL_VALIDATION_RESOURCE_PROFILE"] = (
            "android-safe" if policy.enabled else "standard"
        )
        if policy.enabled:
            validation = _option_dict(config, "validation")
            parallel_enabled = _bool_option(validation.get("parallel"), False)
            safe_gradle_parts = [
                f"-Dorg.gradle.workers.max={policy.max_workers}",
                "-Dorg.gradle.vfs.watch=false",
                f"-Dorg.gradle.jvmargs=-Xmx{policy.gradle_heap_mb}m",
                f"-XX:ActiveProcessorCount={policy.cpu_limit}",
            ]
            if not parallel_enabled:
                safe_gradle_parts.insert(1, "-Dorg.gradle.parallel=false")
            safe_gradle_opts = " ".join(safe_gradle_parts)
            existing = str(env.get("GRADLE_OPTS") or "").strip()
            env["GRADLE_OPTS"] = f"{existing} {safe_gradle_opts}".strip()
            java_options = str(env.get("JAVA_TOOL_OPTIONS") or "").strip()
            processor_cap = f"-XX:ActiveProcessorCount={policy.cpu_limit}"
            if processor_cap not in java_options:
                env["JAVA_TOOL_OPTIONS"] = f"{java_options} {processor_cap}".strip()
            env["CMAKE_BUILD_PARALLEL_LEVEL"] = str(policy.max_workers)
            env["MAKEFLAGS"] = _bounded_makeflags(env.get("MAKEFLAGS", ""), policy.max_workers)
    return env


def _home_for_environment(environment: str, env: Mapping[str, str] | None = None) -> Path:
    env = env or os.environ
    if environment == CHROOT and detect_current_environment(env) != CHROOT:
        chroot_home = str(env.get("DEVTOOL_TERMUX_CHROOT_HOME") or "").strip()
        if chroot_home:
            path = Path(chroot_home)
            if not path.is_absolute():
                raise ValueError("DEVTOOL_TERMUX_CHROOT_HOME must be absolute")
            return path
        raise ValueError(
            "Chroot home is unknown; set DEVTOOL_TERMUX_CHROOT_HOME or allow execution preflight to probe the selected chroot"
        )
    return Path(str(env.get("HOME") or "~")).expanduser()


def _cache_root(config: ProjectConfig, kind: str, environment: str, env: Mapping[str, str] | None = None) -> Path:
    env = env or os.environ
    from .cache_cleanup import repository_cache_key
    repo_key = repository_cache_key(config.repo_root)
    configured = str(_option_dict(config, "build").get("cache_root") or "").strip()
    if configured:
        base = Path(configured).expanduser()
        if not base.is_absolute():
            base = Path(config.project_root or config.repo_root) / base
        base = base.resolve(strict=False)
    elif environment == NATIVE_TERMUX:
        from .termux_runtime import termux_cache_root
        return termux_cache_root(env) / "build" / repo_key / environment / kind
    elif environment == CHROOT and detect_current_environment(env) != CHROOT:
        try:
            base = _home_for_environment(environment, env) / ".cache"
        except ValueError:
            # Cache placement must not invent a Linux identity.  When the host
            # cannot prove the guest home, use a neutral guest-local scratch
            # root; execution preflight/root operations still enforce identity.
            base = Path("/tmp/devtool-cache")
    else:
        raw = str(env.get("XDG_CACHE_HOME") or "").strip()
        candidate = Path(raw).expanduser() if raw else None
        if candidate is not None and candidate.is_absolute():
            base = candidate.resolve(strict=False)
        else:
            base = Path(str(env.get("HOME") or "~")).expanduser() / ".cache"
    return base / "devtool" / "build" / repo_key / environment / kind

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
        # Keep .NET runtime state (including Avalonia test state) out of the user home.
        isolated_home = policy.cache_root / "runtime-home"
        env.setdefault("HOME", str(isolated_home))
        env.setdefault("XDG_CONFIG_HOME", str(isolated_home / ".config"))
        env.setdefault("XDG_DATA_HOME", str(isolated_home / ".local" / "share"))
        env.setdefault("XDG_CACHE_HOME", str(isolated_home / ".cache"))
    elif kind in {"android", "gradle"}:
        try:
            gradle_home = _home_for_environment(policy.environment) / ".gradle"
        except ValueError:
            gradle_home = policy.cache_root / "gradle-user-home"
        env.setdefault("GRADLE_USER_HOME", str(gradle_home))
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
