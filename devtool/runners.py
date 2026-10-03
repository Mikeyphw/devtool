from __future__ import annotations

import contextlib
from collections import Counter, defaultdict, deque
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable, Mapping, Sequence


from .artifacts import (
    archive_suffix,
    artifact_inventory_entries,
    artifact_inventory_metadata_payload_paths,
    artifact_preflight_policy,
    artifact_preflight_phases,
    artifact_preflight_write_paths,
    is_skipped_artifact_payload_member,
    _is_skipped_artifact_payload_member,
    _archive_member_is_root_directory,
    is_overlay_bundle,
    open_tar_archive,
    create_overlay_bundle,
    read_artifact_manifest,
    artifact_solution,
    artifact_target,
    resolve_artifact_path,
    sha256_file,
    _safe_rel as _safe_archive_rel,
)
from .artifact_validation import run_artifact_preflight_script
from .config import ensure_artifact_target_config, generate_template
from .console_utils import create_console, is_termux_environment, resolve_cli_path
from .diagnostics import classify_failure, parse_issues
from .dotnet_progress import DotnetProgressTracker, dotnet_progress_plan
from .generic_progress import GenericProgressTracker
from .npm_progress import NpmProgressTracker, npm_progress_plan
from .gradle_progress import GradleProgressTracker
from .execution_presentation import ExecutionPresentationController
from .progress_events import RunnerExecutionAdapter
from .gradle_reports import collect_report_metadata
from .test_adapters import (
    compatibility_counts as _canonical_test_counts,
    is_test_operation as _is_test_operation,
    reconcile_results as _reconcile_test_results,
    result_from_go_report as _test_result_from_go_report,
    result_from_progress_snapshot as _test_result_from_progress_snapshot,
    result_from_stdout as _test_result_from_stdout,
)
from .transactions import create_apply_transaction, rollback_transaction, checkpoint_transaction_applied
from .transaction_worktree import transaction_execution_root
from .logstore import StreamingLogRedactor, finalize_log, redact_sensitive_text, redact_sensitive_value, write_log
from .process_state import preserve_environment_keys
from .models import CommandSpec, Issue, OperationResult, ProjectConfig
from .core.events import NodeDiscovered, NodeFinished, NodeStarted, TaskFinished, TaskStarted, WarningDetected, publish_event
from .core.execution_contract import NodeId, RunId
from .xdg import state_dir
from .project_runners import (
    DotnetRunner as _DotnetRunner,
    GenericCommandRunner as _GenericCommandRunner,
    PythonRunner as _PythonRunner,
    add_no_build_to_dotnet_test_spec as _runner_add_no_build_to_dotnet_test_spec,
    add_no_build_to_dotnet_test_specs as _runner_add_no_build_to_dotnet_test_specs,
    add_no_restore_to_dotnet_build_spec as _runner_add_no_restore_to_dotnet_build_spec,
    get_project_runner as _resolve_project_runner,
    is_dotnet_build_argv as _runner_is_dotnet_build_argv,
    is_dotnet_test_argv as _runner_is_dotnet_test_argv,
)
from .tasker import TaskerProgressSink

console = create_console()


def _ssh_execution_provenance(metadata: Mapping[str, object]) -> dict[str, object]:
    if str(metadata.get("execution_mode") or "") not in {"ssh-command", "ssh-proot-oci"} and not metadata.get("ssh_destination"):
        return {}
    result: dict[str, object] = {
        "transport": "ssh",
        "profile": str(metadata.get("ssh_profile") or ""),
        "destination": str(metadata.get("ssh_destination") or ""),
        "workspace_id": str(metadata.get("ssh_workspace_id") or ""),
        "workspace_instance_id": str(metadata.get("ssh_workspace_instance_id") or ""),
        "workspace_path": str(metadata.get("ssh_workspace_path") or metadata.get("ssh_cwd") or ""),
        "source_sync_id": str(metadata.get("ssh_source_sync_id") or ""),
        "source_revision": dict(metadata.get("ssh_source_revision") or {}) if isinstance(metadata.get("ssh_source_revision"), Mapping) else {},
        "session": str(metadata.get("ssh_session") or ""),
        "return_state": str((metadata.get("ssh_return") or {}).get("state") or "") if isinstance(metadata.get("ssh_return"), Mapping) else "",
        "return_manifest": str(metadata.get("ssh_return_manifest") or ""),
        "phase_node_id": str(metadata.get("oci_phase_node_id") or metadata.get("ssh_phase_node_id") or ""),
        "return_producer_sha256": str(metadata.get("ssh_return_producer_sha256") or ""),
    }
    if str(metadata.get("execution_provider") or "") == "ssh-proot-oci" or str(metadata.get("execution_mode") or "") == "ssh-proot-oci":
        result.update({
            "execution_provider": "ssh-proot-oci",
            "runtime": str(metadata.get("oci_runtime") or "proot-distro-oci"),
            "container": str(metadata.get("oci_container") or ""),
            "image_reference": str(metadata.get("oci_image_reference") or ""),
            "image_digest": str(metadata.get("oci_image_digest") or ""),
            "image_archive_sha256": str(metadata.get("oci_image_archive_sha256") or ""),
            "architecture": str(metadata.get("oci_architecture") or ""),
            "definition_digest": str(metadata.get("oci_definition_digest") or ""),
            "build_context_sha256": str(metadata.get("oci_build_context_sha256") or ""),
            "guest_workspace": str(metadata.get("oci_guest_workspace") or "/workspace"),
            "guest_cache": str(metadata.get("oci_guest_cache") or "/var/cache/devtool"),
        })
    return result


def _execution_provenance(metadata: Mapping[str, object], argv: Sequence[str]) -> dict[str, object]:
    from .federation_transport import federation_transport_provenance
    federation = federation_transport_provenance(metadata)
    ssh = _ssh_execution_provenance(metadata)
    if ssh:
        ssh.update(federation)
        return ssh
    mode = str(metadata.get("execution_mode") or "").strip()
    result: dict[str, object] = {}
    if mode:
        result["transport"] = mode
    environment = str(metadata.get("execution_environment") or "").strip()
    if environment:
        result["execution_environment"] = environment
    if mode == "proot-distro":
        result["distribution"] = str(
            metadata.get("proot_distribution")
            or metadata.get("distribution")
            or metadata.get("distro")
            or ""
        )
    tokens = [str(value) for value in argv]
    if not result.get("transport") and _looks_like_chrootctl_exec(tokens):
        result["transport"] = "chrootctl"
        for index, token in enumerate(tokens[:-1]):
            if token in {"--name", "-n"}:
                result["chroot"] = tokens[index + 1]
                break
    result.update(federation)
    return {key: value for key, value in result.items() if value not in ("", None, {}, [])}


def _attach_test_provenance(payload: dict[str, object], metadata: Mapping[str, object]) -> dict[str, object]:
    provenance = _ssh_execution_provenance(metadata)
    if not provenance:
        return payload
    updated = dict(payload)
    test_metadata = updated.get("metadata")
    test_metadata = dict(test_metadata) if isinstance(test_metadata, Mapping) else {}
    test_metadata["execution_provenance"] = provenance
    updated["metadata"] = test_metadata
    return updated


class _ArtifactPreflightFailure(RuntimeError):
    def __init__(self, result: OperationResult, phase: str) -> None:
        self.result = result
        self.phase = phase
        super().__init__(f"Artifact preflight script failed during {phase} with exit {result.returncode}.")


def _preflight_path_is_declared(path: str, write_paths: Sequence[str]) -> bool:
    normalized = path.strip().replace("\\", "/").strip("/")
    for raw in write_paths:
        root = str(raw).strip().replace("\\", "/").strip("/")
        if root and (normalized == root or normalized.startswith(root + "/")):
            return True
    return False


def _preflight_path_fingerprint(repo_root: Path, path: str) -> tuple[str, int, str]:
    target = repo_root / path
    if not target.exists():
        return ("missing", 0, "")
    if target.is_file():
        return ("file", target.stat().st_size, sha256_file(target).lower())
    if target.is_dir():
        return ("directory", 0, "")
    return ("other", 0, "")


def _preflight_checksum_watch(repo_root: Path, paths: Sequence[str]) -> dict[str, tuple[str, int, str]]:
    watched: dict[str, tuple[str, int, str]] = {}
    for raw in paths:
        path = str(raw).strip().replace("\\", "/").strip("/")
        if path and path not in watched:
            watched[path] = _preflight_path_fingerprint(repo_root, path)
    return watched


def _preflight_undeclared_checksum_changes(
    repo_root: Path,
    before: Mapping[str, tuple[str, int, str]],
    *,
    write_paths: Sequence[str],
) -> list[str]:
    errors: list[str] = []
    for path, prior in before.items():
        current = _preflight_path_fingerprint(repo_root, path)
        if current != prior and not _preflight_path_is_declared(path, write_paths):
            errors.append(f"Preflight changed checksum/state for undeclared write path: {path}.")
    return errors


def _preflight_restored_violation_errors(
    result: OperationResult | None,
    manifest: dict,
    *,
    phase: str,
    checksum_watch_before: Mapping[str, tuple[str, int, str]],
    write_paths: Sequence[str],
) -> list[str]:
    """Preserve precise checksum diagnostics after hook-local restoration.

    LIFE-X01 restores an undeclared write inside the hook engine before control
    returns here.  The legacy checksum checks therefore see a clean repository.
    The hook receipt still records exactly which paths changed, so translate that
    evidence back into the same source/payload diagnostics callers already rely
    on instead of degrading to a generic hook-exit message.
    """
    if result is None:
        return []
    event = result.metadata.get("hook_event") if isinstance(result.metadata, dict) else None
    if not isinstance(event, dict):
        return []
    violations = [
        str(item).strip().replace("\\", "/").strip("/")
        for item in event.get("write_violations", []) or []
        if str(item).strip()
    ]
    if not violations:
        return []
    inventory = {
        str(entry.get("path") or "").strip().replace("\\", "/"): entry
        for entry in artifact_inventory_entries(manifest)
        if str(entry.get("path") or "").strip()
    }
    errors: list[str] = []
    for path in violations:
        if _preflight_path_is_declared(path, write_paths):
            continue
        entry = inventory.get(path)
        if entry is not None:
            action = str(entry.get("action") or "").strip().lower()
            if phase == "before_extract":
                if action in {"modify", "delete"}:
                    errors.append(f"Preflight changed source SHA-256 for undeclared write path: {path}.")
                    continue
                if action == "add" and checksum_watch_before.get(path, ("missing", 0, ""))[0] == "missing":
                    errors.append(f"Preflight created an undeclared inventory add target: {path}.")
                    continue
            elif phase == "after_extract":
                if action == "delete":
                    errors.append(f"Preflight recreated an undeclared deleted path: {path}.")
                    continue
                errors.append(f"Preflight changed payload SHA-256 for undeclared write path: {path}.")
                continue
        if path in checksum_watch_before:
            errors.append(f"Preflight changed checksum/state for undeclared write path: {path}.")
    return errors


def _preflight_checksum_boundary_errors(
    repo_root: Path,
    manifest: dict,
    *,
    phase: str,
    ignore_checksum: bool,
) -> list[str]:
    """Detect checksum-changing hook writes that were not explicitly declared.

    Inventory source hashes are preconditions checked before the hook runs.  A
    before-extract hook may intentionally change one of those files only when
    the path is covered by preflight.write_paths.  Likewise, an after-extract
    hook may intentionally transform an extracted payload only when declared.
    Undeclared checksum changes fail the apply transaction.
    """
    if ignore_checksum:
        return []
    write_paths = artifact_preflight_write_paths(manifest)
    errors: list[str] = []
    apply = manifest.get("apply") if isinstance(manifest.get("apply"), dict) else {}
    conflict_policy = str(apply.get("conflict_policy") or "fail").strip().lower()
    for entry in artifact_inventory_entries(manifest):
        path = str(entry.get("path") or "").strip().replace("\\", "/")
        action = str(entry.get("action") or "").strip().lower()
        if not path or _preflight_path_is_declared(path, write_paths):
            continue
        target = repo_root / path
        if phase == "before_extract":
            if action in {"modify", "delete"}:
                expected = str(entry.get("source_sha256") or "").strip().lower()
                if not target.is_file():
                    errors.append(f"Preflight changed inventory source state for {path}: expected an existing file.")
                elif expected and sha256_file(target).lower() != expected:
                    errors.append(f"Preflight changed source SHA-256 for undeclared write path: {path}.")
            elif action == "add" and target.exists() and conflict_policy == "fail":
                errors.append(f"Preflight created an undeclared inventory add target: {path}.")
            continue

        if phase == "after_extract":
            if action == "delete":
                if target.exists():
                    errors.append(f"Preflight recreated an undeclared deleted path: {path}.")
                continue
            expected = str(entry.get("sha256") or "").strip().lower()
            expected_size = entry.get("size")
            if not target.is_file():
                errors.append(f"Preflight changed extracted payload state for {path}: expected a file.")
                continue
            if expected and sha256_file(target).lower() != expected:
                errors.append(f"Preflight changed payload SHA-256 for undeclared write path: {path}.")
            if isinstance(expected_size, int) and not isinstance(expected_size, bool) and target.stat().st_size != expected_size:
                errors.append(f"Preflight changed payload size for undeclared write path: {path}.")
    return errors


def _preflight_integrity_failure(
    artifact_path: Path,
    *,
    phase: str,
    messages: Sequence[str],
    log_path: Path | str | None,
) -> _ArtifactPreflightFailure:
    output = "\n".join(str(message) for message in messages if str(message).strip())
    issues = [
        Issue("error", "artifact-preflight-integrity", str(artifact_path), "checksum-boundary", message)
        for message in messages
        if str(message).strip()
    ]
    path = str(write_log(output, log_path, append=log_path is not None))
    result = OperationResult(
        "artifact-preflight-integrity",
        ["preflight-integrity", str(artifact_path)],
        1,
        0.0,
        output,
        issues,
        log_path=path,
        failure_kind="preflight_failure",
        metadata={"artifact_path": str(artifact_path), "preflight_phase": phase},
    )
    return _ArtifactPreflightFailure(result, phase)


def _run_required_preflight(
    repo_root: Path,
    artifact_path: Path,
    *,
    phase: str,
    manifest: dict,
    target: str = "",
    log_path: Path | str | None = None,
    ignore_checksum: bool = False,
    checksum_paths: Sequence[str] = (),
    lifecycle_progress: Callable[[str, str, OperationResult | None], None] | None = None,
    render_hook_progress: bool = False,
    stream_hook_output: bool = False,
) -> OperationResult | None:
    if not artifact_preflight_policy(manifest):
        return None
    artifact_sha_before = sha256_file(artifact_path) if artifact_path.is_file() else ""
    write_paths = artifact_preflight_write_paths(manifest)
    checksum_watch_before = _preflight_checksum_watch(repo_root, checksum_paths)
    declared_phases = set(artifact_preflight_phases(manifest))
    if phase not in declared_phases:
        return None
    if lifecycle_progress is not None:
        lifecycle_progress("preflight", phase, None)
    result = run_artifact_preflight_script(
        repo_root,
        artifact_path,
        phase=phase,
        manifest=manifest,
        target=target,
        run_id=str(os.environ.get("DEVTOOL_RUN_ID", "") or ""),
        log_path=log_path,
        ignore_checksum=ignore_checksum,
        render_progress=render_hook_progress,
        stream_output=stream_hook_output,
    )
    if result is not None and lifecycle_progress is not None:
        lifecycle_progress("preflight", phase, result)
    artifact_sha_after = sha256_file(artifact_path) if artifact_path.is_file() else ""
    integrity_errors: list[str] = []
    if not ignore_checksum:
        integrity_errors.extend(
            _preflight_restored_violation_errors(
                result,
                manifest,
                phase=phase,
                checksum_watch_before=checksum_watch_before,
                write_paths=write_paths,
            )
        )
    if not ignore_checksum and artifact_sha_before and artifact_sha_after != artifact_sha_before:
        integrity_errors.append("Preflight modified the artifact archive; artifact checksums are immutable during apply.")
    if not ignore_checksum:
        integrity_errors.extend(
            _preflight_undeclared_checksum_changes(
                repo_root,
                checksum_watch_before,
                write_paths=write_paths,
            )
        )
    integrity_errors.extend(
        _preflight_checksum_boundary_errors(
            repo_root,
            manifest,
            phase=phase,
            ignore_checksum=ignore_checksum,
        )
    )
    if integrity_errors:
        integrity_errors = list(dict.fromkeys(integrity_errors))
        raise _preflight_integrity_failure(
            artifact_path,
            phase=phase,
            messages=integrity_errors,
            log_path=log_path,
        )
    if result is not None and result.returncode != 0:
        raise _ArtifactPreflightFailure(result, phase)
    return result


def _format_report_issue_location(issue: Issue) -> str:
    location = str(issue.file or "").strip()
    if not location or location == "-":
        return ""
    if issue.line is not None:
        return f" ({location}:{issue.line})"
    return f" ({location})"


def _append_report_issue_summary(
    output: str,
    issues: Sequence[Issue],
    *,
    title: str,
    codes: set[str],
    limit: int = 12,
) -> str:
    selected = [
        issue
        for issue in issues
        if issue.severity == "error" and issue.code in codes and str(issue.message or "").strip()
    ]
    if not selected:
        return output
    existing = output or ""
    lines: list[str] = []
    for issue in selected:
        message = str(issue.message or "").strip()
        if message and message in existing:
            continue
        lines.append(f"- {message}{_format_report_issue_location(issue)}")
    if not lines:
        return output
    prefix = output.rstrip() + "\n" if output else ""
    rendered = prefix + f"{title}:\n" + "\n".join(lines[:limit]) + "\n"
    if len(lines) > limit:
        rendered += f"- ... and {len(lines) - limit} more failure(s).\n"
    return rendered


def _append_complete_diagnostic_summary(
    output: str,
    issues: Sequence[Issue],
    *,
    title: str = "Devtool complete diagnostics",
) -> str:
    """Append every structured warning/error without a finding-count cap.

    Child stdout/stderr is already streamed into the authoritative Devtool
    run log. Report parsers such as Android lint XML/SARIF, JUnit, Python
    reports, package checks, and artifact verification can discover
    diagnostics which the child process only summarized.

    Keeping a canonical appendix in OperationResult.output guarantees that
    those complete diagnostics are persisted into the same log and are also
    available to summary/clipboard surfaces.
    """

    selected: list[Issue] = []
    seen: set[
        tuple[str, str, str, int | None, str, str]
    ] = set()

    for issue in issues:
        severity = str(
            issue.severity or ""
        ).strip().lower()

        if severity not in {"warning", "error"}:
            continue

        key = (
            severity,
            str(issue.command or "").strip(),
            str(issue.file or "").strip(),
            issue.line,
            str(issue.code or "").strip(),
            str(issue.message or "").strip(),
        )

        if key in seen:
            continue

        seen.add(key)
        selected.append(issue)

    if not selected:
        return output

    lines: list[str] = []

    for issue in selected:
        severity = str(
            issue.severity or "diagnostic"
        ).strip().upper()

        command = str(
            issue.command or ""
        ).strip()

        location = str(
            issue.file or ""
        ).strip()

        if location in {"", "-"}:
            location = ""
        elif issue.line is not None:
            location = f"{location}:{issue.line}"

        code = str(
            issue.code or ""
        ).strip()

        message = str(
            issue.message or ""
        ).strip()

        prefix_parts = [severity]

        if command:
            prefix_parts.append(
                f"[{command}]"
            )

        detail_parts: list[str] = []

        if location:
            detail_parts.append(location)

        if code:
            detail_parts.append(code)

        detail = ": ".join(detail_parts)
        prefix = " ".join(prefix_parts)

        if detail and message:
            rendered = (
                f"{prefix} {detail}: {message}"
            )
        elif detail:
            rendered = (
                f"{prefix} {detail}"
            )
        else:
            rendered = (
                f"{prefix} {message}"
            ).rstrip()

        lines.append(f"- {rendered}")

    prefix = (
        output.rstrip() + "\n"
        if output
        else ""
    )

    return (
        prefix
        + f"{title} ({len(lines)}):\n"
        + "\n".join(lines)
        + "\n"
    )


