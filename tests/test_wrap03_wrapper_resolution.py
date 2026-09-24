from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from devtool.config import load_project_config
from devtool.wrapper_resolution import (
    WrapperResolutionError,
    resolve_wrapper_argv,
    wrapper_resolution_as_dict,
)


def _write(root: Path, text: str) -> None:
    (root / ".devtool.toml").write_text(text.strip() + "\n", encoding="utf-8")


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True)


def _base_config(extra: str = "") -> str:
    return f'''
        [project]
        runner = "command"

        [workspace]
        default_target = "android"

        [targets.android]
        runner = "command"

        [targets.android.workflows]
        codegen = ["runner:command#build"]

        {extra}
    '''


def test_native_wrapper_resolution_compiles_to_canonical_devtool_argv() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        _write(root, _base_config('''
            [wrapper]
            schema = 1

            [wrapper.commands.build]
            devtool = "build"
            target = "android"
            aliases = ["bld"]
            allow_passthrough = true

            [wrapper.commands.build.options.release]
            type = "boolean"
            aliases = ["r"]
            command_argv = ["--variant", "release"]
            conflicts = ["debug"]

            [wrapper.commands.build.options.debug]
            type = "boolean"
            command_argv = ["--variant", "debug"]
            conflicts = ["release"]

            [wrapper.commands.build.options.abi]
            type = "choice"
            choices = ["arm64-v8a", "x86_64"]
            passthrough_argv = ["-Pabi={value}"]

            [wrapper.commands.build.options.profile]
            type = "string"
            default = "tablet"
            global_argv = ["--profile", "{value}"]
        '''))
        config = _load(root)
        resolution = resolve_wrapper_argv(
            config,
            ["bld", "-r", "--abi", "arm64-v8a", "--", "--stacktrace"],
            launch_cwd=root / "subdir",
        )

    assert resolution.execution_kind == "native"
    assert resolution.command == "build"
    assert resolution.command_spelling == "bld"
    assert resolution.devtool_command == "build"
    assert resolution.global_argv == ("--repo", str(root), "--profile", "tablet")
    assert resolution.command_argv == ("--target", "android", "--variant", "release")
    assert resolution.passthrough_argv == ("-Pabi=arm64-v8a", "--stacktrace")
    assert resolution.canonical_argv == (
        "--repo", str(root), "--profile", "tablet",
        "build", "--target", "android", "--variant", "release",
        "--", "-Pabi=arm64-v8a", "--stacktrace",
    )
    payload = wrapper_resolution_as_dict(resolution)
    assert payload["options"]["release"] == {"value": True, "provided": True, "defaulted": False}
    assert payload["options"]["profile"] == {"value": "tablet", "provided": False, "defaulted": True}


def test_default_command_allows_option_first_invocation() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        _write(root, _base_config('''
            [wrapper]
            default_command = "build"

            [wrapper.commands.build]
            devtool = "build"
            target = "android"

            [wrapper.commands.build.options.release]
            type = "boolean"
            command_argv = ["--variant", "release"]
        '''))
        resolution = resolve_wrapper_argv(_load(root), ["--release"])

    assert resolution.command == "build"
    assert resolution.command_spelling == "build"
    assert resolution.canonical_argv == ("--repo", str(root), "build", "--target", "android", "--variant", "release")


def test_workflow_wrapper_resolution_compiles_to_invoke_action() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        _write(root, _base_config('''
            [wrapper.commands.codegen]
            workflow = "codegen"
            target = "android"

            [wrapper.commands.codegen.options.fast]
            type = "boolean"
            command_argv = ["--fail-fast"]
        '''))
        resolution = resolve_wrapper_argv(_load(root), ["codegen", "--fast"])

    assert resolution.execution_kind == "workflow"
    assert resolution.workflow_action == "codegen"
    assert resolution.canonical_argv == ("--repo", str(root), "invoke", "codegen", "--target", "android", "--fail-fast")


def test_conflicts_requirements_required_and_unknowns_fail_before_execution() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, _base_config('''
            [wrapper.commands.build]
            devtool = "build"
            target = "android"

            [wrapper.commands.build.options.release]
            type = "boolean"
            conflicts = ["debug"]
            requires = ["channel"]

            [wrapper.commands.build.options.debug]
            type = "boolean"
            conflicts = ["release"]

            [wrapper.commands.build.options.channel]
            type = "choice"
            choices = ["internal", "prod"]
            required = true
        '''))
        config = _load(root)
        with pytest.raises(WrapperResolutionError, match="requires option --channel"):
            resolve_wrapper_argv(config, ["build", "--release"])
        with pytest.raises(WrapperResolutionError, match="conflict"):
            resolve_wrapper_argv(config, ["build", "--release", "--debug", "--channel", "prod"])
        with pytest.raises(WrapperResolutionError, match="Unknown wrapper option.*--relase.*--release"):
            resolve_wrapper_argv(config, ["build", "--relase", "--channel", "prod"])
        with pytest.raises(WrapperResolutionError, match="not one of"):
            resolve_wrapper_argv(config, ["build", "--channel", "beta"])


