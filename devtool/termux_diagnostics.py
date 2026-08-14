from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .models import ProjectConfig
from .proot_distro_runner import installed_distros, proot_distro_available
from .termux_runtime import (
    is_android_shared_storage,
    select_termux_resource_profile,
    termux_cache_root,
    tmux_available,
)
from .termux_toolchains import (
    ToolProvider,
    classify_native_requirement,
    detect_runtime_identity,
    gradle_wrapper_probe,
    termux_android_tool_providers,
    termux_ccache_probe,
    termux_cmake_probe,
    termux_gradle_probe,
    termux_installed_packages,
    termux_java_providers,
    termux_native_provider,
    termux_ninja_probe,
)


@dataclass(frozen=True)
class PackageRecommendation:
    package: str
    capability: str
    reason: str
    required: bool = False


@dataclass(frozen=True)
class ProotGuestProbe:
    distro: str
    available: bool
    installed: bool
    reachable: bool
    os_release: str = ""
    tools: tuple[tuple[str, str], ...] = ()
    reason: str = ""

    def tool(self, name: str) -> str:
        return dict(self.tools).get(name, "")


_PACKAGE_CAPABILITY_ORDER: dict[str, int] = {
    "proot-distro": 0,
    "tmux": 1,
    "gradle": 2,
    "java": 3,
    "native-compiler": 4,
    "native-sysroot": 5,
    "cmake": 6,
    "ninja": 7,
    "ccache": 8,
    "android-build-tools": 9,
    "apk-signing": 10,
    "adb": 11,
}


