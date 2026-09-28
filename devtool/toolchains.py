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

VALID_TOOLCHAIN_MANAGERS = {"auto", "mise", "asdf", "pyenv", "none"}
_MISE_MARKERS = ("mise.toml", ".mise.toml", ".tool-versions")
_ASDF_MARKERS = (".tool-versions",)


def _asdf_versions_filename(env: Mapping[str, str]) -> str:
    raw = str(env.get("ASDF_TOOL_VERSIONS_FILENAME", ".tool-versions") or ".tool-versions").strip()
    if raw in {"", ".", ".."} or "/" in raw or "\\" in raw:
        raise SystemExit("ASDF_TOOL_VERSIONS_FILENAME must be a filename, not a path")
    return raw
_PYENV_MARKERS = (".python-version", ".pyenv-version")
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
    env = os.environ if env is None else env
    override = str(env.get("DEVTOOL_TOOLCHAIN", "")).strip()
    if override:
        return normalize_toolchain_manager(override)
    return normalize_toolchain_manager(config.toolchain_manager)


def has_mise_marker(
    root: Path, *, stop_at: Path | None = None, include_shared_tool_versions: bool = True
) -> bool:
    stop = stop_at.resolve() if stop_at is not None else None
    markers = _MISE_MARKERS if include_shared_tool_versions else ("mise.toml", ".mise.toml")
    for base in [root, *root.parents]:
        if any((base / marker).is_file() for marker in markers):
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
    return selected_toolchain_manager(config, cwd=cwd, env=env) == "mise"


def has_asdf_marker(root: Path, *, stop_at: Path | None = None, filename: str = ".tool-versions") -> bool:
    stop = stop_at.resolve() if stop_at is not None else None
    for base in [root, *root.parents]:
        if (base / filename).is_file():
            return True
        if stop is not None and base.resolve() == stop:
            break
    return False


def has_pyenv_marker(root: Path, *, stop_at: Path | None = None) -> bool:
    stop = stop_at.resolve() if stop_at is not None else None
    for base in [root, *root.parents]:
        if any((base / marker).is_file() for marker in _PYENV_MARKERS):
            return True
        if stop is not None and base.resolve() == stop:
            break
    return False


def selected_toolchain_manager(
    config: ProjectConfig, *, cwd: str | None = None, env: Mapping[str, str] | None = None, destination: bool = False
) -> str:
    environment = os.environ if env is None else env
    manager = configured_toolchain_manager(config, environment)
    if manager == "none":
        return "none"
    root = Path(cwd or config.project_root or config.repo_root).expanduser()
    if not root.is_absolute():
        root = config.repo_root / root
    root = root.resolve(strict=False)
    if manager in {"mise", "asdf", "pyenv"}:
        if manager == "pyenv" and str(config.kind or config.runner or "").strip().lower() != "python":
            raise SystemExit("Toolchain manager 'pyenv' is Python-specific and cannot own a non-Python target.")
        if destination:
            return manager
        if shutil.which(manager, path=str(environment.get("PATH", ""))) is None:
            raise SystemExit(f"Toolchain manager {manager!r} was explicitly requested, but the {manager} executable is unavailable.")
        return manager
    # Auto mode has deterministic ownership. Dedicated mise configuration wins;
    # the shared .tool-versions surface belongs to asdf when asdf is available;
    # Python's .python-version selects pyenv only for Python targets.  A shared
    # marker is never silently reinterpreted as mise ownership.
    if has_mise_marker(root, stop_at=config.repo_root, include_shared_tool_versions=False):
        if destination or shutil.which("mise", path=str(environment.get("PATH", ""))):
            return "mise"
        return "none"
    asdf_filename = _asdf_versions_filename(environment)
    if has_asdf_marker(root, stop_at=config.repo_root, filename=asdf_filename):
        if destination or shutil.which("asdf", path=str(environment.get("PATH", ""))):
            return "asdf"
    if str(config.kind or config.runner or "").strip().lower() == "python":
        raw_pyenv_dir = str(environment.get("PYENV_DIR", "") or "").strip()
        pyenv_root = root
        pyenv_stop: Path | None = config.repo_root
        if raw_pyenv_dir:
            candidate = Path(raw_pyenv_dir).expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            pyenv_root = candidate.resolve(strict=False)
            try:
                pyenv_root.relative_to(config.repo_root.resolve())
            except ValueError:
                pyenv_stop = None
        if has_pyenv_marker(pyenv_root, stop_at=pyenv_stop):
            if destination or shutil.which("pyenv", path=str(environment.get("PATH", ""))):
                return "pyenv"
    return "none"


