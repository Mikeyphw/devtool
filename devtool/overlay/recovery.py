"""R06D read-only transaction/LIFE recovery assessment.

Observes the existing transaction journal and RUN-V3 catalog.  Never calls the
legacy marker enumerator (which can prune damaged markers), acquires mutation
locks, performs rollback, or authorizes an artifact deletion.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from ..transactions import (
    _preflight_git_restore,
    _preflight_live_target_guards,
    _preflight_retry_guards,
    _process_is_alive,
    transaction_recovery_diagnostics,
    transaction_root,
)

_MAX_MARKERS = 256
_MAX_TRANSACTIONS = 128
_MAX_PATHS = 2048


def _regular_json(path: Path) -> dict[str, Any]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 4 * 1024 * 1024:
        raise ValueError("not a bounded regular JSON file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON root is not an object")
    return value


def _one(repo: Path, tx_path: Path, tx: dict[str, Any]) -> dict[str, Any]:
    identity = str(tx.get("transaction_id") or "")
    status = str(tx.get("status") or "unknown")
    entries = tx.get("affected_paths")
    blockers: list[str] = []
    if not identity or tx_path.parent.name != identity:
        blockers.append("transaction ID/path mismatch")
    if str(tx.get("repo_root") or "") != str(repo):
        blockers.append("transaction repository identity mismatch")
    if not isinstance(entries, list) or len(entries) > _MAX_PATHS or any(not isinstance(v, dict) for v in entries):
        blockers.append("invalid or oversized affected-path journal")
        entries = []
    pid = int(tx.get("pid") or 0)
    live = _process_is_alive(pid, str(tx.get("process_start") or ""))
    if live:
        blockers.append("transaction owner still alive")
    phase = transaction_recovery_diagnostics(tx)
    if status == "rollback-failed" and not entries:
        blockers.append("no affected-path checkpoints recorded for failed rollback")
    # The canonical rollback copies from its private backup directory.  Never
    # advertise even conditional retry while recorded originals lack backups.
    backup_dir = Path(str(tx.get("backup_dir") or "")) if tx.get("backup_dir") else None
    for entry in entries:
        if not entry.get("existed"):
            continue
        raw = str(entry.get("path") or "")
        parts = Path(raw).parts
        if (not backup_dir or backup_dir != tx_path.parent / "backup"
                or not parts or Path(raw).is_absolute() or ".." in parts):
            blockers.append("original-file backup path cannot be proven")
            break
        backup = backup_dir.joinpath(*parts)
        # Avoid reading through symlinks or a parent rewritten after capture;
        # directory identities and bytes remain the canonical retry authority.
        if any(parent.is_symlink() for parent in (backup_dir, *list(backup.parents)[:len(parts)])):
            blockers.append("original-file backup parent is a symlink")
            break
        if not backup.exists() and not backup.is_symlink():
            blockers.append("original-file rollback backup is absent")
            break
    if not blockers and status in {"rollback-failed", "rollback-requested", "rollback-staging", "rollback-restoring", "rollback-verifying"}:
        # Existing canonical guards are read-only; this is diagnostic parity,
        # never a durable permission to mutate on a later invocation.
        for checker in (
            lambda: _preflight_retry_guards(repo, entries),
            lambda: _preflight_live_target_guards(repo, entries),
            lambda: _preflight_git_restore(repo, tx),
        ):
            try:
                checker()
            except (RuntimeError, ValueError, OSError) as exc:
                blockers.append(f"{type(exc).__name__}: {exc}"[:280])
    elif status not in {"rollback-failed", "rollback-requested", "rollback-staging", "rollback-restoring", "rollback-verifying"}:
        blockers.append("not an explicitly requested rollback-retry phase; inspect pending validation/publication first")
    if phase.get("missing_checkpoint_paths"):
        blockers.append("missing persisted rollback checkpoint")
    classification = "live_owner" if live else "blocked" if blockers else "retry_preflight_clear"
    # Only the canonical existing rollback command may make recovery decisions.
    historical_review = {"eligible": False, "command": ""}
    if status == "rollback-failed" and identity:
        from ..historical_transactions import inspect_historical_transaction
        historical = inspect_historical_transaction(repo, identity)
        historical_review = {
            "eligible": bool(historical.get("eligible")),
            "classification": str(historical.get("classification") or "blocked"),
            "command": (f"devtool -r {repo} transaction inspect {identity} --json"
                        if historical.get("eligible") else ""),
        }
    return {
        "historical_retirement_review": historical_review,
        "transaction_id": identity, "status": status, "phase": phase.get("phase") or "unknown",
        "owner_live": live, "affected_paths": len(entries),
        "checkpoint_paths": len(phase.get("checkpoint_paths") or []),
        "classification": classification, "blockers": blockers,
        "rollback_retry_preflight": "clear_at_observation_only" if not blockers else "not_established",
        "existing_command": (f"devtool rollback --transaction {identity}" if not blockers and identity else ""),
        "automatic_recovery_authorized": False,
    }


def _transactions(repo: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    root = transaction_root(repo)
    marker_dir = root / "active"
    markers = [root / "active.json"]
    issues: list[dict[str, str]] = []
    if marker_dir.is_symlink():
        issues.append({"kind": "unsafe_marker_directory", "name": "active", "detail": "symbolic link"})
    elif marker_dir.is_dir():
        markers.extend(sorted(marker_dir.glob("*.json")))
    if len(markers) > _MAX_MARKERS:
        issues.append({"kind": "marker_count_exceeded", "name": "active", "detail": "inspection bounded"})
        markers = markers[:_MAX_MARKERS]
    seen: set[str] = set()
    found: list[dict[str, Any]] = []
    for marker in markers:
        if not marker.exists() and not marker.is_symlink():
            continue
        try:
            data = _regular_json(marker)
            destination = Path(str(data.get("path") or ""))
            # Require the exact journal location owned by this repository;
            # never follow an arbitrary marker pointer outside the state root.
            if not destination.is_absolute() or destination.name != "transaction.json":
                raise ValueError("invalid transaction journal pointer")
            if destination.parent.parent != root or destination.parent.is_symlink():
                raise ValueError("journal pointer outside exact repository state")
            if marker.parent == marker_dir and marker.stem != destination.parent.name:
                raise ValueError("marker filename and transaction ID disagree")
            if str(destination) in seen:
                continue
            seen.add(str(destination))
            if len(found) >= _MAX_TRANSACTIONS:
                raise ValueError("transaction count inspection limit reached")
            tx = _regular_json(destination)
            found.append(_one(repo, destination, tx))
        except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
            issues.append({"kind": "unreadable_marker_or_journal", "name": marker.name, "detail": f"{type(exc).__name__}: {exc}"[:160]})
    return sorted(found, key=lambda x: x["transaction_id"]), issues


def _life_runs(repo: Path, *, limit: int) -> dict[str, Any]:
    from ..core_runtime import private_run_root
    from ..run_store import RunCatalog
    root = private_run_root(repo)
    path = root / "catalog.json"
    if not path.exists():
        return {"catalog": "absent", "failures": []}
    if path.is_symlink():
        return {"catalog": "untrusted_symlink", "failures": []}
    try:
        catalog = RunCatalog.open(root, create=False)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return {"catalog": "unreadable", "error": f"{type(exc).__name__}: {exc}"[:160], "failures": []}
    counts: dict[str, int] = {}
    for attachment in catalog.attachments.values():
        if attachment.kind == "cleanup-receipt":
            counts[attachment.run_id] = counts.get(attachment.run_id, 0) + 1
    failures: list[dict[str, Any]] = []
    for run_id, data in reversed(list(catalog.runs.items())):
        if not isinstance(data, dict):
            continue
        kind = str(data.get("failure_kind") or "")
        state = str(data.get("status") or "")
        if kind != "artifact_cleanup_failure" and (state not in {"failed", "error"} or not counts.get(run_id)):
            continue
        failures.append({"run_id": run_id, "run_status": state,
                         "failure_kind": kind or "unknown", "cleanup_receipts_recorded": counts.get(run_id, 0),
                         "classification": "historical_failure_unresolved", "artifact_deletion_authorized": False})
        if len(failures) >= limit:
            break
    return {"catalog": "read_only", "failures": failures}


def overlay_recovery_plan(repo_root: Path, *, transaction: str = "", limit: int = 10) -> dict[str, Any]:
    """Only reports durable recorded state; no fs mutation, Git writes, or lock acquisition."""
    repo = repo_root.resolve()
    rows, issues = _transactions(repo)
    selector = str(transaction or "").strip()
    if selector:
        rows = [r for r in rows if r["transaction_id"] == selector]
        if not rows:
            issues.append({"kind": "transaction_not_found", "name": selector, "detail": "Exact active transaction ID required; archived transactions are not rollback targets"})
    life = _life_runs(repo, limit=max(1, min(int(limit), 50)))
    disposition = "manual_review" if issues or any(r["classification"] != "retry_preflight_clear" for r in rows) else "inspect_only"
    return {"schema_version": 1, "producer": "devtool-overlay-r06d-recovery-plan",
            "repo": str(repo), "read_only": True, "mutation_performed": False,
            "safe_to_auto_recover": False, "safe_to_delete_artifact": False,
            "decision": disposition, "active_transactions": rows,
            "marker_issues": issues, "life": life,
            "next_step": "Review exact durable checkpoints and source identity. Only existing explicit rollback may retry; for ahead-of-HEAD preflight-only rollback failures use `devtool transaction inspect FULL_ID` before considering marker-only retirement. Never auto-retire or delete retained LIFE artifacts."}
