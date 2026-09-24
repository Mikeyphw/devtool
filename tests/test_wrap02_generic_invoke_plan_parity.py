from __future__ import annotations

import io
import json
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

from devtool.cli import (
    PROGRESS_CAPABLE_COMMANDS,
    _single_run_reservation,
    build_parser,
    main,
    split_runner_passthrough_args,
)
from devtool.config import load_project_config
from devtool.termux_chroot_runner import AUTO_CHROOT_COMMANDS


def _write(root: Path, text: str) -> None:
    (root / ".devtool.toml").write_text(text.strip() + "\n", encoding="utf-8")


def _env(root: Path):
    return mock.patch.dict(
        os.environ,
        {
            "DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml"),
            "DEVTOOL_DISABLE_HUD": "1",
        },
        clear=False,
    )


def _single_target_repo(root: Path) -> None:
    _write(
        root,
        '''
        [project]
        runner = "command"

        [workspace]
        default_target = "app"

        [jobs.codegen]
        runner = "command"
        command = ["python3", "-c", "print('generated')"]
        outputs = ["generated/**"]

        [targets.app]
        runner = "command"
        outputs = ["dist/**"]

        [targets.app.workflows]
        codegen = ["job:codegen"]
        ''',
    )


def _machine_data(text: str) -> dict:
    payload = json.loads(text)
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else payload


def test_parser_exposes_invoke_with_workspace_scheduler_contract() -> None:
    args = build_parser().parse_args(
        [
            "invoke",
            "codegen",
            "--group",
            "services",
            "--workflow-parallel",
            "--workflow-max-workers",
            "3",
        ]
    )
    assert args.command == "invoke"
    assert args.action == "codegen"
    assert args.group == ["services"]
    assert args.workflow_parallel is True
    assert args.workflow_max_workers == 3


def test_plan_accepts_repository_defined_action_and_emits_expanded_graph() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _single_target_repo(root)
        stream = io.StringIO()
        with _env(root), redirect_stdout(stream):
            code = main(["--repo", str(root), "plan", "codegen", "--json"])
        payload = _machine_data(stream.getvalue())

    assert code == 0
    assert payload["action"] == "codegen"
    assert payload["targets"] == ["app"]
    assert payload["graph"]["workflow"] == "codegen"
    assert payload["commands"][0]["name"] == "job:codegen"


def test_invoke_executes_declared_action_instead_of_literal_invoke() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _single_target_repo(root)
        with _env(root):
            code = main(
                [
                    "--repo",
                    str(root),
                    "--dry-run",
                    "--no-clipboard",
                    "invoke",
                    "codegen",
                ]
            )

    # If main dispatched the literal command name "invoke" into the command
    # runner, this repository would fail with "No command configured". Success
    # proves the repository action is the canonical execution action.
    assert code == 0


def test_invoke_group_runs_same_action_across_selected_targets() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(
            root,
            '''
            [project]
            runner = "command"

            [jobs.a]
            runner = "command"
            command = ["python3", "-c", "print('a')"]
            outputs = ["out/a"]

            [jobs.b]
            runner = "command"
            command = ["python3", "-c", "print('b')"]
            outputs = ["out/b"]

            [targets.a]
            runner = "command"
            [targets.a.workflows]
            codegen = ["job:a"]

            [targets.b]
            runner = "command"
            [targets.b.workflows]
            codegen = ["job:b"]

            [workspace.groups]
            services = ["a", "b"]
            ''',
        )
        with _env(root):
            code = main(
                [
                    "--repo",
                    str(root),
                    "--dry-run",
                    "--no-clipboard",
                    "invoke",
                    "codegen",
                    "--group",
                    "services",
                ]
            )

    assert code == 0


def test_bare_multitarget_invoke_without_default_or_scope_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(
            root,
            '''
            [project]
            runner = "command"
            [targets.a]
            runner = "command"
            [targets.a.workflows]
            codegen = ["runner:command#build"]
            [targets.b]
            runner = "command"
            [targets.b.workflows]
            codegen = ["runner:command#build"]
            ''',
        )
        stderr = io.StringIO()
        with _env(root), redirect_stderr(stderr):
            code = main(["--repo", str(root), "--dry-run", "invoke", "codegen"])

    assert code != 0
    assert "Multiple targets are configured" in stderr.getvalue()


def test_invoke_reservation_uses_action_workflow_scopes() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _single_target_repo(root)
        with _env(root):
            config = load_project_config(str(root), None)
        args = build_parser().parse_args(["--repo", str(root), "invoke", "codegen"])
        target, paths, known, _transaction = _single_run_reservation(config, args)

    assert target == "app"
    assert known is True
    assert "generated" in paths



def test_invoke_run_preserves_multitarget_long_running_guard() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(
            root,
            '''
            [project]
            runner = "command"
            [targets.a]
            runner = "command"
            run_command = "true"
            [targets.b]
            runner = "command"
            run_command = "true"
            [workspace.groups]
            both = ["a", "b"]
            ''',
        )
        stderr = io.StringIO()
        with _env(root), redirect_stderr(stderr), pytest.raises(SystemExit) as exc:
            main(["--repo", str(root), "--dry-run", "invoke", "run", "--group", "both"])

    assert exc.value.code == 2
    assert "run cannot target multiple workspace members" in stderr.getvalue()

def test_invoke_is_progress_capable_and_auto_chroot_eligible() -> None:
    assert "invoke" in PROGRESS_CAPABLE_COMMANDS
    assert "invoke" in AUTO_CHROOT_COMMANDS


def test_invoke_raw_runner_passthrough_is_rejected() -> None:
    raw = ["invoke", "codegen", "--", "--stacktrace"]
    parsed, passthrough = split_runner_passthrough_args(raw)
    assert parsed == raw
    assert passthrough == []

    stderr = io.StringIO()
    with redirect_stderr(stderr), pytest.raises(SystemExit) as exc:
        build_parser().parse_args(parsed)
    assert exc.value.code == 2
    assert "unrecognized arguments: --stacktrace" in stderr.getvalue()


def test_native_runner_passthrough_contract_remains_unchanged() -> None:
    parsed, passthrough = split_runner_passthrough_args(
        ["build", "--target", "app", "--", "--stacktrace", "-Pfoo=bar"]
    )
    assert parsed == ["build", "--target", "app"]
    assert passthrough == ["--stacktrace", "-Pfoo=bar"]


def test_completion_declares_invoke_and_dynamic_workflow_actions() -> None:
    completion = Path("devtool/completion.py").read_text(encoding="utf-8")
    assert "'invoke:execute an arbitrary declared workflow action'" in completion
    assert "_devtool_workflow_actions()" in completion
    assert "actions=(restore build test lint package run validate wine-validate)" in completion
    assert "'1:workflow action:_devtool_workflow_actions'" in completion
