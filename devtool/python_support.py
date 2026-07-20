from __future__ import annotations

"""Python project inspection, reproducible environments, validation, and packaging.

The module intentionally has no third-party runtime dependency.  It is used by
both the Python project runner and disposable artifact validators, so project
environment selection cannot drift between those two paths.
"""

from dataclasses import asdict, dataclass, field
import argparse
import ast
import base64
import csv
from email.parser import BytesParser
from email.policy import default as email_policy
from functools import lru_cache
import fnmatch
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shlex
import signal
import socket
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import time
from typing import Any, Iterable, Mapping, Sequence
import urllib.error
import urllib.request
import venv
import zipfile


SUPPORTED_MANAGERS = {"auto", "uv", "poetry", "pdm", "pipenv", "hatch", "venv"}
SUPPORTED_TEST_FRAMEWORKS = {"auto", "pytest", "unittest", "tox", "nox"}
LOCK_FILES = {
    "uv": "uv.lock",
    "poetry": "poetry.lock",
    "pdm": "pdm.lock",
    "pipenv": "Pipfile.lock",
}
DEPENDENCY_FILES = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "requirements-dev.txt",
    "constraints.txt",
    "uv.lock",
    "poetry.lock",
    "pdm.lock",
    "Pipfile",
    "Pipfile.lock",
    "tox.ini",
    "noxfile.py",
)
IGNORED_DIRECTORIES = {
    ".git", ".hg", ".svn", ".devtool", ".venv", "venv", "env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".nox", "build", "dist", "site-packages", "node_modules",
    "vendor", "target", "artifacts", ".idea", ".vscode",
}
DOC_SUFFIXES = {".md", ".rst", ".txt", ".adoc", ".png", ".jpg", ".jpeg", ".svg", ".gif"}


