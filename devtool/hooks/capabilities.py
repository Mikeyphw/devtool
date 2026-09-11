from __future__ import annotations

from typing import Any

KNOWN_HOOK_CAPABILITIES = (
    "repo_read",
    "repo_write",
    "artifact_read",
    "artifact_private_copy",
    "subprocess",
    "network",
    "environment",
    "secrets",
)


def normalize_hook_capabilities(raw: object, *, label: str = "hook.capabilities") -> list[str]:
    if raw is None:
        return []
    values = [raw] if isinstance(raw, str) else raw
    if not isinstance(values, list):
        raise ValueError(f"{label} must be a string or list of strings.")
    normalized: list[str] = []
    for item in values:
        if not isinstance(item, str):
            raise ValueError(f"{label} entries must be strings.")
        value = item.strip().lower().replace("-", "_")
        if not value:
            raise ValueError(f"{label} entries must not be empty.")
        if value not in KNOWN_HOOK_CAPABILITIES:
            raise ValueError(
                f"Unsupported {label} capability {item!r}; expected one of: "
                + ", ".join(KNOWN_HOOK_CAPABILITIES)
                + "."
            )
        if value not in normalized:
            normalized.append(value)
    return normalized


def capability_report(
    requested: list[str],
    *,
    protect_artifact_path: bool,
    enforce_write_boundary: bool,
) -> dict[str, Any]:
    """Describe real enforcement without claiming a sandbox that does not exist."""
    requested_set = set(requested)
    effective: dict[str, dict[str, Any]] = {}
    for capability in KNOWN_HOOK_CAPABILITIES:
        is_requested = capability in requested_set
        if capability == "artifact_private_copy":
            enforcement = "enforced" if protect_artifact_path else "unavailable"
            reason = (
                "artifact path points at an owner-only disposable copy"
                if protect_artifact_path
                else "private artifact copy is disabled for this hook"
            )
        elif capability == "repo_write":
            enforcement = "partially_enforced" if enforce_write_boundary else "declared_only"
            reason = (
                "repository mutations are fingerprinted and undeclared writes fail after hook execution"
                if enforce_write_boundary
                else "this runtime does not prevent filesystem writes before they occur"
            )
        elif capability in {"repo_read", "artifact_read", "subprocess", "environment", "secrets"}:
            enforcement = "declared_only"
            reason = "capability is declared for auditability; host-level denial is not currently enforced"
        elif capability == "network":
            enforcement = "declared_only" if is_requested else "unavailable"
            reason = (
                "network use is declared but no portable network namespace is enforced"
                if is_requested
                else "no supported host isolation is active to guarantee no-network execution"
            )
        else:  # defensive
            enforcement = "declared_only"
            reason = "capability is recorded but not independently sandboxed"
        effective[capability] = {
            "requested": is_requested,
            "enforcement": enforcement,
            "reason": reason,
        }
    return {
        "requested": list(requested),
        "effective": effective,
    }
