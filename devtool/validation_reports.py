from __future__ import annotations

"""Durable validation reports and finalization integrity checks."""

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .validation_sessions import ValidationSession

REPORT_SCHEMA_VERSION = 1
_TERMINAL_PHASE_STATES = {"passed", "not-applicable", "succeeded", "skipped"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def report_root(repo_root: Path) -> Path:
    return repo_root.expanduser().resolve() / ".devtool" / "reports" / "validation"


def report_path(repo_root: Path, session_id: str, *, sealed: bool = False) -> Path:
    suffix = ".final.json" if sealed else ".json"
    return report_root(repo_root) / f"{session_id}{suffix}"


def _git_head(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root,
            capture_output=True, text=True, check=False, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _git_dirty(repo_root: Path) -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_root,
            capture_output=True, text=True, check=False, timeout=5,
        )
        return bool(result.stdout.strip()) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _rollback_summary(session: "ValidationSession") -> dict[str, Any]:
    transaction_path = str(session.transaction_path or "")
    if not transaction_path:
        context = dict(session.apply_context or {})
        transaction_path = str(
            context.get("failed_transaction_path")
            or context.get("aborted_transaction_path")
            or context.get("transaction_path")
            or ""
        )
    summary: dict[str, Any] = {
        "transaction_path": transaction_path,
        "status": "none",
        "attempted": bool((session.apply_context or {}).get("rollback_attempted", False)),
        "succeeded": bool((session.apply_context or {}).get("rollback_succeeded", False)),
        "total_paths": 0,
        "verified_paths": 0,
        "error": "",
    }
    if not transaction_path:
        return summary
    try:
        from .transactions import read_transaction
        transaction = read_transaction(transaction_path)
    except Exception as exc:
        summary["status"] = "archived-or-unavailable"
        summary["error"] = str(exc)
        return summary
    entries = [item for item in transaction.get("affected_paths", []) or [] if isinstance(item, dict)]
    summary.update({
        "status": str(transaction.get("status") or "unknown"),
        "attempted": bool(transaction.get("rollback_started_at") or summary["attempted"]),
        "succeeded": str(transaction.get("status") or "") == "rolled-back" or summary["succeeded"],
        "total_paths": len(entries),
        "verified_paths": sum(bool(item.get("verified")) for item in entries),
        "error": str(transaction.get("rollback_error") or ""),
    })
    return summary


def _issue_totals(session: "ValidationSession") -> dict[str, int]:
    warnings = errors = 0
    for phase in session.phases:
        for issue in phase.issues:
            severity = str(issue.get("severity") or issue.get("level") or "").lower()
            if severity == "warning":
                warnings += 1
            elif severity in {"error", "fatal"}:
                errors += 1
    return {"warnings": warnings, "errors": errors}


def _attempt_counts(session: "ValidationSession") -> dict[str, int]:
    counts: dict[str, int] = {}
    for phase in session.phases:
        for attempt in phase.attempt_history:
            verdict = str(attempt.verdict or "unknown")
            counts[verdict] = counts.get(verdict, 0) + 1
    return counts


def final_verdict(session: "ValidationSession") -> str:
    if session.status == "completed":
        return "validated"
    if session.status == "validated-uncommitted":
        return "validated-uncommitted"
    if session.status == "aborted-rolled-back":
        return "rolled-back"
    if session.status == "aborted-kept-changes":
        return "kept-without-verdict"
    if session.status == "rollback-failed":
        return "rollback-failed"
    if session.status == "recovery-required":
        return "recovery-required"
    if session.status == "blocked":
        return "blocked"
    if session.status == "validated":
        return "validated-pending-finalization"
    return session.status or "unknown"


def build_validation_report(
    session: "ValidationSession",
    *,
    commit_status: str = "",
    commit_hash: str = "",
    administrative_warnings: list[str] | None = None,
) -> dict[str, Any]:
    repo_root = Path(session.repo_root)
    context = dict(session.apply_context or {})
    profiles: list[str] = []
    for phase in session.phases:
        for attempt in phase.attempt_history:
            profile = str(attempt.resource_profile or "").strip()
            if profile and profile not in profiles:
                profiles.append(profile)
    artifact = context.get("artifact") or context.get("artifacts") or ""
    phases = []
    for phase in session.phases:
        item = asdict(phase)
        item["terminal"] = phase.status in _TERMINAL_PHASE_STATES
        item["skip_reason"] = phase.invalidated_reason if phase.status in {"skipped", "not-applicable"} else ""
        phases.append(item)
    totals = _issue_totals(session)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "session_id": session.session_id,
        "report_kind": "devtool-validation",
        "final_verdict": final_verdict(session),
        "session_state": session.status,
        "source": {
            "command": str(context.get("command") or "validate"),
            "validation_mode": str(context.get("validation_mode") or "standalone"),
            "artifact": artifact,
            "artifact_type": str(context.get("artifact_type") or ""),
            "artifact_sha256": str(context.get("artifact_sha") or ""),
        },
        "repository": {
            "repo_root": session.repo_root,
            "project_root": session.project_root,
            "project_type": session.project_type,
            "target": session.target,
            "variant": session.variant,
            "initial_git_head": str(getattr(session, "initial_git_head", "") or ""),
            "checkpoint_git_head": session.git_head,
            "final_git_head": _git_head(repo_root),
            "dirty": _git_dirty(repo_root),
        },
        "timing": {
            "created_at": session.created_at,
            "validated_at": session.validated_at,
            "finalized_at": session.finalized_at,
            "updated_at": session.updated_at,
        },
        "plan": {
            "plan_fingerprint": session.plan_fingerprint,
            "required_checkpoint_name": session.required_checkpoint_name,
            "required_checkpoint_keys": list(session.required_checkpoint_keys),
            "required_checkpoint_satisfied": all(
                phase.key not in session.required_checkpoint_keys or phase.status in _TERMINAL_PHASE_STATES
                for phase in session.phases
            ),
            "phase_total": len(session.phases),
            "completed_phases": sum(phase.status in _TERMINAL_PHASE_STATES for phase in session.phases),
            "phases": phases,
        },
        "attempts": {
            "count": sum(len(phase.attempt_history) for phase in session.phases),
            "verdict_counts": _attempt_counts(session),
            "resource_profiles": profiles,
            "recommended_retry_profile": str(session.options.get("recommended_retry_profile") or ""),
        },
        "diagnostics": totals,
        "rollback": _rollback_summary(session),
        "commit": {
            "status": commit_status or str(getattr(session, "commit_status", "") or "not-requested"),
            "hash": commit_hash or str(getattr(session, "commit_hash", "") or ""),
            "message": str(context.get("commit") or ""),
        },
        "failure": {
            "failure_action": session.failure_action,
            "last_error": session.last_error,
            "paused_reason": session.paused_reason,
            "invalidated_from": session.invalidated_from,
        },
        "administrative_warnings": list(administrative_warnings or []),
    }


