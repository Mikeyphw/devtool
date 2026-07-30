from __future__ import annotations

import argparse
import copy
from dataclasses import asdict, replace
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import resource
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from rich.prompt import Confirm

from .ai_prompt import render_ai_prompt, resolve_ai_prompt_options
from .apply_notifications import notify_apply_finished
from .artifacts import (
    SUPPORTED_ARCHIVE_SUFFIXES,
    append_history,
    artifact_commit_policy,
    artifact_cleanup_paths,
    artifact_solution,
    artifact_target,
    artifact_validation_policy,
    artifact_workspaces,
    create_overlay_bundle,
    diff_artifact,
    format_preview,
    is_overlay_bundle,
    materialize_overlay_bundle_entries,
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
from .cache_cleanup import cleanup_devtool_caches
from .artifact_validation import run_artifact_validation_script
from .artifact_inspection import inspect_artifact
from .detect import detect_repo, render_detect_text
from .execution import apply_matrix
from .execution_environments import (
    AUTO as EXECUTION_AUTO,
    CHROOT as EXECUTION_CHROOT,
    NATIVE_TERMUX as EXECUTION_NATIVE_TERMUX,
    EXECUTION_ENVIRONMENTS,
    assert_chroot_path_safe,
    detect_current_environment,
    inside_chroot,
    preflight_execution,
    resolve_execution_plan,
)
from .handoff import render_handoff
from .watch import watch_loop
from .clipboard import copy_text
from .console_utils import create_console, resolve_cli_path
from .completion import ZSH_COMPLETION
from .config import (
    detect_project_kind,
    discover_dotnet_candidates,
    global_config_doctor_entries,
    global_config_path,
    load_global_config,
    load_project_config,
    minimal_global_config_template,
    resolve_config_dict,
)
from .log_commands import choose_active_log, format_log_entries, list_log_entries, open_log, resolve_log_path, tail_log
from .logstore import latest_log, new_log_path
from .models import CommandSpec, Issue, OperationResult, ProjectConfig
from .repair_apply import apply_auto_source_repairs, restore_source_backups
from .repair_policy import repair_summary_lines_from_payload, repair_summary_lines_from_results
from .runners import apply_overlay, apply_overlay_bundle, apply_snapshot, add_no_build_to_dotnet_test_specs, build_specs, build_validation_specs, dotnet_build_server_shutdown_enabled, get_project_runner, git_commit, run_specs, runner_capabilities, shutdown_dotnet_build_server, write_template_config
from .summary import build_json_summary, build_runner_json, render_artifact_manifest, render_status_table, render_summary, summarize_failure_output
from .tasker import DEFAULT_NOTIFICATION_ID, DEFAULT_TASKER_DIR, TaskerProgressSink, build_tasker_sink, hud_disabled_by_environment, start_auto_hud
from .toolchains import apply_toolchain_wrapper, toolchain_doctor_rows
from .transactions import (
    abandon_transaction,
    devtool_xdg_dir,
    find_active_transaction,
    mark_transaction_completed,
    mark_transaction_pending_validation,
    mark_transaction_validated,
    mark_transaction_validation_blocked,
    mark_transaction_validation_running,
    read_transaction,
    rollback_transaction,
    transaction_pending_validation,
    transaction_root,
)
from .validation_evidence import preserve_validation_evidence
from .validation_sessions import (
    ValidationSession,
    ValidationSessionError,
    ValidationControlRequestError,
    accept_plan_change as accept_validation_plan_change,
    annotate_specs as annotate_validation_specs,
    completion_result as validation_completion_result,
    create_session as create_validation_session,
    guard_reason as validation_guard_reason,
    load_active_session,
    load_session as load_validation_session,
    mark_aborted as mark_validation_aborted,
    mark_completed as mark_validation_completed,
    mark_failure_terminal as mark_validation_failure_terminal,
    mark_paused as mark_validation_paused,
    mark_validated_uncommitted as mark_validation_validated_uncommitted,
    heartbeat_phase as heartbeat_validation_phase,
    input_fingerprints as validation_input_fingerprints,
    update_phase_progress as update_validation_phase_progress,
    read_control_request as read_validation_control_request,
    write_control_request as write_validation_control_request,
    clear_control_request as clear_validation_control_request,
    read_events as read_validation_events,
    append_event as append_validation_event,
    mark_phase_running,
    parse_duration_seconds,
    pause_result as validation_pause_result,
    reconcile_inputs as reconcile_validation_inputs,
    record_phase_result,
    recover_interrupted_phases as recover_interrupted_validation_phases,
    reset_all as reset_all_validation,
    reset_phase as reset_validation_phase,
    retry_failed as retry_failed_validation,
    save_session as save_validation_session,
    select_phase_keys,
    required_checkpoint_satisfied as validation_checkpoint_satisfied,
    session_operation_results,
    spec_map as validation_spec_map,
    status_payload as validation_status_payload,
    status_text as validation_status_text,
    checkpoint_text as validation_checkpoint_text,
    sync_plan as sync_validation_plan,
)
from .validation_reports import (
    finalization_issues as validation_finalization_issues,
    load_validation_report,
    report_path as validation_report_path,
    write_validation_report,
)
from .validation_control import (
    EXIT_CONTROL_UNAVAILABLE as VALIDATION_EXIT_CONTROL_UNAVAILABLE,
    EXIT_INVALID_REQUEST as VALIDATION_EXIT_INVALID_REQUEST,
    EXIT_OK as VALIDATION_EXIT_OK,
    EXIT_RECOVERY_REQUIRED as VALIDATION_EXIT_RECOVERY_REQUIRED,
    EXIT_SESSION_BLOCKED as VALIDATION_EXIT_SESSION_BLOCKED,
    EXIT_STALE_REVISION as VALIDATION_EXIT_STALE_REVISION,
    ValidationControlError,
    error_response as validation_control_error_response,
    exit_code_for_session as validation_control_exit_code,
    plan_payload as validation_plan_payload,
    require_revision as require_validation_revision,
    response as validation_control_response,
    reason_for_session as validation_control_reason,
    status_api_payload as validation_status_api_payload,
)
from .single_run import SingleRunLock

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
    "restore", "rs", "build", "b", "test", "t", "lint", "validate", "v", "validate-all",
    "wine-validate", "wv", "start-validation", "sv", "resume-validation", "rv",
    "validation-finalize", "vf", "validation-abort", "va", "validation-recover", "vrec",
    "validation-reset", "vreset", "validation-accept-plan-change", "vapc",
    "validation-plan", "vplan", "validation-pause", "vpause", "validation-stop", "vstop",
    "validation-events", "vevents", "validation-report", "vreport",
    "run", "rn", "package", "pkg", "clean", "cl",
    "apply-overlay", "ao", "apply-overlays", "aos", "apply-snapshot", "as", "batch", "commit", "ci", "tag",
}

RUNNER_PASSTHROUGH_COMMANDS = {
    "restore", "rs", "build", "b", "test", "t", "lint", "clean", "cl",
}

GLOBAL_OPTIONS_WITH_VALUE = {
    "-r",
    "--repo",
    "-c",
    "--config",
    "--target",
    "--variant",
    "--solution",
    "--project",
    "--project-type",
    "--performance-file",
    "--machine-file",
    "--copy-via",
    "--theme",
    "--stdout-payload",
    "--hud-mode",
    "--chroot",
    "--execution-environment",
}


def _runner_passthrough_command(raw_args: list[str], separator: int) -> str:
    index = 0
    while index < separator:
        token = raw_args[index]
        if token in RUNNER_PASSTHROUGH_COMMANDS:
            return token
        if token in GLOBAL_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if any(token.startswith(option + "=") for option in GLOBAL_OPTIONS_WITH_VALUE if option.startswith("--")):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token
    return ""


def split_runner_passthrough_args(raw_args: list[str]) -> tuple[list[str], list[str]]:
    if "--" not in raw_args:
        return list(raw_args), []
    separator = raw_args.index("--")
    command = _runner_passthrough_command(raw_args, separator)
    if command not in RUNNER_PASSTHROUGH_COMMANDS:
        return list(raw_args), []
    return list(raw_args[:separator]), list(raw_args[separator + 1 :])


