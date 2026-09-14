from __future__ import annotations

import hashlib
import importlib.metadata
import math
import os
import subprocess
from pathlib import Path
from typing import Any


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item) for item in value]
    return str(value)


def _devtool_version() -> str:
    try:
        return importlib.metadata.version("devtool")
    except importlib.metadata.PackageNotFoundError:
        return ""


def _git_info(repo_root: Path) -> dict[str, Any]:
    def run(*args: str) -> tuple[int, str]:
        try:
            proc = subprocess.run(
                ["git", *args], cwd=repo_root, capture_output=True, text=True, timeout=10, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            return 127, ""
        return proc.returncode, proc.stdout.strip()

    head_rc, head = run("rev-parse", "HEAD")
    status_rc, status = run("status", "--porcelain=v1", "--untracked-files=all")
    dirty_lines = [line for line in status.splitlines() if line.strip()] if status_rc == 0 else []
    return {
        "git_available": head_rc == 0 or status_rc == 0,
        "head": head if head_rc == 0 else "",
        "dirty": bool(dirty_lines),
        "dirty_count": len(dirty_lines),
    }


def build_hook_payload(
    *,
    repo_root: Path,
    artifact_source: Path,
    artifact_for_hook: Path,
    kind: str,
    phase: str,
    run_id: str,
    target: str,
    solution: str,
    script_path: str,
    script_sha256: str,
    runner: str,
    timeout_seconds: int,
    capabilities: dict[str, Any],
    declared_writes: list[str],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact_sha = ""
    artifact_size: int | None = None
    if artifact_source.is_file():
        data = artifact_source.read_bytes()
        artifact_sha = hashlib.sha256(data).hexdigest()
        artifact_size = len(data)
    extra = json_safe(dict(context or {}))
    validation = extra.pop("validation", {}) if isinstance(extra, dict) else {}
    commit = extra.pop("commit", {}) if isinstance(extra, dict) else {}
    execution = extra.pop("execution", {}) if isinstance(extra, dict) else {}
    event_name = {"preflight": "artifact_preflight", "validation": "artifact_validation", "finalize": "artifact_finalize"}.get(kind, f"artifact_{kind}")
    return {
        "schema_version": 1,
        # Stable v1 compatibility aliases. New consumers should prefer the structured
        # objects below, but existing finalize JSON-v1 hooks continue to work unchanged.
        "event": event_name,
        "phase": phase,
        "run_id": run_id,
        "repo_root": str(repo_root.resolve()),
        "target": target,
        "solution": solution,
        "write_paths": list(declared_writes),
        "hook": {
            "kind": kind,
            "phase": phase,
            "script_path": script_path,
            "script_sha256": script_sha256,
            "runner": runner,
            "timeout_seconds": timeout_seconds,
        },
        "run": {
            "id": run_id,
            "devtool_version": _devtool_version(),
            "pid": os.getpid(),
        },
        "artifact": {
            "path": str(artifact_for_hook.resolve()),
            "private_copy": artifact_for_hook.resolve() != artifact_source.resolve(),
            "name": artifact_source.name,
            "sha256": artifact_sha,
            "size": artifact_size,
        },
        "repository": {
            "root": str(repo_root.resolve()),
            **_git_info(repo_root),
        },
        "target_context": {
            "name": target,
            "solution": solution,
        },
        "validation": validation if isinstance(validation, dict) else {},
        "commit": commit if isinstance(commit, dict) else {},
        "execution": execution if isinstance(execution, dict) else {},
        "capabilities": json_safe(capabilities),
        "declared_writes": list(declared_writes),
        "context": extra if isinstance(extra, dict) else {},
    }
