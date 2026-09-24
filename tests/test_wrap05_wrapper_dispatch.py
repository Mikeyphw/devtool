from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from devtool import cli
from devtool.config import load_project_config
from devtool.wrapper_resolution import resolve_wrapper_argv


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


def test_wrapper_canonical_argv_injects_non_secret_provenance() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        _write(root, _base_config('''
            [wrapper.commands.build]
            devtool = "build"
            target = "android"

            [wrapper.commands.build.options.release]
            type = "boolean"
            command_argv = ["--variant", "release"]
        '''))
        resolution = resolve_wrapper_argv(_load(root), ["build", "--release"])

    argv = cli._wrapper_canonical_argv_with_provenance(resolution)

    assert argv == [
        "--repo", str(root),
        "--internal-invocation-source", "wrapper",
        "--internal-invocation-command", "build",
        "--internal-wrapper-schema", "1",
        "build", "--target", "android", "--variant", "release",
    ]
    assert "wrapper" not in argv[argv.index("build") + 1 :]


def test_wrapper_exec_reenters_canonical_main_once_and_forwards_execution_controls(monkeypatch) -> None:
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
        parser = cli.build_parser()
        args = parser.parse_args([
            "--repo", str(root),
            "--dry-run",
            "--ssh", "tablet",
            "wrapper", "exec", "build", "--abi", "arm64-v8a", "--", "--stacktrace",
        ])
        calls: list[list[str]] = []

        def fake_main(argv):
            calls.append(list(argv))
            return 17

        monkeypatch.setattr(cli, "main", fake_main)
        rc = cli._handle_wrapper_command(args)

    assert rc == 17
    assert len(calls) == 1
    canonical = calls[0]
    assert canonical[:2] == ["--repo", str(root)]
    assert "--dry-run" in canonical
    assert canonical[canonical.index("--ssh") + 1] == "tablet"
    assert canonical[canonical.index("--internal-invocation-source") + 1] == "wrapper"
    assert canonical[canonical.index("--internal-invocation-command") + 1] == "build"
    assert canonical[canonical.index("--internal-wrapper-schema") + 1] == "1"
    assert canonical[canonical.index("build", canonical.index("--internal-wrapper-schema")) + 1 : canonical.index("--")] == ["--target", "android"]
    assert canonical[canonical.index("--") + 1 :] == ["-Pabi=arm64-v8a", "--stacktrace"]


def test_wrapper_workflow_exec_reenters_invoke_action(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        _write(root, _base_config('''
            [wrapper.commands.codegen]
            workflow = "codegen"
            target = "android"
        '''))
        parser = cli.build_parser()
        args = parser.parse_args(["--repo", str(root), "wrapper", "exec", "codegen"])
        calls: list[list[str]] = []
        monkeypatch.setattr(cli, "main", lambda argv: calls.append(list(argv)) or 0)

        assert cli._handle_wrapper_command(args) == 0

    assert len(calls) == 1
    canonical = calls[0]
    assert canonical[canonical.index("invoke") : canonical.index("invoke") + 4] == [
        "invoke", "codegen", "--target", "android",
    ]
    assert canonical[canonical.index("--internal-invocation-source") + 1] == "wrapper"
    assert canonical[canonical.index("--internal-invocation-command") + 1] == "codegen"


def test_wrapper_resolve_json_does_not_execute(monkeypatch, capsys) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        _write(root, _base_config('''
            [wrapper.commands.build]
            devtool = "build"
            target = "android"
        '''))
        called = False

        def fake_main(_argv):
            nonlocal called
            called = True
            return 1

        monkeypatch.setattr(cli, "main", fake_main)
        parser = cli.build_parser()
        args = parser.parse_args(["--repo", str(root), "wrapper", "resolve", "--json", "build"])
        rc = cli._handle_wrapper_command(args)

    assert rc == 0
    assert not called
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "build"
    assert payload["canonical_argv"][-3:] == ["build", "--target", "android"]


def test_wrapper_describe_json_uses_repository_wrapper_contract(capsys) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        _write(root, _base_config('''
            [wrapper]
            default_command = "build"

            [wrapper.commands.build]
            devtool = "build"
            target = "android"
        '''))
        parser = cli.build_parser()
        args = parser.parse_args(["--repo", str(root), "wrapper", "describe", "--json"])
        rc = cli._handle_wrapper_command(args)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["default_command"] == "build"
    assert payload["commands"]["build"]["devtool"] == "build"


def test_wrapper_exec_control_mode_does_not_reenter_main(monkeypatch, capsys) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        _write(root, _base_config('''
            [wrapper.commands.build]
            devtool = "build"
            target = "android"
        '''))
        called = False

        def fake_main(_argv):
            nonlocal called
            called = True
            return 1

        monkeypatch.setattr(cli, "main", fake_main)
        parser = cli.build_parser()
        args = parser.parse_args(["--repo", str(root), "wrapper", "exec", "build", "--help"])
        rc = cli._handle_wrapper_command(args)

    assert rc == 0
    assert not called
    output = capsys.readouterr().out
    assert "Usage: devtoolw build" in output
    assert "Action: build" in output
