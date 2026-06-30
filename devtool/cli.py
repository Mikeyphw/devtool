from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from rich import box
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text

from .ai_prompt import render_ai_prompt, resolve_ai_prompt_options
from .artifacts import (
    append_history,
    format_preview,
    latest_commit_hash,
    load_history,
    make_history_entry,
    preview_artifact,
    read_artifact_manifest,
    sha256_file,
    validate_artifact,
)
from .detect import detect_repo, render_detect_text
from .execution import apply_matrix, wrap_specs_for_chroot
from .handoff import render_handoff
from .watch import watch_loop
from .clipboard import copy_text
from .console_utils import create_console, is_narrow_console, resolve_cli_path
from .completion import ZSH_COMPLETION
from .config import detect_project_kind, discover_dotnet_candidates, load_project_config, resolve_config_dict
from .logstore import latest_log, new_log_path
from .models import CommandSpec, OperationResult, ProjectConfig
from .notify import available_notifier, send_notification
from .runners import apply_overlay, apply_snapshot, build_specs, build_validation_specs, git_commit, run_specs, write_template_config
from .summary import build_json_summary, build_runner_json, render_status_table, render_summary
from .tasker import DEFAULT_NOTIFICATION_ID, DEFAULT_TASKER_ACTION, DEFAULT_TASKER_DIR, TaskerProgressSink, build_tasker_sink

console = create_console()

APPLY_COMMANDS = {"apply-overlay", "ao", "apply-snapshot", "as"}
LEGACY_APPLY_VALIDATE_COMMANDS = APPLY_COMMANDS | {"apply-overlay-validate", "aov", "apply-snapshot-validate", "asv"}

INTERACTIVE_COPY_COMMANDS = {
    "apply-overlay",
    "ao",
    "apply-snapshot",
    "as",
    "batch",
}

_GLOBAL_FLAGS_NO_VALUE = {
    "-v", "--verbose",
    "-j", "--json",
    "-y", "--yes",
    "-f", "--force",
    "--dry-run",
    "-k", "--continue-on-error",
    "--fail-fast",
    "-p", "--copy",
    "--copy-summary", "--copy-json", "--no-clipboard",
    "-n", "--notify",
    "--notify-brief", "--notify-on-success", "--notify-on-fail", "--notify-progress",
    "--tasker", "--tasker-progress", "--tasker-broadcast",
    "--host",
}
_GLOBAL_FLAGS_REQUIRE_VALUE = {
    "-r", "--repo",
    "-c", "--config",
    "--target", "--solution", "--project",
    "--machine-file", "--copy-via", "--notify-id",
    "--tasker-dir", "--tasker-action",
    "--theme", "--chroot", "--stdout-payload",
    "--save-json-payload", "--json-output",
}
_GLOBAL_FLAGS_OPTIONAL_VALUE = {"--machine": {"compact", "pretty"}}


