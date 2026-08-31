from __future__ import annotations

import hashlib
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .artifacts import (
    ARTIFACT_METADATA_DIRECTORY,
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
) -> tuple[int, str]:
    """Run an artifact validator with live output and no-output heartbeats."""
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
    reader_done = False

    def _append(text: str) -> None:
        output_parts.append(text)
        print(text, end="", flush=True)

    while True:
        now = time.monotonic()
        timed_out = timeout_seconds > 0 and (now - started) >= timeout_seconds
        if timed_out and proc.poll() is None:
            _append(f"\nArtifact validation script timed out after {timeout_seconds} seconds.\n")
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
                f"Devtool heartbeat: artifact validator is still running after {elapsed}s without child output. "
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


def _safe_script_path(raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"Unsafe validation script path: {raw}")
    if not path.parts or path.parts[0] != ARTIFACT_METADATA_DIRECTORY:
        raise RuntimeError("Validation scripts must live under .devtool-artifact/.")
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

    script_rel = _safe_script_path(str(spec.get("path") or "").strip())
    runner = str(spec.get("runner") or "bash").strip()
    if runner not in _ALLOWED_RUNNERS:
        raise RuntimeError(f"Unsupported validation script runner: {runner!r}")
    expected_sha = str(spec.get("sha256") or "").strip().lower()
    if not ignore_checksum and not expected_sha:
        raise RuntimeError("validation.script.sha256 is required")
    timeout_raw = spec.get("timeout_seconds", 900)
    if not isinstance(timeout_raw, int) or isinstance(timeout_raw, bool) or timeout_raw <= 0:
        raise RuntimeError("validation.script.timeout_seconds must be a positive integer")
    timeout_seconds = timeout_raw

    argv = [runner, str(script_rel)]
    if dry_run:
        output = "$ " + " ".join(argv) + "\nArtifact validation script skipped during dry-run."
        path = str(write_log(output, log_path, append=log_path is not None))
        return OperationResult("artifact-validate-script", argv, 0, 0.0, output, [], skipped=True, log_path=path)

    members = list_archive_members(artifact_path, include_data=True)
    member = next((item for item in members if item.name == script_rel.as_posix() and not item.is_dir), None)
    if member is None or member.data is None:
        raise RuntimeError(f"Validation script is missing from archive: {script_rel.as_posix()}")
    if not ignore_checksum:
        actual_sha = hashlib.sha256(member.data).hexdigest().lower()
        if actual_sha != expected_sha:
            raise RuntimeError("Validation script SHA-256 mismatch")

    start = time.perf_counter()
    tmp_parent = _preferred_tmp_parent()
    with tempfile.TemporaryDirectory(prefix="devtool-artifact-validation-", dir=tmp_parent) as tmp:
        tmpdir = Path(tmp)
        script_path = tmpdir / script_rel.name
        script_path.write_bytes(member.data)
        script_path.chmod(0o700)

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
                "DEVTOOL_ARTIFACT_PATH": str(artifact_path.resolve()),
                "DEVTOOL_VALIDATION_TMPDIR": str(tmpdir.resolve()),
                "DEVTOOL_RUN_ID": run_id,
                "TMPDIR": str(tmpdir.resolve()),
            }
        )
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
        print("==> Running artifact validation script", flush=True)
        rc, output = _run_validator_command_live(
            command,
            cwd=repo_root.resolve(),
            env=env,
            timeout_seconds=timeout_seconds,
        )

    duration = time.perf_counter() - start
    header = f"$ {runner} {script_rel.as_posix()}"
    if runner == "python3" and command[:3] == [sys.executable, "-m", "devtool.python_support"]:
        header += "  # project environment"
    output = header + ("\n" + output.rstrip() if output.strip() else "")
    path = str(write_log(output, log_path, append=log_path is not None))
    issues: list[Issue] = []
    failure_kind = ""
    if rc != 0:
        failure_kind = "validation_failure"
        issues.append(Issue("error", "artifact-validate-script", script_rel.as_posix(), "exit-code", f"Validation script exited with code {rc}."))
    return OperationResult(
        "artifact-validate-script",
        [runner, script_rel.as_posix()],
        rc,
        duration,
        output,
        issues,
        log_path=path,
        failure_kind=failure_kind,
        metadata={"artifact_path": str(artifact_path), "temporary": True},
    )
