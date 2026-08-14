from __future__ import annotations

import difflib
import os
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from .models import DotnetTarget, ProjectConfig
from .toolchains import normalize_toolchain_manager

CONFIG_NAME = ".devtool.toml"

GLOBAL_CONFIG_NAME = "config.toml"
GLOBAL_SAFE_TOP_LEVEL_SECTIONS = {
    "cli", "environment", "termux_chroot", "termux", "proot", "android", "gradle", "dotnet", "terminal",
    "bootstrap", "chroots",
}
GLOBAL_REPO_SCOPED_SECTIONS = {
    "project", "targets", "tasks", "artifacts", "python", "node", "rust",
    "go", "maven", "cmake", "meson", "make", "test_profiles", "variants",
}
GLOBAL_COMMAND_SCOPED_CLI_KEYS = {
    ("validation", "skip_restore"), ("validation", "skip_build"), ("validation", "skip_test"),
    ("validation", "watch"), ("validation", "matrix"), ("validation", "test_profile"),
    ("validation", "select"), ("validation", "id"), ("validation", "tag"),
    ("validation", "exclude_tag"), ("validation", "path"), ("validation", "class"),
    ("validation", "method"), ("validation", "filter"), ("validation", "task"),
    ("validation", "failed"), ("validation", "changed"),
}


def global_config_path() -> Path:
    raw = os.environ.get("DEVTOOL_GLOBAL_CONFIG", "").strip()
    if raw:
        return Path(raw).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(xdg).expanduser() if xdg else Path.home() / ".config"
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
    merged = dict(defaults)
    for key, value in values.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_defaults(dict(merged[key]), value)
        elif value not in (None, "", [], {}):
            merged[key] = value
        elif key not in merged:
            merged[key] = value
    return merged


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
        "# Diagnostics/package recommendations are read-only; devtool never runs pkg/apt install or upgrade automatically.",
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
        "[terminal]",
        "preferred = \"micro\"",
        "",
        "[dotnet]",
        "stop_after_run = true",
        "",
        "[android]",
        "aapt2_provider = \"auto\"",
        "aapt2_fallback_path = \"/sdcard/Download/aapt2-arm64-v8a.bin\"",
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
    return entries
