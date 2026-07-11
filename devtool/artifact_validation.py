from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
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
    if not expected_sha:
        raise RuntimeError("validation.script.sha256 is required")
    timeout_seconds = int(spec.get("timeout_seconds", 900) or 900)
    if timeout_seconds <= 0:
        raise RuntimeError("validation.script.timeout_seconds must be greater than zero")

    argv = [runner, str(script_rel)]
    if dry_run:
        output = "$ " + " ".join(argv) + "\nArtifact validation script skipped during dry-run."
        path = str(write_log(output, log_path, append=log_path is not None))
        return OperationResult("artifact-validate-script", argv, 0, 0.0, output, [], skipped=True, log_path=path)

    members = list_archive_members(artifact_path, include_data=True)
    member = next((item for item in members if item.name == script_rel.as_posix() and not item.is_dir), None)
    if member is None or member.data is None:
        raise RuntimeError(f"Validation script is missing from archive: {script_rel.as_posix()}")
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
        workspace_root = repo_root / workspace_roots[0] if workspace_roots else repo_root
        env = os.environ.copy()
        env.update(
            {
                "DEVTOOL_REPO_ROOT": str(repo_root.resolve()),
                "DEVTOOL_WORKSPACE_ROOT": str(workspace_root.resolve()),
                "DEVTOOL_WORKSPACE_ROOTS": os.pathsep.join(str((repo_root / root).resolve()) for root in workspace_roots),
                "DEVTOOL_TARGET": target or artifact_target(manifest),
                "DEVTOOL_SOLUTION": solution or artifact_solution(manifest),
                "DEVTOOL_ARTIFACT_PATH": str(artifact_path.resolve()),
                "DEVTOOL_VALIDATION_TMPDIR": str(tmpdir.resolve()),
                "DEVTOOL_RUN_ID": run_id,
                "TMPDIR": str(tmpdir.resolve()),
            }
        )
        command = [runner, str(script_path)]
        try:
            proc = subprocess.run(
                command,
                cwd=tmpdir,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            rc = proc.returncode
            output = proc.stdout or ""
        except subprocess.TimeoutExpired as exc:
            rc = 124
            captured = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            output = captured + f"\nArtifact validation script timed out after {timeout_seconds} seconds."

    duration = time.perf_counter() - start
    header = f"$ {runner} {script_rel.as_posix()}"
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