def _memory_snapshot() -> tuple[int | None, int | None, int | None, float | None, float | None]:
    """Return MemAvailable, SwapFree, SwapTotal and memory PSI avg10 values."""

    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace").splitlines():
            key, separator, remainder = line.partition(":")
            if not separator or key not in {"MemAvailable", "SwapFree", "SwapTotal"}:
                continue
            values[key] = max(0, int(remainder.strip().split()[0]) // 1024)
    except (OSError, ValueError, IndexError):
        pass

    psi_some: float | None = None
    psi_full: float | None = None
    try:
        for line in Path("/proc/pressure/memory").read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            kind, _, payload = line.partition(" ")
            fields = {
                key: value
                for token in payload.split()
                if "=" in token
                for key, value in [token.split("=", 1)]
            }
            if kind == "some" and "avg10" in fields:
                psi_some = float(fields["avg10"])
            elif kind == "full" and "avg10" in fields:
                psi_full = float(fields["avg10"])
    except (OSError, ValueError):
        pass

    return (
        values.get("MemAvailable"),
        values.get("SwapFree"),
        values.get("SwapTotal"),
        psi_some,
        psi_full,
    )


def _memory_guard_limits(memory_guard_mb: int) -> tuple[int, int]:
    """Return adaptive sustained-pressure and hard-emergency thresholds."""

    sustained = max(320, min(memory_guard_mb - 96, int(memory_guard_mb * 0.70)))
    hard = max(192, min(sustained - 96, int(memory_guard_mb * 0.50)))
    return sustained, hard


def _critical_memory_pressure(
    snapshot: tuple[int | None, int | None, int | None, float | None, float | None],
    history: Sequence[int],
    sustained_limit: int,
) -> tuple[bool, str]:
    available, swap_free, swap_total, psi_some, psi_full = snapshot
    if available is None or available >= sustained_limit:
        return False, ""

    swap_exhausted = False
    if swap_total is not None:
        swap_reserve = max(64, int(swap_total * 0.05))
        swap_exhausted = swap_total <= 0 or (swap_free or 0) <= swap_reserve
    worsening = len(history) >= 8 and (history[0] - history[-1]) >= 64
    psi_stalled = (psi_full is not None and psi_full >= 1.0) or (
        psi_some is not None and psi_some >= 25.0
    )
    telemetry_missing = swap_total is None and psi_some is None and psi_full is None
    critical = (
        (swap_exhausted and (psi_stalled or worsening))
        or (psi_stalled and worsening)
        or (telemetry_missing and worsening)
    )

    signals: list[str] = []
    if swap_total is not None:
        signals.append(f"swap {swap_free or 0}/{swap_total} MiB free")
    if psi_some is not None:
        signals.append(f"PSI some avg10={psi_some:.2f}")
    if psi_full is not None:
        signals.append(f"PSI full avg10={psi_full:.2f}")
    if len(history) >= 2:
        signals.append(f"trend {history[-1] - history[0]:+d} MiB")
    return critical, ", ".join(signals)


def _command_delegates_memory_guard(argv: Sequence[str]) -> bool:
    """Avoid two watchdogs monitoring the same Android builder process."""

    return any(
        value == "--memory-guard-mb" or value.startswith("--memory-guard-mb=")
        for value in argv
    )


def _try_root_cache_reclaim() -> bool:
    """Drop clean page cache only when trusted global policy explicitly permits it."""

    if os.name != "posix":
        return False
    from .host_context import detect_host_context
    from .privilege import (
        LINUX_VM_DROP_CACHES, PrivilegeRequest, load_global_privilege_policy, resolve_privilege_plan,
    )

    request = PrivilegeRequest(
        LINUX_VM_DROP_CACHES,
        reason="memory pressure cache reclaim",
        requested_by="memory-guard",
        interactive=False,
        mutation=True,
        reversible=False,
    )
    try:
        plan = resolve_privilege_plan(
            request,
            policy=load_global_privilege_policy(),
            host=detect_host_context(),
            probe=True,
            allow_action_required=False,
        )
    except (PermissionError, SystemExit):
        return False

    command = [shutil.which("sh") or "/bin/sh", "-c", "sync; echo 1 > /proc/sys/vm/drop_caches"]
    argv = plan.wrap_argv(command)
    try:
        result = subprocess.run(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


_TERMUX_DOTNET_ENV = "DEVTOOL_TERMUX_DOTNET"
_TERMUX_DOTNET_OS_RELEASE = """NAME=Termux
PRETTY_NAME=Termux
ID=termux
VERSION_ID=1
"""
_TERMUX_DOTNET_FALLBACKS = (
    "/data/data/com.termux/files/usr/bin/dotnet",
)


def _resolve_termux_dotnet() -> str | None:
    override = os.environ.get(_TERMUX_DOTNET_ENV, "").strip()
    if override:
        return override

    dotnet_path = shutil.which("dotnet")
    if dotnet_path:
        return dotnet_path

    prefix = os.environ.get("PREFIX", "").strip()
    if prefix:
        candidate = Path(prefix) / "bin" / "dotnet"
        if candidate.exists():
            return str(candidate)

    for fallback in _TERMUX_DOTNET_FALLBACKS:
        if Path(fallback).exists():
            return fallback

    return None


def _termux_proot_etc_dir() -> Path:
    """Return a writable synthetic ``/etc`` tree for Termux dotnet.

    The normal location lives below Termux ``PREFIX`` or Devtool's state
    directory. Tests, stripped environments, and unusual launchers may expose
    a read-only HOME such as ``/usr`` while still explicitly declaring
    ``TERMUX_VERSION``. In that case, fall back to the process temporary
    directory instead of attempting to create ``/usr/var/devtool``.
    """

    candidates: list[Path] = []
    override = os.environ.get("DEVTOOL_TERMUX_PROOT_ROOT", "").strip()
    if override:
        candidates.append(Path(override).expanduser())

    prefix = os.environ.get("PREFIX", "").strip()
    if prefix:
        candidates.append(Path(prefix).expanduser() / "var" / "devtool" / "proot")

    candidates.append(state_dir() / "proot")
    candidates.append(
        Path(tempfile.gettempdir())
        / f"devtool-{getattr(os, 'getuid', lambda: 0)()}"
        / "proot"
    )

    attempted: list[str] = []
    seen: set[str] = set()
    for base in candidates:
        normalized = str(base)
        if normalized in seen:
            continue
        seen.add(normalized)
        etc_dir = base / "etc"
        try:
            etc_dir.mkdir(parents=True, exist_ok=True)
            os_release = etc_dir / "os-release"
            current = (
                os_release.read_text(encoding="utf-8", errors="ignore")
                if os_release.exists()
                else ""
            )
            if current != _TERMUX_DOTNET_OS_RELEASE:
                os_release.write_text(_TERMUX_DOTNET_OS_RELEASE, encoding="utf-8")
            return etc_dir
        except OSError as exc:
            attempted.append(f"{etc_dir}: {exc}")

    details = "; ".join(attempted) or "no candidate directories were available"
    raise RuntimeError(
        "Unable to create a writable Termux proot /etc shim. "
        "Set DEVTOOL_TERMUX_PROOT_ROOT to a writable directory. "
        f"Attempts: {details}"
    )


def _prepare_termux_dotnet(argv: list[str]) -> tuple[list[str], str | None]:
    if not argv or argv[0] != "dotnet" or not is_termux_environment():
        return argv, None

    dotnet_path = _resolve_termux_dotnet()
    if not dotnet_path or not shutil.which("proot"):
        return argv, None

    etc_dir = _termux_proot_etc_dir()
    return ["proot", "-b", f"{etc_dir}:/etc", dotnet_path, *argv[1:]], None



def get_project_runner(config: ProjectConfig):
    """Return the command planner for ``config``.

    Kept in this module as the public compatibility entry point while runner
    implementations live in :mod:`devtool.project_runners`.
    """

    return _resolve_project_runner(config)


def runner_capabilities(config: ProjectConfig):
    capabilities = get_project_runner(config).capabilities_for(config)
    target = config.targets.get(config.selected_target) if config.selected_target else None
    if target is None or not target.workflows:
        return capabilities
    values = capabilities.as_dict()
    for action in target.workflows:
        capability = {
            "wine-validate": "validate",
            "wine-test": "test",
        }.get(action, action)
        if capability in values:
            values[capability] = True
    return type(capabilities)(**values)


def build_specs(config: ProjectConfig, action: str) -> list[CommandSpec]:
    from .workflow_runtime import build_workflow_specs, has_selected_workflow
    from .gradle_ingress import converge_gradle_specs

    if has_selected_workflow(config, action):
        specs = _apply_config_metadata(config, build_workflow_specs(config, action))
        return converge_gradle_specs(config, specs, default_origin="devtool")
    runner = get_project_runner(config)
    if action == "validate":
        specs = runner.build_validation_specs(config)
    elif action == "wine-validate":
        specs = runner.build_validation_specs(config, wine=True)
    else:
        specs = runner.build_specs(config, action)
    annotate = getattr(runner, "apply_environment_metadata", None)
    if callable(annotate):
        specs = annotate(config, specs, action=action)
    specs = _apply_config_metadata(config, specs)
    return converge_gradle_specs(config, specs, default_origin="devtool")


def build_validation_specs(
    config: ProjectConfig,
    *,
    wine: bool = False,
    skip_restore: bool = False,
    skip_build: bool = False,
    skip_test: bool = False,
) -> list[CommandSpec]:
    from .workflow_runtime import build_workflow_specs, has_selected_workflow

    workflow_action = "wine-validate" if wine else "validate"
    from .gradle_ingress import converge_gradle_specs
    if has_selected_workflow(config, workflow_action):
        if skip_restore or skip_build or skip_test:
            raise SystemExit(
                "Validation skip flags cannot rewrite an explicit target workflow. "
                "Select a different workflow/profile or remove --skip-restore/--skip-build/--skip-test."
            )
        specs = _apply_config_metadata(config, build_workflow_specs(config, workflow_action))
        return converge_gradle_specs(config, specs, default_origin="validation")
    specs = _apply_config_metadata(config, get_project_runner(config).build_validation_specs(
        config,
        wine=wine,
        skip_restore=skip_restore,
        skip_build=skip_build,
        skip_test=skip_test,
    ))
    return converge_gradle_specs(config, specs, default_origin="validation")


def _apply_config_metadata(config: ProjectConfig, specs: list[CommandSpec]) -> list[CommandSpec]:
    if not config.variant and not config.selected_target:
        return specs
    for spec in specs:
        metadata = dict(spec.metadata)
        if config.selected_target:
            metadata.setdefault("target", config.selected_target)
        if config.variant:
            metadata.setdefault("variant", config.variant)
            env = dict(spec.env)
            env.setdefault("DEVTOOL_VARIANT", config.variant)
            spec.env = env
        spec.metadata = metadata
    return specs


def _contains_dotnet_cli(argv: Sequence[str]) -> bool:
    return any(Path(str(value)).name == "dotnet" for value in argv)


def _is_dotnet_build_argv(argv: list[str]) -> bool:
    return _runner_is_dotnet_build_argv(argv)


def _add_no_restore_to_dotnet_build_spec(spec: CommandSpec, *, allow_explicit: bool = False) -> CommandSpec:
    return _runner_add_no_restore_to_dotnet_build_spec(spec, allow_explicit=allow_explicit)


def _is_dotnet_test_argv(argv: list[str]) -> bool:
    return _runner_is_dotnet_test_argv(argv)


def _add_no_build_to_dotnet_test_spec(spec: CommandSpec, *, allow_explicit: bool = False) -> CommandSpec:
    return _runner_add_no_build_to_dotnet_test_spec(spec, allow_explicit=allow_explicit)


def add_no_build_to_dotnet_test_specs(specs: list[CommandSpec], *, allow_explicit: bool = True) -> tuple[list[CommandSpec], int]:
    return _runner_add_no_build_to_dotnet_test_specs(specs, allow_explicit=allow_explicit)


def _dotnet_specs(config: ProjectConfig, action: str) -> list[CommandSpec]:
    """Backward-compatible private wrapper for older integrations/tests."""

    return _DotnetRunner().build_specs(config, action)


def _python_specs(config: ProjectConfig, action: str) -> list[CommandSpec]:
    """Backward-compatible private wrapper for older integrations/tests."""

    return _PythonRunner().build_specs(config, action)


def _generic_specs(config: ProjectConfig, action: str) -> list[CommandSpec]:
    """Backward-compatible private wrapper for older integrations/tests."""

    return _GenericCommandRunner().build_specs(config, action)


def _interesting_output_line(line: str) -> bool:
    text = line.strip()
    if not text:
        return False
    return bool(re.search(r"(error|warning|failed|fail|restore|build|test|pack|publish|\d+%)", text, re.IGNORECASE))


_DOTNET_TEST_RESULT_LINE_RE = re.compile(
    r"\b(?P<status>Passed|Failed)!\s+-\s+Failed:\s*(?P<failed>\d+),\s*Passed:\s*(?P<passed>\d+),\s*Skipped:\s*(?P<skipped>\d+),\s*Total:\s*(?P<total>\d+)",
    re.IGNORECASE,
)
_TEST_RUN_SUCCESS_RE = re.compile(r"\bTest Run Successful\b", re.IGNORECASE)
_TEST_RUN_FAILED_RE = re.compile(r"\bTest Run Failed\b", re.IGNORECASE)


def _dotnet_test_completion_status(line: str) -> int | None:
    text = line.strip()
    if not text:
        return None
    match = _DOTNET_TEST_RESULT_LINE_RE.search(text)
    if match:
        return 1 if int(match.group("failed")) > 0 or match.group("status").lower() == "failed" else 0
    if _TEST_RUN_SUCCESS_RE.search(text):
        return 0
    if _TEST_RUN_FAILED_RE.search(text):
        return 1
    return None


def _is_dotnet_test_command(argv: list[str], command_name: str = "") -> bool:
    lowered_name = (command_name or "").strip().lower()
    if lowered_name in {"test", "tests", "wine-test"}:
        return True
    for index, value in enumerate(argv[:-1]):
        if Path(value).name == "dotnet" and str(argv[index + 1]).lower() == "test":
            return True
    return False


def _metadata_flag(metadata: dict, *names: str) -> bool:
    for name in names:
        value = metadata.get(name)
        if isinstance(value, bool):
            if value:
                return True
            continue
        if isinstance(value, (int, float)) and value:
            return True
        if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def _is_dotnet_test_command_sequence(argv: Sequence[str], command_name: str = "") -> bool:
    return _is_dotnet_test_command([str(value) for value in argv], command_name)


def _trust_successful_stdout_diagnostics(argv: Sequence[str], command_name: str, metadata: dict) -> bool:
    """Return true when exit-0 stdout diagnostics may fail a command.

    Process exit status is the authoritative boundary for generic commands.
    Tests and build fixtures can intentionally print compiler, lint, Gradle,
    xUnit, or generic error-looking examples while still succeeding. Devtool
    should not turn captured text into a false failure unless the command
    explicitly opts into fatal stdout diagnostics, or it is a known runner
    whose stdout summary can be authoritative despite a hung or forced process
    boundary. Structured report parsers are handled separately and remain
    authoritative.
    """

    if _metadata_flag(metadata, "trust_stdout_diagnostics", "fail_on_stdout_diagnostics"):
        return True
    # Dotnet/vstest can emit a final failed summary before a hang/forced
    # completion path; retain that narrow stdout contract.
    if _is_dotnet_test_command_sequence(argv, command_name):
        return True
    return False


def _filter_successful_stdout_diagnostics(issues: Sequence[Issue], *, trust_stdout_diagnostics: bool) -> tuple[list[Issue], int]:
    if trust_stdout_diagnostics:
        return list(issues), 0
    kept: list[Issue] = []
    suppressed = 0
    for issue in issues:
        if str(issue.severity or "").lower() in {"warning", "error"}:
            suppressed += 1
            continue
        kept.append(issue)
    return kept, suppressed


def _test_completion_grace_for(argv: list[str], command_name: str) -> float | None:
    if not _is_dotnet_test_command(argv, command_name):
        return None
    raw = os.environ.get("DEVTOOL_TEST_COMPLETION_GRACE", os.environ.get("DEVTOOL_TEST_COMPLETION_GRACE_SECONDS", "")).strip()
    if raw:
        try:
            value = float(raw)
            return value if value > 0 else None
        except ValueError:
            pass
    # Only applies after a final test summary has already printed, so this does
    # not kill legitimately quiet tests that are still running.
    return 30.0


def _idle_timeout_for(argv: list[str], command_name: str) -> float | None:
    """Return a safe idle timeout for commands known to hang after output ends.

    Dotnet/vstest can print all visible summaries but keep a process alive with
    no further output.  That makes the Rich progress bar look complete while
    devtool waits forever.  Keep this narrow by defaulting only test commands to
    an idle timeout; users can override or disable it with environment vars.
    """
    raw = os.environ.get("DEVTOOL_COMMAND_IDLE_TIMEOUT", "").strip()
    if raw:
        try:
            value = float(raw)
            return value if value > 0 else None
        except ValueError:
            pass
    test_raw = os.environ.get("DEVTOOL_TEST_IDLE_TIMEOUT", "").strip()
    if test_raw:
        try:
            value = float(test_raw)
            return value if value > 0 else None
        except ValueError:
            pass
    # Do not auto-kill quiet test commands by default. Some test assemblies are
    # legitimately silent for more than a few seconds, and killing them can turn
    # a passing validation into exit 130. If a local environment really has a
    # dotnet/vstest hang, opt in explicitly with DEVTOOL_TEST_IDLE_TIMEOUT or
    # DEVTOOL_COMMAND_IDLE_TIMEOUT.
    return None



def _command_heartbeat_seconds(env: Mapping[str, str] | dict[str, str]) -> float:
    """Return how often Devtool should print a no-output heartbeat.

    Some toolchains, especially Go compilation and shell validators, can be
    legitimately silent for minutes.  A heartbeat is informational only: it is
    not child output, it does not reset idle timeouts, and it can be disabled
    with DEVTOOL_COMMAND_HEARTBEAT_SECONDS=0.
    """
    raw = str(
        env.get(
            "DEVTOOL_COMMAND_HEARTBEAT_SECONDS",
            env.get("DEVTOOL_NO_OUTPUT_HEARTBEAT_SECONDS", "20"),
        )
        or "20"
    ).strip()
    try:
        value = float(raw)
    except ValueError:
        return 20.0
    return max(0.0, min(value, 3600.0))


def _command_heartbeat_message(command_name: str, argv: Sequence[str], seconds: float, elapsed: float) -> str:
    label = (command_name or "command").strip() or "command"
    executable = Path(str(argv[0])).name if argv else "command"
    return (
        f"Devtool heartbeat: {label} ({executable}) is still running after "
        f"{int(round(elapsed))}s without child output. The process was not stopped; "
        f"set DEVTOOL_COMMAND_HEARTBEAT_SECONDS=0 to disable this heartbeat."
    )

def _looks_like_chrootctl_exec(argv: Sequence[str]) -> bool:
    tokens = [str(part) for part in argv]
    for index, token in enumerate(tokens):
        if Path(token).name != "chrootctl":
            continue
        tail = tokens[index + 1:]
        return "exec" in tail
    return False


def _termux_chroot_stall_hint_seconds(env: Mapping[str, str] | dict[str, str]) -> float:
    raw = str(env.get("DEVTOOL_TERMUX_CHROOT_STALL_HINT_SECONDS", "30") or "30").strip()
    try:
        value = float(raw)
    except ValueError:
        return 30.0
    return max(0.0, min(value, 3600.0))


def _termux_chroot_stall_message(argv: Sequence[str], seconds: float) -> str:
    chroot_name = ""
    tokens = [str(part) for part in argv]
    for index, token in enumerate(tokens):
        if token == "--name" and index + 1 < len(tokens):
            chroot_name = tokens[index + 1]
            break
    label = f"Android chroot executor {chroot_name}".strip() if chroot_name else "Android chroot executor"
    return (
        f"Devtool stall diagnostic: {label} is still running after {int(round(seconds))} seconds "
        "with no child output. The process was not stopped. Use -v for the full executor command/output "
        "or DEVTOOL_TERMUX_CHROOT_STALL_HINT_SECONDS=0 to disable this hint."
    )


def _iter_process_output_lines(
    proc: subprocess.Popen,
    *,
    drain_seconds: float = 0.75,
    idle_timeout: float | None = None,
    completion_idle_grace: float | None = None,
    completion_status_detector: Callable[[str], int | None] | None = None,
    memory_guard_mb: int = 0,
    supervisor_probe: Callable[[], tuple[str, str] | dict | str | None] | None = None,
    heartbeat_callback: Callable[[], None] | None = None,
    stall_warning_seconds: float | None = None,
    stall_warning_message: str = "",
    heartbeat_seconds: float = 0.0,
    heartbeat_message: Callable[[float], str] | None = None,
):
    """Yield child output without waiting forever for pipe EOF.

    This implementation avoids ``TextIOWrapper.readline()`` on a pipe after
    ``select`` because it can still block waiting for a newline.  It reads the
    pipe in non-blocking byte chunks, yields complete lines promptly, and stops
    shortly after the real child exits even if descendants inherited stdout.

    If ``idle_timeout`` is set and the main process stays alive with no output
    for that many seconds, the process group is terminated.  This is used for
    dotnet test/vstest hangs where all summaries have printed but the process
    never exits.
    """
    stdout = proc.stdout
    if stdout is None:
        return
    if os.name != "posix":
        for line in stdout:
            yield line
        return

    import codecs
    import fcntl
    import select

    fd = stdout.fileno()
    old_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, old_flags | os.O_NONBLOCK)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    pending = ""
    last_output_at = time.monotonic()
    completion_seen_at: float | None = None
    completion_assumed_returncode: int | None = None
    memory_guard_pressure_samples = 0
    memory_guard_critical_samples = 0
    memory_guard_hard_samples = 0
    memory_guard_reclaim_attempted = False
    memory_guard_checked_at = 0.0
    supervisor_checked_at = 0.0
    heartbeat_checked_at = 0.0
    stall_warning_emitted = False
    last_heartbeat_at = last_output_at
    memory_guard_history: deque[int] = deque(maxlen=12)
    memory_guard_details = ""
    memory_guard_sustained_limit, memory_guard_hard_limit = _memory_guard_limits(memory_guard_mb)

    def emit_text(text: str):
        nonlocal pending
        pending += text
        # MSBuild/NuGet sometimes redraw progress with carriage returns rather
        # than newlines. Treat standalone CR as a live line boundary while
        # preserving normal CRLF pairs.
        while True:
            newline = pending.find("\n")
            carriage = pending.find("\r")
            candidates = [index for index in (newline, carriage) if index >= 0]
            if not candidates:
                break
            index = min(candidates)
            delimiter = pending[index]
            if delimiter == "\r" and index + 1 >= len(pending):
                # A trailing CR may be the first half of CRLF; wait for one more
                # chunk before deciding whether it is a redraw boundary.
                break
            line = pending[:index]
            if delimiter == "\r" and index + 1 < len(pending) and pending[index + 1] == "\n":
                pending = pending[index + 2:]
            else:
                pending = pending[index + 1:]
            yield line + "\n"

    def emit_and_track(text: str):
        nonlocal completion_seen_at, completion_assumed_returncode
        for line in emit_text(text):
            if completion_status_detector is not None:
                status = completion_status_detector(line)
                if status is not None:
                    completion_seen_at = time.monotonic()
                    # Multiple dotnet test projects can print separate summaries.
                    # Preserve the worst result so a later passing project cannot
                    # erase an earlier failed project when the post-summary hang
                    # guard has to assume an exit status.
                    if completion_assumed_returncode is None:
                        completion_assumed_returncode = int(status)
                    else:
                        completion_assumed_returncode = max(completion_assumed_returncode, int(status))
            yield line

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    chunk = b""
                if chunk:
                    last_output_at = time.monotonic()
                    last_heartbeat_at = last_output_at
                    for line in emit_and_track(decoder.decode(chunk)):
                        yield line
                    continue
                if proc.poll() is not None:
                    break

            if proc.poll() is not None:
                deadline = time.monotonic() + max(0.0, drain_seconds)
                while time.monotonic() < deadline:
                    ready, _, _ = select.select([fd], [], [], 0.05)
                    if not ready:
                        break
                    try:
                        chunk = os.read(fd, 65536)
                    except BlockingIOError:
                        chunk = b""
                    if not chunk:
                        break
                    for line in emit_and_track(decoder.decode(chunk)):
                        yield line
                break

            now_monotonic = time.monotonic()
            if heartbeat_seconds > 0 and (now_monotonic - last_output_at) >= heartbeat_seconds and (now_monotonic - last_heartbeat_at) >= heartbeat_seconds:
                last_heartbeat_at = now_monotonic
                elapsed = now_monotonic - last_output_at
                if heartbeat_message is not None:
                    message_text = heartbeat_message(elapsed)
                else:
                    message_text = f"Devtool heartbeat: child process is still running after {int(round(elapsed))}s without output."
                message = "\n" + message_text.rstrip() + "\n"
                for line in emit_and_track(message):
                    yield line

            if (
                stall_warning_seconds is not None
                and stall_warning_seconds > 0
                and not stall_warning_emitted
                and (now_monotonic - last_output_at) >= stall_warning_seconds
            ):
                stall_warning_emitted = True
                message = "\n" + (stall_warning_message or f"Devtool stall diagnostic: no child output for {stall_warning_seconds:g} seconds; process continues.") + "\n"
                for line in emit_and_track(message):
                    yield line
            if heartbeat_callback is not None and (now_monotonic - heartbeat_checked_at) >= 1.0:
                heartbeat_checked_at = now_monotonic
                try:
                    heartbeat_callback()
                except Exception:
                    pass
            if supervisor_probe is not None and (now_monotonic - supervisor_checked_at) >= 1.0:
                supervisor_checked_at = now_monotonic
                event = None
                try:
                    event = supervisor_probe()
                except Exception as exc:
                    event = {"kind": "environment", "reason": f"supervisor probe failed: {type(exc).__name__}: {exc}"}
                kind = ""
                reason = ""
                if isinstance(event, dict):
                    kind = str(event.get("kind") or "").strip().lower()
                    reason = str(event.get("reason") or "").strip()
                elif isinstance(event, tuple) and len(event) >= 2:
                    kind = str(event[0] or "").strip().lower()
                    reason = str(event[1] or "").strip()
                elif isinstance(event, str) and event.strip():
                    kind = "resource"
                    reason = event.strip()
                if kind:
                    assumed_codes = {"timeout": 124, "interrupted": 130, "resource": 75, "environment": 76}
                    setattr(proc, "_devtool_assumed_returncode", assumed_codes.get(kind, 76))
                    setattr(proc, "_devtool_supervisor_event", {"kind": kind, "reason": reason})
                    message = f"\nDevtool validation supervisor stopped process: {kind}: {reason or 'policy requested stop'}.\n"
                    for line in emit_and_track(message):
                        yield line
                    try:
                        os.killpg(proc.pid, 15)
                    except Exception:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                    try:
                        proc.wait(timeout=3.0)
                    except Exception:
                        try:
                            os.killpg(proc.pid, 9)
                        except Exception:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                    break

            if memory_guard_mb > 0 and (time.monotonic() - memory_guard_checked_at) >= 1.0:
                memory_guard_checked_at = time.monotonic()
                snapshot = _memory_snapshot()
                available = snapshot[0]
                if available is not None:
                    memory_guard_history.append(available)
                memory_guard_pressure_samples = (
                    memory_guard_pressure_samples + 1
                    if available is not None and available < memory_guard_mb
                    else 0
                )
                critical, memory_guard_details = _critical_memory_pressure(
                    snapshot, tuple(memory_guard_history), memory_guard_sustained_limit
                )
                memory_guard_critical_samples = (
                    memory_guard_critical_samples + 1 if critical else 0
                )
                memory_guard_hard_samples = (
                    memory_guard_hard_samples + 1
                    if available is not None and available < memory_guard_hard_limit
                    else 0
                )
                if memory_guard_pressure_samples >= 4 and not memory_guard_reclaim_attempted:
                    memory_guard_reclaim_attempted = True
                    before = available
                    if _try_root_cache_reclaim():
                        time.sleep(0.25)
                        snapshot = _memory_snapshot()
                        available = snapshot[0]
                        message = (
                            "\nDevtool used root once to reclaim clean filesystem cache under "
                            f"memory pressure ({before} MiB before; {available} MiB after).\n"
                        )
                        for line in emit_and_track(message):
                            yield line
                        if available is not None:
                            memory_guard_history.append(available)
                        if available is not None and available >= memory_guard_mb:
                            memory_guard_pressure_samples = 0
                            memory_guard_critical_samples = 0
                            memory_guard_hard_samples = 0
                        elif available is not None and available >= memory_guard_sustained_limit:
                            memory_guard_critical_samples = 0
                            memory_guard_hard_samples = 0
                if memory_guard_hard_samples >= 3 or memory_guard_critical_samples >= 10:
                    setattr(proc, "_devtool_assumed_returncode", 75)
                    reason = (
                        "hard emergency floor"
                        if memory_guard_hard_samples >= 3
                        else "corroborated sustained memory pressure"
                    )
                    detail_suffix = f"; {memory_guard_details}" if memory_guard_details else ""
                    message = (
                        f"\nDevtool stopped validation after {reason}: {available} MiB available; "
                        f"configured warning guard {memory_guard_mb} MiB; adaptive sustained threshold "
                        f"{memory_guard_sustained_limit} MiB; hard floor "
                        f"{memory_guard_hard_limit} MiB{detail_suffix}.\n"
                    )
                    for line in emit_and_track(message):
                        yield line
                    try:
                        os.killpg(proc.pid, 15)
                    except Exception:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                    try:
                        proc.wait(timeout=3.0)
                    except Exception:
                        try:
                            os.killpg(proc.pid, 9)
                        except Exception:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                    break

            if (
                completion_idle_grace is not None
                and completion_idle_grace > 0
                and completion_seen_at is not None
                and (time.monotonic() - last_output_at) >= completion_idle_grace
            ):
                assumed = 1 if completion_assumed_returncode is None else completion_assumed_returncode
                setattr(proc, "_devtool_assumed_returncode", assumed)
                message = (
                    f"\nDevtool stopped a quiet test process {completion_idle_grace:g}s after the final test summary. "
                    f"Assuming exit code {assumed}.\n"
                )
                for line in emit_and_track(message):
                    yield line
                try:
                    os.killpg(proc.pid, 15)
                except Exception:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    try:
                        os.killpg(proc.pid, 9)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                break

            if idle_timeout is not None and idle_timeout > 0 and (time.monotonic() - last_output_at) >= idle_timeout:
                setattr(proc, "_devtool_assumed_returncode", 124)
                message = f"\nDevtool stopped a quiet child process after {idle_timeout:g}s without output.\n"
                for line in emit_and_track(message):
                    yield line
                try:
                    os.killpg(proc.pid, 15)
                except Exception:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    try:
                        os.killpg(proc.pid, 9)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                break

        tail = decoder.decode(b"", final=True)
        if tail:
            for line in emit_and_track(tail):
                yield line
        if pending:
            yield pending
    finally:
        try:
            fcntl.fcntl(fd, fcntl.F_SETFL, old_flags)
        except Exception:
            pass


def _wait_process_returncode(proc: subprocess.Popen, *, timeout: float = 5.0) -> int:
    assumed = getattr(proc, "_devtool_assumed_returncode", None)
    try:
        code = proc.wait(timeout=timeout)
        return int(assumed) if assumed is not None else code
    except subprocess.TimeoutExpired:
        # Once Devtool itself decides the child exceeded the wait deadline, the
        # supervisory timeout is authoritative even if the child traps TERM and
        # exits zero. Record that status before sending any termination signal.
        if assumed is None:
            assumed = 124
            setattr(proc, "_devtool_assumed_returncode", assumed)
        try:
            if os.name == "posix":
                os.killpg(proc.pid, 15)
            else:
                proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=2.0)
            return int(assumed)
        except Exception:
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, 9)
                else:
                    proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=1.0)
            except Exception:
                pass
            return int(assumed)

