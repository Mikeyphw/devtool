from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

CONFIRMATION_SCHEMA_VERSION = 1
CONFIRMATION_MODES = ("never", "smart", "always")

DEFAULT_CONFIRMATION_POLICY: dict[str, str] = {
    "cancel": "smart",
    "rollback": "always",
    "delete_artifact": "always",
    "retry": "never",
    "apply_artifact": "smart",
    "artifact_manifest": "smart",
    "dirty_tree": "smart",
    "snapshot_overwrite": "always",
}

_ACTION_POLICY_KEYS = {
    "operation.cancel": "cancel",
    "operation.retry": "retry",
    "operation.rerun": "retry",
    "artifact.apply": "apply_artifact",
    "artifact.rollback": "rollback",
    "validation.retry_failed": "retry",
    "workflow.retry_node": "retry",
}

@dataclass(frozen=True, slots=True)
class ConfirmationDecision:
    action_id: str
    policy_key: str
    mode: str
    required: bool
    default: bool
    prompt: str
    reason: str
    schema_version: int = CONFIRMATION_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_confirmation_policy(value: Mapping[str, Any] | None) -> dict[str, str]:
    result = dict(DEFAULT_CONFIRMATION_POLICY)
    for key, raw in (value or {}).items():
        name = str(key or "").strip().lower().replace("-", "_")
        if name not in DEFAULT_CONFIRMATION_POLICY:
            raise ValueError(f"Unknown confirmation policy: {key}")
        mode = str(raw or "").strip().lower()
        if mode not in CONFIRMATION_MODES:
            raise ValueError(f"Invalid confirmation mode for {key}: {raw!r}; expected never, smart, or always")
        result[name] = mode
    return result


def _context_bool(context: Mapping[str, Any], key: str, default: bool = False) -> bool:
    value = context.get(key, default)
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _smart_required(policy_key: str, context: Mapping[str, Any]) -> tuple[bool, str]:
    if policy_key == "cancel":
        risky = _context_bool(context, "destructive_phase_started") or _context_bool(context, "has_uncommitted_mutation") or _context_bool(context, "confirm_cancel")
        return risky, "active operation has entered a mutation-sensitive phase" if risky else "graceful cancellation is reversible/safe at the current phase"
    if policy_key in {"apply_artifact", "artifact_manifest"}:
        risky = (
            _context_bool(context, "destructive")
            or _context_bool(context, "deletes")
            or _context_bool(context, "overwrites")
            or _context_bool(context, "untrusted")
            or _context_bool(context, "manifest_confirmation", True)
        )
        return risky, "artifact apply may modify repository state" if risky else "artifact is non-destructive and already validated"
    if policy_key == "dirty_tree":
        risky = _context_bool(context, "dirty_tree", True)
        return risky, "repository has uncommitted changes" if risky else "repository is clean"
    if policy_key in {"rollback", "delete_artifact", "snapshot_overwrite"}:
        return True, "operation can replace or remove existing state"
    return False, "operation is retryable and non-destructive"


def confirmation_decision(
    action_id: str,
    policy: Mapping[str, Any] | None = None,
    *,
    context: Mapping[str, Any] | None = None,
    prompt: str = "",
) -> ConfirmationDecision:
    ctx = dict(context or {})
    normalized = normalize_confirmation_policy(policy)
    policy_key = _ACTION_POLICY_KEYS.get(action_id, str(ctx.get("policy_key") or action_id).strip().lower().replace(".", "_").replace("-", "_"))
    if policy_key not in normalized:
        policy_key = "rollback" if _context_bool(ctx, "destructive") else "retry"
    mode = normalized[policy_key]
    if mode == "always":
        required, reason = True, "policy requires confirmation"
    elif mode == "never":
        required, reason = False, "policy skips confirmation"
    else:
        required, reason = _smart_required(policy_key, ctx)
    label = prompt or str(ctx.get("prompt") or "").strip()
    if not label:
        label = {
            "cancel": "Cancel the active operation?",
            "rollback": "Rollback repository changes?",
            "delete_artifact": "Delete this artifact?",
            "retry": "Retry this operation?",
            "apply_artifact": "Apply this artifact?",
            "artifact_manifest": "Continue with this artifact?",
            "dirty_tree": "Git tree is dirty. Continue?",
            "snapshot_overwrite": "Applying a snapshot may overwrite or delete files. Continue?",
        }.get(policy_key, "Continue?")
    default = _context_bool(ctx, "default", policy_key not in {"rollback", "delete_artifact", "snapshot_overwrite"})
    return ConfirmationDecision(action_id, policy_key, mode, required, default, label, reason)


def confirmation_catalog(policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    normalized = normalize_confirmation_policy(policy)
    return {
        "schema_version": CONFIRMATION_SCHEMA_VERSION,
        "modes": list(CONFIRMATION_MODES),
        "policy": normalized,
        "actions": dict(_ACTION_POLICY_KEYS),
    }
