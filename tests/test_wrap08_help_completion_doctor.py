from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from devtool import cli
from devtool.completion_state import build_completion_state, completion_values
from devtool.config import load_project_config
from devtool.doctor import doctor_payload
from devtool.wrapper_help import render_wrapper_command_help, render_wrapper_overview, wrapper_discovery_payload
from devtool.wrapper_launcher import install_launchers


def _write(root: Path, text: str) -> None:
    (root / ".devtool.toml").write_text(text.strip() + "\n", encoding="utf-8")


def _config_text() -> str:
    return r'''
        [project]
        runner = "command"

        [workspace]
        default_target = "android"

        [targets.android]
        runner = "command"

        [targets.android.workflows]
        codegen = ["runner:command#build"]

        [wrapper]
        schema = 1
        default_command = "build"

        [wrapper.commands.build]
        description = "Build the Android app"
        devtool = "build"
        target = "android"
        aliases = ["b"]
        allow_passthrough = true

        [wrapper.commands.build.options.variant]
        type = "choice"
        description = "Build variant"
        choices = ["debug", "release"]
        command_argv = ["--variant", "{value}"]

        [wrapper.commands.upload]
        description = "Upload with a token"
        devtool = "run"
        target = "android"

        [wrapper.commands.upload.options.api_token]
        type = "string"
        required = true
        command_argv = ["--token", "{value}"]

        [wrapper.commands.codegen]
        description = "Regenerate generated sources"
        workflow = "codegen"
        target = "android"
    '''


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True)


def test_wrapper_help_surfaces_overview_and_command_details() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, _config_text())
        config = _load(root)

        overview = render_wrapper_overview(config)
        assert "Usage: devtoolw <command>" in overview
        assert "build" in overview
        assert "codegen" in overview
        assert "Default command: build" in overview

        command_help = render_wrapper_command_help(config, "build")
        assert "Usage: devtoolw build" in command_help
        assert "--variant" in command_help
        assert "choices: debug, release" in command_help
        assert "Passthrough" in command_help


def test_wrapper_exec_help_and_version_are_polished_cli_surfaces(capsys) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, _config_text())
        parser = cli.build_parser()

        args = parser.parse_args(["--repo", str(root), "wrapper", "exec", "--help"])
        assert cli._handle_wrapper_command(args) == 0
        overview = capsys.readouterr().out
        assert "Usage: devtoolw <command>" in overview
        assert "Build the Android app" in overview

        args = parser.parse_args(["--repo", str(root), "wrapper", "exec", "build", "--help"])
        assert cli._handle_wrapper_command(args) == 0
        command_help = capsys.readouterr().out
        assert "Usage: devtoolw build" in command_help
        assert "--variant" in command_help

        args = parser.parse_args(["--repo", str(root), "wrapper", "exec", "--version"])
        assert cli._handle_wrapper_command(args) == 0
        assert capsys.readouterr().out.startswith("devtool ")


def test_wrapper_discover_and_completion_state_expose_wrapper_contract(capsys) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, _config_text())
        install_launchers(root)
        config = _load(root)

        payload = wrapper_discovery_payload(config)
        assert payload["schema_version"] == 1
        assert payload["launchers"]["ok"] is True
        assert [item["name"] for item in payload["commands"]] == ["build", "codegen", "upload"]
        assert payload["docs"] == "docs/WRAPPER.md"

        state = build_completion_state(config).as_dict()
        assert "build" in state["wrapper_commands"]
        assert "b" in state["wrapper_aliases"]
        assert "--variant" in state["wrapper_options"]
        assert "build:--" in state["wrapper_options"]
        assert "build" in completion_values(config, "wrapper-commands")
        assert "--variant" in completion_values(config, "wrapper-options")

        parser = cli.build_parser()
        args = parser.parse_args(["--repo", str(root), "wrapper", "discover", "--json"])
        assert cli._handle_wrapper_command(args) == 0
        cli_payload = json.loads(capsys.readouterr().out)
        assert cli_payload["launchers"]["ok"] is True
        assert cli_payload["commands"][0]["name"] == "build"


def test_doctor_payload_reports_wrapper_launchers_and_docs_status() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, _config_text())
        install_launchers(root)
        payload = doctor_payload(_load(root))

        assert payload["wrapper"]["commands"]["build"]["description"] == "Build the Android app"
        assert payload["wrapper_launchers"]["ok"] is True
        checks = {(item["section"], item["name"]): item for item in payload["checks"]}
        assert checks[("project", "wrapper")]["status"] == "ok"
        assert checks[("project", "wrapper-launchers")]["status"] == "ok"
        assert checks[("project", "wrapper-docs")]["status"] == "ok"


def test_wrapper_documentation_file_records_supported_surfaces() -> None:
    docs = Path("docs/WRAPPER.md").read_text(encoding="utf-8")
    assert "devtool wrapper discover" in docs
    assert "completion-state --kind wrapper-commands" in docs
    assert "./devtoolw build --help" in docs
    assert "Safety model" in docs
