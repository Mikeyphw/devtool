from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from devtool.clipboard import ClipboardResult, prepare_clipboard_text
from devtool.cli import (
    _emit_summary,
    _output_debug_setting,
    _perform_clipboard_copy,
    build_parser,
)
from devtool.models import OperationResult


def _args(*, output_debug=True, quiet=False) -> SimpleNamespace:
    return SimpleNamespace(
        no_clipboard=False,
        copy=True,
        copy_via="auto",
        output_debug=output_debug,
        quiet=quiet,
        command="test",
        _devtool_run_id="output-debug-run",
    )


def _result() -> OperationResult:
    return OperationResult(
        name="validation",
        argv=["pytest"],
        returncode=0,
        duration_seconds=1.25,
        output="240 passed",
        issues=[],
    )


def test_parser_exposes_output_debug_controls_without_enabling_by_default() -> None:
    parser = build_parser()
    default = parser.parse_args(["validate"])
    enabled = parser.parse_args(["--output-debug", "validate"])
    disabled = parser.parse_args(["--no-output-debug", "validate"])

    assert default.output_debug is None
    assert enabled.output_debug is True
    assert disabled.output_debug is False


def test_output_debug_environment_variable_and_cli_override(monkeypatch) -> None:
    args = _args(output_debug=None)
    monkeypatch.setenv("DEVTOOL_OUTPUT_DEBUG", "1")
    assert _output_debug_setting(args) == (True, "DEVTOOL_OUTPUT_DEBUG")

    args.output_debug = False
    assert _output_debug_setting(args) == (False, "--no-output-debug")

    args.output_debug = True
    monkeypatch.setenv("DEVTOOL_OUTPUT_DEBUG", "0")
    assert _output_debug_setting(args) == (True, "--output-debug")


def test_output_debug_unknown_environment_value_fails_closed(monkeypatch) -> None:
    args = _args(output_debug=None)
    monkeypatch.setenv("DEVTOOL_OUTPUT_DEBUG", "maybe")
    assert _output_debug_setting(args) == (False, "DEVTOOL_OUTPUT_DEBUG-invalid")


def test_output_debug_persists_rendered_terminal_summary(tmp_path: Path, capsys) -> None:
    args = _args(output_debug=True)
    log_path = tmp_path / "run.log"
    log_path.write_text("run header\n", encoding="utf-8")

    with (
        patch("devtool.cli.log_state_dir", return_value=tmp_path / "state"),
        patch.dict(os.environ, {"DEVTOOL_RUN_ID": "output-debug-run"}, clear=False),
    ):
        _emit_summary(
            [_result()],
            None,
            as_json=False,
            output_debug_args=args,
            output_debug_log_path=log_path,
        )

    root = tmp_path / "state" / "evidence" / "output-debug-run" / "output-debug"
    payload_path = root / "terminal-summary-0001.txt"
    metadata_path = root / "terminal-summary-0001.json"
    payload = payload_path.read_text(encoding="utf-8")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert "Validation" in payload
    assert metadata["kind"] == "terminal-summary"
    assert metadata["source"] == "terminal-render-summary"
    assert metadata["stream"] == "stdout"
    assert metadata["rendering"] == "rich-plain-equivalent"
    expected = payload.encode("utf-8")
    assert metadata["payload_bytes"] == len(expected)
    assert metadata["payload_sha256"] == hashlib.sha256(expected).hexdigest()
    assert "Output debug capture:" in log_path.read_text(encoding="utf-8")
    assert "kind: terminal-summary" in log_path.read_text(encoding="utf-8")

    rendered = capsys.readouterr().out
    assert "Terminal summary output debug" in rendered
    assert "--- terminal-summary payload begin ---" in rendered


def test_output_debug_captures_exact_clipboard_payload_and_correlates_terminal_hash(tmp_path: Path) -> None:
    args = _args(output_debug=True, quiet=True)
    log_path = tmp_path / "run.log"
    log_path.write_text("run header\n", encoding="utf-8")
    result = ClipboardResult(True, backend="termux-clipboard-set")
    copied: list[str] = []

    with (
        patch("devtool.cli.log_state_dir", return_value=tmp_path / "state"),
        patch("devtool.cli.copy_text", side_effect=lambda text: copied.append(text) or result),
        patch.dict(os.environ, {"DEVTOOL_RUN_ID": "output-debug-run"}, clear=False),
    ):
        _emit_summary(
            [_result()],
            None,
            as_json=False,
            output_debug_args=args,
            output_debug_log_path=log_path,
        )
        terminal_path = tmp_path / "state" / "evidence" / "output-debug-run" / "output-debug" / "terminal-summary-0001.txt"
        terminal_payload = terminal_path.read_text(encoding="utf-8")
        returned = _perform_clipboard_copy(
            terminal_payload,
            "Successful run summary",
            args,
            log_path=log_path,
            source="--copy",
        )

    assert returned is result
    assert copied == [prepare_clipboard_text(terminal_payload)]
    root = tmp_path / "state" / "evidence" / "output-debug-run" / "output-debug"
    clipboard_path = root / "clipboard-0002.txt"
    metadata = json.loads((root / "clipboard-0002.json").read_text(encoding="utf-8"))
    assert clipboard_path.read_text(encoding="utf-8") == copied[0]
    assert metadata["kind"] == "clipboard"
    assert metadata["backend"] == "termux-clipboard-set"
    assert metadata["success"] is True
    assert metadata["source"] == "--copy"
    assert metadata["matches_terminal_summary"] is True
    assert metadata["terminal_summary_output_id"] == "terminal-summary-0001"
    assert (root / "timeline.jsonl").read_text(encoding="utf-8").count("\n") == 2


def test_output_debug_records_clipboard_attempt_even_when_backend_fails(tmp_path: Path) -> None:
    args = _args(output_debug=True, quiet=True)
    log_path = tmp_path / "failure.log"
    log_path.write_text("header\n", encoding="utf-8")
    result = ClipboardResult(False, backend="termux-clipboard-set", reason="API unavailable")

    with (
        patch("devtool.cli.log_state_dir", return_value=tmp_path / "state"),
        patch("devtool.cli.copy_text", return_value=result),
        patch.dict(os.environ, {"DEVTOOL_RUN_ID": "output-debug-run"}, clear=False),
    ):
        _perform_clipboard_copy(
            "failure payload",
            "Failure summary",
            args,
            log_path=log_path,
            source="--copy",
        )

    root = tmp_path / "state" / "evidence" / "output-debug-run" / "output-debug"
    metadata = json.loads((root / "clipboard-0001.json").read_text(encoding="utf-8"))
    assert metadata["success"] is False
    assert metadata["reason"] == "API unavailable"
    assert (root / "clipboard-0001.txt").read_text(encoding="utf-8") == "failure payload"


def test_output_debug_disabled_does_not_create_evidence(tmp_path: Path) -> None:
    args = _args(output_debug=False, quiet=True)
    result = ClipboardResult(True, backend="test")

    with (
        patch("devtool.cli.log_state_dir", return_value=tmp_path / "state"),
        patch("devtool.cli.copy_text", return_value=result),
    ):
        _emit_summary(
            [_result()],
            None,
            as_json=False,
            output_debug_args=args,
            output_debug_log_path=tmp_path / "run.log",
        )
        _perform_clipboard_copy(
            "payload",
            "Successful run summary",
            args,
            log_path=tmp_path / "run.log",
            source="--copy",
        )

    assert not (tmp_path / "state" / "evidence").exists()


def test_old_copy_debug_names_are_not_accepted() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--copy-debug", "validate"])
