from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Any

from .models import ProjectConfig

PROJECT_ENV_PREFIX = "DEVTOOL_PROJECT_"
RUNTIME_ENV_PREFIX = "DEVTOOL_ENV_"


def find_config(repo_root: Path, explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return path if path.exists() else None
    path = repo_root / ".devtool.toml"
    return path if path.exists() else None


def load_project_config(repo_root_arg: str | None, config_path_arg: str | None) -> ProjectConfig:
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

    return ProjectConfig(
        repo_root=repo_root,
        kind=detected_kind,
        build_target=_default_build_target(repo_root, detected_kind, project_section),
        run_target=str(project_section.get("run_target", "")).strip(),
        python_executable=str(project_section.get("python", sys.executable)).strip() or sys.executable,
        run_module=str(project_section.get("run_module", "")).strip(),
        restore_command=str(project_section.get("restore_command", "")).strip(),
        build_command=str(project_section.get("build_command", "")).strip(),
        test_command=str(project_section.get("test_command", "")).strip(),
        package_command=str(project_section.get("package_command", "")).strip(),
        run_command=str(project_section.get("run_command", "")).strip(),
        env=env_section,
        config_path=config_path,
    )


def detect_project_kind(repo_root: Path) -> str:
    if list(repo_root.glob("*.sln")) or list(repo_root.rglob("*.csproj")):
        return "dotnet"
    if (repo_root / "pyproject.toml").exists() or (repo_root / "setup.py").exists():
        return "python"
    return "unknown"


def _default_build_target(repo_root: Path, kind: str, section: dict[str, Any]) -> str:
    explicit = str(section.get("build_target", "")).strip()
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            return str((repo_root / path).resolve())
        return str(path)

    if kind == "dotnet":
        solutions = sorted(repo_root.glob("*.sln"))
        if solutions:
            return str(solutions[0].resolve())
        projects = sorted(repo_root.rglob("*.csproj"))
        if projects:
            return str(projects[0].resolve())
    elif kind == "python":
        if (repo_root / "pyproject.toml").exists():
            return str((repo_root / "pyproject.toml").resolve())
        if (repo_root / "setup.py").exists():
            return str((repo_root / "setup.py").resolve())
    return ""


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
    if kind == "dotnet":
        app_candidates = sorted(p for p in repo_root.rglob("*.csproj") if "test" not in p.name.lower())
        if app_candidates:
            run_target = str(app_candidates[0].relative_to(repo_root))
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
run_target = "{run_target}"
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


def _display(repo_root: Path, value: str) -> str:
    if not value:
        return ""
    try:
        return str(Path(value).resolve().relative_to(repo_root))
    except Exception:
        return value


def resolve_config_dict(config: ProjectConfig) -> dict[str, Any]:
    return {
        "repo_root": str(config.repo_root),
        "config_path": str(config.config_path) if config.config_path else "",
        "project": {
            "kind": config.kind,
            "build_target": config.build_target,
            "run_target": config.run_target,
            "python": config.python_executable,
            "run_module": config.run_module,
            "restore_command": config.restore_command,
            "build_command": config.build_command,
            "test_command": config.test_command,
            "run_command": config.run_command,
            "package_command": config.package_command,
        },
        "env": dict(sorted(config.env.items())),
    }
