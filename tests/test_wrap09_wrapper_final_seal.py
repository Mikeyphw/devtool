from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from devtool import cli
from devtool.config import load_project_config
from devtool.wrapper_final_seal import build_wrapper_final_seal, render_wrapper_final_seal, wrapper_final_seal_as_dict
from devtool.wrapper_launcher import install_launchers


def _write_config(root: Path, text: str) -> None:
    (root / ".devtool.toml").write_text(text.strip() + "\n", encoding="utf-8")


def _write_docs(root: Path) -> None:
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "WRAPPER.md").write_text("# Wrapper\n\ndevtool wrapper seal\n", encoding="utf-8")


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

        [wrapper.commands.codegen]
        description = "Regenerate generated sources"
        workflow = "codegen"
        target = "android"

        [wrapper.commands.upload]
        description = "Upload with a token"
        devtool = "run"
        target = "android"

        [wrapper.commands.upload.options.api_token]
        type = "string"
        required = true
        command_argv = ["--token", "{value}"]
    '''


def _load(root: Path):
    env = {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}
    with mock.patch.dict(os.environ, env, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True)


def test_wrapper_final_seal_converges_wrapper_surfaces() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_config(root, _config_text())
        _write_docs(root)
        install_launchers(root)
        seal = build_wrapper_final_seal(_load(root))
        payload = wrapper_final_seal_as_dict(seal)

        assert payload["status"] == "ok"
        assert payload["ok"] is True
        assert payload["commands"] == ["build", "codegen", "upload"]
        checks = {(item["section"], item["name"]): item for item in payload["checks"]}
        assert checks[("launchers", "current")]["status"] == "ok"
        assert checks[("docs", "wrapper-guide")]["status"] == "ok"
        assert checks[("discovery", "commands")]["status"] == "ok"
        assert checks[("completion", "commands")]["status"] == "ok"
        assert checks[("completion", "aliases")]["status"] == "ok"
        assert checks[("resolution", "execute:upload")]["status"] == "skipped"
        assert checks[("doctor", "project:wrapper")]["status"] == "ok"
        assert checks[("doctor", "project:wrapper-launchers")]["status"] == "ok"
        assert checks[("doctor", "project:wrapper-docs")]["status"] == "ok"

        text = render_wrapper_final_seal(seal)
        assert "Wrapper final seal: ok" in text
        assert "Commands: build, codegen, upload" in text


def test_wrapper_seal_cli_surfaces_json_and_exit_code(capsys) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_config(root, _config_text())
        _write_docs(root)
        install_launchers(root)
        parser = cli.build_parser()

        args = parser.parse_args(["--repo", str(root), "wrapper", "seal", "--json"])
        assert cli._handle_wrapper_command(args) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "ok"
        assert payload["commands"] == ["build", "codegen", "upload"]

        args = parser.parse_args(["--repo", str(root), "wrapper", "seal"])
        assert cli._handle_wrapper_command(args) == 0
        assert "Wrapper final seal: ok" in capsys.readouterr().out


def test_wrapper_final_seal_fails_closed_when_launchers_or_docs_are_missing(capsys) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_config(root, _config_text())
        parser = cli.build_parser()

        args = parser.parse_args(["--repo", str(root), "wrapper", "seal", "--json"])
        assert cli._handle_wrapper_command(args) == 1
        payload = json.loads(capsys.readouterr().out)
        checks = {(item["section"], item["name"]): item for item in payload["checks"]}
        assert payload["status"] == "error"
        assert checks[("launchers", "current")]["status"] == "error"
        assert checks[("docs", "wrapper-guide")]["status"] == "error"


def test_wrapper_final_seal_command_is_documented() -> None:
    docs = Path("docs/WRAPPER.md").read_text(encoding="utf-8")
    assert "devtool wrapper seal" in docs
    assert "returns a non-zero exit code" in docs
