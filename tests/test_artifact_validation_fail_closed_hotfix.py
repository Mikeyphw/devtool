from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from devtool.artifacts import validate_artifact, write_archive_entries
from devtool.cli import (
    _apply_artifact_manifest_defaults,
    _artifact_script_result,
    _maybe_copy,
    _normalize_command_aliases,
    _required_artifact_validation_execution_guard,
    build_parser,
)
from devtool.config import ProjectConfig
from devtool.models import OperationResult
from devtool.summary import build_json_summary


def _artifact(tmp_path: Path, validation: dict) -> Path:
    archive = tmp_path / "overlay.zip"
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "name": "validation-hotfix-fixture",
        "validation": validation,
    }
    write_archive_entries(
        archive,
        {
            ".devtool-artifact.json": json.dumps(manifest).encode(),
            "README.txt": b"ok\n",
        },
    )
    return archive


def test_manifest_tasks_override_disabled_builtin_phases(tmp_path: Path) -> None:
    archive = _artifact(
        tmp_path,
        {
            "required": True,
            "restore": False,
            "build": False,
            "test": False,
            "tasks": ["test"],
            "pytest_tests": ["tests/test_demo.py"],
        },
    )

    validation = validate_artifact(tmp_path, archive)
    assert validation.ok, validation.messages

    parser = build_parser()
    args = parser.parse_args(["apply-overlay", str(archive)])
    _normalize_command_aliases(args)
    args._devtool_cli_target_explicit = False
    args._devtool_cli_solution_explicit = False
    args._devtool_cli_test_task_explicit = False
    _apply_artifact_manifest_defaults(args)

    assert args.validate is True
    assert args.test_task == ["test"]
    assert args.pytest_tests == ["tests/test_demo.py"]
    assert args.skip_restore is False
    assert args.skip_build is False
    assert args.skip_test is False


def test_manifest_allows_declarative_tests_only_required_validation(tmp_path: Path) -> None:
    archive = _artifact(
        tmp_path,
        {
            "required": True,
            "restore": False,
            "build": False,
            "test": False,
            "tests": [
                {
                    "id": "compileall",
                    "runner": "python",
                    "command": ["python", "-c", "print('ok')"],
                    "required": True,
                }
            ],
        },
    )

    result = validate_artifact(tmp_path, archive)

    assert result.ok, result.messages


def test_required_runtime_guard_fails_when_zero_checks_execute() -> None:
    manifest = {"validation": {"required": True, "restore": False, "build": False, "test": False, "tests": [{"id": "x", "runner": "python", "command": ["python", "-c", "pass"]}]}}

    guard = _required_artifact_validation_execution_guard(
        manifest,
        validation_requested=True,
        validation_results=[],
        artifact_validation_result=None,
    )

    assert guard is not None
    assert guard.returncode != 0
    assert guard.failure_kind == "required_validation_not_executed"
    assert guard.metadata["validation_executed_checks"] == 0


def test_required_runtime_guard_accepts_executed_repository_check() -> None:
    manifest = {"validation": {"required": True}}
    result = OperationResult("test", ["pytest"], 0, 0.1, "ok", [])

    guard = _required_artifact_validation_execution_guard(
        manifest,
        validation_requested=True,
        validation_results=[result],
        artifact_validation_result=None,
    )

    assert guard is None


def test_tests_only_artifact_validation_returns_success_primary(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0'\n",
        encoding="utf-8",
    )
    manifest = {
        "validation": {
            "required": True,
            "restore": False,
            "build": False,
            "test": False,
            "tests": [
                {
                    "id": "sentinel",
                    "runner": "python",
                    "command": ["python", "-c", "print('artifact-test-ok')"],
                    "required": True,
                }
            ],
        }
    }
    args = SimpleNamespace(
        no_artifact_script=False,
        verbose=False,
        dry_run=False,
        ignore_checksum=False,
        _devtool_run_id="hotfix-test",
        commit="",
    )
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="python")

    result, raw = _artifact_script_result(
        config,
        args,
        tmp_path / "unused.zip",
        manifest,
        target="",
        solution="",
        log_path=None,
        tasker_progress=None,
    )

    assert result is not None
    assert result.returncode == 0
    assert "artifact-test-ok" in raw
    assert result.metadata.get("artifact_validation_test") is True


def test_zero_test_evidence_is_reported_as_not_run(tmp_path: Path) -> None:
    args = argparse.Namespace(
        no_clipboard=False,
        copy=True,
        copy_via="auto",
        copy_mode="summary",
        command="apply-overlay",
        _devtool_run_id="summary-test",
    )
    result = OperationResult("apply-overlay", ["apply"], 0, 0.1, "ok", [])
    payload = build_json_summary([result], repo_root=tmp_path)
    copied: dict[str, str] = {}

    def _capture(payload_text: str, label: str, _args, **_kwargs):
        copied["text"] = payload_text
        return SimpleNamespace(success=True, backend="test", reason="")

    with patch("devtool.cli._perform_clipboard_copy", side_effect=_capture):
        _maybe_copy(
            "apply-overlay: ok exit=0 time=0.10s\n",
            "",
            json.dumps(payload, default=str),
            args,
            log_path=tmp_path / "run.log",
        )

    assert "Tests: not run" in copied["text"]
    assert "Tests: completed successfully" not in copied["text"]
