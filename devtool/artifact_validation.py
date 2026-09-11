from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .artifacts import (
    ARTIFACT_METADATA_DIRECTORY,
    artifact_finalize_phases,
    artifact_finalize_script,
    artifact_finalize_write_paths,
    artifact_preflight_phase,
    artifact_preflight_script,
    artifact_solution,
    artifact_target,
    artifact_validation_script,
    artifact_workspace_roots,
    list_archive_members,
    read_artifact_manifest,
    resolve_artifact_path,
)
from .logstore import write_log
from .models import Issue, OperationResult

_ALLOWED_RUNNERS = {"bash", "sh", "python3"}


def _validation_heartbeat_seconds(env: dict[str, str]) -> float:
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


def _run_validator_command_live(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
    activity_label: str = "artifact validator",
    script_label: str = "Artifact validation",
    stdin_text: str | None = None,
) -> tuple[int, str]:
    """Run an embedded artifact script with live output and no-output heartbeats."""
    started = time.monotonic()
    last_output_at = started
    last_heartbeat_at = started
    heartbeat_seconds = _validation_heartbeat_seconds(env)
    output_parts: list[str] = []
    lines: queue.Queue[str | None] = queue.Queue()

    try:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if stdin_text is not None else None,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        return 127, str(exc)

    def _reader() -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    reader = threading.Thread(target=_reader, name=f"devtool-artifact-validator-{proc.pid}", daemon=True)
    reader.start()
    if stdin_text is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_text)
            if not stdin_text.endswith("\n"):
                proc.stdin.write("\n")
            proc.stdin.flush()
        finally:
            proc.stdin.close()
    reader_done = False

    def _append(text: str) -> None:
        output_parts.append(text)
        print(text, end="", flush=True)

    while True:
        now = time.monotonic()
        timed_out = timeout_seconds > 0 and (now - started) >= timeout_seconds
        if timed_out and proc.poll() is None:
            _append(f"\n{script_label} script timed out after {timeout_seconds} seconds.\n")
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=2.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            return 124, "".join(output_parts)

        try:
            item = lines.get(timeout=0.1)
        except queue.Empty:
            item = ""

        if item is None:
            reader_done = True
        elif item:
            last_output_at = time.monotonic()
            last_heartbeat_at = last_output_at
            _append(item)

        if proc.poll() is not None and reader_done:
            break

        now = time.monotonic()
        if (
            heartbeat_seconds > 0
            and (now - last_output_at) >= heartbeat_seconds
            and (now - last_heartbeat_at) >= heartbeat_seconds
        ):
            last_heartbeat_at = now
            elapsed = int(round(now - last_output_at))
            _append(
                "\n"
                f"Devtool heartbeat: {activity_label} is still running after {elapsed}s without child output. "
                "The process was not stopped; set DEVTOOL_COMMAND_HEARTBEAT_SECONDS=0 to disable this heartbeat.\n"
            )

    try:
        rc = proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        rc = 124
    reader.join(timeout=0.25)
    return rc, "".join(output_parts)


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