def write_validation_report(
    session: "ValidationSession",
    *,
    commit_status: str = "",
    commit_hash: str = "",
    administrative_warnings: list[str] | None = None,
    sealed: bool = False,
) -> Path:
    path = report_path(Path(session.repo_root), session.session_id, sealed=sealed)
    if sealed and path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_validation_report(
        session,
        commit_status=commit_status,
        commit_hash=commit_hash,
        administrative_warnings=administrative_warnings,
    )
    payload["sealed"] = sealed
    if sealed:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        payload["report_sha256"] = hashlib.sha256(canonical).hexdigest()
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def load_validation_report(repo_root: Path, session_id: str) -> dict[str, Any]:
    final_path = report_path(repo_root, session_id, sealed=True)
    path = final_path if final_path.is_file() else report_path(repo_root, session_id)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def finalization_issues(session: "ValidationSession", current_fingerprints: dict[str, str]) -> list[str]:
    issues: list[str] = []
    if any(phase.status in {"running", "pausing", "waiting", "stale", "failed", "interrupted"} for phase in session.phases):
        issues.append("one or more phases are active, stale, interrupted, or failed")
    required = [phase for phase in session.phases if phase.required]
    if not required or any(phase.status not in _TERMINAL_PHASE_STATES for phase in required):
        issues.append("not all required phases have passed or been declared not applicable")
    if session.required_checkpoint_keys:
        by_key = {phase.key: phase for phase in session.phases}
        if any(key not in by_key or by_key[key].status not in _TERMINAL_PHASE_STATES for key in session.required_checkpoint_keys):
            issues.append("required validation checkpoint is not satisfied")
    changed = sorted(
        domain for domain, value in current_fingerprints.items()
        if value != session.fingerprints.get(domain)
    )
    if changed:
        issues.append("repository validation inputs changed after the final clean checkpoint: " + ", ".join(changed))
    return issues