class PythonProjectError(RuntimeError):
    """A Python project declaration is invalid or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class PythonProjectInfo:
    root: Path
    manager: str
    framework: str
    build_backend: str
    package_name: str
    lock_file: str
    requires_python: str = ""
    source_roots: tuple[str, ...] = ()
    import_packages: tuple[str, ...] = ()
    console_scripts: tuple[tuple[str, str], ...] = ()
    workspace_members: tuple[str, ...] = ()
    dependency_files: tuple[str, ...] = ()
    native_backend: str = ""
    generated_tools: tuple[str, ...] = ()
    service_frameworks: tuple[str, ...] = ()
    database_tools: tuple[str, ...] = ()
    notebooks: tuple[str, ...] = ()

    @property
    def console_script_map(self) -> dict[str, str]:
        return dict(self.console_scripts)


@dataclass(frozen=True, slots=True)
class PythonEnvironmentPlan:
    manager: str
    python: str
    run_prefix: tuple[str, ...]
    environment_root: str
    fingerprint: str
    fingerprint_inputs: tuple[str, ...]
    compatible: bool
    compatibility_detail: str
    bootstrap_python: str = ""


@dataclass(frozen=True, slots=True)
class PythonValidationScope:
    scope: str
    changed_paths: tuple[str, ...] = ()
    lint_targets: tuple[str, ...] = ()
    test_targets: tuple[str, ...] = ()
    full_tests: bool = True
    full_lint: bool = True
    run_build: bool = True
    reasons: tuple[str, ...] = ()


@dataclass(slots=True)
class PackageCheckOptions:
    import_smoke: bool = False
    install_dependencies: bool = False
    verify_all_wheels: bool = True
    rebuild_sdist: bool = False
    verify_cli: bool = False
    import_modules: list[str] = field(default_factory=list)
    console_scripts: list[str] = field(default_factory=list)
    reproducible: bool = False
    offline: bool = False


def _list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,\s]+", value) if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def load_pyproject(root: Path, *, strict: bool = True) -> dict[str, Any]:
    path = root / "pyproject.toml"
    if not path.is_file():
        return {}
    try:
        import tomllib
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        if not strict:
            return {}
        location = ""
        line = getattr(exc, "lineno", None)
        column = getattr(exc, "colno", None)
        if line is not None:
            location = f" at line {line}" + (f", column {column}" if column is not None else "")
        raise PythonProjectError(f"Invalid TOML in {path}{location}: {exc}") from exc
    if not isinstance(value, dict):
        raise PythonProjectError(f"{path} must contain a TOML table at the document root")
    return value


def _pyproject(root: Path) -> dict[str, Any]:
    """Backward-compatible strict pyproject reader."""
    return load_pyproject(root, strict=True)


def discover_python_roots(repo_root: Path) -> list[Path]:
    """Discover independent Python roots without walking generated environments."""
    root = repo_root.expanduser().resolve()
    candidates: set[Path] = set()
    marker_names = {
        "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile",
        "uv.lock", "poetry.lock", "pdm.lock", "tox.ini", "noxfile.py", "pytest.ini",
    }
    if any((root / marker).exists() for marker in marker_names):
        candidates.add(root)
    for marker in marker_names:
        for path in root.glob(f"**/{marker}"):
            if any(part in IGNORED_DIRECTORIES for part in path.relative_to(root).parts):
                continue
            candidates.add(path.parent.resolve())
    # A workspace root owns explicitly declared members; do not also return the
    # same members as ambiguous peers unless the repository has no root project.
    if root in candidates:
        try:
            data = load_pyproject(root)
            workspace = (((data.get("tool") or {}).get("uv") or {}).get("workspace") or {})
            patterns = _list(workspace.get("members")) if isinstance(workspace, dict) else []
            owned: set[Path] = set()
            for pattern in patterns:
                owned.update(path.parent.resolve() for path in root.glob(f"{pattern}/pyproject.toml"))
            candidates.difference_update(owned)
            candidates.add(root)
        except PythonProjectError:
            # Keep discovery useful; inspection will emit the precise parse error.
            pass
    return sorted(candidates)


def _tool_table(data: Mapping[str, Any], name: str) -> dict[str, Any]:
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    value = tool.get(name) if isinstance(tool, dict) else {}
    return dict(value) if isinstance(value, dict) else {}


def _project_dependencies(data: Mapping[str, Any]) -> list[str]:
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    values = _list(project.get("dependencies")) if isinstance(project, dict) else []
    optional = project.get("optional-dependencies") if isinstance(project, dict) else {}
    if isinstance(optional, dict):
        for group in optional.values():
            values.extend(_list(group))
    poetry = _tool_table(data, "poetry")
    deps = poetry.get("dependencies")
    if isinstance(deps, dict):
        values.extend(str(key) for key in deps)
    groups = poetry.get("group")
    if isinstance(groups, dict):
        for group in groups.values():
            if isinstance(group, dict) and isinstance(group.get("dependencies"), dict):
                values.extend(str(key) for key in group["dependencies"])
    return values


def _detect_manager(root: Path, data: Mapping[str, Any], configured: str) -> tuple[str, str]:
    manager = (configured or "auto").strip().lower()
    if manager not in SUPPORTED_MANAGERS:
        raise PythonProjectError(
            f"Unsupported Python environment manager {manager!r}; expected one of: "
            + ", ".join(sorted(SUPPORTED_MANAGERS))
        )
    lock = ""
    if manager != "auto":
        candidate = LOCK_FILES.get(manager, "")
        return manager, candidate if candidate and (root / candidate).is_file() else ""
    has_project_markers = any((root / name).exists() for name in DEPENDENCY_FILES)
    if not has_project_markers:
        return "venv", ""
    for candidate_manager, filename in LOCK_FILES.items():
        if (root / filename).is_file():
            return candidate_manager, filename
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    backend_hint = str((data.get("build-system") or {}).get("build-backend") or "") if isinstance(data.get("build-system"), dict) else ""
    if isinstance(tool, dict):
        if isinstance(tool.get("poetry"), dict) or "poetry" in backend_hint:
            return "poetry", ""
        if isinstance(tool.get("pdm"), dict) or "pdm" in backend_hint:
            return "pdm", ""
        if isinstance(tool.get("hatch"), dict) or "hatch" in backend_hint:
            return "hatch", ""
    if (root / "Pipfile").is_file():
        return "pipenv", ""
    return ("uv", "") if shutil.which("uv") else ("venv", "")


def _detect_framework(root: Path, data: Mapping[str, Any], configured: str) -> str:
    framework = (configured or "auto").strip().lower()
    if framework not in SUPPORTED_TEST_FRAMEWORKS:
        raise PythonProjectError(
            f"Unsupported Python test framework {framework!r}; expected one of: "
            + ", ".join(sorted(SUPPORTED_TEST_FRAMEWORKS))
        )
    if framework != "auto":
        return framework
    if (root / "tox.ini").is_file() or isinstance(_tool_table(data, "tox"), dict) and bool(_tool_table(data, "tox")):
        return "tox"
    if (root / "noxfile.py").is_file():
        return "nox"
    deps = " ".join(_project_dependencies(data)).lower()
    pytest_markers = any((root / name).is_file() for name in ("pytest.ini", "conftest.py")) or bool(_tool_table(data, "pytest"))
    if pytest_markers or re.search(r"(^|[^a-z0-9_-])pytest([^a-z0-9_-]|$)", deps):
        return "pytest"
    test_files = [*root.glob("test_*.py"), *root.glob("tests/test_*.py"), *root.glob("tests/**/*_test.py")]
    for path in test_files[:100]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "pytest" in text or "@pytest." in text:
            return "pytest"
        if "unittest" in text or "TestCase" in text:
            return "unittest"
    return "pytest" if test_files else "unittest"


def _workspace_members(root: Path, data: Mapping[str, Any]) -> tuple[str, ...]:
    uv = _tool_table(data, "uv")
    workspace = uv.get("workspace") if isinstance(uv, dict) else {}
    patterns = _list(workspace.get("members")) if isinstance(workspace, dict) else []
    members: list[str] = []
    for pattern in patterns:
        for path in sorted(root.glob(f"{pattern}/pyproject.toml")):
            try:
                members.append(path.parent.resolve().relative_to(root).as_posix())
            except ValueError:
                continue
    return tuple(dict.fromkeys(members))


def _source_roots(root: Path, data: Mapping[str, Any]) -> tuple[str, ...]:
    roots: list[Path] = []
    if (root / "src").is_dir():
        roots.append(root / "src")
    setuptools = _tool_table(data, "setuptools")
    package_dir = setuptools.get("package-dir")
    if isinstance(package_dir, dict):
        for value in package_dir.values():
            candidate = root / str(value)
            if candidate.is_dir():
                roots.append(candidate)
    if not roots:
        roots.append(root)
    return tuple(dict.fromkeys(path.resolve().relative_to(root).as_posix() or "." for path in roots))


def _import_packages(root: Path, source_roots: Iterable[str], package_name: str = "") -> tuple[str, ...]:
    packages: list[str] = []
    normalized_distribution = re.sub(r"[-.]+", "_", package_name).strip("_")
    ignored_modules = {
        "setup", "conftest", "noxfile", "manage", "tasks", "fabfile",
        "sitecustomize", "usercustomize",
    }
    for relative in source_roots:
        source = (root / relative).resolve()
        if not source.is_dir():
            continue
        source_is_root = source == root
        for child in sorted(source.iterdir()):
            if child.name.startswith(".") or child.name in IGNORED_DIRECTORIES:
                continue
            if child.is_file():
                if child.suffix != ".py":
                    continue
                name = child.stem
                if name in ignored_modules or name.startswith("test"):
                    continue
                if source_is_root and normalized_distribution and name != normalized_distribution:
                    continue
            elif child.is_dir():
                name = child.name
                if name.startswith("test") or not any(child.glob("**/*.py")):
                    continue
            else:
                continue
            if name.isidentifier():
                packages.append(name)
    return tuple(dict.fromkeys(packages))


def _console_scripts(data: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    scripts = project.get("scripts") if isinstance(project, dict) else {}
    values: dict[str, str] = {}
    if isinstance(scripts, dict):
        values.update({str(k): str(v) for k, v in scripts.items()})
    poetry = _tool_table(data, "poetry")
    poetry_scripts = poetry.get("scripts")
    if isinstance(poetry_scripts, dict):
        values.update({str(k): str(v) for k, v in poetry_scripts.items() if isinstance(v, str)})
    return tuple(sorted(values.items()))


def _ecosystem_detection(root: Path, data: Mapping[str, Any], backend: str) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    text = " ".join([backend, *_project_dependencies(data)]).lower()
    native = ""
    for marker, name in (
        ("maturin", "maturin"), ("setuptools-rust", "setuptools-rust"),
        ("scikit-build", "scikit-build-core"), ("meson-python", "meson-python"),
        ("cython", "cython"), ("pybind11", "pybind11"), ("cffi", "cffi"),
    ):
        if marker in text:
            native = name
            break
    generated: list[str] = []
    for marker, name in (("grpc", "grpc"), ("protobuf", "protobuf"), ("openapi", "openapi"), ("cython", "cython"), ("alembic", "alembic"), ("babel", "babel")):
        if marker in text:
            generated.append(name)
    services = [name for name in ("fastapi", "django", "flask", "celery", "gunicorn", "uvicorn") if name in text]
    databases = [name for name in ("alembic", "django", "sqlalchemy", "psycopg", "asyncpg") if name in text]
    notebooks = tuple(path.relative_to(root).as_posix() for path in sorted(root.glob("**/*.ipynb")) if not any(part in IGNORED_DIRECTORIES for part in path.relative_to(root).parts))
    return native, tuple(generated), tuple(services), tuple(databases), notebooks


def inspect_python_project(
    root: Path,
    configured_manager: str = "auto",
    configured_framework: str = "auto",
) -> PythonProjectInfo:
    root = root.expanduser().resolve()
    data = load_pyproject(root)
    manager, lock = _detect_manager(root, data, configured_manager)
    framework = _detect_framework(root, data, configured_framework)
    build = data.get("build-system") if isinstance(data.get("build-system"), dict) else {}
    backend = str(build.get("build-backend") or "setuptools.build_meta")
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    poetry = _tool_table(data, "poetry")
    name = str(project.get("name") or poetry.get("name") or root.name)
    poetry_dependencies = poetry.get("dependencies") if isinstance(poetry.get("dependencies"), dict) else {}
    requires_python = str(project.get("requires-python") or poetry_dependencies.get("python", ""))
    roots = _source_roots(root, data)
    dependency_files = tuple(name for name in DEPENDENCY_FILES if (root / name).exists())
    native, generated, services, databases, notebooks = _ecosystem_detection(root, data, backend)
    return PythonProjectInfo(
        root=root,
        manager=manager,
        framework=framework,
        build_backend=backend,
        package_name=name,
        lock_file=lock,
        requires_python=requires_python,
        source_roots=roots,
        import_packages=_import_packages(root, roots, name),
        console_scripts=_console_scripts(data),
        workspace_members=_workspace_members(root, data),
        dependency_files=dependency_files,
        native_backend=native,
        generated_tools=generated,
        service_frameworks=services,
        database_tools=databases,
        notebooks=notebooks,
    )


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", value)
    return tuple(int(item or 0) for item in match.groups()) if match else (0, 0, 0)


@lru_cache(maxsize=128)
def _interpreter_version(executable: str) -> tuple[tuple[int, int, int], str]:
    try:
        result = subprocess.run(
            [executable, "-c", "import platform; print(platform.python_version())"],
            text=True, capture_output=True, check=False, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (0, 0, 0), str(exc)
    text = (result.stdout or result.stderr).strip()
    return _version_tuple(text), text


def _specifier_satisfied(version: tuple[int, int, int], specifier: str) -> bool:
    if not specifier.strip():
        return True
    if version == (0, 0, 0):
        return False
    try:
        from packaging.specifiers import SpecifierSet  # type: ignore
        from packaging.version import Version  # type: ignore
        return Version(".".join(map(str, version))) in SpecifierSet(specifier)
    except Exception:
        pass
    for part in (item.strip() for item in specifier.split(",") if item.strip()):
        poetry = re.match(r"([~^])\s*(\d+(?:\.\d+){0,2})", part)
        if poetry:
            operator, raw = poetry.groups()
            wanted = _version_tuple(raw)
            components = len(raw.split("."))
            if version < wanted:
                return False
            if operator == "^":
                if wanted[0] > 0 and version[0] != wanted[0]:
                    return False
                if wanted[0] == 0 and wanted[1] > 0 and version[:2] != wanted[:2]:
                    return False
                if wanted[:2] == (0, 0) and version != wanted:
                    return False
            elif components >= 2 and version[:2] != wanted[:2]:
                return False
            continue
        match = re.match(r"(>=|<=|==|!=|>|<|~=)\s*(\d+(?:\.\d+){0,2})", part)
        if not match:
            continue
        op, raw = match.groups()
        wanted = _version_tuple(raw)
        if op == ">=" and not version >= wanted:
            return False
        if op == "<=" and not version <= wanted:
            return False
        if op == ">" and not version > wanted:
            return False
        if op == "<" and not version < wanted:
            return False
        if op == "==" and not version[:len(raw.split("."))] == wanted[:len(raw.split("."))]:
            return False
        if op == "!=" and version[:len(raw.split("."))] == wanted[:len(raw.split("."))]:
            return False
        if op == "~=" and not (version >= wanted and version[0] == wanted[0]):
            return False
    return True


def _python_version_hint(root: Path) -> str:
    path = root / ".python-version"
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return ""


def resolve_python_executable(root: Path, configured: str, environment: str = "") -> str:
    candidates: list[str] = []
    if environment:
        env_root = Path(environment).expanduser()
        if not env_root.is_absolute():
            env_root = root / env_root
        candidates.append(str(env_root / "bin" / "python"))
    configured = str(configured or "").strip()
    if configured:
        candidates.append(configured)
    hint = _python_version_hint(root)
    if hint:
        normalized = hint.removeprefix("python")
        candidates.extend([f"python{normalized}", f"python{'.'.join(normalized.split('.')[:2])}"])
    candidates.extend([str(root / ".venv" / "bin" / "python"), sys.executable, "python3"])
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_absolute() or "/" in candidate:
            if path.is_file() and os.access(path, os.X_OK):
                return str(path.resolve())
        else:
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
    return configured or "python3"


def _is_termux_host_python(value: str) -> bool:
    """Return whether *value* is a Termux-host Python path.

    These paths are valid on the Android/Termux host but are not visible inside
    Ubuntu chroots. Planning chroot validation commands with such absolute paths
    makes the host-side planner leak into the guest namespace.
    """

    normalized = str(value or "").strip()
    if not normalized:
        return False
    try:
        path = Path(normalized).expanduser()
    except (OSError, RuntimeError):
        return False
    parts = path.parts
    return (
        path.is_absolute()
        and len(parts) >= 7
        and parts[:6] == ("/", "data", "data", "com.termux", "files", "usr")
        and parts[6] == "bin"
        and path.name.startswith("python")
    )


def _is_chroot_execution_environment(value: str) -> bool:
    normalized = str(value or "").strip().lower().replace("_", "-")
    return normalized == "chroot"


def resolve_bootstrap_python(
    root: Path,
    configured: str,
    environment: str = "",
    *,
    execution_environment: str = "",
) -> str:
    """Resolve the interpreter used to bootstrap project environments.

    Chroot validation must not embed a Termux host Python path such as
    /data/data/com.termux/files/usr/bin/python3.13 inside the Ubuntu guest.
    """

    if _is_chroot_execution_environment(execution_environment) and _is_termux_host_python(configured):
        return "python3"
    resolved = resolve_python_executable(root, configured, environment)
    if _is_chroot_execution_environment(execution_environment) and _is_termux_host_python(resolved):
        return "python3"
    return resolved


@lru_cache(maxsize=32)
def _manager_version(manager: str) -> str:
    executable = manager if manager != "venv" else ""
    if not executable or not shutil.which(executable):
        return ""
    try:
        proc = _run_capture([executable, "--version"], timeout=15)
    except Exception:
        return ""
    return proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""


def environment_fingerprint(
    info: PythonProjectInfo,
    *,
    python: str,
    groups: Sequence[str] = (),
    extras: Sequence[str] = (),
    execution_environment: str = "",
) -> tuple[str, tuple[str, ...]]:
    inputs: list[str] = []
    files: dict[str, str] = {}
    for name in info.dependency_files:
        path = info.root / name
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        files[name] = digest
        inputs.append(name)
    version, version_text = _interpreter_version(python)
    payload = {
        "manager": info.manager,
        "manager_version": _manager_version(info.manager),
        "python": str(Path(python).expanduser()),
        "python_version": version_text,
        "requires_python": info.requires_python,
        "groups": sorted(groups),
        "extras": sorted(extras),
        "files": files,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "implementation": platform.python_implementation(),
        "execution_environment": execution_environment,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return digest, tuple(inputs)


def resolve_environment(
    info: PythonProjectInfo,
    *,
    configured_python: str = "python3",
    environment: str = "",
    groups: Sequence[str] = (),
    extras: Sequence[str] = (),
    execution_environment: str = "",
) -> PythonEnvironmentPlan:
    bootstrap_python = resolve_bootstrap_python(
        info.root,
        configured_python,
        "",
        execution_environment=execution_environment,
    )
    requested_environment = ""
    if environment:
        env_path = Path(environment).expanduser()
        if not env_path.is_absolute():
            env_path = info.root / env_path
        requested_environment = str(env_path.resolve())
    if info.manager == "venv":
        existing_dotvenv = info.root / ".venv"
        if requested_environment:
            environment_root = requested_environment
        elif _venv_python(existing_dotvenv).is_file():
            environment_root = str(existing_dotvenv.resolve())
        elif info.dependency_files:
            environment_root = str((info.root / ".devtool" / "venvs" / "python").resolve())
        else:
            environment_root = ""
        if environment_root:
            target_python = _venv_python(Path(environment_root))
            python = str(target_python)
            prefix = (python,)
            version_probe = python if target_python.is_file() else bootstrap_python
        else:
            python = bootstrap_python
            prefix = (python,)
            version_probe = python
    else:
        python = bootstrap_python
        environment_root = requested_environment
        if info.manager == "uv":
            prefix = ("uv", "run")
        elif info.manager == "poetry":
            prefix = ("poetry", "run")
        elif info.manager == "pdm":
            prefix = ("pdm", "run")
        elif info.manager == "pipenv":
            prefix = ("pipenv", "run")
        elif info.manager == "hatch":
            prefix = ("hatch", "run")
        else:
            prefix = (python,)
        version_probe = python
    fingerprint, inputs = environment_fingerprint(
        info, python=version_probe, groups=groups, extras=extras, execution_environment=execution_environment
    )
    version, version_text = _interpreter_version(version_probe)
    compatible = _specifier_satisfied(version, info.requires_python)
    detail = (
        f"Python {version_text or 'unknown'} satisfies {info.requires_python}"
        if compatible else f"Python {version_text or 'unknown'} does not satisfy requires-python {info.requires_python}"
    )
    return PythonEnvironmentPlan(
        info.manager, python, prefix, environment_root, fingerprint, inputs,
        compatible, detail, bootstrap_python,
    )


def python_module_argv(plan: PythonEnvironmentPlan, module: str, *args: str) -> list[str]:
    if plan.manager == "venv":
        return [plan.python, "-m", module, *args]
    return [*plan.run_prefix, "python", "-m", module, *args]


def python_script_argv(plan: PythonEnvironmentPlan, script: str, *args: str) -> list[str]:
    if plan.manager == "venv":
        return [plan.python, script, *args]
    return [*plan.run_prefix, "python", script, *args]


def python_executable_argv(plan: PythonEnvironmentPlan, executable: str, *args: str) -> list[str]:
    if plan.manager == "venv":
        sibling = Path(plan.python).parent / executable
        return [str(sibling), *args]
    return [*plan.run_prefix, executable, *args]


def python_cache_env(root: Path, execution_environment: str = "host") -> dict[str, str]:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", execution_environment or "host")
    cache = root / ".devtool" / "cache" / "python" / safe
    workers = os.environ.get("DEVTOOL_MAX_WORKERS", "1" if "termux" in safe or "chroot" in safe else "auto")
    if workers == "auto":
        workers = str(max(1, min(4, os.cpu_count() or 1)))
    abi = f"py{sys.version_info.major}{sys.version_info.minor}-{platform.machine()}"
    env = {
        "PIP_CACHE_DIR": str(cache / "pip"),
        "UV_CACHE_DIR": str(cache / "uv"),
        "MYPY_CACHE_DIR": str(cache / "mypy"),
        "RUFF_CACHE_DIR": str(cache / "ruff"),
        "PYTHONPYCACHEPREFIX": str(cache / "pycache"),
        "TOX_WORK_DIR": str(cache / "tox"),
        "PDM_CACHE_DIR": str(cache / "pdm"),
        "POETRY_CACHE_DIR": str(cache / "poetry"),
        "CARGO_TARGET_DIR": str(cache / "native" / abi / "cargo"),
        "SKBUILD_BUILD_DIR": str(cache / "native" / abi / "scikit-build"),
        "CMAKE_BUILD_PARALLEL_LEVEL": workers,
        "MAKEFLAGS": f"-j{workers}",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if "chroot" in safe or "termux" in safe:
        env.setdefault("LANG", "C.UTF-8")
        env.setdefault("LC_ALL", "C.UTF-8")
        env.setdefault("PYTHONUTF8", "1")
    return env


def write_environment_state(info: PythonProjectInfo, plan: PythonEnvironmentPlan, *, groups: Sequence[str] = (), extras: Sequence[str] = ()) -> Path:
    path = info.root / ".devtool" / "state" / "python" / "environment.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "created_at": int(time.time()),
        "project": info.package_name,
        "manager": info.manager,
        "python": plan.python,
        "environment_root": plan.environment_root,
        "fingerprint": plan.fingerprint,
        "fingerprint_inputs": list(plan.fingerprint_inputs),
        "requires_python": info.requires_python,
        "compatible": plan.compatible,
        "compatibility_detail": plan.compatibility_detail,
        "groups": list(groups),
        "extras": list(extras),
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def build_sync_command(
    info: PythonProjectInfo,
    plan: PythonEnvironmentPlan,
    *,
    groups: Sequence[str] = (),
    extras: Sequence[str] = (),
    locked: bool = True,
    offline: bool = False,
    install_project: bool = True,
    constraints: str = "",
) -> list[list[str]]:
    groups = list(dict.fromkeys(groups))
    extras = list(dict.fromkeys(extras))
    commands: list[list[str]] = []
    if info.manager == "uv":
        command = ["uv", "sync"]
        if locked and info.lock_file:
            command.append("--frozen")
        if offline:
            command.append("--offline")
        for group in groups:
            command.extend(["--group", group])
        for extra in extras:
            command.extend(["--extra", extra])
        if not install_project:
            command.append("--no-install-project")
        return [command]
    if info.manager == "poetry":
        command = ["poetry", "install", "--sync"]
        if groups:
            command.extend(["--with", ",".join(groups)])
        if extras:
            command.extend(["--extras", " ".join(extras)])
        if not install_project:
            command.append("--no-root")
        return [command]
    if info.manager == "pdm":
        command = ["pdm", "sync"]
        if locked and info.lock_file:
            command.append("--frozen-lockfile")
        if not install_project:
            command.append("--no-self")
        for group in groups:
            command.extend(["-G", group])
        return [command]
    if info.manager == "pipenv":
        command = ["pipenv", "sync"]
        if groups or (info.root / "Pipfile").is_file():
            command.append("--dev")
        if offline:
            command.append("--pypi-mirror=none")
        return [command]
    if info.manager == "hatch":
        return [["hatch", "env", "create"]]

    venv_root = Path(plan.environment_root) if plan.environment_root else info.root / ".devtool" / "venvs" / "python"
    venv_python = venv_root / "bin" / "python"
    if not venv_python.is_file():
        commands.append([plan.bootstrap_python or plan.python, "-m", "venv", str(venv_root)])
    pip_base = [str(venv_python), "-m", "pip", "install"]
    if offline:
        pip_base.append("--no-index")
    if constraints:
        pip_base.extend(["-c", constraints])
    requirements: list[str] = []
    if (info.root / "requirements-dev.txt").is_file() and groups:
        requirements.append("requirements-dev.txt")
    if (info.root / "requirements.txt").is_file():
        requirements.append("requirements.txt")
    for requirement in requirements:
        commands.append([*pip_base, "-r", requirement])
    if install_project and (info.root / "pyproject.toml").is_file():
        extra_text = f"[{','.join(extras)}]" if extras else ""
        commands.append([*pip_base, "-e", f".{extra_text}"])
    elif not requirements and (info.root / "setup.py").is_file():
        commands.append([*pip_base, "-e", "."])
    return commands


def python_validation_scope(root: Path, changed: Sequence[str], info: PythonProjectInfo) -> PythonValidationScope:
    normalized = tuple(dict.fromkeys(Path(value).as_posix().lstrip("./") for value in changed if str(value).strip()))
    if not normalized:
        return PythonValidationScope("python-full", reasons=("no changed-file inventory",))
    project_prefix = ""
    try:
        project_prefix = info.root.relative_to(root.resolve()).as_posix()
        if project_prefix == ".":
            project_prefix = ""
    except ValueError:
        project_prefix = ""
    local: list[str] = []
    for value in normalized:
        if project_prefix and value != project_prefix and not value.startswith(project_prefix + "/"):
            continue
        local.append(value[len(project_prefix):].lstrip("/") if project_prefix else value)
    if not local:
        return PythonValidationScope("python-unaffected", normalized, full_tests=False, full_lint=False, run_build=False, reasons=("artifact does not touch this Python target",))
    if all(Path(value).suffix.lower() in DOC_SUFFIXES or value.startswith(("docs/", ".github/")) for value in local):
        return PythonValidationScope("python-docs-only", normalized, full_tests=False, full_lint=False, run_build=False, reasons=("documentation-only changes",))
    critical_names = {
        "pyproject.toml", "setup.py", "setup.cfg", "tox.ini", "noxfile.py", "pytest.ini",
        "conftest.py", ".pre-commit-config.yaml", "MANIFEST.in", "uv.lock", "poetry.lock",
        "pdm.lock", "Pipfile", "Pipfile.lock", "requirements.txt", "requirements-dev.txt",
        "constraints.txt",
    }
    critical_prefixes = ("buildSrc/", "build-logic/", "tools/generate", "scripts/generate")
    for value in local:
        path = Path(value)
        if path.name in critical_names or path.name == "__init__.py" or value.startswith(critical_prefixes):
            return PythonValidationScope("python-full", normalized, reasons=(f"shared Python configuration changed: {value}",))
    changed_python = [value for value in local if Path(value).suffix == ".py"]
    if not changed_python:
        return PythonValidationScope("python-non-code", normalized, full_tests=False, full_lint=False, run_build=False, reasons=("no Python source or project metadata changed",))
    tests: list[str] = []
    for value in changed_python:
        path = Path(value)
        if path.name.startswith("test_") or path.name.endswith("_test.py") or "tests" in path.parts:
            tests.append(value)
            continue
        stem = path.stem
        candidates = [
            info.root / "tests" / f"test_{stem}.py",
            info.root / f"test_{stem}.py",
        ]
        for candidate in candidates:
            if candidate.is_file():
                tests.append(candidate.relative_to(info.root).as_posix())
        for candidate in info.root.glob(f"tests/**/test_*{stem}*.py"):
            tests.append(candidate.relative_to(info.root).as_posix())
    # Supplement filename heuristics with a lightweight import graph.  This
    # catches tests whose names do not mirror the implementation module.
    changed_modules: set[str] = set()
    for value in changed_python:
        absolute = (info.root / value).resolve()
        for source_root in info.source_roots:
            base = (info.root / source_root).resolve()
            try:
                relative = absolute.relative_to(base)
            except ValueError:
                continue
            parts = list(relative.with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts.pop()
            if parts:
                changed_modules.add(".".join(parts))
            break
    if changed_modules:
        for candidate in info.root.glob("tests/**/*.py"):
            if any(part in IGNORED_DIRECTORIES for part in candidate.relative_to(info.root).parts):
                continue
            try:
                tree = ast.parse(candidate.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError):
                continue
            imported: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
            if any(
                imported_name == module
                or imported_name.startswith(module + ".")
                or module.startswith(imported_name + ".")
                for imported_name in imported
                for module in changed_modules
            ):
                tests.append(candidate.relative_to(info.root).as_posix())
    tests = list(dict.fromkeys(tests))
    return PythonValidationScope(
        "python-affected",
        normalized,
        tuple(changed_python),
        tuple(tests),
        full_tests=not bool(tests),
        full_lint=False,
        run_build=False,
        reasons=(("mapped changed modules to tests",) if tests else ("no safe test mapping; retain full test suite",)),
    )


def _wheel_metadata(path: Path) -> tuple[dict[str, str], list[str], list[str], list[str]]:
    metadata: dict[str, str] = {}
    imports: list[str] = []
    scripts: list[str] = []
    tags: list[str] = []
    with zipfile.ZipFile(path) as archive:
        corrupt = archive.testzip()
        if corrupt:
            raise RuntimeError(f"corrupt member: {corrupt}")
        names = archive.namelist()
        required = (".dist-info/METADATA", ".dist-info/WHEEL", ".dist-info/RECORD")
        missing = [member for member in required if not any(name.endswith(member) for name in names)]
        if missing:
            raise RuntimeError("wheel metadata is missing: " + ", ".join(missing))
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        message = BytesParser(policy=email_policy).parsebytes(archive.read(metadata_name))
        metadata = {key: str(message.get(key) or "") for key in ("Name", "Version", "Requires-Python")}
        wheel_name = next(name for name in names if name.endswith(".dist-info/WHEEL"))
        wheel_message = BytesParser(policy=email_policy).parsebytes(archive.read(wheel_name))
        tags = list(wheel_message.get_all("Tag") or [])
        if not tags:
            raise RuntimeError("wheel declares no compatibility tags")
        record_name = next(name for name in names if name.endswith(".dist-info/RECORD"))
        record_rows = csv.reader(archive.read(record_name).decode("utf-8", "strict").splitlines())
        recorded_paths: set[str] = set()
        for row in record_rows:
            if not row:
                continue
            member_name = row[0]
            recorded_paths.add(member_name)
            if member_name not in names:
                raise RuntimeError(f"RECORD references missing member: {member_name}")
            digest_field = row[1] if len(row) > 1 else ""
            size_field = row[2] if len(row) > 2 else ""
            data = archive.read(member_name)
            if digest_field:
                algorithm, _, encoded = digest_field.partition("=")
                try:
                    expected = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
                    actual = hashlib.new(algorithm, data).digest()
                except (ValueError, TypeError) as exc:
                    raise RuntimeError(f"invalid RECORD digest for {member_name}: {exc}") from exc
                if actual != expected:
                    raise RuntimeError(f"RECORD digest mismatch: {member_name}")
            if size_field:
                try:
                    expected_size = int(size_field)
                except ValueError as exc:
                    raise RuntimeError(f"invalid RECORD size for {member_name}: {size_field}") from exc
                if len(data) != expected_size:
                    raise RuntimeError(f"RECORD size mismatch: {member_name}")
        missing_record_entries = [
            name for name in names
            if not name.endswith("/") and name not in recorded_paths
        ]
        if missing_record_entries:
            raise RuntimeError("RECORD omits wheel members: " + ", ".join(missing_record_entries[:10]))
        entry_name = next((name for name in names if name.endswith(".dist-info/entry_points.txt")), "")
        if entry_name:
            section = ""
            for line in archive.read(entry_name).decode("utf-8", "replace").splitlines():
                line = line.strip()
                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1]
                elif section == "console_scripts" and "=" in line:
                    scripts.append(line.split("=", 1)[0].strip())
        top_level = next((name for name in names if name.endswith(".dist-info/top_level.txt")), "")
        if top_level:
            imports.extend(line.strip() for line in archive.read(top_level).decode("utf-8", "replace").splitlines() if line.strip().isidentifier())
        if not imports:
            for name in names:
                if ".dist-info/" in name or ".data/" in name or name.endswith("/"):
                    continue
                first = name.split("/", 1)[0]
                if first.endswith(".py"):
                    first = Path(first).stem
                if first.isidentifier():
                    imports.append(first)
    return metadata, list(dict.fromkeys(imports)), list(dict.fromkeys(scripts)), tags


def _sdist_metadata(path: Path) -> dict[str, str]:
    names: list[str] = []
    pkg_info = b""
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            corrupt = archive.testzip()
            if corrupt:
                raise RuntimeError(f"corrupt member: {corrupt}")
            names = archive.namelist()
            info = next((name for name in names if name.endswith("/PKG-INFO") or name == "PKG-INFO"), "")
            if info:
                pkg_info = archive.read(info)
    else:
        with tarfile.open(path, "r:*") as archive:
            names = archive.getnames()
            info = next((member for member in archive.getmembers() if member.name.endswith("/PKG-INFO") or member.name == "PKG-INFO"), None)
            if info:
                handle = archive.extractfile(info)
                if handle:
                    pkg_info = handle.read()
    if not any(name.endswith(("/PKG-INFO", "/pyproject.toml", "/setup.py")) or name in {"PKG-INFO", "pyproject.toml", "setup.py"} for name in names):
        raise RuntimeError("sdist package metadata is missing")
    if not pkg_info:
        return {}
    message = BytesParser(policy=email_policy).parsebytes(pkg_info)
    return {key: str(message.get(key) or "") for key in ("Name", "Version", "Requires-Python")}


def package_records(dist: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not dist.is_dir():
        return records
    for path in sorted(dist.iterdir()):
        if not path.is_file() or not (path.suffix == ".whl" or path.name.endswith((".tar.gz", ".zip"))):
            continue
        record: dict[str, Any] = {
            "path": str(path), "type": "wheel" if path.suffix == ".whl" else "sdist",
            "valid": True, "checks": [], "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        try:
            if path.suffix == ".whl":
                metadata, imports, scripts, tags = _wheel_metadata(path)
                record.update({"metadata": metadata, "imports": imports, "console_scripts": scripts, "tags": tags})
            else:
                record["metadata"] = _sdist_metadata(path)
            record["checks"].append({"name": "archive", "status": "passed"})
        except Exception as exc:
            record["valid"] = False
            record["checks"].append({"name": "archive", "status": "failed", "detail": str(exc)})
        records.append(record)
    return records


def _venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _run_capture(argv: Sequence[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), cwd=cwd, env=dict(env) if env else None, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=timeout)


def _verify_wheel_install(
    item: dict[str, Any], *, options: PackageCheckOptions,
) -> dict[str, Any]:
    result_payload: dict[str, Any] = {"attempted": True, "returncode": None, "output": "", "imports": {}, "console_scripts": {}}
    with tempfile.TemporaryDirectory(prefix="devtool-python-wheel-") as temporary:
        env_root = Path(temporary) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(env_root)
        python = _venv_python(env_root)
        command = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
        if not options.install_dependencies:
            command.append("--no-deps")
        if options.offline:
            command.append("--no-index")
        command.append(str(item["path"]))
        proc = _run_capture(command, timeout=900)
        result_payload.update({"returncode": proc.returncode, "output": proc.stdout.strip()})
        if proc.returncode != 0:
            item["valid"] = False
            item["checks"].append({"name": "clean_install", "status": "failed", "detail": proc.stdout.strip()})
            return result_payload
        item["checks"].append({"name": "clean_install", "status": "passed"})
        imports = options.import_modules or list(item.get("imports") or [])
        if options.import_smoke:
            for module in imports:
                check = _run_capture([str(python), "-I", "-c", f"import {module}"], cwd=Path(temporary), timeout=60)
                status = "passed" if check.returncode == 0 else "failed"
                result_payload["imports"][module] = {"status": status, "output": check.stdout.strip()}
                item["checks"].append({"name": f"import:{module}", "status": status, "detail": check.stdout.strip()})
                if check.returncode != 0:
                    item["valid"] = False
        scripts = options.console_scripts or list(item.get("console_scripts") or [])
        if options.verify_cli:
            bin_dir = python.parent
            for script in scripts:
                executable = bin_dir / script
                checks: dict[str, Any] = {}
                for flag in ("--help", "--version"):
                    proc = _run_capture([str(executable), flag], cwd=Path(temporary), timeout=60)
                    checks[flag] = {"returncode": proc.returncode, "output": proc.stdout.strip()[:4000]}
                passed = any(value["returncode"] == 0 for value in checks.values())
                result_payload["console_scripts"][script] = checks
                item["checks"].append({"name": f"console_script:{script}", "status": "passed" if passed else "failed", "detail": "" if passed else json.dumps(checks)})
                if not passed:
                    item["valid"] = False
    return result_payload


def _rebuild_sdist(item: dict[str, Any], *, options: PackageCheckOptions) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="devtool-python-sdist-") as temporary:
        output = Path(temporary) / "wheelhouse"
        output.mkdir()
        command = [sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(output)]
        if options.offline:
            command.append("--no-index")
        command.append(str(item["path"]))
        proc = _run_capture(command, timeout=1200)
        wheels = sorted(output.glob("*.whl"))
        rebuilt: list[dict[str, Any]] = []
        source_identity = (
            str(item.get("metadata", {}).get("Name") or "").lower().replace("_", "-"),
            str(item.get("metadata", {}).get("Version") or ""),
        )
        for wheel in wheels:
            record = package_records(output)
            wheel_record = next((value for value in record if value.get("path") == str(wheel)), None)
            if wheel_record is None:
                continue
            rebuilt_identity = (
                str(wheel_record.get("metadata", {}).get("Name") or "").lower().replace("_", "-"),
                str(wheel_record.get("metadata", {}).get("Version") or ""),
            )
            identity_matches = not all(source_identity) or rebuilt_identity == source_identity
            if not identity_matches:
                wheel_record["valid"] = False
                wheel_record["checks"].append({
                    "name": "sdist_wheel_identity", "status": "failed",
                    "detail": f"sdist identity {source_identity} rebuilt as {rebuilt_identity}",
                })
            install = _verify_wheel_install(wheel_record, options=options) if wheel_record.get("valid") else {}
            rebuilt.append({"record": wheel_record, "install": install, "identity_matches": identity_matches})
        success = proc.returncode == 0 and bool(rebuilt) and all(value["record"].get("valid") for value in rebuilt)
        return {
            "returncode": 0 if success else (proc.returncode or 1),
            "build_returncode": proc.returncode,
            "output": proc.stdout.strip(),
            "wheels": [str(path) for path in wheels],
            "rebuilt": rebuilt,
        }


def verify_packages(root: Path, *, import_smoke: bool = False, options: PackageCheckOptions | None = None) -> tuple[dict[str, Any], int]:
    root = root.resolve()
    options = options or PackageCheckOptions(import_smoke=import_smoke)
    if import_smoke:
        options.import_smoke = True
    records = package_records(root / "dist")
    if not records:
        return {"count": 0, "valid": 0, "invalid": 0, "items": [], "error": "No wheel or source distribution found"}, 1
    twine_command = [sys.executable, "-m", "twine", "check", *[item["path"] for item in records]]
    twine_result: dict[str, Any] = {"available": False, "returncode": None, "output": "", "status": "skipped"}
    try:
        available = importlib.util.find_spec("twine") is not None  # type: ignore[attr-defined]
    except Exception:
        available = False
    if available:
        twine_result["available"] = True
        result = _run_capture(twine_command)
        twine_result.update({"returncode": result.returncode, "output": result.stdout.strip(), "status": "passed" if result.returncode == 0 else "failed"})
        for item in records:
            item["checks"].append({"name": "twine", "status": twine_result["status"], "detail": twine_result["output"] if result.returncode else ""})
            if result.returncode:
                item["valid"] = False

    wheel_results: list[dict[str, Any]] = []
    wheels = [item for item in records if item.get("type") == "wheel" and item.get("valid")]
    selected_wheels = wheels if options.verify_all_wheels else wheels[:1]
    for item in selected_wheels:
        wheel_results.append({"path": item["path"], **_verify_wheel_install(item, options=options)})

    rebuilds: list[dict[str, Any]] = []
    if options.rebuild_sdist:
        for item in [value for value in records if value.get("type") == "sdist" and value.get("valid")]:
            result = _rebuild_sdist(item, options=options)
            status = "passed" if result["returncode"] == 0 and result["wheels"] else "failed"
            item["checks"].append({"name": "sdist_rebuild", "status": status, "detail": result["output"] if status == "failed" else ""})
            if status == "failed":
                item["valid"] = False
            rebuilds.append({"path": item["path"], **result})

    identities = {
        (str(item.get("metadata", {}).get("Name") or "").lower().replace("_", "-"), str(item.get("metadata", {}).get("Version") or ""))
        for item in records if item.get("metadata")
    }
    metadata_consistent = len(identities) <= 1
    if not metadata_consistent:
        for item in records:
            item["valid"] = False
            item["checks"].append({"name": "metadata_consistency", "status": "failed", "detail": f"artifact identities disagree: {sorted(identities)}"})

    payload = {
        "count": len(records),
        "valid": sum(1 for item in records if item["valid"]),
        "invalid": sum(1 for item in records if not item["valid"]),
        "items": records,
        "twine": twine_result,
        "wheel_installs": wheel_results,
        "sdist_rebuilds": rebuilds,
        "metadata_consistent": metadata_consistent,
        "skipped": ["twine"] if not available else [],
    }
    return payload, 0 if payload["invalid"] == 0 else 1


def dependency_snapshot(plan: PythonEnvironmentPlan, root: Path) -> dict[str, Any]:
    script = (
        "import importlib.metadata as m,json,sys;"
        "print(json.dumps({'python':sys.executable,'version':sys.version.split()[0],"
        "'packages':sorted([{'name':d.metadata.get('Name',''),'version':d.version} for d in m.distributions()],key=lambda x:x['name'].lower())}))"
    )
    command = [plan.python, "-c", script] if plan.manager == "venv" else [*plan.run_prefix, "python", "-c", script]
    proc = _run_capture(command, cwd=root)
    if proc.returncode:
        return {"returncode": proc.returncode, "error": proc.stdout.strip(), "packages": []}
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"returncode": 1, "error": proc.stdout.strip(), "packages": []}
    payload["returncode"] = 0
    return payload


def clean_python(root: Path, *, categories: Sequence[str] = ("artifacts", "caches")) -> list[str]:
    root = root.resolve()
    selected = set(categories)
    names: set[str] = set()
    if "caches" in selected or "all" in selected:
        names.update({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".coverage", "htmlcov"})
    if "artifacts" in selected or "all" in selected:
        names.update({"build", "dist"})
    if "environments" in selected or "all" in selected:
        names.update({".tox", ".nox"})
    removed: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda value: len(value.parts), reverse=True):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in {".git", "vendor", "node_modules", "fixtures", "testdata"} for part in relative.parts[:-1]):
            continue
        if path.name not in names:
            continue
        # Never cross into nested projects unless they are the selected root.
        if any((parent / "pyproject.toml").is_file() for parent in path.parents if parent not in {root, path.parent} and root in parent.parents):
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(str(path))
        except OSError:
            continue
    if "artifacts" in selected or "all" in selected:
        for path in root.glob("*.egg-info"):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                removed.append(str(path))
    if "environments" in selected or "all" in selected:
        managed = root / ".devtool" / "venvs"
        if managed.is_dir():
            shutil.rmtree(managed, ignore_errors=True)
            removed.append(str(managed))
    return removed


def _missing_imports(output: str) -> list[str]:
    patterns = (
        r"ModuleNotFoundError:\s+No module named ['\"]([^'\"]+)",
        r"ImportError:\s+No module named\s+([^\s]+)",
    )
    values: list[str] = []
    for pattern in patterns:
        values.extend(match.group(1).split(".", 1)[0] for match in re.finditer(pattern, output))
    return list(dict.fromkeys(values))


def _validator_plan(root: Path, args: argparse.Namespace) -> tuple[PythonProjectInfo, PythonEnvironmentPlan]:
    info = inspect_python_project(root, args.manager, args.framework)
    plan = resolve_environment(
        info,
        configured_python=args.python,
        environment=args.environment,
        groups=args.group,
        extras=args.extra,
        execution_environment=os.environ.get("DEVTOOL_EXECUTION_CONTEXT", "host"),
    )
    if not plan.compatible:
        raise PythonProjectError(plan.compatibility_detail)
    return info, plan


def _requirement_from_mapping(name: str, value: Any) -> str:
    if isinstance(value, str):
        version = value.strip()
        return name if not version or version == "*" else f"{name}{version}"
    if not isinstance(value, dict):
        return name
    version = str(value.get("version") or "").strip()
    extras = _list(value.get("extras"))
    markers = str(value.get("markers") or "").strip()
    base = name + (f"[{','.join(extras)}]" if extras else "")
    if version and version != "*":
        base += version
    if markers:
        base += f"; {markers}"
    return base


def declared_group_dependencies(root: Path, groups: Sequence[str]) -> list[str]:
    if not groups:
        return []
    data = load_pyproject(root)
    result: list[str] = []
    pep735 = data.get("dependency-groups") if isinstance(data.get("dependency-groups"), dict) else {}
    for group in groups:
        values = pep735.get(group) if isinstance(pep735, dict) else None
        if isinstance(values, list):
            result.extend(str(item) for item in values if isinstance(item, str))
    poetry = _tool_table(data, "poetry")
    poetry_groups = poetry.get("group") if isinstance(poetry.get("group"), dict) else {}
    for group in groups:
        group_table = poetry_groups.get(group) if isinstance(poetry_groups, dict) else None
        dependencies = group_table.get("dependencies") if isinstance(group_table, dict) else None
        if isinstance(dependencies, dict):
            result.extend(_requirement_from_mapping(str(name), value) for name, value in dependencies.items() if str(name).lower() != "python")
    pdm = _tool_table(data, "pdm")
    pdm_groups = pdm.get("dev-dependencies") if isinstance(pdm.get("dev-dependencies"), dict) else {}
    for group in groups:
        values = pdm_groups.get(group) if isinstance(pdm_groups, dict) else None
        if isinstance(values, list):
            result.extend(str(item) for item in values if isinstance(item, str))
    return list(dict.fromkeys(item for item in result if item.strip()))


def _prepare_declared_validator_dependencies(root: Path, info: PythonProjectInfo, plan: PythonEnvironmentPlan, args: argparse.Namespace) -> PythonEnvironmentPlan:
    dependencies = list(dict.fromkeys([*args.dependency, *declared_group_dependencies(root, args.group)]))
    if not dependencies:
        return plan
    digest = hashlib.sha256(json.dumps({"fingerprint": plan.fingerprint, "dependencies": dependencies, "groups": args.group, "extras": args.extra, "install_project": args.install_project}, sort_keys=True).encode()).hexdigest()[:16]
    env_root = root / ".devtool" / "venvs" / "validators" / digest
    python = _venv_python(env_root)
    marker = env_root / ".devtool-ready"
    if not marker.is_file():
        venv.EnvBuilder(with_pip=True, clear=True).create(env_root)
        command = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
        if args.offline:
            command.append("--no-index")
        if args.install_project:
            install_target = f".[{','.join(args.extra)}]" if args.extra else "."
            command.extend(["-e", install_target])
        command.extend(dependencies)
        proc = _run_capture(command, cwd=root, timeout=1200)
        if proc.returncode:
            raise PythonProjectError("Unable to prepare declared validator dependencies:\n" + proc.stdout.strip())
        marker.write_text(plan.fingerprint + "\n", encoding="utf-8")
    return PythonEnvironmentPlan("venv", str(python), (str(python),), str(env_root), plan.fingerprint, plan.fingerprint_inputs, True, plan.compatibility_detail, plan.bootstrap_python)


def run_validator(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    try:
        info, plan = _validator_plan(root, args)
        if args.sync_dependencies:
            for command in build_sync_command(
                info, plan, groups=args.group, extras=args.extra, locked=not args.unlocked,
                offline=args.offline, install_project=args.install_project,
            ):
                proc = subprocess.run(command, cwd=root, check=False)
                if proc.returncode:
                    return proc.returncode
        plan = _prepare_declared_validator_dependencies(root, info, plan, args)
    except PythonProjectError as exc:
        print(f"Python validator environment error: {exc}", file=sys.stderr)
        return 86
    env = os.environ.copy()
    env.update(python_cache_env(root, os.environ.get("DEVTOOL_EXECUTION_CONTEXT", "host")))
    source_paths = [str((root / item).resolve()) for item in info.source_roots]
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join([*source_paths, *([existing] if existing else [])])
    command = python_script_argv(plan, str(args.script.resolve()))
    execution_cwd = args.cwd.resolve() if args.cwd else root
    try:
        proc = _run_capture(command, cwd=execution_cwd, env=env, timeout=args.timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Python validator could not start: {exc}", file=sys.stderr)
        print(f"Interpreter: {plan.python}", file=sys.stderr)
        return 86
    if proc.stdout:
        print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
    missing = _missing_imports(proc.stdout)
    if missing:
        print("\nPython validator environment is incomplete.", file=sys.stderr)
        print(f"Interpreter: {plan.python}", file=sys.stderr)
        print("Missing imports: " + ", ".join(missing), file=sys.stderr)
        print("Declare them in the project's test/dev dependency group or validation.script.dependencies.", file=sys.stderr)
    return proc.returncode


def run_tool(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    try:
        info = inspect_python_project(root, args.manager, args.framework)
        plan = resolve_environment(info, configured_python=args.python, environment=args.environment)
    except PythonProjectError as exc:
        print(f"DEVTOOL_PYTHON_TOOL_RESULT {json.dumps({'tool': args.tool, 'errors': 1, 'warnings': 0, 'issues': [{'message': str(exc)}]})}")
        return 2
    command = python_module_argv(plan, args.tool, *args.tool_args)
    env = os.environ.copy()
    env.update(python_cache_env(root, os.environ.get("DEVTOOL_EXECUTION_CONTEXT", "host")))
    try:
        proc = _run_capture(command, cwd=root, env=env, timeout=args.timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        payload = {"tool": args.tool, "returncode": 127, "errors": 1, "warnings": 0, "issues": [{"message": str(exc)}]}
        print("DEVTOOL_PYTHON_TOOL_RESULT " + json.dumps(payload, ensure_ascii=False))
        return 127
    output = proc.stdout
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    issues: list[dict[str, str]] = []
    for line in output.splitlines():
        if re.search(r"(^|:)\s*(error|warning)(\[|:|\s)", line, re.I) or re.match(r".+:\d+:\d+:\s+[A-Z]\d+", line):
            issues.append({"message": line[:2000]})
    errors = len(issues) if proc.returncode else 0
    payload = {"tool": args.tool, "returncode": proc.returncode, "errors": errors, "warnings": 0, "issues": issues[:200]}
    print("DEVTOOL_PYTHON_TOOL_RESULT " + json.dumps(payload, ensure_ascii=False))
    return proc.returncode


def _write_snapshot(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    info = inspect_python_project(root, args.manager, args.framework)
    plan = resolve_environment(info, configured_python=args.python, environment=args.environment, groups=args.group, extras=args.extra, execution_environment=args.execution_environment)
    if not plan.compatible:
        print(plan.compatibility_detail, file=sys.stderr)
        return 2
    path = write_environment_state(info, plan, groups=args.group, extras=args.extra)
    snapshot = dependency_snapshot(plan, root)
    snapshot_path = path.with_name("installed-distributions.json")
    snapshot_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Python environment fingerprint: {plan.fingerprint}")
    print(f"State: {path}")
    return 0 if snapshot.get("returncode") == 0 else 1



def prepare_dist(root: Path) -> Path:
    dist = root.resolve() / "dist"
    if dist.exists():
        if dist.is_dir():
            shutil.rmtree(dist)
        else:
            dist.unlink()
    dist.mkdir(parents=True, exist_ok=True)
    return dist


def _package_check(args: argparse.Namespace) -> int:
    options = PackageCheckOptions(
        import_smoke=args.import_smoke,
        install_dependencies=args.install_dependencies,
        verify_all_wheels=not args.first_wheel_only,
        rebuild_sdist=args.rebuild_sdist,
        verify_cli=args.verify_cli,
        import_modules=args.import_module,
        console_scripts=args.console_script,
        reproducible=args.reproducible,
        offline=args.offline,
    )
    payload, rc = verify_packages(args.root.resolve(), options=options)
    report = args.report or args.root.resolve() / ".devtool" / "reports" / "python-packages.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("DEVTOOL_PYTHON_PACKAGE_RESULT " + json.dumps(payload, ensure_ascii=False))
    return rc



def audit_source_tree(root: Path, info: PythonProjectInfo) -> tuple[dict[str, Any], int]:
    root = root.resolve()
    issues: list[dict[str, str]] = []
    top_level: set[str] = set()
    for source_root in info.source_roots:
        base = (root / source_root).resolve()
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.name.startswith("."):
                continue
            name = child.stem if child.is_file() and child.suffix == ".py" else child.name
            if name.isidentifier():
                top_level.add(name)
    stdlib = set(getattr(sys, "stdlib_module_names", set()))
    for name in sorted(top_level & stdlib):
        issues.append({"code": "STDLIB_SHADOW", "path": name, "message": f"Project module {name!r} shadows a Python standard-library module."})

    data = load_pyproject(root)
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    declared_version = str(project.get("version") or "")
    dynamic = set(_list(project.get("dynamic"))) if isinstance(project, dict) else set()
    source_versions: dict[str, str] = {}
    version_pattern = re.compile(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", re.M)
    for package in info.import_packages:
        for source_root in info.source_roots:
            base = root / source_root
            for candidate in (base / package / "__init__.py", base / package / "_version.py", base / f"{package}.py"):
                if not candidate.is_file():
                    continue
                try:
                    match = version_pattern.search(candidate.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    match = None
                if match:
                    source_versions[candidate.relative_to(root).as_posix()] = match.group(1)
    if declared_version and "version" not in dynamic:
        for path, version in source_versions.items():
            if version != declared_version:
                issues.append({"code": "VERSION_MISMATCH", "path": path, "message": f"Source version {version} does not match project version {declared_version}."})
        try:
            tag_proc = _run_capture(["git", "-C", str(root), "describe", "--tags", "--exact-match", "HEAD"], timeout=10)
            tag = tag_proc.stdout.strip().removeprefix("v") if tag_proc.returncode == 0 else ""
        except Exception:
            tag = ""
        if tag and tag != declared_version:
            issues.append({"code": "VERSION_TAG_MISMATCH", "path": ".git", "message": f"Git tag {tag} does not match project version {declared_version}."})
        for changelog_name in ("CHANGELOG.md", "CHANGES.md", "HISTORY.md"):
            changelog = root / changelog_name
            if not changelog.is_file():
                continue
            try:
                heading = re.search(r"^#{1,3}\s+(?:\[)?v?(\d+\.\d+(?:\.\d+)?[^]\s]*)", changelog.read_text(encoding="utf-8", errors="replace"), re.M)
            except OSError:
                heading = None
            if heading and heading.group(1) != declared_version:
                issues.append({"code": "VERSION_CHANGELOG_MISMATCH", "path": changelog_name, "message": f"Latest changelog version {heading.group(1)} does not match project version {declared_version}."})
            break

    stale_generated: list[dict[str, str]] = []
    generation_pairs: list[tuple[Path, Path]] = []
    for proto in root.glob("**/*.proto"):
        generation_pairs.append((proto, proto.with_name(proto.stem + "_pb2.py")))
    for pyx in root.glob("**/*.pyx"):
        generation_pairs.extend([(pyx, pyx.with_suffix(".c")), (pyx, pyx.with_suffix(".cpp"))])
    for source, generated in generation_pairs:
        if any(part in IGNORED_DIRECTORIES for part in source.relative_to(root).parts):
            continue
        if generated.is_file() and source.stat().st_mtime > generated.stat().st_mtime + 1:
            item = {"source": source.relative_to(root).as_posix(), "generated": generated.relative_to(root).as_posix()}
            stale_generated.append(item)
            issues.append({"code": "STALE_GENERATED", "path": item["generated"], "message": f"Generated output is older than {item['source']}."})

    payload = {
        "package": info.package_name,
        "declared_version": declared_version,
        "source_versions": source_versions,
        "stdlib_shadowing": sorted(top_level & stdlib),
        "stale_generated": stale_generated,
        "issues": issues,
        "errors": len(issues),
    }
    return payload, 1 if issues else 0


def _changed_lines(root: Path, base: str) -> dict[str, set[int]]:
    try:
        proc = _run_capture(["git", "-C", str(root), "diff", "--unified=0", base, "--", "*.py"], timeout=60)
    except Exception:
        return {}
    if proc.returncode:
        return {}
    current = ""
    result: dict[str, set[int]] = {}
    for line in proc.stdout.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            result.setdefault(current, set())
            continue
        match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if match and current:
            start = int(match.group(1))
            count = int(match.group(2) or 1)
            result[current].update(range(start, start + count))
    return result


def check_diff_coverage(root: Path, report: Path, *, base: str, minimum: float) -> tuple[dict[str, Any], int]:
    changed = _changed_lines(root, base)
    if not report.is_file():
        payload = {"error": f"Coverage report not found: {report}", "percent": 0.0, "minimum": minimum}
        return payload, 1
    try:
        import xml.etree.ElementTree as ET
        xml_root = ET.parse(report).getroot()
    except Exception as exc:
        return {"error": str(exc), "percent": 0.0, "minimum": minimum}, 1
    hits: dict[str, dict[int, int]] = {}
    for class_node in xml_root.findall(".//class"):
        filename = str(class_node.attrib.get("filename") or "").lstrip("./")
        line_hits: dict[int, int] = {}
        for line in class_node.findall("./lines/line"):
            try:
                line_hits[int(line.attrib["number"])] = int(line.attrib.get("hits", "0"))
            except (KeyError, ValueError):
                continue
        hits[filename] = line_hits
    covered = 0
    executable = 0
    details: list[dict[str, Any]] = []
    for path, lines in changed.items():
        candidates = [path, path.removeprefix("src/")]
        file_hits = next((hits[name] for name in candidates if name in hits), {})
        relevant = sorted(line for line in lines if line in file_hits)
        file_covered = sum(1 for line in relevant if file_hits[line] > 0)
        executable += len(relevant)
        covered += file_covered
        details.append({"path": path, "changed_executable": len(relevant), "covered": file_covered})
    percent = 100.0 if executable == 0 else covered * 100.0 / executable
    payload = {"base": base, "minimum": minimum, "percent": round(percent, 2), "covered": covered, "executable": executable, "files": details}
    return payload, 0 if percent + 1e-9 >= minimum else 1


def write_sbom(root: Path, info: PythonProjectInfo, plan: PythonEnvironmentPlan) -> tuple[Path, int]:
    snapshot = dependency_snapshot(plan, root)
    components = [
        {"type": "library", "name": item.get("name", ""), "version": item.get("version", ""), "purl": f"pkg:pypi/{str(item.get('name', '')).lower().replace('_', '-')}@{item.get('version', '')}"}
        for item in snapshot.get("packages", []) if item.get("name")
    ]
    payload = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{hashlib.sha256(plan.fingerprint.encode()).hexdigest()[:32]}",
        "version": 1,
        "metadata": {"component": {"type": "application", "name": info.package_name}, "properties": [{"name": "devtool:environment-fingerprint", "value": plan.fingerprint}]},
        "components": components,
    }
    path = root / ".devtool" / "reports" / "python-sbom.cdx.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path, 0 if snapshot.get("returncode") == 0 else 1


def _normalized_artifact(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if path.suffix in {".whl", ".zip"}:
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                if name.endswith("/"):
                    continue
                data = archive.read(name)
                # RECORD contains the wheel's own hashes and is expected to vary
                # only when another member differs; compare it last via content.
                result[name] = hashlib.sha256(data).hexdigest()
        return result
    with tarfile.open(path, "r:*") as archive:
        for member in sorted(archive.getmembers(), key=lambda item: item.name):
            if not member.isfile():
                continue
            handle = archive.extractfile(member)
            if handle:
                result[member.name] = hashlib.sha256(handle.read()).hexdigest()
    return result


def check_reproducible_build(root: Path, info: PythonProjectInfo, plan: PythonEnvironmentPlan) -> tuple[dict[str, Any], int]:
    with tempfile.TemporaryDirectory(prefix="devtool-python-repro-") as temporary:
        temp = Path(temporary)
        outputs: list[Path] = []
        logs: list[str] = []
        env = os.environ.copy()
        env.setdefault("SOURCE_DATE_EPOCH", "946684800")
        for index in (1, 2):
            source = temp / f"source-{index}"
            shutil.copytree(
                root,
                source,
                ignore=shutil.ignore_patterns(
                    ".git", ".devtool", ".venv", "venv", "build", "dist",
                    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                    ".tox", ".nox", "*.egg-info",
                ),
            )
            output = temp / f"dist-{index}"
            output.mkdir()
            command = python_module_argv(plan, "build", "--outdir", str(output))
            proc = _run_capture(command, cwd=source, env=env, timeout=1800)
            logs.append(proc.stdout[-10000:])
            if proc.returncode:
                return {"returncode": proc.returncode, "logs": logs, "identical": False}, proc.returncode
            outputs.append(output)
        first = {path.name: _normalized_artifact(path) for path in outputs[0].iterdir() if path.is_file()}
        second = {path.name: _normalized_artifact(path) for path in outputs[1].iterdir() if path.is_file()}
        identical = first == second
        differences = sorted(set(first) ^ set(second))
        if not differences:
            differences = [name for name in sorted(first) if first.get(name) != second.get(name)]
        payload = {"identical": identical, "differences": differences, "artifacts": sorted(first), "logs": logs}
        return payload, 0 if identical else 1


def check_notebooks(root: Path, notebooks: Sequence[str], *, execute: bool, plan: PythonEnvironmentPlan) -> tuple[dict[str, Any], int]:
    issues: list[dict[str, str]] = []
    checked = 0
    executed = 0
    for value in notebooks:
        path = (root / value).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            issues.append({"path": value, "message": "Notebook path escapes the project root"})
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            issues.append({"path": value, "message": str(exc)})
            continue
        checked += 1
        cells = payload.get("cells") if isinstance(payload, dict) else None
        if not isinstance(cells, list):
            issues.append({"path": value, "message": "Notebook has no cells array"})
            continue
        execution_counts = [cell.get("execution_count") for cell in cells if isinstance(cell, dict) and cell.get("cell_type") == "code" and cell.get("execution_count") is not None]
        if execution_counts and execution_counts != sorted(execution_counts):
            issues.append({"path": value, "message": "Notebook execution counts are out of order"})
        if execute:
            with tempfile.TemporaryDirectory(prefix="devtool-notebook-") as temporary:
                temp = Path(temporary)
                copy = temp / path.name
                shutil.copy2(path, copy)
                command = python_module_argv(
                    plan, "jupyter", "nbconvert", "--to", "notebook",
                    "--execute", "--output", "executed.ipynb",
                    "--output-dir", str(temp), str(copy),
                )
                proc = _run_capture(command, cwd=root, timeout=1800)
                if proc.returncode:
                    issues.append({"path": value, "message": proc.stdout[-4000:]})
                else:
                    executed += 1
    return {"checked": checked, "executed": executed, "issues": issues, "errors": len(issues)}, 1 if issues else 0



def check_service_lifecycle(
    root: Path,
    *,
    start_command: str,
    readiness_url: str = "",
    readiness_host: str = "",
    readiness_port: int = 0,
    check_command: str = "",
    timeout: int = 60,
) -> tuple[dict[str, Any], int]:
    started = time.monotonic()
    process = subprocess.Popen(
        shlex.split(start_command), cwd=root, env=os.environ.copy(),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        start_new_session=True,
    )
    ready = False
    detail = ""
    check_payload: dict[str, Any] = {}
    try:
        while time.monotonic() - started < timeout:
            if process.poll() is not None:
                detail = f"service exited with code {process.returncode} before readiness"
                break
            try:
                if readiness_url:
                    with urllib.request.urlopen(readiness_url, timeout=2) as response:
                        ready = 200 <= int(response.status) < 500
                elif readiness_host and readiness_port:
                    with socket.create_connection((readiness_host, readiness_port), timeout=2):
                        ready = True
                else:
                    ready = time.monotonic() - started >= 2
            except (OSError, urllib.error.URLError):
                ready = False
            if ready:
                break
            time.sleep(0.5)
        if not ready and not detail:
            detail = f"service did not become ready within {timeout}s"
        if ready and check_command:
            check = _run_capture(shlex.split(check_command), cwd=root, timeout=timeout)
            check_payload = {"returncode": check.returncode, "output": check.stdout[-10000:]}
            if check.returncode:
                detail = f"service check failed with code {check.returncode}"
                ready = False
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            except Exception:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except Exception:
                    process.kill()
        try:
            output, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate(timeout=5)
    payload = {
        "ready": ready, "detail": detail, "pid": process.pid,
        "duration_seconds": round(time.monotonic() - started, 3),
        "check": check_payload, "output": (output or "")[-10000:],
    }
    return payload, 0 if ready else 1


def _service_command(args: argparse.Namespace) -> int:
    payload, rc = check_service_lifecycle(
        args.root, start_command=args.start, readiness_url=args.url,
        readiness_host=args.host, readiness_port=args.port,
        check_command=args.check, timeout=args.timeout,
    )
    print("DEVTOOL_PYTHON_SERVICE_RESULT " + json.dumps(payload, ensure_ascii=False))
    return rc


def _source_audit_command(args: argparse.Namespace) -> int:
    info = inspect_python_project(args.root, args.manager, args.framework)
    payload, rc = audit_source_tree(args.root, info)
    report = args.root / ".devtool" / "reports" / "python-source-audit.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("DEVTOOL_PYTHON_AUDIT_RESULT " + json.dumps(payload, ensure_ascii=False))
    return rc


def _diff_coverage_command(args: argparse.Namespace) -> int:
    payload, rc = check_diff_coverage(args.root, args.report, base=args.base, minimum=args.minimum)
    print("DEVTOOL_PYTHON_DIFF_COVERAGE_RESULT " + json.dumps(payload, ensure_ascii=False))
    return rc


def _sbom_command(args: argparse.Namespace) -> int:
    info = inspect_python_project(args.root, args.manager, args.framework)
    plan = resolve_environment(info, configured_python=args.python, environment=args.environment)
    path, rc = write_sbom(args.root, info, plan)
    print(f"Python SBOM: {path}")
    return rc


def _reproducible_command(args: argparse.Namespace) -> int:
    info = inspect_python_project(args.root, args.manager, args.framework)
    plan = resolve_environment(info, configured_python=args.python, environment=args.environment)
    payload, rc = check_reproducible_build(args.root, info, plan)
    report = args.root / ".devtool" / "reports" / "python-reproducible-build.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("DEVTOOL_PYTHON_REPRODUCIBLE_RESULT " + json.dumps(payload, ensure_ascii=False))
    return rc


def _notebook_command(args: argparse.Namespace) -> int:
    info = inspect_python_project(args.root, args.manager, args.framework)
    plan = resolve_environment(info, configured_python=args.python, environment=args.environment)
    notebooks = args.notebook or list(info.notebooks)
    payload, rc = check_notebooks(args.root, notebooks, execute=args.execute, plan=plan)
    print("DEVTOOL_PYTHON_NOTEBOOK_RESULT " + json.dumps(payload, ensure_ascii=False))
    return rc

def main(raw_args: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Devtool Python support helper")
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("package-check")
    verify.add_argument("root", type=Path)
    verify.add_argument("--report", type=Path, default=None)
    verify.add_argument("--import-smoke", action="store_true")
    verify.add_argument("--install-dependencies", action="store_true")
    verify.add_argument("--first-wheel-only", action="store_true")
    verify.add_argument("--rebuild-sdist", action="store_true")
    verify.add_argument("--verify-cli", action="store_true")
    verify.add_argument("--import-module", action="append", default=[])
    verify.add_argument("--console-script", action="append", default=[])
    verify.add_argument("--reproducible", action="store_true")
    verify.add_argument("--offline", action="store_true")

    clean = sub.add_parser("clean")
    clean.add_argument("root", type=Path)
    clean.add_argument("--category", action="append", choices=("artifacts", "caches", "environments", "all"), default=[])

    prepare = sub.add_parser("prepare-dist")
    prepare.add_argument("root", type=Path)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("root", type=Path)
    inspect.add_argument("--manager", default="auto")
    inspect.add_argument("--framework", default="auto")

    snapshot = sub.add_parser("environment-snapshot")
    snapshot.add_argument("root", type=Path)
    snapshot.add_argument("--manager", default="auto")
    snapshot.add_argument("--framework", default="auto")
    snapshot.add_argument("--python", default="python3")
    snapshot.add_argument("--environment", default="")
    snapshot.add_argument("--group", action="append", default=[])
    snapshot.add_argument("--extra", action="append", default=[])
    snapshot.add_argument("--execution-environment", default="host")

    validator = sub.add_parser("run-validator")
    validator.add_argument("root", type=Path)
    validator.add_argument("script", type=Path)
    validator.add_argument("--cwd", type=Path, default=None)
    validator.add_argument("--manager", default="auto")
    validator.add_argument("--framework", default="auto")
    validator.add_argument("--python", default="python3")
    validator.add_argument("--environment", default="")
    validator.add_argument("--group", action="append", default=[])
    validator.add_argument("--extra", action="append", default=[])
    validator.add_argument("--dependency", action="append", default=[])
    validator.add_argument("--sync-dependencies", action="store_true")
    validator.add_argument("--install-project", action=argparse.BooleanOptionalAction, default=True)
    validator.add_argument("--unlocked", action="store_true")
    validator.add_argument("--offline", action="store_true")
    validator.add_argument("--timeout", type=int, default=900)

    tool = sub.add_parser("run-tool")
    tool.add_argument("root", type=Path)
    tool.add_argument("tool")
    tool.add_argument("tool_args", nargs=argparse.REMAINDER)
    tool.add_argument("--manager", default="auto")
    tool.add_argument("--framework", default="auto")
    tool.add_argument("--python", default="python3")
    tool.add_argument("--environment", default="")
    tool.add_argument("--timeout", type=int, default=900)

    audit = sub.add_parser("source-audit")
    audit.add_argument("root", type=Path)
    audit.add_argument("--manager", default="auto")
    audit.add_argument("--framework", default="auto")

    diff_cov = sub.add_parser("diff-coverage")
    diff_cov.add_argument("root", type=Path)
    diff_cov.add_argument("report", type=Path)
    diff_cov.add_argument("--base", default="HEAD")
    diff_cov.add_argument("--minimum", type=float, required=True)

    sbom = sub.add_parser("sbom")
    sbom.add_argument("root", type=Path)
    sbom.add_argument("--manager", default="auto")
    sbom.add_argument("--framework", default="auto")
    sbom.add_argument("--python", default="python3")
    sbom.add_argument("--environment", default="")

    reproducible = sub.add_parser("reproducible-build")
    reproducible.add_argument("root", type=Path)
    reproducible.add_argument("--manager", default="auto")
    reproducible.add_argument("--framework", default="auto")
    reproducible.add_argument("--python", default="python3")
    reproducible.add_argument("--environment", default="")

    notebook = sub.add_parser("notebook-check")
    notebook.add_argument("root", type=Path)
    notebook.add_argument("--manager", default="auto")
    notebook.add_argument("--framework", default="auto")
    notebook.add_argument("--python", default="python3")
    notebook.add_argument("--environment", default="")
    notebook.add_argument("--notebook", action="append", default=[])
    notebook.add_argument("--execute", action="store_true")

    service = sub.add_parser("service-check")
    service.add_argument("root", type=Path)
    service.add_argument("--start", required=True)
    service.add_argument("--url", default="")
    service.add_argument("--host", default="")
    service.add_argument("--port", type=int, default=0)
    service.add_argument("--check", default="")
    service.add_argument("--timeout", type=int, default=60)

    args = parser.parse_args(raw_args)
    try:
        if args.command == "package-check":
            return _package_check(args)
        if args.command == "clean":
            removed = clean_python(args.root, categories=args.category or ("artifacts", "caches"))
            print(json.dumps({"removed": removed}, indent=2))
            return 0
        if args.command == "prepare-dist":
            print(f"Prepared Python distribution directory: {prepare_dist(args.root)}")
            return 0
        if args.command == "inspect":
            print(json.dumps(asdict(inspect_python_project(args.root, args.manager, args.framework)), indent=2, default=str))
            return 0
        if args.command == "environment-snapshot":
            return _write_snapshot(args)
        if args.command == "run-validator":
            return run_validator(args)
        if args.command == "run-tool":
            return run_tool(args)
        if args.command == "source-audit":
            return _source_audit_command(args)
        if args.command == "diff-coverage":
            return _diff_coverage_command(args)
        if args.command == "sbom":
            return _sbom_command(args)
        if args.command == "reproducible-build":
            return _reproducible_command(args)
        if args.command == "notebook-check":
            return _notebook_command(args)
        if args.command == "service-check":
            return _service_command(args)
    except PythonProjectError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
