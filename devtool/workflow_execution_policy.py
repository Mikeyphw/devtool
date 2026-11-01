from __future__ import annotations

"""R39-R1 single owner for opt-in R38 workflow preflight policy.

Policy resolution is pure: it never edits CommandSpecs, creates leases, or
inspects a repository. The scheduler applies reuse metadata only AFTER all
policy and tracked-baseline checks have passed. The ordinary/native execution
and ownership lifecycle remain in workflow_scheduler.
"""

from dataclasses import dataclass
import os
from typing import Mapping, Sequence


class WorkflowExecutionPolicyError(ValueError):
    """Stable fail-closed reason for an invalid R38 execution policy."""


@dataclass(frozen=True, slots=True)
class WorkflowExecutionPolicy:
    audit_enabled: bool
    scratch_enabled: bool
    reuse_disabled_reason: str | None


def resolve_workflow_execution_policy(
    *,
    write_audit_mode: str | None,
    scratch_isolation_mode: str | None,
    environment: Mapping[str, str] | None = None,
    dry_run: bool,
    plan_exists: bool,
    parallel_requested: bool,
    command_envs: Sequence[Mapping[str, str]],
) -> WorkflowExecutionPolicy:
    """Validate R38 modes and ALL command environments without side effects.

    The precedence of the reuse reason matches the historical scheduler: when
    both opt-in modes are enforced, scratch isolation owns the displayed reason.
    Cancellation, per-command hooks, and terminal events remain native.
    """
    env = os.environ if environment is None else environment
    audit = (write_audit_mode if write_audit_mode is not None
             else env.get("DEVTOOL_R38_WRITE_AUDIT", "off")).strip().lower()
    scratch = (scratch_isolation_mode if scratch_isolation_mode is not None
               else env.get("DEVTOOL_R38_SCRATCH_ISOLATION", "off")).strip().lower()
    if audit not in {"off", "enforce"}:
        raise WorkflowExecutionPolicyError("R38_INVALID_WRITE_AUDIT_MODE")
    audit_enabled = audit == "enforce" and not dry_run
    if audit_enabled and (not plan_exists or parallel_requested):
        raise WorkflowExecutionPolicyError("R38_WRITE_AUDIT_REQUIRES_SERIAL_WORKFLOW")
    if scratch not in {"off", "enforce"}:
        raise WorkflowExecutionPolicyError("R38_INVALID_SCRATCH_ISOLATION_MODE")
    scratch_enabled = scratch == "enforce" and not dry_run
    if scratch_enabled and not plan_exists:
        raise WorkflowExecutionPolicyError("R38_SCRATCH_REQUIRES_NATIVE_WORKFLOW_NODES")
    if scratch_enabled:
        from .workflow_scratch import reserved_environment_names
        reserved = reserved_environment_names()
        for command_env in command_envs:
            collisions = sorted(key for key in command_env if key.upper() in reserved)
            if collisions:
                raise WorkflowExecutionPolicyError(
                    "R38_SCRATCH_ENV_OVERRIDE_REFUSED: " + ", ".join(collisions)
                )
    reason = ("r38-run-scratch-isolation" if scratch_enabled else
              "r38-tracked-write-audit" if audit_enabled else None)
    return WorkflowExecutionPolicy(audit_enabled, scratch_enabled, reason)
