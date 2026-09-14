from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifacts import (
    artifact_finalize_phases,
    artifact_finalize_script,
    artifact_finalize_write_paths,
    artifact_hook_capabilities,
    artifact_preflight_phases,
    artifact_preflight_script,
    artifact_preflight_write_paths,
    artifact_validation_script,
    artifact_validation_write_paths,
    read_artifact_manifest,
    resolve_artifact_path,
)
from .hooks import EmbeddedHookEngine, HookExecutionRequest
from .models import OperationResult

_ENGINE = EmbeddedHookEngine()


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
    """Run an embedded validator through the canonical hook runtime."""
    artifact_path = resolve_artifact_path(artifact)
    manifest = manifest or read_artifact_manifest(artifact_path)
    spec = artifact_validation_script(manifest)
    if not spec:
        return None
    payload_context = {"validation": {"requested": True, "status": "running"}}
    manifest_validation = manifest.get("validation") if isinstance(manifest.get("validation"), dict) else {}
    extra_payload = manifest_validation.get("payload_context") if isinstance(manifest_validation, dict) else None
    if isinstance(extra_payload, dict):
        payload_context.update(extra_payload)
    return _ENGINE.execute(
        HookExecutionRequest(
            repo_root=repo_root,
            artifact=artifact_path,
            manifest=manifest,
            spec=spec,
            kind="validation",
            phase="validate",
            script_label="Validation",
            operation_name="artifact-validate-script",
            temp_prefix="devtool-artifact-validation-",
            temp_env_key="DEVTOOL_VALIDATION_TMPDIR",
            failure_kind="validation_failure",
            target=target,
            solution=solution,
            run_id=run_id,
            dry_run=dry_run,
            log_path=log_path,
            ignore_checksum=ignore_checksum,
            default_timeout_seconds=900,
            default_protocol="legacy-env",
            protect_artifact_path=True,
            declared_writes=artifact_validation_write_paths(manifest),
            requested_capabilities=artifact_hook_capabilities(manifest, "validation"),
            # Validators historically run builds/tests and can legitimately create build
            # outputs. Enforce write boundaries only when the manifest explicitly opts
            # into write_paths; otherwise preserve the established validator behavior.
            enforce_write_boundary=bool(artifact_validation_write_paths(manifest)),
            extra_env={"DEVTOOL_VALIDATION_PHASE": "validate"},
            payload_context=payload_context,
        )
    )


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
    """Run an optional finalization phase through the canonical hook runtime."""
    artifact_path = resolve_artifact_path(artifact)
    manifest = manifest or read_artifact_manifest(artifact_path)
    if phase not in artifact_finalize_phases(manifest):
        return None
    spec = artifact_finalize_script(manifest)
    if not spec:
        return None
    declared_writes = artifact_finalize_write_paths(manifest)
    # after_commit is observational: even declared repository writes are not permitted
    # because there is no safe automatic way to fold them into the already-created commit.
    effective_writes = [] if phase == "after_commit" else declared_writes
    result = _ENGINE.execute(
        HookExecutionRequest(
            repo_root=repo_root,
            artifact=artifact_path,
            manifest=manifest,
            spec=spec,
            kind="finalize",
            phase=phase,
            script_label="Finalize",
            operation_name="artifact-finalize-script",
            temp_prefix="devtool-artifact-finalize-",
            temp_env_key="DEVTOOL_FINALIZE_TMPDIR",
            failure_kind="finalize_failure",
            target=target,
            solution=solution,
            run_id=run_id,
            dry_run=dry_run,
            log_path=log_path,
            ignore_checksum=ignore_checksum,
            default_timeout_seconds=300,
            default_protocol="json-v1",
            protect_artifact_path=True,
            declared_writes=effective_writes,
            requested_capabilities=artifact_hook_capabilities(manifest, "finalize"),
            enforce_write_boundary=True,
            restore_on_write_violation=phase == "after_commit",
            extra_env={"DEVTOOL_FINALIZE_PHASE": phase},
            payload_context={
                "validation": {
                    "requested": bool(validation_requested),
                    "status": validation_status,
                },
                "commit": {
                    "requested": bool(commit_requested),
                    "message": commit_message,
                    "paths": list(commit_paths or []),
                },
                "execution": {
                    "observational": phase == "after_commit",
                },
            },
        )
    )
    result.metadata["finalize_phase"] = phase
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
    """Run an optional extraction-boundary hook through the canonical hook runtime."""
    artifact_path = resolve_artifact_path(artifact)
    manifest = manifest or read_artifact_manifest(artifact_path)
    phases = artifact_preflight_phases(manifest)
    if phase not in phases:
        return None
    spec = artifact_preflight_script(manifest)
    if not spec:
        return None
    declared_writes = artifact_preflight_write_paths(manifest)
    result = _ENGINE.execute(
        HookExecutionRequest(
            repo_root=repo_root,
            artifact=artifact_path,
            manifest=manifest,
            spec=spec,
            kind="preflight",
            phase=phase,
            script_label="Preflight",
            operation_name="artifact-preflight-script",
            temp_prefix="devtool-artifact-preflight-",
            temp_env_key="DEVTOOL_PREFLIGHT_TMPDIR",
            failure_kind="preflight_failure",
            target=target,
            solution=solution,
            run_id=run_id,
            dry_run=dry_run,
            log_path=log_path,
            ignore_checksum=ignore_checksum,
            default_timeout_seconds=300,
            default_protocol="legacy-env",
            protect_artifact_path=True,
            declared_writes=declared_writes,
            requested_capabilities=artifact_hook_capabilities(manifest, "preflight"),
            enforce_write_boundary=True,
            # Legacy variables are intentionally retained indefinitely.
            extra_env={"DEVTOOL_PREFLIGHT_PHASE": phase},
            payload_context={"execution": {"boundary": "extract"}},
        )
    )
    result.metadata["preflight_phase"] = phase
    return result