def _run_process_live(
    argv: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    verbose: bool,
    command_name: str = "",
    tasker_progress: TaskerProgressSink | None = None,
    phase_index: int | None = None,
    phase_total: int | None = None,
    progress_argv: list[str] | None = None,
    progress_cwd: str | None = None,
    display_target: str = "",
    target_phase_index: int | None = None,
    target_phase_total: int | None = None,
    progress_parser: str = "",
    requested_gradle_tasks: list[str] | tuple[str, ...] | None = None,
    live_log_path: Path | str | None = None,
    live_log_header: str = "",
    timeout_seconds: float | None = None,
    memory_guard_mb: int = 0,
    supervisor_probe: Callable[[], tuple[str, str] | dict | str | None] | None = None,
    heartbeat_callback: Callable[[], None] | None = None,
    progress_callback: Callable[[dict], None] | None = None,
    structured_state: dict[str, object] | None = None,
    execution_adapter: RunnerExecutionAdapter | None = None,
    presentation_mode: str = "",
) -> tuple[int, str]:
    live_log_handle = None
    if live_log_path is not None:
        try:
            if live_log_header:
                write_log(live_log_header, live_log_path, append=True)
            live_log_handle = Path(live_log_path).open("a", encoding="utf-8")
        except OSError:
            live_log_handle = None

    def _append_live_log(line: str) -> None:
        if live_log_handle is None:
            return
        try:
            live_log_handle.write(live_log_redactor.redact(line))
            live_log_handle.flush()
        except OSError:
            pass

    live_log_redactor = StreamingLogRedactor()
    live_log_finalized = False

    def _close_live_log() -> None:
        nonlocal live_log_finalized
        if live_log_handle is not None:
            with contextlib.suppress(Exception):
                live_log_handle.flush()
                os.fsync(live_log_handle.fileno())
                live_log_handle.close()
            if live_log_path is not None and not live_log_finalized:
                live_log_finalized = True
                with contextlib.suppress(Exception):
                    finalize_log(live_log_path)

    parser_argv = list(progress_argv or argv)
    parser_cwd = progress_cwd or cwd
    tracker = None
    presentation: ExecutionPresentationController | None = None
    # Gradle/chroot startup can be quiet for several seconds. Create its semantic
    # tracker before spawning the child; presentation ownership is attached by
    # the controller after process creation and never lives in the parser.
    if (not verbose or tasker_progress is not None) and progress_parser == "gradle":
        tracker = GradleProgressTracker(
            parser_argv,
            parser_cwd,
            command_name,
            console,
            phase_index=phase_index,
            phase_total=phase_total,
            display_target=display_target,
            target_phase_index=target_phase_index,
            target_phase_total=target_phase_total,
            requested_tasks=tuple(requested_gradle_tasks or ()),
        )
        if verbose:
            tracker.enabled = False

    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        if tracker is not None:
            tracker.__exit__(None, None, None)
        # Keep live streaming as the normal path, but return a structured failure
        # instead of crashing when the executable is unavailable.  This also keeps
        # older tests/wrappers that monkeypatch subprocess.run working.
        try:
            fallback_kwargs = {
                "cwd": cwd,
                "env": env,
                "text": True,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "check": False,
            }
            if timeout_seconds is not None:
                fallback_kwargs["timeout"] = timeout_seconds
            completed = subprocess.run(argv, **fallback_kwargs)
            output = completed.stdout or ""
            if live_log_handle is not None and output:
                _append_live_log(output)
            if verbose and output:
                print(output, end="", flush=True)
            _close_live_log()
            return completed.returncode, output
        except subprocess.TimeoutExpired as timeout_exc:
            captured = timeout_exc.stdout or ""
            if isinstance(captured, bytes):
                captured = captured.decode("utf-8", "replace")
            output = str(captured)
            timeout_value = float(timeout_seconds or 0.0)
            timeout_message = f"\nDevtool command timeout: exceeded configured timeout of {timeout_value:g}s.\n"
            output += timeout_message
            _append_live_log(output)
            if verbose:
                print(output, end="", flush=True)
            _close_live_log()
            return 124, output
        except OSError:
            _append_live_log(str(exc))
            _close_live_log()
            return 127, str(exc)

    if execution_adapter is not None:
        try:
            process_id = execution_adapter.process_started(pid=int(proc.pid), argv=argv, cwd=cwd)
            if structured_state is not None:
                structured_state["execution_process_id"] = process_id
                structured_state["execution_pid"] = int(proc.pid)
        except Exception:
            # Execution telemetry must never make the child process itself fail.
            pass

    output_parts: list[str] = []
    go_test_stream = None
    if progress_parser == "go-test-json":
        from .go_test import GoTestJSONStream
        go_test_stream = GoTestJSONStream()

    def _render_process_line(raw_line: str) -> str:
        if go_test_stream is None:
            return raw_line
        return go_test_stream.feed(raw_line)

    def _finalize_structured_state() -> None:
        if structured_state is None:
            return
        if go_test_stream is not None:
            structured_state["go_test_report"] = go_test_stream.report()
        if isinstance(tracker, GradleProgressTracker):
            try:
                structured_state["gradle_progress"] = dict(tracker.snapshot())
            except Exception:
                pass

    assert proc.stdout is not None
    if not verbose or tasker_progress is not None:
        if progress_parser == "gradle":
            if tracker is None:
                tracker = GradleProgressTracker(
                    parser_argv,
                    parser_cwd,
                    command_name,
                    console,
                    phase_index=phase_index,
                    phase_total=phase_total,
                    display_target=display_target,
                    target_phase_index=target_phase_index,
                    target_phase_total=target_phase_total,
                    requested_tasks=tuple(requested_gradle_tasks or ()),
                )
                tracker.mark_process_started()
        elif dotnet_progress_plan(parser_argv, parser_cwd):
            tracker = DotnetProgressTracker(
                parser_argv,
                parser_cwd,
                command_name,
                console,
                phase_index=phase_index,
                phase_total=phase_total,
                display_target=display_target,
                target_phase_index=target_phase_index,
                target_phase_total=target_phase_total,
            )
        elif progress_parser == "npm" or npm_progress_plan(parser_argv, parser_cwd):
            tracker = NpmProgressTracker(
                parser_argv,
                parser_cwd,
                command_name,
                console,
                phase_index=phase_index,
                phase_total=phase_total,
                display_target=display_target,
                target_phase_index=target_phase_index,
                target_phase_total=target_phase_total,
            )
        else:
            tracker = GenericProgressTracker(
                parser_argv,
                parser_cwd,
                command_name,
                console,
                phase_index=phase_index,
                phase_total=phase_total,
                display_target=display_target,
                target_phase_index=target_phase_index,
                target_phase_total=target_phase_total,
            )
    if tracker is not None and execution_adapter is not None:
        if isinstance(tracker, GradleProgressTracker):
            tracker.structured_event_callback = execution_adapter.gradle_event
        elif isinstance(tracker, NpmProgressTracker):
            if tracker.plan is not None:
                execution_adapter.register_npm_plan(tracker.plan.steps)
            tracker.script_event_callback = execution_adapter.npm_script_event
        elif isinstance(tracker, DotnetProgressTracker):
            tracker.unit_event_callback = execution_adapter.dotnet_unit_event

    if tracker is not None and verbose:
        # In verbose mode stdout is already streamed as raw text. Keep semantic
        # parsing active while the presentation controller deliberately selects
        # the plain/non-owning surface.
        tracker.enabled = False
    if tracker is not None:
        # Tasker/HUD publication is a parallel projection of canonical execution
        # truth, not ownership of the local terminal.  Normal interactive TTY
        # execution must still select the Textual presenter while Tasker/HUD
        # consumers receive the same semantic snapshots out-of-band.
        presentation_env = dict(env)
        requested_presentation = str(presentation_mode or "").strip().lower()
        if requested_presentation in {"plain", "textual"}:
            presentation_env["DEVTOOL_EXECUTION_PRESENTATION"] = requested_presentation
        presentation = ExecutionPresentationController(
            console,
            verbose=verbose,
            external_surface=False,
            env=presentation_env,
        )
    if tracker is not None and (presentation is not None or tasker_progress is not None or progress_callback is not None or execution_adapter is not None):
        def _progress_update(snapshot: dict) -> None:
            if presentation is not None:
                presentation.update(snapshot)
            if execution_adapter is not None:
                try:
                    execution_adapter.progress_snapshot(dict(snapshot))
                except Exception:
                    pass
            if tasker_progress is not None:
                phase = tasker_progress.current_phase or {}
                display_target_value = str(phase.get("target") or phase.get("workspace") or snapshot.get("target") or "")
                tasker_progress.progress(
                    detail=str(snapshot.get("detail") or ""),
                    completed=snapshot.get("completed"),
                    total=snapshot.get("total"),
                    percent=snapshot.get("percent"),
                    target=display_target_value,
                    warnings=snapshot.get("warnings"),
                    errors=snapshot.get("errors"),
                    metrics={
                        key: snapshot.get(key)
                        for key in (
                            "command", "unit_label", "tests_passed", "tests_failed",
                            "tests_skipped", "tests_total", "test_assemblies",
                            "current_task", "current_test",
                            "requested_tasks", "requested_task_count", "dependency_task_count",
                            "requested_duplicate_count", "requested_duplicate_tasks",
                            "task_execution_count", "task_completed_count", "task_resume_count",
                            "task_resume_counts", "task_header_count", "task_header_counts",
                            "task_outcomes", "duplicate_execution_start_count",
                            "duplicate_terminal_header_count",
                        )
                        if snapshot.get(key) is not None
                    },
                    force=bool(snapshot.get("force", False)),
                )
            if progress_callback is not None:
                try:
                    progress_callback(dict(snapshot))
                except Exception:
                    pass
        tracker.update_callback = _progress_update
    if tracker is not None and presentation is not None:
        presentation.start(tracker.snapshot())
        if isinstance(tracker, GradleProgressTracker):
            tracker.mark_process_started()
    interrupted_by_devtool = threading.Event()
    timed_out_by_devtool = threading.Event()

    def _terminate_process() -> None:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, 2)
            else:
                proc.terminate()
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, 9)
                else:
                    proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                pass

    def _interrupt_process() -> None:
        interrupted_by_devtool.set()
        _terminate_process()

    if presentation is not None:
        presentation.set_stop_callback(_interrupt_process)

    cancel_watch_stop = threading.Event()
    cancel_watcher: threading.Thread | None = None
    timeout_watch_stop = threading.Event()
    timeout_watcher: threading.Thread | None = None
    if timeout_seconds is not None and timeout_seconds > 0:
        def _watch_timeout() -> None:
            if timeout_watch_stop.wait(timeout_seconds):
                return
            if proc.poll() is not None:
                return
            timed_out_by_devtool.set()
            setattr(proc, "_devtool_assumed_returncode", 124)
            _terminate_process()
        timeout_watcher = threading.Thread(target=_watch_timeout, name=f"devtool-timeout-{proc.pid}", daemon=True)
        timeout_watcher.start()
    if tasker_progress is not None:
        def _watch_cancel() -> None:
            while not cancel_watch_stop.wait(0.1):
                if proc.poll() is not None:
                    return
                if tasker_progress.cancel_requested():
                    _interrupt_process()
                    return
        cancel_watcher = threading.Thread(target=_watch_cancel, name=f"devtool-cancel-{proc.pid}", daemon=True)
        cancel_watcher.start()

    try:
        idle_timeout = _idle_timeout_for(parser_argv, command_name)
        completion_idle_grace = _test_completion_grace_for(parser_argv, command_name)
        stall_warning_seconds = None
        stall_warning_message = ""
        if progress_parser == "gradle":
            raw_stall = str(env.get("DEVTOOL_GRADLE_STALL_WARNING_SECONDS", "180") or "180").strip()
            try:
                stall_warning_seconds = max(0.0, float(raw_stall))
            except ValueError:
                stall_warning_seconds = 180.0
            if stall_warning_seconds > 0:
                from .termux_runtime import gradle_stall_message
                stall_warning_message = gradle_stall_message(stall_warning_seconds)
        if _looks_like_chrootctl_exec(argv):
            chroot_stall_seconds = _termux_chroot_stall_hint_seconds(env)
            if chroot_stall_seconds > 0 and (stall_warning_seconds is None or chroot_stall_seconds < stall_warning_seconds):
                stall_warning_seconds = chroot_stall_seconds
                stall_warning_message = _termux_chroot_stall_message(argv, chroot_stall_seconds)
        heartbeat_seconds = _command_heartbeat_seconds(env)
        def _heartbeat_message(elapsed: float) -> str:
            return _command_heartbeat_message(command_name, argv, heartbeat_seconds, elapsed)

        completion_detector = _dotnet_test_completion_status if completion_idle_grace is not None else None
        if tracker is None:
            for raw_line in _iter_process_output_lines(
                proc,
                idle_timeout=idle_timeout,
                completion_idle_grace=completion_idle_grace,
                completion_status_detector=completion_detector,
                memory_guard_mb=memory_guard_mb,
                supervisor_probe=supervisor_probe,
                heartbeat_callback=heartbeat_callback,
                stall_warning_seconds=stall_warning_seconds,
                stall_warning_message=stall_warning_message,
                heartbeat_seconds=heartbeat_seconds,
                heartbeat_message=_heartbeat_message,
            ):
                line = _render_process_line(raw_line)
                if not line:
                    continue
                output_parts.append(line)
                _append_live_log(line)
                print(line, end="", flush=True)
            code = _wait_process_returncode(proc)
            if timed_out_by_devtool.is_set():
                timeout_message = f"\nDevtool command timeout: exceeded configured timeout of {timeout_seconds:g}s.\n"
                output_parts.append(timeout_message)
                _append_live_log(timeout_message)
                print(timeout_message, end="", flush=True)
                code = 124
            elif interrupted_by_devtool.is_set() or (tasker_progress is not None and tasker_progress.cancel_requested()):
                code = 130
            _finalize_structured_state()
            return code, "".join(output_parts)

        with tracker:
            for raw_line in _iter_process_output_lines(
                proc,
                idle_timeout=idle_timeout,
                completion_idle_grace=completion_idle_grace,
                completion_status_detector=completion_detector,
                memory_guard_mb=memory_guard_mb,
                supervisor_probe=supervisor_probe,
                heartbeat_callback=heartbeat_callback,
                stall_warning_seconds=stall_warning_seconds,
                stall_warning_message=stall_warning_message,
                heartbeat_seconds=heartbeat_seconds,
                heartbeat_message=_heartbeat_message,
            ):
                line = _render_process_line(raw_line)
                if not line:
                    continue
                output_parts.append(line)
                _append_live_log(line)
                if presentation is not None:
                    presentation.output_line(line)
                surface_decision = tracker.update_from_line(line)
                # Existing progress trackers return None. Only the Gradle tracker
                # opts into line collapsing by returning an explicit boolean.
                surface_line = True if surface_decision is None else bool(surface_decision)
                # Non-verbose mode intentionally keeps the complete child stream
                # in captured output/evidence, but collapses Gradle's repeated
                # bare task headers in the human-facing stream. With --verbose,
                # users explicitly requested the raw stream, so nothing is hidden.
                if verbose or ((presentation is None or not presentation.hides_raw_output) and surface_line):
                    # Non-interactive/CI/redirected output remains observable.
                    # Only the presentation controller may suppress the raw TTY
                    # stream, and only while its Textual surface is active.
                    print(line, end="", flush=True)
            returncode = _wait_process_returncode(proc)
            has_task_telemetry = getattr(tracker, "has_task_telemetry", None)
            task_telemetry_line = getattr(tracker, "task_telemetry_line", None)
            if callable(has_task_telemetry) and callable(task_telemetry_line) and has_task_telemetry():
                telemetry_line = task_telemetry_line() + "\n"
                _append_live_log(telemetry_line)
                if presentation is not None:
                    presentation.output_line(telemetry_line)
                if presentation is None or not presentation.hides_raw_output:
                    print(telemetry_line, end="", flush=True)
            if timed_out_by_devtool.is_set():
                timeout_message = f"\nDevtool command timeout: exceeded configured timeout of {timeout_seconds:g}s.\n"
                output_parts.append(timeout_message)
                _append_live_log(timeout_message)
                if presentation is not None:
                    presentation.output_line(timeout_message)
                if presentation is None or not presentation.hides_raw_output:
                    print(timeout_message, end="", flush=True)
                returncode = 124
            elif interrupted_by_devtool.is_set() or (tasker_progress is not None and tasker_progress.cancel_requested()):
                returncode = 130
                output_parts.append("\nCancelled from Devtool Control Center. Child process was terminated.\n")
            # Do not render a terminal success here. Report parsing and
            # post-processing can still turn rc=0 into a failure; the outer
            # runner publishes the authoritative final state afterwards.
            if returncode != 0:
                tracker.finish(returncode)
        _finalize_structured_state()
        return returncode, "".join(output_parts)
    except KeyboardInterrupt:
        _interrupt_process()
        output_parts.append("\nInterrupted by user. Child process was terminated.\n")
        if tracker is not None:
            try:
                tracker.finish(130)
            except Exception:
                pass
        _finalize_structured_state()
        return 130, "".join(output_parts)
    finally:
        if presentation is not None:
            presentation.stop()
        cancel_watch_stop.set()
        timeout_watch_stop.set()
        if cancel_watcher is not None and cancel_watcher is not threading.current_thread():
            cancel_watcher.join(timeout=0.25)
        if timeout_watcher is not None and timeout_watcher is not threading.current_thread():
            timeout_watcher.join(timeout=0.25)
        if tasker_progress is not None:
            tasker_progress.clear_cancel()
        _close_live_log()
        with contextlib.suppress(Exception):
            proc.stdout.close()


def _requested_gradle_tasks_for_spec(spec: CommandSpec, progress_argv: list[str]) -> list[str]:
    for key in ("explicit_validation_tasks", "gradle_tasks"):
        raw = spec.metadata.get(key)
        if isinstance(raw, (list, tuple)):
            tasks = [str(item).strip() for item in raw if str(item).strip()]
            if tasks:
                # Preserve request multiplicity for telemetry. The Gradle command
                # planner is responsible for execution de-duplication; the tracker
                # needs the original list so it can report accidental duplicate
                # requests instead of silently erasing the evidence.
                return tasks

    tasks: list[str] = []
    index = 0
    while index < len(progress_argv):
        token = str(progress_argv[index])
        if token == "--task" and index + 1 < len(progress_argv):
            task = str(progress_argv[index + 1]).strip()
            if task:
                tasks.append(task)
            index += 2
            continue
        if token.startswith("--task="):
            task = token.split("=", 1)[1].strip()
            if task:
                tasks.append(task)
        index += 1
    return tasks


def _shutdown_gradle_daemons(
    config: ProjectConfig,
    *,
    project_root: str,
    dry_run: bool,
    log_path: Path | str | None,
) -> tuple[OperationResult, str]:
    root = Path(project_root or config.project_root or config.repo_root).resolve()
    wrapper = root / "gradlew"
    argv = [str(wrapper), "--stop"] if wrapper.is_file() else ["gradle", "--stop"]
    command_line = "$ " + " ".join(shlex.quote(value) for value in argv)
    if dry_run:
        output = "Gradle daemon shutdown skipped during dry-run."
        log_file = str(write_log(command_line + "\n" + output, log_path, append=log_path is not None))
        return OperationResult("gradle-daemon-shutdown", argv, 0, 0.0, output, [], skipped=True, log_path=log_file, metadata={"cleanup": True}), command_line + "\n" + output
    started = time.perf_counter()
    env = os.environ.copy()
    env.update(config.env)
    try:
        completed = subprocess.run(argv, cwd=str(root), env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=30)
        code = completed.returncode
        output = completed.stdout or ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        code = 127
        output = str(exc)
    raw = command_line + ("\n" + output if output else "")
    log_file = str(write_log(raw, log_path, append=log_path is not None))
    return OperationResult(
        "gradle-daemon-shutdown", argv, code, time.perf_counter() - started, output,
        parse_issues(config.repo_root, "gradle-daemon-shutdown", output),
        log_path=log_file, failure_kind="cleanup_failure" if code else "", metadata={"cleanup": True},
    ), raw


def _shutdown_dotnet_build_server(
    config: ProjectConfig,
    *,
    dry_run: bool,
    log_path: Path | str | None,
) -> tuple[OperationResult, str]:
    argv, cleanup_dir = _prepare_termux_dotnet(["dotnet", "build-server", "shutdown"])
    command_line = "$ " + " ".join(shlex.quote(value) for value in argv)
    if dry_run:
        raw = command_line + "\nBuild-server shutdown skipped during dry-run."
        log_file = str(write_log(raw, log_path, append=log_path is not None))
        return OperationResult(
            "dotnet-build-server-shutdown", argv, 0, 0.0, raw.split("\n", 1)[1], [],
            skipped=True, log_path=log_file, metadata={"cleanup": True},
        ), raw

    env = os.environ.copy()
    env.update(config.env)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            cwd=str(config.repo_root),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        returncode = completed.returncode
        output = completed.stdout or ""
    except OSError as exc:
        returncode = 127
        output = str(exc)
    finally:
        if cleanup_dir:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
    duration = time.perf_counter() - started
    issues = parse_issues(config.repo_root, "dotnet-build-server-shutdown", output)
    raw = command_line + ("\n" + output if output else "")
    log_file = str(write_log(raw, log_path, append=log_path is not None))
    result = OperationResult(
        "dotnet-build-server-shutdown",
        argv,
        returncode,
        duration,
        output,
        issues,
        log_path=log_file,
        failure_kind="cleanup_failure" if returncode else "",
        metadata={"cleanup": True},
    )
    return result, raw


def _dotnet_target_argument(argv: Sequence[str], cwd: str) -> str:
    command_index: int | None = None
    for index, value in enumerate(argv):
        if Path(str(value)).name == "dotnet":
            command_index = index
            break
    if command_index is None or command_index + 1 >= len(argv):
        return ""
    skip_next = False
    value_options = {
        "--framework", "-f", "--runtime", "-r", "--configuration", "-c",
        "--output", "-o", "--logger", "-l", "--verbosity", "-v",
    }
    for value in argv[command_index + 2 :]:
        text = str(value)
        if skip_next:
            skip_next = False
            continue
        if text in value_options:
            skip_next = True
            continue
        if text.startswith("-"):
            continue
        return text
    return cwd


