from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile

import pytest

from devtool.artifacts import artifact_validation_policy, artifact_validation_pytest_selection, _validate_validation_policy
from devtool.cli import _apply_runner_cli_options, build_parser
from devtool.desktop.command_model import DevtoolCommandDraft
from devtool.diagnostics import parse_issues
from devtool.interactive.validation_wizard import ValidationDraft
from devtool.models import ProjectConfig
from devtool.project_runners import PythonRunner
from devtool.test_selection import (
    discover_pytest_runtime,
    resolve_test_selection,
)


def _python_project(root: Path, *, selection: dict | None = None, python: dict | None = None) -> ProjectConfig:
    (root / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1'\ndependencies=['pytest']\n",
        encoding="utf-8",
    )
    tests = root / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_demo.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.unit\n"
        "def test_login(): pass\n\n"
        "class TestUsers:\n"
        "    def test_create(self): pass\n",
        encoding="utf-8",
    )
    options = {"python": {"manager": "venv", "test_framework": "pytest", **(python or {})}}
    if selection is not None:
        options["test_selection"] = selection
    return ProjectConfig(repo_root=root, project_root=str(root), kind="python", runner_options=options)


def test_cli_accepts_repeated_pytest_tests_and_native_filters() -> None:
    args = build_parser().parse_args([
        "validate",
        "--pytest-test", "tests/test_demo.py::test_login",
        "--pytest-test", "tests/test_demo.py::TestUsers::test_create",
        "--pytest-marker", "unit",
        "--pytest-marker", "not network",
        "--pytest-k", "login or create",
        "--pytest-arg=--maxfail=1",
        "--pytest-arg=-x",
    ])
    assert args.pytest_tests == [
        "tests/test_demo.py::test_login",
        "tests/test_demo.py::TestUsers::test_create",
    ]
    assert args.pytest_markers == ["unit", "not network"]
    assert args.pytest_k == "login or create"
    assert args.pytest_args == ["--maxfail=1", "-x"]


def test_direct_test_command_exposes_same_pytest_flags() -> None:
    args = build_parser().parse_args([
        "test",
        "--pytest-test", "tests/test_demo.py::test_login",
        "--pytest-include-defaults",
    ])
    assert args.pytest_tests == ["tests/test_demo.py::test_login"]
    assert args.pytest_include_defaults is True


def test_cli_pytest_selection_is_written_to_runner_options() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _python_project(root)
        args = build_parser().parse_args([
            "validate",
            "--pytest-test", "tests/test_demo.py::test_login",
            "--pytest-marker", "unit",
            "--pytest-k", "login",
            "--pytest-arg=--maxfail=1",
        ])
        args._devtool_cli_pytest_test_explicit = True
        _apply_runner_cli_options(config, args)
        selection = resolve_test_selection(config)
    assert selection.pytest_tests == ["tests/test_demo.py::test_login"]
    assert selection.pytest_markers == ["unit"]
    assert selection.pytest_k == "login"
    assert selection.pytest_args == ["--maxfail=1"]
    assert selection.pytest_source == "cli"


def test_project_pytest_defaults_are_supported() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _python_project(root, python={
            "pytest": {
                "tests": ["tests/test_demo.py::test_login"],
                "markers": ["unit"],
                "k": "login",
                "args": ["--maxfail=1"],
            }
        })
        selection = resolve_test_selection(config)
    assert selection.pytest_tests == ["tests/test_demo.py::test_login"]
    assert selection.pytest_markers == ["unit"]
    assert selection.pytest_k == "login"
    assert selection.pytest_args == ["--maxfail=1"]
    assert selection.pytest_source == "project-config"


def test_profile_pytest_tests_override_project_defaults() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _python_project(root, selection={"profile": "quick"}, python={
            "pytest": {"tests": ["tests/test_demo.py::test_login"]}
        })
        config.runner_options["test_profiles"] = {
            "quick": {"pytest_tests": ["tests/test_demo.py::TestUsers::test_create"]}
        }
        selection = resolve_test_selection(config)
    assert selection.pytest_tests == ["tests/test_demo.py::TestUsers::test_create"]
    assert selection.pytest_source == "profile:quick"


