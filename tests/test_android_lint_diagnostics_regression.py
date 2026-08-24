from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from devtool.cli import _maybe_copy
from devtool.diagnostics import parse_issues


def test_android_lint_error_keeps_real_issue_id() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "app/src/main/kotlin/demo/MainActivity.kt"
        source.parent.mkdir(parents=True)
        source.write_text("class MainActivity", encoding="utf-8")

        output = (
            f"{source}:52: Error: Field requires API level 33 "
            "(current min is 26): "
            "android.Manifest.permission#POST_NOTIFICATIONS "
            "[InlinedApi]\n"
        )

        issues = parse_issues(root, "lint", output)

    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].code == "InlinedApi"
    assert issues[0].file == "app/src/main/kotlin/demo/MainActivity.kt"
    assert issues[0].line == 52
    assert "[InlinedApi]" not in issues[0].message


def test_clipboard_dedupes_android_lint_renderings() -> None:
    message = (
        "Field requires API level 33 (current min is 26): "
        "android.Manifest.permission#POST_NOTIFICATIONS"
    )

    raw_output = (
        f"app/src/main/kotlin/demo/MainActivity.kt:52: "
        f"INLINEDAPI: {message}\n"
        f"app/src/main/kotlin/demo/MainActivity.kt:52: "
        f"InlinedApi: {message}\n"
        f"app/src/main/kotlin/demo/MainActivity.kt:52: "
        f"Error: {message} [InlinedApi]\n"
        "- ERROR [android-validation-lint] "
        f"app/src/main/kotlin/demo/MainActivity.kt:52: "
        f"InlinedApi: {message}\n"
    )

    issues = [{
        "severity": "error",
        "command": "android-validation-lint",
        "file": "app/src/main/kotlin/demo/MainActivity.kt",
        "line": 52,
        "code": "InlinedApi",
        "message": message,
    }]

    payload = json.dumps({
        "warnings": 0,
        "errors": 1,
        "failed": 1,
        "results": [{
            "name": "android-validation-lint",
            "returncode": 2,
            "failure_kind": "compile_failure",
            "output": raw_output,
            "issues": issues,
        }],
    })

    args = SimpleNamespace(
        no_clipboard=False,
        copy=True,
        copy_via="auto",
    )

    copied: list[str] = []
    result = SimpleNamespace(
        success=True,
        backend="test",
        reason="",
    )

    with (
        patch(
            "devtool.cli.copy_text",
            side_effect=lambda value: copied.append(value) or result,
        ),
        patch("devtool.cli.console.print"),
    ):
        _maybe_copy(
            raw_output,
            "android-validation-lint: failed exit=2 time=1.00s\n",
            payload,
            args,
        )

    errors = (
        copied[0]
        .split("Errors:\n", 1)[1]
        .split("\nFailed:", 1)[0]
    )

    assert errors.count(message) == 1
    assert "INLINEDAPI:" not in errors
    assert "Error: Field requires API" not in errors
