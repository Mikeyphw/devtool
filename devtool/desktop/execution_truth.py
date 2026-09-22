from __future__ import annotations

"""Canonical execution truth helpers for desktop/GTK surfaces.

EXO-09 deliberately keeps transport compatibility while moving desktop semantic
state onto the EXO execution snapshot.  Raw stdout/stderr and legacy HUD files
remain useful evidence/recovery inputs, but they are not allowed to override an
embedded canonical execution graph.
"""

from typing import Any, Mapping

from ..hud import _execution_runtime_projection, _execution_snapshot_from_payload


def desktop_runtime_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a desktop runtime payload whose semantic truth is canonical when available."""
    source = dict(payload) if isinstance(payload, Mapping) else {}
    execution = _execution_snapshot_from_payload(source)
    if execution is None:
        return source

    notification = source.get("tasker_notification")
    if not isinstance(notification, dict):
        notification = source.get("notification") if isinstance(source.get("notification"), dict) else None
    projected = _execution_runtime_projection(execution, notification=notification)

    # Preserve transport/context fields that are not semantic execution truth.
    for key in (
        "updated_at", "updated_at_ms", "repo_root", "target", "targets", "runner",
        "project_type", "execution_environment", "variant", "log_path", "result",
        "artifact", "artifact_path", "overlay", "test_selection", "retryable",
        "resumable", "cancellable", "has_uncommitted_mutation", "ui_presentations",
        "ui_viewport", "events",
    ):
        if key in source and key not in projected:
            projected[key] = source[key]
    projected["desktop_truth"] = "canonical"
    return projected


def canonical_execution_available(payload: Mapping[str, Any] | None) -> bool:
    return _execution_snapshot_from_payload(dict(payload) if isinstance(payload, Mapping) else {}) is not None


def canonical_run_from_ipc(snapshot: Mapping[str, Any] | None, run_id: str) -> dict[str, Any]:
    if not isinstance(snapshot, Mapping):
        return {}
    runs = snapshot.get("runs") if isinstance(snapshot.get("runs"), list) else []
    wanted = str(run_id or "")
    for item in runs:
        if not isinstance(item, Mapping):
            continue
        candidate = desktop_runtime_payload(item)
        if str(candidate.get("run_id") or candidate.get("id") or "") == wanted:
            return candidate
    return {}


__all__ = ["canonical_execution_available", "canonical_run_from_ipc", "desktop_runtime_payload"]
