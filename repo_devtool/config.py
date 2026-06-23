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

    loaded: dict[str, Any] = {}
    if config_path:
        try:
            loaded = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise SystemExit(f"Invalid TOML in {config_path}: {exc}") from exc

    project_section = _apply_project_env_overrides(dict(loaded.get("project", {})))
    env_section = _apply_runtime_env_overrides({str(k): str(v) for k, v in loaded.get("env", {}).items()})

    kind = str(project_section.get("kind", "auto")).strip().lower() or "auto"
    detected_kind = detect_project_kind(repo_root) if kind == "auto" else kind

    targets = _load_named_targets(repo_root, loaded.get("targets", {}), env_section)
    default_target = str(project_section.get("default_target", "")).strip()

    selected_target_name = target_name.strip() or str(project_section.get("target", "")).strip()
    explicit_solution = solution_override.strip() or str(project_section.get("solution", "")).strip()
    explicit_project = project_override.strip() or str(project_section.get("project", "")).strip()

    resolved = _resolve_targets(
        repo_root,
        detected_kind,
        project_section,
        targets,
        default_target=default_target,
        selected_target_name=selected_target_name,
        explicit_solution=explicit_solution,
        explicit_project=explicit_project,
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
    if list(repo_root.rglob("*.sln")) or list(repo_root.rglob("*.csproj")):
        return "dotnet"
    if (repo_root / "pyproject.toml").exists() or (repo_root / "setup.py").exists():
        return "python"
    return "unknown"


def discover_dotnet_candidates(repo_root: Path) -> list[Path]:
    solutions = sorted(repo_root.rglob("*.sln"))
    if solutions:
        return solutions
    return sorted(repo_root.rglob("*.csproj"))


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
    kind: str,
    project_section: dict[str, Any],
    targets: dict[str, DotnetTarget],
    *,
    default_target: str,
    selected_target_name: str,
    explicit_solution: str,
    explicit_project: str,
) -> dict[str, str]:
    if kind != "dotnet":
        build_target = _default_build_target(repo_root, kind, project_section)
        run_target = str(project_section.get("run_target", "")).strip()
        if run_target:
            run_target = _resolve_path(repo_root, run_target)
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

    explicit_build = _default_build_target(repo_root, kind, project_section)
    explicit_run = str(project_section.get("run_target", "")).strip()
    if explicit_build or explicit_run:
        return {
            "selected_target": "",
            "restore_target": explicit_build,
            "build_target": explicit_build,
            "test_target": explicit_build,
            "run_target": _resolve_optional_path(repo_root, explicit_run) or explicit_build,
        }

    candidates = discover_dotnet_candidates(repo_root)
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
        app_candidates = sorted(p for p in repo_root.rglob("*.csproj") if "test" not in p.name.lower())
        if len(candidates) <= 1:
            if app_candidates:
                run_target = str(app_candidates[0].relative_to(repo_root))
        else:
            named_blocks: list[str] = []
            for candidate in candidates:
                target_name = _target_name_from_path(repo_root, candidate)
                run_project = _pick_run_project_for_solution(repo_root, candidate, app_candidates)
                named_blocks.append(
                    f"""\n[targets.{target_name}]\nsolution = "{candidate.relative_to(repo_root)}"\nrun_project = "{run_project}"\n"""
                )
            default_target = _target_name_from_path(repo_root, candidates[0])
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
    for candidate in app_candidates:
        if solution_dir in candidate.parents:
            return str(candidate.relative_to(repo_root))
    return str(app_candidates[0].relative_to(repo_root)) if app_candidates else ""


def _target_name_from_path(repo_root: Path, path: Path) -> str:
    parts = list(path.relative_to(repo_root).parts[:-1])
    if parts:
        return parts[0].replace("-", "_").replace(".", "_")
    return path.stem.replace("-", "_").replace(".", "_")


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