def _normalize_global_flag_placement(argv: list[str] | None) -> list[str]:
    raw = list(sys.argv[1:] if argv is None else argv)
    moved: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(raw):
        token = raw[i]
        option_name = token.split("=", 1)[0] if token.startswith("--") else token

        if option_name in _GLOBAL_FLAGS_NO_VALUE:
            moved.append(token)
            i += 1
            continue

        if option_name in _GLOBAL_FLAGS_REQUIRE_VALUE:
            moved.append(token)
            if "=" not in token and i + 1 < len(raw):
                moved.append(raw[i + 1])
                i += 2
            else:
                i += 1
            continue

        if option_name in _GLOBAL_FLAGS_OPTIONAL_VALUE:
            moved.append(token)
            allowed = _GLOBAL_FLAGS_OPTIONAL_VALUE[option_name]
            if "=" not in token and i + 1 < len(raw) and raw[i + 1] in allowed:
                moved.append(raw[i + 1])
                i += 2
            else:
                i += 1
            continue

        rest.append(token)
        i += 1

    return moved + rest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devtool")
    parser.add_argument("-r", "--repo", default=None, help="Repository root")
    parser.add_argument("-c", "--config", default=None, help="Config file path")
    parser.add_argument("--target", default="", help="Named target/profile from .devtool.toml")
    parser.add_argument("--solution", default="", help="Exact solution path for restore/build/test")
    parser.add_argument("--project", default="", help="Exact project path for run/validation")
    parser.add_argument("-v", "--verbose", action="store_true", help="Stream full command output")
    parser.add_argument("-j", "--json", action="store_true", help="Shortcut for the machine envelope stdout payload")
    parser.add_argument("--machine", nargs="?", const="compact", choices=["compact", "pretty"], default="", help="Emit the clean machine envelope on stdout")
    parser.add_argument("--machine-file", default="", help="Write the machine envelope to this file")
    parser.add_argument("-y", "--yes", action="store_true", help="Assume yes for confirmations")
    parser.add_argument("-f", "--force", action="store_true", help="Force dangerous actions")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running them")
    parser.add_argument("-k", "--continue-on-error", action="store_true", help="Continue batch after errors")
    parser.add_argument("--fail-fast", action="store_true", help="Stop batch on first error")
    parser.add_argument("-p", "--copy", action="store_true", help="Copy raw output to clipboard without prompting")
    parser.add_argument("--copy-summary", action="store_true", help="Copy rendered summary to clipboard without prompting")
    parser.add_argument("--copy-json", action="store_true", help="Copy JSON summary to clipboard without prompting")
    parser.add_argument("--no-clipboard", action="store_true", help="Disable clipboard prompts and copy behavior")
    parser.add_argument("--copy-via", choices=["auto", "socket", "fifo", "termux", "x11", "wayland"], default="auto", help="Prefer a clipboard backend family")
    parser.add_argument("-n", "--notify", action="store_true", help="Send notification when done")
    parser.add_argument("--notify-brief", action="store_true", help="Use shorter notification body")
    parser.add_argument("--notify-on-success", action="store_true", help="Only notify on success")
    parser.add_argument("--notify-on-fail", action="store_true", help="Only notify on failure")
    parser.add_argument("--notify-progress", action="store_true", help="Update a live Termux notification while phases run")
    parser.add_argument("--notify-id", default=DEFAULT_NOTIFICATION_ID, help="Notification id for --notify-progress")
    parser.add_argument("--tasker", action="store_true", help="Enable Tasker JSON, broadcast, and live notification integration")
    parser.add_argument("--tasker-progress", action="store_true", help="Write live Tasker-compatible JSON status files")
    parser.add_argument("--tasker-dir", default=DEFAULT_TASKER_DIR, help="Directory for Tasker JSON files")
    parser.add_argument("--tasker-broadcast", action="store_true", help="Send an Android broadcast after Tasker event files are written")
    parser.add_argument("--tasker-action", default=DEFAULT_TASKER_ACTION, help="Android broadcast action for Tasker events")
    parser.add_argument("--theme", choices=["auto", "plain"], default="auto", help="Summary theme")
    exec_group = parser.add_mutually_exclusive_group()
    exec_group.add_argument("--host", action="store_true", help="Force commands to run on the host")
    exec_group.add_argument("--chroot", default="", help="Run command phases inside a named chroot via chrootctl exec")
    parser.add_argument(
        "--stdout-payload",
        choices=["auto", "summary", "json", "raw"],
        default="auto",
        help="Select the stdout payload; use summary/json/raw, or auto for normal CLI output",
    )
    parser.add_argument(
        "--save-json-payload",
        "--json-output",
        dest="save_json_payload",
        default="",
        help="Write the machine JSON payload to this file path",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, aliases in [
        ("restore", ["rs"]),
        ("build", ["b"]),
        ("test", ["t"]),
        ("validate", ["v"]),
        ("wine-validate", ["wv"]),
        ("run", ["rn"]),
        ("package", ["pkg"]),
        ("status", ["st"]),
        ("doctor", ["dr"]),
    ]:
        cmd = subparsers.add_parser(name, aliases=aliases)
        if name == "doctor":
            cmd.add_argument("--fix", action="store_true", help="Create safe devtool state/cache directories")
        if name in {"validate", "wine-validate"}:
            _add_validation_skip_flags(cmd)
            cmd.add_argument("--watch", action="store_true", help="Rerun validation when files change")
            cmd.add_argument("--watch-interval", type=float, default=1.5, help="Seconds between watch polls")
            cmd.add_argument("--matrix", default="", help="Comma-separated runtime IDs or target names to validate")
            cmd.add_argument("--matrix-mode", choices=["rid", "target"], default="rid")

    ai_prompt = subparsers.add_parser("ai-prompt", aliases=["ap"])
    ai_prompt.add_argument("--repo-name", required=True, help="Human-friendly repo name for the generated prompt")
    ai_prompt.add_argument("--project-type", choices=["auto", "dotnet", "python", "unknown"], default="auto")
    ai_prompt.add_argument("--build-target", default="")
    ai_prompt.add_argument("--run-target", default="")
    ai_prompt.add_argument("--target-name", default="", help="Named monorepo target/profile to reference")
    ai_prompt.add_argument("--solution-target", default="", help="Exact solution target to reference")
    ai_prompt.add_argument("--delivery-mode", choices=["overlay", "snapshot"], default="overlay")
    ai_prompt.add_argument("--artifact-type", choices=["zip", "tar"], default="zip")
    ai_prompt.add_argument("--overlay-count", type=int, default=0)
    ai_prompt.add_argument("--commit-message", default="Clear commit message")
    ai_prompt.add_argument("--wine", action="store_true", help="Prefer wine-validate wording in the generated prompt")
    ai_prompt.add_argument("-o", "--output", default="", help="Write the generated prompt to a file")
    ai_prompt.add_argument("--format", choices=["markdown", "text"], default="markdown")

    commit = subparsers.add_parser("commit", aliases=["ci"])
    commit.add_argument("message")

    for name, aliases in [
        ("apply-overlay", ["ao"]),
        ("apply-snapshot", ["as"]),
    ]:
        cmd = subparsers.add_parser(name, aliases=aliases)
        cmd.add_argument("artifact")
        cmd.add_argument("--validate", action="store_true", help="Run validation after applying the artifact")
        cmd.add_argument("--validate-only", action="store_true", help="Skip apply and only run validation")
        cmd.add_argument("--preview", action="store_true", help="Preview archive changes without applying")
        cmd.add_argument("--interactive", action="store_true", help="Show preview and ask before applying")
        cmd.add_argument("--no-manifest-check", action="store_true", help="Disable devtool artifact manifest validation")
        _add_validation_skip_flags(cmd)
        cmd.add_argument("--commit", default="", help="Commit with this message after apply and optional validation")
        cmd.add_argument(
            "--commit-even-on-failure",
            action="store_true",
            help="Commit after a successful apply even if validation fails",
        )

    batch = subparsers.add_parser("batch")
    batch.add_argument("steps", nargs="+", help="Steps like validate or apply-overlay=/path.zip")

    init_cfg = subparsers.add_parser("init-config")
    init_cfg.add_argument("repo_root", nargs="?", default=".")
    init_cfg.add_argument("--force", action="store_true")

    logs_cmd = subparsers.add_parser("logs")
    logs_cmd.add_argument("which", choices=["latest"], default="latest", nargs="?")
    logs_cmd.add_argument("--print", action="store_true")

    tag_cmd = subparsers.add_parser("tag")
    tag_cmd.add_argument("name")
    tag_cmd.add_argument("-m", "--message", default="")

    rn = subparsers.add_parser("release-notes")
    rn.add_argument("--from-ref", default="")
    rn.add_argument("--to-ref", default="HEAD")

    completion = subparsers.add_parser("completion")
    completion.add_argument("shell", choices=["zsh"])

    config_cmd = subparsers.add_parser("config")
    cfg_sub = config_cmd.add_subparsers(dest="config_command", required=True)
    cfg_sub.add_parser("show")
    cfg_sub.add_parser("defaults")
    cfg_sub.add_parser("resolve")
    cfg_sub.add_parser("doctor")

    detect_cmd = subparsers.add_parser("detect")
    detect_cmd.add_argument("--format", choices=["text", "json"], default="text")

    handoff_cmd = subparsers.add_parser("handoff")
    handoff_cmd.add_argument("-o", "--output", default="")

    history_cmd = subparsers.add_parser("history")
    history_cmd.add_argument("--limit", type=int, default=20)

    rollback_cmd = subparsers.add_parser("rollback")
    rollback_cmd.add_argument("commit", nargs="?", default="")
    rollback_cmd.add_argument("--last", action="store_true")

    subparsers.add_parser("targets", aliases=["tg"])

    return parser


def _add_validation_skip_flags(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--skip-restore", action="store_true", help="Skip restore during validation")
    cmd.add_argument("--skip-build", action="store_true", help="Skip build during validation")
    cmd.add_argument("--skip-test", action="store_true", help="Skip test during validation")



def _write_json_payload(path_text: str, json_payload: str) -> None:
    if not path_text:
        return
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_payload + ("" if json_payload.endswith("\n") else "\n"), encoding="utf-8")
    console.print(f"[green]Wrote JSON payload[/green] {path}")

def _maybe_copy(raw_output: str, summary_text: str, json_text: str, args: argparse.Namespace) -> None:
    if args.no_clipboard:
        return

    to_copy = raw_output
    label = "Raw command output"
    if args.copy_json:
        to_copy = json_text
        label = "JSON summary"
    elif args.copy_summary:
        to_copy = summary_text
        label = "Summary"

    if args.copy or args.copy_summary or args.copy_json:
        result = copy_text(to_copy) if args.copy_via == "auto" else copy_text(to_copy, preferred=args.copy_via)
        if result.success:
            console.print(f"[green]{label} copied to clipboard via {result.backend}.[/green]")
        else:
            reason = result.reason or "Clipboard copy failed."
            console.print(f"[yellow]{reason}[/yellow]")
        return

    if args.command not in INTERACTIVE_COPY_COMMANDS:
        return
    if not console.is_interactive or not sys.stdin.isatty() or not to_copy.strip():
        return

    copy_now = Confirm.ask(f"Copy {label.lower()} to clipboard?", default=True, console=console)
    if copy_now:
        result = copy_text(to_copy) if args.copy_via == "auto" else copy_text(to_copy, preferred=args.copy_via)
        if result.success:
            console.print(f"[green]{label} copied to clipboard via {result.backend}.[/green]")
        else:
            reason = result.reason or "Clipboard copy failed."
            console.print(f"[yellow]{reason}[/yellow]")


def _validate_output_flags(args: argparse.Namespace, parser: argparse.ArgumentParser) -> tuple[str, str]:
    explicit_stdout = args.stdout_payload
    if args.json and explicit_stdout != "auto":
        parser.error("--json cannot be combined with --stdout-payload; use --stdout-payload json instead.")
    if args.machine and explicit_stdout != "auto":
        parser.error("--machine cannot be combined with --stdout-payload; use --machine-file for sidecar JSON.")
    if args.json and args.machine:
        parser.error("Use either --json/-j or --machine, not both.")
    if (args.json or args.machine) and explicit_stdout == "auto":
        explicit_stdout = "json"

    copy_flags = [name for name, enabled in (("raw", args.copy), ("summary", args.copy_summary), ("json", args.copy_json)) if enabled]
    if len(copy_flags) > 1:
        parser.error("Choose only one of --copy, --copy-summary, or --copy-json.")
    if args.no_clipboard and copy_flags:
        parser.error("--no-clipboard cannot be combined with --copy, --copy-summary, or --copy-json.")

    copy_mode = copy_flags[0] if copy_flags else "auto"
    return explicit_stdout, copy_mode


def _notification_style(*, warnings: int, errors: int, failed: int) -> tuple[str, str]:
    if failed or errors:
        return "❌", "FAIL"
    if warnings:
        return "⚠️", "WARN"
    return "✅", "PASS"


def _notification_message(*, warnings: int, errors: int, failed: int, command: str, repo_name: str, duration_seconds: float, brief: bool) -> tuple[str, str]:
    emoji, status = _notification_style(warnings=warnings, errors=errors, failed=failed)
    title = f"{emoji} {status}"
    body = f"{emoji} {status}: {repo_name}: {command}"
    if not brief:
        body += f" in {duration_seconds:.1f}s"
    body += f" • {errors}E {warnings}W"
    return title, body


def _maybe_notify(args: argparse.Namespace, *, warnings: int, errors: int, failed: int, command: str, repo_name: str, duration_seconds: float) -> None:
    if not args.notify:
        return
    succeeded = failed == 0
    if args.notify_on_success and not succeeded:
        return
    if args.notify_on_fail and succeeded:
        return
    title, body = _notification_message(
        warnings=warnings,
        errors=errors,
        failed=failed,
        command=command,
        repo_name=repo_name,
        duration_seconds=duration_seconds,
        brief=args.notify_brief,
    )
    send_notification(title, body)


def _normalize_tasker_args(args: argparse.Namespace) -> None:
    if getattr(args, "tasker", False):
        args.tasker_progress = True
        args.tasker_broadcast = True
        args.notify_progress = True


def _tasker_for_args(args: argparse.Namespace, config: ProjectConfig | None = None, repo: Path | None = None) -> TaskerProgressSink | None:
    repo_root = (config.repo_root if config is not None else (repo or Path(args.repo or Path.cwd()))).resolve()
    target = str(getattr(args, "target", "") or getattr(args, "solution", "") or getattr(args, "project", "") or "")
    return build_tasker_sink(
        enabled=bool(getattr(args, "tasker_progress", False)),
        directory=str(getattr(args, "tasker_dir", DEFAULT_TASKER_DIR)),
        command=str(getattr(args, "command", "devtool") or "devtool"),
        repo_root=repo_root,
        target=target,
        broadcast=bool(getattr(args, "tasker_broadcast", False)),
        broadcast_action=str(getattr(args, "tasker_action", DEFAULT_TASKER_ACTION)),
        notify_progress=bool(getattr(args, "notify_progress", False)),
        notification_id=str(getattr(args, "notify_id", DEFAULT_NOTIFICATION_ID)),
    )


def _notification_payload_from_totals(*, warnings: int, errors: int, failed: int, command: str, repo_name: str, duration_seconds: float, brief: bool) -> dict:
    title, body = _notification_message(
        warnings=warnings,
        errors=errors,
        failed=failed,
        command=command,
        repo_name=repo_name,
        duration_seconds=duration_seconds,
        brief=brief,
    )
    level = "error" if failed or errors else ("warning" if warnings else "success")
    return {"level": level, "title": title, "body": body}


def _finish_tasker(tasker_progress: TaskerProgressSink | None, *, totals: dict[str, int], results: list[OperationResult], command: str, repo_name: str, duration_seconds: float, args: argparse.Namespace) -> None:
    if tasker_progress is None:
        return
    notification = _notification_payload_from_totals(
        warnings=totals["warnings"],
        errors=totals["errors"],
        failed=totals["failed"],
        command=command,
        repo_name=repo_name,
        duration_seconds=duration_seconds,
        brief=getattr(args, "notify_brief", False),
    )
    tasker_progress.finished(ok=totals["failed"] == 0 and totals["errors"] == 0, totals=totals, results=results, notification=notification)


def _summary_text(results: list[OperationResult]) -> str:
    parts: list[str] = []
    for result in results:
        status = "skipped" if result.skipped else ("ok" if result.returncode == 0 else "failed")
        parts.append(f"{result.name}: {status} exit={result.returncode} time={result.duration_seconds:.2f}s")
        for issue in result.issues:
            location = issue.file if issue.line is None or issue.file in {"", "-"} else f"{issue.file}:{issue.line}"
            parts.append(f"  {issue.severity} {issue.command} {location} {issue.code} {issue.message}")
    return "\n".join(parts)


def _json_summary_text(results: list[OperationResult], config: ProjectConfig | None) -> str:
    data = build_json_summary(results, repo_root=str(config.repo_root if config else Path.cwd()))
    return json.dumps(data, indent=2)


def _select_stdout_payload(
    payload: str,
    *,
    summary_text: str,
    json_text: str,
    raw_output: str,
    machine_json_text: str,
) -> str:
    if payload == "summary":
        return summary_text
    if payload == "json":
        return machine_json_text
    if payload == "raw":
        return raw_output
    raise ValueError(f"Unsupported stdout payload: {payload}")


def _emit_summary(
    results: list[OperationResult],
    config: ProjectConfig | None,
    *,
    as_json: bool,
    payload_mode: str = "auto",
    raw_output: str = "",
    command: str = "",
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    machine_format: str = "pretty",
) -> tuple[dict[str, int], str, str, str]:
    repo_root = str(config.repo_root if config else Path.cwd())
    summary_text = _summary_text(results)
    json_data = build_json_summary(results, repo_root=repo_root)
    json_text = json.dumps(json_data, indent=2)
    machine_json = build_runner_json(
        results,
        repo_root=repo_root,
        command=command or "devtool",
        raw_output=raw_output,
        summary_text=summary_text,
        started_at=started_at,
        finished_at=finished_at,
    )
    machine_json_text = json.dumps(machine_json, indent=(2 if machine_format != "compact" else None), separators=(None if machine_format != "compact" else (",", ":")))

    if payload_mode == "auto":
        if as_json:
            print(json_text)
        else:
            render_summary(results)
        return {
            "warnings": json_data["warnings"],
            "errors": json_data["errors"],
            "failed": json_data["failed"],
            "skipped": json_data["skipped"],
        }, summary_text, json_text, machine_json_text

    render_summary(results, console_obj=create_console(stderr=True))
    payload_text = _select_stdout_payload(
        payload_mode,
        summary_text=summary_text,
        json_text=json_text,
        raw_output=raw_output,
        machine_json_text=machine_json_text,
    )
    print(payload_text)
    return {
        "warnings": json_data["warnings"],
        "errors": json_data["errors"],
        "failed": json_data["failed"],
        "skipped": json_data["skipped"],
    }, summary_text, json_text, machine_json_text


def _resolve_batch_steps(config: ProjectConfig, steps: list[str]) -> tuple[list[CommandSpec], list[OperationResult], list[str]]:
    specs: list[CommandSpec] = []
    pre_results: list[OperationResult] = []
    pre_outputs: list[str] = []
    for step in steps:
        if "=" in step:
            name, value = step.split("=", 1)
        else:
            name, value = step, ""
        if name in {"apply-overlay", "ao"}:
            raise RuntimeError("batch apply steps must be executed from main runner")
        specs.extend(build_specs(config, name))
    return specs, pre_results, pre_outputs



def _read_artifact_manifest(artifact: str) -> dict[str, str]:
    artifact_path = resolve_cli_path(artifact)
    sidecar_candidates = [
        artifact_path.with_name(artifact_path.name + ".devtool.json"),
        artifact_path.with_name(artifact_path.name + ".manifest.json"),
    ]
    for candidate in sidecar_candidates:
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items()}
            except Exception:
                return {}

    names = {".devtool-artifact.json", "artifact.devtool.json"}
    try:
        if artifact_path.suffix == ".zip":
            with zipfile.ZipFile(artifact_path) as zf:
                for name in zf.namelist():
                    if Path(name).name in names:
                        data = json.loads(zf.read(name).decode("utf-8"))
                        if isinstance(data, dict):
                            return {str(k): str(v) for k, v in data.items()}
        elif artifact_path.suffix == ".tar" or artifact_path.name.endswith((".tar.gz", ".tgz")):
            mode = "r:gz" if artifact_path.name.endswith((".tar.gz", ".tgz")) else "r"
            with tarfile.open(artifact_path, mode) as tf:
                for member in tf.getmembers():
                    if Path(member.name).name in names and member.isfile():
                        fh = tf.extractfile(member)
                        if fh is None:
                            continue
                        data = json.loads(fh.read().decode("utf-8"))
                        if isinstance(data, dict):
                            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        return {}
    return {}