def _preferred_termux_openjdk_package(
    *,
    env: Mapping[str, str],
    runtime,
) -> str:
    """Return the best read-only OpenJDK package candidate for this Termux repo.

    Prefer an already installed OpenJDK package.  Otherwise query apt-cache
    (which does not mutate package state) so a future Termux JDK rename/version
    does not leave diagnostics permanently pinned to ``openjdk-21``.  The
    current known package remains a conservative final fallback.
    """

    def rank(name: str) -> tuple[int, str]:
        match = re.fullmatch(r"openjdk-(\d+)", name)
        return (int(match.group(1)) if match else -1, name)

    installed = [
        item.name for item in termux_installed_packages("openjdk-*", env=env, runtime=runtime)
        if re.fullmatch(r"openjdk-\d+", item.name)
    ]
    if installed:
        return max(installed, key=rank)

    apt_cache = shutil.which("apt-cache", path=str(env.get("PATH") or ""))
    if apt_cache:
        try:
            result = subprocess.run(
                [apt_cache, "pkgnames", "openjdk-"],
                text=True, capture_output=True, check=False, timeout=5, env=dict(env),
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0:
            available = [
                line.strip() for line in result.stdout.splitlines()
                if re.fullmatch(r"openjdk-\d+", line.strip())
            ]
            if available:
                return max(available, key=rank)
    return "openjdk-21"


def _android_termux_doctor_rows(config: ProjectConfig) -> list[tuple[str, str]]:
    """Report Android SDK/full-NDK readiness without installing or building."""
    project_root = Path(config.project_root or config.repo_root)
    requirement = classify_native_requirement(project_root, android=config.kind == "android")
    rows: list[tuple[str, str]] = [
        ("native_requirement", f"{requirement.capability}: {requirement.reason}"),
    ]
    if config.kind != "android":
        return rows
    try:
        from . import android_build

        project = android_build.inspect_project(project_root)
        sdk, source = android_build.discover_sdk_root_with_source(None, project_root)
    except Exception as exc:
        rows.append(("android_sdk", f"UNAVAILABLE: {exc}"))
        return rows

    rows.append(("android_sdk", f"{sdk} ({source or 'unknown source'})" if sdk else "MISSING"))
    if sdk:
        versions = android_build.installed_build_tools_versions(sdk)
        rows.append(("android_sdk_build_tools", ", ".join(versions) if versions else "MISSING"))
    else:
        rows.append(("android_sdk_build_tools", "MISSING"))

    if requirement.capability != "android-ndk":
        rows.append(("android_full_ndk", "not required for detected project capability"))
        return rows

    declared = str(project.ndk_version or "").strip()
    if declared and sdk:
        ndk_root = sdk / "ndk" / declared
        ready = android_build.ndk_is_ready(sdk, declared)
        rows.append(("android_full_ndk", f"{'READY' if ready else 'MISSING/INCOMPLETE'}: {declared} @ {ndk_root}"))
        return rows
    if declared:
        rows.append(("android_full_ndk", f"MISSING: declared {declared}; Android SDK not found"))
        return rows

    installed: list[str] = []
    if sdk and (sdk / "ndk").is_dir():
        try:
            installed = sorted(path.name for path in (sdk / "ndk").iterdir() if path.is_dir())
        except OSError:
            installed = []
    rows.append(("android_full_ndk", "installed: " + ", ".join(installed) if installed else "required; version auto/none installed"))
    return rows


def _dict_option(config: ProjectConfig, key: str) -> dict:
    value = config.runner_options.get(key, {}) if isinstance(config.runner_options, dict) else {}
    return dict(value) if isinstance(value, dict) else {}


def _provider_status(provider: ToolProvider) -> str:
    package = f" package={provider.package}{'@' + provider.package_version if provider.package_version else ''}" if provider.package else ""
    if provider.compatible and provider.path:
        version = f" {provider.version}" if provider.version else ""
        return f"READY{version} @ {provider.path}{package}"
    if provider.available:
        reason = provider.rejection_reason or provider.reason or "provider is not usable"
        return f"UNUSABLE: {reason}{package}"
    reason = provider.rejection_reason or provider.reason or "not detected"
    return f"MISSING: {reason}{package}"


def _java_status(providers: Sequence[ToolProvider]) -> str:
    if not providers:
        return "MISSING: no Termux OpenJDK provider detected"
    parts: list[str] = []
    for provider in providers:
        if provider.compatible and provider.path:
            parts.append(f"{provider.version or '?'} @ {provider.path}")
        else:
            parts.append(f"UNUSABLE {provider.path or provider.package}: {provider.rejection_reason or provider.reason}")
    return "; ".join(parts)


def _configured_providers(config: ProjectConfig) -> dict[str, str]:
    android = _dict_option(config, "android")
    gradle = _dict_option(config, "gradle")
    java = android.get("java", {}) if isinstance(android.get("java"), dict) else {}
    native = android.get("native", {}) if isinstance(android.get("native"), dict) else {}
    build_tools = android.get("build_tools", {}) if isinstance(android.get("build_tools"), dict) else {}
    return {
        "java": str(java.get("provider") or android.get("java_provider") or "auto"),
        "gradle": str(gradle.get("provider") or "auto"),
        "native": str(native.get("provider") or android.get("native_provider") or "auto"),
        "native_cache": str(native.get("cache") or android.get("native_cache") or "auto"),
        "android_build_tools": str(build_tools.get("provider") or android.get("build_tools_provider") or "auto"),
        "aapt2": str(build_tools.get("aapt2_provider") or android.get("aapt2_provider") or "auto"),
    }


def provider_explanation_rows(config: ProjectConfig, *, env: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    env = env or os.environ
    runtime = detect_runtime_identity(env)
    configured = _configured_providers(config)
    wrapper = gradle_wrapper_probe(Path(config.project_root or config.repo_root))
    gradle = termux_gradle_probe(env=env, runtime=runtime)
    java = termux_java_providers(env=env, runtime=runtime)
    native = termux_native_provider(env=env, runtime=runtime)
    native_requirement = classify_native_requirement(
        Path(config.project_root or config.repo_root),
        android=config.kind == "android",
    )

    if wrapper.complete:
        gradle_reason = "wrapper selected/preferred: complete project Gradle Wrapper pins the project version"
    elif wrapper.present:
        gradle_reason = "wrapper blocked: incomplete Gradle Wrapper; missing " + ", ".join(wrapper.missing)
    elif configured["gradle"] == "termux":
        gradle_reason = "Termux Gradle explicitly requested; " + ("provider is ready" if gradle.compatible else _provider_status(gradle))
    elif configured["gradle"] in {"system", "download"}:
        gradle_reason = f"{configured['gradle']} Gradle explicitly requested; Termux Gradle is not auto-selected"
    elif gradle.compatible:
        gradle_reason = "auto: no complete wrapper detected; Termux Gradle is an eligible native fallback"
    else:
        gradle_reason = "auto: no complete wrapper and Termux Gradle is unavailable/unusable; managed/system fallback remains eligible"

    if native_requirement.capability == "android-ndk":
        native_reason = "full Android NDK required: Termux ndk-sysroot is compiler/sysroot capability only"
    elif native_requirement.capability == "compiler" and native.compatible:
        native_reason = "compiler-only native work can use Termux clang/lld + ndk-sysroot"
    elif native_requirement.capability == "compiler":
        native_reason = "compiler-only native work detected, but Termux native provider is unavailable/unusable"
    else:
        native_reason = "no native compiler work detected; no native provider is required"

    java_ready = [item for item in java if item.compatible]
    if configured["java"] == "termux":
        java_reason = "Termux Java explicitly requested; " + (f"{len(java_ready)} compatible JDK provider(s) detected" if java_ready else "no compatible Termux JDK detected")
    elif configured["java"] == "system":
        java_reason = "system Java explicitly requested; Termux JDK providers are not auto-selected"
    else:
        java_reason = f"auto: {len(java_ready)} compatible Termux JDK provider(s) detected; project/runtime compatibility decides the final JDK"

    return [
        ("provider_config", ", ".join(f"{key}={value}" for key, value in configured.items())),
        ("provider_explain_gradle", gradle_reason),
        ("provider_explain_java", java_reason),
        ("provider_explain_native", native_reason),
        ("provider_explain_android_tools", f"requested={configured['android_build_tools']}, aapt2={configured['aapt2']}; Android runner performs compileSdk/runtime compatibility arbitration"),
    ]


def termux_package_recommendations(config: ProjectConfig, *, env: Mapping[str, str] | None = None) -> tuple[PackageRecommendation, ...]:
    env = env or os.environ
    runtime = detect_runtime_identity(env)
    if not runtime.is_termux:
        return ()

    recommendations: list[PackageRecommendation] = []
    proot = proot_distro_available()
    if not proot:
        recommendations.append(PackageRecommendation("proot-distro", "proot-distro", "enables the rootless Linux execution backend"))
    if not tmux_available():
        recommendations.append(PackageRecommendation("tmux", "tmux", "allows detaching whole local devtool orchestration"))

    project_root = Path(config.project_root or config.repo_root)
    wrapper = gradle_wrapper_probe(project_root)
    gradle = termux_gradle_probe(env=env, runtime=runtime)
    configured = _configured_providers(config)
    if not wrapper.complete and configured["gradle"] in {"auto", "termux"} and not gradle.compatible:
        recommendations.append(PackageRecommendation("gradle", "gradle", "wrapperless native Termux Gradle fallback is unavailable", configured["gradle"] == "termux"))

    java = termux_java_providers(env=env, runtime=runtime)
    if configured["java"] == "termux" and not any(item.compatible for item in java):
        recommendations.append(PackageRecommendation(
            _preferred_termux_openjdk_package(env=env, runtime=runtime),
            "java",
            "Termux Java provider is explicitly requested but no usable Termux JDK was detected",
            True,
        ))

    native_requirement = classify_native_requirement(project_root, android=config.kind == "android")
    native = termux_native_provider(env=env, runtime=runtime)
    if native_requirement.capability == "compiler" and not native.compatible:
        recommendations.extend((
            PackageRecommendation("clang", "native-compiler", "compiler-only native work needs a Termux-native compiler", True),
            PackageRecommendation("ndk-sysroot", "native-sysroot", "compiler-only native work needs the Termux Android/Bionic sysroot", True),
        ))

    has_cmake = any(project_root.rglob("CMakeLists.txt"))
    if has_cmake and not termux_cmake_probe(env=env, runtime=runtime).compatible:
        recommendations.append(PackageRecommendation("cmake", "cmake", "CMake project detected and Termux CMake is unavailable"))
    if has_cmake and not termux_ninja_probe(env=env, runtime=runtime).compatible:
        recommendations.append(PackageRecommendation("ninja", "ninja", "CMake project detected and Termux Ninja is unavailable"))
    if native_requirement.capability != "none" and configured["native_cache"] in {"auto", "ccache"} and not termux_ccache_probe(env=env, runtime=runtime).compatible:
        recommendations.append(PackageRecommendation("ccache", "ccache", "optional native compiler cache is unavailable"))

    if config.kind == "android" and configured["android_build_tools"] == "termux":
        tools = {item.capability: item for item in termux_android_tool_providers(env=env, runtime=runtime)}
        if not tools.get("aapt2") or not tools["aapt2"].compatible:
            recommendations.append(PackageRecommendation("aapt", "android-build-tools", "Termux Android Build Tools provider was explicitly requested", True))
        if not tools.get("apksigner") or not tools["apksigner"].compatible:
            recommendations.append(PackageRecommendation("apksigner", "apk-signing", "Termux Android Build Tools provider was explicitly requested", True))
        if not tools.get("adb") or not tools["adb"].compatible:
            recommendations.append(PackageRecommendation("android-tools", "adb", "Termux Android platform tools are unavailable"))

    # Preserve deterministic order and deduplicate packages that satisfy several capabilities.
    deduped: dict[str, PackageRecommendation] = {}
    for item in recommendations:
        previous = deduped.get(item.package)
        if previous is None or (item.required and not previous.required):
            deduped[item.package] = item
    return tuple(sorted(
        deduped.values(),
        key=lambda item: (_PACKAGE_CAPABILITY_ORDER.get(item.capability, 999), item.package),
    ))


def package_recommendation_rows(config: ProjectConfig, *, env: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    items = termux_package_recommendations(config, env=env)
    if not items:
        return [("termux_packages", "no additional Termux package recommendations")]
    names = " ".join(item.package for item in items)
    rows = [("termux_package_install_hint", f"pkg install {names}  # suggestion only; devtool never installs/upgrades packages automatically")]
    for item in items:
        rows.append((f"package_{item.package}", f"{'REQUIRED' if item.required else 'OPTIONAL'}: {item.reason}"))
    return rows


def _proot_guest_probe_script(tool_names: Sequence[str]) -> str:
    """Return the POSIX-shell guest probe used by :func:`proot_guest_probe`."""

    return (
        "set -eu; "
        "os=''; if [ -r /etc/os-release ]; then os=$(sed -n 's/^PRETTY_NAME=//p' /etc/os-release | head -n1 | tr -d '\"'); fi; "
        "printf 'os=%s\\n' \"$os\"; "
        + " ".join(
            f"p=$(command -v {shlex.quote(name)} 2>/dev/null || true); printf '{name}=%s\\n' \"$p\";"
            for name in tool_names
        )
    )


def proot_guest_probe(distro: str, *, executable: str | None = None, timeout: int = 20) -> ProotGuestProbe:
    distro = str(distro or "").strip()
    command = executable or proot_distro_available()
    if not distro:
        return ProotGuestProbe("", bool(command), False, False, reason="no distro selected")
    if not command:
        return ProotGuestProbe(distro, False, False, False, reason="proot-distro executable not found")
    distros = installed_distros(command)
    if distros is None:
        return ProotGuestProbe(distro, True, False, False, reason="installed-container discovery failed")
    if distro not in distros:
        return ProotGuestProbe(distro, True, False, False, reason=f"container is not installed; installed: {', '.join(distros) or 'none'}")

    tool_names = ("python3", "java", "gradle", "cmake", "ninja", "clang", "git")
    script = _proot_guest_probe_script(tool_names)
    try:
        result = subprocess.run(
            [command, "login", distro, "--", "/bin/sh", "-lc", script],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ProotGuestProbe(distro, True, True, False, reason=f"guest probe failed: {exc}")
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return ProotGuestProbe(distro, True, True, False, reason=(detail[-1] if detail else f"guest probe exited {result.returncode}"))
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return ProotGuestProbe(
        distro,
        True,
        True,
        True,
        os_release=values.get("os", ""),
        tools=tuple((name, values.get(name, "")) for name in tool_names),
    )


def proot_guest_doctor_rows(distro: str, *, executable: str | None = None) -> list[tuple[str, str]]:
    probe = proot_guest_probe(distro, executable=executable)
    if not distro:
        return [("proot_guest", "not requested")]
    if not probe.reachable:
        return [("proot_guest", f"UNAVAILABLE: {probe.reason}")]
    rows = [
        ("proot_guest", f"READY: {probe.distro}"),
        ("proot_guest_os", probe.os_release or "unknown"),
    ]
    rows.extend((f"proot_guest_{name}", path or "MISSING") for name, path in probe.tools)
    return rows


def termux_integration_doctor_rows(
    config: ProjectConfig,
    *,
    env: Mapping[str, str] | None = None,
    explain: bool = False,
    proot_distro: str = "",
) -> list[tuple[str, str]]:
    env = env or os.environ
    runtime = detect_runtime_identity(env)
    if not runtime.is_termux:
        return [("termux_runtime", f"not native Termux ({runtime.platform}/{runtime.environment}/{runtime.libc}/{runtime.arch})")]

    prefix = runtime.prefix or Path(str(env.get("PREFIX") or ""))
    wrapper = gradle_wrapper_probe(Path(config.project_root or config.repo_root))
    gradle = termux_gradle_probe(env=env, runtime=runtime)
    java = termux_java_providers(env=env, runtime=runtime)
    native = termux_native_provider(env=env, runtime=runtime)
    cmake = termux_cmake_probe(env=env, runtime=runtime)
    ninja = termux_ninja_probe(env=env, runtime=runtime)
    ccache = termux_ccache_probe(env=env, runtime=runtime)
    android_tools = termux_android_tool_providers(env=env, runtime=runtime)
    options = _dict_option(config, "termux")
    storage = options.get("storage", {}) if isinstance(options.get("storage"), dict) else {}
    artifacts = options.get("artifacts", {}) if isinstance(options.get("artifacts"), dict) else {}
    profile_name = str(options.get("resource_profile") or "balanced")
    try:
        profile = select_termux_resource_profile(profile_name)
        profile_detail = f"{profile.name}: workers={profile.max_workers}, heap={profile.gradle_heap_mb}MiB, native_jobs={profile.native_jobs}"
    except ValueError as exc:
        profile_detail = f"INVALID: {exc}"
    package_manager = shutil.which("dpkg-query", path=str(env.get("PATH") or ""))
    packages = termux_installed_packages("openjdk-*", env=env, runtime=runtime) if package_manager else ()
    distro = proot_distro or str(_dict_option(config, "proot").get("distro") or "")

    rows: list[tuple[str, str]] = [
        ("termux_runtime", f"READY: Android/Bionic {runtime.arch}; API {runtime.android_api or 'unknown'}; PREFIX={prefix}"),
        ("termux_package_manager", f"READY: {package_manager}" if package_manager else "MISSING: dpkg-query"),
        ("termux_openjdk_packages", ", ".join(f"{item.name}={item.version}" for item in packages) or "none detected"),
        ("gradle_wrapper", "READY: complete" if wrapper.complete else ("INCOMPLETE: " + ", ".join(wrapper.missing) if wrapper.present else "absent")),
        ("termux_gradle", _provider_status(gradle)),
        ("termux_java", _java_status(java)),
        ("termux_native", _provider_status(native)),
        ("termux_cmake", _provider_status(cmake)),
        ("termux_ninja", _provider_status(ninja)),
        ("termux_ccache", _provider_status(ccache)),
        ("termux_android_tools", ", ".join(f"{item.capability}={'READY' if item.compatible else 'UNUSABLE' if item.available else 'MISSING'}" for item in android_tools)),
        *[(f"termux_android_tool_{item.capability}", _provider_status(item)) for item in android_tools],
        ("termux_cache", str(termux_cache_root(env))),
        ("termux_repo_storage", "shared" if is_android_shared_storage(config.repo_root) else "private"),
        ("termux_shared_storage_policy", str(storage.get("shared_storage_policy") or "warn")),
        ("termux_artifact_export", str(artifacts.get("export_dir") or "disabled")),
        ("termux_resource_profile", profile_detail),
        ("termux_wake_lock", "READY" if shutil.which("termux-wake-lock") and shutil.which("termux-wake-unlock") else "MISSING"),
        ("tmux", "READY" if tmux_available() else "MISSING"),
        ("ssh", f"READY: {shutil.which('ssh', path=str(env.get('PATH') or ''))}" if shutil.which('ssh', path=str(env.get('PATH') or '')) else "MISSING"),
        ("chrootctl", f"READY: {shutil.which('chrootctl', path=str(env.get('PATH') or ''))}" if shutil.which('chrootctl', path=str(env.get('PATH') or '')) else "MISSING"),
        ("proot_distro", f"READY: {proot_distro_available()}" if proot_distro_available() else "MISSING"),
    ]
    rows.extend(_android_termux_doctor_rows(config))
    distros = installed_distros(proot_distro_available()) if proot_distro_available() else ()
    rows.append(("proot_containers", "discovery failed" if distros is None else (", ".join(distros) if distros else "none")))
    if distro:
        rows.extend(proot_guest_doctor_rows(distro))
    rows.extend(package_recommendation_rows(config, env=env))
    if explain:
        rows.extend(provider_explanation_rows(config, env=env))
    return rows


def termux_report_payload(
    config: ProjectConfig,
    *,
    env: Mapping[str, str] | None = None,
    explain: bool = False,
    proot_distro: str = "",
) -> dict:
    rows = termux_integration_doctor_rows(config, env=env, explain=explain, proot_distro=proot_distro)
    recommendations = termux_package_recommendations(config, env=env)
    return {
        "rows": [{"name": name, "value": value} for name, value in rows],
        "package_recommendations": [asdict(item) for item in recommendations],
    }


def format_package_recommendations_json(config: ProjectConfig, *, env: Mapping[str, str] | None = None) -> str:
    return json.dumps([asdict(item) for item in termux_package_recommendations(config, env=env)], indent=2, ensure_ascii=False)