def should_use_asdf(
    config: ProjectConfig, *, cwd: str | None = None, env: Mapping[str, str] | None = None
) -> bool:
    return selected_toolchain_manager(config, cwd=cwd, env=env) == "asdf"


def should_use_pyenv(
    config: ProjectConfig, *, cwd: str | None = None, env: Mapping[str, str] | None = None
) -> bool:
    return selected_toolchain_manager(config, cwd=cwd, env=env) == "pyenv"


def should_wrap_spec_with_mise(spec: CommandSpec) -> bool:
    if not spec.argv:
        return False
    if spec.name in _SKIP_NAMES:
        return False
    executable = Path(str(spec.argv[0])).name
    if executable in _SKIP_EXECUTABLES or executable in {"mise", "asdf", "pyenv"}:
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


def wrap_spec_with_asdf(spec: CommandSpec) -> CommandSpec:
    if not should_wrap_spec_with_mise(spec):
        return spec
    metadata = dict(spec.metadata)
    metadata.setdefault("progress_argv", list(spec.argv))
    if spec.cwd:
        metadata.setdefault("progress_cwd", spec.cwd)
    metadata["toolchain_manager"] = "asdf"
    metadata["toolchain_wrapper"] = "asdf exec"
    return replace(spec, argv=["asdf", "exec", *spec.argv], metadata=metadata)


def wrap_spec_with_pyenv(spec: CommandSpec) -> CommandSpec:
    if not should_wrap_spec_with_mise(spec):
        return spec
    metadata = dict(spec.metadata)
    metadata.setdefault("progress_argv", list(spec.argv))
    if spec.cwd:
        metadata.setdefault("progress_cwd", spec.cwd)
    metadata["toolchain_manager"] = "pyenv"
    metadata["toolchain_wrapper"] = "pyenv exec"
    return replace(spec, argv=["pyenv", "exec", *spec.argv], metadata=metadata)


def _language_provider(config: ProjectConfig, *, cwd: str) -> str:
    kind = str(config.kind or config.runner or "").strip().lower()
    if kind == "go":
        return "go"
    if kind != "python":
        return ""
    try:
        from .python_support import inspect_python_project
        options = config.runner_options.get("python", {})
        options = dict(options) if isinstance(options, dict) else {}
        return inspect_python_project(
            Path(cwd).resolve(), str(options.get("manager") or "auto"), str(options.get("test_framework") or "auto")
        ).manager
    except Exception:
        # PythonRunner already owns fail-closed project inspection. Composition
        # must not invent a competing error path after specs have been planned.
        return "python"


def _composition_for_spec(
    config: ProjectConfig,
    spec: CommandSpec,
    *,
    env: Mapping[str, str] | None,
    destination: bool,
):
    from .environment_composition import build_environment_composition_plan

    base_env = dict(os.environ if env is None else env)
    base_env.update({str(k): str(v) for k, v in config.env.items()})
    base_env.update({str(k): str(v) for k, v in spec.env.items()})
    cwd = spec.cwd or config.project_root or str(config.repo_root)
    provider = selected_toolchain_manager(config, cwd=cwd, env=base_env, destination=destination)
    return build_environment_composition_plan(
        Path(cwd), use_mise=provider == "mise", use_asdf=provider == "asdf", use_pyenv=provider == "pyenv",
        language_provider=_language_provider(config, cwd=cwd), env=base_env,
        declared_overlay=config.env, destination=destination,
    )


def apply_toolchain_wrapper(
    config: ProjectConfig,
    specs: list[CommandSpec],
    *,
    env: Mapping[str, str] | None = None,
) -> list[CommandSpec]:
    from .environment_composition import apply_composition_to_spec

    wrapped: list[CommandSpec] = []
    compose = str(config.kind or config.runner or "").strip().lower() in {"python", "go"}
    for spec in specs:
        if compose and should_wrap_spec_with_mise(spec):
            plan = _composition_for_spec(config, spec, env=env, destination=False)
            effective_env = dict(os.environ if env is None else env)
            effective_env.update({str(k): str(v) for k, v in config.env.items()})
            effective_env.update({str(k): str(v) for k, v in spec.env.items()})
            item = apply_composition_to_spec(spec, plan, verification_env=effective_env)
            metadata = dict(item.metadata)
            metadata.setdefault("toolchain_manager", configured_toolchain_manager(config, env))
            wrapped.append(replace(item, metadata=metadata))
            continue
        cwd = spec.cwd or config.project_root or str(config.repo_root)
        provider = selected_toolchain_manager(config, cwd=cwd, env=env)
        if provider == "mise" and should_wrap_spec_with_mise(spec):
            wrapped.append(wrap_spec_with_mise(spec))
            continue
        if provider == "asdf" and should_wrap_spec_with_mise(spec):
            wrapped.append(wrap_spec_with_asdf(spec))
            continue
        if provider == "pyenv" and should_wrap_spec_with_mise(spec):
            wrapped.append(wrap_spec_with_pyenv(spec))
            continue
        metadata = dict(spec.metadata)
        metadata.setdefault("toolchain_manager", configured_toolchain_manager(config, env))
        wrapped.append(replace(spec, metadata=metadata))
    return wrapped


