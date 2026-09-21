from __future__ import annotations

import contextlib
import hashlib
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..artifacts import (
    ARTIFACT_METADATA_DIRECTORY,
    artifact_solution,
    artifact_target,
    artifact_workspace_roots,
    list_archive_members,
    resolve_artifact_path,
)
from ..console_utils import create_console
from ..generic_progress import GenericProgressTracker
from ..logstore import write_log
from ..models import Issue, OperationResult
from .capabilities import capability_report, normalize_hook_capabilities
from .isolation import RepositoryMutationSnapshot
from .models import HookExecutionContext, HookExecutionRequest, HookExecutionResult
from .payload import build_hook_payload, json_safe
from .result import load_structured_hook_result

_ALLOWED_RUNNERS = {"bash", "sh", "python3"}
_ALLOWED_PROTOCOLS = {"legacy-env", "json-v1"}

console = create_console()


def _heartbeat_seconds(env: dict[str, str]) -> float:
    raw = str(env.get("DEVTOOL_COMMAND_HEARTBEAT_SECONDS", env.get("DEVTOOL_NO_OUTPUT_HEARTBEAT_SECONDS", "20")) or "20").strip()
    try:
        value = float(raw)
    except ValueError:
        return 20.0
    return max(0.0, min(value, 3600.0))


def _run_live_with_events(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
    activity_label: str,
    script_label: str,
    progress_label: str = "",
    stdin_text: str | None,
    render_progress: bool = False,
    stream_output: bool = False,
    progress_target: str = "",
) -> tuple[int, str, GenericProgressTracker | None, list[dict[str, Any]]]:
    started = time.monotonic()
    last_output_at = started
    last_heartbeat_at = started
    heartbeat_seconds = _heartbeat_seconds(env)
    output_parts: list[str] = []
    lines: queue.Queue[str | None] = queue.Queue()
    test_events: list[dict[str, Any]] = []
    event_r, event_w = os.pipe()
    child_env = dict(env)
    child_env["DEVTOOL_TEST_EVENT_FD"] = str(event_w)
    child_env["DEVTOOL_TEST_PROTOCOL"] = "devtool.test/v1"

    # Artifact hooks use the exact same Rich progress surface as normal generic
    # Devtool commands.  The hook runner owns no spinner/status UI of its own.
    tracker: GenericProgressTracker | None = None
    if render_progress:
        tracker = GenericProgressTracker(
            list(command),
            str(cwd),
            progress_label or activity_label,
            console,
            display_target=progress_target or str(cwd),
        )
        # GenericProgressTracker decides whether Rich Live is appropriate from
        # the same Console capability checks used by normal Devtool commands.
        # Do not force-enable it: redirected/CI output must retain the standard
        # raw-stream fallback.
        tracker.detail = f"Running {progress_target or progress_label or activity_label}".strip()
        tracker.__enter__()

    try:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if stdin_text is not None else None,
            text=True,
            bufsize=1,
            start_new_session=True,
            pass_fds=(event_w,),
        )
        os.close(event_w)
    except OSError as exc:
        os.close(event_r); os.close(event_w)
        return 127, str(exc), tracker, test_events

    def reader_fn() -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    reader = threading.Thread(target=reader_fn, name=f"devtool-artifact-hook-{proc.pid}", daemon=True)
    reader.start()

    def event_reader_fn() -> None:
        from ..test_protocol import parse_line
        with os.fdopen(event_r, "r", encoding="utf-8", errors="replace") as stream:
            for raw in stream:
                event=parse_line(raw)
                if event is not None: test_events.append(event)
    event_reader=threading.Thread(target=event_reader_fn,name=f"devtool-test-events-{proc.pid}",daemon=True)
    event_reader.start()
    if stdin_text is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_text)
            if not stdin_text.endswith("\n"):
                proc.stdin.write("\n")
            proc.stdin.flush()
        finally:
            proc.stdin.close()
    reader_done = False

    def append(text: str) -> None:
        output_parts.append(text)
        # Raw hook output belongs in the durable log/inspection surface while a
        # progress UI is active.  Verbose/non-UI callers can explicitly request
        # streaming instead.
        if tracker is not None:
            for raw_line in text.splitlines():
                if raw_line:
                    tracker.inspection.append_line(raw_line)
        # Match _run_process_live: non-interactive progress falls back to raw
        # observable stdout, while an active Rich UI owns the terminal.
        if stream_output or (tracker is not None and not tracker.enabled):
            print(text, end="", flush=True)

    while True:
        now = time.monotonic()
        if timeout_seconds > 0 and (now - started) >= timeout_seconds and proc.poll() is None:
            timeout_message = f"{script_label} script timed out after {timeout_seconds} seconds."
            append(f"\n{timeout_message}\n")
            if tracker is not None:
                tracker.update_from_line(timeout_message)
            with contextlib.suppress(Exception):
                os.killpg(proc.pid, signal.SIGTERM)
            with contextlib.suppress(Exception):
                proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except Exception:
                with contextlib.suppress(Exception):
                    os.killpg(proc.pid, signal.SIGKILL)
                with contextlib.suppress(Exception):
                    proc.kill()
                with contextlib.suppress(Exception):
                    proc.wait(timeout=1.0)
            event_reader.join(timeout=0.25)
            return 124, "".join(output_parts), tracker, test_events
        try:
            item = lines.get(timeout=0.1)
        except queue.Empty:
            item = ""
        if item is None:
            reader_done = True
        elif item:
            last_output_at = time.monotonic()
            last_heartbeat_at = last_output_at
            append(item)
        if proc.poll() is not None and reader_done:
            break
        now = time.monotonic()
        if heartbeat_seconds > 0 and now - last_output_at >= heartbeat_seconds and now - last_heartbeat_at >= heartbeat_seconds:
            last_heartbeat_at = now
            elapsed = int(round(now - last_output_at))
            heartbeat = (
                f"Devtool heartbeat: {activity_label} is still running after {elapsed}s without child output. "
                "The process was not stopped; set DEVTOOL_COMMAND_HEARTBEAT_SECONDS=0 to disable this heartbeat."
            )
            append("\n" + heartbeat + "\n")
            if tracker is not None:
                tracker.update_from_line(f"Still running · {elapsed}s without output")
    try:
        rc = proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(Exception):
            os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            proc.kill()
        rc = 124
    reader.join(timeout=0.25)
    event_reader.join(timeout=0.25)
    return rc, "".join(output_parts), tracker, test_events


