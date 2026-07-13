from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from pathlib import Path

from rich import box
from rich.console import Console
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree
from rich.syntax import Syntax
from rich.pretty import Pretty

from .console_utils import create_console, is_narrow_console
from .models import Issue, OperationResult
from .repair_policy import build_repair_report, repair_summary_lines_from_results, applied_repair_findings_from_results

console = create_console()


def _status_text(result: OperationResult) -> Text:
    if result.skipped:
        return Text("skipped", style="yellow")
    if result.returncode == 0:
        return Text("ok", style="green")
    return Text("failed", style="bold red")


def _severity_text(issue: Issue) -> Text:
    style = "yellow" if issue.severity == "warning" else "bold red"
    return Text(issue.severity, style=style)


def _cell(value: str | None) -> Text:
    return Text(value or "-", overflow="fold", no_wrap=False)


def _strip_command_echo(output: str) -> str:
    text = (output or "").strip()
    if text.startswith("$ ") and "\n" in text:
        return text.split("\n", 1)[1].strip()
    return text


def _try_parse_json_text(text: str) -> object | None:
    stripped = _strip_command_echo(text)
    if not stripped:
        return None
    candidates = [stripped]
    first_obj = min([idx for idx in [stripped.find("{"), stripped.find("[")] if idx >= 0], default=-1)
    if first_obj > 0:
        candidates.append(stripped[first_obj:])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


def _shorten_output(text: str, *, max_lines: int = 12, max_chars: int = 3000) -> str:
    stripped = _strip_command_echo(text)
    if len(stripped) > max_chars:
        stripped = stripped[:max_chars].rstrip() + "\n… output truncated …"
    lines = stripped.splitlines()
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines] + [f"… {len(lines) - max_lines} more line(s) …"])
    return stripped


def _render_failed_output_details(results: list[OperationResult], target_console: Console) -> None:
    failed = [result for result in results if result.returncode != 0 and (result.output or "").strip()]
    if not failed:
        return
    renderables: list[object] = []
    for result in failed:
        parsed = _try_parse_json_text(result.output)
        if parsed is not None:
            renderables.append(_tree_from_json(f"{result.name} parsed output", parsed))
            continue
        table = Table(title=result.name, show_header=False, expand=not (is_narrow_console(target_console) or target_console.width <= 60), box=None)
        table.add_column("Field", style="bold", no_wrap=True)
        table.add_column("Value", overflow="fold")
        table.add_row("failure", result.failure_kind or "-")
        table.add_row("exit", str(result.returncode))
        if result.log_path:
            table.add_row("log", result.log_path)
        table.add_row("details", Text(_shorten_output(result.output), overflow="fold"))
        renderables.append(table)
    target_console.print(Panel(Group(*renderables), title="Failure details", expand=not is_narrow_console(target_console)))


def _format_issue_location(issue: Issue) -> str:
    if issue.file in {"", "-"}:
        return "-"
    if issue.line is None:
        return issue.file
    return f"{issue.file}:{issue.line}"


def calculate_totals(results: list[OperationResult]) -> dict[str, int]:
    return {
        "warnings": sum(1 for result in results for issue in result.issues if issue.severity == "warning"),
        "errors": sum(1 for result in results for issue in result.issues if issue.severity == "error"),
        "failed": sum(1 for result in results if result.returncode != 0),
        "skipped": sum(1 for result in results if result.skipped),
    }


_ACTION_LABELS = {
    "apply-overlay": "Apply overlay",
    "apply-overlays": "Apply overlay bundle",
    "apply-snapshot": "Apply snapshot",
    "artifact-check": "Check artifact",
    "artifact-cleanup": "Clean artifact",
    "restore": "Restore dependencies",
    "build": "Build",
    "test": "Run tests",
    "wine-test": "Run Wine tests",
    "package": "Package",
    "run": "Run",
    "git-add": "Stage changes",
    "git-commit": "Commit changes",
    "git-add-skipped": "Stage changes",
    "git-commit-skipped": "Commit changes",
    "rollback": "Rollback",
    "dotnet-build-server-shutdown": "Stop .NET build servers",
}

_DOTNET_TEST_SUMMARY_RE = re.compile(
    r"\b(?:Passed|Failed)!\s*-\s*Failed:\s*(\d+),\s*Passed:\s*(\d+),\s*Skipped:\s*(\d+),\s*Total:\s*(\d+)",
    re.IGNORECASE,
)
_VSTEST_SUMMARY_RE = re.compile(
    r"Total tests:\s*(\d+).*?Passed:\s*(\d+).*?Failed:\s*(\d+).*?Skipped:\s*(\d+)",
    re.IGNORECASE | re.DOTALL,
)
_PYTEST_SUMMARY_RE = re.compile(
    r"(?:(\d+)\s+failed(?:,\s*)?)?(?:(\d+)\s+passed)(?:,\s*(\d+)\s+skipped)?",
    re.IGNORECASE,
)
_COMMIT_PATTERNS = (
    re.compile(r"^\[[^\]\n]*?\s([0-9a-f]{7,40})\]\s", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\[detached HEAD\s+([0-9a-f]{7,40})\]\s", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\bcommit(?:ted)?(?:\s+as)?\s+([0-9a-f]{7,40})\b", re.IGNORECASE),
)


