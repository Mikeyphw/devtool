from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Any

from .models import DotnetTarget, ProjectConfig

CONFIG_NAME = ".devtool.toml"
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
    solution_override: str = "",
    project_override: str = "",
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

    kind = str(project_section.get("kind", "auto")).strip().lower() or "auto"
    explicit_dotnet_intent = bool(
        solution_override.strip()
        or project_override.strip()
        or str(project_section.get("solution", "")).strip()
        or str(project_section.get("project", "")).strip()
        or isinstance(loaded.get("targets"), dict) and bool(loaded.get("targets"))
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
    default_target = str(project_section.get("default_target", "")).strip()

    selected_target_name = target_name.strip() or str(project_section.get("target", "")).strip()
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
        default_target=default_target,
        selected_target_name=selected_target_name,
        explicit_solution=explicit_solution,
        explicit_project=explicit_project,
        config_solution=config_solution,
        config_project=config_project,
        config_run_target=config_run_target,
    )

    effective_env = dict(env_section)
    if resolved["selected_target"] and resolved["selected_target"] in targets:
        effective_env.update(targets[resolved["selected_target"]].env)

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
        default_target=default_target,
        selected_target=resolved["selected_target"],
        targets=targets,
    )


def detect_project_kind(repo_root: Path) -> str:
    dotnet_candidates = discover_dotnet_candidates(repo_root)
    python_markers = (repo_root / "pyproject.toml").exists() or (repo_root / "setup.py").exists()
    if dotnet_candidates and python_markers:
        print(
            f"devtool: mixed dotnet/python markers found under {repo_root}. "
            "Set [project].kind explicitly in .devtool.toml.",
            file=sys.stderr,
        )
    if dotnet_candidates:
        return "dotnet"
    if python_markers:
        return "python"
    return "unknown"


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
    if kind == "python":
        if (repo_root / "pyproject.toml").exists():
            return str((repo_root / "pyproject.toml").resolve())
        if (repo_root / "setup.py").exists():
            return str((repo_root / "setup.py").resolve())
    return ""


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
        target_env = dict(base_env)
        for key, value in raw_value.get("env", {}).items() if isinstance(raw_value.get("env", {}), dict) else []:
            target_env[str(key)] = str(value)
        targets[name] = DotnetTarget(
            name=name,
            solution=solution,
            restore_target=restore_target,
            build_target=build_target,
            test_target=test_target,
            run_project=run_project,
            cwd=cwd,
            env=target_env,
        )
    return targets


def _resolve_targets(
    repo_root: Path,
    config_root: Path,
    kind: str,
    project_section: dict[str, Any],
    targets: dict[str, DotnetTarget],
    *,
    default_target: str,
    selected_target_name: str,
    explicit_solution: str,
    explicit_project: str,
    config_solution: str,
    config_project: str,
    config_run_target: str,
) -> dict[str, str]:
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
            "run_target": run_target,
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

    chosen_name = selected_target_name or default_target
    if chosen_name:
        target = targets.get(chosen_name)
        if target is None:
            available = ", ".join(sorted(targets)) or "<none>"
            raise SystemExit(f"Unknown target '{chosen_name}'. Available targets: {available}")
        return {
            "selected_target": target.name,
            "restore_target": target.effective_restore_target(),
            "build_target": target.effective_build_target(),
            "test_target": target.effective_test_target(),
            "run_target": target.effective_run_project(),
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
            "Use --target <name>, --solution <path>, or configure [project].default_target.\n"
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
            default_target = ""
            for index, candidate in enumerate(candidates):
                base_name = _target_name_from_path(repo_root, candidate)
                target_name = _unique_target_name(used_target_names, base_name, candidate)
                if index == 0:
                    default_target = target_name
                run_project = _pick_run_project_for_solution(repo_root, candidate, app_candidates)
                named_blocks.append(
                    f"""\n[targets.{target_name}]\nsolution = "{candidate.relative_to(repo_root)}"\nrun_project = "{run_project}"\n"""
                )
            target_blocks = f'\ndefault_target = "{default_target}"\n' + "".join(named_blocks)
            build_target = ""
    elif kind == "python":
        if (repo_root / "src").exists():
            for candidate in sorted((repo_root / "src").glob("*.py")):
                run_target = str(candidate.relative_to(repo_root))
                break
        if not run_target:
            for candidate in sorted(repo_root.glob("*.py")):
                run_target = str(candidate.relative_to(repo_root))
                break
        if not run_module and (repo_root / "pyproject.toml").exists():
            run_module = "your_package"

    return f"""[project]
kind = "{kind}" # auto | dotnet | python | unknown
build_target = "{_display(repo_root, build_target)}"
run_target = "{run_target}"{target_blocks}
python = "{sys.executable}"
run_module = "{run_module}"
restore_command = ""
build_command = ""
test_command = ""
run_command = ""
package_command = ""

[env]
# Arbitrary runtime env vars for commands:
# SIMSTOOLKIT_WINE_PATH = "wine"

# Environment override rules:
# DEVTOOL_PROJECT_KIND=python
# DEVTOOL_PROJECT_BUILD_TARGET=pyproject.toml
# DEVTOOL_PROJECT_RUN_TARGET=src/app.py
# DEVTOOL_PROJECT_TARGET=s3pe
# DEVTOOL_PROJECT_DEFAULT_TARGET=s3pe
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

    if not str(project_section.get("default_target", "")).strip():
        text = _insert_project_setting(text, "default_target", target_name)

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
            "build_target": config.build_target,
            "restore_target": config.restore_target,
            "test_target": config.test_target,
            "run_target": config.run_target,
            "default_target": config.default_target,
            "selected_target": config.selected_target,
            "python": config.python_executable,
            "run_module": config.run_module,
            "restore_command": config.restore_command,
            "build_command": config.build_command,
            "test_command": config.test_command,
            "run_command": config.run_command,
            "package_command": config.package_command,
        },
        "targets": {
            name: {
                "solution": target.solution,
                "restore_target": target.restore_target,
                "build_target": target.build_target,
                "test_target": target.test_target,
                "run_project": target.run_project,
                "cwd": target.cwd,
                "env": dict(sorted(target.env.items())),
            }
            for name, target in sorted(config.targets.items())
        },
        "env": dict(sorted(config.env.items())),
    }