def _load_command_config(args: argparse.Namespace) -> ProjectConfig:
    target_name = args.target
    solution = args.solution
    project = args.project

    if args.command in LEGACY_APPLY_VALIDATE_COMMANDS and (getattr(args, "validate", False) or getattr(args, "validate_only", False)):
        manifest = _read_artifact_manifest(args.artifact)
        if not target_name:
            target_name = manifest.get("validate_target", "")
        if not solution:
            solution = manifest.get("solution", "")
        if not project:
            project = manifest.get("run_project", "") or manifest.get("project", "")

    return load_project_config(
        args.repo,
        args.config,
        target_name=target_name,
        solution_override=solution,
        project_override=project,
    )


def _validate_apply_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.command not in APPLY_COMMANDS:
        return
    if (args.skip_restore or args.skip_build or args.skip_test) and not (args.validate or args.validate_only):
        parser.error("--skip-restore, --skip-build, and --skip-test require --validate or --validate-only.")
    if args.validate_only and args.validate:
        parser.error("--validate and --validate-only cannot be combined.")
    if args.validate_only and (args.commit or args.commit_even_on_failure):
        parser.error("--validate-only cannot be combined with --commit or --commit-even-on-failure.")
    if getattr(args, "preview", False) and (args.validate or args.validate_only or args.commit or args.commit_even_on_failure):
        parser.error("--preview cannot be combined with validation or commit flags.")