def _run_embedded_artifact_script(
    repo_root: Path,
    artifact: str | Path,
    *,
    manifest: dict[str, Any],
    spec: dict[str, Any],
    script_label: str,
    operation_name: str,
    temp_prefix: str,
    temp_env_key: str,
    target: str = "",
    solution: str = "",
    run_id: str = "",
    dry_run: bool = False,
    log_path: Path | str | None = None,
    ignore_checksum: bool = False,
    default_timeout_seconds: int = 900,
    extra_env: dict[str, str] | None = None,
    failure_kind: str = "validation_failure",
    protect_artifact_path: bool = False,
    stdin_payload: dict[str, Any] | None = None,
) -> OperationResult:
    artifact_path = resolve_artifact_path(artifact)
    script_rel = _safe_script_path(str(spec.get("path") or "").strip(), label=script_label)
    runner = str(spec.get("runner") or "bash").strip()
    if runner not in _ALLOWED_RUNNERS:
        raise RuntimeError(f"Unsupported {script_label.lower()} script runner: {runner!r}")
    expected_sha = str(spec.get("sha256") or "").strip().lower()
    if not ignore_checksum and not expected_sha:
        raise RuntimeError(f"{script_label.lower()}.script.sha256 is required")
    timeout_raw = spec.get("timeout_seconds", default_timeout_seconds)
    if not isinstance(timeout_raw, int) or isinstance(timeout_raw, bool) or timeout_raw <= 0:
        raise RuntimeError(f"{script_label.lower()}.script.timeout_seconds must be a positive integer")
    timeout_seconds = timeout_raw

    argv = [runner, str(script_rel)]
    if dry_run:
        output = "$ " + " ".join(argv) + f"\n{script_label} script skipped during dry-run."
        path = str(write_log(output, log_path, append=log_path is not None))
        return OperationResult(operation_name, argv, 0, 0.0, output, [], skipped=True, log_path=path)

    members = list_archive_members(artifact_path, include_data=True)
    member = next((item for item in members if item.name == script_rel.as_posix() and not item.is_dir), None)
    if member is None or member.data is None:
        raise RuntimeError(f"{script_label} script is missing from archive: {script_rel.as_posix()}")
    if not ignore_checksum:
        actual_sha = hashlib.sha256(member.data).hexdigest().lower()
        if actual_sha != expected_sha:
            raise RuntimeError(f"{script_label} script SHA-256 mismatch")

    start = time.perf_counter()
    tmp_parent = _preferred_tmp_parent()
    with tempfile.TemporaryDirectory(prefix=temp_prefix, dir=tmp_parent) as tmp:
        tmpdir = Path(tmp)
        script_path = tmpdir / script_rel.name
        script_path.write_bytes(member.data)
        script_path.chmod(0o700)

        script_artifact_path = artifact_path.resolve()
        if protect_artifact_path:
            artifact_dir = tmpdir / "artifact"
            artifact_dir.mkdir(mode=0o700)
            script_artifact_path = artifact_dir / artifact_path.name
            shutil.copy2(artifact_path, script_artifact_path)
            # The hook receives a disposable private copy. Keep it owner-only but writable
            # so preflight can inspect or mutate its sandbox copy without ever touching
            # the source archive; source integrity is enforced separately by digest checks.
            script_artifact_path.chmod(0o600)

        workspace_roots = artifact_workspace_roots(manifest)
        resolved_workspace_roots = [
            _safe_repo_relative_path(repo_root, root, label="workspace")
            for root in workspace_roots
        ]
        workspace_root = resolved_workspace_roots[0] if resolved_workspace_roots else repo_root.resolve()
        declared_solution = solution or artifact_solution(manifest)
        if declared_solution:
            _safe_repo_relative_path(repo_root, declared_solution, label="solution")
        env = os.environ.copy()
        env.update(
            {
                "DEVTOOL_REPO_ROOT": str(repo_root.resolve()),
                "DEVTOOL_WORKSPACE_ROOT": str(workspace_root.resolve()),
                "DEVTOOL_WORKSPACE_ROOTS": os.pathsep.join(str(root) for root in resolved_workspace_roots),
                "DEVTOOL_TARGET": target or artifact_target(manifest),
                "DEVTOOL_SOLUTION": declared_solution,
                "DEVTOOL_ARTIFACT_PATH": str(script_artifact_path),
                temp_env_key: str(tmpdir.resolve()),
                "DEVTOOL_ARTIFACT_SCRIPT_TMPDIR": str(tmpdir.resolve()),
                "DEVTOOL_RUN_ID": run_id,
                "TMPDIR": str(tmpdir.resolve()),
            }
        )
        env.update(extra_env or {})
        stdin_text: str | None = None
        if stdin_payload is not None:
            stdin_text = json.dumps(stdin_payload, sort_keys=True, separators=(",", ":"))
            payload_path = tmpdir / "hook-payload.json"
            payload_path.write_text(stdin_text + "\n", encoding="utf-8")
            payload_path.chmod(0o600)
            env["DEVTOOL_HOOK_PAYLOAD_PATH"] = str(payload_path.resolve())
            env["DEVTOOL_HOOK_PROTOCOL"] = "json-v1"
        command = [runner, str(script_path)]
        if runner == "python3":
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
            if bool(python_spec.get("sync_dependencies", False)):
                command.append("--sync-dependencies")
            if python_spec.get("install_project") is False:
                command.append("--no-install-project")
            if bool(python_spec.get("unlocked", False)):
                command.append("--unlocked")
            if bool(python_spec.get("offline", False)):
                command.append("--offline")
        print(f"==> Running {script_label.lower()} script", flush=True)
        rc, output = _run_validator_command_live(
            command,
            cwd=repo_root.resolve(),
            env=env,
            timeout_seconds=timeout_seconds,
            activity_label="artifact validator" if script_label == "Validation" else "artifact preflight",
            script_label=f"Artifact {script_label.lower()}",
            stdin_text=stdin_text,
        )

    duration = time.perf_counter() - start
    header = f"$ {runner} {script_rel.as_posix()}"
    if runner == "python3" and command[:3] == [sys.executable, "-m", "devtool.python_support"]:
        header += "  # project environment"
    output = header + ("\n" + output.rstrip() if output.strip() else "")
    path = str(write_log(output, log_path, append=log_path is not None))
    issues: list[Issue] = []
    result_failure_kind = ""
    if rc != 0:
        result_failure_kind = failure_kind
        issues.append(
            Issue(
                "error",
                operation_name,
                script_rel.as_posix(),
                "exit-code",
                f"{script_label} script exited with code {rc}.",
            )
        )
    return OperationResult(
        operation_name,
        [runner, script_rel.as_posix()],
        rc,
        duration,
        output,
        issues,
        log_path=path,
        failure_kind=result_failure_kind,
        metadata={
            "artifact_path": str(artifact_path),
            "temporary": True,
            **({"hook_payload_schema": 1, "hook_phase": str(stdin_payload.get("phase") or "")} if stdin_payload else {}),
        },
    )