def _action_label(name: str) -> str:
    base = name.strip()
    # Preserve useful target suffixes such as "build [S3pe.Modern.sln]".
    prefix, separator, suffix = base.partition(" [")
    label = _ACTION_LABELS.get(prefix.lower(), prefix.replace("-", " ").replace("_", " ").strip().title())
    return f"{label} [{suffix}" if separator else label


def _human_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {remainder:04.1f}s"
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours}h {minutes}m"


def _extract_test_totals(results: list[OperationResult]) -> tuple[int, int, int, int] | None:
    output = "\n".join(result.output or "" for result in results if "test" in result.name.lower())
    if not output:
        output = "\n".join(result.output or "" for result in results)

    dotnet = list(_DOTNET_TEST_SUMMARY_RE.finditer(output))
    if dotnet:
        failed = sum(int(match.group(1)) for match in dotnet)
        passed = sum(int(match.group(2)) for match in dotnet)
        skipped = sum(int(match.group(3)) for match in dotnet)
        total = sum(int(match.group(4)) for match in dotnet)
        return passed, failed, skipped, total

    vstest = list(_VSTEST_SUMMARY_RE.finditer(output))
    if vstest:
        total = sum(int(match.group(1)) for match in vstest)
        passed = sum(int(match.group(2)) for match in vstest)
        failed = sum(int(match.group(3)) for match in vstest)
        skipped = sum(int(match.group(4)) for match in vstest)
        return passed, failed, skipped, total

    pytest_match = _PYTEST_SUMMARY_RE.search(output)
    if pytest_match:
        failed = int(pytest_match.group(1) or 0)
        passed = int(pytest_match.group(2) or 0)
        skipped = int(pytest_match.group(3) or 0)
        return passed, failed, skipped, passed + failed + skipped
    return None


def _extract_commit_hash(results: list[OperationResult]) -> str:
    for result in reversed(results):
        for key in ("commit_hash", "git_commit", "commit"):
            value = result.metadata.get(key) if result.metadata else None
            if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{7,40}", value, re.IGNORECASE):
                return value
        if "commit" not in result.name.lower():
            continue
        for pattern in _COMMIT_PATTERNS:
            match = pattern.search(result.output or "")
            if match:
                return match.group(1)
    return ""


def _result_status(result: OperationResult) -> tuple[str, str, str]:
    if result.skipped:
        return "–", "Skipped", "yellow"
    if result.returncode == 0:
        return "✓", "Done", "green"
    return "✗", "Failed", "bold red"


def _outcome(totals: dict[str, int]) -> tuple[str, str, str]:
    if totals["failed"] or totals["errors"]:
        return "✗", "Devtool failed", "red"
    if totals["warnings"]:
        return "!", "Completed with warnings", "yellow"
    return "✓", "Completed successfully", "green"


def _render_outcome_panel(results: list[OperationResult], totals: dict[str, int], target_console: Console) -> None:
    symbol, title, style = _outcome(totals)
    duration = sum(result.duration_seconds for result in results)
    tests = _extract_test_totals(results)
    commit_hash = _extract_commit_hash(results)

    headline = Text()
    headline.append(f"{symbol} ", style=f"bold {style}")
    headline.append(title, style=f"bold {style}")

    metrics = Table.grid(padding=(0, 2))
    metrics.add_column(style="dim", no_wrap=True)
    metrics.add_column(no_wrap=False)
    metrics.add_row("Actions", f"{sum(not result.skipped for result in results)} completed, {totals['skipped']} skipped")
    metrics.add_row("Duration", _human_duration(duration))
    if tests is not None:
        passed, failed, skipped, total = tests
        test_text = Text()
        test_text.append(f"{passed} passed", style="bold green" if failed == 0 else "green")
        test_text.append(f", {failed} failed", style="bold red" if failed else "dim")
        test_text.append(f", {skipped} skipped", style="yellow" if skipped else "dim")
        test_text.append(f" ({total} total)", style="dim")
        metrics.add_row("Tests", test_text)
    metrics.add_row(
        "Diagnostics",
        Text.assemble(
            (str(totals["warnings"]), "bold yellow" if totals["warnings"] else "dim"),
            (" warnings, ", "dim"),
            (str(totals["errors"]), "bold red" if totals["errors"] else "dim"),
            (" errors", "dim"),
        ),
    )
    if commit_hash:
        metrics.add_row("Commit", Text(commit_hash[:12], style="bold cyan"))

    target_console.print(
        Panel(
            Group(headline, Text(""), metrics),
            border_style=style,
            padding=(1, 2),
            expand=not (is_narrow_console(target_console) or target_console.width <= 60),
        )
    )


