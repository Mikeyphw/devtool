from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from rich.prompt import Confirm

from .ai_prompt import render_ai_prompt, resolve_ai_prompt_options
from .artifacts import (
    SUPPORTED_ARCHIVE_SUFFIXES,
    append_history,
    artifact_cleanup_paths,
    create_overlay_bundle,
    diff_artifact,
    format_preview,
    is_overlay_bundle,
    overlay_bundle_targets,
    latest_commit_hash,
    load_history,
    make_history_entry,
    preview_artifact,
    read_artifact_manifest,
    resolve_artifact_path,
    sha256_file,
    validate_artifact,
    validate_overlay_bundle,
)
from .detect import detect_repo, render_detect_text
from .execution import apply_matrix, wrap_specs_for_chroot
from .handoff import render_handoff
from .watch import watch_loop
from .clipboard import copy_text
from .console_utils import create_console, resolve_cli_path
from .completion import ZSH_COMPLETION
from .config import detect_project_kind, discover_dotnet_candidates, load_project_config, resolve_config_dict
from .logstore import latest_log, new_log_path
from .models import CommandSpec, Issue, OperationResult, ProjectConfig
from .notify import available_notifier, send_notification
from .runners import apply_overlay, apply_overlay_bundle, apply_snapshot, add_no_build_to_dotnet_test_specs, build_specs, build_validation_specs, git_commit, run_specs, write_template_config
from .summary import build_json_summary, build_runner_json, render_artifact_manifest, render_status_table, render_summary
from .tasker import DEFAULT_NOTIFICATION_ID, DEFAULT_TASKER_ACTION, DEFAULT_TASKER_DIR, TaskerProgressSink, build_tasker_sink, start_auto_hud
from .transactions import devtool_xdg_dir, find_active_transaction, mark_transaction_completed, rollback_transaction, transaction_root

console = create_console()

APPLY_COMMANDS = {"apply-overlay", "ao", "apply-overlays", "aos", "apply-snapshot", "as"}
LEGACY_APPLY_VALIDATE_COMMANDS = APPLY_COMMANDS | {"apply-overlay-validate", "aov", "apply-snapshot-validate", "asv"}

INTERACTIVE_COPY_COMMANDS = {
    "apply-overlay",
    "ao",
    "apply-overlays",
    "aos",
    "apply-snapshot",
    "as",
    "batch",
}


