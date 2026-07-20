from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Any

from .models import DotnetTarget, ProjectConfig
from .toolchains import normalize_toolchain_manager

CONFIG_NAME = ".devtool.toml"
SUPPORTED_PROJECT_KINDS = {
    "auto", "dotnet", "android", "gradle", "python", "node", "rust",
    "go", "maven", "cmake", "meson", "make", "command", "unknown",
}
RUNNER_OPTION_SECTIONS = (
    "android", "gradle", "python", "dotnet", "node", "rust", "go",
    "maven", "cmake", "meson", "make", "build", "tasks", "artifacts",
    "test_profiles", "validation",
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


def load_project_config(
    repo_root_arg: str | None,
    config_path_arg: str | None,
    *,
    target_name: str = "",
    variant_name: str = "",
    solution_override: str = "",
    project_override: str = "",
    kind_override: str = "",
) -> ProjectConfig:
    repo_root = Path(repo_root_arg or Path.cwd()).expanduser().resolve()
    config_path = find_config(repo_root, config_path_arg)
    config_root = config_path.parent if config_path else repo_root

    loaded: dict[str, Any] = {}
    if config_path:
        try:
            loaded = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise SystemExit(f"Invalid TOML in {config_path}: {exc}") from exc

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
    if kind == "auto":
        detected_kind = detect_project_kind(config_root)
        # Apply-time manifests may point at a solution/project that the overlay is
        # about to create. Treat that declaration as authoritative .NET intent
        # instead of requiring the target to exist before extraction.
        if detected_kind == "unknown" and explicit_dotnet_intent:
            detected_kind = "dotnet"
    else:
        detected_kind = kind

    targets = _load_named_targets(config_root, loaded.get("targets", {}), env_section)
    selected_target_hint = targets.get(target_name.strip()) if target_name.strip() else None
    if selected_target_hint is not None and selected_target_hint.kind:
        detected_kind = selected_target_hint.kind
    # Legacy [project].default_target is intentionally ignored. Devtool no longer
    # chooses a target implicitly; use --target, --solution, or artifact metadata.

    selected_target_name = target_name.strip()
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
    chroot_name = str(
        (selected_target.chroot_name if selected_target and selected_target.chroot_name else "")
        or execution_section.get("chroot_name", "")
        or project_section.get("chroot_name", "")
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
        restore_command=str(project_section.get("restore_command", "")).strip(),
        build_command=str(project_section.get("build_command", "")).strip(),
        test_command=str(project_section.get("test_command", "")).strip(),
        package_command=str(project_section.get("package_command", "")).strip(),
        run_command=str(project_section.get("run_command", "")).strip(),
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
    )


def detect_project_kind(repo_root: Path) -> str:
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
    kinds = [name for name, present in markers.items() if present]
    if len(kinds) > 1:
        print(
            f"devtool: mixed project markers ({', '.join(kinds)}) found under {repo_root}. "
            "Set [project].kind explicitly in .devtool.toml or select a named target.",
            file=sys.stderr,
        )
    for kind in (
        "dotnet", "android", "gradle", "python", "node", "rust", "go",
        "maven", "cmake", "meson", "make",
    ):
        if markers[kind]:
            return kind
    return "unknown"


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
            available = ", ".join(sorted(targets)) or "<none>"
            raise SystemExit(f"Unknown target '{selected_target_name}'. Available targets: {available}")
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


def generate_template(repo_root: Path) -> str:
    repo_root = repo_root.resolve()
    kind = detect_project_kind(repo_root)
    build_target = _default_build_target(repo_root, kind, {})
    run_target = ""
    run_module = ""
    target_blocks = ""

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
                run_module = info.import_packages[0] if info.import_packages else "your_package"
            except Exception:
                run_module = "your_package"

    return f"""[project]
kind = "{kind}" # auto | dotnet | android | gradle | python | node | rust | go | maven | cmake | meson | make | command | unknown
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
environment = "auto" # auto | native-termux | chroot | desktop-linux | android-device | external-device
chroot_name = "androidos"
device_serial = ""

[toolchain]
manager = "auto" # auto | mise | none

[android]
variant = "debug"
aapt2_provider = "auto"
prefer_native_termux = false

[gradle]
max_workers = 4
offline = false

[python]
manager = "auto" # auto | uv | poetry | pdm | pipenv | hatch | venv
test_framework = "auto" # auto | pytest | unittest | tox | nox
environment = ".devtool/venvs/python"
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
manager = "auto" # auto | npm | pnpm | yarn | bun
coalesce_validation = true

[rust]
coalesce_validation = true

[go]
coalesce_validation = true

[maven]
coalesce_validation = true

[cmake]
build_directory = ".devtool/build/cmake"

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