def _dotnet_display_target(spec: CommandSpec, config: ProjectConfig) -> str:
    for key in ("bundle_target", "matrix", "workspace", "devtool_target"):
        value = str(spec.metadata.get(key) or "").strip()
        if value:
            return value
    if config.selected_target:
        return config.selected_target
    progress_argv = list(spec.metadata.get("progress_argv") or spec.argv)
    progress_cwd = str(spec.metadata.get("progress_cwd") or spec.cwd or config.repo_root)
    target_text = _dotnet_target_argument(progress_argv, progress_cwd)
    if not target_text:
        return "repository"
    target = Path(target_text)
    if not target.is_absolute():
        target = Path(progress_cwd) / target
    try:
        return str(target.resolve().relative_to(config.repo_root.resolve())).replace(os.sep, "/")
    except (OSError, ValueError):
        return target.name or target_text


def _dotnet_phase_layout(specs: Sequence[CommandSpec], config: ProjectConfig) -> dict[int, tuple[str, int, int]]:
    targets: dict[int, str] = {}
    totals: Counter[str] = Counter()
    for index, spec in enumerate(specs):
        progress_argv = list(spec.metadata.get("progress_argv") or spec.argv)
        if not _contains_dotnet_cli(progress_argv):
            continue
        target = _dotnet_display_target(spec, config) or "repository"
        targets[index] = target
        totals[target] += 1

    seen: defaultdict[str, int] = defaultdict(int)
    layout: dict[int, tuple[str, int, int]] = {}
    for index, target in targets.items():
        seen[target] += 1
        layout[index] = (target, seen[target], totals[target])
    return layout


def dotnet_build_server_shutdown_enabled(config: ProjectConfig) -> bool:
    """Return whether devtool should stop shared .NET build servers after a run."""
    value = config.runner_options.get("dotnet", {})
    options = dict(value) if isinstance(value, dict) else {}
    return bool(options.get("stop_after_run", False))


def _workflow_synthetic_result(
    spec: CommandSpec,
    *,
    state: str,
    reason: str,
    log_path: Path | str | None,
) -> tuple[OperationResult, str]:
    metadata = dict(spec.metadata)
    metadata["workflow_node_state"] = state
    metadata["workflow_skip_reason"] = reason
    output = f"Devtool workflow node {state}: {reason}\n"
    log_file = str(write_log(output, log_path, append=log_path is not None))
    result = OperationResult(
        spec.name,
        list(spec.argv),
        0,
        0.0,
        output,
        [],
        skipped=True,
        log_path=log_file,
        failure_kind="dependency_blocked" if state == "blocked" else "workflow_" + state,
        metadata=metadata,
    )
    return result, output



def _publish_workflow_plan_events(specs: Sequence[CommandSpec], event_run_id: str) -> None:
    """Publish the existing workflow DAG nodes before command execution starts."""
    discovered: set[str] = set()
    for spec in specs:
        node_id = str(spec.metadata.get("workflow_node_key") or "").strip()
        if not node_id or node_id in discovered:
            continue
        discovered.add(node_id)
        raw_dependencies = spec.metadata.get("workflow_depends_on") or ()
        dependencies = raw_dependencies if isinstance(raw_dependencies, (list, tuple, set)) else ()
        node_label = str(
            spec.metadata.get("workflow_node_id")
            or spec.metadata.get("workflow_ref")
            or spec.name
        ).strip() or spec.name
        publish_event(NodeDiscovered(
            run_id=event_run_id,
            node_id=node_id,
            parent_id=str(spec.metadata.get("parent_id") or spec.metadata.get("workflow_parent_id") or ""),
            source="workflow",
            label=node_label,
            kind=str(spec.metadata.get("workflow_ref_kind") or "operation"),
            depends_on=tuple(str(value) for value in dependencies if str(value).strip()),
            metadata={
                "target": str(spec.metadata.get("workflow_target") or ""),
                "workflow": str(spec.metadata.get("workflow_name") or ""),
                "index": spec.metadata.get("workflow_node_index"),
                "runner": str(spec.metadata.get("workflow_runner") or ""),
                "action": str(spec.metadata.get("workflow_action") or ""),
            },
        ))