PROGRESS_CAPABLE_COMMANDS = {
    "restore", "rs", "build", "b", "test", "t", "validate", "v",
    "wine-validate", "wv", "run", "rn", "package", "pkg",
    "apply-overlay", "ao", "apply-overlays", "aos", "apply-snapshot", "as", "batch", "commit", "ci", "tag",
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
    parser.add_argument("--machine", nargs="?", const="compact", choices=["compact", "pretty"], default="", help="Emit the clean machine envelope on stdout")
    parser.add_argument("--machine-file", default="", help="Write the machine envelope to this file")
    parser.add_argument("-y", "--yes", action="store_true", help="Assume yes for confirmations")
    parser.add_argument("-f", "--force", action="store_true", help="Force dangerous actions")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running them")
    parser.add_argument("--no-build", action="store_true", default=argparse.SUPPRESS, help="For test: pass --no-build to dotnet test")
    parser.add_argument("-k", "--continue-on-error", action="store_true", help="Continue batch after errors")
    parser.add_argument("--fail-fast", action="store_true", help="Stop batch on first error")
    parser.add_argument("-p", "--copy", action="store_true", help="Copy a concise summary on success, or verbose output on failure")
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
    parser.add_argument("--tasker-progress", "--progress", dest="tasker_progress", action="store_true", default=None, help="Write live HUD/Tasker-compatible JSON progress files (default for build/test/validate/apply commands)")
    parser.add_argument("--no-tasker-progress", "--no-progress", dest="tasker_progress", action="store_false", help="Disable live HUD/Tasker progress files")
    parser.add_argument("--tasker-dir", default=DEFAULT_TASKER_DIR, help="Directory for HUD/Tasker JSON files (default: platform state dir, usually ~/.local/state/devtool on Linux)")
    parser.add_argument("--tasker-broadcast", action="store_true", help="Send an Android broadcast after Tasker event files are written")
    parser.add_argument("--tasker-action", default=DEFAULT_TASKER_ACTION, help="Android broadcast action for Tasker events")
    parser.add_argument("--auto-hud", "--hud-auto", dest="auto_hud", action="store_true", default=None, help="Auto-start/reuse the HUD when progress is emitted (default)")
    parser.add_argument("--no-hud", dest="auto_hud", action="store_false", help="Do not auto-start the HUD for this command")
    parser.add_argument("--hud-mode", default="desktop", help="HUD mode to auto-start, e.g. desktop, android-gui, android-bridge, android-notify, desktop-window, desktop-notify, v5, terminal")
    parser.add_argument("--theme", choices=["auto", "plain"], default="auto", help="Summary theme")
    exec_group = parser.add_mutually_exclusive_group()
    exec_group.add_argument("--host", action="store_true", help="Force commands to run on the host")
    exec_group.add_argument("--chroot", default="", help="Run command phases inside a named chroot via chrootctl exec")
    parser.add_argument("--no-auto-chroot", action="store_true", help="On Termux host, do not auto-route this devtool command through chrootctl")
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
        if name == "test":
            cmd.add_argument("--no-build", action="store_true", default=argparse.SUPPRESS, help="Pass --no-build to dotnet test")
        if name in {"validate", "wine-validate"}:
            _add_validation_skip_flags(cmd)
            if name == "validate":
                cmd.add_argument("validate_scope", nargs="?", help="Optional artifact validation scope: overlay or overlays")
                cmd.add_argument("validate_artifact", nargs="?", help="Artifact archive to validate when scope is overlay/overlays")
                cmd.add_argument("--fail-on-conflict", action="store_true", help="For overlay bundles, fail if multiple nested overlays write the same file")
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
    ai_prompt.add_argument("--artifact-type", choices=["zip", "tar", "tar.gz", "tgz", "tar.xz", "txz", "tar.bz2", "tbz2", "tar.zst", "tzst"], default="zip")
    ai_prompt.add_argument("--overlay-count", type=int, default=0)
    ai_prompt.add_argument("--commit-message", default="Clear commit message")
    ai_prompt.add_argument("--wine", action="store_true", help="Prefer wine-validate wording in the generated prompt")
    ai_prompt.add_argument("-o", "--output", default="", help="Write the generated prompt to a file")
    ai_prompt.add_argument("--format", choices=["markdown", "text"], default="markdown")

    commit = subparsers.add_parser("commit", aliases=["ci"])
    commit.add_argument("message")

    for name, aliases in [
        ("apply-overlay", ["ao"]),
        ("apply-overlays", ["aos"]),
        ("apply-snapshot", ["as"]),
    ]:
        cmd = subparsers.add_parser(name, aliases=aliases)
        if name == "apply-overlays":
            cmd.add_argument("artifact", nargs="+", help="Overlay bundle archive, or multiple overlay archives to bundle/apply transactionally")
        else:
            cmd.add_argument("artifact")
        cmd.add_argument("--validate", action="store_true", help="Run validation after applying the artifact")
        cmd.add_argument("--validate-only", action="store_true", help="Skip apply and only run validation")
        cmd.add_argument("--preview", action="store_true", help="Preview archive changes without applying")
        cmd.add_argument("--diff", action="store_true", help="Show a repo-relative unified diff of artifact changes")
        cmd.add_argument("--interactive", action="store_true", help="Show preview and ask before applying")
        cmd.add_argument("--no-manifest-check", action="store_true", help="Disable devtool artifact manifest validation")
        cmd.add_argument("--keep-artifact", action="store_true", help="Keep the source artifact and sidecars after apply (default: delete after success or failure)")
        cmd.add_argument("--delete-artifact-on-failure", action="store_true", help="Deprecated: artifacts are deleted after failure by default unless --keep-artifact is used")
        _add_validation_skip_flags(cmd)
        cmd.add_argument("--commit", default="", help="Commit with this message after apply and optional validation")

    apply_cmd = subparsers.add_parser("apply", help="Apply an overlay, overlay bundle, or snapshot")
    apply_sub = apply_cmd.add_subparsers(dest="apply_command", required=True)
    for sub_name, command_name in [("overlay", "apply-overlay"), ("overlays", "apply-overlays"), ("snapshot", "apply-snapshot")]:
        sub = apply_sub.add_parser(sub_name)
        sub.set_defaults(normalized_command=command_name)
        if command_name == "apply-overlays":
            sub.add_argument("artifact", nargs="+", help="Overlay bundle archive, or multiple overlay archives to bundle/apply transactionally")
        else:
            sub.add_argument("artifact")
        sub.add_argument("--validate", action="store_true", help="Run validation after applying the artifact")
        sub.add_argument("--validate-only", action="store_true", help="Skip apply and only run validation")
        sub.add_argument("--preview", action="store_true", help="Preview archive changes without applying")
        sub.add_argument("--diff", action="store_true", help="Show a repo-relative unified diff of artifact changes")
        sub.add_argument("--interactive", action="store_true", help="Show preview and ask before applying")
        sub.add_argument("--no-manifest-check", action="store_true", help="Disable devtool artifact manifest validation")
        sub.add_argument("--keep-artifact", action="store_true", help="Keep the source artifact and sidecars after apply (default: delete after success or failure)")
        sub.add_argument("--delete-artifact-on-failure", action="store_true", help="Deprecated: artifacts are deleted after failure by default unless --keep-artifact is used")
        _add_validation_skip_flags(sub)
        sub.add_argument("--commit", default="", help="Commit with this message after apply and optional validation")

    validate_overlay_cmd = subparsers.add_parser("validate-overlay", aliases=["vo"])
    validate_overlay_cmd.add_argument("artifact")
    validate_overlay_cmd.add_argument("--fail-on-conflict", action="store_true", help="For bundles, fail if multiple nested overlays write the same file")
    validate_overlays_cmd = subparsers.add_parser("validate-overlays", aliases=["vos"])
    validate_overlays_cmd.add_argument("artifact", nargs="+", help="Overlay bundle archive, or multiple overlay archives to validate as a bundle")
    validate_overlays_cmd.add_argument("--fail-on-conflict", action="store_true", help="Fail if multiple nested overlays write the same file")

    bundle_cmd = subparsers.add_parser("bundle")
    bundle_sub = bundle_cmd.add_subparsers(dest="bundle_command", required=True)
    bundle_overlays = bundle_sub.add_parser("overlays")
    bundle_overlays.add_argument("overlays", nargs="+")
    bundle_overlays.add_argument("--name", default="")
    bundle_overlays.add_argument("--description", default="")
    bundle_overlays.add_argument("--default-target", default="", help="Default target for entries that do not declare one")
    bundle_overlays.add_argument("--targets", default="", help="Comma-separated targets aligned with the overlay arguments; use '-' to infer")
    bundle_overlays.add_argument("-o", "--out", required=True, help="Output bundle archive; extension selects format")

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

    hud_cmd = subparsers.add_parser("hud")
    hud_cmd.add_argument("--tasker-dir", default=DEFAULT_TASKER_DIR, help="Directory containing devtool HUD/Tasker JSON files (default: platform state dir)")
    hud_cmd.add_argument("--mode", choices=["auto", "android-gui", "android-bridge", "android-notify", "termux-gui", "terminal", "notification", "web", "studio", "desktop", "desktop-window", "desktop-notify", "v3", "v4", "v5", "v6", "console", "dashboard", "all", "unix", "gnome"], default="auto", help="Run HUD renderer: android-gui/android-bridge, terminal/v3, web/v4, studio/v5, or Unix desktop/v6")
    hud_cmd.add_argument("--host", default="127.0.0.1", help="Host/IP for HUD web/studio modes")
    hud_cmd.add_argument("--port", type=int, default=8765, help="Port for HUD web/studio modes")
    hud_cmd.add_argument("--poll-interval", type=float, default=0.1, help="Seconds between HUD refreshes")
    hud_cmd.add_argument("--recent-events", type=int, default=8, help="Recent JSONL events to show/serve")
    hud_cmd.add_argument("--notify-id", default=DEFAULT_NOTIFICATION_ID, help="Notification id for HUD notification/studio mode")
    hud_cmd.add_argument("--studio-notify", action="store_true", help="In studio/v5 mode, also mirror state into termux-notification when available")
    hud_cmd.add_argument("--once", action="store_true", help="Render/update once and exit")
    hud_cmd.add_argument("--follow-active", action="store_true", help="Follow the global active HUD directory written by auto-HUD")

    android_bridge_cmd = subparsers.add_parser("android-hud-bridge", aliases=["termux-hud-bridge"], help="Display chroot/proot Android HUD bridge state from the Termux host")
    android_bridge_cmd.add_argument("--state-dir", default="", help="Shared bridge state directory; defaults to ~/.local/state/devtool-android-hud on Termux or /home/termux/.local/state/devtool-android-hud in a chroot")
    android_bridge_cmd.add_argument("--mode", choices=["android-gui", "android-notify", "terminal"], default="android-gui", help="Renderer used by the Termux host helper")
    android_bridge_cmd.add_argument("--poll-interval", type=float, default=0.1, help="Seconds between HUD refreshes")
    android_bridge_cmd.add_argument("--recent-events", type=int, default=8, help="Recent events to show")
    android_bridge_cmd.add_argument("--notify-id", default=DEFAULT_NOTIFICATION_ID, help="Notification id for android-notify mode")
    android_bridge_cmd.add_argument("--once", action="store_true", help="Render/update once and exit")
    android_bridge_cmd.add_argument("--follow-active", action="store_true", default=True, help="Follow active HUD pointer in the shared bridge directory")
    android_bridge_cmd.add_argument("--no-follow-active", dest="follow_active", action="store_false", help="Read the shared bridge directory directly")

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

    import re as _re

    try:
        payload = json.loads(json_text)
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}

    warnings = int(payload.get("warnings", 0) or 0)
    errors = int(payload.get("errors", 0) or 0)
    failed = int(payload.get("failed", 0) or 0)
    skipped = int(payload.get("skipped", 0) or 0)
    succeeded = errors == 0 and failed == 0

    def _test_totals() -> tuple[int, int, int, int] | None:
        """Extract and aggregate test totals from captured command output."""

        # Modern dotnet test:
        # Passed! - Failed: 0, Passed: 16, Skipped: 0, Total: 16
        dotnet_matches = list(
            _re.finditer(
                r"\b(?:Passed|Failed)!\s*-\s*"
                r"Failed:\s*(\d+),\s*"
                r"Passed:\s*(\d+),\s*"
                r"Skipped:\s*(\d+),\s*"
                r"Total:\s*(\d+)",
                raw_output,
                _re.IGNORECASE,
            )
        )

        if dotnet_matches:
            test_failed = sum(int(match.group(1)) for match in dotnet_matches)
            test_passed = sum(int(match.group(2)) for match in dotnet_matches)
            test_skipped = sum(int(match.group(3)) for match in dotnet_matches)
            test_total = sum(int(match.group(4)) for match in dotnet_matches)
            return test_passed, test_failed, test_skipped, test_total

        # Older VSTest summary:
        # Total tests: 16. Passed: 16. Failed: 0. Skipped: 0.
        vstest_matches = list(
            _re.finditer(
                r"Total tests:\s*(\d+).*?"
                r"Passed:\s*(\d+).*?"
                r"Failed:\s*(\d+).*?"
                r"Skipped:\s*(\d+)",
                raw_output,
                _re.IGNORECASE,
            )
        )

        if vstest_matches:
            test_total = sum(int(match.group(1)) for match in vstest_matches)
            test_passed = sum(int(match.group(2)) for match in vstest_matches)
            test_failed = sum(int(match.group(3)) for match in vstest_matches)
            test_skipped = sum(int(match.group(4)) for match in vstest_matches)
            return test_passed, test_failed, test_skipped, test_total

        # Pytest fallback, for validating devtool itself.
        pytest_match = _re.search(
            r"(?:(\d+)\s+failed(?:,\s*)?)?"
            r"(?:(\d+)\s+passed)"
            r"(?:,\s*(\d+)\s+skipped)?",
            raw_output,
            _re.IGNORECASE,
        )
        if pytest_match:
            test_failed = int(pytest_match.group(1) or 0)
            test_passed = int(pytest_match.group(2) or 0)
            test_skipped = int(pytest_match.group(3) or 0)
            return (
                test_passed,
                test_failed,
                test_skipped,
                test_passed + test_failed + test_skipped,
            )

        # Use structured JSON values when available.
        json_passed = payload.get("passed")
        json_total = payload.get("total")

        if json_passed is not None or json_total is not None:
            test_passed = int(json_passed or 0)
            test_failed = int(payload.get("failed", 0) or 0)
            test_skipped = int(payload.get("skipped", 0) or 0)
            test_total = int(
                json_total
                if json_total is not None
                else test_passed + test_failed + test_skipped
            )
            return test_passed, test_failed, test_skipped, test_total

        return None

    def _warning_details(limit: int = 10) -> tuple[list[str], int]:
        """Extract useful, deduplicated warnings from captured output."""

        warning_lines: list[str] = []
        seen: set[str] = set()

        warning_patterns = (
            # MSBuild/compiler warnings:
            # file.cs(1,2): warning CS1234: Message [project.csproj]
            r"^(.*?\bwarning\s+[A-Z]{1,5}\d+\s*:\s*.+)$",

            # NuGet warnings such as NU1603 and NU1903.
            r"^(.*?\bwarning\s+NU\d+\s*:\s*.+)$",

            # Generic command warnings.
            r"^(.*?\bwarning\s*:\s*.+)$",

            # Common Python warning forms.
            r"^(.+Warning:\s*.+)$",
        )

        combined = _re.compile(
            "|".join(f"(?:{pattern})" for pattern in warning_patterns),
            _re.IGNORECASE,
        )

        for raw_line in raw_output.splitlines():
            line = raw_line.strip()
            if not line or "0 Warning(s)" in line or "warnings=0" in line:
                continue
            if not combined.search(line):
                continue

            # Remove repeated whitespace while preserving the useful message.
            cleaned = _re.sub(r"\s+", " ", line).strip()

            # Omit redundant project suffixes when they make the copied summary
            # excessively noisy, while retaining file, code, and message.
            cleaned = _re.sub(
                r"\s+\[[^\[\]]+\.(?:csproj|fsproj|vbproj)\]\s*$",
                "",
                cleaned,
                flags=_re.IGNORECASE,
            )

            key = cleaned.casefold()
            if key in seen:
                continue

            seen.add(key)
            warning_lines.append(cleaned)

        total_unique = len(warning_lines)
        return warning_lines[:limit], total_unique

    def _commit_hash() -> str:
        patterns = (
            # Standard git commit output: [main abc1234] Message
            r"^\[[^\]\n]*?\s([0-9a-f]{7,40})\]\s",
            # Detached HEAD output.
            r"^\[detached HEAD\s+([0-9a-f]{7,40})\]\s",
            # Other explicit commit output.
            r"\bcommit(?:ted)?(?:\s+as)?\s+([0-9a-f]{7,40})\b",
        )

        for pattern in patterns:
            match = _re.search(pattern, raw_output, _re.IGNORECASE | _re.MULTILINE)
            if match:
                return match.group(1)

        return ""

    action_pattern = _re.compile(
        r"^([a-z0-9_-]+):\s+"
        r"(ok|failed|skipped)\s+"
        r"exit=(\d+)\s+"
        r"time=([0-9.]+)s",
        _re.IGNORECASE | _re.MULTILINE,
    )

    action_names = {
        "apply-overlay": "Applied overlay",
        "apply-overlays": "Applied overlay bundle",
        "apply-snapshot": "Applied snapshot",
        "restore": "Restored dependencies",
        "build": "Built project",
        "test": "Ran test suite",
        "wine-test": "Ran Wine test suite",
        "package": "Created package",
        "run": "Ran project",
        "git-commit": "Committed repository changes",
        "artifact-cleanup": "Cleaned applied artifact",
        "rollback": "Rolled back changes",
    }

    action_lines: list[str] = []
    total_duration = 0.0

    for match in action_pattern.finditer(summary_text):
        name = match.group(1).lower()
        status = match.group(2).lower()
        duration = float(match.group(4))
        total_duration += duration

        # git-add is an implementation detail; git-commit conveys the useful action.
        if name == "git-add":
            continue

        description = action_names.get(
            name,
            name.replace("-", " ").replace("_", " ").capitalize(),
        )

        if status == "ok":
            action_lines.append(f"- {description} ({duration:.2f}s)")
        elif status == "skipped":
            action_lines.append(f"- {description}: skipped")
        else:
            action_lines.append(f"- {description}: failed ({duration:.2f}s)")

    test_totals = _test_totals()
    commit_hash = _commit_hash()

    success_lines = [
        "Devtool completed successfully.",
        "",
        "Actions completed:",
        *action_lines,
        "",
    ]

    if test_totals is not None:
        test_passed, test_failed, test_skipped, test_total = test_totals
        success_lines.append(
            "Tests: "
            f"{test_passed} passed, "
            f"{test_failed} failed, "
            f"{test_skipped} skipped "
            f"({test_total} total)"
        )
    else:
        success_lines.append("Tests: completed successfully")

    success_lines.append(
        f"Diagnostics: {warnings} warnings, {errors} errors"
    )

    warning_details, unique_warning_count = _warning_details()

    if warnings > 0 or warning_details:
        success_lines.extend(("", "Warnings:"))

        if warning_details:
            success_lines.extend(f"- {warning}" for warning in warning_details)

            hidden_warning_count = max(0, unique_warning_count - len(warning_details))
            if hidden_warning_count:
                success_lines.append(
                    f"- …and {hidden_warning_count} more unique warning"
                    f"{'s' if hidden_warning_count != 1 else ''}; see the full log."
                )
        else:
            success_lines.append(
                f"- {warnings} warning{'s' if warnings != 1 else ''} reported; "
                "individual warning text was not available in captured output."
            )

    if commit_hash:
        success_lines.append(f"Git commit: {commit_hash}")

    if total_duration > 0:
        success_lines.append(f"Total action time: {total_duration:.2f}s")

    success_summary = "\n".join(success_lines).strip()
    failure_output = raw_output.strip() or summary_text.strip() or json_text.strip()

    # Automatic behavior:
    # - successful run: descriptive concise summary
    # - failed run: verbose output for troubleshooting
    if succeeded:
        to_copy = success_summary
        label = "Successful run summary"
    else:
        to_copy = failure_output
        label = "Failure output"

    # Explicit clipboard modes retain their override behavior.
    if args.copy_json:
        to_copy = json_text
        label = "JSON summary"
    elif args.copy_summary:
        to_copy = success_summary if succeeded else summary_text
        label = "Summary"

    if args.copy or args.copy_summary or args.copy_json:
        result = (
            copy_text(to_copy)
            if args.copy_via == "auto"
            else copy_text(to_copy, preferred=args.copy_via)
        )
        if result.success:
            console.print(
                f"[green]{label} copied to clipboard via {result.backend}.[/green]"
            )
        else:
            console.print(
                f"[yellow]{result.reason or 'Clipboard copy failed.'}[/yellow]"
            )
        return

    if args.command not in INTERACTIVE_COPY_COMMANDS:
        return
    if not console.is_interactive or not sys.stdin.isatty() or not to_copy.strip():
        return

    copy_now = Confirm.ask(
        f"Copy {label.lower()} to clipboard?",
        default=True,
        console=console,
    )
    if copy_now:
        result = (
            copy_text(to_copy)
            if args.copy_via == "auto"
            else copy_text(to_copy, preferred=args.copy_via)
        )
        if result.success:
            console.print(
                f"[green]{label} copied to clipboard via {result.backend}.[/green]"
            )
        else:
            console.print(
                f"[yellow]{result.reason or 'Clipboard copy failed.'}[/yellow]"
            )

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
    command = str(getattr(args, "command", "") or "")
    if getattr(args, "tasker", False):
        args.tasker_progress = True
        args.tasker_broadcast = True
        args.notify_progress = True
    elif getattr(args, "tasker_progress", None) is None:
        # Progress is useful by default for commands that run external phases.
        # Keep introspection/config/HUD commands quiet unless the user opts in.
        args.tasker_progress = command in PROGRESS_CAPABLE_COMMANDS

    if getattr(args, "auto_hud", None) is None:
        args.auto_hud = bool(getattr(args, "tasker_progress", False)) and command != "hud"


