"""R35-R1: source-audit sibling imports and pinned uv targeted tests."""
from pathlib import Path
from unittest.mock import patch

import pytest

from devtool.models import ProjectConfig
from devtool.project_runners import PythonRunner
from devtool.python_support import audit_source_tree, inspect_python_project


def sample(tmp_path: Path, module: str = "helper") -> tuple[Path, ProjectConfig]:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='1.0'\n", encoding="utf-8"
    )
    (tmp_path / "devtool-test-requirements.lock").write_text("pytest==9.0.2\n", encoding="utf-8")
    folder = tmp_path / "tools"
    folder.mkdir()
    (folder / "runner.py").write_text(f"from {module} import run\n", encoding="utf-8")
    (folder / "helper.py").write_text("def run(): return 1\n", encoding="utf-8")
    config = ProjectConfig(
        repo_root=tmp_path, kind="python",
        runner_options={"python": {"manager": "uv", "test_framework": "pytest",
                                   "test_requirements": "devtool-test-requirements.lock"}},
    )
    return folder, config


def test_audit_accepts_real_sibling_tools_script_without_suppressing_other_imports(tmp_path):
    folder, config = sample(tmp_path)
    info = inspect_python_project(tmp_path, "uv", "pytest")
    payload, status = audit_source_tree(tmp_path, info)
    assert status == 0, payload["issues"]
    assert not any(issue["path"] == "tools/runner.py" for issue in payload["issues"])
    (folder / "runner.py").write_text("from helper import run\nimport undeclared_demo_wheel\n")
    payload, status = audit_source_tree(tmp_path, info)
    assert status != 0
    assert any(issue["code"] == "PYTHON_UNDECLARED_IMPORT"
               and issue["path"] == "tools/runner.py" for issue in payload["issues"])


def test_audit_refuses_symlinked_sibling_import(tmp_path):
    folder, config = sample(tmp_path)
    (folder / "helper.py").unlink()
    outside = tmp_path / "outside.py"
    outside.write_text("def run(): return 1\n")
    (folder / "helper.py").symlink_to(outside)
    payload, status = audit_source_tree(tmp_path, inspect_python_project(tmp_path, "uv", "pytest"))
    assert status != 0
    assert any(issue["path"] == "tools/runner.py" for issue in payload["issues"])


def test_uv_pytest_uses_pinned_test_only_lock_and_preserves_test_selection(tmp_path):
    folder, config = sample(tmp_path)
    runner = PythonRunner()
    spec = runner._pytest_spec(config)
    assert spec.argv[:4] == ["uv", "run", "--with-requirements",
                              str(tmp_path / "devtool-test-requirements.lock")]
    assert spec.argv[4:7] == ["python", "-m", "pytest"]
    assert any(x.startswith("--junitxml=") for x in spec.argv)


@pytest.mark.parametrize("invalid", ["../outside.lock", "missing.lock", "/etc/passwd", "bad name.txt"])
def test_uv_pytest_requirements_refuses_missing_or_unsafe_paths(tmp_path, invalid):
    folder, config = sample(tmp_path)
    config.runner_options["python"]["test_requirements"] = invalid
    with pytest.raises(SystemExit, match="test_requirements"):
        PythonRunner()._pytest_spec(config)


def test_uv_pytest_requirements_refuses_symlink(tmp_path):
    folder, config = sample(tmp_path)
    (tmp_path / "devtool-test-requirements.lock").unlink()
    (tmp_path / "external.lock").write_text("pytest==9.0.2\n")
    (tmp_path / "devtool-test-requirements.lock").symlink_to(tmp_path / "external.lock")
    with pytest.raises(SystemExit, match="test_requirements"):
        PythonRunner()._pytest_spec(config)


def test_existing_artifact_test_executor_accepts_explicit_uv_selected_tests(tmp_path):
    """A pre-overlay installed Devtool must be able to plan pinned tests directly."""
    from unittest.mock import patch
    from devtool.artifact_tests import run_artifact_tests
    tests = ["tests/test_r35_termux_validation.py", "tests/test_r35_verified_import.py"]
    command = ["uv", "run", "--with-requirements", "devtool-test-requirements.lock",
               "python", "-m", "pytest", "--tb=short", "-ra", *tests]
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1.0'\n")
    manifest = {"target": "", "validation": {"required": True, "test": False,
        "tests": [{"id": "r35-targeted-pytest", "runner": "python", "command": command,
                   "required": True}]}}
    with patch("devtool.artifact_tests._run_specs", return_value=([], "")) as run:
        run_artifact_tests(tmp_path, manifest)
    specs = run.call_args.args[1]
    assert len(specs) == 1
    assert specs[0].argv == command
    assert specs[0].metadata["artifact_validation_test"] is True
    assert specs[0].metadata["required"] is True