def _run_live(*args, **kwargs):
    """Backward-compatible hook runner surface; protocol events are internal."""
    rc, output, tracker, _events = _run_live_with_events(*args, **kwargs)
    return rc, output, tracker

def _preferred_tmp_parent() -> str | None:
    configured = os.environ.get("TMPDIR", "").strip()
    if not configured:
        return None
    path = Path(configured).expanduser()
    if path.is_dir() and os.access(path, os.W_OK | os.X_OK):
        return str(path)
    return None


def _safe_script_path(raw: str, *, label: str) -> Path:
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"Unsafe {label.lower()} script path: {raw}")
    if not path.parts or path.parts[0] != ARTIFACT_METADATA_DIRECTORY:
        raise RuntimeError(f"{label} scripts must live under .devtool-artifact/.")
    return path


def _strict_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return default


def _safe_repo_relative_path(repo_root: Path, raw: str, *, label: str) -> Path:
    rel = Path(raw)
    if rel.is_absolute() or ".." in rel.parts:
        raise RuntimeError(f"Unsafe {label} path: {raw}")
    root = repo_root.resolve()
    resolved = (root / rel).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"Unsafe {label} path: {raw}") from exc
    return resolved


def _python_command(
    *,
    script_path: Path,
    spec: dict[str, Any],
    workspace_root: Path,
    repo_root: Path,
    timeout_seconds: int,
) -> list[str]:
    python_spec = spec.get("python") if isinstance(spec.get("python"), dict) else {}
    python_spec = dict(python_spec) if isinstance(python_spec, dict) else {}
    project_root = workspace_root if (workspace_root / "pyproject.toml").exists() else repo_root
    command = [
        sys.executable,
        "-m",
        "devtool.python_support",
        "run-validator",
        str(project_root.resolve()),
        str(script_path.resolve()),
        "--cwd",
        str(repo_root.resolve()),
        "--manager",
        str(python_spec.get("manager") or "auto"),
        "--framework",
        str(python_spec.get("test_framework") or "auto"),
        "--python",
        str(python_spec.get("python") or "python3"),
        "--timeout",
        str(timeout_seconds),
    ]
    environment = str(python_spec.get("environment") or "").strip()
    if environment:
        command.extend(["--environment", environment])
    for group in python_spec.get("groups", []) if isinstance(python_spec.get("groups"), list) else []:
        command.extend(["--group", str(group)])
    for extra in python_spec.get("extras", []) if isinstance(python_spec.get("extras"), list) else []:
        command.extend(["--extra", str(extra)])
    for dependency in python_spec.get("dependencies", []) if isinstance(python_spec.get("dependencies"), list) else []:
        command.extend(["--dependency", str(dependency)])
    if _strict_bool(python_spec.get("sync_dependencies", False)):
        command.append("--sync-dependencies")
    if _strict_bool(python_spec.get("install_project", True), default=True) is False:
        command.append("--no-install-project")
    if _strict_bool(python_spec.get("unlocked", False)):
        command.append("--unlocked")
    if _strict_bool(python_spec.get("offline", False)):
        command.append("--offline")
    return command


