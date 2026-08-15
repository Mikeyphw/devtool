from __future__ import annotations

import os
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from .models import CommandSpec, ProjectConfig
from .termux_toolchains import (
    detect_runtime_identity,
    termux_gradle_probe,
    termux_native_provider,
    termux_cmake_probe,
    termux_ninja_probe,
    termux_ccache_probe,
    termux_android_tool_providers,
    termux_installed_packages,
    termux_java_providers,
    termux_package_probe,
)

VALID_TOOLCHAIN_MANAGERS = {"auto", "mise", "none"}
_MISE_MARKERS = ("mise.toml", ".mise.toml", ".tool-versions")
_SKIP_EXECUTABLES = {"git", "sudo", "su"}
_SKIP_NAMES = {"git-add", "git-commit", "tag", "rollback"}


def normalize_toolchain_manager(value: str | None) -> str:
    normalized = (value or "auto").strip().lower() or "auto"
    if normalized in {"off", "false", "disabled", "disable"}:
        normalized = "none"
    if normalized not in VALID_TOOLCHAIN_MANAGERS:
        valid = ", ".join(sorted(VALID_TOOLCHAIN_MANAGERS))
        raise ValueError(f"Unsupported toolchain manager: {value}. Expected one of: {valid}")
    return normalized


def configured_toolchain_manager(
    config: ProjectConfig,
    env: Mapping[str, str] | None = None,
) -> str:
    env = env or os.environ
    override = str(env.get("DEVTOOL_TOOLCHAIN", "")).strip()
    if override:
        return normalize_toolchain_manager(override)
    return normalize_toolchain_manager(config.toolchain_manager)


def has_mise_marker(root: Path, *, stop_at: Path | None = None) -> bool:
    stop = stop_at.resolve() if stop_at is not None else None
    for base in [root, *root.parents]:
        if any((base / marker).is_file() for marker in _MISE_MARKERS):
            return True
        if stop is not None and base.resolve() == stop:
            break
    return False


def should_use_mise(
    config: ProjectConfig,
    *,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    env = env or os.environ
    manager = configured_toolchain_manager(config, env)
    if manager == "none":
        return False
    if shutil.which("mise", path=str(env.get("PATH", ""))) is None:
        return False
    if manager == "mise":
        return True
    root = Path(cwd or config.project_root or config.repo_root).expanduser()
    if not root.is_absolute():
        root = config.repo_root / root
    return has_mise_marker(root.resolve(), stop_at=config.repo_root)


def should_wrap_spec_with_mise(spec: CommandSpec) -> bool:
    if not spec.argv or spec.destructive:
        return False
    if spec.name in _SKIP_NAMES:
        return False
    executable = Path(str(spec.argv[0])).name
    if executable in _SKIP_EXECUTABLES or executable == "mise":
        return False
    return True


def wrap_spec_with_mise(spec: CommandSpec) -> CommandSpec:
    if not should_wrap_spec_with_mise(spec):
        return spec
    metadata = dict(spec.metadata)
    metadata.setdefault("progress_argv", list(spec.argv))
    if spec.cwd:
        metadata.setdefault("progress_cwd", spec.cwd)
    metadata["toolchain_manager"] = "mise"
    metadata["toolchain_wrapper"] = "mise exec"
    return replace(spec, argv=["mise", "exec", "--", *spec.argv], metadata=metadata)


def apply_toolchain_wrapper(
    config: ProjectConfig,
    specs: list[CommandSpec],
    *,
    env: Mapping[str, str] | None = None,
) -> list[CommandSpec]:
    wrapped: list[CommandSpec] = []
    for spec in specs:
        cwd = spec.cwd or config.project_root or str(config.repo_root)
        if should_use_mise(config, cwd=cwd, env=env) and should_wrap_spec_with_mise(spec):
            wrapped.append(wrap_spec_with_mise(spec))
            continue
        metadata = dict(spec.metadata)
        metadata.setdefault("toolchain_manager", configured_toolchain_manager(config, env))
        wrapped.append(replace(spec, metadata=metadata))
    return wrapped


def toolchain_doctor_rows(
    config: ProjectConfig,
    env: Mapping[str, str] | None = None,
) -> list[tuple[str, str]]:
    env = env or os.environ
    manager = configured_toolchain_manager(config, env)
    mise = shutil.which("mise", path=str(env.get("PATH", "")))
    root = Path(config.project_root or config.repo_root)
    if not root.is_absolute():
        root = config.repo_root / root
    marker = has_mise_marker(root.resolve(), stop_at=config.repo_root)
    active = should_use_mise(config, cwd=str(root), env=env)
    rows = [
        ("toolchain", manager),
        ("mise", mise or "MISSING"),
        ("mise_marker", "yes" if marker else "no"),
        ("toolchain_active", "yes" if active else "no"),
    ]
    runtime = detect_runtime_identity(env)
    if runtime.is_termux:
        rows.extend([
            ("runtime_environment", "termux"),
            ("termux_prefix", str(runtime.prefix or "")),
            ("runtime_abi", f"android/{runtime.libc}/{runtime.arch}"),
            ("android_api", str(runtime.android_api) if runtime.android_api is not None else "unknown"),
        ])
        gradle_package = termux_package_probe("gradle", env=env, runtime=runtime)
        gradle_provider = termux_gradle_probe(env=env, runtime=runtime)
        rows.append((
            "termux_package_gradle",
            gradle_package.version if gradle_package.installed else "MISSING",
        ))
        rows.append((
            "termux_gradle_provider",
            (
                f"{gradle_provider.version} @ {gradle_provider.path}"
                if gradle_provider.available and gradle_provider.compatible and gradle_provider.path
                else f"UNUSABLE ({gradle_provider.rejection_reason or gradle_provider.reason})"
                if gradle_provider.available
                else f"MISSING ({gradle_provider.reason or 'not detected'})"
            ),
        ))
        jdks = [
            f"{package.name}={package.version or 'installed'}"
            for package in termux_installed_packages("openjdk-*", env=env, runtime=runtime)
        ]
        rows.append(("termux_package_java", ", ".join(jdks) if jdks else "MISSING"))
        java_providers = termux_java_providers(env=env, runtime=runtime)
        rows.append((
            "termux_java_provider",
            ", ".join(
                (
                    f"{provider.version} @ {provider.path}"
                    if provider.compatible
                    else f"UNUSABLE {provider.path} ({provider.rejection_reason or provider.reason})"
                )
                for provider in java_providers
                if provider.path
            ) or "MISSING",
        ))
        native = termux_native_provider(env=env, runtime=runtime)
        rows.append((
            "termux_native_provider",
            f"{native.version} @ {native.path}" if native.compatible and native.path
            else f"UNUSABLE ({native.rejection_reason or native.reason})" if native.available
            else f"MISSING ({native.reason or 'not detected'})",
        ))
        for label, probe in (
            ("termux_cmake_provider", termux_cmake_probe(env=env, runtime=runtime)),
            ("termux_ninja_provider", termux_ninja_probe(env=env, runtime=runtime)),
            ("termux_ccache_provider", termux_ccache_probe(env=env, runtime=runtime)),
        ):
            rows.append((label, f"{probe.version} @ {probe.path}" if probe.compatible and probe.path else f"UNUSABLE ({probe.rejection_reason or probe.reason})" if probe.available else "MISSING"))
        android_tools = termux_android_tool_providers(env=env, runtime=runtime)
        rows.append((
            "termux_android_tools",
            ", ".join(
                f"{provider.capability}={provider.version or ('UNUSABLE' if provider.available else 'MISSING')}"
                for provider in android_tools
            ),
        ))
    return rows