def run_artifact_validation_script(
    repo_root: Path,
    artifact: str | Path,
    *,
    manifest: dict[str, Any] | None = None,
    target: str = "",
    solution: str = "",
    run_id: str = "",
    dry_run: bool = False,
    log_path: Path | str | None = None,
    ignore_checksum: bool = False,
) -> OperationResult | None:
    """Run an embedded artifact validator from a disposable private directory."""
    artifact_path = resolve_artifact_path(artifact)
    manifest = manifest or read_artifact_manifest(artifact_path)
    spec = artifact_validation_script(manifest)
    if not spec:
        return None
    return _run_embedded_artifact_script(
        repo_root,
        artifact_path,
        manifest=manifest,
        spec=spec,
        script_label="Validation",
        operation_name="artifact-validate-script",
        temp_prefix="devtool-artifact-validation-",
        temp_env_key="DEVTOOL_VALIDATION_TMPDIR",
        target=target,
        solution=solution,
        run_id=run_id,
        dry_run=dry_run,
        log_path=log_path,
        ignore_checksum=ignore_checksum,
        default_timeout_seconds=900,
        failure_kind="validation_failure",
    )


def _git_changed_paths(repo_root: Path) -> set[str] | None:
    """Return tracked/untracked working-tree paths without touching the user's index."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    paths: set[str] = set()
    for record in proc.stdout.split("\0"):
        if len(record) < 4:
            continue
        raw = record[3:]
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1]
        raw = raw.strip().replace("\\", "/")
        if raw:
            paths.add(raw)
    return paths


def _repo_file_states(repo_root: Path) -> dict[str, tuple[str, int, str]]:
    """Fallback write-boundary snapshot for repositories without usable Git metadata."""
    states: dict[str, tuple[str, int, str]] = {}
    for path in repo_root.rglob("*"):
        try:
            rel_path = path.relative_to(repo_root)
        except ValueError:
            continue
        if ".git" in rel_path.parts or not path.is_file():
            continue
        rel = rel_path.as_posix()
        states[rel] = _path_state(repo_root, rel)
    return states


def _path_state(repo_root: Path, rel: str) -> tuple[str, int, str]:
    path = repo_root / rel
    if not path.exists():
        return ("missing", 0, "")
    if path.is_file():
        return ("file", path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
    if path.is_dir():
        return ("directory", 0, "")
    return ("other", 0, "")


def _path_is_declared(rel: str, declared: list[str]) -> bool:
    value = rel.strip().replace("\\", "/").strip("/")
    return any(value == item or value.startswith(item.rstrip("/") + "/") for item in declared)


def run_artifact_finalize_script(
    repo_root: Path,
    artifact: str | Path,
    *,
    phase: str,
    manifest: dict[str, Any] | None = None,
    target: str = "",
    solution: str = "",
    run_id: str = "",
    dry_run: bool = False,
    log_path: Path | str | None = None,
    ignore_checksum: bool = False,
    validation_requested: bool = False,
    validation_status: str = "pending",
    commit_requested: bool = False,
    commit_message: str = "",
    commit_paths: list[str] | None = None,
) -> OperationResult | None:
    """Run a post-apply final hook using a versioned JSON-v1 stdin contract."""
    artifact_path = resolve_artifact_path(artifact)
    manifest = manifest or read_artifact_manifest(artifact_path)
    phases = artifact_finalize_phases(manifest)
    if phase not in phases:
        return None
    spec = artifact_finalize_script(manifest)
    if not spec:
        return None
    declared_writes = artifact_finalize_write_paths(manifest)
    git_before = _git_changed_paths(repo_root)
    fallback_before = _repo_file_states(repo_root) if git_before is None else None
    before_paths = git_before or set()
    before_state = {path: _path_state(repo_root, path) for path in before_paths}
    payload = {
        "schema_version": 1,
        "event": "artifact_finalize",
        "phase": phase,
        "run_id": run_id,
        "repo_root": str(repo_root.resolve()),
        "target": target or artifact_target(manifest),
        "solution": solution or artifact_solution(manifest),
        "artifact": {
            "path": str(artifact_path.resolve()),
            "name": artifact_path.name,
            "sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest() if artifact_path.is_file() else "",
        },
        "validation": {
            "requested": bool(validation_requested),
            "status": validation_status,
        },
        "commit": {
            "requested": bool(commit_requested),
            "message": commit_message,
            "paths": list(commit_paths or []),
        },
        "write_paths": declared_writes,
    }
    result = _run_embedded_artifact_script(
        repo_root,
        artifact_path,
        manifest=manifest,
        spec=spec,
        script_label="Finalize",
        operation_name="artifact-finalize-script",
        temp_prefix="devtool-artifact-finalize-",
        temp_env_key="DEVTOOL_FINALIZE_TMPDIR",
        target=target,
        solution=solution,
        run_id=run_id,
        dry_run=dry_run,
        log_path=log_path,
        ignore_checksum=ignore_checksum,
        default_timeout_seconds=300,
        extra_env={"DEVTOOL_FINALIZE_PHASE": phase},
        failure_kind="finalize_failure",
        protect_artifact_path=True,
        stdin_payload=payload,
    )
    result.metadata["finalize_phase"] = phase
    if dry_run or result.returncode != 0:
        return result
    git_after = _git_changed_paths(repo_root)
    if fallback_before is not None or git_after is None:
        before_all = fallback_before if fallback_before is not None else _repo_file_states(repo_root)
        after_all = _repo_file_states(repo_root)
        changed_candidates = set(before_all) | set(after_all)
        violations = [
            path for path in sorted(changed_candidates)
            if before_all.get(path, ("missing", 0, "")) != after_all.get(path, ("missing", 0, ""))
            and not _path_is_declared(path, declared_writes)
        ]
    else:
        changed_candidates = before_paths | git_after
        violations = [
            path for path in sorted(changed_candidates)
            if _path_state(repo_root, path) != before_state.get(path, ("missing", 0, ""))
            and not _path_is_declared(path, declared_writes)
        ]
    if violations:
        message = "Finalize hook changed undeclared path(s): " + ", ".join(violations[:12])
        if len(violations) > 12:
            message += f" (and {len(violations) - 12} more)"
        result.returncode = 1
        result.failure_kind = "finalize_failure"
        result.output = (result.output + "\n" + message).strip()
        result.issues.append(Issue("error", "artifact-finalize-script", str(artifact_path), "write-boundary", message))
    return result


def run_artifact_preflight_script(
    repo_root: Path,
    artifact: str | Path,
    *,
    phase: str,
    manifest: dict[str, Any] | None = None,
    target: str = "",
    solution: str = "",
    run_id: str = "",
    dry_run: bool = False,
    log_path: Path | str | None = None,
    ignore_checksum: bool = False,
) -> OperationResult | None:
    """Run the optional embedded preflight hook for the requested extraction phase."""
    artifact_path = resolve_artifact_path(artifact)
    manifest = manifest or read_artifact_manifest(artifact_path)
    declared_phase = artifact_preflight_phase(manifest)
    if not declared_phase or declared_phase != phase:
        return None
    spec = artifact_preflight_script(manifest)
    if not spec:
        return None
    result = _run_embedded_artifact_script(
        repo_root,
        artifact_path,
        manifest=manifest,
        spec=spec,
        script_label="Preflight",
        operation_name="artifact-preflight-script",
        temp_prefix="devtool-artifact-preflight-",
        temp_env_key="DEVTOOL_PREFLIGHT_TMPDIR",
        target=target,
        solution=solution,
        run_id=run_id,
        dry_run=dry_run,
        log_path=log_path,
        ignore_checksum=ignore_checksum,
        default_timeout_seconds=300,
        extra_env={"DEVTOOL_PREFLIGHT_PHASE": declared_phase},
        failure_kind="preflight_failure",
        protect_artifact_path=True,
    )
    result.metadata["preflight_phase"] = declared_phase
    return result