def _tasker_for_args(args: argparse.Namespace, config: ProjectConfig | None = None, repo: Path | None = None) -> TaskerProgressSink | None:
    repo_root = (config.repo_root if config is not None else (repo or Path(args.repo or Path.cwd()))).resolve()
    target = str(getattr(args, "target", "") or getattr(args, "solution", "") or getattr(args, "project", "") or "")
    tasker_dir_text = str(getattr(args, "tasker_dir", DEFAULT_TASKER_DIR))
    directory = resolve_cli_path(tasker_dir_text)
    enabled = bool(getattr(args, "tasker_progress", False))
    notify_progress = bool(getattr(args, "notify_progress", False))
    hud_mode = str(getattr(args, "hud_mode", "desktop") or "desktop")
    if enabled and bool(getattr(args, "auto_hud", False)):
        try:
            from .hud import choose_hud_mode, default_android_bridge_dir
            chosen_hud_mode = choose_hud_mode(hud_mode)
            tasker_dir_is_default = tasker_dir_text == DEFAULT_TASKER_DIR
            if chosen_hud_mode == "android-bridge" and (tasker_dir_is_default or os.environ.get("DEVTOOL_HUD_BRIDGE_DIR")):
                directory = default_android_bridge_dir()
        except Exception:
            chosen_hud_mode = hud_mode
        start_auto_hud(directory, mode=hud_mode, poll_interval=0.1)
    return build_tasker_sink(
        enabled=enabled,
        directory=str(directory),
        command=str(getattr(args, "command", "devtool") or "devtool"),
        repo_root=repo_root,
        target=target,
        broadcast=bool(getattr(args, "tasker_broadcast", False)),
        broadcast_action=str(getattr(args, "tasker_action", DEFAULT_TASKER_ACTION)),
        notify_progress=notify_progress,
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
    return {"level": level, "title": title, "text": body, "expanded_title": title, "expanded_text": body, "body": body}


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
        if name in APPLY_COMMANDS:
            raise RuntimeError("batch apply steps must be executed from main runner")
        specs.extend(build_specs(config, name))
    return specs, pre_results, pre_outputs



def _read_artifact_manifest(artifact: str) -> dict[str, str]:
    try:
        data = read_artifact_manifest(artifact)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def _artifact_arg_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _single_artifact_arg(value: object) -> str:
    items = _artifact_arg_list(value)
    return items[0] if items else ""


def _make_ad_hoc_overlay_bundle(artifacts: list[str], *, name: str = "adhoc_overlay_bundle") -> tuple[tempfile.TemporaryDirectory[str], Path, list[Path]]:
    tmp = tempfile.TemporaryDirectory(prefix="devtool-adhoc-overlays-")
    paths = [resolve_artifact_path(item) for item in artifacts]
    bundle = create_overlay_bundle(Path(tmp.name) / f"{name}.zip", paths, name=name)
    return tmp, bundle, paths


def _load_command_config(args: argparse.Namespace) -> ProjectConfig:
    target_name = args.target
    solution = args.solution
    project = args.project

    if args.command in LEGACY_APPLY_VALIDATE_COMMANDS and (getattr(args, "validate", False) or getattr(args, "validate_only", False)):
        manifest = _read_artifact_manifest(_single_artifact_arg(getattr(args, "artifact", "")))
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


def _normalize_command_aliases(args: argparse.Namespace) -> None:
    if getattr(args, "command", "") == "apply":
        args.command = str(getattr(args, "normalized_command", "") or "apply-overlay")


def _is_artifact_validation_args(args: argparse.Namespace) -> bool:
    if args.command in {"validate-overlay", "vo", "validate-overlays", "vos"}:
        return True
    return args.command in {"validate", "v"} and str(getattr(args, "validate_scope", "") or "") in {"overlay", "overlays"}


def _artifact_validation_paths(args: argparse.Namespace) -> list[str]:
    if args.command in {"validate-overlay", "vo", "validate-overlays", "vos"}:
        return _artifact_arg_list(getattr(args, "artifact", ""))
    return _artifact_arg_list(getattr(args, "validate_artifact", ""))


def _artifact_validation_path(args: argparse.Namespace) -> str:
    return _single_artifact_arg(_artifact_validation_paths(args))


def _validate_apply_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.command in {"validate", "v"}:
        scope = str(getattr(args, "validate_scope", "") or "")
        if scope and scope not in {"overlay", "overlays"}:
            parser.error("validate positional form is only supported as: validate overlay <archive> or validate overlays <bundle>")
        if scope in {"overlay", "overlays"} and not getattr(args, "validate_artifact", ""):
            parser.error(f"validate {scope} requires an artifact archive")
    if args.command not in APPLY_COMMANDS:
        return
    if (args.skip_restore or args.skip_build or args.skip_test) and not (args.validate or args.validate_only):
        parser.error("--skip-restore, --skip-build, and --skip-test require --validate or --validate-only.")
    if args.validate_only and args.validate:
        parser.error("--validate and --validate-only cannot be combined.")
    if args.validate_only and args.commit:
        parser.error("--validate-only cannot be combined with --commit.")
    preview_only = getattr(args, "preview", False) or (getattr(args, "diff", False) and not getattr(args, "interactive", False))
    if preview_only and (args.validate or args.validate_only or args.commit):
        parser.error("--preview/--diff cannot be combined with validation or commit flags unless --interactive is used with --diff.")


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


def _prepare_specs_for_execution(config: ProjectConfig, specs: list[CommandSpec], args: argparse.Namespace) -> list[CommandSpec]:
    items = _matrix_items(args)
    if items and getattr(args, "matrix_mode", "rid") == "rid":
        specs = apply_matrix(specs, items, mode="rid")
    if getattr(args, "chroot", "") and not getattr(args, "host", False):
        if bool(getattr(args, "_devtool_termux_host_chroot_backend", False)):
            from .termux_chroot_runner import wrap_specs_for_termux_host_chroot

            specs = wrap_specs_for_termux_host_chroot(
                specs,
                str(args.chroot),
                repo_root=config.repo_root,
                path_map=getattr(args, "_devtool_chroot_path_map", None),
            )
        else:
            specs = wrap_specs_for_chroot(specs, args.chroot)
    return specs


def _validation_specs_for_args(config: ProjectConfig, args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[CommandSpec]:
    bundle_targets = [str(item).strip() for item in getattr(args, "_devtool_bundle_targets", []) if str(item).strip()]
    items = _matrix_items(args)
    if bundle_targets and args.command in APPLY_COMMANDS and not items:
        specs: list[CommandSpec] = []
        for item in dict.fromkeys(bundle_targets):
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
                spec.metadata["bundle_target"] = item
                spec.metadata["matrix_mode"] = "target"
            specs.extend(target_specs)
        return specs
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


def _maybe_apply_test_no_build_flag(specs: list[CommandSpec], args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[CommandSpec]:
    if args.command not in {"test", "t"} or not bool(getattr(args, "no_build", False)):
        return specs
    updated, changed = add_no_build_to_dotnet_test_specs(specs, allow_explicit=True)
    if changed == 0:
        parser.error("--no-build is only supported for dotnet test commands.")
    return updated


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
        _prepare_specs_for_execution(config, specs, args),
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
    if any(r.name == "apply-rollback" and r.returncode == 0 for r in results):
        status = "rolled-back"
    validation_results = [r for r in results[1:] if r.name not in {"git-add", "git-commit", "apply-rollback"}]
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
            artifact_type=getattr(args, "_devtool_artifact_type", "snapshot" if "snapshot" in args.command else "overlay"),
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


def _artifact_commit_paths(args: argparse.Namespace) -> list[str]:
    """Return repo-relative paths that belong to the current applied artifact.

    The preview is computed before apply, so it contains exactly the paths the
    artifact is expected to add/modify/delete. This prevents unrelated dirty repo
    files from being included in the automatic handoff commit.
    """
    paths: list[str] = []
    seen: set[str] = set()

    def add_path(value: object) -> None:
        text = str(value or "").strip().replace("\\", "/")
        if not text or text == ".":
            return
        if text.startswith("/") or text.startswith("../") or "/../" in text:
            return
        if text in seen:
            return
        seen.add(text)
        paths.append(text)

    preview = getattr(args, "_devtool_artifact_preview", None)
    if isinstance(preview, dict):
        changes = preview.get("changes")
        if isinstance(changes, list):
            for change in changes:
                if not isinstance(change, dict):
                    continue
                kind = str(change.get("kind") or "").strip()
                if kind == "unchanged":
                    continue
                add_path(change.get("path"))

    # Fallback for tests/older apply paths where preview was not attached.
    if not paths:
        manifest = getattr(args, "_devtool_artifact_manifest", None)
        if isinstance(manifest, dict):
            changes = manifest.get("changes")
            if isinstance(changes, dict):
                for key in ("addedFiles", "modifiedFiles", "deletedPaths"):
                    values = changes.get(key)
                    if isinstance(values, list):
                        for value in values:
                            add_path(value)

    # Applying overlays/snapshots updates the artifact history file.
    # Include it in scoped auto-commits so the repo records what was applied.
    add_path(".devtool/artifacts.json")

    return paths


def _git_commit_with_optional_log(
    repo_root: Path,
    message: str,
    *,
    verbose: bool,
    dry_run: bool,
    log_path: Path | str | None,
    paths: list[str] | None = None,
) -> tuple[list[OperationResult], str]:
    kwargs = {"verbose": verbose, "dry_run": dry_run}
    if log_path is not None:
        kwargs["log_path"] = log_path
    if paths is not None:
        kwargs["paths"] = paths

    # git_commit grew optional log_path/paths kwargs over time. Some tests and
    # external callers monkeypatch the older signature, so peel unsupported
    # kwargs one by one instead of failing after the first compatibility retry.
    unsupported: set[str] = set()
    while True:
        try:
            return git_commit(repo_root, message, **kwargs)
        except TypeError as exc:
            message_text = str(exc)
            removed = False
            for key in ("log_path", "paths"):
                if key in kwargs and key in message_text and key not in unsupported:
                    kwargs.pop(key, None)
                    unsupported.add(key)
                    removed = True
                    break
            if not removed:
                raise

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
    # Failed applies or failed validation are never committed.
    if not results or any(result.returncode != 0 for result in results):
        return results, raw_output

    # Record artifact history before the auto-commit so .devtool/artifacts.json
    # is staged with the same overlay/snapshot commit.
    if not getattr(args, "_devtool_history_recorded", False):
        _record_apply_history(config, args, results)
        args._devtool_history_recorded = True

    commit_paths = _artifact_commit_paths(args)
    commit_results, commit_raw = _git_commit_with_optional_log(
        config.repo_root,
        commit_message,
        verbose=args.verbose,
        dry_run=args.dry_run,
        log_path=log_path,
        paths=commit_paths,
    )
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

def _format_artifact_exception(exc: BaseException, *, artifact: str, artifact_type: str) -> str:
    message = str(exc).strip()
    exc_name = type(exc).__name__
    if not message:
        message = "No detail message was provided by the exception."
    return (
        f"Artifact check failed before apply.\n"
        f"artifact_type: {artifact_type}\n"
        f"artifact: {artifact}\n"
        f"error_type: {exc_name}\n"
        f"error: {message}"
    )


def _show_and_confirm_artifact_manifest(args: argparse.Namespace, manifest: dict, *, confirm: bool = True) -> None:
    if not manifest:
        return
    render_artifact_manifest(manifest, console_obj=console)
    if not confirm:
        return
    if getattr(args, "yes", False) or getattr(args, "force", False):
        return
    if console.is_interactive and sys.stdin.isatty():
        if not Confirm.ask("Continue with this artifact?", default=True, console=console):
            raise SystemExit(1)
        return
    raise SystemExit("Artifact manifest confirmation requires --yes in non-interactive mode.")

def _run_apply(config: ProjectConfig, args: argparse.Namespace, name: str, artifact: object, *, log_path: Path | str | None = None, tasker_progress: TaskerProgressSink | None = None) -> tuple[list[OperationResult], str]:
    snapshot = "snapshot" in name
    artifact_type = "snapshot" if snapshot else "overlay"
    artifact_items = _artifact_arg_list(artifact)
    display_artifact = " ".join(artifact_items) if artifact_items else ""
    temp_bundle: tempfile.TemporaryDirectory[str] | None = None
    source_artifacts: list[Path] = []
    if name in {"apply-overlays", "aos"} and len(artifact_items) > 1:
        temp_bundle, temp_path, source_artifacts = _make_ad_hoc_overlay_bundle(artifact_items, name="adhoc_overlay_bundle")
        artifact = str(temp_path)
    else:
        artifact = _single_artifact_arg(artifact)
    if tasker_progress is not None:
        tasker_progress.phase_started(name, index=1, total=1, argv=[name, display_artifact or str(artifact)])

    def _return_apply(result: OperationResult, text: str) -> tuple[list[OperationResult], str]:
        if tasker_progress is not None:
            tasker_progress.phase_finished(result)
        if temp_bundle is not None:
            temp_bundle.cleanup()
        return [result], text

    if getattr(args, "validate_only", False):
        result = OperationResult(
            "apply-skipped",
            [name, display_artifact or str(artifact)],
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
        artifact_path = resolve_artifact_path(artifact)
        is_bundle = (not snapshot) and (name in {"apply-overlays", "aos"} or is_overlay_bundle(artifact_path))
        artifact_type = "overlay_bundle" if is_bundle else artifact_type
        args._devtool_artifact_type = artifact_type
        args._devtool_artifact_path = str(artifact_path)
        args._devtool_artifact_sha = sha256_file(artifact_path) if artifact_path.exists() else ""
        validation = validate_artifact(config.repo_root, artifact_path, artifact_type=artifact_type)
        args._devtool_artifact_manifest = validation.manifest
        args._devtool_bundle_targets = overlay_bundle_targets(artifact_path) if artifact_type == "overlay_bundle" else []
        preview = preview_artifact(config.repo_root, artifact_path, snapshot=snapshot)
        args._devtool_artifact_preview = preview
    except Exception as exc:
        detail = _format_artifact_exception(exc, artifact=artifact, artifact_type=artifact_type)
        result = OperationResult(
            "artifact-check",
            [name, artifact],
            1,
            0.0,
            detail,
            [Issue("error", "artifact-check", str(artifact), type(exc).__name__, str(exc).strip() or "Artifact check failed without a detail message.")],
            failure_kind="artifact_failure",
        )
        return _return_apply(result, "$ " + " ".join(result.argv) + "\n" + result.output)

    # Show manifest metadata before any destructive confirmation such as dirty-tree
    # or snapshot overwrite prompts.  --yes/-y confirms this prompt too.
    preview_only = getattr(args, "preview", False) or (getattr(args, "diff", False) and not getattr(args, "interactive", False))
    try:
        _show_and_confirm_artifact_manifest(args, validation.manifest, confirm=not preview_only)
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
    diff_text = ""
    if getattr(args, "diff", False):
        diff_text = diff_artifact(config.repo_root, artifact_path, snapshot=snapshot)

    if getattr(args, "preview", False) or (getattr(args, "diff", False) and not getattr(args, "interactive", False)):
        output_parts = []
        if getattr(args, "preview", False):
            output_parts.append(preview_text)
            console.print(preview_text)
        if getattr(args, "diff", False):
            output_parts.append(diff_text)
            console.print(diff_text.rstrip())
        output = "\n".join(part for part in output_parts if part)
        result = OperationResult(
            "apply-diff" if getattr(args, "diff", False) else "apply-preview",
            [name, "--diff" if getattr(args, "diff", False) else "--preview", artifact],
            0,
            0.0,
            output,
            [],
            skipped=True,
        )
        return _return_apply(result, "$ " + " ".join(result.argv) + "\n" + output)

    if getattr(args, "interactive", False):
        console.print(preview_text)
        if getattr(args, "diff", False) and diff_text:
            console.print(diff_text.rstrip())
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

    if name in {"apply-overlays", "aos"}:
        result = apply_overlay_bundle(config.repo_root, artifact, force=args.force, yes=args.yes, dry_run=args.dry_run, log_path=log_path, preview=preview, source_artifacts=source_artifacts or None)
    elif name.startswith("apply-overlay") or name == "ao":
        result = apply_overlay(config.repo_root, artifact, force=args.force, yes=args.yes, dry_run=args.dry_run, log_path=log_path, preview=preview)
    else:
        result = apply_snapshot(config.repo_root, artifact, force=args.force, yes=args.yes, dry_run=args.dry_run, log_path=log_path, preview=preview)
    if result.returncode != 0 and not result.failure_kind:
        result.failure_kind = "artifact_failure"
    manifest_note = "" if getattr(args, "no_manifest_check", False) else "\n" + "\n".join(validation.messages)
    text = "$ " + " ".join(result.argv) + "\n" + result.output + manifest_note
    result.output = result.output + manifest_note
    return _return_apply(result, text)



def _apply_transaction_path(results: list[OperationResult]) -> str:
    for result in results:
        path = result.metadata.get("transaction_path") if getattr(result, "metadata", None) else ""
        if path:
            return str(path)
    return ""


def _apply_artifact_paths(results: list[OperationResult]) -> list[str]:
    paths: list[str] = []
    for result in results:
        metadata = result.metadata if getattr(result, "metadata", None) else {}
        if not metadata.get("delete_artifact_on_success"):
            continue
        raw_paths = metadata.get("artifact_paths")
        if isinstance(raw_paths, list):
            for item in raw_paths:
                text = str(item)
                if text and text not in paths:
                    paths.append(text)
        else:
            path = metadata.get("artifact_path")
            text = str(path or "")
            if text and text not in paths:
                paths.append(text)
    return paths


def _apply_artifact_path(results: list[OperationResult]) -> str:
    paths = _apply_artifact_paths(results)
    return paths[0] if paths else ""


def _artifact_paths_for_cleanup(artifact_path: Path) -> list[Path]:
    return artifact_cleanup_paths(artifact_path)


def _delete_original_artifact_after_apply(
    results: list[OperationResult],
    *,
    artifact: str = "",
    delete_on_failure: bool = False,
) -> OperationResult | None:
    """Delete the source artifact after success, or after failure when requested."""
    failed = any(result.returncode != 0 for result in results)
    rolled_back = any(
        result.name == "apply-rollback" or result.metadata.get("rolled_back_during_apply")
        for result in results
        if getattr(result, "metadata", None) is not None
    )
    if (failed or rolled_back) and not delete_on_failure:
        return None

    artifact_paths = _apply_artifact_paths(results)
    if not artifact_paths and artifact:
        artifact_paths = [str(resolve_artifact_path(item)) for item in _artifact_arg_list(artifact)]
    if not artifact_paths:
        return None

    started = datetime.now(timezone.utc)
    start_ts = started.timestamp()
    removed: list[str] = []
    errors: list[str] = []
    reason = "after failed apply" if failed or rolled_back else "after successful apply"
    try:
        for raw_path in artifact_paths:
            path = Path(raw_path)
            for candidate in _artifact_paths_for_cleanup(path):
                try:
                    if candidate.exists() or candidate.is_symlink():
                        candidate.unlink()
                        removed.append(str(candidate))
                except Exception as exc:
                    errors.append(f"{candidate}: {type(exc).__name__}: {exc}")
        if errors:
            return OperationResult(
                "artifact-cleanup",
                ["delete", *artifact_paths],
                1,
                datetime.now(timezone.utc).timestamp() - start_ts,
                f"Failed to delete original artifact(s) {reason}.\n" + "\n".join(errors),
                [],
                failure_kind="artifact_cleanup_failure",
            )
        if not removed:
            output = "Original artifact cleanup skipped; artifact(s) were already absent."
        else:
            output = f"Deleted original artifact(s) {reason}.\n" + "\n".join(f"removed: {item}" for item in removed)
        return OperationResult("artifact-cleanup", ["delete", *artifact_paths], 0, datetime.now(timezone.utc).timestamp() - start_ts, output, [])
    except BaseException as exc:
        return OperationResult(
            "artifact-cleanup",
            ["delete", str(path)],
            1,
            datetime.now(timezone.utc).timestamp() - start_ts,
            f"Failed to delete original artifact(s) {reason}.\n{type(exc).__name__}: {exc}",
            [],
            failure_kind="artifact_cleanup_failure",
        )


def _delete_original_artifact_after_success(results: list[OperationResult]) -> OperationResult | None:
    return _delete_original_artifact_after_apply(results, delete_on_failure=False)


def _run_transaction_rollback(
    transaction_path: str | Path,
    *,
    reason: str,
    tasker_progress: TaskerProgressSink | None = None,
    log_path: Path | str | None = None,
) -> tuple[OperationResult, str]:
    if tasker_progress is not None:
        tasker_progress.phase_started("apply-rollback", index=1, total=1, argv=["rsync", "rollback", str(transaction_path)])
    result = rollback_transaction(transaction_path, reason=reason)
    if log_path is not None:
        try:
            from .logstore import write_log
            result.log_path = str(write_log("$ " + " ".join(result.argv) + "\n" + result.output, log_path, append=True))
        except Exception:
            pass
    if tasker_progress is not None:
        tasker_progress.phase_finished(result)
    raw = "$ " + " ".join(result.argv) + "\n" + result.output
    return result, raw


def _has_failed_after_apply(results: list[OperationResult]) -> bool:
    if not results:
        return False
    # Ignore preview/cancelled-only flows; real apply results carry transaction metadata.
    tx_path = _apply_transaction_path(results)
    if not tx_path:
        return False
    return any(result.returncode != 0 for result in results)

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


def _is_devtool_dirty_path(path_text: str) -> bool:
    path_text = path_text.strip().strip('"')
    return path_text == ".devtool" or path_text.startswith(".devtool/")


def _status_line_ignored(line: str) -> bool:
    if not line.strip():
        return True
    paths_text = line[3:] if len(line) > 3 else line.strip()
    paths = [part.strip() for part in paths_text.split(" -> ")]
    return bool(paths) and all(_is_devtool_dirty_path(path) for path in paths)


def _git_dirty(repo_root: Path) -> bool:
    proc = subprocess.run(["git", "status", "--porcelain"], cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        return False
    return any(not _status_line_ignored(line) for line in proc.stdout.splitlines())


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
            okay = Confirm.ask("Git tree is dirty. Continue?", default=True, console=console)
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
        devtool_xdg_dir().mkdir(parents=True, exist_ok=True)
        fixed.append("state/log/XDG data directories")
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
        ("xdg_data", str(devtool_xdg_dir())),
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
        ("transaction_root", str(transaction_root(config.repo_root))),
    ]
    render_status_table(rows)


def _release_notes(repo_root: Path, from_ref: str, to_ref: str) -> str:
    range_expr = f"{from_ref}..{to_ref}" if from_ref else to_ref
    proc = subprocess.run(["git", "log", "--oneline", "--decorate=no", range_expr], cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc.stdout


def _run_artifact_validation_command(config: ProjectConfig, args: argparse.Namespace, *, as_json: bool = False) -> int:
    artifacts = _artifact_validation_paths(args)
    if not artifacts:
        raise SystemExit("Artifact archive is required.")
    explicit_bundle = args.command in {"validate-overlays", "vos"} or str(getattr(args, "validate_scope", "") or "") == "overlays"
    temp_bundle: tempfile.TemporaryDirectory[str] | None = None
    try:
        if explicit_bundle and len(artifacts) > 1:
            temp_bundle, artifact_path, _paths = _make_ad_hoc_overlay_bundle(artifacts, name="adhoc_validate_overlay_bundle")
            artifact_type = "overlay_bundle"
        else:
            artifact_path = resolve_artifact_path(artifacts[0])
            artifact_type = "overlay_bundle" if explicit_bundle or is_overlay_bundle(artifact_path) else "overlay"
        if artifact_type == "overlay_bundle":
            validation = validate_overlay_bundle(config.repo_root, artifact_path, fail_on_conflict=bool(getattr(args, "fail_on_conflict", False)))
        else:
            validation = validate_artifact(config.repo_root, artifact_path, artifact_type=artifact_type)
        if as_json:
            print(json.dumps({
                "ok": validation.ok,
                "artifact": str(artifact_path),
                "artifacts": artifacts,
                "artifact_type": artifact_type,
                "messages": validation.messages,
                "manifest": validation.manifest,
            }, indent=(2 if not getattr(args, "machine", "") == "compact" else None), separators=(None if not getattr(args, "machine", "") == "compact" else (",", ":"))))
        else:
            for message in validation.messages:
                console.print(message)
        return 0 if validation.ok else 1
    finally:
        if temp_bundle is not None:
            temp_bundle.cleanup()


def _run_bundle_command(args: argparse.Namespace) -> int:
    if args.bundle_command != "overlays":
        raise SystemExit(f"Unknown bundle command: {args.bundle_command}")
    targets = [item.strip() for item in str(getattr(args, "targets", "") or "").split(",") if item.strip()]
    out = create_overlay_bundle(
        args.out,
        list(args.overlays),
        name=args.name,
        description=args.description,
        targets=targets or None,
        default_target=getattr(args, "default_target", "") or "",
    )
    console.print(f"[green]Created overlay bundle[/green] {out}")
    return 0


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


def _maybe_prepare_termux_auto_chroot(raw_argv: list[str], args: argparse.Namespace) -> int | None:
    """Enable Android chroot execution from Termux.

    Default mode keeps devtool itself on the Termux host and uses the chroot as
    an execution backend for command phases.  This preserves the native Rich UI,
    Termux:GUI HUD, clipboard, and command summary on Android.  The old whole-
    devtool delegation remains available with DEVTOOL_AUTO_CHROOT_MODE=full.
    """
    if bool(getattr(args, "no_auto_chroot", False)):
        return None
    try:
        from .termux_chroot_runner import (
            normalize_hud_mode_for_termux_host,
            run_auto_chroot,
            should_auto_chroot,
            translate_devtool_args,
        )
        if not should_auto_chroot(raw_argv, command=str(getattr(args, "command", "") or "")):
            return None
        mode = os.environ.get("DEVTOOL_AUTO_CHROOT_MODE", "backend").strip().lower()
        if mode in {"full", "legacy", "process", "wrapper"}:
            return run_auto_chroot(raw_argv)
        config, _forwarded = translate_devtool_args(raw_argv, cwd=os.getcwd())
        args.chroot = config.chroot_name
        args.host = False
        args._devtool_termux_host_chroot_backend = True
        args._devtool_chroot_path_map = config.path_map
        args._devtool_chroot_name = config.chroot_name
        args.hud_mode = normalize_hud_mode_for_termux_host(str(getattr(args, "hud_mode", "desktop") or "desktop"))
        if str(getattr(args, "copy_via", "auto") or "auto") == "auto":
            args.copy_via = "termux"
        if getattr(args, "tasker_progress", None) is True and getattr(args, "auto_hud", None) is None:
            args.auto_hud = True
        return None
    except Exception as exc:
        if os.environ.get("DEVTOOL_AUTO_CHROOT_STRICT", "0").strip().lower() in {"1", "true", "yes"}:
            raise
        console.print(f"[yellow]Auto chroot unavailable; running on host: {exc}[/yellow]")
    return None


def _print_chroot_indicator_once(args: argparse.Namespace) -> None:
    if os.environ.get("DEVTOOL_CHROOT_INDICATOR", "1").strip().lower() in {"0", "false", "no", "off"}:
        return
    if getattr(args, "machine", "") or getattr(args, "json", False):
        return
    command = str(getattr(args, "command", "") or "")
    if command in {"hud", "android-hud-bridge", "termux-hud-bridge"}:
        return
    if bool(getattr(args, "_devtool_termux_host_chroot_backend", False)):
        chroot_name = str(getattr(args, "_devtool_chroot_name", "") or getattr(args, "chroot", "androidos"))
        console.print(f"[magenta]Android chroot executor[/magenta] [dim]{chroot_name}[/dim] [green]host UI[/green]")
        return
    if os.environ.get("DEVTOOL_INSIDE_TERMUX_CHROOT") == "1":
        chroot_name = os.environ.get("DEVTOOL_CHROOT_CONTEXT", "androidos")
        console.print(f"[magenta]Android chroot[/magenta] [dim]{chroot_name}[/dim]")


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    _normalize_command_aliases(args)
    _normalize_tasker_args(args)
    _validate_apply_args(args, parser)
    effective_payload, copy_mode = _validate_output_flags(args, parser)
    auto_chroot_rc = _maybe_prepare_termux_auto_chroot(raw_argv, args)
    if auto_chroot_rc is not None:
        return auto_chroot_rc
    _print_chroot_indicator_once(args)

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

    if args.command == "bundle":
        return _run_bundle_command(args)

    if _is_artifact_validation_args(args):
        config = _load_command_config(args)
        return _run_artifact_validation_command(config, args, as_json=effective_payload == "json")

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

    if args.command == "hud":
        from .hud import run_hud

        return run_hud(args)

    if args.command in {"android-hud-bridge", "termux-hud-bridge"}:
        from .hud import default_android_bridge_dir, run_hud

        state_dir = Path(args.state_dir).expanduser() if args.state_dir else default_android_bridge_dir()
        hud_args = argparse.Namespace(
            tasker_dir=str(state_dir),
            mode=str(args.mode),
            host="127.0.0.1",
            port=8765,
            poll_interval=float(args.poll_interval),
            recent_events=int(args.recent_events),
            notify_id=str(args.notify_id),
            studio_notify=False,
            once=bool(args.once),
            follow_active=bool(args.follow_active),
        )
        return run_hud(hud_args)

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

    stale_transaction = find_active_transaction(config.repo_root)
    if stale_transaction is not None and args.command not in {"history"}:
        results = []
        raw_output = ""
        rollback_result, rollback_raw = _run_transaction_rollback(
            stale_transaction,
            reason="previous devtool apply appears to have been interrupted or killed",
            tasker_progress=tasker_progress,
            log_path=log_path,
        )
        # Force a failed command result even when rollback succeeds so callers know
        # their requested command was not executed and must be rerun.
        if rollback_result.returncode == 0:
            rollback_result.returncode = 1
            rollback_result.failure_kind = "stale_apply_rolled_back"
            rollback_result.output += "\nRequested command was not executed. Re-run it after reviewing the rollback."
        results.append(rollback_result)
        raw_output = rollback_raw
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
        return 1

    if args.command == "batch":
        destructive_batch = any(step.split("=", 1)[0] in {"apply-overlay", "ao", "apply-overlays", "aos", "apply-snapshot", "as", "commit", "ci", "tag"} for step in args.steps)
        if destructive_batch:
            _ensure_safe_repo(config.repo_root, force=args.force, yes=args.yes, command=args.command)

    if args.command in APPLY_COMMANDS:
        results, raw_output = _run_apply(config, args, args.command, args.artifact, log_path=log_path, tasker_progress=tasker_progress)
        transaction_path = _apply_transaction_path(results)
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
        if transaction_path and _has_failed_after_apply(results):
            rollback_result, rollback_raw = _run_transaction_rollback(
                transaction_path,
                reason="validation/build/test failed after artifact apply",
                tasker_progress=tasker_progress,
                log_path=log_path,
            )
            results.append(rollback_result)
            raw_output += "\n\n" + rollback_raw
        results, raw_output = _maybe_commit_after_apply(config, args, results, raw_output, log_path=log_path)
        if transaction_path:
            if any(result.returncode != 0 for result in results):
                # A commit failure after validation still means the apply transaction is unsafe.
                if not any(result.name == "apply-rollback" for result in results):
                    rollback_result, rollback_raw = _run_transaction_rollback(
                        transaction_path,
                        reason="post-apply command failed",
                        tasker_progress=tasker_progress,
                        log_path=log_path,
                    )
                    results.append(rollback_result)
                    raw_output += "\n\n" + rollback_raw
            else:
                mark_transaction_completed(transaction_path)
        if not getattr(args, "dry_run", False) and not getattr(args, "keep_artifact", False):
            cleanup_result = _delete_original_artifact_after_apply(
                results,
                artifact=getattr(args, "artifact", ""),
                delete_on_failure=True,
            )
            if cleanup_result is not None:
                results.append(cleanup_result)
                raw_output += "\n\n$ " + " ".join(cleanup_result.argv) + "\n" + cleanup_result.output
        if not getattr(args, "_devtool_history_recorded", False):
            _record_apply_history(config, args, results)
            args._devtool_history_recorded = True
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
        specs = _maybe_apply_test_no_build_flag(specs, args, parser)
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
