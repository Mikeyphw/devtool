from __future__ import annotations

import copy
import difflib
import os
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from .models import DotnetTarget, JobConfig, ProjectConfig, TargetConfig
from .workspace import WorkspaceConfigError, load_workspace_config, workspace_as_dict
from .workflow_config import (
    job_as_dict,
    load_jobs,
    load_target_workflows,
    normalize_platform_name,
    normalize_runner_name,
    workflow_as_dict,
    workflow_steps_as_strings,
)
from .toolchains import normalize_toolchain_manager
from .execution_environments import normalize_execution_environment
from .features import FEATURE_DEFAULTS, FeatureFlags, resolve_feature_flags
from .ui_core.preferences import UIPreferencesError, resolve_ui_preferences, validate_ui_config

CONFIG_NAME = ".devtool.toml"

GLOBAL_CONFIG_NAME = "config.toml"
GLOBAL_SAFE_TOP_LEVEL_SECTIONS = {
    "cli", "environment", "termux_chroot", "termux", "proot", "android", "gradle", "dotnet", "terminal",
    "bootstrap", "chroots", "features", "ui",
}
GLOBAL_REPO_SCOPED_SECTIONS = {
    "project", "workspace", "targets", "jobs", "tasks", "artifacts", "python", "node", "rust",
    "go", "maven", "cmake", "meson", "make", "test_profiles", "variants",
}
GLOBAL_COMMAND_SCOPED_CLI_KEYS = {
    ("validation", "skip_restore"), ("validation", "skip_build"), ("validation", "skip_test"),
    ("validation", "watch"), ("validation", "matrix"), ("validation", "test_profile"),
    ("validation", "select"), ("validation", "id"), ("validation", "tag"),
    ("validation", "exclude_tag"), ("validation", "path"), ("validation", "class"),
    ("validation", "method"), ("validation", "filter"), ("validation", "task"),
    ("validation", "pytest_test"), ("validation", "pytest_marker"),
    ("validation", "pytest_k"), ("validation", "pytest_arg"),
    ("validation", "failed"), ("validation", "changed"),
}


def global_config_path() -> Path:
    raw = os.environ.get("DEVTOOL_GLOBAL_CONFIG", "").strip()
    if raw:
        return Path(raw).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    candidate = Path(xdg).expanduser() if xdg else None
    root = candidate.resolve(strict=False) if candidate is not None and candidate.is_absolute() else Path.home() / ".config"
    return root / "devtool" / GLOBAL_CONFIG_NAME


def load_global_config(path: str | Path | None = None) -> tuple[dict[str, Any], Path]:
    config_path = Path(path).expanduser() if path is not None else global_config_path()
    if not config_path.exists():
        return {}, config_path
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"Invalid TOML in global config {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        return {}, config_path
    return data, config_path