def _render_repair_panel(results: list[OperationResult], target_console: Console) -> None:
    summary_lines = repair_summary_lines_from_results(results)
    if not summary_lines:
        return

    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column(ratio=1, overflow="fold")
    for line in summary_lines:
        table.add_row(Text(line, overflow="fold"))

    target_console.print(
        Panel(
            table,
            title="Repair summary",
            border_style="bright_magenta",
            padding=(1, 2),
            expand=not (is_narrow_console(target_console) or target_console.width <= 60),
        )
    )


def _runner_notification_message(*, repo_root: str, command: str, warnings: int, errors: int, failed: int) -> tuple[str, str, str]:
    if failed or errors:
        emoji, level, status = "❌", "error", "FAIL"
    elif warnings:
        emoji, level, status = "⚠️", "warning", "WARN"
    else:
        emoji, level, status = "✅", "success", "PASS"

    repo_name = Path(repo_root).name or repo_root
    title = f"{emoji} {status}"
    body = f"{emoji} {status}: {repo_name}: {command} • {errors}E {warnings}W"
    return level, title, body




def _overlay_groups(results: list[OperationResult]) -> tuple[list[tuple[dict[str, object], list[OperationResult]]], list[OperationResult]]:
    grouped: dict[tuple[int, str], tuple[dict[str, object], list[OperationResult]]] = {}
    order: list[tuple[int, str]] = []
    ungrouped: list[OperationResult] = []
    for result in results:
        metadata = result.metadata or {}
        raw_index = metadata.get("overlay_index")
        if raw_index is None:
            ungrouped.append(result)
            continue
        try:
            index = int(raw_index)
        except Exception:
            ungrouped.append(result)
            continue
        label = str(metadata.get("overlay_label") or f"Overlay {index}")
        key = (index, label)
        if key not in grouped:
            info = {
                "index": index,
                "total": int(metadata.get("overlay_total") or 0),
                "label": label,
                "target": str(metadata.get("overlay_target") or ""),
                "artifact": str(metadata.get("overlay_artifact") or ""),
            }
            grouped[key] = (info, [])
            order.append(key)
        grouped[key][1].append(result)
    order.sort(key=lambda item: item[0])
    return [grouped[key] for key in order], ungrouped


def _overlay_status(results: list[OperationResult]) -> tuple[str, str, str]:
    totals = calculate_totals(results)
    if totals["failed"] or totals["errors"]:
        return "✗", "Failed", "red"
    if totals["warnings"]:
        return "!", "Passed with warnings", "yellow"
    return "✓", "Passed", "green"


def _overlay_panel(info: dict[str, object], results: list[OperationResult], target_console: Console) -> Panel:
    symbol, status, style = _overlay_status(results)
    totals = calculate_totals(results)
    tests = _extract_test_totals(results)
    commit_hash = _extract_commit_hash(results)
    duration = sum(result.duration_seconds for result in results)

    title = Text()
    title.append(f"{symbol} ", style=f"bold {style}")
    title.append(f"Overlay {info['index']}/{info['total']}: {info['label']}", style="bold")

    metrics = Table.grid(expand=True, padding=(0, 2))
    metrics.add_column(style="dim", no_wrap=True)
    metrics.add_column(ratio=1, overflow="fold")
    metrics.add_row("Status", Text(status, style=f"bold {style}"))
    if info.get("target"):
        metrics.add_row("Target", Text(str(info["target"]), style="cyan"))
    if tests is not None:
        passed, failed, skipped, total = tests
        test_text = Text()
        test_text.append(f"{passed} passed", style="bold green" if failed == 0 else "green")
        test_text.append(f", {failed} failed", style="bold red" if failed else "dim")
        test_text.append(f", {skipped} skipped", style="yellow" if skipped else "dim")
        test_text.append(f" ({total} total)", style="dim")
        metrics.add_row("Tests", test_text)
    metrics.add_row(
        "Diagnostics",
        Text.assemble(
            (str(totals["warnings"]), "bold yellow" if totals["warnings"] else "dim"),
            (" warnings, ", "dim"),
            (str(totals["errors"]), "bold red" if totals["errors"] else "dim"),
            (" errors", "dim"),
        ),
    )
    metrics.add_row("Duration", _human_duration(duration))
    if commit_hash:
        metrics.add_row("Commit", Text(commit_hash[:12], style="bold cyan"))

    actions = Table(box=box.SIMPLE, show_header=True, expand=True, padding=(0, 1))
    actions.add_column("", width=1, no_wrap=True)
    actions.add_column("Action", ratio=3)
    actions.add_column("Result", no_wrap=True)
    actions.add_column("Time", justify="right", no_wrap=True)
    actions.add_column("Details", ratio=4, overflow="fold")
    for result in results:
        mark, result_status, result_style = _result_status(result)
        actions.add_row(
            Text(mark, style=result_style),
            Text(_action_label(result.name), style="bold"),
            Text(result_status, style=result_style),
            Text(_human_duration(result.duration_seconds), style="dim"),
            _result_detail(result),
        )

    issue_items = [issue for result in results for issue in result.issues]
    body: list[object] = [metrics, Text(""), actions]
    if issue_items:
        warning_table = Table(box=None, show_header=False, expand=True, padding=(0, 1))
        warning_table.add_column("", width=1)
        warning_table.add_column(ratio=1, overflow="fold")
        for issue in issue_items[:8]:
            mark = "!" if issue.severity == "warning" else "✗"
            issue_style = "yellow" if issue.severity == "warning" else "bold red"
            location = _format_issue_location(issue)
            message = f"{location}: {issue.message}" if location != "-" else issue.message
            warning_table.add_row(Text(mark, style=issue_style), Text(message, style=issue_style))
        if len(issue_items) > 8:
            warning_table.add_row(Text("…", style="dim"), Text(f"{len(issue_items) - 8} more issue(s); see full log", style="dim"))
        body.extend([Text(""), warning_table])

    return Panel(
        Group(*body),
        title=title,
        border_style=style,
        padding=(1, 1),
        expand=not (is_narrow_console(target_console) or target_console.width <= 60),
    )


