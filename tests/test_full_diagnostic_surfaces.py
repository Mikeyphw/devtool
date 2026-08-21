from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

from devtool.cli import _maybe_copy
from devtool.models import CommandSpec, ProjectConfig
from devtool.runners import run_specs


def _lint_xml(count: int = 18) -> str:
    issues = []

    for index in range(1, count + 1):
        severity = (
            "Error"
            if index % 5 == 0
            else "Warning"
        )

        issues.append(
            f'<issue '
            f'id="Rule{index}" '
            f'severity="{severity}" '
            f'message="Diagnostic {index}">'
            f'<location '
            f'file="src/File{index}.kt" '
            f'line="{index}"/>'
            f'</issue>'
        )

    return (
        "<issues>"
        + "".join(issues)
        + "</issues>"
    )


def test_run_log_contains_every_android_lint_report_diagnostic(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    report = (
        project
        / "app"
        / "build"
        / "reports"
        / "lint-results-debug.xml"
    )

    log_path = tmp_path / "run.log"
    xml = _lint_xml(18)

    script = (
        "from pathlib import Path; "
        f"p=Path({str(report)!r}); "
        "p.parent.mkdir("
        "parents=True, exist_ok=True); "
        f"p.write_text({xml!r}, "
        "encoding='utf-8'); "
        "print("
        "'Lint found 3 errors and 15 warnings. "
        "First failure: Diagnostic 5'"
        "); "
        "raise SystemExit(1)"
    )

    spec = CommandSpec(
        "lint",
        [
            sys.executable,
            "-c",
            script,
        ],
        cwd=str(project),
        metadata={
            "report_parsers": [
                "android-lint",
            ],
            "gradle_tasks": [
                ":app:lintDebug",
            ],
            "project_root": str(project),
        },
    )

    config = ProjectConfig(
        repo_root=project,
        project_root=project,
        kind="android",
    )

    results, _raw = run_specs(
        config,
        [spec],
        verbose=False,
        log_path=log_path,
    )

    assert len(results) == 1
    assert len(results[0].issues) == 18

    text = log_path.read_text(
        encoding="utf-8"
    )

    assert (
        "Devtool complete diagnostics (18):"
        in text
    )

    assert (
        "Rule1: Diagnostic 1"
        in text
    )

    assert (
        "Rule18: Diagnostic 18"
        in text
    )

    # There must be no diagnostic count cap.
    assert "... and" not in text


def test_clipboard_failure_summary_contains_every_structured_warning_and_error(
) -> None:
    issues = []

    for index in range(1, 31):
        severity = (
            "error"
            if index % 6 == 0
            else "warning"
        )

        issues.append(
            {
                "severity": severity,
                "command": "lint",
                "file": (
                    "app/src/main/kotlin/"
                    f"File{index}.kt"
                ),
                "line": index,
                "code": f"Rule{index}",
                "message": (
                    f"Diagnostic {index}"
                ),
            }
        )

    json_text = json.dumps(
        {
            "warnings": sum(
                item["severity"] == "warning"
                for item in issues
            ),
            "errors": sum(
                item["severity"] == "error"
                for item in issues
            ),
            "failed": 1,
            "skipped": 0,
            "results": [
                {
                    "name": (
                        "android-validation-lint"
                    ),
                    "returncode": 1,
                    "failure_kind": (
                        "validation_failure"
                    ),
                    "output": (
                        "Lint found errors; "
                        "aborting build."
                    ),
                    "issues": issues,
                }
            ],
        }
    )

    args = SimpleNamespace(
        no_clipboard=False,
        copy=True,
        copy_via="auto",
        command="validate",
    )

    copied: list[str] = []

    result = SimpleNamespace(
        success=True,
        backend="test",
        reason="",
    )

    with patch(
        "devtool.cli.copy_text",
        side_effect=lambda text: (
            copied.append(text)
            or result
        ),
    ), patch(
        "devtool.cli.console.print"
    ):
        _maybe_copy(
            (
                "Lint found errors; "
                "aborting build."
            ),
            (
                "android-validation-lint: "
                "failed exit=1 time=1.00s"
            ),
            json_text,
            args,
        )

    assert len(copied) == 1

    text = copied[0]

    assert "Warnings:" in text
    assert "Errors:" in text

    assert (
        "File1.kt:1: "
        "Rule1: Diagnostic 1"
        in text
    )

    assert (
        "File30.kt:30: "
        "Rule30: Diagnostic 30"
        in text
    )

    for index in range(1, 31):
        assert (
            f"Diagnostic {index}"
            in text
        )

    assert (
        "more unique warning"
        not in text
    )

    assert (
        "output truncated"
        not in text
    )


def test_clipboard_raw_fallback_recognizes_canonical_and_kotlin_diagnostics(
) -> None:
    raw = """$ ./gradlew lintDebug
w: file:///repo/app/src/main/kotlin/Demo.kt:7:5 Deprecated API use.
Devtool complete diagnostics (2):
- WARNING [lint] app/src/main/kotlin/Lint.kt:8: NewApi: Requires API 33.
- ERROR [lint] app/src/main/kotlin/Lint.kt:9: FatalRule: Broken contract.
"""

    json_text = json.dumps(
        {
            "warnings": 0,
            "errors": 0,
            "failed": 1,
            "skipped": 0,
            "results": [
                {
                    "name": "lint",
                    "returncode": 1,
                    "failure_kind": (
                        "validation_failure"
                    ),
                    "output": raw,
                    "issues": [],
                }
            ],
        }
    )

    args = SimpleNamespace(
        no_clipboard=False,
        copy=True,
        copy_via="auto",
        command="validate",
    )

    copied: list[str] = []

    result = SimpleNamespace(
        success=True,
        backend="test",
        reason="",
    )

    with patch(
        "devtool.cli.copy_text",
        side_effect=lambda text: (
            copied.append(text)
            or result
        ),
    ), patch(
        "devtool.cli.console.print"
    ):
        _maybe_copy(
            raw,
            (
                "lint: failed "
                "exit=1 time=1.00s"
            ),
            json_text,
            args,
        )

    assert len(copied) == 1

    text = copied[0]

    assert (
        "w: file:///repo/app/src/main/"
        "kotlin/Demo.kt:7:5 "
        "Deprecated API use."
        in text
    )

    assert (
        "- WARNING [lint] "
        "app/src/main/kotlin/Lint.kt:8: "
        "NewApi: Requires API 33."
        in text
    )

    assert (
        "- ERROR [lint] "
        "app/src/main/kotlin/Lint.kt:9: "
        "FatalRule: Broken contract."
        in text
    )