def run_specs(
    config: ProjectConfig,
    specs: list[CommandSpec],
    *,
    verbose: bool,
    dry_run: bool = False,
    continue_on_error: bool = False,
    log_path: Path | str | None = None,
    tasker_progress: TaskerProgressSink | None = None,
    shutdown_dotnet: bool | None = None,
    supervisor_probe: Callable[[], tuple[str, str] | dict | str | None] | None = None,
    heartbeat_callback: Callable[[], None] | None = None,
    progress_callback: Callable[[dict], None] | None = None,
    workflow_initial_statuses: dict[str, str] | None = None,
    workflow_initial_fail_fast: bool = False,
    workflow_initial_cancelled: bool = False,
    event_run_id: str = "",
    publish_workflow_plan: bool = True,
) -> tuple[list[OperationResult], str]:
    # EXEC-X05 library-call safety net. Normal CLI/workflow/TestSpec paths are
    # converged earlier, before reusable identity is pinned. Direct run_specs()
    # callers still receive the same broker boundary, with any pre-existing RER
    # fingerprint refreshed by converge_gradle_specs.
    from .gradle_ingress import converge_gradle_specs
    specs = converge_gradle_specs(config, specs, default_origin="devtool")

    # CLI executions already own DEVTOOL_RUN_ID. Direct/library run_specs()
    # calls still need an isolated execution identity so the authoritative
    # event store never merges separate invocations into one fallback run.
    event_run_id = str(event_run_id or os.environ.get("DEVTOOL_RUN_ID") or RunId.new(prefix="runner"))
    results: list[OperationResult] = []
    raw_sections: list[str] = []
    used_dotnet = any(_contains_dotnet_cli(spec.argv) for spec in specs)
    stop_dotnet_after_run = (
        dotnet_build_server_shutdown_enabled(config)
        if shutdown_dotnet is None
        else bool(shutdown_dotnet)
    )
    used_gradle = any(
        str(spec.metadata.get("progress_parser") or "") == "gradle"
        or any(Path(str(value)).name in {"gradle", "gradlew"} for value in spec.argv)
        for spec in specs
    )
    gradle_project_root = next((
        str(spec.metadata.get("project_root") or spec.cwd or config.project_root or config.repo_root)
        for spec in specs
        if str(spec.metadata.get("progress_parser") or "") == "gradle"
           or any(Path(str(value)).name in {"gradle", "gradlew"} for value in spec.argv)
    ), str(config.project_root or config.repo_root))
    dotnet_layout = _dotnet_phase_layout(specs, config)
    workflow_graph_active = any(bool(spec.metadata.get("workflow_graph")) for spec in specs)
    workflow_node_status: dict[str, str] = dict(workflow_initial_statuses or {})
    workflow_fail_fast = bool(workflow_initial_fail_fast)
    workflow_cancelled = bool(workflow_initial_cancelled)
    try:
        from .execution_scheduler_truth import decisions_for_specs
        exec_scheduler_decisions = decisions_for_specs(config, specs)
        exec_scheduler_error = ""
    except Exception as exc:
        # Fail safe to RUN.  RER qualification must never become a new reason
        # to suppress real execution when history is unavailable or corrupt.
        exec_scheduler_decisions = {}
        exec_scheduler_error = f"{type(exc).__name__}: {exc}"

    def _metadata_int(value: object, default: int) -> int:
        try:
            if value in (None, ""):
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    def _metadata_sequence(value: object) -> tuple[object, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, (list, tuple, set)):
            return tuple(value)
        return ()

    if publish_workflow_plan:
        _publish_workflow_plan_events(specs, event_run_id)

    if tasker_progress is not None and not dry_run:
        tasker_progress.plan_phases([
            {
                "name": spec.name,
                "target": str(spec.metadata.get("bundle_target") or spec.metadata.get("workflow_target") or spec.metadata.get("matrix") or ""),
                "workspace": str(spec.metadata.get("workspace") or spec.metadata.get("workflow_workspace") or ""),
                "workspace_root": str(spec.metadata.get("workspace_root") or ""),
                "workflow": str(spec.metadata.get("workflow_name") or ""),
                "node_id": str(spec.metadata.get("workflow_node_key") or ""),
                "runner": str(spec.metadata.get("workflow_runner") or spec.metadata.get("runner") or ""),
                "execution_environment": str(spec.metadata.get("execution_environment") or spec.metadata.get("effective_execution_environment") or ""),
                "depends_on": [str(value) for value in _metadata_sequence(spec.metadata.get("workflow_depends_on", [])) if str(value).strip()],
            }
            for spec in specs
        ])

    for spec_index, spec in enumerate(specs, start=1):
        workflow_node_key = str(spec.metadata.get("workflow_node_key") or "")
        workflow_dependencies = [
            str(value) for value in (spec.metadata.get("workflow_depends_on") or []) if str(value).strip()
        ]
        workflow_part_index = _metadata_int(spec.metadata.get("workflow_node_spec_index"), 1)
        workflow_part_total = _metadata_int(spec.metadata.get("workflow_node_spec_total"), 1)
        workflow_parent_id = str(spec.metadata.get("parent_id") or spec.metadata.get("workflow_parent_id") or "")
        event_node_id = workflow_node_key or str(NodeId.dynamic(
            parent_id=f"{event_run_id}:run",
            kind="command",
            key=spec.name,
            instance=spec_index,
        ))
        command_part_node_id = ""
        if workflow_node_key and workflow_part_total > 1:
            command_part_node_id = str(NodeId.dynamic(
                parent_id=workflow_node_key,
                kind="command-part",
                key=spec.name,
                instance=workflow_part_index,
            ))
        progress_parent_node_id = command_part_node_id or event_node_id
        exec_scheduler_decision = exec_scheduler_decisions.get(spec_index)
        if exec_scheduler_decision is not None:
            metadata = dict(spec.metadata or {})
            metadata["exec_scheduler_decision"] = exec_scheduler_decision.as_dict()
            metadata["exec_scheduler_disposition"] = exec_scheduler_decision.disposition
            metadata["exec_scheduler_authoritative"] = bool(exec_scheduler_decision.authoritative)
            metadata["exec_scheduler_process_executed"] = bool(exec_scheduler_decision.process_executed)
            spec.metadata = metadata
        elif exec_scheduler_error:
            metadata = dict(spec.metadata or {})
            metadata["exec_scheduler_decision_error"] = exec_scheduler_error
            metadata["exec_scheduler_disposition"] = "run"
            metadata["exec_scheduler_authoritative"] = False
            metadata["exec_scheduler_process_executed"] = True
            spec.metadata = metadata
        if workflow_node_key:
            blocked_dependencies = [
                key for key in workflow_dependencies
                if workflow_node_status.get(key) in {"failed", "blocked", "cancelled", "skipped"}
            ]
            prior_state = workflow_node_status.get(workflow_node_key, "")
            synthetic_state = ""
            synthetic_reason = ""
            if workflow_cancelled:
                synthetic_state = "cancelled"
                synthetic_reason = "an earlier workflow node was cancelled"
            elif blocked_dependencies:
                synthetic_state = "blocked"
                synthetic_reason = "dependency failed or was blocked: " + ", ".join(blocked_dependencies)
            elif prior_state in {"failed", "blocked", "cancelled", "skipped"}:
                synthetic_state = "skipped"
                synthetic_reason = f"workflow node already ended as {prior_state}"
            elif workflow_fail_fast and prior_state != "running":
                synthetic_state = "skipped"
                synthetic_reason = "workflow fail-fast stopped independent nodes after an earlier failure"

            if synthetic_state:
                if workflow_part_index == 1 or prior_state not in {"failed", "blocked", "cancelled", "skipped"}:
                    workflow_node_status[workflow_node_key] = synthetic_state
                if tasker_progress is not None and not dry_run:
                    tasker_progress.phase_started(spec.name, index=spec_index, total=len(specs), argv=spec.argv, metadata=spec.metadata)
                publish_event(TaskStarted(
                    run_id=event_run_id,
                    task=spec.name,
                    argv=tuple(str(value) for value in redact_sensitive_value(list(spec.argv))),
                    node_id=workflow_node_key,
                    parent_id=str(spec.metadata.get("parent_id") or spec.metadata.get("workflow_parent_id") or ""),
                    source="runner",
                    metadata=redact_sensitive_value({"index": spec_index, "total": len(specs), "argv": list(spec.argv), **dict(spec.metadata)}),
                ))
                synthetic_result, synthetic_raw = _workflow_synthetic_result(
                    spec,
                    state=synthetic_state,
                    reason=synthetic_reason,
                    log_path=log_path,
                )
                synthetic_result.metadata["workflow_node_statuses"] = dict(workflow_node_status)
                if progress_callback is not None:
                    try:
                        progress_callback({
                            "force": True,
                            "final": True,
                            "skipped": True,
                            "workflow_node_key": workflow_node_key,
                            "workflow_node_state": synthetic_state,
                            "workflow_skip_reason": synthetic_reason,
                            "workflow_node_statuses": dict(workflow_node_status),
                        })
                    except Exception:
                        pass
                results.append(synthetic_result)
                raw_sections.append(synthetic_raw)
                publish_event(TaskFinished(
                    run_id=event_run_id,
                    task=spec.name,
                    success=synthetic_state not in {"failed", "blocked", "cancelled"},
                    status=synthetic_state,
                    exit_code=0,
                    node_id=workflow_node_key,
                    parent_id=str(spec.metadata.get("parent_id") or spec.metadata.get("workflow_parent_id") or ""),
                    source="runner",
                    transition_origin="scheduler_inferred",
                    transition_reason=synthetic_reason,
                    metadata={
                        "skipped": True,
                        "workflow_state": synthetic_state,
                        "workflow_skip_reason": synthetic_reason,
                        "workflow_transition_origin": "scheduler_inferred",
                        "returncode": 0,
                    },
                ))
                if tasker_progress is not None and not dry_run:
                    tasker_progress.phase_finished(synthetic_result)
                continue
            workflow_node_status.setdefault(workflow_node_key, "running")

        effective_argv, cleanup_dir = _prepare_termux_dotnet(spec.argv)
        last_progress_snapshot: dict[str, object] = {}
        execution_adapter: RunnerExecutionAdapter | None = None

        def _capture_progress(snapshot: dict) -> None:
            last_progress_snapshot.clear()
            last_progress_snapshot.update(snapshot)
            # Intermediate tracker snapshots have already passed through the
            # in-process adapter inside _run_process_live. The outer callback
            # owns only the authoritative post-processing terminal update.
            if execution_adapter is not None and snapshot.get("final"):
                try:
                    execution_adapter.progress_snapshot(dict(snapshot))
                except Exception:
                    pass
            if progress_callback is not None:
                progress_callback(dict(snapshot))
        display_target, target_phase_index, target_phase_total = dotnet_layout.get(
            spec_index - 1, ("", None, None)
        )
        if tasker_progress is not None and not dry_run:
            tasker_progress.phase_started(spec.name, index=spec_index, total=len(specs), argv=effective_argv, metadata=spec.metadata)
        publish_event(TaskStarted(
                    run_id=event_run_id,
            task=spec.name,
            argv=tuple(str(value) for value in redact_sensitive_value(list(effective_argv))),
            node_id=event_node_id,
            parent_id=workflow_parent_id,
            source="runner",
            metadata=redact_sensitive_value({"index": spec_index, "total": len(specs), "argv": list(effective_argv), **dict(spec.metadata)}),
        ))
        if exec_scheduler_decision is not None and exec_scheduler_decision.disposition == "materialize":
            from .execution_scheduler_truth import materialize_outputs
            cwd_for_materialize = Path(str(spec.cwd or config.repo_root)).expanduser().resolve(strict=False)
            exec_scheduler_decision = materialize_outputs(exec_scheduler_decision, cwd=cwd_for_materialize)
            metadata = dict(spec.metadata or {})
            metadata["exec_scheduler_decision"] = exec_scheduler_decision.as_dict()
            metadata["exec_scheduler_disposition"] = exec_scheduler_decision.disposition
            metadata["exec_scheduler_authoritative"] = bool(exec_scheduler_decision.authoritative)
            metadata["exec_scheduler_process_executed"] = bool(exec_scheduler_decision.process_executed)
            spec.metadata = metadata
        if exec_scheduler_decision is not None and exec_scheduler_decision.terminal_without_spawn:
            started = time.perf_counter()
            command_line = "$ " + " ".join(shlex.quote(a) for a in effective_argv)
            disposition = exec_scheduler_decision.disposition
            output = (
                f"Devtool EXEC scheduler {disposition}: process spawn skipped for {spec.name}; "
                f"source_run={exec_scheduler_decision.source_run_id or '-'} "
                f"source_node={exec_scheduler_decision.source_node_id or '-'}; "
                f"reason={exec_scheduler_decision.reason}.\n"
            )
            raw_text = command_line + "\n" + output
            log_file = str(write_log(raw_text, log_path, append=log_path is not None))
            duration = time.perf_counter() - started
            result_metadata = dict(spec.metadata or {})
            result_metadata.update({
                "execution_disposition": disposition,
                "reuse_decision": disposition,
                "reused": disposition == "reuse",
                "materialized": disposition == "materialize",
                "process_executed": False,
                "returncode": 0,
                "duration_seconds": duration,
                "reuse_fingerprint": dict(exec_scheduler_decision.fingerprint),
                "reuse_output_manifest": dict(exec_scheduler_decision.output_manifest),
                "reuse_result_identity": exec_scheduler_decision.result_identity,
                "reuse_evidence_refs": list(exec_scheduler_decision.proof_refs),
                "exec_scheduler_decision": exec_scheduler_decision.as_dict(),
            })
            if workflow_node_key:
                workflow_node_status[workflow_node_key] = "passed"
                result_metadata["workflow_node_state"] = "passed"
                result_metadata["workflow_node_statuses"] = dict(workflow_node_status)
            result = OperationResult(
                spec.name,
                effective_argv,
                0,
                duration,
                output,
                [],
                skipped=False,
                log_path=log_file,
                failure_kind="",
                metadata=result_metadata,
            )
            results.append(result)
            raw_sections.append(raw_text)
            if command_part_node_id:
                publish_event(NodeFinished(
                    run_id=event_run_id,
                    node_id=command_part_node_id,
                    parent_id=workflow_node_key,
                    status="succeeded",
                    exit_code=0,
                    duration_seconds=duration,
                    source="runner",
                    transition_origin="rer_scheduler",
                    transition_reason=exec_scheduler_decision.reason,
                    evidence=(log_file,) if log_file else (),
                    metadata={
                        "part_index": workflow_part_index,
                        "part_total": workflow_part_total,
                        "execution_disposition": disposition,
                        "process_executed": False,
                    },
                ))
            publish_event(TaskFinished(
                run_id=event_run_id,
                task=spec.name,
                success=True,
                status="succeeded",
                exit_code=0,
                duration_seconds=duration,
                node_id=event_node_id,
                parent_id=workflow_parent_id,
                source="runner",
                transition_origin="rer_scheduler",
                transition_reason=exec_scheduler_decision.reason,
                evidence=(log_file,) if log_file and not command_part_node_id else (),
                metadata={
                    "returncode": 0,
                    "duration_seconds": duration,
                    "execution_disposition": disposition,
                    "reuse_decision": disposition,
                    "reused": disposition == "reuse",
                    "materialized": disposition == "materialize",
                    "process_executed": False,
                    "node_definition_id": exec_scheduler_decision.node_definition_id,
                    "reuse_fingerprint": dict(exec_scheduler_decision.fingerprint),
                    "reuse_output_manifest": dict(exec_scheduler_decision.output_manifest),
                    "reuse_result_identity": exec_scheduler_decision.result_identity,
                    "reuse_evidence_refs": list(exec_scheduler_decision.proof_refs),
                    "exec_scheduler_decision": exec_scheduler_decision.as_dict(),
                },
            ))
            if tasker_progress is not None and not dry_run:
                tasker_progress.phase_finished(result)
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            continue
        if command_part_node_id:
            publish_event(NodeDiscovered(
                run_id=event_run_id,
                node_id=command_part_node_id,
                parent_id=workflow_node_key,
                label=spec.name,
                kind="command-part",
                source="runner",
                metadata={"part_index": workflow_part_index, "part_total": workflow_part_total},
            ))
            publish_event(NodeStarted(
                run_id=event_run_id,
                node_id=command_part_node_id,
                parent_id=workflow_node_key,
                label=spec.name,
                kind="command-part",
                argv=tuple(str(value) for value in effective_argv),
                source="runner",
                metadata={"part_index": workflow_part_index, "part_total": workflow_part_total},
            ))

        if dry_run:
            output = "$ " + " ".join(effective_argv)
            log_file = str(write_log(output, log_path, append=log_path is not None))
            dry_metadata = dict(spec.metadata)
            if workflow_node_key:
                workflow_node_status[workflow_node_key] = "ready"
                dry_metadata["workflow_node_state"] = "ready"
                dry_metadata["workflow_node_statuses"] = dict(workflow_node_status)
            dry_result = OperationResult(spec.name, effective_argv, 0, 0.0, output, [], skipped=True, log_path=log_file, metadata=dry_metadata)
            results.append(dry_result)
            if command_part_node_id:
                publish_event(NodeFinished(
                    run_id=event_run_id, node_id=command_part_node_id, parent_id=workflow_node_key,
                    status="skipped", exit_code=0, source="runner", metadata={"dry_run": True},
                ))
            publish_event(TaskFinished(
                    run_id=event_run_id,
                task=spec.name,
                success=True,
                status="skipped",
                exit_code=0,
                node_id=event_node_id,
                parent_id=workflow_parent_id,
                source="runner",
                metadata={"skipped": True, "dry_run": True, "returncode": 0},
            ))
            raw_sections.append(output)
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            continue

        env = os.environ.copy()
        from .environment_qualification import assert_environment_overlay_safe
        try:
            assert_environment_overlay_safe(config.env, source="project execution environment")
            assert_environment_overlay_safe(spec.env, source=f"command spec {spec.name}")
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        if str(spec.metadata.get("execution_mode") or "") != "proot-distro":
            env.update(config.env)
            env.update(spec.env)
        else:
            # Project/spec environment values have already been converted into
            # explicit PRoot --env arguments. Do not leak them into the host
            # proot-distro process as a second, backend-ambiguous channel.
            env.update(spec.env)

        raw_timeout_seconds = spec.metadata.get("timeout_seconds")
        command_timeout_seconds: float | None = None
        if raw_timeout_seconds not in (None, ""):
            try:
                parsed_timeout = float(raw_timeout_seconds)
            except (TypeError, ValueError):
                parsed_timeout = 0.0
            if parsed_timeout > 0:
                command_timeout_seconds = parsed_timeout

        started = time.perf_counter()
        started_wall = time.time()
        returncode = 1
        output = ""
        process_output = ""
        structured_process_state: dict[str, object] = {}
        command_line = "$ " + " ".join(shlex.quote(a) for a in effective_argv)
        try:
            # AOPS03 secret boundary: signing password values are deliberately
            # absent from CommandSpec/env/session state and are resolved only
            # now, after all execution-backend wrapping has finished. Backends
            # that serialize env assignments into argv fail closed here.
            from .android.signing import signing_environment_for_execution
            signing_metadata = dict(spec.metadata)
            # Signing intent is attached during Android planning from the
            # canonical variant profile. Execution must not infer policy again
            # from Gradle task names; that breaks custom build types/flavors.
            env = signing_environment_for_execution(config, signing_metadata, env=env)
            cwd = spec.cwd or str(config.repo_root)
            progress_argv = list(spec.metadata.get("progress_argv") or effective_argv)
            progress_cwd = str(spec.metadata.get("progress_cwd") or cwd)
            progress_parser = str(spec.metadata.get("progress_parser") or "")
            requested_gradle_tasks = _requested_gradle_tasks_for_spec(spec, progress_argv)
            dotnet_plan = dotnet_progress_plan(progress_argv, progress_cwd)
            npm_plan = npm_progress_plan(progress_argv, progress_cwd)
            if progress_parser == "gradle":
                execution_tool = "gradle"
            elif dotnet_plan:
                execution_tool = "dotnet"
            elif progress_parser == "npm" or npm_plan:
                execution_tool = "npm"
            else:
                execution_tool = str(spec.metadata.get("runner") or progress_parser or "generic")
            execution_adapter = RunnerExecutionAdapter(
                run_id=event_run_id,
                parent_node_id=progress_parent_node_id,
                tool=execution_tool,
                provenance=_execution_provenance(spec.metadata, effective_argv),
            )
            try:
                overall_phase_index = _metadata_int(spec.metadata.get("validation_phase_index"), spec_index)
            except (TypeError, ValueError):
                overall_phase_index = spec_index
            try:
                overall_phase_total = _metadata_int(spec.metadata.get("validation_phase_total"), len(specs))
            except (TypeError, ValueError):
                overall_phase_total = len(specs)
            memory_guard_mb = _metadata_int(
                spec.metadata.get("validation_memory_guard_mb")
                or spec.metadata.get("memory_guard_mb"),
                0,
            )
            if memory_guard_mb > 0 and _command_delegates_memory_guard(effective_argv):
                # The Android builder monitors the actual Gradle child itself.
                # A second outer watchdog would sample the same kernel state,
                # reclaim caches twice, and race the inner adaptive policy.
                memory_guard_mb = 0
            if verbose:
                console.print(f"[cyan]$ {' '.join(shlex.quote(a) for a in effective_argv)}[/cyan]")
                returncode, output = _run_process_live(
                    effective_argv,
                    cwd=cwd,
                    env=env,
                    verbose=True,
                    command_name=spec.name,
                    tasker_progress=tasker_progress,
                    phase_index=overall_phase_index,
                    phase_total=overall_phase_total,
                    progress_argv=progress_argv,
                    progress_cwd=progress_cwd,
                    display_target=display_target,
                    target_phase_index=target_phase_index,
                    target_phase_total=target_phase_total,
                    progress_parser=progress_parser,
                    requested_gradle_tasks=requested_gradle_tasks,
                    live_log_path=log_path,
                    live_log_header=command_line + "\n" if log_path is not None else "",
                    timeout_seconds=command_timeout_seconds,
                    memory_guard_mb=memory_guard_mb,
                    supervisor_probe=supervisor_probe,
                    heartbeat_callback=heartbeat_callback,
                    progress_callback=_capture_progress,
                    structured_state=structured_process_state,
                    execution_adapter=execution_adapter,
                    presentation_mode=str(spec.metadata.get("execution_presentation") or ""),
                )
            else:
                if progress_parser == "gradle" or dotnet_plan:
                    returncode, output = _run_process_live(
                        effective_argv,
                        cwd=cwd,
                        env=env,
                        verbose=False,
                        command_name=spec.name,
                        tasker_progress=tasker_progress,
                        phase_index=overall_phase_index,
                        phase_total=overall_phase_total,
                        progress_argv=progress_argv,
                        progress_cwd=progress_cwd,
                        display_target=display_target,
                        target_phase_index=target_phase_index,
                        target_phase_total=target_phase_total,
                        progress_parser=progress_parser,
                        requested_gradle_tasks=requested_gradle_tasks,
                        live_log_path=log_path,
                        live_log_header=command_line + "\n" if log_path is not None else "",
                        timeout_seconds=command_timeout_seconds,
                        memory_guard_mb=memory_guard_mb,
                        supervisor_probe=supervisor_probe,
                        heartbeat_callback=heartbeat_callback,
                        progress_callback=_capture_progress,
                        structured_state=structured_process_state,
                        execution_adapter=execution_adapter,
                        presentation_mode=str(spec.metadata.get("execution_presentation") or ""),
                    )
                else:
                    returncode, output = _run_process_live(
                        effective_argv,
                        cwd=cwd,
                        env=env,
                        verbose=False,
                        command_name=spec.name,
                        tasker_progress=tasker_progress,
                        phase_index=overall_phase_index,
                        phase_total=overall_phase_total,
                        progress_argv=progress_argv,
                        progress_cwd=progress_cwd,
                        display_target=str(
                            spec.metadata.get("bundle_target")
                            or spec.metadata.get("project_root")
                            or spec.metadata.get("workspace_root")
                            or spec.metadata.get("validation_scope")
                            or progress_cwd
                        ),
                        target_phase_index=target_phase_index,
                        target_phase_total=target_phase_total,
                        progress_parser=progress_parser,
                        requested_gradle_tasks=requested_gradle_tasks,
                        live_log_path=log_path,
                        live_log_header=command_line + "\n" if log_path is not None else "",
                        timeout_seconds=command_timeout_seconds,
                        memory_guard_mb=memory_guard_mb,
                        supervisor_probe=supervisor_probe,
                        heartbeat_callback=heartbeat_callback,
                        progress_callback=_capture_progress,
                        structured_state=structured_process_state,
                        execution_adapter=execution_adapter,
                        presentation_mode=str(spec.metadata.get("execution_presentation") or ""),
                    )
        finally:
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
        process_output = output
        duration = time.perf_counter() - started
        result_metadata = dict(spec.metadata)
        if result_metadata.get("ssh_return_manifest"):
            from .ssh_returns import load_return_state, publish_return_transfer_event
            ssh_return = load_return_state(result_metadata)
            result_metadata["ssh_return"] = ssh_return
            publish_return_transfer_event(result_metadata, ssh_return)
        canonical_test_id = str(result_metadata.get("test_spec_id") or spec.name).strip() or spec.name
        canonical_test_candidates = []
        is_go_test = (
            str(result_metadata.get("runner") or "").lower() == "go"
            and str(spec.name or "").lower() in {"test", "go-test", "go-validation"}
        )
        go_report = structured_process_state.get("go_test_report")
        go_report_evidence = "protocol" if go_report is not None else "stdout"
        if go_report is None and (
            bool(result_metadata.get("go_test_json"))
            or str(result_metadata.get("progress_parser") or "") == "go-test-json"
            or is_go_test
        ):
            from .go_test import parse_go_test_report
            go_report = parse_go_test_report(output)
        if go_report is not None and hasattr(go_report, "to_metadata"):
            go_metadata = go_report.to_metadata()
            result_metadata["go_test"] = go_metadata
            result_metadata["tests"] = dict(go_metadata["tests"])
            canonical_test_candidates.append(_test_result_from_go_report(canonical_test_id, go_report, evidence=go_report_evidence))
        command_issues = parse_issues(config.repo_root, spec.name, output)
        ssh_return = result_metadata.get("ssh_return") if isinstance(result_metadata.get("ssh_return"), dict) else {}
        if go_report is not None:
            from .diagnostics import go_test_issues
            command_issues = [issue for issue in command_issues if issue.code not in {"GO_TEST", "GO_TEST_PACKAGE"}]
            command_issues.extend(go_test_issues(config.repo_root, spec.name, go_report))
        suppressed_stdout_diagnostics = 0
        if returncode == 0:
            command_issues, suppressed_stdout_diagnostics = _filter_successful_stdout_diagnostics(
                command_issues,
                trust_stdout_diagnostics=_trust_successful_stdout_diagnostics(
                    effective_argv,
                    spec.name,
                    result_metadata,
                ),
            )
        issues = command_issues
        if suppressed_stdout_diagnostics:
            result_metadata["suppressed_stdout_diagnostics"] = suppressed_stdout_diagnostics
            result_metadata["suppressed_stdout_diagnostics_reason"] = "successful process boundary captured non-authoritative stdout diagnostics"
        try:
            ssh_return_code = int(ssh_return.get("returncode", -1)) if ssh_return else -1
        except (TypeError, ValueError):
            ssh_return_code = -1
        if ssh_return and ssh_return_code not in {0, 125}:
            command_issues.append(Issue(
                "warning", "ssh-return", str(ssh_return.get("local_root") or config.repo_root),
                "SSH_RETURN_PARTIAL",
                "Remote reports/artifacts were only partially returned; the build result is preserved and the return bundle can be retried with 'devtool ssh retrieve'.",
            ))
            result_metadata["ssh_return_recovery_required"] = True
        configured_report_parsers = result_metadata.get("report_parsers")
        if isinstance(configured_report_parsers, (list, tuple)):
            report_parsers = [str(value).strip() for value in configured_report_parsers if str(value).strip()]
        else:
            report_parser = str(result_metadata.get("report_parser") or "").strip()
            report_parsers = [report_parser] if report_parser else []
        report_parsers = list(dict.fromkeys(report_parsers))
        nonfatal_report_issues: list[Issue] = []
        report_interrupted = returncode == 130 or "Interrupted by user. Child process was terminated." in output
        if report_parsers and report_interrupted:
            result_metadata["report_parsing_skipped"] = "interrupted"
        if report_parsers and not report_interrupted:
            original_report_root = Path(str(result_metadata.get("project_root") or spec.cwd or config.repo_root)).resolve()
            returned_project_root = str(result_metadata.get("ssh_return_project_root") or "").strip()
            report_root = Path(returned_project_root).resolve() if returned_project_root else original_report_root
            returned_repo_root = str(result_metadata.get("ssh_return_repo_root") or "").strip()
            for report_parser in report_parsers:
                raw_report_path = str(
                    result_metadata.get("coverage_report")
                    if report_parser == "python-coverage"
                    else result_metadata.get("test_report") or result_metadata.get("package_report") or ""
                )
                if returned_repo_root and raw_report_path:
                    from .ssh_returns import map_returned_path
                    raw_report_path = map_returned_path(
                        raw_report_path,
                        local_repo_root=config.repo_root,
                        returned_repo_root=Path(returned_repo_root),
                        project_root=original_report_root,
                    )
                collection = collect_report_metadata(
                    report_root,
                    repo_root=config.repo_root,
                    parser=report_parser,
                    tasks=result_metadata.get("gradle_tasks") or (),
                    started_at=None if returned_project_root else started_wall,
                    command_name=spec.name,
                    output=output,
                    report_path=raw_report_path,
                )
                if report_parser in {"gradle-junit", "pytest-junit", "android-instrumentation", "dotnet-trx", "maven-surefire", "node-json", "node-junit"}:
                    tests = dict(collection.metadata)
                    catalog_discovered = result_metadata.get("test_catalog_discovered")
                    catalog_selected = result_metadata.get("test_catalog_selected")
                    if catalog_discovered is not None:
                        tests["discovered"] = int(catalog_discovered)
                    if catalog_selected is not None:
                        tests["selected"] = int(catalog_selected)
                    result_metadata["tests"] = tests
                    if collection.test_result is not None:
                        normalized_report_result = _reconcile_test_results(canonical_test_id, [collection.test_result])
                        if normalized_report_result is not None:
                            canonical_test_candidates.append(normalized_report_result)
                    if tests.get("report_files") or report_parser == "android-instrumentation":
                        test_prefix = "Devtool Gradle tests: " if report_parser == "gradle-junit" else f"Devtool tests ({tests.get('framework', report_parser)}): "
                        output = (output.rstrip() + "\n" if output else "") + (
                            test_prefix
                            + f"{tests.get('passed', 0)} passed, "
                            + f"{tests.get('failed', 0)} failed, "
                            + f"{tests.get('skipped', 0)} skipped, "
                            + f"{tests.get('total', 0)} total.\n"
                        )
                    output = _append_report_issue_summary(
                        output,
                        collection.issues,
                        title=(
                            "Devtool Gradle test failures"
                            if report_parser == "gradle-junit"
                            else "Devtool .NET test failures"
                            if report_parser == "dotnet-trx"
                            else "Devtool Maven test failures"
                            if report_parser == "maven-surefire"
                            else "Devtool test failures"
                        ),
                        codes=(
                            {"GRADLE_TEST"}
                            if report_parser == "gradle-junit"
                            else {"ANDROID_INSTRUMENTATION"}
                            if report_parser == "android-instrumentation"
                            else {"DOTNET_TEST"}
                            if report_parser == "dotnet-trx"
                            else {"MAVEN_TEST"}
                            if report_parser == "maven-surefire"
                            else {"NODE_TEST"}
                            if report_parser in {"node-json", "node-junit"}
                            else {"PYTEST_FAILURE", "PYTHON_DEPENDENCY", "PYTHON_PLATFORM", "PYTHON_IMPORT"}
                        ),
                    )
                    # Pytest JUnit XML is the authoritative identity/source for
                    # failed test cases.  Console ``FAILED ...`` lines are useful
                    # fallback diagnostics when XML is missing, but keeping both
                    # makes one failed testcase appear twice in HUD/clipboard
                    # summaries.  Once a current pytest report exists, discard
                    # only the raw pytest failure duplicates and retain warnings
                    # and unrelated process diagnostics.
                    if report_parser == "pytest-junit" and tests.get("report_files"):
                        issues = [issue for issue in issues if issue.code != "PYTEST_FAILURE"]
                    try:
                        from .test_selection import save_failed_test_ids
                        metadata_failed = collection.metadata.get("failed_ids") if isinstance(collection.metadata, dict) else None
                        if isinstance(metadata_failed, (list, tuple)):
                            failed_ids = [str(value).strip() for value in metadata_failed if str(value).strip()]
                        else:
                            failed_ids = [
                                issue.message.split(":", 1)[0]
                                for issue in collection.issues
                                if issue.code in {
                                    "GRADLE_TEST", "ANDROID_INSTRUMENTATION",
                                    "PYTEST_FAILURE", "PYTHON_DEPENDENCY",
                                    "PYTHON_PLATFORM", "PYTHON_IMPORT", "DOTNET_TEST", "MAVEN_TEST", "NODE_TEST",
                                }
                            ]
                        save_failed_test_ids(config.repo_root, failed_ids)
                        result_metadata["failed_test_ids"] = failed_ids
                    except Exception:
                        pass
                    if report_parser == "pytest-junit" and tests.get("report_files"):
                        from .test_reporting import render_structured_test_summary, structured_junit_summary

                        normalized_for_summary = (
                            _reconcile_test_results(canonical_test_id, [collection.test_result])
                            if collection.test_result is not None
                            else None
                        )
                        structured_summary = structured_junit_summary(
                            normalized_for_summary,
                            issues=collection.issues,
                        )
                        result_metadata["structured_test_summary"] = structured_summary
                        result_metadata["failure_clusters"] = list(structured_summary.get("failure_clusters") or [])
                        result_metadata["generated_surface_root_diagnostics"] = list(
                            structured_summary.get("generated_surface_root_diagnostics") or []
                        )
                        output = (output.rstrip() + "\n" if output else "") + render_structured_test_summary(structured_summary) + "\n"
                elif report_parser == "android-lint":
                    result_metadata["lint"] = collection.metadata
                    if collection.metadata.get("report_files"):
                        output = (output.rstrip() + "\n" if output else "") + (
                            "Devtool Android Lint: "
                            f"{collection.metadata.get('errors', 0)} errors, "
                            f"{collection.metadata.get('warnings', 0)} warnings.\n"
                        )
                    nonfatal_report_issues.extend(collection.issues)
                elif report_parser == "python-package":
                    result_metadata["packages"] = collection.metadata
                    output = (output.rstrip() + "\n" if output else "") + (
                        "Devtool Python packages: "
                        f"{collection.metadata.get('valid', 0)} valid, "
                        f"{collection.metadata.get('invalid', 0)} invalid.\n"
                    )
                elif report_parser == "python-tool":
                    result_metadata.setdefault("python_tools", []).append(collection.metadata)
                    if collection.metadata:
                        output = (output.rstrip() + "\n" if output else "") + (
                            f"Devtool Python {collection.metadata.get('tool', 'tool')}: "
                            f"{collection.metadata.get('errors', 0)} errors, "
                            f"{collection.metadata.get('warnings', 0)} warnings.\n"
                        )
                elif report_parser == "python-audit":
                    result_metadata["python_audit"] = collection.metadata
                    if collection.metadata:
                        output = (output.rstrip() + "\n" if output else "") + (
                            f"Devtool Python source audit: {collection.metadata.get('errors', 0)} issue(s).\n"
                        )
                elif report_parser == "python-coverage":
                    result_metadata["coverage"] = collection.metadata
                    if collection.metadata:
                        output = (output.rstrip() + "\n" if output else "") + (
                            f"Devtool Python coverage: {collection.metadata.get('line_percent', 0):.2f}%.\n"
                        )
                issues.extend(collection.issues)


        if _is_test_operation(spec.name, effective_argv, result_metadata):
            # Structured protocol/report evidence is authoritative. Progress/stdout
            # parsing is used only when no structured result was produced.
            if not canonical_test_candidates:
                framework = str(result_metadata.get("test_framework") or result_metadata.get("runner") or "")
                progress_result = _test_result_from_progress_snapshot(canonical_test_id, last_progress_snapshot, framework=framework)
                if progress_result is not None:
                    canonical_test_candidates.append(progress_result)
                else:
                    stdout_result = _test_result_from_stdout(canonical_test_id, effective_argv, process_output, framework=framework)
                    if stdout_result is not None:
                        canonical_test_candidates.append(stdout_result)
            canonical_test_result = _reconcile_test_results(canonical_test_id, canonical_test_candidates)
            if canonical_test_result is not None:
                result_metadata["test_result"] = _attach_test_provenance(canonical_test_result.to_dict(), result_metadata)
                canonical_counts = _canonical_test_counts(canonical_test_result)
                existing_tests = result_metadata.get("tests") if isinstance(result_metadata.get("tests"), dict) else {}
                result_metadata["tests"] = {**existing_tests, **canonical_counts}
                result_metadata["test_evidence"] = str(canonical_test_result.metadata.get("evidence") or "")
            else:
                # Exit status is deliberately metadata-only: do not invent a fake
                # test case when a runner exposes no test identity or counters.
                result_metadata["test_result"] = _attach_test_provenance({
                    "schema_version": 1, "id": canonical_test_id, "suites": [], "artifacts": [],
                    "metadata": {"evidence": "exit", "exit_code": returncode},
                }, result_metadata)
                result_metadata["test_evidence"] = "exit"


        artifact_options = config.runner_options.get("artifacts", {})
        artifact_options = dict(artifact_options) if isinstance(artifact_options, dict) else {}
        from .artifact_pipeline import _coerce_bool as _artifact_bool
        if bool(
            _artifact_bool(result_metadata.get("collect_artifacts"), False)
            or _artifact_bool(result_metadata.get("verify_artifacts"), False)
            or _artifact_bool(artifact_options.get("verify"), False)
            or artifact_options.get("paths")
            or artifact_options.get("artifact_paths")
            or artifact_options.get("directories")
            or artifact_options.get("artifact_directories")
        ):
            from .artifact_pipeline import collect_and_process_artifacts, format_artifact_summary
            returned_artifact_root = str(result_metadata.get("ssh_return_project_root") or "").strip()
            artifact_root = Path(returned_artifact_root or str(result_metadata.get("project_root") or spec.cwd or config.repo_root)).resolve()
            artifact_metadata, artifact_issues = collect_and_process_artifacts(
                config,
                project_root=artifact_root,
                started_at=0.0 if returned_artifact_root else started_wall,
                spec_name=spec.name,
                spec_metadata=dict(spec.metadata),
                result_metadata=result_metadata,
                process_output=process_output,
                returncode=returncode,
            )
            if artifact_metadata:
                termux_options = config.runner_options.get("termux", {})
                termux_options = dict(termux_options) if isinstance(termux_options, dict) else {}
                termux_artifacts = termux_options.get("artifacts", {}) if isinstance(termux_options.get("artifacts", {}), dict) else {}
                export_dir = str(termux_artifacts.get("export_dir") or "").strip()
                artifact_errors = [issue for issue in artifact_issues if issue.severity == "error"]
                if export_dir and returncode == 0 and not artifact_errors:
                    from .termux_runtime import export_artifacts
                    valid_paths = []
                    for item in artifact_metadata.get("items", []):
                        if not isinstance(item, dict) or not item.get("valid") or not item.get("path"):
                            continue
                        path = Path(str(item["path"])).expanduser()
                        if not path.is_absolute():
                            path = config.repo_root / path
                        valid_paths.append(path.resolve())
                    exported = export_artifacts(valid_paths, export_dir)
                    artifact_metadata["exported"] = [str(path) for path in exported]
                    artifact_metadata["export_directory"] = str(Path(export_dir).expanduser())
                elif export_dir:
                    artifact_metadata["exported"] = []
                    artifact_metadata["export_skipped_reason"] = (
                        "command failed before artifact export" if returncode != 0
                        else "artifact verification reported errors"
                    )
                result_metadata["artifacts"] = artifact_metadata
                if int(artifact_metadata.get("invalid") or 0) > 0 and not artifact_errors:
                    artifact_issues.append(Issue(
                        "error", "artifact-verify", relativize(config.repo_root, str(artifact_root)),
                        "ARTIFACT_INVALID_TOTAL",
                        "Artifact metadata reports invalid outputs but no detailed verification issue was emitted.",
                    ))
                    artifact_errors = [issue for issue in artifact_issues if issue.severity == "error"]
                issues.extend(artifact_issues)
                output = (output.rstrip() + "\n" if output else "") + format_artifact_summary(artifact_metadata)
                if artifact_issues:
                    failures = [issue for issue in artifact_issues if issue.severity == "error"]
                    warnings = [issue for issue in artifact_issues if issue.severity == "warning"]
                    if failures:
                        result_metadata["artifact_verification_failed"] = True
                        if returncode == 0:
                            returncode = 1
                        output += "Devtool artifact verification failures:\n"
                        for issue in failures[:8]:
                            output += f"- {issue.file} [{issue.code}]: {issue.message}\n"
                        if len(failures) > 8:
                            output += f"- ... and {len(failures) - 8} more failure(s).\n"
                    if warnings:
                        output += "Devtool artifact verification notices:\n"
                        for issue in warnings[:8]:
                            output += f"- {issue.file} [{issue.code}]: {issue.message}\n"
                        if len(warnings) > 8:
                            output += f"- ... and {len(warnings) - 8} more notice(s).\n"
        # Persist every structured diagnostic discovered
        # from stdout and report parsers into the authoritative command
        # output/log. This deliberately has no finding-count cap: tools such
        # as Android lint may print only their first failure while the report
        # contains the complete set.
        output = _append_complete_diagnostic_summary(output, issues)

        if _is_dotnet_test_command(spec.argv, spec.name):
            try:
                from .test_selection import save_failed_test_ids
                failed_ids = list(dict.fromkeys(
                    match.group(1).strip()
                    for match in re.finditer(r"(?m)^\s*Failed\s+([^\s\[]+)", process_output or "")
                    if match.group(1).strip()
                ))
                save_failed_test_ids(config.repo_root, failed_ids)
                result_metadata["failed_test_ids"] = failed_ids
            except OSError as exc:
                result_metadata["failed_test_history_error"] = str(exc)

        nonfatal_issue_ids = {id(issue) for issue in nonfatal_report_issues}
        has_fatal_error_diagnostics = any(
            issue.severity == "error"
            and id(issue) not in nonfatal_issue_ids
            # Generic text like "error: cache miss" is not authoritative when
            # the process itself succeeded. Typed parser/report diagnostics are.
            and issue.code != "GENERIC"
            for issue in issues
        )
        if returncode == 0 and has_fatal_error_diagnostics:
            output = (output.rstrip() + "\n" if output else "") + (
                "Devtool detected error diagnostics even though the process exited 0; "
                "treating this command as failed.\n"
            )
            returncode = 1
        supervisor_events = []
        for match in re.finditer(
            r"Devtool validation supervisor stopped process:\s*([a-z-]+):\s*(.+?)(?:\.|$)",
            output,
            re.IGNORECASE,
        ):
            supervisor_events.append({"kind": match.group(1).lower(), "reason": match.group(2).strip()})
        if supervisor_events:
            result_metadata["supervisor_events"] = supervisor_events
            result_metadata["supervisor_reason"] = supervisor_events[-1]["reason"]
            kind = supervisor_events[-1]["kind"]
            result_metadata["resource_guard_triggered"] = kind == "resource"
            result_metadata["timed_out"] = kind == "timeout"
        failure_kind = classify_failure(spec.name, output, returncode)
        if command_timeout_seconds is not None and returncode == 124 and "Devtool command timeout:" in output:
            result_metadata["timed_out"] = True
            result_metadata["timeout_seconds"] = command_timeout_seconds
            failure_kind = "command_timeout"
        if returncode != 0:
            from .execution_environments import NATIVE_TERMUX, detect_current_environment
            from .termux_runtime import classify_runtime_failure
            native_termux_command = (
                detect_current_environment() == NATIVE_TERMUX
                and str(spec.metadata.get("execution_mode") or "") not in {"proot-distro", "ssh", "chroot"}
            )
            runtime_hint = classify_runtime_failure(output, native_termux=native_termux_command)
            if runtime_hint is not None:
                result_metadata["runtime_failure_hint"] = {"kind": runtime_hint.kind, "detail": runtime_hint.detail}
                output = (output.rstrip() + "\n" if output else "") + f"Devtool runtime diagnostic ({runtime_hint.kind}): {runtime_hint.detail}\n"
                if runtime_hint.kind == "network-timeout":
                    failure_kind = "network_timeout"
                elif failure_kind in {"command_failure", "unknown", ""}:
                    failure_kind = "environment_failure"
        if any(
            issue.severity == "error"
            and issue.code in {"GRADLE_MEMORY_GUARD", "GRADLE_DAEMON_DISAPPEARED", "GRADLE_METASPACE_OOM"}
            for issue in issues
        ):
            failure_kind = (
                "metaspace_oom"
                if any(issue.severity == "error" and issue.code == "GRADLE_METASPACE_OOM" for issue in issues)
                else "resource_interrupted"
            )
        elif spec.name == "python-source-audit" and any(
            issue.severity == "error" and issue.code == "PYTHON_UNDECLARED_IMPORT"
            for issue in issues
        ):
            failure_kind = "dependency_failure"
        elif spec.name == "python-source-audit" and any(
            issue.severity == "error" and issue.code == "PYTHON_PLATFORM_GLIBC"
            for issue in issues
        ):
            failure_kind = "platform_failure"
        elif any(
            issue.severity == "error"
            and issue.code in {
                "GRADLE_TEST", "ANDROID_INSTRUMENTATION", "UNITTEST", "PYTEST", "VSTEST",
                "PYTEST_FAILURE", "PYTHON_DEPENDENCY", "PYTHON_PLATFORM", "PYTHON_IMPORT",
                "GO_TEST", "GO_TEST_PACKAGE", "DOTNET_TEST", "MAVEN_TEST", "NODE_TEST",
            }
            for issue in issues
        ):
            failure_kind = "test_failure"

        if supervisor_events:
            failure_kind = {
                "resource": "resource_interrupted",
                "timeout": "command_timeout",
                "interrupted": "interrupted",
                "environment": "environment_failure",
            }.get(supervisor_events[-1]["kind"], failure_kind)

        go_test_metadata = result_metadata.get("go_test") if isinstance(result_metadata.get("go_test"), dict) else {}
        if go_test_metadata:
            packages = go_test_metadata.get("packages") if isinstance(go_test_metadata.get("packages"), dict) else {}
            failures = go_test_metadata.get("failures") if isinstance(go_test_metadata.get("failures"), list) else []
            go_log_lines = [
                "Devtool Go test summary: "
                f"{int(packages.get('passed') or 0)} package(s) passed, "
                f"{int(packages.get('failed') or 0)} failed, "
                f"{int(packages.get('skipped') or 0)} skipped, "
                f"{int(packages.get('no_tests') or 0)} no tests "
                f"({int(packages.get('total') or 0)} total); "
                f"exit {returncode}; {failure_kind or 'success'}."
            ]
            if failures and isinstance(failures[0], dict):
                first_failure = failures[0]
                package = str(first_failure.get("package") or "").strip()
                test = str(first_failure.get("test") or "").strip()
                file_text = str(first_failure.get("file") or "").strip()
                line = first_failure.get("line")
                message = str(first_failure.get("message") or "Go test failed").strip()
                identity = " · ".join(part for part in (package, test) if part)
                if file_text and file_text != "-":
                    identity = " · ".join(part for part in (identity, f"{file_text}:{line}" if line is not None else file_text) if part)
                go_log_lines.append(f"Devtool Go failure: {identity}: {message}" if identity else f"Devtool Go failure: {message}")
            rerun = str(go_test_metadata.get("rerun") or "").strip()
            if rerun:
                go_log_lines.append(f"Devtool Go rerun: {rerun}")
            go_log_summary = "\n".join(go_log_lines) + "\n"
            if go_log_summary.strip() not in output:
                output = (output.rstrip() + "\n" if output else "") + go_log_summary
        if progress_callback is not None:
            final_snapshot = dict(last_progress_snapshot)
            final_snapshot.update({
                "returncode": returncode,
                "warnings": sum(1 for issue in issues if issue.severity == "warning" and issue.code != "GENERIC"),
                "errors": sum(1 for issue in issues if issue.severity == "error" and issue.code != "GENERIC"),
                "force": True,
                "final": True,
            })
            total_value = final_snapshot.get("total")
            if returncode == 0 and isinstance(total_value, (int, float)) and total_value:
                measured_completed = final_snapshot.get("completed")
                measured_percent = final_snapshot.get("percent")
                if measured_completed != total_value:
                    # Process success is authoritative, but do not pretend a
                    # partially observed progress stream measured every unit.
                    # Preserve the measured state and declare the finalization
                    # rule explicitly before publishing terminal 100%.
                    final_snapshot["progress_finalization"] = "process-exit"
                    final_snapshot["measured_completed"] = measured_completed
                    final_snapshot["measured_percent"] = measured_percent
                final_snapshot["completed"] = total_value
                final_snapshot["percent"] = 100.0
            try:
                progress_callback(final_snapshot)
            except Exception:
                pass
        raw_text = command_line + "\n" + output
        if log_path is not None:
            if output != process_output:
                extra_output = output[len(process_output):] if output.startswith(process_output) else output
                if extra_output:
                    write_log(extra_output, log_path, append=True)
            log_file = str(log_path)
        else:
            log_file = str(write_log(raw_text, log_path, append=False))
        if workflow_node_key:
            if returncode == 130:
                workflow_node_status[workflow_node_key] = "cancelled"
                workflow_cancelled = True
                result_metadata["workflow_node_state"] = "cancelled"
            elif returncode != 0:
                workflow_node_status[workflow_node_key] = "failed"
                result_metadata["workflow_node_state"] = "failed"
                if not continue_on_error:
                    workflow_fail_fast = True
            elif workflow_part_index >= workflow_part_total:
                workflow_node_status[workflow_node_key] = "passed"
                result_metadata["workflow_node_state"] = "passed"
            else:
                workflow_node_status[workflow_node_key] = "running"
                result_metadata["workflow_node_state"] = "running"
            result_metadata["workflow_node_statuses"] = dict(workflow_node_status)

        diagnostic_ids: list[str] = []
        if execution_adapter is not None:
            try:
                execution_adapter.process_exited(
                    exit_code=returncode,
                    duration_seconds=duration,
                    failure_kind=failure_kind,
                    log_path=log_file,
                    evidence=(log_file,) if log_file else (),
                )
            except Exception:
                pass
        has_error_diagnostic = False
        for issue in issues:
            severity = str(issue.severity or "warning").strip().lower()
            if severity in {"error", "fatal"}:
                has_error_diagnostic = True
            if execution_adapter is not None:
                try:
                    diagnostic_ids.append(execution_adapter.diagnostic(
                        severity=severity,
                        code=str(issue.code or ""),
                        message=str(issue.message or ""),
                        file=str(issue.file or ""),
                        line=issue.line,
                        failure_kind=failure_kind if severity in {"error", "fatal"} else "",
                        log_path=log_file,
                        evidence=(log_file,) if log_file else (),
                    ))
                    continue
                except Exception:
                    pass
            if severity == "warning":
                publish_event(WarningDetected(
                    run_id=event_run_id,
                    message=str(issue.message or ""),
                    code=str(issue.code or ""),
                    file=str(issue.file or ""),
                    line=issue.line,
                    node_id=progress_parent_node_id,
                    parent_id=workflow_node_key or workflow_parent_id,
                    source="runner",
                    metadata={"task": spec.name},
                ))

        if returncode not in {0, 130} and not has_error_diagnostic and execution_adapter is not None:
            try:
                diagnostic_ids.append(execution_adapter.diagnostic(
                    severity="error",
                    code="PROCESS_EXIT",
                    message=f"{spec.name} exited with code {returncode}",
                    failure_kind=failure_kind,
                    log_path=log_file,
                    evidence=(log_file,) if log_file else (),
                ))
            except Exception:
                pass

        result_metadata["execution_process_id"] = execution_adapter.active_process_id if execution_adapter is not None else ""
        result_metadata["execution_diagnostic_ids"] = list(diagnostic_ids)

        if isinstance(spec.metadata.get("reuse_fingerprint"), dict):
            from .core.reuse import finalize_spec_reuse_metadata
            result_metadata.update(finalize_spec_reuse_metadata(
                spec,
                evidence_refs=((log_file,) if log_file else ()),
                execution_status=("cancelled" if returncode == 130 else "succeeded" if returncode == 0 else "failed"),
            ))
        gradle_plan_payload = result_metadata.get("gradle_execution_plan")
        if isinstance(gradle_plan_payload, dict):
            from .gradle_planning import GradleExecutionReceipt

            artifact_refs: list[str] = []
            producer_artifact_refs: dict[str, list[str]] = {}
            artifacts_payload = result_metadata.get("artifacts")
            if isinstance(artifacts_payload, dict):
                for item in artifacts_payload.get("items", []) or []:
                    if not isinstance(item, dict):
                        continue
                    ref = str(item.get("attachment_id") or item.get("ref_id") or item.get("path") or "").strip()
                    if ref and ref not in artifact_refs:
                        artifact_refs.append(ref)
                    producer = str(item.get("producer_task") or "").strip()
                    if producer and ref:
                        bucket = producer_artifact_refs.setdefault(producer, [])
                        if ref not in bucket:
                            bucket.append(ref)
            report_refs: list[str] = []
            for report_key in ("tests", "lint", "coverage", "packages", "python_audit"):
                report_payload = result_metadata.get(report_key)
                if not isinstance(report_payload, dict):
                    continue
                for value in report_payload.get("report_files", []) or []:
                    ref = str(value or "").strip()
                    if ref and ref not in report_refs:
                        report_refs.append(ref)
            progress_payload = structured_process_state.get("gradle_progress")
            if not isinstance(progress_payload, dict):
                progress_payload = last_progress_snapshot if isinstance(last_progress_snapshot, dict) else {}
            receipt = GradleExecutionReceipt.from_plan_metadata(
                gradle_plan_payload,
                progress=progress_payload,
                success=returncode == 0,
                process_executed=not bool(result_metadata.get("reuse_decision") == "reuse" or result_metadata.get("reused")),
                artifact_refs=artifact_refs, producer_artifact_refs=producer_artifact_refs,
                report_refs=report_refs, diagnostic_ids=diagnostic_ids,
                duration_seconds=duration,
                exo_run_ref=(execution_adapter.active_process_id if execution_adapter is not None else ""),
                rer_ref=str(result_metadata.get("reuse_result_identity") or result_metadata.get("reuse_fingerprint_digest") or ""),
                run_ref=str(event_run_id or ""),
            )
            result_metadata["gradle_execution_receipt"] = receipt.as_dict()
            result_metadata["gradle_receipt_id"] = receipt.receipt_id

        result = OperationResult(spec.name, effective_argv, returncode, duration, output, issues, log_path=log_file, failure_kind=failure_kind, metadata=result_metadata)
        results.append(result)
        command_status = "cancelled" if returncode == 130 else "succeeded" if returncode == 0 else "failed"
        if command_part_node_id:
            publish_event(NodeFinished(
                run_id=event_run_id,
                node_id=command_part_node_id,
                parent_id=workflow_node_key,
                status=command_status,
                exit_code=returncode,
                duration_seconds=duration,
                failure_kind=failure_kind,
                process_id=execution_adapter.active_process_id if execution_adapter is not None else "",
                diagnostic_ids=tuple(diagnostic_ids),
                evidence=(log_file,) if log_file else (),
                source="runner",
                metadata={"part_index": workflow_part_index, "part_total": workflow_part_total},
            ))
        publish_event(TaskFinished(
                    run_id=event_run_id,
            task=spec.name,
            success=returncode == 0,
            status=str(result_metadata.get("workflow_node_state") or command_status),
            exit_code=returncode,
            duration_seconds=duration,
            failure_kind=failure_kind,
            process_id=(execution_adapter.active_process_id if execution_adapter is not None and not command_part_node_id else ""),
            diagnostic_ids=(tuple(diagnostic_ids) if not command_part_node_id else ()),
            evidence=((log_file,) if log_file and not command_part_node_id else ()),
            node_id=event_node_id,
            parent_id=workflow_parent_id,
            source="runner",
            metadata={
                "returncode": returncode,
                "duration_seconds": duration,
                "failure_kind": failure_kind,
                **({key: result_metadata[key] for key in (
                    "reuse_fingerprint", "reuse_output_manifest", "reuse_result_identity",
                    "reuse_evidence_refs", "reuse_portability_class",
                    "gradle_execution_receipt", "gradle_receipt_id",
                    "gradle_takeover", "gradle_takeover_receipt_id",
                    "gradle_takeover_semantic_identity", "gradle_takeover_origin",
                    "gradle_takeover_origin_chain", "gradle_takeover_destination_identity",
                    "gradle_takeover_launcher_identity", "gradle_takeover_ingress_status",
                ) if key in result_metadata}),
            },
        ))
        if tasker_progress is not None and not dry_run:
            tasker_progress.phase_finished(result)
        raw_sections.append(raw_text)

        # Cancellation/interruption is an orchestration terminal state, not an
        # ordinary phase failure. Never advance to later phases merely because
        # continue_on_error was requested.
        if returncode == 130 or (returncode != 0 and not continue_on_error):
            if not workflow_graph_active:
                break
            # A workflow graph keeps walking the already-planned node list so
            # dependents receive explicit blocked/skipped states. No later
            # command is executed after cancellation or fail-fast failure.
            continue

    gradle_options = config.runner_options.get("gradle", {})
    gradle_options = dict(gradle_options) if isinstance(gradle_options, dict) else {}
    stop_gradle_after_run = bool(gradle_options.get("stop_after_run", False))
    if used_gradle and stop_gradle_after_run:
        gradle_result, gradle_raw = _shutdown_gradle_daemons(
            config, project_root=gradle_project_root, dry_run=dry_run, log_path=log_path
        )
        results.append(gradle_result)
        raw_sections.append(gradle_raw)

    if used_dotnet and stop_dotnet_after_run:
        shutdown_result, shutdown_raw = _shutdown_dotnet_build_server(
            config,
            dry_run=dry_run,
            log_path=log_path,
        )
        results.append(shutdown_result)
        raw_sections.append(shutdown_raw)

    if not dry_run:
        try:
            from .test_runstore import persist_test_run
            test_run_path = persist_test_run(config.repo_root, results, run_id=event_run_id)
            if test_run_path is not None:
                for item in results:
                    if isinstance(item.metadata, dict) and isinstance(item.metadata.get("test_result"), dict):
                        item.metadata["test_run_path"] = str(test_run_path)
        except Exception as exc:
            # Test-result persistence is historical/evidence plumbing and must
            # never turn an otherwise valid test execution into a false failure.
            for item in results:
                if isinstance(item.metadata, dict) and isinstance(item.metadata.get("test_result"), dict):
                    item.metadata["test_run_persistence_warning"] = f"{type(exc).__name__}: {exc}"

    return results, "\n\n".join(raw_sections)