def _add_apply_validation_flags(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--validate", action="store_true", help="Run validation after applying the artifact; required artifact validation is enabled automatically")
    cmd.add_argument("--defer-validate", action="store_true", help="Apply now, preserve rollback state, and create a resumable validation session without running a phase")
    cmd.add_argument("--static-validate", action="store_true", help="Apply artifact and run only artifact/static validation scripts without Gradle validation phases")
    cmd.add_argument("--no-validate", action="store_true", help="Disable validation requested or required by the artifact manifest")
    cmd.add_argument("--test-profile", default="", help="Saved test profile used by artifact validation")
    _add_validation_test_selection_flags(cmd)
    cmd.add_argument("--validate-only", action="store_true", help="Skip apply and only run validation")
    _add_validation_skip_flags(cmd)
    _add_resumable_validation_flags(cmd)


def _add_apply_policy_flags(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--preview", action="store_true", help="Preview archive changes without applying")
    cmd.add_argument("--diff", action="store_true", help="Show a repo-relative unified diff of artifact changes")
    cmd.add_argument("--interactive", action="store_true", help="Show preview and ask before applying")
    cmd.add_argument("--no-manifest-check", action="store_true", help="Disable devtool artifact manifest validation")
    cmd.add_argument("--ignore-checksum", action="store_true", help="Skip SHA-256 checksum validation while retaining all other artifact manifest checks")
    cmd.add_argument("--keep-artifact", action="store_true", help="Keep the source artifact and sidecars after a successful apply (failures are always preserved by default)")
    cmd.add_argument("--delete-artifact-on-failure", action="store_true", help="Explicitly delete the source artifact after a failed apply (default: preserve it for retry and diagnosis)")
    cmd.add_argument("--no-rollback-on-failure", action="store_true", help="Disable rollback when an apply or its validation fails")
    cmd.add_argument("--no-apply-notification", action="store_true", help="Do not show the final apply notification/toast")
    cmd.add_argument("--commit", default="", help="Commit with this message after apply and optional validation")
    cmd.add_argument("--no-commit", action="store_true", help="Disable a commit requested by the artifact manifest")
    cmd.add_argument("--no-artifact-script", action="store_true", help="Skip embedded artifact validation scripts while keeping built-in validation")


def _add_apply_command_flags(cmd: argparse.ArgumentParser, *, name: str) -> None:
    if name == "apply-overlays":
        cmd.add_argument("artifact", nargs="+", help="Overlay bundle archive, or multiple overlay archives to bundle/apply transactionally")
    else:
        cmd.add_argument("artifact")
    _add_apply_validation_flags(cmd)
    _add_apply_policy_flags(cmd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devtool",
        description="Apply artifacts, validate projects, and produce reviewable handoff commits.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Common workflows:\n"
            "  devtool -r REPO validate\n"
            "  devtool -r REPO apply-overlay PATCH.zip --validate --commit MESSAGE\n"
            "  devtool -r REPO apply-overlays BUNDLE.zip --validate --commit MESSAGE\n"
            "\nMultiple overlays run sequentially: each overlay is applied, validated, and committed before the next starts."
        ),
    )

    project = parser.add_argument_group("Project selection")
    project.add_argument("-r", "--repo", default=None, help="Repository root")
    project.add_argument("-c", "--config", default=None, help="Config file path")
    project.add_argument("--target", default="", help="Named target/profile from .devtool.toml")
    project.add_argument("--variant", default="", help="Build/test variant within the selected target")
    project.add_argument("--solution", default="", help="Exact solution path for restore/build/test")
    project.add_argument("--project", default="", help="Exact project path for run/validation")
    project.add_argument(
        "--project-type",
        choices=["auto", "dotnet", "android", "gradle", "python", "node", "rust", "go", "maven", "cmake", "meson", "make", "command", "unknown"],
        default="auto",
        help="Override project detection for this run",
    )

    execution = parser.add_argument_group("Execution")
    execution.add_argument("-v", "--verbose", action="store_true", help="Stream full command output")
    execution.add_argument("-y", "--yes", action="store_true", help="Assume yes for confirmations")
    execution.add_argument("-f", "--force", action="store_true", help="Force dangerous actions")
    execution.add_argument("--dry-run", action="store_true", help="Print commands without running them")
    execution.add_argument("--no-build", action="store_true", default=argparse.SUPPRESS, help="For test: pass --no-build to dotnet test")
    execution.add_argument("-k", "--continue-on-error", action="store_true", help="Continue with the next independent step after an error")
    execution.add_argument("--fail-fast", action="store_true", help="Stop at the first failed step")
    execution.add_argument("--performance", action="store_true", help="Write a JSON performance report for this run")
    execution.add_argument("--performance-file", default="", help="Performance report path (default: devtool state directory)")

    output = parser.add_argument_group("Output and clipboard")
    output.add_argument("--stdout-payload", choices=["auto", "summary", "raw"], default="auto", help="Select stdout content: normal UI, concise summary, or raw command output")
    output.add_argument("--machine-file", default="", help="Write the machine envelope to this file")
    output.add_argument("-p", "--copy", action="store_true", help="Copy the selected run summary or prompt output to the clipboard")
    output.add_argument("--no-clipboard", action="store_true", help="Disable clipboard prompts and copy behavior")
    output.add_argument("--copy-via", choices=["auto", "socket", "fifo", "termux", "x11", "wayland"], default="auto", help="Prefer a clipboard backend family")
    output.add_argument("--theme", choices=["auto", "plain"], default="auto", help="Summary theme")

    hud = parser.add_argument_group("HUD and app bridge")
    hud.add_argument("--auto-hud", "--hud-auto", dest="auto_hud", action="store_true", default=None, help="Auto-start or reuse the selected HUD while progress is emitted (default)")
    hud.add_argument("--no-hud", dest="auto_hud", action="store_false", help="Do not publish live HUD progress for this command")
    hud.add_argument("--hud-mode", default="desktop", help="HUD backend: desktop, android-bridge, desktop-window (Qt 5), gtk-window (legacy), web, studio, or terminal")
    hud_presentation = hud.add_mutually_exclusive_group()
    hud_presentation.add_argument("--hud-overlay", action="store_true", help="On Android, also show the run in the user-enabled floating overlay HUD")
    hud_presentation.add_argument("--hud-no-overlay", action="store_true", help="On Android, keep the update in the app without opening the floating overlay")

    environment = parser.add_argument_group("Execution environment")
    exec_group = environment.add_mutually_exclusive_group()
    exec_group.add_argument("--host", action="store_true", help="Force command phases to run in devtool's current host environment")
    exec_group.add_argument("--chroot", default="", help="Run command phases inside a named chroot via chrootctl exec")
    exec_group.add_argument(
        "--execution-environment",
        choices=EXECUTION_ENVIRONMENTS,
        default=EXECUTION_AUTO,
        help="Execution backend: auto, native-termux, chroot, desktop-linux, android-device, or external-device",
    )
    environment.add_argument("--no-auto-chroot", action="store_true", help="On Termux, do not auto-route command phases through chrootctl")

    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, aliases in [
        ("restore", ["rs"]),
        ("build", ["b"]),
        ("test", ["t"]),
        ("lint", []),
        ("validate", ["v"]),
        ("validate-all", []),
        ("wine-validate", ["wv"]),
        ("run", ["rn"]),
        ("package", ["pkg"]),
        ("clean", ["cl"]),
        ("inspect", ["info"]),
        ("status", ["st"]),
        ("doctor", ["dr"]),
    ]:
        cmd = subparsers.add_parser(name, aliases=aliases)
        if name == "build":
            cmd.add_argument(
                "-o",
                "--artifact-output-dir",
                "--output-dir",
                dest="artifact_output_dir",
                default="",
                help="Copy produced Android/Gradle artifacts here (default: .devtool/build-artifacts)",
            )
            cmd.add_argument(
                "-n",
                "--artifact-output-name",
                "--output-name",
                dest="artifact_output_name",
                default="",
                help="Base filename for copied Android/Gradle artifacts",
            )
        if name == "doctor":
            cmd.add_argument("--fix", action="store_true", help="Create safe devtool state/cache directories")
        if name == "clean":
            cmd.add_argument("--caches", action="store_true", help="Also prune devtool-managed caches and transient state")
        if name == "test":
            cmd.add_argument("--no-build", action="store_true", default=argparse.SUPPRESS, help="Pass --no-build to dotnet test")
            cmd.add_argument("--list-tests", "--list", action="store_true", help="Discover and list tests without executing them")
            cmd.add_argument("--profile", default="", help="Saved test profile from .devtool.toml")
            cmd.add_argument("--select", action="append", default=[], help="Select a type, suite, tag, class, or name; repeatable")
            cmd.add_argument("--id", dest="test_ids", action="append", default=[], help="Select an exact framework test ID; repeatable")
            cmd.add_argument("--tag", action="append", default=[], help="Include tests with this tag/trait; repeatable")
            cmd.add_argument("--exclude-tag", action="append", default=[], help="Exclude tests with this tag/trait; repeatable")
            cmd.add_argument("--path", dest="test_paths", action="append", default=[], help="Select tests below a source path; repeatable")
            cmd.add_argument("--class", "--tests", dest="test_classes", action="append", default=[], help="Select a test class; repeatable")
            cmd.add_argument("--method", dest="test_methods", action="append", default=[], help="Select a test method; repeatable")
            cmd.add_argument("--filter", dest="test_filter", default="", help="Framework-native test filter")
            cmd.add_argument("--task", dest="test_task", default="", help="Gradle test task override")
            cmd.add_argument("--failed", action="store_true", help="Rerun tests that failed in the previous structured run")
            cmd.add_argument("--changed", action="store_true", help="Select tests related to changed files")
            cmd.add_argument("--changed-from", default="HEAD", help="Git base for --changed")
            cmd.add_argument("--instrumented", action="store_true", help="Run Android instrumentation tests on a device")
        if name in {"validate", "validate-all", "wine-validate"}:
            _add_validation_skip_flags(cmd)
            if name == "validate":
                cmd.add_argument("validate_scope", nargs="?", help="Optional artifact validation scope: overlay or overlays")
                cmd.add_argument("validate_artifact", nargs="?", help="Artifact archive to validate when scope is overlay/overlays")
                cmd.add_argument("--fail-on-conflict", action="store_true", help="For overlay bundles, fail if multiple nested overlays write the same file")
                cmd.add_argument("--ignore-checksum", action="store_true", help="Skip SHA-256 checksum validation while retaining all other artifact manifest checks")
            cmd.add_argument("--watch", action="store_true", help="Rerun validation when files change")
            cmd.add_argument("--watch-interval", type=float, default=1.5, help="Seconds between watch polls")
            cmd.add_argument("--matrix", default="", help="Comma-separated runtime IDs, target names, or variants to validate")
            cmd.add_argument("--matrix-mode", choices=["rid", "target", "variant"], default="rid")
            cmd.add_argument("--test-profile", default="", help="Saved test profile used by validation")
            _add_validation_test_selection_flags(cmd)
            if name == "validate":
                _add_resumable_validation_flags(cmd)

    for name, aliases, mode in [
        ("start-validation", ["sv"], "start"),
        ("resume-validation", ["rv"], "resume"),
    ]:
        cmd = subparsers.add_parser(name, aliases=aliases, help=f"{mode.title()} a persistent sequential validation session")
        cmd.set_defaults(_devtool_validation_mode=mode)
        _add_validation_skip_flags(cmd)
        _add_validation_test_selection_flags(cmd)
        cmd.add_argument("--test-profile", default="", help="Saved test profile used by validation")
        cmd.add_argument("--matrix", default="", help="Comma-separated runtime IDs, targets, or variants")
        cmd.add_argument("--matrix-mode", choices=["rid", "target", "variant"], default="rid")
        _add_resumable_validation_flags(cmd)

    ai_prompt = subparsers.add_parser("ai-prompt", aliases=["ap"])
    ai_prompt.add_argument("--repo-name", required=True, help="Human-friendly repo name for the generated prompt")
    ai_prompt.add_argument("--project-type", choices=["auto", "dotnet", "android", "gradle", "python", "node", "rust", "go", "maven", "cmake", "meson", "make", "command", "unknown"], default="auto")
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
        _add_apply_command_flags(cmd, name=name)

    apply_cmd = subparsers.add_parser("apply", help="Apply an overlay, overlay bundle, or snapshot")
    apply_sub = apply_cmd.add_subparsers(dest="apply_command", required=True)
    for sub_name, command_name in [("overlay", "apply-overlay"), ("overlays", "apply-overlays"), ("snapshot", "apply-snapshot")]:
        sub = apply_sub.add_parser(sub_name)
        sub.set_defaults(normalized_command=command_name)
        _add_apply_command_flags(sub, name=command_name)

    validate_overlay_cmd = subparsers.add_parser("validate-overlay", aliases=["vo"])
    validate_overlay_cmd.add_argument("artifact")
    validate_overlay_cmd.add_argument("--fail-on-conflict", action="store_true", help="For bundles, fail if multiple nested overlays write the same file")
    validate_overlay_cmd.add_argument("--ignore-checksum", action="store_true", help="Skip SHA-256 checksum validation while retaining all other artifact manifest checks")
    validate_overlays_cmd = subparsers.add_parser("validate-overlays", aliases=["vos"])
    validate_overlays_cmd.add_argument("artifact", nargs="+", help="Overlay bundle archive, or multiple overlay archives to validate as a bundle")
    validate_overlays_cmd.add_argument("--fail-on-conflict", action="store_true", help="Fail if multiple nested overlays write the same file")
    validate_overlays_cmd.add_argument("--ignore-checksum", action="store_true", help="Skip SHA-256 checksum validation while retaining all other artifact manifest checks")

    inspect_artifact_cmd = subparsers.add_parser("inspect-artifact", aliases=["ia"], help="Print machine-readable artifact, bundle, inventory, validation, and diff metadata")
    inspect_artifact_cmd.add_argument("artifact")
    inspect_artifact_cmd.add_argument("--diff", action="store_true", help="Include a repository-relative unified diff")
    inspect_artifact_cmd.add_argument("--diff-context", type=int, default=3)
    inspect_artifact_cmd.add_argument("--max-diff-lines", type=int, default=4000)

    bundle_cmd = subparsers.add_parser("bundle")
    bundle_sub = bundle_cmd.add_subparsers(dest="bundle_command", required=True)
    bundle_overlays = bundle_sub.add_parser("overlays")
    bundle_overlays.add_argument("overlays", nargs="+")
    bundle_overlays.add_argument("--name", default="")
    bundle_overlays.add_argument("--description", default="")
    bundle_overlays.add_argument("--targets", default="", help="Comma-separated targets aligned with the overlay arguments; use '-' to infer")
    bundle_overlays.add_argument("--repository-name", default="", help="Repository identity stored in the bundle manifest")
    bundle_overlays.add_argument("--repository-remote", default="", help="Optional expected Git remote")
    bundle_overlays.add_argument("--repository-branch", default="", help="Optional expected Git branch")
    bundle_overlays.add_argument("--commit-message", default="", help="Embed one final atomic bundle commit message")
    bundle_overlays.add_argument("--validate", action="store_true", help="Require validation when the bundle is applied")
    bundle_overlays.add_argument("--validation-strategy", choices=["per-overlay", "after-all", "both"], default="per-overlay")
    bundle_overlays.add_argument("--validation-script", default="", help="Embed a disposable bundle validation script")
    bundle_overlays.add_argument("--validation-runner", choices=["bash", "sh", "python3"], default="bash")
    bundle_overlays.add_argument("--validation-timeout", type=int, default=900, help="Embedded validation script timeout in seconds")
    bundle_overlays.add_argument("-o", "--out", required=True, help="Output bundle archive; extension selects format")

    batch = subparsers.add_parser("batch")
    batch.add_argument("steps", nargs="+", help="Steps like validate or apply-overlay=/path.zip")

    init_cfg = subparsers.add_parser("init-config", aliases=["init"])
    init_cfg.add_argument("repo_root", nargs="?", default=".")
    init_cfg.add_argument("--force", action="store_true")

    logs_cmd = subparsers.add_parser("logs")
    logs_cmd.add_argument("--print", action="store_true", help="Print the latest log; kept for compatibility")
    logs_sub = logs_cmd.add_subparsers(dest="logs_command")
    logs_latest = logs_sub.add_parser("latest")
    logs_latest.add_argument("--print", action="store_true")
    logs_list = logs_sub.add_parser("list", aliases=["ls"])
    logs_list.add_argument("--limit", type=int, default=25)
    logs_open = logs_sub.add_parser("open", aliases=["show"])
    logs_open.add_argument("selector", nargs="?", default="", help="Log path, 'latest', or empty for interactive selection")
    logs_open.add_argument("--pager", choices=["auto", "bat", "cat"], default="auto")
    logs_open.add_argument("--fzf", dest="fzf", action="store_true", default=True)
    logs_open.add_argument("--no-fzf", dest="fzf", action="store_false")
    logs_tail = logs_sub.add_parser("tail", aliases=["follow"])
    logs_tail.add_argument("--lines", type=int, default=200)
    logs_tail.add_argument("--no-follow", action="store_true", help="Print the selected active log tail and exit")
    logs_tail.add_argument("--fzf", dest="fzf", action="store_true", default=True)
    logs_tail.add_argument("--no-fzf", dest="fzf", action="store_false")

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
    cfg_path = cfg_sub.add_parser("path", help="Print the repo or global config path")
    cfg_path.add_argument("--global", dest="global_config", action="store_true", help="Use the user global config path")
    cfg_show = cfg_sub.add_parser("show", help="Print repo or global config")
    cfg_show.add_argument("--global", dest="global_config", action="store_true", help="Show ~/.config/devtool/config.toml")
    cfg_defaults = cfg_sub.add_parser("defaults", help="Print a config template")
    cfg_defaults.add_argument("--global", dest="global_config", action="store_true", help="Print the sparse global config template")
    cfg_defaults.add_argument("--minimal", action="store_true", help="Prefer the minimal sparse template")
    cfg_init = cfg_sub.add_parser("init", help="Create a config file")
    cfg_init.add_argument("--global", dest="global_config", action="store_true", help="Create ~/.config/devtool/config.toml")
    cfg_init.add_argument("--minimal", action="store_true", help="Write the sparse global preference template")
    cfg_init.add_argument("--force", action="store_true", help="Overwrite an existing config")
    cfg_edit = cfg_sub.add_parser("edit", help="Open config in an editor")
    cfg_edit.add_argument("--global", dest="global_config", action="store_true", help="Edit ~/.config/devtool/config.toml")
    cfg_edit.add_argument("--editor", default="", help="Editor command; defaults to DEVTOOL_EDITOR, VISUAL, EDITOR, then micro")
    cfg_sub.add_parser("resolve")
    cfg_doctor = cfg_sub.add_parser("doctor")
    cfg_doctor.add_argument("--global", dest="global_config", action="store_true", help="Diagnose the user global config")

    detect_cmd = subparsers.add_parser("detect")
    detect_cmd.add_argument("--format", choices=["text", "json"], default="text")

    handoff_cmd = subparsers.add_parser("handoff")
    handoff_cmd.add_argument("-o", "--output", default="")

    hud_cmd = subparsers.add_parser("hud")
    hud_cmd.add_argument("--state-dir", dest="tasker_dir", default=DEFAULT_TASKER_DIR, help="Directory containing devtool live HUD state")
    hud_cmd.add_argument("--mode", choices=["auto", "android-gui", "android-bridge", "termux-gui", "terminal", "web", "studio", "desktop", "desktop-window", "gtk-window", "desktop-notify", "v3", "v4", "v5", "v6", "console", "dashboard", "all", "unix", "gnome"], default="auto", help="Run HUD renderer: Android app bridge, terminal, web, studio, or Unix desktop")
    hud_cmd.add_argument("--host", default="127.0.0.1", help="Host/IP for HUD web/studio modes")
    hud_cmd.add_argument("--port", type=int, default=8765, help="Port for HUD web/studio modes")
    hud_cmd.add_argument("--poll-interval", type=float, default=0.1, help="Seconds between HUD refreshes")
    hud_cmd.add_argument("--recent-events", type=int, default=8, help="Recent JSONL events to show/serve")
    hud_cmd.add_argument("--once", action="store_true", help="Render/update once and exit")
    hud_cmd.add_argument("--follow-active", action="store_true", help="Follow the global active HUD directory written by auto-HUD")
    hud_presentation_cmd = hud_cmd.add_mutually_exclusive_group()
    hud_presentation_cmd.add_argument("--overlay", action="store_true", help="Also publish runs to the enabled Android floating overlay HUD")
    hud_presentation_cmd.add_argument("--no-overlay", action="store_true", help="Do not open the floating overlay")

    android_bridge_cmd = subparsers.add_parser("android-hud-bridge", aliases=["termux-hud-bridge"], help="Display chroot/proot Android HUD bridge state from the Termux host")
    android_bridge_cmd.add_argument("--state-dir", default="", help="Shared bridge state directory; defaults to ~/.local/state/devtool-android-hud on Termux or /home/termux/.local/state/devtool-android-hud in a chroot")
    android_bridge_cmd.add_argument("--poll-interval", type=float, default=0.1, help="Seconds between HUD refreshes")
    android_bridge_cmd.add_argument("--recent-events", type=int, default=8, help="Recent events to show")
    android_bridge_cmd.add_argument("--once", action="store_true", help="Render/update once and exit")
    android_bridge_cmd.add_argument("--follow-active", action="store_true", default=True, help="Follow active HUD pointer in the shared bridge directory")
    android_bridge_cmd.add_argument("--no-follow-active", dest="follow_active", action="store_false", help="Read the shared bridge directory directly")
    android_bridge_presentation = android_bridge_cmd.add_mutually_exclusive_group()
    android_bridge_presentation.add_argument("--overlay", action="store_true", help="Also publish runs to the enabled Android floating overlay HUD")
    android_bridge_presentation.add_argument("--no-overlay", action="store_true", help="Do not open the floating overlay")

    history_cmd = subparsers.add_parser("history")
    history_cmd.add_argument("--limit", type=int, default=20)

    rollback_cmd = subparsers.add_parser("rollback")
    rollback_cmd.add_argument("commit", nargs="?", default="")
    rollback_cmd.add_argument("--last", action="store_true")

    validation_status = subparsers.add_parser("validation-status", aliases=["vs"], help="Show the active resumable validation session")
    validation_status.add_argument("--session", default="", help="Specific validation session ID")
    _add_validation_api_output_flags(validation_status)
    validation_status.add_argument("--events", action="store_true", help="Include durable validation events")
    validation_status.add_argument("--after-event", default="", help="Return events after this event ID")
    validation_status.add_argument("--event-limit", type=int, default=100, help="Maximum event records to return")

    validation_plan = subparsers.add_parser("validation-plan", aliases=["vplan"], help="Show the durable validation phase plan")
    validation_plan.add_argument("--session", default="", help="Specific validation session ID")
    _add_validation_api_output_flags(validation_plan)

    validation_pause = subparsers.add_parser("validation-pause", aliases=["vpause"], help="Pause a validation session at the next phase boundary")
    validation_pause.add_argument("--session", default="", help="Specific validation session ID")
    validation_pause.add_argument("--if-revision", type=int, default=-1, help="Apply only when the session revision matches")
    validation_pause.add_argument("--after-phase", action="store_true", default=True, help="Finish the current phase before pausing")
    validation_pause.add_argument("--cancel", dest="cancel_pause", action="store_true", help="Cancel a pending pause-after-phase request and keep validation running")
    _add_validation_api_output_flags(validation_pause)

    validation_stop = subparsers.add_parser("validation-stop", aliases=["vstop"], help="Stop the current validation phase and leave it retryable")
    validation_stop.add_argument("--session", default="", help="Specific validation session ID")
    validation_stop.add_argument("--if-revision", type=int, default=-1, help="Apply only when the session revision matches")
    _add_validation_api_output_flags(validation_stop)

    validation_events = subparsers.add_parser("validation-events", aliases=["vevents"], help="Read durable validation session events")
    validation_events.add_argument("--session", default="", help="Specific validation session ID")
    validation_events.add_argument("--after-event", default="", help="Return events after this event ID")
    validation_events.add_argument("--limit", type=int, default=100, help="Maximum event records to return")
    _add_validation_api_output_flags(validation_events)

    validation_report = subparsers.add_parser("validation-report", aliases=["vreport"], help="Show the durable validation report")
    validation_report.add_argument("--session", default="", help="Specific validation session ID")
    _add_validation_api_output_flags(validation_report)

    validation_abort = subparsers.add_parser("validation-abort", aliases=["va"], help="Abort a resumable validation session")
    validation_abort.add_argument("--session", default="", help="Specific validation session ID")
    validation_abort.add_argument("--keep-changes", action="store_true", help="Do not roll back a pending artifact transaction")
    validation_abort.add_argument("--rollback", action="store_true", help="Explicitly roll back a pending artifact transaction")
    validation_abort.add_argument("--if-revision", type=int, default=-1, help="Apply only when the session revision matches")
    _add_validation_api_output_flags(validation_abort)

    validation_finalize = subparsers.add_parser("validation-finalize", aliases=["vf"], help="Finalize a completed resumable validation session")
    validation_finalize.add_argument("--session", default="", help="Specific validation session ID")
    validation_finalize.add_argument("--if-revision", type=int, default=-1, help="Apply only when the session revision matches")
    _add_validation_api_output_flags(validation_finalize)

    validation_recover = subparsers.add_parser("validation-recover", aliases=["vrec"], help="Recover an interrupted validation attempt or resume a failed rollback")
    validation_recover.add_argument("--session", default="", help="Specific validation session ID")
    validation_recover.add_argument("--if-revision", type=int, default=-1, help="Apply only when the session revision matches")
    _add_validation_api_output_flags(validation_recover)

    validation_reset = subparsers.add_parser("validation-reset", aliases=["vreset"], help="Reset one phase and its dependents, or all validation progress")
    validation_reset.add_argument("--session", default="", help="Specific validation session ID")
    validation_reset.add_argument("--phase", default="", help="Reset this phase and every dependent phase")
    validation_reset.add_argument("--from-phase", default="", help="Alias for --phase")
    validation_reset.add_argument("--all", dest="reset_all", action="store_true", help="Reset all validation verdicts while retaining attempt evidence")
    validation_reset.add_argument("--if-revision", type=int, default=-1, help="Apply only when the session revision matches")
    _add_validation_api_output_flags(validation_reset)

    validation_accept = subparsers.add_parser("validation-accept-plan-change", aliases=["vapc"], help="Explicitly accept an incompatible validation plan change")
    validation_accept.add_argument("--session", default="", help="Specific validation session ID")
    validation_accept.add_argument("--reason", required=True, help="Audit reason for accepting the plan change")
    validation_accept.add_argument("--if-revision", type=int, default=-1, help="Apply only when the session revision matches")
    _add_validation_api_output_flags(validation_accept)

    subparsers.add_parser("targets", aliases=["tg"])

    control_center = subparsers.add_parser("control-center", aliases=["gui"], help="Launch the Qt 5 Devtool Control Center")
    control_center.add_argument("--binary", default="", help="Override the devtool-control-center executable")

    ipc_server = subparsers.add_parser("ipc-server", help="Run or start the local Control Center IPC broker")
    ipc_server.add_argument("--socket", default="", help="Override the Unix socket path")
    ipc_server.add_argument("--state-dir", default="", help="Load initial run snapshots from this state directory")
    ipc_server.add_argument("--foreground", action="store_true", help="Run the broker in the foreground")

    return parser


def _add_validation_skip_flags(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--skip-restore", action="store_true", help="Skip restore during validation")
    cmd.add_argument("--skip-build", action="store_true", help="Skip build during validation")
    cmd.add_argument("--skip-test", action="store_true", help="Skip test during validation")


def _add_validation_api_output_flags(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--json", action="store_true", help="Emit a stable validation-control JSON response")
    cmd.add_argument("--machine", choices=["pretty", "compact"], default="", help="Machine response formatting; implies --json")
    cmd.add_argument("--machine-file", default=argparse.SUPPRESS, help="Also write the validation-control JSON envelope to this file")


def _add_resumable_validation_flags(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--sequential", action="store_true", help="Use a persistent Android/Gradle validation session")
    cmd.add_argument("--resume", action="store_true", help="Resume the active validation session")
    cmd.add_argument("--restart-session", action="store_true", help="Replace an existing non-transactional validation session")
    cmd.add_argument("--retry-failed", action="store_true", help="Retry the cached failed phase and every dependent phase")
    selection = cmd.add_mutually_exclusive_group()
    selection.add_argument("--next", dest="validation_next", action="store_true", help="Run only the next pending phase (the default for `validate continue`)")
    selection.add_argument("--phase", action="append", default=[], help="Run a named phase and every still-pending prerequisite; repeat with --scope selected")
    selection.add_argument("--phase-only", default="", help="Run only one named pending phase without automatically including prerequisites")
    selection.add_argument("--through", default="", help="Run pending phases through this phase")
    selection.add_argument("--complete", dest="validation_complete", action="store_true", help="Run every remaining phase")
    cmd.add_argument("--from-phase", "--from", dest="from_phase", default="", help="Start this invocation at a named phase")
    cmd.add_argument("--pause-after", default="", help="Pause after this phase (alias for --through)")
    cmd.add_argument("--max-phases", type=int, default=0, help="Maximum phase boundaries to cross in this invocation")
    cmd.add_argument("--max-runtime", default="", help="Stop before the next phase after this runtime budget, such as 15m or 1h")
    cmd.add_argument("--require-complete", action="store_true", help="Return a failure code when validation pauses incomplete")
    cmd.add_argument("--stop-below-battery", type=int, default=0, metavar="PERCENT", help="Pause before a phase when battery is below this percentage")
    cmd.add_argument("--require-charging", action="store_true", help="Pause before a phase unless the Android device is charging")
    cmd.add_argument("--stop-above-temperature", type=float, default=0.0, metavar="CELSIUS", help="Pause before a phase when device temperature exceeds this value")
    cmd.add_argument("--stop-below-disk-mb", type=int, default=256, metavar="MIB", help="Pause a running validation phase when repository free space falls below this threshold; 0 disables")
    daemon = cmd.add_mutually_exclusive_group()
    daemon.add_argument("--stop-gradle-after-phase", action="store_true", help="Stop Gradle daemons after each completed phase")
    daemon.add_argument("--keep-gradle-daemon", action="store_true", help="Keep Gradle daemons available between phase invocations")
    cmd.add_argument("--resource-profile", "--validation-profile", "--profile", dest="validation_resource_profile", choices=["automatic", "android-low-power", "standard"], default="automatic", help="Validation resource profile; android-low-power uses one worker, two CPUs, no daemon, and low priority")
    cmd.add_argument("--max-infrastructure-retries", type=int, default=1, metavar="COUNT", help="Automatically retry environment/tooling interruptions this many times per phase")
    cmd.add_argument("--max-resource-retries", type=int, default=1, metavar="COUNT", help="Automatically retry corroborated resource interruptions this many times per phase, using the recommended safe profile when available")
    cmd.add_argument("--no-auto-retry", action="store_true", help="Disable automatic infrastructure and resource retries for this session")
    cmd.add_argument("--rollback-on-failure", action="store_true", help="Immediately roll back an attached apply transaction when a validation phase fails")
    cmd.add_argument(
        "--failure-action",
        choices=["pause", "rollback", "keep", "prompt"],
        default="",
        help="Action after a sequential validation failure; pause preserves the tree and rollback point",
    )
    cmd.add_argument("--keep-changes", action="store_true", help="When aborting through `validate abort`, keep the applied repository changes")
    cmd.add_argument("--session", default="", help="Specific validation session ID")
    cmd.add_argument("--all", dest="reset_all", action="store_true", help=argparse.SUPPRESS)
    cmd.add_argument("--reason", default="", help=argparse.SUPPRESS)
    cmd.add_argument("--scope", dest="validation_run_scope", choices=["next", "through", "selected", "remaining"], default="", help="Stable validation run scope")
    cmd.add_argument("--if-revision", type=int, default=-1, help="Apply the control request only when the session revision matches")
    cmd.add_argument("--after-phase", action="store_true", help="For validate pause, finish the active phase before pausing")
    cmd.add_argument("--cancel", dest="cancel_pause", action="store_true", help="For validate pause, cancel a pending pause request")
    cmd.add_argument("--rollback", action="store_true", help="For validate abort, explicitly roll back the attached transaction")
    cmd.add_argument("--events", action="store_true", help="Include durable events in validation status JSON")
    cmd.add_argument("--after-event", default="", help="Return events after this event ID")
    cmd.add_argument("--event-limit", "--limit", dest="event_limit", type=int, default=100, help="Maximum event records to return")
    _add_validation_api_output_flags(cmd)


def _add_validation_test_selection_flags(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--select", action="append", default=[], help="Select tests by type, suite, tag, class, or name during validation; repeatable")
    cmd.add_argument("--id", dest="test_ids", action="append", default=[], help="Select an exact framework test ID during validation; repeatable")
    cmd.add_argument("--tag", action="append", default=[], help="Include tests with this tag/trait/annotation during validation; repeatable")
    cmd.add_argument("--exclude-tag", action="append", default=[], help="Exclude tests with this tag/trait/annotation during validation; repeatable")
    cmd.add_argument("--path", dest="test_paths", action="append", default=[], help="Select tests below a source path or package during validation; repeatable")
    cmd.add_argument("--class", "--tests", dest="test_classes", action="append", default=[], help="Select a test class during validation; repeatable")
    cmd.add_argument("--method", dest="test_methods", action="append", default=[], help="Select a test method during validation; repeatable")
    cmd.add_argument("--filter", dest="test_filter", default="", help="Framework-native test filter used during validation")
    cmd.add_argument("--task", "--test-task", dest="test_task", action="append", default=[], help="Native test task/script/target override used during validation; repeatable where supported")
    cmd.add_argument("--failed", action="store_true", help="Rerun tests that failed in the previous structured run during validation")
    cmd.add_argument("--changed", action="store_true", help="Select tests related to changed files during validation")
    cmd.add_argument("--changed-from", default="HEAD", help="Git base for --changed during validation")
    cmd.add_argument("--instrumented", action="store_true", help="Run Android instrumentation tests during validation")



def _write_json_payload(path_text: str, json_payload: str) -> None:
    if not path_text:
        return
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_payload + ("" if json_payload.endswith("\n") else "\n"), encoding="utf-8")
    console.print(f"[green]Wrote JSON payload[/green] {path}")

def _maybe_copy(raw_output: str, summary_text: str, json_text: str, args: argparse.Namespace):
    if args.no_clipboard:
        return None

    import re as _re

    try:
        payload = json.loads(json_text)
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    payload_ok = bool(payload.get("ok") or payload.get("completion", {}).get("ok"))

    suppression_patterns = (
        (r"Artifact (?:not found|check failed before apply)|No matching artifact|Could not resolve artifact|FileNotFoundError|No such file or directory.*(?:\.zip|\.tar)", "artifact is missing"),
        (r"Requested command was not executed.*re-run|previous devtool apply appears to have been interrupted|Cleared stale devtool apply transaction|Interrupted by user|Interrupted during (?:artifact|overlay)|Child process was terminated", "run was interrupted or guarded by recovery"),
    )
    for pattern, reason in suppression_patterns:
        if not args.copy and not payload_ok and _re.search(pattern, raw_output, _re.IGNORECASE | _re.DOTALL):
            console.print(f"[yellow]Clipboard copy skipped because the {reason}.[/yellow]")
            return None

    repair_lines = repair_summary_lines_from_payload(payload)

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

    def _warning_details() -> tuple[list[str], int]:
        """Extract useful, deduplicated warnings from structured issues or output."""

        warning_lines: list[str] = []
        seen: set[str] = set()

        def _add_warning(text: str) -> None:
            cleaned = _re.sub(r"\s+", " ", str(text or "")).strip()
            if not cleaned:
                return
            key = cleaned.casefold()
            if key in seen:
                return
            seen.add(key)
            warning_lines.append(cleaned)

        def _format_issue_warning(issue: dict) -> str:
            code = str(issue.get("code") or "").strip()
            message = str(issue.get("message") or "").strip()
            file_text = str(issue.get("file") or "").strip()
            line = issue.get("line")
            location = ""
            if file_text and file_text != "-":
                location = file_text
                if line not in {None, ""}:
                    location += f":{line}"
            prefix = f"{location}: " if location else ""
            code_text = f"{code}: " if code else ""
            return f"{prefix}{code_text}{message or 'Warning'}"

        for result in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
            if not isinstance(result, dict):
                continue
            issues = result.get("issues")
            if not isinstance(issues, list):
                continue
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                if str(issue.get("severity") or "").lower() != "warning":
                    continue
                _add_warning(_format_issue_warning(issue))

        if warning_lines:
            total_unique = len(warning_lines)
            return warning_lines, total_unique

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

            _add_warning(cleaned)

        total_unique = len(warning_lines)
        return warning_lines, total_unique

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
    action_records: list[tuple[str, str, str, float]] = []
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
        action_records.append((name, status, description, duration))

        if status == "ok":
            action_lines.append(f"- {description} ({duration:.2f}s)")
        elif status == "skipped":
            action_lines.append(f"- {description}: skipped")
        else:
            action_lines.append(f"- {description}: failed ({duration:.2f}s)")

    test_totals = _test_totals()
    commit_hash = _commit_hash()

    overlay_summaries = payload.get("overlays") if isinstance(payload, dict) else None
    success_lines = ["Devtool completed successfully.", ""]

    if isinstance(overlay_summaries, list) and overlay_summaries:
        valid_overlays = [item for item in overlay_summaries if isinstance(item, dict)]
        if len(valid_overlays) == 1:
            first = valid_overlays[0]
            first_label = str(first.get("label") or "Overlay")
            first_target = str(first.get("target") or "")
            success_lines.append("Applied overlay: " + first_label + (f" [{first_target}]" if first_target else ""))
        else:
            success_lines.append(f"Applied overlays: {len(valid_overlays)}")
        for overlay in valid_overlays:
            index = int(overlay.get("index") or 0)
            total = int(overlay.get("total") or len(valid_overlays))
            label_text = str(overlay.get("label") or f"Overlay {index}")
            target_text = str(overlay.get("target") or "")
            artifact_text = str(overlay.get("artifact") or "")
            success_lines.extend(("", f"Overlay {index}/{total}: {label_text}" + (f" [{target_text}]" if target_text else "")))
            if artifact_text and artifact_text != label_text:
                success_lines.append(f"- Artifact: {artifact_text}")
            tests_data = overlay.get("tests")
            if isinstance(tests_data, dict):
                success_lines.append(
                    "- Tests: "
                    f"{int(tests_data.get('passed') or 0)} passed, "
                    f"{int(tests_data.get('failed') or 0)} failed, "
                    f"{int(tests_data.get('skipped') or 0)} skipped "
                    f"({int(tests_data.get('total') or 0)} total)"
                )
            success_lines.append(
                f"- Diagnostics: {int(overlay.get('warnings') or 0)} warnings, "
                f"{int(overlay.get('errors') or 0)} errors"
            )
            if overlay.get("commit_hash"):
                success_lines.append(f"- Git commit: {overlay['commit_hash']}")
            success_lines.append(f"- Duration: {float(overlay.get('duration_seconds') or 0):.2f}s")
        success_lines.extend(("", "Overall actions:"))
        success_lines.extend(action_lines)
        success_lines.append("")
    else:
        success_lines.extend(("Actions completed:", *action_lines, ""))

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
        else:
            success_lines.append(
                f"- {warnings} warning{'s' if warnings != 1 else ''} reported; "
                "individual warning text was not available in captured output."
            )

    if commit_hash:
        success_lines.append(f"Git commit: {commit_hash}")

    if total_duration > 0:
        success_lines.append(f"Total action time: {total_duration:.2f}s")

    if repair_lines:
        success_lines.extend(("", *repair_lines))

    success_summary = "\n".join(success_lines).strip()

    def _failure_summary() -> str:
        terse_names = {
            "apply-overlay": "overlay apply",
            "apply-overlays": "bundle apply",
            "apply-snapshot": "snapshot apply",
            "restore": "restore",
            "build": "build",
            "test": "tests",
            "wine-test": "Wine tests",
            "package": "package",
            "run": "run",
            "git-commit": "commit",
            "artifact-cleanup": "artifact cleanup",
            "rollback": "rollback",
            "apply-rollback": "rollback",
        }

        successful_steps: list[str] = []
        failed_steps: list[str] = []
        recovery_steps: list[str] = []
        for name, status, _description, _duration in action_records:
            label_text = terse_names.get(name, name.replace("-", " ").replace("_", " "))
            if name in {"rollback", "artifact-cleanup"}:
                if status == "ok":
                    recovery_steps.append(f"{label_text} succeeded")
                elif status == "failed":
                    recovery_steps.append(f"{label_text} failed")
                continue
            if status == "ok":
                successful_steps.append(label_text)
            elif status == "failed":
                failed_steps.append(label_text)

        # Some wrappers perform rollback and artifact cleanup outside the normal
        # operation table. Detect those outcomes without copying their verbose
        # restored/removed path inventories.
        if _re.search(r"Rolled back (?:devtool apply transaction|changes)", raw_output, _re.IGNORECASE):
            if not any(step.startswith("rollback ") for step in recovery_steps):
                recovery_steps.append("rollback succeeded")
        if _re.search(r"Deleted original artifact\(s\) after failed apply|removed:\s+.*\.(?:zip|tar)", raw_output, _re.IGNORECASE):
            if not any(step.startswith("artifact cleanup ") for step in recovery_steps):
                recovery_steps.append("artifact cleanup succeeded")

        def _error_details() -> tuple[list[str], int]:
            error_lines: list[str] = []
            seen: set[str] = set()

            def _add_error(text: str) -> None:
                cleaned = _re.sub(r"\s+", " ", str(text or "")).strip()
                if not cleaned:
                    return
                key = cleaned.casefold()
                if key in seen:
                    return
                seen.add(key)
                error_lines.append(cleaned)

            def _format_issue_error(issue: dict) -> str:
                code = str(issue.get("code") or "").strip()
                message = str(issue.get("message") or "").strip()
                file_text = str(issue.get("file") or "").strip()
                line = issue.get("line")
                if code in {"GRADLE_TASK", "GRADLE_TEST", "UNITTEST", "PYTEST", "VSTEST"}:
                    return message or code
                location = ""
                if file_text and file_text != "-":
                    location = file_text
                    if line not in {None, ""}:
                        location += f":{line}"
                prefix = f"{location}: " if location else ""
                code_text = f"{code}: " if code else ""
                return f"{prefix}{code_text}{message or 'Error'}"

            for result in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
                if not isinstance(result, dict):
                    continue
                issues = result.get("issues")
                if not isinstance(issues, list):
                    continue
                for issue in issues:
                    if not isinstance(issue, dict):
                        continue
                    if str(issue.get("severity") or "").lower() != "error":
                        continue
                    _add_error(_format_issue_error(issue))

            return error_lines, len(error_lines)

        def _overlay_failure_lines() -> list[str]:
            overlays = payload.get("overlays") if isinstance(payload, dict) else None
            if not isinstance(overlays, list):
                return []
            valid_overlays = [item for item in overlays if isinstance(item, dict)]
            if not valid_overlays:
                return []

            lines: list[str] = []
            if len(valid_overlays) == 1:
                overlay = valid_overlays[0]
                label_text = str(overlay.get("label") or "Overlay")
                target_text = str(overlay.get("target") or "")
                artifact_text = str(overlay.get("artifact") or "")
                lines.append("Overlay: " + label_text + (f" [{target_text}]" if target_text else ""))
                if artifact_text and artifact_text != label_text:
                    lines.append(f"- Artifact: {artifact_text}")
                lines.append(
                    f"- Diagnostics: {int(overlay.get('warnings') or 0)} warnings, "
                    f"{int(overlay.get('errors') or 0)} errors"
                )
                lines.append(f"- Duration: {float(overlay.get('duration_seconds') or 0):.2f}s")
                return lines

            lines.append(f"Overlays: {len(valid_overlays)}")
            for overlay in valid_overlays:
                index = int(overlay.get("index") or 0)
                total = int(overlay.get("total") or len(valid_overlays))
                label_text = str(overlay.get("label") or f"Overlay {index}")
                target_text = str(overlay.get("target") or "")
                artifact_text = str(overlay.get("artifact") or "")
                suffix = f" [{target_text}]" if target_text else ""
                lines.append(f"- Overlay {index}/{total}: {label_text}{suffix}")
                if artifact_text and artifact_text != label_text:
                    lines.append(f"  Artifact: {artifact_text}")
                lines.append(
                    f"  Diagnostics: {int(overlay.get('warnings') or 0)} warnings, "
                    f"{int(overlay.get('errors') or 0)} errors"
                )
            return lines

        failed_names = [name for name, status, _description, _duration in action_records if status == "failed"]
        command_hints = {
            "apply-overlay": ("devtool extract-overlay", "apply-overlay"),
            "apply-overlays": ("apply-overlays",),
            "apply-snapshot": ("apply-snapshot",),
            "restore": ("dotnet restore",),
            "build": ("dotnet build",),
            "test": ("dotnet test", "pytest", "unittest"),
            "wine-test": ("wine",),
            "package": ("dotnet pack", "python -m build", "npm pack"),
            "run": ("dotnet run",),
            "git-commit": ("git commit",),
        }

        command_blocks = [block.strip() for block in _re.split(r"(?m)(?=^\$ )", raw_output) if block.strip()]
        failed_block = ""
        for failed_name in failed_names:
            hints = command_hints.get(failed_name, (failed_name.replace("-", " "),))
            for block in command_blocks:
                first_line = block.splitlines()[0].casefold() if block.splitlines() else ""
                if any(hint.casefold() in first_line for hint in hints):
                    failed_block = block
                    break
            if failed_block:
                break

        if failed_block:
            failure_details = summarize_failure_output(failed_block, max_lines=80, max_chars=12000)
            failure_details = "\n".join(
                line for line in failure_details.splitlines()
                if not _re.search(r"^\s*\S+(?:\.\S+)?\s+->\s+\S+", line)
            ).strip()
            failed_command = failed_block.splitlines()[0].strip() if failed_block.splitlines() else ""
            if failed_command.startswith("$ ") and failed_command not in failure_details:
                failure_details = (failed_command + ("\n" + failure_details if failure_details else "")).strip()
        else:
            failure_details = summarize_failure_output(raw_output, max_lines=80, max_chars=12000) or summary_text.strip() or json_text.strip()

        lines = ["Devtool failed."]
        overlay_lines = _overlay_failure_lines()
        if overlay_lines:
            lines.extend(("", *overlay_lines))
        error_details, unique_error_count = _error_details()
        if error_details:
            lines.extend(("", "Errors:"))
            lines.extend(f"- {error}" for error in error_details[:12])
            if unique_error_count > 12:
                lines.append(f"- ... {unique_error_count - 12} more error(s)")
        if successful_steps:
            lines.extend(("", "Succeeded: " + ", ".join(successful_steps)))
        if failed_steps:
            lines.append("Failed: " + ", ".join(failed_steps))
        if recovery_steps:
            lines.append("Recovery: " + "; ".join(recovery_steps))
        if failure_details:
            if repair_lines:
                lines.extend(("", *repair_lines))
            lines.extend(("", "Failure details:", failure_details))
        return "\n".join(lines).strip()

    failure_summary = _failure_summary()

    # Automatic behavior:
    # - successful run: descriptive concise summary
    # - failed run: concise phase status plus focused diagnostics
    if succeeded:
        to_copy = success_summary
        label = "Successful run summary"
    else:
        to_copy = failure_summary
        label = "Failure summary"

    # Explicit clipboard modes retain their override behavior.
    if args.copy:
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
                f"[yellow]{label} was not copied to clipboard"
                f"{f' via {result.backend}' if result.backend else ''}: "
                f"{result.reason or 'Clipboard copy failed.'}[/yellow]"
            )
        return result

    if args.command not in INTERACTIVE_COPY_COMMANDS:
        return None
    if not console.is_interactive or not sys.stdin.isatty() or not to_copy.strip():
        return None

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
                f"[yellow]{label} was not copied to clipboard"
                f"{f' via {result.backend}' if result.backend else ''}: "
                f"{result.reason or 'Clipboard copy failed.'}[/yellow]"
            )
        return result
    return None

def _validate_output_flags(args: argparse.Namespace, parser: argparse.ArgumentParser) -> tuple[str, str]:
    explicit_stdout = args.stdout_payload
    if args.no_clipboard and args.copy:
        parser.error("--no-clipboard cannot be combined with --copy.")

    copy_mode = "raw" if args.copy else "auto"
    return explicit_stdout, copy_mode


def _normalize_live_progress_args(args: argparse.Namespace) -> None:
    command = str(getattr(args, "command", "") or "")
    env_overlay = os.environ.get("DEVTOOL_HUD_OVERLAY", "").strip().lower() in {"1", "true", "yes", "on"}
    if env_overlay and not bool(getattr(args, "hud_no_overlay", False)):
        args.hud_overlay = True
    # Live state is an internal HUD/app bridge facility now. Legacy Tasker and
    # standalone notification flags are intentionally not exposed by the CLI.
    args.tasker_progress = command in PROGRESS_CAPABLE_COMMANDS
    args.notify_progress = False
    args.tasker_dir = DEFAULT_TASKER_DIR
    args.notify_id = DEFAULT_NOTIFICATION_ID
    if hud_disabled_by_environment():
        # Hard environment override: command flags cannot re-enable any HUD
        # state publication, renderer, bridge, notification, or overlay.
        args.auto_hud = False
        args.tasker_progress = False
        args.hud_overlay = False
        args.hud_no_overlay = True
        return
    if getattr(args, "auto_hud", None) is None:
        args.auto_hud = bool(args.tasker_progress) and command != "hud"
    if args.auto_hud is False:
        args.tasker_progress = False


def _live_progress_for_args(args: argparse.Namespace, config: ProjectConfig | None = None, repo: Path | None = None) -> TaskerProgressSink | None:
    repo_root = (config.repo_root if config is not None else (repo or Path(args.repo or Path.cwd()))).resolve()
    target = str(getattr(args, "target", "") or getattr(args, "solution", "") or getattr(args, "project", "") or "")
    directory = resolve_cli_path(DEFAULT_TASKER_DIR)
    enabled = bool(getattr(args, "tasker_progress", False))
    hud_mode = str(getattr(args, "hud_mode", "desktop") or "desktop")
    if enabled and bool(getattr(args, "auto_hud", False)):
        try:
            from .hud import choose_hud_mode, default_android_bridge_dir
            chosen_hud_mode = choose_hud_mode(hud_mode)
            if chosen_hud_mode == "android-bridge" or os.environ.get("DEVTOOL_HUD_BRIDGE_DIR"):
                directory = default_android_bridge_dir()
        except Exception:
            pass

    sink = build_tasker_sink(
        enabled=enabled,
        directory=str(directory),
        command=str(getattr(args, "command", "devtool") or "devtool"),
        repo_root=repo_root,
        target=target,
        project_type=(config.kind if config is not None else str(getattr(args, "project_type", "auto") or "auto")),
        execution_environment=str(getattr(args, "execution_environment", "auto") or (config.execution_environment if config is not None else "auto")),
        notify_progress=False,
        notification_id=DEFAULT_NOTIFICATION_ID,
        run_id=str(getattr(args, "_devtool_run_id", "") or os.environ.get("DEVTOOL_RUN_ID", "")),
        overlay=bool(getattr(args, "hud_overlay", False)) and not bool(getattr(args, "hud_no_overlay", False)),
    )

    if sink is not None and enabled and bool(getattr(args, "auto_hud", False)):
        sink.prepare_hud()
        overlay_requested = bool(getattr(args, "hud_overlay", False)) and not bool(getattr(args, "hud_no_overlay", False))
        start_auto_hud(directory, mode=hud_mode, poll_interval=0.1, no_overlay=not overlay_requested)
    return sink


def _finish_live_progress(live_progress: TaskerProgressSink | None, *, totals: dict[str, int], results: list[OperationResult]) -> None:
    if live_progress is None:
        return
    live_progress.finished(
        ok=totals["failed"] == 0 and totals["errors"] == 0,
        totals=totals,
        results=results,
        notification=None,
    )


def _summary_text(results: list[OperationResult]) -> str:
    parts: list[str] = []
    current_group: tuple[int, str] | None = None
    for result in results:
        metadata = result.metadata or {}
        if metadata.get("overlay_index") is not None:
            index = int(metadata.get("overlay_index") or 0)
            total = int(metadata.get("overlay_total") or 0)
            label = str(metadata.get("overlay_label") or f"Overlay {index}")
            target = str(metadata.get("overlay_target") or "")
            group = (index, label)
            if group != current_group:
                if parts:
                    parts.append("")
                heading = f"Overlay {index}/{total}: {label}"
                if target:
                    heading += f" [{target}]"
                parts.append(heading)
                current_group = group
        elif current_group is not None:
            parts.append("")
            parts.append("Bundle/final actions")
            current_group = None

        status = "skipped" if result.skipped else ("ok" if result.returncode == 0 else "failed")
        indent = "  " if metadata.get("overlay_index") is not None else ""
        parts.append(f"{indent}{result.name}: {status} exit={result.returncode} time={result.duration_seconds:.2f}s")
        for issue in result.issues:
            location = issue.file if issue.line is None or issue.file in {"", "-"} else f"{issue.file}:{issue.line}"
            parts.append(f"{indent}  {issue.severity} {issue.command} {location} {issue.code} {issue.message}")
    repair_lines = repair_summary_lines_from_results(results)
    if repair_lines:
        if parts:
            parts.append("")
        parts.extend(repair_lines)
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



def _read_artifact_manifest(artifact: str) -> dict:
    try:
        data = read_artifact_manifest(artifact)
        return dict(data) if isinstance(data, dict) else {}
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

    if args.command in LEGACY_APPLY_VALIDATE_COMMANDS:
        manifest = _read_artifact_manifest(_single_artifact_arg(getattr(args, "artifact", "")))
        if not target_name:
            target_name = artifact_target(manifest)
        if not solution:
            solution = artifact_solution(manifest)
        if not project:
            project = manifest.get("run_project", "") or manifest.get("project", "")

    try:
        return load_project_config(
            args.repo,
            args.config,
            target_name=target_name,
            variant_name=str(getattr(args, "variant", "") or ""),
            solution_override=solution,
            project_override=project,
            kind_override=str(getattr(args, "project_type", "auto") or "auto"),
        )
    except SystemExit as exc:
        # An overlay may introduce both the named target and .devtool.toml. Allow
        # extraction to proceed with a repository-only config, then reload after
        # apply once the artifact-created configuration exists. Explicit CLI target
        # mistakes remain fatal.
        if (
            args.command in APPLY_COMMANDS
            and not getattr(args, "_devtool_cli_target_explicit", False)
            and "Unknown target" in str(exc)
        ):
            return load_project_config(
                args.repo,
                args.config,
                target_name="",
                variant_name=str(getattr(args, "variant", "") or ""),
                solution_override=solution,
                project_override=project,
                kind_override=str(getattr(args, "project_type", "auto") or "auto"),
            )
        raise


def _apply_artifact_manifest_defaults(args: argparse.Namespace) -> None:
    """Apply manifest defaults while preserving explicit CLI precedence."""
    if getattr(args, "command", "") not in APPLY_COMMANDS:
        return
    artifacts = _artifact_arg_list(getattr(args, "artifact", ""))
    if len(artifacts) != 1:
        return
    manifest = _read_artifact_manifest(artifacts[0])
    args._devtool_declared_manifest = manifest
    if not manifest:
        return

    preview_only = bool(getattr(args, "preview", False) or (getattr(args, "diff", False) and not getattr(args, "interactive", False)))

    if not getattr(args, "target", ""):
        args.target = artifact_target(manifest)
    if not getattr(args, "solution", ""):
        args.solution = artifact_solution(manifest)

    validation = artifact_validation_policy(manifest)
    if validation and not getattr(args, "no_validate", False) and not preview_only:
        if (
            not getattr(args, "defer_validate", False)
            and bool(validation.get("required", False))
            and not getattr(args, "validate_only", False)
        ):
            args.validate = True
        if not bool(validation.get("restore", True)):
            args.skip_restore = True
        if not bool(validation.get("build", True)):
            args.skip_build = True
        if not bool(validation.get("test", True)):
            args.skip_test = True
    if getattr(args, "no_validate", False) or getattr(args, "defer_validate", False):
        args.validate = False

    enabled, message, strategy = artifact_commit_policy(manifest)
    args._devtool_artifact_commit_strategy = strategy
    if (
        enabled
        and not getattr(args, "no_commit", False)
        and not getattr(args, "validate_only", False)
        and not preview_only
        and not str(getattr(args, "commit", "") or "").strip()
    ):
        args.commit = message


def _rollback_on_failure_enabled(args: argparse.Namespace, policy: dict[str, object] | None = None) -> bool:
    if getattr(args, "no_rollback_on_failure", False):
        return False
    if policy is None:
        return True
    return bool(policy.get("rollback_on_failure", True))


def _validation_failure_action(
    args: argparse.Namespace,
    context: dict[str, object] | None = None,
) -> str:
    """Resolve sequential/deferred failure policy without inheriting destructive defaults."""

    explicit = str(getattr(args, "failure_action", "") or "").strip().lower()
    if explicit:
        return explicit
    if bool(getattr(args, "rollback_on_failure", False)):
        return "rollback"
    if bool(getattr(args, "keep_changes", False)):
        return "keep"
    context = dict(context or {})
    configured = str(context.get("failure_action") or "").strip().lower()
    if configured in {"pause", "rollback", "keep", "prompt"}:
        return configured
    # The old boolean is honored only when it was explicitly preserved in the
    # durable context. New deferred sessions default to non-destructive pause.
    if bool(context.get("legacy_rollback_on_failure_explicit", False)):
        return "rollback" if bool(context.get("rollback_on_failure", False)) else "pause"
    return "pause"


def _resolve_prompted_failure_action(args: argparse.Namespace, action: str, *, phase_label: str) -> str:
    """Resolve ``prompt`` only in an interactive human CLI; machines fail safe to pause."""

    if action != "prompt":
        return action
    if not _validation_console_enabled(args) or not getattr(sys.stdin, "isatty", lambda: False)():
        return "pause"
    console.print(f"[bold yellow]Validation failed at {phase_label}.[/bold yellow]")
    if Confirm.ask("Roll back the applied changes now?", default=False, console=console):
        return "rollback"
    if Confirm.ask("Detach the rollback point and keep the applied changes without a validation verdict?", default=False, console=console):
        return "keep"
    console.print("[yellow]Validation paused; changes and rollback point were retained.[/yellow]")
    return "pause"


def _resolve_prompted_validation_retry(
    args: argparse.Namespace,
    *,
    phase_label: str,
    verdict: str,
    recommended_profile: str = "",
    memory_guard_invoked: bool = False,
) -> bool:
    """Ask once whether a retryable validation interruption should run again now."""

    if not _validation_console_enabled(args) or bool(getattr(args, "yes", False)):
        return False
    if not getattr(sys.stdin, "isatty", lambda: False)():
        return False
    if verdict not in {"resource-failed", "environment-failed", "timed-out"}:
        return False
    console.print(f"[bold yellow]{phase_label} stopped with {verdict.replace('-', ' ')}.[/bold yellow]")
    if memory_guard_invoked:
        return Confirm.ask(
            f"Retry {phase_label} now with memory guard disabled?",
            default=True,
            console=console,
        )
    suffix = (
        f" using {recommended_profile}"
        if recommended_profile and recommended_profile != "automatic"
        else ""
    )
    return Confirm.ask(f"Retry {phase_label} now{suffix}?", default=True, console=console)


def _attempt_invoked_memory_guard(attempt: object | None) -> bool:
    if attempt is None:
        return False
    failure_kind = str(getattr(attempt, "failure_kind", "") or "").strip().lower().replace("_", "-")
    if failure_kind in {"memory-guard", "resource-interrupted"}:
        text = str(getattr(attempt, "output_tail", "") or "").lower()
        evidence = "\n".join(str(item) for item in getattr(attempt, "classification_evidence", []) or []).lower()
        if "memory guard" in text or "memory guard" in evidence:
            return True
    issues = getattr(attempt, "issues", []) or []
    for issue in issues:
        if isinstance(issue, dict) and str(issue.get("code") or "").upper() == "GRADLE_MEMORY_GUARD":
            return True
    text = str(getattr(attempt, "output_tail", "") or "").lower()
    return "memory guard" in text or "devtool stopped validation after hard emergency floor" in text


def _argv_with_memory_guard_disabled(argv: list[str]) -> list[str]:
    updated: list[str] = []
    skip_next = False
    for value in argv:
        text = str(value)
        if skip_next:
            updated.append("0")
            skip_next = False
            continue
        if text == "--memory-guard-mb":
            updated.append(text)
            skip_next = True
            continue
        if text.startswith("--memory-guard-mb="):
            updated.append("--memory-guard-mb=0")
            continue
        updated.append(text)
    return updated


def _spec_with_memory_guard_disabled(spec: CommandSpec) -> CommandSpec:
    metadata = dict(spec.metadata)
    metadata["validation_memory_guard_mb"] = 0
    metadata["memory_guard_mb"] = 0
    metadata["validation_memory_guard_disabled_retry"] = True
    progress_argv = metadata.get("progress_argv")
    if isinstance(progress_argv, list):
        metadata["progress_argv"] = _argv_with_memory_guard_disabled([str(item) for item in progress_argv])
    return replace(
        spec,
        argv=_argv_with_memory_guard_disabled([str(item) for item in spec.argv]),
        metadata=metadata,
    )


def _normalize_command_aliases(args: argparse.Namespace) -> None:
    if getattr(args, "command", "") == "apply":
        args.command = str(getattr(args, "normalized_command", "") or "apply-overlay")
    command = str(getattr(args, "command", "") or "")
    if command in {"validate", "v"}:
        action = str(getattr(args, "validate_scope", "") or "").strip().lower()
        if action in {"start", "next", "run", "resume", "continue", "retry", "status", "checkpoint", "plan", "events", "report", "pause", "stop", "finalize", "abort", "recover", "reset", "accept-plan-change"}:
            args.validate_scope = ""
            if action == "start":
                args._devtool_validation_mode = "start"
                args._devtool_validation_api_action = "start"
                args.sequential = True
                args.restart_session = True
            elif action == "next":
                args._devtool_validation_mode = "resume"
                args._devtool_validation_api_action = "run"
                args.sequential = True
                args.resume = True
                args.validation_next = True
            elif action == "run":
                args._devtool_validation_mode = "resume"
                args._devtool_validation_api_action = "run"
                args.sequential = True
                args.resume = True
            elif action == "retry":
                args._devtool_validation_mode = "resume"
                args._devtool_validation_api_action = "retry"
                args.sequential = True
                args.resume = True
                args.retry_failed = True
            elif action == "resume":
                args._devtool_validation_mode = "resume"
                args._devtool_validation_api_action = "resume"
                args.sequential = True
                args.resume = True
            elif action == "continue":
                args._devtool_validation_mode = "resume"
                args._devtool_validation_api_action = "run"
                args.sequential = True
                args.resume = True
            elif action in {"status", "checkpoint"}:
                args.command = "validation-status"
                args._devtool_validation_api_action = "status"
                args._devtool_validation_checkpoint = action == "checkpoint"
            elif action == "plan":
                args.command = "validation-plan"
                args._devtool_validation_api_action = "plan"
            elif action == "events":
                args.command = "validation-events"
                args._devtool_validation_api_action = "events"
            elif action == "report":
                args.command = "validation-report"
                args._devtool_validation_api_action = "report"
            elif action == "pause":
                args.command = "validation-pause"
                args._devtool_validation_api_action = "pause"
            elif action == "stop":
                args.command = "validation-stop"
                args._devtool_validation_api_action = "stop"
            elif action == "finalize":
                args.command = "validation-finalize"
                args._devtool_validation_api_action = "finalize"
            elif action == "abort":
                args.command = "validation-abort"
                args._devtool_validation_api_action = "abort"
            elif action == "recover":
                args.command = "validation-recover"
                args._devtool_validation_api_action = "recover"
            elif action == "reset":
                args.command = "validation-reset"
                args._devtool_validation_api_action = "reset"
            elif action == "accept-plan-change":
                args.command = "validation-accept-plan-change"
                args._devtool_validation_api_action = "accept-plan-change"
            command = str(args.command)
        api_action = str(getattr(args, "_devtool_validation_api_action", "") or "")
        if api_action in {"start", "run", "resume", "retry"}:
            scope = str(getattr(args, "validation_run_scope", "") or "").strip().lower()
            if not scope:
                if bool(getattr(args, "validation_next", False)):
                    scope = "next"
                elif bool(getattr(args, "validation_complete", False)):
                    scope = "remaining"
                elif str(getattr(args, "through", "") or "").strip():
                    scope = "through"
                elif _validation_phase_values(args):
                    scope = "selected"
                else:
                    scope = "remaining" if api_action == "resume" and _validation_json_requested(args) else "next"
                args.validation_run_scope = scope
            if scope == "next":
                args.validation_next = True
                args.validation_complete = False
            elif scope == "remaining":
                args.validation_complete = True
                args.validation_next = False
            elif scope == "through":
                args.validation_next = False
                args.validation_complete = False
            elif scope == "selected":
                args.validation_next = False
                args.validation_complete = False
    if command in {"start-validation", "sv"}:
        args._devtool_validation_mode = "start"
        if _validation_json_requested(args):
            args._devtool_validation_api_action = "start"
        args.sequential = True
        args.restart_session = True
        args.command = "validate"
    elif command in {"resume-validation", "rv"}:
        args._devtool_validation_mode = "resume"
        if _validation_json_requested(args):
            args._devtool_validation_api_action = "resume"
        args.sequential = True
        args.resume = True
        args.command = "validate"


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
        api_action = str(getattr(args, "_devtool_validation_api_action", "") or "")
        run_scope = str(getattr(args, "validation_run_scope", "") or "")
        phases = _validation_phase_values(args)
        if api_action in {"start", "run", "resume", "retry"}:
            if run_scope == "through" and not str(getattr(args, "through", "") or "").strip():
                if not _validation_json_requested(args):
                    parser.error("validate run --scope through requires --through PHASE")
            if run_scope == "selected" and not phases:
                if not _validation_json_requested(args):
                    parser.error("validate run --scope selected requires one or more --phase PHASE values")
            if run_scope in {"next", "remaining"} and (phases or str(getattr(args, "through", "") or "").strip()):
                if not _validation_json_requested(args):
                    parser.error(f"validate run --scope {run_scope} cannot be combined with --phase or --through")
    if args.command not in APPLY_COMMANDS:
        return
    deferred = bool(getattr(args, "defer_validate", False))
    if _resumable_validation_requested(args) and not (getattr(args, "validate", False) or getattr(args, "validate_only", False) or deferred):
        parser.error("Sequential/resumable validation flags on apply require --validate, --validate-only, or --defer-validate.")
    if getattr(args, "no_validate", False) and (getattr(args, "validate", False) or getattr(args, "validate_only", False) or deferred):
        parser.error("--no-validate cannot be combined with --validate, --validate-only, or --defer-validate.")
    if deferred and (getattr(args, "validate", False) or getattr(args, "validate_only", False)):
        parser.error("--defer-validate cannot be combined with --validate or --validate-only.")
    if deferred and any(str(getattr(args, name, "") or "").strip() for name in ("phase", "phase_only", "through", "from_phase", "pause_after")):
        parser.error("Phase selection is not used during --defer-validate; apply first, then run `devtool validate continue ...`.")
    if getattr(args, "no_commit", False) and str(getattr(args, "commit", "") or "").strip():
        parser.error("--no-commit cannot be combined with --commit.")
    if getattr(args, "no_rollback_on_failure", False) and getattr(args, "rollback_on_failure", False):
        parser.error("--rollback-on-failure cannot be combined with --no-rollback-on-failure.")
    if (args.skip_restore or args.skip_build or args.skip_test) and not (args.validate or args.validate_only or deferred):
        parser.error("--skip-restore, --skip-build, and --skip-test require --validate, --validate-only, or --defer-validate.")
    if args.validate_only and args.validate:
        parser.error("--validate and --validate-only cannot be combined.")
    if args.validate_only and args.commit:
        parser.error("--validate-only cannot be combined with --commit.")
    manifest = dict(getattr(args, "_devtool_declared_manifest", {}) or {})
    if deferred and manifest and not bool(artifact_validation_policy(manifest).get("allow_deferred", True)):
        parser.error("This artifact explicitly forbids deferred validation.")
    preview_only = getattr(args, "preview", False) or (getattr(args, "diff", False) and not getattr(args, "interactive", False))
    if preview_only and (args.validate or args.validate_only or args.commit):
        parser.error("--preview/--diff cannot be combined with validation or commit flags unless --interactive is used with --diff.")


def _apply_artifact_validation_defaults(config: ProjectConfig, args: argparse.Namespace, manifest: dict) -> None:
    """Apply language-neutral validation defaults declared by an artifact.

    Explicit CLI choices always win. The defaults are copied into the same
    runner/test-selection model used by direct validation, so overlays do not
    need project-type-specific validation code.
    """
    policy = artifact_validation_policy(manifest)
    if not str(getattr(args, "test_profile", "") or "").strip():
        profile = str(policy.get("test_profile") or "").strip()
        if profile:
            args.test_profile = profile
    requested = str(getattr(args, "execution_environment", "auto") or "auto").strip().lower()
    environment = str(policy.get("execution_environment") or "").strip().lower()
    if requested == "auto" and environment:
        args.execution_environment = environment
    validation_options = config.runner_options.setdefault("validation", {})
    if policy:
        validation_options.update({
            key: value for key, value in policy.items()
            if key in {"test_profile", "execution_environment"} and value
        })
    declared_paths = sorted(_artifact_declared_paths(manifest))
    changed_paths_source = "artifact-inventory"
    if not declared_paths:
        preview = dict(getattr(args, "_devtool_artifact_preview", {}) or {})
        declared_paths = sorted({
            str(item.get("path") or "").replace("\\", "/").strip("/")
            for item in preview.get("changes", [])
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        })
        changed_paths_source = "apply-preview"
    if declared_paths:
        # Preserve the exact changed-file inventory for project runners.
        # Android validation uses it for phase selection and affected-module
        # linting, while root/shared Gradle changes retain the full fallback.
        validation_options["changed_paths"] = declared_paths
        validation_options["changed_paths_source"] = changed_paths_source


def _validation_skip_values(args: argparse.Namespace) -> tuple[bool, bool, bool]:
    return (
        bool(getattr(args, "skip_restore", False)),
        bool(getattr(args, "skip_build", False)),
        bool(getattr(args, "skip_test", False)),
    )


def _cli_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        values: list[str] = []
        for item in value:
            values.extend(_cli_string_list(item))
        return values
    text = str(value).strip()
    return [text] if text else []


def _apply_runner_cli_options(config: ProjectConfig, args: argparse.Namespace) -> None:
    if args.command in {"build", "b"} and config.kind in {"android", "gradle"}:
        artifacts = config.runner_options.setdefault("artifacts", {})
        if not isinstance(artifacts, dict):
            artifacts = {}
            config.runner_options["artifacts"] = artifacts
        output_dir = str(getattr(args, "artifact_output_dir", "") or "").strip()
        output_name = str(getattr(args, "artifact_output_name", "") or "").strip()
        if output_dir:
            artifacts["output_directory"] = output_dir
        elif not (artifacts.get("output_directory") or artifacts.get("output_dir")):
            artifacts["output_directory"] = ".devtool/build-artifacts"
        if output_name:
            artifacts["output_name"] = output_name

    passthrough = [str(value) for value in getattr(args, "_devtool_passthrough_args", []) if str(value)]
    if passthrough and config.kind in {"android", "gradle"}:
        gradle = config.runner_options.setdefault("gradle", {})
        if not isinstance(gradle, dict):
            gradle = {}
            config.runner_options["gradle"] = gradle
        existing = gradle.get("args", [])
        values = _cli_string_list(existing)
        values.extend(passthrough)
        gradle["args"] = values

    selection = dict(config.runner_options.get("test_selection", {}) or {})
    profile = str(getattr(args, "profile", "") or getattr(args, "test_profile", "") or "").strip()
    if not profile:
        validation = config.runner_options.get("validation", {})
        if isinstance(validation, dict) and args.command in {"validate", "v", "validate-all", "wine-validate", "wv", *APPLY_COMMANDS}:
            profile = str(validation.get("test_profile") or "").strip()
    if profile:
        selection["profile"] = profile
    mapping = {
        "selectors": "select",
        "ids": "test_ids",
        "tags": "tag",
        "exclude_tags": "exclude_tag",
        "paths": "test_paths",
        "classes": "test_classes",
        "methods": "test_methods",
    }
    for key, attr in mapping.items():
        values = getattr(args, attr, None)
        if values:
            selection[key] = list(values)
    filter_value = str(getattr(args, "test_filter", "") or "").strip()
    if filter_value:
        selection["filter"] = filter_value
    task_values = _cli_string_list(getattr(args, "test_task", None))
    if task_values:
        selection["tasks"] = task_values
        selection["task"] = task_values[0]
    changed_from = str(getattr(args, "changed_from", "") or "").strip()
    if changed_from and not (changed_from == "HEAD" and not getattr(args, "changed", False)):
        selection["changed_from"] = changed_from
    for key, attr in (("failed", "failed"), ("changed", "changed"), ("instrumented", "instrumented")):
        if bool(getattr(args, attr, False)):
            selection[key] = True
    if selection:
        config.runner_options["test_selection"] = selection


def _print_test_catalog(config: ProjectConfig, args: argparse.Namespace, *, as_json: bool) -> int:
    from .test_selection import discover_tests, resolve_test_selection, select_catalog
    catalog = discover_tests(config)
    selected = select_catalog(catalog, resolve_test_selection(config))
    if as_json:
        print(json.dumps({
            "project_type": config.kind,
            "target": config.selected_target,
            "variant": config.variant,
            "discovered": len(catalog),
            "selected": len(selected),
            "tests": [item.as_dict() for item in selected],
        }, indent=None if getattr(args, "machine", "") == "compact" else 2, ensure_ascii=False))
        return 0
    if not catalog:
        console.print("[yellow]No source-discoverable tests found.[/yellow]")
        return 0
    console.print(f"[bold]Tests[/bold]  selected {len(selected)} of {len(catalog)}")
    for item in selected:
        tags = f" [{', '.join(item.tags)}]" if item.tags else ""
        location = f"{item.file}:{item.line}" if item.line else item.file
        console.print(f"  [cyan]{item.id}[/cyan]{tags} [dim]{location}[/dim]")
    return 0


def _config_for_validation_planning(config: ProjectConfig, args: argparse.Namespace) -> ProjectConfig:
    """Return a config whose execution fields match the selected backend.

    Validation specs are built before the generic execution wrapper runs.  When
    Termux auto-chroot backend mode is active, Python environment planning must
    already know that the commands will run in the chroot; otherwise it can
    embed a Termux-host interpreter or venv path into commands that are later
    executed inside Ubuntu.
    """

    plan = _execution_plan_for_args(config, args)
    if plan.uses_chroot and plan.current not in {EXECUTION_NATIVE_TERMUX, EXECUTION_CHROOT}:
        raise SystemExit(
            "Chroot validation is available only from native Termux. "
            f"Current environment: {plan.current}. Use --execution-environment desktop-linux on this host."
        )
    effective = plan.effective
    chroot_name = plan.chroot_name or config.chroot_name
    if plan.uses_chroot:
        effective = EXECUTION_CHROOT
    if effective == config.execution_environment and chroot_name == config.chroot_name:
        return config
    return replace(config, execution_environment=effective, chroot_name=chroot_name)


def _validation_specs(config: ProjectConfig, args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[CommandSpec]:
    _apply_runner_cli_options(config, args)
    planning_config = _config_for_validation_planning(config, args)
    skip_restore, skip_build, skip_test = _validation_skip_values(args)
    if skip_restore and skip_build and skip_test:
        parser.error("Validation would run no commands. Clear at least one skip flag.")
    return build_validation_specs(
        planning_config,
        wine=args.command in {"wine-validate", "wv"},
        skip_restore=skip_restore,
        skip_build=skip_build,
        skip_test=skip_test,
    )



def _matrix_items(args: argparse.Namespace) -> list[str]:
    return [item.strip() for item in str(getattr(args, "matrix", "") or "").split(",") if item.strip()]


def _execution_plan_for_args(config: ProjectConfig, args: argparse.Namespace):
    requested = str(getattr(args, "execution_environment", EXECUTION_AUTO) or EXECUTION_AUTO)
    configured_environment = str(config.execution_environment or EXECUTION_AUTO)
    current_environment = detect_current_environment()
    explicit_chroot = bool(str(getattr(args, "chroot", "") or "").strip())
    auto_promoted_to_chroot = False
    if bool(getattr(args, "instrumented", False)):
        requested = current_environment
    if bool(getattr(args, "host", False)):
        requested = current_environment
    elif explicit_chroot:
        requested = EXECUTION_CHROOT
    elif requested == EXECUTION_AUTO and configured_environment == EXECUTION_AUTO:
        if current_environment == EXECUTION_NATIVE_TERMUX:
            from .android_integration import should_prefer_android_chroot
            if should_prefer_android_chroot(config):
                requested = EXECUTION_CHROOT
                auto_promoted_to_chroot = True

    chroot_name = str(
        getattr(args, "chroot", "")
        or getattr(args, "_devtool_chroot_name", "")
        or config.chroot_name
        or "androidos"
    ).strip()
    policy_requested_chroot = (
        not explicit_chroot
        and current_environment not in {EXECUTION_NATIVE_TERMUX, EXECUTION_CHROOT}
        and (
            requested == EXECUTION_CHROOT
            or (requested == EXECUTION_AUTO and configured_environment == EXECUTION_CHROOT)
        )
    )
    if policy_requested_chroot:
        fallback = resolve_execution_plan(
            requested=current_environment,
            configured=current_environment,
            chroot_name=chroot_name,
            device_serial=config.device_serial,
            path_map=getattr(args, "_devtool_chroot_path_map", None),
        )
        return replace(
            fallback,
            requested=EXECUTION_CHROOT if requested == EXECUTION_CHROOT else EXECUTION_AUTO,
            reason=(
                "configured chroot ignored because the current environment is "
                f"{current_environment}; chroot execution is available only from native Termux"
            ),
        )

    plan = resolve_execution_plan(
        requested=requested,
        configured=configured_environment,
        chroot_name=chroot_name,
        device_serial=config.device_serial,
        path_map=getattr(args, "_devtool_chroot_path_map", None),
    )

    # Auto mode is a preference, not a trap. If the repository is outside all
    # configured Termux-to-chroot mappings, keep execution native rather than
    # selecting chroot and failing later with host-only paths. Explicit
    # ``--chroot`` remains strict and is rejected by the normal preflight when
    # it cannot be mapped or is requested outside native Termux.
    if auto_promoted_to_chroot and plan.uses_chroot:
        path_check = next(
            (item for item in preflight_execution(config.repo_root, plan).checks if item.name == "path-map"),
            None,
        )
        if path_check is not None and path_check.status == "incompatible":
            fallback = resolve_execution_plan(
                requested=EXECUTION_NATIVE_TERMUX,
                configured=EXECUTION_NATIVE_TERMUX,
                chroot_name=chroot_name,
                device_serial=config.device_serial,
                path_map=getattr(args, "_devtool_chroot_path_map", None),
            )
            return replace(
                fallback,
                requested=EXECUTION_AUTO,
                reason=(
                    "auto kept native Termux because the repository path is not "
                    "mapped into the configured chroot"
                ),
            )
    return plan


def _prepare_specs_for_execution(config: ProjectConfig, specs: list[CommandSpec], args: argparse.Namespace) -> list[CommandSpec]:
    items = _matrix_items(args)
    if items and getattr(args, "matrix_mode", "rid") == "rid":
        specs = apply_matrix(specs, items, mode="rid")

    plan = _execution_plan_for_args(config, args)

    # Toolchain discovery occurs in the current host process. Do not inject a
    # host-only mise wrapper into commands that will execute inside a chroot.
    if not (plan.uses_chroot and plan.current != EXECUTION_CHROOT):
        specs = apply_toolchain_wrapper(config, specs)
    preflight = preflight_execution(
        config.repo_root,
        plan,
        expected_paths=(config.restore_target, config.build_target, config.test_target),
    )
    failures = preflight.failures()
    if failures and not bool(getattr(args, "dry_run", False)):
        details = "; ".join(f"{item.name}: {item.detail}" for item in failures)
        raise SystemExit(f"Execution environment preflight failed ({plan.effective}): {details}")

    if plan.uses_chroot and plan.current != EXECUTION_CHROOT:
        host_specs = [spec for spec in specs if str(spec.metadata.get("execution_environment") or "") == "android-device"]
        chroot_specs = [spec for spec in specs if spec not in host_specs]
        if plan.current == EXECUTION_NATIVE_TERMUX:
            from .termux_chroot_runner import wrap_specs_for_termux_host_chroot

            chroot_specs = wrap_specs_for_termux_host_chroot(
                chroot_specs,
                plan.chroot_name,
                repo_root=config.repo_root,
                path_map=[mapping.as_tuple() for mapping in plan.path_map],
            )
        else:
            # preflight_execution rejects this path. Keep the branch defensive
            # for callers that bypass preflight in custom integrations.
            raise SystemExit(
                "Chroot execution is available only from native Termux; "
                f"current environment is {plan.current}."
            )
        # Preserve original phase ordering when host-only instrumentation follows
        # chroot build/test phases.
        wrapped_iter = iter(chroot_specs)
        specs = [spec if spec in host_specs else next(wrapped_iter) for spec in specs]
    elif plan.current == EXECUTION_CHROOT:
        # Commands launched directly inside the chroot must never inherit
        # native-Termux paths from repository/global configuration.
        for spec in specs:
            assert_chroot_path_safe(
                [str(spec.cwd or ""), *spec.argv, *spec.env.values()],
                context=f"{spec.name} execution plan",
            )

    for spec in specs:
        metadata = dict(spec.metadata)
        metadata.update(plan.metadata())
        metadata["execution_preflight"] = [
            {"name": item.name, "status": item.status, "detail": item.detail}
            for item in preflight.checks
        ]
        spec.metadata = metadata
    return specs


def _validation_specs_for_args(config: ProjectConfig, args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[CommandSpec]:
    bundle_targets = [str(item).strip() for item in getattr(args, "_devtool_bundle_targets", []) if str(item).strip()]
    workspace_validations = [item for item in getattr(args, "_devtool_workspace_validations", []) if isinstance(item, dict)]
    items = _matrix_items(args)
    if workspace_validations and args.command in APPLY_COMMANDS and not items:
        specs: list[CommandSpec] = []
        seen: set[tuple[str, str]] = set()
        for item in workspace_validations:
            target = str(item.get("target") or "").strip()
            solution = str(item.get("solution") or "").strip()
            if not target and not solution:
                continue
            key = (target, solution)
            if key in seen:
                continue
            seen.add(key)
            target_config = load_project_config(
                args.repo,
                args.config,
                target_name=target,
                variant_name=str(getattr(args, "variant", "") or ""),
                solution_override=solution or args.solution,
                project_override=args.project,
                kind_override=str(getattr(args, "project_type", "auto") or "auto"),
            )
            target_specs = _validation_specs(target_config, args, parser)
            label = str(item.get("name") or target or solution or "workspace")
            for spec in target_specs:
                spec.name = f"{spec.name}[{label}]"
                spec.metadata["workspace"] = label
                spec.metadata["workspace_root"] = str(item.get("root") or "")
                if target:
                    spec.metadata["bundle_target"] = target
            specs.extend(target_specs)
        if specs:
            return specs
    if bundle_targets and args.command in APPLY_COMMANDS and not items:
        specs: list[CommandSpec] = []
        for item in dict.fromkeys(bundle_targets):
            target_config = load_project_config(
                args.repo,
                args.config,
                target_name=item,
                variant_name=str(getattr(args, "variant", "") or ""),
                solution_override=args.solution,
                project_override=args.project,
                kind_override=str(getattr(args, "project_type", "auto") or "auto"),
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
                variant_name=str(getattr(args, "variant", "") or ""),
                solution_override=args.solution,
                project_override=args.project,
                kind_override=str(getattr(args, "project_type", "auto") or "auto"),
            )
            target_specs = _validation_specs(target_config, args, parser)
            for spec in target_specs:
                spec.name = f"{spec.name}[{item}]"
                spec.metadata["matrix"] = item
                spec.metadata["matrix_mode"] = "target"
            specs.extend(target_specs)
        return specs
    if items and getattr(args, "matrix_mode", "rid") == "variant":
        specs: list[CommandSpec] = []
        target_name = str(getattr(args, "target", "") or "")
        for item in items:
            variant_config = load_project_config(
                args.repo,
                args.config,
                target_name=target_name,
                variant_name=item,
                solution_override=args.solution,
                project_override=args.project,
                kind_override=str(getattr(args, "project_type", "auto") or "auto"),
            )
            target_specs = _validation_specs(variant_config, args, parser)
            for spec in target_specs:
                spec.name = f"{spec.name}[{item}]"
                spec.metadata["matrix"] = item
                spec.metadata["matrix_mode"] = "variant"
                spec.metadata["variant"] = item
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
    shutdown_dotnet: bool | None = None,
    supervisor_probe=None,
    heartbeat_callback=None,
    progress_callback=None,
) -> tuple[list[OperationResult], str]:
    return run_specs(
        config,
        _prepare_specs_for_execution(config, specs, args),
        verbose=args.verbose,
        dry_run=args.dry_run,
        continue_on_error=continue_on_error,
        log_path=log_path,
        tasker_progress=tasker_progress,
        shutdown_dotnet=shutdown_dotnet,
        supervisor_probe=supervisor_probe,
        heartbeat_callback=heartbeat_callback,
        progress_callback=progress_callback,
    )



def _validation_phase_values(args: argparse.Namespace) -> list[str]:
    value = getattr(args, "phase", [])
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _validation_json_requested(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False) or str(getattr(args, "machine", "") or ""))


def _validation_machine_text(payload: dict[str, object], args: argparse.Namespace) -> str:
    compact = str(getattr(args, "machine", "") or "") == "compact"
    return json.dumps(payload, ensure_ascii=False, indent=None if compact else 2, separators=(",", ":") if compact else None)


def _emit_validation_control_payload(payload: dict[str, object], args: argparse.Namespace) -> None:
    text = _validation_machine_text(payload, args)
    print(text)
    path_text = str(getattr(args, "machine_file", "") or "")
    if path_text:
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + ("" if text.endswith("\n") else "\n"), encoding="utf-8")


def _validate_validation_api_request(args: argparse.Namespace) -> None:
    action = str(getattr(args, "_devtool_validation_api_action", "") or "")
    if action not in {"start", "run", "resume", "retry"}:
        return
    scope = str(getattr(args, "validation_run_scope", "") or "next")
    phases = _validation_phase_values(args)
    through = str(getattr(args, "through", "") or "").strip()
    if scope == "through":
        if not through:
            raise ValidationControlError(
                "invalid_request",
                "Validation scope 'through' requires --through PHASE.",
                VALIDATION_EXIT_INVALID_REQUEST,
            )
        if phases:
            raise ValidationControlError(
                "invalid_request",
                "Validation scope 'through' cannot be combined with --phase.",
                VALIDATION_EXIT_INVALID_REQUEST,
            )
    elif scope == "selected":
        if not phases:
            raise ValidationControlError(
                "invalid_request",
                "Validation scope 'selected' requires at least one --phase PHASE.",
                VALIDATION_EXIT_INVALID_REQUEST,
            )
        if through:
            raise ValidationControlError(
                "invalid_request",
                "Validation scope 'selected' cannot be combined with --through.",
                VALIDATION_EXIT_INVALID_REQUEST,
            )
    elif scope in {"next", "remaining"} and (phases or through):
        raise ValidationControlError(
            "invalid_request",
            f"Validation scope {scope!r} cannot be combined with --phase or --through.",
            VALIDATION_EXIT_INVALID_REQUEST,
        )


def _resumable_validation_requested(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "sequential", False)
        or getattr(args, "resume", False)
        or getattr(args, "restart_session", False)
        or getattr(args, "retry_failed", False)
        or getattr(args, "validation_next", False)
        or bool(_validation_phase_values(args))
        or str(getattr(args, "phase_only", "") or "").strip()
        or str(getattr(args, "through", "") or "").strip()
        or str(getattr(args, "from_phase", "") or "").strip()
        or str(getattr(args, "pause_after", "") or "").strip()
        or bool(getattr(args, "validation_complete", False))
        or int(getattr(args, "max_phases", 0) or 0) > 0
        or str(getattr(args, "max_runtime", "") or "").strip()
        or int(getattr(args, "stop_below_battery", 0) or 0) > 0
        or bool(getattr(args, "require_charging", False))
        or float(getattr(args, "stop_above_temperature", 0.0) or 0.0) > 0
        or str(getattr(args, "_devtool_validation_mode", "") or "") in {"start", "resume"}
    )