def test_explicit_pytest_tests_override_profile_and_project_defaults() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _python_project(root, selection={
            "profile": "quick",
            "pytest_tests": ["tests/test_demo.py::test_login"],
            "pytest_source": "artifact-manifest",
        }, python={"pytest": {"tests": ["tests/default.py"]}})
        config.runner_options["test_profiles"] = {
            "quick": {"pytest_tests": ["tests/profile.py"]}
        }
        selection = resolve_test_selection(config)
    assert selection.pytest_tests == ["tests/test_demo.py::test_login"]
    assert selection.pytest_source == "artifact-manifest"


def test_exact_pytest_selection_suppresses_affected_default_targets() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _python_project(root, selection={
            "pytest_tests": ["tests/test_demo.py::test_login"],
            "pytest_source": "cli",
        })
        spec = PythonRunner()._pytest_spec(config, targets=["tests/test_demo.py"])
    assert "tests/test_demo.py::test_login" in spec.argv
    assert "tests/test_demo.py" not in spec.argv
    assert spec.metadata["pytest_defaults_suppressed"] is True
    assert spec.metadata["pytest_selection_mode"] == "explicit"


def test_pytest_include_defaults_keeps_affected_targets() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _python_project(root, selection={
            "pytest_tests": ["tests/test_demo.py::test_login"],
            "pytest_include_defaults": True,
        })
        spec = PythonRunner()._pytest_spec(config, targets=["tests/test_demo.py"])
    assert "tests/test_demo.py" in spec.argv
    assert "tests/test_demo.py::test_login" in spec.argv
    assert spec.metadata["pytest_defaults_suppressed"] is False


def test_pytest_marker_k_and_args_are_structured_argv() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _python_project(root, selection={
            "pytest_tests": ["tests/test_demo.py::test_login"],
            "pytest_markers": ["unit", "not network"],
            "pytest_k": "login",
            "pytest_args": ["--maxfail=1", "-x"],
        })
        spec = PythonRunner()._pytest_spec(config)
    marker_index = spec.argv.index("-m", spec.argv.index("pytest") + 1) if spec.argv.count("-m") > 1 else spec.argv.index("-m")
    marker_expr = spec.argv[marker_index + 1]
    assert "(unit)" in marker_expr and "(not network)" in marker_expr
    k_index = spec.argv.index("-k")
    assert spec.argv[k_index + 1] == "login"
    assert spec.argv[-2:] == ["--maxfail=1", "-x"]


def test_native_pytest_collection_preserves_parametrized_node_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _python_project(root)
        monkeypatch.setattr(
            "devtool.test_selection.subprocess.run",
            lambda *args, **kwargs: SimpleNamespace(
                returncode=0,
                stdout=(
                    "tests/test_demo.py::test_login[param-a]\n"
                    "tests/test_demo.py::test_login[param-b]\n"
                    "tests/test_demo.py::TestUsers::test_create\n"
                    "3 tests collected in 0.01s\n"
                ),
            ),
        )
        result = discover_pytest_runtime(config)
    assert result.source == "pytest-collect"
    assert [item.id for item in result.tests] == [
        "tests/test_demo.py::TestUsers::test_create",
        "tests/test_demo.py::test_login[param-a]",
        "tests/test_demo.py::test_login[param-b]",
    ]


def test_native_collection_falls_back_to_source_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _python_project(root)
        monkeypatch.setattr(
            "devtool.test_selection.subprocess.run",
            lambda *args, **kwargs: SimpleNamespace(returncode=4, stdout="collection import failure"),
        )
        result = discover_pytest_runtime(config)
    assert result.source == "source-fallback"
    assert result.error == "collection import failure"
    assert {item.id for item in result.tests} == {
        "tests/test_demo.py::test_login",
        "tests/test_demo.py::TestUsers::test_create",
    }


def test_artifact_manifest_normalizes_pytest_selection() -> None:
    manifest = {
        "validation": {
            "pytest_tests": ["tests/test_a.py::test_a", "tests/test_a.py::test_a"],
            "pytest_markers": ["unit", "not network"],
            "pytest_k": "auth",
            "pytest_args": ["--maxfail=1"],
            "pytest_include_defaults": False,
        }
    }
    policy = artifact_validation_policy(manifest)
    assert policy["pytest_tests"] == ["tests/test_a.py::test_a"]
    assert artifact_validation_pytest_selection(manifest)["pytest_k"] == "auth"