class EmbeddedHookEngine:
    """Canonical executor for all embedded artifact-controlled scripts."""

    def execute(self, request: HookExecutionRequest) -> OperationResult:
        repo_root = request.repo_root.resolve()
        artifact_path = resolve_artifact_path(request.artifact)
        spec = dict(request.spec)
        script_rel = _safe_script_path(str(spec.get("path") or "").strip(), label=request.script_label)
        runner = str(spec.get("runner") or "bash").strip()
        if runner not in _ALLOWED_RUNNERS:
            raise RuntimeError(f"Unsupported {request.script_label.lower()} script runner: {runner!r}")
        protocol = str(spec.get("protocol") or request.default_protocol).strip().lower().replace("_", "-")
        if protocol not in _ALLOWED_PROTOCOLS:
            raise RuntimeError(f"Unsupported {request.script_label.lower()} script protocol: {protocol!r}")
        expected_sha = str(spec.get("sha256") or "").strip().lower()
        if not request.ignore_checksum and not expected_sha:
            raise RuntimeError(f"{request.script_label.lower()}.script.sha256 is required")
        timeout_raw = spec.get("timeout_seconds", request.default_timeout_seconds)
        if not isinstance(timeout_raw, int) or isinstance(timeout_raw, bool) or timeout_raw <= 0:
            raise RuntimeError(f"{request.script_label.lower()}.script.timeout_seconds must be a positive integer")
        timeout_seconds = timeout_raw
        requested_caps = normalize_hook_capabilities(
            request.requested_capabilities,
            label=f"{request.kind}.capabilities",
        )
        if request.protect_artifact_path and "artifact_private_copy" not in requested_caps:
            # Private copies are an execution property, not a privilege request; expose it in
            # effective capability data without forcing manifests to opt in.
            pass
        cap_report = capability_report(
            requested_caps,
            protect_artifact_path=request.protect_artifact_path,
            enforce_write_boundary=request.enforce_write_boundary,
        )
        context = HookExecutionContext(
            protocol=protocol,
            runner=runner,
            script_path=script_rel.as_posix(),
            script_sha256=expected_sha,
            timeout_seconds=timeout_seconds,
            requested_capabilities=requested_caps,
            effective_capabilities=cap_report,
            declared_writes=list(request.declared_writes),
        )
        argv = [runner, script_rel.as_posix()]
        if request.dry_run:
            output = "$ " + " ".join(argv) + f"\n{request.script_label} script skipped during dry-run."
            path = str(write_log(output, request.log_path, append=request.log_path is not None))
            return OperationResult(
                request.operation_name,
                argv,
                0,
                0.0,
                output,
                [],
                skipped=True,
                log_path=path,
                metadata={
                    "hook_event": {
                        "schema_version": 1,
                        "kind": request.kind,
                        "phase": request.phase,
                        "protocol": protocol,
                        "script_path": script_rel.as_posix(),
                        "script_sha256": expected_sha,
                        "runner": runner,
                        "timeout_seconds": timeout_seconds,
                        "status": "skipped",
                        "declared_capabilities": requested_caps,
                        "effective_capabilities": cap_report,
                        "declared_writes": list(request.declared_writes),
                        "detected_writes": [],
                    }
                },
            )

        members = list_archive_members(artifact_path, include_data=True)
        member = next((item for item in members if item.name == script_rel.as_posix() and not item.is_dir), None)
        if member is None or member.data is None:
            raise RuntimeError(f"{request.script_label} script is missing from archive: {script_rel.as_posix()}")
        actual_sha = hashlib.sha256(member.data).hexdigest().lower()
        if not request.ignore_checksum and actual_sha != expected_sha:
            raise RuntimeError(f"{request.script_label} script SHA-256 mismatch")

        mutation_snapshot = RepositoryMutationSnapshot.capture(
            repo_root, backup_dirty=request.restore_on_write_violation
        ) if request.enforce_write_boundary else None
        started_wall = datetime.now(timezone.utc)
        start = time.perf_counter()
        structured: HookExecutionResult | None = None
        detected_writes: list[str] = []
        violations: list[str] = []
        tmp_parent = _preferred_tmp_parent()
        with tempfile.TemporaryDirectory(prefix=request.temp_prefix, dir=tmp_parent) as tmp:
            tmpdir = Path(tmp)
            script_path = tmpdir / script_rel.name
            script_path.write_bytes(member.data)
            script_path.chmod(0o700)

            script_artifact_path = artifact_path.resolve()
            if request.protect_artifact_path:
                artifact_dir = tmpdir / "artifact"
                artifact_dir.mkdir(mode=0o700)
                script_artifact_path = artifact_dir / artifact_path.name
                shutil.copy2(artifact_path, script_artifact_path)
                script_artifact_path.chmod(0o600)

            workspace_roots = artifact_workspace_roots(request.manifest)
            resolved_workspace_roots = [_safe_repo_relative_path(repo_root, root, label="workspace") for root in workspace_roots]
            workspace_root = resolved_workspace_roots[0] if resolved_workspace_roots else repo_root
            declared_solution = request.solution or artifact_solution(request.manifest)
            if declared_solution:
                _safe_repo_relative_path(repo_root, declared_solution, label="solution")

            result_path = tmpdir / "hook-result.json"
            env = os.environ.copy()
            env.update({
                "DEVTOOL_REPO_ROOT": str(repo_root),
                "DEVTOOL_WORKSPACE_ROOT": str(workspace_root.resolve()),
                "DEVTOOL_WORKSPACE_ROOTS": os.pathsep.join(str(root) for root in resolved_workspace_roots),
                "DEVTOOL_TARGET": request.target or artifact_target(request.manifest),
                "DEVTOOL_SOLUTION": declared_solution,
                "DEVTOOL_ARTIFACT_PATH": str(script_artifact_path),
                request.temp_env_key: str(tmpdir.resolve()),
                "DEVTOOL_ARTIFACT_SCRIPT_TMPDIR": str(tmpdir.resolve()),
                "DEVTOOL_RUN_ID": request.run_id,
                "DEVTOOL_HOOK_KIND": request.kind,
                "DEVTOOL_HOOK_PHASE": request.phase,
                "DEVTOOL_HOOK_PROTOCOL": protocol,
                "DEVTOOL_HOOK_RESULT_PATH": str(result_path.resolve()),
                "TMPDIR": str(tmpdir.resolve()),
            })
            env.update(request.extra_env)

            stdin_text: str | None = None
            if protocol == "json-v1":
                payload = build_hook_payload(
                    repo_root=repo_root,
                    artifact_source=artifact_path,
                    artifact_for_hook=script_artifact_path,
                    kind=request.kind,
                    phase=request.phase,
                    run_id=request.run_id,
                    target=request.target or artifact_target(request.manifest),
                    solution=declared_solution,
                    script_path=script_rel.as_posix(),
                    script_sha256=actual_sha,
                    runner=runner,
                    timeout_seconds=timeout_seconds,
                    capabilities=cap_report,
                    declared_writes=list(request.declared_writes),
                    context=request.payload_context,
                )
                stdin_text = json.dumps(json_safe(payload), sort_keys=True, separators=(",", ":"), allow_nan=False)
                payload_path = tmpdir / "hook-payload.json"
                payload_path.write_text(stdin_text + "\n", encoding="utf-8")
                payload_path.chmod(0o600)
                env["DEVTOOL_HOOK_PAYLOAD_PATH"] = str(payload_path.resolve())

            command = [runner, str(script_path)]
            if runner == "python3":
                command = _python_command(
                    script_path=script_path,
                    spec=spec,
                    workspace_root=workspace_root,
                    repo_root=repo_root,
                    timeout_seconds=timeout_seconds,
                )
            rc, child_output, progress_tracker, test_events = _run_live_with_events(
                command,
                cwd=repo_root,
                env=env,
                timeout_seconds=timeout_seconds,
                activity_label="artifact validator" if request.kind == "validation" else f"artifact {request.kind}",
                script_label=f"Artifact {request.script_label.lower()}",
                progress_label="Artifact validation" if request.kind == "validation" else f"Artifact {request.kind}",
                stdin_text=stdin_text,
                render_progress=request.render_progress,
                stream_output=request.stream_output,
                progress_target=request.phase.replace("_", " "),
            )
            try:
                structured = load_structured_hook_result(result_path)
            except RuntimeError as exc:
                structured = HookExecutionResult(status="fail", summary=str(exc))
                if rc == 0:
                    rc = 2

        duration = time.perf_counter() - start
        ended_wall = datetime.now(timezone.utc)
        restoration_errors: list[str] = []
        if mutation_snapshot is not None:
            detected_writes, violations = mutation_snapshot.undeclared_changes(repo_root, request.declared_writes)
            if violations and request.restore_on_write_violation:
                restoration_errors = mutation_snapshot.restore(repo_root, violations)
            if violations and rc == 0:
                rc = 1
            if restoration_errors and rc == 0:
                rc = 1
        if structured is not None and structured.status == "fail" and rc == 0:
            rc = 1

        if test_events:
            from ..test_protocol import EventAccumulator
            acc=EventAccumulator()
            for event in test_events: acc.feed(event)
            canonical_test_result=acc.to_result(f"artifact-{request.kind}")
            if canonical_test_result.tests_failed and rc == 0: rc = 1
        else:
            canonical_test_result=None

        if progress_tracker is not None:
            # Match the normal command runner: keep successful progress transient
            # and let the authoritative operation summary report success. Failures
            # remain visible immediately.
            if rc != 0:
                progress_tracker.finish(rc)
            progress_tracker.__exit__(None, None, None)
            progress_tracker = None

        header = f"$ {runner} {script_rel.as_posix()}"
        if runner == "python3" and command[:3] == [sys.executable, "-m", "devtool.python_support"]:
            header += "  # project environment"
        output = header + ("\n" + child_output.rstrip() if child_output.strip() else "")
        if structured is not None and structured.summary:
            output += ("\n" if output else "") + f"Hook result: {structured.status}: {structured.summary}"
        if violations:
            message = "Embedded hook changed undeclared path(s): " + ", ".join(violations[:12])
            if len(violations) > 12:
                message += f" (and {len(violations) - 12} more)"
            output += ("\n" if output else "") + message
            if request.restore_on_write_violation and not restoration_errors:
                output += "\nObservational hook writes were restored."
        if restoration_errors:
            output += ("\n" if output else "") + "Failed to restore observational hook writes: " + "; ".join(restoration_errors[:8])
        path = str(write_log(output, request.log_path, append=request.log_path is not None))
        issues: list[Issue] = []
        if structured is not None:
            for warning in structured.warnings:
                issues.append(Issue("warning", request.operation_name, script_rel.as_posix(), "hook-warning", warning))
            for annotation in structured.annotations:
                severity = str(annotation.get("severity") or "info").strip().lower()
                if severity not in {"info", "warning", "error"}:
                    severity = "info"
                message = str(annotation.get("message") or annotation.get("summary") or "").strip()
                if message:
                    issues.append(Issue(severity, request.operation_name, script_rel.as_posix(), str(annotation.get("code") or "hook-annotation"), message))
        if violations:
            issues.append(Issue("error", request.operation_name, str(artifact_path), "write-boundary", "Hook changed undeclared path(s): " + ", ".join(violations[:12])))
        if rc != 0 and not any(issue.severity == "error" for issue in issues):
            issues.append(Issue("error", request.operation_name, script_rel.as_posix(), "exit-code", f"{request.script_label} script exited with code {rc}."))
        result_status = structured.status if structured is not None else ("pass" if rc == 0 else "fail")
        event = {
            "schema_version": 1,
            "kind": request.kind,
            "phase": request.phase,
            "protocol": protocol,
            "script_path": script_rel.as_posix(),
            "script_sha256": actual_sha,
            "runner": runner,
            "started_at": started_wall.isoformat().replace("+00:00", "Z"),
            "ended_at": ended_wall.isoformat().replace("+00:00", "Z"),
            "duration_seconds": duration,
            "returncode": rc,
            "timeout_seconds": timeout_seconds,
            "status": result_status,
            "summary": structured.summary if structured is not None else "",
            "warnings": list(structured.warnings) if structured is not None else [],
            "annotations": list(structured.annotations) if structured is not None else [],
            "evidence": dict(structured.evidence) if structured is not None else {},
            "declared_capabilities": requested_caps,
            "effective_capabilities": cap_report,
            "declared_writes": list(request.declared_writes),
            "detected_writes": detected_writes,
            "write_violations": violations,
            "write_restoration_errors": restoration_errors,
            "writes_restored": bool(violations and request.restore_on_write_violation and not restoration_errors),
        }
        return OperationResult(
            request.operation_name,
            [runner, script_rel.as_posix()],
            rc,
            duration,
            output,
            issues,
            log_path=path,
            failure_kind=request.failure_kind if rc != 0 else "",
            metadata={
                "artifact_path": str(artifact_path),
                "temporary": True,
                "hook_kind": request.kind,
                "hook_phase": request.phase,
                "hook_protocol": protocol,
                "test_protocol": "devtool.test/v1" if test_events else "",
                "canonical_test_result": canonical_test_result.to_dict() if canonical_test_result is not None else None,
                "hook_result": structured.raw if structured is not None else {},
                "hook_event": event,
            },
        )
