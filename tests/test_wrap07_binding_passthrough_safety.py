from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from devtool import cli
from devtool.config import load_project_config
from devtool.wrapper_resolution import WrapperResolutionError, resolve_wrapper_argv, wrapper_resolution_as_dict


def _write(root: Path, text: str) -> None:
    (root / ".devtool.toml").write_text(text.strip() + "\n", encoding="utf-8")


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


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True)


def test_static_bindings_cannot_redefine_devtool_routing_controls() -> None:
    cases = [
        ('''[wrapper.commands.build]\n            devtool = "build"\n            target = "android"\n            [wrapper.commands.build.options.repo]\n            type = "string"\n            global_argv = ["--repo", "{value}"]\n        ''', "repository/global control"),
        ('''[wrapper.commands.build]\n            devtool = "build"\n            target = "android"\n            [wrapper.commands.build.options.scope]\n            type = "string"\n            command_argv = ["--target", "{value}"]\n        ''', "target/repository control"),
        ('''[wrapper.commands.build]\n            devtool = "build"\n            target = "android"\n            [wrapper.commands.build.options.internal]\n            type = "string"\n            command_argv = ["--internal-invocation-source", "{value}"]\n        ''', "internal Devtool"),
        ('''[wrapper.commands.build]\n            devtool = "build"\n            target = "android"\n            allow_passthrough = true\n            [wrapper.commands.build.options.sep]\n            type = "boolean"\n            passthrough_argv = ["--"]\n        ''', "raw .* separator"),
    ]
    for wrapper_config, match in cases:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, _base_config(wrapper_config))
            with pytest.raises(SystemExit, match=match):
                _load(root)


def test_raw_passthrough_rejects_nested_separator_internal_flags_and_newlines() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, _base_config('''
            [wrapper.commands.build]
            devtool = "build"
            target = "android"
            allow_passthrough = true
        '''))
        config = _load(root)

        with pytest.raises(WrapperResolutionError, match="additional '--' separator"):
            resolve_wrapper_argv(config, ["build", "--", "--"])
        with pytest.raises(WrapperResolutionError, match="internal Devtool control"):
            resolve_wrapper_argv(config, ["build", "--", "--internal-invocation-source"])
        with pytest.raises(WrapperResolutionError, match="NUL or newline"):
            resolve_wrapper_argv(config, ["build", "--", "line\nbreak"])


def test_bound_values_reject_newlines_before_canonical_argv_is_created() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, _base_config('''
            [wrapper.commands.build]
            devtool = "build"
            target = "android"

            [wrapper.commands.build.options.variant]
            type = "string"
            command_argv = ["--variant", "{value}"]
        '''))
        config = _load(root)

        with pytest.raises(WrapperResolutionError, match="NUL or newline"):
            resolve_wrapper_argv(config, ["build", "--variant", "debug\n--target=evil"])


def test_sensitive_wrapper_options_get_redacted_resolution_representation() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        _write(root, _base_config('''
            [wrapper.commands.upload]
            devtool = "run"
            target = "android"

            [wrapper.commands.upload.options.api_token]
            type = "string"
            required = true
            command_argv = ["--token", "{value}"]
        '''))
        resolution = resolve_wrapper_argv(_load(root), ["upload", "--api_token", "super-secret-token"])

    payload = wrapper_resolution_as_dict(resolution)
    assert payload["sensitive_options"] == ["api_token"]
    assert "super-secret-token" in payload["canonical_argv"]
    assert "super-secret-token" not in payload["redacted_canonical_argv"]
    assert "<redacted>" in payload["redacted_canonical_argv"]
    assert "super-secret-token" not in cli._wrapper_resolution_text(resolution)


def test_safe_gradle_passthrough_remains_supported() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        _write(root, _base_config('''
            [wrapper.commands.build]
            devtool = "build"
            target = "android"
            allow_passthrough = true

            [wrapper.commands.build.options.abi]
            type = "choice"
            choices = ["arm64-v8a", "x86_64"]
            passthrough_argv = ["-Pabi={value}"]
        '''))
        resolution = resolve_wrapper_argv(
            _load(root),
            ["build", "--abi", "arm64-v8a", "--", "--stacktrace", "--info"],
        )

    assert resolution.passthrough_argv == ("-Pabi=arm64-v8a", "--stacktrace", "--info")
    assert resolution.canonical_argv[-4:] == ("--", "-Pabi=arm64-v8a", "--stacktrace", "--info")