def _validation_skip_values(args: argparse.Namespace) -> tuple[bool, bool, bool]:
    return (
        bool(getattr(args, "skip_restore", False)),
        bool(getattr(args, "skip_build", False)),
        bool(getattr(args, "skip_test", False)),
    )


def _validation_specs(config: ProjectConfig, args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[CommandSpec]:
    skip_restore, skip_build, skip_test = _validation_skip_values(args)
    if skip_restore and skip_build and skip_test:
        parser.error("Validation would run no commands. Clear at least one skip flag.")
    return build_validation_specs(
        config,
        wine=args.command in {"wine-validate", "wv"},
        skip_restore=skip_restore,
        skip_build=skip_build,
        skip_test=skip_test,
    )



def _matrix_items(args: argparse.Namespace) -> list[str]:
    return [item.strip() for item in str(getattr(args, "matrix", "") or "").split(",") if item.strip()]


def _prepare_specs_for_execution(specs: list[CommandSpec], args: argparse.Namespace) -> list[CommandSpec]:
    items = _matrix_items(args)
    if items and getattr(args, "matrix_mode", "rid") == "rid":
        specs = apply_matrix(specs, items, mode="rid")
    if getattr(args, "chroot", "") and not getattr(args, "host", False):
        specs = wrap_specs_for_chroot(specs, args.chroot)
    return specs


def _validation_specs_for_args(config: ProjectConfig, args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[CommandSpec]:
    items = _matrix_items(args)
    if items and getattr(args, "matrix_mode", "rid") == "target":
        specs: list[CommandSpec] = []
        for item in items:
            target_config = load_project_config(
                args.repo,
                args.config,
                target_name=item,
                solution_override=args.solution,
                project_override=args.project,
            )
            target_specs = _validation_specs(target_config, args, parser)
            for spec in target_specs:
                spec.name = f"{spec.name}[{item}]"
                spec.metadata["matrix"] = item
                spec.metadata["matrix_mode"] = "target"
            specs.extend(target_specs)
        return specs
    specs = _validation_specs(config, args, parser)
    items = _matrix_items(args)
    if items and getattr(args, "matrix_mode", "rid") == "rid":
        specs = apply_matrix(specs, items, mode="rid")
    return specs


def _run_specs_for_args(
    config: ProjectConfig,
    specs: list[CommandSpec],
    args: argparse.Namespace,
    *,
    continue_on_error: bool = False,
    log_path: Path | str | None = None,
    tasker_progress: TaskerProgressSink | None = None,
) -> tuple[list[OperationResult], str]:
    return run_specs(
        config,
        _prepare_specs_for_execution(specs, args),
        verbose=args.verbose,
        dry_run=args.dry_run,
        continue_on_error=continue_on_error,
        log_path=log_path,
        tasker_progress=tasker_progress,
    )


def _record_apply_history(config: ProjectConfig, args: argparse.Namespace, results: list[OperationResult]) -> None:
    if args.command not in APPLY_COMMANDS or getattr(args, "validate_only", False) or getattr(args, "preview", False):
        return
    artifact = getattr(args, "artifact", "")
    if not artifact:
        return
    apply_result = results[0] if results else None
    status = "ok" if apply_result and apply_result.returncode == 0 else "failed"
    validation_results = [r for r in results[1:] if r.name not in {"git-add", "git-commit"}]
    if not (getattr(args, "validate", False) or getattr(args, "validate_only", False)):
        validation_status = "not-run"
    elif validation_results and any(r.returncode != 0 for r in validation_results):
        validation_status = "failed"
    else:
        validation_status = "ok"
    commit_hash = ""
    if getattr(args, "commit", "") and any(r.name == "git-commit" and r.returncode == 0 for r in results):
        commit_hash = latest_commit_hash(config.repo_root)
    try:
        entry = make_history_entry(
            config.repo_root,
            artifact,
            artifact_type="snapshot" if "snapshot" in args.command else "overlay",
            status=status,
            validation_status=validation_status,
            commit_message=getattr(args, "commit", "") or "",
            commit_hash=commit_hash,
            preview=getattr(args, "_devtool_artifact_preview", None),
            manifest=getattr(args, "_devtool_artifact_manifest", None),
        )
        if getattr(args, "_devtool_artifact_sha", ""):
            entry["sha256"] = args._devtool_artifact_sha
        append_history(config.repo_root, entry)
    except Exception as exc:
        console.print(f"[yellow]Could not write artifact history: {exc}[/yellow]")


def _print_history(config: ProjectConfig, *, limit: int) -> None:
    entries = load_history(config.repo_root)[-max(1, limit):]
    if not entries:
        console.print("[yellow]No artifact history found.[/yellow]")
        return
    rows = []
    for entry in entries:
        rows.append((entry.get("artifact_name", "-"), f"{entry.get('status', '-')} validation={entry.get('validation_status', '-')} commit={entry.get('commit_hash') or '-'}"))
    render_status_table(rows)


def _rollback(config: ProjectConfig, args: argparse.Namespace) -> int:
    commit = args.commit
    if args.last:
        entries = [e for e in load_history(config.repo_root) if e.get("commit_hash")]
        if not entries:
            raise SystemExit("No history entry with commit_hash is available for rollback.")
        commit = str(entries[-1]["commit_hash"])
    if not commit:
        raise SystemExit("Pass a commit hash or use --last.")
    _ensure_safe_repo(config.repo_root, force=args.force, yes=args.yes, command="rollback")
    specs = [CommandSpec("rollback", ["git", "revert", "--no-edit", commit], destructive=True)]
    results, raw = _run_specs_for_args(config, specs, args, log_path=new_log_path())
    totals, _, _, machine = _emit_summary(results, config, as_json=False, raw_output=raw, command="rollback")
    _write_json_payload(args.save_json_payload or args.machine_file, machine)
    return 0 if totals["failed"] == 0 else 1


def _git_commit_with_optional_log(repo_root: Path, message: str, *, verbose: bool, dry_run: bool, log_path: Path | str | None) -> tuple[list[OperationResult], str]:
    try:
        return git_commit(repo_root, message, verbose=verbose, dry_run=dry_run, log_path=log_path)
    except TypeError as exc:
        if "log_path" not in str(exc):
            raise
        return git_commit(repo_root, message, verbose=verbose, dry_run=dry_run)

def _maybe_commit_after_apply(
    config: ProjectConfig,
    args: argparse.Namespace,
    results: list[OperationResult],
    raw_output: str,
    *,
    log_path: Path | str | None = None,
) -> tuple[list[OperationResult], str]:
    commit_message = str(getattr(args, "commit", "") or "").strip()
    if args.command not in APPLY_COMMANDS or not commit_message:
        return results, raw_output
    if not results or results[0].returncode != 0:
        return results, raw_output

    validation_failed = any(result.returncode != 0 for result in results[1:])
    if validation_failed and not getattr(args, "commit_even_on_failure", False):
        return results, raw_output

    commit_results, commit_raw = _git_commit_with_optional_log(config.repo_root, commit_message, verbose=args.verbose, dry_run=args.dry_run, log_path=log_path)
    results.extend(commit_results)
    if raw_output:
        raw_output = raw_output + "\n\n" + commit_raw
    else:
        raw_output = commit_raw
    return results, raw_output


def _targets(config: ProjectConfig) -> None:
    rows: list[tuple[str, str]] = []
    if config.targets:
        rows = [
            (
                name,
                target.solution or target.build_target or target.restore_target or target.run_project or "-"
            )
            for name, target in sorted(config.targets.items())
        ]
    elif config.kind == "dotnet":
        rows = [(candidate.stem, str(candidate.relative_to(config.repo_root))) for candidate in discover_dotnet_candidates(config.repo_root)]
    if not rows:
        console.print("[yellow]No targets found.[/yellow]")
        return
    render_status_table([("target", name) if path == "-" else (name, path) for name, path in rows])

def _format_manifest_value(value: object) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)
    if value is None:
        return "null"
    return str(value)


def _render_artifact_manifest(manifest: dict) -> None:
    table = Table(
        title="Artifact manifest",
        box=box.SIMPLE if is_narrow_console(console) else box.SIMPLE_HEAVY,
        show_lines=not is_narrow_console(console),
        expand=True,
    )
    table.add_column("Field", style="bold cyan", no_wrap=True, ratio=1)
    table.add_column("Value", overflow="fold", no_wrap=False, ratio=3)
    for key in sorted(manifest):
        table.add_row(Text(str(key), overflow="fold"), Text(_format_manifest_value(manifest[key]), overflow="fold", no_wrap=False))
    console.print(Panel(table, title="Parsed artifact metadata", expand=True))


def _show_and_confirm_artifact_manifest(args: argparse.Namespace, manifest: dict) -> None:
    if not manifest:
        return
    _render_artifact_manifest(manifest)
    if getattr(args, "yes", False) or getattr(args, "force", False):
        return
    if console.is_interactive and sys.stdin.isatty():
        if not Confirm.ask("Continue with this artifact?", default=True, console=console):
            raise SystemExit(1)
        return
    raise SystemExit("Artifact manifest confirmation requires --yes in non-interactive mode.")

def _run_apply(config: ProjectConfig, args: argparse.Namespace, name: str, artifact: str, *, log_path: Path | str | None = None, tasker_progress: TaskerProgressSink | None = None) -> tuple[list[OperationResult], str]:
    snapshot = "snapshot" in name
    artifact_type = "snapshot" if snapshot else "overlay"
    if tasker_progress is not None:
        tasker_progress.phase_started(name, index=1, total=1, argv=[name, artifact])

    def _return_apply(result: OperationResult, text: str) -> tuple[list[OperationResult], str]:
        if tasker_progress is not None:
            tasker_progress.phase_finished(result)
        return [result], text

    if getattr(args, "validate_only", False):
        result = OperationResult(
            "apply-skipped",
            [name, artifact],
            0,
            0.0,
            "Skipped artifact apply because --validate-only was requested.",
            [],
            skipped=True,
        )
        text = "$ " + " ".join(result.argv) + "\n" + result.output
        return _return_apply(result, text)

    started = datetime.now(timezone.utc)
    try:
        artifact_path = resolve_cli_path(artifact)
        args._devtool_artifact_sha = sha256_file(artifact_path) if artifact_path.exists() else ""
        validation = validate_artifact(config.repo_root, artifact_path, artifact_type=artifact_type)
        args._devtool_artifact_manifest = validation.manifest
        preview = preview_artifact(config.repo_root, artifact_path, snapshot=snapshot)
        args._devtool_artifact_preview = preview
    except Exception as exc:
        result = OperationResult(
            "artifact-check",
            [name, artifact],
            1,
            0.0,
            str(exc),
            [],
            failure_kind="artifact_failure",
        )
        return _return_apply(result, "$ " + " ".join(result.argv) + "\n" + result.output)

    # Show manifest metadata before any destructive confirmation such as dirty-tree
    # or snapshot overwrite prompts.  --yes/-y confirms this prompt too.
    if not getattr(args, "preview", False):
        try:
            _show_and_confirm_artifact_manifest(args, validation.manifest)
        except SystemExit as exc:
            result = OperationResult(
                "artifact-cancelled",
                [name, artifact],
                int(exc.code) if isinstance(exc.code, int) else 1,
                0.0,
                "Cancelled before artifact apply.",
                [],
                failure_kind="artifact_failure",
            )
            return _return_apply(result, "$ " + " ".join(result.argv) + "\n" + result.output)

    if not getattr(args, "no_manifest_check", False) and not validation.ok:
        output = "\n".join(validation.messages)
        result = OperationResult(
            "artifact-manifest",
            [name, artifact],
            1,
            0.0,
            output,
            [],
            failure_kind="artifact_failure",
        )
        return _return_apply(result, "$ " + " ".join(result.argv) + "\n" + output)

    try:
        _ensure_safe_repo(config.repo_root, force=args.force, yes=args.yes, command=name)
    except SystemExit as exc:
        result = OperationResult(
            "apply-cancelled",
            [name, artifact],
            int(exc.code) if isinstance(exc.code, int) else 1,
            0.0,
            "Cancelled before artifact apply.",
            [],
            failure_kind="artifact_failure",
        )
        return _return_apply(result, "$ " + " ".join(result.argv) + "\n" + result.output)

    preview_text = format_preview(preview)
    if getattr(args, "preview", False):
        console.print(preview_text)
        result = OperationResult(
            "apply-preview",
            [name, "--preview", artifact],
            0,
            0.0,
            preview_text,
            [],
            skipped=True,
        )
        return _return_apply(result, "$ " + " ".join(result.argv) + "\n" + preview_text)

    if getattr(args, "interactive", False):
        console.print(preview_text)
        if not getattr(args, "yes", False):
            if not Confirm.ask("Apply this artifact?", default=True, console=console):
                result = OperationResult(
                    "apply-cancelled",
                    [name, artifact],
                    1,
                    0.0,
                    "Cancelled by user.",
                    [],
                    failure_kind="artifact_failure",
                )
                return _return_apply(result, "$ " + " ".join(result.argv) + "\n" + result.output)

    if name.startswith("apply-overlay") or name == "ao":
        result = apply_overlay(config.repo_root, artifact, force=args.force, yes=args.yes, dry_run=args.dry_run, log_path=log_path)
    else:
        result = apply_snapshot(config.repo_root, artifact, force=args.force, yes=args.yes, dry_run=args.dry_run, log_path=log_path)
    if result.returncode != 0 and not result.failure_kind:
        result.failure_kind = "artifact_failure"
    manifest_note = "" if getattr(args, "no_manifest_check", False) else "\n" + "\n".join(validation.messages)
    text = "$ " + " ".join(result.argv) + "\n" + result.output + manifest_note
    result.output = result.output + manifest_note
    return _return_apply(result, text)

def _handle_batch(config: ProjectConfig, args: argparse.Namespace, *, log_path: Path | str | None = None, tasker_progress: TaskerProgressSink | None = None) -> tuple[list[OperationResult], str]:
    results: list[OperationResult] = []
    raw: list[str] = []
    continue_on_error = args.continue_on_error and not args.fail_fast

    for step in args.steps:
        if "=" in step:
            name, value = step.split("=", 1)
        else:
            name, value = step, ""
        if name in LEGACY_APPLY_VALIDATE_COMMANDS:
            raise RuntimeError("batch apply validation modes were removed; use apply-overlay/apply-snapshot flags instead")
        if name in APPLY_COMMANDS:
            batch_results, raw_text = _run_apply(config, args, name, value, log_path=log_path, tasker_progress=tasker_progress)
            results.extend(batch_results)
            raw.append(raw_text)
            if batch_results[-1].returncode != 0 and not continue_on_error:
                break
            if getattr(args, "validate", False) or getattr(args, "validate_only", False):
                more, text = _run_specs_for_args(config, build_specs(config, "validate"), args, continue_on_error=continue_on_error, log_path=log_path, tasker_progress=tasker_progress)
                results.extend(more)
                raw.append(text)
                if any(r.returncode != 0 for r in more) and not continue_on_error:
                    break
            continue
        if name in {"commit", "ci"}:
            more, text = _git_commit_with_optional_log(config.repo_root, value, verbose=args.verbose, dry_run=args.dry_run, log_path=log_path)
            results.extend(more)
            raw.append(text)
            if any(r.returncode != 0 for r in more) and not continue_on_error:
                break
            continue
        if name == "tag":
            tag_spec = CommandSpec("tag", ["git", "tag", value], destructive=True)
            more, text = _run_specs_for_args(config, [tag_spec], args, continue_on_error=continue_on_error, log_path=log_path, tasker_progress=tasker_progress)
            results.extend(more)
            raw.append(text)
            if any(r.returncode != 0 for r in more) and not continue_on_error:
                break
            continue
        if name == "release-notes":
            text = _release_notes(config.repo_root, "", "HEAD")
            path = latest_log()
            results.append(OperationResult("release-notes", ["git", "log"], 0, 0.0, text, [], log_path=str(path or "")))
            raw.append(text)
            continue
        specs = build_specs(config, name)
        more, text = _run_specs_for_args(config, specs, args, continue_on_error=continue_on_error, log_path=log_path, tasker_progress=tasker_progress)
        results.extend(more)
        raw.append(text)
        if any(r.returncode != 0 for r in more) and not continue_on_error:
            break
    return results, "\n\n".join(raw)


def _git_dirty(repo_root: Path) -> bool:
    proc = subprocess.run(["git", "status", "--porcelain"], cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc.returncode == 0 and bool(proc.stdout.strip())


def _ensure_safe_repo(repo_root: Path, *, force: bool, yes: bool, command: str) -> None:
    if repo_root == Path("/"):
        raise SystemExit("Refusing to operate on '/'")
    if not (repo_root / ".git").exists() and not force:
        raise SystemExit("Repo root does not look like a git repository. Use --force to override.")
    if "snapshot" in command and not (yes or force):
        if console.is_interactive and sys.stdin.isatty():
            okay = Confirm.ask("Applying a snapshot may overwrite/delete files. Continue?", default=False, console=console)
            if not okay:
                raise SystemExit(1)
        else:
            raise SystemExit("Snapshot apply needs --yes or --force in non-interactive mode.")
    if command.startswith("apply") and _git_dirty(repo_root) and not (yes or force):
        if console.is_interactive and sys.stdin.isatty():
            okay = Confirm.ask("Git tree is dirty. Continue?", default=False, console=console)
            if not okay:
                raise SystemExit(1)
        else:
            raise SystemExit("Git tree is dirty. Use --yes or --force to continue.")


def _doctor(config: ProjectConfig, *, fix: bool = False) -> None:
    fixed: list[str] = []
    if fix:
        from .logstore import logs_dir, state_dir
        state_dir().mkdir(parents=True, exist_ok=True)
        logs_dir().mkdir(parents=True, exist_ok=True)
        fixed.append("state/log directories")
        if os.environ.get("TERMUX_VERSION"):
            try:
                from .runners import _termux_proot_etc_dir
                _termux_proot_etc_dir()
                fixed.append("Termux proot os-release cache")
            except Exception:
                pass
    rows = [
        ("repo", str(config.repo_root)),
        ("kind", config.kind),
        ("git", "yes" if (config.repo_root / ".git").exists() else "no"),
        ("config", str(config.config_path) if config.config_path else "-"),
        ("notifier", available_notifier() or "-"),
        ("wl-copy", "yes" if shutil.which("wl-copy") else "no"),
        ("fzf", "yes" if shutil.which("fzf") else "no"),
        ("dotnet", "yes" if shutil.which("dotnet") else "no"),
        ("python", config.python_executable),
        ("selected_target", config.selected_target or "-"),
        ("default_target", config.default_target or "-"),
        ("restore_target", config.restore_target or "-"),
        ("build_target", config.build_target or "-"),
        ("test_target", config.test_target or "-"),
        ("run_target", config.run_target or "-"),
        ("wine", config.env.get("SIMSTOOLKIT_WINE_PATH", os.environ.get("SIMSTOOLKIT_WINE_PATH", "")) or "-"),
        ("fixed", ", ".join(fixed) if fixed else "-"),
    ]
    render_status_table(rows)


def _status(config: ProjectConfig) -> None:
    rows = [
        ("repo", str(config.repo_root)),
        ("kind", config.kind),
        ("selected_target", config.selected_target or "-"),
        ("default_target", config.default_target or "-"),
        ("restore_target", config.restore_target or "-"),
        ("build_target", config.build_target or "-"),
        ("test_target", config.test_target or "-"),
        ("run_target", config.run_target or "-"),
        ("git_dirty", "yes" if _git_dirty(config.repo_root) else "no"),
    ]
    render_status_table(rows)


def _release_notes(repo_root: Path, from_ref: str, to_ref: str) -> str:
    range_expr = f"{from_ref}..{to_ref}" if from_ref else to_ref
    proc = subprocess.run(["git", "log", "--oneline", "--decorate=no", range_expr], cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc.stdout


def _config_command(args: argparse.Namespace) -> int:
    config = _load_command_config(args)
    if args.config_command == "show":
        print(config.config_path.read_text(encoding="utf-8") if config.config_path else "")
        return 0
    if args.config_command == "defaults":
        from .config import generate_template

        print(generate_template(config.repo_root))
        return 0
    if args.config_command == "resolve":
        print(json.dumps(resolve_config_dict(config), indent=2))
        return 0
    if args.config_command == "doctor":
        rows = [
            ("config_path", str(config.config_path) if config.config_path else "-"),
            ("repo_root", str(config.repo_root)),
            ("kind", config.kind),
            ("build_target", config.build_target or "-"),
            ("run_target", config.run_target or "-"),
        ]
        render_status_table(rows)
        return 0
    return 1


def _main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(_normalize_global_flag_placement(argv))
    _normalize_tasker_args(args)
    _validate_apply_args(args, parser)
    effective_payload, copy_mode = _validate_output_flags(args, parser)

    if args.command in {"ai-prompt", "ap"}:
        if copy_mode in {"summary", "json"}:
            parser.error("ai-prompt only supports raw clipboard copy; use --copy or omit clipboard flags.")
        config = _load_command_config(args)
        options = resolve_ai_prompt_options(
            config,
            args.repo,
            repo_name=args.repo_name,
            project_type=args.project_type,
            build_target=args.build_target,
            run_target=args.run_target,
            target_name=args.target_name,
            solution_target=args.solution_target,
            delivery_mode=args.delivery_mode,
            artifact_type=args.artifact_type,
            overlay_count=args.overlay_count,
            commit_message=args.commit_message,
            notify=args.notify,
            notify_brief=args.notify_brief,
            copy=args.copy,
            no_clipboard=args.no_clipboard,
            prefer_wine_validation=args.wine,
        )
        prompt_text = render_ai_prompt(options, args.format)
        if args.output:
            output_path = Path(args.output).expanduser()
            if not output_path.is_absolute():
                output_path = Path.cwd() / output_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(prompt_text, encoding="utf-8")
            console.print(f"[green]Wrote[/green] {output_path}")
        else:
            print(prompt_text, end="" if prompt_text.endswith("\n") else "\n")
        if copy_mode == "raw":
            result = copy_text(prompt_text)
            if result.success:
                console.print(f"[green]AI prompt copied to clipboard via {result.backend}.[/green]")
            else:
                reason = result.reason or "Clipboard copy failed."
                console.print(f"[yellow]{reason}[/yellow]")
        return 0

    if args.command in {"completion"}:
        if args.shell == "zsh":
            print(ZSH_COMPLETION)
            return 0

    if args.command in {"logs"}:
        path = latest_log()
        if not path:
            console.print("[yellow]No logs found.[/yellow]")
            return 0
        if args.print:
            print(path.read_text(encoding="utf-8"))
        else:
            console.print(str(path))
        return 0

    if args.command == "init-config":
        target = write_template_config(Path(args.repo_root), force=args.force)
        console.print(f"[green]Wrote[/green] {target}")
        return 0

    if args.command == "config":
        return _config_command(args)
    if args.command in {"targets", "tg"}:
        config = _load_command_config(args)
        _targets(config)
        return 0


    if args.command == "detect":
        repo = Path(args.repo or Path.cwd()).resolve()
        info = detect_repo(repo)
        if args.format == "json" or effective_payload == "json":
            print(json.dumps(info, indent=(2 if args.machine != "compact" else None), separators=(None if args.machine != "compact" else (",", ":"))))
        else:
            print(render_detect_text(info))
        return 0

    if args.command == "handoff":
        config = _load_command_config(args)
        text = render_handoff(config)
        if args.output:
            out = Path(args.output).expanduser()
            if not out.is_absolute():
                out = Path.cwd() / out
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            console.print(f"[green]Wrote[/green] {out}")
        else:
            print(text)
        return 0

    if args.command == "history":
        config = _load_command_config(args)
        if effective_payload == "json":
            print(json.dumps(load_history(config.repo_root)[-max(1, args.limit):], indent=(2 if args.machine != "compact" else None), separators=(None if args.machine != "compact" else (",", ":"))))
        else:
            _print_history(config, limit=args.limit)
        return 0

    if args.command == "rollback":
        config = _load_command_config(args)
        return _rollback(config, args)

    if args.command in {"doctor", "dr", "status", "st", "release-notes", "tag"} or args.command in {"commit", "ci"}:
        repo = Path(args.repo or Path.cwd()).resolve()
        if args.command in {"commit", "ci"}:
            _ensure_safe_repo(repo, force=args.force, yes=args.yes, command="commit")
            log_path = new_log_path()
            started_at = datetime.now(timezone.utc)
            results, raw_output = _git_commit_with_optional_log(repo, args.message, verbose=args.verbose, dry_run=args.dry_run, log_path=log_path)
            finished_at = datetime.now(timezone.utc)
            totals, summary_text, json_text, _machine_json_text = _emit_summary(
                results,
                None,
                as_json=effective_payload == "json",
                payload_mode=effective_payload,
                raw_output=raw_output,
                command=args.command,
                started_at=started_at,
                finished_at=finished_at,
                machine_format=args.machine or "pretty",
            )
            failed = totals["failed"]
            _write_json_payload(args.save_json_payload or args.machine_file, _machine_json_text)
            _maybe_copy(raw_output, summary_text, json_text, args)
            _maybe_notify(args, warnings=totals["warnings"], errors=totals["errors"], failed=failed, command=args.command, repo_name=repo.name, duration_seconds=sum(r.duration_seconds for r in results))
            return 0 if failed == 0 else 1
        config = load_project_config(str(repo), args.config)
        if args.command in {"doctor", "dr"}:
            _doctor(config, fix=getattr(args, "fix", False))
            return 0
        if args.command in {"status", "st"}:
            _status(config)
            return 0
        if args.command == "release-notes":
            print(_release_notes(config.repo_root, args.from_ref, args.to_ref))
            return 0
        if args.command == "tag":
            _ensure_safe_repo(repo, force=args.force, yes=args.yes, command="tag")
            specs = [CommandSpec("tag", ["git", "tag"] + (["-m", args.message] if args.message else []) + [args.name], destructive=True)]
            log_path = new_log_path()
            started_at = datetime.now(timezone.utc)
            results, raw_output = _run_specs_for_args(config, specs, args, continue_on_error=False, log_path=log_path)
            finished_at = datetime.now(timezone.utc)
            totals, summary_text, json_text, _machine_json_text = _emit_summary(
                results,
                config,
                as_json=effective_payload == "json",
                payload_mode=effective_payload,
                raw_output=raw_output,
                command=args.command,
                started_at=started_at,
                finished_at=finished_at,
                machine_format=args.machine or "pretty",
            )
            _write_json_payload(args.save_json_payload or args.machine_file, _machine_json_text)
            _maybe_copy(raw_output, summary_text, json_text, args)
            _maybe_notify(args, warnings=totals["warnings"], errors=totals["errors"], failed=totals["failed"], command=args.command, repo_name=repo.name, duration_seconds=sum(r.duration_seconds for r in results))
            return 0 if totals["failed"] == 0 else 1

    config = _load_command_config(args)
    continue_on_error = args.continue_on_error and not args.fail_fast
    log_path = new_log_path()
    tasker_progress = _tasker_for_args(args, config)

    started_at = datetime.now(timezone.utc)
    if tasker_progress is not None:
        tasker_progress.started()

    if args.command == "batch":
        destructive_batch = any(step.split("=", 1)[0] in {"apply-overlay", "ao", "apply-snapshot", "as", "commit", "ci", "tag"} for step in args.steps)
        if destructive_batch:
            _ensure_safe_repo(config.repo_root, force=args.force, yes=args.yes, command=args.command)

    if args.command in APPLY_COMMANDS:
        results, raw_output = _run_apply(config, args, args.command, args.artifact, log_path=log_path, tasker_progress=tasker_progress)
        should_validate = getattr(args, "validate", False) or getattr(args, "validate_only", False)
        if should_validate and (getattr(args, "validate_only", False) or results[-1].returncode == 0):
            more_results, more_raw = _run_specs_for_args(
                config,
                _validation_specs_for_args(config, args, parser),
                args,
                continue_on_error=continue_on_error,
                log_path=log_path,
                tasker_progress=tasker_progress,
            )
            results.extend(more_results)
            raw_output += "\n\n" + more_raw
        results, raw_output = _maybe_commit_after_apply(config, args, results, raw_output, log_path=log_path)
        _record_apply_history(config, args, results)
    elif args.command in {"validate", "v", "wine-validate", "wv"}:
        if getattr(args, "watch", False):
            def _run_once() -> int:
                run_log_path = new_log_path()
                run_started = datetime.now(timezone.utc)
                run_specs_list = _validation_specs_for_args(config, args, parser)
                run_results, run_raw = _run_specs_for_args(config, run_specs_list, args, continue_on_error=continue_on_error, log_path=run_log_path, tasker_progress=tasker_progress)
                run_finished = datetime.now(timezone.utc)
                run_totals, run_summary, run_json, run_machine = _emit_summary(
                    run_results,
                    config,
                    as_json=effective_payload == "json",
                    payload_mode=effective_payload,
                    raw_output=run_raw,
                    command=args.command,
                    started_at=run_started,
                    finished_at=run_finished,
                    machine_format=args.machine or "pretty",
                )
                _write_json_payload(args.save_json_payload or args.machine_file, run_machine)
                _maybe_copy(run_raw, run_summary, run_json, args)
                return 0 if run_totals["failed"] == 0 else 1
            return watch_loop(config.repo_root, _run_once, interval=args.watch_interval)
        specs = _validation_specs_for_args(config, args, parser)
        results, raw_output = _run_specs_for_args(config, specs, args, continue_on_error=continue_on_error, log_path=log_path, tasker_progress=tasker_progress)
    elif args.command == "batch":
        results, raw_output = _handle_batch(config, args, log_path=log_path, tasker_progress=tasker_progress)
    else:
        specs = build_specs(config, args.command)
        results, raw_output = _run_specs_for_args(config, specs, args, continue_on_error=continue_on_error, log_path=log_path, tasker_progress=tasker_progress)

    finished_at = datetime.now(timezone.utc)
    totals, summary_text, json_text, _machine_json_text = _emit_summary(
        results,
        config,
        as_json=effective_payload == "json",
        payload_mode=effective_payload,
        raw_output=raw_output,
        command=args.command,
        started_at=started_at,
        finished_at=finished_at,
        machine_format=args.machine or "pretty",
    )
    duration_seconds = sum(r.duration_seconds for r in results)
    _write_json_payload(args.save_json_payload or args.machine_file, _machine_json_text)
    _maybe_copy(raw_output, summary_text, json_text, args)
    _finish_tasker(tasker_progress, totals=totals, results=results, command=args.command, repo_name=config.repo_root.name, duration_seconds=duration_seconds, args=args)
    _maybe_notify(args, warnings=totals["warnings"], errors=totals["errors"], failed=totals["failed"], command=args.command, repo_name=config.repo_root.name, duration_seconds=duration_seconds)
    return 0 if totals["failed"] == 0 else 1


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        return 130
