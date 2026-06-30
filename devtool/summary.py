from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from .console_utils import create_console, is_narrow_console
from .models import Issue, OperationResult

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


def render_summary(results: list[OperationResult], *, console_obj: Console | None = None) -> dict[str, int]:
    target_console = console_obj or console
    totals = calculate_totals(results)

    if is_narrow_console(target_console):
        _render_compact_results(results, target_console)
        _render_compact_issues([issue for result in results for issue in result.issues], target_console)
    else:
        _render_wide_results(results, target_console)
        _render_wide_issues([issue for result in results for issue in result.issues], target_console)

    summary = Text()
    summary.append("warnings=", style="yellow")
    summary.append(str(totals["warnings"]), style="bold yellow")
    summary.append("  errors=", style="red")
    summary.append(str(totals["errors"]), style="bold red")
    summary.append("  failed=", style="red")
    summary.append(str(totals["failed"]), style="bold red")
    summary.append("  skipped=", style="yellow")
    summary.append(str(totals["skipped"]), style="bold yellow")
    target_console.print(Panel(summary, title="Totals", expand=False))

    return totals


def _render_wide_results(results: list[OperationResult], target_console: Console) -> None:
    op_table = Table(title="Command Summary", show_lines=False, expand=False)
    op_table.add_column("Command", style="bold")
    op_table.add_column("Status")
    op_table.add_column("Time (s)", justify="right")
    op_table.add_column("Exit", justify="right")
    op_table.add_column("Log")

    for result in results:
        op_table.add_row(
            _cell(result.name),
            _status_text(result),
            Text(f"{result.duration_seconds:.2f}"),
            Text(str(result.returncode)),
            _cell(result.log_path or "-"),
        )

    target_console.print(op_table)


def _render_compact_results(results: list[OperationResult], target_console: Console) -> None:
    for result in results:
        grid = Table.grid(expand=False)
        grid.add_column(style="bold", no_wrap=True)
        grid.add_column(overflow="fold")
        grid.add_row(_cell("command"), _cell(result.name))
        grid.add_row(_cell("status"), _status_text(result))
        grid.add_row(_cell("time"), _cell(f"{result.duration_seconds:.2f}s"))
        grid.add_row(_cell("exit"), _cell(str(result.returncode)))
        if result.failure_kind:
            grid.add_row(_cell("failure"), _cell(result.failure_kind))
        if result.log_path:
            grid.add_row(_cell("log"), _cell(result.log_path))
        target_console.print(Panel(grid, title="Command Summary", expand=False))


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
    return {
        "repo_root": repo_root,
        "warnings": totals["warnings"],
        "errors": totals["errors"],
        "failed": totals["failed"],
        "skipped": totals["skipped"],
        "results": [
            {
                "name": result.name,
                "argv": result.argv,
                "returncode": result.returncode,
                "duration_seconds": result.duration_seconds,
                "log_path": result.log_path,
                "skipped": result.skipped,
                "failure_kind": result.failure_kind,
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


def _manifest_summary_table(manifest: dict) -> Table:
    table = Table(title="Summary", show_header=False, expand=False, box=None)
    table.add_column("Field", style="bold", no_wrap=True)
    table.add_column("Value", overflow="fold")
    scalar_keys = [
        "type",
        "repo_name",
        "repo",
        "target",
        "validate_target",
        "description",
        "expected_solution",
        "solution",
        "project",
    ]
    seen: set[str] = set()
    for key in scalar_keys:
        if key not in manifest or isinstance(manifest.get(key), (dict, list)):
            continue
        table.add_row(_human_json_key(key), Text(_json_scalar(manifest.get(key)), overflow="fold"))
        seen.add(key)
    if not seen:
        table.add_row("Fields", str(len(manifest)))
    return table


def _manifest_list_table(title: str, values: list[object]) -> Table:
    table = Table(title=title, show_lines=False, expand=False)
    table.add_column("#", justify="right", style="dim", no_wrap=True)
    table.add_column("Value", overflow="fold")
    for index, value in enumerate(values, start=1):
        if isinstance(value, (dict, list)):
            table.add_row(str(index), Text(str(value), overflow="fold"))
        else:
            table.add_row(str(index), Text(_json_scalar(value), overflow="fold"))
    return table


def render_artifact_manifest(manifest: dict, *, console_obj: Console | None = None) -> None:
    """Render a devtool artifact manifest as semantic Rich sections instead of raw JSON."""
    if not manifest:
        return
    target_console = console_obj or console
    scalar_summary = _manifest_summary_table(manifest)
    renderables: list[object] = [scalar_summary]

    for key, title in [
        ("compat", "Compatibility"),
        ("files", "Files"),
        ("mutates", "Mutates"),
        ("deletes", "Deletes"),
    ]:
        value = manifest.get(key)
        if isinstance(value, list):
            renderables.append(_manifest_list_table(title, value))

    validation = manifest.get("validation")
    if isinstance(validation, dict):
        validation_summary = Table(title="Validation", show_header=False, expand=False, box=None)
        validation_summary.add_column("Field", style="bold", no_wrap=True)
        validation_summary.add_column("Value", overflow="fold")
        for key, value in validation.items():
            if isinstance(value, list):
                continue
            validation_summary.add_row(_human_json_key(key), Text(_json_scalar(value), overflow="fold"))
        renderables.append(validation_summary)
        commands = validation.get("recommended_commands")
        if isinstance(commands, list):
            renderables.append(_manifest_list_table("Recommended commands", commands))

    known = {
        "type", "repo", "repo_name", "target", "validate_target", "description", "expected_solution", "solution", "project",
        "compat", "files", "mutates", "deletes", "validation",
    }
    extras = {key: value for key, value in manifest.items() if key not in known}
    if extras:
        renderables.append(_tree_from_json("Additional metadata", extras))

    target_console.print(Panel(Group(*renderables), title="Parsed artifact metadata", expand=not is_narrow_console(target_console)))

