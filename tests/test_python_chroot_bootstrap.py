from __future__ import annotations

from pathlib import Path

from devtool.python_support import (
    PythonProjectInfo,
    python_cache_env,
    resolve_bootstrap_python,
    resolve_environment,
)


def _info(root: Path) -> PythonProjectInfo:
    return PythonProjectInfo(
        root=root,
        manager="venv",
        framework="pytest",
        build_backend="setuptools.build_meta",
        package_name="sample",
        lock_file="",
        requires_python=">=3.8",
        dependency_files=("pyproject.toml",),
    )


def test_chroot_bootstrap_python_does_not_leak_termux_host_path(tmp_path: Path) -> None:
    host_python = "/data/data/com.termux/files/usr/bin/python3.13"

    resolved = resolve_bootstrap_python(
        tmp_path,
        host_python,
        execution_environment="chroot",
    )

    assert resolved == "python3"


def test_chroot_environment_plan_uses_guest_bootstrap_python(tmp_path: Path) -> None:
    plan = resolve_environment(
        _info(tmp_path),
        configured_python="/data/data/com.termux/files/usr/bin/python3.13",
        environment=str(tmp_path / ".devtool" / "venvs" / "rmx"),
        execution_environment="chroot",
    )

    assert plan.bootstrap_python == "python3"
    assert plan.python == str(tmp_path / ".devtool" / "venvs" / "rmx" / "bin" / "python")


def test_python_cache_env_for_chroot_forces_utf8(tmp_path: Path) -> None:
    env = python_cache_env(tmp_path, "chroot")

    assert env["LANG"] == "C.UTF-8"
    assert env["LC_ALL"] == "C.UTF-8"
    assert env["PYTHONUTF8"] == "1"