def _render_overlay_groups(results: list[OperationResult], target_console: Console) -> bool:
    groups, ungrouped = _overlay_groups(results)
    if not groups:
        return False
    for info, group_results in groups:
        target_console.print(_overlay_panel(info, group_results, target_console))
    if ungrouped:
        if is_narrow_console(target_console) or target_console.width <= 60:
            _render_compact_results(ungrouped, target_console)
        else:
            _render_wide_results(ungrouped, target_console)
    return True

def render_summary(results: list[OperationResult], *, console_obj: Console | None = None) -> dict[str, int]:
    target_console = console_obj or console
    totals = calculate_totals(results)

    _render_outcome_panel(results, totals, target_console)
    _render_repair_panel(results, target_console)
    grouped = _render_overlay_groups(results, target_console)
    if not grouped:
        if is_narrow_console(target_console) or target_console.width <= 60:
            _render_compact_results(results, target_console)
            _render_compact_issues([issue for result in results for issue in result.issues], target_console)
        else:
            _render_wide_results(results, target_console)
            _render_wide_issues([issue for result in results for issue in result.issues], target_console)

    _render_failed_output_details(results, target_console)
    return totals


def _result_detail(result: OperationResult) -> Text:
    if result.returncode != 0:
        parts = [f"exit {result.returncode}"]
        if result.failure_kind:
            parts.append(result.failure_kind.replace("_", " "))
        if result.log_path:
            parts.append(f"log: {result.log_path}")
        return Text(" • ".join(parts), style="red", overflow="fold")
    if result.skipped:
        return Text("Not run", style="dim yellow")
    if result.issues:
        warnings = sum(issue.severity == "warning" for issue in result.issues)
        errors = sum(issue.severity == "error" for issue in result.issues)
        pieces = []
        if warnings:
            pieces.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
        if errors:
            pieces.append(f"{errors} error{'s' if errors != 1 else ''}")
        return Text(", ".join(pieces), style="yellow" if not errors else "red")
    return Text("", style="dim")


def _render_wide_results(results: list[OperationResult], target_console: Console) -> None:
    op_table = Table(
        title="Actions",
        show_lines=False,
        expand=True,
        box=box.SIMPLE_HEAVY,
        header_style="bold",
        padding=(0, 1),
    )
    op_table.add_column("", width=1, no_wrap=True)
    op_table.add_column("Action", style="bold", ratio=3)
    op_table.add_column("Result", no_wrap=True)
    op_table.add_column("Duration", justify="right", no_wrap=True)
    op_table.add_column("Details", ratio=4, overflow="fold")

    for result in results:
        symbol, status, style = _result_status(result)
        op_table.add_row(
            Text(symbol, style=style),
            _cell(_action_label(result.name)),
            Text(status, style=style),
            Text(_human_duration(result.duration_seconds), style="dim"),
            _result_detail(result),
        )
    target_console.print(op_table)


def _render_compact_results(results: list[OperationResult], target_console: Console) -> None:
    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column(width=1, no_wrap=True)
    table.add_column(ratio=1, overflow="fold")
    table.add_column(justify="right", no_wrap=True)
    for result in results:
        symbol, _status, style = _result_status(result)
        label = Text(_action_label(result.name), style="bold")
        detail = _result_detail(result)
        if detail.plain:
            label.append("\n")
            label.append_text(detail)
        table.add_row(
            Text(symbol, style=style),
            label,
            Text(_human_duration(result.duration_seconds), style="dim"),
        )
    target_console.print(Panel(table, title="Actions", border_style="dim", expand=True))


