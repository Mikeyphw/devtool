from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]

FRESH_PROCESS_NODEIDS = [
    # Fresh-process proof is intentionally milestone-sized. Full implementation
    # suites remain available as roadmap-env-x01 / roadmap-env-x02 reseal tests.
    "tests/test_env_x01_host_privilege_foundation.py::test_explicit_sparse_linux_environment_does_not_leak_android_parent_platform",
    "tests/test_env_x01_host_privilege_foundation.py::test_repo_config_cannot_grant_privilege",
    "tests/test_env_x01_host_privilege_foundation.py::test_proot_uid_zero_never_satisfies_real_host_privilege",
    "tests/test_env_x02_language_environment_convergence.py::test_mise_receipt_captures_version_trust_exact_version_backend",
    "tests/test_env_x02_language_environment_convergence.py::test_asdf_explicit_provider_uses_tool_versions_and_exec",
    "tests/test_env_x02_language_environment_convergence.py::test_pyenv_explicit_provider_resolves_runtime_and_wraps",
    "tests/test_env_x02_language_environment_convergence.py::test_direnv_cannot_override_pyenv_version",
    "tests/test_env_x02_language_environment_convergence.py::test_toolchain_wrapper_reverification_uses_authoritative_execution_path_when_spec_env_omits_path",
]


def _fresh_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV",
        "MISE_CONFIG_FILE", "MISE_ENV", "ASDF_CONFIG_FILE", "ASDF_DATA_DIR", "ASDF_DIR",
        "PYENV_VERSION", "PYENV_DIR", "PYENV_ROOT", "DIRENV_DIR", "DIRENV_CONFIG",
        "DEVTOOL_DIRENV", "DEVTOOL_DIRENV_AUTHORIZED",
    ):
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _test_declaration(test_id: str) -> dict[str, object]:
    payload = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
    for item in payload.get("test", []):
        if str(item.get("id") or "") == test_id:
            return dict(item)
    raise AssertionError(f"missing first-class test declaration {test_id}")


def test_env_g1_fresh_process_milestone_contract() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", *FRESH_PROCESS_NODEIDS],
        cwd=ROOT,
        env=_fresh_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stdout
    assert re.search(r"\b8 passed\b", proc.stdout), proc.stdout


def test_env_x02_first_class_contract_collects_full_closure() -> None:
    declaration = _test_declaration("roadmap-env-x02")
    command = [str(item) for item in declaration.get("command", [])]
    assert command[:4] == ["python", "-m", "pytest", "-q"]
    proc = subprocess.run(
        [*command, "--collect-only"],
        cwd=ROOT,
        env=_fresh_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout
    match = re.search(r"(\d+) tests collected", proc.stdout)
    assert match, proc.stdout
    assert int(match.group(1)) >= 270, proc.stdout


def test_env_gate_ledgers_do_not_overclaim_later_owned_work() -> None:
    x01 = json.loads((ROOT / "ENV_X01_PROMISE_LEDGER.json").read_text(encoding="utf-8"))
    x02 = json.loads((ROOT / "ENV_X02_PROMISE_LEDGER.json").read_text(encoding="utf-8"))
    assert {f"ENV-P{i:03d}" for i in range(1, 16)} <= set(x01["promises_delivered"])
    delivered = set(x02["delivered"])
    assert "ENV-P029" not in delivered
    assert {"ENV-P105", "ENV-P109", "ENV-P110", "ENV-P117", "ENV-P122", "ENV-P128", "ENV-P130"} <= delivered
    pending = x02["foundations_not_finally_closed"]
    for key in ("ENV-P043", "ENV-P049", "ENV-P113", "ENV-P125", "ENV-P131"):
        assert key in pending


def test_manager_owned_environment_conflict_depends_on_explicit_config_provenance(tmp_path: Path) -> None:
    import pytest
    from devtool.models import ProjectConfig
    from devtool.python_support import PythonProjectError
    from devtool.runners import build_specs

    (tmp_path / "pyproject.toml").write_text("[tool.hatch]\n", encoding="utf-8")
    options = {"python": {"manager": "hatch", "environment": ".devtool/venvs/python"}}
    config = ProjectConfig(
        repo_root=tmp_path,
        project_root=str(tmp_path),
        kind="python",
        runner_options=options,
        configured_runner_options=options,
    )
    with pytest.raises(PythonProjectError, match="Explicit \\[python\\]\\.environment"):
        build_specs(config, "restore")


def test_manager_owned_stale_default_environment_is_not_treated_as_explicit(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from unittest.mock import patch

    from devtool.models import ProjectConfig
    from devtool.project_runners import PythonRunner

    (tmp_path / "pyproject.toml").write_text("[tool.hatch]\n", encoding="utf-8")
    options = {"python": {"manager": "hatch", "environment": ".devtool/venvs/python"}}
    config = ProjectConfig(
        repo_root=tmp_path,
        project_root=str(tmp_path),
        kind="python",
        runner_options=options,
        configured_runner_options={},
    )
    captured = {}

    fake_composition = SimpleNamespace(identity="gate", require_valid=lambda: None, require_runtime_tool=lambda *a, **k: "")

    def fake_resolve(_info, request, **_kwargs):
        captured["environment"] = request.environment
        return SimpleNamespace(compatible=True, compatibility_detail="")

    runner = PythonRunner()
    with patch("devtool.toolchains.selected_toolchain_manager", return_value="system"), \
         patch("devtool.environment_composition.build_environment_composition_plan", return_value=fake_composition), \
         patch("devtool.python_environment.resolve_python_environment", side_effect=fake_resolve):
        runner._plan(config, action="restore")

    assert captured["environment"] == ""


def test_rust_validation_remains_rust_owned(tmp_path: Path, monkeypatch) -> None:
    from devtool.models import ProjectConfig
    from devtool.runners import build_validation_specs

    (tmp_path / "Cargo.toml").write_text("[package]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
    (tmp_path / "Cargo.lock").write_text("", encoding="utf-8")
    monkeypatch.setenv("DEVTOOL_MAX_WORKERS", "2")
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="rust")
    specs = build_validation_specs(config)
    assert len(specs) == 1
    assert specs[0].name == "cargo-validation"
    assert specs[0].argv[:3] == ["cargo", "test", "--all-targets"]
    assert not any("go" in str(value).lower() for value in specs[0].metadata.get("environment_domains", {}))
