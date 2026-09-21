from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class HookExecutionRequest:
    repo_root: Path
    artifact: str | Path
    manifest: dict[str, Any]
    spec: dict[str, Any]
    kind: str
    phase: str
    script_label: str
    operation_name: str
    temp_prefix: str
    temp_env_key: str
    failure_kind: str
    target: str = ""
    solution: str = ""
    run_id: str = ""
    dry_run: bool = False
    log_path: Path | str | None = None
    ignore_checksum: bool = False
    default_timeout_seconds: int = 300
    default_protocol: str = "legacy-env"
    protect_artifact_path: bool = True
    declared_writes: list[str] = field(default_factory=list)
    requested_capabilities: list[str] = field(default_factory=list)
    enforce_write_boundary: bool = False
    restore_on_write_violation: bool = False
    render_progress: bool = False
    stream_output: bool = False
    extra_env: dict[str, str] = field(default_factory=dict)
    payload_context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class HookExecutionContext:
    protocol: str
    runner: str
    script_path: str
    script_sha256: str
    timeout_seconds: int
    requested_capabilities: list[str] = field(default_factory=list)
    effective_capabilities: dict[str, Any] = field(default_factory=dict)
    declared_writes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class HookExecutionResult:
    status: str = "pass"
    summary: str = ""
    warnings: list[str] = field(default_factory=list)
    annotations: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