def _apply_validation_resource_profile(config: ProjectConfig, args: argparse.Namespace) -> None:
    profile = str(getattr(args, "validation_resource_profile", "automatic") or "automatic").strip().lower()
    if profile == "automatic":
        return
    validation = config.runner_options.setdefault("validation", {})
    if not isinstance(validation, dict):
        validation = {}
        config.runner_options["validation"] = validation
    gradle = config.runner_options.setdefault("gradle", {})
    if not isinstance(gradle, dict):
        gradle = {}
        config.runner_options["gradle"] = gradle
    if profile == "android-low-power":
        validation.update({
            "android_safe": True,
            "max_workers": 1,
            "cpu_limit": 2,
            "split_gradle": True,
            "no_daemon": True,
            "low_priority": True,
            "parallel": False,
            "fail_fast": True,
        })
        gradle.update({"no_daemon": True, "parallel": False, "build_cache": True})
    elif profile == "standard":
        validation["android_safe"] = False


def _enable_resumable_validation(config: ProjectConfig, args: argparse.Namespace) -> None:
    _apply_validation_resource_profile(config, args)
    if config.kind not in {"android", "gradle"}:
        raise ValidationSessionError(
            "Persistent sequential validation is currently supported for Android/Gradle targets."
        )
    validation = config.runner_options.setdefault("validation", {})
    if not isinstance(validation, dict):
        validation = {}
        config.runner_options["validation"] = validation
    validation["resumable"] = True
    # Each phase is deliberately its own Gradle process boundary.  Gradle's
    # project and user-home caches remain intact, while no automatic clean is
    # introduced between invocations.
    validation["split_gradle"] = True
    configured_stop = bool(validation.get("stop_daemon_after_phase", False))
    stop_after_phase = bool(getattr(args, "stop_gradle_after_phase", False)) or configured_stop
    if bool(getattr(args, "keep_gradle_daemon", False)):
        stop_after_phase = False
    gradle = config.runner_options.setdefault("gradle", {})
    if not isinstance(gradle, dict):
        gradle = {}
        config.runner_options["gradle"] = gradle
    gradle["stop_after_run"] = stop_after_phase