def test_artifact_manifest_rejects_invalid_pytest_shape() -> None:
    manifest = {
        "schema_version": 2,
        "kind": "devtool.overlay",
        "validation": {"pytest_tests": ["tests/test_a.py", 42]},
        "files": [],
    }
    messages: list[str] = []
    ok = _validate_validation_policy(manifest, messages, bundle=False)
    assert ok is False
    assert any("pytest_tests entries must be strings" in item for item in messages)


def test_validation_draft_emits_repeatable_pytest_flags() -> None:
    draft = ValidationDraft(
        pytest_tests=["tests/test_a.py::test_a", "tests/test_b.py::test_b"],
        pytest_markers=["unit"],
        pytest_k="auth",
        pytest_args=["--maxfail=1"],
    )
    argv = draft.command_argv()
    assert argv.count("--pytest-test") == 2
    assert [argv[index + 1] for index, value in enumerate(argv) if value == "--pytest-test"] == [
        "tests/test_a.py::test_a",
        "tests/test_b.py::test_b",
    ]
    assert "--pytest-marker" in argv
    assert "--pytest-k" in argv
    assert "--pytest-arg" in argv


def test_desktop_command_model_emits_pytest_flags() -> None:
    draft = DevtoolCommandDraft(
        repository=".",
        operation="validate",
        pytest_tests=["tests/test_demo.py::test_login"],
        pytest_markers=["unit"],
        pytest_k="login",
        pytest_args=["--maxfail=1"],
    )
    argv = draft.operation_argv()
    assert argv[:3] == ["validate", "--pytest-test", "tests/test_demo.py::test_login"]
    assert "--pytest-marker" in argv
    assert "--pytest-k" in argv
    assert "--pytest-arg" in argv


def test_pytest_selector_not_found_becomes_structured_diagnostic(tmp_path: Path) -> None:
    issues = parse_issues(
        tmp_path,
        "test",
        "ERROR: not found: tests/test_demo.py::test_logni\n(no match in any of [<Module test_demo.py>])\n",
    )
    assert any(item.code == "PYTEST_SELECTOR_NOT_FOUND" for item in issues)


def test_pytest_no_tests_becomes_structured_diagnostic(tmp_path: Path) -> None:
    issues = parse_issues(tmp_path, "test", "no tests ran in 0.01s\n")
    assert any(item.code == "PYTEST_NO_TESTS" for item in issues)


def test_legacy_pytest_validation_still_keeps_default_target_when_no_exact_selection() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _python_project(root)
        spec = PythonRunner()._pytest_spec(config, targets=["tests/test_demo.py"])
    assert "tests/test_demo.py" in spec.argv
    assert spec.metadata["pytest_defaults_suppressed"] is False


