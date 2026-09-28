from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests._compat import host_is_termux, portable_shell_argv, shared_tool_cache_env
from tests.fake_toolchains import install_fake_go

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_pytest_runtime_exposes_termux_capabilities_and_strict_slow_budget() -> None:
    conftest = read("tests/conftest.py")
    pytest_config = read("pyproject.toml")
    # Marker policy is static/fail-closed so pytest validates it during collection.
    assert 'requires_tool(name)' in pytest_config
    assert 'requires_posix' in pytest_config
    assert 'not_termux(reason)' in pytest_config
    assert 'slow_external' in pytest_config
    assert 'integration:' in pytest_config
    assert 'external:' in pytest_config
    assert 'system:' in pytest_config
    assert 'devtool_check(id)' in pytest_config
    assert 'requires_git:' in pytest_config
    assert '--strict-markers' in pytest_config
    assert '--strict-config' in pytest_config
    assert '--devtool-slow-threshold' in conftest
    assert '--devtool-strict-slow' in conftest
    assert '--devtool-lane' in conftest
    assert 'policy_for_pytest_item' in conftest
    assert 'report.outcome = "failed"' in conftest


def test_shared_tool_cache_is_private_and_session_reusable(tmp_path: Path) -> None:
    env = shared_tool_cache_env(tmp_path / "cache")
    assert set(env) >= {"GOCACHE", "GOMODCACHE", "GRADLE_USER_HOME", "UV_CACHE_DIR", "PIP_CACHE_DIR", "npm_config_cache"}
    for value in env.values():
        path = Path(value)
        assert path.is_dir()
        assert path.is_relative_to(tmp_path)


def test_host_detection_matches_termux_prefixes_without_real_device_env() -> None:
    assert host_is_termux({"PREFIX": "/data/data/com.termux/files/usr"}) is True
    assert host_is_termux({"HOME": "/data/data/com.termux/files/home"}) is True
    assert host_is_termux({"TERMUX_VERSION": "0.118"}) is True
    assert host_is_termux({"PREFIX": "/usr", "HOME": "/tmp/home"}) is False


def test_portable_shell_argv_uses_existing_shell() -> None:
    argv = portable_shell_argv("printf ok")
    assert argv[-2:] == ["-c", "printf ok"]
    assert Path(argv[0]).name in {"sh", "bash", "dash", "busybox"} or argv[0].endswith("/sh")


def test_fake_go_shim_models_build_metadata_without_real_go(tmp_path: Path, monkeypatch) -> None:
    go = install_fake_go(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    (project / "go.mod").write_text("module example.com/demo\n\ngo 1.23\n", encoding="utf-8")
    (project / "main.go").write_text("package main\nfunc main(){}\n", encoding="utf-8")
    output = tmp_path / "demo"
    built = subprocess.run([str(go), "build", "-o", str(output), "."], cwd=project, text=True, capture_output=True, check=False)
    assert built.returncode == 0, built.stderr
    meta = subprocess.run([str(go), "version", "-m", str(output)], cwd=project, text=True, capture_output=True, check=False)
    assert meta.returncode == 0, meta.stderr
    assert "mod\texample.com/demo" in meta.stdout
    assert "GOOS=linux" in meta.stdout
