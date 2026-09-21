from __future__ import annotations

import argparse
import hashlib
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
import traceback
import resource
import tempfile
import zipfile
from contextlib import contextmanager, redirect_stdout
import io
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm

from .ai_prompt import render_ai_prompt, resolve_ai_prompt_options
from .affected import AffectedSelectionError, analyze_affected_targets
from .apply_notifications import notify_apply_finished
from .bootstrap import (
    apply_registered_bootstrap_policy,
    bootstrap_status,
    install_bootstrap,
    parse_destination as parse_bootstrap_destination,
    remove_receipt as remove_bootstrap_receipt,
    render_bootstrap_result,
    request_from_args as bootstrap_request_from_args,
)
from .artifacts import (
    SUPPORTED_ARCHIVE_SUFFIXES,
    append_history,
    artifact_commit_policy,
    artifact_cleanup_paths,
    artifact_finalize_phases,
    artifact_solution,
    artifact_target,
    artifact_validation_policy,
    artifact_validation_pytest_selection,
    artifact_validation_task_declaration,
    artifact_validation_tasks,
    artifact_workspaces,
    create_overlay_bundle,
    diff_artifact,
    format_preview,
    is_overlay_bundle,
    list_archive_members,
    materialize_overlay_bundle_entries,
    overlay_bundle_targets,
    latest_commit_hash,
    load_history,
    make_history_entry,
    preview_artifact,
    read_artifact_manifest,
    read_delete_manifest,
    resolve_artifact_path,
    sha256_file,
    validate_artifact,
    validate_overlay_bundle,
)
from .cache_cleanup import cleanup_devtool_caches
from .artifact_validation import run_artifact_finalize_script, run_artifact_validation_script
from .hooks.history import collect_hook_events
from .artifact_inspection import inspect_artifact
from .artifact_registry import load_registry, find_record, verify_record, prune_registry
from .api_output import extract_universal_json_flag, run_json_invocation
from .core import configure_logging
from .core.events import BuildFinished, BuildStarted, OverlayApplied, configure_event_console, publish_event
from .overlay import check_overlay, rebase_overlay
from .plugins import discover_plugins, doctor_plugin
from .doctor import collect_doctor_checks, doctor_payload
from .validation_profiles import apply_profile as apply_validation_workflow_profile, available_profiles as available_validation_profiles
from .detect import detect_repo, render_detect_text
from .diagnostics import issue_counts_toward_target_warning_budget, target_warning_count
from .execution import apply_matrix
from .execution_environments import (
    AUTO as EXECUTION_AUTO,
    CHROOT as EXECUTION_CHROOT,
    NATIVE_TERMUX as EXECUTION_NATIVE_TERMUX,
    SSH as EXECUTION_SSH,
    PROOT_DISTRO as EXECUTION_PROOT_DISTRO,
    EXECUTION_ENVIRONMENTS,
    assert_chroot_path_safe,
    detect_current_environment,
    inside_chroot,
    preflight_execution,
    resolve_execution_plan,
)
from .handoff import render_handoff
from .watch import watch_loop
from .clipboard import copy_text, prepare_clipboard_text
from .console_utils import create_console, resolve_cli_path
from .completion import ZSH_COMPLETION
from .completion_state import build_completion_state, completion_values
from .compatibility import compatibility_report, render_compatibility_text
from .final_seal import final_seal_report, render_final_seal_text
from .target_seal import target_seal_report, render_target_seal_text
from .config_migration import analyze_config_migration, migrate_config_text, migration_report
from .config import (
    detect_project_kind,
    discover_dotnet_candidates,
    global_config_doctor_entries,
    global_config_path,
    find_config,
    load_global_config,
    load_project_config,
    minimal_global_config_template,
    resolve_repository_root,
    resolve_config_dict,
)
from .log_commands import choose_active_log, format_log_entries, list_log_entries, open_log, resolve_log_path, tail_log
from .logstore import latest_log, new_log_path, redact_sensitive_text, redact_sensitive_value, set_log_dir_override, state_dir as log_state_dir, write_log
from .models import CommandSpec, Issue, OperationResult, ProjectConfig
from .project_runners import resolve_validation_plan
from .repair_apply import apply_auto_source_repairs, restore_source_backups
from .repair_policy import repair_summary_lines_from_payload, repair_summary_lines_from_results
from .runners import apply_overlay, apply_overlay_bundle, apply_snapshot, add_no_build_to_dotnet_test_specs, build_specs, build_validation_specs, dotnet_build_server_shutdown_enabled, get_project_runner, git_commit, run_specs, runner_capabilities, shutdown_dotnet_build_server, write_template_config
from .workspace import WorkspaceConfigError, resolve_workspace_targets, workspace_as_dict, workspace_group_memberships
from .workflow_runtime import build_workspace_specs, build_workspace_validation_specs, plan_workspace_targets
from .workflow_scheduler import (
    WorkflowExecutionPlan,
    build_execution_plan_from_specs,
    run_workflow_specs,
)
from .summary import artifact_confirmation_text, build_json_summary, build_runner_json, render_artifact_manifest, render_status_table, render_summary, summarize_failure_output
from .tasker import DEFAULT_NOTIFICATION_ID, DEFAULT_TASKER_DIR, TaskerProgressSink, build_tasker_sink, hud_disabled_by_environment, start_auto_hud
from .toolchains import apply_destination_toolchain_wrapper, apply_toolchain_wrapper, toolchain_doctor_rows
from .toolchain_requirements import analyze_toolchain_requirements
from .termux_toolchains import (
    VALID_GRADLE_PROVIDERS, VALID_JAVA_PROVIDERS, VALID_NATIVE_PROVIDERS,
    VALID_ANDROID_SDK_PROVIDERS,
    VALID_NATIVE_CACHE_PROVIDERS, VALID_ANDROID_BUILD_TOOLS_PROVIDERS,
)
from .termux_diagnostics import (
    format_package_recommendations_json,
    provider_explanation_rows,
    termux_integration_doctor_rows,
    termux_report_payload,
)
from .transactions import (
    abandon_transaction,
    affected_paths_from_preview,
    devtool_xdg_dir,
    find_active_transaction,
    find_active_transactions,
    list_active_transaction_records,
    resolve_active_transaction,
    mark_transaction_completed,
    mark_transaction_pending_validation,
    mark_transaction_validated,
    mark_transaction_validation_blocked,
    mark_transaction_validation_running,
    read_transaction,
    read_transaction_summary,
    register_transaction_paths,
    record_transaction_git_commit,
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
    live_running_phase as live_validation_phase,
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
    resolve_resumable_session,
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
    stored_specs as stored_validation_specs,
    checkpoint_text as validation_checkpoint_text,
    sync_plan as sync_validation_plan,
    validation_session_target_hint,
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
    "--group",
    "--solution",
    "--project",
    "--project-type",
    "--runner",
    "--affected-base",
    "--affected-head",
    "--log-dir",
    "--performance-file",
    "--machine-file",
    "--copy-via",
    "--theme",
    "--stdout-payload",
    "--ui-profile",
    "--ui-theme",
    "--ui-density",
    "--ui-icons",
    "--ui-motion",
    "--ui-verbosity",
    "--hud-mode",
    "--chroot",
    "--ssh",
    "--ssh-host",
    "--ssh-port",
    "--ssh-user",
    "--ssh-identity",
    "--ssh-session",
    "--ssh-cwd",
    "--ssh-tool",
    "--ssh-mode",
    "--ssh-stage-dir",
    "--execution-environment",
    "--proot-distro",
    "--proot-user",
    "--proot-cwd",
    "--proot-repo-mount",
    "--proot-bind",
    "--proot-env",
    "--proot-stage-dir",
    "--termux-resource-profile",
    "--shared-storage-policy",
    "--artifact-export",
    "--bootstrap-downloads",
    "--toolchain-profile",
    "--android-sdk-root",
    "--android-sdk-provider",
    "--gradle-command",
    "--gradle-provider",
    "--java-home",
    "--java-provider",
    "--gradle-user-home",
    "--native-provider",
    "--native-cache",
    "--android-build-tools-provider",
    "--aapt2-provider",
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


def _add_python_format_flags(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument(
        "--format",
        "--fix",
        dest="python_format_fix",
        action="store_true",
        help="Autofix Python formatter output during lint/validation; default is check-only",
    )


def _add_apply_validation_flags(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--validate", action="store_true", help="Run validation after applying the artifact; required artifact validation is enabled automatically")
    _add_python_format_flags(cmd)
    cmd.add_argument("--defer-validate", action="store_true", help="Apply now, preserve rollback state, and create a resumable validation session without running a phase")
    cmd.add_argument("--static-validate", action="store_true", help="Apply artifact and run only artifact/static validation scripts without Gradle validation phases")
    cmd.add_argument("--no-validate", action="store_true", help="Disable validation requested or required by the artifact manifest")
    cmd.add_argument("--test-profile", default="", help="Saved test profile used by artifact validation")
    _add_validation_test_selection_flags(cmd)
    cmd.add_argument("--validate-only", action="store_true", help="Skip apply and only run validation")
    _add_validation_skip_flags(cmd)
    _add_resumable_validation_flags(cmd)
    _add_validation_resource_override_flags(cmd)
    _add_workflow_scheduler_flags(cmd)


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
    cmd.add_argument("--rebase", action="store_true", help="Rebase a single overlay inventory against the current repository before applying")


def _add_apply_command_flags(cmd: argparse.ArgumentParser, *, name: str) -> None:
    if name == "apply-overlays":
        cmd.add_argument("artifact", nargs="+", help="Overlay bundle archive, or multiple overlay archives to bundle/apply transactionally")
    else:
        cmd.add_argument("artifact")
    _add_apply_validation_flags(cmd)
    _add_apply_policy_flags(cmd)


def _add_workspace_scope_flags(cmd: argparse.ArgumentParser, *, include_affected: bool = True) -> None:
    """Allow workspace selectors after a subcommand without overriding global values."""
    cmd.add_argument("--target", default=argparse.SUPPRESS, help="Named target/profile from .devtool.toml")
    cmd.add_argument("--group", action="append", default=argparse.SUPPRESS, help="Workspace target group; repeatable")
    cmd.add_argument("--all-targets", action="store_true", default=argparse.SUPPRESS, help="Select every configured workspace target")
    if include_affected:
        cmd.add_argument("--affected", action="store_true", default=argparse.SUPPRESS, help="Select dependency-aware targets affected by Git changes")
        cmd.add_argument("--affected-base", default=argparse.SUPPRESS, help="Git base revision for affected selection")
        cmd.add_argument("--affected-head", default=argparse.SUPPRESS, help="Git head revision for affected selection")
    cmd.add_argument("--variant", default=argparse.SUPPRESS, help="Variant within the selected workspace target(s)")


def _add_workflow_scheduler_flags(cmd: argparse.ArgumentParser) -> None:
    group = cmd.add_mutually_exclusive_group()
    group.add_argument(
        "--workflow-parallel",
        dest="workflow_parallel",
        action="store_true",
        default=None,
        help="Run independent workflow DAG nodes concurrently only when dependency, read/write-scope, and resource checks prove it safe",
    )
    group.add_argument(
        "--no-workflow-parallel",
        dest="workflow_parallel",
        action="store_false",
        help="Force workflow DAG nodes to execute sequentially",
    )
    cmd.add_argument(
        "--workflow-max-workers",
        type=int,
        default=0,
        metavar="COUNT",
        help="Maximum concurrent safe workflow nodes; 0 uses workspace policy/automatic default",
    )


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
    project.add_argument("--group", action="append", default=[], help="Workspace target group from [workspace.groups]; repeatable")
    project.add_argument("--all-targets", action="store_true", help="Select every configured workspace target")
    project.add_argument("--affected", action="store_true", help="Select workspace targets affected by current Git changes; may be combined with --group/--all-targets as a candidate filter")
    project.add_argument("--affected-base", default="", help="Git base revision for --affected selection (default: staged/unstaged/untracked working tree)")
    project.add_argument("--affected-head", default="", help="Git head revision for --affected selection; with --affected-base uses base...head")
    project.add_argument("--variant", default="", help="Build/test variant within the selected target")
    project.add_argument("--solution", default="", help="Exact solution path for restore/build/test")
    project.add_argument("--project", default="", help="Exact project path for run/validation")
    project.add_argument(
        "--runner",
        "--project-type",
        dest="project_type",
        choices=["auto", "dotnet", "android", "gradle", "python", "node", "rust", "go", "maven", "cmake", "meson", "make", "command", "unknown"],
        default="auto",
        help="Override the project runner for this run; --project-type remains a compatibility alias",
    )

    execution = parser.add_argument_group("Execution")
    execution.add_argument("-v", "--verbose", action="store_true", help="Stream full command output")
    execution.add_argument("-q", "--quiet", action="store_true", help="Suppress normal human output; errors still print")
    execution.add_argument("--trace", action="store_true", help="Enable architecture event tracing and full exception tracebacks")
    execution.add_argument("-y", "--yes", action="store_true", help="Assume yes for confirmations")
    execution.add_argument("-f", "--force", action="store_true", help="Force dangerous actions")
    execution.add_argument("--allow-pending-validation", action="store_true", help="Allow a new operation while another artifact remains pending validation; rollback state is preserved")
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
    output_debug = output.add_mutually_exclusive_group()
    output_debug.add_argument(
        "--output-debug",
        dest="output_debug",
        action="store_true",
        default=None,
        help="Persist the rendered terminal summary and any --copy clipboard payload for debugging without echoing captured payload bodies (also DEVTOOL_OUTPUT_DEBUG=1)",
    )
    output_debug.add_argument(
        "--no-output-debug",
        dest="output_debug",
        action="store_false",
        help="Disable terminal/clipboard output debugging even when DEVTOOL_OUTPUT_DEBUG is enabled",
    )
    output.add_argument("--theme", choices=["auto", "plain"], default="auto", help="Summary theme")
    output.add_argument("--log-dir", default="", help="Override the directory used for command logs (also DEVTOOL_LOG_DIR)")

    presentation = parser.add_argument_group("UI presentation")
    presentation.add_argument("--ui-profile", default="", help="UI profile: auto, minimal, standard, developer, debug, or a configured [ui.profiles.*] name")
    presentation.add_argument("--ui-theme", choices=["auto", "light", "dark"], default="", help="Override the shared UI theme preference for this invocation")
    presentation.add_argument("--ui-density", choices=["dense", "compact", "comfortable", "detailed"], default="", help="Override shared information density for this invocation")
    presentation.add_argument("--ui-icons", choices=["auto", "nerd", "unicode", "ascii", "native"], default="", help="Override semantic icon rendering mode")
    presentation.add_argument("--ui-motion", choices=["auto", "full", "reduced"], default="", help="Override shared motion preference")
    presentation.add_argument("--ui-verbosity", choices=["compact", "normal", "detailed", "debug"], default="", help="Override shared information verbosity")

    hud = parser.add_argument_group("HUD and app bridge")
    hud.add_argument("--auto-hud", "--hud-auto", dest="auto_hud", action="store_true", default=None, help="Auto-start or reuse the selected HUD while progress is emitted (default)")
    hud.add_argument("--no-hud", dest="auto_hud", action="store_false", help="Do not publish live HUD progress for this command")
    hud.add_argument("--hud-mode", default="desktop", help="HUD backend: desktop, android-bridge, desktop-window (GTK4), web, studio, or terminal")
    hud_presentation = hud.add_mutually_exclusive_group()
    hud_presentation.add_argument("--hud-overlay", action="store_true", help="On Android, also show the run in the user-enabled floating overlay HUD")
    hud_presentation.add_argument("--hud-no-overlay", action="store_true", help="On Android, keep the update in the app without opening the floating overlay")

    environment = parser.add_argument_group("Execution environment")
    exec_group = environment.add_mutually_exclusive_group()
    exec_group.add_argument("--host", action="store_true", help="Force command phases to run in devtool's current host environment")
    exec_group.add_argument("--chroot", default="", help="Run command phases inside a named chroot via chrootctl exec")
    exec_group.add_argument("--ssh", "--ssh-host", dest="ssh", default="", metavar="HOST", help="Use HOST as the SSH execution target (whole devtool or command-phase mode)")
    exec_group.add_argument("--proot-distro", dest="proot_distro", default="", metavar="DISTRO", help="Run command phases inside a PRoot-Distro container; guest does not need devtool")
    exec_group.add_argument(
        "--execution-environment",
        choices=EXECUTION_ENVIRONMENTS,
        default=EXECUTION_AUTO,
        help="Execution backend: auto, native-termux, chroot, proot-distro, desktop-linux, ssh, android-device, or external-device",
    )
    environment.add_argument("--ssh-port", type=int, default=0, metavar="PORT", help="SSH port (default: 22 or [ssh].port)")
    environment.add_argument("--ssh-user", default="", metavar="USER", help="SSH login user")
    environment.add_argument("--ssh-identity", default="", metavar="PATH", help="SSH private-key identity file")
    environment.add_argument("--ssh-session", default="", metavar="NAME", help="tmux session name; stable names can be reattached")
    environment.add_argument("--ssh-cwd", default="", metavar="PATH", help="Working directory on the SSH host; omitted means the remote login default")
    environment.add_argument("--ssh-tool", default="", metavar="COMMAND", help="Remote devtool executable used by --ssh-mode devtool (default: devtool)")
    environment.add_argument("--ssh-mode", choices=["devtool", "commands"], default="", help="SSH mode: delegate the whole devtool run, or keep devtool local and run command phases remotely")
    environment.add_argument("--ssh-stage-dir", default="", metavar="PATH", help="Remote staging root for copied overlays/helper files")
    environment.add_argument("--ssh-no-artifact-copy", action="store_true", help="Disable automatic SSH/SCP artifact transfer")
    environment.add_argument("--ssh-detached", action="store_true", help="Start/reuse the remote tmux session without attaching (whole-run devtool mode)")
    environment.add_argument("--proot-user", default="", metavar="USER", help="PRoot-Distro guest user (default: root or [proot].user)")
    environment.add_argument("--proot-cwd", default="", metavar="PATH", help="Initial absolute working directory inside the PRoot guest")
    environment.add_argument("--proot-repo-mount", default="", metavar="PATH", help="Absolute guest mount point for the current repository")
    environment.add_argument("--proot-bind", action="append", default=[], metavar="HOST:GUEST", help="Additional PRoot bind; repeatable, guest destination must be absolute")
    environment.add_argument("--proot-env", action="append", default=[], metavar="NAME=VALUE", help="Explicit environment variable passed to the PRoot guest; repeatable")
    environment.add_argument("--proot-no-auto-bind-repo", action="store_true", help="Do not automatically bind the repository into the PRoot guest")
    environment.add_argument("--proot-shared-home", action="store_true", help="Ask PRoot-Distro to share the host home with the guest")
    environment.add_argument("--proot-shared-tmp", action="store_true", help="Ask PRoot-Distro to share Termux tmp with the guest")
    proot_isolation = environment.add_mutually_exclusive_group()
    proot_isolation.add_argument("--proot-isolated", action="store_true", help="Use PRoot-Distro isolated mode (Termux hosts only)")
    proot_isolation.add_argument("--proot-minimal", action="store_true", help="Use PRoot-Distro minimal mode (Termux hosts only)")
    environment.add_argument("--proot-stage-dir", default="", metavar="PATH", help="Host staging directory bound into the PRoot guest")
    termux_profile = environment.add_mutually_exclusive_group()
    termux_profile.add_argument("--termux-resource-profile", choices=["auto", "automatic", "conservative", "balanced", "performance", "off", "none"], default="", help="Termux resource defaults for workers/JVM/native concurrency; off/none bypasses automatic Termux resource defaults")
    termux_profile.add_argument("--no-termux-resource-profile", dest="termux_resource_profile", action="store_const", const="off", help="Bypass automatic Termux resource defaults for this invocation")
    wake_lock_group = environment.add_mutually_exclusive_group()
    wake_lock_group.add_argument("--termux-wake-lock", action="store_true", help="Acquire a Termux wake lock while command phases run")
    wake_lock_group.add_argument("--no-termux-wake-lock", action="store_true", help="Disable Termux wake-lock integration for this run")
    environment.add_argument("--shared-storage-policy", choices=["allow", "warn", "deny"], default="", help="Policy for repositories on Android shared storage")
    environment.add_argument("--allow-shared-storage-build", action="store_true", help="Allow a build from Android shared storage for this run")
    environment.add_argument("--artifact-export", default="", metavar="DIR", help="Copy verified artifacts to DIR after successful local verification")
    environment.add_argument("--no-auto-chroot", action="store_true", help="On Termux, do not auto-route command phases through chrootctl")

    bootstrap = parser.add_argument_group("Toolchain bootstrap")
    bootstrap.add_argument("--no-bootstrap-downloads", action="store_true", help="Never download or install missing toolchain components")
    bootstrap.add_argument("--bootstrap-downloads", choices=["auto", "missing", "force", "never"], default="", help="Override receipt/config toolchain provisioning policy for this run")
    bootstrap.add_argument("--deny-network", action="store_true", help="Deny Devtool-managed network downloads for this run")
    bootstrap.add_argument("--offline", action="store_true", help="Alias for --no-bootstrap-downloads --deny-network and Gradle offline mode")
    bootstrap.add_argument("--toolchain-profile", default="", help="Chroot/toolchain profile name from global or repo config")
    bootstrap.add_argument("--android-sdk-root", default="", help="Android SDK root for Android/Gradle projects")
    bootstrap.add_argument("--android-sdk-provider", choices=sorted(VALID_ANDROID_SDK_PROVIDERS), default="", help="Android SDK provider: auto, TermuxVoid, HomuHomu ARM64 Android SDK, or managed Google SDK")
    bootstrap.add_argument("--gradle-command", default="", help="Gradle or gradlew executable for Android/Gradle projects")
    bootstrap.add_argument("--gradle-provider", choices=sorted(VALID_GRADLE_PROVIDERS), default="", help="Gradle provider for Android/Gradle projects: auto, wrapper, termux, system, or download")
    bootstrap.add_argument("--java-home", default="", help="JDK home for Android/Gradle projects")
    bootstrap.add_argument("--java-provider", choices=sorted(VALID_JAVA_PROVIDERS), default="", help="Java provider for Android/Gradle projects: auto, termux, or system")
    bootstrap.add_argument("--gradle-user-home", default="", help="GRADLE_USER_HOME for Android/Gradle projects")
    bootstrap.add_argument("--native-provider", choices=sorted(VALID_NATIVE_PROVIDERS), default="", help="Native provider for Android/native projects: auto, termux-sysroot, ndk, or system")
    bootstrap.add_argument("--native-cache", choices=sorted(VALID_NATIVE_CACHE_PROVIDERS), default="", help="Native compiler cache provider: auto, ccache, or off")
    bootstrap.add_argument("--android-build-tools-provider", choices=sorted(VALID_ANDROID_BUILD_TOOLS_PROVIDERS), default="", help="Android native build-tools provider: auto, termux, sdk, commit451, or none")
    bootstrap.add_argument("--aapt2-provider", choices=["auto", "termux", "sdk", "revanced", "none"], default="", help="AAPT2 provider for Android builds")

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
        ("release", ["rel"]),
        ("clean", ["cl"]),
        ("inspect", ["info"]),
        ("status", ["st"]),
        ("doctor", ["dr"]),
    ]:
        cmd = subparsers.add_parser(name, aliases=aliases)
        _add_workspace_scope_flags(cmd)
        if name in {"restore", "build", "test", "lint", "package", "release", "validate", "validate-all", "wine-validate"}:
            _add_workflow_scheduler_flags(cmd)
        # Resource/runtime overrides are valid after the subcommand as well as
        # before it, matching the global parser surface used by validation.
        if name in {"restore", "build", "test", "lint"}:
            _add_validation_resource_override_flags(cmd)
        if name in {"build", "package"}:
            cmd.add_argument(
                "-o",
                "--artifact-output-dir",
                "--output-dir",
                dest="artifact_output_dir",
                default="",
                help="Copy produced artifacts here (default: .devtool/build-artifacts)",
            )
            cmd.add_argument(
                "-n",
                "--artifact-output-name",
                "--output-name",
                dest="artifact_output_name",
                default="",
                help="Base filename for copied artifacts",
            )
            cmd.add_argument(
                "--artifact-collision",
                choices=["unique", "replace", "fail"],
                default="",
                help="Managed artifact collision policy (default: unique)",
            )
            verification = cmd.add_mutually_exclusive_group()
            verification.add_argument(
                "--verify-artifacts",
                dest="verify_artifacts_cli",
                action="store_true",
                default=None,
                help="Verify produced artifacts before registration/export",
            )
            verification.add_argument(
                "--no-verify-artifacts",
                dest="verify_artifacts_cli",
                action="store_false",
                default=None,
                help="Collect artifacts without content verification",
            )
        if name == "release":
            cmd.add_argument("--device-tests", choices=("disabled", "when-device-present", "required"), default="", help="Release device/instrumentation policy")
            cmd.add_argument("--previous-apk", default="", help="Previous APK used by upgrade-smoke")
            cmd.add_argument("--package", dest="release_package", default="", help="Application package override for smoke gates")
            cmd.add_argument("--activity", dest="release_activity", default="", help="Launch activity override for smoke gates")
            cmd.add_argument("--evidence-output", default="", help="Explicit immutable release-evidence JSON path")
            cmd.add_argument("--no-unit-tests", action="store_true")
            cmd.add_argument("--no-lint", action="store_true")
            cmd.add_argument("--no-install-smoke", action="store_true")
            cmd.add_argument("--no-upgrade-smoke", action="store_true")
            cmd.add_argument("--no-verify-signature", action="store_true")
            cmd.add_argument("--no-verify-certificate-identity", action="store_true")
            cmd.add_argument("--format", choices=("text", "json"), default="text")
        if name == "doctor":
            cmd.add_argument("--fix", action="store_true", help="Create safe devtool state/cache directories")
            cmd.add_argument("--explain", action="store_true", help="Explain Termux provider selection and rejection reasons")
            cmd.add_argument("--json", action="store_true", help="Emit structured runtime, Android, device, project, and expanded-workflow health checks")
            cmd.add_argument("--workflow", default="all", help="Workflow/action to analyze across the selected target scope; default: all declared workflows")
        if name == "clean":
            cmd.add_argument("--caches", action="store_true", help="Also prune repository-scoped devtool-managed caches and transient state")
            cmd.add_argument("--space", action="store_true", help="Prune safe global Android/toolchain caches managed by Devtool")
            cmd.add_argument(
                "--gradle-user-cache",
                action="store_true",
                help="With --space, also prune old version-specific ~/.gradle caches and build-cache-1; modules/wrapper caches are preserved",
            )
            cmd.add_argument(
                "--space-target-free-mb",
                type=int,
                default=0,
                help="With --space, stop after reaching this much free space; 0 removes all safe candidates",
            )
            cmd.add_argument(
                "--managed-gradle-retention",
                type=int,
                default=2,
                help="With --space, keep this many newest Devtool-managed Gradle distributions",
            )
        if name == "test":
            cmd.add_argument("test_names", nargs="*", help="First-class [[test]] IDs from .devtool.toml")
            cmd.add_argument("--all", dest="all_declared_tests", action="store_true", help="Run all first-class [[test]] declarations")
            cmd.add_argument("--no-build", action="store_true", default=argparse.SUPPRESS, help="Pass --no-build to dotnet test")
            cmd.add_argument("--list-tests", "--list", action="store_true", help="Discover and list tests without executing them; pytest uses native --collect-only discovery")
            _add_pytest_selection_flags(cmd)
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
        if name == "lint":
            _add_python_format_flags(cmd)
        if name in {"validate", "validate-all", "wine-validate"}:
            _add_python_format_flags(cmd)
            _add_validation_skip_flags(cmd)
            if name == "validate":
                cmd.add_argument("--test", dest="declared_tests", action="append", default=[], help="Run a first-class [[test]] declaration during validation; repeatable")
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
            _add_validation_resource_override_flags(cmd)
            if name == "validate":
                _add_resumable_validation_flags(cmd)

    variants_cmd = subparsers.add_parser("variants", help="List Android variant execution profiles for the selected target")
    _add_workspace_scope_flags(variants_cmd)

    for name, aliases, mode in [
        ("start-validation", ["sv"], "start"),
        ("resume-validation", ["rv"], "resume"),
    ]:
        cmd = subparsers.add_parser(name, aliases=aliases, help=f"{mode.title()} a persistent sequential validation session")
        cmd.set_defaults(_devtool_validation_mode=mode)
        _add_python_format_flags(cmd)
        _add_validation_skip_flags(cmd)
        _add_validation_test_selection_flags(cmd)
        cmd.add_argument("--test-profile", default="", help="Saved test profile used by validation")
        cmd.add_argument("--matrix", default="", help="Comma-separated runtime IDs, targets, or variants")
        cmd.add_argument("--matrix-mode", choices=["rid", "target", "variant"], default="rid")
        _add_resumable_validation_flags(cmd)
        _add_validation_resource_override_flags(cmd)

    validation_menu = subparsers.add_parser("validation-menu", aliases=["vm"], help="Open the interactive validation menu and wizard")
    validation_menu.add_argument("--no-fzf", action="store_true", help="Use the numbered terminal fallback even when fzf is installed")
    validation_menu.add_argument("--runtime-tasks", action="store_true", help="Query Gradle for the full task catalog when the menu opens")
    validation_menu.add_argument("--wizard", action="store_true", help="Start directly in the guided validation wizard")
    validation_menu.add_argument("--recipe", default="", help="Open a built-in or saved validation recipe directly")
    validation_menu.add_argument("--artifact", default="", help="Artifact path used to expand a recipe placeholder")
    validation_menu.add_argument("--matrix-values", default="", help="Comma-separated matrix values used to expand a recipe placeholder")
    validation_menu.add_argument("--print-command", action="store_true", help="Print the generated command instead of opening the review screen")

    menu_cmd = subparsers.add_parser("menu", help="Open an interactive Devtool menu")
    menu_sub = menu_cmd.add_subparsers(dest="menu_command", required=True)
    menu_validation = menu_sub.add_parser("validation", aliases=["validate", "v"], help="Open the validation menu and wizard")
    menu_validation.set_defaults(normalized_command="validation-menu")
    menu_validation.add_argument("--no-fzf", action="store_true", help="Use the numbered terminal fallback even when fzf is installed")
    menu_validation.add_argument("--runtime-tasks", action="store_true", help="Query Gradle for the full task catalog when the menu opens")
    menu_validation.add_argument("--wizard", action="store_true", help="Start directly in the guided validation wizard")
    menu_validation.add_argument("--recipe", default="", help="Open a built-in or saved validation recipe directly")
    menu_validation.add_argument("--artifact", default="", help="Artifact path used to expand a recipe placeholder")
    menu_validation.add_argument("--matrix-values", default="", help="Comma-separated matrix values used to expand a recipe placeholder")
    menu_validation.add_argument("--print-command", action="store_true", help="Print the generated command instead of opening the review screen")

    ai_prompt = subparsers.add_parser("ai-prompt", aliases=["ap"])
    ai_prompt.add_argument("--repo-name", required=True, help="Human-friendly repo name for the generated prompt")
    ai_prompt.add_argument("--runner", "--project-type", dest="project_type", choices=["auto", "dotnet", "android", "gradle", "python", "node", "rust", "go", "maven", "cmake", "meson", "make", "command", "unknown"], default="auto")
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

    overlay_cmd = subparsers.add_parser("overlay", help="Inspect, preflight, and rebase overlay artifacts")
    overlay_sub = overlay_cmd.add_subparsers(dest="overlay_command", required=True)
    overlay_check = overlay_sub.add_parser("check", help="Preflight an overlay against the current repository")
    overlay_check.add_argument("artifact")
    overlay_check.add_argument("--diff", action="store_true", help="Include a repository-relative unified diff")
    overlay_check.add_argument("--diff-context", type=int, default=3)
    overlay_check.add_argument("--max-diff-lines", type=int, default=4000)
    overlay_check.add_argument("--ignore-checksum", action="store_true")
    overlay_check.add_argument("--json", action="store_true", help="Emit structured overlay preflight JSON")
    overlay_rebase = overlay_sub.add_parser("rebase", help="Regenerate overlay inventory source hashes against the current repository")
    overlay_rebase.add_argument("artifact")
    overlay_rebase.add_argument("-o", "--output", default="", help="Output archive; defaults to <name>.rebased.<ext>")
    overlay_rebase.add_argument("--json", action="store_true", help="Emit structured rebase JSON")

    plugin_cmd = subparsers.add_parser("plugin", aliases=["plugins"], help="Discover and diagnose devtool plugins")
    plugin_sub = plugin_cmd.add_subparsers(dest="plugin_command", required=True)
    plugin_list = plugin_sub.add_parser("list", aliases=["ls"], help="List built-in and entry-point plugins")
    plugin_list.add_argument("--json", action="store_true", help="Emit plugin discovery JSON")
    plugin_doctor = plugin_sub.add_parser("doctor", help="Run plugin-specific health checks")
    plugin_doctor.add_argument("name")
    plugin_doctor.add_argument("--json", action="store_true", help="Emit plugin doctor JSON")

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
    bundle_overlays.add_argument("--validation-task", "--task", dest="validation_tasks", action="append", default=None, help="Gradle task to write into bundle validation.tasks; repeatable")
    bundle_overlays.add_argument("--validation-tasks", dest="validation_tasks_csv", default=None, help="Comma-separated Gradle tasks to write into bundle validation.tasks; pass an empty value to declare an explicit empty task list")
    bundle_overlays.add_argument("-o", "--out", required=True, help="Output bundle archive; extension selects format")

    artifact_cmd = subparsers.add_parser("artifact", help="Inspect and maintain the produced build-artifact registry")
    artifact_sub = artifact_cmd.add_subparsers(dest="artifact_command", required=True)
    artifact_list = artifact_sub.add_parser("list", aliases=["ls"], help="List registered build artifacts")
    artifact_list.add_argument("--json", action="store_true", help="Emit registry JSON")
    artifact_inspect = artifact_sub.add_parser("inspect", aliases=["show"], help="Inspect a registered build artifact")
    artifact_inspect.add_argument("selector", help="Artifact id, path, or filename")
    artifact_inspect.add_argument("--json", action="store_true", help="Emit artifact JSON")
    artifact_verify = artifact_sub.add_parser("verify", help="Verify existence and SHA-256 of a registered artifact")
    artifact_verify.add_argument("selector", help="Artifact id, path, or filename")
    artifact_verify.add_argument("--json", action="store_true", help="Emit verification JSON")
    artifact_prune = artifact_sub.add_parser("prune", help="Prune old registry entries")
    artifact_prune.add_argument("--older-than", default="30d", help="Age threshold such as 30d, 12h, or an integer day count")
    artifact_prune.add_argument("--delete-files", action="store_true", help="Also delete matching files inside .devtool/build-artifacts only")
    artifact_prune.add_argument("--json", action="store_true", help="Emit prune result JSON")

    tui_cmd = subparsers.add_parser("tui", help="Open the responsive terminal dashboard")
    tui_cmd.add_argument("--page", choices=["dashboard", "build", "overlays"], default="dashboard", help="Initial terminal dashboard page")
    tui_cmd.add_argument("--artifact", default="", help="Overlay artifact to preflight on the overlays page")
    tui_cmd.add_argument("--once", action="store_true", help="Render one frame and exit")
    tui_cmd.add_argument("--width", type=int, default=0, help="Override terminal width for rendering/debugging")

    cleanup_cmd = subparsers.add_parser("cleanup", help="Archive historical repository backups and optionally prune old backup history")
    cleanup_cmd.add_argument("--dry-run", action="store_true", help="Show what would be archived or pruned without changing files")
    cleanup_cmd.add_argument("--prune-history-days", type=float, default=0.0, metavar="DAYS", help="Delete archived backup batches older than this many days; 0 keeps all history")

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
    completion_state = subparsers.add_parser("completion-state", help="Emit config-aware values for shell/editor completion")
    completion_state.add_argument("--kind", choices=["targets", "groups", "ui-profiles", "test-profiles", "variants", "jobs", "workflows", "pytest-tests", "all"], default="all")
    completion_state.add_argument("--format", choices=["lines", "json"], default="lines")

    compatibility_cmd = subparsers.add_parser("compatibility", aliases=["compat"], help="Audit retained legacy interfaces and migration paths")
    compatibility_cmd.add_argument("--json", action="store_true", help="Emit the versioned compatibility report as JSON")
    compatibility_cmd.add_argument("--active-only", action="store_true", help="Show only compatibility entries active in the selected config")
    compatibility_cmd.add_argument("--category", choices=["config", "cli", "environment", "python-api", "machine", "ipc", "tasker", "android", "artifact", "validation", "state", "workflow"], default="", help="Filter compatibility entries by category")
    compatibility_cmd.add_argument("--strict", action="store_true", help="Exit non-zero when the selected config still needs migration")
    compatibility_cmd.add_argument("--global", dest="global_config", action="store_true", help="Audit the user global config instead of the repository config")

    final_seal_cmd = subparsers.add_parser("final-seal", aliases=["seal"], help="Run the FS03 repository-wide final release seal audit")
    final_seal_cmd.add_argument("--json", action="store_true", help="Emit the versioned FS03 seal report as JSON")
    final_seal_cmd.add_argument("--strict", action="store_true", help="Exit non-zero when any final-seal check fails")
    final_seal_cmd.add_argument("--target", default=argparse.SUPPRESS, help="Emit the final seal for one configured target instead of the whole repository")

    pytest_cmd = subparsers.add_parser("pytest", help="Inspect and profile Devtool's pytest suite")
    pytest_sub = pytest_cmd.add_subparsers(dest="pytest_command", required=True)
    pytest_profile = pytest_sub.add_parser("profile", help="Run pytest with Devtool slow-test reporting enabled")
    pytest_profile.add_argument("pytest_args", nargs="*", help="Optional pytest files, node IDs, or arguments; defaults to tests")
    pytest_profile.add_argument("--threshold", type=float, default=2.0, help="Slow-test threshold in seconds")
    pytest_profile.add_argument("--top", type=int, default=30, help="Maximum number of slow entries to show")
    pytest_profile.add_argument("--strict", action="store_true", help="Fail when unmarked tests exceed the slow threshold")
    pytest_profile.add_argument("--json", action="store_true", help="Emit a structured pytest-profile report")

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
    cfg_resolve = cfg_sub.add_parser("resolve", help="Print the fully resolved repository configuration")
    cfg_resolve.add_argument("--provenance", action="store_true", help="Include a top-level provenance explanation")
    cfg_explain = cfg_sub.add_parser("explain", help="Explain canonical target/runner/UI configuration and provenance")
    cfg_explain.add_argument("--json", action="store_true", help="Emit machine-readable explanation JSON")
    cfg_migrate = cfg_sub.add_parser("migrate", help="Check or rewrite legacy kind/type aliases to canonical runner/platform syntax")
    migrate_mode = cfg_migrate.add_mutually_exclusive_group()
    migrate_mode.add_argument("--check", action="store_true", help="Check and report migration opportunities without changing files")
    migrate_mode.add_argument("--write", action="store_true", help="Rewrite safe legacy aliases in place and create a .bak backup")
    cfg_migrate.add_argument("--json", action="store_true", help="Emit machine-readable migration report")
    cfg_migrate.add_argument("--global", dest="global_config", action="store_true", help="Migrate legacy UI preferences in the user global config")
    cfg_doctor = cfg_sub.add_parser("doctor")
    cfg_doctor.add_argument("--global", dest="global_config", action="store_true", help="Diagnose the user global config")

    bootstrap_cmd = subparsers.add_parser("bootstrap", help="Provision reusable destination-specific build toolchains")
    bootstrap_sub = bootstrap_cmd.add_subparsers(dest="bootstrap_command", required=True)

    def add_bootstrap_destination(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--destination",
            default="current",
            help="current, termux, linux-arm64, linux-amd64, chroot:<name>, chroot-arm64:<name>, or chroot-amd64:<name>",
        )
        sub.add_argument("--format", choices=["text", "json"], default="text")

    bootstrap_install = bootstrap_sub.add_parser("install", help="Provision and seal a reusable toolchain for a destination")
    add_bootstrap_destination(bootstrap_install)
    bootstrap_install.add_argument("--java-version", dest="bootstrap_java_version", type=int, default=21)
    bootstrap_install.add_argument("--gradle-version", dest="bootstrap_gradle_version", default="9.7.1")
    bootstrap_install.add_argument("--android-platform", dest="bootstrap_android_platform", default="36")
    bootstrap_install.add_argument("--build-tools", dest="bootstrap_build_tools", default="auto")
    bootstrap_install.add_argument("--ndk-version", dest="bootstrap_ndk_version", default="auto")
    bootstrap_install.add_argument("--cmake-version", dest="bootstrap_cmake_version", default="auto")
    bootstrap_install.add_argument("--sdk-provider", dest="bootstrap_sdk_provider", choices=sorted(VALID_ANDROID_SDK_PROVIDERS), default="auto")
    bootstrap_install.add_argument("--aapt2-provider", dest="bootstrap_aapt2_provider", choices=["auto", "termux", "sdk", "revanced", "none"], default="auto")
    bootstrap_install.add_argument("--arm-build-tools-provider", dest="bootstrap_arm_build_tools_provider", choices=sorted(VALID_ANDROID_BUILD_TOOLS_PROVIDERS), default="auto")
    bootstrap_install.add_argument("--native-provider", dest="bootstrap_native_provider", choices=sorted(VALID_NATIVE_PROVIDERS), default="auto")
    bootstrap_install.add_argument("--gradle-provider", dest="bootstrap_gradle_provider", choices=sorted(VALID_GRADLE_PROVIDERS), default="auto")
    bootstrap_install.add_argument("--java-provider", dest="bootstrap_java_provider", choices=sorted(VALID_JAVA_PROVIDERS), default="auto")
    bootstrap_install.add_argument("--sdk-root", dest="bootstrap_sdk_root", default="")
    bootstrap_install.add_argument("--gradle-user-home", dest="bootstrap_gradle_user_home", default="")
    bootstrap_install.add_argument("--cache-dir", dest="bootstrap_cache_dir", default="")
    bootstrap_install.add_argument("--repair", action="store_true", help="Re-run provisioning and repair missing/corrupt components")
    bootstrap_install.add_argument("--upgrade", action="store_true", help="Force refresh of Devtool-managed toolchain downloads")
    bootstrap_install.add_argument("--dry-run", dest="bootstrap_dry_run", action="store_true", help="Print/probe provisioning without writing a receipt")

    bootstrap_status_cmd = bootstrap_sub.add_parser("status", help="Verify the provisioning receipt for a destination")
    add_bootstrap_destination(bootstrap_status_cmd)

    bootstrap_remove = bootstrap_sub.add_parser("remove", help="Remove the destination receipt without uninstalling shared system packages")
    add_bootstrap_destination(bootstrap_remove)

    android_cmd = subparsers.add_parser("android", help="Inspect Android capabilities, ADB providers, and connected devices")
    android_sub = android_cmd.add_subparsers(dest="android_command", required=True)

    def add_android_adb_flags(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--provider", choices=("auto", "sdk", "termux", "termux-adb", "path", "none"), default="", help="ADB provider override")
        sub.add_argument("--adb-path", default="", help="Explicit ADB executable path")
        sub.add_argument("--format", choices=("text", "json"), default="text")

    android_capabilities = android_sub.add_parser("capabilities", help="Show the resolved Android execution context and tool providers")
    add_android_adb_flags(android_capabilities)

    android_devices = android_sub.add_parser("devices", help="List connected Android devices or diagnose the active device transport")
    add_android_adb_flags(android_devices)
    android_devices.add_argument("device_action", nargs="?", choices=("list", "doctor"), default="list")
    android_devices.add_argument("--enrich", action="store_true", help="Query usable devices for API, ABI, battery, screen, and boot metadata")
    android_devices.add_argument("--policy", choices=("exactly-one", "serial", "preferred", "first-physical", "first-emulator", "all"), default="", help="Select devices using a deterministic policy")
    android_devices.add_argument("--serial", default="", help="Select a device serial or transport ID (ANDROID_SERIAL remains supported)")
    android_devices.add_argument("--preferred", default="", help="Preferred serial/model/device for --policy preferred")
    android_devices.add_argument("--include-unusable", action="store_true", help="Allow offline/unauthorized devices in explicit selection")

    android_doctor = android_sub.add_parser("doctor", help="Diagnose Android tools and the active ADB/device transport")
    add_android_adb_flags(android_doctor)

    def add_android_device_selection_flags(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--serial", default="", help="Android device serial/transport ID")
        sub.add_argument("--policy", choices=("exactly-one", "serial", "preferred", "first-physical", "first-emulator"), default="", help="Device selection policy")
        sub.add_argument("--format", choices=("text", "json"), default="text")

    android_signing = android_sub.add_parser("signing", help="Create, inspect, verify, compare, import, and export Android signing identities")
    signing_sub = android_signing.add_subparsers(dest="signing_action", required=True)
    signing_create = signing_sub.add_parser("create", help="Create a Devtool-managed Android signing keystore")
    signing_create.add_argument("--keystore", default="")
    signing_create.add_argument("--alias", default="")
    signing_create.add_argument("--store-password-env", default="")
    signing_create.add_argument("--key-password-env", default="")
    signing_create.add_argument("--dname", default="CN=Devtool Android Release")
    signing_create.add_argument("--overwrite", action="store_true")
    signing_create.add_argument("--format", choices=("text", "json"), default="text")
    signing_list = signing_sub.add_parser("list", help="List aliases and certificate identities in a signing keystore")
    signing_list.add_argument("--keystore", default="")
    signing_list.add_argument("--store-password-env", default="")
    signing_list.add_argument("--format", choices=("text", "json"), default="text")
    signing_inspect = signing_sub.add_parser("inspect", help="Inspect configured keystore alias and certificate identity")
    signing_inspect.add_argument("--keystore", default="")
    signing_inspect.add_argument("--alias", default="")
    signing_inspect.add_argument("--store-password-env", default="")
    signing_inspect.add_argument("--format", choices=("text", "json"), default="text")
    signing_verify = signing_sub.add_parser("verify", help="Verify the configured identity or a signed APK/AAB")
    signing_verify.add_argument("artifact", nargs="?", default="")
    signing_verify.add_argument("--keystore", default="")
    signing_verify.add_argument("--alias", default="")
    signing_verify.add_argument("--store-password-env", default="")
    signing_verify.add_argument("--format", choices=("text", "json"), default="text")
    signing_compare = signing_sub.add_parser("compare", help="Compare signer certificate identities of two APK/AAB artifacts")
    signing_compare.add_argument("artifact")
    signing_compare.add_argument("other_artifact")
    signing_compare.add_argument("--format", choices=("text", "json"), default="text")
    signing_import = signing_sub.add_parser("import", help="Import/migrate a keystore, including JKS to PKCS12")
    signing_import.add_argument("source")
    signing_import.add_argument("destination")
    signing_import.add_argument("--alias", default="")
    signing_import.add_argument("--source-password-env", required=True)
    signing_import.add_argument("--destination-password-env", required=True)
    signing_import.add_argument("--overwrite", action="store_true")
    signing_import.add_argument("--format", choices=("text", "json"), default="text")
    signing_export = signing_sub.add_parser("export-certificate", help="Export configured signing certificate as PEM")
    signing_export.add_argument("--destination", required=True)
    signing_export.add_argument("--keystore", default="")
    signing_export.add_argument("--alias", default="")
    signing_export.add_argument("--store-password-env", default="")
    signing_export.add_argument("--format", choices=("text", "json"), default="text")

    android_install = android_sub.add_parser("install", help="Install an APK on the selected Android device")
    android_install.add_argument("apk")
    add_android_device_selection_flags(android_install)
    android_install.add_argument("--no-replace", action="store_true")
    android_install.add_argument("--downgrade", action="store_true")
    android_uninstall = android_sub.add_parser("uninstall", help="Uninstall a package from the selected device")
    android_uninstall.add_argument("package")
    add_android_device_selection_flags(android_uninstall)
    android_uninstall.add_argument("--keep-data", action="store_true")
    for _name, _help in (("launch", "Launch an Android package"), ("stop", "Force-stop an Android package"), ("restart", "Force-stop and relaunch an Android package"), ("clear-data", "Clear an Android package's app data")):
        _sub = android_sub.add_parser(_name, help=_help)
        _sub.add_argument("package")
        if _name in {"launch", "restart"}:
            _sub.add_argument("--activity", default="")
        add_android_device_selection_flags(_sub)
    android_shell = android_sub.add_parser("shell", help="Run an adb shell command on the selected device")
    android_shell.add_argument("shell_args", nargs=argparse.REMAINDER)
    add_android_device_selection_flags(android_shell)
    android_push = android_sub.add_parser("push", help="Push a file/directory to the selected device")
    android_push.add_argument("source")
    android_push.add_argument("destination")
    add_android_device_selection_flags(android_push)
    android_pull = android_sub.add_parser("pull", help="Pull a file/directory from the selected device")
    android_pull.add_argument("source")
    android_pull.add_argument("destination")
    add_android_device_selection_flags(android_pull)
    android_logcat = android_sub.add_parser("logcat", help="Capture recent logcat from the selected device")
    android_logcat.add_argument("--lines", type=int, default=400)
    add_android_device_selection_flags(android_logcat)
    android_screenshot = android_sub.add_parser("screenshot", help="Capture a PNG screenshot from the selected device")
    android_screenshot.add_argument("destination")
    add_android_device_selection_flags(android_screenshot)
    android_permission = android_sub.add_parser("permission", help="Grant or revoke a runtime permission")
    permission_sub = android_permission.add_subparsers(dest="permission_action", required=True)
    for _name in ("grant", "revoke"):
        _sub = permission_sub.add_parser(_name)
        _sub.add_argument("package")
        _sub.add_argument("permission")
        add_android_device_selection_flags(_sub)

    android_release = android_sub.add_parser("release", help="Run the canonical Android release pipeline and seal evidence")
    android_release.add_argument("--device-tests", choices=("disabled", "when-device-present", "required"), default="")
    android_release.add_argument("--previous-apk", default="")
    android_release.add_argument("--package", dest="release_package", default="")
    android_release.add_argument("--activity", dest="release_activity", default="")
    android_release.add_argument("--evidence-output", default="")
    android_release.add_argument("--no-unit-tests", action="store_true")
    android_release.add_argument("--no-lint", action="store_true")
    android_release.add_argument("--no-install-smoke", action="store_true")
    android_release.add_argument("--no-upgrade-smoke", action="store_true")
    android_release.add_argument("--no-verify-signature", action="store_true")
    android_release.add_argument("--no-verify-certificate-identity", action="store_true")
    android_release.add_argument("--format", choices=("text", "json"), default="text")

    android_instrumentation_cmd = android_sub.add_parser("instrumentation", help="Run Android instrumentation through the canonical device orchestrator")
    android_instrumentation_cmd.add_argument("--class", dest="instrumentation_classes", action="append", default=[])
    android_instrumentation_cmd.add_argument("--method", dest="instrumentation_methods", action="append", default=[])
    android_instrumentation_cmd.add_argument("--annotation", dest="instrumentation_annotations", action="append", default=[])
    android_instrumentation_cmd.add_argument("--not-annotation", dest="instrumentation_not_annotations", action="append", default=[])
    android_instrumentation_cmd.add_argument("--size", dest="instrumentation_size", choices=("small", "medium", "large"), default="")
    android_instrumentation_cmd.add_argument("--runner-arg", dest="instrumentation_runner_args", action="append", default=[])
    android_instrumentation_cmd.add_argument("--skip-build", action="store_true")
    android_instrumentation_cmd.add_argument("--format", choices=("text", "json"), default="text")

    android_smoke = android_sub.add_parser("smoke", help="Run reusable Android install/upgrade/lifecycle/signature smoke gates")
    android_smoke.add_argument("gate", choices=("install", "upgrade", "cold-start", "restart", "restore-restart", "process-death", "signed-artifact"))
    android_smoke.add_argument("--apk", default="")
    android_smoke.add_argument("--old-apk", default="")
    android_smoke.add_argument("--package", default="")
    android_smoke.add_argument("--activity", default="")
    android_smoke.add_argument("--expected-cert-sha256", default="")
    add_android_device_selection_flags(android_smoke)

    instrumentation_cmd = subparsers.add_parser("instrumentation", help="Run Android instrumentation for the selected target")
    instrumentation_cmd.add_argument("--class", dest="instrumentation_classes", action="append", default=[])
    instrumentation_cmd.add_argument("--method", dest="instrumentation_methods", action="append", default=[])
    instrumentation_cmd.add_argument("--annotation", dest="instrumentation_annotations", action="append", default=[])
    instrumentation_cmd.add_argument("--not-annotation", dest="instrumentation_not_annotations", action="append", default=[])
    instrumentation_cmd.add_argument("--size", dest="instrumentation_size", choices=("small", "medium", "large"), default="")
    instrumentation_cmd.add_argument("--runner-arg", dest="instrumentation_runner_args", action="append", default=[])
    instrumentation_cmd.add_argument("--skip-build", action="store_true")
    instrumentation_cmd.add_argument("--format", choices=("text", "json"), default="text")

    termux_cmd = subparsers.add_parser("termux", help="Inspect native Termux providers, packages, and PRoot guests")
    termux_sub = termux_cmd.add_subparsers(dest="termux_command", required=True)
    termux_doctor = termux_sub.add_parser("doctor", help="Run the detailed Termux/provider doctor")
    termux_doctor.add_argument("--distro", default="", help="Probe an installed PRoot-Distro guest by name")
    termux_doctor.add_argument("--explain", action="store_true", help="Include provider selection and rejection reasons")
    termux_doctor.add_argument("--format", choices=["text", "json"], default="text")
    termux_packages = termux_sub.add_parser("packages", help="Show read-only Termux package recommendations")
    termux_packages.add_argument("--format", choices=["text", "json"], default="text")
    termux_explain = termux_sub.add_parser("explain", help="Explain Termux tool-provider decisions")
    termux_explain.add_argument("--format", choices=["text", "json"], default="text")

    detect_cmd = subparsers.add_parser("detect")
    detect_cmd.add_argument("--format", choices=["text", "json"], default="text")

    handoff_cmd = subparsers.add_parser("handoff")
    handoff_cmd.add_argument("-o", "--output", default="")

    hud_cmd = subparsers.add_parser("hud")
    hud_cmd.add_argument("--state-dir", dest="tasker_dir", default=DEFAULT_TASKER_DIR, help="Directory containing devtool live HUD state")
    hud_cmd.add_argument("--mode", choices=["auto", "android-gui", "android-bridge", "termux-gui", "terminal", "web", "studio", "desktop", "desktop-window", "gtk4-window", "gtk-window", "desktop-notify", "v3", "v4", "v5", "v6", "console", "dashboard", "all", "unix", "gnome"], default="auto", help="Run HUD renderer: Android app bridge, terminal, web, studio, or GTK4 desktop")
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
    android_bridge_cmd.add_argument("--state-dir", default="", help="Shared bridge state directory; defaults to ~/.local/state/devtool-android-hud on Termux; delegated backends receive an explicit shared bridge path")
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
    rollback_cmd.add_argument("--transaction", default="", metavar="ID_OR_PATH", help="Resume/perform rollback for an active apply transaction")
    rollback_cmd.add_argument("--failed", action="store_true", help="Resume the newest rollback-failed transaction")
    rollback_cmd.add_argument("--list-transactions", action="store_true", help="List active apply transactions and their recovery state")

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

    targets_cmd = subparsers.add_parser("targets", aliases=["tg"], help="List configured targets with platform, runner, group, and workflow metadata")
    targets_cmd.add_argument("--json", action="store_true", help="Emit structured target metadata")
    groups_cmd = subparsers.add_parser("groups", aliases=["gr"], help="List workspace target groups and their expanded targets")
    groups_cmd.add_argument("--json", action="store_true", help="Emit structured workspace-group metadata")
    jobs_cmd = subparsers.add_parser("jobs", aliases=["jb"], help="List reusable polyglot jobs declared under [jobs.*]")
    jobs_cmd.add_argument("--json", action="store_true", help="Emit structured reusable-job metadata")
    workflows_cmd = subparsers.add_parser("workflows", aliases=["wf"], help="List configured target workflows without executing them")
    workflows_cmd.add_argument("--json", action="store_true", help="Emit structured workflow metadata")
    plan_cmd = subparsers.add_parser("plan", aliases=["pl"], help="Plan the expanded workflow DAG, safe scheduling waves, scopes, and commands without executing them")
    _add_workspace_scope_flags(plan_cmd)
    _add_workflow_scheduler_flags(plan_cmd)
    plan_cmd.add_argument("action", nargs="?", default="validate", choices=["restore", "build", "test", "lint", "package", "run", "validate", "wine-validate"], help="Workflow/action to plan (default: validate)")
    plan_cmd.add_argument("--json", action="store_true", help="Emit the complete graph and scheduling plan as JSON")
    plan_cmd.add_argument("--dot", action="store_true", help="Emit Graphviz DOT instead of the human plan")
    plan_cmd.add_argument("--explain", action="store_true", help="Show read/write scope and serialization reasons for every node")
    plan_cmd.add_argument("--show-commands", action="store_true", help="Include resolved command argv/cwd in the human plan")
    affected_cmd = subparsers.add_parser("affected", aliases=["aff"], help="Show dependency-aware targets affected by Git changes")
    _add_workspace_scope_flags(affected_cmd, include_affected=False)
    affected_cmd.add_argument("--base", default="", help="Git base revision; default is the current working tree")
    affected_cmd.add_argument("--head", default="", help="Git head revision; with --base uses base...head")
    affected_cmd.add_argument("--explain", action="store_true", help="Explain why every target is selected")
    affected_cmd.add_argument("--json", action="store_true", help="Emit structured affected-target metadata")
    ui_contract_cmd = subparsers.add_parser("ui-contract", aliases=["uic"], help="Inspect shared UI preferences, capabilities, responsive policy, progress/diagnostics, confirmation/notification policy, tokens, actions, and navigation")
    ui_contract_cmd.add_argument("--surface", choices=["machine", "desktop", "tui", "android", "hud"], default="machine", help="Render one frontend surface contract")
    ui_contract_cmd.add_argument("--width", type=int, default=0, help="Viewport width/terminal columns for responsive-policy inspection")
    ui_contract_cmd.add_argument("--height", type=int, default=0, help="Viewport height/terminal rows for responsive-policy inspection")
    ui_contract_cmd.add_argument("--require-capability", action="append", default=[], help="Negotiate one required UI capability; repeatable")
    ui_contract_cmd.add_argument("--json", action="store_true", help="Emit the complete machine-readable UI Core contract")

    preflight_cmd = subparsers.add_parser("preflight", aliases=["pf"], help="Check expanded workflow roots, environments, runners, tools, and output safety")
    _add_workspace_scope_flags(preflight_cmd)
    preflight_cmd.add_argument("--workflow", default="all", help="Workflow/action to inspect; default: all declared workflows")
    preflight_cmd.add_argument("--json", action="store_true", help="Emit structured graph-aware preflight metadata")

    control_center = subparsers.add_parser("control-center", aliases=["gui"], help="Launch the Devtool desktop Control Center")
    control_center.add_argument("--backend", choices=["auto", "gtk4"], default="auto", help="Control Center backend; GTK4 is the only desktop backend")
    control_center.add_argument("--target", default=argparse.SUPPRESS, help="Named target/profile from .devtool.toml (also accepted globally before the command)")
    control_center.add_argument("--variant", default=argparse.SUPPRESS, help="Build/test variant within the selected target (also accepted globally before the command)")
    control_center.add_argument("--state-dir", default=DEFAULT_TASKER_DIR, help="Directory containing devtool live HUD state")
    control_center.add_argument("--follow-active", action="store_true", help="Follow the global active HUD directory")
    control_center.add_argument("--history-limit", type=int, default=25, help="Artifact history rows to load in the GTK4 shell")
    control_center.add_argument("--recent-events", type=int, default=8, help="Recent HUD events to load in the GTK4 shell")
    control_center.add_argument("--poll-interval", type=float, default=1.0, help="Seconds between GTK4 shell refreshes")
    control_center.add_argument("--page", choices=["overview", "runs", "history", "artifacts", "diagnostics", "repositories", "targets", "run", "settings"], default="overview", help="Initial GTK4 shell page")
    control_center.add_argument("--once", action="store_true", help="Print a read-only GTK4 Control Center JSON snapshot and exit")
    control_center.add_argument("--startup-test", action="store_true", help="Open one GTK frame, record startup diagnostics, then exit")

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
    cmd.add_argument("--profile", dest="validation_workflow_profile", default="default", help="Named validation workflow profile (minimal, default, android-full, release, termux, architecture-final, final-seal, or a repo-defined profile)")
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


def _add_validation_resource_override_flags(cmd: argparse.ArgumentParser) -> None:
    """Add one-invocation validation resource overrides.

    These flags intentionally override only the in-memory ProjectConfig used by
    the current command. They do not rewrite .devtool.toml or global config.
    """

    cmd.add_argument("--resource-profile", dest="validation_resource_profile", choices=["automatic", "android-low-power", "standard"], default="automatic", help="Validation resource profile")
    cmd.add_argument("--max-workers", type=int, default=None, metavar="COUNT", help="Override validation worker count for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits")
    cmd.add_argument("--cpu-limit", type=int, default=None, metavar="COUNT", help="Override validation CPU limit for this invocation; explicit CLI value wins over Android-safe/project phase defaults and is only clamped by real host limits")
    cmd.add_argument("--gradle-heap-mb", type=int, default=None, metavar="MIB", help="Override the Gradle validation heap for this invocation; explicit CLI value wins over Android-safe/project phase defaults")
    metaspace = cmd.add_mutually_exclusive_group()
    metaspace.add_argument("--gradle-metaspace-mb", "--metaspace-guard-mb", dest="gradle_metaspace_mb", type=int, default=None, metavar="MIB", help="Override the Gradle validation max metaspace; 0 disables Devtool's explicit MaxMetaspaceSize cap")
    metaspace.add_argument("--no-metaspace-guard", dest="disable_metaspace_guard", action="store_true", help="Do not set an explicit Gradle MaxMetaspaceSize for this validation invocation")
    cmd.add_argument("--memory-guard-mb", type=int, default=None, metavar="MIB", help="Override the validation free-memory guard for this invocation; 0 disables it and explicit CLI value wins over phase defaults")
    priority = cmd.add_mutually_exclusive_group()
    priority.add_argument("--low-priority", dest="validation_low_priority", action="store_true", default=None, help="Run validation commands at low scheduler priority")
    priority.add_argument("--normal-priority", dest="validation_low_priority", action="store_false", help="Do not lower validation process priority")
    parallel = cmd.add_mutually_exclusive_group()
    parallel.add_argument("--parallel", dest="validation_parallel", action="store_true", default=None, help="Enable Gradle parallel execution for validation")
    parallel.add_argument("--no-parallel", dest="validation_parallel", action="store_false", help="Disable Gradle parallel execution for validation")
    cache = cmd.add_mutually_exclusive_group()
    cache.add_argument("--build-cache", dest="validation_build_cache", action="store_true", default=None, help="Enable the Gradle build cache for validation")
    cache.add_argument("--no-build-cache", dest="validation_build_cache", action="store_false", help="Disable the Gradle build cache for validation")
    daemon = cmd.add_mutually_exclusive_group()
    daemon.add_argument("--daemon", dest="validation_daemon", action="store_true", default=None, help="Allow the Gradle daemon during validation")
    daemon.add_argument("--no-daemon", dest="validation_daemon", action="store_false", help="Disable the Gradle daemon during validation")


def _add_pytest_selection_flags(cmd: argparse.ArgumentParser) -> None:
    """Add pytest-native selectors shared by test/validation/apply surfaces."""
    cmd.add_argument(
        "--pytest-test",
        dest="pytest_tests",
        action="append",
        default=[],
        metavar="NODEID",
        help="Run exactly this pytest file/node ID; repeatable. Suppresses default/affected pytest targets unless --pytest-include-defaults is set.",
    )
    cmd.add_argument(
        "--pytest-marker",
        dest="pytest_markers",
        action="append",
        default=[],
        metavar="EXPR",
        help="Add a pytest -m marker expression; repeatable expressions are combined with AND.",
    )
    cmd.add_argument(
        "--pytest-k",
        dest="pytest_k",
        default="",
        metavar="EXPR",
        help="Apply a pytest -k expression in addition to explicit pytest tests/selectors.",
    )
    cmd.add_argument(
        "--pytest-arg",
        dest="pytest_args",
        action="append",
        default=[],
        metavar="ARG",
        help="Pass one structured argument directly to pytest; repeatable (for example --pytest-arg=--maxfail=1).",
    )
    cmd.add_argument(
        "--pytest-include-defaults",
        dest="pytest_include_defaults",
        action="store_true",
        help="Keep configured/affected default pytest targets in addition to explicit --pytest-test selectors.",
    )


def _add_validation_test_selection_flags(cmd: argparse.ArgumentParser) -> None:
    _add_pytest_selection_flags(cmd)
    cmd.add_argument("--select", action="append", default=[], help="Select tests by type, suite, tag, class, or name during validation; repeatable")
    cmd.add_argument("--id", dest="test_ids", action="append", default=[], help="Select an exact framework test ID during validation; repeatable")
    cmd.add_argument("--tag", action="append", default=[], help="Include tests with this tag/trait/annotation during validation; repeatable")
    cmd.add_argument("--exclude-tag", action="append", default=[], help="Exclude tests with this tag/trait/annotation during validation; repeatable")
    cmd.add_argument("--path", dest="test_paths", action="append", default=[], help="Select tests below a source path or package during validation; repeatable")
    cmd.add_argument("--class", "--tests", dest="test_classes", action="append", default=[], help="Select a test class during validation; repeatable")
    cmd.add_argument("--method", dest="test_methods", action="append", default=[], help="Select a test method during validation; repeatable")
    cmd.add_argument("--filter", dest="test_filter", default="", help="Framework-native test filter used during validation")
    cmd.add_argument(
        "--task", "--test-task",
        dest="test_task",
        action="append",
        default=[],
        help=(
            "Exclusive native validation unit list; repeatable. Android/Gradle values are Gradle tasks; "
            "Python/Node/Rust/etc. values are supported validation actions such as test, lint, build, "
            "package-check, or Python test targets."
        ),
    )
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

_OUTPUT_DEBUG_TRUE = {"1", "true", "yes", "on"}
_OUTPUT_DEBUG_FALSE = {"", "0", "false", "no", "off"}


def _output_debug_setting(args: argparse.Namespace) -> tuple[bool, str]:
    explicit = getattr(args, "output_debug", None)
    if explicit is not None:
        return bool(explicit), "--output-debug" if bool(explicit) else "--no-output-debug"
    raw = os.environ.get("DEVTOOL_OUTPUT_DEBUG", "").strip().lower()
    if raw in _OUTPUT_DEBUG_TRUE:
        return True, "DEVTOOL_OUTPUT_DEBUG"
    if raw in _OUTPUT_DEBUG_FALSE:
        return False, "default" if not raw else "DEVTOOL_OUTPUT_DEBUG"
    # Unknown values fail closed because output debugging persists summaries and
    # clipboard payloads as private evidence.
    return False, "DEVTOOL_OUTPUT_DEBUG-invalid"


def _write_private_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _sha256_file(path: Path) -> tuple[int, str]:
    data = path.read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()


def _preserve_output_debug_log(run_id: str, log_path: Path | str | None) -> dict[str, object]:
    if not log_path:
        return {"log_preserved": False, "log_preserve_reason": "no-log-path"}
    source = Path(str(log_path)).expanduser()
    if not source.is_absolute():
        source = (Path.cwd() / source).resolve()
    if not source.is_file():
        return {
            "log_preserved": False,
            "log_preserve_reason": "log-not-found",
            "log_file": str(source),
        }

    root = log_state_dir() / "evidence" / run_id
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    try:
        logs_dir.chmod(0o700)
    except OSError:
        pass
    target = logs_dir / source.name
    shutil.copy2(source, target)
    try:
        target.chmod(0o600)
    except OSError:
        pass
    byte_count, digest = _sha256_file(target)
    return {
        "log_preserved": True,
        "log_file": str(source),
        "evidence_log_file": str(target),
        "evidence_log_bytes": byte_count,
        "evidence_log_sha256": digest,
    }


def _merge_output_debug_evidence_index(root: Path, capture: dict[str, object], log_record: dict[str, object]) -> None:
    index_path = root / "evidence.json"
    try:
        existing = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        existing = {}
    if not isinstance(existing, dict):
        existing = {}

    output_debug = existing.get("output_debug") if isinstance(existing.get("output_debug"), dict) else {}
    captures = output_debug.get("captures") if isinstance(output_debug.get("captures"), list) else []
    preserved_logs = output_debug.get("preserved_logs") if isinstance(output_debug.get("preserved_logs"), list) else []

    output_id = str(capture.get("output_id") or "")
    captures = [item for item in captures if not (isinstance(item, dict) and str(item.get("output_id") or "") == output_id)]
    captures.append(capture)

    if log_record.get("log_preserved"):
        evidence_log_file = str(log_record.get("evidence_log_file") or "")
        preserved_logs = [
            item for item in preserved_logs
            if not (isinstance(item, dict) and str(item.get("evidence_log_file") or "") == evidence_log_file)
        ]
        preserved_logs.append(log_record)

    existing.setdefault("run_id", capture.get("run_id") or root.name)
    existing.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    existing["updated_at"] = datetime.now(timezone.utc).isoformat()
    existing["output_debug"] = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "captures": captures,
        "preserved_logs": preserved_logs,
    }
    _write_private_text(index_path, json.dumps(existing, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def _append_private_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _record_output_debug_payload(
    *,
    args: argparse.Namespace,
    kind: str,
    label: str,
    payload: str,
    log_path: Path | str | None,
    source: str,
    extra: dict[str, object] | None = None,
) -> dict[str, object] | None:
    enabled, enabled_by = _output_debug_setting(args)
    if not enabled:
        return None

    encoded = payload.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    run_id = _ensure_run_id(args)
    sequence = int(getattr(args, "_devtool_output_debug_sequence", 0) or 0) + 1
    args._devtool_output_debug_sequence = sequence
    safe_kind = kind.replace("_", "-")
    output_id = f"{safe_kind}-{sequence:04d}"
    root = log_state_dir() / "evidence" / run_id / "output-debug"
    payload_path = root / f"{output_id}.txt"
    metadata_path = root / f"{output_id}.json"
    timestamp = datetime.now(timezone.utc).isoformat()
    metadata: dict[str, object] = {
        "schema_version": 1,
        "run_id": run_id,
        "output_id": output_id,
        "sequence": sequence,
        "timestamp": timestamp,
        "command": str(getattr(args, "command", "") or "devtool"),
        "kind": kind,
        "label": label,
        "source": source,
        "enabled_by": enabled_by,
        "payload_bytes": len(encoded),
        "payload_sha256": digest,
        "payload_file": str(payload_path),
        "metadata_file": str(metadata_path),
        "log_file": str(log_path or ""),
        "sanitized": True,
    }
    if extra:
        metadata.update(extra)

    terminal_metadata = getattr(args, "_devtool_output_debug_terminal_summary", None)
    if kind == "clipboard" and isinstance(terminal_metadata, dict):
        terminal_hash = str(terminal_metadata.get("payload_sha256") or "")
        metadata["terminal_summary_sha256"] = terminal_hash
        metadata["matches_terminal_summary"] = bool(terminal_hash and terminal_hash == digest)
        metadata["terminal_summary_output_id"] = str(terminal_metadata.get("output_id") or "")

    marker_lines = [
        "Output debug capture:",
        f"  run_id: {run_id}",
        f"  output_id: {output_id}",
        f"  kind: {kind}",
        f"  label: {label}",
        f"  source: {source}",
        f"  enabled_by: {enabled_by}",
        f"  payload_bytes: {len(encoded)}",
        f"  payload_sha256: {digest}",
        f"  payload_file: {payload_path}",
        f"  metadata_file: {metadata_path}",
    ]
    if "matches_terminal_summary" in metadata:
        marker_lines.append(
            f"  matches_terminal_summary: {'yes' if metadata['matches_terminal_summary'] else 'no'}"
        )

    try:
        _write_private_text(payload_path, payload)
        _append_run_log(log_path, "\n".join(marker_lines))
        log_record = _preserve_output_debug_log(run_id, log_path)
        metadata.update(log_record)
        _write_private_text(metadata_path, json.dumps(metadata, indent=2, ensure_ascii=False) + "\n")
        timeline_event = {
            "timestamp": timestamp,
            "run_id": run_id,
            "output_id": output_id,
            "kind": kind,
            "source": source,
            "payload_bytes": len(encoded),
            "payload_sha256": digest,
            "matches_terminal_summary": metadata.get("matches_terminal_summary"),
            "log_preserved": metadata.get("log_preserved"),
            "evidence_log_file": metadata.get("evidence_log_file"),
        }
        _append_private_jsonl(root / "timeline.jsonl", timeline_event)
        capture_record = {
            "output_id": output_id,
            "kind": kind,
            "source": source,
            "label": label,
            "payload_file": str(payload_path),
            "metadata_file": str(metadata_path),
            "payload_bytes": len(encoded),
            "payload_sha256": digest,
            "log_file": str(metadata.get("log_file") or ""),
            "log_preserved": bool(metadata.get("log_preserved")),
            "evidence_log_file": str(metadata.get("evidence_log_file") or ""),
        }
        _merge_output_debug_evidence_index(root.parent, capture_record, log_record)
    except OSError as exc:
        if not bool(getattr(args, "quiet", False)):
            console.print(f"[yellow]Output debug evidence could not be saved: {exc}[/yellow]")
        _append_run_log(log_path, f"Output debug capture failed: run={run_id} kind={kind} error={exc}")
        return None

    if kind == "terminal-summary":
        args._devtool_output_debug_terminal_summary = metadata

    if not bool(getattr(args, "quiet", False)):
        title = "Terminal summary output debug" if kind == "terminal-summary" else "Clipboard output debug"
        console.print(f"[cyan]{title}[/cyan]")
        console.print(f"  Run: {run_id}  Output: {output_id}  Source: {source}")
        console.print(f"  Bytes: {len(encoded)}  SHA-256: {digest}")
        console.print(f"  Saved payload: {payload_path}")
        if "matches_terminal_summary" in metadata:
            console.print(
                "  Matches terminal summary bytes: "
                + ("yes" if metadata["matches_terminal_summary"] else "no")
            )
    return metadata


def _render_summary_debug_text(results: list[OperationResult], *, stderr: bool = False) -> str:
    # Render a plain-text equivalent using the same detected terminal width. This
    # preserves the human summary layout while intentionally excluding ANSI style
    # bytes from durable evidence.
    target = create_console(stderr=stderr)
    stream = io.StringIO()
    capture = Console(file=stream, force_terminal=False, color_system=None, width=target.width)
    render_summary(results, console_obj=capture)
    return redact_sensitive_text(stream.getvalue())


def _record_terminal_summary_debug(
    *,
    args: argparse.Namespace | None,
    results: list[OperationResult],
    as_json: bool,
    json_text: str,
    payload_mode: str,
    log_path: Path | str | None,
) -> dict[str, object] | None:
    if args is None or not _output_debug_setting(args)[0]:
        return None
    if payload_mode == "auto" and as_json:
        payload = redact_sensitive_text(json_text) + ("" if json_text.endswith("\n") else "\n")
        source = "terminal-json-summary"
        stream = "stdout"
        rendering = "json"
    else:
        stderr = payload_mode != "auto"
        payload = _render_summary_debug_text(results, stderr=stderr)
        source = "terminal-render-summary"
        stream = "stderr" if stderr else "stdout"
        rendering = "rich-plain-equivalent"
    return _record_output_debug_payload(
        args=args,
        kind="terminal-summary",
        label="Final terminal summary",
        payload=payload,
        log_path=log_path,
        source=source,
        extra={"stream": stream, "rendering": rendering},
    )


def _record_clipboard_output_debug(
    *,
    args: argparse.Namespace,
    label: str,
    payload: str,
    result,
    log_path: Path | str | None,
    source: str,
) -> dict[str, object] | None:
    if not _output_debug_setting(args)[0]:
        return None
    return _record_output_debug_payload(
        args=args,
        kind="clipboard",
        label=label,
        payload=payload,
        log_path=log_path,
        source=source,
        extra={
            "copy_via_requested": str(getattr(args, "copy_via", "auto") or "auto"),
            "backend": str(getattr(result, "backend", "") or ""),
            "success": bool(getattr(result, "success", False)),
            "reason": str(getattr(result, "reason", "") or ""),
        },
    )


def _perform_clipboard_copy(
    to_copy: str,
    label: str,
    args: argparse.Namespace,
    *,
    log_path: Path | str | None,
    source: str,
):
    # Prepare once so debug evidence and the clipboard transport receive exactly
    # the same sanitized string. copy_text() sanitizes again defensively.
    prepared = prepare_clipboard_text(to_copy)
    result = (
        copy_text(prepared)
        if args.copy_via == "auto"
        else copy_text(prepared, preferred=args.copy_via)
    )
    _record_clipboard_output_debug(
        args=args,
        label=label,
        payload=prepared,
        result=result,
        log_path=log_path,
        source=source,
    )
    if result.success:
        console.print(f"[green]{label} copied to clipboard via {result.backend}.[/green]")
    else:
        console.print(
            f"[yellow]{label} was not copied to clipboard"
            f"{f' via {result.backend}' if result.backend else ''}: "
            f"{result.reason or 'Clipboard copy failed.'}[/yellow]"
        )
    return result


def _maybe_copy(raw_output: str, summary_text: str, json_text: str, args: argparse.Namespace, *, log_path: Path | str | None = None):
    if args.no_clipboard:
        return None

    import re as _re

    raw_output = redact_sensitive_text(raw_output)
    summary_text = redact_sensitive_text(summary_text)

    try:
        payload = redact_sensitive_value(json.loads(json_text))
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    json_text = json.dumps(payload, indent=2, ensure_ascii=False) if payload else redact_sensitive_text(json_text)
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
            r"(?:(?P<failed>\d+)\s+failed(?:,\s*)?)?"
            r"(?:(?P<passed>\d+)\s+passed(?:,\s*)?)?"
            r"(?:(?P<skipped>\d+)\s+skipped)?",
            raw_output,
            _re.IGNORECASE,
        )
        if pytest_match and any(pytest_match.group(name) for name in ("failed", "passed", "skipped")):
            test_failed = int(pytest_match.group("failed") or 0)
            test_passed = int(pytest_match.group("passed") or 0)
            test_skipped = int(pytest_match.group("skipped") or 0)
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

    def _go_package_totals() -> tuple[int, int, int, int, int] | None:
        package_sets: list[dict] = []
        for result in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
            if not isinstance(result, dict):
                continue
            metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
            go_test = metadata.get("go_test") if isinstance(metadata.get("go_test"), dict) else {}
            packages = go_test.get("packages") if isinstance(go_test.get("packages"), dict) else {}
            if packages:
                package_sets.append(packages)
        if not package_sets:
            return None
        passed = sum(int(item.get("passed") or 0) for item in package_sets)
        failed_count = sum(int(item.get("failed") or 0) for item in package_sets)
        skipped_count = sum(int(item.get("skipped") or 0) for item in package_sets)
        no_tests = sum(int(item.get("no_tests") or 0) for item in package_sets)
        total = sum(int(item.get("total") or 0) for item in package_sets)
        return passed, failed_count, skipped_count, no_tests, total

    def _semantic_detail_key(line: str) -> str:
        """Collapse alternate renderings of the same diagnostic into one identity.

        Android's child builder emits both original compiler output and its own
        canonical ``- ERROR [...]``/``- WARNING [...]`` report.  The outer
        validator also receives structured issues for those same diagnostics.
        Clipboard/failure summaries should keep unique raw context, but must not
        repeat one Kotlin/Gradle issue two or three times just because it crossed
        those representation boundaries.
        """

        cleaned = _re.sub(r"\s+", " ", str(line or "")).strip()
        cleaned = _re.sub(
            r"^-\s+(?:ERROR|WARNING)(?:\s+\[[^\]]+\])?\s+",
            "",
            cleaned,
            flags=_re.IGNORECASE,
        )
        cleaned = _re.sub(r"^(?:warning|error):\s+", "", cleaned, flags=_re.IGNORECASE)

        task = _re.match(r"^>\s*Task\s+(\S+)\s+FAILED$", cleaned, _re.IGNORECASE)
        if task:
            return f"gradle-task:{task.group(1).casefold()}"
        task = _re.match(r"^Gradle task failed:\s*(\S+)$", cleaned, _re.IGNORECASE)
        if task:
            return f"gradle-task:{task.group(1).casefold()}"

        def normalize_message(message: str) -> str:
            normalized = _re.sub(r"\s+", " ", str(message or "")).strip()
            normalized = _re.sub(
                r"^(?:error|warning):\s+",
                "",
                normalized,
                flags=_re.IGNORECASE,
            )
            normalized = _re.sub(
                r"\s+\[[A-Za-z][A-Za-z0-9_.-]*\]$",
                "",
                normalized,
            )
            return normalized.casefold()

        compiler = _re.match(
            r"^[ew]:\s+file:///(?P<file>.+?):(?P<line>\d+):(?P<column>\d+)\s+(?P<message>.+)$",
            cleaned,
            _re.IGNORECASE,
        )
        if compiler:
            location = f"{compiler.group('file')}:{compiler.group('line')}:{compiler.group('column')}"
            return f"located:{location.casefold()}:{normalize_message(compiler.group('message'))}"

        located = _re.match(
            r"^(?P<location>.+?:\d+(?::\d+)?):\s+(?:[A-Za-z][A-Za-z0-9_.-]*:\s+)?(?P<message>.+)$",
            cleaned,
        )
        if located:
            return f"located:{located.group('location').casefold()}:{normalize_message(located.group('message'))}"

        coded = _re.match(
            r"^(?:GRADLE_ENVIRONMENT|GRADLE_MEMORY_GUARD|GRADLE_METASPACE_OOM|GRADLE_TASK|LOW_STORAGE|ADB_PROBE|KOTLIN|PYTEST_WARNING|PYTEST_FAILURE|PYTHON_DEPENDENCY|PYTHON_PLATFORM|PYTHON_IMPORT|PYTHON_UNDECLARED_IMPORT|PYTHON_PLATFORM_GLIBC):\s+(?P<message>.+)$",
            cleaned,
            _re.IGNORECASE,
        )
        if coded:
            return _semantic_detail_key(coded.group("message"))

        return cleaned.casefold()

    def _dedupe_detail_lines(lines: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for line in lines:
            cleaned = _re.sub(r"\s+", " ", str(line or "")).strip()
            if not cleaned:
                continue
            key = _semantic_detail_key(cleaned)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(cleaned)
        return deduped

    def _merge_detail_lines(primary: list[str], fallback: list[str]) -> list[str]:
        """Prefer structured diagnostics and add only semantically new raw context."""

        merged: list[str] = []
        seen: set[str] = set()
        for line in [*primary, *fallback]:
            cleaned = _re.sub(r"\s+", " ", str(line or "")).strip()
            if not cleaned:
                continue
            key = _semantic_detail_key(cleaned)
            if key in seen:
                continue
            seen.add(key)
            merged.append(cleaned)
        return merged

    def _format_issue_detail(issue: dict, *, default: str) -> str:
        code = str(issue.get("code") or "").strip()
        message = str(issue.get("message") or "").strip()
        file_text = str(issue.get("file") or "").strip()
        line = issue.get("line")
        if code in {
            "GRADLE_TASK", "GRADLE_TEST", "UNITTEST", "PYTEST", "VSTEST",
            "PYTEST_FAILURE", "PYTHON_DEPENDENCY", "PYTHON_PLATFORM", "PYTHON_IMPORT",
            "GO_TEST", "GO_TEST_PACKAGE",
        }:
            return message or code or default
        location = ""
        if file_text and file_text != "-":
            location = file_text
            if line not in {None, ""}:
                location += f":{line}"
        prefix = f"{location}: " if location else ""
        code_text = f"{code}: " if code else ""
        return f"{prefix}{code_text}{message or default}"

    def _structured_issue_details(severity: str) -> list[str]:
        lines: list[str] = []
        default = "Warning" if severity == "warning" else "Error"
        for result in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
            if not isinstance(result, dict):
                continue
            issues = result.get("issues")
            if not isinstance(issues, list):
                continue
            codes = {
                str(item.get("code") or "").upper()
                for item in issues
                if isinstance(item, dict)
            }
            metaspace_root = "GRADLE_METASPACE_OOM" in codes
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                if str(issue.get("severity") or "").lower() != severity:
                    continue
                code = str(issue.get("code") or "").upper()
                if metaspace_root and severity == "error" and code in {"GRADLE_TASK", "GRADLE_DAEMON_DISAPPEARED"}:
                    continue
                lines.append(_format_issue_detail(issue, default=default))
        return _dedupe_detail_lines(lines)

    def _has_structured_issue_code(code: str) -> bool:
        wanted = str(code or "").upper()
        for result in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
            if not isinstance(result, dict):
                continue
            for issue in result.get("issues", []) if isinstance(result.get("issues"), list) else []:
                if isinstance(issue, dict) and str(issue.get("code") or "").upper() == wanted:
                    return True
        return False

    def _raw_warning_details(text: str) -> list[str]:
        warning_patterns = (
            # MSBuild/compiler warnings:
            # file.cs(1,2): warning CS1234: Message [project.csproj]
            r"^(.*?\bwarning\s+[A-Z]{1,5}\d+\s*:\s*.+)$",

            # NuGet warnings such as NU1603 and NU1903.
            r"^(.*?\bwarning\s+NU\d+\s*:\s*.+)$",

            # Kotlin compiler warnings.
            r"^(w:\s+file:///.+)$",

            # File/line warning forms used by Android lint and compilers.
            r"^(.+?:\d+(?::\d+)?:\s*warning\s*:\s*.+)$",

            # Canonical diagnostics appended by the runner after reports.
            r"^(-\s+WARNING(?:\s+\[[^\]]+\])?\s+.+)$",

            # Generic command warnings.
            r"^(.*?\bwarning\s*:\s*.+)$",

            # Common Python warning forms.
            r"^(.+Warning:\s*.+)$",
        )
        combined = _re.compile(
            "|".join(f"(?:{pattern})" for pattern in warning_patterns),
            _re.IGNORECASE,
        )
        lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or "0 warning(s)" in line.casefold() or "warnings=0" in line.casefold():
                continue
            if line.startswith(
                "Warning: Unable to set daemon's environment variables to match the client because:"
            ):
                continue
            if not combined.search(line):
                continue
            cleaned = _re.sub(r"\s+", " ", line).strip()
            cleaned = _re.sub(
                r"\s+\[[^\[\]]+\.(?:csproj|fsproj|vbproj)\]\s*$",
                "",
                cleaned,
                flags=_re.IGNORECASE,
            )
            lines.append(cleaned)
        return _dedupe_detail_lines(lines)

    def _raw_error_details(text: str) -> list[str]:
        """Extract concrete raw failure lines without using them for success/failure promotion."""

        error_patterns = (
            # Kotlin/Java/Android compiler diagnostics.
            r"^(?:e|error):\s+file:///.+",
            r"^.+?:\d+(?::\d+)?:\s*(?:error|fatal error)\b.+",

            # Canonical diagnostics appended by the runner
            # after report parsing.
            r"^-\s+ERROR(?:\s+\[[^\]]+\])?\s+.+$",

            # Gradle/lint/build summaries.
            r"^>\s*Task\s+.+\s+FAILED$",
            r"^FAILURE:\s+Build failed with an exception\.",
            r"^Execution failed for task .+",
            r"^Gradle task failed:\s+.+",
            r"^Lint found\s+[1-9]\d*\s+errors?.*",

            # Python/process/runtime failures.
            r"^Traceback \(most recent call last\):$",
            r"^\w*(?:Error|Exception):\s+.+",
            r"^[A-Za-z0-9_.-]+(?:\.py)?\s*:\s*error:\s+.+",
            r"^(?:fatal|error):\s+.+",
            r"^FAILED\s+.+",

            # Go test failures do not use the word "error" for assertion
            # failures, so retain their native failure markers as fallback
            # evidence when structured issue extraction is unavailable.
            r"^---\s+FAIL:\s+.+",
            r"^FAIL\s+\S+.*",
            r"^.+?\.go:\d+(?::\d+)?:\s+.+",
        )
        combined = _re.compile(
            "|".join(f"(?:{pattern})" for pattern in error_patterns),
            _re.IGNORECASE,
        )
        lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            low = line.casefold()
            if (
                "0 error" in low
                or "errors=0" in low
                or "0 failed" in low
                or "failed=0" in low
                or low.startswith("warning:")
            ):
                continue
            if not combined.search(line):
                continue
            lines.append(_re.sub(r"\s+", " ", line).strip())
        return _dedupe_detail_lines(lines)

    def _warning_details() -> tuple[list[str], int]:
        """Extract all useful, deduplicated warnings from structured issues and output."""
        structured = _structured_issue_details("warning")
        # Once pytest warnings were parsed into structured diagnostics they are
        # canonical.  Re-adding the raw warnings-summary lines would inflate the
        # clipboard count even though the HUD correctly counts one issue each.
        raw = [] if warnings <= 0 or structured or _has_structured_issue_code("PYTEST_WARNING") else _raw_warning_details(raw_output)
        warning_lines = _merge_detail_lines(structured, raw)
        return warning_lines, len(warning_lines)

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
    go_package_totals = _go_package_totals()
    commit_hash = _commit_hash()
    warning_details, unique_warning_count = _warning_details()
    effective_warning_count = max(warnings, unique_warning_count)

    def _traceability_lines() -> list[str]:
        trace: list[str] = []
        run_id = str(getattr(args, "_devtool_run_id", "") or os.environ.get("DEVTOOL_RUN_ID", "")).strip()
        if run_id:
            trace.append(f"Run: {run_id}")
        if log_path is not None:
            trace.append(f"Log: {log_path}")
        return trace

    overlay_summaries = payload.get("overlays") if isinstance(payload, dict) else None
    success_headline = "Devtool completed with warnings." if effective_warning_count > 0 else "Devtool completed successfully."
    success_lines = [success_headline]
    success_lines.extend(_traceability_lines())
    success_lines.append("")

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
            overlay_warning_count = int(overlay.get('warnings') or 0)
            if len(valid_overlays) == 1:
                overlay_warning_count = max(overlay_warning_count, unique_warning_count)
            success_lines.append(
                f"- Diagnostics: {overlay_warning_count} warnings, "
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

    if go_package_totals is not None:
        package_passed, package_failed, package_skipped, package_no_tests, package_total = go_package_totals
        success_lines.append(
            "Go packages: "
            f"{package_passed} passed, {package_failed} failed, "
            f"{package_skipped} skipped, {package_no_tests} no tests "
            f"({package_total} total)"
        )

    success_lines.append(
        f"Diagnostics: {effective_warning_count} warnings, {errors} errors"
    )

    if effective_warning_count > 0 or warning_details:
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
            "lint": "lint",
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
            if name in {"rollback", "apply-rollback", "artifact-cleanup"}:
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

        def _failed_result_outputs() -> str:
            chunks: list[str] = []
            for result in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
                if not isinstance(result, dict):
                    continue
                try:
                    returncode = int(result.get("returncode") or 0)
                except (TypeError, ValueError):
                    returncode = 0
                if returncode == 0:
                    continue
                output = str(result.get("output") or "").strip()
                if output:
                    chunks.append(output)
            return "\n".join(chunks)

        def _error_details() -> tuple[list[str], int]:
            raw_error_source = "\n".join(
                part for part in [failed_block, _failed_result_outputs(), raw_output]
                if str(part or "").strip()
            )
            structured_errors = _structured_issue_details("error")
            authoritative_python = any(
                _has_structured_issue_code(code)
                for code in ("PYTEST_FAILURE", "PYTHON_DEPENDENCY", "PYTHON_PLATFORM", "PYTHON_IMPORT")
            )
            authoritative_go = any(
                _has_structured_issue_code(code)
                for code in ("GO_TEST", "GO_TEST_PACKAGE")
            )
            raw_errors = (
                []
                if structured_errors or _has_structured_issue_code("GRADLE_METASPACE_OOM") or authoritative_python or authoritative_go
                else _raw_error_details(raw_error_source)
            )
            error_lines = _merge_detail_lines(structured_errors, raw_errors)
            return error_lines, len(error_lines)

        def _overlay_failure_lines(*, detail_warnings: int = 0, detail_errors: int = 0) -> list[str]:
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
                overlay_warning_count = int(overlay.get('warnings') if overlay.get('warnings') is not None else (detail_warnings or 0))
                overlay_warning_count = max(overlay_warning_count, unique_warning_count)
                overlay_error_count = int(overlay.get('errors') if overlay.get('errors') is not None else (detail_errors or 0))
                lines.append(
                    f"- Diagnostics: {overlay_warning_count} warnings, "
                    f"{overlay_error_count} errors"
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
            "restore": ("dotnet restore", "go mod download"),
            "build": ("dotnet build", "go build", "devtool-go"),
            "test": ("dotnet test", "go test", "pytest", "unittest"),
            "wine-test": ("wine",),
            "lint": ("go vet",),
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

        error_details, unique_error_count = _error_details()
        failure_warning_details, unique_failure_warning_count = warning_details, unique_warning_count

        def _failed_result_reason_lines() -> list[str]:
            reasons: list[str] = []
            for result in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
                if not isinstance(result, dict):
                    continue
                try:
                    returncode = int(result.get("returncode") or 0)
                except (TypeError, ValueError):
                    returncode = 0
                if returncode == 0:
                    continue
                raw_name = str(result.get("name") or "command").strip() or "command"
                metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
                runner_name = str(metadata.get("runner") or metadata.get("project_type") or "").lower()
                if runner_name == "go":
                    name = {
                        "restore": "Go restore",
                        "build": "Go build",
                        "test": "Go tests",
                        "go-validation": "Go tests",
                        "lint": "Go vet",
                        "package": "Go package",
                    }.get(raw_name.lower(), raw_name.replace("-", " ").replace("_", " "))
                else:
                    name = raw_name.replace("-", " ").replace("_", " ")
                failure_kind = str(result.get("failure_kind") or "").replace("_", " ").strip()
                issues = result.get("issues") if isinstance(result.get("issues"), list) else []
                issue_message = ""
                error_issues = [
                    issue for issue in issues
                    if isinstance(issue, dict) and str(issue.get("severity") or "").lower() == "error"
                ]
                priority = {
                    "GRADLE_METASPACE_OOM": 0,
                    "GRADLE_MEMORY_GUARD": 1,
                    "PYTHON_DEPENDENCY": 10,
                    "PYTHON_PLATFORM": 11,
                    "PYTHON_IMPORT": 12,
                    "PYTHON_UNDECLARED_IMPORT": 13,
                    "PYTHON_PLATFORM_GLIBC": 14,
                    "GO_TEST": 18,
                    "GO_TEST_PACKAGE": 19,
                    "PYTEST_FAILURE": 20,
                    "PYTEST": 21,
                    "GRADLE_TEST": 30,
                    "KOTLIN": 24,
                    "JAVA": 24,
                    "JAVAC": 24,
                    "AAPT": 24,
                    "AAPT2": 24,
                    "ANDROID_LINT": 25,
                    "GRADLE_TASK": 70,
                    "GENERIC": 90,
                }
                if error_issues:
                    issue = min(
                        enumerate(error_issues),
                        key=lambda item: (
                            priority.get(str(item[1].get("code") or "").upper(), 50),
                            item[0],
                        ),
                    )[1]
                    issue_message = _format_issue_detail(issue, default="Error")
                raw_error_lines = _raw_error_details(str(result.get("output") or ""))
                raw_error_message = raw_error_lines[0] if raw_error_lines else ""
                normalized_failure_kind = failure_kind.casefold().replace("-", "_").replace(" ", "_")
                go_test = metadata.get("go_test") if isinstance(metadata.get("go_test"), dict) else {}
                go_failures = go_test.get("failures") if isinstance(go_test.get("failures"), list) else []
                go_reason = ""
                if runner_name == "go" and go_failures and isinstance(go_failures[0], dict):
                    first_failure = go_failures[0]
                    package = str(first_failure.get("package") or "").strip()
                    test = str(first_failure.get("test") or "").strip()
                    file_text = str(first_failure.get("file") or "").strip()
                    line = first_failure.get("line")
                    message = str(first_failure.get("message") or "Go test failed").strip()
                    identity = " · ".join(part for part in (package, test) if part)
                    if file_text and file_text != "-":
                        identity = " · ".join(part for part in (identity, f"{file_text}:{line}" if line is not None else file_text) if part)
                    go_reason = f"{identity}: {message}" if identity else message
                if go_reason:
                    reason = f"{name} failed with exit {returncode}: {go_reason}"
                elif issue_message:
                    reason = f"{name} failed with exit {returncode}: {issue_message}"
                elif raw_error_message and normalized_failure_kind in {"", "unknown", "unknown_failure"}:
                    reason = f"{name} failed with exit {returncode}: {raw_error_message}"
                elif failure_kind:
                    reason = f"{name} failed with exit {returncode}: {failure_kind}"
                elif raw_error_message:
                    reason = f"{name} failed with exit {returncode}: {raw_error_message}"
                else:
                    reason = f"{name} failed with exit {returncode}"
                reasons.append(reason)
            if not reasons and error_details:
                reasons.append(error_details[0])
            if not reasons and failed_block:
                failed_command = failed_block.splitlines()[0].strip() if failed_block.splitlines() else ""
                if failed_command:
                    reasons.append(f"Command failed: {failed_command}")
            return _dedupe_detail_lines(reasons)

        rerun_commands: list[str] = []
        for result in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
            if not isinstance(result, dict):
                continue
            metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
            go_test = metadata.get("go_test") if isinstance(metadata.get("go_test"), dict) else {}
            rerun = str(go_test.get("rerun") or "").strip()
            if rerun and rerun not in rerun_commands:
                rerun_commands.append(rerun)

        completion = payload.get("completion") if isinstance(payload.get("completion"), dict) else {}
        headline = str(completion.get("headline") or "").strip()
        if not headline and failed_steps:
            headline = failed_steps[0].capitalize() + " failed"
        lines = [headline or "Devtool failed."]
        trace_lines = _traceability_lines()
        if trace_lines:
            lines.extend(trace_lines)
        overlay_lines = _overlay_failure_lines(
            detail_warnings=unique_failure_warning_count,
            detail_errors=unique_error_count,
        )
        if overlay_lines:
            lines.extend(("", *overlay_lines))
        failure_reasons = _failed_result_reason_lines()
        if go_package_totals is not None:
            package_passed, package_failed, package_skipped, package_no_tests, package_total = go_package_totals
            lines.extend((
                "",
                "Go packages: "
                f"{package_passed} passed, {package_failed} failed, "
                f"{package_skipped} skipped, {package_no_tests} no tests "
                f"({package_total} total)",
            ))
        if failure_reasons:
            lines.extend(("", "Failure reason:"))
            lines.extend(f"- {reason}" for reason in failure_reasons)
        if rerun_commands:
            lines.extend(("", "Rerun:"))
            lines.extend(f"- {command}" for command in rerun_commands)
        # A typed Go test diagnostic is already the failure reason; repeating
        # the same assertion under an additional Errors heading makes the
        # clipboard noisier without adding evidence. Other toolchains retain
        # the historical diagnostics section.
        show_error_details = error_details
        if any(_has_structured_issue_code(code) for code in ("GO_TEST", "GO_TEST_PACKAGE")) and failure_reasons:
            show_error_details = []
        if show_error_details:
            # Keep the long-standing clipboard rendering contract while allowing
            # typed Go failures to suppress duplicate raw diagnostic lines.
            error_details = show_error_details
            lines.extend(("", "Errors:"))
            lines.extend(f"- {error}" for error in error_details)
        if failure_warning_details:
            lines.extend(("", "Warnings:"))
            lines.extend(f"- {warning}" for warning in failure_warning_details)
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
        return _perform_clipboard_copy(
            to_copy,
            label,
            args,
            log_path=log_path,
            source="--copy",
        )

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
        return _perform_clipboard_copy(
            to_copy,
            label,
            args,
            log_path=log_path,
            source="interactive-prompt",
        )
    return None

def _validate_output_flags(args: argparse.Namespace, parser: argparse.ArgumentParser) -> tuple[str, str]:
    explicit_stdout = args.stdout_payload
    if args.no_clipboard and args.copy:
        parser.error("--no-clipboard cannot be combined with --copy.")

    copy_mode = "raw" if args.copy else "auto"
    return explicit_stdout, copy_mode


def _normalize_live_progress_args(args: argparse.Namespace) -> None:
    command = str(getattr(args, "command", "") or "")
    if bool(getattr(args, "_devtool_json", False)):
        args.auto_hud = False
        args.tasker_progress = False
        args.notify_progress = False
        args.hud_overlay = False
        args.hud_no_overlay = True
        return
    env_overlay = os.environ.get("DEVTOOL_HUD_OVERLAY", "").strip().lower() in {"1", "true", "yes", "on"}
    if env_overlay and not bool(getattr(args, "hud_no_overlay", False)):
        args.hud_overlay = True
    # Live state is an internal HUD/app bridge facility now. Legacy Tasker and
    # standalone notification flags are intentionally not exposed by the CLI.
    args.tasker_progress = command in PROGRESS_CAPABLE_COMMANDS
    args.notify_progress = False
    if not hasattr(args, "tasker_dir"):
        args.tasker_dir = DEFAULT_TASKER_DIR
    if not hasattr(args, "notify_id"):
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


def _live_test_selection(args: argparse.Namespace) -> dict[str, object]:
    values = {
        "pytest_tests": _cli_string_list(getattr(args, "pytest_tests", None)),
        "pytest_markers": _cli_string_list(getattr(args, "pytest_markers", None)),
        "pytest_k": str(getattr(args, "pytest_k", "") or ""),
        "pytest_args": _cli_string_list(getattr(args, "pytest_args", None)),
        "pytest_include_defaults": bool(getattr(args, "pytest_include_defaults", False)),
        "selectors": _cli_string_list(getattr(args, "selectors", None)),
        "ids": _cli_string_list(getattr(args, "test_ids", None)),
        "paths": _cli_string_list(getattr(args, "test_paths", None)),
        "classes": _cli_string_list(getattr(args, "test_classes", None)),
        "methods": _cli_string_list(getattr(args, "test_methods", None)),
    }
    return {key: value for key, value in values.items() if value not in ([], "", False, None)}


def _live_progress_for_args(args: argparse.Namespace, config: ProjectConfig | None = None, repo: Path | None = None) -> TaskerProgressSink | None:
    repo_root = (config.repo_root if config is not None else (repo or Path(args.repo or Path.cwd()))).resolve()
    target = str(getattr(args, "target", "") or getattr(args, "solution", "") or getattr(args, "project", "") or "")
    if config is not None and (bool(getattr(args, "all_targets", False)) or bool(getattr(args, "group", [])) or bool(getattr(args, "affected", False))):
        try:
            if getattr(args, "affected", False) is True:
                candidate_targets = resolve_workspace_targets(
                    config.workspace,
                    config.targets,
                    groups=tuple(getattr(args, "group", []) or ()),
                    all_targets=bool(getattr(args, "all_targets", False)),
                ) if (getattr(args, "group", []) or getattr(args, "all_targets", False)) else tuple(config.targets)
                selection = analyze_affected_targets(
                    config,
                    candidate_targets=candidate_targets,
                    base=str(getattr(args, "affected_base", "") or ""),
                    head=str(getattr(args, "affected_head", "") or ""),
                )
                selected_targets = selection.targets
            else:
                selected_targets = resolve_workspace_targets(
                    config.workspace,
                    config.targets,
                    groups=tuple(getattr(args, "group", []) or ()),
                    all_targets=bool(getattr(args, "all_targets", False)),
                )
            if selected_targets:
                target = ",".join(selected_targets)
        except (WorkspaceConfigError, AffectedSelectionError):
            # Argument/config validation reports the actionable error later. HUD
            # publication must not turn a parser error into a progress crash.
            pass
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

    ui_presentations: dict[str, dict[str, object]] = {}
    if config is not None:
        from .ui_core.preferences import resolve_ui_preferences
        ui_presentations = {
            surface: resolve_ui_preferences(config.ui_config, surface=surface).as_dict()
            for surface in ("machine", "desktop", "tui", "hud", "android")
        }

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
        ui_presentations=ui_presentations,
        test_selection=_live_test_selection(args),
    )

    if sink is not None and enabled and bool(getattr(args, "auto_hud", False)):
        sink.prepare_hud()
        overlay_requested = bool(getattr(args, "hud_overlay", False)) and not bool(getattr(args, "hud_no_overlay", False))
        start_auto_hud(directory, mode=hud_mode, poll_interval=0.1, no_overlay=not overlay_requested)
    return sink


def _finish_live_progress(live_progress: TaskerProgressSink | None, *, totals: dict[str, int], results: list[OperationResult]) -> None:
    if live_progress is None:
        return
    # Control/API responses intentionally use a compact totals payload that may
    # omit diagnostic-only keys such as ``errors``.  Live progress is an output
    # adapter and must tolerate those valid partial totals rather than turning a
    # successful validation-control request into an internal KeyError.
    failed = int(totals.get("failed", 0) or 0)
    errors = int(totals.get("errors", 0) or 0)
    live_progress.finished(
        ok=failed == 0 and errors == 0,
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
    output_debug_args: argparse.Namespace | None = None,
    output_debug_log_path: Path | str | None = None,
) -> tuple[dict[str, int], str, str, str]:
    repo_root = str(config.repo_root if config else Path.cwd())
    if output_debug_log_path:
        for result in results:
            if not result.log_path:
                result.log_path = str(output_debug_log_path)
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
        _record_terminal_summary_debug(
            args=output_debug_args,
            results=results,
            as_json=as_json,
            json_text=json_text,
            payload_mode=payload_mode,
            log_path=output_debug_log_path,
        )
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
    _record_terminal_summary_debug(
        args=output_debug_args,
        results=results,
        as_json=as_json,
        json_text=json_text,
        payload_mode=payload_mode,
        log_path=output_debug_log_path,
    )
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


def _validation_command_reuses_session(args: argparse.Namespace) -> bool:
    command = str(getattr(args, "command", "") or "")
    if str(getattr(args, "session", "") or "").strip():
        return True
    if command in {
        "validation-status", "vs", "validation-plan", "vplan",
        "validation-events", "vevents", "validation-report", "vreport",
        "validation-pause", "vpause", "validation-stop", "vstop",
        "validation-abort", "va", "validation-recover", "vrec",
        "validation-reset", "vreset", "validation-accept-plan-change", "vapc",
        "validation-finalize", "vf",
    }:
        return True
    if command not in {"validate", "v"}:
        return False
    mode = str(getattr(args, "_devtool_validation_mode", "") or "").strip()
    return bool(getattr(args, "sequential", False)) and mode != "start" and not bool(
        getattr(args, "restart_session", False)
    )


def _make_ad_hoc_overlay_bundle(artifacts: list[str], *, name: str = "adhoc_overlay_bundle") -> tuple[tempfile.TemporaryDirectory[str], Path, list[Path]]:
    tmp = tempfile.TemporaryDirectory(prefix="devtool-adhoc-overlays-")
    paths = [resolve_artifact_path(item) for item in artifacts]
    bundle = create_overlay_bundle(Path(tmp.name) / f"{name}.zip", paths, name=name)
    return tmp, bundle, paths


def _load_command_config(args: argparse.Namespace) -> ProjectConfig:
    target_name = args.target
    solution = args.solution
    project = args.project
    manifest: dict[str, object] | None = None

    workspace_selection = bool(getattr(args, "group", []) or getattr(args, "all_targets", False) or getattr(args, "affected", False))
    if args.command in LEGACY_APPLY_VALIDATE_COMMANDS:
        manifest = _read_artifact_manifest(_single_artifact_arg(getattr(args, "artifact", "")))
        if not target_name and not workspace_selection:
            target_name = artifact_target(manifest)
        if not solution:
            solution = artifact_solution(manifest)
        if not project:
            project = manifest.get("run_project", "") or manifest.get("project", "")

    if not target_name and _validation_command_reuses_session(args):
        target_name = validation_session_target_hint(
            Path(args.repo or Path.cwd()).expanduser().resolve(),
            str(getattr(args, "session", "") or ""),
        )

    bundle_entry_targets = []
    if isinstance(manifest, dict) and str(manifest.get("type") or "").strip().lower() == "overlay_bundle":
        raw_order = manifest.get("apply_order") or manifest.get("overlays") or []
        if isinstance(raw_order, list):
            bundle_entry_targets = [
                str(item.get("target") or "").strip()
                for item in raw_order
                if isinstance(item, dict) and str(item.get("target") or "").strip()
            ]
    allow_bundle_bootstrap = bool(bundle_entry_targets) and not target_name

    try:
        return load_project_config(
            args.repo,
            args.config,
            target_name=target_name,
            variant_name=str(getattr(args, "variant", "") or ""),
            solution_override=solution,
            project_override=project,
            kind_override=str(getattr(args, "project_type", "auto") or "auto"),
            allow_ambiguous_target=args.command in {"targets", "tg", "groups", "gr", "jobs", "jb", "workflows", "wf", "plan", "pl", "affected", "aff", "preflight", "pf", "doctor", "dr", "completion-state"} or allow_bundle_bootstrap or workspace_selection,
        )
    except SystemExit as exc:
        # An overlay may introduce both the named target and .devtool.toml. Allow
        # extraction to proceed with a repository-only config, then reload after
        # apply once the artifact-created configuration exists. Explicit CLI target
        # mistakes remain fatal unless the requested target is the artifact's own
        # declared target.
        manifest_target = artifact_target(manifest) if manifest is not None else ""
        target_is_artifact_declared = bool(target_name and manifest_target and target_name == manifest_target)
        if (
            manifest is not None
            and args.command in APPLY_COMMANDS
            and (not getattr(args, "_devtool_cli_target_explicit", False) or target_is_artifact_declared)
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
                allow_ambiguous_target=True,
            )
        raise


def _argv_contains_option(argv: list[str], names: tuple[str, ...]) -> bool:
    for token in argv:
        if token in names:
            return True
        if any(token.startswith(name + "=") for name in names):
            return True
    return False


def _validation_is_requested_by_args(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "validate", False)
        or getattr(args, "validate_only", False)
        or getattr(args, "defer_validate", False)
        or getattr(args, "static_validate", False)
    )


def _apply_declared_validation_tasks(args: argparse.Namespace, manifest: dict, *, active: bool) -> None:
    if not active or getattr(args, "_devtool_cli_test_task_explicit", False):
        return
    declared, tasks = artifact_validation_task_declaration(manifest)
    if not declared:
        return
    args.test_task = list(tasks)
    args._devtool_manifest_validation_tasks = list(tasks)
    args._devtool_validation_tasks_declared = True
    args._devtool_validation_task_source = "artifact-manifest"


def _apply_declared_pytest_selection(args: argparse.Namespace, manifest: dict, *, active: bool) -> None:
    """Apply artifact-declared pytest selectors without overriding explicit CLI fields."""
    if not active:
        return
    declared = artifact_validation_pytest_selection(manifest)
    if not declared:
        return
    if "pytest_tests" in declared and not bool(getattr(args, "_devtool_cli_pytest_test_explicit", False)):
        args.pytest_tests = list(declared.get("pytest_tests") or [])
        args._devtool_pytest_selection_source = "artifact-manifest"
    if "pytest_markers" in declared and not bool(getattr(args, "_devtool_cli_pytest_marker_explicit", False)):
        args.pytest_markers = list(declared.get("pytest_markers") or [])
    if "pytest_k" in declared and not bool(getattr(args, "_devtool_cli_pytest_k_explicit", False)):
        args.pytest_k = str(declared.get("pytest_k") or "")
    if "pytest_args" in declared and not bool(getattr(args, "_devtool_cli_pytest_arg_explicit", False)):
        args.pytest_args = list(declared.get("pytest_args") or [])
    if bool(declared.get("pytest_include_defaults", False)) and not bool(getattr(args, "pytest_include_defaults", False)):
        args.pytest_include_defaults = True


def _apply_artifact_manifest_defaults(args: argparse.Namespace) -> None:
    """Apply manifest defaults while preserving explicit CLI precedence."""
    cli_requested_validation = _validation_is_requested_by_args(args)
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
            not cli_requested_validation
            and not getattr(args, "defer_validate", False)
            and bool(validation.get("required", False))
            and not getattr(args, "validate_only", False)
        ):
            args.validate = True
        if not cli_requested_validation:
            if not bool(validation.get("restore", True)):
                args.skip_restore = True
            if not bool(validation.get("build", True)):
                args.skip_build = True
            if not bool(validation.get("test", True)):
                args.skip_test = True
        _apply_declared_validation_tasks(args, manifest, active=_validation_is_requested_by_args(args))
        _apply_declared_pytest_selection(args, manifest, active=_validation_is_requested_by_args(args))
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
    if getattr(args, "command", "") in {"apply", "menu"}:
        args.command = str(getattr(args, "normalized_command", "") or ("apply-overlay" if getattr(args, "command", "") == "apply" else "validation-menu"))
    elif getattr(args, "command", "") == "vm":
        args.command = "validation-menu"
    command = str(getattr(args, "command", "") or "")
    if command in {"validate", "v"}:
        action = str(getattr(args, "validate_scope", "") or "").strip().lower()
        if action in {"wizard", "menu"}:
            args.command = "validation-menu"
            args.wizard = action == "wizard"
            args.validate_scope = ""
            command = str(args.command)
        elif action in {"start", "next", "run", "resume", "continue", "retry", "status", "checkpoint", "plan", "events", "report", "pause", "stop", "finalize", "abort", "recover", "reset", "accept-plan-change"}:
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
    exclusive_tasks = _exclusive_validation_task_values(args)
    if exclusive_tasks and bool(getattr(args, "static_validate", False)):
        parser.error("--task defines an executable native-task plan and cannot be combined with --static-validate.")
    if args.command in {"validate", "v"}:
        scope = str(getattr(args, "validate_scope", "") or "")
        if scope and scope not in {"overlay", "overlays"}:
            parser.error("validate positional form is only supported as: validate overlay <archive> or validate overlays <bundle>")
        if scope in {"overlay", "overlays"} and not getattr(args, "validate_artifact", ""):
            parser.error(f"validate {scope} requires an artifact archive")
        if scope in {"overlay", "overlays"} and exclusive_tasks:
            parser.error("--task applies to project validation, not validate overlay/overlays artifact inspection.")
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
    if deferred and bool(getattr(args, "rebase", False)):
        parser.error("--rebase cannot be combined with --defer-validate because deferred validation requires a persistent source artifact.")
    if bool(getattr(args, "rebase", False)) and args.command in {"apply-overlays", "aos", "apply-snapshot", "as"}:
        parser.error("--rebase is supported only for a single overlay apply.")
    if deferred and any(str(getattr(args, name, "") or "").strip() for name in ("phase", "phase_only", "through", "from_phase", "pause_after")):
        parser.error("Phase selection is not used during --defer-validate; apply first, then run `devtool validate continue ...`.")
    if getattr(args, "no_commit", False) and str(getattr(args, "commit", "") or "").strip():
        parser.error("--no-commit cannot be combined with --commit.")
    if getattr(args, "no_rollback_on_failure", False) and getattr(args, "rollback_on_failure", False):
        parser.error("--rollback-on-failure cannot be combined with --no-rollback-on-failure.")
    if (args.skip_restore or args.skip_build or args.skip_test) and not (args.validate or args.validate_only or deferred):
        parser.error("--skip-restore, --skip-build, and --skip-test require --validate, --validate-only, or --defer-validate.")
    if exclusive_tasks and not (args.validate or args.validate_only or deferred):
        parser.error("--task on apply requires --validate, --validate-only, or --defer-validate.")
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
    _apply_declared_validation_tasks(args, manifest, active=True)
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



def _apply_validation_resource_overrides(config: ProjectConfig, args: argparse.Namespace) -> None:
    if str(getattr(args, "command", "") or "") not in {
        "validate", "v", "validate-all", "wine-validate", "wv", *APPLY_COMMANDS
    }:
        return
    validation = config.runner_options.setdefault("validation", {})
    if not isinstance(validation, dict):
        validation = {}
        config.runner_options["validation"] = validation
    gradle = config.runner_options.setdefault("gradle", {})
    if not isinstance(gradle, dict):
        gradle = {}
        config.runner_options["gradle"] = gradle

    cli_overrides = validation.setdefault("_cli_resource_overrides", {})
    if not isinstance(cli_overrides, dict):
        cli_overrides = {}
        validation["_cli_resource_overrides"] = cli_overrides

    def remember_cli_override(
        key: str,
        value: object,
        *,
        flag: str,
        prior: dict[str, object] | None = None,
    ) -> None:
        existing = cli_overrides.get(key)
        preserved_prior = None
        if isinstance(existing, dict) and existing.get("value") == value and existing.get("flag") == flag:
            existing_prior = existing.get("prior")
            if isinstance(existing_prior, dict):
                preserved_prior = dict(existing_prior)
        cli_overrides[key] = {
            "value": value,
            "flag": flag,
            "prior": preserved_prior if preserved_prior is not None else dict(prior or {}),
        }

    if bool(getattr(args, "disable_metaspace_guard", False)):
        setattr(args, "gradle_metaspace_mb", 0)

    numeric = {
        "max_workers": ("max_workers", 1, False),
        "cpu_limit": ("cpu_limit", 1, False),
        "gradle_heap_mb": ("gradle_heap_mb", 128, False),
        "gradle_metaspace_mb": ("gradle_metaspace_mb", 128, True),
        "memory_guard_mb": ("memory_guard_mb", 0, True),
    }
    for attr, (key, minimum, allow_zero) in numeric.items():
        value = getattr(args, attr, None)
        if value is None:
            continue
        parsed = int(value)
        if parsed < 0 or (parsed == 0 and not allow_zero) or (parsed > 0 and parsed < minimum):
            suffix = " or 0 to disable" if allow_zero and minimum > 0 else ""
            raise SystemExit(f"--{attr.replace('_', '-')} must be at least {minimum}{suffix}.")
        prior = {
            "validation": validation.get(key),
            "gradle": gradle.get(key),
        }
        android = config.runner_options.get("android", {})
        if isinstance(android, dict):
            prior["android"] = android.get(key)
        flag_name = (
            "--no-metaspace-guard"
            if attr == "gradle_metaspace_mb" and bool(getattr(args, "disable_metaspace_guard", False))
            else f"--{attr.replace('_', '-')}"
        )
        remember_cli_override(
            key,
            parsed,
            flag=flag_name,
            prior=prior,
        )
        validation[key] = parsed

    low_priority = getattr(args, "validation_low_priority", None)
    if low_priority is not None:
        remember_cli_override(
            "low_priority",
            bool(low_priority),
            flag="--low-priority" if bool(low_priority) else "--normal-priority",
            prior={"validation": validation.get("low_priority")},
        )
        validation["low_priority"] = bool(low_priority)
    parallel = getattr(args, "validation_parallel", None)
    if parallel is not None:
        remember_cli_override(
            "parallel",
            bool(parallel),
            flag="--parallel" if bool(parallel) else "--no-parallel",
            prior={
                "validation": validation.get("parallel"),
                "gradle": gradle.get("parallel"),
            },
        )
        validation["parallel"] = bool(parallel)
        gradle["parallel"] = bool(parallel)
    build_cache = getattr(args, "validation_build_cache", None)
    if build_cache is not None:
        remember_cli_override(
            "build_cache",
            bool(build_cache),
            flag="--build-cache" if bool(build_cache) else "--no-build-cache",
            prior={"gradle": gradle.get("build_cache")},
        )
        gradle["build_cache"] = bool(build_cache)
    daemon = getattr(args, "validation_daemon", None)
    if daemon is not None:
        no_daemon = not bool(daemon)
        remember_cli_override(
            "no_daemon",
            no_daemon,
            flag="--daemon" if bool(daemon) else "--no-daemon",
            prior={
                "validation": validation.get("no_daemon"),
                "gradle": gradle.get("no_daemon"),
            },
        )
        validation["no_daemon"] = no_daemon
        gradle["no_daemon"] = no_daemon


def _runner_option_delta(base: dict, current: dict) -> dict:
    """Return only runtime changes layered on top of configured runner options."""
    delta: dict = {}
    for key, value in current.items():
        if key not in base:
            delta[key] = copy.deepcopy(value)
            continue
        base_value = base.get(key)
        if isinstance(base_value, dict) and isinstance(value, dict):
            nested = _runner_option_delta(base_value, value)
            if nested:
                delta[key] = nested
        elif value != base_value:
            delta[key] = copy.deepcopy(value)
    return delta


def _apply_runner_cli_options(config: ProjectConfig, args: argparse.Namespace) -> None:
    if str(getattr(args, "proot_distro", "") or "").strip():
        config.execution_environment = EXECUTION_PROOT_DISTRO
    termux = config.runner_options.setdefault("termux", {})
    if not isinstance(termux, dict):
        termux = {}
        config.runner_options["termux"] = termux
    requested_profile = str(getattr(args, "termux_resource_profile", "") or termux.get("resource_profile") or "balanced")
    if str(getattr(args, "termux_resource_profile", "") or ""):
        termux["resource_profile"] = requested_profile
    if bool(getattr(args, "termux_wake_lock", False)):
        termux["wake_lock"] = "on"
    elif bool(getattr(args, "no_termux_wake_lock", False)):
        termux["wake_lock"] = "off"
    if str(getattr(args, "shared_storage_policy", "") or ""):
        storage = termux.setdefault("storage", {})
        if isinstance(storage, dict):
            storage["shared_storage_policy"] = args.shared_storage_policy
    if str(getattr(args, "artifact_export", "") or ""):
        artifacts = termux.setdefault("artifacts", {})
        if isinstance(artifacts, dict):
            artifacts["export_dir"] = args.artifact_export
    from .termux_runtime import apply_termux_resource_defaults
    requested_profile_normalized = requested_profile.strip().lower().replace("_", "-")
    requested_execution = str(getattr(args, "execution_environment", "") or config.execution_environment or "auto").strip().lower().replace("_", "-")
    if str(getattr(args, "ssh", "") or "").strip():
        requested_execution = "ssh"
    # Termux resource limits describe work running on this Android device. Do
    # not bake phone-sized Gradle/JVM limits into SSH command plans that will
    # execute on an independent remote host. Chroot and PRoot still share the
    # phone's CPU/RAM and therefore keep the Termux profile.
    if (
        requested_execution not in {"ssh", "desktop-linux", "android-device", "external-device"}
        and requested_profile_normalized not in {"off", "none", "disabled", "disable"}
    ):
        apply_termux_resource_defaults(config.runner_options, profile_name=requested_profile)
        termux["_resource_profile_applied"] = True
    else:
        termux["_resource_profile_applied"] = False
    apply_validation_workflow_profile(config, args)
    _apply_validation_resource_profile(config, args)
    _apply_validation_resource_overrides(config, args)
    def ensure_section(name: str) -> dict:
        section = config.runner_options.get(name, {})
        if not isinstance(section, dict):
            section = {}
        config.runner_options[name] = section
        return section

    if str(getattr(args, "command", "") or "") in {
        "lint",
        "validate",
        "v",
        "validate-all",
        "wine-validate",
        "wv",
        "start-validation",
        "sv",
        "resume-validation",
        "rv",
        *APPLY_COMMANDS,
    }:
        python_options = ensure_section("python")

        # Source formatting must always be explicitly requested.
        # Normal validation remains check-only.
        python_options["format_fix"] = bool(
            getattr(args, "python_format_fix", False)
        )

    if bool(getattr(args, "offline", False)):
        bootstrap = ensure_section("bootstrap")
        android = ensure_section("android")
        gradle = ensure_section("gradle")
        bootstrap["downloads"] = "never"
        bootstrap["network"] = "deny"
        android["offline"] = True
        gradle["offline"] = True
    explicit_bootstrap_downloads = str(getattr(args, "bootstrap_downloads", "") or "").strip()
    if explicit_bootstrap_downloads:
        bootstrap = ensure_section("bootstrap")
        bootstrap["downloads"] = explicit_bootstrap_downloads
    if bool(getattr(args, "no_bootstrap_downloads", False)):
        bootstrap = ensure_section("bootstrap")
        bootstrap["downloads"] = "never"
    if bool(getattr(args, "deny_network", False)):
        bootstrap = ensure_section("bootstrap")
        bootstrap["network"] = "deny"
    profile = str(getattr(args, "toolchain_profile", "") or "").strip()
    if profile:
        config.runner_options["toolchain_profile"] = profile
        config.chroot_name = profile
        chroots = config.runner_options.get("chroots", {})
        selected = chroots.get(profile) if isinstance(chroots, dict) else None
        if isinstance(selected, dict):
            for section_name in ("android", "gradle", "bootstrap"):
                section_value = selected.get(section_name, {})
                if not isinstance(section_value, dict):
                    continue
                current = config.runner_options.get(section_name, {})
                config.runner_options[section_name] = {
                    **dict(section_value),
                    **(dict(current) if isinstance(current, dict) else {}),
                }
    sdk_root = str(getattr(args, "android_sdk_root", "") or "").strip()
    if sdk_root:
        android = ensure_section("android")
        android["sdk_root"] = sdk_root
    sdk_provider = str(getattr(args, "android_sdk_provider", "") or "").strip()
    if sdk_provider:
        android = ensure_section("android")
        android["sdk_provider"] = sdk_provider
    java_home = str(getattr(args, "java_home", "") or "").strip()
    if java_home:
        android = ensure_section("android")
        android["java_home"] = java_home
    java_provider = str(getattr(args, "java_provider", "") or "").strip()
    if java_provider:
        android = ensure_section("android")
        java_options = android.get("java", {}) if isinstance(android.get("java", {}), dict) else {}
        java_options = dict(java_options)
        java_options["provider"] = java_provider
        android["java"] = java_options
    gradle_command = str(getattr(args, "gradle_command", "") or "").strip()
    if gradle_command:
        gradle = ensure_section("gradle")
        gradle["command"] = gradle_command
    gradle_provider = str(getattr(args, "gradle_provider", "") or "").strip()
    if gradle_provider:
        gradle = ensure_section("gradle")
        gradle["provider"] = gradle_provider
    gradle_user_home = str(getattr(args, "gradle_user_home", "") or "").strip()
    if gradle_user_home:
        gradle = ensure_section("gradle")
        android = ensure_section("android")
        gradle["user_home"] = gradle_user_home
        android["gradle_user_home"] = gradle_user_home
    native_provider = str(getattr(args, "native_provider", "") or "").strip()
    native_cache = str(getattr(args, "native_cache", "") or "").strip()
    if native_provider or native_cache:
        android = ensure_section("android")
        native_options = android.get("native", {}) if isinstance(android.get("native", {}), dict) else {}
        native_options = dict(native_options)
        if native_provider:
            native_options["provider"] = native_provider
        if native_cache:
            native_options["cache"] = native_cache
        android["native"] = native_options
    build_tools_provider = str(getattr(args, "android_build_tools_provider", "") or "").strip()
    aapt2_provider = str(getattr(args, "aapt2_provider", "") or "").strip()
    if build_tools_provider or aapt2_provider:
        android = ensure_section("android")
        build_tools = android.get("build_tools", {}) if isinstance(android.get("build_tools", {}), dict) else {}
        build_tools = dict(build_tools)
        if build_tools_provider:
            build_tools["provider"] = build_tools_provider
        if aapt2_provider:
            build_tools["aapt2_provider"] = aapt2_provider
        android["build_tools"] = build_tools
    if args.command in {"build", "b", "package", "pkg"}:
        artifacts = config.runner_options.setdefault("artifacts", {})
        if not isinstance(artifacts, dict):
            artifacts = {}
            config.runner_options["artifacts"] = artifacts
        output_dir = str(getattr(args, "artifact_output_dir", "") or "").strip()
        output_name = str(getattr(args, "artifact_output_name", "") or "").strip()
        collision = str(getattr(args, "artifact_collision", "") or "").strip()
        verify_override = getattr(args, "verify_artifacts_cli", None)
        if output_dir:
            artifacts["output_directory"] = output_dir
        elif not (artifacts.get("output_directory") or artifacts.get("output_dir")):
            artifacts["output_directory"] = ".devtool/build-artifacts"
        if output_name:
            artifacts["output_name"] = output_name
        if collision:
            artifacts["collision"] = collision
        if verify_override is not None:
            artifacts["verify"] = bool(verify_override)

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
    pytest_tests = _cli_string_list(getattr(args, "pytest_tests", None))
    if pytest_tests:
        selection["pytest_tests"] = pytest_tests
        selection["pytest_source"] = "cli" if bool(getattr(args, "_devtool_cli_pytest_test_explicit", False)) else str(getattr(args, "_devtool_pytest_selection_source", "explicit") or "explicit")
    pytest_markers = _cli_string_list(getattr(args, "pytest_markers", None))
    if pytest_markers:
        selection["pytest_markers"] = pytest_markers
    pytest_k = str(getattr(args, "pytest_k", "") or "").strip()
    if pytest_k:
        selection["pytest_k"] = pytest_k
    pytest_args = _cli_string_list(getattr(args, "pytest_args", None))
    if pytest_args:
        selection["pytest_args"] = pytest_args
    if bool(getattr(args, "pytest_include_defaults", False)):
        selection["pytest_include_defaults"] = True

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

    apply_registered_bootstrap_policy(config, args)
    config.runtime_runner_overrides = _runner_option_delta(
        dict(config.configured_runner_options or {}),
        dict(config.runner_options or {}),
    )


def _print_test_catalog(config: ProjectConfig, args: argparse.Namespace, *, as_json: bool) -> int:
    from .test_selection import discover_tests_for_ui, resolve_test_selection, select_catalog
    discovery = discover_tests_for_ui(config)
    catalog = list(discovery.tests)
    selected = select_catalog(catalog, resolve_test_selection(config))
    if as_json:
        print(json.dumps({
            "project_type": config.kind,
            "target": config.selected_target,
            "variant": config.variant,
            "discovered": len(catalog),
            "selected": len(selected),
            "discovery_source": discovery.source,
            "discovery_error": discovery.error,
            "test_selection": resolve_test_selection(config).as_dict(),
            "tests": [item.as_dict() for item in selected],
        }, indent=None if getattr(args, "machine", "") == "compact" else 2, ensure_ascii=False))
        return 0
    if not catalog:
        console.print("[yellow]No source-discoverable tests found.[/yellow]")
        return 0
    console.print(f"[bold]Tests[/bold]  selected {len(selected)} of {len(catalog)} [dim]({discovery.source})[/dim]")
    if discovery.error:
        console.print(f"[yellow]Native discovery fallback:[/yellow] {discovery.error.splitlines()[-1]}")
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


def _exclusive_validation_task_values(args: argparse.Namespace) -> list[str]:
    """Return the stable, de-duplicated task list selected by CLI/artifact/session.

    Artifact-declared tasks are a validation-plan contract, not merely test
    selection metadata. Preserve them even if a resumed/deferred path rebuilt
    an argparse namespace without copying ``test_task``.
    """

    values = _cli_string_list(getattr(args, "test_task", None))
    if not values and bool(getattr(args, "_devtool_validation_tasks_declared", False)):
        values = _cli_string_list(getattr(args, "_devtool_manifest_validation_tasks", None))
    return list(dict.fromkeys(values))


_EXCLUSIVE_VALIDATION_ACTIONS: dict[str, str] = {
    "restore": "restore",
    "build": "build",
    "test": "test",
    "lint": "lint",
    "check": "lint",
    "package": "package",
    "pack": "package",
    "package-check": "package-check",
    "package_check": "package-check",
    "pkg-check": "package-check",
    "clean": "clean",
    "run": "run",
}


def _exclusive_validation_skip_flags(args: argparse.Namespace) -> list[str]:
    return [
        flag
        for flag, enabled in (
            ("--skip-restore", bool(getattr(args, "skip_restore", False))),
            ("--skip-build", bool(getattr(args, "skip_build", False))),
            ("--skip-test", bool(getattr(args, "skip_test", False))),
        )
        if enabled
    ]


def _validate_exclusive_task_common(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[str]:
    tasks = _exclusive_validation_task_values(args)
    if not tasks:
        return []
    invalid_options = [task for task in tasks if task.startswith("-")]
    if invalid_options:
        parser.error(
            "--task accepts validation units, not tool options: "
            + ", ".join(invalid_options)
            + ". Pass project-tool options after '--' or configure them explicitly."
        )
    skip_flags = _exclusive_validation_skip_flags(args)
    if skip_flags:
        parser.error(
            "--task already defines the complete validation plan and cannot be combined with "
            + ", ".join(skip_flags)
            + "."
        )
    if bool(getattr(args, "instrumented", False)):
        parser.error("--task cannot be combined with --instrumented; select the instrumentation validation task explicitly instead.")
    return tasks


def _config_with_test_selection_tasks(config: ProjectConfig, tasks: list[str]) -> ProjectConfig:
    runner_options = copy.deepcopy(config.runner_options)
    selection = runner_options.setdefault("test_selection", {})
    if not isinstance(selection, dict):
        selection = {}
        runner_options["test_selection"] = selection
    existing = _cli_string_list(selection.get("tasks") or selection.get("task"))
    selection["tasks"] = list(dict.fromkeys([*existing, *tasks]))
    return replace(config, runner_options=runner_options)




def _config_with_exact_test_selection_tasks(config: ProjectConfig, tasks: list[str]) -> ProjectConfig:
    runner_options = copy.deepcopy(config.runner_options)
    selection = runner_options.setdefault("test_selection", {})
    if not isinstance(selection, dict):
        selection = {}
    selection = dict(selection)
    values = list(dict.fromkeys(tasks))
    selection["tasks"] = values
    if values:
        selection["task"] = values[0]
    else:
        selection.pop("task", None)
    runner_options["test_selection"] = selection
    return replace(config, runner_options=runner_options)

def _python_test_framework(config: ProjectConfig) -> str:
    runner = get_project_runner(config)
    info_fn = getattr(runner, "_info", None)
    if callable(info_fn):
        return str(info_fn(config).framework or "").strip().lower()
    return ""


def _python_validation_specs_for_action(
    config: ProjectConfig,
    action: str,
    test_targets: list[str],
) -> list[CommandSpec]:
    if action != "test" or not test_targets:
        return build_specs(config, action)
    framework = _python_test_framework(config)
    if framework in {"tox", "nox"}:
        return build_specs(_config_with_exact_test_selection_tasks(config, test_targets), "test")
    runner = get_project_runner(config)
    framework_specs = getattr(runner, "_framework_test_specs", None)
    if callable(framework_specs):
        return framework_specs(config, targets=test_targets)
    return build_specs(config, "test")


def _annotate_exclusive_native_specs(
    config: ProjectConfig,
    specs: list[CommandSpec],
    tasks: list[str],
    *,
    source: str,
) -> list[CommandSpec]:
    total = len(specs)
    for index, spec in enumerate(specs, 1):
        metadata = dict(spec.metadata or {})
        components = metadata.get("validation_components")
        if isinstance(components, list):
            validation_components = [str(item) for item in components]
        else:
            validation_components = [str(metadata.get("validation_component") or spec.name)]
        metadata.update({
            "explicit_validation_tasks": list(tasks),
            "native_validation_tasks": list(tasks),
            "validation_components": validation_components,
            "validation_phase_name": spec.name,
            "validation_resumable": True,
            "validation_pipeline": True,
            "validation_phase_index": index,
            "validation_phase_total": total,
            "validation_scope": "explicit-tasks",
            "validation_change_scope": "explicit",
            "validation_plan_source": source,
            "project_type": config.kind,
            "runner": config.kind,
        })
        spec.metadata = metadata
    return specs


def _exclusive_cross_project_validation_specs(
    config: ProjectConfig,
    tasks: list[str],
    parser: argparse.ArgumentParser,
) -> list[CommandSpec]:
    actions_in_order: list[str] = []
    python_test_targets: list[str] = []
    node_test_tasks: list[str] = []
    first_python_test_index: int | None = None

    for index, task in enumerate(tasks):
        value = str(task).strip()
        action = _EXCLUSIVE_VALIDATION_ACTIONS.get(value.lower())
        if action:
            if action not in actions_in_order:
                actions_in_order.append(action)
            continue
        if config.kind == "python":
            python_test_targets.append(value)
            if first_python_test_index is None:
                first_python_test_index = index
            if "test" not in actions_in_order:
                actions_in_order.append("test")
            continue
        if config.kind == "node":
            # Node projects often expose validation units as package.json scripts
            # (for example `unit`, `e2e`, or `typecheck`). Map non-action task
            # names to the runner's native test-script selector instead of
            # rejecting them as Gradle-only values.
            if "test" not in actions_in_order:
                actions_in_order.append("test")
            node_test_tasks.append(value)
            continue
        available = ", ".join(sorted(set(_EXCLUSIVE_VALIDATION_ACTIONS)))
        parser.error(
            f"Validation --task value {value!r} is not supported for project type {config.kind!r}. "
            f"Use one of: {available}. For framework-specific test paths, use --path/--class/--method."
        )

    if config.kind == "node":
        config = _config_with_exact_test_selection_tasks(config, node_test_tasks)
    elif config.kind == "python":
        # _apply_runner_cli_options stores every --task value in test_selection
        # for historical Gradle/test-task metadata. For non-Gradle explicit
        # validation, action names such as `lint` or `package-check` are not
        # framework test selectors and must not leak into tox/nox/npm test
        # selection. Keep other selectors (--path/--class/--method) intact.
        config = _config_with_exact_test_selection_tasks(config, [])

    if first_python_test_index is not None:
        ordered: list[str] = []
        test_inserted = False
        for task in tasks:
            action = _EXCLUSIVE_VALIDATION_ACTIONS.get(str(task).strip().lower())
            if action:
                if action not in ordered:
                    ordered.append(action)
            elif not test_inserted:
                if "test" not in ordered:
                    ordered.append("test")
                test_inserted = True
        actions_in_order = ordered

    specs: list[CommandSpec] = []
    for action in actions_in_order:
        try:
            if config.kind == "python":
                specs.extend(_python_validation_specs_for_action(config, action, python_test_targets))
            else:
                specs.extend(build_specs(config, action))
        except SystemExit as exc:
            detail = str(exc).strip() or f"unsupported action {action!r}"
            parser.error(
                f"Validation --task value {action!r} is not supported for project type {config.kind!r}: {detail}"
            )
    if not specs:
        parser.error("Validation --task did not resolve to any executable validation units.")
    return _annotate_exclusive_native_specs(config, specs, tasks, source="cli")


def _exclusive_android_validation_specs(
    config: ProjectConfig,
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> list[CommandSpec] | None:
    """Build a validation plan containing only explicitly requested Gradle tasks.

    ``--task`` is intentionally stronger than the test-selection flags. Once it
    is present, Devtool must not append preflight, dependency, compile, package,
    lint, finalize, or other configured validation phases. Gradle may still run
    dependencies of the requested tasks, which is native Gradle behavior.
    """

    tasks = _validate_exclusive_task_common(args, parser)
    if not tasks:
        return None
    if config.kind not in {"android", "gradle"}:
        return _exclusive_cross_project_validation_specs(config, tasks, parser)

    from .android_integration import android_builder_arguments_for_tasks, android_gradle_task_execution_plan, plan_android_command
    from .build_optimization import optimized_validation_env, resolve_android_validation_phase_policy
    from .test_selection import apply_test_selection

    plan = plan_android_command(config, "test")
    policy = resolve_android_validation_phase_policy(config, "explicit-tasks")
    task_execution = android_gradle_task_execution_plan(
        config,
        tasks,
        validation=True,
        validation_phase="explicit-tasks",
    )
    lowered = [task.lower() for task in tasks]
    contains_tests = any("test" in task or "androidtest" in task for task in lowered)
    contains_lint = any("lint" in task for task in lowered)
    report_parsers: list[str] = []
    if contains_tests:
        report_parsers.append("gradle-junit")
    if contains_lint:
        report_parsers.append("android-lint")

    spec = CommandSpec(
        f"{config.kind}-validation-explicit-tasks",
        [
            *plan.argv_prefix,
            *android_builder_arguments_for_tasks(
                config,
                tasks,
                scan_artifacts=False,
                validation=True,
                validation_phase="explicit-tasks",
            ),
        ],
        env=optimized_validation_env(config, config.kind),
        cwd=str(plan.project_root),
        metadata={
            "runner": config.kind,
            "project_type": config.kind,
            "gradle_tasks": list(task_execution.optimized),
            "requested_gradle_tasks": list(tasks),
            "gradle_workload_tasks": list(task_execution.workload_tasks),
            "gradle_task_optimization": task_execution.as_dict(),
            "explicit_validation_tasks": list(tasks),
            "android_builder": str(plan.builder),
            "project_root": str(plan.project_root),
            "collect_artifacts": False,
            "verify_artifacts": False,
            "progress_parser": "gradle",
            "report_parser": report_parsers[0] if report_parsers else "",
            "report_parsers": report_parsers,
            "contains_tests": contains_tests,
            "validation_components": ["explicit-tasks"],
            "validation_phase_name": "tasks",
            "validation_resumable": True,
            "validation_pipeline": True,
            "validation_phase_index": 1,
            "validation_phase_total": 1,
            "validation_scope": "explicit-tasks",
            "validation_resource_profile": "android-explicit-tasks",
            "validation_max_workers": policy.max_workers,
            "validation_cpu_limit": policy.cpu_limit,
            "validation_gradle_heap_mb": policy.gradle_heap_mb,
            "validation_memory_guard_mb": policy.memory_guard_mb,
            "validation_low_priority": policy.low_priority,
            "validation_change_scope": "explicit",
        },
    )
    if contains_tests:
        spec = apply_test_selection(config, spec)
    return [spec]


def _annotate_resolved_validation_plan(
    config: ProjectConfig,
    args: argparse.Namespace,
    specs: list[CommandSpec],
) -> list[CommandSpec]:
    cli_declared = bool(getattr(args, "_devtool_cli_test_task_explicit", False))
    manifest_declared = bool(getattr(args, "_devtool_validation_tasks_declared", False))
    tasks_declared = cli_declared or manifest_declared
    source = "cli" if cli_declared else str(getattr(args, "_devtool_validation_task_source", "") or "runner-default")
    declared_tasks = _exclusive_validation_task_values(args) if tasks_declared else []
    manifest = getattr(args, "_devtool_declared_manifest", None)
    if not isinstance(manifest, dict):
        manifest = getattr(args, "_devtool_artifact_manifest", None)
    required = bool(artifact_validation_policy(manifest or {}).get("required", False))
    plan = resolve_validation_plan(
        config,
        specs,
        source=source,
        tasks_declared=tasks_declared,
        declared_tasks=declared_tasks,
        required=required,
    )
    args._devtool_resolved_validation_plan = plan.metadata()
    for index, spec in enumerate(specs, 1):
        metadata = dict(spec.metadata or {})
        metadata.update(plan.metadata())
        metadata["validation_contract_step"] = spec.name
        metadata["validation_contract_step_index"] = index
        metadata["validation_contract_step_total"] = len(specs)
        spec.metadata = metadata
    return specs


def _validation_specs(config: ProjectConfig, args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[CommandSpec]:
    _apply_runner_cli_options(config, args)
    planning_config = _config_for_validation_planning(config, args)
    explicit_specs = _exclusive_android_validation_specs(planning_config, args, parser)
    if explicit_specs is not None:
        return _annotate_resolved_validation_plan(planning_config, args, explicit_specs)
    skip_restore, skip_build, skip_test = _validation_skip_values(args)
    if skip_restore and skip_build and skip_test:
        parser.error("Validation would run no commands. Clear at least one skip flag.")
    specs = build_validation_specs(
        planning_config,
        wine=args.command in {"wine-validate", "wv"},
        skip_restore=skip_restore,
        skip_build=skip_build,
        skip_test=skip_test,
    )
    return _annotate_resolved_validation_plan(planning_config, args, specs)



def _matrix_items(args: argparse.Namespace) -> list[str]:
    return [item.strip() for item in str(getattr(args, "matrix", "") or "").split(",") if item.strip()]


def _execution_plan_for_args(config: ProjectConfig, args: argparse.Namespace):
    requested = str(getattr(args, "execution_environment", EXECUTION_AUTO) or EXECUTION_AUTO)
    configured_environment = str(config.execution_environment or EXECUTION_AUTO)
    if str(os.environ.get("DEVTOOL_INSIDE_SSH", "")).strip().lower() in {"1", "true", "yes", "on"} and configured_environment == EXECUTION_SSH:
        configured_environment = EXECUTION_AUTO
    current_environment = detect_current_environment()
    explicit_chroot = bool(str(getattr(args, "chroot", "") or "").strip())
    auto_promoted_to_chroot = False
    if bool(getattr(args, "instrumented", False)):
        requested = current_environment
    if bool(getattr(args, "host", False)):
        requested = current_environment
    elif str(getattr(args, "proot_distro", "") or "").strip():
        requested = EXECUTION_PROOT_DISTRO
    elif explicit_chroot:
        requested = EXECUTION_CHROOT
    elif requested == EXECUTION_AUTO and configured_environment == EXECUTION_AUTO:
        if current_environment == EXECUTION_NATIVE_TERMUX and not bool(getattr(args, "no_auto_chroot", False)):
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
    workflow_scoped = bool(getattr(args, "_devtool_workflow_scoped_execution", False))
    if not workflow_scoped:
        items = _matrix_items(args)
        if items and getattr(args, "matrix_mode", "rid") == "rid":
            specs = apply_matrix(specs, items, mode="rid")

        # MP04 workflows may intentionally mix runners and execution
        # environments. Reuse this proven execution-preparation path one spec
        # at a time so each node gets its own toolchain/env/chroot/device
        # semantics while preserving the graph's topological order. Explicit
        # CLI environment flags still win because the cloned Namespace keeps
        # them unchanged.
        if any(bool(spec.metadata.get("workflow_graph")) for spec in specs):
            def merge_options(base: dict, extra: dict) -> dict:
                merged = copy.deepcopy(base)
                for key, value in extra.items():
                    if isinstance(value, dict) and isinstance(merged.get(key), dict):
                        merged[key] = merge_options(dict(merged[key]), value)
                    else:
                        merged[key] = copy.deepcopy(value)
                return merged

            prepared: list[CommandSpec] = []
            for spec in specs:
                metadata = spec.metadata if isinstance(spec.metadata, dict) else {}
                requested = str(metadata.get("workflow_execution_environment") or "auto").strip()
                if requested in {"", "auto"}:
                    requested = config.execution_environment
                scoped_options = metadata.get("workflow_runner_options")
                scoped_config = replace(
                    config,
                    execution_environment=requested,
                    chroot_name=str(metadata.get("workflow_chroot_name") or config.chroot_name),
                    device_serial=str(metadata.get("workflow_device_serial") or config.device_serial),
                    runner_options=merge_options(
                        config.runner_options,
                        dict(scoped_options) if isinstance(scoped_options, dict) else {},
                    ),
                )
                scoped_args = copy.copy(args)
                setattr(scoped_args, "_devtool_workflow_scoped_execution", True)
                prepared.extend(_prepare_specs_for_execution(scoped_config, [spec], scoped_args))
            return prepared

    plan = _execution_plan_for_args(config, args)

    if plan.current == EXECUTION_NATIVE_TERMUX:
        from .termux_runtime import is_android_shared_storage, shared_storage_message
        termux_options = config.runner_options.get("termux", {})
        termux_options = dict(termux_options) if isinstance(termux_options, dict) else {}
        storage = termux_options.get("storage", {}) if isinstance(termux_options.get("storage", {}), dict) else {}
        policy = str(getattr(args, "shared_storage_policy", "") or storage.get("shared_storage_policy") or "warn").strip().lower()
        if policy not in {"allow", "warn", "deny"}:
            raise SystemExit(
                f"Invalid Termux shared-storage policy {policy!r}; expected allow, warn, or deny."
            )
        if bool(getattr(args, "allow_shared_storage_build", False)):
            policy = "allow"
        protected_paths: list[Path] = [Path(config.repo_root)]
        if config.project_root:
            protected_paths.append(Path(config.project_root))
        protected_paths.extend(Path(spec.cwd) for spec in specs if spec.cwd)
        for spec in specs:
            base = Path(spec.cwd or config.project_root or config.repo_root).expanduser()
            def _output_path(raw: object) -> Path:
                candidate = Path(str(raw)).expanduser()
                return candidate if candidate.is_absolute() else base / candidate
            metadata = spec.metadata if isinstance(spec.metadata, dict) else {}
            for key, value in metadata.items():
                if any(token in str(key).lower() for token in ("output", "artifact")) and isinstance(value, (str, Path)) and str(value).strip():
                    protected_paths.append(_output_path(value))
            argv = [str(item) for item in spec.argv]
            for index, token in enumerate(argv):
                if token in {"-o", "--output", "--output-dir", "--artifact-output"} and index + 1 < len(argv):
                    protected_paths.append(_output_path(argv[index + 1]))
                elif token.startswith(("--output=", "--output-dir=", "--artifact-output=")):
                    protected_paths.append(_output_path(token.split("=", 1)[1]))
        artifacts = termux_options.get("artifacts", {}) if isinstance(termux_options.get("artifacts", {}), dict) else {}
        export_dir = str(artifacts.get("export_dir") or "").strip()
        if export_dir:
            protected_paths.append(Path(export_dir).expanduser())
        seen_storage: set[str] = set()
        for path in protected_paths:
            canonical = str(path.expanduser().resolve(strict=False))
            if canonical in seen_storage or not is_android_shared_storage(canonical) or policy == "allow":
                continue
            seen_storage.add(canonical)
            message = shared_storage_message(canonical)
            if policy == "deny":
                raise SystemExit(message + " Override with --allow-shared-storage-build if intentional.")
            create_console(stderr=True).print(f"[yellow]Warning:[/yellow] {message}")

    if plan.current == EXECUTION_NATIVE_TERMUX and plan.effective == EXECUTION_NATIVE_TERMUX:
        from .termux_runtime import adapt_termux_script_argv
        for spec in specs:
            original = list(spec.argv)
            adapted, message = adapt_termux_script_argv(
                original, cwd=spec.cwd or config.repo_root, env={**os.environ, **config.env, **spec.env}
            )
            if adapted != original:
                metadata = dict(spec.metadata)
                metadata.setdefault("progress_argv", original)
                metadata["termux_shebang_adapted"] = message
                spec.metadata = metadata
                spec.argv = adapted
            elif message:
                create_console(stderr=True).print(f"[yellow]Warning:[/yellow] {message}")
        from .termux_runtime import native_termux_executable_hint
        for spec in specs:
            hint = native_termux_executable_hint(
                spec.argv, cwd=spec.cwd or config.repo_root, env={**os.environ, **config.env, **spec.env}
            )
            if hint is not None:
                raise SystemExit(f"Native Termux executable preflight failed ({hint.kind}): {hint.detail}")

    # Validation sessions and retries may outlive the staged Android builder
    # that existed when their command specs were first planned. Refresh it at
    # the final execution boundary before any host-to-chroot path wrapping.
    from .android_integration import refresh_android_builders_for_specs
    refresh_android_builders_for_specs(specs)

    destination_backend = (
        (plan.uses_chroot and plan.current != EXECUTION_CHROOT)
        or plan.effective in {EXECUTION_SSH, EXECUTION_PROOT_DISTRO}
    )
    if destination_backend:
        specs = apply_destination_toolchain_wrapper(config, specs)
    else:
        specs = apply_toolchain_wrapper(config, specs)
    preflight = preflight_execution(
        config.repo_root,
        plan,
        expected_paths=(config.restore_target, config.build_target, config.test_target, config.project_root),
        require_expected_paths=True,
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
    elif plan.effective == EXECUTION_PROOT_DISTRO:
        from .proot_distro_runner import resolve_proot_config, wrap_specs_for_proot_distro
        try:
            proot_config = resolve_proot_config(config, args)
            specs = wrap_specs_for_proot_distro(
                specs, proot_config, repo_root=config.repo_root, dry_run=bool(getattr(args, "dry_run", False)),
                project_env=config.env,
            )
            if specs and not bool(getattr(args, "json", False)) and not str(getattr(args, "machine", "") or ""):
                first_metadata = specs[0].metadata
                bindings = first_metadata.get("proot_bindings", [])
                repo_host = str(config.repo_root.resolve())
                repo_guest = ""
                if isinstance(bindings, list):
                    for binding in bindings:
                        if isinstance(binding, dict) and str(binding.get("host") or "") == repo_host:
                            repo_guest = str(binding.get("guest") or "")
                            break
                detail = f"PRoot-Distro {proot_config.distro}: cwd {first_metadata.get('proot_guest_cwd', proot_config.cwd)}"
                if repo_guest:
                    detail += f"; repository {repo_host} -> {repo_guest}"
                create_console(stderr=True).print(f"[cyan]{detail}[/cyan]")
        except ValueError as exc:
            raise SystemExit(f"PRoot-Distro command execution unavailable: {exc}") from exc
    elif plan.effective == EXECUTION_SSH:
        from .ssh_tmux_runner import SSHRunnerConfig, wrap_specs_for_ssh_commands

        ssh_config = getattr(args, "_devtool_ssh_config", None)
        if not isinstance(ssh_config, SSHRunnerConfig):
            raise SystemExit("SSH command execution was selected without resolved SSH connection settings.")
        try:
            specs = wrap_specs_for_ssh_commands(
                specs,
                ssh_config,
                project_config=config,
                repo_root=config.repo_root,
                dry_run=bool(getattr(args, "dry_run", False)),
            )
        except ValueError as exc:
            raise SystemExit(f"SSH command execution unavailable: {exc}") from exc

    for spec in specs:
        metadata = dict(spec.metadata)
        metadata.update(plan.metadata())
        metadata["execution_preflight"] = [
            {"name": item.name, "status": item.status, "detail": item.detail}
            for item in preflight.checks
        ]
        spec.metadata = metadata
    return specs


def _workspace_target_names_for_args(
    config: ProjectConfig,
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> tuple[str, ...]:
    groups = [str(item).strip() for item in getattr(args, "group", []) if str(item).strip()]
    all_targets = bool(getattr(args, "all_targets", False))
    affected = bool(getattr(args, "affected", False))
    if not groups and not all_targets and not affected:
        return ()
    if str(getattr(args, "target", "") or "").strip():
        scope = "--affected" if affected else "--group or --all-targets"
        parser.error(f"--target cannot be combined with {scope}; select one workspace scope")
    if all_targets and groups:
        parser.error("--all-targets cannot be combined with --group because it already selects the complete workspace")
    if str(getattr(args, "matrix", "") or "").strip():
        parser.error("--matrix cannot be combined with workspace --group/--all-targets/--affected selection")
    try:
        if groups or all_targets:
            candidates = resolve_workspace_targets(
                config.workspace,
                config.targets,
                groups=groups,
                all_targets=all_targets,
            )
        else:
            candidates = tuple(config.targets)
        if affected:
            selection = analyze_affected_targets(
                config,
                candidate_targets=candidates,
                base=str(getattr(args, "affected_base", "") or ""),
                head=str(getattr(args, "affected_head", "") or ""),
            )
            setattr(args, "_devtool_affected_selection", selection)
            selected = selection.targets
        else:
            selected = candidates
    except (WorkspaceConfigError, AffectedSelectionError) as exc:
        parser.error(str(exc))
    if not selected:
        if affected:
            setattr(args, "_devtool_affected_empty", True)
            return ()
        parser.error("Workspace target selection resolved to no targets")
    if str(getattr(args, "variant", "") or "").strip():
        variant = str(args.variant).strip()
        missing = [name for name in selected if variant not in config.targets[name].variants]
        if missing:
            parser.error(
                f"--variant {variant!r} is not declared by workspace target(s): {', '.join(missing)}"
            )
    return tuple(selected)


def _workspace_selection_metadata(args: argparse.Namespace) -> tuple[str, list[str]]:
    if bool(getattr(args, "affected", False)):
        return "affected", [str(item) for item in getattr(args, "group", []) if str(item).strip()]
    if bool(getattr(args, "all_targets", False)):
        return "all", []
    return "group", [str(item) for item in getattr(args, "group", []) if str(item).strip()]


def _validation_specs_for_args(config: ProjectConfig, args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[CommandSpec]:
    workspace_targets = _workspace_target_names_for_args(config, args, parser)
    if bool(getattr(args, "affected", False)) and bool(getattr(args, "_devtool_affected_empty", False)):
        return []
    if workspace_targets:
        if _exclusive_validation_task_values(args):
            parser.error("Explicit --task Gradle validation selection cannot be combined with workspace --group/--all-targets; declare per-target workflows instead")
        skip_restore, skip_build, skip_test = _validation_skip_values(args)
        specs = build_workspace_validation_specs(
            config,
            workspace_targets,
            wine=args.command in {"wine-validate", "wv"},
            skip_restore=skip_restore,
            skip_build=skip_build,
            skip_test=skip_test,
        )
        for spec in specs:
            metadata = dict(spec.metadata)
            selection_kind, selection_groups = _workspace_selection_metadata(args)
            metadata["workspace_selection"] = selection_kind
            metadata["workspace_groups"] = selection_groups
            affected_selection = getattr(args, "_devtool_affected_selection", None)
            if affected_selection is not None:
                metadata["affected_changed_paths"] = list(affected_selection.changed_paths)
                metadata["affected_targets"] = list(affected_selection.targets)
            spec.metadata = metadata
        return _annotate_resolved_validation_plan(config, args, specs)

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
    prepared_specs = _prepare_specs_for_execution(config, specs, args)
    termux_options = config.runner_options.get("termux", {})
    termux_options = dict(termux_options) if isinstance(termux_options, dict) else {}
    wake_mode = str(termux_options.get("wake_lock") or "auto")
    if bool(getattr(args, "termux_wake_lock", False)):
        wake_mode = "on"
    elif bool(getattr(args, "no_termux_wake_lock", False)):
        wake_mode = "off"
    from .termux_runtime import termux_wake_lock
    completed_successfully = False
    try:
        with termux_wake_lock(wake_mode):
            workflow_graph = any(bool(spec.metadata.get("workflow_graph")) for spec in prepared_specs)
            if workflow_graph:
                requested = getattr(args, "workflow_parallel", None)
                # Validation's established --parallel flag keeps its Gradle
                # meaning and additionally opts the DAG scheduler into safe
                # parallel waves. It never bypasses scope/resource checks.
                if requested is None and getattr(args, "validation_parallel", None) is True:
                    requested = True
                max_workers = int(getattr(args, "workflow_max_workers", 0) or 0)
                if max_workers <= 0:
                    max_workers = int(getattr(args, "max_workers", 0) or 0)
                results, raw, workflow_plan = run_workflow_specs(
                    config,
                    prepared_specs,
                    verbose=args.verbose,
                    dry_run=args.dry_run,
                    continue_on_error=continue_on_error,
                    log_path=log_path,
                    tasker_progress=tasker_progress,
                    shutdown_dotnet=shutdown_dotnet,
                    supervisor_probe=supervisor_probe,
                    heartbeat_callback=heartbeat_callback,
                    progress_callback=progress_callback,
                    parallel_requested=requested,
                    max_parallel_nodes=max_workers,
                )
                if workflow_plan is not None:
                    args._devtool_workflow_execution_plan = workflow_plan
                    for result in results:
                        metadata = dict(result.metadata)
                        metadata["workflow_schedule"] = {
                            "parallel_requested": workflow_plan.parallel_requested,
                            "parallel_effective": workflow_plan.parallel_effective,
                            "wave_count": len(workflow_plan.waves),
                            "max_parallel_nodes": workflow_plan.max_parallel_nodes,
                        }
                        result.metadata = metadata
            else:
                results, raw = run_specs(
                    config,
                    prepared_specs,
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
        completed_successfully = all(result.skipped or result.returncode == 0 for result in results)
        return results, raw
    finally:
        if any(str(spec.metadata.get("execution_mode") or "") == "proot-distro" for spec in prepared_specs):
            from .proot_distro_runner import cleanup_proot_staging
            for warning in cleanup_proot_staging(prepared_specs, retain=not completed_successfully):
                create_console(stderr=True).print(f"[yellow]Warning:[/yellow] {warning}")



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
    _apply_validation_resource_overrides(config, args)
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
        "test_task": _exclusive_validation_task_values(args),
        "validation_tasks_declared": bool(getattr(args, "_devtool_validation_tasks_declared", False) or getattr(args, "_devtool_cli_test_task_explicit", False)),
        "validation_task_source": ("cli" if bool(getattr(args, "_devtool_cli_test_task_explicit", False)) else str(getattr(args, "_devtool_validation_task_source", "") or "")),
        "max_runtime": str(getattr(args, "max_runtime", "") or ""),
        "stop_below_battery": int(getattr(args, "stop_below_battery", 0) or 0),
        "require_charging": bool(getattr(args, "require_charging", False)),
        "stop_above_temperature": float(getattr(args, "stop_above_temperature", 0.0) or 0.0),
        "stop_below_disk_mb": max(0, int(getattr(args, "stop_below_disk_mb", 256) or 0)),
        "require_complete": bool(getattr(args, "require_complete", False)),
        "stop_gradle_after_phase": bool(getattr(args, "stop_gradle_after_phase", False)),
        "keep_gradle_daemon": bool(getattr(args, "keep_gradle_daemon", False)),
        "validation_resource_profile": str(getattr(args, "validation_resource_profile", "automatic") or "automatic"),
        "max_workers": getattr(args, "max_workers", None),
        "cpu_limit": getattr(args, "cpu_limit", None),
        "gradle_heap_mb": getattr(args, "gradle_heap_mb", None),
        "memory_guard_mb": getattr(args, "memory_guard_mb", None),
        "validation_low_priority": getattr(args, "validation_low_priority", None),
        "validation_parallel": getattr(args, "validation_parallel", None),
        "validation_build_cache": getattr(args, "validation_build_cache", None),
        "validation_daemon": getattr(args, "validation_daemon", None),
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
    exclusive_tasks = _exclusive_validation_task_values(args)
    context: dict[str, object] = {
        "command": str(getattr(args, "command", "") or "apply-overlay"),
        "artifacts": artifact_values,
        "artifact": artifact_values[0] if len(artifact_values) == 1 else artifact_values,
        "artifact_type": str(getattr(args, "_devtool_artifact_type", "") or ""),
        "artifact_sha": str(getattr(args, "_devtool_artifact_sha", "") or ""),
        "artifact_size": getattr(args, "_devtool_artifact_size", None),
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
        "minimum_phase": "finalize" if exclusive_tasks else str(validation_policy.get("minimum_phase") or "finalize"),
        "resource_profile": str(getattr(args, "validation_resource_profile", "automatic") or "automatic"),
        "exclusive_validation_tasks": list(exclusive_tasks),
        "validation_tasks_declared": bool(getattr(args, "_devtool_validation_tasks_declared", False) or getattr(args, "_devtool_cli_test_task_explicit", False)),
        "validation_task_source": ("cli" if bool(getattr(args, "_devtool_cli_test_task_explicit", False)) else str(getattr(args, "_devtool_validation_task_source", "") or "artifact-manifest")),
        "pytest_tests": _cli_string_list(getattr(args, "pytest_tests", None)),
        "pytest_markers": _cli_string_list(getattr(args, "pytest_markers", None)),
        "pytest_k": str(getattr(args, "pytest_k", "") or ""),
        "pytest_args": _cli_string_list(getattr(args, "pytest_args", None)),
        "pytest_include_defaults": bool(getattr(args, "pytest_include_defaults", False)),
        "pytest_selection_source": ("cli" if bool(getattr(args, "_devtool_cli_pytest_test_explicit", False)) else str(getattr(args, "_devtool_pytest_selection_source", "") or "artifact-manifest")),
        "artifact_script_in_plan": bool(validation_policy.get("script")) and not bool(getattr(args, "no_artifact_script", False)) and not exclusive_tasks,
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


def _rehydrate_apply_validation_context(
    config: ProjectConfig,
    args: argparse.Namespace,
    context: dict[str, object],
) -> None:
    manifest = context.get("manifest") if isinstance(context.get("manifest"), dict) else {}
    preview = context.get("preview") if isinstance(context.get("preview"), dict) else {}
    if not manifest and not preview and not context.get("validation_tasks_declared"):
        return
    if preview:
        args._devtool_artifact_preview = dict(preview)

    context_declared = bool(context.get("validation_tasks_declared", False))
    context_tasks = list(dict.fromkeys(_cli_string_list(context.get("exclusive_validation_tasks", []))))
    if context_declared and not bool(getattr(args, "_devtool_cli_test_task_explicit", False)):
        args.test_task = list(context_tasks)
        args._devtool_manifest_validation_tasks = list(context_tasks)
        args._devtool_validation_tasks_declared = True
        args._devtool_validation_task_source = str(context.get("validation_task_source") or "artifact-manifest")

    if not bool(getattr(args, "_devtool_cli_pytest_test_explicit", False)) and context.get("pytest_tests") is not None:
        args.pytest_tests = _cli_string_list(context.get("pytest_tests"))
        args._devtool_pytest_selection_source = str(context.get("pytest_selection_source") or "stored-apply-context")
    if not bool(getattr(args, "_devtool_cli_pytest_marker_explicit", False)) and context.get("pytest_markers") is not None:
        args.pytest_markers = _cli_string_list(context.get("pytest_markers"))
    if not bool(getattr(args, "_devtool_cli_pytest_k_explicit", False)) and context.get("pytest_k") is not None:
        args.pytest_k = str(context.get("pytest_k") or "")
    if not bool(getattr(args, "_devtool_cli_pytest_arg_explicit", False)) and context.get("pytest_args") is not None:
        args.pytest_args = _cli_string_list(context.get("pytest_args"))
    if bool(context.get("pytest_include_defaults", False)):
        args.pytest_include_defaults = True

    if manifest:
        manifest_declared, manifest_tasks = artifact_validation_task_declaration(dict(manifest))
        if context_declared and manifest_declared and list(manifest_tasks) != context_tasks:
            raise ValidationSessionError(
                "Deferred validation task contract changed since apply: "
                f"stored={context_tasks!r}, artifact={list(manifest_tasks)!r}. "
                "Restart validation or explicitly accept the plan change instead of falling back to default phases."
            )
    # Preview-only deferred sessions still need changed-path scoping even when
    # their original artifact had no manifest.
    _apply_artifact_validation_defaults(config, args, dict(manifest or {}))


def _load_or_create_validation_session(
    config: ProjectConfig,
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    transaction_path: str = "",
    apply_context: dict[str, object] | None = None,
) -> tuple[ValidationSession, list[CommandSpec]]:
    requested_session = str(getattr(args, "session", "") or "").strip()
    mode = str(getattr(args, "_devtool_validation_mode", "") or "").strip()
    explicitly_resuming = mode == "resume" or bool(getattr(args, "resume", False)) or bool(requested_session)
    force_new = mode == "start" or bool(getattr(args, "restart_session", False))
    if requested_session:
        active = load_validation_session(config.repo_root, requested_session)
    elif force_new:
        try:
            active = load_active_session(config.repo_root)
        except ValidationSessionError:
            active = None
    else:
        active = resolve_resumable_session(config.repo_root, required=explicitly_resuming)
    if active is not None and str(getattr(args, "_devtool_validation_api_action", "") or ""):
        previous_revision = require_validation_revision(active, _validation_expected_revision(args))
        args._devtool_validation_previous_revision = previous_revision
    if explicitly_resuming and active is None:
        raise ValidationSessionError("No matching active validation session exists to resume.")

    start_new = force_new or active is None
    if not start_new and active is not None:
        selected_target = str(config.selected_target or "")
        selected_variant = str(config.variant or "")
        if selected_target != active.target:
            raise ValidationSessionError(
                f"Validation session target is {active.target or '-'}, but the selected target is "
                f"{selected_target or '-'}. Use --target {active.target} or start a new session explicitly."
            )
        if selected_variant != active.variant:
            raise ValidationSessionError(
                f"Validation session variant is {active.variant or '-'}, but the selected variant is "
                f"{selected_variant or '-'}. Select the session variant or start a new session explicitly."
            )
        live_phase = live_validation_phase(active)
        if live_phase is not None:
            raise ValidationSessionError(
                f"Validation session {active.session_id} is already running. "
                f"Current phase: {live_phase.label}, phase {live_phase.index} of {live_phase.total}; "
                f"owner PID: {live_phase.owner_pid}."
            )
        # A resumed plan must retain its original task list and skip/daemon
        # choices. Runtime, battery, temperature and phase selection are per-run.
        stored_tasks = list(dict.fromkeys(_cli_string_list(active.options.get("test_task", []))))
        requested_tasks = _exclusive_validation_task_values(args)
        if requested_tasks and requested_tasks != stored_tasks:
            raise ValidationSessionError(
                "Validation session task list differs from the requested --task values. "
                "Resume without --task, use the original task list, or start a new session explicitly."
            )
        if stored_tasks:
            args.test_task = list(stored_tasks)
        if bool(active.options.get("validation_tasks_declared", False)) or stored_tasks:
            args._devtool_validation_tasks_declared = True
            args._devtool_manifest_validation_tasks = list(stored_tasks)
            args._devtool_validation_task_source = str(active.options.get("validation_task_source") or "session")
        for name in ("skip_restore", "skip_build", "skip_test", "stop_gradle_after_phase", "keep_gradle_daemon", "validation_resource_profile", "max_workers", "cpu_limit", "gradle_heap_mb", "gradle_metaspace_mb", "memory_guard_mb", "validation_low_priority", "validation_parallel", "validation_build_cache", "validation_daemon", "max_infrastructure_retries", "max_resource_retries", "no_auto_retry", "rollback_on_failure", "failure_action"):
            if name in active.options:
                setattr(args, name, active.options[name])

    _enable_resumable_validation(config, args)
    effective_context = dict(apply_context or (active.apply_context if active is not None else {}) or {})
    if active is not None and not start_new:
        _rehydrate_apply_validation_context(config, args, effective_context)
    script_only = bool(effective_context.get("artifact_script_in_plan", False)) and all(
        bool(getattr(args, name, False)) for name in ("skip_restore", "skip_build", "skip_test")
    )
    if start_new:
        # Planning is intentionally done once. Android planning may inspect a large
        # Gradle tree and resolve chroot/toolchain details, so a sequential resume
        # must not pay that cost again or accidentally drift the phase commands.
        specs = [] if script_only else _validation_specs_for_args(config, args, parser)
        specs = _with_artifact_validation_phase(specs, effective_context)
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

        specs = stored_validation_specs(session)
        durable_plan = bool(specs)
        if not specs:
            # Schema-v5 and older sessions did not persist executable specs. Do
            # one compatibility replan, synchronize it, and backfill the durable
            # plan so all following resumes are exact and cheap.
            specs = [] if script_only else _validation_specs_for_args(config, args, parser)
            specs = _with_artifact_validation_phase(specs, effective_context)
            specs = sync_validation_plan(session, specs)
        reconcile_validation_inputs(
            session,
            config,
            ignore_domains=("config",) if durable_plan else (),
        )
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
    final_args._devtool_artifact_size = context.get("artifact_size")
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
    exclusive_tasks = [str(item) for item in context.get("exclusive_validation_tasks") or [] if str(item)]
    if artifact_path is not None and not exclusive_tasks and not bool(context.get("artifact_script_in_plan", False)) and not any(result.returncode != 0 for result in results):
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
        and not exclusive_tasks
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
            evidence_line = f"Validation evidence preserved: {evidence}"
            raw_output += "\n\n" + evidence_line
            _append_run_log(log_path, evidence_line)
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
    results, raw_output = _maybe_commit_after_apply(config, final_args, results, raw_output, log_path=log_path, tasker_progress=tasker_progress)
    _record_transaction_commit_from_results(
        session.transaction_path, config.repo_root, results, start_index=commit_start
    )
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
        explicitly_resuming = mode == "resume" or bool(getattr(args, "resume", False)) or bool(requested)
        force_new = mode == "start" or bool(getattr(args, "restart_session", False))
        if requested:
            active = load_validation_session(config.repo_root, requested)
        elif force_new:
            try:
                active = load_active_session(config.repo_root)
            except ValidationSessionError:
                active = None
        else:
            active = resolve_resumable_session(
                config.repo_root,
                required=explicitly_resuming,
                repair_active_marker=False,
            )
        start_new = mode == "start" or bool(getattr(args, "restart_session", False)) or active is None
        if active is not None and not start_new:
            selected_target = str(config.selected_target or "")
            selected_variant = str(config.variant or "")
            if selected_target != active.target or selected_variant != active.variant:
                raise ValidationSessionError(
                    "Validation session target or variant does not match the selected configuration."
                )
            live_phase = live_validation_phase(active)
            if live_phase is not None:
                raise ValidationSessionError(
                    f"Validation session {active.session_id} is already running phase "
                    f"{live_phase.index} of {live_phase.total} ({live_phase.label}), owner PID {live_phase.owner_pid}."
                )
        if active is not None and not start_new:
            stored_tasks = list(dict.fromkeys(_cli_string_list(active.options.get("test_task", []))))
            requested_tasks = _exclusive_validation_task_values(args)
            if requested_tasks and requested_tasks != stored_tasks:
                raise ValidationSessionError(
                    "Validation session task list differs from the requested --task values. "
                    "Resume without --task, use the original task list, or start a new session explicitly."
                )
            if stored_tasks:
                args.test_task = list(stored_tasks)
            if bool(active.options.get("validation_tasks_declared", False)) or stored_tasks:
                args._devtool_validation_tasks_declared = True
                args._devtool_manifest_validation_tasks = list(stored_tasks)
                args._devtool_validation_task_source = str(active.options.get("validation_task_source") or "session")
        _enable_resumable_validation(config, args)
        effective_context = dict(apply_context or (active.apply_context if active is not None else {}) or {})
        if active is not None and not start_new:
            _rehydrate_apply_validation_context(config, args, effective_context)
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
            session = copy.deepcopy(active)
            original_repo_root = session.repo_root
            with tempfile.TemporaryDirectory() as dry_run_state:
                session.repo_root = str(Path(dry_run_state).resolve())
                try:
                    sync_validation_plan(session, preview_specs)
                except ValidationSessionError as exc:
                    lines = [
                        "VALIDATION PREVIEW",
                        f"Session: {active.session_id}",
                        f"Failure action: {active.failure_action}",
                        "Session state was not changed.",
                        "Plan drift:",
                        f"  {exc}",
                    ]
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
                            "validation_session_id": active.session_id,
                            "selected_phase_keys": [],
                            "state_changed": False,
                            "plan_drift_status": session.plan_drift_status,
                            "plan_drift_details": list(session.plan_drift_details),
                        },
                    )
                    return [preview], preview.output, active
                finally:
                    session.repo_root = original_repo_root

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
        ]
        if session.plan_drift_status:
            lines.append(f"Plan drift: {session.plan_drift_status}")
        lines.append("Would run:")
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
            evidence_line = f"Validation evidence preserved: {evidence}"
            raw_output += ("\n\n" if raw_output else "") + evidence_line
            _append_run_log(log_path, evidence_line)
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
    stored_tasks = list(dict.fromkeys(_cli_string_list(session.options.get("test_task", []))))
    if stored_tasks:
        args.test_task = stored_tasks
    for name in (
        "skip_restore", "skip_build", "skip_test", "stop_gradle_after_phase",
        "keep_gradle_daemon", "validation_resource_profile", "max_workers", "cpu_limit",
        "gradle_heap_mb", "gradle_metaspace_mb", "memory_guard_mb", "validation_low_priority", "validation_parallel",
        "validation_build_cache", "validation_daemon", "max_infrastructure_retries",
        "max_resource_retries", "no_auto_retry", "rollback_on_failure", "failure_action",
    ):
        if name in session.options:
            setattr(args, name, session.options[name])
    _enable_resumable_validation(config, args)
    context = dict(session.apply_context or {})
    _rehydrate_apply_validation_context(config, args, context)
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
    apply_result = next(
        (
            result for result in results
            if not (result.name == "apply-rollback" and result.failure_kind == "stale_apply_rolled_back")
        ),
        None,
    )
    rolled_back = any(
        r.name == "apply-rollback"
        and r.returncode == 0
        and r.failure_kind != "stale_apply_rolled_back"
        for r in results
    )
    rollback_failed = any(r.name == "apply-rollback" and r.returncode != 0 for r in results)
    if rolled_back:
        status = "rolled-back"
    elif any(r.returncode != 0 for r in results):
        status = "failed"
    else:
        status = "ok" if apply_result and apply_result.returncode == 0 else "failed"

    validation_results = [
        r for r in results
        if r.name not in {"git-add", "git-commit", "apply-rollback"}
        and (
            bool((r.metadata or {}).get("validation_pipeline"))
            or r.name.startswith("artifact-valid")
            or r.name == "validation-contract"
        )
    ]
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
            if isinstance(entry.get("content_identity"), dict):
                entry["content_identity"]["sha256"] = args._devtool_artifact_sha
        artifact_size = getattr(args, "_devtool_artifact_size", None)
        if isinstance(artifact_size, int) and not isinstance(artifact_size, bool) and artifact_size >= 0:
            if isinstance(entry.get("content_identity"), dict):
                entry["content_identity"]["size"] = artifact_size
        run_id = str(getattr(args, "_devtool_run_id", "") or "")
        artifact_sha = str(entry.get("sha256") or "")
        operation_identity = f"{run_id}\0{artifact_sha}\0{getattr(args, 'command', '')}"
        entry["operation_id"] = run_id or hashlib.sha256(operation_identity.encode("utf-8")).hexdigest()[:24]
        entry["history_id"] = hashlib.sha256(operation_identity.encode("utf-8")).hexdigest()[:32]
        entry["rollback_status"] = "rolled-back" if rolled_back else ("failed" if rollback_failed else "not-needed")
        entry["child_outcomes"] = [
            {
                "name": result.name,
                "returncode": result.returncode,
                "skipped": bool(result.skipped),
                "failure_kind": result.failure_kind or "",
                "overlay_id": str((result.metadata or {}).get("overlay_id") or ""),
                "overlay_index": (result.metadata or {}).get("overlay_index"),
                "target": str((result.metadata or {}).get("target") or (result.metadata or {}).get("validation_target") or ""),
            }
            for result in results
        ]
        hook_events = collect_hook_events(results)
        if hook_events:
            entry["hook_events"] = hook_events
        transaction_path = _apply_transaction_path(results)
        if transaction_path:
            transaction_id = Path(transaction_path).parent.name
            try:
                transaction = read_transaction(transaction_path)
            except Exception:
                transaction = read_transaction_summary(config.repo_root, transaction_id)
            entry["transaction"] = {
                "id": str(transaction.get("transaction_id") or transaction_id),
                "status": str(transaction.get("status") or entry["rollback_status"]),
            }
        static_plan = dict(getattr(args, "_devtool_resolved_validation_plan", {}) or {})
        step_outcomes = []
        for result in validation_results:
            metadata = dict(result.metadata or {})
            warnings = sum(1 for issue in result.issues if str(issue.severity).lower() == "warning")
            if result.skipped:
                outcome = "skipped"
            elif result.returncode != 0:
                outcome = "failed"
            elif warnings:
                outcome = "warning"
            else:
                outcome = "passed"
            step_outcomes.append({
                "name": result.name,
                "outcome": outcome,
                "returncode": result.returncode,
                "skipped": bool(result.skipped),
                "warnings": warnings,
                "failure_kind": result.failure_kind or "",
                "step_index": metadata.get("validation_contract_step_index") or metadata.get("validation_phase_index"),
                "step_total": metadata.get("validation_contract_step_total") or metadata.get("validation_phase_total"),
            })
        if step_outcomes:
            static_plan["validation_step_outcomes"] = step_outcomes
        contract_results = [r for r in results if r.name == "validation-contract"]
        if contract_results:
            contract_metadata = dict(contract_results[-1].metadata or {})
            entry["validation_contract"] = contract_metadata
            static_plan["validation_contract_satisfied"] = bool(contract_metadata.get("validation_contract_satisfied", False))
            static_plan["validation_contract_evidence"] = dict(contract_metadata.get("validation_contract_evidence") or {})
        entry["validation_plan"] = static_plan
        validation_session_id = str(getattr(args, "_devtool_validation_session_id", "") or "")
        if validation_session_id:
            entry["validation_session_id"] = validation_session_id
            entry["validation_report_path"] = str(getattr(args, "_devtool_validation_report_path", "") or "")
            entry["validation_final_verdict"] = str(getattr(args, "_devtool_validation_final_verdict", "") or "validated")
        committed = bool(commit_hash)
        history_entry_path = append_history(
            config.repo_root,
            entry,
            repo_local=_repo_local_artifact_history_enabled(config.repo_root) and not committed,
        )
        try:
            args._devtool_history_paths = [
                str(history_entry_path.relative_to(config.repo_root)).replace("\\", "/"),
                ".devtool/artifacts.json",
            ]
        except Exception:
            args._devtool_history_paths = [".devtool/artifacts.json"]
        if status == "ok":
            publish_event(OverlayApplied(artifact=str(artifact), metadata={"commit": commit_hash, "validation_status": validation_status}))
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
    if bool(getattr(args, "list_transactions", False)):
        records = list_active_transaction_records(config.repo_root)
        if not records:
            console.print("[yellow]No active apply transactions.[/yellow]")
            return 0
        rows = []
        for item in records:
            rows.append((
                str(item.get("transaction_id") or "-"),
                f"{item.get('status') or '-'} target={item.get('target') or '-'} paths={len(item.get('reserved_paths') or item.get('affected_paths') or [])} {item.get('path') or ''}",
            ))
        render_status_table(rows)
        return 0

    transaction_selector = str(getattr(args, "transaction", "") or "").strip()
    if transaction_selector or bool(getattr(args, "failed", False)):
        if str(getattr(args, "commit", "") or "").strip() or bool(getattr(args, "last", False)):
            raise SystemExit("Transaction rollback cannot be combined with a Git commit hash or --last.")
        try:
            transaction_path = resolve_active_transaction(
                config.repo_root,
                transaction_selector,
                failed_only=bool(getattr(args, "failed", False)),
            )
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
        result = rollback_transaction(transaction_path, reason="manual rollback requested by user")
        totals, _, _, machine = _emit_summary([result], config, as_json=False, raw_output=result.output, command="rollback")
        _write_json_payload(args.machine_file, machine)
        return 0 if totals["failed"] == 0 else 1

    commit = args.commit
    if args.last:
        entries = [e for e in load_history(config.repo_root) if e.get("commit_hash")]
        if not entries:
            raise SystemExit("No history entry with commit_hash is available for rollback.")
        commit = str(entries[-1]["commit_hash"])
    if not commit:
        raise SystemExit("Pass a commit hash/--last, or use --transaction/--failed for an apply transaction.")
    _ensure_safe_repo(config.repo_root, force=args.force, yes=args.yes, command="rollback", ui_config=config.ui_config)
    specs = [CommandSpec("rollback", ["git", "revert", "--no-edit", commit], destructive=True)]
    results, raw = _run_specs_for_args(config, specs, args, log_path=new_log_path(f"rollback-{commit[:12]}"))
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

    def commit_path_still_relevant(value: object) -> bool:
        """Keep paths which can actually contribute to the artifact commit.

        After validation an artifact may have deleted generated/untracked legacy
        residue. Such a path is absent from both the worktree and HEAD, so an
        exact ``git add`` pathspec would fail with "did not match any files".
        Real tracked deletions remain relevant because they still exist in HEAD.
        """
        text = str(value or "").strip().replace("\\", "/")
        if not text or repo_root is None:
            return True

        target = repo_root / Path(text)
        if target.exists() or target.is_symlink():
            return True

        head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=repo_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if head.returncode != 0:
            return False

        tracked = subprocess.run(
            [
                "git",
                "ls-tree",
                "-r",
                "--name-only",
                "HEAD",
                "--",
                f":(literal){text}",
            ],
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return bool((tracked.stdout or "").strip())

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
                path = change.get("path")
                if not commit_path_still_relevant(path):
                    continue
                add_path(path)

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

def _artifact_hook_presentation(args: argparse.Namespace) -> tuple[bool, bool]:
    """Return (render_progress, stream_output) for embedded artifact hooks.

    Normal interactive CLI runs use Devtool's shared Rich progress surface.
    Verbose runs stream child output like other verbose commands. Machine/quiet
    modes keep hook presentation silent while retaining durable logs.
    """
    machine = bool(getattr(args, "_devtool_json", False))
    quiet = bool(getattr(args, "quiet", False))
    verbose = bool(getattr(args, "verbose", False))
    return (not machine and not quiet and not verbose, verbose and not machine and not quiet)


def _maybe_commit_after_apply(
    config: ProjectConfig,
    args: argparse.Namespace,
    results: list[OperationResult],
    raw_output: str,
    *,
    log_path: Path | str | None = None,
    tasker_progress: TaskerProgressSink | None = None,
) -> tuple[list[OperationResult], str]:
    commit_message = str(getattr(args, "commit", "") or "").strip()
    if args.command not in APPLY_COMMANDS or not commit_message:
        return results, raw_output
    # Failed applies or failed validation are never committed.
    if not results or any(result.returncode != 0 for result in results):
        return results, raw_output

    commit_paths = _artifact_commit_paths(args, config.repo_root)
    manifest = dict(getattr(args, "_devtool_artifact_manifest", {}) or {})
    artifact_value = getattr(args, "artifact", "")
    artifact_path: Path | None = None
    if isinstance(artifact_value, (str, Path)) and str(artifact_value):
        try:
            artifact_path = resolve_artifact_path(artifact_value)
        except Exception:
            artifact_path = None
    render_hook_progress, stream_hook_output = _artifact_hook_presentation(args)
    if artifact_path is not None and manifest:
        finalize_display = "Artifact finalize: before commit"
        if tasker_progress is not None and "before_commit" in set(artifact_finalize_phases(manifest)):
            tasker_progress.phase_started(
                "artifact-finalize-before_commit", index=1, total=1,
                argv=["artifact-finalize", "before_commit", str(artifact_path)],
                metadata={"validation_phase_name": finalize_display, "hook_kind": "finalize", "hook_phase": "before_commit"},
            )
        try:
            finalize_result = run_artifact_finalize_script(
                config.repo_root,
                artifact_path,
                phase="before_commit",
                manifest=manifest,
                target=str(getattr(args, "target", "") or artifact_target(manifest)),
                solution=str(getattr(args, "solution", "") or artifact_solution(manifest)),
                run_id=str(getattr(args, "_devtool_run_id", "") or ""),
                dry_run=bool(getattr(args, "dry_run", False)),
                log_path=log_path,
                ignore_checksum=bool(getattr(args, "ignore_checksum", False)),
                render_progress=render_hook_progress,
                stream_output=stream_hook_output,
                validation_requested=bool(getattr(args, "validate", False) or getattr(args, "validate_only", False)),
                validation_status="passed",
                commit_requested=True,
                commit_message=commit_message,
                commit_paths=commit_paths,
            )
        except Exception as exc:
            finalize_result = OperationResult(
                "artifact-finalize-script",
                ["artifact-finalize-script", str(artifact_path)],
                1,
                0.0,
                f"{type(exc).__name__}: {exc}",
                [Issue("error", "artifact-finalize-script", str(artifact_path), type(exc).__name__, str(exc))],
                failure_kind="finalize_failure",
                metadata={"finalize_phase": "before_commit", "hook_phase": "before_commit", "hook_kind": "finalize", "validation_phase_name": finalize_display},
            )
        if finalize_result is not None:
            finalize_result.metadata.setdefault("validation_phase_name", finalize_display)
            finalize_result.metadata.setdefault("hook_kind", "finalize")
            finalize_result.metadata.setdefault("hook_phase", "before_commit")
            if tasker_progress is not None:
                tasker_progress.phase_finished(finalize_result)
            results.append(finalize_result)
            finalize_raw = "$ " + " ".join(finalize_result.argv) + "\n" + finalize_result.output
            raw_output = raw_output + ("\n\n" if raw_output else "") + finalize_raw
            if finalize_result.returncode != 0:
                return results, raw_output

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

    commit_succeeded = any(result.name == "git-commit" and result.returncode == 0 for result in commit_results)
    if commit_succeeded and artifact_path is not None and manifest:
        finalize_display = "Artifact finalize: after commit"
        if tasker_progress is not None and "after_commit" in set(artifact_finalize_phases(manifest)):
            tasker_progress.phase_started(
                "artifact-finalize-after_commit", index=1, total=1,
                argv=["artifact-finalize", "after_commit", str(artifact_path)],
                metadata={"validation_phase_name": finalize_display, "hook_kind": "finalize", "hook_phase": "after_commit"},
            )
        try:
            after_commit = run_artifact_finalize_script(
                config.repo_root,
                artifact_path,
                phase="after_commit",
                manifest=manifest,
                target=str(getattr(args, "target", "") or artifact_target(manifest)),
                solution=str(getattr(args, "solution", "") or artifact_solution(manifest)),
                run_id=str(getattr(args, "_devtool_run_id", "") or ""),
                dry_run=bool(getattr(args, "dry_run", False)),
                log_path=log_path,
                ignore_checksum=bool(getattr(args, "ignore_checksum", False)),
                render_progress=render_hook_progress,
                stream_output=stream_hook_output,
                validation_requested=bool(getattr(args, "validate", False) or getattr(args, "validate_only", False)),
                validation_status="passed",
                commit_requested=True,
                commit_message=commit_message,
                commit_paths=commit_paths,
            )
        except Exception as exc:
            after_commit = OperationResult(
                "artifact-finalize-script",
                ["artifact-finalize-script", str(artifact_path)],
                1,
                0.0,
                f"{type(exc).__name__}: {exc}",
                [Issue("error", "artifact-finalize-script", str(artifact_path), type(exc).__name__, str(exc))],
                failure_kind="finalize_failure",
                metadata={"finalize_phase": "after_commit", "hook_phase": "after_commit", "hook_kind": "finalize", "validation_phase_name": finalize_display},
            )
        if after_commit is not None:
            after_commit.metadata.setdefault("validation_phase_name", finalize_display)
            after_commit.metadata.setdefault("hook_kind", "finalize")
            after_commit.metadata.setdefault("hook_phase", "after_commit")
            if tasker_progress is not None:
                tasker_progress.phase_finished(after_commit)
            event = dict((after_commit.metadata or {}).get("hook_event") or {})
            restoration_errors = [str(item) for item in event.get("write_restoration_errors") or []]
            if after_commit.returncode != 0 and not restoration_errors:
                # after_commit is observational. Its failure is durable evidence/warning,
                # not a reason to pretend the already-created Git commit did not happen.
                original_rc = after_commit.returncode
                after_commit.metadata["observational_failure"] = True
                after_commit.metadata["observational_returncode"] = original_rc
                after_commit.returncode = 0
                after_commit.failure_kind = ""
                after_commit.issues = [
                    Issue(
                        "warning" if issue.severity == "error" else issue.severity,
                        issue.command, issue.file, issue.code, issue.message, issue.line,
                    )
                    for issue in after_commit.issues
                ]
                after_commit.output += "\nPost-commit hook failure recorded as observational; Git commit was preserved."
            results.append(after_commit)
            after_raw = "$ " + " ".join(after_commit.argv) + "\n" + after_commit.output
            raw_output = raw_output + ("\n\n" if raw_output else "") + after_raw
    return results, raw_output


def _record_transaction_commit_from_results(
    transaction_path: str | Path | None,
    repo_root: Path,
    results: list[OperationResult],
    *,
    start_index: int = 0,
) -> None:
    if not transaction_path:
        return
    committed = any(
        result.name == "git-commit" and result.returncode == 0 and not result.skipped
        for result in results[start_index:]
    )
    if committed:
        record_transaction_git_commit(transaction_path, latest_commit_hash(repo_root))


def _resolved_artifact_paths_for_args(args: argparse.Namespace) -> list[Path]:
    if str(getattr(args, "command", "") or "") not in APPLY_COMMANDS:
        return []
    resolved: list[Path] = []
    for artifact in _artifact_arg_list(getattr(args, "artifact", "")):
        try:
            resolved.append(resolve_artifact_path(artifact))
        except Exception:
            resolved.append(Path(artifact).expanduser())
    return resolved


def _log_identifier_for_args(args: argparse.Namespace) -> str:
    artifacts = _resolved_artifact_paths_for_args(args)
    if len(artifacts) == 1:
        return artifacts[0].name
    if artifacts:
        return artifacts[0].stem + "-bundle"
    return ""


def _run_log_start_text(args: argparse.Namespace, config: ProjectConfig | None) -> str:
    artifacts = _resolved_artifact_paths_for_args(args)
    repo_root = config.repo_root if config is not None else Path(getattr(args, "repo", "") or Path.cwd()).resolve()
    lines = [
        "--- devtool run start ---",
        f"Devtool run id: {_ensure_run_id(args)}",
        f"Devtool command: {str(getattr(args, 'command', '') or 'devtool')}",
        f"Devtool repository: {repo_root}",
    ]
    target = str((config.selected_target if config is not None else "") or getattr(args, "target", "") or "").strip()
    if target:
        lines.append(f"Devtool target: {target}")
    for artifact in artifacts:
        lines.append(f"Devtool artifact: {artifact}")
    lines.append("Devtool status: running")
    return "\n".join(lines)


def _append_run_log_summary(
    log_path: Path | str,
    args: argparse.Namespace,
    config: ProjectConfig | None,
    totals: dict[str, int],
    summary_text: str,
    *,
    started_at: datetime,
    finished_at: datetime,
    exit_code: int | None = None,
    results: list[OperationResult] | None = None,
) -> None:
    failed = int(totals.get("failed", 0) or 0)
    errors = int(totals.get("errors", 0) or 0)
    code = int(exit_code if exit_code is not None else (1 if failed or errors else 0))
    status = "failed" if code != 0 or failed or errors else "ok"
    duration = max(0.0, (finished_at - started_at).total_seconds())

    def _first_matching_line(prefix: str) -> str:
        for line in summary_text.splitlines():
            cleaned = line.strip()
            if cleaned.lower().startswith(prefix.lower()):
                return cleaned.split(":", 1)[1].strip() if ":" in cleaned else cleaned
        return ""

    def _failure_kind() -> str:
        for result in results or []:
            if result.returncode != 0 and result.failure_kind:
                return str(result.failure_kind)
        return ""

    def _rollback_state() -> str:
        for result in results or []:
            name = str(result.name or "").lower()
            if name in {"rollback", "apply-rollback"}:
                return "succeeded" if result.returncode == 0 else "failed"
        recovery = _first_matching_line("Recovery:")
        if "rollback succeeded" in recovery.lower():
            return "succeeded"
        if "rollback failed" in recovery.lower():
            return "failed"
        return ""

    lines = [
        "--- devtool run final ---",
        f"Devtool run id: {_ensure_run_id(args)}",
        f"Devtool status: {status}",
        f"Devtool exit code: {code}",
        f"Devtool command: {str(getattr(args, 'command', '') or 'devtool')}",
        f"Devtool repository: {config.repo_root if config is not None else Path.cwd()}",
        f"Devtool warnings: {int(totals.get('warnings', 0) or 0)}",
        f"Devtool errors: {int(totals.get('errors', 0) or 0)}",
        f"Devtool failed actions: {failed}",
        f"Devtool skipped actions: {int(totals.get('skipped', 0) or 0)}",
        f"Devtool started at: {started_at.isoformat()}",
        f"Devtool finished at: {finished_at.isoformat()}",
        f"Devtool duration: {duration:.3f}s",
    ]
    test_totals = _first_matching_line("Tests:")
    if test_totals:
        lines.append(f"Devtool tests: {test_totals}")
    go_packages = _first_matching_line("Go packages:")
    if go_packages:
        lines.append(f"Devtool go packages: {go_packages}")
    commit_hash = _first_matching_line("Git commit:")
    if commit_hash:
        lines.append(f"Devtool commit: {commit_hash}")
    failure_kind = _failure_kind()
    if failure_kind:
        lines.append(f"Devtool failure kind: {failure_kind}")
    rollback_state = _rollback_state()
    if rollback_state:
        lines.append(f"Devtool rollback: {rollback_state}")
    artifacts = _resolved_artifact_paths_for_args(args)
    for artifact in artifacts:
        lines.append(f"Devtool artifact: {artifact}")
    if summary_text.strip():
        lines.extend(("", summary_text.strip()))
    write_log("\n".join(lines), log_path, append=True)


def _ensure_run_id(args: argparse.Namespace) -> str:
    run_id = str(getattr(args, "_devtool_run_id", "") or os.environ.get("DEVTOOL_RUN_ID", "")).strip()
    if not run_id:
        run_id = __import__("uuid").uuid4().hex
    args._devtool_run_id = run_id
    os.environ["DEVTOOL_RUN_ID"] = run_id
    return run_id


def _reservation_from_transaction(path: str | Path, *, fallback_target: str = "") -> tuple[str, tuple[str, ...], bool, str]:
    tx = read_transaction(path)
    target = str(tx.get("target") or fallback_target or "").strip()
    raw_paths = tx.get("reserved_paths")
    if not isinstance(raw_paths, list) and isinstance(tx.get("affected_paths"), list):
        raw_paths = [
            str(item.get("path") or "")
            for item in tx.get("affected_paths") or []
            if isinstance(item, dict) and str(item.get("path") or "")
        ]
    known = int(tx.get("schema_version") or 0) >= 3 and isinstance(raw_paths, list)
    return target, tuple(str(item) for item in raw_paths or []) if known else (), known, str(tx.get("transaction_id") or "")


def _single_run_reservation(config: ProjectConfig, args: argparse.Namespace) -> tuple[str, tuple[str, ...], bool, str]:
    target = str(config.selected_target or getattr(args, "target", "") or "").strip()
    command = str(getattr(args, "command", "") or "")

    # Explicit OV18 escape hatch: preserve one pending validation transaction
    # without treating it as a stale apply. This remains opt-in and does not
    # bypass any separate live run reservation.
    # Interrupted non-validation applies are recovered under an exclusive
    # compatibility reservation before the requested command proceeds. This
    # preserves the historical auto-recovery behavior without racing a live
    # scoped operation.
    stale_nonpending = []
    for candidate in find_active_transactions(config.repo_root, include_live=False):
        if not transaction_pending_validation(candidate):
            try:
                stale_nonpending.append(read_transaction(candidate))
            except Exception:
                pass
    if len(stale_nonpending) == 1:
        return "", (), False, str(stale_nonpending[0].get("transaction_id") or "")

    forced_pending_id = ""
    if bool(getattr(args, "allow_pending_validation", False)):
        pending = []
        for candidate in find_active_transactions(config.repo_root, include_live=False):
            if transaction_pending_validation(candidate):
                try:
                    pending.append(read_transaction(candidate))
                except Exception:
                    pass
        if len(pending) == 1:
            forced_pending_id = str(pending[0].get("transaction_id") or "")

    # A resumed validation owns the transaction it is validating. Its source
    # reservation remains authoritative across the process boundary.
    if command in {
        "validate", "v", "validation-finalize", "vf", "validation-status", "vs",
        "validation-plan", "vplan", "validation-events", "vevents", "validation-report", "vreport",
        "validation-pause", "vpause", "validation-stop", "vstop", "validation-abort", "va",
        "validation-recover", "vrec", "validation-reset", "vreset", "validation-accept-plan-change", "vapc",
    }:
        session = load_active_session(config.repo_root)
        if session is not None and str(session.transaction_path or "").strip():
            try:
                return _reservation_from_transaction(session.transaction_path, fallback_target=target)
            except Exception:
                # Fail closed below when a legacy/corrupt session cannot prove
                # its write set.
                return target, (), False, forced_pending_id

    # Artifact applies can prove their source write set before taking the lock.
    if command in APPLY_COMMANDS:
        artifacts = _artifact_arg_list(getattr(args, "artifact", ""))
        if len(artifacts) == 1:
            try:
                artifact_path = resolve_artifact_path(artifacts[0])
                snapshot = command in {"apply-snapshot", "as"}
                if not snapshot and (command in {"apply-overlays", "aos"} or is_overlay_bundle(artifact_path)):
                    return target, (), False, forced_pending_id
                manifest = _read_artifact_manifest(str(artifact_path))
                target = artifact_target(manifest) or target
                preview = preview_artifact(config.repo_root, artifact_path, snapshot=snapshot)
                args._devtool_artifact_preview = preview
                paths = affected_paths_from_preview(preview)
                if not snapshot:
                    seen = set(paths)
                    for raw in read_delete_manifest(artifact_path):
                        value = str(raw).strip()
                        if value and value not in seen:
                            seen.add(value)
                            paths.append(value)
                return target, tuple(paths), True, ""
            except Exception:
                return target, (), False, forced_pending_id

    # MP09: ordinary executable workspace workflows can participate in the
    # same scoped cross-process reservation protocol as artifact transactions.
    # We only relax repository exclusivity when every workflow node proves its
    # write set through explicit outputs/read-only metadata. Any ambiguity
    # intentionally falls back to the historical exclusive lock.
    if command in {"restore", "r", "build", "b", "test", "t", "lint", "l", "package", "p", "validate", "v", "validate-all", "wine-validate", "wv"} and config.targets:
        try:
            groups = tuple(str(item).strip() for item in getattr(args, "group", []) if str(item).strip())
            if groups or bool(getattr(args, "all_targets", False)):
                candidates = resolve_workspace_targets(
                    config.workspace,
                    config.targets,
                    groups=groups,
                    all_targets=bool(getattr(args, "all_targets", False)),
                )
            elif config.selected_target:
                candidates = (config.selected_target,)
            else:
                candidates = tuple(config.targets)
            if bool(getattr(args, "affected", False)):
                selection = analyze_affected_targets(
                    config,
                    candidate_targets=candidates,
                    base=str(getattr(args, "affected_base", "") or ""),
                    head=str(getattr(args, "affected_head", "") or ""),
                )
                candidates = selection.targets
            if candidates:
                action = "validate" if command in {"validate", "v", "validate-all"} else ("wine-validate" if command in {"wine-validate", "wv"} else {"r": "restore", "b": "build", "t": "test", "l": "lint", "p": "package"}.get(command, command))
                specs = (
                    build_workspace_validation_specs(config, candidates, wine=action == "wine-validate")
                    if action in {"validate", "wine-validate"}
                    else build_workspace_specs(config, candidates, action)
                )
                workflow_plan = build_execution_plan_from_specs(config, specs, parallel_requested=False)
                if workflow_plan is not None and workflow_plan.reservation_paths_known:
                    identity = candidates[0] if len(candidates) == 1 else "workspace:" + ",".join(candidates)
                    return identity, workflow_plan.reservation_paths, True, forced_pending_id
        except Exception:
            # Reservation derivation is a safety optimization. Never turn a
            # planning/config edge case into permissive locking; fail closed.
            return target, (), False, forced_pending_id

    return target, (), False, forced_pending_id


def _rollback_single_run_reservation(repo_root: Path, args: argparse.Namespace) -> tuple[str, tuple[str, ...], bool, str]:
    if str(getattr(args, "command", "") or "") != "rollback":
        return "", (), False, ""
    selector = str(getattr(args, "transaction", "") or "").strip()
    failed = bool(getattr(args, "failed", False))
    if not selector and not failed:
        return "", (), False, ""
    try:
        tx_path = resolve_active_transaction(repo_root, selector, failed_only=failed)
        return _reservation_from_transaction(tx_path)
    except Exception:
        return "", (), False, ""


def _acquire_repo_single_run_lock(
    repo_root: Path,
    args: argparse.Namespace,
    *,
    log_path: Path | str | None = None,
    target: str = "",
    reserved_paths: tuple[str, ...] | list[str] = (),
    paths_known: bool = False,
    owned_transaction_id: str = "",
) -> SingleRunLock:
    if not target and not paths_known and not owned_transaction_id:
        target, reserved_paths, paths_known, owned_transaction_id = _rollback_single_run_reservation(repo_root, args)
    lock = SingleRunLock(
        repo_root,
        command=str(getattr(args, "command", "devtool") or "devtool"),
        run_id=_ensure_run_id(args),
        log_path=str(log_path or ""),
        target=target,
        reserved_paths=reserved_paths,
        paths_known=paths_known,
        owned_transaction_id=owned_transaction_id,
    )
    try:
        lock.acquire()
    except BlockingIOError as exc:
        raise SystemExit(str(exc)) from exc
    args._devtool_single_run_lock = lock
    args._devtool_owned_transaction_id = owned_transaction_id
    if target:
        args._devtool_active_target = target
        os.environ["DEVTOOL_ACTIVE_TARGET"] = target
    return lock


def _acquire_single_run_lock(
    config: ProjectConfig,
    args: argparse.Namespace,
    *,
    log_path: Path | str | None = None,
) -> SingleRunLock:
    target, paths, paths_known, transaction_id = _single_run_reservation(config, args)
    return _acquire_repo_single_run_lock(
        config.repo_root,
        args,
        log_path=log_path,
        target=target,
        reserved_paths=paths,
        paths_known=paths_known,
        owned_transaction_id=transaction_id,
    )


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
    target = str(getattr(args, "_devtool_active_target", "") or "")
    if target and os.environ.get("DEVTOOL_ACTIVE_TARGET") == target:
        os.environ.pop("DEVTOOL_ACTIVE_TARGET", None)
    run_id = str(getattr(args, "_devtool_run_id", "") or "")
    if run_id and os.environ.get("DEVTOOL_RUN_ID") == run_id:
        os.environ.pop("DEVTOOL_RUN_ID", None)

def _targets(config: ProjectConfig, *, as_json: bool = False) -> None:
    memberships = workspace_group_memberships(config.workspace, config.targets)
    if as_json:
        payload = {
            "schema_version": 2,
            "workspace": workspace_as_dict(config.workspace, config.targets),
            "targets": [
                {
                    "name": name,
                    "platform": target.platform,
                    "runner": target.effective_runner,
                    "groups": list(memberships.get(name, ())),
                    "default": name == config.workspace.default_target,
                    "root": target.root or target.solution or target.build_target or target.restore_target or target.run_project,
                    "inputs": list(target.inputs),
                    "outputs": list(target.outputs),
                    "workflows": {
                        workflow_name: [step.raw for step in workflow.steps]
                        for workflow_name, workflow in sorted(target.workflows.items())
                    },
                }
                for name, target in sorted(config.targets.items())
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    rows: list[tuple[str, str]] = []
    if config.targets:
        for name, target in sorted(config.targets.items()):
            location = target.root or target.solution or target.build_target or target.restore_target or target.run_project or "-"
            workflows = ",".join(sorted(target.workflows)) or "-"
            groups = ",".join(memberships.get(name, ())) or "-"
            default = " default" if name == config.workspace.default_target else ""
            rows.append((
                name,
                f"platform={target.platform or '-'} runner={target.effective_runner or '-'} "
                f"groups={groups} workflows={workflows} inputs={len(target.inputs)} outputs={len(target.outputs)} root={location}{default}",
            ))
    elif config.kind == "dotnet":
        rows = [(candidate.stem, f"platform={config.platform or 'dotnet'} runner=dotnet root={candidate.relative_to(config.repo_root)}") for candidate in discover_dotnet_candidates(config.repo_root)]
    if not rows:
        console.print("[yellow]No targets found.[/yellow]")
        return
    render_status_table(rows)


def _groups(config: ProjectConfig, *, as_json: bool = False) -> None:
    payload = workspace_as_dict(config.workspace, config.targets)
    if as_json:
        print(json.dumps({"schema_version": 1, "workspace": payload}, indent=2, ensure_ascii=False))
        return
    groups = payload.get("groups", {}) if isinstance(payload, dict) else {}
    if not groups:
        console.print("[yellow]No workspace groups configured.[/yellow]")
        return
    rows: list[tuple[str, str]] = []
    for name, value in sorted(groups.items()):
        if not isinstance(value, dict):
            continue
        members = ", ".join(str(item) for item in value.get("members", [])) or "-"
        targets = ", ".join(str(item) for item in value.get("targets", [])) or "-"
        rows.append((name, f"members=[{members}] targets=[{targets}]"))
    render_status_table(rows)

def _jobs(config: ProjectConfig, *, as_json: bool = False) -> None:
    from .workflow_config import job_as_dict
    if as_json:
        print(json.dumps({
            "schema_version": 1,
            "jobs": {name: job_as_dict(job) for name, job in sorted(config.jobs.items())},
        }, indent=2, ensure_ascii=False))
        return
    if not config.jobs:
        console.print("[yellow]No reusable jobs configured.[/yellow]")
        return
    rows: list[tuple[str, str]] = []
    for name, job in sorted(config.jobs.items()):
        payload = job.action or " ".join(job.command) or job.shell_command
        rows.append((name, f"runner={job.runner} mode={job.command_mode} root={job.root or '-'} {payload}".strip()))
    render_status_table(rows)


def _workflows(config: ProjectConfig, *, as_json: bool = False) -> None:
    from .workflow_config import workflow_as_dict

    payload: dict[str, dict[str, dict[str, object]]] = {}
    for target_name, target in sorted(config.targets.items()):
        if not target.workflows:
            continue
        target_payload: dict[str, dict[str, object]] = {}
        for workflow_name, workflow in sorted(target.workflows.items()):
            declared = workflow_as_dict(target_name, workflow)
            expanded_graph = plan_workspace_targets(config, [target_name], workflow_name)
            expanded = expanded_graph.as_dict()
            declared["expanded_graph"] = expanded
            try:
                if workflow_name in {"validate", "wine-validate"}:
                    workflow_specs = build_workspace_validation_specs(config, [target_name], wine=workflow_name == "wine-validate")
                else:
                    workflow_specs = build_workspace_specs(config, [target_name], workflow_name)
                workflow_schedule = build_execution_plan_from_specs(config, workflow_specs)
                declared["schedule"] = workflow_schedule.as_dict() if workflow_schedule is not None else None
                declared["schedule_error"] = ""
            except (Exception, SystemExit) as exc:
                # `workflows` remains a declaration/graph inspector even when a
                # target's runner cannot yet produce executable specs (for
                # example an unbootstrapped Node target). MP10 scheduling detail
                # is additive and must not make MP02 inspection fail.
                declared["schedule"] = None
                declared["schedule_error"] = str(exc)
            target_payload[workflow_name] = declared
        payload[target_name] = target_payload

    if as_json:
        print(json.dumps({"schema_version": 3, "workflows": payload}, indent=2, ensure_ascii=False))
        return
    if not payload:
        console.print("[yellow]No target workflows configured.[/yellow]")
        return
    rows: list[tuple[str, str]] = []
    for target_name, workflows in payload.items():
        for workflow_name, description in workflows.items():
            graph = description.get("expanded_graph", {}) if isinstance(description, dict) else {}
            node_rows = graph.get("nodes", []) if isinstance(graph, dict) else []
            pieces: list[str] = []
            for node in node_rows if isinstance(node_rows, list) else []:
                if not isinstance(node, dict):
                    continue
                label = str(node.get("id") or node.get("ref") or node.get("key") or "step")
                node_target = str(node.get("target") or "")
                if node_target and node_target != target_name:
                    label = f"{node_target}:{label}"
                dependencies = [str(value) for value in node.get("depends_on", []) or []]
                short_dependencies = [value.rsplit(".", 1)[-1] for value in dependencies]
                pieces.append(label + (f" <- {','.join(short_dependencies)}" if short_dependencies else ""))
            schedule = description.get("schedule") if isinstance(description, dict) else None
            suffix = ""
            if isinstance(schedule, dict):
                waves = schedule.get("waves", []) if isinstance(schedule.get("waves"), list) else []
                parallel = "parallel" if schedule.get("parallel_effective") else ("parallel-requested/serialized" if schedule.get("parallel_requested") else "sequential")
                suffix = f" | schedule={parallel} waves={len(waves)}"
            rows.append((f"{target_name}.{workflow_name}", " | ".join(pieces) + suffix))
    render_status_table(rows)



def _ui_contract(*, surface: str = "machine", as_json: bool = False, width: int = 0, height: int = 0, required_capabilities: list[str] | None = None) -> None:
    from .ui_core import (
        CAPABILITY_SCHEMA_VERSION, CONFIRMATION_SCHEMA_VERSION, COPY_SCHEMA_VERSION, DIAGNOSTIC_SCHEMA_VERSION, ICON_SCHEMA_VERSION, NOTIFICATION_SCHEMA_VERSION, PROGRESS_SCHEMA_VERSION, RESPONSIVE_SCHEMA_VERSION, TOKEN_SCHEMA_VERSION, UI_SCHEMA_VERSION,
        IPC_PROTOCOL_VERSION, MACHINE_SCHEMA_VERSION, TASKER_SCHEMA_VERSION, action_catalog, capability_catalog, capabilities_for_surface, confirmation_catalog, copy_catalog, icon_catalog, ipc_contract, navigation_for_surface, negotiate_capabilities, notification_catalog, profile_catalog,
        resolve_responsive_layout, responsive_policy_catalog, resolve_ui_preferences, token_catalog,
    )

    global_config, _ = load_global_config()
    ui_config = global_config.get("ui", {}) if isinstance(global_config.get("ui", {}), dict) else {}
    preferences = resolve_ui_preferences(ui_config, surface=surface)
    navigation = [{
        "id": item.id,
        "label": item.label,
        "short_label": item.short_label,
        "icon": item.icon,
        "route": item.route,
        "section": item.section,
        "order": item.order,
    } for item in navigation_for_surface(surface)]
    capabilities = capabilities_for_surface(surface)
    negotiation = negotiate_capabilities(surface, required_capabilities or [])
    responsive = resolve_responsive_layout(surface, width=width or None, height=height or None, preferences=preferences, capabilities=capabilities)
    payload = {
        "schema_version": UI_SCHEMA_VERSION,
        "capability_schema_version": CAPABILITY_SCHEMA_VERSION,
        "responsive_schema_version": RESPONSIVE_SCHEMA_VERSION,
        "confirmation_schema_version": CONFIRMATION_SCHEMA_VERSION,
        "notification_schema_version": NOTIFICATION_SCHEMA_VERSION,
        "progress_schema_version": PROGRESS_SCHEMA_VERSION,
        "diagnostic_schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "token_schema_version": TOKEN_SCHEMA_VERSION,
        "icon_schema_version": ICON_SCHEMA_VERSION,
        "copy_schema_version": COPY_SCHEMA_VERSION,
        "machine_schema_version": MACHINE_SCHEMA_VERSION,
        "ipc_protocol_version": IPC_PROTOCOL_VERSION,
        "tasker_schema_version": TASKER_SCHEMA_VERSION,
        "ipc": ipc_contract(),
        "machine_model": {
            "sections": ["workspace", "selection", "operation", "workflow", "test_selection", "progress", "diagnostics", "actions", "navigation", "notifications", "presentation", "capabilities", "responsive"],
            "compatibility": "legacy machine/HUD/Tasker fields remain additive aliases",
        },
        "surface": surface,
        "preferences": preferences.as_dict(),
        "profiles": profile_catalog(ui_config),
        "capabilities": capabilities.as_dict(),
        "capability_negotiation": negotiation.as_dict(),
        "responsive": responsive.as_dict(),
        "capability_catalog": capability_catalog(),
        "responsive_policy": responsive_policy_catalog(),
        "confirmation_policy": confirmation_catalog(preferences.confirmation_policy),
        "notification_policy": notification_catalog(preferences.notification_policy),
        "progress_model": {"hierarchy": ["operation", "workflow", "target", "phase", "step"], "compatibility": "flat progress remains available with parent_id links"},
        "diagnostic_model": {"sections": ["what_happened", "why", "where", "next_actions", "technical_details"], "compatibility": "legacy severity/message fields remain available"},
        "tokens": token_catalog(),
        "icons": icon_catalog(),
        "copy": copy_catalog(),
        "actions": action_catalog(),
        "navigation": navigation,
    }
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    console.print(f"[bold]UI Core[/bold]  schema={UI_SCHEMA_VERSION} surface={surface} profile={preferences.profile}")
    render_status_table([
        ("theme", preferences.theme),
        ("density", preferences.density),
        ("icons", preferences.icons),
        ("motion", preferences.motion),
        ("verbosity", preferences.verbosity),
        ("layout", f"{responsive.mode} · panes={responsive.panes} · nav={responsive.navigation}"),
        ("capabilities", ", ".join(capabilities.enabled()) or "semantic-only"),
        ("fallbacks", ", ".join(f"{key}→{value}" for key, value in negotiation.fallbacks.items()) or "none"),
        ("confirmations", ", ".join(f"{k}={v}" for k, v in preferences.confirmation_policy.items())),
        ("notifications", ", ".join(k for k, v in preferences.notification_policy.items() if v) or "none"),
    ])
    console.print("[bold]Actions[/bold]")
    render_status_table([(item["id"], f"{item['label']} · intent={item['intent']}") for item in payload["actions"]])
    if navigation:
        console.print("[bold]Navigation[/bold]")
        render_status_table([(item["route"], f"{item['label']} · id={item['id']}") for item in navigation])


def _candidate_targets_for_analysis(config: ProjectConfig, args: argparse.Namespace) -> tuple[str, ...]:
    raw_groups = getattr(args, "group", [])
    if isinstance(raw_groups, str):
        group_values = [raw_groups]
    elif isinstance(raw_groups, (list, tuple, set)):
        group_values = list(raw_groups)
    else:
        # Programmatic callers and compatibility tests often pass a loose Mock/Namespace.
        # Unknown attributes must behave like argparse defaults rather than truthy iterables.
        group_values = []
    groups = [str(item).strip() for item in group_values if str(item).strip()]
    all_targets = getattr(args, "all_targets", False) is True
    if groups or all_targets:
        try:
            selected = resolve_workspace_targets(config.workspace, config.targets, groups=groups, all_targets=all_targets)
        except WorkspaceConfigError as exc:
            raise SystemExit(str(exc)) from exc
        if not selected:
            raise SystemExit("Workspace target selection resolved to no targets")
        return selected
    if config.selected_target and config.selected_target in config.targets:
        return (config.selected_target,)
    return tuple(config.targets)


def _requested_environment_for_analysis(args: argparse.Namespace) -> str:
    if bool(getattr(args, "host", False)):
        return detect_current_environment()
    if str(getattr(args, "chroot", "") or "").strip():
        return EXECUTION_CHROOT
    if str(getattr(args, "ssh", "") or "").strip():
        return EXECUTION_SSH
    if str(getattr(args, "proot_distro", "") or "").strip():
        return EXECUTION_PROOT_DISTRO
    return str(getattr(args, "execution_environment", EXECUTION_AUTO) or EXECUTION_AUTO)


def _affected(config: ProjectConfig, args: argparse.Namespace) -> int:
    candidates = _candidate_targets_for_analysis(config, args)
    base = str(getattr(args, "base", "") or getattr(args, "affected_base", "") or "")
    head = str(getattr(args, "head", "") or getattr(args, "affected_head", "") or "")
    try:
        selection = analyze_affected_targets(config, candidate_targets=candidates, base=base, head=head)
    except AffectedSelectionError as exc:
        raise SystemExit(str(exc)) from exc
    if bool(getattr(args, "json", False)):
        print(json.dumps(selection.as_dict(), indent=2, ensure_ascii=False))
        return 0
    rows: list[tuple[str, str]] = [
        ("source", selection.source),
        ("changed", str(len(selection.changed_paths))),
        ("affected", ", ".join(selection.targets) or "none"),
    ]
    if selection.uncertain_paths:
        rows.append(("uncertain", ", ".join(selection.uncertain_paths)))
    render_status_table(rows)
    if bool(getattr(args, "explain", False)):
        explanation: list[tuple[str, str]] = []
        for target in selection.targets:
            reasons = selection.reasons.get(target, ())
            detail = "; ".join(
                f"{item.kind}: {item.path or item.source} ({item.detail})" for item in reasons
            ) or "selected"
            explanation.append((target, detail))
        if explanation:
            render_status_table(explanation)
    return 0


def _toolchain_report_for_args(config: ProjectConfig, args: argparse.Namespace):
    targets = _candidate_targets_for_analysis(config, args)
    if getattr(args, "affected", False) is True:
        try:
            selection = analyze_affected_targets(
                config,
                candidate_targets=targets,
                base=str(getattr(args, "affected_base", "") or ""),
                head=str(getattr(args, "affected_head", "") or ""),
            )
        except AffectedSelectionError as exc:
            raise SystemExit(str(exc)) from exc
        targets = selection.targets
        setattr(args, "_devtool_affected_selection", selection)
    if not targets:
        return None
    return analyze_toolchain_requirements(
        config,
        targets,
        workflow=str(getattr(args, "workflow", "all") or "all"),
        requested_environment=_requested_environment_for_analysis(args),
    )


def _preflight(config: ProjectConfig, args: argparse.Namespace) -> int:
    report = _toolchain_report_for_args(config, args)
    if report is None:
        payload = {"schema_version": 1, "healthy": True, "targets": [], "message": "No affected targets."}
        if bool(getattr(args, "json", False)):
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            render_status_table([("preflight", "No affected targets; nothing to check")])
        return 0
    if bool(getattr(args, "json", False)):
        print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
    else:
        rows: list[tuple[str, str]] = [
            ("targets", ", ".join(report.targets)),
            ("runners", ", ".join(report.runners) or "-"),
            ("environments", ", ".join(report.environments) or "-"),
        ]
        for tool in report.tools:
            rows.append((f"tool.{tool.executable}", f"{tool.status}: {tool.detail}"))
        for check in report.checks:
            if check.status not in {"ready", "ok"}:
                rows.append((f"{check.section}.{check.name}", f"{check.status}: {check.detail}"))
        render_status_table(rows)
    return 0 if report.healthy else 1


def _workflow_plan_dot(graph, schedule: WorkflowExecutionPlan | None) -> str:
    lines = ["digraph devtool_workflow {", '  rankdir="LR";', '  node [shape="box"];']
    schedule_nodes = schedule.by_key if schedule is not None else {}
    for node in graph.nodes:
        scheduled = schedule_nodes.get(node.key)
        runner = scheduled.runner if scheduled is not None else (node.runner or node.ref.kind)
        label = f"{node.target}\\n{node.label}\\n{runner}"
        lines.append(f'  "{node.key}" [label="{label}"];')
    for node in graph.nodes:
        for dependency in node.depends_on:
            lines.append(f'  "{dependency}" -> "{node.key}";')
    lines.append("}")
    return "\n".join(lines)


def _plan(config: ProjectConfig, args: argparse.Namespace) -> int:
    action = str(getattr(args, "action", "validate") or "validate")
    candidates = _candidate_targets_for_analysis(config, args)
    affected_payload = None
    if bool(getattr(args, "affected", False)):
        try:
            selection = analyze_affected_targets(
                config,
                candidate_targets=candidates,
                base=str(getattr(args, "affected_base", "") or ""),
                head=str(getattr(args, "affected_head", "") or ""),
            )
        except AffectedSelectionError as exc:
            raise SystemExit(str(exc)) from exc
        candidates = selection.targets
        affected_payload = selection.as_dict()
    if config.targets and not candidates:
        payload = {
            "schema_version": 1,
            "action": action,
            "targets": [],
            "affected": affected_payload,
            "message": "No targets selected; execution would be a successful no-op.",
        }
        if bool(getattr(args, "json", False)):
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            render_status_table([("plan", payload["message"])])
        return 0

    if config.targets:
        if not candidates:
            raise SystemExit("No target selected. Use --target, --group, --all-targets, or configure [workspace].default_target.")
        graph = plan_workspace_targets(config, candidates, action)
        if action in {"validate", "wine-validate"}:
            specs = build_workspace_validation_specs(config, candidates, wine=action == "wine-validate")
        else:
            specs = build_workspace_specs(config, candidates, action)
        requested = getattr(args, "workflow_parallel", None)
        plan = build_execution_plan_from_specs(
            config,
            specs,
            parallel_requested=requested,
            max_parallel_nodes=int(getattr(args, "workflow_max_workers", 0) or 0),
        )
        payload = {
            "schema_version": 1,
            "action": action,
            "targets": list(candidates),
            "affected": affected_payload,
            "graph": graph.as_dict(),
            "schedule": plan.as_dict() if plan is not None else None,
            "commands": [
                {
                    "index": index,
                    "name": spec.name,
                    "argv": list(spec.argv),
                    "cwd": str(spec.cwd or config.repo_root),
                    "node": str(spec.metadata.get("workflow_node_key") or ""),
                    "runner": str(spec.metadata.get("workflow_runner") or spec.metadata.get("runner") or ""),
                }
                for index, spec in enumerate(specs, 1)
            ],
        }
        if bool(getattr(args, "dot", False)):
            print(_workflow_plan_dot(graph, plan))
            return 0
        if bool(getattr(args, "json", False)):
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        rows: list[tuple[str, str]] = [
            ("action", action),
            ("targets", ", ".join(candidates)),
            ("parallel", "effective" if plan and plan.parallel_effective else ("requested/serialized" if plan and plan.parallel_requested else "sequential")),
            ("waves", str(len(plan.waves) if plan else 0)),
        ]
        if plan is not None:
            reservation = "known: " + (", ".join(plan.reservation_paths) or "read-only") if plan.reservation_paths_known else "unknown (exclusive repository reservation)"
            rows.append(("reservation", reservation))
            for wave in plan.waves:
                detail = ", ".join(wave.nodes)
                if wave.serialized_reason:
                    detail += f" | {wave.serialized_reason}"
                rows.append((f"wave {wave.index}", detail))
        render_status_table(rows)
        if bool(getattr(args, "explain", False)) and plan is not None:
            render_status_table([
                (
                    node.key,
                    f"runner={node.runner or '-'} resource={node.resource_class} reads=[{', '.join(node.reads) or '-'}] "
                    f"writes=[{', '.join(node.writes) or '-'}] known={str(node.writes_known).lower()} | {node.scope_reason}",
                )
                for node in plan.nodes
            ])
        if bool(getattr(args, "show_commands", False)):
            render_status_table([
                (f"{index}:{spec.name}", f"cwd={spec.cwd or config.repo_root} | {' '.join(spec.argv)}")
                for index, spec in enumerate(specs, 1)
            ])
        return 0

    specs = build_validation_specs(config) if action == "validate" else build_specs(config, action)
    payload = {
        "schema_version": 1,
        "action": action,
        "targets": [],
        "graph": None,
        "schedule": None,
        "commands": [{"index": i, "name": spec.name, "argv": list(spec.argv), "cwd": str(spec.cwd or config.repo_root)} for i, spec in enumerate(specs, 1)],
    }
    if bool(getattr(args, "json", False)):
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        render_status_table([(f"{i}:{spec.name}", f"cwd={spec.cwd or config.repo_root} | {' '.join(spec.argv)}") for i, spec in enumerate(specs, 1)])
    return 0

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


def _show_and_confirm_artifact_manifest(args: argparse.Namespace, manifest: dict, *, confirm: bool = True, ui_config: dict[str, Any] | None = None) -> None:
    if not manifest:
        return
    render_artifact_manifest(manifest, console_obj=console)
    if not confirm or getattr(args, "yes", False) or getattr(args, "force", False):
        return
    from .ui_core.confirmations import confirmation_decision
    from .ui_core.preferences import resolve_ui_preferences, resolve_user_ui_preferences
    preferences = resolve_ui_preferences(ui_config, surface="tui") if ui_config is not None else resolve_user_ui_preferences(surface="tui")
    inventory = manifest.get("inventory") if isinstance(manifest.get("inventory"), dict) else {}
    entries = inventory.get("entries") if isinstance(inventory.get("entries"), list) else []
    destructive = any(isinstance(item, dict) and str(item.get("action") or "") in {"delete", "remove"} for item in entries)
    decision = confirmation_decision(
        "artifact.apply", preferences.confirmation_policy,
        context={"policy_key": "artifact_manifest", "manifest_confirmation": True, "destructive": destructive, "default": True},
        prompt="Continue with this artifact?",
    )
    if not decision.required:
        return
    if console.is_interactive and sys.stdin.isatty():
        if not Confirm.ask(decision.prompt, default=decision.default, console=console):
            raise SystemExit(1)
        console.print(artifact_confirmation_text(manifest))
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


def _append_run_log(log_path: Path | str | None, text: str) -> None:
    """Append orchestration evidence to the same durable run log as commands."""
    if log_path is None or not str(text or "").strip():
        return
    try:
        from .logstore import write_log
        write_log(str(text).strip(), log_path, append=True)
    except Exception:
        # Logging must never replace the original validation/rollback outcome.
        pass


def _run_apply(config: ProjectConfig, args: argparse.Namespace, name: str, artifact: object, *, log_path: Path | str | None = None, tasker_progress: TaskerProgressSink | None = None) -> tuple[list[OperationResult], str]:
    snapshot = "snapshot" in name
    artifact_type = "snapshot" if snapshot else "overlay"
    artifact_items = _artifact_arg_list(artifact)
    display_artifact = " ".join(artifact_items) if artifact_items else ""
    temp_bundle: tempfile.TemporaryDirectory[str] | None = None
    rebase_temp: tempfile.TemporaryDirectory[str] | None = None
    source_artifacts: list[Path] = []
    if name in {"apply-overlays", "aos"} and len(artifact_items) > 1:
        temp_bundle, temp_path, source_artifacts = _make_ad_hoc_overlay_bundle(artifact_items, name="adhoc_overlay_bundle")
        artifact = str(temp_path)
    else:
        artifact = _single_artifact_arg(artifact)
    if bool(getattr(args, "rebase", False)):
        if snapshot or name in {"apply-overlays", "aos"}:
            raise RuntimeError("--rebase is supported only for a single overlay apply.")
        rebase_temp = tempfile.TemporaryDirectory(prefix="devtool-overlay-rebase-")
        rebased_path = Path(rebase_temp.name) / Path(str(artifact)).name
        rebase_result = rebase_overlay(config.repo_root, str(artifact), output=rebased_path)
        args._devtool_rebase_result = rebase_result.to_dict()
        artifact = str(rebased_path)
    if tasker_progress is not None:
        tasker_progress.phase_started(name, index=1, total=1, argv=[name, display_artifact or str(artifact)])

    def _return_apply(result: OperationResult, text: str) -> tuple[list[OperationResult], str]:
        if tasker_progress is not None:
            tasker_progress.phase_finished(result)
        if temp_bundle is not None:
            temp_bundle.cleanup()
        if rebase_temp is not None:
            rebase_temp.cleanup()
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
        args._devtool_artifact_size = artifact_path.stat().st_size if artifact_path.exists() and artifact_path.is_file() else None
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
        _show_and_confirm_artifact_manifest(args, validation.manifest, confirm=not preview_only, ui_config=config.ui_config)
    except SystemExit as exc:
        result = OperationResult(
            "artifact-cancelled",
            [name, artifact],
            int(exc.code) if isinstance(exc.code, int) else 1,
            0.0,
            str(exc).strip() or "Cancelled before artifact apply.",
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
        _ensure_safe_repo(config.repo_root, force=args.force, yes=args.yes, command=name, ui_config=config.ui_config)
    except SystemExit as exc:
        result = OperationResult(
            "apply-cancelled",
            [name, artifact],
            int(exc.code) if isinstance(exc.code, int) else 1,
            0.0,
            str(exc).strip() or "Cancelled before artifact apply.",
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

    _artifact_lifecycle_steps: list[dict[str, object]] = []
    render_hook_progress, stream_hook_output = _artifact_hook_presentation(args)

    def _artifact_lifecycle_progress(kind: str, phase: str, result: OperationResult | None) -> None:
        display = f"Artifact {kind}: {phase.replace('_', ' ')}"
        if result is None:
            if tasker_progress is not None:
                tasker_progress.phase_started(
                    f"artifact-{kind}-{phase}", index=1, total=1,
                    argv=[f"artifact-{kind}", phase, artifact],
                    metadata={"validation_phase_name": display, "hook_kind": kind, "hook_phase": phase},
                )
            return
        result.metadata.setdefault("validation_phase_name", display)
        result.metadata.setdefault("hook_kind", kind)
        result.metadata.setdefault("hook_phase", phase)
        _artifact_lifecycle_steps.append({
            "name": result.name,
            "label": display,
            "returncode": int(result.returncode or 0),
            "skipped": bool(result.skipped),
            "duration_seconds": float(result.duration_seconds or 0.0),
            "hook_kind": kind,
            "hook_phase": phase,
        })
        if tasker_progress is not None:
            tasker_progress.phase_finished(result)

    if name in {"apply-overlays", "aos"}:
        result = apply_overlay_bundle(
            config.repo_root,
            artifact,
            force=args.force,
            yes=args.yes,
            dry_run=args.dry_run,
            log_path=log_path,
            preview=preview,
            source_artifacts=source_artifacts or None,
            ignore_checksum=bool(getattr(args, "ignore_checksum", False)),
            skip_artifact_validation=bool(getattr(args, "no_manifest_check", False)),
            lifecycle_progress=_artifact_lifecycle_progress,
            render_hook_progress=render_hook_progress,
            stream_hook_output=stream_hook_output,
        )
    elif name.startswith("apply-overlay") or name == "ao":
        result = apply_overlay(
            config.repo_root,
            artifact,
            force=args.force,
            yes=args.yes,
            dry_run=args.dry_run,
            log_path=log_path,
            preview=preview,
            ignore_checksum=bool(getattr(args, "ignore_checksum", False)),
            skip_artifact_validation=bool(getattr(args, "no_manifest_check", False)),
            lifecycle_progress=_artifact_lifecycle_progress,
            render_hook_progress=render_hook_progress,
            stream_hook_output=stream_hook_output,
        )
    else:
        result = apply_snapshot(
            config.repo_root,
            artifact,
            force=args.force,
            yes=args.yes,
            dry_run=args.dry_run,
            log_path=log_path,
            preview=preview,
            ignore_checksum=bool(getattr(args, "ignore_checksum", False)),
        )
    if _artifact_lifecycle_steps:
        result.metadata.setdefault("lifecycle_steps", []).extend(_artifact_lifecycle_steps)
        result.metadata.setdefault("lifecycle_steps_position", "before")
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
    if manifest_note.strip():
        _append_run_log(log_path, manifest_note)
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
        if policy:
            if not bool(policy.get("restore", True)):
                args.skip_restore = True
            if not bool(policy.get("build", True)):
                args.skip_build = True
            if not bool(policy.get("test", True)):
                args.skip_test = True
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
    transaction_path: str = "",
    shutdown_dotnet: bool | None = None,
) -> tuple[list[OperationResult], str, OperationResult | None]:
    if _repair_failure_count(validation_results) == 0:
        return validation_results, "", None

    def _journal_repair_path(path: Path) -> None:
        if not transaction_path:
            return
        register_transaction_paths(transaction_path, [path])

    applied, backups = apply_auto_source_repairs(
        config.repo_root,
        validation_results,
        before_write=_journal_repair_path,
    )
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


def _manifest_with_validation_tasks(manifest: dict, tasks: list[str] | tuple[str, ...], *, declared: bool) -> dict:
    """Return a manifest copy whose validation policy carries explicit tasks.

    Bundle apply_order entries can intentionally override the nested overlay
    manifest for validation routing.  Keeping that override in a manifest copy
    makes every later validation decision see the same task set instead of
    falling back to the nested target/default pipeline.
    """

    clean_tasks = list(dict.fromkeys(str(item).strip() for item in tasks if str(item).strip()))
    if not declared:
        return manifest
    updated = dict(manifest)
    validation = dict(updated.get("validation") if isinstance(updated.get("validation"), dict) else {})
    validation["tasks"] = clean_tasks
    updated["validation"] = validation
    return updated


def _bundle_entry_validation_tasks(entry: dict[str, object], entry_manifest: dict, bundle_manifest: dict) -> tuple[bool, list[str], str]:
    """Resolve validation tasks for a materialized bundle entry.

    Precedence is intentionally narrower than CLI precedence, which is enforced
    by callers before applying this value:

    1. bundle apply_order / overlays entry ``validation.tasks``
    2. nested overlay manifest ``validation.tasks``
    3. bundle-level ``validation.tasks`` as a default for bundle validation
    """

    entry_declared = bool(entry.get("validation_tasks_declared", False))
    entry_tasks = [str(item).strip() for item in (entry.get("validation_tasks") or []) if str(item).strip()]
    if entry_declared:
        return True, list(dict.fromkeys(entry_tasks)), "bundle-entry"
    nested_declared, nested_tasks = artifact_validation_task_declaration(entry_manifest)
    if nested_declared:
        return True, nested_tasks, "nested-artifact"
    bundle_declared, bundle_tasks = artifact_validation_task_declaration(bundle_manifest)
    if bundle_declared:
        return True, bundle_tasks, "bundle-default"
    return False, [], "runner-default"


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
                    "validation_tasks": list(item.validation_tasks),
                    "validation_tasks_declared": item.validation_tasks_declared,
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
                    "validation_tasks": [],
                    "validation_tasks_declared": False,
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
    """Run finalization/validator hooks as one lifecycle while preserving legacy return shape."""
    if getattr(args, "no_artifact_script", False):
        return None, ""
    validation_script = artifact_validation_policy(manifest).get("script")
    from .artifacts import artifact_validation_tests
    validation_tests = artifact_validation_tests(manifest)
    finalize_phases = set(artifact_finalize_phases(manifest))
    if not validation_script and not validation_tests and not ({"before_validation", "after_validation"} & finalize_phases):
        return None, ""

    raw_sections: list[str] = []
    executed: list[OperationResult] = []
    render_hook_progress, stream_hook_output = _artifact_hook_presentation(args)

    def _raw(result: OperationResult) -> str:
        return "$ " + " ".join(result.argv) + "\n" + result.output

    def _finalize(phase: str, status: str) -> OperationResult | None:
        display = f"Artifact finalize: {phase.replace('_', ' ')}"
        if tasker_progress is not None and phase in set(artifact_finalize_phases(manifest)):
            tasker_progress.phase_started(
                f"artifact-finalize-{phase}", index=1, total=1,
                argv=["artifact-finalize", phase, str(artifact)],
                metadata={"validation_phase_name": display, "hook_kind": "finalize", "hook_phase": phase},
            )
        try:
            result = run_artifact_finalize_script(
                config.repo_root,
                artifact,
                phase=phase,
                manifest=manifest,
                target=target,
                solution=solution,
                run_id=str(getattr(args, "_devtool_run_id", "") or ""),
                dry_run=bool(getattr(args, "dry_run", False)),
                log_path=log_path,
                ignore_checksum=bool(getattr(args, "ignore_checksum", False)),
                render_progress=render_hook_progress,
                stream_output=stream_hook_output,
                validation_requested=True,
                validation_status=status,
                commit_requested=bool(str(getattr(args, "commit", "") or "").strip()),
                commit_message=str(getattr(args, "commit", "") or ""),
                commit_paths=_artifact_commit_paths(args, config.repo_root),
            )
            if result is not None and tasker_progress is not None:
                result.metadata.setdefault("validation_phase_name", display)
                tasker_progress.phase_finished(result)
            return result
        except Exception as exc:
            result = OperationResult(
                "artifact-finalize-script",
                ["artifact-finalize-script", str(artifact)],
                1,
                0.0,
                f"{type(exc).__name__}: {exc}",
                [Issue("error", "artifact-finalize-script", str(artifact), type(exc).__name__, str(exc))],
                failure_kind="finalize_failure",
                metadata={"finalize_phase": phase, "hook_phase": phase, "hook_kind": "finalize", "validation_phase_name": display},
            )
            if tasker_progress is not None:
                tasker_progress.phase_finished(result)
            return result

    before = _finalize("before_validation", "pending")
    if before is not None:
        executed.append(before)
        raw_sections.append(_raw(before))
        if before.returncode != 0:
            return before, "\n\n".join(raw_sections)

    validator: OperationResult | None = None
    if validation_script:
        try:
            if tasker_progress is not None:
                tasker_progress.phase_started(
                    "artifact-validate-script",
                    index=1,
                    total=1,
                    argv=["artifact-validate-script", str(artifact)],
                )
            validator = run_artifact_validation_script(
                config.repo_root,
                artifact,
                manifest=manifest,
                target=target,
                solution=solution,
                run_id=str(getattr(args, "_devtool_run_id", "") or ""),
                dry_run=bool(getattr(args, "dry_run", False)),
                log_path=log_path,
                ignore_checksum=bool(getattr(args, "ignore_checksum", False)),
                render_progress=render_hook_progress,
                stream_output=stream_hook_output,
            )
        except Exception as exc:
            validator = OperationResult(
                "artifact-validate-script",
                ["artifact-validate-script", str(artifact)],
                1,
                0.0,
                f"{type(exc).__name__}: {exc}",
                [Issue("error", "artifact-validate-script", str(artifact), type(exc).__name__, str(exc))],
                failure_kind="validation_failure",
                metadata={"hook_kind": "validation", "hook_phase": "validate", "validation_phase_name": "Artifact validation"},
            )
        if validator is not None:
            validator.metadata.setdefault("validation_phase_name", "Artifact validation")
            validator.metadata.setdefault("hook_kind", "validation")
            validator.metadata.setdefault("hook_phase", "validate")
        if validator is not None and tasker_progress is not None:
            tasker_progress.phase_finished(validator)
        if validator is not None:
            executed.append(validator)
            raw_sections.append(_raw(validator))

    if validation_tests and (validator is None or validator.returncode == 0):
        from .artifact_tests import run_artifact_tests
        test_results, test_raw = run_artifact_tests(config.repo_root, manifest, verbose=bool(getattr(args,"verbose",False)), dry_run=bool(getattr(args,"dry_run",False)), log_path=log_path, tasker_progress=tasker_progress)
        executed.extend(test_results)
        if test_raw: raw_sections.append(test_raw)
        failed_tests=[r for r in test_results if r.returncode != 0 and bool(r.metadata.get("required",True))]
        if failed_tests:
            validator=failed_tests[0]
            validator.failure_kind=validator.failure_kind or "artifact_test_failure"

    validation_status = "not-run" if validator is None else ("passed" if validator.returncode == 0 else "failed")
    after = _finalize("after_validation", validation_status)
    if after is not None:
        executed.append(after)
        raw_sections.append(_raw(after))

    if not executed:
        return None, ""

    # Preserve the historical behavior of returning the validator as the main result
    # whenever it ran, except when a successful validator is followed by a failing
    # after-validation hook. Before-validation failure returned earlier unchanged.
    if validator is not None and validator.returncode != 0:
        primary = validator
    elif after is not None and after.returncode != 0:
        primary = after
    elif validator is not None:
        primary = validator
    elif after is not None:
        primary = after
    else:
        primary = before
    assert primary is not None

    hook_events = collect_hook_events(executed, dedupe=False)
    if hook_events:
        primary.metadata["hook_events"] = hook_events
    if before is not None:
        primary.metadata["finalize_before_validation"] = True
    if after is not None:
        primary.metadata["finalize_after_validation"] = True
    if len(executed) > 1:
        primary.output = "\n\n".join(raw_sections)
        combined_issues: list[Issue] = []
        for item in executed:
            combined_issues.extend(item.issues)
        primary.issues = combined_issues
    primary.metadata["lifecycle_steps"] = [
        {
            "name": item.name,
            "label": str((item.metadata or {}).get("validation_phase_name") or item.name),
            "returncode": int(item.returncode or 0),
            "skipped": bool(item.skipped),
            "duration_seconds": float(item.duration_seconds or 0.0),
            "hook_kind": str((item.metadata or {}).get("hook_kind") or ""),
            "hook_phase": str((item.metadata or {}).get("hook_phase") or ""),
        }
        for item in executed
    ]
    primary.metadata["lifecycle_steps_replace_parent"] = True
    return primary, "\n\n".join(raw_sections)


def _validation_expectation_result(manifest: dict, results: list[OperationResult]) -> OperationResult | None:
    policy = artifact_validation_policy(manifest)
    expected = policy.get("expected") if isinstance(policy.get("expected"), dict) else {}
    maximum_warnings = policy.get("maximum_warnings")
    has_warning_limit = isinstance(maximum_warnings, int) and not isinstance(maximum_warnings, bool)
    if not expected and not has_warning_limit:
        return None

    def _phase(result: OperationResult) -> str:
        metadata = dict(result.metadata or {})
        phase = str(metadata.get("validation_phase_name") or metadata.get("validation_scope") or "").lower()
        name = str(result.name or "").lower()
        text = f"{phase} {name}"
        if "restore" in text or "dependenc" in text:
            return "restore"
        if "test" in text or bool(metadata.get("contains_tests")):
            return "tests"
        if any(token in text for token in ("build", "compile", "package", "lint", "resource", "finalize")):
            return "build"
        return "other"

    def _outcome(items: list[OperationResult]) -> str:
        if not items or all(item.skipped for item in items):
            return "skipped"
        if any(item.returncode != 0 for item in items):
            return "failed"
        warnings = sum(
            1
            for item in items
            for issue in item.issues
            if issue_counts_toward_target_warning_budget(issue)
        )
        return "warning" if warnings else "passed"

    aliases = {
        "pass": "passed", "success": "passed", "succeeded": "passed",
        "skip": "skipped", "warn": "warning", "warnings": "warning",
        "fail": "failed", "error": "failed", "errors": "failed",
    }
    grouped = {key: [item for item in results if _phase(item) == key] for key in ("restore", "build", "tests")}
    mismatches: list[str] = []
    evidence: dict[str, object] = {}
    for key, wanted in expected.items():
        if key not in grouped:
            continue
        wanted_normalized = aliases.get(str(wanted).strip().lower(), str(wanted).strip().lower())
        actual = _outcome(grouped[key])
        evidence[key] = {
            "expected": wanted_normalized,
            "actual": actual,
            "steps": [item.name for item in grouped[key]],
        }
        if actual != wanted_normalized:
            mismatches.append(f"{key}: expected {wanted_normalized}, got {actual}")

    warning_count = target_warning_count(results)
    infrastructure_warning_count = sum(
        1
        for item in results
        for issue in item.issues
        if str(issue.severity).lower() == "warning"
        and not issue_counts_toward_target_warning_budget(issue)
    )
    if has_warning_limit:
        evidence["warnings"] = {
            "maximum": int(maximum_warnings),
            "actual": warning_count,
            "scope": "target",
            "infrastructure_ignored": infrastructure_warning_count,
        }
        if warning_count > int(maximum_warnings):
            mismatches.append(f"warnings: maximum {maximum_warnings}, got {warning_count}")

    plan_meta = next(
        (dict(item.metadata or {}) for item in results if (item.metadata or {}).get("validation_plan_source")),
        {},
    )
    output_lines = ["Validation contract " + ("failed:" if mismatches else "satisfied.")]
    output_lines.extend(f"- {item}" for item in mismatches)
    if not mismatches:
        for key, value in evidence.items():
            output_lines.append(f"- {key}: {value}")
    return OperationResult(
        "validation-contract",
        ["devtool", "validation-contract"],
        1 if mismatches else 0,
        0.0,
        "\n".join(output_lines),
        [Issue("error", "validation-contract", "", "ExpectationMismatch", item) for item in mismatches],
        failure_kind="validation_failure" if mismatches else "",
        metadata={
            "validation_contract_satisfied": not mismatches,
            "validation_contract_evidence": evidence,
            "validation_warning_count": warning_count,
            "validation_target_warning_count": warning_count,
            "validation_infrastructure_warning_count": infrastructure_warning_count,
            **{key: value for key, value in plan_meta.items() if key.startswith("validation_plan_") or key.startswith("validation_tasks_") or key == "validation_declared_tasks"},
        },
    )


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
        run_per_overlay_validation = bundle_strategy in {"per-overlay", "both"}
        run_after_all_validation = bundle_strategy in {"after-all", "both"}
        bundle_target_mode = str(bundle_manifest.get("target_mode") or "").strip().lower().replace("-", "_")
        bundle_uses_per_overlay_targets = bundle_target_mode == "per_overlay" or any(
            str(entry.get("target") or "").strip() for entry in entries
        )

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
                raw_entry_manifest = read_artifact_manifest(path)
                tasks_declared, selected_entry_tasks, task_source = _bundle_entry_validation_tasks(entry, raw_entry_manifest, bundle_manifest)
                entry_manifest = _manifest_with_validation_tasks(raw_entry_manifest, selected_entry_tasks, declared=tasks_declared)
                entry_policy = artifact_validation_policy(entry_manifest)
                should_validate = run_per_overlay_validation and not getattr(args, "no_validate", False) and bool(
                    getattr(args, "validate", False)
                    or getattr(args, "validate_only", False)
                    or bundle_policy.get("required", False)
                    or entry_policy.get("required", False)
                )
                if not should_validate:
                    continue

                manifest_target = str(entry["target"] or artifact_target(entry_manifest)).strip()
                cli_target = str(getattr(args, "target", "") or "").strip()
                if bundle_uses_per_overlay_targets and manifest_target:
                    target = manifest_target
                elif getattr(args, "_devtool_cli_target_explicit", False):
                    target = cli_target
                else:
                    target = manifest_target
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
                entry_args._devtool_validation_tasks_declared = tasks_declared
                entry_args._devtool_validation_task_source = task_source
                entry_args.command = "apply-overlay"
                entry_args.artifact = str(path)
                if selected_entry_tasks and not getattr(entry_args, "_devtool_cli_test_task_explicit", False):
                    entry_args.test_task = list(selected_entry_tasks)
                    entry_args._devtool_manifest_validation_tasks = list(selected_entry_tasks)
                _apply_artifact_validation_defaults(entry_config, entry_args, entry_manifest)
                restore_enabled = entry_policy.get("restore", bundle_policy.get("restore", True))
                build_enabled = entry_policy.get("build", bundle_policy.get("build", True))
                test_enabled = entry_policy.get("test", bundle_policy.get("test", True))
                if _exclusive_validation_task_values(entry_args):
                    entry_args.skip_restore = False
                    entry_args.skip_build = False
                    entry_args.skip_test = False
                else:
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

                if not any(result.returncode != 0 for result in group_results) and not _exclusive_validation_task_values(entry_args):
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
                        transaction_path=transaction_path,
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

            # "after-all" validates each distinct affected target once after every overlay
            # has been applied. "both" performs both the per-overlay and final passes.
            if run_after_all_validation and not any(result.returncode != 0 for result in all_results):
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
                            transaction_path=transaction_path,
                            shutdown_dotnet=False,
                        )
                        if repair_raw:
                            final_raw = repair_raw
                    all_results.extend(final_results)
                    raw_sections.append(f"=== Final validation [{target}] ===\n{final_raw}")
                    if any(result.returncode != 0 for result in final_results) and not continue_on_error:
                        break

            if source_bundle is not None and not _exclusive_validation_task_values(args) and not any(result.returncode != 0 for result in all_results):
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
                evidence_line = f"Validation evidence preserved: {evidence_path}"
                raw_sections.append(evidence_line)
                _append_run_log(log_path, evidence_line)
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
        commit_start = len(all_results)
        all_results, commit_raw = _maybe_commit_after_apply(
            config,
            commit_args,
            all_results,
            "\n\n".join(raw_sections),
            log_path=log_path,
            tasker_progress=tasker_progress,
        )
        _record_transaction_commit_from_results(
            transaction_path, config.repo_root, all_results, start_index=commit_start
        )
        raw_sections = [commit_raw]

        if transaction_path and not any(result.returncode != 0 for result in all_results):
            verification = _verify_applied_artifact_effects(config, args)
            if verification is not None:
                all_results.append(verification)
                raw_sections.append("$ " + " ".join(verification.argv) + "\n" + verification.output)

        if transaction_path:
            if any(result.returncode != 0 for result in all_results):
                if rollback_enabled and not any(result.name == "apply-rollback" for result in all_results):
                    evidence_path = preserve_validation_evidence(
                        config.repo_root, all_results, run_id=str(getattr(args, "_devtool_run_id", "") or "")
                    )
                    if evidence_path:
                        evidence_line = f"Validation evidence preserved: {evidence_path}"
                        raw_sections.append(evidence_line)
                        _append_run_log(log_path, evidence_line)
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

        delete_on_failure = bool(getattr(args, "delete_artifact_on_failure", False))
        if (
            not getattr(args, "dry_run", False)
            and not getattr(args, "keep_artifact", False)
            and (not any(result.returncode != 0 for result in all_results) or delete_on_failure)
        ):
            cleanup = _delete_original_artifact_after_apply(
                all_results,
                artifact=getattr(args, "artifact", ""),
                delete_on_failure=delete_on_failure,
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


def _safe_preview_path(raw: object) -> Path:
    rel = Path(str(raw or "").strip())
    if not str(rel) or rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"Unsafe preview path: {raw}")
    return rel


def _artifact_payload_hashes(artifact: object, *, snapshot: bool) -> dict[str, str]:
    artifacts = _artifact_arg_list(artifact)
    if len(artifacts) != 1:
        return {}
    try:
        artifact_path = resolve_artifact_path(artifacts[0])
        if not snapshot and is_overlay_bundle(artifact_path):
            return {}
        hashes: dict[str, str] = {}
        for member in list_archive_members(artifact_path, include_data=True):
            if member.is_dir or member.data is None:
                continue
            rel = _safe_preview_path(member.name)
            hashes[str(rel).replace("\\", "/")] = hashlib.sha256(member.data).hexdigest()
        return hashes
    except Exception:
        return {}


def _verify_applied_artifact_effects(config: ProjectConfig, args: argparse.Namespace) -> OperationResult | None:
    if (
        getattr(args, "dry_run", False)
        or getattr(args, "preview", False)
        or (getattr(args, "diff", False) and not getattr(args, "interactive", False))
        or getattr(args, "validate_only", False)
    ):
        return None
    if getattr(args, "command", "") not in APPLY_COMMANDS:
        return None

    preview = dict(getattr(args, "_devtool_artifact_preview", {}) or {})
    changes = [item for item in preview.get("changes", []) if isinstance(item, dict)]
    if not changes:
        return OperationResult("apply-verification", ["devtool", "verify-apply"], 0, 0.0, "Applied artifact verification passed.", [])

    start = time.perf_counter()
    artifact_type = str(getattr(args, "_devtool_artifact_type", "") or "").strip()
    snapshot = artifact_type == "snapshot" or "snapshot" in str(getattr(args, "command", ""))
    payload_hashes = _artifact_payload_hashes(getattr(args, "artifact", ""), snapshot=snapshot)
    problems: list[str] = []
    checked = 0

    for change in changes:
        kind = str(change.get("kind") or "").strip()
        if kind == "unchanged":
            continue
        try:
            rel = _safe_preview_path(change.get("path"))
        except ValueError as exc:
            problems.append(str(exc))
            continue
        display = str(rel).replace("\\", "/")
        target = config.repo_root / rel
        checked += 1
        if kind == "delete":
            if target.exists() or target.is_symlink():
                problems.append(f"{display}: expected deleted but still exists")
            continue
        if kind == "add-dir":
            if not target.is_dir() or target.is_symlink():
                problems.append(f"{display}: expected applied directory but is missing or not a directory")
            continue
        if kind in {"add", "modify", "replace-dir"}:
            if not (target.exists() or target.is_symlink()):
                problems.append(f"{display}: expected applied but is missing")
                continue
            expected_hash = payload_hashes.get(display)
            if expected_hash and target.is_file() and sha256_file(target) != expected_hash:
                problems.append(f"{display}: content does not match artifact payload")
            continue
        problems.append(f"{display}: unsupported preview change kind {kind!r}")

    if problems:
        output = "Applied artifact verification failed:\n" + "\n".join(f"- {item}" for item in problems[:20])
        if len(problems) > 20:
            output += f"\n- ... {len(problems) - 20} more issue(s)"
        return OperationResult(
            "apply-verification",
            ["devtool", "verify-apply"],
            1,
            time.perf_counter() - start,
            output,
            [Issue("error", "apply-verification", "", "ApplyVerificationError", item) for item in problems],
            failure_kind="apply_verification_failure",
        )

    return OperationResult(
        "apply-verification",
        ["devtool", "verify-apply"],
        0,
        time.perf_counter() - start,
        f"Applied artifact verification passed ({checked} path{'s' if checked != 1 else ''} checked).",
        [],
    )


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



def _continue_on_error_for_args(args: argparse.Namespace) -> bool:
    """Return whether independent validation/workflow steps should keep running.

    Devtool defaults validation to diagnostic collection now: plain validation
    and apply-time validation continue through independent phases/tasks unless
    the user explicitly passes --fail-fast. Non-validation workflows retain the
    historical stop-on-first-failure behavior unless -k/--continue-on-error is
    requested.
    """
    if bool(getattr(args, "fail_fast", False)):
        return False
    if bool(getattr(args, "continue_on_error", False)):
        return True
    command = str(getattr(args, "command", "") or "")
    if command in {"validate", "v", "validate-all", "wine-validate", "wv"}:
        return True
    if command in APPLY_COMMANDS and (
        bool(getattr(args, "validate", False))
        or bool(getattr(args, "validate_only", False))
    ):
        return True
    return False

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
    continue_on_error = _continue_on_error_for_args(args)

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


def _ensure_safe_repo(repo_root: Path, *, force: bool, yes: bool, command: str, ui_config: dict[str, Any] | None = None) -> None:
    if repo_root == Path("/"):
        raise SystemExit("Refusing to operate on '/'")
    if not (repo_root / ".git").exists() and not force:
        raise SystemExit("Repo root does not look like a git repository. Use --force to override.")
    from .ui_core.confirmations import confirmation_decision
    from .ui_core.preferences import resolve_ui_preferences, resolve_user_ui_preferences
    preferences = resolve_ui_preferences(ui_config, surface="tui") if ui_config is not None else resolve_user_ui_preferences(surface="tui")
    if "snapshot" in command and not (yes or force):
        decision = confirmation_decision(
            "artifact.apply", preferences.confirmation_policy,
            context={"policy_key": "snapshot_overwrite", "destructive": True, "overwrites": True, "default": False},
            prompt="Applying a snapshot may overwrite/delete files. Continue?",
        )
        if decision.required:
            if console.is_interactive and sys.stdin.isatty():
                okay = Confirm.ask(decision.prompt, default=decision.default, console=console)
                if not okay:
                    raise SystemExit(1)
            else:
                raise SystemExit("Snapshot apply needs --yes or --force in non-interactive mode.")
    dirty = command.startswith("apply") and _git_dirty(repo_root)
    if dirty and not (yes or force):
        decision = confirmation_decision(
            "artifact.apply", preferences.confirmation_policy,
            context={"policy_key": "dirty_tree", "dirty_tree": True, "default": True},
            prompt="Git tree is dirty. Continue?",
        )
        if decision.required:
            if console.is_interactive and sys.stdin.isatty():
                okay = Confirm.ask(decision.prompt, default=decision.default, console=console)
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
        ("platform", config.platform or "-"),
        ("runner", config.runner or runner.kind),
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
    health_checks = collect_doctor_checks(config)
    rows.extend((f"health.{item.section}.{item.name}", f"{item.status}: {item.detail}") for item in health_checks)
    workflow_report = _toolchain_report_for_args(config, args or argparse.Namespace())
    if workflow_report is not None:
        rows.append(("workflow_targets", ", ".join(workflow_report.targets)))
        rows.append(("workflow_runners", ", ".join(workflow_report.runners) or "-"))
        rows.append(("workflow_environments", ", ".join(workflow_report.environments) or "-"))
        for tool in workflow_report.tools:
            rows.append((f"workflow_tool.{tool.executable}", f"{tool.status}: {tool.detail}"))
        for check in workflow_report.checks:
            if check.status not in {"ready", "ok"}:
                rows.append((f"workflow.{check.section}.{check.name}", f"{check.status}: {check.detail}"))
    rows.extend(toolchain_doctor_rows(config))
    rows.extend(termux_integration_doctor_rows(
        config,
        explain=bool(getattr(args, "explain", False)) if args is not None else False,
        proot_distro=str(getattr(args, "proot_distro", "") or "") if args is not None else "",
    ))
    rows.extend(runner.doctor_rows(config))
    json_requested = args is not None and getattr(args, "json", False) is True
    if json_requested:
        payload = doctor_payload(config)
        payload["workflow_preflight"] = workflow_report.as_dict() if workflow_report is not None else {"healthy": True, "targets": []}
        payload["healthy"] = bool(payload.get("healthy", True)) and bool(workflow_report is None or workflow_report.healthy)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        render_status_table(rows)


def _status(config: ProjectConfig, *, args: argparse.Namespace | None = None) -> None:
    runner = get_project_runner(config)
    capabilities = runner_capabilities(config).supported()
    plan = _execution_plan_for_args(config, args or argparse.Namespace(host=False, chroot="", execution_environment=EXECUTION_AUTO))
    rows = [
        ("repo", str(config.repo_root)),
        ("kind", config.kind),
        ("platform", config.platform or "-"),
        ("runner", config.runner or runner.kind),
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
        ("platform", config.platform or "-"),
        ("runner", config.runner or runner.kind),
        ("runner_name", runner.display_name),
        ("project_root", config.project_root or str(config.repo_root)),
        ("selected_target", config.selected_target or "-"),
        ("variant", config.variant or "-"),
        ("jobs", ", ".join(sorted(config.jobs)) or "-"),
    ]
    if config.selected_target and config.selected_target in config.targets:
        selected = config.targets[config.selected_target]
        rows.extend([
            ("target_platform", selected.platform or "-"),
            ("target_runner", selected.effective_runner or "-"),
            ("target_workflows", ", ".join(sorted(selected.workflows)) or "-"),
        ])
        for workflow_name, workflow in sorted(selected.workflows.items()):
            rows.append((f"workflow.{workflow_name}", " -> ".join(step.raw for step in workflow.steps)))
            try:
                inspect_specs = (
                    build_workspace_validation_specs(config, [config.selected_target], wine=workflow_name == "wine-validate")
                    if workflow_name in {"validate", "wine-validate"}
                    else build_workspace_specs(config, [config.selected_target], workflow_name)
                )
                inspect_schedule = build_execution_plan_from_specs(config, inspect_specs)
            except Exception:
                inspect_schedule = None
            if inspect_schedule is not None:
                mode = "parallel" if inspect_schedule.parallel_effective else ("parallel-requested/serialized" if inspect_schedule.parallel_requested else "sequential")
                rows.append((f"workflow_plan.{workflow_name}", f"{mode}; waves={len(inspect_schedule.waves)}; reservation={'known' if inspect_schedule.reservation_paths_known else 'exclusive'}"))
    for job_name, job in sorted(config.jobs.items()):
        mode = job.command_mode
        payload = job.action or " ".join(job.command) or job.shell_command
        rows.append((f"job.{job_name}", f"runner={job.runner} mode={mode} {payload}".strip()))
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
    raw_validation_tasks = getattr(args, "validation_tasks", None)
    raw_validation_tasks_csv = getattr(args, "validation_tasks_csv", None)
    validation_tasks_declared = raw_validation_tasks is not None or raw_validation_tasks_csv is not None
    resolved_validation_tasks = [
        *(_cli_string_list(raw_validation_tasks)),
        *(_cli_string_list(raw_validation_tasks_csv)),
    ]
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
        validation_timeout_seconds=int(getattr(args, "validation_timeout", 900)),
        validation_tasks=resolved_validation_tasks if validation_tasks_declared else None,
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


_TRUE_CONFIG_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_CONFIG_VALUES = {"0", "false", "no", "off", "disabled"}


def _config_bool(value: object, *, default: bool | None = None) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_CONFIG_VALUES:
            return True
        if text in _FALSE_CONFIG_VALUES:
            return False
        if text == "":
            return default
    return default


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
    if _config_bool(output.get("copy"), default=False) is True and not _raw_flag_present(raw_argv, "-p", "--copy", "--no-clipboard"):
        args.copy = True
    if _config_bool(output.get("no_clipboard"), default=False) is True and not _raw_flag_present(raw_argv, "--no-clipboard", "-p", "--copy"):
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
        parsed = _config_bool(hud.get("auto_hud"), default=None)
        if parsed is not None:
            args.auto_hud = parsed
    if "mode" in hud and not _raw_flag_present(raw_argv, "--hud-mode"):
        value = str(hud.get("mode") or "").strip()
        if value:
            args.hud_mode = value
    overlay_value = _config_bool(hud.get("overlay", "auto"), default=None)
    if overlay_value is True and not _raw_flag_present(raw_argv, "--hud-overlay", "--hud-no-overlay"):
        args.hud_overlay = True
    elif overlay_value is False and not _raw_flag_present(raw_argv, "--hud-overlay", "--hud-no-overlay"):
        args.hud_no_overlay = True

    env = cli.get("environment", {}) if isinstance(cli.get("environment", {}), dict) else {}
    if "execution_environment" in env and not _raw_flag_present(raw_argv, "--execution-environment", "--host", "--chroot"):
        value = str(env.get("execution_environment") or EXECUTION_AUTO).strip()
        if value in EXECUTION_ENVIRONMENTS:
            args.execution_environment = value
    if _config_bool(env.get("no_auto_chroot"), default=False) is True and not _raw_flag_present(raw_argv, "--no-auto-chroot", "--chroot"):
        args.no_auto_chroot = True

    apply = cli.get("apply", {}) if isinstance(cli.get("apply", {}), dict) else {}
    if str(getattr(args, "command", "") or "") in LEGACY_APPLY_VALIDATE_COMMANDS:
        if _config_bool(apply.get("validate"), default=False) is True and not _raw_flag_present(raw_argv, "--validate", "--no-validate"):
            args.validate = True
        if _config_bool(apply.get("manifest_check"), default=True) is False and not _raw_flag_present(raw_argv, "--no-manifest-check"):
            args.no_manifest_check = True
        if _config_bool(apply.get("ignore_checksum"), default=False) is True and not _raw_flag_present(raw_argv, "--ignore-checksum"):
            args.ignore_checksum = True
        if _config_bool(apply.get("keep_artifact"), default=False) is True and not _raw_flag_present(raw_argv, "--keep-artifact"):
            args.keep_artifact = True
        if _config_bool(apply.get("delete_artifact_on_failure"), default=False) is True and not _raw_flag_present(raw_argv, "--delete-artifact-on-failure"):
            args.delete_artifact_on_failure = True
        if _config_bool(apply.get("rollback_on_failure"), default=True) is False and not _raw_flag_present(raw_argv, "--no-rollback-on-failure"):
            args.no_rollback_on_failure = True
        if _config_bool(apply.get("apply_notification"), default=True) is False and not _raw_flag_present(raw_argv, "--no-apply-notification"):
            args.no_apply_notification = True
        if _config_bool(apply.get("artifact_script"), default=True) is False and not _raw_flag_present(raw_argv, "--no-artifact-script"):
            args.no_artifact_script = True

def _resolve_config_editor(requested: str = "") -> list[str]:
    global_config, _ = load_global_config()
    terminal = global_config.get("terminal", {}) if isinstance(global_config.get("terminal", {}), dict) else {}
    preferred = str(terminal.get("preferred", "") or "").strip()
    candidates = [
        requested,
        os.environ.get("DEVTOOL_EDITOR", ""),
        os.environ.get("VISUAL", ""),
        os.environ.get("EDITOR", ""),
        preferred,
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
    raise SystemExit("No editor found. Set [terminal].preferred or DEVTOOL_EDITOR/VISUAL/EDITOR.")

def _config_command(args: argparse.Namespace) -> int:
    if args.config_command == "path":
        if getattr(args, "global_config", False):
            print(global_config_path())
            return 0
        config = _load_command_config(args)
        print(config.config_path or "")
        return 0

    # Migration works on raw TOML before loading the full project model. This
    # keeps the migration command usable even when legacy/canonical keys
    # conflict and normal configuration loading would intentionally fail.
    if args.config_command == "migrate":
        if getattr(args, "global_config", False):
            path = global_config_path()
        else:
            repo_root = resolve_repository_root(getattr(args, "repo", None), getattr(args, "config", None))
            path = find_config(repo_root, getattr(args, "config", None)) or (repo_root / ".devtool.toml")
        if not path.exists():
            raise SystemExit(f"Config not found: {path}")
        report = migration_report(path)
        if bool(getattr(args, "write", False)) and report.get("needs_migration"):
            if not report.get("safe_to_write", True):
                unsafe = [item for item in report.get("findings", []) if not item.get("safe", True)]
                detail = "; ".join(str(item.get("message") or item.get("compatibility_id") or "unsafe migration") for item in unsafe)
                raise SystemExit(f"Config migration requires manual reconciliation before --write: {detail}")
            original = path.read_text(encoding="utf-8")
            migrated, findings = migrate_config_text(original)
            backup = path.with_name(path.name + ".bak")
            backup.write_text(original, encoding="utf-8")
            path.write_text(migrated, encoding="utf-8")
            report = migration_report(path)
            report["written"] = True
            report["backup"] = str(backup)
            report["migrated_findings"] = [item.as_dict() for item in findings]
        else:
            report["written"] = False
        if bool(getattr(args, "json", False)):
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            rows = [("config", str(path)), ("status", "migration recommended" if report.get("needs_migration") else "canonical")]
            for item in report.get("findings", []):
                safety = "safe" if item.get("safe", True) else "manual"
                rows.append((f"line {item.get('line')}", f"[{item.get('section')}] {item.get('key')} → {item.get('replacement_key')} ({safety})"))
            if report.get("written"):
                rows.append(("backup", str(report.get("backup") or "")))
            render_status_table(rows)
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
        print(generate_template(config.repo_root))
        return 0
    if args.config_command == "init":
        target = write_template_config(config.repo_root, force=getattr(args, "force", False))
        console.print(f"[green]Wrote[/green] {target}")
        return 0
    if args.config_command == "edit":
        path = config.config_path or (config.repo_root / ".devtool.toml")
        if not path.exists():
            path = write_template_config(config.repo_root, force=False)
        editor = _resolve_config_editor(str(getattr(args, "editor", "") or ""))
        return subprocess.run([*editor, str(path)], check=False).returncode
    if args.config_command == "resolve":
        payload = resolve_config_dict(config)
        if bool(getattr(args, "provenance", False)):
            payload["explanation"] = {
                "runner": config.runner,
                "platform": config.platform,
                "selected_target": config.selected_target,
                "provenance": dict(sorted(config.config_provenance.items())),
            }
        print(json.dumps(payload, indent=2))
        return 0
    if args.config_command == "explain":
        path = config.config_path or (config.repo_root / ".devtool.toml")
        migration = migration_report(path) if path.exists() else {"schema_version": 2, "path": str(path), "needs_migration": False, "safe_to_write": True, "findings": []}
        payload = {
            "schema_version": 2,
            "config_path": str(path),
            "repo_root": str(config.repo_root),
            "project": {"runner": config.runner, "platform": config.platform, "selected_target": config.selected_target, "variant": config.variant},
            "workspace": workspace_as_dict(config.workspace, config.targets),
            "provenance": dict(sorted(config.config_provenance.items())),
            "migration": migration,
            "compatibility": compatibility_report(path).get("summary", {}),
            "ui_profiles": build_completion_state(config).ui_profiles,
        }
        if bool(getattr(args, "json", False)):
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            rows = [
                ("config", str(path)),
                ("runner", config.runner),
                ("platform", config.platform or "-"),
                ("selected_target", config.selected_target or "-"),
                ("workspace_targets", ", ".join(sorted(config.targets)) or "-"),
                ("workspace_groups", ", ".join(sorted(config.workspace.groups)) or "-"),
                ("migration", "recommended" if migration.get("needs_migration") else "canonical"),
            ]
            rows.extend((f"provenance.{key}", value) for key, value in sorted(config.config_provenance.items()))
            render_status_table(rows)
        return 0
    if args.config_command == "doctor":
        migration = migration_report(config.config_path) if config.config_path and config.config_path.exists() else {"needs_migration": False}
        rows = [
            ("config_path", str(config.config_path) if config.config_path else "-"),
            ("global_config_path", str(config.global_config_path) if config.global_config_path else "-"),
            ("repo_root", str(config.repo_root)),
            ("runner", config.runner),
            ("platform", config.platform or "-"),
            ("compatibility_migration", "recommended" if migration.get("needs_migration") else "canonical"),
            ("build_target", config.build_target or "-"),
            ("run_target", config.run_target or "-"),
        ]
        rows.extend((f"provenance.{key}", value) for key, value in sorted(config.config_provenance.items()))
        render_status_table(rows)
        return 0
    return 1

def _maybe_delegate_ssh(raw_argv: list[str], args: argparse.Namespace) -> int | None:
    """Select SSH whole-run or local-devtool/remote-command mode."""
    if str(os.environ.get("DEVTOOL_INSIDE_SSH", "")).strip().lower() in {"1", "true", "yes", "on"}:
        return None
    from .ssh_tmux_runner import resolve_ssh_config, run_ssh_tmux, ssh_requested

    try:
        if not ssh_requested(raw_argv, cwd=os.getcwd()):
            return None
        ssh_config = resolve_ssh_config(raw_argv, cwd=os.getcwd())
        args._devtool_ssh_config = ssh_config
        if ssh_config.mode == "commands":
            # Keep this devtool process local; only planned external command
            # phases are wrapped later at the shared execution boundary.
            args.execution_environment = EXECUTION_SSH
            return None
        return run_ssh_tmux(raw_argv, cwd=os.getcwd())
    except ValueError as exc:
        raise SystemExit(f"SSH execution requested but unavailable: {exc}") from exc


def _maybe_prepare_termux_auto_chroot(raw_argv: list[str], args: argparse.Namespace) -> int | None:
    """Enable Android chroot execution from Termux.

    Default mode keeps devtool itself on the Termux host and uses the chroot as
    an execution backend for command phases.  This preserves the native Rich UI,
    native app HUD bridge, clipboard, and command summary on Android.  The old whole-
    devtool delegation remains available with DEVTOOL_AUTO_CHROOT_MODE=full.
    """
    if str(getattr(args, "command", "") or "") == "validation-menu":
        return None
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
            if str(os.environ.get("DEVTOOL_INSIDE_SSH", "")).strip().lower() in {"1", "true", "yes", "on"} and configured_environment == EXECUTION_SSH:
                configured_environment = EXECUTION_AUTO
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


def _build_cache_cleanup_result(config: ProjectConfig, args: argparse.Namespace, *, dry_run: bool) -> OperationResult:
    started = datetime.now(timezone.utc)
    active_transaction = find_active_transaction(config.repo_root)
    if active_transaction is not None:
        raise SystemExit(
            "Refusing cache cleanup while this repository has an active artifact transaction: "
            f"{active_transaction}"
        )
    plan = _execution_plan_for_args(config, args)
    extra_paths: list[Path] = []
    try:
        from .build_optimization import _cache_root as resolved_build_cache_root
        extra_paths.append(resolved_build_cache_root(config, config.kind, plan.effective))
    except (OSError, ValueError):
        # The generic repository/backend-scoped roots below are still safe to
        # clean.  A custom/guest cache path that cannot be resolved must not be
        # guessed from host identity.
        pass
    removed = cleanup_devtool_caches(
        repo_root=config.repo_root,
        backend=plan.effective,
        extra_cache_paths=extra_paths,
        dry_run=dry_run,
    )
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



def _build_space_cleanup_result(config: ProjectConfig, args: argparse.Namespace, *, dry_run: bool) -> OperationResult:
    started = datetime.now(timezone.utc)
    from . import android_build

    cache_root = android_build.devtool_cache_dir() / android_build.APP_NAME
    target_mb = max(0, int(getattr(args, "space_target_free_mb", 0) or 0))
    target_bytes = target_mb * 1024 * 1024 if target_mb else None
    report = android_build.cleanup_android_space(
        cache_root,
        target_free_bytes=target_bytes,
        dry_run=dry_run,
        managed_gradle_retention=max(0, int(getattr(args, "managed_gradle_retention", 2) or 0)),
        include_gradle_user_cache=bool(getattr(args, "gradle_user_cache", False)),
        gradle_user_home=Path.home() / ".gradle",
    )
    duration = max(0.0, (datetime.now(timezone.utc) - started).total_seconds())
    lines = [
        "Android/toolchain space cleanup:",
        f"- Free before: {android_build.format_transfer_size(report.free_before) if report.free_before is not None else 'unknown'}",
    ]
    if report.removed:
        verb = "Would remove" if dry_run else "Removed"
        lines.append(f"- {verb}: {android_build.format_transfer_size(report.reclaimed_bytes)} across {len(report.removed)} path(s)")
        for candidate in report.removed:
            lines.append(
                f"  - {android_build.format_transfer_size(candidate.size_bytes)} [{candidate.category}] "
                f"{candidate.path} — {candidate.reason}"
            )
    else:
        lines.append("- No safe reclaimable Android/toolchain cache paths were found.")
    lines.append(
        f"- {'Estimated free after' if dry_run else 'Free after'}: "
        f"{android_build.format_transfer_size(report.free_after) if report.free_after is not None else 'unknown'}"
    )
    lines.append("- Preserved: active SDK/NDK/CMake trees, QEMU sysroots, Gradle modules-2, and Gradle wrapper distributions.")
    return OperationResult(
        "space-cleanup",
        ["devtool", "clean", "--space"],
        0,
        duration,
        "\n".join(lines),
        [],
        metadata={
            "cleanup": True,
            "space_cleanup": True,
            "dry_run": dry_run,
            "reclaimed_bytes": report.reclaimed_bytes,
            "paths": [str(item.path) for item in report.removed],
        },
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



def _apply_ui_cli_overrides(args: argparse.Namespace) -> None:
    mapping = {
        "ui_profile": "DEVTOOL_UI_PROFILE",
        "ui_theme": "DEVTOOL_UI_THEME",
        "ui_density": "DEVTOOL_UI_DENSITY",
        "ui_icons": "DEVTOOL_UI_ICONS",
        "ui_motion": "DEVTOOL_UI_MOTION",
        "ui_verbosity": "DEVTOOL_UI_VERBOSITY",
    }
    for attr, env_key in mapping.items():
        value = str(getattr(args, attr, "") or "").strip()
        if value:
            os.environ[env_key] = value


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parse_argv, passthrough_args = split_runner_passthrough_args(raw_argv)
    parse_argv, universal_json = extract_universal_json_flag(parse_argv)
    parser = build_parser()
    args = parser.parse_args(parse_argv)
    _apply_ui_cli_overrides(args)
    configure_logging(verbose=bool(getattr(args, "verbose", False)), quiet=bool(getattr(args, "quiet", False)), trace=bool(getattr(args, "trace", False)))
    console.quiet = bool(getattr(args, "quiet", False))
    configure_event_console(console, enabled=bool((getattr(args, "verbose", False) or getattr(args, "trace", False)) and not getattr(args, "quiet", False) and not universal_json))

    def _invoke() -> int:
        command = str(getattr(args, "command", "") or "devtool")
        emit_build = command in PROGRESS_CAPABLE_COMMANDS
        if emit_build:
            publish_event(BuildStarted(project=str(getattr(args, "repo", "") or Path.cwd()), metadata={"command": command}))
        try:
            if bool(getattr(args, "quiet", False)) and not universal_json:
                with redirect_stdout(io.StringIO()):
                    code = int(_main_after_parse(raw_argv, parse_argv, passthrough_args, parser, args))
            else:
                code = int(_main_after_parse(raw_argv, parse_argv, passthrough_args, parser, args))
        except BaseException:
            if emit_build:
                publish_event(BuildFinished(success=False, metadata={"command": command, "exception": True}))
            raise
        if emit_build:
            publish_event(BuildFinished(success=code == 0, metadata={"command": command, "exit_code": code}))
        return code

    if universal_json:
        args._devtool_json = True
        if hasattr(args, "json"):
            args.json = True
        command = str(getattr(args, "command", "") or "devtool")
        repo = str(getattr(args, "repo", "") or Path.cwd())
        return run_json_invocation(
            lambda: _invoke(),
            command=command,
            repo=repo,
        )
    try:
        return _invoke()
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, int):
            raise
        message = str(code or "devtool command failed")
        print(f"Error: {message}", file=sys.stderr)
        return 1
    except Exception as exc:
        if bool(getattr(args, "verbose", False)) or bool(getattr(args, "trace", False)) or _tracebacks_enabled():
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
            return 1
        print(f"Internal devtool error: {exc}", file=sys.stderr)
        print("Run again with --verbose or DEVTOOL_TRACEBACK=1 for a full traceback; --trace also enables traceback logging.", file=sys.stderr)
        return 1


def _tracebacks_enabled() -> bool:
    return str(os.environ.get("DEVTOOL_TRACEBACK", "")).strip().lower() in {"1", "true", "yes", "on"}


def _main_after_parse(
    raw_argv: list[str],
    parse_argv: list[str],
    passthrough_args: list[str],
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> int:
    args._devtool_passthrough_args = passthrough_args
    args._devtool_hud_explicit = any(token in {"--auto-hud", "--hud-auto", "--no-hud"} for token in parse_argv)
    _normalize_command_aliases(args)
    _apply_global_cli_defaults(args, parse_argv)
    _normalize_live_progress_args(args)
    ssh_rc = _maybe_delegate_ssh(raw_argv, args)
    if ssh_rc is not None:
        return ssh_rc
    log_dir_override = str(getattr(args, "log_dir", "") or "").strip()
    set_log_dir_override(log_dir_override or None)

    args._devtool_cli_repo_explicit = bool(str(getattr(args, "repo", "") or "").strip())
    if args._devtool_cli_repo_explicit or args.command not in {"inspect-artifact", "ia", "bootstrap"}:
        args.repo = str(resolve_repository_root(args.repo, args.config))
    args._devtool_cli_target_explicit = bool(str(getattr(args, "target", "") or "").strip())
    args._devtool_cli_solution_explicit = bool(str(getattr(args, "solution", "") or "").strip())
    args._devtool_cli_test_task_explicit = _argv_contains_option(parse_argv, ("--task", "--test-task"))
    args._devtool_cli_pytest_test_explicit = _argv_contains_option(parse_argv, ("--pytest-test",))
    args._devtool_cli_pytest_marker_explicit = _argv_contains_option(parse_argv, ("--pytest-marker",))
    args._devtool_cli_pytest_k_explicit = _argv_contains_option(parse_argv, ("--pytest-k",))
    args._devtool_cli_pytest_arg_explicit = _argv_contains_option(parse_argv, ("--pytest-arg",))
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
            _perform_clipboard_copy(
                prompt_text,
                "AI prompt",
                args,
                log_path=None,
                source="--copy",
            )
        return 0

    if args.command in {"completion"}:
        if args.shell == "zsh":
            print(ZSH_COMPLETION)
            return 0

    if args.command == "completion-state":
        config = _load_command_config(args)
        kind = str(getattr(args, "kind", "all") or "all")
        if str(getattr(args, "format", "lines") or "lines") == "json":
            state = build_completion_state(config, include_pytest=kind in {"pytest-tests", "all"})
            print(json.dumps(state.as_dict(), indent=2, ensure_ascii=False))
        else:
            for value in completion_values(config, kind):
                print(value)
        return 0

    if args.command in {"final-seal", "seal"}:
        repo_root = resolve_repository_root(getattr(args, "repo", None), getattr(args, "config", None))
        target_name = str(getattr(args, "target", "") or "").strip()
        if target_name:
            payload = target_seal_report(repo_root, target_name)
            renderer = render_target_seal_text
        else:
            payload = final_seal_report(repo_root)
            renderer = render_final_seal_text
        if bool(getattr(args, "json", False)):
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(renderer(payload))
        if bool(getattr(args, "strict", False)) and payload.get("status") != "pass":
            return 1
        return 0

    if args.command == "pytest":
        if str(getattr(args, "pytest_command", "") or "") == "profile":
            from .pytest_profile import render_pytest_profile_json, render_pytest_profile_text, run_pytest_profile

            repo_root = resolve_repository_root(getattr(args, "repo", None), getattr(args, "config", None))
            result = run_pytest_profile(
                repo_root,
                getattr(args, "pytest_args", None),
                threshold_seconds=float(getattr(args, "threshold", 2.0) or 2.0),
                top=int(getattr(args, "top", 30) or 30),
                strict_slow=bool(getattr(args, "strict", False)),
            )
            print(render_pytest_profile_json(result) if bool(getattr(args, "json", False)) else render_pytest_profile_text(result))
            return result.returncode
        return 1

    if args.command in {"compatibility", "compat"}:
        if bool(getattr(args, "global_config", False)):
            path = global_config_path()
        else:
            repo_root = resolve_repository_root(getattr(args, "repo", None), getattr(args, "config", None))
            path = find_config(repo_root, getattr(args, "config", None))
        payload = compatibility_report(
            path,
            category=str(getattr(args, "category", "") or ""),
            active_only=bool(getattr(args, "active_only", False)),
        )
        if bool(getattr(args, "json", False)):
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(render_compatibility_text(payload))
        if bool(getattr(args, "strict", False)) and payload.get("config_migration", {}).get("needs_migration"):
            return 1
        return 0

    if args.command in {"logs"}:
        logs_command = str(getattr(args, "logs_command", "") or "latest")
        if logs_command in {"latest"}:
            path = resolve_log_path("latest", use_fzf=False)
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

    if args.command == "bootstrap":
        try:
            destination = parse_bootstrap_destination(str(getattr(args, "destination", "current") or "current"))
            action = str(getattr(args, "bootstrap_command", "") or "")
            if action == "install":
                if bool(getattr(args, "repair", False)) and bool(getattr(args, "upgrade", False)):
                    parser.error("bootstrap install --repair and --upgrade are mutually exclusive")
                payload = install_bootstrap(
                    destination,
                    bootstrap_request_from_args(args),
                    repair=bool(getattr(args, "repair", False)),
                    upgrade=bool(getattr(args, "upgrade", False)),
                    dry_run=bool(getattr(args, "bootstrap_dry_run", False)),
                )
            elif action == "status":
                payload = bootstrap_status(destination)
            elif action == "remove":
                removed = remove_bootstrap_receipt(destination)
                payload = {
                    "ok": True,
                    "destination": destination.canonical,
                    "status": "removed" if removed else "not-registered",
                    "detail": (
                        "bootstrap receipt removed; shared system/toolchain packages were left installed"
                        if removed
                        else "no bootstrap receipt was registered"
                    ),
                    "receipt": "",
                    "resolved": {},
                }
            else:
                parser.error(f"Unknown bootstrap command: {action}")
                return 2
        except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
            if str(getattr(args, "format", "text") or "text") == "json":
                print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False))
            else:
                print(f"Error: {exc}", file=sys.stderr)
            return 2
        if str(getattr(args, "format", "text") or "text") == "json":
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(render_bootstrap_result(payload))
        return 0 if bool(payload.get("ok", False)) else 1

    if args.command == "android":
        from .android.cli import android_capabilities_payload, android_devices_payload, android_device_doctor_payload

        repo = Path(args.repo or Path.cwd()).resolve()
        config = load_project_config(
            str(repo), args.config,
            target_name=str(getattr(args, "target", "") or ""),
            variant_name=str(getattr(args, "variant", "") or ""),
            kind_override=str(getattr(args, "project_type", "auto") or "auto"),
            allow_ambiguous_target=True,
        )
        action = str(getattr(args, "android_command", "") or "")
        provider = str(getattr(args, "provider", "") or "")
        adb_path = str(getattr(args, "adb_path", "") or "")
        output_format = str(getattr(args, "format", "text") or "text")
        if action == "capabilities":
            payload = android_capabilities_payload(config, provider=provider, adb_path=adb_path)
            if output_format == "json":
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                rows = [
                    ("runtime", f"{payload['runtime']['environment']} / {payload['runtime']['arch']}"),
                    ("sdk", payload["sdk"]["root"] or "not discovered"),
                    ("adb_requested", payload["adb_provider_requested"]),
                ]
                for name, tool in payload["tools"].items():
                    status = "ready" if tool.get("available") and tool.get("compatible") is not False else "unavailable"
                    detail = tool.get("path") or tool.get("rejection_reason") or "-"
                    rows.append((f"tool.{name}", f"{status}: {tool.get('provider') or 'none'}: {detail}"))
                render_status_table(rows)
            return 0
        if action == "devices":
            nested = str(getattr(args, "device_action", "list") or "list")
            if nested == "doctor":
                payload = android_device_doctor_payload(config, provider=provider, adb_path=adb_path)
                report = payload["device_doctor"]
                if output_format == "json":
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                else:
                    rows = [
                        ("status", report["status"]), ("code", report["code"]),
                        ("provider", report["provider"]), ("adb", report["adb_path"] or "-"),
                        ("detail", report["detail"]),
                    ]
                    if report.get("termux_usb"):
                        usb = report["termux_usb"]
                        rows.extend([(
                            "termux_usb", str(usb.get("list_status") or "-"),
                        ), ("usb_devices", ", ".join(usb.get("devices") or []) or "none")])
                    render_status_table(rows)
                return 1 if report["status"] == "error" else 0
            payload = android_devices_payload(
                config, provider=provider, adb_path=adb_path, enrich=bool(getattr(args, "enrich", False)),
                policy=str(getattr(args, "policy", "") or ""), serial=str(getattr(args, "serial", "") or ""),
                preferred=str(getattr(args, "preferred", "") or ""), include_unusable=bool(getattr(args, "include_unusable", False)),
            )
            if output_format == "json":
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                rows = [("adb", f"{payload['adb']['provider']}: {payload['adb']['path'] or payload['adb']['rejection_reason']}")]
                if payload.get("error"):
                    rows.append(("error", payload["error"]))
                for item in payload["devices"]:
                    detail = f"{item['state']} {item.get('model') or item.get('product') or ''} {item.get('connection_type') or ''}".strip()
                    rows.append((item["serial"], detail))
                if payload.get("selection"):
                    rows.append(("selected", ", ".join(item["serial"] for item in payload["selection"])))
                if payload.get("selection_error"):
                    rows.append(("selection_error", payload["selection_error"]))
                if not payload["devices"] and not payload.get("error"):
                    rows.append(("devices", "none"))
                render_status_table(rows)
            return 1 if payload.get("error") or payload.get("selection_error") else 0
        if action == "doctor":
            payload = android_device_doctor_payload(config, provider=provider, adb_path=adb_path)
            report = payload["device_doctor"]
            if output_format == "json":
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                rows = [
                    ("device_status", report["status"]), ("device_code", report["code"]),
                    ("provider", report["provider"]), ("adb", report["adb_path"] or "-"),
                    ("detail", report["detail"]),
                ]
                for name, tool in payload["context"]["tools"].items():
                    rows.append((f"tool.{name}", f"{tool.get('provider')}: {tool.get('path') or tool.get('rejection_reason') or '-'}"))
                render_status_table(rows)
            return 1 if report["status"] == "error" else 0
        if action == "signing":
            from .android.cli import android_signing_payload
            signing_action = str(getattr(args, "signing_action", "") or "")
            try:
                payload = android_signing_payload(
                    config, action=signing_action,
                    keystore=str(getattr(args, "keystore", "") or ""), alias=str(getattr(args, "alias", "") or ""),
                    artifact=str(getattr(args, "artifact", "") or ""), other_artifact=str(getattr(args, "other_artifact", "") or ""),
                    destination=str(getattr(args, "destination", "") or ""), source=str(getattr(args, "source", "") or ""),
                    store_password_env=str(getattr(args, "store_password_env", "") or ""), key_password_env=str(getattr(args, "key_password_env", "") or ""),
                    source_password_env=str(getattr(args, "source_password_env", "") or ""), destination_password_env=str(getattr(args, "destination_password_env", "") or ""),
                    overwrite=bool(getattr(args, "overwrite", False)), distinguished_name=str(getattr(args, "dname", "CN=Devtool Android Release") or "CN=Devtool Android Release"),
                )
            except Exception as exc:
                if output_format == "json":
                    print(json.dumps({"schema_version": 1, "ok": False, "error": str(exc)}, indent=2))
                else:
                    print(f"Error: {exc}", file=sys.stderr)
                return 1
            if output_format == "json":
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                rows = [("action", signing_action)]
                identity = payload.get("identity") or (payload.get("verification") or {}).get("identity") or {}
                if identity:
                    rows.extend([("alias", str(identity.get("alias") or "-")), ("sha256", str(identity.get("sha256") or "-")), ("subject", str(identity.get("subject") or "-"))])
                for index, listed_identity in enumerate(payload.get("identities") or [], start=1):
                    rows.extend([
                        (f"alias.{index}", str(listed_identity.get("alias") or "-")),
                        (f"sha256.{index}", str(listed_identity.get("sha256") or "-")),
                        (f"expires.{index}", str(listed_identity.get("valid_until") or "-")),
                    ])
                if "same_identity" in payload:
                    rows.append(("same_identity", str(bool(payload["same_identity"])).lower()))
                verification = payload.get("verification") or {}
                if verification:
                    rows.extend([("status", str(verification.get("status") or "-")), ("detail", str(verification.get("detail") or "-"))])
                render_status_table(rows)
            return 0 if payload.get("ok", True) is not False and (payload.get("verification") or {}).get("status", "passed") != "failed" else 1
        if action in {"install", "uninstall", "launch", "stop", "restart", "clear-data", "shell", "push", "pull", "logcat", "screenshot", "permission"}:
            from .android.cli import android_device_operation_payload
            op_action = str(getattr(args, "permission_action", "") or action) if action == "permission" else action
            try:
                payload = android_device_operation_payload(
                    config, action=op_action, serial=str(getattr(args, "serial", "") or ""), policy=str(getattr(args, "policy", "") or ""),
                    apk=str(getattr(args, "apk", "") or ""), package=str(getattr(args, "package", "") or ""), activity=str(getattr(args, "activity", "") or ""),
                    source=str(getattr(args, "source", "") or ""), destination=str(getattr(args, "destination", "") or ""), permission=str(getattr(args, "permission", "") or ""),
                    shell_args=list(getattr(args, "shell_args", []) or []), replace=not bool(getattr(args, "no_replace", False)), downgrade=bool(getattr(args, "downgrade", False)),
                    keep_data=bool(getattr(args, "keep_data", False)), lines=int(getattr(args, "lines", 400) or 400),
                )
            except Exception as exc:
                if output_format == "json": print(json.dumps({"schema_version": 1, "ok": False, "error": str(exc)}, indent=2))
                else: print(f"Error: {exc}", file=sys.stderr)
                return 1
            if output_format == "json":
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                results = payload.get("results") or ([payload.get("result")] if payload.get("result") else [])
                rows = [("operation", op_action)]
                for item in results:
                    rows.extend([("status", str(item.get("status") or "-")), ("device", str(item.get("serial") or "-"))])
                    if item.get("detail"): rows.append(("detail", str(item["detail"])))
                    if op_action in {"shell", "logcat"} and item.get("stdout"): print(str(item["stdout"]), end="" if str(item["stdout"]).endswith("\n") else "\n")
                render_status_table(rows)
            results = payload.get("results") or ([payload.get("result")] if payload.get("result") else [])
            return 0 if all(bool(item.get("ok", item.get("status") == "passed")) for item in results) else 1
        if action == "release":
            from .android.release import AndroidReleaseOrchestrator
            overrides = {}
            if str(getattr(args, "device_tests", "") or ""): overrides["device_tests"] = str(args.device_tests)
            if str(getattr(args, "previous_apk", "") or ""): overrides["previous_apk"] = str(args.previous_apk)
            if str(getattr(args, "release_package", "") or ""): overrides["package"] = str(args.release_package)
            if str(getattr(args, "release_activity", "") or ""): overrides["activity"] = str(args.release_activity)
            if bool(getattr(args, "no_unit_tests", False)): overrides["unit_tests"] = False
            if bool(getattr(args, "no_lint", False)): overrides["lint"] = False
            if bool(getattr(args, "no_install_smoke", False)): overrides["install_smoke"] = False
            if bool(getattr(args, "no_upgrade_smoke", False)): overrides["upgrade_smoke"] = False
            if bool(getattr(args, "no_verify_signature", False)): overrides["verify_signature"] = False
            if bool(getattr(args, "no_verify_certificate_identity", False)): overrides["verify_certificate_identity"] = False
            try:
                outcome = AndroidReleaseOrchestrator(config).run(overrides=overrides, verbose=bool(getattr(args, "verbose", False)), dry_run=bool(getattr(args, "dry_run", False)), continue_on_error=bool(getattr(args, "continue_on_error", False)), output=(Path(args.evidence_output).expanduser() if str(getattr(args, "evidence_output", "") or "") else None))
            except Exception as exc:
                if output_format == "json": print(json.dumps({"schema_version": 1, "ok": False, "error": str(exc)}, indent=2))
                else: print(f"Error: {exc}", file=sys.stderr)
                return 1
            if output_format == "json":
                print(json.dumps({"ok": outcome.passed, "session_id": outcome.session_id, "evidence_path": outcome.evidence_path, "evidence": outcome.evidence}, indent=2, ensure_ascii=False))
            else:
                render_status_table([("session", outcome.session_id), ("verdict", outcome.evidence.get("verdict", "unknown")), ("evidence", outcome.evidence_path)])
            return 0 if outcome.passed else 1
        if action == "instrumentation":
            from .android.cli import android_instrumentation_payload
            try:
                payload = android_instrumentation_payload(config, classes=list(getattr(args, "instrumentation_classes", []) or []), methods=list(getattr(args, "instrumentation_methods", []) or []), annotations=list(getattr(args, "instrumentation_annotations", []) or []), not_annotations=list(getattr(args, "instrumentation_not_annotations", []) or []), size=str(getattr(args, "instrumentation_size", "") or ""), runner_args={str(item).split("=", 1)[0]: str(item).split("=", 1)[1] for item in list(getattr(args, "instrumentation_runner_args", []) or []) if "=" in str(item)}, skip_build=bool(getattr(args, "skip_build", False)))
            except Exception as exc:
                if output_format == "json": print(json.dumps({"schema_version": 1, "ok": False, "error": str(exc)}, indent=2))
                else: print(f"Error: {exc}", file=sys.stderr)
                return 1
            result = payload["result"]
            if output_format == "json": print(json.dumps(payload, indent=2, ensure_ascii=False))
            else: render_status_table([("status", result.get("status", "-")), ("device", result.get("device_serial") or "-"), ("passed", str(result.get("passed", 0))), ("failed", str(result.get("failed", 0))), ("reason", result.get("reason") or "-")])
            return 0 if result.get("ok") else 1
        if action == "smoke":
            from .android.cli import android_smoke_payload
            try:
                payload = android_smoke_payload(config, gate=str(args.gate), apk=str(args.apk or ""), old_apk=str(args.old_apk or ""), package=str(args.package or ""), activity=str(args.activity or ""), serial=str(args.serial or ""), policy=str(args.policy or ""), expected_cert_sha256=str(args.expected_cert_sha256 or ""))
            except Exception as exc:
                if output_format == "json": print(json.dumps({"schema_version": 1, "ok": False, "error": str(exc)}, indent=2))
                else: print(f"Error: {exc}", file=sys.stderr)
                return 1
            result = payload["result"]
            if output_format == "json": print(json.dumps(payload, indent=2, ensure_ascii=False))
            else: render_status_table([("gate", result.get("gate", "-")), ("status", result.get("status", "-")), ("device", result.get("serial") or "-"), ("package", result.get("package") or "-"), ("detail", result.get("detail") or "-")])
            return 0 if result.get("ok") else 1
        parser.error(f"Unknown android command: {action}")

    if args.command == "instrumentation":
        from .android.cli import android_instrumentation_payload
        repo = Path(args.repo or Path.cwd()).resolve()
        config = load_project_config(str(repo), args.config, target_name=str(getattr(args, "target", "") or ""), variant_name=str(getattr(args, "variant", "") or ""), kind_override=str(getattr(args, "project_type", "auto") or "auto"), allow_ambiguous_target=True)
        try:
            payload = android_instrumentation_payload(config, classes=list(getattr(args, "instrumentation_classes", []) or []), methods=list(getattr(args, "instrumentation_methods", []) or []), annotations=list(getattr(args, "instrumentation_annotations", []) or []), not_annotations=list(getattr(args, "instrumentation_not_annotations", []) or []), size=str(getattr(args, "instrumentation_size", "") or ""), runner_args={str(item).split("=", 1)[0]: str(item).split("=", 1)[1] for item in list(getattr(args, "instrumentation_runner_args", []) or []) if "=" in str(item)}, skip_build=bool(getattr(args, "skip_build", False)))
        except Exception as exc:
            if str(getattr(args, "format", "text")) == "json": print(json.dumps({"schema_version": 1, "ok": False, "error": str(exc)}, indent=2))
            else: print(f"Error: {exc}", file=sys.stderr)
            return 1
        result = payload["result"]
        if str(getattr(args, "format", "text")) == "json": print(json.dumps(payload, indent=2, ensure_ascii=False))
        else: render_status_table([("status", result.get("status", "-")), ("device", result.get("device_serial") or "-"), ("passed", str(result.get("passed", 0))), ("failed", str(result.get("failed", 0))), ("reason", result.get("reason") or "-")])
        return 0 if result.get("ok") else 1

    if args.command == "termux":
        repo = Path(args.repo or Path.cwd()).resolve()
        config = load_project_config(
            str(repo), args.config, variant_name=str(getattr(args, "variant", "") or ""),
            kind_override=str(getattr(args, "project_type", "auto") or "auto"),
        )
        action = str(getattr(args, "termux_command", "") or "")
        output_format = str(getattr(args, "format", "text") or "text")
        if action == "doctor":
            payload = termux_report_payload(
                config,
                explain=bool(getattr(args, "explain", False)),
                proot_distro=str(getattr(args, "distro", "") or ""),
            )
            if output_format == "json":
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                render_status_table([(item["name"], item["value"]) for item in payload["rows"]])
            return 0
        if action == "packages":
            if output_format == "json":
                print(format_package_recommendations_json(config))
            else:
                from .termux_diagnostics import package_recommendation_rows
                render_status_table(package_recommendation_rows(config))
            return 0
        if action == "explain":
            rows = provider_explanation_rows(config)
            if output_format == "json":
                print(json.dumps([{"name": name, "value": value} for name, value in rows], indent=2, ensure_ascii=False))
            else:
                render_status_table(rows)
            return 0
        parser.error(f"Unknown termux command: {action}")

    if args.command in {"init-config", "init"}:
        target = write_template_config(Path(args.repo_root), force=args.force)
        console.print(f"[green]Wrote[/green] {target}")
        return 0

    if args.command == "bundle":
        return _run_bundle_command(args)

    if args.command == "overlay":
        repo = Path(args.repo or Path.cwd()).resolve()
        action = str(getattr(args, "overlay_command", "") or "")
        if action == "check":
            result = check_overlay(
                repo,
                args.artifact,
                include_diff=bool(getattr(args, "diff", False)),
                diff_context=max(0, int(getattr(args, "diff_context", 3) or 3)),
                max_diff_lines=max(100, int(getattr(args, "max_diff_lines", 4000) or 4000)),
                ignore_checksum=bool(getattr(args, "ignore_checksum", False)),
            )
            payload = result.to_dict()
            if bool(getattr(args, "json", False)) or bool(getattr(args, "_devtool_json", False)):
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                rows = [
                    ("artifact", result.artifact),
                    ("status", "ready" if result.ok else "conflict"),
                    ("structural", "ok" if result.structural_ok else "failed"),
                    ("source_state", "ok" if result.source_state_ok else "failed"),
                    ("conflicts", str(len(result.conflicts))),
                    ("rebaseable", "yes" if result.rebaseable else "no"),
                ]
                render_status_table(rows)
                for message in result.messages:
                    console.print(message)
                for conflict in result.conflicts:
                    console.print(f"[yellow]{conflict.kind}[/yellow] {conflict.path}: {conflict.detail}")
                if result.diff:
                    print(result.diff, end="" if result.diff.endswith("\n") else "\n")
            return 0 if result.ok else 1
        if action == "rebase":
            try:
                result = rebase_overlay(repo, args.artifact, output=(args.output or None))
            except Exception as exc:
                if bool(getattr(args, "json", False)) or bool(getattr(args, "_devtool_json", False)):
                    print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False))
                else:
                    print(f"Overlay rebase failed: {exc}", file=sys.stderr)
                return 2
            payload = {"ok": True, **result.to_dict()}
            if bool(getattr(args, "json", False)) or bool(getattr(args, "_devtool_json", False)):
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                render_status_table([("output", result.output), ("changed_entries", str(result.changed_entries)), ("actions_changed", str(result.actions_changed)), ("validation", "passed")])
            return 0

    if args.command in {"plugin", "plugins"}:
        repo = Path(args.repo or Path.cwd()).resolve()
        action = str(getattr(args, "plugin_command", "") or "")
        if action in {"list", "ls"}:
            plugins, errors = discover_plugins()
            payload = {"ok": not errors, "plugins": [item.to_dict() for item in plugins], "load_errors": errors}
            if bool(getattr(args, "json", False)) or bool(getattr(args, "_devtool_json", False)):
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                render_status_table([(item.name, f"{item.version} | {item.source} | {', '.join(item.capabilities) or 'no capabilities'}") for item in plugins])
                for error in errors:
                    console.print(f"[yellow]{error['plugin']}[/yellow]: {error['error']}")
            return 0 if not errors else 1
        if action == "doctor":
            try:
                payload = doctor_plugin(args.name, repo_root=repo, project_type=str(getattr(args, "project_type", "auto") or "auto"))
            except KeyError as exc:
                if bool(getattr(args, "json", False)) or bool(getattr(args, "_devtool_json", False)):
                    print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False))
                else:
                    print(str(exc), file=sys.stderr)
                return 2
            if bool(getattr(args, "json", False)) or bool(getattr(args, "_devtool_json", False)):
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                render_status_table([(item["name"], ("ok" if item["ok"] else "missing") + " | " + item["detail"]) for item in payload["checks"]] or [("plugin", "no doctor checks")])
            return 0 if bool(payload.get("ok")) else 1

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

    if args.command == "artifact":
        repo = Path(args.repo or Path.cwd()).resolve()
        command = str(getattr(args, "artifact_command", "") or "")
        if command in {"list", "ls"}:
            payload = load_registry(repo)
            if bool(getattr(args, "json", False)):
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                items = [item for item in payload.get("items", []) if isinstance(item, dict)]
                rows = [(str(item.get("id") or "-"), f"{item.get('path') or '-'} | {item.get('type') or 'file'} | {item.get('size') or 0} bytes | {'valid' if item.get('valid', True) else 'invalid'}") for item in items]
                render_status_table(rows or [("artifacts", "none registered")])
            return 0
        if command in {"inspect", "show"}:
            record = find_record(repo, args.selector)
            if record is None:
                print(f"Artifact not found in registry: {args.selector}", file=sys.stderr)
                return 1
            if bool(getattr(args, "json", False)):
                print(json.dumps(record, indent=2, ensure_ascii=False))
            else:
                render_status_table([(str(key), json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)) for key, value in record.items()])
            return 0
        if command == "verify":
            result = verify_record(repo, args.selector)
            if bool(getattr(args, "json", False)):
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                render_status_table([(str(key), str(value)) for key, value in result.items() if key in {"id", "path", "exists", "valid", "current_sha256", "verified_at"}])
            return 0 if bool(result.get("valid")) else 1
        if command == "prune":
            raw_age = str(args.older_than or "30d").strip().lower()
            multiplier = 1
            if raw_age.endswith("d"):
                raw_age = raw_age[:-1]
            elif raw_age.endswith("h"):
                raw_age = raw_age[:-1]
                multiplier = 0
            try:
                value = int(raw_age)
            except ValueError:
                parser.error("--older-than must be an integer day count or use a d/h suffix")
            days = float(value) if multiplier else max(0.0, float(value) / 24.0)
            result = prune_registry(repo, older_than_days=days, delete_files=bool(args.delete_files))
            if bool(getattr(args, "json", False)):
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                render_status_table([("removed", str(len(result.get("removed", [])))), ("remaining", str(result.get("remaining", 0))), ("files_deleted", str(sum(1 for item in result.get("removed", []) if item.get("file_deleted"))))])
            return 0

    if args.command == "validation-menu":
        config = _load_command_config(args)
        from .interactive.validation_menu import run_validation_menu
        return run_validation_menu(config, args)

    if args.command in {"ui-contract", "uic"}:
        _ui_contract(
            surface=str(getattr(args, "surface", "machine") or "machine"),
            as_json=bool(getattr(args, "json", False)),
            width=int(getattr(args, "width", 0) or 0),
            height=int(getattr(args, "height", 0) or 0),
            required_capabilities=[str(item) for item in getattr(args, "require_capability", []) if str(item).strip()],
        )
        return 0
    if args.command == "config":
        return _config_command(args)
    if args.command in {"targets", "tg"}:
        config = _load_command_config(args)
        _targets(config, as_json=bool(getattr(args, "json", False)))
        return 0
    if args.command in {"groups", "gr"}:
        config = _load_command_config(args)
        _groups(config, as_json=bool(getattr(args, "json", False)))
        return 0
    if args.command in {"jobs", "jb"}:
        config = _load_command_config(args)
        _jobs(config, as_json=bool(getattr(args, "json", False)))
        return 0
    if args.command in {"workflows", "wf"}:
        config = _load_command_config(args)
        _workflows(config, as_json=bool(getattr(args, "json", False)))
        return 0
    if args.command in {"plan", "pl"}:
        config = _load_command_config(args)
        _apply_runner_cli_options(config, args)
        return _plan(config, args)
    if args.command in {"affected", "aff"}:
        config = _load_command_config(args)
        return _affected(config, args)
    if args.command in {"preflight", "pf"}:
        config = _load_command_config(args)
        return _preflight(config, args)


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
        from .desktop.gtk_control_center import Gtk4ControlCenter

        config = _load_command_config(args)
        center = Gtk4ControlCenter(
            history_limit=int(getattr(args, "history_limit", 25)),
            recent_events=int(getattr(args, "recent_events", 8)),
        )
        backend = str(getattr(args, "backend", "auto") or "auto")
        if backend not in {"auto", "gtk4"}:
            console.print("[red]Legacy Qt Control Center backend was removed; use --backend gtk4.[/red]")
            return 2
        if bool(getattr(args, "once", False)) or backend == "gtk4" or Gtk4ControlCenter.available():
            try:
                return center.run(
                    repository=config.repo_root,
                    config_path=str(config.config_path or "") or None,
                    target=str(getattr(args, "target", "") or ""),
                    variant=str(getattr(args, "variant", "") or ""),
                    state_dir=Path(getattr(args, "state_dir", DEFAULT_TASKER_DIR)).expanduser(),
                    follow_active=bool(getattr(args, "follow_active", False)),
                    once=bool(getattr(args, "once", False)),
                    page=str(getattr(args, "page", "overview") or "overview"),
                    interval_s=float(getattr(args, "poll_interval", 1.0)),
                    startup_test=bool(getattr(args, "startup_test", False)),
                )
            except Exception as exc:
                from .desktop.gtk_runtime import append_gtk_log, redact_text
                log_path = append_gtk_log("control-center", "devtool control-center command failed", exc)
                console.print(f"[red]{redact_text(str(exc))}[/red]")
                console.print(f"Details logged to {log_path}")
                return 2
        console.print("[red]Devtool GTK4 Control Center is not available.[/red]")
        console.print("Install GTK4 dependencies: python3-gi gir1.2-gtk-4.0 gir1.2-adw-1.")
        return 2

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

    if args.command == "tui":
        from .tui import run_tui

        config = _load_command_config(args)
        return run_tui(args, repo_root=config.repo_root, console=console)

    if args.command == "cleanup":
        from .repository_cleanup import cleanup_repository

        config = _load_command_config(args)
        payload = cleanup_repository(
            config.repo_root,
            dry_run=bool(getattr(args, "dry_run", False)),
            prune_history_days=float(getattr(args, "prune_history_days", 0.0) or 0.0),
        )
        archived = int(payload.get("archived_count", 0))
        pruned = int(payload.get("pruned_count", 0))
        prefix = "Would archive" if bool(payload.get("dry_run")) else "Archived"
        console.print(f"{prefix} {archived} historical backup item(s).")
        if float(getattr(args, "prune_history_days", 0.0) or 0.0) > 0:
            prune_prefix = "Would prune" if bool(payload.get("dry_run")) else "Pruned"
            console.print(f"{prune_prefix} {pruned} old backup-history batch(es).")
        if archived and bool(getattr(args, "verbose", False)):
            for item in payload.get("archived", []):
                console.print(f"  {item.get('source')} -> {item.get('destination')}")
        return 0

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

    if args.command in {"doctor", "dr", "status", "st", "inspect", "info", "variants", "release-notes", "tag"} or args.command in {"commit", "ci"}:
        repo = Path(args.repo or Path.cwd()).resolve()
        if args.command in {"commit", "ci"}:
            with _single_run_guard(repo, args):
                _ensure_safe_repo(repo, force=args.force, yes=args.yes, command="commit")
                _ensure_run_id(args)
                log_path = new_log_path()
                write_log(_run_log_start_text(args, None), log_path)
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
                    output_debug_args=args,
                    output_debug_log_path=log_path,
                )
                failed = totals["failed"]
                _write_json_payload(args.machine_file, _machine_json_text)
                _maybe_copy(raw_output, summary_text, json_text, args, log_path=log_path)
                final_exit = 0 if failed == 0 else 1
                _append_run_log_summary(log_path, args, None, totals, summary_text, started_at=started_at, finished_at=finished_at, exit_code=final_exit, results=results)
                return final_exit
        if args.command in {"doctor", "dr", "status", "st", "inspect", "info", "variants"}:
            config = _load_command_config(args)
        else:
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
        if args.command == "variants":
            from .android.variants import list_android_variant_profiles
            profiles = list_android_variant_profiles(config)
            default_name = next((p.name for p in profiles if p.name == (config.variant or "debug")), profiles[0].name if profiles else "debug")
            console.print("Android variants")
            for profile in profiles:
                marker = "*" if profile.name == default_name else " "
                signing = profile.signing_profile or profile.signing
                console.print(f"{marker} {profile.name:<18} build type: {profile.build_type:<10} signing: {signing:<12} deployable: {'yes' if profile.deployable else 'no'}  release gates: {'yes' if profile.release_gates else 'no'}")
            console.print(f"Default: {default_name}")
            return 0
        if args.command == "release-notes":
            print(_release_notes(config.repo_root, args.from_ref, args.to_ref))
            return 0
        if args.command == "tag":
            with _single_run_guard(repo, args):
                _ensure_safe_repo(repo, force=args.force, yes=args.yes, command="tag")
                specs = [CommandSpec("tag", ["git", "tag"] + (["-m", args.message] if args.message else []) + [args.name], destructive=True)]
                _ensure_run_id(args)
                log_path = new_log_path()
                write_log(_run_log_start_text(args, config), log_path)
                started_at = datetime.now(timezone.utc)
                results, raw_output = _run_specs_for_args(config, specs, args, continue_on_error=_continue_on_error_for_args(args), log_path=log_path)
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
                    output_debug_args=args,
                    output_debug_log_path=log_path,
                )
                _write_json_payload(args.machine_file, _machine_json_text)
                _maybe_copy(raw_output, summary_text, json_text, args, log_path=log_path)
                final_exit = 0 if totals["failed"] == 0 else 1
                _append_run_log_summary(log_path, args, config, totals, summary_text, started_at=started_at, finished_at=finished_at, exit_code=final_exit, results=results)
                return final_exit

    config = _load_command_config(args)
    _apply_runner_cli_options(config, args)
    if args.command in {"test", "t"} and bool(getattr(args, "list_tests", False)):
        return _print_test_catalog(config, args, as_json=False)
    continue_on_error = _continue_on_error_for_args(args)
    _ensure_run_id(args)
    log_path = new_log_path(_log_identifier_for_args(args))
    write_log(_run_log_start_text(args, config), log_path)
    _single_run_lock = _acquire_single_run_lock(config, args, log_path=log_path)
    tasker_progress = _live_progress_for_args(args, config)
    if tasker_progress is not None:
        tasker_progress.log_path = str(log_path)

    started_at = datetime.now(timezone.utc)
    if tasker_progress is not None:
        tasker_progress.started()

    cache_cleanup_result: OperationResult | None = None
    space_cleanup_result: OperationResult | None = None
    if args.command in {"clean", "cl"} and bool(getattr(args, "caches", False)):
        cache_cleanup_result = _build_cache_cleanup_result(config, args, dry_run=bool(getattr(args, "dry_run", False)))
    if args.command in {"clean", "cl"} and bool(getattr(args, "space", False)):
        space_cleanup_result = _build_space_cleanup_result(config, args, dry_run=bool(getattr(args, "dry_run", False)))

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
        elif _single_run_lock.allows_transaction(transaction_data):
            if not bool(getattr(args, "quiet", False)):
                console.print(
                    "[yellow]Preserving a disjoint pending validation transaction while this target proceeds.[/yellow]"
                )
            stale_transaction = None
        elif bool(getattr(args, "allow_pending_validation", False)):
            if not bool(getattr(args, "quiet", False)):
                console.print(
                    "[yellow]Proceeding with a pending validation transaction preserved. "
                    "Use `devtool rollback --transaction ...` if it later needs recovery.[/yellow]"
                )
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
                output_debug_args=args, output_debug_log_path=log_path,
            )
            _write_json_payload(args.machine_file, machine_text)
            _finish_live_progress(tasker_progress, totals=totals, results=[pending_result])
            _maybe_copy(pending_result.output, summary_text, json_text, args, log_path=log_path)
            _append_run_log_summary(log_path, args, config, totals, summary_text, started_at=started_at, finished_at=finished_at, exit_code=1, results=results)
            _release_single_run_lock(args)
            return 1

    if stale_transaction is not None and not transaction_pending_validation(stale_transaction):
        try:
            stale_data = read_transaction(stale_transaction)
        except Exception:
            stale_data = {}
        stale_id = str(stale_data.get("transaction_id") or "") if stale_data else ""
        owned_id = str(getattr(_single_run_lock, "owned_transaction_id", "") or "")
        if stale_data and stale_id != owned_id and _single_run_lock.allows_transaction(stale_data):
            if not bool(getattr(args, "quiet", False)):
                console.print(
                    "[yellow]Preserving a disjoint interrupted transaction; it can be recovered independently.[/yellow]"
                )
            stale_transaction = None

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
                output_debug_args=args,
                output_debug_log_path=log_path,
            )
            duration_seconds = sum(r.duration_seconds for r in results)
            _write_json_payload(args.machine_file, _machine_json_text)
            _finish_live_progress(tasker_progress, totals=totals, results=results)
            _maybe_copy(raw_output, summary_text, json_text, args, log_path=log_path)
            if args.command in APPLY_COMMANDS:
                notify_apply_finished(args, config, results, totals)
            _append_run_log_summary(log_path, args, config, totals, summary_text, started_at=started_at, finished_at=finished_at, exit_code=1, results=results)
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
                output_debug_args=args, output_debug_log_path=log_path,
            )
            _write_json_payload(args.machine_file, machine_text)
            _finish_live_progress(tasker_progress, totals=totals, results=[rollback_result])
            _maybe_copy(recovery_output, summary_text, json_text, args, log_path=log_path)
            _append_run_log_summary(log_path, args, config, totals, summary_text, started_at=started_at, finished_at=finished_at, exit_code=1, results=results)
            _release_single_run_lock(args)
            return 1

        stale_recovery_results = [rollback_result]
        stale_recovery_raw = rollback_raw

    if args.command == "batch":
        destructive_batch = any(step.split("=", 1)[0] in {"apply-overlay", "ao", "apply-overlays", "aos", "apply-snapshot", "as", "commit", "ci", "tag"} for step in args.steps)
        if destructive_batch:
            _ensure_safe_repo(config.repo_root, force=args.force, yes=args.yes, command=args.command, ui_config=config.ui_config)

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
                            transaction_path=transaction_path,
                        )
                        if repair_raw:
                            validation_raw = repair_raw
                    results.extend(validation_results)
                    if validation_raw:
                        raw_output += "\n\n" + validation_raw
                    if not _exclusive_validation_task_values(args) and not any(result.returncode != 0 for result in results):
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
                        evidence_line = f"Validation evidence preserved: {evidence_path}"
                        raw_output += "\n\n" + evidence_line
                        _append_run_log(log_path, evidence_line)
                    rollback_result, rollback_raw = _run_transaction_rollback(
                        transaction_path,
                        reason="validation or artifact validation script failed after artifact apply",
                        tasker_progress=tasker_progress,
                        log_path=log_path,
                    )
                    results.append(rollback_result)
                    raw_output += "\n\n" + rollback_raw
                commit_start = len(results)
                results, raw_output = _maybe_commit_after_apply(config, args, results, raw_output, log_path=log_path, tasker_progress=tasker_progress)
                _record_transaction_commit_from_results(
                    transaction_path, config.repo_root, results, start_index=commit_start
                )
                if transaction_path and not any(result.returncode != 0 for result in results):
                    verification = _verify_applied_artifact_effects(config, args)
                    if verification is not None:
                        results.append(verification)
                        raw_output += "\n\n$ " + " ".join(verification.argv) + "\n" + verification.output
                if transaction_path:
                    if any(result.returncode != 0 for result in results):
                        if rollback_enabled and not any(result.name == "apply-rollback" for result in results):
                            evidence_path = preserve_validation_evidence(
                                config.repo_root, results, run_id=str(getattr(args, "_devtool_run_id", "") or "")
                            )
                            if evidence_path:
                                evidence_line = f"Validation evidence preserved: {evidence_path}"
                                raw_output += "\n\n" + evidence_line
                                _append_run_log(log_path, evidence_line)
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
                delete_on_failure = bool(getattr(args, "delete_artifact_on_failure", False))
                if (
                    not getattr(args, "dry_run", False)
                    and not getattr(args, "keep_artifact", False)
                    and (not any(result.returncode != 0 for result in results) or delete_on_failure)
                ):
                    cleanup_result = _delete_original_artifact_after_apply(
                        results,
                        artifact=getattr(args, "artifact", ""),
                        delete_on_failure=delete_on_failure,
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
                early_totals = {"warnings": 0, "errors": 1, "passed": 0, "failed": 1, "skipped": 0}
                _finish_live_progress(tasker_progress, totals=early_totals, results=[])
                early_finished = datetime.now(timezone.utc)
                _append_run_log_summary(log_path, args, config, early_totals, f"Devtool failed.\n\nFailure reason:\n- {exc}", started_at=started_at, finished_at=early_finished, exit_code=exc.exit_code, results=[])
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
                early_totals = {"warnings": 0, "errors": 1, "passed": 0, "failed": 1, "skipped": 0}
                _finish_live_progress(tasker_progress, totals=early_totals, results=[])
                early_finished = datetime.now(timezone.utc)
                _append_run_log_summary(log_path, args, config, early_totals, f"Devtool failed.\n\nFailure reason:\n- {error}", started_at=started_at, finished_at=early_finished, exit_code=error.exit_code, results=[])
                _release_single_run_lock(args)
                return error.exit_code
            results = [OperationResult(
                "validation-finalize", ["devtool", "validation-finalize"], 2, 0.0, str(exc), [],
                failure_kind="validation_session_error",
            )]
            raw_output = str(exc)
    elif args.command in {"release", "rel"}:
        from .android.release import AndroidReleaseOrchestrator
        overrides = {}
        if str(getattr(args, "device_tests", "") or ""): overrides["device_tests"] = str(args.device_tests)
        if str(getattr(args, "previous_apk", "") or ""): overrides["previous_apk"] = str(args.previous_apk)
        if str(getattr(args, "release_package", "") or ""): overrides["package"] = str(args.release_package)
        if str(getattr(args, "release_activity", "") or ""): overrides["activity"] = str(args.release_activity)
        if bool(getattr(args, "no_unit_tests", False)): overrides["unit_tests"] = False
        if bool(getattr(args, "no_lint", False)): overrides["lint"] = False
        if bool(getattr(args, "no_install_smoke", False)): overrides["install_smoke"] = False
        if bool(getattr(args, "no_upgrade_smoke", False)): overrides["upgrade_smoke"] = False
        if bool(getattr(args, "no_verify_signature", False)): overrides["verify_signature"] = False
        if bool(getattr(args, "no_verify_certificate_identity", False)): overrides["verify_certificate_identity"] = False
        try:
            outcome = AndroidReleaseOrchestrator(config).run(
                overrides=overrides, verbose=bool(getattr(args, "verbose", False)), dry_run=bool(getattr(args, "dry_run", False)),
                continue_on_error=continue_on_error, log_path=log_path,
                output=(Path(args.evidence_output).expanduser() if str(getattr(args, "evidence_output", "") or "") else None),
            )
            results = outcome.results
            raw_output = "\n\n".join(result.output for result in results if result.output)
            args._devtool_release_payload = outcome.evidence
        except Exception as exc:
            results = [OperationResult("android-release", ["devtool", "release"], 1, 0.0, str(exc), [], failure_kind="android_release")]
            raw_output = str(exc)
    elif args.command in {"clean", "cl"}:
        specs = build_specs(config, args.command)
        results, raw_output = _run_specs_for_args(config, specs, args, continue_on_error=continue_on_error, log_path=log_path, tasker_progress=tasker_progress)
        cleanup_prefix: list[OperationResult] = []
        cleanup_outputs: list[str] = []
        if space_cleanup_result is not None:
            cleanup_prefix.append(space_cleanup_result)
            cleanup_outputs.append(space_cleanup_result.output)
        if cache_cleanup_result is not None:
            cleanup_prefix.append(cache_cleanup_result)
            cleanup_outputs.append(cache_cleanup_result.output)
        if cleanup_prefix:
            results = [*cleanup_prefix, *results]
            prefix_text = "\n\n".join(cleanup_outputs)
            raw_output = prefix_text + ("\n\n" + raw_output if raw_output else "")
    elif args.command in {"validate", "v", "validate-all", "wine-validate", "wv"}:
        if getattr(args, "watch", False):
            def _run_once() -> int:
                run_log_path = new_log_path()
                write_log(_run_log_start_text(args, config), run_log_path)
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
                    output_debug_args=args,
                    output_debug_log_path=run_log_path,
                )
                _write_json_payload(args.machine_file, run_machine)
                _maybe_copy(run_raw, run_summary, run_json, args, log_path=run_log_path)
                run_exit = 0 if run_totals["failed"] == 0 else 1
                _append_run_log_summary(run_log_path, args, config, run_totals, run_summary, started_at=run_started, finished_at=run_finished, exit_code=run_exit, results=run_results)
                return run_exit
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
                    early_totals = {"warnings": 0, "errors": 1, "passed": 0, "failed": 1, "skipped": 0}
                    _finish_live_progress(tasker_progress, totals=early_totals, results=[])
                    early_finished = datetime.now(timezone.utc)
                    _append_run_log_summary(log_path, args, config, early_totals, f"Devtool failed.\n\nFailure reason:\n- {exc}", started_at=started_at, finished_at=early_finished, exit_code=exc.exit_code, results=[])
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
                    early_totals = {"warnings": 0, "errors": 1, "passed": 0, "failed": 1, "skipped": 0}
                    _finish_live_progress(tasker_progress, totals=early_totals, results=[])
                    early_finished = datetime.now(timezone.utc)
                    _append_run_log_summary(log_path, args, config, early_totals, f"Devtool failed.\n\nFailure reason:\n- {error}", started_at=started_at, finished_at=early_finished, exit_code=error.exit_code, results=[])
                    _release_single_run_lock(args)
                    return error.exit_code
                results = [OperationResult(
                    "validation-session", ["devtool", "resume-validation"], 2, 0.0, str(exc), [],
                    failure_kind="validation_session_error",
                )]
                raw_output = str(exc)
        else:
            specs = _validation_specs_for_args(config, args, parser)
            if getattr(args, "declared_tests", []):
                from .test_declarations import command_specs as _declared_test_specs
                specs.extend(_declared_test_specs(config, getattr(args, "declared_tests", [])))
            results, raw_output = _run_specs_for_args(config, specs, args, continue_on_error=continue_on_error, log_path=log_path, tasker_progress=tasker_progress)
    elif args.command == "batch":
        results, raw_output = _handle_batch(config, args, log_path=log_path, tasker_progress=tasker_progress)
    else:
        planning_config = _config_for_validation_planning(config, args)
        workspace_targets = _workspace_target_names_for_args(planning_config, args, parser)
        if bool(getattr(args, "affected", False)) and bool(getattr(args, "_devtool_affected_empty", False)):
            specs = []
        elif workspace_targets:
            if args.command in {"run", "rn"} and len(workspace_targets) > 1:
                parser.error("run cannot target multiple workspace members because the first long-running process would block later targets")
            specs = build_workspace_specs(planning_config, workspace_targets, args.command)
            for spec in specs:
                metadata = dict(spec.metadata)
                selection_kind, selection_groups = _workspace_selection_metadata(args)
                metadata["workspace_selection"] = selection_kind
                metadata["workspace_groups"] = selection_groups
                affected_selection = getattr(args, "_devtool_affected_selection", None)
                if affected_selection is not None:
                    metadata["affected_changed_paths"] = list(affected_selection.changed_paths)
                    metadata["affected_targets"] = list(affected_selection.targets)
                spec.metadata = metadata
        else:
            if args.command in {"test", "t"} and (getattr(args, "test_names", []) or bool(getattr(args, "all_declared_tests", False))):
                from .test_declarations import command_specs as _declared_test_specs
                specs = _declared_test_specs(planning_config, getattr(args, "test_names", []), all_tests=bool(getattr(args, "all_declared_tests", False)))
            else:
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
        api_counts = build_json_summary(results, repo_root=str(config.repo_root))
        totals = {
            "passed": sum(1 for item in results if item.returncode == 0 and not item.skipped),
            "failed": int(api_counts.get("failed", 0) or 0),
            "skipped": int(api_counts.get("skipped", 0) or 0),
            "warnings": int(api_counts.get("warnings", 0) or 0),
            "errors": int(api_counts.get("errors", 0) or 0),
        }
        api_finished = datetime.now(timezone.utc)
        _finish_live_progress(tasker_progress, totals=totals, results=results)
        _write_performance_report(args, results, started_at=started_at, finished_at=api_finished, config=config)
        api_exit = validation_control_exit_code(validation_api_session)
        api_summary = _summary_text(results)
        _append_run_log_summary(log_path, args, config, totals, api_summary, started_at=started_at, finished_at=api_finished, exit_code=api_exit, results=results)
        _release_single_run_lock(args)
        return api_exit

    finished_at = datetime.now(timezone.utc)
    totals, summary_text, json_text, _machine_json_text = _emit_summary(
        results,
        config,
        as_json=bool(getattr(args, "_devtool_json", False)),
        payload_mode=effective_payload,
        raw_output=raw_output,
        command=args.command,
        started_at=started_at,
        finished_at=finished_at,
        machine_format="pretty",
        output_debug_args=args,
        output_debug_log_path=log_path,
    )
    duration_seconds = sum(r.duration_seconds for r in results)
    _write_json_payload(args.machine_file, _machine_json_text)
    _finish_live_progress(tasker_progress, totals=totals, results=results)
    _maybe_copy(raw_output, summary_text, json_text, args, log_path=log_path)
    if args.command in APPLY_COMMANDS:
        notify_apply_finished(args, config, results, totals)
    _write_performance_report(args, results, started_at=started_at, finished_at=finished_at, config=config)
    final_exit = 0 if totals["failed"] == 0 else 1
    _append_run_log_summary(log_path, args, config, totals, summary_text, started_at=started_at, finished_at=finished_at, exit_code=final_exit, results=results)
    _release_single_run_lock(args)
    return final_exit
