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

from rich.prompt import Confirm
from rich.syntax import Syntax

from .ai_prompt import render_ai_prompt, resolve_ai_prompt_options
from .clipboard import copy_text
from .console_utils import create_console, resolve_cli_path, syntax_line_numbers
from .completion import ZSH_COMPLETION
from .config import detect_project_kind, discover_dotnet_candidates, load_project_config, resolve_config_dict
from .logstore import latest_log
from .models import CommandSpec, OperationResult, ProjectConfig
from .notify import available_notifier, send_notification
from .runners import apply_overlay, apply_snapshot, build_specs, build_validation_specs, git_commit, run_specs, write_template_config
from .summary import build_json_summary, build_runner_json, render_status_table, render_summary

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



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devtool")
    parser.add_argument("-r", "--repo", default=None, help="Repository root")
    parser.add_argument("-c", "--config", default=None, help="Config file path")
    parser.add_argument("--target", default="", help="Named target/profile from .devtool.toml")
    parser.add_argument("--solution", default="", help="Exact solution path for restore/build/test")
    parser.add_argument("--project", default="", help="Exact project path for run/validation")
    parser.add_argument("-v", "--verbose", action="store_true", help="Stream full command output")
    parser.add_argument("-j", "--json", action="store_true", help="Shortcut for the machine envelope stdout payload")
    parser.add_argument("-y", "--yes", action="store_true", help="Assume yes for confirmations")
    parser.add_argument("-f", "--force", action="store_true", help="Force dangerous actions")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running them")
    parser.add_argument("-k", "--continue-on-error", action="store_true", help="Continue batch after errors")
    parser.add_argument("--fail-fast", action="store_true", help="Stop batch on first error")
    parser.add_argument("-p", "--copy", action="store_true", help="Copy raw output to clipboard without prompting")
    parser.add_argument("--copy-summary", action="store_true", help="Copy rendered summary to clipboard without prompting")
    parser.add_argument("--copy-json", action="store_true", help="Copy JSON summary to clipboard without prompting")
    parser.add_argument("--no-clipboard", action="store_true", help="Disable clipboard prompts and copy behavior")
    parser.add_argument("-n", "--notify", action="store_true", help="Send notification when done")
    parser.add_argument("--notify-brief", action="store_true", help="Use shorter notification body")
    parser.add_argument("--notify-on-success", action="store_true", help="Only notify on success")
    parser.add_argument("--notify-on-fail", action="store_true", help="Only notify on failure")
    parser.add_argument("--theme", choices=["auto", "plain"], default="auto", help="Summary theme")
    parser.add_argument(
        "--stdout-payload",
        choices=["auto", "summary", "json", "raw"],
        default="auto",
        help="Select the stdout payload; use summary/json/raw, or auto for normal CLI output",
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
        if name in {"validate", "wine-validate"}:
            _add_validation_skip_flags(cmd)

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

    subparsers.add_parser("targets", aliases=["tg"])

    return parser


def _add_validation_skip_flags(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--skip-restore", action="store_true", help="Skip restore during validation")
    cmd.add_argument("--skip-build", action="store_true", help="Skip build during validation")
    cmd.add_argument("--skip-test", action="store_true", help="Skip test during validation")


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
        result = copy_text(to_copy)
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
        result = copy_text(to_copy)
        if result.success:
            console.print(f"[green]{label} copied to clipboard via {result.backend}.[/green]")
        else:
            reason = result.reason or "Clipboard copy failed."
            console.print(f"[yellow]{reason}[/yellow]")


def _validate_output_flags(args: argparse.Namespace, parser: argparse.ArgumentParser) -> tuple[str, str]:
    explicit_stdout = args.stdout_payload
    if args.json and explicit_stdout != "auto":
        parser.error("--json cannot be combined with --stdout-payload; use --stdout-payload json instead.")
    if args.json and explicit_stdout == "auto":
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
    machine_json_text = json.dumps(machine_json, indent=2)

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


def _maybe_commit_after_apply(
    config: ProjectConfig,
    args: argparse.Namespace,
    results: list[OperationResult],
    raw_output: str,
) -> tuple[list[OperationResult], str]:
    commit_message = str(getattr(args, "commit", "") or "").strip()
    if args.command not in APPLY_COMMANDS or not commit_message:
        return results, raw_output
    if not results or results[0].returncode != 0:
        return results, raw_output

    validation_failed = any(result.returncode != 0 for result in results[1:])
    if validation_failed and not getattr(args, "commit_even_on_failure", False):
        return results, raw_output

    commit_results, commit_raw = git_commit(config.repo_root, commit_message, verbose=args.verbose, dry_run=args.dry_run)
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

def _run_apply(config: ProjectConfig, args: argparse.Namespace, name: str, artifact: str) -> tuple[list[OperationResult], str]:
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
        return [result], text

    if name.startswith("apply-overlay"):
        result = apply_overlay(config.repo_root, artifact, force=args.force, yes=args.yes, dry_run=args.dry_run)
    else:
        result = apply_snapshot(config.repo_root, artifact, force=args.force, yes=args.yes, dry_run=args.dry_run)
    text = "$ " + " ".join(result.argv) + "\n" + result.output
    return [result], text


def _handle_batch(config: ProjectConfig, args: argparse.Namespace) -> tuple[list[OperationResult], str]:
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
            batch_results, raw_text = _run_apply(config, args, name, value)
            results.extend(batch_results)
            raw.append(raw_text)
            if batch_results[-1].returncode != 0 and not continue_on_error:
                break
            if getattr(args, "validate", False) or getattr(args, "validate_only", False):
                more, text = run_specs(config, build_specs(config, "validate"), verbose=args.verbose, dry_run=args.dry_run, continue_on_error=continue_on_error)
                results.extend(more)
                raw.append(text)
                if any(r.returncode != 0 for r in more) and not continue_on_error:
                    break
            continue
        if name in {"commit", "ci"}:
            more, text = git_commit(config.repo_root, value, verbose=args.verbose, dry_run=args.dry_run)
            results.extend(more)
            raw.append(text)
            if any(r.returncode != 0 for r in more) and not continue_on_error:
                break
            continue
        if name == "tag":
            tag_spec = CommandSpec("tag", ["git", "tag", value], destructive=True)
            more, text = run_specs(config, [tag_spec], verbose=args.verbose, dry_run=args.dry_run, continue_on_error=continue_on_error)
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
        more, text = run_specs(config, specs, verbose=args.verbose, dry_run=args.dry_run, continue_on_error=continue_on_error)
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


def _doctor(config: ProjectConfig) -> None:
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
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

    if args.command in {"doctor", "dr", "status", "st", "release-notes", "tag"} or args.command in {"commit", "ci"}:
        repo = Path(args.repo or Path.cwd()).resolve()
        if args.command in {"commit", "ci"}:
            _ensure_safe_repo(repo, force=args.force, yes=args.yes, command="commit")
            started_at = datetime.now(timezone.utc)
            results, raw_output = git_commit(repo, args.message, verbose=args.verbose, dry_run=args.dry_run)
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
            )
            failed = totals["failed"]
            _maybe_copy(raw_output, summary_text, json_text, args)
            _maybe_notify(args, warnings=totals["warnings"], errors=totals["errors"], failed=failed, command=args.command, repo_name=repo.name, duration_seconds=sum(r.duration_seconds for r in results))
            return 0 if failed == 0 else 1
        config = load_project_config(str(repo), args.config)
        if args.command in {"doctor", "dr"}:
            _doctor(config)
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
            started_at = datetime.now(timezone.utc)
            results, raw_output = run_specs(config, specs, verbose=args.verbose, dry_run=args.dry_run, continue_on_error=False)
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
            )
            _maybe_copy(raw_output, summary_text, json_text, args)
            _maybe_notify(args, warnings=totals["warnings"], errors=totals["errors"], failed=totals["failed"], command=args.command, repo_name=repo.name, duration_seconds=sum(r.duration_seconds for r in results))
            return 0 if totals["failed"] == 0 else 1

    config = _load_command_config(args)
    continue_on_error = args.continue_on_error and not args.fail_fast

    started_at = datetime.now(timezone.utc)

    if args.command in APPLY_COMMANDS and not args.validate_only:
        _ensure_safe_repo(config.repo_root, force=args.force, yes=args.yes, command=args.command)
    if args.command == "batch":
        destructive_batch = any(step.split("=", 1)[0] in {"apply-overlay", "ao", "apply-snapshot", "as", "commit", "ci", "tag"} for step in args.steps)
        if destructive_batch:
            _ensure_safe_repo(config.repo_root, force=args.force, yes=args.yes, command=args.command)

    if args.command in APPLY_COMMANDS:
        results, raw_output = _run_apply(config, args, args.command, args.artifact)
        should_validate = getattr(args, "validate", False) or getattr(args, "validate_only", False)
        if should_validate and (getattr(args, "validate_only", False) or results[-1].returncode == 0):
            more_results, more_raw = run_specs(
                config,
                _validation_specs(config, args, parser),
                verbose=args.verbose,
                dry_run=args.dry_run,
                continue_on_error=continue_on_error,
            )
            results.extend(more_results)
            raw_output += "\n\n" + more_raw
        results, raw_output = _maybe_commit_after_apply(config, args, results, raw_output)
    elif args.command in {"validate", "v", "wine-validate", "wv"}:
        specs = _validation_specs(config, args, parser)
        results, raw_output = run_specs(config, specs, verbose=args.verbose, dry_run=args.dry_run, continue_on_error=continue_on_error)
    elif args.command == "batch":
        results, raw_output = _handle_batch(config, args)
    else:
        specs = build_specs(config, args.command)
        results, raw_output = run_specs(config, specs, verbose=args.verbose, dry_run=args.dry_run, continue_on_error=continue_on_error)

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
    )
    _maybe_copy(raw_output, summary_text, json_text, args)
    _maybe_notify(args, warnings=totals["warnings"], errors=totals["errors"], failed=totals["failed"], command=args.command, repo_name=config.repo_root.name, duration_seconds=sum(r.duration_seconds for r in results))
    return 0 if totals["failed"] == 0 else 1