def shutdown_dotnet_build_server(
    config: ProjectConfig,
    *,
    dry_run: bool = False,
    log_path: Path | str | None = None,
) -> tuple[OperationResult, str]:
    """Stop shared .NET build servers once at an outer workflow boundary."""
    return _shutdown_dotnet_build_server(config, dry_run=dry_run, log_path=log_path)


def _artifact_config_declaration(manifest: dict) -> dict[str, str]:
    return {
        "target": artifact_target(manifest),
        "solution": artifact_solution(manifest),
        "project": str(manifest.get("run_project") or manifest.get("project") or "").strip(),
    }


def _declaration_has_dotnet_target(declaration: dict[str, str]) -> bool:
    return bool(declaration.get("solution") or declaration.get("project"))


def _preview_with_config_registration(preview: dict, declarations: Sequence[dict[str, str]]) -> dict:
    if not any(_declaration_has_dotnet_target(item) for item in declarations):
        return preview
    updated = dict(preview)
    original_changes = [dict(item) for item in preview.get("changes", []) if isinstance(item, dict)]
    already_listed = any(str(item.get("path") or "") == ".devtool.toml" for item in original_changes)
    if not already_listed:
        original_changes.append({"path": ".devtool.toml", "kind": "modify"})
    updated["changes"] = original_changes
    if not already_listed:
        counts = dict(preview.get("counts", {}))
        counts["modify"] = int(counts.get("modify", 0) or 0) + 1
        updated["counts"] = counts
    return updated


def _register_artifact_config(repo_root: Path, manifest: dict, *, target_override: str = "") -> dict[str, str]:
    declaration = _artifact_config_declaration(manifest)
    if target_override.strip():
        declaration["target"] = target_override.strip()
    if not _declaration_has_dotnet_target(declaration):
        return {}
    return ensure_artifact_target_config(repo_root, **declaration)


def _resolve_executable_artifact_plan(
    repo_root: Path,
    artifact: str | Path,
    *,
    resolved_plan: object | None = None,
    ignore_checksum: bool = False,
):
    from .artifact_lifecycle import ArtifactEnvelopeResolver, ResolvedArtifactPlan
    if resolved_plan is not None:
        if not isinstance(resolved_plan, ResolvedArtifactPlan):
            raise TypeError("resolved_plan must be a ResolvedArtifactPlan")
        plan = resolved_plan
    else:
        plan = ArtifactEnvelopeResolver().resolve_qualified_plan(
            artifact, repo_root=repo_root, public_runner=False,
            isolation=str(os.environ.get("DEVTOOL_TRANSACTION_ISOLATION") or "worktree"),
            ignore_checksum=ignore_checksum,
        )
    actual = resolve_artifact_path(artifact).resolve(strict=False)
    planned = Path(str(plan.artifact.source.get("path") or "")).resolve(strict=False)
    if planned != actual:
        raise RuntimeError(f"ResolvedArtifactPlan source mismatch: planned {planned}, requested {actual}")
    # Qualification binds the transport bytes, not merely a pathname. A caller
    # must not be able to replace an artifact between plan construction and
    # mutation and then rely on --ignore-checksum to execute stale authority.
    if not actual.is_file():
        raise RuntimeError(f"ResolvedArtifactPlan artifact is unavailable: {actual}")
    actual_transport_sha256 = sha256_file(actual)
    planned_transport_sha256 = str(plan.artifact.identities.transport_sha256 or "")
    if not planned_transport_sha256 or actual_transport_sha256 != planned_transport_sha256:
        raise RuntimeError(
            "ResolvedArtifactPlan transport identity mismatch: qualified bytes changed "
            f"(planned={planned_transport_sha256 or '<missing>'}, actual={actual_transport_sha256})"
        )
    if not plan.executable:
        detail = "; ".join(f"{item.code}: {item.message}" for item in plan.qualification.blockers)
        raise RuntimeError("Canonical artifact qualification failed: " + (detail or "plan is not executable"))
    return plan


def _plan_mutation_owner(source_identity: str) -> str:
    value = str(source_identity or "")
    if "::" in value:
        return value.split("::", 1)[0]
    return value


def _plan_mutation_is_generated_config(source_identity: str) -> bool:
    return str(source_identity or "").endswith("devtool:artifact-config-registration")


def _canonical_snapshot_extra_paths(repo_root: Path, plan: object) -> list[str]:
    from .artifact_lifecycle import ResolvedArtifactPlan
    if not isinstance(plan, ResolvedArtifactPlan) or not plan.expected_final_tree.exact:
        return []
    present = {str(item.path) for item in plan.expected_final_tree.entries if item.state == "present"}
    preserve = {str(item.path) for item in plan.expected_final_tree.entries if item.state == "preserve"}

    def protected(rel: str, path: Path) -> bool:
        if rel in present or rel in preserve:
            return True
        # Preserving a directory preserves its subtree. Existing directories
        # that are required as parents of an expected file must also survive.
        if any(rel.startswith(prefix.rstrip("/") + "/") for prefix in preserve):
            return True
        if path.is_dir() and any(item.startswith(rel.rstrip("/") + "/") for item in present | preserve):
            return True
        return False

    candidates: list[str] = []
    root = repo_root.resolve()
    for path in sorted(root.rglob("*"), key=lambda item: (len(item.relative_to(root).parts), item.as_posix())):
        rel_path = path.relative_to(root)
        if any(part in {".git", ".devtool"} for part in rel_path.parts):
            continue
        rel = rel_path.as_posix()
        if protected(rel, path):
            continue
        candidates.append(rel)

    # A directory deletion already covers its descendants. Keep only minimal
    # roots so transaction backup/rollback does not independently own the same
    # bytes through multiple overlapping affected-path entries.
    selected: list[str] = []
    for rel in candidates:
        if any(rel == parent or rel.startswith(parent.rstrip("/") + "/") for parent in selected):
            continue
        selected.append(rel)
    return selected


def _canonical_plan_preview(repo_root: Path, plan: object) -> dict[str, object]:
    from .artifact_lifecycle import MutationKind, ResolvedArtifactPlan
    if not isinstance(plan, ResolvedArtifactPlan):
        raise TypeError("canonical preview requires ResolvedArtifactPlan")
    root = repo_root.resolve()
    changes_by_path: dict[str, dict[str, str]] = {}
    order: list[str] = []

    def record(path: str, kind: str) -> None:
        rel = str(path or "").replace("\\", "/").strip("/")
        if not rel:
            return
        if rel not in changes_by_path:
            order.append(rel)
        changes_by_path[rel] = {"path": rel, "kind": kind, "authority": "resolved-artifact-plan"}

    for mutation in plan.mutations.mutations:
        if mutation.kind == MutationKind.PRESERVE:
            continue
        if mutation.kind == MutationKind.DELETE:
            record(mutation.path, "delete")
            continue
        destination = _safe_destination_path(root, Path(mutation.path))
        record(mutation.path, "modify" if destination.exists() or destination.is_symlink() else "add")

    for rel in _canonical_snapshot_extra_paths(root, plan):
        record(rel, "delete")

    for lease in plan.write_leases:
        if lease.path in changes_by_path:
            continue
        kind = f"{str(lease.owner or 'artifact')}-write"
        record(lease.path, kind)

    changes = [changes_by_path[path] for path in order]
    counts: dict[str, int] = {}
    for change in changes:
        kind = str(change["kind"])
        counts[kind] = counts.get(kind, 0) + 1
    return {
        "artifact": str(plan.artifact.source.get("path") or ""),
        "plan_id": plan.plan_id,
        "canonical_mutation_contract": True,
        "snapshot": bool(plan.expected_final_tree.exact),
        "files": sum(1 for item in plan.mutations.mutations if item.kind not in {MutationKind.DELETE, MutationKind.PRESERVE}),
        "changes": changes,
        "counts": counts,
    }


def _member_map_for_plan_source(artifact_path: Path) -> dict[str, object]:
    from .artifacts import list_archive_members
    result: dict[str, object] = {}
    for member in list_archive_members(artifact_path, include_data=True):
        if not member.is_dir:
            result[str(member.name)] = member
    return result


def _materialize_plan_bundle_entries(artifact_path: Path, plan: object, target_dir: Path) -> list[tuple[object, Path]]:
    """Materialize nested archives using only descriptors already captured by the plan."""
    from .artifact_lifecycle import ResolvedArtifactPlan
    if not isinstance(plan, ResolvedArtifactPlan):
        raise TypeError("canonical bundle materialization requires ResolvedArtifactPlan")
    if not plan.bundle_entries:
        raise RuntimeError("Canonical bundle plan has no resolved bundle entries")
    members = _member_map_for_plan_source(artifact_path)
    result: list[tuple[object, Path]] = []
    target_dir.mkdir(parents=True, exist_ok=True)
    for entry in plan.bundle_entries:
        member = members.get(entry.bundle_path)
        if member is None or getattr(member, "is_dir", False):
            if entry.required:
                raise RuntimeError(f"Canonical bundle member is unavailable: {entry.bundle_path}")
            continue
        data = getattr(member, "data", None)
        if data is None:
            raise RuntimeError(f"Canonical bundle member bytes are unavailable: {entry.bundle_path}")
        if entry.size is not None and len(data) != int(entry.size):
            raise RuntimeError(f"Canonical bundle member size changed: {entry.bundle_path}")
        if entry.sha256 and hashlib.sha256(data).hexdigest().lower() != entry.sha256.lower():
            raise RuntimeError(f"Canonical bundle member digest changed: {entry.bundle_path}")
        destination = target_dir / entry.bundle_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        result.append((entry, destination))
    return result


def _verify_mutation_source_state(execution_root: Path, mutation: object) -> None:
    expected_source = str(getattr(mutation, "source_sha256", "") or "").strip()
    if not expected_source:
        return
    destination = _safe_destination_path(execution_root, Path(str(getattr(mutation, "path"))))
    if not destination.is_file() or destination.is_symlink():
        raise RuntimeError(
            f"Canonical mutation source mismatch for {getattr(mutation, 'path')}: "
            "expected an existing regular file"
        )
    actual = sha256_file(destination)
    if actual != expected_source:
        raise RuntimeError(
            f"Canonical mutation source mismatch for {getattr(mutation, 'path')}: "
            f"expected {expected_source}, actual {actual}"
        )