def _validation_session_options(args: argparse.Namespace) -> dict[str, object]:
    options = {
        "skip_restore": bool(getattr(args, "skip_restore", False)),
        "skip_build": bool(getattr(args, "skip_build", False)),
        "skip_test": bool(getattr(args, "skip_test", False)),
        "max_runtime": str(getattr(args, "max_runtime", "") or ""),
        "stop_below_battery": int(getattr(args, "stop_below_battery", 0) or 0),
        "require_charging": bool(getattr(args, "require_charging", False)),
        "stop_above_temperature": float(getattr(args, "stop_above_temperature", 0.0) or 0.0),
        "stop_below_disk_mb": max(0, int(getattr(args, "stop_below_disk_mb", 256) or 0)),
        "require_complete": bool(getattr(args, "require_complete", False)),
        "stop_gradle_after_phase": bool(getattr(args, "stop_gradle_after_phase", False)),
        "keep_gradle_daemon": bool(getattr(args, "keep_gradle_daemon", False)),
        "validation_resource_profile": str(getattr(args, "validation_resource_profile", "automatic") or "automatic"),
        "max_infrastructure_retries": 0 if bool(getattr(args, "no_auto_retry", False)) else max(0, int(getattr(args, "max_infrastructure_retries", 1) or 0)),
        "max_resource_retries": 0 if bool(getattr(args, "no_auto_retry", False)) else max(0, int(getattr(args, "max_resource_retries", 1) or 0)),
        "no_auto_retry": bool(getattr(args, "no_auto_retry", False)),
        "rollback_on_failure": bool(getattr(args, "rollback_on_failure", False)),
    }
    options["failure_action"] = _validation_failure_action(args, options)
    return options


def _validation_apply_context(args: argparse.Namespace, *, transaction_path: str, manifest: dict) -> dict[str, object]:
    artifact_values = _artifact_arg_list(getattr(args, "artifact", ""))
    validation_policy = artifact_validation_policy(manifest)
    raw_validation = manifest.get("validation") if isinstance(manifest.get("validation"), dict) else {}
    context: dict[str, object] = {
        "command": str(getattr(args, "command", "") or "apply-overlay"),
        "artifacts": artifact_values,
        "artifact": artifact_values[0] if len(artifact_values) == 1 else artifact_values,
        "artifact_type": str(getattr(args, "_devtool_artifact_type", "") or ""),
        "artifact_sha": str(getattr(args, "_devtool_artifact_sha", "") or ""),
        "manifest": manifest,
        "preview": dict(getattr(args, "_devtool_artifact_preview", {}) or {}),
        "commit": str(getattr(args, "commit", "") or ""),
        "keep_artifact": bool(getattr(args, "keep_artifact", False)),
        "delete_artifact_on_failure": bool(getattr(args, "delete_artifact_on_failure", False)),
        "no_artifact_script": bool(getattr(args, "no_artifact_script", False)),
        "no_manifest_check": bool(getattr(args, "no_manifest_check", False)),
        "ignore_checksum": bool(getattr(args, "ignore_checksum", False)),
        "validate_only": bool(getattr(args, "validate_only", False)),
        "rollback_on_failure": _rollback_on_failure_enabled(args, validation_policy),
        "legacy_rollback_on_failure_explicit": "rollback_on_failure" in raw_validation,
        "validation_mode": "deferred" if bool(getattr(args, "defer_validate", False)) else "sequential",
        "minimum_phase": str(validation_policy.get("minimum_phase") or "finalize"),
        "resource_profile": str(getattr(args, "validation_resource_profile", "automatic") or "automatic"),
        "artifact_script_in_plan": bool(validation_policy.get("script")) and not bool(getattr(args, "no_artifact_script", False)),
        "target": str(getattr(args, "target", "") or artifact_target(manifest)),
        "solution": str(getattr(args, "solution", "") or artifact_solution(manifest)),
        "transaction_path": transaction_path,
    }
    configured_action = str(raw_validation.get("failure_action") or "").strip().lower()
    if configured_action in {"pause", "rollback", "keep", "prompt"}:
        context["failure_action"] = configured_action
    elif "rollback_on_failure" in raw_validation:
        context["failure_action"] = "rollback" if bool(raw_validation.get("rollback_on_failure")) else "pause"
    else:
        context["failure_action"] = _validation_failure_action(args, context)
    return context


def _with_artifact_validation_phase(
    specs: list[CommandSpec],
    context: dict[str, object] | None,
) -> list[CommandSpec]:
    context = dict(context or {})
    if not bool(context.get("artifact_script_in_plan", False)):
        return specs
    artifacts = [str(item) for item in context.get("artifacts") or [] if str(item)]
    if len(artifacts) != 1:
        return specs
    static = CommandSpec(
        "artifact-validate-script",
        ["artifact-validate-script", artifacts[0]],
        metadata={
            "validation_phase_name": "static",
            "validation_phase_key": "artifact:static:0",
            "devtool_internal_validation": "artifact-script",
        },
    )
    return [static, *specs]


def _load_or_create_validation_session(
    config: ProjectConfig,
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    transaction_path: str = "",
    apply_context: dict[str, object] | None = None,
) -> tuple[ValidationSession, list[CommandSpec]]:
    requested_session = str(getattr(args, "session", "") or "").strip()
    active = load_validation_session(config.repo_root, requested_session) if requested_session else load_active_session(config.repo_root)
    mode = str(getattr(args, "_devtool_validation_mode", "") or "").strip()
    explicitly_resuming = mode == "resume" or bool(getattr(args, "resume", False)) or bool(requested_session)
    if active is not None and str(getattr(args, "_devtool_validation_api_action", "") or ""):
        previous_revision = require_validation_revision(active, _validation_expected_revision(args))
        args._devtool_validation_previous_revision = previous_revision
    if explicitly_resuming and active is None:
        raise ValidationSessionError("No matching active validation session exists to resume.")

    start_new = mode == "start" or bool(getattr(args, "restart_session", False)) or active is None
    if not start_new and active is not None:
        # A resumed plan must retain its original skip/daemon choices. Runtime,
        # battery, temperature and phase selection are intentionally per-run.
        for name in ("skip_restore", "skip_build", "skip_test", "stop_gradle_after_phase", "keep_gradle_daemon", "validation_resource_profile", "max_infrastructure_retries", "max_resource_retries", "no_auto_retry", "rollback_on_failure", "failure_action"):
            if name in active.options:
                setattr(args, name, active.options[name])

    _enable_resumable_validation(config, args)
    effective_context = dict(apply_context or (active.apply_context if active is not None else {}) or {})
    script_only = bool(effective_context.get("artifact_script_in_plan", False)) and all(
        bool(getattr(args, name, False)) for name in ("skip_restore", "skip_build", "skip_test")
    )
    specs = [] if script_only else _validation_specs_for_args(config, args, parser)
    specs = _with_artifact_validation_phase(specs, effective_context)
    if start_new:
        if active is not None and active.transaction_path:
            raise ValidationSessionError(
                "The active validation session owns a pending apply transaction. Resume or abort it instead of replacing it."
            )
        session_options = _validation_session_options(args)
        session_options["execution_environment"] = str(config.execution_environment or "auto")
        session_options["chroot_name"] = str(config.chroot_name or "")
        session_options["linux_user"] = str(os.environ.get("USER") or "")
        session_options["failure_action"] = _validation_failure_action(args, dict(apply_context or {}))
        session = create_validation_session(
            config,
            specs,
            transaction_path=transaction_path,
            apply_context=dict(apply_context or {}),
            options=session_options,
            replace_active=bool(active is not None),
        )
        specs = annotate_validation_specs(specs, session_id=session.session_id)
    else:
        session = active
        if session is None:
            raise ValidationSessionError("No active validation session exists.")
        if transaction_path and session.transaction_path and transaction_path != session.transaction_path:
            raise ValidationSessionError("The active validation session belongs to a different apply transaction.")
        if session.status in {"rollback-running", "rollback-failed", "recovery-required"}:
            raise ValidationSessionError(
                f"Validation session {session.session_id} is in {session.status!r}. "
                "Run `devtool validate recover` before continuing validation."
            )
        recovered = recover_interrupted_validation_phases(session, stale_after_seconds=5.0)
        if recovered and session.transaction_path:
            _mark_session_transaction_pending(session)
        specs = sync_validation_plan(session, specs)
        reconcile_validation_inputs(session, config)
        if session.failed:
            if bool(getattr(args, "retry_failed", False)):
                retry_failed_validation(session)
            else:
                failed = next((item.name for item in session.phases if item.status == "failed"), "unknown")
                raise ValidationSessionError(
                    f"Validation phase {failed!r} already failed with unchanged inputs. "
                    "Change its inputs or use --retry-failed to run it again."
                )
    return session, specs


def _session_pause_metadata(session: ValidationSession) -> tuple[int, int, str]:
    completed = sum(item.status in {"passed", "not-applicable", "succeeded", "skipped"} for item in session.phases)
    next_phase = session.next_pending()
    return completed, len(session.phases), next_phase.name if next_phase else ""


def _validation_console_enabled(args: argparse.Namespace) -> bool:
    """Return whether human-facing validation milestone banners may be printed."""

    return not bool(getattr(args, "json", False) or getattr(args, "machine", ""))


def _announce_validation_phase_started(args: argparse.Namespace, phase: object) -> None:
    if not _validation_console_enabled(args):
        return
    index = int(getattr(phase, "index", 0) or 0)
    total = int(getattr(phase, "total", 0) or 0)
    label = str(getattr(phase, "label", "validation") or "validation")
    console.print()
    console.print(f"[bold cyan]▶ VALIDATION PHASE {index} OF {total}[/bold cyan]")
    console.print(f"[bold]{label}[/bold] [dim]started[/dim]")


def _announce_validation_phase_finished(args: argparse.Namespace, session: ValidationSession, phase: object) -> None:
    if not _validation_console_enabled(args):
        return
    index = int(getattr(phase, "index", 0) or 0)
    total = int(getattr(phase, "total", 0) or 0)
    label = str(getattr(phase, "label", "validation") or "validation")
    status = str(getattr(phase, "status", "") or "")
    completed = sum(item.status in {"passed", "not-applicable", "succeeded", "skipped"} for item in session.phases)
    if status in {"passed", "succeeded"}:
        console.print(f"[bold green]✓ VALIDATION PHASE {index} OF {total} COMPLETED[/bold green]")
        console.print(f"[green]{label}[/green] [dim]· Overall {completed} of {total} phases complete[/dim]")
    elif status in {"not-applicable", "skipped"}:
        console.print(f"[bold yellow]○ VALIDATION PHASE {index} OF {total} SKIPPED[/bold yellow]")
        console.print(f"[yellow]{label}[/yellow] [dim]· Overall {completed} of {total} phases complete[/dim]")
    elif status == "interrupted":
        console.print(f"[bold yellow]Ⅱ VALIDATION PHASE {index} OF {total} INTERRUPTED[/bold yellow]")
        console.print(f"[yellow]{label} remains pending and can be resumed.[/yellow]")
    else:
        console.print(f"[bold red]✗ VALIDATION PHASE {index} OF {total} FAILED[/bold red]")
        console.print(f"[red]{label}[/red]")


def _announce_validation_session_completed(args: argparse.Namespace, session: ValidationSession) -> None:
    if not _validation_console_enabled(args):
        return
    total = len(session.phases)
    console.print()
    console.print("[bold green]════════ VALIDATION COMPLETE ════════[/bold green]")
    console.print(f"[bold green]✓ {total} of {total} phases completed successfully[/bold green]")
    console.print(f"[dim]Session {session.session_id} is finalized. No validation phases remain.[/dim]")


def _mark_session_transaction_pending(session: ValidationSession) -> None:
    if not session.transaction_path:
        return
    completed, total, next_phase = _session_pause_metadata(session)
    mark_transaction_pending_validation(
        session.transaction_path,
        session_id=session.session_id,
        next_phase=next_phase,
        completed_phases=completed,
        phase_total=total,
    )


def _finalize_validation_session(
    config: ProjectConfig,
    args: argparse.Namespace,
    session: ValidationSession,
    results: list[OperationResult],
    raw_output: str,
    *,
    log_path: Path | str | None,
    tasker_progress: TaskerProgressSink | None,
) -> tuple[list[OperationResult], str]:
    changed_domains = reconcile_validation_inputs(session, config)
    integrity_issues = validation_finalization_issues(session, validation_input_fingerprints(config.repo_root))
    if changed_domains or integrity_issues or not session.complete or not validation_checkpoint_satisfied(session):
        reason = "; ".join(integrity_issues) or "required validation checkpoint is not satisfied"
        paused = validation_pause_result(session, reason, require_complete=True)
        try:
            session.report_path = str(write_validation_report(session))
            save_validation_session(session, active=True)
        except Exception:
            pass
        return [*results, paused], raw_output + ("\n\n" if raw_output else "") + paused.output

    context = dict(session.apply_context or {})
    if not context:
        mark_validation_completed(session, commit_status="not-requested")
        session.report_path = str(write_validation_report(session, commit_status="not-requested", sealed=True))
        save_validation_session(session, active=False)
        completed = validation_completion_result(session)
        return [*results, completed], raw_output + ("\n\n" if raw_output else "") + completed.output

    final_args = argparse.Namespace(**vars(args))
    final_args.command = str(context.get("command") or "apply-overlay")
    final_args.artifact = context.get("artifact") or context.get("artifacts") or ""
    final_args.commit = str(context.get("commit") or "")
    final_args.keep_artifact = bool(context.get("keep_artifact", False))
    final_args.delete_artifact_on_failure = bool(context.get("delete_artifact_on_failure", False))
    final_args.no_artifact_script = bool(context.get("no_artifact_script", False))
    final_args.no_manifest_check = bool(context.get("no_manifest_check", False))
    final_args.ignore_checksum = bool(context.get("ignore_checksum", False))
    final_args.validate_only = bool(context.get("validate_only", False))
    final_args._devtool_artifact_type = str(context.get("artifact_type") or "")
    final_args._devtool_artifact_sha = str(context.get("artifact_sha") or "")
    final_args._devtool_artifact_preview = dict(context.get("preview") or {})
    final_args._devtool_artifact_manifest = dict(context.get("manifest") or {})

    historical_results = session_operation_results(session)
    manifest = dict(context.get("manifest") or {})
    artifact_values = [str(item) for item in context.get("artifacts") or [] if str(item)]
    artifact_path: Path | None = None
    if len(artifact_values) == 1:
        try:
            artifact_path = resolve_artifact_path(artifact_values[0])
        except Exception:
            artifact_path = None
    if artifact_path is not None and not bool(context.get("artifact_script_in_plan", False)) and not any(result.returncode != 0 for result in results):
        script_result, script_raw = _artifact_script_result(
            config,
            final_args,
            artifact_path,
            manifest,
            target=str(context.get("target") or artifact_target(manifest)),
            solution=str(context.get("solution") or artifact_solution(manifest)),
            log_path=log_path,
            tasker_progress=tasker_progress,
        )
        if script_result is not None:
            results.append(script_result)
            raw_output += ("\n\n" if raw_output else "") + script_raw
    elif (
        artifact_validation_policy(manifest).get("script")
        and not bool(context.get("artifact_script_in_plan", False))
        and artifact_path is None
    ):
        missing = OperationResult(
            "artifact-validate-script",
            ["artifact-validate-script", *(artifact_values or ["<missing>"])],
            2,
            0.0,
            "Deferred validation artifact is unavailable; the required embedded validator cannot run.",
            [],
            failure_kind="deferred_artifact_missing",
        )
        results.append(missing)
        raw_output += ("\n\n" if raw_output else "") + missing.output

    expectation = _validation_expectation_result(manifest, [*historical_results, *results])
    if expectation is not None:
        results.append(expectation)
        raw_output += ("\n\n" if raw_output else "") + "=== Validation expectations ===\n" + expectation.output

    finalization_failed = any(result.returncode != 0 for result in results)
    action = _validation_failure_action(args, context)
    if finalization_failed:
        action = _resolve_prompted_failure_action(args, action, phase_label="finalization")
    session.failure_action = action
    if finalization_failed:
        evidence = preserve_validation_evidence(
            config.repo_root,
            [*historical_results, *results],
            run_id=str(getattr(args, "_devtool_run_id", "") or ""),
        )
        if evidence:
            raw_output += f"\n\nValidation evidence preserved: {evidence}"
        session.last_error = "resumable validation finalization failed"
        session.status = "blocked"
        session.paused_reason = "validation finalization failed; applied changes were retained"
        if session.transaction_path and action == "rollback":
            rollback_result, rollback_raw = _run_transaction_rollback(
                session.transaction_path,
                reason="explicit rollback policy after resumable validation finalization failure",
                tasker_progress=tasker_progress,
                log_path=log_path,
            )
            results.append(rollback_result)
            raw_output += "\n\n" + rollback_raw
            mark_validation_failure_terminal(
                session,
                "validation failed; apply transaction rollback completed"
                if rollback_result.returncode == 0
                else "validation failed; apply transaction rollback failed and requires recovery",
                rollback_attempted=True,
                rollback_succeeded=rollback_result.returncode == 0,
            )
        elif session.transaction_path and action == "keep":
            abandon_transaction(
                session.transaction_path,
                reason="validation finalization failed and failure_action=keep retained changes",
            )
            mark_validation_aborted(
                session,
                "validation finalization failed; changes retained without rollback",
                kept_changes=True,
            )
        else:
            if session.transaction_path:
                mark_transaction_validation_blocked(
                    session.transaction_path,
                    session_id=session.session_id,
                    failed_phase="finalize",
                    verdict="code-failed",
                )
            try:
                session.report_path = str(write_validation_report(session))
            except Exception:
                pass
            save_validation_session(session, active=True)
        return results, raw_output

    if session.transaction_path:
        mark_transaction_validated(
            session.transaction_path,
            session_id=session.session_id,
            commit_pending=bool(str(final_args.commit or "").strip()),
        )
    commit_start = len(results)
    results, raw_output = _maybe_commit_after_apply(config, final_args, results, raw_output, log_path=log_path)
    commit_failed = any(result.returncode != 0 for result in results[commit_start:])
    if commit_failed:
        mark_validation_validated_uncommitted(session, "post-validation commit failed")
        if session.transaction_path:
            mark_transaction_validated(
                session.transaction_path,
                session_id=session.session_id,
                commit_pending=True,
            )
        return results, raw_output
    if session.transaction_path:
        mark_transaction_completed(session.transaction_path)

    if not bool(getattr(final_args, "dry_run", False)) and not final_args.keep_artifact:
        cleanup = _delete_original_artifact_after_apply(
            results,
            artifact=final_args.artifact,
            delete_on_failure=final_args.delete_artifact_on_failure,
        )
        if cleanup is not None:
            if cleanup.returncode != 0:
                cleanup = OperationResult(
                    cleanup.name,
                    cleanup.argv,
                    0,
                    cleanup.duration_seconds,
                    "Administrative warning: " + cleanup.output,
                    [Issue("warning", cleanup.name, "", "ArtifactCleanupWarning", cleanup.output)],
                    failure_kind="",
                    metadata={**cleanup.metadata, "administrative_warning": True},
                )
            results.append(cleanup)
            raw_output += ("\n\n" if raw_output else "") + "$ " + " ".join(cleanup.argv) + "\n" + cleanup.output

    commit_requested = bool(str(final_args.commit or "").strip())
    commit_hash = latest_commit_hash(config.repo_root) if commit_requested else ""
    commit_status = "committed" if commit_requested else "not-requested"
    mark_validation_completed(session, commit_status=commit_status, commit_hash=commit_hash)
    final_args._devtool_validation_session_id = session.session_id
    final_args._devtool_validation_report_path = str(validation_report_path(
        config.repo_root, session.session_id, sealed=True,
    ))
    final_args._devtool_validation_final_verdict = "validated"
    history_warning = ""
    if not getattr(final_args, "_devtool_history_recorded", False):
        try:
            _record_apply_history(config, final_args, [*historical_results, *results])
            history_error = str(getattr(final_args, "_devtool_history_error", "") or "")
            if history_error:
                raise RuntimeError(history_error)
            final_args._devtool_history_recorded = True
        except Exception as exc:
            history_warning = f"HistoryWriteWarning: {type(exc).__name__}: {exc}"
            warning = OperationResult(
                "validation-history-warning",
                ["devtool", "history", "record"],
                0,
                0.0,
                f"Administrative warning: validation passed, but history recording failed: {type(exc).__name__}: {exc}",
                [Issue("warning", "validation-history", "", "HistoryWriteWarning", str(exc))],
                metadata={"administrative_warning": True},
            )
            results.append(warning)
            raw_output += ("\n\n" if raw_output else "") + warning.output
    try:
        session.report_path = str(write_validation_report(
            session, commit_status=commit_status, commit_hash=commit_hash,
            administrative_warnings=[history_warning] if history_warning else [],
            sealed=True,
        ))
        save_validation_session(session, active=False)
    except Exception:
        pass
    completed = validation_completion_result(session)
    results.append(completed)
    raw_output += ("\n\n" if raw_output else "") + completed.output
    return results, raw_output