def _render_wide_issues(issues: list[Issue], target_console: Console) -> None:
    if not issues:
        return
    issue_table = Table(title="Warnings and Errors", show_lines=False, expand=False)
    issue_table.add_column("Level")
    issue_table.add_column("Command")
    issue_table.add_column("Location")
    issue_table.add_column("Code")
    issue_table.add_column("Message", overflow="fold")
    for issue in issues:
        issue_table.add_row(
            _severity_text(issue),
            _cell(issue.command),
            _cell(_format_issue_location(issue)),
            _cell(issue.code or "-"),
            _cell(issue.message),
        )
    target_console.print(issue_table)


def _render_compact_issues(issues: list[Issue], target_console: Console) -> None:
    for issue in issues:
        grid = Table.grid(expand=False)
        grid.add_column(style="bold", no_wrap=True)
        grid.add_column(overflow="fold")
        grid.add_row(_cell("level"), _severity_text(issue))
        grid.add_row(_cell("command"), _cell(issue.command))
        grid.add_row(_cell("location"), _cell(_format_issue_location(issue)))
        grid.add_row(_cell("code"), _cell(issue.code or "-"))
        grid.add_row(_cell("message"), _cell(issue.message))
        target_console.print(Panel(grid, title="Issue", expand=False))


def render_status_table(rows: list[tuple[str, str]], *, console_obj: Console | None = None) -> None:
    target_console = console_obj or console
    if is_narrow_console(target_console):
        for key, value in rows:
            grid = Table.grid(expand=False)
            grid.add_column(style="bold", no_wrap=True)
            grid.add_column(overflow="fold")
            grid.add_row(_cell(key), _cell(value))
            target_console.print(Panel(grid, title="Status", expand=False))
        return

    table = Table(title="Status", show_lines=False, expand=False)
    table.add_column("Field", style="bold")
    table.add_column("Value", overflow="fold")
    for key, value in rows:
        table.add_row(_cell(key), _cell(value))
    target_console.print(table)


def build_json_summary(results: list[OperationResult], *, repo_root: str) -> dict:
    totals = calculate_totals(results)
    overlay_groups, _ungrouped = _overlay_groups(results)
    repair_report = build_repair_report(results)
    applied_repairs = [
        {
            "bucket": finding.bucket,
            "code": finding.code,
            "file": finding.file,
            "line": finding.line,
            "message": finding.message,
            "fix": finding.fix,
        }
        for finding in applied_repair_findings_from_results(results)
    ]
    applied_metadata = next(
        (result.metadata for result in results if result.metadata and result.metadata.get("applied_repairs")),
        {},
    ) or {}
    data = {
        "repo_root": repo_root,
        "warnings": totals["warnings"],
        "errors": totals["errors"],
        "failed": totals["failed"],
        "skipped": totals["skipped"],
        "repairs": {
            **repair_report.to_dict(),
            "applied": applied_repairs,
            "before_failures": applied_metadata.get("before_failures"),
            "after_failures": applied_metadata.get("after_failures"),
            "restored": applied_metadata.get("restored", False),
        },
        "results": [
            {
                "name": result.name,
                "argv": result.argv,
                "returncode": result.returncode,
                "duration_seconds": result.duration_seconds,
                "log_path": result.log_path,
                "skipped": result.skipped,
                "failure_kind": result.failure_kind,
                "metadata": result.metadata,
                "output": result.output,
                "issues": [
                    {
                        "severity": issue.severity,
                        "command": issue.command,
                        "file": issue.file,
                        "line": issue.line,
                        "code": issue.code,
                        "message": issue.message,
                    }
                    for issue in result.issues
                ],
            }
            for result in results
        ],
    }
    if overlay_groups:
        data["overlays"] = [
            {
                **info,
                "warnings": calculate_totals(group_results)["warnings"],
                "errors": calculate_totals(group_results)["errors"],
                "failed": calculate_totals(group_results)["failed"],
                "skipped": calculate_totals(group_results)["skipped"],
                "duration_seconds": sum(result.duration_seconds for result in group_results),
                "tests": (
                    {
                        "passed": tests[0],
                        "failed": tests[1],
                        "skipped": tests[2],
                        "total": tests[3],
                    }
                    if (tests := _extract_test_totals(group_results)) is not None
                    else None
                ),
                "commit_hash": _extract_commit_hash(group_results) or None,
                "result_names": [result.name for result in group_results],
            }
            for info, group_results in overlay_groups
        ]
    return data