def _execute_plan_mutations(
    execution_root: Path,
    artifact_path: Path,
    plan: object,
    *,
    source_owner: str | None = None,
) -> dict[str, object]:
    """Execute only mutations already authorized by ``ResolvedArtifactPlan``.

    Archive membership, delete manifests, and snapshot synchronization are not
    reinterpreted here. The archive supplies bytes; the plan supplies authority.
    """
    from .artifact_lifecycle import MutationKind, ResolvedArtifactPlan
    if not isinstance(plan, ResolvedArtifactPlan):
        raise TypeError("canonical mutation execution requires ResolvedArtifactPlan")
    root = execution_root.resolve()
    members = _member_map_for_plan_source(artifact_path)
    applied = 0
    deleted: list[str] = []
    skipped_generated = 0

    selected = []
    for mutation in plan.mutations.mutations:
        if source_owner is not None and _plan_mutation_owner(mutation.source_identity) != source_owner:
            continue
        selected.append(mutation)

    # Snapshot exactness is part of the top-level plan and happens once, before
    # payload writes. A bundle never carries exact snapshot semantics.
    if source_owner is None and plan.expected_final_tree.exact:
        for rel in sorted(_canonical_snapshot_extra_paths(root, plan), key=lambda item: (item.count("/"), item), reverse=True):
            target = _safe_destination_path(root, Path(rel))
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
                deleted.append(rel)
            elif target.exists() or target.is_symlink():
                target.unlink()
                deleted.append(rel)

    for mutation in selected:
        if mutation.kind == MutationKind.PRESERVE:
            continue
        if _plan_mutation_is_generated_config(mutation.source_identity):
            skipped_generated += 1
            continue
        destination = _safe_destination_path(root, Path(mutation.path))
        _verify_mutation_source_state(root, mutation)
        if mutation.kind == MutationKind.DELETE:
            if destination == root:
                raise RuntimeError("Canonical mutation contract attempted to delete repository root")
            if destination.is_dir() and not destination.is_symlink():
                shutil.rmtree(destination)
                deleted.append(mutation.path)
            elif destination.exists() or destination.is_symlink():
                destination.unlink()
                deleted.append(mutation.path)
            applied += 1
            continue

        member = members.get(str(mutation.path))
        if member is None:
            raise RuntimeError(
                f"Canonical mutation {mutation.kind.value} for {mutation.path} has no matching payload member"
            )
        data = bytes(getattr(member, "data", None) or b"")
        actual_sha = hashlib.sha256(data).hexdigest()
        expected_sha = str(mutation.expected_sha256 or "").strip()
        if expected_sha and actual_sha != expected_sha:
            raise RuntimeError(
                f"Canonical mutation payload mismatch for {mutation.path}: "
                f"expected {expected_sha}, actual {actual_sha}"
            )
        _prepare_file_destination(destination, str(mutation.path))
        destination.write_bytes(data)
        _apply_member_mode(destination, getattr(member, "mode", None))
        applied += 1

    return {"applied": applied, "deleted": deleted, "generated": skipped_generated}


def _plan_authorizes_config_registration(plan: object, *, source_owner: str | None = None) -> bool:
    from .artifact_lifecycle import ResolvedArtifactPlan
    if not isinstance(plan, ResolvedArtifactPlan):
        return False
    for mutation in plan.mutations.mutations:
        if source_owner is not None and _plan_mutation_owner(mutation.source_identity) != source_owner:
            continue
        if mutation.path == ".devtool.toml" and _plan_mutation_is_generated_config(mutation.source_identity):
            return True
    return False


def _artifact_terminal_tree_identity(repo_root: Path) -> str:
    """Return a concrete terminal source-tree identity for artifact evidence."""
    root = repo_root.resolve()
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return "git-tree:" + completed.stdout.strip()
    except Exception:
        pass
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if rel.startswith(".git/") or rel.startswith(".devtool/run/") or "/__pycache__/" in f"/{rel}/":
            continue
        try:
            if path.is_file() or path.is_symlink():
                digest.update(rel.encode("utf-8"))
                digest.update(b"\0")
                if path.is_symlink():
                    digest.update(b"symlink:")
                    digest.update(os.readlink(path).encode("utf-8", "surrogateescape"))
                else:
                    digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
                digest.update(b"\0")
        except OSError:
            continue
    return "tree-sha256:" + digest.hexdigest()


def _artifact_execution_status(result: OperationResult) -> str:
    metadata = dict(result.metadata or {})
    if bool(metadata.get("rolled_back_during_apply")):
        return "rolled-back" if int(metadata.get("rollback_returncode") or 0) == 0 else "rollback-failed"
    if int(result.returncode or 0) != 0:
        return "failed"
    if bool(metadata.get("validation_complete")) or str(metadata.get("validation_status") or "").lower() in {"passed", "succeeded"}:
        return "succeeded"
    if metadata.get("transaction_path") or metadata.get("transaction_id"):
        return "applied-pending-validation"
    return "succeeded"


