"""Native overlay manifest phase precedence — no project runners are executed.

A requested validation phase must not silently turn restore/build/test back on.
An exclusive validation.tasks plan is not the built-in phase pipeline.
"""
from __future__ import annotations

from argparse import Namespace

import pytest

from devtool import cli


PHASES_DISABLED = {"required": True, "restore": False, "build": False, "test": False}


def _args(**kwargs: object) -> Namespace:
    values: dict[str, object] = {
        "command": "apply-overlay",
        "artifact": "synthetic-native-overlay.zip",
        "validate": False,
        "validate_only": False,
        "defer_validate": False,
        "static_validate": False,
        "no_validate": False,
        "preview": False,
        "diff": False,
        "interactive": False,
        "target": "",
        "solution": "",
        "test_task": [],
        "test_profile": "",
        "pytest_tests": [],
        "pytest_markers": [],
        "pytest_args": [],
        "pytest_k": "",
        "pytest_include_defaults": False,
        "skip_restore": False,
        "skip_build": False,
        "skip_test": False,
        "commit": "",
        "commit_mode": "none",
        "no_commit": True,
    }
    values.update(kwargs)
    return Namespace(**values)


def _normalized(monkeypatch: pytest.MonkeyPatch, *, manifest: dict, **kwargs: object) -> Namespace:
    monkeypatch.setattr(cli, "_read_artifact_manifest", lambda path: manifest)
    args = _args(**kwargs)
    cli._apply_artifact_manifest_defaults(args)
    return args


@pytest.mark.parametrize("validation_flags", (
    {"validate": True},
    {"validate_only": True},
    {"static_validate": True},
    {"defer_validate": True},
    {},
))
def test_manifest_disabled_phases_survive_requested_validation(
        monkeypatch: pytest.MonkeyPatch, validation_flags: dict[str, bool]) -> None:
    """No Android Gradle restore/build/test phase is selected by defaulting."""
    manifest = {"schema_version": 2, "validation": {**PHASES_DISABLED,
                "script": {"path": ".devtool-artifact/validate.sh", "runner": "sh",
                           "sha256": "a" * 64}}}
    args = _normalized(monkeypatch, manifest=manifest, **validation_flags)
    assert args.skip_restore is True
    assert args.skip_build is True
    assert args.skip_test is True
    assert args.validate is (validation_flags.get("validate", False) or not validation_flags)


def test_python_only_manifest_tests_keep_gradle_phases_disabled(
        monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = {"schema_version": 2, "validation": {**PHASES_DISABLED,
                "tests": [{"id": "python-suite-a", "runner": "python", "command": "python -m pytest tests/a.py"},
                          {"id": "python-suite-b", "runner": "python", "command": "python -m pytest tests/b.py"},
                          {"id": "python-suite-c", "runner": "python", "command": "python -m pytest tests/c.py"}]}}
    args = _normalized(monkeypatch, manifest=manifest, validate=True)
    assert (args.skip_restore, args.skip_build, args.skip_test) == (True, True, True)
    assert args.test_task == []  # Native validation.tests is not an exclusive --task list.
    assert args.validate is True


def test_exclusive_manifest_tasks_ignore_disabled_builtin_phase_booleans(
        monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = {"schema_version": 2, "validation": {**PHASES_DISABLED,
                "tasks": ["test", "lint"]}}
    args = _normalized(monkeypatch, manifest=manifest, validate=True)
    assert args.test_task == ["test", "lint"]
    assert args._devtool_validation_task_source == "artifact-manifest"
    assert (args.skip_restore, args.skip_build, args.skip_test) == (False, False, False)


def test_exclusive_manifest_tasks_also_apply_when_validation_implicit(
        monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = {"schema_version": 2, "validation": {**PHASES_DISABLED,
                "tasks": ["test"]}}
    args = _normalized(monkeypatch, manifest=manifest)
    assert args.validate is True
    assert args.test_task == ["test"]
    assert (args.skip_restore, args.skip_build, args.skip_test) == (False, False, False)


def test_explicit_cli_task_retains_explicit_skip_for_native_conflict_guard(
        monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = {"schema_version": 2, "validation": {**PHASES_DISABLED}}
    args = _normalized(monkeypatch, manifest=manifest, validate=True,
                       test_task=["test"], _devtool_cli_test_task_explicit=True,
                       skip_restore=True)
    assert args.test_task == ["test"]
    assert (args.skip_restore, args.skip_build, args.skip_test) == (True, False, False)


def test_cli_disabled_validation_does_not_invent_skips(
        monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = {"schema_version": 2, "validation": {**PHASES_DISABLED}}
    args = _normalized(monkeypatch, manifest=manifest, no_validate=True)
    assert args.validate is False
    assert (args.skip_restore, args.skip_build, args.skip_test) == (False, False, False)


def test_disabled_individual_phases_are_not_reenabled_by_validate(
        monkeypatch: pytest.MonkeyPatch) -> None:
    for disabled in ("restore", "build", "test"):
        phases = {"required": True, "restore": True, "build": True, "test": True,
                  disabled: False}
        args = _normalized(monkeypatch, manifest={"schema_version": 2,
                              "validation": phases}, validate=True)
        expected = {"restore": args.skip_restore, "build": args.skip_build, "test": args.skip_test}
        assert expected == {name: name == disabled for name in ("restore", "build", "test")}


def test_unrelated_non_apply_command_is_not_modified(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(_path: str) -> dict:
        raise AssertionError("manifest reader must not run for non-apply command")
    monkeypatch.setattr(cli, "_read_artifact_manifest", unexpected)
    args = _args(command="test", validate=True)
    cli._apply_artifact_manifest_defaults(args)
    assert (args.skip_restore, args.skip_build, args.skip_test) == (False, False, False)