def _run_validation_session(
    config: ProjectConfig,
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    log_path: Path | str | None,
    tasker_progress: TaskerProgressSink | None,
    continue_on_error: bool,
    transaction_path: str = "",
    apply_context: dict[str, object] | None = None,
) -> tuple[list[OperationResult], str, ValidationSession]:
    if bool(getattr(args, "dry_run", False)):
        requested = str(getattr(args, "session", "") or "").strip()
        mode = str(getattr(args, "_devtool_validation_mode", "") or "").strip()
        active = load_validation_session(config.repo_root, requested) if requested else load_active_session(config.repo_root)
        start_new = mode == "start" or bool(getattr(args, "restart_session", False)) or active is None
        _enable_resumable_validation(config, args)
        effective_context = dict(apply_context or (active.apply_context if active is not None else {}) or {})
        script_only = bool(effective_context.get("artifact_script_in_plan", False)) and all(
            bool(getattr(args, name, False)) for name in ("skip_restore", "skip_build", "skip_test")
        )
        preview_specs = [] if script_only else _validation_specs_for_args(config, args, parser)
        preview_specs = _with_artifact_validation_phase(preview_specs, effective_context)
        if start_new:
            preview_options = _validation_session_options(args)
            preview_options["execution_environment"] = str(config.execution_environment or "auto")
            preview_options["chroot_name"] = str(config.chroot_name or "")
            preview_options["linux_user"] = str(os.environ.get("USER") or "")
            preview_options["failure_action"] = _validation_failure_action(args, effective_context)
            session = create_validation_session(
                config,
                preview_specs,
                transaction_path=transaction_path,
                apply_context=effective_context,
                options=preview_options,
                persist=False,
            )
        else:
            if active is None:
                raise ValidationSessionError("No active validation session exists to preview.")
            session = active

        through = str(getattr(args, "through", "") or getattr(args, "pause_after", "") or "").strip()
        phase_values = _validation_phase_values(args)
        phase = phase_values[0] if phase_values else ""
        phase_only = str(getattr(args, "phase_only", "") or "").strip()
        keys = select_phase_keys(
            session,
            phase=phase or phase_only,
            selected_phases=phase_values if str(getattr(args, "validation_run_scope", "") or "") == "selected" else (),
            exact_phase=bool(phase_only),
            through=through,
            from_phase=str(getattr(args, "from_phase", "") or "").strip(),
            next_only=bool(getattr(args, "validation_next", False)),
            complete=bool(getattr(args, "validation_complete", False)),
            max_phases=max(0, int(getattr(args, "max_phases", 0) or 0)),
        )
        selected = [session.phase_by_key(key) for key in keys]
        lines = [
            "VALIDATION PREVIEW",
            f"Session: {session.session_id if not start_new else '(new session not created)'}",
            f"Failure action: {session.failure_action}",
            "Session state was not changed.",
            "Would run:",
        ]
        lines.extend(
            f"  {item.index}/{item.total} {item.label}"
            for item in selected
            if item is not None
        )
        if not selected:
            lines.append("  No runnable phases")
        preview = OperationResult(
            "validation-preview",
            ["devtool", "validate", "--dry-run"],
            0,
            0.0,
            "\n".join(lines),
            [],
            skipped=True,
            metadata={
                "validation_preview": True,
                "validation_session_id": "" if start_new else session.session_id,
                "selected_phase_keys": keys,
                "state_changed": False,
            },
        )
        return [preview], preview.output, session

    session, specs = _load_or_create_validation_session(
        config,
        args,
        parser,
        transaction_path=transaction_path,
        apply_context=apply_context,
    )
    mapping = validation_spec_map(specs)
    through = str(getattr(args, "through", "") or "").strip()
    pause_after = str(getattr(args, "pause_after", "") or "").strip()
    if through and pause_after and through != pause_after:
        raise ValidationSessionError("--through and --pause-after must name the same phase when combined.")
    through = through or pause_after
    phase_values = _validation_phase_values(args)
    phase = phase_values[0] if phase_values else ""
    phase_only = str(getattr(args, "phase_only", "") or "").strip()
    keys = select_phase_keys(
        session,
        phase=phase or phase_only,
        selected_phases=phase_values if str(getattr(args, "validation_run_scope", "") or "") == "selected" else (),
        exact_phase=bool(phase_only),
        through=through,
        from_phase=str(getattr(args, "from_phase", "") or "").strip(),
        next_only=bool(getattr(args, "validation_next", False)),
        complete=bool(getattr(args, "validation_complete", False)),
        max_phases=max(0, int(getattr(args, "max_phases", 0) or 0)),
    )
    results: list[OperationResult] = []
    raw_sections: list[str] = []
    started = time.monotonic()
    runtime_budget = parse_duration_seconds(getattr(args, "max_runtime", ""))
    pause_reason = ""

    safe_retry_mapping: dict[str, CommandSpec] | None = None
    safe_retry_config: ProjectConfig | None = None

    def _safe_retry_plan() -> tuple[ProjectConfig, dict[str, CommandSpec]]:
        nonlocal safe_retry_config, safe_retry_mapping
        if safe_retry_config is not None and safe_retry_mapping is not None:
            return safe_retry_config, safe_retry_mapping
        safe_retry_config = copy.deepcopy(config)
        safe_args = copy.copy(args)
        safe_args.validation_resource_profile = "android-low-power"
        _apply_validation_resource_profile(safe_retry_config, safe_args)
        safe_specs = _validation_specs_for_args(safe_retry_config, safe_args, parser)
        safe_specs = _with_artifact_validation_phase(safe_specs, dict(session.apply_context or {}))
        safe_specs = annotate_validation_specs(safe_specs, session_id=session.session_id)
        safe_retry_mapping = validation_spec_map(safe_specs)
        return safe_retry_config, safe_retry_mapping

    def _retry_limit(verdict: str) -> int:
        if verdict == "resource-failed":
            return max(0, int(session.options.get("max_resource_retries", getattr(args, "max_resource_retries", 1)) or 0))
        if verdict == "environment-failed":
            return max(0, int(session.options.get("max_infrastructure_retries", getattr(args, "max_infrastructure_retries", 1)) or 0))
        return 0

    def _automatic_retry_number(phase_record) -> tuple[int, int]:
        verdict = str(phase_record.last_verdict or "")
        limit = _retry_limit(verdict)
        matching_attempts = sum(
            1 for item in phase_record.attempt_history
            if str(item.verdict or "") == verdict
        )
        return matching_attempts, limit

    for key in keys:
        if runtime_budget > 0 and results and time.monotonic() - started >= runtime_budget:
            pause_reason = f"runtime budget of {runtime_budget:g}s reached at a phase boundary"
            break
        guard = validation_guard_reason(
            stop_below_battery=int(getattr(args, "stop_below_battery", 0) or 0),
            require_charging=bool(getattr(args, "require_charging", False)),
            stop_above_temperature=float(getattr(args, "stop_above_temperature", 0.0) or 0.0),
        )
        if guard:
            pause_reason = guard
            break
        base_spec = mapping.get(key)
        if base_spec is None:
            raise ValidationSessionError(f"Validation plan no longer contains phase key: {key}")

        active_spec = base_spec
        active_config = config
        automatic_retry = False
        prompted_retry_used = False
        prompted_retry_attempt = False
        retry_profile = ""
        while True:
            phase = mark_phase_running(session, key)
            _announce_validation_phase_started(args, phase)

            def _phase_supervisor_probe():
                control = read_validation_control_request(session)
                control_matches_attempt = (
                    not str(control.get("active_attempt_id") or "")
                    or str(control.get("active_attempt_id") or "") == phase.current_attempt_id
                )
                if str(control.get("action") or "") == "stop-now" and control_matches_attempt:
                    return ("interrupted", "stop requested through validation control API")
                if runtime_budget > 0 and (time.monotonic() - started) >= runtime_budget:
                    return ("timeout", f"invocation runtime budget of {runtime_budget:g}s was reached")
                reason = validation_guard_reason(
                    stop_below_battery=int(getattr(args, "stop_below_battery", 0) or 0),
                    require_charging=bool(getattr(args, "require_charging", False)),
                    stop_above_temperature=float(getattr(args, "stop_above_temperature", 0.0) or 0.0),
                )
                if reason:
                    return ("resource", reason)
                disk_limit_mb = max(0, int(getattr(args, "stop_below_disk_mb", session.options.get("stop_below_disk_mb", 256)) or 0))
                if disk_limit_mb > 0:
                    try:
                        free_mb = shutil.disk_usage(config.repo_root).free // (1024 * 1024)
                        if free_mb < disk_limit_mb:
                            return ("resource", f"repository free space {free_mb} MiB is below the {disk_limit_mb} MiB validation guard")
                    except OSError:
                        pass
                return None

            def _phase_heartbeat():
                heartbeat_validation_phase(session, key)

            def _phase_progress(snapshot):
                update_validation_phase_progress(session, key, dict(snapshot or {}))

            if session.transaction_path:
                mark_transaction_validation_running(
                    session.transaction_path,
                    session_id=session.session_id,
                    phase=phase.name,
                )
            if str(active_spec.metadata.get("devtool_internal_validation") or "") == "artifact-script":
                context = dict(session.apply_context or {})
                artifacts = [str(item) for item in context.get("artifacts") or [] if str(item)]
                artifact_path = resolve_artifact_path(artifacts[0]) if len(artifacts) == 1 else None
                internal_result: OperationResult | None = None
                phase_raw = ""
                if artifact_path is not None:
                    expected_sha = str(context.get("artifact_sha") or "").strip().lower()
                    actual_sha = sha256_file(artifact_path) if expected_sha else ""
                    if expected_sha and actual_sha.lower() != expected_sha:
                        internal_result = OperationResult(
                            "artifact-validate-script",
                            list(active_spec.argv),
                            2,
                            0.0,
                            (
                                "Deferred validation artifact changed after apply.\n"
                                f"artifact: {artifact_path}\n"
                                f"expected_sha256: {expected_sha}\n"
                                f"actual_sha256: {actual_sha}"
                            ),
                            [],
                            failure_kind="deferred_artifact_changed",
                            metadata=dict(active_spec.metadata),
                        )
                    else:
                        internal_result, phase_raw = _artifact_script_result(
                            active_config,
                            args,
                            artifact_path,
                            dict(context.get("manifest") or {}),
                            target=str(context.get("target") or ""),
                            solution=str(context.get("solution") or ""),
                            log_path=log_path,
                            tasker_progress=tasker_progress,
                        )
                if internal_result is None:
                    internal_result = OperationResult(
                        "artifact-validate-script",
                        list(active_spec.argv),
                        2,
                        0.0,
                        (
                            "Deferred validation cannot run because its source artifact is missing or ambiguous.\n"
                            f"expected_artifacts: {artifacts or ['<none>']}\n"
                            "The embedded validator is a required validation prerequisite."
                        ),
                        [],
                        failure_kind="deferred_artifact_missing",
                        metadata=dict(active_spec.metadata),
                    )
                if not phase_raw:
                    phase_raw = internal_result.output
                phase_results = [internal_result]
            else:
                attempt_metadata = dict(active_spec.metadata)
                if automatic_retry:
                    attempt_metadata["validation_automatic_retry"] = True
                    attempt_metadata["validation_retry_profile"] = retry_profile
                if prompted_retry_attempt:
                    attempt_metadata["validation_prompted_retry"] = True
                    if retry_profile:
                        attempt_metadata["validation_retry_profile"] = retry_profile
                    if bool(active_spec.metadata.get("validation_memory_guard_disabled_retry")):
                        attempt_metadata["validation_memory_guard_disabled_retry"] = True
                attempt_spec = replace(active_spec, metadata=attempt_metadata)
                phase_results, phase_raw = _run_specs_for_args(
                    active_config,
                    [attempt_spec],
                    args,
                    continue_on_error=False,
                    log_path=log_path,
                    tasker_progress=tasker_progress,
                    shutdown_dotnet=False,
                    supervisor_probe=_phase_supervisor_probe,
                    heartbeat_callback=_phase_heartbeat,
                    progress_callback=_phase_progress,
                )
            result = next(
                (item for item in phase_results if not bool((item.metadata or {}).get("cleanup"))),
                phase_results[0],
            )
            for cleanup_result in phase_results:
                if cleanup_result is result or not bool((cleanup_result.metadata or {}).get("cleanup")):
                    continue
                if cleanup_result.returncode != 0:
                    cleanup_result.metadata["validation_original_returncode"] = int(cleanup_result.returncode)
                    cleanup_result.metadata["validation_original_failure_kind"] = str(cleanup_result.failure_kind or "")
                    cleanup_result.metadata["validation_administrative_warning"] = True
                    cleanup_result.returncode = 0
                    cleanup_result.failure_kind = ""
                    cleanup_result.skipped = True
            recorded_phase = record_phase_result(session, key, result)
            _announce_validation_phase_finished(args, session, recorded_phase)
            if recorded_phase.status in {"interrupted", "stale"}:
                # A paused/retryable validation attempt is evidence, not a failed
                # CLI operation. Preserve its original process verdict in the
                # durable attempt and metadata while allowing the invocation to
                # end cleanly unless --require-complete makes the pause fatal.
                result.metadata["validation_original_returncode"] = int(result.returncode)
                result.metadata["validation_original_failure_kind"] = str(result.failure_kind or "")
                result.metadata["validation_attempt_verdict"] = recorded_phase.last_verdict
                result.metadata["validation_pause_evidence"] = True
                result.returncode = 0
                result.failure_kind = ""
                result.skipped = True
            results.extend(phase_results)
            if phase_raw:
                raw_sections.append(phase_raw)

            control = read_validation_control_request(session)
            control_action = str(control.get("action") or "")
            control_matches_attempt = (
                not str(control.get("active_attempt_id") or "")
                or str(control.get("active_attempt_id") or "") == recorded_phase.attempt_history[-1].attempt_id
            )
            if control_action == "stop-now" and control_matches_attempt:
                clear_validation_control_request(session, request_id=str(control.get("request_id") or ""))
                append_validation_event(session, "stop-completed", data={
                    "phase": recorded_phase.name,
                    "attempt_id": recorded_phase.attempt_history[-1].attempt_id if recorded_phase.attempt_history else "",
                })
                pause_reason = session.paused_reason or f"{recorded_phase.label} was stopped and remains retryable"
            elif control_action == "pause-after-phase" and control_matches_attempt:
                clear_validation_control_request(session, request_id=str(control.get("request_id") or ""))
                if recorded_phase.status in {"passed", "not-applicable"}:
                    append_validation_event(session, "pause-boundary-reached", data={"phase": recorded_phase.name})
                    pause_reason = f"pause requested after {recorded_phase.label}"
                else:
                    append_validation_event(session, "pause-request-cleared", data={
                        "phase": recorded_phase.name,
                        "phase_status": recorded_phase.status,
                    })
            elif control_action and not control_matches_attempt:
                clear_validation_control_request(session, request_id=str(control.get("request_id") or ""))
                append_validation_event(session, "control-request-expired", data={
                    "request_id": str(control.get("request_id") or ""),
                    "action": control_action,
                    "active_attempt_id": str(control.get("active_attempt_id") or ""),
                })

            if recorded_phase.status in {"passed", "not-applicable"}:
                break

            latest_attempt = recorded_phase.attempt_history[-1] if recorded_phase.attempt_history else None
            recommended = str(getattr(latest_attempt, "recommended_profile", "") or "")
            memory_guard_invoked = _attempt_invoked_memory_guard(latest_attempt)
            if recorded_phase.status == "interrupted" and memory_guard_invoked and not prompted_retry_used:
                if _resolve_prompted_validation_retry(
                    args,
                    phase_label=recorded_phase.label,
                    verdict=str(recorded_phase.last_verdict or ""),
                    recommended_profile=recommended,
                    memory_guard_invoked=True,
                ):
                    active_spec = _spec_with_memory_guard_disabled(base_spec)
                    active_config = config
                    automatic_retry = False
                    prompted_retry_used = True
                    prompted_retry_attempt = True
                    retry_profile = "memory-guard-disabled"
                    raw_sections.append(
                        f"Prompted validation retry for {recorded_phase.label} with memory guard disabled"
                    )
                    continue
            retry_number, retry_limit = _automatic_retry_number(recorded_phase)
            if (
                recorded_phase.status == "interrupted"
                and not memory_guard_invoked
                and retry_limit > 0
                and retry_number <= retry_limit
            ):
                retry_profile = ""
                active_spec = base_spec
                active_config = config
                if recommended == "android-low-power":
                    active_config, retry_mapping = _safe_retry_plan()
                    active_spec = retry_mapping.get(key, base_spec)
                    retry_profile = recommended
                automatic_retry = True
                raw_sections.append(
                    f"Automatic validation retry {retry_number} of {retry_limit} for {recorded_phase.label}"
                    + (f" using {retry_profile}" if retry_profile else "")
                )
                continue

            if (
                recorded_phase.status == "interrupted"
                and not prompted_retry_used
                and not memory_guard_invoked
                and _resolve_prompted_validation_retry(
                    args,
                    phase_label=recorded_phase.label,
                    verdict=str(recorded_phase.last_verdict or ""),
                    recommended_profile=recommended,
                    memory_guard_invoked=False,
                )
            ):
                active_spec = base_spec
                active_config = config
                retry_profile = ""
                if recommended == "android-low-power":
                    active_config, retry_mapping = _safe_retry_plan()
                    active_spec = retry_mapping.get(key, base_spec)
                    retry_profile = recommended
                automatic_retry = False
                prompted_retry_used = True
                prompted_retry_attempt = True
                raw_sections.append(
                    f"Prompted validation retry for {recorded_phase.label}"
                    + (f" using {retry_profile}" if retry_profile else "")
                )
                continue

            if recorded_phase.status == "interrupted" and recorded_phase.last_interrupted_at:
                pause_reason = session.paused_reason or f"{recorded_phase.label} was interrupted and remains pending"
            elif recorded_phase.status == "stale":
                pause_reason = session.paused_reason or f"{recorded_phase.label} inputs changed during execution"
            break

        if recorded_phase.status not in {"passed", "not-applicable"}:
            break
        if not session.complete:
            _mark_session_transaction_pending(session)
        if pause_reason:
            break

    raw_output = "\n\n".join(raw_sections)
    if session.failed:
        context = dict(session.apply_context or {})
        action = _validation_failure_action(args, context)
        failed_phase = next((item for item in session.phases if item.status == "failed"), None)
        action = _resolve_prompted_failure_action(
            args, action, phase_label=failed_phase.label if failed_phase is not None else "validation"
        )
        session.failure_action = action
        evidence = preserve_validation_evidence(
            config.repo_root,
            [*session_operation_results(session), *results],
            run_id=str(getattr(args, "_devtool_run_id", "") or ""),
        )
        if evidence:
            raw_output += ("\n\n" if raw_output else "") + f"Validation evidence preserved: {evidence}"
        if session.transaction_path and action == "rollback":
            rollback_result, rollback_raw = _run_transaction_rollback(
                session.transaction_path,
                reason="explicit rollback policy after resumable validation phase failure",
                tasker_progress=tasker_progress,
                log_path=log_path,
            )
            results.append(rollback_result)
            raw_output += ("\n\n" if raw_output else "") + rollback_raw
            mark_validation_failure_terminal(
                session,
                "validation phase failed; apply transaction rollback completed"
                if rollback_result.returncode == 0
                else "validation phase failed; apply transaction rollback failed and requires recovery",
                rollback_attempted=True,
                rollback_succeeded=rollback_result.returncode == 0,
            )
        elif session.transaction_path and action == "keep":
            abandon_transaction(
                session.transaction_path,
                reason="validation failed and failure_action=keep retained the applied changes",
            )
            mark_validation_aborted(
                session,
                "validation failed; applied changes retained without rollback",
                kept_changes=True,
            )
        else:
            if session.transaction_path:
                mark_transaction_validation_blocked(
                    session.transaction_path,
                    session_id=session.session_id,
                    failed_phase=failed_phase.name if failed_phase is not None else "unknown",
                    verdict=failed_phase.last_verdict if failed_phase is not None else "code-failed",
                )
            save_validation_session(session, active=True)
            blocked = OperationResult(
                "validation-blocked",
                ["devtool", "validate", "retry"],
                0,
                0.0,
                (
                    "VALIDATION BLOCKED\n"
                    f"Session: {session.session_id}\n"
                    f"Failed phase: {failed_phase.label if failed_phase is not None else 'unknown'}\n"
                    "Applied changes: retained\n"
                    f"Rollback point: {'retained' if session.transaction_path else 'not attached'}\n"
                    "Fix the reported problem and run `devtool validate continue --retry-failed`, "
                    "or explicitly abort with rollback."
                ),
                [],
                skipped=True,
                metadata={
                    "validation_session_id": session.session_id,
                    "validation_session_status": session.status,
                    "validation_failure_action": action,
                    "changes_retained": True,
                    "recovery_available": bool(session.transaction_path),
                },
            )
            results.append(blocked)
            raw_output += ("\n\n" if raw_output else "") + blocked.output
        return results, raw_output, session

    if session.complete:
        results, raw_output = _finalize_validation_session(
            config,
            args,
            session,
            results,
            raw_output,
            log_path=log_path,
            tasker_progress=tasker_progress,
        )
        if session.status == "completed":
            _announce_validation_session_completed(args, session)
        return results, raw_output, session

    pause_reason = pause_reason or "requested phase boundary reached"
    mark_validation_paused(session, pause_reason)
    _mark_session_transaction_pending(session)
    paused = validation_pause_result(
        session,
        pause_reason,
        require_complete=bool(getattr(args, "require_complete", False)),
    )
    results.append(paused)
    raw_output += ("\n\n" if raw_output else "") + paused.output
    return results, raw_output, session


def _validation_management_session(
    config: ProjectConfig,
    args: argparse.Namespace,
    *,
    recover: bool = True,
) -> ValidationSession:
    requested = str(getattr(args, "session", "") or "").strip()
    session = load_validation_session(config.repo_root, requested) if requested else load_active_session(config.repo_root, required=True)
    if recover:
        recovered = recover_interrupted_validation_phases(session, stale_after_seconds=5.0)
        if recovered and session.transaction_path:
            _mark_session_transaction_pending(session)
    return session


def _validation_expected_revision(args: argparse.Namespace) -> int | None:
    raw = getattr(args, "if_revision", -1)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _handle_validation_status(config: ProjectConfig, args: argparse.Namespace, *, machine: bool) -> int:
    session = _validation_management_session(config, args, recover=False)
    if machine or _validation_json_requested(args):
        data = validation_status_api_payload(
            session,
            include_events=bool(getattr(args, "events", False)),
            after_event_id=str(getattr(args, "after_event", "") or ""),
            event_limit=max(1, int(getattr(args, "event_limit", 100) or 100)),
        )
        payload = validation_control_response(
            "status",
            accepted=True,
            session=session,
            reason=validation_control_reason(session),
            message="Validation status returned.",
            data=data,
        )
        _emit_validation_control_payload(payload, args)
    elif bool(getattr(args, "_devtool_validation_checkpoint", False)):
        print(validation_checkpoint_text(session))
    else:
        print(validation_status_text(session))
    return validation_control_exit_code(session) if _validation_json_requested(args) else 0


def _handle_validation_plan(config: ProjectConfig, args: argparse.Namespace) -> int:
    session = _validation_management_session(config, args, recover=False)
    data = validation_plan_payload(session)
    if _validation_json_requested(args):
        _emit_validation_control_payload(
            validation_control_response(
                "plan",
                accepted=True,
                session=session,
                reason=validation_control_reason(session),
                message="Validation plan returned.",
                data=data,
            ),
            args,
        )
    else:
        print(f"Validation plan: {session.session_id} (revision {session.revision})")
        for phase in data["phases"]:
            deps = ",".join(phase["depends_on"]) or "-"
            print(f"  {phase['index']}/{phase['total']} {phase['label']} [{phase['status']}] deps={deps}")
    return validation_control_exit_code(session) if _validation_json_requested(args) else 0


def _handle_validation_events(config: ProjectConfig, args: argparse.Namespace) -> int:
    session = _validation_management_session(config, args, recover=False)
    events = read_validation_events(
        session,
        after_event_id=str(getattr(args, "after_event", "") or ""),
        limit=max(1, int(getattr(args, "limit", getattr(args, "event_limit", 100)) or 100)),
    )
    after_event_id = str(getattr(args, "after_event", "") or "")
    payload = validation_control_response(
        "events",
        accepted=True,
        session=session,
        reason=validation_control_reason(session),
        message=f"Returned {len(events)} validation event(s).",
        data={
            "events": events,
            "cursor": {
                "after_event_id": after_event_id,
                "returned": len(events),
                "next_event_id": str(events[-1].get("event_id") or "") if events else after_event_id,
            },
        },
    )
    if _validation_json_requested(args):
        _emit_validation_control_payload(payload, args)
    else:
        for event in events:
            print(f"{event.get('timestamp','')} {event.get('type','')} revision={event.get('revision','')}")
    return 0



def _handle_validation_report(config: ProjectConfig, args: argparse.Namespace) -> int:
    session = _validation_management_session(config, args, recover=False)
    report = load_validation_report(config.repo_root, session.session_id)
    if not report:
        path = write_validation_report(session)
        session.report_path = str(path)
        save_validation_session(session, active=session.status not in {"completed", "aborted-kept-changes", "aborted-rolled-back"})
        report = load_validation_report(config.repo_root, session.session_id)
    if _validation_json_requested(args):
        _emit_validation_control_payload({
            "api_version": 1,
            "accepted": True,
            "action": "report",
            "session_id": session.session_id,
            "revision": session.revision,
            "state": session.status,
            "message": "Validation report loaded.",
            "reason": "",
            "data": {"report": report, "report_path": session.report_path},
        }, args)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0

def _handle_validation_pause_control(config: ProjectConfig, args: argparse.Namespace) -> int:
    session = _validation_management_session(config, args, recover=False)
    previous = require_validation_revision(session, _validation_expected_revision(args))
    if bool(getattr(args, "cancel_pause", False)):
        current = read_validation_control_request(session)
        if str(current.get("action") or "") != "pause-after-phase":
            raise ValidationControlError(
                "control_unavailable",
                "No pause-after-phase request is pending.",
                VALIDATION_EXIT_CONTROL_UNAVAILABLE,
            )
        request_id = str(current.get("request_id") or "")
        if not clear_validation_control_request(session, request_id=request_id):
            raise ValidationControlError(
                "control_unavailable",
                "The pending pause request changed before it could be cancelled.",
                VALIDATION_EXIT_CONTROL_UNAVAILABLE,
            )
        save_validation_session(session, active=True)
        append_validation_event(session, "pause-cancelled", data={"request_id": request_id})
        message = "Pending pause cancelled; validation will keep running."
        payload = validation_control_response(
            "pause", accepted=True, session=session, previous_revision=previous,
            message=message, data={"cancelled_request_id": request_id},
        )
        if _validation_json_requested(args):
            _emit_validation_control_payload(payload, args)
        else:
            console.print(f"[green]{message}[/green]")
        return 0
    if session.status == "blocked":
        raise ValidationControlError(
            "session_blocked",
            "Validation is blocked by a failed phase; retry, reset, or abort the session instead.",
            VALIDATION_EXIT_SESSION_BLOCKED,
        )
    if session.status in {"validated", "validated-uncommitted", "completed", "aborted-kept-changes", "aborted-rolled-back"}:
        raise ValidationControlError(
            "control_unavailable",
            f"Validation pause is unavailable while the session is {session.status}.",
            VALIDATION_EXIT_CONTROL_UNAVAILABLE,
        )
    running = [phase for phase in session.phases if phase.status == "running"]
    if running:
        try:
            request = write_validation_control_request(
                session, "pause-after-phase", expected_revision=previous, requested_by="validation-api"
            )
        except ValidationControlRequestError as exc:
            raise ValidationControlError(
                exc.reason,
                str(exc),
                VALIDATION_EXIT_STALE_REVISION if exc.reason == "stale_revision" else VALIDATION_EXIT_CONTROL_UNAVAILABLE,
            ) from exc
        message = f"Validation will pause after {running[0].label}."
        data = {"request": request, "active_phase": running[0].name}
    else:
        clear_validation_control_request(session)
        mark_validation_paused(session, "pause requested through validation control API")
        message = "Validation is paused."
        data = {"request": {}, "active_phase": ""}
    payload = validation_control_response(
        "pause", accepted=True, session=session, previous_revision=previous, message=message, data=data
    )
    if _validation_json_requested(args):
        _emit_validation_control_payload(payload, args)
    else:
        console.print(f"[yellow]{message}[/yellow]")
    return 0


def _handle_validation_stop_control(config: ProjectConfig, args: argparse.Namespace) -> int:
    session = _validation_management_session(config, args, recover=False)
    previous = require_validation_revision(session, _validation_expected_revision(args))
    running = [phase for phase in session.phases if phase.status == "running"]
    if not running:
        raise ValidationControlError(
            "control_unavailable",
            "No validation phase is currently running; there is nothing to stop.",
            VALIDATION_EXIT_CONTROL_UNAVAILABLE,
        )
    try:
        request = write_validation_control_request(
            session, "stop-now", expected_revision=previous, requested_by="validation-api"
        )
    except ValidationControlRequestError as exc:
        raise ValidationControlError(
            exc.reason,
            str(exc),
            VALIDATION_EXIT_STALE_REVISION if exc.reason == "stale_revision" else VALIDATION_EXIT_CONTROL_UNAVAILABLE,
        ) from exc
    message = f"Stop requested for {running[0].label}; the attempt will remain retryable."
    data = {"request": request, "active_phase": running[0].name}
    payload = validation_control_response(
        "stop", accepted=True, session=session, previous_revision=previous, message=message, data=data
    )
    if _validation_json_requested(args):
        _emit_validation_control_payload(payload, args)
    else:
        console.print(f"[yellow]{message}[/yellow]")
    return 0


def _handle_validation_recover(config: ProjectConfig, args: argparse.Namespace) -> int:
    """Recover only provably dead phase owners or resume a journaled rollback."""

    session = _validation_management_session(config, args, recover=False)
    previous = require_validation_revision(session, _validation_expected_revision(args))
    data: dict[str, object] = {}
    message = ""
    exit_code = 0
    if session.status in {"rollback-running", "rollback-failed"}:
        if not session.transaction_path:
            session.status = "recovery-required"
            session.last_error = "rollback recovery was requested but the transaction path is unavailable"
            session.paused_reason = "manual transaction recovery is required"
            save_validation_session(session, active=True)
            message = session.last_error
            exit_code = VALIDATION_EXIT_RECOVERY_REQUIRED
        else:
            def _rollback_event(data):
                append_validation_event(session, "rollback-progress", data=dict(data or {}))

            result = rollback_transaction(
                session.transaction_path,
                reason="explicit validation recovery resumed the attached rollback",
                progress_callback=_rollback_event,
            )
            data["rollback"] = {"returncode": result.returncode, "output": result.output}
            if result.returncode != 0:
                session.status = "rollback-failed"
                session.last_error = result.output
                session.paused_reason = "rollback remains incomplete and resumable"
                save_validation_session(session, active=True)
                append_validation_event(session, "rollback-failed", data={"output": result.output})
                message = result.output
                exit_code = VALIDATION_EXIT_RECOVERY_REQUIRED
            else:
                mark_validation_aborted(
                    session,
                    "rollback recovery completed; applied changes were restored",
                    rolled_back=True,
                )
                append_validation_event(session, "rollback-completed", data={})
                message = f"Recovered validation session {session.session_id}; rollback completed and verified."
    else:
        recovered = recover_interrupted_validation_phases(session, stale_after_seconds=5.0)
        data["recovered_phases"] = recovered
        if recovered:
            if session.transaction_path:
                _mark_session_transaction_pending(session)
            append_validation_event(session, "session-recovered", data={"phases": recovered})
            message = "Recovered interrupted validation phase(s): " + ", ".join(recovered) + "."
        else:
            live = [item for item in session.phases if item.status == "running"]
            if live:
                labels = ", ".join(item.label for item in live)
                message = f"No recovery was performed. The validation owner is still alive for: {labels}."
            else:
                message = f"Validation session {session.session_id} has no interrupted phase to recover."

    payload = validation_control_response(
        "recover",
        accepted=exit_code == 0,
        session=session,
        previous_revision=previous,
        message=message,
        reason="recovery_required" if exit_code else "",
        data=data,
    )
    if _validation_json_requested(args):
        _emit_validation_control_payload(payload, args)
    else:
        console.print(("[green]" if exit_code == 0 else "[red]") + message + ("[/green]" if exit_code == 0 else "[/red]"))
    return exit_code


def _handle_validation_reset(config: ProjectConfig, args: argparse.Namespace) -> int:
    session = _validation_management_session(config, args, recover=False)
    previous = require_validation_revision(session, _validation_expected_revision(args))
    live = [item for item in session.phases if item.status == "running"]
    if live:
        labels = ", ".join(item.label for item in live)
        raise ValidationControlError(
            "control_unavailable",
            f"Cannot reset validation while a phase is running: {labels}",
            VALIDATION_EXIT_CONTROL_UNAVAILABLE,
        )
    if bool(getattr(args, "reset_all", False)):
        reset = reset_all_validation(session)
        message = f"Reset all {len(reset)} validation phase verdicts. Attempt evidence was retained."
    else:
        phase_values = _validation_phase_values(args)
        phase = (phase_values[0] if phase_values else str(getattr(args, "from_phase", "") or "").strip())
        if not phase:
            raise ValidationControlError(
                "invalid_request",
                "validation reset requires --phase PHASE, --from-phase PHASE, or --all.",
                VALIDATION_EXIT_INVALID_REQUEST,
            )
        reset = reset_validation_phase(session, phase)
        message = f"Reset {len(reset)} phase(s) from {phase!r}; attempt evidence was retained."
    append_validation_event(session, "validation-reset", data={"phase_keys": reset})
    payload = validation_control_response(
        "reset", accepted=True, session=session, previous_revision=previous, message=message, data={"reset_phase_keys": reset}
    )
    if _validation_json_requested(args):
        _emit_validation_control_payload(payload, args)
    else:
        console.print(f"[green]{message}[/green]")
    return 0