def build_runner_json(
    results: list[OperationResult],
    *,
    repo_root: str,
    command: str,
    raw_output: str,
    summary_text: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> dict:
    json_summary = build_json_summary(results, repo_root=repo_root)
    totals = {
        "warnings": json_summary["warnings"],
        "errors": json_summary["errors"],
        "failed": json_summary["failed"],
        "skipped": json_summary["skipped"],
    }
    ok = totals["errors"] == 0 and totals["failed"] == 0
    duration_s = None
    if started_at and finished_at:
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=timezone.utc)
        duration_s = max(0.0, (finished_at - started_at).total_seconds())

    def _iso(value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    level, title, body = _runner_notification_message(
        repo_root=repo_root,
        command=command,
        warnings=totals["warnings"],
        errors=totals["errors"],
        failed=totals["failed"],
    )

    return {
        "schema_version": 1,
        "ok": ok,
        "command": command,
        "repo": repo_root,
        "started_at": _iso(started_at),
        "finished_at": _iso(finished_at),
        "duration_s": duration_s,
        "exit_code": 0 if ok else 1,
        "totals": totals,
        "results": [
            {
                "name": result.name,
                "status": "skipped" if result.skipped else ("ok" if result.returncode == 0 else "failed"),
                "time_s": result.duration_seconds,
                "exit_code": result.returncode,
                "log": result.log_path or None,
                "failure_kind": result.failure_kind or None,
                "metadata": result.metadata or {},
            }
            for result in results
        ],
        "clipboard": {
            "preferred": "json",
            "summary": summary_text,
            "json": json_summary,
            "raw": raw_output,
        },
        "notification": {
            "should_notify": True,
            "level": level,
            "title": title,
            "body": body,
        },
    }


def _human_json_key(key: str) -> str:
    return key.replace("_", " ").replace("-", " ").title()


def _json_scalar(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _tree_from_json(label: str, value: object) -> Tree:
    tree = Tree(str(label), guide_style="dim")
    _add_json_to_tree(tree, value)
    return tree


def _add_json_to_tree(tree: Tree, value: object) -> None:
    if isinstance(value, dict):
        if not value:
            tree.add("{}")
            return
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                child = tree.add(f"[bold]{key}[/bold]")
                _add_json_to_tree(child, item)
            else:
                tree.add(f"[bold]{key}[/bold]: {_json_scalar(item)}")
        return
    if isinstance(value, list):
        if not value:
            tree.add("[]")
            return
        for index, item in enumerate(value, start=1):
            if isinstance(item, (dict, list)):
                child = tree.add(f"[{index}]")
                _add_json_to_tree(child, item)
            else:
                tree.add(f"{index}. {_json_scalar(item)}")
        return
    tree.add(_json_scalar(value))


def _without_sha256(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): _without_sha256(item)
            for key, item in value.items()
            if "sha256" not in str(key).lower()
        }
    if isinstance(value, list):
        return [_without_sha256(item) for item in value]
    return value


def _artifact_kind(manifest: dict) -> str:
    kind = str(manifest.get("kind") or manifest.get("type") or "overlay").strip()
    return kind.replace("devtool.", "").replace("_", " ").replace("-", " ").title()


def _artifact_name(manifest: dict) -> str:
    return str(manifest.get("name") or manifest.get("label") or "Devtool artifact").strip() or "Devtool artifact"


def _inventory_rows(manifest: dict) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    inventory = manifest.get("inventory")
    entries = inventory.get("entries") if isinstance(inventory, dict) else inventory if isinstance(inventory, list) else None
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path") or "").strip()
            if path:
                rows.append((str(entry.get("action") or "payload").strip().lower() or "payload", path))
    if rows:
        return rows
    for key, action in (("files", "payload"), ("mutates", "modify"), ("deletes", "delete")):
        values = manifest.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            path = str(value).strip()
            if path:
                rows.append((action, path))
    return rows


def _artifact_headline_panel(manifest: dict, target_console: Console) -> Panel:
    kind = _artifact_kind(manifest)
    name = _artifact_name(manifest)
    description = str(manifest.get("description") or "").strip()
    inventory_rows = _inventory_rows(manifest)
    overlays = manifest.get("overlays") or manifest.get("apply_order")
    overlay_count = len(overlays) if isinstance(overlays, list) else 0

    headline = Text()
    headline.append("◆ ", style="bold cyan")
    headline.append(name, style="bold")

    details = Table.grid(padding=(0, 2))
    details.add_column(style="dim", no_wrap=True)
    details.add_column(overflow="fold")
    details.add_row("Type", Text(kind, style="bold cyan"))
    schema = manifest.get("schema_version", manifest.get("version"))
    if schema is not None:
        details.add_row("Schema", str(schema))
    repository = manifest.get("repository")
    if isinstance(repository, dict):
        repo_name = str(repository.get("name") or "").strip()
    else:
        repo_name = str(manifest.get("repo_name") or manifest.get("repo") or "").strip()
    if repo_name:
        details.add_row("Repository", repo_name)
    target = str(manifest.get("target") or manifest.get("validate_target") or "").strip()
    if target:
        details.add_row("Target", Text(target, style="cyan"))
    solution = str(manifest.get("solution") or manifest.get("expected_solution") or "").strip()
    if solution:
        details.add_row("Solution", solution)
    if overlay_count:
        details.add_row("Overlays", Text(str(overlay_count), style="bold magenta"))
    if inventory_rows:
        details.add_row("Paths", Text(str(len(inventory_rows)), style="bold green"))
    if description:
        details.add_row("Purpose", description)

    validation = manifest.get("validation") if isinstance(manifest.get("validation"), dict) else {}
    if validation:
        required = bool(validation.get("required", False))
        details.add_row("Validation", Text("Required" if required else "Configured", style="bold green" if required else "yellow"))
    apply = manifest.get("apply") if isinstance(manifest.get("apply"), dict) else {}
    commit = apply.get("commit") if isinstance(apply.get("commit"), dict) else {}
    if commit:
        details.add_row("Commit", Text("Enabled" if commit.get("enabled") else "Disabled", style="green" if commit.get("enabled") else "dim"))

    return Panel(
        Group(headline, Text(""), details),
        title="Artifact summary",
        border_style="cyan",
        padding=(1, 2),
        expand=not (is_narrow_console(target_console) or target_console.width <= 60),
    )


def _inventory_table(manifest: dict) -> Panel | None:
    rows = _inventory_rows(manifest)
    if not rows:
        return None

    action_styles = {"add": "green", "modify": "yellow", "delete": "red", "payload": "cyan"}
    action_marks = {"add": "+", "modify": "~", "delete": "−", "payload": "•"}
    counts: dict[str, int] = {}
    for action, _path in rows:
        counts[action] = counts.get(action, 0) + 1

    metrics = Text()
    metric_order = (("add", "added"), ("modify", "modified"), ("delete", "deleted"), ("payload", "payload"))
    first = True
    for action, label in metric_order:
        count = counts.get(action, 0)
        if not count:
            continue
        if not first:
            metrics.append("   ", style="dim")
        style = action_styles.get(action, "cyan")
        metrics.append(f"{action_marks.get(action, '•')} ", style=f"bold {style}")
        metrics.append(f"{count} {label}", style=style)
        first = False
    other_count = sum(value for key, value in counts.items() if key not in action_styles)
    if other_count:
        if not first:
            metrics.append("   ", style="dim")
        metrics.append("• ", style="bold cyan")
        metrics.append(f"{other_count} other", style="cyan")

    table = Table(show_lines=False, expand=True, box=box.MINIMAL, pad_edge=False, header_style="bold dim")
    table.add_column("Change", no_wrap=True, width=13)
    table.add_column("Repository path", overflow="fold", ratio=1)
    for action, path in rows:
        style = action_styles.get(action, "cyan")
        label = Text()
        label.append(action_marks.get(action, "•") + " ", style=f"bold {style}")
        label.append(action.upper(), style=f"bold {style}")
        table.add_row(label, Text(path, overflow="fold"))

    return Panel(
        Group(metrics, Text(""), table),
        title="[bold]Artifact contents[/]",
        subtitle=f"[dim]{len(rows)} repository path{'s' if len(rows) != 1 else ''}[/]",
        title_align="left",
        subtitle_align="right",
        border_style="bright_blue",
        padding=(1, 2),
        expand=True,
    )


def _bundle_table(manifest: dict) -> Panel | None:
    overlays = manifest.get("overlays") or manifest.get("apply_order")
    if not isinstance(overlays, list) or not overlays:
        return None
    table = Table(show_lines=False, expand=True, box=box.MINIMAL, pad_edge=False, header_style="bold dim")
    table.add_column("Overlay", overflow="fold", ratio=1)
    table.add_column("Target", overflow="fold")
    table.add_column("Requirement", no_wrap=True)
    table.add_column("Ordering", overflow="fold")
    required_count = 0
    for value in overlays:
        if isinstance(value, str):
            required_count += 1
            table.add_row(Path(value).name, "—", Text("● Required", style="green"), "—")
            continue
        if not isinstance(value, dict):
            continue
        path = str(value.get("path") or value.get("artifact") or "").strip()
        label = str(value.get("label") or value.get("name") or value.get("id") or Path(path).name or "Overlay").strip()
        target = str(value.get("target") or "—").strip() or "—"
        required = bool(value.get("required", True))
        required_count += int(required)
        deps: list[str] = []
        for key in ("after", "requires"):
            items = value.get(key)
            if isinstance(items, list) and items:
                deps.append(f"{key}: " + ", ".join(str(item) for item in items))
        table.add_row(
            label,
            Text(target, style="cyan" if target != "—" else "dim"),
            Text("● Required" if required else "○ Optional", style="green" if required else "yellow"),
            "; ".join(deps) or "—",
        )
    return Panel(
        table,
        title="[bold]Bundle composition[/]",
        subtitle=f"[dim]{len(overlays)} overlays · {required_count} required[/]",
        title_align="left",
        subtitle_align="right",
        border_style="magenta",
        padding=(1, 2),
        expand=True,
    )


def _policy_rows(section: str, value: dict) -> list[tuple[str, str, Text]]:
    cleaned = _without_sha256(value)
    if not isinstance(cleaned, dict) or not cleaned:
        return []
    rows: list[tuple[str, str, Text]] = []
    for key, item in cleaned.items():
        if isinstance(item, list):
            rendered = ", ".join(str(part) for part in item) if item else "—"
        elif isinstance(item, dict):
            compact = ", ".join(f"{subkey}={_json_scalar(subvalue)}" for subkey, subvalue in item.items())
            rendered = compact or "—"
        else:
            rendered = _json_scalar(item)
        value_text = Text(rendered, overflow="fold")
        lowered = rendered.lower()
        if lowered in {"true", "enabled", "required"}:
            value_text.stylize("green")
        elif lowered in {"false", "disabled"}:
            value_text.stylize("dim")
        rows.append((section, _human_json_key(str(key)), value_text))
    return rows


def _execution_policy_panel(manifest: dict) -> Panel | None:
    rows: list[tuple[str, str, Text]] = []
    validation = manifest.get("validation")
    if isinstance(validation, dict):
        rows.extend(_policy_rows("Validation policy", validation))
    apply = manifest.get("apply")
    if isinstance(apply, dict):
        rows.extend(_policy_rows("Apply policy", apply))
    if not rows:
        return None

    table = Table(show_lines=False, expand=True, box=box.MINIMAL, pad_edge=False, header_style="bold dim")
    table.add_column("Policy", no_wrap=True, width=19)
    table.add_column("Setting", style="bold", no_wrap=True, width=27)
    table.add_column("Value", overflow="fold", ratio=1)
    previous = ""
    for section, setting, value in rows:
        section_text = Text(section if section != previous else "", style="cyan" if section.startswith("Validation") else "magenta")
        table.add_row(section_text, setting, value)
        previous = section
    return Panel(
        table,
        title="[bold]Execution contract[/]",
        title_align="left",
        border_style="cyan",
        padding=(1, 2),
        expand=True,
    )


def _repository_layout_panel(manifest: dict) -> Panel | None:
    repository = manifest.get("repository")
    workspace = manifest.get("workspace")
    workspaces = manifest.get("workspaces")
    has_repository = isinstance(repository, dict) and bool(repository)
    has_workspace = isinstance(workspace, dict) and bool(workspace)
    has_workspaces = isinstance(workspaces, list) and bool(workspaces)
    if not (has_repository or has_workspace or has_workspaces):
        return None

    renderables: list[object] = []
    metadata = Table.grid(padding=(0, 2), expand=True)
    metadata.add_column(style="bold dim", no_wrap=True)
    metadata.add_column(overflow="fold", ratio=1)
    if has_repository:
        cleaned = _without_sha256(repository)
        for key, value in cleaned.items():
            metadata.add_row(_human_json_key(str(key)), Text(_json_scalar(value), overflow="fold"))
    if has_workspace:
        cleaned = _without_sha256(workspace)
        for key, value in cleaned.items():
            metadata.add_row("Workspace " + _human_json_key(str(key)), Text(_json_scalar(value), overflow="fold"))
    if metadata.row_count:
        renderables.append(metadata)

    if has_workspaces:
        if renderables:
            renderables.append(Text(""))
        table = Table(show_lines=False, expand=True, box=box.MINIMAL, pad_edge=False, header_style="bold dim")
        table.add_column("Workspace path", overflow="fold", ratio=1)
        table.add_column("Devtool target", overflow="fold", ratio=1)
        for item in workspaces:
            if isinstance(item, dict):
                path = str(item.get("root") or item.get("path") or "—")
                target = str(item.get("target") or "—")
            else:
                path, target = str(item), "—"
            table.add_row(path, Text(target, style="cyan" if target != "—" else "dim"))
        renderables.append(table)

    return Panel(
        Group(*renderables),
        title="[bold]Repository layout[/]",
        subtitle=f"[dim]{len(workspaces)} workspace{'s' if len(workspaces) != 1 else ''}[/]" if has_workspaces else None,
        title_align="left",
        subtitle_align="right",
        border_style="green",
        padding=(1, 2),
        expand=True,
    )

def render_artifact_manifest(manifest: dict, *, console_obj: Console | None = None) -> None:
    """Render an artifact or bundle as one cohesive, card-based inspection report."""
    if not manifest:
        return
    target_console = console_obj or console
    renderables: list[object] = [_artifact_headline_panel(manifest, target_console)]

    bundle_panel = _bundle_table(manifest)
    if bundle_panel is not None:
        renderables.append(bundle_panel)
    inventory_panel = _inventory_table(manifest)
    if inventory_panel is not None:
        renderables.append(inventory_panel)
    policy_panel = _execution_policy_panel(manifest)
    if policy_panel is not None:
        renderables.append(policy_panel)
    layout_panel = _repository_layout_panel(manifest)
    if layout_panel is not None:
        renderables.append(layout_panel)

    target_console.print(Group(*renderables))