def _deep_merge_defaults(defaults: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Merge defaults while preserving explicit empty values as overrides."""
    merged = dict(defaults)
    for key, value in values.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict) and value:
            merged[key] = _deep_merge_defaults(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _require_table(data: dict[str, Any], key: str, *, source: Path) -> dict[str, Any]:
    value = data.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SystemExit(f"Invalid config {source}: [{key}] must be a TOML table, got {type(value).__name__}.")
    return value


_EXPLICIT_EMPTY = object()


def _declared_value(mapping: dict[str, Any], *keys: str) -> Any:
    """Return a config value while preserving an explicitly declared empty string."""
    for key in keys:
        if key not in mapping:
            continue
        value = mapping[key]
        if isinstance(value, str) and value.strip() == "":
            return _EXPLICIT_EMPTY
        return value
    return None


def _target_declared_value(target: DotnetTarget | None, field: str, value: Any) -> Any:
    if target is None or field not in target.declared_fields:
        return None
    if isinstance(value, str) and value.strip() == "":
        return _EXPLICIT_EMPTY
    return value


def _resolve_with_provenance(
    *candidates: tuple[str, Any],
    default: Any = "",
    clear_value: Any | None = None,
) -> tuple[Any, str]:
    for source, value in candidates:
        if value is _EXPLICIT_EMPTY:
            return clear_value, source
        if value is not None:
            if isinstance(value, str):
                if value.strip() == "":
                    continue
                return value, source
            return value, source
    return default, "default"

def _global_project_defaults(global_config: dict[str, Any]) -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    environment = global_config.get("environment", {})
    if isinstance(environment, dict):
        default_chroot = str(environment.get("default_chroot", "") or "").strip()
        if default_chroot:
            defaults.setdefault("execution", {})["chroot_name"] = default_chroot
    for section in ("android", "gradle", "dotnet", "bootstrap", "chroots", "termux", "proot"):
        value = global_config.get(section, {})
        if isinstance(value, dict) and value:
            defaults[section] = dict(value)
    termux = global_config.get("termux_chroot", {})
    if isinstance(termux, dict) and termux:
        filtered = {str(k): v for k, v in termux.items() if k in {"project_kinds", "default_chroot", "mode"}}
        if filtered:
            defaults["termux_chroot"] = filtered
            default_chroot = str(filtered.get("default_chroot", "") or "").strip()
            if default_chroot:
                defaults.setdefault("execution", {})["chroot_name"] = default_chroot
    return defaults


def minimal_global_config_template() -> str:
    lines = [
        "# ~/.config/devtool/config.toml",
        "# Sparse user preferences only. Omitted keys use built-in defaults.",
        "",
        "[cli.output]",
        "copy = false",
        "copy_via = \"auto\"",
        "theme = \"auto\"",
        "stdout_payload = \"auto\"",
        "",
        "[cli.hud]",
        "auto_hud = \"auto\"",
        "mode = \"desktop\"",
        "",
        "[cli.apply]",
        "validate = true",
        "manifest_check = true",
        "rollback_on_failure = true",
        "apply_notification = true",
        "artifact_script = true",
        "keep_artifact = false",
        "delete_artifact_on_failure = false",
        "ignore_checksum = false",
        "",
        "[cli.environment]",
        "execution_environment = \"auto\"",
        "no_auto_chroot = false",
        "",
        "[environment]",
        "default_chroot = \"androidos\"",
        "",
        "[bootstrap]",
        "downloads = \"auto\" # never | missing | auto | force",
        "network = \"allow\" # deny | allow",
        "fail_on_missing_tools = true",
        "",
        "[termux_chroot]",
        "# Project kinds that auto mode may route through chroot when running on native Termux.",
        "# Repo and CLI settings still win, and --host / --execution-environment native-termux opt out.",
        "project_kinds = [\"android\", \"gradle\"]",
        "default_chroot = \"androidos\"",
        "",
        "[termux]",
        "# Diagnostics/package recommendations are read-only. Android SDK provider bootstrap may install narrowly-scoped Termux prerequisites/repository metadata when bootstrap policy allows it.",
        "wake_lock = \"auto\" # auto | on | off",
        "resource_profile = \"balanced\" # auto | conservative | balanced | performance",
        "",
        "[termux.storage]",
        "shared_storage_policy = \"warn\" # allow | warn | deny",
        "",
        "[termux.artifacts]",
        "# export_dir = \"/sdcard/Download\"",
        "",
        "[proot]",
        "# The guest does not need devtool; its tool providers are discovered inside the guest.",
        "# distro = \"ubuntu\"",
        "user = \"root\"",
        "cwd = \"/workspace/project\"",
        "auto_bind_repo = true",
        "repo_mount = \"/workspace/project\"",
        "shared_home = false",
        "shared_tmp = false",
        "isolated = false",
        "minimal = false",
        "stage_dir = \"~/.cache/devtool/proot-stage\"",
        "stage_mount = \"/run/devtool\"",
        "cache_dir = \"~/.cache/devtool/proot\"",
        "cache_mount = \"/var/cache/devtool\"",
        "bind = []",
        "env = []",
        "",
        "[ui]",
        "profile = \"auto\" # auto | minimal | standard | developer | debug | custom profile",
        "theme = \"auto\" # auto | light | dark",
        "density = \"comfortable\" # dense | compact | comfortable | detailed",
        "icons = \"auto\" # auto | nerd | unicode | ascii | native",
        "motion = \"auto\" # auto | full | reduced",
        "verbosity = \"normal\" # compact | normal | detailed | debug",
        "timestamps = \"relative\" # relative | absolute | off",
        "duration_format = \"human\" # human | precise",
        "confirm_destructive = true",
        "confirm_cancel = false",
        "show_completed_steps = true",
        "show_environment = \"when-relevant\" # never | when-relevant | always",
        "show_commands = \"on-error\" # never | on-error | always",
        "show_diagnostics = \"expanded-on-error\" # collapsed | expanded-on-error | all",
        "",
        "[ui.tui]",
        "density = \"compact\"",
        "sidebar = \"auto\"",
        "mouse = true",
        "",
        "[ui.hud]",
        "density = \"dense\"",
        "max_steps = 6",
        "",
        "[ui.desktop]",
        "density = \"comfortable\"",
        "sidebar = \"expanded\"",
        "",
        "[ui.android]",
        "density = \"compact\"",
        "notification_detail = \"normal\"",
        "",
        "# Custom profiles can inherit built-ins:",
        "# [ui.profiles.focused]",
        "# extends = \"minimal\"",
        "# show_diagnostics = \"expanded-on-error\"",
        "",
        "[terminal]",
        "preferred = \"micro\"",
        "",
        "[dotnet]",
        "stop_after_run = true",
        "",
        "[android]",
        "sdk_provider = \"auto\" # auto | termuxvoid | homuhomu | managed",
        "aapt2_provider = \"auto\"",
        "aapt2_fallback_path = \"/sdcard/Download/aapt2-arm64-v8a.bin\"",
        "",
        "[android.storage]",
        "auto_cleanup = \"safe\" # safe | off",
        "min_free_mb = 4096",
        "target_free_mb = 6144",
        "managed_gradle_retention = 2",
        "manage_gradle_user_cache = false # opt in to pruning old ~/.gradle version caches/build-cache-1",
        "keep_provider_downloads = false",
        "cleanup_after_build = true",
        "",
        "[android.java]",
        "provider = \"auto\" # auto | termux | system",
        "",
        "[android.native]",
        "provider = \"auto\" # auto | termux-sysroot | ndk | system",
        "cache = \"auto\" # auto | ccache | off",
        "",
        "[android.build_tools]",
        "provider = \"auto\" # auto | termux | sdk | commit451 | none",
        "aapt2_provider = \"auto\" # auto | termux | sdk | revanced | none",
        "",
        "[gradle]",
        "provider = \"auto\" # auto | wrapper | termux | system | download",
        "",
    ]
    return "\n".join(lines)


def global_config_doctor_entries(config: dict[str, Any]) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for section in sorted(config):
        if section in GLOBAL_REPO_SCOPED_SECTIONS:
            entries.append((f"{section}", "repo-scoped; keep this in .devtool.toml"))
        elif section not in GLOBAL_SAFE_TOP_LEVEL_SECTIONS:
            entries.append((f"{section}", "unknown global section"))
    cli = config.get("cli", {}) if isinstance(config.get("cli", {}), dict) else {}
    validation = cli.get("validation", {}) if isinstance(cli.get("validation", {}), dict) else {}
    for key in sorted(validation):
        if ("validation", str(key)) in GLOBAL_COMMAND_SCOPED_CLI_KEYS:
            entries.append((f"cli.validation.{key}", "command-specific; prefer a CLI flag or repo profile"))
    session = validation.get("session", {}) if isinstance(validation.get("session", {}), dict) else {}
    for key in sorted(session):
        entries.append((f"cli.validation.session.{key}", "runtime session state; do not store globally"))
    env = cli.get("environment", {}) if isinstance(cli.get("environment", {}), dict) else {}
    if str(env.get("chroot", "") or "").strip():
        entries.append(("cli.environment.chroot", "forces every command into one chroot; prefer environment.default_chroot"))
    ui_value = config.get("ui", {})
    if ui_value is not None and not isinstance(ui_value, dict):
        entries.append(("ui", f"invalid: [ui] must be a table, got {type(ui_value).__name__}"))
    else:
        ui = dict(ui_value or {})
        try:
            validate_ui_config(ui)
            if ui:
                resolved = resolve_ui_preferences(ui, surface="machine")
                entries.append(("ui", f"ok; profile={resolved.profile} density={resolved.density} icons={resolved.icons}"))
        except UIPreferencesError as exc:
            entries.append(("ui", f"invalid: {exc}"))
    return entries
SUPPORTED_PROJECT_KINDS = {
    "auto", "dotnet", "android", "gradle", "python", "node", "rust",
    "go", "maven", "cmake", "meson", "make", "command", "unknown",
}
# MP01 canonical name. Keep SUPPORTED_PROJECT_KINDS for public compatibility.
SUPPORTED_RUNNERS = SUPPORTED_PROJECT_KINDS
RUNNER_OPTION_SECTIONS = (
    "android", "gradle", "python", "dotnet", "node", "rust", "go",
    "maven", "cmake", "meson", "make", "build", "tasks", "artifacts",
    "test_profiles", "validation", "termux_chroot", "termux", "proot", "ssh", "bootstrap", "chroots", "features",
)
PROJECT_ENV_PREFIX = "DEVTOOL_PROJECT_"
RUNTIME_ENV_PREFIX = "DEVTOOL_ENV_"
_IGNORED_DOTNET_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".cache",
    ".idea",
    ".pytest_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "artifacts",
    "bin",
    "build",
    "dist",
    "node_modules",
    "obj",
    "out",
    "target",
    "tmp",
    "temp",
    "vendor",
    "venv",
}


def find_config(repo_root: Path, config_path_arg: str | None) -> Path | None:
    if config_path_arg:
        path = Path(config_path_arg).expanduser().resolve()
        if not path.exists():
            raise SystemExit(f"Explicit config file does not exist: {path}")
        if not path.is_file():
            raise SystemExit(f"Explicit config path is not a file: {path}")
        return path

    for parent in [repo_root, *repo_root.parents]:
        candidate = parent / CONFIG_NAME
        if candidate.exists():
            if not candidate.is_file():
                raise SystemExit(f"Config path is not a file: {candidate}")
            return candidate
    return None

def resolve_repository_root(
    repo_root_arg: str | None,
    config_path_arg: str | None = None,
    *,
    cwd: Path | None = None,
) -> Path:
    """Resolve the repository boundary used for project state.

    An explicit ``--repo`` remains authoritative.  When it is omitted, prefer
    the directory containing an explicitly selected or nearest ``.devtool.toml``
    file, then the nearest Git worktree root, and finally the current directory.
    This keeps durable validation state stable when Devtool is launched from a
    nested project directory.
    """

    if repo_root_arg:
        return Path(repo_root_arg).expanduser().resolve()

    if config_path_arg:
        config_path = Path(config_path_arg).expanduser().resolve()
        if config_path.exists():
            return config_path.parent.resolve()

    start = Path(cwd or Path.cwd()).expanduser().resolve()
    for parent in (start, *start.parents):
        if (parent / CONFIG_NAME).is_file():
            return parent.resolve()

    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is not None and result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).expanduser().resolve()
    return start


def _default_target_name(
    project_section: dict[str, Any],
    targets: dict[str, DotnetTarget],
    *,
    workspace_default: str = "",
    requested: str,
    explicit_solution: str,
    explicit_project: str,
    allow_ambiguous: bool,
) -> str:
    """Choose a named target without silently guessing between alternatives."""

    requested_name = str(requested or "").strip()
    if requested_name:
        return requested_name

    # Explicit solution/project paths are themselves an unambiguous selection
    # and intentionally bypass named-target defaulting.
    if str(explicit_solution or "").strip() or str(explicit_project or "").strip():
        return ""

    configured_default = str(workspace_default or project_section.get("default_target", "") or "").strip()
    if configured_default:
        if configured_default not in targets:
            available = ", ".join(sorted(targets)) or "<none>"
            raise SystemExit(
                f"Configured default target '{configured_default}' does not exist. "
                f"Available targets: {available}"
            )
        return configured_default

    if len(targets) == 1:
        return next(iter(targets))
    if len(targets) > 1:
        if allow_ambiguous:
            return ""
        available = "\n".join(f"  - {name}" for name in sorted(targets))
        raise SystemExit(
            "Multiple targets are configured and none is the default:\n"
            f"{available}\n"
            "Use --target <name> or set [project].default_target."
        )
    return ""


def load_project_config(
    repo_root_arg: str | None,
    config_path_arg: str | None,
    *,
    target_name: str = "",
    variant_name: str = "",
    solution_override: str = "",
    project_override: str = "",
    kind_override: str = "",
    allow_ambiguous_target: bool = False,
) -> ProjectConfig:
    repo_root = resolve_repository_root(repo_root_arg, config_path_arg)
    config_path = find_config(repo_root, config_path_arg)
    config_root = config_path.parent if config_path else repo_root

    loaded: dict[str, Any] = {}
    if config_path:
        try:
            loaded = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise SystemExit(f"Invalid TOML in {config_path}: {exc}") from exc
    repo_loaded = dict(loaded)

    global_loaded, global_path = load_global_config()
    global_defaults = _global_project_defaults(global_loaded)
    if global_defaults:
        loaded = _deep_merge_defaults(global_defaults, loaded)

    global_ui_value = global_loaded.get("ui", {})
    repo_ui_value = repo_loaded.get("ui", {})
    if global_ui_value is not None and not isinstance(global_ui_value, dict):
        raise SystemExit(f"Invalid config {global_path}: [ui] must be a TOML table, got {type(global_ui_value).__name__}.")
    if repo_ui_value is not None and not isinstance(repo_ui_value, dict):
        raise SystemExit(f"Invalid config {repo_source_label}: [ui] must be a TOML table, got {type(repo_ui_value).__name__}.")
    global_ui = dict(global_ui_value or {})
    repo_ui = dict(repo_ui_value or {})
    ui_config = _deep_merge_defaults(global_ui, repo_ui) if global_ui or repo_ui else {}
    try:
        validate_ui_config(ui_config)
    except UIPreferencesError as exc:
        raise SystemExit(f"Invalid UI configuration: {exc}") from exc

    source_label = config_path or global_path
    repo_source_label = config_path or repo_root / CONFIG_NAME
    repo_project_raw = _require_table(repo_loaded, "project", source=repo_source_label)
    repo_execution_section = _require_table(repo_loaded, "execution", source=repo_source_label)
    project_raw = _require_table(loaded, "project", source=source_label)
    workspace_raw = _require_table(loaded, "workspace", source=source_label)
    env_raw = _require_table(loaded, "env", source=source_label)
    execution_section = _require_table(loaded, "execution", source=source_label)
    toolchain_section = _require_table(loaded, "toolchain", source=source_label)
    targets_raw = _require_table(loaded, "targets", source=source_label)
    jobs_raw = _require_table(loaded, "jobs", source=source_label)
    project_section = _apply_project_env_overrides(dict(project_raw))
    env_section = _apply_runtime_env_overrides({str(k): str(v) for k, v in env_raw.items()})
    python_env_override = os.environ.get("DEVTOOL_PROJECT_PYTHON")

    override_kind = normalize_runner_name(kind_override)
    legacy_kind = normalize_runner_name(project_section.get("kind", "auto")) or "auto"
    canonical_runner = normalize_runner_name(project_section.get("runner", ""))
    if canonical_runner and legacy_kind not in {"auto", "unknown"} and canonical_runner != legacy_kind:
        raise SystemExit(
            f"[project] declares conflicting runner={canonical_runner!r} and legacy kind={legacy_kind!r}. "
            "Use runner as the canonical identity or keep both values equal during migration."
        )
    config_runner = canonical_runner or legacy_kind
    kind = override_kind if override_kind and override_kind != "auto" else config_runner
    if kind not in SUPPORTED_RUNNERS:
        raise SystemExit(f"Unsupported project runner: {kind}")
    explicit_dotnet_intent = bool(
        solution_override.strip()
        or project_override.strip()
        or str(project_section.get("solution", "")).strip()
        or str(project_section.get("project", "")).strip()
    )
    kind_details = detect_project_kind_details(repo_root) if kind == "auto" else {"kind": kind, "candidates": (), "root_candidates": (), "reason": "explicit"}
    if kind == "auto":
        detected_kind = str(kind_details["kind"])
        # Apply-time manifests may point at a solution/project that the overlay is
        # about to create. Treat that declaration as authoritative .NET intent
        # instead of requiring the target to exist before extraction.
        if detected_kind == "unknown" and explicit_dotnet_intent:
            detected_kind = "dotnet"
    else:
        detected_kind = kind

    jobs = load_jobs(
        config_root,
        jobs_raw,
        env_section,
        supported_runners=SUPPORTED_RUNNERS,
        runner_option_sections=RUNNER_OPTION_SECTIONS,
    )
    targets = _load_named_targets(config_root, targets_raw, env_section)
    load_target_workflows(targets_raw, targets, jobs, supported_runners=SUPPORTED_RUNNERS)
    try:
        workspace = load_workspace_config(
            workspace_raw,
            targets,
            legacy_default_target=str(project_section.get("default_target", "") or "").strip(),
        )
    except WorkspaceConfigError as exc:
        raise SystemExit(str(exc)) from exc
    selected_target_name = _default_target_name(
        project_section,
        targets,
        workspace_default=workspace.default_target,
        requested=target_name,
        explicit_solution=solution_override,
        explicit_project=project_override,
        allow_ambiguous=allow_ambiguous_target,
    )
    selected_target_hint = targets.get(selected_target_name) if selected_target_name else None
    if selected_target_hint is not None and selected_target_hint.effective_runner:
        detected_kind = selected_target_hint.effective_runner
    if detected_kind == "unknown" and len(kind_details.get("candidates", ())) > 1 and selected_target_hint is None:
        available = ", ".join(kind_details["candidates"])
        named = ", ".join(sorted(targets)) or "<none>"
        raise SystemExit(
            "Mixed repository has no safe default project type "
            f"({available}). Set [project].kind, pass --project-type, or select a typed --target. "
            f"Named targets: {named}."
        )
    selected_variant_name = (variant_name.strip() or str(project_section.get("variant", "")).strip())
    explicit_solution = solution_override.strip()
    explicit_project = project_override.strip()
    config_solution = str(project_section.get("solution", "")).strip()
    config_project = str(project_section.get("project", "")).strip()
    config_run_target = str(project_section.get("run_target", "")).strip()

    resolved = _resolve_targets(
        repo_root,
        config_root,
        detected_kind,
        project_section,
        targets,
        selected_target_name=selected_target_name,
        explicit_solution=explicit_solution,
        explicit_project=explicit_project,
        config_solution=config_solution,
        config_project=config_project,
        config_run_target=config_run_target,
    )

    effective_env = dict(env_section)
    selected_target = targets.get(resolved["selected_target"]) if resolved["selected_target"] else None
    if selected_target is not None:
        effective_env.update(selected_target.env)

    provenance: dict[str, str] = {}
    env_execution = os.environ.get("DEVTOOL_PROJECT_EXECUTION_ENVIRONMENT")
    target_execution_field = "execution_environment"
    if selected_target is not None and "execution_environment" not in selected_target.declared_fields and "environment" in selected_target.declared_fields:
        target_execution_field = "environment"
    execution_raw, provenance["execution_environment"] = _resolve_with_provenance(
        ("target", _target_declared_value(selected_target, target_execution_field, selected_target.execution_environment if selected_target else None)),
        ("execution", _declared_value(repo_execution_section, "environment", "mode")),
        ("environment", env_execution),
        ("project", _declared_value(repo_project_raw, "execution_environment")),
        default="auto",
        clear_value="auto",
    )
    try:
        execution_environment = normalize_execution_environment(str(execution_raw or "auto"))
    except ValueError as exc:
        raise SystemExit(f"Invalid execution environment in {source_label}: {exc}") from exc
    if env_execution is not None and provenance["execution_environment"] == "environment":
        try:
            execution_environment = normalize_execution_environment(str(env_execution))
        except ValueError as exc:
            raise SystemExit(f"Invalid DEVTOOL_PROJECT_EXECUTION_ENVIRONMENT: {exc}") from exc

    termux_chroot_section = _require_table(loaded, "termux_chroot", source=source_label)
    global_termux_section = _require_table(global_loaded, "termux_chroot", source=global_path or Path("<global>"))
    global_environment_section = _require_table(global_loaded, "environment", source=global_path or Path("<global>"))
    global_chroot_default = (
        global_termux_section.get("default_chroot")
        or global_environment_section.get("default_chroot")
    )
    chroot_raw, provenance["chroot_name"] = _resolve_with_provenance(
        ("target", _target_declared_value(selected_target, "chroot_name", selected_target.chroot_name if selected_target else None)),
        ("execution", _declared_value(repo_execution_section, "chroot_name")),
        ("project", _declared_value(repo_project_raw, "chroot_name")),
        ("environment", os.environ.get("DEVTOOL_TERMUX_CHROOT")),
        ("global", global_chroot_default),
        default="androidos",
        clear_value="androidos",
    )
    chroot_name = str(chroot_raw or "androidos").strip()
    device_raw, provenance["device_serial"] = _resolve_with_provenance(
        ("target", _target_declared_value(selected_target, "device_serial", selected_target.device_serial if selected_target else None)),
        ("execution", _declared_value(repo_execution_section, "device_serial")),
        ("project", _declared_value(repo_project_raw, "device_serial")),
        default="",
        clear_value="",
    )
    device_serial = str(device_raw or "").strip()
    try:
        toolchain_manager = normalize_toolchain_manager(
            str(
                toolchain_section.get("manager", "")
                or project_section.get("toolchain", "")
                or "auto"
            )
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    runner_options: dict[str, Any] = {}
    for section_name in RUNNER_OPTION_SECTIONS:
        section_value = loaded.get(section_name, {})
        if isinstance(section_value, dict):
            runner_options[section_name] = dict(section_value)
    if selected_target is not None:
        for key, value in selected_target.options.items():
            runner_options[key] = _merge_option_value(runner_options.get(key), value)
        if selected_variant_name:
            variant_options = selected_target.variants.get(selected_variant_name)
            if isinstance(variant_options, dict):
                for key, value in variant_options.items():
                    if key == "env" and isinstance(value, dict):
                        effective_env.update({str(k): str(v) for k, v in value.items()})
                    else:
                        runner_options[key] = _merge_option_value(runner_options.get(key), value)
    elif selected_variant_name:
        top_variants = loaded.get("variants", {}) if isinstance(loaded.get("variants", {}), dict) else {}
        variant_options = top_variants.get(selected_variant_name) if isinstance(top_variants, dict) else None
        if isinstance(variant_options, dict):
            for key, value in variant_options.items():
                if key == "env" and isinstance(value, dict):
                    effective_env.update({str(k): str(v) for k, v in value.items()})
                elif isinstance(value, dict):
                    runner_options[key] = _merge_option_value(runner_options.get(key), value)

    chroots_options = runner_options.get("chroots", {})
    selected_chroot_profile = (
        chroots_options.get(chroot_name)
        if isinstance(chroots_options, dict) and chroot_name
        else None
    )
    if isinstance(selected_chroot_profile, dict):
        profile_android = selected_chroot_profile.get("android", {})
        if isinstance(profile_android, dict):
            runner_options["android"] = _deep_merge_defaults(
                dict(profile_android),
                dict(runner_options.get("android", {})) if isinstance(runner_options.get("android", {}), dict) else {},
            )
        profile_gradle = selected_chroot_profile.get("gradle", {})
        if isinstance(profile_gradle, dict):
            runner_options["gradle"] = _deep_merge_defaults(
                dict(profile_gradle),
                dict(runner_options.get("gradle", {})) if isinstance(runner_options.get("gradle", {}), dict) else {},
            )
        profile_bootstrap = selected_chroot_profile.get("bootstrap", {})
        if isinstance(profile_bootstrap, dict):
            runner_options["bootstrap"] = _deep_merge_defaults(
                dict(profile_bootstrap),
                dict(runner_options.get("bootstrap", {})) if isinstance(runner_options.get("bootstrap", {}), dict) else {},
            )
        runner_options.setdefault("toolchain_profile", chroot_name)
    project_root = (selected_target.root if selected_target and selected_target.root else "")
    if not project_root:
        configured_root = str(project_section.get("root", "")).strip()
        if configured_root:
            project_root = _resolve_optional_path(config_root, configured_root)
        elif detected_kind in {"android", "gradle"} and resolved["build_target"]:
            # Auto-detection may find a nested build such as app/XDM.Android.
            # Keep repo_root at the repository boundary while sending Gradle to
            # the discovered project root.
            project_root = resolved["build_target"]
        elif detected_kind == "python" and resolved["build_target"]:
            candidate = Path(resolved["build_target"])
            project_root = str(candidate.parent if candidate.is_file() or candidate.name in {"pyproject.toml", "setup.py", "setup.cfg"} else candidate)
        else:
            project_root = str(config_root)

    python_executable = str(project_section.get("python", sys.executable)).strip() or sys.executable
    if python_env_override is not None:
        provenance["python_executable"] = "environment"
    elif "python" in project_raw:
        provenance["python_executable"] = "project"
    else:
        provenance["python_executable"] = "default"

    family, build_system, subtype = project_identity(detected_kind)
    project_platform = normalize_platform_name(
        (selected_target.platform if selected_target and selected_target.platform else (project_section.get("platform") or detected_kind)),
        context="project.platform",
    )
    if selected_target is not None:
        if "runner" in selected_target.declared_fields:
            provenance["runner"] = "target"
        elif {"type", "kind"}.intersection(selected_target.declared_fields):
            provenance["runner"] = "target-legacy-alias"
        else:
            provenance["runner"] = "target-inherited"
        provenance["platform"] = "target" if "platform" in selected_target.declared_fields else "inferred"
    else:
        if canonical_runner:
            provenance["runner"] = "project"
        elif legacy_kind not in {"auto", "unknown"}:
            provenance["runner"] = "project-legacy-kind"
        else:
            provenance["runner"] = "detected"
        provenance["platform"] = "project" if str(project_section.get("platform", "") or "").strip() else "inferred"
    config_result = ProjectConfig(
        repo_root=repo_root,
        kind=detected_kind,
        runner=detected_kind,
        platform=project_platform,
        project_family=family,
        build_system=build_system,
        project_subtype=subtype,
        build_target=resolved["build_target"],
        run_target=resolved["run_target"],
        restore_target=resolved["restore_target"],
        test_target=resolved["test_target"],
        python_executable=python_executable,
        run_module=str(project_section.get("run_module", "")).strip(),
        restore_command=(
            selected_target.restore_command
            if selected_target and selected_target.restore_command
            else str(project_section.get("restore_command", "")).strip()
        ),
        build_command=(
            selected_target.build_command
            if selected_target and selected_target.build_command
            else str(project_section.get("build_command", "")).strip()
        ),
        test_command=(
            selected_target.test_command
            if selected_target and selected_target.test_command
            else str(project_section.get("test_command", "")).strip()
        ),
        package_command=(
            selected_target.package_command
            if selected_target and selected_target.package_command
            else str(project_section.get("package_command", "")).strip()
        ),
        run_command=(
            selected_target.run_command
            if selected_target and selected_target.run_command
            else str(project_section.get("run_command", "")).strip()
        ),
        env=effective_env,
        config_path=config_path,
        selected_target=resolved["selected_target"],
        variant=selected_variant_name,
        targets=targets,
        jobs=jobs,
        workspace=workspace,
        ui_config=ui_config,
        execution_environment=execution_environment,
        chroot_name=chroot_name,
        device_serial=device_serial,
        project_root=project_root,
        toolchain_manager=toolchain_manager,
        runner_options=runner_options,
        global_config_path=global_path if global_loaded else None,
        config_provenance=provenance,
        configured_runner_options=copy.deepcopy(runner_options),
    )

    # MP05 validates the complete configured cross-target workspace graph at
    # config-load time. Local workflow DAG syntax is already validated while
    # parsing; this second pass catches target-to-target cycles before an
    # operation starts rather than failing half-way through execution.
    if any(target.workflows for target in targets.values()):
        from .workflow_runtime import WorkflowRuntimeError, validate_workspace_workflows
        try:
            validate_workspace_workflows(config_result)
        except WorkflowRuntimeError as exc:
            raise SystemExit(str(exc)) from exc
    return config_result


def project_identity(kind: str) -> tuple[str, str, str]:
    """Return execution family, build system, and capability subtype."""
    normalized = str(kind or "unknown").strip().lower()
    mapping = {
        "android": ("jvm", "gradle", "android"),
        "gradle": ("jvm", "gradle", "jvm"),
        "maven": ("jvm", "maven", "jvm"),
        "dotnet": ("dotnet", "dotnet", "managed"),
        "python": ("python", "python", "python"),
        "node": ("node", "node", "javascript"),
        "rust": ("native", "cargo", "rust"),
        "go": ("native", "go", "go"),
        "cmake": ("native", "cmake", "native"),
        "meson": ("native", "meson", "native"),
        "make": ("native", "make", "native"),
        "command": ("command", "custom", "custom"),
    }
    return mapping.get(normalized, (normalized or "unknown", normalized or "unknown", normalized or "unknown"))

def detect_project_kind_details(repo_root: Path) -> dict[str, Any]:
    """Resolve a safe default kind, preferring exactly one root-local marker.

    Nested Android/desktop/demo projects must not steal the default from a
    repository-root Python, Node, or other project. When the root itself is
    ambiguous, or only multiple nested kinds exist, Devtool refuses to guess.
    """

    repo_root = repo_root.expanduser().resolve()
    dotnet_candidates = discover_dotnet_candidates(repo_root)
    android_markers = discover_gradle_roots(repo_root, android_only=True)
    gradle_markers = discover_gradle_roots(repo_root, android_only=False)
    android_marker_set = set(android_markers)
    plain_gradle_markers = [root for root in gradle_markers if root not in android_marker_set]
    from .python_support import discover_python_roots
    python_roots = discover_python_roots(repo_root)
    markers = {
        "dotnet": bool(dotnet_candidates),
        "android": bool(android_markers),
        "gradle": bool(plain_gradle_markers),
        "python": bool(python_roots),
        "node": (repo_root / "package.json").is_file(),
        "rust": (repo_root / "Cargo.toml").is_file(),
        "go": (repo_root / "go.mod").is_file() or (repo_root / "go.work").is_file(),
        "maven": (repo_root / "pom.xml").is_file() or (repo_root / "mvnw").is_file(),
        "cmake": (repo_root / "CMakeLists.txt").is_file(),
        "meson": (repo_root / "meson.build").is_file(),
        "make": any((repo_root / name).is_file() for name in ("Makefile", "makefile", "GNUmakefile")),
    }
    root_markers = {
        "dotnet": any(path.parent.resolve() == repo_root for path in dotnet_candidates),
        "android": repo_root in android_markers,
        "gradle": repo_root in plain_gradle_markers,
        "python": repo_root in python_roots,
        "node": markers["node"],
        "rust": markers["rust"],
        "go": markers["go"],
        "maven": markers["maven"],
        "cmake": markers["cmake"],
        "meson": markers["meson"],
        "make": markers["make"],
    }
    order = (
        "dotnet", "android", "gradle", "python", "node", "rust", "go",
        "maven", "cmake", "meson", "make",
    )
    candidates = tuple(name for name in order if markers[name])
    root_candidates = tuple(name for name in order if root_markers[name])
    if len(root_candidates) == 1:
        kind = root_candidates[0]
        reason = "single repository-root project marker"
    elif len(root_candidates) > 1:
        kind = "unknown"
        reason = "multiple repository-root project markers"
    elif len(candidates) == 1:
        kind = candidates[0]
        reason = "single nested project kind"
    elif len(candidates) > 1:
        kind = "unknown"
        reason = "multiple nested project kinds"
    else:
        kind = "unknown"
        reason = "no supported project markers"
    family, build_system, subtype = project_identity(kind)
    return {
        "kind": kind,
        "family": family,
        "build_system": build_system,
        "subtype": subtype,
        "candidates": candidates,
        "root_candidates": root_candidates,
        "reason": reason,
    }


def detect_project_kinds(repo_root: Path) -> tuple[str, ...]:
    return tuple(detect_project_kind_details(repo_root)["candidates"])


def detect_project_kind(repo_root: Path) -> str:
    details = detect_project_kind_details(repo_root)
    kinds = tuple(details["candidates"])
    selected = str(details["kind"])
    if len(kinds) > 1:
        roots = ", ".join(details["root_candidates"]) or "none"
        verdict = f"using root-local {selected}" if selected != "unknown" else "refusing to guess"
        print(
            f"devtool: mixed project markers ({', '.join(kinds)}) found under {repo_root}; "
            f"root markers: {roots}; {verdict}. "
            "Set [project].kind explicitly or select a typed target.",
            file=sys.stderr,
        )
    return selected


def _gradle_root_is_android(root: Path) -> bool:
    """Return whether *this Gradle root itself* declares Android capability.

    A nested Android build must not turn an unrelated repository-root JVM
    Gradle project into Android.  Module/build discovery is handled separately
    and selected targets can still declare ``type = "android"`` explicitly.
    """

    scripts = (
        root / "build.gradle",
        root / "build.gradle.kts",
        root / "settings.gradle",
        root / "settings.gradle.kts",
    )
    for path in scripts:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "com.android.application" in text or "com.android.library" in text:
            return True
    return (root / "src" / "main" / "AndroidManifest.xml").is_file()

def discover_gradle_roots(repo_root: Path, *, android_only: bool = False) -> list[Path]:
    """Discover top-level Gradle builds anywhere below ``repo_root``.

    A prior implementation only inspected the repository root and its direct
    children, which missed common layouts such as ``app/XDM.Android``.  The
    recursive walk stops descending once it finds a Gradle root so module build
    files are not reported as independent projects.
    """

    candidates: list[Path] = []
    markers = {
        "settings.gradle",
        "settings.gradle.kts",
        "build.gradle",
        "build.gradle.kts",
        "gradlew",
    }
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [
            name for name in dirnames
            if name.lower() not in _IGNORED_DOTNET_DIRS
        ]
        if not markers.intersection(filenames):
            continue

        root = Path(dirpath).resolve()
        is_android = _gradle_root_is_android(root)
        if not android_only or is_android:
            candidates.append(root)

        # A settings/build/wrapper marker establishes this project root.  Its
        # module build files belong to the same Gradle build and must not be
        # returned as separate targets.
        dirnames[:] = []

    return sorted(set(candidates), key=lambda path: str(path))


def discover_dotnet_candidates(repo_root: Path) -> list[Path]:
    """Discover .NET entry points without letting nested files steal root identity."""
    root = repo_root.expanduser().resolve()
    root_solutions = sorted(root.glob("*.sln"))
    if root_solutions:
        return [path.resolve() for path in root_solutions]
    root_projects = sorted(root.glob("*.csproj"))
    if root_projects:
        return [path.resolve() for path in root_projects]
    nested_solutions = _collect_paths_with_suffixes(root, (".sln",))
    if nested_solutions:
        return nested_solutions
    return _collect_paths_with_suffixes(root, (".csproj",))


def _default_build_target(repo_root: Path, kind: str, section: dict[str, Any]) -> str:
    explicit = str(section.get("build_target", "")).strip()
    if explicit:
        return _resolve_path(repo_root, explicit)

    if kind == "dotnet":
        candidates = discover_dotnet_candidates(repo_root)
        if len(candidates) == 1:
            return str(candidates[0].resolve())
        return ""
    if kind in {"android", "gradle"}:
        roots = discover_gradle_roots(repo_root, android_only=(kind == "android"))
        if len(roots) == 1:
            return str(roots[0])
        if any((repo_root / marker).exists() for marker in ("settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts", "gradlew")):
            return str(repo_root.resolve())
        return ""
    if kind == "python":
        from .python_support import discover_python_roots
        roots = discover_python_roots(repo_root)
        if len(roots) == 1:
            root = roots[0]
            if (root / "pyproject.toml").exists():
                return str((root / "pyproject.toml").resolve())
            if (root / "setup.py").exists():
                return str((root / "setup.py").resolve())
            return str(root.resolve())
    marker_by_kind = {
        "node": "package.json",
        "rust": "Cargo.toml",
        "go": "go.mod" if (repo_root / "go.mod").exists() else "go.work",
        "maven": "pom.xml",
        "cmake": "CMakeLists.txt",
        "meson": "meson.build",
        "make": next((name for name in ("Makefile", "makefile", "GNUmakefile") if (repo_root / name).exists()), ""),
    }
    marker = marker_by_kind.get(kind, "")
    if marker and (repo_root / marker).exists():
        return str(repo_root.resolve())
    return ""


def _merge_option_value(current: Any, incoming: Any) -> Any:
    if isinstance(current, dict) and isinstance(incoming, dict):
        merged = dict(current)
        for key, value in incoming.items():
            merged[key] = _merge_option_value(merged.get(key), value)
        return merged
    return incoming


def _load_target_variants(raw_value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_variants = raw_value.get("variants", {})
    if not isinstance(raw_variants, dict):
        return {}
    variants: dict[str, dict[str, Any]] = {}
    for raw_name, raw_options in raw_variants.items():
        if not isinstance(raw_name, str) or not isinstance(raw_options, dict):
            continue
        name = raw_name.strip()
        if not name:
            continue
        options: dict[str, Any] = {}
        for option_name in (*RUNNER_OPTION_SECTIONS, "env"):
            option_value = raw_options.get(option_name, {})
            if isinstance(option_value, dict):
                options[option_name] = dict(option_value)
        variants[name] = options
    return variants


def _normalize_target_execution(value: Any, target_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return normalize_execution_environment(text)
    except ValueError as exc:
        raise SystemExit(f"Invalid execution environment for target {target_name!r}: {exc}") from exc


def _load_named_targets(repo_root: Path, section: Any, base_env: dict[str, str]) -> dict[str, TargetConfig]:
    if not isinstance(section, dict):
        raise SystemExit("[targets] must be a TOML table")

    targets: dict[str, TargetConfig] = {}
    for raw_name, raw_value in section.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, dict):
            raise SystemExit(f"Target {raw_name!r} must be a TOML table")
        name = raw_name.strip()
        if not name:
            continue
        solution = _resolve_optional_path(repo_root, raw_value.get("solution", ""))
        restore_target = _resolve_optional_path(repo_root, raw_value.get("restore_target", ""))
        build_target = _resolve_optional_path(repo_root, raw_value.get("build_target", ""))
        test_target = _resolve_optional_path(repo_root, raw_value.get("test_target", ""))
        run_project = _resolve_optional_path(repo_root, raw_value.get("run_project", ""))
        cwd = _resolve_optional_path(repo_root, raw_value.get("cwd", ""))
        target_root = _resolve_optional_path(repo_root, raw_value.get("root", ""))
        restore_command = str(raw_value.get("restore_command", "")).strip()
        build_command = str(raw_value.get("build_command", "")).strip()
        test_command = str(raw_value.get("test_command", "")).strip()
        package_command = str(raw_value.get("package_command", "")).strip()
        run_command = str(raw_value.get("run_command", "")).strip()
        target_env = dict(base_env)
        for key, value in raw_value.get("env", {}).items() if isinstance(raw_value.get("env", {}), dict) else []:
            target_env[str(key)] = str(value)
        canonical_runner = normalize_runner_name(raw_value.get("runner", ""))
        legacy_runner = normalize_runner_name(raw_value.get("type", raw_value.get("kind", "")))
        if canonical_runner and legacy_runner and canonical_runner != legacy_runner:
            raise SystemExit(
                f"Target '{name}' declares conflicting runner={canonical_runner!r} and legacy type/kind={legacy_runner!r}. "
                "Use one runner identity."
            )
        target_runner = canonical_runner or legacy_runner
        if target_runner and target_runner not in SUPPORTED_RUNNERS:
            raise SystemExit(f"Target '{name}' declares unsupported runner '{target_runner}'.")
        target_platform = normalize_platform_name(
            raw_value.get("platform", target_runner), context=f"targets.{name}.platform"
        )
        metadata_raw = raw_value.get("metadata", {})
        if metadata_raw is not None and not isinstance(metadata_raw, dict):
            raise SystemExit(f"[targets.{name}.metadata] must be a TOML table")
        def target_paths(field: str) -> tuple[str, ...]:
            value = raw_value.get(field)
            if value in (None, ""):
                return ()
            if not isinstance(value, list):
                raise SystemExit(f"[targets.{name}] {field} must be a TOML array of non-empty strings")
            items: list[str] = []
            for index, item in enumerate(value):
                if not isinstance(item, str) or not item.strip():
                    raise SystemExit(f"[targets.{name}] {field}[{index}] must be a non-empty string")
                items.append(item.strip())
            return tuple(items)
        options: dict[str, Any] = {}
        for option_name in RUNNER_OPTION_SECTIONS:
            option_value = raw_value.get(option_name, {})
            if isinstance(option_value, dict):
                options[option_name] = dict(option_value)
        targets[name] = TargetConfig(
            name=name,
            runner=target_runner,
            platform=target_platform,
            kind=target_runner,
            root=target_root,
            solution=solution,
            restore_target=restore_target,
            build_target=build_target,
            test_target=test_target,
            run_project=run_project,
            cwd=cwd,
            restore_command=restore_command,
            build_command=build_command,
            test_command=test_command,
            package_command=package_command,
            run_command=run_command,
            env=target_env,
            execution_environment=_normalize_target_execution(raw_value.get("execution_environment", raw_value.get("environment", "")), name),
            chroot_name=str(raw_value.get("chroot_name", "")).strip(),
            device_serial=str(raw_value.get("device_serial", "")).strip(),
            options=options,
            variants=_load_target_variants(raw_value),
            inputs=target_paths("inputs"),
            outputs=target_paths("outputs"),
            metadata=dict(metadata_raw) if isinstance(metadata_raw, dict) else {},
            declared_fields=frozenset(str(key) for key in raw_value),
        )
    return targets


def _resolve_targets(
    repo_root: Path,
    config_root: Path,
    kind: str,
    project_section: dict[str, Any],
    targets: dict[str, DotnetTarget],
    *,
    selected_target_name: str,
    explicit_solution: str,
    explicit_project: str,
    config_solution: str,
    config_project: str,
    config_run_target: str,
) -> dict[str, str]:
    if selected_target_name:
        target = targets.get(selected_target_name)
        if target is None:
            target_names = sorted(targets)
            available = ", ".join(target_names) or "<none>"
            message = f"Unknown target '{selected_target_name}'. Available targets: {available}"
            matches = difflib.get_close_matches(selected_target_name, target_names, n=1)
            if matches:
                message += f"\nDid you mean '{matches[0]}'?"
            message += "\nRun 'devtool targets' to list configured targets."
            raise SystemExit(message)
        target_kind = target.kind or kind
        if target_kind == "dotnet":
            return {
                "selected_target": target.name,
                "restore_target": target.effective_restore_target(),
                "build_target": target.effective_build_target(),
                "test_target": target.effective_test_target(),
                "run_target": target.effective_run_project(),
            }
        project_root = target.root or target.cwd or _default_build_target(config_root, target_kind, project_section) or str(config_root)
        return {
            "selected_target": target.name,
            "restore_target": target.restore_target or project_root,
            "build_target": target.build_target or project_root,
            "test_target": target.test_target or project_root,
            "run_target": target.run_project or project_root,
        }

    if kind != "dotnet":
        build_target = _default_build_target(config_root, kind, project_section)
        run_target = config_run_target
        if run_target:
            run_target = _resolve_path(config_root, run_target)
        return {
            "selected_target": "",
            "restore_target": build_target,
            "build_target": build_target,
            "test_target": build_target,
            "run_target": run_target or build_target,
        }

    if explicit_solution or explicit_project:
        solution_path = _resolve_optional_path(repo_root, explicit_solution)
        project_path = _resolve_optional_path(repo_root, explicit_project)
        if not solution_path and config_solution:
            solution_path = _resolve_optional_path(config_root, config_solution)
        if not project_path and config_project:
            project_path = _resolve_optional_path(config_root, config_project)
        build_target = solution_path or project_path
        return {
            "selected_target": "",
            "restore_target": solution_path or build_target,
            "build_target": build_target,
            "test_target": solution_path or build_target,
            "run_target": project_path or build_target,
        }

    if config_solution or config_project:
        solution_path = _resolve_optional_path(config_root, config_solution)
        project_path = _resolve_optional_path(config_root, config_project)
        build_target = solution_path or project_path
        return {
            "selected_target": "",
            "restore_target": solution_path or build_target,
            "build_target": build_target,
            "test_target": solution_path or build_target,
            "run_target": project_path or build_target,
        }

    explicit_build = _default_build_target(config_root, kind, project_section)
    explicit_run = config_run_target
    if explicit_build or explicit_run:
        return {
            "selected_target": "",
            "restore_target": explicit_build,
            "build_target": explicit_build,
            "test_target": explicit_build,
            "run_target": _resolve_optional_path(config_root, explicit_run) or explicit_build,
        }

    candidates = discover_dotnet_candidates(config_root)
    if len(candidates) == 1:
        single = str(candidates[0].resolve())
        return {
            "selected_target": "",
            "restore_target": single,
            "build_target": single,
            "test_target": single,
            "run_target": single,
        }

    if len(candidates) > 1:
        displayed = "\n".join(f"  - {candidate.relative_to(repo_root)}" for candidate in candidates[:20])
        raise SystemExit(
            "Multiple dotnet targets found in monorepo. Refusing to guess.\n"
            "Use --target <name>, --solution <path>, or declare the target in the artifact manifest.\n"
            f"Candidates:\n{displayed}"
        )

    return {
        "selected_target": "",
        "restore_target": "",
        "build_target": "",
        "test_target": "",
        "run_target": "",
    }


def _apply_project_env_overrides(section: dict[str, Any]) -> dict[str, Any]:
    for env_name, env_value in os.environ.items():
        if not env_name.startswith(PROJECT_ENV_PREFIX):
            continue
        key = env_name[len(PROJECT_ENV_PREFIX):].strip().lower()
        if key:
            section[key] = env_value
    return section


def _apply_runtime_env_overrides(section: dict[str, str]) -> dict[str, str]:
    for env_name, env_value in os.environ.items():
        if not env_name.startswith(RUNTIME_ENV_PREFIX):
            continue
        key = env_name[len(RUNTIME_ENV_PREFIX):].strip()
        if key:
            section[key] = env_value
    return section


def _detect_node_manager(repo_root: Path) -> str:
    try:
        data = json.loads((repo_root / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    package_manager = str(data.get("packageManager") or "").split("@", 1)[0].strip().lower()
    if package_manager in {"npm", "pnpm", "yarn", "bun"}:
        return package_manager
    for filename, manager in (
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lockb", "bun"),
        ("bun.lock", "bun"),
        ("package-lock.json", "npm"),
    ):
        if (repo_root / filename).exists():
            return manager
    return "auto"


def generate_template(repo_root: Path) -> str:
    repo_root = repo_root.resolve()
    detected_kinds = detect_project_kinds(repo_root)
    kind = detect_project_kind(repo_root)
    template_kind = kind if kind != "unknown" else "auto"
    ambiguity_note = ""
    if len(detected_kinds) > 1:
        ambiguity_note = (
            "# Mixed repository detected: " + ", ".join(detected_kinds) + ".\n"
            "# Replace project.runner=auto or define/select typed targets before running commands.\n"
        )
    build_target = _default_build_target(repo_root, kind, {})
    run_target = ""
    run_module = ""
    target_blocks = ""
    python_manager = "auto"
    python_test_framework = "auto"
    node_manager = "auto"

    if kind == "dotnet":
        candidates = discover_dotnet_candidates(repo_root)
        app_candidates = sorted(
            p for p in _collect_paths_with_suffixes(repo_root, (".csproj",))
            if "test" not in p.name.lower()
        )
        if len(candidates) <= 1:
            if candidates and candidates[0].suffix.lower() == ".sln":
                run_target = _pick_run_project_for_solution(repo_root, candidates[0], app_candidates)
            elif app_candidates:
                run_target = str(app_candidates[0].relative_to(repo_root))
        else:
            named_blocks: list[str] = []
            used_target_names: set[str] = set()
            for candidate in candidates:
                base_name = _target_name_from_path(repo_root, candidate)
                target_name = _unique_target_name(used_target_names, base_name, candidate)
                run_project = _pick_run_project_for_solution(repo_root, candidate, app_candidates)
                named_blocks.append(
                    f"""\n[targets.{target_name}]\nrunner = "dotnet"\nplatform = "desktop"\nsolution = "{candidate.relative_to(repo_root)}"\nrun_project = "{run_project}"\n"""
                )
            target_blocks = '\n' + ''.join(named_blocks)
            build_target = ""
    elif kind in {"android", "gradle"}:
        roots = discover_gradle_roots(repo_root, android_only=(kind == "android"))
        if len(roots) == 1:
            build_target = str(roots[0])
        elif len(roots) > 1:
            named_blocks: list[str] = []
            used_target_names: set[str] = set()
            for candidate in roots:
                base_name = _target_name_from_path(repo_root, candidate / "project.gradle")
                target_name = _unique_target_name(used_target_names, base_name, candidate)
                named_blocks.append(
                    f"\n[targets.{target_name}]\nrunner = \"{kind}\"\nplatform = \"{'android' if kind == 'android' else 'jvm'}\"\nroot = \"{candidate.relative_to(repo_root)}\"\n"
                )
            target_blocks = "\n" + "".join(named_blocks)
            build_target = ""
    elif kind == "python":
        from .python_support import discover_python_roots, inspect_python_project
        roots = discover_python_roots(repo_root)
        if len(roots) > 1 or (roots and roots[0] != repo_root):
            named_blocks: list[str] = []
            used_target_names: set[str] = set()
            for candidate in roots:
                base_name = _target_name_from_path(repo_root, candidate / "pyproject.toml")
                target_name = _unique_target_name(used_target_names, base_name, candidate)
                named_blocks.append(
                    f"\n[targets.{target_name}]\nrunner = \"python\"\nplatform = \"server\"\nroot = \"{candidate.relative_to(repo_root)}\"\n"
                )
            target_blocks = "\n" + "".join(named_blocks)
            build_target = ""
        active_root = roots[0] if len(roots) == 1 else repo_root
        if (active_root / "src").exists():
            for candidate in sorted((active_root / "src").glob("*.py")):
                run_target = str(candidate.relative_to(repo_root))
                break
        if not run_target:
            for candidate in sorted(active_root.glob("*.py")):
                run_target = str(candidate.relative_to(repo_root))
                break
        if not run_module and (active_root / "pyproject.toml").exists():
            try:
                info = inspect_python_project(active_root)
                python_manager = info.manager
                python_test_framework = info.framework
                run_module = info.import_packages[0] if info.import_packages else "your_package"
            except Exception:
                run_module = "your_package"
    elif kind == "node":
        node_manager = _detect_node_manager(repo_root)

    default_platform = {
        "android": "android", "gradle": "jvm", "python": "server", "node": "web",
        "dotnet": "desktop", "rust": "native", "go": "native", "maven": "jvm",
        "cmake": "native", "meson": "native", "make": "native", "command": "custom",
    }.get(template_kind, template_kind if template_kind != "auto" else "")

    return f"""{ambiguity_note}[project]
runner = "{template_kind}" # canonical runner: auto | dotnet | android | gradle | python | node | rust | go | maven | cmake | meson | make | command
platform = "{default_platform}" # product surface; independent from runner (server, web, android, tv, desktop, shared, ...)
# kind = "{template_kind}" # legacy alias for runner; retained for compatibility
build_target = "{_display(repo_root, build_target)}"
run_target = "{run_target}"
variant = ""{target_blocks}
python = "{sys.executable}"
run_module = "{run_module}"
restore_command = ""
build_command = ""
test_command = ""
run_command = ""
package_command = ""

# Canonical multi-target workspace identity (MP05/MP06). Legacy
# [project].default_target remains accepted for compatibility.
[workspace]
# default_target = "api"

# MP09 safe workflow scheduler. Sequential execution remains the default;
# parallel mode only overlaps DAG nodes when write/read scopes and resource
# limits prove the overlap safe.
[workspace.scheduler]
# parallel = false
# max_parallel_nodes = 4
# max_gradle_nodes = 1
# max_dotnet_nodes = 1

[workspace.groups]
# mobile = ["android", "tv"]
# frontend = ["web", "desktop"]
# release = ["api", "@frontend", "@mobile"]
# everything = ["*"]

# Reusable polyglot job example (MP02):
# [jobs.generate-client]
# runner = "node"
# root = "tools/openapi"
# command = ["npm", "run", "generate"]
# inputs = ["schema/**", "package.json", "package-lock.json"]
# outputs = ["generated/**"]
#
# Attach jobs/runner actions to a named target without changing that target's
# primary runner. A plain string list is sequential:
# [targets.api.workflows]
# validate = ["job:generate-client", "runner:python#validate"]
#
# MP03 explicit DAG form (depends_on names stable step ids):
# validate = [
#   {{ id = "generate", ref = "job:generate-client", depends_on = [] }},
#   {{ id = "python", ref = "runner:python#validate", depends_on = ["generate"] }},
#   {{ id = "node", ref = "runner:node#test", depends_on = ["generate"] }},
# ]
# Cross-target dependencies are executable and deduplicated (MP05):
# build = ["target:protocol#build", "runner:python#build"]
#
# MP07 affected selection uses target roots automatically. Declare additional
# ownership when files outside the target root should affect this target:
# [targets.api]
# inputs = ["../../packages/protocol/**", "../../schemas/openapi.json"]
# outputs = ["build/**", "dist/**"]
# `outputs` are also validated by MP08 preflight to stay within the repository.

# UI03 shared presentation preferences. User-global [ui] values from
# ~/.config/devtool/config.toml are inherited; repository values override them.
# [ui]
# profile = "developer" # auto | minimal | standard | developer | debug | custom
# theme = "auto"
# density = "comfortable"
# icons = "auto"
# motion = "auto"
# verbosity = "normal"
# show_environment = "when-relevant"
# show_commands = "on-error"
# show_diagnostics = "expanded-on-error"
#
# [ui.tui]
# density = "compact"
# [ui.hud]
# density = "dense"
# max_steps = 6
# [ui.desktop]
# sidebar = "expanded"
# [ui.android]
# notification_detail = "normal"
#
# [ui.profiles.focused]
# extends = "minimal"
# show_diagnostics = "expanded-on-error"

[execution]
environment = "auto" # auto | native-termux | chroot | proot-distro | desktop-linux | ssh | android-device | external-device
chroot_name = "androidos"
device_serial = ""

[proot]
# The guest does not need devtool; its tool providers are discovered inside the guest.
distro = ""
user = "root"
cwd = "/workspace/project"
auto_bind_repo = true
repo_mount = "/workspace/project"
shared_home = false
shared_tmp = false
isolated = false
minimal = false
stage_dir = "~/.cache/devtool/proot-stage"
stage_mount = "/run/devtool"
cache_dir = "~/.cache/devtool/proot"
cache_mount = "/var/cache/devtool"
bind = []
env = []

[termux]
# Diagnostics/package recommendations are read-only. Android SDK provider bootstrap may install narrowly-scoped Termux prerequisites/repository metadata when bootstrap policy allows it.
wake_lock = "auto"
resource_profile = "balanced"

[termux.storage]
shared_storage_policy = "warn"

[termux.artifacts]
# export_dir = "/sdcard/Download"

[ssh]
host = ""
port = 22
user = ""
identity = ""
mode = "devtool" # devtool | commands
cwd = "" # remote working directory; required by commands mode
session = ""
tool = "devtool" # used only by devtool mode
stage_dir = "~/.cache/devtool/ssh-stage"
copy_artifacts = true
detached = false # whole-run devtool mode only

[toolchain]
manager = "auto" # auto | mise | none

[android]
variant = "debug"
sdk_provider = "auto" # auto | termuxvoid | homuhomu | managed
aapt2_provider = "auto"
ndk_host_provider = "auto" # auto | google | arm64 | qemu | none
arm64_ndk_url = ""
qemu_command = ""
qemu_ld_prefix = "auto"
qemu_sysroot_url = "" # optional amd64 rootfs archive; default auto-download uses Ubuntu Base
qemu_sysroot_sha256 = ""
qemu_install = "auto"
prefer_native_termux = false

[android.storage]
auto_cleanup = "safe" # safe | off
min_free_mb = 4096
target_free_mb = 6144
managed_gradle_retention = 2
manage_gradle_user_cache = false # opt in to pruning old ~/.gradle version caches/build-cache-1
keep_provider_downloads = false
cleanup_after_build = true

[android.java]
provider = "auto" # auto | termux | system

[android.native]
provider = "auto" # auto | termux-sysroot | ndk | system
cache = "auto" # auto | ccache | off

[android.build_tools]
provider = "auto" # auto | termux | sdk | commit451 | none
aapt2_provider = "auto" # auto | termux | sdk | revanced | none

[gradle]
provider = "auto" # auto | wrapper | termux | system | download
max_workers = 4
offline = false

[python]
manager = "{python_manager}" # auto | uv | poetry | pdm | pipenv | hatch | venv
test_framework = "{python_test_framework}" # auto | pytest | unittest | tox | nox
environment = "" # blank uses devtool-managed XDG storage
groups = [] # e.g. ["test", "dev"]
extras = []
locked = true
offline = false
install_project = true
# lint = ["ruff", "mypy"]
validator_dependencies = []
pre_commit = "auto"
source_import_isolation = false # wheel import isolation is enabled by python.package.import_smoke
cli_contract = false
isolation = true
disable_network = false
matrix = []
matrix_mode = "active" # active | full
fresh_dist = true
clean_categories = ["artifacts", "caches"]
# generated_commands = []
# database_commands = []
# notebook_commands = []
# service_commands = []
# security_commands = []

[python.pytest]
# Exact pytest selectors used when no higher-precedence profile/artifact/CLI selection exists.
# tests = ["tests/unit"]
# markers = ["not slow"]
# k = ""
# args = ["--maxfail=1"]
# include_defaults = false

[python.coverage]
minimum_total = 0
minimum_changed_lines = 0

[python.package]
verify_all_wheels = true
rebuild_sdist = true
import_smoke = true
verify_cli = true
install_dependencies = false
reproducible = false
import_modules = []
console_scripts = []

[python.notebooks]
validate = true
execute = false

[python.database]
enabled = "auto"
# commands = ["alembic upgrade head", "alembic downgrade -1", "alembic upgrade head"]

[python.service]
enabled = false
start = ""
url = ""
host = ""
port = 0
check = ""
timeout = 60

[python.security]
enabled = false
tools = ["pip_audit", "bandit"]
sbom = false

[dotnet]
# configuration = "Debug"
# framework = "net9.0"
# runtime = "linux-x64"
coalesce_validation = true
explicit_restore = false
stop_after_run = false


[build]
max_workers = "auto" # CPU/memory-aware; DEVTOOL_MAX_WORKERS overrides

[node]
manager = "{node_manager}" # auto | npm | pnpm | yarn | bun
coalesce_validation = true

[rust]
coalesce_validation = true

[go]
coalesce_validation = true
# packages = ["./..."]                # test/vet scope
# build_target = "./cmd/app"          # optional single executable target
# build_targets = ["./..."]           # package-main discovery scope
# build_directory = ".devtool/build/go"
# package_directory = ".devtool/package/go"
# run_target = "."
#
# Optional executable-name overrides for colliding/custom main packages:
# [go.outputs]
# "./cmd/server" = "server"

[maven]
coalesce_validation = true

[cmake]
build_directory = ".devtool/build/cmake"
provider = "auto" # auto | termux | system
native_provider = "auto" # auto | termux-sysroot | system
cache = "auto" # auto | ccache | off

[meson]
build_directory = ".devtool/build/meson"

[make]
# build_target = "all"

[tasks]
# restore = ["help"]
# build = ["assembleDebug"]
# clean = ["clean"]

[artifacts]
# output_directory = ".devtool/build-artifacts"
# output_name = "app"
# collision = "unique" # unique | replace | fail
# verify = true
# required = true
# external_verification = "auto" # auto | always | required | never (Android/Gradle)
# paths = ["dist/custom-package.zip"]
# directories = ["dist/packages"]
# patterns = ["*.zip", "*.tar.gz"]
# recursive = true

# [test_profiles.quick]
# include = ["unit", "smoke"]
# exclude_tags = ["slow", "network"]
# pytest_tests = ["tests/test_auth.py::test_login"]
# pytest_markers = ["not network"]
# pytest_k = "authentication"
# pytest_args = ["--maxfail=1"]
# pytest_include_defaults = false

[validation]
# test_profile = "full"
# Android/Termux safety defaults are automatic. Resumable mode is selected by
# `validate start`, `validate --sequential`, or an apply validation flag.
# android_safe = true
# max_workers = 1
# cpu_limit = 2
# split_gradle = true
# no_daemon = true
# low_priority = true
# gradle_heap_mb = 1536
# gradle_metaspace_mb = 0 # 0 disables Devtool MaxMetaspaceSize; positive values set an explicit cap
# memory_guard_mb = 0
# stop_daemon_after_phase = false
# affected_module_lint = true
#
# [validation.phases]
# preflight = ["help"]
# dependencies = ["dependencies"]
# compile = [":app:compileDebugSources"]
# resources = [":app:processDebugResources"]
# unit-test = [":app:testDebugUnitTest"]
# package = [":app:assembleDebug"]
# lint = [":app:lintDebug"]
#
# [validation.phase_profiles.compile]
# max_workers = 1
# cpu_limit = 2
# gradle_heap_mb = 1536
# gradle_metaspace_mb = 0 # 0 disables Devtool MaxMetaspaceSize; positive values set an explicit cap
# memory_guard_mb = 0
# low_priority = true

[env]
# Arbitrary runtime env vars for commands:
# SIMSTOOLKIT_WINE_PATH = "wine"

# Environment override rules:
# DEVTOOL_PROJECT_KIND=python
# DEVTOOL_PROJECT_BUILD_TARGET=pyproject.toml
# DEVTOOL_PROJECT_RUN_TARGET=src/app.py
# DEVTOOL_PROJECT_PYTHON=python3.12
# DEVTOOL_PROJECT_RUN_MODULE=my_app
# DEVTOOL_PROJECT_RESTORE_COMMAND=python3 -m pip install -e .
# DEVTOOL_PROJECT_BUILD_COMMAND=python3 -m build
# DEVTOOL_PROJECT_TEST_COMMAND=python3 -m pytest -q
# DEVTOOL_PROJECT_RUN_COMMAND=python3 -m my_app
# DEVTOOL_PROJECT_PACKAGE_COMMAND=python3 -m build
#
# DEVTOOL_ENV_EXAMPLE_FLAG=1
"""


def _pick_run_project_for_solution(repo_root: Path, solution: Path, app_candidates: list[Path]) -> str:
    solution_dir = solution.parent
    matching = [candidate for candidate in app_candidates if solution_dir in candidate.parents]
    if not matching:
        return ""
    best = min(
        matching,
        key=lambda candidate: (len(candidate.relative_to(solution_dir).parts), str(candidate.relative_to(repo_root))),
    )
    return str(best.relative_to(repo_root))




def _unique_target_name(existing: set[str], base_name: str, path: Path) -> str:
    candidate = base_name
    if candidate not in existing:
        existing.add(candidate)
        return candidate

    stem_name = path.stem.replace("-", "_").replace(".", "_")
    if stem_name:
        candidate = f"{base_name}_{stem_name}"
        if candidate not in existing:
            existing.add(candidate)
            return candidate

    index = 2
    while True:
        candidate = f"{base_name}_{index}"
        if candidate not in existing:
            existing.add(candidate)
            return candidate
        index += 1

def _target_name_from_path(repo_root: Path, path: Path) -> str:
    relative = path.relative_to(repo_root)
    name_parts = [part.replace("-", "_").replace(".", "_") for part in relative.parts[:-1]]
    stem = relative.stem.replace("-", "_").replace(".", "_")
    if name_parts:
        name_parts.append(stem)
        return "_".join(part for part in name_parts if part)
    return stem


def _display(repo_root: Path, value: str) -> str:
    if not value:
        return ""
    try:
        return str(Path(value).resolve().relative_to(repo_root))
    except Exception:
        return value


def _resolve_path(repo_root: Path, value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        return str((repo_root / path).resolve())
    return str(path.resolve())


def _resolve_optional_path(repo_root: Path, value: Any) -> str:
    text = str(value).strip()
    return _resolve_path(repo_root, text) if text else ""


def _collect_paths_with_suffixes(repo_root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    matches: list[Path] = []
    suffixes_lower = tuple(suffix.lower() for suffix in suffixes)
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [name for name in dirnames if name.lower() not in _IGNORED_DOTNET_DIRS]
        base = Path(dirpath)
        for filename in filenames:
            lower_name = filename.lower()
            if lower_name.endswith(suffixes_lower):
                matches.append(base / filename)
    return sorted(matches)



def _repository_relative_path(repo_root: Path, value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if path.is_absolute():
        try:
            return path.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()
    return path.as_posix().lstrip("./")


def _artifact_target_name(repo_root: Path, target: str, solution: str, project: str) -> str:
    declared = str(target or "").strip()
    if declared:
        return declared
    candidate = solution or project
    if not candidate:
        return ""
    path = Path(_repository_relative_path(repo_root, candidate))
    parts = [part.replace("-", "_").replace(".", "_") for part in path.parts[:-1]]
    stem = path.stem.replace("-", "_").replace(".", "_")
    parts.append(stem or "dotnet")
    return "_".join(part for part in parts if part) or "dotnet"


def _toml_key(value: str) -> str:
    if value and all(ch.isalnum() or ch in "_-" for ch in value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _insert_project_setting(text: str, key: str, value: str) -> str:
    lines = text.splitlines()
    project_index = next((index for index, line in enumerate(lines) if line.strip() == "[project]"), None)
    rendered = f"{key} = {_toml_string(value)}"
    if project_index is None:
        prefix = ["[project]", rendered, ""]
        return "\n".join(prefix + lines).rstrip() + "\n"
    end = len(lines)
    for index in range(project_index + 1, len(lines)):
        if lines[index].lstrip().startswith("["):
            end = index
            break
    for index in range(project_index + 1, end):
        stripped = lines[index].strip()
        if stripped.startswith(key + " ") or stripped.startswith(key + "="):
            lines[index] = rendered
            return "\n".join(lines).rstrip() + "\n"
    lines.insert(end, rendered)
    return "\n".join(lines).rstrip() + "\n"


def ensure_artifact_target_config(
    repo_root: Path,
    *,
    target: str = "",
    solution: str = "",
    project: str = "",
) -> dict[str, str]:
    """Register an overlay-created .NET target in .devtool.toml when needed.

    The function is intentionally additive: an overlay-provided configuration wins,
    existing target tables are preserved, and only missing project/target metadata is
    introduced. It is safe to call repeatedly for bundle entries.
    """
    repo_root = repo_root.expanduser().resolve()
    solution_rel = _repository_relative_path(repo_root, solution)
    project_rel = _repository_relative_path(repo_root, project)
    if not solution_rel and not project_rel:
        return {}

    target_name = _artifact_target_name(repo_root, target, solution_rel, project_rel)
    if not target_name:
        return {}

    config_path = repo_root / CONFIG_NAME
    original = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    parsed: dict[str, Any] = {}
    if original.strip():
        try:
            parsed = tomllib.loads(original)
        except tomllib.TOMLDecodeError:
            # Do not hide or overwrite a malformed user configuration. The normal
            # config loader will report the actionable parse error later.
            return {}

    targets = parsed.get("targets") if isinstance(parsed.get("targets"), dict) else {}
    if target_name in targets:
        return {}

    text = original
    project_section = parsed.get("project") if isinstance(parsed.get("project"), dict) else {}
    current_runner = normalize_runner_name(project_section.get("runner", project_section.get("kind", "auto"))) or "auto"
    if current_runner in {"auto", "unknown"}:
        text = _insert_project_setting(text, "runner", "dotnet")
        if not str(project_section.get("platform", "") or "").strip():
            text = _insert_project_setting(text, "platform", "desktop")
        try:
            parsed = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return {}
        project_section = parsed.get("project") if isinstance(parsed.get("project"), dict) else {}

    block = [f"[targets.{_toml_key(target_name)}]", 'runner = "dotnet"', 'platform = "desktop"']
    if solution_rel:
        block.append(f"solution = {_toml_string(solution_rel)}")
    if project_rel:
        block.append(f"run_project = {_toml_string(project_rel)}")
    text = text.rstrip() + ("\n\n" if text.strip() else "") + "\n".join(block) + "\n"
    config_path.write_text(text, encoding="utf-8")
    return {
        "path": str(config_path),
        "target": target_name,
        "solution": solution_rel,
        "project": project_rel,
        "action": "created" if not original else "updated",
    }


def _expanded_workflow_graph_dict(config: ProjectConfig, target_name: str, action: str) -> dict[str, Any]:
    from .workflow_runtime import plan_workspace_targets
    return plan_workspace_targets(config, [target_name], action).as_dict()


def resolve_config_dict(config: ProjectConfig) -> dict[str, Any]:
    return {
        "repo_root": str(config.repo_root),
        "config_path": str(config.config_path) if config.config_path else "",
        "global_config_path": str(config.global_config_path) if config.global_config_path else "",
        "workspace": workspace_as_dict(config.workspace, config.targets),
        "ui": {
            "config": config.ui_config,
            "resolved": {surface: resolve_ui_preferences(config.ui_config, surface=surface).as_dict() for surface in ("machine", "desktop", "tui", "hud", "android")},
        },
        "project": {
            "kind": config.kind,
            "runner": config.runner,
            "platform": config.platform,
            "project_root": config.project_root,
            "build_target": config.build_target,
            "restore_target": config.restore_target,
            "test_target": config.test_target,
            "run_target": config.run_target,
            "selected_target": config.selected_target,
            "variant": config.variant,
            "python": config.python_executable,
            "run_module": config.run_module,
            "restore_command": config.restore_command,
            "build_command": config.build_command,
            "test_command": config.test_command,
            "run_command": config.run_command,
            "package_command": config.package_command,
            "execution_environment": config.execution_environment,
            "chroot_name": config.chroot_name,
            "device_serial": config.device_serial,
            "runner_options": config.runner_options,
        },
        "targets": {
            name: {
                "type": target.kind,
                "runner": target.effective_runner,
                "platform": target.platform,
                "root": target.root,
                "solution": target.solution,
                "restore_target": target.restore_target,
                "build_target": target.build_target,
                "test_target": target.test_target,
                "run_project": target.run_project,
                "cwd": target.cwd,
                "restore_command": target.restore_command,
                "build_command": target.build_command,
                "test_command": target.test_command,
                "run_command": target.run_command,
                "package_command": target.package_command,
                "env": dict(sorted(target.env.items())),
                "execution_environment": target.execution_environment,
                "chroot_name": target.chroot_name,
                "device_serial": target.device_serial,
                "options": target.options,
                "variants": target.variants,
                "inputs": list(target.inputs),
                "outputs": list(target.outputs),
                "metadata": target.metadata,
                "workflows": {
                    workflow_name: workflow_steps_as_strings(workflow)
                    for workflow_name, workflow in sorted(target.workflows.items())
                },
                "workflow_graphs": {
                    workflow_name: workflow_as_dict(name, workflow)
                    for workflow_name, workflow in sorted(target.workflows.items())
                },
                "expanded_workflow_graphs": {
                    workflow_name: _expanded_workflow_graph_dict(config, name, workflow_name)
                    for workflow_name in sorted(target.workflows)
                },
            }
            for name, target in sorted(config.targets.items())
        },
        "jobs": {
            name: job_as_dict(job)
            for name, job in sorted(config.jobs.items())
        },
        "env": dict(sorted(config.env.items())),
        "provenance": dict(sorted(config.config_provenance.items())),
    }