def _handle_validation_accept_plan_change(
    config: ProjectConfig,
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> int:
    session = _validation_management_session(config, args, recover=False)
    previous = require_validation_revision(session, _validation_expected_revision(args))
    if any(item.status == "running" for item in session.phases):
        raise ValidationControlError(
            "control_unavailable",
            "Cannot accept a plan change while a validation phase is running.",
            VALIDATION_EXIT_CONTROL_UNAVAILABLE,
        )
    reason = str(getattr(args, "reason", "") or "").strip()
    if not reason:
        raise ValidationControlError("invalid_request", "Accepting a validation plan change requires --reason.")
    for name in (
        "skip_restore", "skip_build", "skip_test", "stop_gradle_after_phase",
        "keep_gradle_daemon", "validation_resource_profile", "max_infrastructure_retries",
        "max_resource_retries", "no_auto_retry", "rollback_on_failure", "failure_action",
    ):
        if name in session.options:
            setattr(args, name, session.options[name])
    _enable_resumable_validation(config, args)
    context = dict(session.apply_context or {})
    script_only = bool(context.get("artifact_script_in_plan", False)) and all(
        bool(getattr(args, name, False)) for name in ("skip_restore", "skip_build", "skip_test")
    )
    specs = [] if script_only else _validation_specs_for_args(config, args, parser)
    specs = _with_artifact_validation_phase(specs, context)
    accept_validation_plan_change(session, specs, reason=reason)
    reconcile_validation_inputs(session, config)
    append_validation_event(session, "plan-change-accepted", data={"reason": reason})
    message = "Accepted validation plan change. Affected phases were invalidated safely."
    payload = validation_control_response(
        "accept-plan-change", accepted=True, session=session, previous_revision=previous, message=message, data={"reason": reason}
    )
    if _validation_json_requested(args):
        _emit_validation_control_payload(payload, args)
    else:
        console.print(f"[green]{message}[/green]")
    return 0


def _handle_validation_abort(config: ProjectConfig, args: argparse.Namespace) -> int:
    session = _validation_management_session(config, args)
    previous = require_validation_revision(session, _validation_expected_revision(args))
    keep_changes = bool(getattr(args, "keep_changes", False))
    rollback_requested = bool(getattr(args, "rollback", False))
    if keep_changes and rollback_requested:
        raise ValidationControlError("invalid_request", "--keep-changes cannot be combined with --rollback.")
    if _validation_json_requested(args) and not (keep_changes or rollback_requested):
        raise ValidationControlError(
            "invalid_request",
            "Machine-controlled abort requires either --keep-changes or --rollback.",
        )
    data: dict[str, object] = {"kept_changes": keep_changes, "rollback_requested": rollback_requested or not keep_changes}
    if session.transaction_path and keep_changes:
        abandon_transaction(
            session.transaction_path,
            reason="validation session aborted by user while retaining applied changes",
        )
        mark_validation_aborted(session, "aborted by user; applied changes retained", kept_changes=True)
        message = "Validation session aborted; applied changes were retained without a validation verdict."
    elif session.transaction_path:
        append_validation_event(session, "rollback-started", data={})
        def _rollback_event(data):
            append_validation_event(session, "rollback-progress", data=dict(data or {}))

        result = rollback_transaction(
            session.transaction_path,
            reason="validation session aborted by user",
            progress_callback=_rollback_event,
        )
        data["rollback"] = {"returncode": result.returncode, "output": result.output}
        if result.returncode != 0:
            session.status = "rollback-failed"
            session.last_error = result.output
            session.paused_reason = "rollback failed and must be resumed"
            save_validation_session(session, active=True)
            append_validation_event(session, "rollback-failed", data={"output": result.output})
            payload = validation_control_response(
                "abort", accepted=False, session=session, previous_revision=previous,
                reason="recovery_required", message=result.output, data=data,
            )
            if _validation_json_requested(args):
                _emit_validation_control_payload(payload, args)
            else:
                console.print(f"[red]{result.output}[/red]")
            return VALIDATION_EXIT_RECOVERY_REQUIRED
        mark_validation_aborted(session, "aborted by user; applied changes rolled back", rolled_back=True)
        append_validation_event(session, "rollback-completed", data={})
        message = "Validation session aborted; applied changes were rolled back and verified."
    else:
        mark_validation_aborted(session, "aborted by user", kept_changes=True)
        message = "Validation session aborted. No attached transaction required rollback."
    payload = validation_control_response(
        "abort", accepted=True, session=session, previous_revision=previous, message=message, data=data
    )
    if _validation_json_requested(args):
        _emit_validation_control_payload(payload, args)
    else:
        console.print(f"[yellow]{message}[/yellow]")
    return 0


def _handle_validation_finalize(
    config: ProjectConfig,
    args: argparse.Namespace,
    *,
    log_path: Path | str | None,
    tasker_progress: TaskerProgressSink | None,
) -> tuple[list[OperationResult], str]:
    session = _validation_management_session(config, args)
    previous = require_validation_revision(session, _validation_expected_revision(args))
    args._devtool_validation_previous_revision = previous
    args._devtool_validation_final_session = session
    return _finalize_validation_session(
        config,
        args,
        session,
        [],
        "",
        log_path=log_path,
        tasker_progress=tasker_progress,
    )


def _record_apply_history(config: ProjectConfig, args: argparse.Namespace, results: list[OperationResult]) -> None:
    if args.command not in APPLY_COMMANDS or getattr(args, "validate_only", False) or getattr(args, "preview", False):
        return
    artifact = getattr(args, "artifact", "")
    if not artifact:
        return
    if not _repo_local_artifact_history_enabled(config.repo_root):
        return
    apply_result = next(
        (
            result for result in results
            if not (result.name == "apply-rollback" and result.failure_kind == "stale_apply_rolled_back")
        ),
        None,
    )
    status = "ok" if apply_result and apply_result.returncode == 0 else "failed"
    if any(
        r.name == "apply-rollback"
        and r.returncode == 0
        and r.failure_kind != "stale_apply_rolled_back"
        for r in results
    ):
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
        validation_session_id = str(getattr(args, "_devtool_validation_session_id", "") or "")
        if validation_session_id:
            entry["validation_session_id"] = validation_session_id
            entry["validation_report_path"] = str(getattr(args, "_devtool_validation_report_path", "") or "")
            entry["validation_final_verdict"] = str(getattr(args, "_devtool_validation_final_verdict", "") or "validated")
        history_entry_path = append_history(config.repo_root, entry)
        try:
            args._devtool_history_paths = [
                str(history_entry_path.relative_to(config.repo_root)).replace("\\", "/"),
                ".devtool/artifacts.json",
            ]
        except Exception:
            args._devtool_history_paths = [".devtool/artifacts.json"]
    except Exception as exc:
        args._devtool_history_error = f"{type(exc).__name__}: {exc}"
        console.print(f"[yellow]Could not write artifact history: {exc}[/yellow]")


def _repo_local_artifact_history_enabled(repo_root: Path) -> bool:
    """Return True when this repository has opted into committed artifact history."""
    if (repo_root / ".devtool" / "artifacts.json").exists() or (repo_root / ".devtool" / "artifacts.d").exists():
        return True
    if not (repo_root / ".git").exists():
        return True
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", ".devtool/artifacts.json"],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if tracked.returncode == 0:
        return True
    tracked_dir = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", ".devtool/artifacts.d"],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return tracked_dir.returncode == 0


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
    _write_json_payload(args.machine_file, machine)
    return 0 if totals["failed"] == 0 else 1


def _artifact_commit_paths(args: argparse.Namespace, repo_root: Path | None = None) -> list[str]:
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

    def add_history_path(value: str) -> None:
        # Most repositories intentionally ignore devtool's runtime state. Do not
        # force that state into an artifact commit merely because path-scoped Git
        # staging uses -f for declared payload files. Tracked or non-ignored
        # history registries are still reconciled exactly as before.
        if repo_root is not None:
            check = subprocess.run(
                ["git", "check-ignore", "-q", "--", value],
                cwd=repo_root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if check.returncode == 0:
                return
        add_path(value)

    # Artifact history is devtool-owned repository metadata. Stage the complete
    # registry when the repository tracks it, rather than only this run's record,
    # so earlier failed/manual runs cannot leave it dirty after a later automatic
    # commit. Ignored runtime history remains local.
    add_history_path(".devtool/artifacts.json")
    if repo_root is None or (repo_root / ".devtool" / "artifacts.d").exists():
        add_history_path(".devtool/artifacts.d")

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

    # The whole devtool operation already owns the repository's single-run lock.
    if not getattr(args, "_devtool_history_recorded", False):
        _record_apply_history(config, args, results)
        args._devtool_history_recorded = True

    commit_paths = _artifact_commit_paths(args, config.repo_root)
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


def _ensure_run_id(args: argparse.Namespace) -> str:
    run_id = str(getattr(args, "_devtool_run_id", "") or os.environ.get("DEVTOOL_RUN_ID", "")).strip()
    if not run_id:
        run_id = __import__("uuid").uuid4().hex
    args._devtool_run_id = run_id
    os.environ["DEVTOOL_RUN_ID"] = run_id
    return run_id


def _acquire_repo_single_run_lock(
    repo_root: Path,
    args: argparse.Namespace,
    *,
    log_path: Path | str | None = None,
) -> SingleRunLock:
    lock = SingleRunLock(
        repo_root,
        command=str(getattr(args, "command", "devtool") or "devtool"),
        run_id=_ensure_run_id(args),
        log_path=str(log_path or ""),
    )
    try:
        lock.acquire()
    except BlockingIOError as exc:
        raise SystemExit(str(exc)) from exc
    args._devtool_single_run_lock = lock
    return lock


def _acquire_single_run_lock(
    config: ProjectConfig,
    args: argparse.Namespace,
    *,
    log_path: Path | str | None = None,
) -> SingleRunLock:
    return _acquire_repo_single_run_lock(config.repo_root, args, log_path=log_path)


@contextmanager
def _single_run_guard(repo_root: Path, args: argparse.Namespace):
    _acquire_repo_single_run_lock(repo_root, args)
    try:
        yield
    finally:
        _release_single_run_lock(args)


def _release_single_run_lock(args: argparse.Namespace) -> None:
    lock = getattr(args, "_devtool_single_run_lock", None)
    if lock is not None:
        lock.release()
        args._devtool_single_run_lock = None
    run_id = str(getattr(args, "_devtool_run_id", "") or "")
    if run_id and os.environ.get("DEVTOOL_RUN_ID") == run_id:
        os.environ.pop("DEVTOOL_RUN_ID", None)


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

def _reload_config_after_artifact_apply(args: argparse.Namespace, fallback: ProjectConfig, manifest: dict) -> ProjectConfig:
    target = str(getattr(args, "target", "") or artifact_target(manifest)).strip()
    solution = str(getattr(args, "solution", "") or artifact_solution(manifest)).strip()
    project = str(getattr(args, "project", "") or manifest.get("run_project") or manifest.get("project") or "").strip()
    try:
        return load_project_config(
            args.repo,
            args.config,
            target_name=target,
            variant_name=str(getattr(args, "variant", "") or ""),
            solution_override=solution,
            project_override=project,
            kind_override=str(getattr(args, "project_type", "auto") or "auto"),
        )
    except SystemExit:
        return fallback


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
        validation = validate_artifact(
            config.repo_root,
            artifact_path,
            artifact_type=artifact_type,
            ignore_checksum=bool(getattr(args, "ignore_checksum", False)),
        )
        args._devtool_artifact_manifest = validation.manifest
        args._devtool_bundle_targets = overlay_bundle_targets(artifact_path) if artifact_type == "overlay_bundle" else []
        args._devtool_workspace_validations = artifact_workspaces(validation.manifest) if artifact_type == "overlay" else []
        if tasker_progress is not None:
            manifest_targets = [str(item.get("target") or "").strip() for item in args._devtool_workspace_validations if isinstance(item, dict)]
            manifest_targets.extend(str(item).strip() for item in args._devtool_bundle_targets)
            embedded_target = artifact_target(validation.manifest)
            if embedded_target:
                manifest_targets.insert(0, embedded_target)
            tasker_progress.set_targets(manifest_targets)
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
    config_paths = result.metadata.get("config_paths", []) if result.metadata else []
    if isinstance(config_paths, list) and config_paths:
        preview_data = dict(getattr(args, "_devtool_artifact_preview", {}) or {})
        changes = [dict(item) for item in preview_data.get("changes", []) if isinstance(item, dict)]
        known = {str(item.get("path") or "") for item in changes}
        for raw_path in config_paths:
            try:
                relative = str(Path(str(raw_path)).resolve().relative_to(config.repo_root.resolve())).replace("\\", "/")
            except (OSError, ValueError):
                relative = str(raw_path).replace("\\", "/")
            if relative and relative not in known:
                changes.append({"path": relative, "kind": "modify"})
                known.add(relative)
        preview_data["changes"] = changes
        args._devtool_artifact_preview = preview_data
    manifest_note = "" if getattr(args, "no_manifest_check", False) else "\n" + "\n".join(validation.messages)
    text = "$ " + " ".join(result.argv) + "\n" + result.output + manifest_note
    result.output = result.output + manifest_note
    return _return_apply(result, text)



def _run_deferred_apply(
    config: ProjectConfig,
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    log_path: Path | str | None,
    tasker_progress: TaskerProgressSink | None,
) -> tuple[list[OperationResult], str]:
    apply_results, apply_raw = _run_apply(
        config,
        args,
        args.command,
        getattr(args, "artifact", ""),
        log_path=log_path,
        tasker_progress=tasker_progress,
    )
    if not apply_results or any(result.returncode != 0 for result in apply_results):
        return apply_results, apply_raw

    manifest = dict(getattr(args, "_devtool_artifact_manifest", {}) or {})
    policy = artifact_validation_policy(manifest)
    if not bool(policy.get("allow_deferred", True)):
        transaction_path = _apply_transaction_path(apply_results)
        if transaction_path:
            rollback_result, rollback_raw = _run_transaction_rollback(
                transaction_path,
                reason="artifact policy forbids deferred validation",
                tasker_progress=tasker_progress,
                log_path=log_path,
            )
            return [*apply_results, rollback_result], apply_raw + "\n\n" + rollback_raw
        raise ValidationSessionError("This artifact explicitly forbids deferred validation.")

    transaction_path = _apply_transaction_path(apply_results)
    try:
        config = _reload_config_after_artifact_apply(args, config, manifest)
        _apply_artifact_validation_defaults(config, args, manifest)
        context = _validation_apply_context(args, transaction_path=transaction_path, manifest=manifest)
        session, _specs = _load_or_create_validation_session(
            config,
            args,
            parser,
            transaction_path=transaction_path,
            apply_context=context,
        )
    except BaseException as exc:
        failure = OperationResult(
            "validation-session",
            ["devtool", "validate", "continue"],
            1,
            0.0,
            f"Could not create deferred validation session: {type(exc).__name__}: {exc}",
            [Issue("error", "validation-session", "", type(exc).__name__, str(exc))],
            failure_kind="validation_session_error",
        )
        results = [*apply_results, failure]
        raw = apply_raw + "\n\n" + failure.output
        if transaction_path:
            rollback_result, rollback_raw = _run_transaction_rollback(
                transaction_path,
                reason="deferred validation session creation failed",
                tasker_progress=tasker_progress,
                log_path=log_path,
            )
            results.append(rollback_result)
            raw += "\n\n" + rollback_raw
        return results, raw
    mark_validation_paused(session, "validation deferred after artifact apply")
    completed, total, next_phase = _session_pause_metadata(session)
    mark_transaction_pending_validation(
        transaction_path,
        session_id=session.session_id,
        next_phase=next_phase,
        completed_phases=completed,
        phase_total=total,
        deferred=True,
    )
    deferred = OperationResult(
        "validation-deferred",
        ["devtool", "validate", "continue"],
        0,
        0.0,
        (
            f"Applied artifact and deferred validation.\n"
            f"session: {session.session_id}\n"
            f"phases: {len(session.phases)}\n"
            f"next_phase: {next_phase or '-'}\n"
            "rollback: available until validation finalization"
        ),
        [],
        metadata={
            "validation_session_id": session.session_id,
            "validation_session_status": "deferred",
            "validation_complete": False,
            "validation_deferred": True,
            "next_phase": next_phase,
            "completed_phases": completed,
            "phase_total": total,
            "transaction_path": transaction_path,
            "recovery_available": bool(transaction_path),
            "resource_profile": str(getattr(args, "validation_resource_profile", "automatic") or "automatic"),
        },
    )
    for result in apply_results:
        result.metadata.setdefault("validation_session_id", session.session_id)
        result.metadata.setdefault("validation_deferred", True)
    results = [*apply_results, deferred]
    if args.command in {"apply-overlay", "ao"}:
        artifact_values = _artifact_arg_list(getattr(args, "artifact", ""))
        label = Path(artifact_values[0]).name if artifact_values else "Overlay"
        _tag_overlay_results(
            results,
            index=1,
            total=1,
            label=label,
            target=config.selected_target or "",
            artifact=artifact_values[0] if artifact_values else "",
        )
    return results, apply_raw + "\n\n" + deferred.output


def _run_resumable_apply(
    config: ProjectConfig,
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    log_path: Path | str | None,
    tasker_progress: TaskerProgressSink | None,
    continue_on_error: bool,
) -> tuple[list[OperationResult], str]:
    apply_results, apply_raw = _run_apply(
        config,
        args,
        args.command,
        getattr(args, "artifact", ""),
        log_path=log_path,
        tasker_progress=tasker_progress,
    )
    if not apply_results or any(result.returncode != 0 for result in apply_results):
        return apply_results, apply_raw

    manifest = dict(getattr(args, "_devtool_artifact_manifest", {}) or {})
    if not getattr(args, "validate_only", False):
        config = _reload_config_after_artifact_apply(args, config, manifest)
    _apply_artifact_validation_defaults(config, args, manifest)
    transaction_path = _apply_transaction_path(apply_results)
    context = _validation_apply_context(args, transaction_path=transaction_path, manifest=manifest)
    session_results, session_raw, session = _run_validation_session(
        config,
        args,
        parser,
        log_path=log_path,
        tasker_progress=tasker_progress,
        continue_on_error=continue_on_error,
        transaction_path=transaction_path,
        apply_context=context,
    )
    results = [*apply_results, *session_results]
    raw_output = apply_raw + (("\n\n" + session_raw) if session_raw else "")
    for result in results:
        result.metadata.setdefault("validation_session_id", session.session_id)
    if args.command in {"apply-overlay", "ao"}:
        artifact_values = _artifact_arg_list(getattr(args, "artifact", ""))
        label = Path(artifact_values[0]).name if artifact_values else "Overlay"
        _tag_overlay_results(
            results,
            index=1,
            total=1,
            label=label,
            target=config.selected_target or "",
            artifact=artifact_values[0] if artifact_values else "",
        )
    return results, raw_output


def _repair_result_name() -> str:
    return "auto-repair"


def _repair_failure_count(results: list[OperationResult]) -> int:
    return sum(1 for result in results if result.returncode != 0 and result.name not in {"apply-rollback"})


def _repair_output_lines(applied: list[object], *, before_failures: int, after_failures: int, restored: bool) -> str:
    lines = ["Auto repair pass:"]
    if applied:
        lines.append(f"- Applied {len(applied)} safe fix{'es' if len(applied) != 1 else ''}.")
        for finding in applied[:3]:
            location = str(getattr(finding, "file", "") or "-")
            line = getattr(finding, "line", None)
            if line is not None:
                location = f"{location}:{line}"
            lines.append(f"  - {location} {getattr(finding, 'code', '-')}: {getattr(finding, 'fix', '')}")
    else:
        lines.append("- No safe fixes were available.")
    if restored:
        lines.append("- Restored original source files because the retry did not reduce the error set.")
    else:
        lines.append(f"- Validation failures: {before_failures} -> {after_failures}")
    return "\n".join(lines)


def _attempt_auto_repair(
    config: ProjectConfig,
    args: argparse.Namespace,
    *,
    validation_results: list[OperationResult],
    validation_specs: list[CommandSpec],
    continue_on_error: bool,
    log_path: Path | str | None,
    tasker_progress: TaskerProgressSink | None,
    parser: argparse.ArgumentParser,
    shutdown_dotnet: bool | None = None,
) -> tuple[list[OperationResult], str, OperationResult | None]:
    if _repair_failure_count(validation_results) == 0:
        return validation_results, "", None

    applied, backups = apply_auto_source_repairs(config.repo_root, validation_results)
    if not applied:
        return validation_results, "", None

    before_failures = _repair_failure_count(validation_results)
    repair_started = datetime.now(timezone.utc)
    try:
        repaired_results, repaired_raw = _run_specs_for_args(
            config,
            validation_specs,
            args,
            continue_on_error=continue_on_error,
            log_path=log_path,
            tasker_progress=tasker_progress,
            shutdown_dotnet=shutdown_dotnet,
        )
    except Exception:
        restore_source_backups(backups)
        raise

    after_failures = _repair_failure_count(repaired_results)
    improved = after_failures < before_failures
    if not improved:
        restore_source_backups(backups)
        output = _repair_output_lines(applied, before_failures=before_failures, after_failures=after_failures, restored=True)
        repair_result = OperationResult(
            _repair_result_name(),
            ["devtool", "repair"],
            0,
            (datetime.now(timezone.utc) - repair_started).total_seconds(),
            output,
            [],
            metadata={
                "applied_repairs": [asdict(finding) for finding in applied],
                "restored": True,
                "before_failures": before_failures,
                "after_failures": after_failures,
            },
        )
        combined_raw = output + ("\n\n" + repaired_raw if repaired_raw else "")
        return validation_results + [repair_result], combined_raw, repair_result

    output = _repair_output_lines(applied, before_failures=before_failures, after_failures=after_failures, restored=False)
    repair_result = OperationResult(
        _repair_result_name(),
        ["devtool", "repair"],
        0,
        (datetime.now(timezone.utc) - repair_started).total_seconds(),
        output,
        [],
        metadata={
            "applied_repairs": [asdict(finding) for finding in applied],
            "restored": False,
            "before_failures": before_failures,
            "after_failures": after_failures,
        },
    )
    return [repair_result, *repaired_results], output + ("\n\n" + repaired_raw if repaired_raw else ""), repair_result




def _tag_overlay_results(
    results: list[OperationResult],
    *,
    index: int,
    total: int,
    label: str,
    target: str,
    artifact: str,
) -> None:
    """Attach stable grouping metadata used by Rich/JSON/clipboard summaries."""
    for result in results:
        metadata = dict(result.metadata or {})
        metadata.update(
            {
                "overlay_index": index,
                "overlay_total": total,
                "overlay_label": label,
                "overlay_target": target,
                "overlay_artifact": artifact,
            }
        )
        result.metadata = metadata


def _overlay_sequence_entries(args: argparse.Namespace):
    """Return materialized overlays in execution order.

    A real bundle is materialized into a temporary directory. Multiple direct
    archives remain direct source artifacts so each can be cleaned independently.
    """
    artifacts = [resolve_artifact_path(item) for item in _artifact_arg_list(getattr(args, "artifact", ""))]
    if not artifacts:
        raise RuntimeError("No overlay artifacts were provided.")

    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    source_bundle: Path | None = None
    entries: list[dict[str, object]] = []

    if len(artifacts) == 1 and is_overlay_bundle(artifacts[0]):
        source_bundle = artifacts[0]
        temp_dir = tempfile.TemporaryDirectory(prefix="devtool-sequential-overlays-")
        _manifest, materialized = materialize_overlay_bundle_entries(source_bundle, temp_dir.name)
        total = len(materialized)
        for item in materialized:
            label = item.label or Path(item.bundle_path).name
            entries.append(
                {
                    "path": item.path,
                    "source": source_bundle,
                    "bundle_member": item.bundle_path,
                    "label": label,
                    "target": item.target.strip(),
                    "index": item.index,
                    "total": total,
                    "materialized": True,
                }
            )
    else:
        total = len(artifacts)
        for index, path in enumerate(artifacts, start=1):
            manifest = read_artifact_manifest(path)
            label = str(manifest.get("name") or manifest.get("label") or path.name).strip() or path.name
            target = str(manifest.get("validate_target") or manifest.get("target") or "").strip()
            entries.append(
                {
                    "path": path,
                    "source": path,
                    "bundle_member": "",
                    "label": label,
                    "target": target,
                    "index": index,
                    "total": total,
                    "materialized": False,
                }
            )
    return temp_dir, source_bundle, entries


def _overlay_commit_message(base: str, *, index: int, total: int, label: str) -> str:
    base = base.strip()
    if not base:
        return ""
    if total <= 1:
        return base
    return f"{base} [{index}/{total}] {label}"


def _artifact_script_result(
    config: ProjectConfig,
    args: argparse.Namespace,
    artifact: Path,
    manifest: dict,
    *,
    target: str,
    solution: str,
    log_path: Path | str | None,
    tasker_progress: TaskerProgressSink | None,
) -> tuple[OperationResult | None, str]:
    if getattr(args, "no_artifact_script", False):
        return None, ""
    if not artifact_validation_policy(manifest).get("script"):
        return None, ""
    try:
        if tasker_progress is not None:
            tasker_progress.phase_started(
                "artifact-validate-script",
                index=1,
                total=1,
                argv=["artifact-validate-script", str(artifact)],
            )
        result = run_artifact_validation_script(
            config.repo_root,
            artifact,
            manifest=manifest,
            target=target,
            solution=solution,
            run_id=str(getattr(args, "_devtool_run_id", "") or ""),
            dry_run=bool(getattr(args, "dry_run", False)),
            log_path=log_path,
            ignore_checksum=bool(getattr(args, "ignore_checksum", False)),
        )
    except Exception as exc:
        result = OperationResult(
            "artifact-validate-script",
            ["artifact-validate-script", str(artifact)],
            1,
            0.0,
            f"{type(exc).__name__}: {exc}",
            [Issue("error", "artifact-validate-script", str(artifact), type(exc).__name__, str(exc))],
            failure_kind="validation_failure",
        )
    if result is not None and tasker_progress is not None:
        tasker_progress.phase_finished(result)
    if result is None:
        return None, ""
    return result, "$ " + " ".join(result.argv) + "\n" + result.output


def _validation_expectation_result(manifest: dict, results: list[OperationResult]) -> OperationResult | None:
    _ = manifest, results
    return None


def _artifact_declared_paths(manifest: dict) -> set[str]:
    paths: set[str] = set()
    inventory = manifest.get("inventory") if isinstance(manifest.get("inventory"), dict) else {}
    entries = inventory.get("entries") if isinstance(inventory.get("entries"), list) else []
    for item in entries:
        if isinstance(item, dict):
            path = str(item.get("path") or "").strip()
            if path:
                paths.add(path)
    legacy = manifest.get("files")
    if isinstance(legacy, list):
        for item in legacy:
            path = str(item).strip()
            if path:
                paths.add(path)
    return paths


def _run_sequential_overlays(
    config: ProjectConfig,
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    log_path: Path | str | None,
    tasker_progress: TaskerProgressSink | None,
    continue_on_error: bool,
) -> tuple[list[OperationResult], str]:
    """Apply a bundle atomically, validate its overlays, and create one final commit."""
    all_results: list[OperationResult] = []
    raw_sections: list[str] = []
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    adhoc_bundle_temp: tempfile.TemporaryDirectory[str] | None = None
    source_bundle: Path | None = None

    try:
        temp_dir, source_bundle, entries = _overlay_sequence_entries(args)
        bundle_manifest = read_artifact_manifest(source_bundle) if source_bundle is not None else {}
        _apply_artifact_validation_defaults(config, args, bundle_manifest)
        bundle_policy = artifact_validation_policy(bundle_manifest)
        bundle_strategy = str(bundle_policy.get("strategy") or "per-overlay").strip().lower()
        if bundle_strategy not in {"per-overlay", "after-all", "both"}:
            bundle_strategy = "per-overlay"

        declared_paths: dict[int, set[str]] = {}
        last_writer: dict[str, int] = {}
        for entry in entries:
            manifest = read_artifact_manifest(Path(entry["path"]))
            index = int(entry["index"])
            paths = _artifact_declared_paths(manifest)
            declared_paths[index] = paths
            for changed_path in paths:
                last_writer[changed_path] = index
        superseded_paths = {
            index: {path for path in paths if last_writer.get(path) != index}
            for index, paths in declared_paths.items()
        }

        apply_artifact: object = getattr(args, "artifact", "")
        direct_artifacts = _artifact_arg_list(apply_artifact)
        direct_sources: list[Path] = []
        if len(direct_artifacts) > 1:
            adhoc_bundle_temp, adhoc_bundle_path, direct_sources = _make_ad_hoc_overlay_bundle(
                direct_artifacts,
                name="adhoc_overlay_bundle",
            )
            apply_artifact = str(adhoc_bundle_path)

        apply_results, apply_raw = _run_apply(
            config,
            args,
            "apply-overlays",
            apply_artifact,
            log_path=log_path,
            tasker_progress=tasker_progress,
        )
        if direct_sources:
            for result in apply_results:
                metadata = dict(result.metadata or {})
                if metadata.get("transaction_path"):
                    metadata["artifact_paths"] = [str(path) for path in direct_sources]
                    metadata["artifact_path"] = str(direct_sources[0])
                    result.metadata = metadata
        all_results.extend(apply_results)
        raw_sections.append(apply_raw)
        transaction_path = _apply_transaction_path(all_results)
        apply_ok = bool(apply_results) and all(result.returncode == 0 for result in apply_results)
        validation_cache: set[tuple[str, str, str, bool, bool, bool]] = set()
        dotnet_shutdown_config: ProjectConfig | None = None

        if apply_ok or getattr(args, "validate_only", False):
            for entry in entries:
                path = Path(entry["path"])
                label = str(entry["label"])
                entry_manifest = read_artifact_manifest(path)
                entry_policy = artifact_validation_policy(entry_manifest)
                should_validate = not getattr(args, "no_validate", False) and bool(
                    getattr(args, "validate", False)
                    or getattr(args, "validate_only", False)
                    or bundle_policy.get("required", False)
                    or entry_policy.get("required", False)
                )
                if not should_validate:
                    continue

                manifest_target = str(entry["target"] or artifact_target(entry_manifest)).strip()
                target = str(getattr(args, "target", "") or "").strip() if getattr(args, "_devtool_cli_target_explicit", False) else manifest_target
                solution = str(getattr(args, "solution", "") or "").strip() if getattr(args, "_devtool_cli_solution_explicit", False) else artifact_solution(entry_manifest)
                entry_config = load_project_config(
                    args.repo,
                    args.config,
                    target_name=target,
                    variant_name=str(getattr(args, "variant", "") or ""),
                    solution_override=solution,
                    project_override=args.project,
                    kind_override=str(getattr(args, "project_type", "auto") or "auto"),
                )
                entry_args = argparse.Namespace(**vars(args))
                entry_args.command = "apply-overlay"
                entry_args.artifact = str(path)
                _apply_artifact_validation_defaults(entry_config, entry_args, entry_manifest)
                restore_enabled = entry_policy.get("restore", bundle_policy.get("restore", True))
                build_enabled = entry_policy.get("build", bundle_policy.get("build", True))
                test_enabled = entry_policy.get("test", bundle_policy.get("test", True))
                entry_args.skip_restore = bool(getattr(args, "skip_restore", False) or not restore_enabled)
                entry_args.skip_build = bool(getattr(args, "skip_build", False) or not build_enabled)
                entry_args.skip_test = bool(getattr(args, "skip_test", False) or not test_enabled)

                group_results: list[OperationResult] = []
                if not (entry_args.skip_restore and entry_args.skip_build and entry_args.skip_test):
                    specs = _validation_specs(entry_config, entry_args, parser)
                    validation_key = (
                        entry_config.restore_target,
                        entry_config.build_target,
                        entry_config.test_target,
                        not entry_args.skip_restore,
                        not entry_args.skip_build,
                        not entry_args.skip_test,
                    )
                    can_reuse = entry_config.kind == "dotnet" and validation_key in validation_cache
                    if can_reuse:
                        message = (
                            f"Reused the completed .NET validation for {entry_config.build_target or target}; "
                            "the same workspace is restored, built, and tested only once per bundle."
                        )
                        group_results.append(OperationResult(
                            "validation-reused",
                            ["validation-reused", entry_config.build_target or target],
                            0,
                            0.0,
                            message,
                            [],
                            skipped=True,
                            metadata={"shared_validation": True, "bundle_target": target},
                        ))
                        raw_sections.append(f"=== Validate {label} ===\n{message}")
                    else:
                        validation_results, validation_raw = _run_specs_for_args(
                            entry_config,
                            specs,
                            entry_args,
                            continue_on_error=continue_on_error,
                            log_path=log_path,
                            tasker_progress=tasker_progress,
                            shutdown_dotnet=False,
                        )
                        group_results.extend(validation_results)
                        raw_sections.append(f"=== Validate {label} ===\n{validation_raw}")
                        if entry_config.kind == "dotnet":
                            if dotnet_build_server_shutdown_enabled(entry_config):
                                dotnet_shutdown_config = entry_config
                            if not any(result.returncode != 0 for result in validation_results):
                                validation_cache.add(validation_key)

                if not any(result.returncode != 0 for result in group_results):
                    overridden = superseded_paths.get(int(entry["index"]), set())
                    has_script = bool(entry_policy.get("script"))
                    if has_script and overridden:
                        preview = ", ".join(sorted(overridden)[:3])
                        if len(overridden) > 3:
                            preview += f", and {len(overridden) - 3} more"
                        message = (
                            "Skipped this artifact's final-tree validator because later overlays intentionally "
                            f"replace {len(overridden)} declared path(s): {preview}. The winning overlay validator "
                            "and shared .NET validation remain authoritative."
                        )
                        group_results.append(OperationResult(
                            "artifact-validator-superseded",
                            ["artifact-validator-superseded", str(path)],
                            0,
                            0.0,
                            message,
                            [],
                            skipped=True,
                            metadata={"superseded_paths": sorted(overridden)},
                        ))
                        raw_sections.append(f"=== Artifact validator {label} ===\n{message}")
                    else:
                        script_result, script_raw = _artifact_script_result(
                            entry_config,
                            entry_args,
                            path,
                            entry_manifest,
                            target=target,
                            solution=solution,
                            log_path=log_path,
                            tasker_progress=tasker_progress,
                        )
                        if script_result is not None:
                            group_results.append(script_result)
                            raw_sections.append(f"=== Artifact validator {label} ===\n{script_raw}")

                if any(result.returncode != 0 for result in group_results):
                    group_results, repair_raw, _repair_result = _attempt_auto_repair(
                        entry_config,
                        entry_args,
                        validation_results=group_results,
                        validation_specs=_validation_specs(entry_config, entry_args, parser),
                        continue_on_error=continue_on_error,
                        log_path=log_path,
                        tasker_progress=tasker_progress,
                        parser=parser,
                        shutdown_dotnet=False,
                    )
                    if repair_raw:
                        raw_sections.append(f"=== Auto repair {label} ===\n{repair_raw}")

                expectation = _validation_expectation_result(entry_manifest, group_results)
                if expectation is not None:
                    group_results.append(expectation)
                    raw_sections.append(
                        f"=== Validation expectations {label} ===\n{expectation.output}"
                    )
                _tag_overlay_results(
                    group_results,
                    index=int(entry["index"]),
                    total=int(entry["total"]),
                    label=label,
                    target=target,
                    artifact=str(path),
                )
                all_results.extend(group_results)
                if any(result.returncode != 0 for result in group_results) and not continue_on_error:
                    break

            # "both" adds a final integration pass across distinct affected targets.
            if bundle_strategy == "both" and not any(result.returncode != 0 for result in all_results):
                seen_targets: set[str] = set()
                for entry in entries:
                    path = Path(entry["path"])
                    manifest = read_artifact_manifest(path)
                    target = str(entry["target"] or artifact_target(manifest)).strip()
                    if not target or target in seen_targets:
                        continue
                    seen_targets.add(target)
                    final_config = load_project_config(
                        args.repo,
                        args.config,
                        target_name=target,
                        variant_name=str(getattr(args, "variant", "") or ""),
                        solution_override="",
                        project_override=args.project,
                        kind_override=str(getattr(args, "project_type", "auto") or "auto"),
                    )
                    final_args = argparse.Namespace(**vars(args))
                    final_args.command = "apply-overlay"
                    union_changed_paths = sorted({
                        changed_path
                        for paths in declared_paths.values()
                        for changed_path in paths
                    })
                    if union_changed_paths:
                        final_config.runner_options.setdefault("validation", {}).update({
                            "changed_paths": union_changed_paths,
                            "changed_paths_source": "bundle-inventory",
                        })
                    specs = _validation_specs(final_config, final_args, parser)
                    final_key = (
                        final_config.restore_target,
                        final_config.build_target,
                        final_config.test_target,
                        True,
                        True,
                        True,
                    )
                    if final_config.kind == "dotnet" and final_key in validation_cache:
                        continue
                    final_results, final_raw = _run_specs_for_args(
                        final_config,
                        specs,
                        final_args,
                        continue_on_error=continue_on_error,
                        log_path=log_path,
                        tasker_progress=tasker_progress,
                        shutdown_dotnet=False,
                    )
                    if final_config.kind == "dotnet":
                        if dotnet_build_server_shutdown_enabled(final_config):
                            dotnet_shutdown_config = final_config
                        if not any(result.returncode != 0 for result in final_results):
                            validation_cache.add(final_key)
                    if any(result.returncode != 0 for result in final_results):
                        final_results, repair_raw, _repair_result = _attempt_auto_repair(
                            final_config,
                            final_args,
                            validation_results=final_results,
                            validation_specs=specs,
                            continue_on_error=continue_on_error,
                            log_path=log_path,
                            tasker_progress=tasker_progress,
                            parser=parser,
                            shutdown_dotnet=False,
                        )
                        if repair_raw:
                            final_raw = repair_raw
                    all_results.extend(final_results)
                    raw_sections.append(f"=== Final validation [{target}] ===\n{final_raw}")
                    if any(result.returncode != 0 for result in final_results) and not continue_on_error:
                        break

            if source_bundle is not None and not any(result.returncode != 0 for result in all_results):
                script_result, script_raw = _artifact_script_result(
                    config,
                    args,
                    source_bundle,
                    bundle_manifest,
                    target=str(getattr(args, "target", "") or ""),
                    solution=str(getattr(args, "solution", "") or ""),
                    log_path=log_path,
                    tasker_progress=tasker_progress,
                )
                if script_result is not None:
                    all_results.append(script_result)
                    raw_sections.append("=== Bundle validator ===\n" + script_raw)
                expectation = _validation_expectation_result(bundle_manifest, all_results)
                if expectation is not None:
                    all_results.append(expectation)
                    raw_sections.append(
                        "=== Bundle validation expectations ===\n" + expectation.output
                    )

        if dotnet_shutdown_config is not None:
            shutdown_result, shutdown_raw = shutdown_dotnet_build_server(
                dotnet_shutdown_config,
                dry_run=bool(getattr(args, "dry_run", False)),
                log_path=log_path,
            )
            all_results.append(shutdown_result)
            raw_sections.append(shutdown_raw)

        rollback_enabled = _rollback_on_failure_enabled(args, bundle_policy)
        if transaction_path and rollback_enabled and _has_failed_after_apply(all_results):
            evidence_path = preserve_validation_evidence(
                config.repo_root, all_results, run_id=str(getattr(args, "_devtool_run_id", "") or "")
            )
            if evidence_path:
                raw_sections.append(f"Validation evidence preserved: {evidence_path}")
            rollback_result, rollback_raw = _run_transaction_rollback(
                transaction_path,
                reason="bundle apply or validation failed",
                tasker_progress=tasker_progress,
                log_path=log_path,
            )
            all_results.append(rollback_result)
            raw_sections.append(rollback_raw)

        commit_args = argparse.Namespace(**vars(args))
        commit_args.artifact = str(source_bundle or apply_artifact)
        all_results, commit_raw = _maybe_commit_after_apply(
            config,
            commit_args,
            all_results,
            "\n\n".join(raw_sections),
            log_path=log_path,
        )
        raw_sections = [commit_raw]

        if transaction_path:
            if any(result.returncode != 0 for result in all_results):
                if rollback_enabled and not any(result.name == "apply-rollback" for result in all_results):
                    evidence_path = preserve_validation_evidence(
                        config.repo_root, all_results, run_id=str(getattr(args, "_devtool_run_id", "") or "")
                    )
                    if evidence_path:
                        raw_sections.append(f"Validation evidence preserved: {evidence_path}")
                    rollback_result, rollback_raw = _run_transaction_rollback(
                        transaction_path,
                        reason="post-bundle command failed",
                        tasker_progress=tasker_progress,
                        log_path=log_path,
                    )
                    all_results.append(rollback_result)
                    raw_sections.append(rollback_raw)
            else:
                mark_transaction_completed(transaction_path)

        if not getattr(args, "_devtool_history_recorded", False):
            _record_apply_history(config, args, all_results)
            args._devtool_history_recorded = True

        if not getattr(args, "dry_run", False) and not getattr(args, "keep_artifact", False):
            cleanup = _delete_original_artifact_after_apply(
                all_results,
                artifact=getattr(args, "artifact", ""),
                delete_on_failure=bool(getattr(args, "delete_artifact_on_failure", False)),
            )
            if cleanup is not None:
                all_results.append(cleanup)
                raw_sections.append("$ " + " ".join(cleanup.argv) + "\n" + cleanup.output)

        return all_results, "\n\n".join(section for section in raw_sections if section)
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()
        if adhoc_bundle_temp is not None:
            adhoc_bundle_temp.cleanup()

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
        result.failure_kind != "stale_apply_rolled_back"
        and (result.name == "apply-rollback" or result.metadata.get("rolled_back_during_apply"))
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
    failure_kind: str | None = None,
) -> tuple[OperationResult, str]:
    if tasker_progress is not None:
        tasker_progress.phase_started("apply-rollback", index=1, total=1, argv=["rsync", "rollback", str(transaction_path)])
    result = rollback_transaction(transaction_path, reason=reason)
    if failure_kind is not None:
        result.failure_kind = failure_kind
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
        planning_config = _config_for_validation_planning(config, args)
        specs = build_specs(planning_config, name)
        more, text = _run_specs_for_args(planning_config, specs, args, continue_on_error=continue_on_error, log_path=log_path, tasker_progress=tasker_progress)
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


def _doctor(config: ProjectConfig, *, fix: bool = False, args: argparse.Namespace | None = None) -> None:
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
    runner = get_project_runner(config)
    capabilities = runner_capabilities(config).supported()
    plan = _execution_plan_for_args(config, args or argparse.Namespace(host=False, chroot="", execution_environment=EXECUTION_AUTO))
    execution_preflight = preflight_execution(config.repo_root, plan, expected_paths=(config.build_target, config.test_target))
    rows = [
        ("repo", str(config.repo_root)),
        ("kind", config.kind),
        ("runner", runner.kind),
        ("runner_name", runner.display_name),
        ("capabilities", ", ".join(capabilities) if capabilities else "-"),
        ("execution_current", plan.current),
        ("execution_requested", plan.requested),
        ("execution_effective", plan.effective),
        ("execution_reason", plan.reason),
        ("execution_preflight", "ready" if execution_preflight.ok else "failed"),
        ("chroot", plan.chroot_name or "-"),
        ("git", "yes" if (config.repo_root / ".git").exists() else "no"),
        ("config", str(config.config_path) if config.config_path else "-"),
        ("wl-copy", "yes" if shutil.which("wl-copy") else "no"),
        ("fzf", "yes" if shutil.which("fzf") else "no"),
        ("dotnet", "yes" if shutil.which("dotnet") else "no"),
        ("xdg_data", str(devtool_xdg_dir())),
        ("python", config.python_executable),
        ("selected_target", config.selected_target or "-"),
        ("variant", config.variant or "-"),
        ("restore_target", config.restore_target or "-"),
        ("build_target", config.build_target or "-"),
        ("test_target", config.test_target or "-"),
        ("run_target", config.run_target or "-"),
        ("wine", config.env.get("SIMSTOOLKIT_WINE_PATH", os.environ.get("SIMSTOOLKIT_WINE_PATH", "")) or "-"),
        ("fixed", ", ".join(fixed) if fixed else "-"),
    ]
    rows.extend(toolchain_doctor_rows(config))
    rows.extend(runner.doctor_rows(config))
    render_status_table(rows)


def _status(config: ProjectConfig, *, args: argparse.Namespace | None = None) -> None:
    runner = get_project_runner(config)
    capabilities = runner_capabilities(config).supported()
    plan = _execution_plan_for_args(config, args or argparse.Namespace(host=False, chroot="", execution_environment=EXECUTION_AUTO))
    rows = [
        ("repo", str(config.repo_root)),
        ("kind", config.kind),
        ("runner", runner.kind),
        ("runner_name", runner.display_name),
        ("capabilities", ", ".join(capabilities) if capabilities else "-"),
        ("execution_current", plan.current),
        ("execution_requested", plan.requested),
        ("execution_effective", plan.effective),
        ("execution_reason", plan.reason),
        ("chroot", plan.chroot_name or "-"),
        ("selected_target", config.selected_target or "-"),
        ("variant", config.variant or "-"),
        ("restore_target", config.restore_target or "-"),
        ("build_target", config.build_target or "-"),
        ("test_target", config.test_target or "-"),
        ("run_target", config.run_target or "-"),
        ("git_dirty", "yes" if _git_dirty(config.repo_root) else "no"),
        ("transaction_root", str(transaction_root(config.repo_root))),
    ]
    rows.extend(runner.inspect_rows(config))
    render_status_table(rows)


def _inspect_project(config: ProjectConfig, *, args: argparse.Namespace | None = None) -> None:
    runner = get_project_runner(config)
    rows = [
        ("repo", str(config.repo_root)),
        ("kind", config.kind),
        ("runner", runner.kind),
        ("runner_name", runner.display_name),
        ("project_root", config.project_root or str(config.repo_root)),
        ("selected_target", config.selected_target or "-"),
        ("variant", config.variant or "-"),
    ]
    specific = runner.inspect_rows(config)
    if specific:
        existing = {name for name, _value in rows}
        rows.extend((name, value) for name, value in specific if name not in existing)
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
            validation = validate_overlay_bundle(
                config.repo_root,
                artifact_path,
                fail_on_conflict=bool(getattr(args, "fail_on_conflict", False)),
                ignore_checksum=bool(getattr(args, "ignore_checksum", False)),
            )
        else:
            validation = validate_artifact(
                config.repo_root,
                artifact_path,
                artifact_type=artifact_type,
                ignore_checksum=bool(getattr(args, "ignore_checksum", False)),
            )
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
    repository = {
        "name": str(getattr(args, "repository_name", "") or "").strip(),
        "remote": str(getattr(args, "repository_remote", "") or "").strip(),
        "branch": str(getattr(args, "repository_branch", "") or "").strip(),
    }
    out = create_overlay_bundle(
        args.out,
        list(args.overlays),
        name=args.name,
        description=args.description,
        targets=targets or None,
        repository=repository,
        commit_message=str(getattr(args, "commit_message", "") or ""),
        validation_required=bool(getattr(args, "validate", False)),
        validation_strategy=str(getattr(args, "validation_strategy", "per-overlay") or "per-overlay"),
        validation_script=str(getattr(args, "validation_script", "") or "") or None,
        validation_runner=str(getattr(args, "validation_runner", "bash") or "bash"),
        validation_timeout_seconds=int(getattr(args, "validation_timeout", 900) or 900),
    )
    console.print(f"[green]Created overlay bundle[/green] {out}")
    return 0




def _devtool_global_cli_config() -> dict:
    data, _path = load_global_config()
    cli = data.get("cli", {}) if isinstance(data.get("cli", {}), dict) else {}
    return cli if isinstance(cli, dict) else {}


def _raw_flag_present(raw_argv: list[str], *flags: str) -> bool:
    for token in raw_argv:
        for flag in flags:
            if token == flag or (flag.startswith("--") and token.startswith(flag + "=")):
                return True
    return False


def _apply_global_cli_defaults(args: argparse.Namespace, raw_argv: list[str]) -> None:
    cli = _devtool_global_cli_config()
    if not cli:
        return
    output = cli.get("output", {}) if isinstance(cli.get("output", {}), dict) else {}
    if "stdout_payload" in output and not _raw_flag_present(raw_argv, "--stdout-payload"):
        value = str(output.get("stdout_payload") or "auto")
        if value in {"auto", "summary", "raw"}:
            args.stdout_payload = value
    if "machine_file" in output and not _raw_flag_present(raw_argv, "--machine-file"):
        value = str(output.get("machine_file") or "").strip()
        if value:
            args.machine_file = value
    if bool(output.get("copy", False)) and not _raw_flag_present(raw_argv, "-p", "--copy", "--no-clipboard"):
        args.copy = True
    if bool(output.get("no_clipboard", False)) and not _raw_flag_present(raw_argv, "--no-clipboard", "-p", "--copy"):
        args.no_clipboard = True
    if "copy_via" in output and not _raw_flag_present(raw_argv, "--copy-via"):
        value = str(output.get("copy_via") or "auto")
        if value in {"auto", "socket", "fifo", "termux", "x11", "wayland"}:
            args.copy_via = value
    if "theme" in output and not _raw_flag_present(raw_argv, "--theme"):
        value = str(output.get("theme") or "auto")
        if value in {"auto", "plain"}:
            args.theme = value

    hud = cli.get("hud", {}) if isinstance(cli.get("hud", {}), dict) else {}
    if "auto_hud" in hud and not _raw_flag_present(raw_argv, "--auto-hud", "--hud-auto", "--no-hud"):
        value = hud.get("auto_hud")
        if isinstance(value, bool):
            args.auto_hud = value
        elif str(value).strip().lower() in {"true", "yes", "on", "1"}:
            args.auto_hud = True
        elif str(value).strip().lower() in {"false", "no", "off", "0"}:
            args.auto_hud = False
    if "mode" in hud and not _raw_flag_present(raw_argv, "--hud-mode"):
        value = str(hud.get("mode") or "").strip()
        if value:
            args.hud_mode = value
    overlay_value = str(hud.get("overlay", "auto") or "auto").strip().lower()
    if overlay_value in {"true", "yes", "on", "1"} and not _raw_flag_present(raw_argv, "--hud-overlay", "--hud-no-overlay"):
        args.hud_overlay = True
    elif overlay_value in {"false", "no", "off", "0"} and not _raw_flag_present(raw_argv, "--hud-overlay", "--hud-no-overlay"):
        args.hud_no_overlay = True

    env = cli.get("environment", {}) if isinstance(cli.get("environment", {}), dict) else {}
    if "execution_environment" in env and not _raw_flag_present(raw_argv, "--execution-environment", "--host", "--chroot"):
        value = str(env.get("execution_environment") or EXECUTION_AUTO).strip()
        if value in EXECUTION_ENVIRONMENTS:
            args.execution_environment = value
    if bool(env.get("no_auto_chroot", False)) and not _raw_flag_present(raw_argv, "--no-auto-chroot", "--chroot"):
        args.no_auto_chroot = True

    apply = cli.get("apply", {}) if isinstance(cli.get("apply", {}), dict) else {}
    if str(getattr(args, "command", "") or "") in LEGACY_APPLY_VALIDATE_COMMANDS:
        if bool(apply.get("validate", False)) and not _raw_flag_present(raw_argv, "--validate", "--no-validate"):
            args.validate = True
        if bool(apply.get("manifest_check", True)) is False and not _raw_flag_present(raw_argv, "--no-manifest-check"):
            args.no_manifest_check = True
        if bool(apply.get("ignore_checksum", False)) and not _raw_flag_present(raw_argv, "--ignore-checksum"):
            args.ignore_checksum = True
        if bool(apply.get("keep_artifact", False)) and not _raw_flag_present(raw_argv, "--keep-artifact"):
            args.keep_artifact = True
        if bool(apply.get("delete_artifact_on_failure", False)) and not _raw_flag_present(raw_argv, "--delete-artifact-on-failure"):
            args.delete_artifact_on_failure = True
        if bool(apply.get("rollback_on_failure", True)) is False and not _raw_flag_present(raw_argv, "--no-rollback-on-failure"):
            args.no_rollback_on_failure = True
        if bool(apply.get("apply_notification", True)) is False and not _raw_flag_present(raw_argv, "--no-apply-notification"):
            args.no_apply_notification = True
        if bool(apply.get("artifact_script", True)) is False and not _raw_flag_present(raw_argv, "--no-artifact-script"):
            args.no_artifact_script = True


def _resolve_config_editor(requested: str = "") -> list[str]:
    candidates = [
        requested,
        os.environ.get("DEVTOOL_EDITOR", ""),
        os.environ.get("VISUAL", ""),
        os.environ.get("EDITOR", ""),
        "micro",
        "nano",
        "vim",
        "vi",
    ]
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text:
            continue
        argv = shlex.split(text)
        if not argv:
            continue
        if shutil.which(argv[0]) or os.path.isabs(argv[0]) or "/" in argv[0]:
            return argv
    raise SystemExit("No editor found. Install micro or set DEVTOOL_EDITOR, VISUAL, or EDITOR.")

def _config_command(args: argparse.Namespace) -> int:
    if args.config_command == "path":
        if getattr(args, "global_config", False):
            print(global_config_path())
            return 0
        config = _load_command_config(args)
        print(config.config_path or "")
        return 0

    if getattr(args, "global_config", False):
        data, path = load_global_config()
        if args.config_command == "show":
            print(path.read_text(encoding="utf-8") if path.exists() else "")
            return 0
        if args.config_command == "defaults":
            print(minimal_global_config_template(), end="")
            return 0
        if args.config_command == "init":
            if path.exists() and not getattr(args, "force", False):
                raise SystemExit(f"Global config already exists: {path}. Use --force to overwrite.")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(minimal_global_config_template(), encoding="utf-8")
            console.print(f"[green]Wrote[/green] {path}")
            return 0
        if args.config_command == "edit":
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(minimal_global_config_template(), encoding="utf-8")
            editor = _resolve_config_editor(str(getattr(args, "editor", "") or ""))
            return subprocess.run([*editor, str(path)], check=False).returncode
        if args.config_command == "doctor":
            rows = [("global_config_path", str(path)), ("exists", "yes" if path.exists() else "no")]
            rows.extend(global_config_doctor_entries(data))
            if len(rows) == 2:
                rows.append(("status", "ok"))
            render_status_table(rows)
            return 0

    config = _load_command_config(args)
    if args.config_command == "show":
        print(config.config_path.read_text(encoding="utf-8") if config.config_path else "")
        return 0
    if args.config_command == "defaults":
        from .config import generate_template

        if getattr(args, "minimal", False):
            print(generate_template(config.repo_root))
        else:
            print(generate_template(config.repo_root))
        return 0
    if args.config_command == "init":
        target = write_template_config(config.repo_root, force=getattr(args, "force", False))
        console.print(f"[green]Wrote[/green] {target}")
        return 0
    if args.config_command == "edit":
        path = config.config_path or (config.repo_root / ".devtool.toml")
        if not path.exists():
            target = write_template_config(config.repo_root, force=False)
            path = target
        editor = _resolve_config_editor(str(getattr(args, "editor", "") or ""))
        return subprocess.run([*editor, str(path)], check=False).returncode
    if args.config_command == "resolve":
        print(json.dumps(resolve_config_dict(config), indent=2))
        return 0
    if args.config_command == "doctor":
        rows = [
            ("config_path", str(config.config_path) if config.config_path else "-"),
            ("global_config_path", str(config.global_config_path) if config.global_config_path else "-"),
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
    native app HUD bridge, clipboard, and command summary on Android.  The old whole-
    devtool delegation remains available with DEVTOOL_AUTO_CHROOT_MODE=full.
    """
    if bool(getattr(args, "no_auto_chroot", False)) or inside_chroot():
        return None
    # Auto/full chroot routing is an Android Termux feature. Never inspect or
    # invoke chrootctl from ordinary desktop Linux, even when repo/global config
    # requests chroot execution. Generic execution preflight will report an
    # explicit chroot request as incompatible later.
    if detect_current_environment() != EXECUTION_NATIVE_TERMUX:
        return None
    requested_environment = str(getattr(args, "execution_environment", EXECUTION_AUTO) or EXECUTION_AUTO)
    if requested_environment == EXECUTION_AUTO and not bool(getattr(args, "host", False)) and not str(getattr(args, "chroot", "") or "").strip():
        try:
            configured = load_project_config(
                getattr(args, "repo", None),
                getattr(args, "config", None),
                target_name=str(getattr(args, "target", "") or ""),
                variant_name=str(getattr(args, "variant", "") or ""),
                solution_override=str(getattr(args, "solution", "") or ""),
                project_override=str(getattr(args, "project", "") or ""),
                kind_override=str(getattr(args, "project_type", "auto") or "auto"),
            )
            configured_environment = str(configured.execution_environment or EXECUTION_AUTO)
            if configured_environment == EXECUTION_CHROOT:
                requested_environment = EXECUTION_CHROOT
                if not str(getattr(args, "chroot", "") or "").strip():
                    args.chroot = configured.chroot_name or "androidos"
            elif configured_environment != EXECUTION_AUTO:
                return None
            else:
                from .android_integration import should_prefer_android_chroot
                if detect_current_environment() == "native-termux" and not should_prefer_android_chroot(configured):
                    return None
                if not str(getattr(args, "chroot", "") or "").strip():
                    args.chroot = configured.chroot_name or "androidos"
        except (OSError, SystemExit, ValueError):
            # Apply-time artifacts may introduce or repair configuration. The
            # normal loader will produce the actionable error after apply.
            pass
    if requested_environment not in {EXECUTION_AUTO, EXECUTION_CHROOT}:
        return None
    try:
        from .termux_chroot_runner import (
            _is_termux_host_env,
            chrootctl_available,
            normalize_hud_mode_for_termux_host,
            repair_termux_home_mapping,
            repo_path_from_argv,
            run_auto_chroot,
            should_auto_chroot,
            should_delegate_full_for_chroot_only_repo,
            translate_devtool_args,
        )
        explicit_chroot = str(getattr(args, "chroot", "") or "").strip()
        chroot_name = explicit_chroot or os.environ.get("DEVTOOL_TERMUX_CHROOT", "androidos").strip() or "androidos"
        # Backend mode intentionally keeps devtool on the Termux host. That is
        # impossible when -r points to a directory which exists only inside the
        # chroot. Detect that namespace mismatch before project discovery and
        # delegate the whole process instead of reporting a misleading missing
        # repository error. Explicit --chroot is handled here too.
        if (
            _is_termux_host_env()
            and chrootctl_available()
            and should_delegate_full_for_chroot_only_repo(
                raw_argv, chroot_name=chroot_name, cwd=os.getcwd()
            )
        ):
            return run_auto_chroot(raw_argv)
        eligible = should_auto_chroot(raw_argv, command=str(getattr(args, "command", "") or ""))
        if not eligible and _is_termux_host_env() and explicit_chroot and chrootctl_available():
            eligible = True
        if not eligible:
            return None
        mode = os.environ.get("DEVTOOL_AUTO_CHROOT_MODE", "backend").strip().lower()
        if mode in {"full", "legacy", "process", "wrapper"}:
            return run_auto_chroot(raw_argv)
        config, _forwarded = translate_devtool_args(raw_argv, cwd=os.getcwd())
        host_repo = repo_path_from_argv(raw_argv, cwd=os.getcwd()) or os.getcwd()
        config.path_map = repair_termux_home_mapping(
            config.path_map,
            host_path=host_repo,
            chroot_name=config.chroot_name,
        )
        args.chroot = config.chroot_name
        args.host = False
        args._devtool_termux_host_chroot_backend = True
        args._devtool_chroot_path_map = config.path_map
        args._devtool_chroot_name = config.chroot_name
        args.hud_mode = normalize_hud_mode_for_termux_host(str(getattr(args, "hud_mode", "desktop") or "desktop"))
        if str(getattr(args, "copy_via", "auto") or "auto") == "auto":
            args.copy_via = "termux"
        return None
    except Exception as exc:
        explicit = (
            requested_environment == EXECUTION_CHROOT
            or bool(str(getattr(args, "chroot", "") or "").strip())
        )
        if explicit or os.environ.get("DEVTOOL_AUTO_CHROOT_STRICT", "0").strip().lower() in {"1", "true", "yes"}:
            raise SystemExit(f"Chroot execution requested but unavailable: {exc}") from exc
        console.print(f"[yellow]Auto chroot unavailable; running in native Termux: {exc}[/yellow]")
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


def _build_cache_cleanup_result(*, dry_run: bool) -> OperationResult:
    started = datetime.now(timezone.utc)
    removed = cleanup_devtool_caches(dry_run=dry_run)
    duration = max(0.0, (datetime.now(timezone.utc) - started).total_seconds())
    if removed:
        verb = "Would remove" if dry_run else "Removed"
        output = "\n".join([f"{verb} devtool cache paths:"] + [f"- {path}" for path in removed])
    else:
        output = "No devtool-managed caches or transient state were found."
    return OperationResult(
        "cache-cleanup",
        ["devtool", "clean", "--caches"],
        0,
        duration,
        output,
        [],
        metadata={"cleanup": True, "cache_cleanup": True, "dry_run": dry_run},
    )



def _write_performance_report(
    args: argparse.Namespace,
    results: list[OperationResult],
    *,
    started_at: datetime,
    finished_at: datetime,
    config: ProjectConfig | None,
) -> Path | None:
    if not bool(getattr(args, "performance", False) or os.environ.get("DEVTOOL_PERFORMANCE")):
        return None
    usage_self = resource.getrusage(resource.RUSAGE_SELF)
    usage_children = resource.getrusage(resource.RUSAGE_CHILDREN)
    phases = [
        {
            "name": result.name,
            "duration_seconds": round(float(result.duration_seconds or 0.0), 6),
            "returncode": int(result.returncode or 0),
            "argv": list(result.argv),
            "log_path": str(result.log_path or ""),
        }
        for result in results
    ]
    payload = {
        "schema_version": 1,
        "command": str(getattr(args, "command", "") or ""),
        "repo_root": str(config.repo_root) if config is not None else str(getattr(args, "repo", "") or ""),
        "target": str(config.selected_target) if config is not None else str(getattr(args, "target", "") or ""),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "wall_seconds": round((finished_at - started_at).total_seconds(), 6),
        "operation_seconds": round(sum(float(result.duration_seconds or 0.0) for result in results), 6),
        "operation_count": len(results),
        "spawned_process_estimate": sum(1 for result in results if result.argv),
        "peak_rss_kib": int(usage_self.ru_maxrss),
        "child_peak_rss_kib": int(usage_children.ru_maxrss),
        "phases": phases,
    }
    requested = str(getattr(args, "performance_file", "") or "").strip()
    path = Path(requested).expanduser() if requested else devtool_xdg_dir() / "performance-latest.json"
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    console.print(f"[dim]Performance report:[/dim] {path}")
    return path


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parse_argv, passthrough_args = split_runner_passthrough_args(raw_argv)
    parser = build_parser()
    args = parser.parse_args(parse_argv)
    args._devtool_passthrough_args = passthrough_args
    _normalize_command_aliases(args)
    _apply_global_cli_defaults(args, parse_argv)
    _normalize_live_progress_args(args)
    args._devtool_cli_target_explicit = bool(str(getattr(args, "target", "") or "").strip())
    args._devtool_cli_solution_explicit = bool(str(getattr(args, "solution", "") or "").strip())
    _apply_artifact_manifest_defaults(args)
    _validate_apply_args(args, parser)
    effective_payload, _copy_mode = _validate_output_flags(args, parser)
    auto_chroot_rc = _maybe_prepare_termux_auto_chroot(raw_argv, args)
    if auto_chroot_rc is not None:
        return auto_chroot_rc
    _print_chroot_indicator_once(args)

    if args.command in {"ai-prompt", "ap"}:
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
            notify=False,
            notify_brief=False,
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
        if args.copy:
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
        logs_command = str(getattr(args, "logs_command", "") or "latest")
        if logs_command in {"latest"}:
            path = latest_log()
            if not path:
                console.print("[yellow]No logs found.[/yellow]")
                return 0
            if getattr(args, "print", False):
                print(path.read_text(encoding="utf-8"))
            else:
                console.print(str(path))
            return 0
        if logs_command in {"list", "ls"}:
            entries = list_log_entries()
            limit = max(1, int(getattr(args, "limit", 25) or 25))
            for line in format_log_entries(entries[-limit:]):
                console.print(line)
            return 0
        if logs_command in {"open", "show"}:
            path = resolve_log_path(str(getattr(args, "selector", "") or ""), use_fzf=bool(getattr(args, "fzf", True)))
            if not path:
                console.print("[yellow]No log selected.[/yellow]")
                return 1
            return open_log(path, pager=str(getattr(args, "pager", "auto") or "auto"))
        if logs_command in {"tail", "follow"}:
            path = choose_active_log(use_fzf=bool(getattr(args, "fzf", True)))
            if not path:
                console.print("[yellow]No active devtool run with a log was found.[/yellow]")
                return 1
            return tail_log(path, lines=int(getattr(args, "lines", 200) or 200), follow=not bool(getattr(args, "no_follow", False)))
        console.print(f"[red]Unknown logs command: {logs_command}[/red]")
        return 2

    if args.command in {"init-config", "init"}:
        target = write_template_config(Path(args.repo_root), force=args.force)
        console.print(f"[green]Wrote[/green] {target}")
        return 0

    if args.command == "bundle":
        return _run_bundle_command(args)

    if args.command in {"inspect-artifact", "ia"}:
        repo_root = Path(args.repo).expanduser().resolve() if args.repo else None
        try:
            payload = inspect_artifact(
                args.artifact,
                repo_root=repo_root,
                include_diff=bool(args.diff),
                diff_context=max(0, int(args.diff_context)),
                max_diff_lines=max(100, int(args.max_diff_lines)),
            )
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            return 2
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if bool(payload.get("validation", {}).get("ok", False)) else 1

    if _is_artifact_validation_args(args):
        config = _load_command_config(args)
        return _run_artifact_validation_command(config, args, as_json=False)

    if args.command == "config":
        return _config_command(args)
    if args.command in {"targets", "tg"}:
        config = _load_command_config(args)
        _targets(config)
        return 0


    if args.command == "detect":
        repo = Path(args.repo or Path.cwd()).resolve()
        info = detect_repo(repo)
        if args.format == "json":
            print(json.dumps(info, indent=2, ensure_ascii=False))
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

    if args.command == "ipc-server":
        from .ipc import run_ipc_server
        return run_ipc_server(args)

    if args.command in {"control-center", "gui"}:
        binary = str(getattr(args, "binary", "") or os.environ.get("DEVTOOL_CONTROL_CENTER", "") or "devtool-control-center")
        candidate = shutil.which(binary)
        if candidate is None:
            repo_candidate = Path(__file__).resolve().parent.parent / "desktop" / "devtool-control-center" / "build" / "devtool-control-center"
            candidate = str(repo_candidate) if repo_candidate.is_file() else None
        if candidate is None:
            console.print("[red]Devtool Control Center is not installed.[/red]")
            console.print("Build desktop/devtool-control-center and install devtool-control-center on PATH.")
            return 2
        return subprocess.call([candidate])

    if args.command == "hud":
        from .hud import run_hud

        return run_hud(args)

    if args.command in {"android-hud-bridge", "termux-hud-bridge"}:
        from .hud import default_android_bridge_dir, run_hud

        state_dir = Path(args.state_dir).expanduser() if args.state_dir else default_android_bridge_dir()
        hud_args = argparse.Namespace(
            tasker_dir=str(state_dir),
            mode="android-bridge",
            host="127.0.0.1",
            port=8765,
            poll_interval=float(args.poll_interval),
            recent_events=int(args.recent_events),
            once=bool(args.once),
            follow_active=bool(args.follow_active),
            overlay=bool(getattr(args, "overlay", False)),
            no_overlay=bool(getattr(args, "no_overlay", False)),
        )
        return run_hud(hud_args)

    if args.command == "history":
        config = _load_command_config(args)
        if args.format == "json":
            print(json.dumps(load_history(config.repo_root)[-max(1, args.limit):], indent=2, ensure_ascii=False))
        else:
            _print_history(config, limit=args.limit)
        return 0

    if args.command == "rollback":
        config = _load_command_config(args)
        with _single_run_guard(config.repo_root, args):
            return _rollback(config, args)

    if args.command in {
        "validation-status", "vs", "validation-plan", "vplan", "validation-events", "vevents", "validation-report", "vreport",
        "validation-pause", "vpause", "validation-stop", "vstop",
        "validation-abort", "va", "validation-recover", "vrec",
        "validation-reset", "vreset", "validation-accept-plan-change", "vapc",
    }:
        config = _load_command_config(args)
        try:
            if args.command in {"validation-status", "vs"}:
                return _handle_validation_status(config, args, machine=False)
            if args.command in {"validation-plan", "vplan"}:
                return _handle_validation_plan(config, args)
            if args.command in {"validation-events", "vevents"}:
                return _handle_validation_events(config, args)
            if args.command in {"validation-report", "vreport"}:
                return _handle_validation_report(config, args)
            # Pause/stop must be able to publish an atomic control request while
            # the validation process owns the repository run lock.
            if args.command in {"validation-pause", "vpause"}:
                return _handle_validation_pause_control(config, args)
            if args.command in {"validation-stop", "vstop"}:
                return _handle_validation_stop_control(config, args)
            with _single_run_guard(config.repo_root, args):
                if args.command in {"validation-recover", "vrec"}:
                    return _handle_validation_recover(config, args)
                if args.command in {"validation-reset", "vreset"}:
                    return _handle_validation_reset(config, args)
                if args.command in {"validation-accept-plan-change", "vapc"}:
                    return _handle_validation_accept_plan_change(config, args, parser)
                return _handle_validation_abort(config, args)
        except ValidationControlError as exc:
            if _validation_json_requested(args):
                session = None
                try:
                    session = _validation_management_session(config, args, recover=False)
                except ValidationSessionError:
                    pass
                _emit_validation_control_payload(validation_control_error_response(
                    str(getattr(args, "_devtool_validation_api_action", "") or args.command), exc, session
                ), args)
            else:
                console.print(f"[red]{exc}[/red]")
            return exc.exit_code
        except ValidationSessionError as exc:
            if _validation_json_requested(args):
                error = ValidationControlError("invalid_request", str(exc), VALIDATION_EXIT_INVALID_REQUEST)
                _emit_validation_control_payload(validation_control_error_response(
                    str(getattr(args, "_devtool_validation_api_action", "") or args.command), error
                ), args)
            else:
                console.print(f"[red]{exc}[/red]")
            return VALIDATION_EXIT_INVALID_REQUEST

    if args.command in {"doctor", "dr", "status", "st", "inspect", "info", "release-notes", "tag"} or args.command in {"commit", "ci"}:
        repo = Path(args.repo or Path.cwd()).resolve()
        if args.command in {"commit", "ci"}:
            with _single_run_guard(repo, args):
                _ensure_safe_repo(repo, force=args.force, yes=args.yes, command="commit")
                log_path = new_log_path()
                started_at = datetime.now(timezone.utc)
                results, raw_output = _git_commit_with_optional_log(repo, args.message, verbose=args.verbose, dry_run=args.dry_run, log_path=log_path)
                finished_at = datetime.now(timezone.utc)
                totals, summary_text, json_text, _machine_json_text = _emit_summary(
                    results,
                    None,
                    as_json=False,
                    payload_mode=effective_payload,
                    raw_output=raw_output,
                    command=args.command,
                    started_at=started_at,
                    finished_at=finished_at,
                    machine_format="pretty",
                )
                failed = totals["failed"]
                _write_json_payload(args.machine_file, _machine_json_text)
                _maybe_copy(raw_output, summary_text, json_text, args)
                return 0 if failed == 0 else 1
        config = load_project_config(
            str(repo), args.config, variant_name=str(getattr(args, "variant", "") or ""), kind_override=str(getattr(args, "project_type", "auto") or "auto")
        )
        if args.command in {"doctor", "dr"}:
            _doctor(config, fix=getattr(args, "fix", False), args=args)
            return 0
        if args.command in {"status", "st"}:
            _status(config, args=args)
            return 0
        if args.command in {"inspect", "info"}:
            _inspect_project(config, args=args)
            return 0
        if args.command == "release-notes":
            print(_release_notes(config.repo_root, args.from_ref, args.to_ref))
            return 0
        if args.command == "tag":
            with _single_run_guard(repo, args):
                _ensure_safe_repo(repo, force=args.force, yes=args.yes, command="tag")
                specs = [CommandSpec("tag", ["git", "tag"] + (["-m", args.message] if args.message else []) + [args.name], destructive=True)]
                log_path = new_log_path()
                started_at = datetime.now(timezone.utc)
                results, raw_output = _run_specs_for_args(config, specs, args, continue_on_error=False, log_path=log_path)
                finished_at = datetime.now(timezone.utc)
                totals, summary_text, json_text, _machine_json_text = _emit_summary(
                    results,
                    config,
                    as_json=False,
                    payload_mode=effective_payload,
                    raw_output=raw_output,
                    command=args.command,
                    started_at=started_at,
                    finished_at=finished_at,
                    machine_format="pretty",
                )
                _write_json_payload(args.machine_file, _machine_json_text)
                _maybe_copy(raw_output, summary_text, json_text, args)
                return 0 if totals["failed"] == 0 else 1

    config = _load_command_config(args)
    _apply_runner_cli_options(config, args)
    if args.command in {"test", "t"} and bool(getattr(args, "list_tests", False)):
        return _print_test_catalog(config, args, as_json=False)
    continue_on_error = (args.continue_on_error or args.command == "validate-all") and not args.fail_fast
    log_path = new_log_path()
    log_path.touch(exist_ok=True)
    _ensure_run_id(args)
    _single_run_lock = _acquire_single_run_lock(config, args, log_path=log_path)
    tasker_progress = _live_progress_for_args(args, config)
    if tasker_progress is not None:
        tasker_progress.log_path = str(log_path)

    started_at = datetime.now(timezone.utc)
    if tasker_progress is not None:
        tasker_progress.started()

    cache_cleanup_result: OperationResult | None = None
    if args.command in {"clean", "cl"} and bool(getattr(args, "caches", False)):
        cache_cleanup_result = _build_cache_cleanup_result(dry_run=bool(getattr(args, "dry_run", False)))

    stale_transaction = find_active_transaction(config.repo_root)
    stale_recovery_results: list[OperationResult] = []
    stale_recovery_raw = ""
    if stale_transaction is not None and transaction_pending_validation(stale_transaction):
        active_validation = load_active_session(config.repo_root)
        transaction_data = read_transaction(stale_transaction)
        transaction_session = str(transaction_data.get("validation_session_id") or "")
        session_matches = bool(
            active_validation is not None
            and active_validation.session_id == transaction_session
            and active_validation.transaction_path == str(stale_transaction)
        )
        validation_commands = {"validate", "v", "validation-finalize", "vf"}
        if session_matches and args.command in validation_commands:
            stale_transaction = None
        else:
            pending_result = OperationResult(
                "validation-pending",
                ["devtool", "resume-validation"],
                1,
                0.0,
                (
                    "An applied artifact is waiting for resumable validation.\n"
                    f"transaction: {stale_transaction}\n"
                    f"session: {transaction_session or '-'}\n"
                    "Run 'devtool validate continue' once per phase, or abort it with 'devtool validation-abort'. "
                    "Interrupted validation phases remain pending and the applied changes are not rolled back automatically."
                ),
                [],
                failure_kind="validation_pending",
                metadata={"validation_session_id": transaction_session, "transaction_path": str(stale_transaction)},
            )
            finished_at = datetime.now(timezone.utc)
            totals, summary_text, json_text, machine_text = _emit_summary(
                [pending_result], config, as_json=False, payload_mode=effective_payload,
                raw_output=pending_result.output, command=args.command, started_at=started_at,
                finished_at=finished_at, machine_format="pretty",
            )
            _write_json_payload(args.machine_file, machine_text)
            _finish_live_progress(tasker_progress, totals=totals, results=[pending_result])
            _maybe_copy(pending_result.output, summary_text, json_text, args)
            _release_single_run_lock(args)
            return 1

    if stale_transaction is not None and args.command not in {"history", "clean", "cl"}:
        rollback_result, rollback_raw = _run_transaction_rollback(
            stale_transaction,
            reason="previous devtool apply appears to have been interrupted or killed",
            tasker_progress=tasker_progress,
            log_path=log_path,
            failure_kind="stale_apply_rolled_back",
        )
        if rollback_result.returncode != 0:
            results = [rollback_result]
            raw_output = rollback_raw
            finished_at = datetime.now(timezone.utc)
            totals, summary_text, json_text, _machine_json_text = _emit_summary(
                results,
                config,
                as_json=False,
                payload_mode=effective_payload,
                raw_output=raw_output,
                command=args.command,
                started_at=started_at,
                finished_at=finished_at,
                machine_format="pretty",
            )
            duration_seconds = sum(r.duration_seconds for r in results)
            _write_json_payload(args.machine_file, _machine_json_text)
            _finish_live_progress(tasker_progress, totals=totals, results=results)
            _maybe_copy(raw_output, summary_text, json_text, args)
            if args.command in APPLY_COMMANDS:
                notify_apply_finished(args, config, results, totals)
            _release_single_run_lock(args)
            return 1

        # Applying another artifact may intentionally clean up a stale apply
        # transaction and continue with the new transactional operation. Other
        # commands stop at the recovery boundary: silently running restore,
        # build, test, or validation against a just-rolled-back tree is
        # surprising and can hide why repository state changed.
        if args.command not in APPLY_COMMANDS:
            finished_at = datetime.now(timezone.utc)
            recovery_output = (
                rollback_raw
                + "\n\nRequested command was not executed. Re-run it after reviewing the restored repository."
            )
            totals, summary_text, json_text, machine_text = _emit_summary(
                [rollback_result], config, as_json=False, payload_mode=effective_payload,
                raw_output=recovery_output, command=args.command, started_at=started_at,
                finished_at=finished_at, machine_format="pretty",
            )
            _write_json_payload(args.machine_file, machine_text)
            _finish_live_progress(tasker_progress, totals=totals, results=[rollback_result])
            _maybe_copy(recovery_output, summary_text, json_text, args)
            _release_single_run_lock(args)
            return 1

        stale_recovery_results = [rollback_result]
        stale_recovery_raw = rollback_raw

    if args.command == "batch":
        destructive_batch = any(step.split("=", 1)[0] in {"apply-overlay", "ao", "apply-overlays", "aos", "apply-snapshot", "as", "commit", "ci", "tag"} for step in args.steps)
        if destructive_batch:
            _ensure_safe_repo(config.repo_root, force=args.force, yes=args.yes, command=args.command)

    if args.command in APPLY_COMMANDS:
        if bool(getattr(args, "static_validate", False)):
            args.validate_only = False
        if bool(getattr(args, "defer_validate", False)):
            try:
                results, raw_output = _run_deferred_apply(
                    config,
                    args,
                    parser,
                    log_path=log_path,
                    tasker_progress=tasker_progress,
                )
            except ValidationSessionError as exc:
                results = [OperationResult(
                    "validation-session", ["devtool", "validate", "continue"], 2, 0.0, str(exc), [],
                    failure_kind="validation_session_error",
                )]
                raw_output = str(exc)
        elif _resumable_validation_requested(args):
            try:
                results, raw_output = _run_resumable_apply(
                    config,
                    args,
                    parser,
                    log_path=log_path,
                    tasker_progress=tasker_progress,
                    continue_on_error=continue_on_error,
                )
            except ValidationSessionError as exc:
                results = [OperationResult(
                    "validation-session", ["devtool", "resume-validation"], 2, 0.0, str(exc), [],
                    failure_kind="validation_session_error",
                )]
                raw_output = str(exc)
        else:
            implicit_or_explicit_bundle = False
            if args.command in {"apply-overlay", "ao"}:
                try:
                    implicit_or_explicit_bundle = is_overlay_bundle(resolve_artifact_path(args.artifact))
                except Exception:
                    implicit_or_explicit_bundle = False
            if args.command in {"apply-overlays", "aos"} or implicit_or_explicit_bundle:
                results, raw_output = _run_sequential_overlays(
                    config,
                    args,
                    parser,
                    log_path=log_path,
                    tasker_progress=tasker_progress,
                    continue_on_error=continue_on_error,
                )
            else:
                results, raw_output = _run_apply(config, args, args.command, args.artifact, log_path=log_path, tasker_progress=tasker_progress)
                transaction_path = _apply_transaction_path(results)
                applied_manifest = read_artifact_manifest(args.artifact)
                if results and results[-1].returncode == 0 and not getattr(args, "validate_only", False):
                    config = _reload_config_after_artifact_apply(args, config, applied_manifest)
                _apply_artifact_validation_defaults(config, args, applied_manifest)
                should_validate = getattr(args, "validate", False) or getattr(args, "validate_only", False)
                if should_validate and (getattr(args, "validate_only", False) or results[-1].returncode == 0):
                    skip_restore, skip_build, skip_test = _validation_skip_values(args)
                    validation_results: list[OperationResult] = []
                    if not (skip_restore and skip_build and skip_test):
                        more_results, more_raw = _run_specs_for_args(
                            config,
                            _validation_specs_for_args(config, args, parser),
                            args,
                            continue_on_error=continue_on_error,
                            log_path=log_path,
                            tasker_progress=tasker_progress,
                        )
                        validation_results.extend(more_results)
                        validation_raw = more_raw
                    else:
                        validation_raw = ""
                    if any(result.returncode != 0 for result in validation_results):
                        validation_results, repair_raw, _repair_result = _attempt_auto_repair(
                            config,
                            args,
                            validation_results=validation_results,
                            validation_specs=_validation_specs_for_args(config, args, parser),
                            continue_on_error=continue_on_error,
                            log_path=log_path,
                            tasker_progress=tasker_progress,
                            parser=parser,
                        )
                        if repair_raw:
                            validation_raw = repair_raw
                    results.extend(validation_results)
                    if validation_raw:
                        raw_output += "\n\n" + validation_raw
                    if not any(result.returncode != 0 for result in results):
                        artifact_path = resolve_artifact_path(args.artifact)
                        manifest = read_artifact_manifest(artifact_path)
                        script_result, script_raw = _artifact_script_result(
                            config,
                            args,
                            artifact_path,
                            manifest,
                            target=str(getattr(args, "target", "") or artifact_target(manifest)),
                            solution=str(getattr(args, "solution", "") or artifact_solution(manifest)),
                            log_path=log_path,
                            tasker_progress=tasker_progress,
                        )
                        if script_result is not None:
                            results.append(script_result)
                            raw_output += "\n\n" + script_raw
                    expectation = _validation_expectation_result(read_artifact_manifest(args.artifact), results)
                    if expectation is not None:
                        results.append(expectation)
                        raw_output += "\n\n=== Validation expectations ===\n" + expectation.output
                if getattr(args, "static_validate", False) and results and results[-1].returncode == 0:
                    artifact_path = resolve_artifact_path(args.artifact)
                    manifest = read_artifact_manifest(artifact_path)
                    script_result, script_raw = _artifact_script_result(
                        config, args, artifact_path, manifest,
                        target=str(getattr(args, "target", "") or artifact_target(manifest)),
                        solution=str(getattr(args, "solution", "") or artifact_solution(manifest)),
                        log_path=log_path,
                        tasker_progress=tasker_progress,
                    )
                    if script_result is not None:
                        results.append(script_result)
                        raw_output += "\n\n" + script_raw
                    expectation = _validation_expectation_result(manifest, results)
                    if expectation is not None:
                        results.append(expectation)
                        raw_output += "\n\n=== Validation expectations ===\n" + expectation.output

                validation_policy = artifact_validation_policy(read_artifact_manifest(args.artifact))
                rollback_enabled = _rollback_on_failure_enabled(args, validation_policy)
                if transaction_path and rollback_enabled and _has_failed_after_apply(results):
                    evidence_path = preserve_validation_evidence(
                        config.repo_root, results, run_id=str(getattr(args, "_devtool_run_id", "") or "")
                    )
                    if evidence_path:
                        raw_output += f"\n\nValidation evidence preserved: {evidence_path}"
                    rollback_result, rollback_raw = _run_transaction_rollback(
                        transaction_path,
                        reason="validation or artifact validation script failed after artifact apply",
                        tasker_progress=tasker_progress,
                        log_path=log_path,
                    )
                    results.append(rollback_result)
                    raw_output += "\n\n" + rollback_raw
                results, raw_output = _maybe_commit_after_apply(config, args, results, raw_output, log_path=log_path)
                if transaction_path:
                    if any(result.returncode != 0 for result in results):
                        if rollback_enabled and not any(result.name == "apply-rollback" for result in results):
                            evidence_path = preserve_validation_evidence(
                                config.repo_root, results, run_id=str(getattr(args, "_devtool_run_id", "") or "")
                            )
                            if evidence_path:
                                raw_output += f"\n\nValidation evidence preserved: {evidence_path}"
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
                        delete_on_failure=bool(getattr(args, "delete_artifact_on_failure", False)),
                    )
                    if cleanup_result is not None:
                        results.append(cleanup_result)
                        raw_output += "\n\n$ " + " ".join(cleanup_result.argv) + "\n" + cleanup_result.output
                if args.command in {"apply-overlay", "ao"}:
                    artifact_text = str(getattr(args, "artifact", "") or "")
                    try:
                        overlay_label = resolve_artifact_path(artifact_text).name
                    except Exception:
                        overlay_label = Path(artifact_text).name or "Overlay"
                    _tag_overlay_results(
                        results,
                        index=1,
                        total=1,
                        label=overlay_label,
                        target=config.selected_target or "",
                        artifact=artifact_text,
                    )
                if not getattr(args, "_devtool_history_recorded", False):
                    _record_apply_history(config, args, results)
                    args._devtool_history_recorded = True
    elif args.command in {"validation-finalize", "vf"}:
        try:
            results, raw_output = _handle_validation_finalize(
                config, args, log_path=log_path, tasker_progress=tasker_progress
            )
        except ValidationControlError as exc:
            if _validation_json_requested(args):
                session = None
                try:
                    session = _validation_management_session(config, args, recover=False)
                except ValidationSessionError:
                    pass
                _emit_validation_control_payload(validation_control_error_response("finalize", exc, session), args)
                _finish_live_progress(tasker_progress, totals={"passed": 0, "failed": 1, "skipped": 0}, results=[])
                _release_single_run_lock(args)
                return exc.exit_code
            results = [OperationResult(
                "validation-finalize", ["devtool", "validation-finalize"], 2, 0.0, str(exc), [],
                failure_kind="validation_session_error",
            )]
            raw_output = str(exc)
        except ValidationSessionError as exc:
            if _validation_json_requested(args):
                error = ValidationControlError("invalid_request", str(exc), VALIDATION_EXIT_INVALID_REQUEST)
                _emit_validation_control_payload(validation_control_error_response("finalize", error), args)
                _finish_live_progress(tasker_progress, totals={"passed": 0, "failed": 1, "skipped": 0}, results=[])
                _release_single_run_lock(args)
                return error.exit_code
            results = [OperationResult(
                "validation-finalize", ["devtool", "validation-finalize"], 2, 0.0, str(exc), [],
                failure_kind="validation_session_error",
            )]
            raw_output = str(exc)
    elif args.command in {"clean", "cl"}:
        specs = build_specs(config, args.command)
        results, raw_output = _run_specs_for_args(config, specs, args, continue_on_error=continue_on_error, log_path=log_path, tasker_progress=tasker_progress)
        if cache_cleanup_result is not None:
            results = [cache_cleanup_result, *results]
            raw_output = cache_cleanup_result.output + ("\n\n" + raw_output if raw_output else "")
    elif args.command in {"validate", "v", "validate-all", "wine-validate", "wv"}:
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
                    as_json=False,
                    payload_mode=effective_payload,
                    raw_output=run_raw,
                    command=args.command,
                    started_at=run_started,
                    finished_at=run_finished,
                    machine_format="pretty",
                )
                _write_json_payload(args.machine_file, run_machine)
                _maybe_copy(run_raw, run_summary, run_json, args)
                return 0 if run_totals["failed"] == 0 else 1
            try:
                return watch_loop(config.repo_root, _run_once, interval=args.watch_interval)
            finally:
                _release_single_run_lock(args)
        if _resumable_validation_requested(args):
            if getattr(args, "watch", False):
                parser.error("--watch cannot be combined with resumable sequential validation.")
            try:
                _validate_validation_api_request(args)
                results, raw_output, _session = _run_validation_session(
                    config,
                    args,
                    parser,
                    log_path=log_path,
                    tasker_progress=tasker_progress,
                    continue_on_error=continue_on_error,
                )
                args._devtool_validation_final_session = _session
            except ValidationControlError as exc:
                if _validation_json_requested(args):
                    session = None
                    try:
                        session = _validation_management_session(config, args, recover=False)
                    except ValidationSessionError:
                        pass
                    _emit_validation_control_payload(validation_control_error_response(
                        str(getattr(args, "_devtool_validation_api_action", "") or "run"), exc, session
                    ), args)
                    _finish_live_progress(tasker_progress, totals={"passed": 0, "failed": 1, "skipped": 0}, results=[])
                    _release_single_run_lock(args)
                    return exc.exit_code
                results = [OperationResult(
                    "validation-session", ["devtool", "resume-validation"], 2, 0.0, str(exc), [],
                    failure_kind="validation_session_error",
                )]
                raw_output = str(exc)
            except ValidationSessionError as exc:
                if _validation_json_requested(args) and str(getattr(args, "_devtool_validation_api_action", "") or ""):
                    error = ValidationControlError("invalid_request", str(exc), VALIDATION_EXIT_INVALID_REQUEST)
                    _emit_validation_control_payload(validation_control_error_response(
                        str(getattr(args, "_devtool_validation_api_action", "") or "run"), error
                    ), args)
                    _finish_live_progress(tasker_progress, totals={"passed": 0, "failed": 1, "skipped": 0}, results=[])
                    _release_single_run_lock(args)
                    return error.exit_code
                results = [OperationResult(
                    "validation-session", ["devtool", "resume-validation"], 2, 0.0, str(exc), [],
                    failure_kind="validation_session_error",
                )]
                raw_output = str(exc)
        else:
            specs = _validation_specs_for_args(config, args, parser)
            results, raw_output = _run_specs_for_args(config, specs, args, continue_on_error=continue_on_error, log_path=log_path, tasker_progress=tasker_progress)
    elif args.command == "batch":
        results, raw_output = _handle_batch(config, args, log_path=log_path, tasker_progress=tasker_progress)
    else:
        planning_config = _config_for_validation_planning(config, args)
        specs = build_specs(planning_config, args.command)
        specs = _maybe_apply_test_no_build_flag(specs, args, parser)
        results, raw_output = _run_specs_for_args(planning_config, specs, args, continue_on_error=continue_on_error, log_path=log_path, tasker_progress=tasker_progress)

    if stale_recovery_results:
        results = [*stale_recovery_results, *results]
        raw_output = stale_recovery_raw + ("\n\n" + raw_output if raw_output else "")

    validation_api_action = str(getattr(args, "_devtool_validation_api_action", "") or "")
    validation_api_session = getattr(args, "_devtool_validation_final_session", None)
    if validation_api_action and _validation_json_requested(args) and isinstance(validation_api_session, ValidationSession):
        phase_results = [
            item for item in results
            if str(item.metadata.get("validation_phase_key") or "")
        ]
        result_data = [{
            "name": item.name,
            "returncode": int(item.returncode),
            "failure_kind": str(item.failure_kind or ""),
            "skipped": bool(item.skipped),
            "duration_seconds": float(item.duration_seconds or 0.0),
            "phase_key": str(item.metadata.get("validation_phase_key") or ""),
            "phase_name": str(item.metadata.get("validation_phase_name") or item.name),
        } for item in phase_results]
        payload = validation_control_response(
            validation_api_action,
            accepted=True,
            session=validation_api_session,
            reason=validation_control_reason(validation_api_session),
            previous_revision=getattr(args, "_devtool_validation_previous_revision", None),
            message=(
                "Validation request completed." if validation_api_session.status not in {"blocked", "recovery-required", "rollback-failed"}
                else "Validation request completed with a session state requiring attention."
            ),
            data={
                "scope": str(getattr(args, "validation_run_scope", "") or ""),
                "status": validation_status_api_payload(validation_api_session),
                "results": result_data,
            },
        )
        _emit_validation_control_payload(payload, args)
        totals = {
            "passed": sum(1 for item in results if item.returncode == 0 and not item.skipped),
            "failed": sum(1 for item in results if item.returncode != 0),
            "skipped": sum(1 for item in results if item.skipped),
        }
        _finish_live_progress(tasker_progress, totals=totals, results=results)
        _write_performance_report(args, results, started_at=started_at, finished_at=datetime.now(timezone.utc), config=config)
        _release_single_run_lock(args)
        return validation_control_exit_code(validation_api_session)

    finished_at = datetime.now(timezone.utc)
    totals, summary_text, json_text, _machine_json_text = _emit_summary(
        results,
        config,
        as_json=False,
        payload_mode=effective_payload,
        raw_output=raw_output,
        command=args.command,
        started_at=started_at,
        finished_at=finished_at,
        machine_format="pretty",
    )
    duration_seconds = sum(r.duration_seconds for r in results)
    _write_json_payload(args.machine_file, _machine_json_text)
    _finish_live_progress(tasker_progress, totals=totals, results=results)
    _maybe_copy(raw_output, summary_text, json_text, args)
    if args.command in APPLY_COMMANDS:
        notify_apply_finished(args, config, results, totals)
    _write_performance_report(args, results, started_at=started_at, finished_at=finished_at, config=config)
    _release_single_run_lock(args)
    return 0 if totals["failed"] == 0 else 1