def _persist_artifact_execution_record(repo_root: Path, plan: object, result: OperationResult) -> None:
    from .artifact_lifecycle import ArtifactExecutionRecord, ResolvedArtifactPlan
    from .core_runtime import private_run_root
    from .run_store import AttachmentDescriptor, RunCatalog
    import uuid as _uuid
    from datetime import datetime, timezone

    if not isinstance(plan, ResolvedArtifactPlan):
        return
    metadata = dict(result.metadata or {})
    canonical_repo = Path(str(metadata.get("transaction_primary_repo_root") or repo_root)).resolve()
    root = private_run_root(canonical_repo)
    catalog = RunCatalog.open(root)

    # Reuse ambient run identity only when RUN-V3 itself confirms this is a live
    # canonical operation. Direct/library callers with stale process environment
    # receive an independent durable run without mutating os.environ.
    ambient = str(os.environ.get("DEVTOOL_RUN_ID") or "").strip()
    ambient_row = dict(catalog.runs.get(ambient, {})) if ambient else {}
    ambient_live = bool(ambient and str(ambient_row.get("status") or "") == "running")
    run_id = ambient if ambient_live else _uuid.uuid4().hex

    execution_status = _artifact_execution_status(result)
    terminal_tree_identity = _artifact_terminal_tree_identity(canonical_repo)
    final_identity = hashlib.sha256(json.dumps({
        "returncode": int(result.returncode or 0),
        "execution_status": execution_status,
        "transaction": str(metadata.get("transaction_id") or ""),
        "artifact": plan.artifact.identities.semantic_sha256,
        "plan": plan.plan_id,
        "terminal_tree_identity": terminal_tree_identity,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    record = ArtifactExecutionRecord.create(
        run_id=run_id,
        artifact_id=plan.artifact.artifact_id,
        plan_id=plan.plan_id,
        qualification_receipt_id=plan.qualification.receipt_id,
        transaction_id=str(metadata.get("transaction_id") or ""),
        rollback_receipt_id=str(metadata.get("rollback_receipt_id") or ""),
        trust_evidence_ids=plan.qualification.trust_evidence_ids,
        final_state_identity=final_identity,
        execution_status=execution_status,
    )
    record_dir = root / run_id / "artifacts"
    record_dir.mkdir(parents=True, exist_ok=True)

    qualification_path = record_dir / (plan.qualification.receipt_id.replace(":", "-") + ".json")
    qualification_tmp = qualification_path.with_suffix(qualification_path.suffix + ".tmp")
    qualification_tmp.write_text(
        json.dumps(plan.qualification.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(qualification_tmp, qualification_path)

    record_path = record_dir / (record.record_id.replace(":", "-") + ".json")
    tmp = record_path.with_suffix(record_path.suffix + ".tmp")
    tmp.write_text(json.dumps(record.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, record_path)

    status = execution_status
    row = dict(catalog.runs.get(run_id, {}))
    row.update({
        "schema_version": 3,
        "run_id": run_id,
        "operation_id": hashlib.sha256(json.dumps({
            "artifact": plan.artifact.identities.semantic_sha256,
            "plan": plan.plan_id,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32],
        "command_id": str(row.get("command_id") or "artifact.apply"),
        "status": str(row.get("status") or status) if ambient_live else status,
        "provider": str(row.get("provider") or "local"),
        "artifact_id": plan.artifact.artifact_id,
        "artifact_plan_id": plan.plan_id,
        "artifact_qualification_receipt_id": plan.qualification.receipt_id,
        "artifact_execution_status": execution_status,
        "artifact_terminal_tree_identity": terminal_tree_identity,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    if not ambient_live:
        row.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    catalog.add_run(row)

    qualification_attachment = AttachmentDescriptor.from_file(
        run_id=run_id,
        path=qualification_path,
        kind="artifact-qualification-receipt",
        media_type="application/json",
    )
    execution_attachment = AttachmentDescriptor.from_file(
        run_id=run_id,
        path=record_path,
        kind="artifact-execution-record",
        media_type="application/json",
    )
    qualification_ref = catalog.add_attachment(qualification_attachment)
    execution_ref = catalog.add_attachment(execution_attachment)
    catalog.save()

    result.metadata.setdefault("resolved_artifact_plan_id", plan.plan_id)
    result.metadata.setdefault("artifact_qualification_receipt_id", plan.qualification.receipt_id)
    result.metadata.setdefault("artifact_qualification_attachment_id", qualification_attachment.attachment_id)
    result.metadata.setdefault("artifact_qualification_reference", qualification_ref.ref_id)
    result.metadata.setdefault("artifact_execution_record_id", record.record_id)
    result.metadata.setdefault("artifact_execution_status", execution_status)
    result.metadata.setdefault("artifact_terminal_tree_identity", terminal_tree_identity)
    result.metadata.setdefault("artifact_execution_attachment_id", execution_attachment.attachment_id)
    result.metadata.setdefault("artifact_execution_reference", execution_ref.ref_id)
    result.metadata.setdefault("run_id", run_id)


def apply_overlay(
    repo_root: Path,
    artifact: str,
    *,
    force: bool = False,
    yes: bool = False,
    dry_run: bool = False,
    log_path: Path | str | None = None,
    preview: dict | None = None,
    ignore_checksum: bool = False,
    skip_artifact_validation: bool = False,
    lifecycle_progress: Callable[[str, str, OperationResult | None], None] | None = None,
    render_hook_progress: bool = False,
    stream_hook_output: bool = False,
    resolved_plan: object | None = None,
) -> OperationResult:
    """Apply one overlay without leaking transaction-root process state."""

    with preserve_environment_keys((
        "DEVTOOL_TRANSACTION_REPO_ROOT",
        "DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT",
        "DEVTOOL_VALIDATION_STATE_REPO_ROOT",
        "DEVTOOL_TRANSACTION_ISOLATION",
    )):
        os.environ.setdefault("DEVTOOL_TRANSACTION_ISOLATION", "worktree")
        return _apply_overlay_impl(
            repo_root, artifact, force=force, yes=yes, dry_run=dry_run,
            log_path=log_path, preview=preview, ignore_checksum=ignore_checksum,
            skip_artifact_validation=skip_artifact_validation,
            lifecycle_progress=lifecycle_progress,
            render_hook_progress=render_hook_progress,
            stream_hook_output=stream_hook_output, resolved_plan=resolved_plan,
        )


def _apply_overlay_impl(
    repo_root: Path,
    artifact: str,
    *,
    force: bool = False,
    yes: bool = False,
    dry_run: bool = False,
    log_path: Path | str | None = None,
    preview: dict | None = None,
    ignore_checksum: bool = False,
    skip_artifact_validation: bool = False,
    lifecycle_progress: Callable[[str, str, OperationResult | None], None] | None = None,
    render_hook_progress: bool = False,
    stream_hook_output: bool = False,
    resolved_plan: object | None = None,
) -> OperationResult:
    artifact_path = resolve_artifact_path(artifact)
    plan = _resolve_executable_artifact_plan(repo_root, artifact_path, resolved_plan=resolved_plan, ignore_checksum=ignore_checksum)
    if str(plan.artifact.subtype.value) == "bundle":
        return apply_overlay_bundle(
            repo_root,
            artifact,
            force=force,
            yes=yes,
            dry_run=dry_run,
            log_path=log_path,
            preview=preview,
            ignore_checksum=ignore_checksum,
            skip_artifact_validation=skip_artifact_validation,
            lifecycle_progress=lifecycle_progress,
            render_hook_progress=render_hook_progress,
            stream_hook_output=stream_hook_output, resolved_plan=plan,
        )
    argv = ["devtool", "extract-overlay", str(artifact_path), "-C", str(repo_root)]
    return _apply_archive(repo_root, artifact_path, argv, delete_artifact=True, snapshot=False, force=force, yes=yes, dry_run=dry_run, log_path=log_path, preview=preview, ignore_checksum=ignore_checksum, lifecycle_progress=lifecycle_progress, render_hook_progress=render_hook_progress, stream_hook_output=stream_hook_output, resolved_plan=plan)


def apply_overlay_bundle(
    repo_root: Path,
    artifact: str | Path | Sequence[str | Path],
    *,
    force: bool = False,
    yes: bool = False,
    dry_run: bool = False,
    log_path: Path | str | None = None,
    preview: dict | None = None,
    source_artifacts: Sequence[str | Path] | None = None,
    ignore_checksum: bool = False,
    skip_artifact_validation: bool = False,
    lifecycle_progress: Callable[[str, str, OperationResult | None], None] | None = None,
    render_hook_progress: bool = False,
    stream_hook_output: bool = False,
    resolved_plan: object | None = None,
) -> OperationResult:
    """Apply an overlay bundle without leaking transaction-root process state."""

    with preserve_environment_keys((
        "DEVTOOL_TRANSACTION_REPO_ROOT",
        "DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT",
        "DEVTOOL_VALIDATION_STATE_REPO_ROOT",
        "DEVTOOL_TRANSACTION_ISOLATION",
    )):
        os.environ.setdefault("DEVTOOL_TRANSACTION_ISOLATION", "worktree")
        return _apply_overlay_bundle_impl(
            repo_root, artifact, force=force, yes=yes, dry_run=dry_run,
            log_path=log_path, preview=preview, source_artifacts=source_artifacts,
            ignore_checksum=ignore_checksum, skip_artifact_validation=skip_artifact_validation,
            lifecycle_progress=lifecycle_progress, render_hook_progress=render_hook_progress,
            stream_hook_output=stream_hook_output, resolved_plan=resolved_plan,
        )


def _apply_overlay_bundle_impl(
    repo_root: Path,
    artifact: str | Path | Sequence[str | Path],
    *,
    force: bool = False,
    yes: bool = False,
    dry_run: bool = False,
    log_path: Path | str | None = None,
    preview: dict | None = None,
    source_artifacts: Sequence[str | Path] | None = None,
    ignore_checksum: bool = False,
    skip_artifact_validation: bool = False,
    lifecycle_progress: Callable[[str, str, OperationResult | None], None] | None = None,
    render_hook_progress: bool = False,
    stream_hook_output: bool = False,
    resolved_plan: object | None = None,
) -> OperationResult:
    raw_artifacts = [artifact] if isinstance(artifact, (str, Path)) else list(artifact)
    if not raw_artifacts:
        output = "No overlay artifacts were provided."
        path = str(write_log(output, log_path, append=log_path is not None))
        return OperationResult("apply-overlays", ["devtool", "apply-overlays"], 1, 0.0, output, [], log_path=path)

    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    try:
        if len(raw_artifacts) == 1:
            artifact_path = resolve_artifact_path(raw_artifacts[0])
            original_paths = [artifact_path]
        else:
            temp_dir = tempfile.TemporaryDirectory(prefix="devtool-adhoc-overlays-")
            original_paths = [resolve_artifact_path(item) for item in raw_artifacts]
            artifact_path = create_overlay_bundle(Path(temp_dir.name) / "adhoc_overlay_bundle.zip", original_paths, name="adhoc_overlay_bundle")
        plan = _resolve_executable_artifact_plan(repo_root, artifact_path, resolved_plan=resolved_plan, ignore_checksum=ignore_checksum)
        argv = ["devtool", "apply-overlays", *(str(item) for item in original_paths), "-C", str(repo_root)]
        start = time.perf_counter()
        if dry_run:
            text = "$ " + " ".join(argv)
            path = str(write_log(text, log_path, append=log_path is not None))
            return OperationResult("apply-overlays", argv, 0, 0.0, text, [], skipped=True, log_path=path)
        if not artifact_path.exists():
            output = f"Artifact not found: {artifact_path}"
            path = str(write_log(output, log_path, append=log_path is not None))
            return OperationResult("apply-overlays", argv, 1, 0.0, output, [], log_path=path)

        transaction: dict | None = None
        rollback_result: OperationResult | None = None
        issues: list[Issue] = []
        failure_kind = ""
        preflight_results: list[OperationResult] = []
        try:
            # The qualified plan is the only mutation authority. The bundle
            # manifest here is the immutable projection captured by that plan;
            # preview/delete/config discovery must not reparse the archive.
            bundle_manifest = dict(plan.artifact.manifest)
            preview_data = _canonical_plan_preview(repo_root, plan)
            checksum_paths = [
                str(item.get("path") or "")
                for item in preview_data.get("changes", [])
                if isinstance(item, dict) and str(item.get("path") or "").strip()
            ]
            transaction = create_apply_transaction(
                repo_root,
                artifact_path,
                artifact_type="overlay_bundle",
                preview=preview_data,
                canonical_plan=True,
            )
            execution_root = transaction_execution_root(transaction)
            os.environ["DEVTOOL_TRANSACTION_REPO_ROOT"] = str(execution_root)
            os.environ["DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT"] = str(repo_root.resolve())
            top_before = _run_required_preflight(
                execution_root,
                artifact_path,
                phase="before_extract",
                manifest=bundle_manifest,
                log_path=log_path,
                ignore_checksum=ignore_checksum,
                checksum_paths=checksum_paths,
                lifecycle_progress=lifecycle_progress,
                render_hook_progress=render_hook_progress,
                stream_hook_output=stream_hook_output,
            )
            if top_before is not None:
                preflight_results.append(top_before)
            skipped_metadata = 0
            deleted_paths: list[str] = []
            applied: list[str] = []
            registrations: list[dict[str, str]] = []
            with tempfile.TemporaryDirectory(prefix="devtool-bundle-apply-") as tmp:
                entries = _materialize_plan_bundle_entries(artifact_path, plan, Path(tmp))
                for entry, entry_path in entries:
                    # All nested mutation/config/hook semantics are immutable
                    # projections captured by ResolvedArtifactPlan. The runner
                    # reads archive bytes only; it never reparses a nested manifest.
                    nested_manifest = entry.manifest
                    nested_before = _run_required_preflight(
                        execution_root,
                        entry_path,
                        phase="before_extract",
                        manifest=nested_manifest,
                        target=entry.target,
                        log_path=log_path,
                        ignore_checksum=ignore_checksum,
                        checksum_paths=checksum_paths,
                        lifecycle_progress=lifecycle_progress,
                        render_hook_progress=render_hook_progress,
                        stream_hook_output=stream_hook_output,
                    )
                    if nested_before is not None:
                        preflight_results.append(nested_before)
                    source_owner = entry.source_owner
                    mutation_result = _execute_plan_mutations(
                        execution_root, entry_path, plan, source_owner=source_owner,
                    )
                    deleted_paths.extend(str(item) for item in mutation_result.get("deleted", []))
                    if _plan_authorizes_config_registration(plan, source_owner=source_owner):
                        registration = _register_artifact_config(
                            execution_root, nested_manifest, target_override=entry.target
                        )
                        if registration:
                            registrations.append(registration)
                    nested_after = _run_required_preflight(
                        execution_root,
                        entry_path,
                        phase="after_extract",
                        manifest=nested_manifest,
                        target=entry.target,
                        log_path=log_path,
                        ignore_checksum=ignore_checksum,
                        checksum_paths=checksum_paths,
                        lifecycle_progress=lifecycle_progress,
                        render_hook_progress=render_hook_progress,
                        stream_hook_output=stream_hook_output,
                    )
                    if nested_after is not None:
                        preflight_results.append(nested_after)
                    label = entry.label or Path(entry.bundle_path).name
                    target_note = f" target={entry.target}" if entry.target else ""
                    applied.append(f"[{entry.index}/{len(entries)}] {label}{target_note}")
            top_after = _run_required_preflight(
                execution_root,
                artifact_path,
                phase="after_extract",
                manifest=bundle_manifest,
                log_path=log_path,
                ignore_checksum=ignore_checksum,
                checksum_paths=checksum_paths,
                lifecycle_progress=lifecycle_progress,
                render_hook_progress=render_hook_progress,
                stream_hook_output=stream_hook_output,
            )
            if top_after is not None:
                preflight_results.append(top_after)
            checkpoint_transaction_applied(transaction.get("path"))
            output = f"Applied overlay bundle from {artifact_path}"
            output += f"\nTransaction backup: {transaction.get('transaction_id')}"
            if preflight_results:
                phases = ", ".join(str((item.metadata or {}).get("preflight_phase") or "preflight") for item in preflight_results)
                output += f"\nPreflight scripts passed: {phases}."
            if applied:
                output += "\nApplied overlays:\n" + "\n".join(applied)
            if skipped_metadata:
                output += f"\nSkipped {skipped_metadata} metadata/cache file(s)."
            if deleted_paths:
                output += f"\nDeleted {len(deleted_paths)} path(s) from nested .devtool-delete file(s)."
            if registrations:
                output += f"\nRegistered {len(registrations)} target(s) in .devtool.toml."
            rc = 0
        except KeyboardInterrupt:
            rc = 130
            output = "Interrupted during overlay bundle apply."
            if transaction:
                rollback_result = rollback_transaction(transaction.get("path"), reason="interrupted during overlay bundle apply")
                output += "\n" + rollback_result.output
        except _ArtifactPreflightFailure as exc:
            rc = exc.result.returncode or 1
            failure_kind = "preflight_failure"
            issues = list(exc.result.issues)
            output = f"{exc}\n{exc.result.output}"
            if transaction:
                rollback_result = rollback_transaction(transaction.get("path"), reason=f"preflight failed during {exc.phase}")
                output += "\n" + rollback_result.output
                if rollback_result.returncode != 0:
                    output += "\nRollback failed; manual intervention may be required."
        except BaseException as exc:
            output = f"{type(exc).__name__}: {exc}"
            rc = 1
            if transaction:
                rollback_result = rollback_transaction(transaction.get("path"), reason="error during overlay bundle apply")
                output += "\n" + rollback_result.output
                if rollback_result.returncode != 0:
                    output += "\nRollback failed; manual intervention may be required."
        duration = time.perf_counter() - start
        path = str(write_log(output, log_path, append=log_path is not None))
        metadata = {}
        if transaction:
            tx_id = str(transaction.get("transaction_id") or "")
            tx_path = str(transaction.get("path") or "") if tx_id else ""
            cleanup_sources = [str(resolve_artifact_path(item)) for item in (source_artifacts or original_paths)]
            metadata = {
                "transaction_id": tx_id,
                "transaction_path": tx_path,
                "artifact_path": str(cleanup_sources[0] if len(cleanup_sources) == 1 else artifact_path),
                "artifact_paths": cleanup_sources,
                "delete_artifact_on_success": True,
                "artifact_type": "overlay_bundle",
                "config_paths": sorted({item.get("path", "") for item in registrations if item.get("path")}),
                "transaction_repo_root": str(transaction_execution_root(transaction)),
                "transaction_primary_repo_root": str(repo_root.resolve()),
                "transaction_isolation": str((transaction.get("isolation") or {}).get("effective") or "direct"),
            }
            if rollback_result is not None:
                metadata["rollback_returncode"] = rollback_result.returncode
                metadata["rolled_back_during_apply"] = True
        result = OperationResult(
            "apply-overlays",
            argv,
            rc,
            duration,
            output,
            issues,
            log_path=path,
            metadata=metadata,
            failure_kind=failure_kind,
        )
        _persist_artifact_execution_record(repo_root, plan, result)
        return result
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def apply_snapshot(repo_root: Path, artifact: str, *, force: bool = False, yes: bool = False, dry_run: bool = False, log_path: Path | str | None = None, preview: dict | None = None, ignore_checksum: bool = False, skip_artifact_validation: bool = False, resolved_plan: object | None = None) -> OperationResult:
    with preserve_environment_keys((
        "DEVTOOL_TRANSACTION_REPO_ROOT",
        "DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT",
        "DEVTOOL_VALIDATION_STATE_REPO_ROOT",
        "DEVTOOL_TRANSACTION_ISOLATION",
    )):
        os.environ.setdefault("DEVTOOL_TRANSACTION_ISOLATION", "worktree")
        artifact_path = resolve_artifact_path(artifact)
        argv = ["snapshot", str(artifact_path)]
        try:
            plan = _resolve_executable_artifact_plan(
                repo_root, artifact_path, resolved_plan=resolved_plan,
                ignore_checksum=ignore_checksum,
            )
        except Exception as exc:
            output = f"{type(exc).__name__}: {exc}"
            path = str(write_log(output, log_path, append=log_path is not None))
            return OperationResult(
                "apply-snapshot", argv, 1, 0.0, output, [], log_path=path,
                failure_kind="artifact_failure",
                metadata={"artifact_path": str(artifact_path)},
            )
        return _apply_archive(repo_root, artifact_path, argv, delete_artifact=True, snapshot=True, force=force, yes=yes, dry_run=dry_run, log_path=log_path, preview=preview, ignore_checksum=ignore_checksum, resolved_plan=plan)


def _apply_archive(repo_root: Path, artifact_path: Path, argv: list[str], *, delete_artifact: bool, snapshot: bool, force: bool, yes: bool, dry_run: bool, log_path: Path | str | None = None, preview: dict | None = None, ignore_checksum: bool = False, lifecycle_progress: Callable[[str, str, OperationResult | None], None] | None = None, render_hook_progress: bool = False, stream_hook_output: bool = False, resolved_plan: object | None = None) -> OperationResult:
    plan = _resolve_executable_artifact_plan(repo_root, artifact_path, resolved_plan=resolved_plan, ignore_checksum=ignore_checksum)
    start = time.perf_counter()
    if dry_run:
        text = "$ " + " ".join(argv)
        path = str(write_log(text, log_path, append=log_path is not None))
        return OperationResult("apply-snapshot" if snapshot else "apply-overlay", argv, 0, 0.0, text, [], skipped=True, log_path=path)

    if not artifact_path.exists():
        output = f"Artifact not found: {artifact_path}"
        path = str(write_log(output, log_path, append=log_path is not None))
        return OperationResult("apply-snapshot" if snapshot else "apply-overlay", argv, 1, 0.0, output, [], log_path=path)

    transaction: dict | None = None
    rollback_result: OperationResult | None = None
    registration: dict[str, str] = {}
    issues: list[Issue] = []
    failure_kind = ""
    preflight_results: list[OperationResult] = []
    try:
        # Snapshot is a first-class lifecycle artifact: its declared hooks and
        # validation contract must be the same manifest the qualification gate saw.
        manifest = dict(plan.artifact.manifest)
        preview_data = _canonical_plan_preview(repo_root, plan)
        checksum_paths = [
            str(item.get("path") or "")
            for item in preview_data.get("changes", [])
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]
        transaction = create_apply_transaction(
            repo_root,
            artifact_path,
            artifact_type="snapshot" if snapshot else "overlay",
            preview=preview_data,
            canonical_plan=True,
        )
        execution_root = transaction_execution_root(transaction)
        os.environ["DEVTOOL_TRANSACTION_REPO_ROOT"] = str(execution_root)
        os.environ["DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT"] = str(repo_root.resolve())
        before = _run_required_preflight(
            execution_root,
            artifact_path,
            phase="before_extract",
            manifest=manifest,
            log_path=log_path,
            ignore_checksum=ignore_checksum,
            checksum_paths=checksum_paths,
            lifecycle_progress=lifecycle_progress,
            render_hook_progress=render_hook_progress,
            stream_hook_output=stream_hook_output,
        )
        if before is not None:
            preflight_results.append(before)
        skipped_metadata = 0
        mutation_result = _execute_plan_mutations(execution_root, artifact_path, plan)
        deleted_paths = [str(item) for item in mutation_result.get("deleted", [])]
        if _plan_authorizes_config_registration(plan):
            registration = _register_artifact_config(execution_root, manifest)
        after = _run_required_preflight(
            execution_root,
            artifact_path,
            phase="after_extract",
            manifest=manifest,
            log_path=log_path,
            ignore_checksum=ignore_checksum,
            checksum_paths=checksum_paths,
            lifecycle_progress=lifecycle_progress,
            render_hook_progress=render_hook_progress,
            stream_hook_output=stream_hook_output,
        )
        if after is not None:
            preflight_results.append(after)
        checkpoint_transaction_applied(transaction.get("path"))
        # The original artifact is intentionally not deleted here.  Apply is now
        # transactional across extraction plus any later validation/commit steps,
        # so cleanup is deferred until the CLI knows no rollback happened.
        output = f"Applied {'snapshot' if snapshot else 'overlay'} from {artifact_path}"
        output += f"\nTransaction backup: {transaction.get('transaction_id')}"
        if preflight_results:
            phases = ", ".join(str((item.metadata or {}).get("preflight_phase") or "preflight") for item in preflight_results)
            output += f"\nPreflight scripts passed: {phases}."
        if skipped_metadata:
            output += f"\nSkipped {skipped_metadata} metadata/cache file(s)."
        if deleted_paths:
            output += f"\nDeleted {len(deleted_paths)} path(s) from .devtool-delete."
        if registration:
            output += f"\n{registration.get('action', 'updated').title()} .devtool.toml target: {registration.get('target', '')}."
        rc = 0
    except KeyboardInterrupt as exc:
        rc = 130
        output = "Interrupted during artifact apply."
        if transaction:
            rollback_result = rollback_transaction(transaction.get("path"), reason="interrupted during artifact apply")
            output += "\n" + rollback_result.output
    except _ArtifactPreflightFailure as exc:
        rc = exc.result.returncode or 1
        failure_kind = "preflight_failure"
        issues = list(exc.result.issues)
        output = f"{exc}\n{exc.result.output}"
        if transaction:
            rollback_result = rollback_transaction(transaction.get("path"), reason=f"preflight failed during {exc.phase}")
            output += "\n" + rollback_result.output
            if rollback_result.returncode != 0:
                output += "\nRollback failed; manual intervention may be required."
    except BaseException as exc:
        output = f"{type(exc).__name__}: {exc}"
        rc = 1
        if transaction:
            rollback_result = rollback_transaction(transaction.get("path"), reason="error during artifact apply")
            output += "\n" + rollback_result.output
            if rollback_result.returncode != 0:
                output += "\nRollback failed; manual intervention may be required."
    duration = time.perf_counter() - start
    path = str(write_log(output, log_path, append=log_path is not None))
    metadata = {}
    if transaction:
        tx_id = str(transaction.get("transaction_id") or "")
        tx_path = str(transaction.get("path") or "") if tx_id else ""
        metadata = {
            "transaction_id": tx_id,
            "transaction_path": tx_path,
            "artifact_path": str(artifact_path),
            "delete_artifact_on_success": bool(delete_artifact),
            "config_paths": [registration.get("path", "")] if registration.get("path") else [],
            "transaction_repo_root": str(transaction_execution_root(transaction)),
            "transaction_primary_repo_root": str(repo_root.resolve()),
            "transaction_isolation": str((transaction.get("isolation") or {}).get("effective") or "direct"),
        }
        if rollback_result is not None:
            metadata["rollback_returncode"] = rollback_result.returncode
            metadata["rolled_back_during_apply"] = True
    result = OperationResult(
        "apply-snapshot" if snapshot else "apply-overlay",
        argv,
        rc,
        duration,
        output,
        issues,
        log_path=path,
        metadata=metadata,
        failure_kind=failure_kind,
    )
    _persist_artifact_execution_record(repo_root, plan, result)
    return result



def _artifact_sidecars(artifact_path: Path) -> list[Path]:
    return [
        artifact_path.with_name(artifact_path.name + ".devtool.json"),
        artifact_path.with_name(artifact_path.name + ".manifest.json"),
    ]

def _extract_archive(artifact_path: Path, target: Path) -> int:
    # Force archive layout validation even when manifest resolution would fall
    # back to an empty legacy manifest.  Direct extraction must not accept
    # duplicate normalized names or internal file/child collisions.
    from .artifacts import list_archive_members

    list_archive_members(artifact_path, include_data=False)
    manifest = read_artifact_manifest(artifact_path)
    allowed_metadata_payloads = artifact_inventory_metadata_payload_paths(manifest)
    suffix = archive_suffix(artifact_path)
    if suffix == ".zip":
        with zipfile.ZipFile(artifact_path) as zf:
            return _safe_extract_zip(zf, target, allowed_metadata_payloads=allowed_metadata_payloads)
    if suffix and suffix != ".zip":
        with open_tar_archive(artifact_path) as tf:
            return _safe_extract_tar(tf, target, allowed_metadata_payloads=allowed_metadata_payloads)
    raise RuntimeError(f"Unsupported artifact type: {artifact_path}")


def _apply_member_mode(path: Path, mode: int | None) -> None:
    if not mode:
        return
    try:
        path.chmod(mode & 0o777)
    except OSError:
        pass


def _prepare_file_destination(path: Path, archive_name: str) -> None:
    if path.is_dir() and not path.is_symlink():
        raise RuntimeError(f"Archive payload cannot replace existing directory: {archive_name}")
    if path.exists() or path.is_symlink():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)


def _safe_extract_zip(
    archive: zipfile.ZipFile,
    target: Path,
    *,
    allowed_metadata_payloads: set[str] | None = None,
) -> int:
    target = target.resolve()
    skipped_metadata = 0
    for member in archive.infolist():
        raw_name = member.filename.rstrip("/")
        if _archive_member_is_root_directory(raw_name, is_dir=member.is_dir()):
            # Harmless archive-root directory markers ("", ".", "./") do not
            # map to repository payload. list_archive_members() already ignores
            # them during preview/validation; extraction must apply the same
            # normalization so validated artifacts do not fail at apply time.
            continue
        archive_name = _safe_archive_rel(raw_name).as_posix()
        mode_type = (member.external_attr >> 16) & 0o170000
        if mode_type == 0o120000:
            raise RuntimeError(f"Unsafe zip symlink member: {member.filename}")
        if _is_skipped_artifact_payload_member(
            archive_name,
            allowed_metadata_payloads=allowed_metadata_payloads,
        ):
            skipped_metadata += 1
            continue
        member_path = _safe_destination_path(target, Path(archive_name))
        mode = (member.external_attr >> 16) & 0o7777
        if member.is_dir():
            if member_path.exists() and not member_path.is_dir():
                if member_path.is_symlink():
                    member_path.unlink()
                else:
                    raise RuntimeError(f"Archive directory cannot replace existing file: {archive_name}")
            member_path.mkdir(parents=True, exist_ok=True)
            _apply_member_mode(member_path, mode)
            continue
        _prepare_file_destination(member_path, archive_name)
        with archive.open(member, "r") as source, member_path.open("wb") as dest:
            shutil.copyfileobj(source, dest)
        _apply_member_mode(member_path, mode)
    return skipped_metadata


def _safe_extract_tar(
    archive: tarfile.TarFile,
    target: Path,
    *,
    allowed_metadata_payloads: set[str] | None = None,
) -> int:
    target = target.resolve()
    skipped_metadata = 0
    for member in archive.getmembers():
        raw_name = member.name.rstrip("/")
        if _archive_member_is_root_directory(raw_name, is_dir=member.isdir()):
            # Harmless archive-root directory markers ("", ".", "./") do not
            # map to repository payload. list_archive_members() already ignores
            # them during preview/validation; extraction must apply the same
            # normalization so validated artifacts do not fail at apply time.
            continue
        archive_name = _safe_archive_rel(raw_name).as_posix()
        if _is_skipped_artifact_payload_member(
            archive_name,
            allowed_metadata_payloads=allowed_metadata_payloads,
        ):
            skipped_metadata += 1
            continue
        if member.islnk() or member.issym() or member.isdev():
            raise RuntimeError(f"Unsafe tar member: {member.name}")
        member_path = _safe_destination_path(target, Path(archive_name))
        mode = member.mode & 0o7777
        if member.isdir():
            if member_path.exists() and not member_path.is_dir():
                if member_path.is_symlink():
                    member_path.unlink()
                else:
                    raise RuntimeError(f"Archive directory cannot replace existing file: {archive_name}")
            member_path.mkdir(parents=True, exist_ok=True)
            _apply_member_mode(member_path, mode)
            continue
        if not member.isfile():
            raise RuntimeError(f"Unsupported tar member type: {member.name}")
        _prepare_file_destination(member_path, archive_name)
        extracted = archive.extractfile(member)
        if extracted is None:
            raise RuntimeError(f"Failed to extract tar member: {member.name}")
        with extracted, member_path.open("wb") as dest:
            shutil.copyfileobj(extracted, dest)
        _apply_member_mode(member_path, mode)
    return skipped_metadata


def _safe_destination_path(base: Path, member_path: Path) -> Path:
    base = base.resolve()
    rel = _safe_archive_rel(member_path.as_posix())
    destination = base / rel
    try:
        parent_resolved = destination.parent.resolve(strict=False)
        parent_resolved.relative_to(base)
    except ValueError as exc:
        raise RuntimeError(f"Unsafe archive path: {member_path}") from exc
    return destination



def _sync_snapshot(snapshot_root: Path, repo_root: Path) -> None:
    existing = {
        p.relative_to(repo_root)
        for p in repo_root.rglob("*")
        if ".git" not in p.parts and ".devtool" not in p.parts
    }
    incoming = {
        p.relative_to(snapshot_root)
        for p in snapshot_root.rglob("*")
        if ".git" not in p.parts and ".devtool" not in p.parts
    }
    for rel in sorted(existing - incoming, reverse=True):
        target = repo_root / rel
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            target.unlink(missing_ok=True)
    for path in sorted(snapshot_root.rglob("*")):
        rel = path.relative_to(snapshot_root)
        target = repo_root / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def _normalize_git_pathspecs(paths: list[str] | tuple[str, ...] | None) -> list[str] | None:
    """Return safe repo-relative pathspecs for artifact-scoped commits.

    ``None`` preserves the old behavior for the explicit ``devtool commit`` command:
    stage the whole worktree. A concrete list means "only stage these paths".
    """
    if paths is None:
        return None

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        value = str(raw).strip().replace("\\", "/")
        if not value or value == ".":
            continue
        if value.startswith("/") or value.startswith("../") or "/../" in value:
            raise ValueError(f"Refusing unsafe git pathspec: {raw!r}")
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _literal_git_pathspec(path: str) -> str:
    """Treat artifact inventory paths as exact repository paths, never Git magic/globs."""
    return f":(literal){path}"


def _artifact_pathspec_was_staged(repo_root: Path, path: str, *, has_head: bool) -> tuple[bool, str]:
    """Return whether the user's live index already staged this artifact path."""
    literal = _literal_git_pathspec(path)
    if has_head:
        argv = ["git", "diff", "--cached", "--quiet", "HEAD", "--", literal]
        proc = subprocess.run(
            argv, cwd=repo_root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        if proc.returncode == 0:
            return False, ""
        if proc.returncode == 1:
            return True, ""
    else:
        argv = ["git", "ls-files", "--error-unmatch", "--", literal]
        proc = subprocess.run(
            argv, cwd=repo_root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        if proc.returncode == 0:
            return True, ""
        if proc.returncode == 1:
            return False, ""
    return False, (proc.stdout or f"Git index preflight exited {proc.returncode}").strip()


def git_commit(
    repo_root: Path,
    message: str,
    *,
    verbose: bool = False,
    dry_run: bool = False,
    log_path: Path | str | None = None,
    paths: list[str] | tuple[str, ...] | None = None,
    sign: bool = False,
) -> tuple[list[OperationResult], str]:
    pathspecs = _normalize_git_pathspecs(paths)

    if pathspecs is None:
        specs = [
            CommandSpec("git-add", ["git", "add", "-A"]),
            CommandSpec("git-commit", ["git", "commit", *(["-S"] if sign else []), "-m", message]),
        ]
        config = ProjectConfig(repo_root=repo_root, kind="unknown")
        return run_specs(config, specs, verbose=verbose, dry_run=dry_run, log_path=log_path)
    if not pathspecs:
        output = "No artifact paths changed; skipped git add and git commit."
        results = [
            OperationResult("git-add-skipped", ["git", "add", "-A", "--"], 0, 0.0, output, [], skipped=True),
            OperationResult("git-commit-skipped", ["git", "commit", *(["-S"] if sign else []), "-m", message], 0, 0.0, output, [], skipped=True),
        ]
        return results, output

    # Artifact commits must never stage through the user's live index. Build a
    # private index from HEAD, stage only literal artifact-owned paths there, and
    # commit through GIT_INDEX_FILE. After HEAD advances, reconcile only artifact
    # paths that were *not* already user-staged; otherwise the untouched old index
    # entry would appear as a reverse staged change against the new HEAD.
    with tempfile.TemporaryDirectory(prefix="devtool-artifact-index-") as tmp:
        isolated_index = Path(tmp) / "index"
        isolated_env = os.environ.copy()
        isolated_env["GIT_INDEX_FILE"] = str(isolated_index)
        head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=repo_root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        has_head = head.returncode == 0
        protected_paths: set[str] = set()
        if not dry_run:
            for artifact_path in pathspecs:
                was_staged, preflight_error = _artifact_pathspec_was_staged(
                    repo_root, artifact_path, has_head=has_head
                )
                if preflight_error:
                    result = OperationResult(
                        "git-index-preflight", ["git", "diff", "--cached", "--", artifact_path], 1, 0.0,
                        preflight_error, parse_issues(repo_root, "git-index-preflight", preflight_error),
                        failure_kind="command_failure",
                    )
                    return [result], preflight_error
                if was_staged:
                    protected_paths.add(artifact_path)

        init_argv = ["git", "read-tree", "HEAD"] if has_head else ["git", "read-tree", "--empty"]
        if not dry_run:
            started = time.perf_counter()
            init = subprocess.run(
                init_argv, cwd=repo_root, env=isolated_env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            if init.returncode != 0:
                output = init.stdout or "Unable to initialize isolated Git index."
                result = OperationResult(
                    "git-index-init", init_argv, init.returncode, time.perf_counter() - started,
                    output, parse_issues(repo_root, "git-index-init", output),
                    failure_kind=classify_failure("git-index-init", output, init.returncode),
                )
                return [result], output
        literal_pathspecs = [_literal_git_pathspec(path) for path in pathspecs]
        specs = [
            CommandSpec("git-add", ["git", "add", "-f", "-A", "--", *literal_pathspecs], env={"GIT_INDEX_FILE": str(isolated_index)}),
            CommandSpec("git-commit", ["git", "commit", *(["-S"] if sign else []), "-m", message], env={"GIT_INDEX_FILE": str(isolated_index)}),
        ]
        config = ProjectConfig(repo_root=repo_root, kind="unknown")
        results, raw = run_specs(config, specs, verbose=verbose, dry_run=dry_run, log_path=log_path)
        commit_succeeded = any(result.name == "git-commit" and result.returncode == 0 for result in results)
        if commit_succeeded:
            reconcile = [path for path in pathspecs if path not in protected_paths]
            if reconcile:
                reconcile_specs = [CommandSpec(
                    "git-index-reconcile",
                    ["git", "reset", "-q", "HEAD", "--", *[_literal_git_pathspec(path) for path in reconcile]],
                )]
                reconcile_results, reconcile_raw = run_specs(
                    config, reconcile_specs, verbose=verbose, dry_run=dry_run, log_path=log_path
                )
                results.extend(reconcile_results)
                raw = raw + ("\n\n" if raw and reconcile_raw else "") + reconcile_raw
        return results, raw




def git_commit_with_policy(
    repo_root: Path,
    message: str,
    *,
    mode: str,
    fixup_target: str = "",
    reset_author: bool = False,
    verbose: bool = False,
    dry_run: bool = False,
    log_path: Path | str | None = None,
    paths: list[str] | tuple[str, ...] | None = None,
    sign: bool = False,
) -> tuple[list[OperationResult], str]:
    """Create a commit using the GIT-FRX-04 history policy.

    Safety decisions for amend/published history are performed by
    ``devtool.git_commit_policy`` before this function is entered.  This runner
    owns only isolated-index staging and the exact Git commit argv.
    """

    selected = str(mode or "none").strip().lower() or "none"
    if selected == "none":
        output = "Commit policy is none; changes remain uncommitted."
        return [OperationResult("git-commit-skipped", ["git", "commit"], 0, 0.0, output, [], skipped=True,
                                metadata={"commit_mode": "none"})], output
    if selected == "new":
        results, raw = git_commit(
            repo_root, message, verbose=verbose, dry_run=dry_run, log_path=log_path, paths=paths, sign=sign
        )
        for result in results:
            if result.name == "git-commit":
                result.metadata.setdefault("commit_mode", "new")
        return results, raw
    if selected not in {"amend", "fixup"}:
        raise ValueError(f"Unsupported commit policy mode: {selected!r}")

    pathspecs = _normalize_git_pathspecs(paths)
    if pathspecs is not None and not pathspecs:
        output = "No artifact paths changed; skipped git add and git commit."
        return [
            OperationResult("git-add-skipped", ["git", "add", "-A", "--"], 0, 0.0, output, [], skipped=True),
            OperationResult("git-commit-skipped", ["git", "commit"], 0, 0.0, output, [], skipped=True,
                            metadata={"commit_mode": selected}),
        ], output

    with tempfile.TemporaryDirectory(prefix="devtool-artifact-index-") as tmp:
        isolated_index = Path(tmp) / "index"
        isolated_env = os.environ.copy()
        isolated_env["GIT_INDEX_FILE"] = str(isolated_index)
        head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=repo_root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if head.returncode != 0:
            output = f"Commit mode {selected} requires an existing HEAD commit."
            return [OperationResult(
                "git-commit-policy", ["git", "rev-parse", "--verify", "HEAD"], 2, 0.0,
                output, parse_issues(repo_root, "git-commit-policy", output), failure_kind="git_commit_policy",
                metadata={"commit_mode": selected},
            )], output

        protected_paths: set[str] = set()
        if pathspecs is not None and not dry_run:
            for artifact_path in pathspecs:
                was_staged, preflight_error = _artifact_pathspec_was_staged(repo_root, artifact_path, has_head=True)
                if preflight_error:
                    result = OperationResult(
                        "git-index-preflight", ["git", "diff", "--cached", "--", artifact_path], 1, 0.0,
                        preflight_error, parse_issues(repo_root, "git-index-preflight", preflight_error),
                        failure_kind="command_failure",
                    )
                    return [result], preflight_error
                if was_staged:
                    protected_paths.add(artifact_path)

        if not dry_run:
            started = time.perf_counter()
            init = subprocess.run(
                ["git", "read-tree", "HEAD"], cwd=repo_root, env=isolated_env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            if init.returncode != 0:
                output = init.stdout or "Unable to initialize isolated Git index."
                result = OperationResult(
                    "git-index-init", ["git", "read-tree", "HEAD"], init.returncode,
                    time.perf_counter() - started, output, parse_issues(repo_root, "git-index-init", output),
                    failure_kind=classify_failure("git-index-init", output, init.returncode),
                )
                return [result], output

        add_argv = ["git", "add", "-f", "-A"]
        if pathspecs is not None:
            add_argv.extend(["--", *[_literal_git_pathspec(path) for path in pathspecs]])
        commit_argv = ["git", "commit"]
        if sign:
            commit_argv.append("-S")
        if selected == "amend":
            commit_argv.append("--amend")
            if reset_author:
                commit_argv.append("--reset-author")
            if str(message or "").strip():
                commit_argv.extend(["-m", str(message).strip()])
            else:
                commit_argv.append("--no-edit")
        else:
            commit_argv.append(f"--fixup={str(fixup_target or 'HEAD').strip() or 'HEAD'}")

        specs = [
            CommandSpec("git-add", add_argv, env={"GIT_INDEX_FILE": str(isolated_index)}),
            CommandSpec("git-commit", commit_argv, env={"GIT_INDEX_FILE": str(isolated_index)}),
        ]
        config = ProjectConfig(repo_root=repo_root, kind="unknown")
        results, raw = run_specs(config, specs, verbose=verbose, dry_run=dry_run, log_path=log_path)
        commit_succeeded = any(result.name == "git-commit" and result.returncode == 0 for result in results)
        for result in results:
            if result.name == "git-commit":
                result.metadata.setdefault("commit_mode", selected)
                if selected == "fixup":
                    result.metadata.setdefault("fixup_target", str(fixup_target or "HEAD"))
                if selected == "amend":
                    result.metadata.setdefault("reset_author", bool(reset_author))
        if commit_succeeded and pathspecs is not None:
            reconcile = [path for path in pathspecs if path not in protected_paths]
            if reconcile:
                reconcile_specs = [CommandSpec(
                    "git-index-reconcile",
                    ["git", "reset", "-q", "HEAD", "--", *[_literal_git_pathspec(path) for path in reconcile]],
                )]
                reconcile_results, reconcile_raw = run_specs(
                    config, reconcile_specs, verbose=verbose, dry_run=dry_run, log_path=log_path
                )
                results.extend(reconcile_results)
                raw = raw + ("\n\n" if raw and reconcile_raw else "") + reconcile_raw
        return results, raw

def write_template_config(repo_root: Path, *, force: bool = False) -> Path:
    repo_root = repo_root.expanduser().resolve()
    repo_root.mkdir(parents=True, exist_ok=True)
    target = repo_root / ".devtool.toml"
    if target.exists() and not force:
        raise SystemExit(f"{target} already exists. Use --force to overwrite.")
    target.write_text(generate_template(repo_root), encoding="utf-8")
    return target