def apply_destination_toolchain_wrapper(
    config: ProjectConfig,
    specs: list[CommandSpec],
    *,
    env: Mapping[str, str] | None = None,
) -> list[CommandSpec]:
    """Apply the selected toolchain/environment envelope for a destination backend."""
    from .environment_composition import apply_composition_to_spec

    env = os.environ if env is None else env
    manager = configured_toolchain_manager(config, env)
    wrapped: list[CommandSpec] = []
    compose = str(config.kind or config.runner or "").strip().lower() in {"python", "go"}
    for spec in specs:
        if compose and should_wrap_spec_with_mise(spec):
            plan = _composition_for_spec(config, spec, env=env, destination=True)
            effective_env = dict(env)
            effective_env.update({str(k): str(v) for k, v in config.env.items()})
            effective_env.update({str(k): str(v) for k, v in spec.env.items()})
            item = apply_composition_to_spec(spec, plan, verification_env=effective_env)
            metadata = dict(item.metadata)
            metadata.setdefault("toolchain_manager", manager)
            metadata["toolchain_resolution"] = "destination"
            wrapped.append(replace(item, metadata=metadata))
            continue
        cwd = spec.cwd or config.project_root or str(config.repo_root)
        provider = selected_toolchain_manager(config, cwd=cwd, env=env, destination=True)
        if provider in {"mise", "asdf", "pyenv"} and should_wrap_spec_with_mise(spec):
            item = (
                wrap_spec_with_mise(spec) if provider == "mise"
                else wrap_spec_with_asdf(spec) if provider == "asdf"
                else wrap_spec_with_pyenv(spec)
            )
            metadata = dict(item.metadata)
            metadata["toolchain_resolution"] = "destination"
            wrapped.append(replace(item, metadata=metadata))
            continue
        metadata = dict(spec.metadata)
        metadata.setdefault("toolchain_manager", manager)
        metadata.setdefault("toolchain_resolution", "destination")
        wrapped.append(replace(spec, metadata=metadata))
    return wrapped

def toolchain_doctor_rows(
    config: ProjectConfig,
    env: Mapping[str, str] | None = None,
) -> list[tuple[str, str]]:
    env = os.environ if env is None else env
    manager = configured_toolchain_manager(config, env)
    mise = shutil.which("mise", path=str(env.get("PATH", "")))
    asdf = shutil.which("asdf", path=str(env.get("PATH", "")))
    pyenv = shutil.which("pyenv", path=str(env.get("PATH", "")))
    root = Path(config.project_root or config.repo_root)
    if not root.is_absolute():
        root = config.repo_root / root
    marker = has_mise_marker(root.resolve(), stop_at=config.repo_root)
    active_error = ""
    asdf_marker = has_asdf_marker(root.resolve(), stop_at=config.repo_root)
    pyenv_marker = has_pyenv_marker(root.resolve(), stop_at=config.repo_root)
    try:
        selected = selected_toolchain_manager(config, cwd=str(root), env=env)
    except SystemExit as exc:
        selected = f"error: {exc}"
    rows = [
        ("toolchain", manager),
        ("mise", mise or "MISSING"),
        ("mise_marker", "yes" if marker else "no"),
        ("asdf", asdf or "MISSING"),
        ("asdf_marker", "yes" if asdf_marker else "no"),
        ("pyenv", pyenv or "MISSING"),
        ("pyenv_marker", "yes" if pyenv_marker else "no"),
        ("toolchain_selected", selected),
        ("toolchain_active", f"error: {active_error}" if active_error else ("yes" if selected not in {"none", "auto"} and not str(selected).startswith("error:") else "no")),
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