def test_repository_config_parses_python_pytest_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.config import load_project_config

    (tmp_path / ".devtool.toml").write_text(
        """
[project]
kind = "python"

[python]
manager = "venv"
test_framework = "pytest"

[python.pytest]
tests = ["tests/test_demo.py::test_login"]
markers = ["unit"]
k = "login"
args = ["--maxfail=1"]
include_defaults = true
""".strip() + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEVTOOL_GLOBAL_CONFIG", str(tmp_path / "missing-global.toml"))
    config = load_project_config(str(tmp_path), None)
    selection = resolve_test_selection(config)
    assert selection.pytest_tests == ["tests/test_demo.py::test_login"]
    assert selection.pytest_markers == ["unit"]
    assert selection.pytest_k == "login"
    assert selection.pytest_args == ["--maxfail=1"]
    assert selection.pytest_include_defaults is True


def test_artifact_pytest_defaults_apply_but_cli_field_wins() -> None:
    from devtool.cli import _apply_declared_pytest_selection

    manifest = {
        "validation": {
            "pytest_tests": ["tests/artifact.py::test_artifact"],
            "pytest_markers": ["artifact"],
            "pytest_k": "artifact",
            "pytest_args": ["--maxfail=1"],
            "pytest_include_defaults": True,
        }
    }
    args = SimpleNamespace(
        pytest_tests=["tests/cli.py::test_cli"],
        pytest_markers=[],
        pytest_k="",
        pytest_args=[],
        pytest_include_defaults=False,
        _devtool_cli_pytest_test_explicit=True,
        _devtool_cli_pytest_marker_explicit=False,
        _devtool_cli_pytest_k_explicit=False,
        _devtool_cli_pytest_arg_explicit=False,
    )
    _apply_declared_pytest_selection(args, manifest, active=True)
    assert args.pytest_tests == ["tests/cli.py::test_cli"]
    assert args.pytest_markers == ["artifact"]
    assert args.pytest_k == "artifact"
    assert args.pytest_args == ["--maxfail=1"]
    assert args.pytest_include_defaults is True


def test_deferred_apply_context_round_trips_pytest_selection(tmp_path: Path) -> None:
    from devtool.cli import _rehydrate_apply_validation_context, _validation_apply_context

    args = SimpleNamespace(
        command="apply-overlay",
        artifact="overlay.zip",
        commit="",
        keep_artifact=False,
        delete_artifact_on_failure=False,
        no_artifact_script=False,
        no_manifest_check=False,
        ignore_checksum=False,
        validate_only=False,
        defer_validate=True,
        rollback_on_failure=False,
        no_rollback_on_failure=False,
        validation_resource_profile="automatic",
        test_task=[],
        pytest_tests=["tests/test_demo.py::test_login"],
        pytest_markers=["unit"],
        pytest_k="login",
        pytest_args=["--maxfail=1"],
        pytest_include_defaults=False,
        target="",
        solution="",
        _devtool_artifact_type="overlay",
        _devtool_artifact_sha="deadbeef",
        _devtool_artifact_size=1,
        _devtool_artifact_preview={},
        _devtool_validation_tasks_declared=False,
        _devtool_cli_test_task_explicit=False,
        _devtool_cli_pytest_test_explicit=True,
        _devtool_cli_pytest_marker_explicit=True,
        _devtool_cli_pytest_k_explicit=True,
        _devtool_cli_pytest_arg_explicit=True,
    )
    manifest = {"validation": {"required": True}}
    context = _validation_apply_context(args, transaction_path=str(tmp_path / "tx"), manifest=manifest)
    assert context["pytest_tests"] == ["tests/test_demo.py::test_login"]
    assert context["pytest_selection_source"] == "cli"

    config = _python_project(tmp_path)
    resumed = SimpleNamespace(
        pytest_tests=[], pytest_markers=[], pytest_k="", pytest_args=[], pytest_include_defaults=False,
        test_task=[], _devtool_cli_test_task_explicit=False,
        _devtool_cli_pytest_test_explicit=False,
        _devtool_cli_pytest_marker_explicit=False,
        _devtool_cli_pytest_k_explicit=False,
        _devtool_cli_pytest_arg_explicit=False,
    )
    _rehydrate_apply_validation_context(config, resumed, context)
    assert resumed.pytest_tests == ["tests/test_demo.py::test_login"]
    assert resumed.pytest_markers == ["unit"]
    assert resumed.pytest_k == "login"
    assert resumed.pytest_args == ["--maxfail=1"]


def test_machine_json_preserves_pytest_selection_metadata(tmp_path: Path) -> None:
    from devtool.models import OperationResult
    from devtool.summary import build_json_summary

    result = OperationResult(
        name="test",
        argv=["python", "-m", "pytest", "tests/test_demo.py::test_login"],
        returncode=0,
        duration_seconds=0.1,
        output="1 passed",
        issues=[],
        metadata={
            "test_selection": {
                "pytest_selection_mode": "explicit",
                "pytest_selected_tests": ["tests/test_demo.py::test_login"],
                "pytest_selected_count": 1,
                "pytest_selection_source": "cli",
            }
        },
    )
    payload = build_json_summary([result], repo_root=str(tmp_path))
    metadata = payload["results"][0]["metadata"]
    assert metadata["test_selection"]["pytest_selected_tests"] == ["tests/test_demo.py::test_login"]
    assert metadata["test_selection"]["pytest_selected_count"] == 1


def test_help_documents_pytest_selection_flags() -> None:
    parser = build_parser()
    validate = next(action for action in parser._actions if action.dest == "command").choices["validate"]
    help_text = validate.format_help()
    for flag in (
        "--pytest-test",
        "--pytest-marker",
        "--pytest-k",
        "--pytest-arg",
        "--pytest-include-defaults",
    ):
        assert flag in help_text
