from __future__ import annotations

import os
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from .models import CommandSpec, ProjectConfig

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
    return [
        ("toolchain", manager),
        ("mise", mise or "MISSING"),
        ("mise_marker", "yes" if marker else "no"),
        ("toolchain_active", "yes" if active else "no"),
    ]