def test_positionals_repeatable_values_and_explicit_path_bases_are_resolved() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        launch = root / "work"
        launch.mkdir()
        _write(root, _base_config('''
            [wrapper.commands.install]
            devtool = "run"
            target = "android"
            allow_passthrough = true

            [[wrapper.commands.install.positionals]]
            name = "artifact"
            type = "path"
            relative_to = "cwd"
            required = true
            command_argv = ["--artifact", "{value}"]

            [[wrapper.commands.install.positionals]]
            name = "extra"
            type = "string"
            repeatable = true
            passthrough_argv = ["--extra={value}"]

            [wrapper.commands.install.options.count]
            type = "integer"
            command_argv = ["--count", "{value}"]

            [wrapper.commands.install.options.ratio]
            type = "float"
            command_argv = ["--ratio", "{value}"]
        '''))
        config = _load(root)
        resolution = resolve_wrapper_argv(
            config,
            ["install", "dist/app.apk", "one", "two", "--count", "3", "--ratio=1.25"],
            launch_cwd=launch,
        )

    artifact = str((launch / "dist/app.apk").resolve(strict=False))
    assert resolution.positionals == (("artifact", artifact), ("extra", ("one", "two")))
    assert resolution.command_argv == ("--target", "android", "--count", "3", "--ratio", "1.25", "--artifact", artifact)
    assert resolution.passthrough_argv == ("--extra=one", "--extra=two")


def test_control_modes_are_structural_and_do_not_compile_execution_argv() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, _base_config('''
            [wrapper.commands.build]
            devtool = "build"
            target = "android"
        '''))
        config = _load(root)
        top_help = resolve_wrapper_argv(config, ["--help"])
        command_help = resolve_wrapper_argv(config, ["build", "--help"])
        version = resolve_wrapper_argv(config, ["--version"])

    assert top_help.mode == "help" and top_help.help_requested and top_help.canonical_argv == ()
    assert command_help.mode == "help" and command_help.command == "build" and command_help.canonical_argv == ()
    assert version.mode == "version" and version.version_requested and version.canonical_argv == ()


def test_raw_passthrough_requires_opt_in_and_workflows_reject_it() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, _base_config('''
            [wrapper.commands.build]
            devtool = "build"
            target = "android"

            [wrapper.commands.codegen]
            workflow = "codegen"
            target = "android"
        '''))
        config = _load(root)
        with pytest.raises(WrapperResolutionError, match="does not allow raw passthrough"):
            resolve_wrapper_argv(config, ["build", "--", "--stacktrace"])
        with pytest.raises(WrapperResolutionError, match="does not allow raw passthrough"):
            resolve_wrapper_argv(config, ["codegen", "--", "--stacktrace"])


def test_reserved_wrapper_controls_are_rejected_by_schema() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, _base_config('''
            [wrapper.commands.help]
            devtool = "build"
        '''))
        with pytest.raises(SystemExit, match="reserved wrapper command spelling"):
            _load(root)

        _write(root, _base_config('''
            [wrapper.commands.build]
            devtool = "build"
            aliases = ["version"]
        '''))
        with pytest.raises(SystemExit, match="reserved wrapper command spelling"):
            _load(root)

        _write(root, _base_config('''
            [wrapper.commands.build]
            devtool = "build"
            [wrapper.commands.build.options.help]
            type = "boolean"
        '''))
        with pytest.raises(SystemExit, match="reserved wrapper control spelling"):
            _load(root)


def test_unexpected_positionals_and_missing_command_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, _base_config('''
            [wrapper.commands.build]
            devtool = "build"
            target = "android"
        '''))
        config = _load(root)
        with pytest.raises(WrapperResolutionError, match="Unexpected wrapper positional"):
            resolve_wrapper_argv(config, ["build", "extra"])
        with pytest.raises(WrapperResolutionError, match="No wrapper command"):
            resolve_wrapper_argv(config, [])
        with pytest.raises(WrapperResolutionError, match="Unknown wrapper command.*buid.*build"):
            resolve_wrapper_argv(config, ["buid"])