SUPPORTED_PROJECT_KINDS = {
    "auto", "dotnet", "android", "gradle", "python", "node", "rust",
    "go", "maven", "cmake", "meson", "make", "command", "unknown",
}
RUNNER_OPTION_SECTIONS = (
    "android", "gradle", "python", "dotnet", "node", "rust", "go",
    "maven", "cmake", "meson", "make", "build", "tasks", "artifacts",
    "test_profiles", "validation", "termux_chroot", "termux", "proot", "ssh", "bootstrap", "chroots",
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
        return path if path.exists() else None

    for parent in [repo_root, *repo_root.parents]:
        candidate = parent / CONFIG_NAME
        if candidate.exists():
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

    configured_default = str(project_section.get("default_target", "") or "").strip()
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

    global_loaded, global_path = load_global_config()
    global_defaults = _global_project_defaults(global_loaded)
    if global_defaults:
        loaded = _deep_merge_defaults(global_defaults, loaded)

    project_section = _apply_project_env_overrides(dict(loaded.get("project", {})))
    env_section = _apply_runtime_env_overrides({str(k): str(v) for k, v in loaded.get("env", {}).items()})
    execution_section = loaded.get("execution", {}) if isinstance(loaded.get("execution", {}), dict) else {}
    toolchain_section = loaded.get("toolchain", {}) if isinstance(loaded.get("toolchain", {}), dict) else {}

    override_kind = str(kind_override or "").strip().lower()
    config_kind = str(project_section.get("kind", "auto")).strip().lower() or "auto"
    kind = override_kind if override_kind and override_kind != "auto" else config_kind
    if kind == "generic":
        kind = "command"
    if kind not in SUPPORTED_PROJECT_KINDS:
        raise SystemExit(f"Unsupported project type: {kind}")
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

    targets = _load_named_targets(config_root, loaded.get("targets", {}), env_section)
    selected_target_name = _default_target_name(
        project_section,
        targets,
        requested=target_name,
        explicit_solution=solution_override,
        explicit_project=project_override,
        allow_ambiguous=allow_ambiguous_target,
    )
    selected_target_hint = targets.get(selected_target_name) if selected_target_name else None
    if selected_target_hint is not None and selected_target_hint.kind:
        detected_kind = selected_target_hint.kind
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

    execution_environment = str(
        (selected_target.execution_environment if selected_target and selected_target.execution_environment else "")
        or execution_section.get("environment", "")
        or execution_section.get("mode", "")
        or project_section.get("execution_environment", "")
        or "auto"
    ).strip().lower()
    termux_chroot_section = loaded.get("termux_chroot", {}) if isinstance(loaded.get("termux_chroot", {}), dict) else {}
    chroot_name = str(
        (selected_target.chroot_name if selected_target and selected_target.chroot_name else "")
        or execution_section.get("chroot_name", "")
        or project_section.get("chroot_name", "")
        or termux_chroot_section.get("default_chroot", "")
        or os.environ.get("DEVTOOL_TERMUX_CHROOT", "androidos")
        or "androidos"
    ).strip()
    device_serial = str(
        (selected_target.device_serial if selected_target and selected_target.device_serial else "")
        or execution_section.get("device_serial", "")
        or project_section.get("device_serial", "")
    ).strip()
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
        else:
            project_root = str(config_root)

    return ProjectConfig(
        repo_root=repo_root,
        kind=detected_kind,
        build_target=resolved["build_target"],
        run_target=resolved["run_target"],
        restore_target=resolved["restore_target"],
        test_target=resolved["test_target"],
        python_executable=str(project_section.get("python", sys.executable)).strip() or sys.executable,
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
        execution_environment=execution_environment,
        chroot_name=chroot_name,
        device_serial=device_serial,
        project_root=project_root,
        toolchain_manager=toolchain_manager,
        runner_options=runner_options,
        global_config_path=global_path if global_loaded else None,
    )


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
    from .python_support import discover_python_roots
    python_roots = discover_python_roots(repo_root)
    markers = {
        "dotnet": bool(dotnet_candidates),
        "android": bool(android_markers),
        "gradle": bool(gradle_markers and not android_markers),
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
        "gradle": repo_root in gradle_markers and repo_root not in android_markers,
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
    return {
        "kind": kind,
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
    """Return whether a Gradle root contains an Android plugin or manifest.

    Discovery deliberately ignores generated/vendor directories.  It can find
    Android modules below a repository-level settings file without mistaking
    their individual ``build.gradle`` files for separate project roots.
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

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name for name in dirnames
            if name.lower() not in _IGNORED_DOTNET_DIRS
        ]
        base = Path(dirpath)
        if (
            "AndroidManifest.xml" in filenames
            and base.name == "main"
            and base.parent.name == "src"
        ):
            return True
        for filename in ("build.gradle", "build.gradle.kts"):
            if filename not in filenames:
                continue
            try:
                text = (base / filename).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "com.android.application" in text or "com.android.library" in text:
                return True
    return False


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
    solutions = _collect_paths_with_suffixes(repo_root, (".sln",))
    if solutions:
        return solutions
    return _collect_paths_with_suffixes(repo_root, (".csproj",))


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
        if (repo_root / "pyproject.toml").exists():
            return str((repo_root / "pyproject.toml").resolve())
        if (repo_root / "setup.py").exists():
            return str((repo_root / "setup.py").resolve())
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


def _load_named_targets(repo_root: Path, section: Any, base_env: dict[str, str]) -> dict[str, DotnetTarget]:
    if not isinstance(section, dict):
        return {}

    targets: dict[str, DotnetTarget] = {}
    for raw_name, raw_value in section.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, dict):
            continue
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
        target_kind = str(raw_value.get("type", raw_value.get("kind", ""))).strip().lower()
        options: dict[str, Any] = {}
        for option_name in RUNNER_OPTION_SECTIONS:
            option_value = raw_value.get(option_name, {})
            if isinstance(option_value, dict):
                options[option_name] = dict(option_value)
        targets[name] = DotnetTarget(
            name=name,
            kind=target_kind,
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
            execution_environment=str(raw_value.get("execution_environment", raw_value.get("environment", ""))).strip().lower(),
            chroot_name=str(raw_value.get("chroot_name", "")).strip(),
            device_serial=str(raw_value.get("device_serial", "")).strip(),
            options=options,
            variants=_load_target_variants(raw_value),
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
            "# Replace project.kind=auto or define/select typed targets before running commands.\n"
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
                    f"""\n[targets.{target_name}]\nsolution = "{candidate.relative_to(repo_root)}"\nrun_project = "{run_project}"\n"""
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
                    f"\n[targets.{target_name}]\ntype = \"{kind}\"\nroot = \"{candidate.relative_to(repo_root)}\"\n"
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
                    f"\n[targets.{target_name}]\ntype = \"python\"\nroot = \"{candidate.relative_to(repo_root)}\"\n"
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

    return f"""{ambiguity_note}[project]
kind = "{template_kind}" # auto | dotnet | android | gradle | python | node | rust | go | maven | cmake | meson | make | command
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
# Diagnostics/package recommendations are read-only; devtool never runs pkg/apt install or upgrade automatically.
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
aapt2_provider = "auto"
ndk_host_provider = "auto" # auto | google | arm64 | qemu | none
arm64_ndk_url = ""
qemu_command = ""
qemu_ld_prefix = "auto"
qemu_sysroot_url = "" # optional amd64 rootfs archive; default auto-download uses Ubuntu Base
qemu_sysroot_sha256 = ""
qemu_install = "auto"
prefer_native_termux = false

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
# output_directory = "artifacts/android"
# required = true
# external_verification = "auto" # auto | always | required | never

# [test_profiles.quick]
# include = ["unit", "smoke"]
# exclude_tags = ["slow", "network"]

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
# memory_guard_mb = 640
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
# memory_guard_mb = 640
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
    current_kind = str(project_section.get("kind", "auto")).strip().lower() or "auto"
    if current_kind in {"auto", "unknown"}:
        text = _insert_project_setting(text, "kind", "dotnet")
        try:
            parsed = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return {}
        project_section = parsed.get("project") if isinstance(parsed.get("project"), dict) else {}

    block = [f"[targets.{_toml_key(target_name)}]"]
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


def resolve_config_dict(config: ProjectConfig) -> dict[str, Any]:
    return {
        "repo_root": str(config.repo_root),
        "config_path": str(config.config_path) if config.config_path else "",
        "global_config_path": str(config.global_config_path) if config.global_config_path else "",
        "project": {
            "kind": config.kind,
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
            }
            for name, target in sorted(config.targets.items())
        },
        "env": dict(sorted(config.env.items())),
    }
